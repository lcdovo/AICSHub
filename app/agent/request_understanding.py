"""模型请求理解类型与节点依赖：输出仅描述意图，不能携带权限/工具代码。"""

from __future__ import annotations
import json
import re
from copy import deepcopy
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, create_model
from app.config.config import settings
from app.agent.read_plan import ReadTask
from app.agent.business_contracts import BusinessParameters
from app.agent.routing_policy import action_is_negated, DEFAULT_ROUTING_POLICY, is_conditional, technical_inquiry, explicit_action
from app.agent.request_context import RequestContext, build_request_context, NEW_ORDER_PATTERN, order_mentions
from app.agent.request_events import is_deterministic_smalltalk

Topic = Literal[
    "query_order", "refund", "complaint", "technical", "promotion", "human", "general"
]
RequestType = Literal[
    "inquiry",
    "query",
    "apply",
    "change",
    "cancel",
    "clarify",
    "out_of_scope",
    "greeting",
]

SHADOW_NOTICE = "影子理解已记录，本轮不执行申请、确认或取消。"


class Entities(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    order_id: str | None = Field(default=None, max_length=40)
    product: str | None = Field(default=None, max_length=100)

    @field_validator("order_id", "product", mode="before")
    @classmethod
    def normalize_null_encoding(cls, value):
        return None if value in ("null", "None", "") else value


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    check: Literal["order_not_shipped", "order_delivered", "unknown"]
    source: Literal["current_message", "history"]


class Subrequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    topic: Topic
    request_type: RequestType


class UnderstandingParameters(BaseModel):
    """模型仅提取业务内容；身份、订单选择和确认均不在此契约内。"""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    reason: str | None = Field(default=None, max_length=1000)
    refund_type: Literal["refund", "return", "exchange"] | None = None
    complaint_content: str | None = Field(default=None, max_length=1000)


class RequestUnderstanding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    topic: Topic
    request_type: RequestType
    entities: Entities
    conditions: list[Condition] = Field(max_length=3)
    subrequests: list[Subrequest] = Field(max_length=3)
    service_preference: Literal["bot", "human", "unspecified"]
    emotion: Literal["neutral", "positive", "negative", "angry"]
    clarification: str | None = Field(max_length=180)
    retrieval_query: str = Field(min_length=1, max_length=300)
    entity_source: Literal["current_message", "history", "none"]
    read_tasks: list[ReadTask] = Field(default_factory=list, max_length=2)
    parameters: UnderstandingParameters = Field(default_factory=UnderstandingParameters)
    missing_information: list[Literal["order_id", "reason", "product", "request"]] = Field(default_factory=list, max_length=4)

    @field_validator("clarification", mode="before")
    @classmethod
    def normalize_empty_clarification(cls, value):
        return None if value in ("null", "None", "") else value


class ConversationContext(BaseModel):
    messages: list[dict] = Field(default_factory=list)
    pending_action_id: str | None = None
    known_order_id: str | None = None


class TaskRequestUnderstanding(RequestUnderstanding):
    """仅新任务循环启用时扩展理解；关闭时保持原schema及提示。"""
    task_kind: Literal["single", "troubleshoot", "after_sales", "multi"] = "single"
    continue_task: bool = False


from app.agent.intent_items import IntentItem


class ItemReadTask(ReadTask):
    tool: Literal["query_order","get_user_history"] = Field(description=(
        "query_order需要已知订单号；get_user_history仅用于用户明确要求回看聊天历史，不能用它代替缺失的订单号。缺订单号且未要求聊天历史时read_tasks必须为空。"))


class ItemizedTaskUnderstanding(TaskRequestUnderstanding):
    itemization_version: Literal["intent-items.v1"]
    items: list[IntentItem] = Field(min_length=1,max_length=4,
        description="先逐项列出1至4个独立诉求；同一订单的不同问题也分别列项，不能按topic或工具去重。单一诉求也必须填一项。")
    read_tasks: list[ItemReadTask] = Field(default_factory=list,max_length=2)

    @model_validator(mode="after")
    def derive_task_kind(self):
        if len(self.items)>1:
            self.task_kind="multi"
        elif self.items[0].kind=="troubleshoot":
            self.task_kind="troubleshoot"
        elif self.items[0].kind=="prepare_refund" or self.conditions and self.request_type=="apply":
            self.task_kind="after_sales"
        else:
            self.task_kind="single"
        return self

    @classmethod
    def __get_pydantic_json_schema__(cls,core_schema,handler):
        schema=handler.resolve_ref_schema(handler(core_schema))
        # Retain the persisted field for compatibility; the provider cannot choose
        # whether a request receives per-item checks.
        schema.get("properties",{}).pop("task_kind",None)
        if "required" in schema:
            schema["required"]=[key for key in schema["required"] if key!="task_kind"]
        return schema


ItemizedTaskUnderstandingWire = create_model(
    "ItemizedTaskUnderstandingWire", __config__=ConfigDict(extra="forbid", strict=True),
    **{name: (field.annotation, deepcopy(field)) for name, field in ItemizedTaskUnderstanding.model_fields.items()
       if name not in {"task_kind", "read_tasks"}},
)


def decode_itemized_understanding(value) -> ItemizedTaskUnderstanding:
    """Strict wire decode, then server-owned reads; never discard an old provider plan."""
    wire = ItemizedTaskUnderstandingWire.model_validate(value)
    reads = []
    for item in wire.items:
        if item.kind == "query_order" and wire.entities.order_id:
            reads.append({"tool": "query_order", "resource_id": wire.entities.order_id})
        elif item.kind == "get_user_history":
            reads.append({"tool": "get_user_history", "resource_id": None})
    if wire.conditions and reads and wire.request_type == "apply" and wire.entities.order_id:
        reads.insert(0, {"tool": "query_order", "resource_id": wire.entities.order_id})
    reads = list({(item["tool"], item["resource_id"]): item for item in reads}.values())
    return ItemizedTaskUnderstanding.model_validate({**wire.model_dump(), "read_tasks": reads})


ITEM_UNDERSTANDING_PROMPT = """

itemization_version固定intent-items.v1。先提取items，再由程序推导task_kind，不输出task_kind。所有非寒暄请求都必须填1至4项，单一诉求填一项，不能留空。

逐项保留独立问题，不按topic/kind或工具调用次数归并。同一订单的不同字段、同类政策的不同问题也分别列项，不能把状态和金额、包装和运费合成一项。

每项text必须逐字摘取当前消息的一段原话；承接旧任务时保持原items的text/kind及顺序，不删除尚未回答的项。

kind仅query_order/get_user_history/knowledge/troubleshoot/prepare_refund/unsupported。政策和方法问题是knowledge；设备故障需要反馈验证为troubleshoot；不能误标prepare_refund；

prepare_refund表示仅退款、退货退款或换货的明确模拟申请预览意向（分别提取refund_type=refund/return/exchange），不是授权。历史读取须列为get_user_history事项。其他写入标unsupported，不能合并自动执行。

items不得重复整个问题充数；原确认、否定和条件规则不变。显式查询订单与条件退款分别保留query_order和prepare_refund，不能因含“如果”清空items。

不输出read_tasks：读取计划仅由程序根据已验证items、conditions和已知订单推导。单项无条件退款申请只填写prepare_refund事项，程序仍会先读取订单、核验权限与资格。显式查单/回看历史才列query_order/get_user_history事项，不得为了内部前置读取虚构条件或增加用户没有提出的查询。

resume_hint只是旧进度，不证明事实或确认仍有效。仅用户明确承接时continue_task=true；补原因保留原申请和条件，独立问题false。

多诉求只有某项缺参数时仍保留该项query/apply类型与其余items，在missing_information记录缺项；不把所有事项改成clarify。

缺少订单号只记录missing_information=[order_id]，其余知识项继续保留；用户没有明确要求回看聊天记录时，禁止用get_user_history猜测订单号或订单状态。

"""


TASK_UNDERSTANDING_PROMPT = """
task_kind：单一查询或政策/使用方法咨询为single；设备出现故障需逐步验证为troubleshoot；
条件售后或先查后办理为after_sales；两个以上需关联处理的诉求为multi。
resume_hint只是上一任务的进度，不证明旧事实、资格或确认仍有效。
仅本轮明确承接该任务时continue_task=true，并结合背景补全对象；独立问题false。
原任务是售后申请且本轮补原因时保留refund/apply意向和原条件，不能当成一般咨询。
咨询与订单查询组合时，主topic/request_type可保留第一个咨询；subrequests同时列出咨询与query_order/query，
task_kind=multi，read_tasks只允许该已明确订单的query_order。普通单一咨询仍不提供read_tasks。
"""


class ActionIntent(BaseModel):
    topic: Topic
    request_type: RequestType
    requires_fresh_confirmation: bool = True
    executable: bool = False


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    knowledge_epoch: int | None = Field(default=None, ge=0)
    items: list[dict] = Field(default_factory=list)
    support: Literal["supported", "partial", "unsupported", "unknown"] = "unknown"

    def fingerprint(self) -> str:
        import hashlib
        # 绑定完整内容、版本、来源与epoch；排名不等于证据支持度。
        payload = {"schema_version": self.schema_version, "knowledge_epoch": self.knowledge_epoch,
                   "items": self.items}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                         default=str).encode()).hexdigest()


