import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.agent.refund_authorization import authorize_refund
from app.agent.tools import ApplyRefundTool
from app.models.models import RefundAction, RefundApplication, Order, Session, Message, MessageRole, OperationAudit
from app.services import refund_actions
from tests.test_refund_persistence import args, seed, prepared
from tests.test_ticket_idempotency import api_context


async def submit(payload, message_id=1):
    return await ApplyRefundTool().execute(**payload, authorization=authorize_refund(payload, message_id, 101))


@pytest.mark.parametrize("change", ["expiry", "version", "reason", "amount", "cancel", "order", "type"])
async def test_stale_or_changed_action_cannot_submit(tmp_path, monkeypatch, change):
    async with api_context(tmp_path / "action.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        payload = await prepared(factory, args())
        if change == "cancel":
            await refund_actions.cancel(factory, 101, 101, payload["action_id"])
        else:
            async with factory() as db:
                item = await db.get(RefundAction, payload["action_id"])
                if change == "expiry":
                    item.expires_at = datetime.utcnow() - timedelta(seconds=1)
                elif change == "version":
                    item.version += 1
                elif change == "amount":
                    (await db.get(Order, "ORD900001")).total_amount = Decimal("100.00")
                elif change == "order":
                    payload["order_id"] = "ORD900002"
                elif change == "type":
                    payload["type"] = "exchange"
                else:
                    payload["reason"] = "另一个原因"
                await db.commit()
        result = await submit(payload)
        assert not result["success"]
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(RefundApplication)) == 0


async def test_repreview_versions_and_cancel_are_persistent(tmp_path):
    async with api_context(tmp_path / "action.db") as (_, factory):
        await seed(factory)
        a = await refund_actions.prepare(factory, 101, args())
        b = await refund_actions.prepare(factory, 101, {**args(), "reason": "新的申请原因"}, a["action_id"])
        assert b["action_id"] == a["action_id"] and b["version"] == 2
        await refund_actions.cancel(factory, 101, 101, b["action_id"])
        await refund_actions.cancel(factory, 101, 101, b["action_id"])
        async with factory() as db:
            assert (await db.get(RefundAction, b["action_id"])).status == "cancelled"
            assert await db.scalar(select(func.count()).select_from(OperationAudit).where(OperationAudit.operation == "cancel_refund")) == 1
        renewed = await refund_actions.prepare(factory,101,args(),b["action_id"])
        assert renewed["version"] == 3
        await refund_actions.cancel(factory,101,101,renewed["action_id"])
        async with factory() as db:
            assert (await db.get(RefundAction,renewed["action_id"])).status == "cancelled"
            assert await db.scalar(select(func.count()).select_from(OperationAudit).where(OperationAudit.operation == "cancel_refund")) == 2


@pytest.mark.parametrize("received,refunded,allowed", [(None, "0", True), (-8, "0", False), (-1, "0", True),
                                                       (1, "0", False), (-1, "99.10", False)])
async def test_qualification_uses_received_time_and_remaining_amount(tmp_path, received, refunded, allowed):
    async with api_context(tmp_path / "action.db") as (_, factory):
        await seed(factory)
        async with factory() as db:
            order = await db.get(Order, "ORD900001")
            order.received_at = None if received is None else datetime.utcnow() + timedelta(days=received)
            order.refunded_amount = Decimal(refunded)
            await db.commit()
        if allowed:
            assert (await refund_actions.prepare(factory, 101, args()))["amount"] == "99.10"
        else:
            with pytest.raises(HTTPException):
                await refund_actions.prepare(factory, 101, args())


async def test_cross_session_duplicate_returns_original_and_changed_reason_conflicts(tmp_path, monkeypatch):
    async with api_context(tmp_path / "action.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        first = await submit(await prepared(factory, args()))
        assert first["success"]
        async with factory() as db:
            db.add(Session(id="second", user_id=101))
            await db.flush()
            db.add(Message(id=2, session_id="second", role=MessageRole.USER, content="确认"))
            await db.commit()
        payload = {**args(), "session_id": "second", "idempotency_key": "another-key"}
        action = await refund_actions.prepare(factory, 101, payload)
        second = await submit({**payload, "action_id": action["action_id"], "action_version": action["version"]}, 2)
        assert second["success"] and second["data"]["refund_id"] == first["data"]["refund_id"]
        with pytest.raises(HTTPException) as error:
            await refund_actions.prepare(factory, 101, {**payload, "reason": "不同原因"})
        assert error.value.status_code == 409
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(RefundApplication)) == 1


async def test_manual_review_requires_received_evidence_and_never_marks_paid(tmp_path, monkeypatch):
    async with api_context(tmp_path / "action.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        result = await submit(await prepared(factory, args()))
        refund_id = result["data"]["refund_id"]
        with pytest.raises(HTTPException):
            await refund_actions.review(factory, 101, refund_id, "approved", "越权")
        with pytest.raises(HTTPException):
            await refund_actions.review(factory, 999, refund_id, "approved", "缺少签收")
        async with factory() as db:
            (await db.get(Order, "ORD900001")).received_at = datetime.utcnow() - timedelta(days=1)
            await db.commit()
        approved = await refund_actions.review(factory, 999, refund_id, "approved", "合成审核通过")
        assert approved["status"] == "approved" and "未支付" in approved["status_label"]
        assert await refund_actions.review(factory, 999, refund_id, "approved", "合成审核通过") == approved
        with pytest.raises(HTTPException):
            await refund_actions.review(factory, 999, refund_id, "rejected", "变更决定")


@pytest.mark.parametrize("repetition", range(20))
async def test_cross_session_concurrent_confirmation_creates_one_application(tmp_path, monkeypatch, repetition):
    async with api_context(tmp_path / "action.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        payloads = []
        for index in range(2):
            session_id = f"concurrent-{index}"
            async with factory() as db:
                db.add(Session(id=session_id, user_id=101))
                await db.flush()
                db.add(Message(id=10 + index, session_id=session_id, role=MessageRole.USER, content="确认"))
                await db.commit()
            payload = {**args(), "session_id": session_id, "idempotency_key": f"key-{index}"}
            action = await refund_actions.prepare(factory, 101, payload)
            payloads.append({**payload, "action_id": action["action_id"], "action_version": action["version"]})
        results = await asyncio.gather(*[submit(p, 10 + i) for i, p in enumerate(payloads)])
        assert all(r["success"] for r in results)
        assert len({r["data"]["refund_id"] for r in results}) == 1
