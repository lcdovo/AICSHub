"""可尚未补齐的本轮业务参数；不含Principal、确认或持久动作授权。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class BusinessParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    # 已有OrderReadInput/RefundToolInput限制；空值表示待补齐，不在这里新增资格规则。
    order_id: str | None = Field(default=None, min_length=1, max_length=32)
    reason: str | None = Field(default=None, max_length=10000)
    refund_type: Literal["refund", "return", "exchange"] | None = None
    complaint_content: str | None = Field(default=None, max_length=10000)