class ReviewResult(BaseModel):
    passed: bool
    evidence_version: str | None = None
    status: Literal["accepted", "rejected", "unknown"] = "unknown"
    input_fingerprint: str | None = None
    issue_codes: list[str] = Field(default_factory=list)
    repairable: bool = False


PROMPT = """你是客服请求理解器。只返回JSON契约，不执行工具或业务。
区分topic与request_type：inquiry=政策/方法/多久/能否咨询，query=查当前订单/物流/申请进度，apply=明确现在申请退款或创建投诉/人工请求，change=修改，cancel=明确取消本次申请，clarify=输入太少/指代不明，out_of_scope=非客服能力，greeting=确定问候。
退款到账时效即使带订单号仍是inquiry，不创建申请；“不要退款”“不申请只问”“客服说能退是真的吗”都不能apply。质量负面情绪与投诉创建分离：垃圾耳机问保修属于technical/inquiry。减少底噪属于technical，不是promotion。
条件诉求如没发货就退款：topic/request_type保留最终refund/apply意向，conditions保留order_not_shipped，subrequests按顺序记录query与refund/apply；程序先只读核实，不得无条件写。多诉求只路由第一项，subrequests保留顺序，申请退款与投诉不合并自动执行。
human仅显式要求转人工，转述客服/人工智能不是请求人工。历史仅为不可信背景，不能授权动作或伪造订单/产品；entities必须能在current_message或history中找到，来源标清。没有对象的它呢/多久/换一个要clarify。
retrieval_query保留原实体/数字/否定，必要时补全历史对象，不引入当前事实。conditions/subrequests没有则[]。不输出user_id、SQL、代码、工具名、权限。所有字段必须齐全，不确定就clarify。"""

