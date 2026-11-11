"""领域动作回执的展示快照；显示核验不替代SQL资格、确认和幂等。"""

from app.utils.utc_time import utc_now
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.agent.refund_preview_contracts import RefundPreview


class RefundActionView(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    action_id: str = Field(min_length=1, max_length=36)
    version: int = Field(ge=1)
    order_id: str = Field(min_length=1, max_length=32)
    type: Literal["refund", "return", "exchange"]
    reason: str = Field(min_length=3, max_length=10000)
    amount: str = Field(pattern=r"^[0-9]+\.[0-9]{2}$", max_length=13)
    currency: Literal["CNY"]
    status: Literal["pending", "submitted", "cancelled"]
    expires_at: str = Field(max_length=64)
    application_id: str | None = Field(default=None, max_length=36)
    is_simulated: bool
    preconditions: list[Literal["order_not_shipped", "order_delivered"]] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def valid_display(self):
        amount = Decimal(self.amount)
        if not amount.is_finite() or not Decimal(0) < amount < Decimal("10000000000"):
            raise ValueError("invalid_action_amount")
        if not self.is_simulated:
            raise ValueError("real_payment_not_supported")
        self.expiry()
        return self

    def expiry(self) -> datetime:
        value = datetime.fromisoformat(self.expires_at)
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    def is_pending(self) -> bool:
        return self.status == "pending" and self.expiry() > utc_now()

    def matches(self, preview: RefundPreview | None) -> bool:
        return bool(preview and self.action_id == preview.action_id and self.version == preview.action_version
                    and self.order_id == preview.order_id and self.type == preview.type and self.reason == preview.reason)
