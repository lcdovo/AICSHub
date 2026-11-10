"""纯路由策略：主题识别提供候选，本模块统一决定咨询、补参及业务目标。

不访问模型、存储或工具，不把分类结果当作业务授权。有限中文规则不是通用语义解析。
"""
from dataclasses import dataclass
import re
from typing import Literal, Mapping


@dataclass(frozen=True)
class TopicPolicy:
    destination: str
    inquiry_words: tuple[str, ...] = ()
    action_words: tuple[str, ...] = ()
    required_slots: tuple[str, ...] = ()


TOPIC_POLICIES = {
    "query_order": TopicPolicy("tool_execution", required_slots=("order_id",)),
    "refund": TopicPolicy(
        "tool_execution",
        inquiry_words=(
            "流程", "几天", "多久", "怎么退", "如何退", "政策", "规则", "到账", "无理由",
            "质保期", "保修期", "售后", "运费谁出", "能退吗", "可以退", "好退吗", "给退吗",
            "包装", "保留", "区别", "换货", "条件", "要求", "什么样", "需要注意", "规定",
            "原路", "返回", "从哪里退", "退到", "退款方式", "什么时候到", "材料", "需要什么",
        ),
        action_words=(
            "退钱", "帮我退", "给我退", "我要退", "直接退", "马上退", "申请退款",
            "申请退货", "办理退款", "办理退货", "提交退款", "提交退货",
        ),
        required_slots=("order_id", "reason"),
    ),
    "complaint": TopicPolicy(
        "tool_execution",
        inquiry_words=(
            "多久", "几天", "流程", "怎么投诉", "如何投诉", "怎么提交", "在哪里投诉",
            "哪里投诉", "材料", "匿名", "影响", "反馈", "进度", "电话", "入口", "工单",
        ),
        action_words=(
            "我要投诉", "我要举报", "投诉你们", "举报你们", "投诉你", "举报你", "帮我投诉",
            "帮我举报", "提交投诉", "提交举报", "投诉一下", "举报一下", "投诉一个", "举报一个",
            "我要正式投诉", "我要正式举报", "创建投诉", "发起投诉",
        ),
    ),
    "technical": TopicPolicy("query_rewrite"),
    "promotion": TopicPolicy("query_rewrite"),
    "human": TopicPolicy("human_handoff"),
    "general": TopicPolicy("response_generation"),
}

# 统一理解的语义校验与规则路由复用同一否定契约；按主题检查，不否定其他业务。
_NEGATED_ACTIONS = {
    "refund": re.compile(
        r"(?:不要|不想|不需要|别)(?:给我|帮我|现在|立即|直接)?(?:申请|办理|提交)?(?:退款|退货)"
        r"|不是申请|不(?:申请|提交|办理)(?:退款|退货)"
    ),
    "complaint": re.compile(
        r"(?:不要|不想|不需要|别)(?:给我|帮我|现在|立即|直接)?(?:提交|发起|创建|新建)?(?:投诉|举报)"
        r"|不(?:提交|创建|发起)(?:投诉|举报)"
    ),
    "human": re.compile(
        r"(?:不要|不想|不需要|别)(?:给我|帮我|现在|立即|直接)?(?:转|接|联系|找)(?:接)?(?:人工|真人|活人)"
        r"|不要人工|不需要人工"
    ),
}
_CLAUSE_BOUNDARY = re.compile(r"[，。！？；,!?;\n]|但是|不过|但")
_ACTION_QUESTION = re.compile(r"怎么|如何|怎样|哪里|需要|流程|多久|几天|材料|条件|规则|是否|能否|吗")
_CONDITIONAL = re.compile(r"如果|假如|要是|倘若|若|(?:没|未)发货就")
_FOLLOW_UP_PATTERNS = tuple(re.compile(pattern) for pattern in (
    r"^[好嗯哦啊]", r"^(那|然后|接着|还有)",
    r"^(好的?|行(?:吧|的)?|可以(?:的)?|明白(?:了)?|知道了)",
    r"^(谢谢|感谢|多谢)", r"^\d+$", r"^(对|是|没错|正确)",
    r"^(现在|目前|大概|预计|什么时候)", r"^(还是|仍然|依然|重试|重置).*(没用|不行|失败)",
))


def action_is_negated(topic: str, message: str) -> bool:
    pattern = _NEGATED_ACTIONS.get(topic)
    return bool(pattern and pattern.search(message))


def explicit_action(topic: str, message: str) -> bool:
    """有限动作表达检测，仅供冲突保护；不是权限或确认凭据。"""
    if topic == "human":
        words = ("转人工", "转真人", "找客服", "找真人", "联系人工", "联系真人", "我要见人")
        if re.fullmatch(r"(?:人工|真人|人工客服|真人客服|在线客服)[！!。 ]*", message):
            return True
    else:
        profile = TOPIC_POLICIES.get(topic)
        words = profile.action_words if profile else ()
    for clause in _CLAUSE_BOUNDARY.split(message):
        if action_is_negated(topic, clause) or _ACTION_QUESTION.search(clause):
            continue
        if re.search(r"(?:客服|他说|她说|有人|之前).{0,8}(?:说|让|建议)|说过|听说", clause):
            continue
        if any(word in clause for word in words):
            return True
    return False


def technical_inquiry(message: str) -> bool:
    return bool(re.search(r"怎么|如何|怎样|能否|吗|保修", message) and re.search(
        r"连接|维修|安装|设置|配置|使用|保修|配对|开机|充电|指示灯|电池|耳机|蓝牙|故障|排查", message))