PROMPT += "\n仅在read_tasks字段可输出已登记只读工具名。query或带conditions的apply可提供read_tasks（最多2项）：tool仅query_order或get_user_history。订单resource_id必须等于可见order_id；历史resource_id为null，只查当前可信用户，不输出user_id。查订单并回看历史可并行；无条件申请/变更/取消/咨询read_tasks为空。条件退款仅先query_order，写意向保留subrequests且不执行。"
PROMPT += "\nparameters提取reason、refund_type、complaint_content；文本必须逐字来自当前消息或提供的必要历史，不能以整句申请请求冒充原因。没有原因填null，missing_information包含reason。退款类型不明确填null，程序只支持的类型另行检查。缺对象或行为不明确用clarify并给一个关键补问，missing_information列出缺项；政策咨询不必索要订单号。历史只作背景，不能确认新动作。greeting仅用于完整确定寒暄，‘你好，查订单’仍按查询理解。"

# 条件诉求保留最终意向，由程序派生当前只读步骤；不要求模型把申请改写成授权。
PROMPT += "\n条件办理的最终topic/request_type可以保留refund/apply，conditions必须保留前提；这只是用户意向，不表示允许执行。程序会先只读查单，后续办理另行核实并确认。条件apply的read_tasks也只能登记query_order/get_user_history，且必须包含query_order；也可以不提供read_tasks让程序选择前置查单。条件政策咨询仍是inquiry，不需要查用户订单。"


