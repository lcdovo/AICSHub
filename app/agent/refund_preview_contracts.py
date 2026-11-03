"""尚可补参的退款预览；不含权限令牌，完整执行参数仍由领域服务核验。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class RefundPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    order_id: str | None = Field(default=None, min_length=1, max_length=32)
    reason: str | None = Field(default=None, max_length=10000)
    type: Literal["refund", "return", "exchange"] | None = None
    user_id: int | None = Field(default=None, gt=0)
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    action_id: str | None = Field(default=None, min_length=1, max_length=36)
    action_version: int | None = Field(default=None, ge=1)