def service_scope_query(message: str) -> bool:
    """识别客服能力/服务范围问法，避免把固定能力说明送进业务 RAG。"""
    return bool(re.search(
        r"业务范围|提供哪些服务|能提供什么服务|可以提供什么|可以帮我什么|能帮我什么|你们能做什么|支持哪些服务|有什么功能",
        message,
    ))


def is_follow_up(message: str) -> bool:
    return any(pattern.match(message) for pattern in _FOLLOW_UP_PATTERNS)


def is_conditional(message: str) -> bool:
    return bool(_CONDITIONAL.search(message))


@dataclass(frozen=True)
class RouteDecision:
    next_node: str
    reason: str
    missing_slots: tuple[str, ...] = ()
    reply: str | None = None
    needs_clarification: bool = False


class RoutingPolicy:
    """固定优先级：结构化理解→咨询/条件保护→补参→意图目标。"""

    def decide_structured(
        self, *, topic: str, request_type: str, parameters: Mapping[str, object],
        missing_information: tuple[str, ...] = (),
    ) -> RouteDecision:
        """仅按语义契约和必需参数选择步骤，不读取原话或咨询/动作词表。"""
        if request_type == "inquiry":
            return RouteDecision("rag_retrieval", f"model:{topic}:inquiry->rag")
        if request_type == "cancel":
            return RouteDecision("response_generation", "model:cancel->clarify",
                                 reply="当前没有可核实的待确认操作可取消，请说明要取消的具体事项或申请编号。",
                                 needs_clarification=True)
        if request_type not in {"query", "apply"} or (
            request_type == "apply" and topic not in {"refund", "complaint", "human"}
        ):
            return RouteDecision("response_generation", "model:unsupported->clarify",
                                 reply="请明确咨询对象和希望办理的事项；当前不会自动执行未支持的操作。",
                                 needs_clarification=True)
        if request_type == "query" and topic != "query_order":
            return RouteDecision("response_generation", "model:query->clarify",
                                 reply="请提供申请编号或在申请列表查看进度；本轮不会新建申请。",
                                 needs_clarification=True)
        if "product" in missing_information or "request" in missing_information:
            return RouteDecision("response_generation", "model:missing_context->clarify",
                                 reply="请补充具体产品或型号，以及希望咨询或办理的事项。",
                                 needs_clarification=True)
        declared = tuple(key for key in missing_information if key in {"order_id", "reason"})
        missing = tuple(dict.fromkeys((*self.missing_slots(topic, parameters), *declared)))
        if missing:
            return RouteDecision("slot_filling", f"missing_slots:{','.join(missing)}", missing)
        destination = self.destination(topic)
        return RouteDecision(destination, f"model:{topic}:{request_type}->{destination}")

    def request_kind(self, topic: str, message: str) -> Literal["inquiry", "apply"] | None:
        profile = TOPIC_POLICIES.get(topic)
        if profile is None or not profile.inquiry_words:
            return None
        # 分句识别正向动作，避免“不要投诉，我要退款”跨业务互相否定。
        if explicit_action(topic, message):
            return "apply"
        if (any(word in message for word in profile.inquiry_words)
                or action_is_negated(topic, message)):
            return "inquiry"
        return None

    def missing_slots(self, topic: str, parameters: Mapping[str, object]) -> tuple[str, ...]:
        profile = TOPIC_POLICIES.get(topic)
        if profile is None:
            return ()
        return tuple(slot for slot in profile.required_slots if (
            not parameters.get(slot)
            or (slot == "reason" and len(str(parameters.get(slot) or "").strip()) < 6)
        ))

    def destination(self, topic: str) -> str:
        profile = TOPIC_POLICIES.get(topic)
        return profile.destination if profile else "response_generation"

    def decide(
        self, *, topic: str, message: str, parameters: Mapping[str, object],
        smalltalk: bool = False, request_type: str | None = None,
    ) -> RouteDecision:
        # 模型已验证的请求类型不再被旧关键词重判；确认/权限仍由工具层强制。
        kind = request_type if request_type is not None else self.request_kind(topic, message)
        if kind in {"inquiry", "cancel"}:
            return RouteDecision("query_rewrite", f"{topic}:policy_inquiry->rag")
        if request_type is None and topic == "human" and action_is_negated(topic, message):
            return RouteDecision(
                "response_generation", "human:negated->clarify",
                reply="本轮未创建转人工请求，请说明您希望咨询的具体事项。",
                needs_clarification=True,
            )
        if (request_type is None and topic in {"complaint", "human"}
                and not explicit_action(topic, message)):
            return RouteDecision(
                "response_generation", f"{topic}:intent->clarify",
                reply="您是想咨询处理方式，还是希望现在提交投诉或转人工请求？请明确希望办理的事项。",
                needs_clarification=True,
            )
        if request_type is None and topic in {"refund", "complaint"} and is_conditional(message):
            return RouteDecision(
                "response_generation", f"{topic}:conditional->clarify",
                reply="请先核实前置条件，再明确是否办理；本轮尚未创建申请或执行后续操作。",
                needs_clarification=True,
            )
        missing = self.missing_slots(topic, parameters)
        if missing:
            return RouteDecision("slot_filling", f"missing_slots:{','.join(missing)}", missing)
        if topic == "general":
            if smalltalk:
                return RouteDecision("response_generation", "general:smalltalk->template")
            if service_scope_query(message):
                return RouteDecision("response_generation", "general:service_scope->template")
            return RouteDecision("query_rewrite", "general:rag_fallback")
        destination = self.destination(topic)
        return RouteDecision(destination, f"intent:{topic}->{destination}")


DEFAULT_ROUTING_POLICY = RoutingPolicy()
