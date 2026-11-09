"""任务恢复提示与本轮执行状态分离；模型只能提出下一步，不能提供权限或回执。"""
from __future__ import annotations

import time
from functools import lru_cache
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator, create_model
from app.agent.intent_items import PlannedItem, ItemObservation, ItemVerdict, ItemStatus

TaskKind = Literal["single", "troubleshoot", "after_sales", "multi"]
TaskStatus = Literal["running", "waiting_input", "waiting_confirmation", "completed", "stopped", "cancelled"]
TaskAction = Literal["read", "search_knowledge", "prepare_refund", "ask_user", "finish", "suggest_handoff"]


class TaskRead(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    tool: Literal["query_order", "get_user_history"]
    order_id: str | None = Field(default=None, max_length=32)


class NextStep(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    action: TaskAction
    reads: list[TaskRead] = Field(default_factory=list, max_length=2)
    knowledge_focus: Literal["request", "refund_policy", "troubleshooting"] | None = None
    question: Literal["order_id", "reason", "product", "request", "diagnostic_feedback"] | None = None

    @model_validator(mode="after")
    def action_fields(self):
        if bool(self.reads) != (self.action == "read"):
            raise ValueError("read_fields_require_read_action")
        if (self.knowledge_focus is not None) != (self.action == "search_knowledge"):
            raise ValueError("knowledge_focus_requires_search")
        if (self.question is not None) != (self.action == "ask_user"):
            raise ValueError("question_requires_ask")
        if len({(r.tool, r.order_id) for r in self.reads}) != len(self.reads):
            raise ValueError("duplicate_read")
        if any(r.tool == "get_user_history" and r.order_id is not None for r in self.reads):
            raise ValueError("history_has_no_resource_selector")
        return self


class _DecisionFields(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ReadDecision(_DecisionFields):
    action: Literal["read"]
    reads: list[TaskRead] = Field(min_length=1, max_length=2)


class SearchDecision(_DecisionFields):
    action: Literal["search_knowledge"]
    knowledge_focus: Literal["request", "refund_policy", "troubleshooting"]


class AskDecision(_DecisionFields):
    action: Literal["ask_user"]
    question: Literal["order_id", "reason", "product", "request", "diagnostic_feedback"]


class PrepareRefundDecision(_DecisionFields):
    action: Literal["prepare_refund"]


class FinishDecision(_DecisionFields):
    action: Literal["finish"]


class HandoffSuggestionDecision(_DecisionFields):
    action: Literal["suggest_handoff"]


class TaskDecision(_DecisionFields):
    """Provider contract: each action exposes only the fields it may actually use."""

    step: Annotated[
        ReadDecision | SearchDecision | AskDecision | PrepareRefundDecision | FinishDecision | HandoffSuggestionDecision,
        Field(discriminator="action"),
    ]

    def next_step(self) -> NextStep:
        # Keep the execution/persistence contract and all its cross-field checks.
        return NextStep.model_validate(self.step.model_dump())


@lru_cache(maxsize=64)
def decision_contract(actions: tuple[str, ...]) -> type[TaskDecision]:
    """Expose only presently allowed actions in the provider's actual JSON schema."""
    choices = {"read": ReadDecision, "search_knowledge": SearchDecision, "ask_user": AskDecision,
               "prepare_refund": PrepareRefundDecision, "finish": FinishDecision,
               "suggest_handoff": HandoffSuggestionDecision}
    if not actions or len(set(actions)) != len(actions) or any(action not in choices for action in actions):
        raise ValueError("invalid_decision_actions")
    variants = tuple(choices[action] for action in actions)
    selected = Annotated[Union[variants], Field(discriminator="action")] if len(variants) > 1 else variants[0]
    return create_model("TaskDecision", __base__=TaskDecision, step=(selected, ...))


class TaskNote(BaseModel):
    """有来源的对话记录，不表示当前事实或执行授权。"""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    source: Literal["user_report", "suggested_step"]
    message_id: int | None = None
    turn_digest: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=600)
    recorded_at: str = Field(max_length=40)


class TaskProgress(BaseModel):
    """可持久化进度，无工具事实、知识证据、旧确认或执行许可。"""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    task_id: str = Field(min_length=1, max_length=36)
    session_id: str = Field(min_length=1, max_length=36)
    user_id: int = Field(gt=0)
    actor_id: int = Field(gt=0)
    version: int = Field(default=0, ge=0)
    goal: str = Field(min_length=1, max_length=1000)
    kind: TaskKind
    status: TaskStatus = "running"
    order_id: str | None = Field(default=None, max_length=32)
    product: str | None = Field(default=None, max_length=100)
    # 用户目标约束可跨轮保留；它们不是条件已经满足的事实。
    conditions: list[Literal["order_not_shipped", "order_delivered", "unknown"]] = Field(default_factory=list, max_length=3)
    refund_requested: bool = False
    waiting_for: str | None = Field(default=None, max_length=50)
    stop_reason: str | None = Field(default=None, max_length=80)
    action_id: str | None = Field(default=None, max_length=36)
    notes: list[TaskNote] = Field(default_factory=list, max_length=24)
    requirements: list[Literal["query_order", "get_user_history", "knowledge", "refund_policy",
                               "refund_preview", "diagnostic_resolution"]] = Field(default_factory=list, max_length=6)
    items: list[PlannedItem] = Field(default_factory=list, max_length=4)
    item_outcomes: dict[str, ItemStatus] = Field(default_factory=dict)


class StepRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    sequence: int = Field(ge=1)
    action: TaskAction
    status: Literal["succeeded", "failed", "proposed", "blocked", "unknown", "running"]
    capability: str = Field(max_length=80)
    input_digest: str = Field(max_length=64)
    error_code: str | None = Field(default=None, max_length=80)


class TaskRuntime(BaseModel):
    """每轮清空；不从SQL/Redis恢复旧结果或重复写操作。"""
    model_config = ConfigDict(extra="forbid", strict=True)
    scope_digest: str
    understanding_digest: str
    parameters_digest: str
    task_spec_digest: str
    context_digest: str = ""
    decisions: int = 0
    tool_calls: int = 0
    deadline: float = Field(default_factory=lambda: time.monotonic() + 20)
    seen_inputs: list[str] = Field(default_factory=list)
    steps: list[StepRecord] = Field(default_factory=list)
    next_step: NextStep | None = None
    response_goal: Literal["answer", "diagnostic_feedback"] = "answer"
    last_observed_count: int = 0
    progress_signatures: list[str] = Field(default_factory=list, max_length=6)
    completion_gaps: list[str] = Field(default_factory=list)
    item_observations: dict[str, ItemObservation] = Field(default_factory=dict)
    item_reviews: list[ItemVerdict] = Field(default_factory=list, max_length=4)
