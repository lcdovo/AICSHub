"""LLM 增强的查询改写。

组合改写器：先按保守规则改写，规则未命中（自我含查询）时，在 LLM 可用且
开关开启的情况下，由模型做一次多轮语义改写以提升 RAG 召回。任何一环不可用
都完全退化为规则改写，保证离线/演示环境的确定性与既有召回效果不回退。
"""
import logging
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field

from app.agent.query_rewrite import ConservativeQueryRewriter, QueryRewriteResult
from app.services.llm_service import LLMService
from app.config.config import settings

logger = logging.getLogger(__name__)

REWRITE_SYSTEM_PROMPT = (
    "你是一名客服知识库检索助手，负责在用户出现指代省略时，"
    "把一句不完整的追问改写为可独立检索的完整查询。"
    "要求：只补全指代与必要上下文，不臆造事实，不要加入未提及的信息；"
    "若查询本身已完整，applied 必须为 false 并原样返回。"
)


class _QueryRewriteItem(BaseModel):
    """模型输出的改写指令契约。"""

    rewritten_query: str = Field(default="", description="改写后的独立检索查询")
    applied: bool = Field(default=False, description="是否确实需要改写")
    reason: str = Field(default="", description="改写理由")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class LLMQueryRewriter:
    """组合改写器：规则优先，未命中时由模型做语义改写。"""

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

        rule = self.rule_rewriter.rewrite(original, messages, collected_info)
        if rule.applied:
            return rule

        # 规则已明确判定"问题自包含、无需改写"：直接短路返回，不再调 LLM 复审。
        # 这类查询（如"机械键盘的轴体怎么换"）字面完整可独立检索，LLM 复审
        # 大概率也返回不改写，只为它串行多花 5~9s 纯属浪费。
        if rule.reason == "self_contained_query":
            return rule

        # 走到这里 = 规则无法给出改写（主要是没有上轮上下文可供拼接，
        # 但问题又可能语义不完整，如单轮省略主语的追问），此兜底场景量极少，
        # 才交给 LLM 语义补全。
        enable_llm = (
            bool(getattr(settings, "LLM_QUERY_REWRITE_ENABLED", False))
            and self.llm_service is not None
            and self.llm_service.is_available()
        )
        if not enable_llm:
            return rule

        llm = await self.llm_service.run_structured(
            REWRITE_SYSTEM_PROMPT,
            original,
            _QueryRewriteItem,
            tags=["query_rewrite"],
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
        return rule