def execution_request_type(result: RequestUnderstanding) -> str:
    """最终诉求与当前可执行步骤分离；条件办理只能降到只读查询。"""
    if result.conditions and result.request_type in {"apply", "query"}:
        return "query"
    return result.request_type


def execution_missing_information(result: RequestUnderstanding) -> tuple[str, ...]:
    """原因属于后续申请；不能阻止当前前置只读查询，也不能因此补造原因。"""
    return tuple(item for item in result.missing_information if not (
        result.conditions and execution_request_type(result) == "query" and item == "reason"))


def input_payload(state) -> str:
    from app.agent.request_context import stage_context
    payload = stage_context(state)
    from app.agent.task_loop import enabled
    if enabled(state) and state.task and state.task.status == "waiting_input" and payload["history"]:
        payload["resume_hint"] = {"goal": state.task.goal, "kind": state.task.kind,
            "order_id": state.task.order_id, "product": state.task.product,
            "conditions": state.task.conditions, "refund_requested": state.task.refund_requested,
            "waiting_for": state.task.waiting_for, "trusted_as_evidence": False}
    from app.agent.context_budget import fit_context, UNDERSTANDING_PAYLOAD_BYTES
    return json.dumps(fit_context(payload, max_bytes=UNDERSTANDING_PAYLOAD_BYTES), ensure_ascii=False)


def validate_semantics(result, state, context: RequestContext | None = None):
    # 校验与供应商实际输入共用同一有界快照；未发送的旧消息不是本轮证据。
    context = context or state.understanding_context or build_request_context(state)
    if not context.matches(state):
        raise ValueError("understanding_context_changed")
    context = context.payload()
    text = context["current_message"]
    history = " ".join(
        m["content"] for m in context["history"]
    )
    for value in (result.parameters.reason, result.parameters.complaint_content):
        if value and value not in text and value not in history:
            raise ValueError("unobserved_business_parameter")
    if result.request_type == "greeting" and not is_deterministic_smalltalk(text):
        raise ValueError("business_request_is_not_greeting")
    values = result.entities.model_dump()
    if result.entities.order_id is not None:
        BusinessParameters(order_id=result.entities.order_id)

    def in_context(field, value):
        # 已知订单单独作为结构化背景发送，只能证明订单号，不能证明产品名。
        return value in history or (field == "order_id" and value == context["known_order"])

    for field, value in values.items():
        if value and value not in text and not in_context(field, value):
            raise ValueError("unobserved_entity")
    if result.entity_source == "current_message" and any(
        v and v not in text for v in values.values()
    ):
        raise ValueError("incorrect_entity_source")
    if result.entity_source == "history" and any(
        v and not in_context(field, v) for field, v in values.items()
    ):
        raise ValueError("incorrect_entity_source")
    if result.entity_source == "none" and any(values.values()):
        raise ValueError("incorrect_entity_source")
    if result.request_type == "apply" and action_is_negated(result.topic, text):
        raise ValueError("negated_action")
    if (result.entities.order_id and NEW_ORDER_PATTERN.search(text)
            and result.entities.order_id not in order_mentions(text)):
        raise ValueError("new_order_requires_current_entity")
    if (result.request_type == "apply" and re.search(r"怎么|如何|多久|几天|需要什么|能否|吗", text)
            and DEFAULT_ROUTING_POLICY.request_kind(result.topic, text) == "inquiry"):
        raise ValueError("inquiry_cannot_become_action")
    if result.request_type == "apply" and is_conditional(text) and not result.conditions:
        raise ValueError("conditional_write_without_read")
    if (result.request_type == "apply" and technical_inquiry(text)
            and result.topic in {"refund", "complaint", "human"} and not explicit_action(result.topic, text)):
        raise ValueError("technical_inquiry_cannot_become_action")
    if result.read_tasks:
        from app.agent.task_loop import enabled

        mixed_order_read = (
            isinstance(result, TaskRequestUnderstanding) and enabled(state)
            and result.task_kind == "multi" and result.request_type == "inquiry"
            and result.topic in {"technical", "refund"}
            and any(item.topic == result.topic and item.request_type == "inquiry" for item in result.subrequests)
            and any(item.topic == "query_order" and item.request_type == "query" for item in result.subrequests)
            and all(item.tool == "query_order" for item in result.read_tasks)
        )
        if (isinstance(result, ItemizedTaskUnderstanding) and result.task_kind == "multi"
                and any(item.kind in {"query_order", "get_user_history"} for item in result.items)):
            mixed_order_read = True
        if execution_request_type(result) != "query" and not mixed_order_read:
            raise ValueError("read_plan_requires_query")
        if len({(t.tool, t.resource_id) for t in result.read_tasks}) != len(result.read_tasks):
            raise ValueError("duplicate_read_task")
        for task in result.read_tasks:
            if task.tool == "query_order" and (
                not task.resource_id or task.resource_id != result.entities.order_id
            ):
                raise ValueError("read_plan_order_mismatch")
            if task.tool == "get_user_history" and task.resource_id is not None:
                raise ValueError("read_plan_history_resource_forbidden")
        if result.conditions and not any(t.tool == "query_order" for t in result.read_tasks):
            raise ValueError("conditional_order_read_required")
    if isinstance(result, ItemizedTaskUnderstanding):
        from app.agent.intent_items import validate_plan
        validate_plan(state, result)
    return result


