"""可信能力目录与模型观察投影；业务权限仍由各领域服务核验。"""
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OrderObservation(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    order_id: str = Field(min_length=1, max_length=32)
    status: str | None = Field(default=None, max_length=80)
    status_code: str | None = Field(default=None, max_length=50)
    total_amount: str | None = Field(default=None, max_length=32)
    shipping: dict | None = None
    created_at: str | None = Field(default=None, max_length=50)


class HistoryObservation(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    history: list[dict]


class TicketObservation(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    ticket_id: str = Field(min_length=1, max_length=100)
    status: str | None = None
    user_id: int | None = None


class RefundObservation(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    refund_id: str = Field(min_length=1, max_length=100)
    order_id: str | None = None
    amount: str | None = None
    status: str | None = None
    is_simulated: bool | None = None


class HandoffObservation(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    request_id: str = Field(min_length=1, max_length=100)
    status: str | None = None
    user_id: int | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class CapabilitySpec:
    effect: Literal["read", "write"]
    attempts: int = 1
    timeout_seconds: float = 5.
    receipt_key: str | None = None
    output: type[BaseModel] | None = None


CAPABILITIES = {
    "query_order": CapabilitySpec("read", attempts=3, output=OrderObservation),
    "get_user_history": CapabilitySpec("read", attempts=3, output=HistoryObservation),
    "search_kb": CapabilitySpec("read", attempts=3),
    "create_ticket": CapabilitySpec("write", receipt_key="ticket_id", output=TicketObservation),
    "apply_refund": CapabilitySpec("write", receipt_key="refund_id", output=RefundObservation),
    "update_ticket_status": CapabilitySpec("write", receipt_key="ticket_id", output=TicketObservation),
    "escalate_to_human": CapabilitySpec("write", receipt_key="request_id", output=HandoffObservation),
    "send_notification": CapabilitySpec("write", receipt_key="notification_id"),
}


def output_issue(name: str, data: object, arguments: dict) -> str | None:
    spec = CAPABILITIES[name]
    if not isinstance(data, dict):
        return "invalid_tool_output"
    try:
        if spec.output:
            spec.output.model_validate(data)
        if spec.receipt_key and (not isinstance(data.get(spec.receipt_key), str) or not data[spec.receipt_key]):
            return "missing_business_receipt"
        if name == "query_order" and arguments.get("order_id") and (
                data["order_id"].upper() != arguments["order_id"].strip().upper()):
            return "tool_resource_mismatch"
        for key in ("user_id", "session_id", "order_id", "ticket_id"):
            if key in data and key in arguments and data[key] != arguments[key]:
                if key == "order_id" and isinstance(data[key], str) and isinstance(arguments[key], str) and (
                        data[key].upper() == arguments[key].strip().upper()):
                    continue
                return "tool_resource_mismatch"
    except (ValueError, TypeError, KeyError, AttributeError):
        return "invalid_tool_output"
    return None


def project_observation(name: str, result: dict) -> dict:
    """按字段缩减数据，不能把截断JSON当作完整观察；不向规划模型发送地址/授权。"""
    observation = {"capability": name, "success": result.get("success") is True,
                   "error_code": result.get("error_type"), "call_id": result.get("call_id"),
                   "observed_at": result.get("observed_at")}
    if not observation["success"]:
        return observation
    data = result.get("data")
    try:
        if name == "query_order":
            order = OrderObservation.model_validate(data).model_dump(exclude_none=True)
            # Arbitrary carrier payloads can include personal data and free text.
            shipping = order.pop("shipping", None)
            if isinstance(shipping, dict):
                order["shipping"] = {key: str(shipping[key])[:120] for key in (
                    "status", "carrier", "tracking_number") if key in shipping}
            observation["data"] = order
        elif name == "get_user_history":
            history = HistoryObservation.model_validate(data).history
            observation["data"] = {"count": len(history), "source": "historical_conversation",
                "items": [{"message_id": row.get("message_id"),
                           "role": row.get("role", row.get("type", "unknown")),
                           "summary": str(row.get("summary", row.get("content", "")))[:200]}
                          for row in history[:3]]}
        else:
            spec = CAPABILITIES.get(name)
            observation["data"] = ({spec.receipt_key: data[spec.receipt_key]}
                if spec and spec.receipt_key and isinstance(data, dict) and spec.receipt_key in data else {})
    except (ValueError, TypeError):
        observation.update(success=False, error_code="invalid_tool_output")
    return observation
