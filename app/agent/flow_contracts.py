"""本轮流程状态；None表示该阶段尚无结论，不与False混用。"""

from enum import Enum
from pydantic import BaseModel, ConfigDict


class FlowFlag(str, Enum):
    SMALLTALK = "smalltalk"
    GROUNDED = "rag_grounded"
    REFUSED = "rag_refused"
    VALIDATED = "answer_validated"
    CACHE_HIT = "answer_from_cache"
    HANDOFF_ATTEMPTED = "handoff_attempted"


class TurnFlow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    smalltalk: bool | None = None
    rag_grounded: bool | None = None
    rag_refused: bool | None = None
    answer_validated: bool | None = None
    answer_from_cache: bool | None = None
    handoff_attempted: bool | None = None