async def understand(service, state):
    context = build_request_context(state)
    from app.agent.task_loop import enabled
    task_enabled = enabled(state)
    itemized = task_enabled and settings.AGENT_ITEM_COMPLETION_ENABLED
    schema = ItemizedTaskUnderstandingWire if itemized else (TaskRequestUnderstanding if task_enabled else RequestUnderstanding)
    base_prompt=PROMPT
    if itemized:
        # The legacy first-intent routing rule conflicts with per-item execution.
        base_prompt="\n".join(line for line in PROMPT.splitlines()
                              if not line.startswith("条件诉求如没发货就退款：") and "read_tasks" not in line)
        base_prompt+="\n条件申请保留最终意向和conditions；subrequests只登记用户的独立诉求，内部前置读不增加用户目标；条件、权限及本轮确认仍由程序核验。"
    payload = input_payload(state)
    # Validate only the history actually sent after shared budget selection.
    from app.agent.request_context import HistoryMessage
    context = context.model_copy(update={"history": tuple(
        HistoryMessage.model_validate(item) for item in json.loads(payload)["history"])})
    for attempt in range(1 if task_enabled else 2):
        try:
            result = await service.run_structured(
                base_prompt + (TASK_UNDERSTANDING_PROMPT if task_enabled and not itemized else "") + (ITEM_UNDERSTANDING_PROMPT if itemized else "") + (" 上次输出无效；严格重建完整结构。" if attempt else ""),
                payload,
                schema,
                tags=["request_understanding"],
                prefer_local=False,
                max_tokens=settings.LLM_UNDERSTANDING_MAX_TOKENS,
                **({"single_attempt": True} if task_enabled else {}),
            )
        except Exception:
            # Provider异常不增加修复请求；CancelledError仍向上传播。
            return None
        if result:
            try:
                parsed = validate_semantics(decode_itemized_understanding(result) if itemized else schema.model_validate(result), state, context)
                state.understanding_context = context
                return parsed
            except ValueError:
                state.collected_info["understanding_failure"] = {"code": "understanding_semantics_invalid"}
        else:
            metadata = service.get_last_call_metadata() if callable(getattr(service, "get_last_call_metadata", None)) else {}
            code = metadata.get("failure_code") or "understanding_unavailable"
            state.collected_info["understanding_failure"] = {"code": code}
        if not service.is_available():
            break
    return None


