"""LLM-as-judge 防幻觉校验。

对 RAG/工具生成回答做事实一致性与引用溯源核查，识别"无依据自信回答"。
当开关开启且 LLM 可用时才启用；否则返回 None 交由启发式校验兜底，
保证离线环境可运行且不降低既有判定能力。
"""
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.llm_service import LLMService
from app.config.config import settings

logger = logging.getLogger(__name__)

JUDGE_SYSTEM_PROMPT = (
    "你是客服回答质检员。给出回答、用户问题与可用证据（知识库片段/工具结果）。"
    "请判断：1) 回答中的关键论断是否有证据支撑（grounded）；2) 是否存在证据之外"
    "的臆造信息（has_unsupported_claim）；3) 综合可信度 fact_score（0-1）；"
    "4) 是否建议改采用“无据不答”的拒答话术（refuse_recommended）。"
    "只依据给定证据与常识判断，不要求回答必须逐字复述证据。"
)


class _JudgeVerdict(BaseModel):
    """模型输出的质检契约。"""

    grounded: bool = Field(default=False, description="回答中关键论断是否都有证据支撑")
    fact_score: float = Field(default=0.5, ge=0.0, le=1.0)
    has_unsupported_claim: bool = Field(default=False, description="是否存在证据之外的臆造信息")
    refuse_recommended: bool = Field(default=False, description="是否建议拒答/标注不确定")
    reason: str = Field(default="", description="判定理由")


class LLMRAGJudge:
    """RAG 回答的可信度评估器（防幻觉增强层）。"""

    DEFAULT_THRESHOLD = 0.45

    def __init__(
        self,
        llm_service: Optional[LLMService] = None,
        threshold: float = DEFAULT_THRESHOLD,
    ):
        self.llm_service = llm_service
        self.threshold = threshold

    async def judge(
        self,
        response: str,
        user_query: str,
        kb_context: Optional[List[Dict[str, Any]]] = None,
        tool_evidence: Optional[str] = "",
    ) -> Optional[Dict[str, Any]]:
        """评估回答可信度；LLM 不可用或未开启时返回 None，由上层回退启发式。"""
        enable = bool(getattr(settings, "LLM_JUDGE_ENABLED", False))
        if not (enable and self.llm_service is not None and self.llm_service.is_available()):
            return None
        if not response or not response.strip():
            return None

        evidence = self._format_evidence(kb_context or [], tool_evidence)
        prompt = _compose_prompt(response, user_query, evidence)
        verdict = await self.llm_service.run_structured(
            JUDGE_SYSTEM_PROMPT, prompt, _JudgeVerdict, tags=["llm_judge"]
        )
        if verdict is None:
            return None

        fact_score = max(0.0, min(1.0, float(verdict.fact_score or 0.0)))
        refuse = bool(verdict.refuse_recommended or verdict.has_unsupported_claim) or (
            not verdict.grounded and fact_score < self.threshold
        )
        return {
            "grounded": bool(verdict.grounded),
            "fact_score": round(fact_score, 2),
            "has_unsupported_claim": bool(verdict.has_unsupported_claim),
            "refuse_recommended": bool(refuse),
            "reason": verdict.reason,
        }

    @staticmethod
    def _format_evidence(
        kb_context: List[Dict[str, Any]], tool_evidence: str
    ) -> str:
        lines = []
        for i, item in enumerate(kb_context[:3], 1):
            title = item.get("title", "未命名文档")
            content = " ".join(str(item.get("content", "")).split())[:200]
            lines.append(f"[知识库{i}] {title}: {content}")
        if tool_evidence:
            lines.append(f"[工具结果] {tool_evidence[:300]}")
        return "\n".join(lines) if lines else "无可用证据"


def _compose_prompt(response: str, user_query: str, evidence: str) -> str:
    return (
        f"用户问题：{user_query}\n"
        f"客服回答：{response}\n"
        f"可用证据：\n{evidence}"
    )


def create_llm_judge(llm_service: Optional[LLMService] = None) -> LLMRAGJudge:
    return LLMRAGJudge(llm_service=llm_service)