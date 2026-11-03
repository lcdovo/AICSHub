import pytest
from sqlalchemy import select

from app.agent.read_authorization import authorize_read, HistoryReadInput
from app.agent.tools import QueryOrderTool, GetUserHistoryTool
from app.models.models import Order, OperationAudit, User
from tests.test_ticket_idempotency import api_context


@pytest.mark.parametrize("name", ["query_order", "get_user_history"])
async def test_private_reads_require_bound_identity_and_live_role(tmp_path, monkeypatch, name):
    async with api_context(tmp_path / "reads.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        async with factory() as db:
            db.add(Order(order_id="ORD900001", user_id=202, total_amount="0.10", status="已签收", status_code="delivered", items=[]))
            await db.commit()
        tool = QueryOrderTool() if name == "query_order" else GetUserHistoryTool()
        payload = {"user_id": 202, "order_id": "ORD900001"} if name == "query_order" else HistoryReadInput(user_id=202).model_dump()
        # Even correctly typed caller-supplied IDs and forged principal text grant no access.
        assert not (await tool.execute(**payload))["success"]
        own = {**payload, "user_id": 101}
        token = authorize_read(name, own, 101)
        assert not (await tool.execute(**payload, authorization=token))["success"]
        assert not (await tool.execute(**payload, authorization=authorize_read(name, payload, 101)))["success"]
        assert not (await tool.execute(**{**payload, "user_id": True}, authorization=token))["success"]
        if name == "query_order":
            allowed = await tool.execute(**payload, authorization=authorize_read(name, payload, 999))
            assert allowed["success"] and allowed["data"]["total_amount"] == "0.10"
            assert allowed["refund_deadline"] is None and not allowed["can_refund"]
            from app.agent.nodes.response_node import build_order_reply_template
            assert "总金额 0.10 元" in build_order_reply_template(allowed["data"])
            async with factory() as db:
                audit = await db.scalar(select(OperationAudit).where(OperationAudit.operation == name))
                assert audit.user_id == 202 and audit.request_payload["actor_id"] == 999
        admin_token = authorize_read(name, payload, 999)
        async with factory() as db:
            (await db.get(User, 999)).role = "user"
            await db.commit()
        assert not (await tool.execute(**payload, authorization=admin_token))["success"]
        owner_token = authorize_read(name, payload, 202)
        async with factory() as db:
            (await db.get(User, 202)).status = False
            await db.commit()
        assert not (await tool.execute(**payload, authorization=owner_token))["success"]