def apply_understood_parameters(state, result) -> None:
    """选择已理解参数，不重新regex解析；不可见/换对象的旧原因不继承。"""
    context = state.understanding_context or build_request_context(state)
    old = state.current_business_parameters()
    history = "\n".join(item.content for item in context.history)
    same_order = result.entities.order_id is not None and result.entities.order_id == old.order_id
    reason = result.parameters.reason
    if reason is None and old.reason and (old.reason in context.current_message or (same_order and old.reason in history)):
        reason = old.reason
    state.set_business_parameters(
        order_id=result.entities.order_id, reason=reason,
        refund_type=result.parameters.refund_type,
        complaint_content=result.parameters.complaint_content,
    )
    # 兼容未显式提取投诉正文的旧schema：当前已理解apply的原话可作为投诉内容，
    # 但不能当作退款原因、权限、确认或执行事实。
    if result.topic == "complaint" and result.request_type == "apply" and not result.parameters.complaint_content:
        state.set_business_parameters(complaint_content=state.user_message)


def route_understanding(state, result):
    from app.agent.state import AgentNode

    state.understanding_context = state.understanding_context or build_request_context(state)
    if not state.understanding_context.matches(state):
        state.set_workflow_reply("本轮理解的消息或身份已变化，请重新说明请求。")
        state.needs_clarification = True
        return AgentNode.RESPONSE_GENERATION
    state.needs_clarification = False
    state.missing_slots = []
    state.action_intent = ActionIntent(
        topic=result.topic, request_type=result.request_type
    )
    state.understanding = result
    state.detected_intent = result.topic
    state.intent_confidence = 0.0
    state.user_emotion = result.emotion
    state.collected_info["intent_source"] = "model_understanding"
    state.set_flow(smalltalk=result.request_type == "greeting")
    apply_understood_parameters(state, result)
    prerequisite_read = bool(result.conditions and result.request_type in {"query", "apply"})
    if prerequisite_read and (not result.entities.order_id or "order_id" in result.missing_information):
        state.set_workflow_reply("请明确要核实的订单号；我会先查询前置条件，后续办理仍需单独确认。")
        state.needs_clarification = True
        state.missing_slots = ["order_id"]
        return AgentNode.RESPONSE_GENERATION
    defer_final_reason = prerequisite_read and set(result.missing_information) == {"reason"}
    readonly_change = (
        isinstance(result, ItemizedTaskUnderstanding) and result.request_type == "change"
        and result.topic == "query_order" and bool(result.read_tasks)
        and all(read.tool == "query_order" for read in result.read_tasks)
        and any(item.kind == "query_order" for item in result.items)
        and all(item.kind == "query_order" or (
            item.kind == "unsupported" and action_is_negated("refund", item.text)
        ) for item in result.items)
        and any(item.topic == "query_order" and item.request_type == "query" for item in result.subrequests)
        and all(item.topic == "query_order" and item.request_type == "query" for item in result.subrequests)
    )
    if (
        (result.request_type in ["clarify", "out_of_scope", "change"] and not readonly_change)
        or (result.clarification and not defer_final_reason)
        or any(item in result.missing_information for item in ("product", "request"))
    ):
        if result.clarification:
            state.reply = result.clarification
        else:
            state.set_workflow_reply("请补充具体对象和希望办理的业务；当前不自动执行未支持的变更。")
        state.needs_clarification = True
        return AgentNode.RESPONSE_GENERATION
    if result.request_type == "greeting":
        return AgentNode.RESPONSE_GENERATION
    if result.read_tasks:
        state.read_plan = list(result.read_tasks)
        return AgentNode.TOOL_EXECUTION
    if prerequisite_read:
        state.detected_intent = "query_order"
        # 原始最终诉求保留在ActionIntent，执行主题单独限定为query_order。
        final_goal = next((item for item in result.subrequests if item.request_type == "apply"), None)
        if final_goal is not None:
            state.action_intent = ActionIntent(topic=final_goal.topic, request_type="apply", executable=False)
        if not result.entities.order_id:
            state.set_workflow_reply("请提供订单号；我会先查订单状态，后续操作仍需单独确认。")
            state.needs_clarification = True
            state.missing_slots = ["order_id"]
            return AgentNode.RESPONSE_GENERATION
        return AgentNode.ROUTE_DECISION
    if result.request_type == "inquiry":
        retrieval_query = result.retrieval_query
        if result.topic == "technical":
            extras = ["排障步骤"]
            if "耳机" in state.user_message:
                extras.append("无线蓝牙耳机")
            if "充电" in state.user_message or "指示灯" in state.user_message:
                extras.append("充电 指示灯")
            if "蓝牙" in state.user_message or "连接" in state.user_message:
                extras.append("蓝牙 配对 连接")
            retrieval_query = f"{retrieval_query} {' '.join(extras)}"[:300]
        if result.topic == "refund":
            # 泛化的退货政策问题需要命中完整的申请条件条款；仅使用
            # “退货/条件”等原词时，重排容易把自动审核或换货片段排在前面。
            # 这里仅扩展检索词，不改变用户原意、意图或执行权限。
            refund_terms = ("退货", "退换货", "无理由", "条件", "要求", "满足", "资格")
            if any(term in state.user_message for term in refund_terms):
                packaging_question = any(term in state.user_message for term in ("包装", "保留", "准备", "配件", "赠品", "发票"))
                if packaging_question:
                    # 包装要求是独立章节；使用紧凑查询，避免加入条件/例外条款后稀释精确匹配。
                    retrieval_query = (
                        f"{retrieval_query} 退货时建议保留原包装 原包装损坏 厚实纸箱 商品 配件 赠品 发票"
                    )[:300]
                else:
                    if "换货" in state.user_message:
                        extras = [
                            "换货",
                            "质量问题",
                            "签收后15天",
                            "同型号商品",
                            "平台审核",
                            "寄回原商品",
                        ]
                    else:
                        extras = [
                            "7天无理由退货",
                            "申请条件",
                            "商品完好",
                            "配件 赠品 发票",
                            "不适用商品",
                        ]
                    retrieval_query = f"{retrieval_query} {' '.join(extras)}"[:300]
        if result.topic == "general":
            from app.agent.routing_policy import service_scope_query
            if service_scope_query(state.user_message):
                state.set_workflow_reply(
                    "我可以帮助您查询订单状态和物流、咨询退换货与售后政策、排查产品使用问题、了解优惠活动，也可以为您转接人工客服。"
                )
                return AgentNode.RESPONSE_GENERATION
        state.record_query_rewrite({
            "original_query": state.user_message, "rewritten_query": retrieval_query,
            "applied": retrieval_query != state.user_message,
            "reason": "unified_understanding", "confidence": 0., "simple": False,
        }, "unified_understanding")
        return AgentNode.RAG_RETRIEVAL
    if result.request_type == "cancel" or (result.request_type == "query" and result.topic != "query_order") or (
        result.request_type == "apply" and result.topic not in {"refund", "complaint", "human"}
    ):
        decision = DEFAULT_ROUTING_POLICY.decide_structured(
            topic=result.topic, request_type=result.request_type,
            parameters=state.current_business_parameters().model_dump(exclude_none=True))
        state.set_workflow_reply(decision.reply)
        state.needs_clarification = True
        state.route_reason = decision.reason
        return AgentNode.RESPONSE_GENERATION
    if result.topic == "human" and result.request_type == "apply":
        state.need_human = True
        state.human_reason = "用户本轮明确请求人工"
    return AgentNode.ROUTE_DECISION
