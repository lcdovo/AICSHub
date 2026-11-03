"""同一轮编排内的确认观察；不替代签名、SQL消息或持久Action资格核验。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class TurnRefundConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    origin: Literal["current_message", "interrupt"]
    context_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
