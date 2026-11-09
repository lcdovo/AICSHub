"""查询改写是有当前轮次来源的观察，不是工具参数或业务授权。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RewriteOrigin = Literal["rewriter", "unified_understanding", "legacy", "fallback"]


class QueryRewriteObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    original_query: str
    rewritten_query: str
    applied: bool
    reason: str = Field(max_length=300)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    simple: bool = False
    origin: RewriteOrigin
    context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def consistent_rewrite(self):
        original = " ".join(self.original_query.split())
        rewritten = " ".join(self.rewritten_query.split())
        if self.applied != (original != rewritten):
            raise ValueError("rewrite_applied_mismatch")
        if self.applied and (not rewritten or len(self.rewritten_query) > 300):
            raise ValueError("rewritten_query_invalid")
        return self
