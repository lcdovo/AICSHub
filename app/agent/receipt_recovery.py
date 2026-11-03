"""MCP写调用结果未知时仅查询已提交回执，不重新执行写工具。"""
import hashlib

import jwt
from sqlalchemy import select

from app.agent.tools import ToolExecutionResult
from app.config.config import settings
from app.models.models import HandoffRequestRecord, OperationAudit, RefundApplication, Ticket, User
from app.services.ticket_creation import _digest, ticket_receipt, handoff_receipt, handoff_message

WRITES = {"create_ticket", "apply_refund", "update_ticket_status", "escalate_to_human"}
PURPOSE = {"create_ticket": "create_ticket", "apply_refund": "apply_refund",
           "update_ticket_status": "update_ticket_status", "escalate_to_human": "request_handoff"}


async def recover(tool_name: str, arguments: dict) -> ToolExecutionResult | None:
    from app.utils.database import async_session
    try:
        claims = jwt.decode(arguments.get("authorization", ""), settings.AUTH_SECRET_KEY, algorithms=["HS256"],
                            options={"require": ["exp", "sub", "purpose", "request_hash"]})
        if claims["purpose"] != PURPOSE[tool_name]:
            return None
        payload = {k: v for k, v in arguments.items() if k != "authorization"}
        actor_id = claims.get("actor_id", int(claims["sub"]))
        owner_id = payload.get("user_id")
        key = payload.get("idempotency_key")
        if not key or type(actor_id) is not int:
            return None
        if tool_name in {"create_ticket", "escalate_to_human"}:
            operation = PURPOSE[tool_name]
            scoped = "ticket-v1:" + _digest([operation, actor_id, owner_id, key])
            business = {k: v for k, v in payload.items() if k != "idempotency_key"}
            if tool_name == "escalate_to_human":
                business["context"] = {"context": business.get("context")} if business.get("context") else {}
            digest = _digest(business)
        elif tool_name == "apply_refund":
            scoped = "refund-v1:" + hashlib.sha256(f"{actor_id}:{owner_id}:{key}".encode()).hexdigest()
            payload["order_id"] = payload["order_id"].strip().upper()
            payload["reason"] = payload["reason"].strip()
            digest = _digest(payload)
            operation = "apply_refund"
        else:
            scoped = "ticket-update-v1:" + _digest([actor_id, payload["ticket_id"], key])
            digest = _digest({"changes": {"status": payload["status"]}, "note": payload.get("note", "")})
            operation = "update_ticket_status"
        async with async_session() as db:
            actor = await db.get(User, actor_id)
            if actor is None or not actor.status or actor.disabled_at is not None:
                return None
            if (tool_name == "update_ticket_status" or actor_id != owner_id) and actor.role != "admin":
                return None
            audit = await db.scalar(select(OperationAudit).where(OperationAudit.idempotency_key == scoped))
            if (audit is None or audit.operation != operation or audit.status != "succeeded"
                    or (audit.request_payload or {}).get("request_hash") != digest):
                return None
            if tool_name == "create_ticket":
                item = await db.get(Ticket, audit.resource_id)
                if item is None or item.user_id != owner_id:
                    return None
                data = ticket_receipt(item)
                data["message"] = f"工单 {item.id} 已登记，当前状态：{item.status.value}。"
            elif tool_name == "escalate_to_human":
                item = await db.get(HandoffRequestRecord, audit.resource_id)
                if item is None or item.user_id != owner_id:
                    return None
                data = handoff_receipt(item)
                data.update(escalation_id=item.id, message=handoff_message(data))
            elif tool_name == "apply_refund":
                from app.services.refund_application import receipt
                item = await db.get(RefundApplication, audit.resource_id)
                if item is None or item.user_id != owner_id:
                    return None
                data = receipt(item)
            else:
                saved = audit.result_payload
                data = {**saved["ticket"], "old_status": saved["old_status"], "new_status": saved["new_status"],
                        "note": saved["note"]}
            return ToolExecutionResult(success=True, data=data)
    except Exception:
        return None
