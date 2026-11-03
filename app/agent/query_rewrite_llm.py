"""LLM 增强的查询改写。

组合改写器：规则负责判定当前追问是否依赖上文（指代/省略），命中后按复杂度分流——
简单指代（短句+短上文+单指代）直接用规则拼接，零延迟；复杂场景（长上下文/
多指代/话题跳跃）由 LLM 结合对话历史补全为可独立检索的完整查询。LLM 不可用、
开关未开或模型返回无效时，回退到保守规则的结果。任何一环不可用都完全退化为
规则改写，保证离线/演示环境的确定性与既有召回效果不回退。
"""
import logging
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field

from app.agent.query_rewrite import ConservativeQueryRewriter, QueryRewriteResult
from app.services.llm_service import LLMService
from app.config.config import settings

logger = logging.getLogger(__name__)

REWRITE_SYSTEM_PROMPT = (
    "你是一名客服知识库检索助手。对话中用户可能用指代词（如：这个/那个/它/该产品）"
    "或省略主语提出追问，导致单句无法独立检索。你的任务：结合对话历史，"
    "把当前追问改写为一句可独立检索的完整查询。"
    "要求：只补全指代与必要上下文，不臆造事实，不要加入对话中未提及的信息；"
    "若当前追问本身已完整、可独立检索，applied 必须为 false 并原样返回 rewritten_query。"
)


class _QueryRewriteItem(BaseModel):
    """模型输出的改写指令契约。"""

    rewritten_query: str = Field(default="", description="改写后的独立检索查询")
    applied: bool = Field(default=False, description="是否确实需要改写")
    reason: str = Field(default="", description="改写理由")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class LLMQueryRewriter:
    """组合改写器：规则判改写，LLM 做语义补全，规则结果为兜底。"""

    _HISTORY_TURNS = 3        # 提供给 LLM 的最近 user 消息轮数
    _MAX_HISTORY_CHARS = 400  # 历史文本上限，控制 prompt 长度

    def __init__(self, llm_service: Optional[LLMService] = None):
        self.rule_rewriter = ConservativeQueryRewriter()
        self.llm_service = llm_service

    async def rewrite(
        self,
        query: str,
        messages: Optional[Iterable[Dict[str, Any]]] = None,
        collected_info: Optional[Dict[str, Any]] = None,
    ) -> QueryRewriteResult:
        original = " ".join(str(query or "").split())
        if not original:
            return QueryRewriteResult(original, original, False, "empty_query", 1.0)

        messages = list(messages or [])
        rule = self.rule_rewriter.rewrite(original, messages, collected_info)

        # 规则已明确判定"无需改写"：直接短路返回，不再调 LLM 复审。
        # ① self_contained_query：问题字面完整可独立检索（如"机械键盘的轴体
        #    怎么换"），LLM 复审大概率也返回不改写，只为它串行多花 1~3s 浪费。
        if rule.reason == "self_contained_query":
            return rule
        if rule.reason == "no_previous_user_query":
            return await self._rewrite_with_llm(original, messages, collected_info) or rule

        # 规则命中"需要改写"（reference_with_context / short_follow_up）：
        # 按复杂度分流——简单指代规则拼接已足够（零延迟），复杂场景才由 LLM
        # 结合历史生成独立查询（语义补全比机械拼接更利于向量召回）；LLM 不可用 /
        # 开关关闭 / 模型返回无效时回退到规则拼接结果。
        if rule.applied:
            if rule.simple:
                return rule
            llm_result = await self._rewrite_with_llm(original, messages, collected_info)
            if llm_result is not None:
                return llm_result
            return rule

        # 理论不可达：非空且未短路时规则 applied 必为 True，此处兜底防御。
        return rule

    async def _rewrite_with_llm(
        self,
        original: str,
        messages: List[Dict[str, Any]],
        collected_info: Optional[Dict[str, Any]],
    ) -> Optional[QueryRewriteResult]:
        """带对话历史做一次结构化语义补全；不可用或无效时返回 None 由调用方回退。"""
        enable_llm = (
            bool(getattr(settings, "LLM_QUERY_REWRITE_ENABLED", False))
            and self.llm_service is not None
            and self.llm_service.is_available()
        )
        if not enable_llm:
            return None

        history = self._recent_user_messages(original, messages, self._HISTORY_TURNS)

        info = collected_info or {}
        order_id = str(info.get("order_id") or "").strip()
        history_text = "\n".join(history)[: self._MAX_HISTORY_CHARS] or "无历史；不要编造省略的对象，无法补全时返回不改写。"
        user_text = (
            f"对话历史（按时间先后）：\n{history_text}\n\n当前追问：{original}"
            + (f"\n已知信息：订单号 {order_id}" if order_id else "")
        )

        llm = await self.llm_service.run_structured(
            REWRITE_SYSTEM_PROMPT,
            user_text,
            _QueryRewriteItem,
            tags=["query_rewrite"],
            prefer_local=True,
        )
        if (
            llm
            and llm.applied
            and llm.rewritten_query
            and llm.rewritten_query != original
        ):
            return QueryRewriteResult(
                original,
                llm.rewritten_query,
                True,
                f"llm:{llm.reason or 'semantic'}",
                round(float(llm.confidence or 0.5), 2),
            )
        return None

    @staticmethod
    def _recent_user_messages(
        current_query: str, messages: List[Dict[str, Any]], limit: int
    ) -> List[str]:
        """收集当前追问之前的最近 limit 条 user 消息正文（不含当前 query），时间正序。"""
        skipped_current = False
        recent: List[str] = []
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            content = " ".join(str(message.get("content") or "").split())
            if not content:
                continue
            if not skipped_current and content == current_query:
                skipped_current = True
                continue
            recent.append(content)
            if len(recent) >= limit:
                break
        recent.reverse()
        return recent
