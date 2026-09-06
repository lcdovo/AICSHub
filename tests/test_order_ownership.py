import asyncio

from sqlalchemy import delete

from app.agent.tools import QueryOrderTool
from app.models.models import Base, Order, User
from app.services.chat_service import ChatService
from app.utils.database import async_session, engine


ORDER_ID = "ORD-OWNERSHIP-TEST"
OWNER_ID = 91001
OTHER_USER_ID = 91002


def test_query_order_only_returns_the_authenticated_users_order():
    async def scenario():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_session() as session:
            await session.execute(delete(Order).where(Order.order_id == ORDER_ID))
            await session.execute(delete(User).where(User.id.in_([OWNER_ID, OTHER_USER_ID])))
            session.add_all([
                User(id=OWNER_ID, username="order-owner-test", role="user"),
                User(id=OTHER_USER_ID, username="order-other-test", role="user"),
                Order(
                    order_id=ORDER_ID, user_id=OWNER_ID, status="已发货",
                    status_code="shipped", items=[{"name": "测试耳机", "qty": 1, "price": 299}],
                    total_amount=299, address="测试地址",
                ),
            ])
            await session.commit()

        try:
            tool = QueryOrderTool()
            owner_result = await tool.execute(order_id=ORDER_ID, user_id=OWNER_ID)
            other_result = await tool.execute(order_id=ORDER_ID, user_id=OTHER_USER_ID)
            anonymous_result = await tool.execute(order_id=ORDER_ID)

            assert owner_result["success"] is True
            assert owner_result["data"]["order_id"] == ORDER_ID
            assert other_result["success"] is False
            assert "data" not in other_result
            assert anonymous_result["success"] is False
            assert anonymous_result["error_type"] == "permission"
        finally:
            async with async_session() as session:
                await session.execute(delete(Order).where(Order.order_id == ORDER_ID))
                await session.execute(delete(User).where(User.id.in_([OWNER_ID, OTHER_USER_ID])))
                await session.commit()

    asyncio.run(scenario())


def test_hydrate_state_overwrites_untrusted_cached_user(monkeypatch):
    class SavedState:
        messages = []
        collected_info = {"user_id": OWNER_ID, "order_id": ORDER_ID}
        detected_intent = "query_order"
        intent_confidence = 1.0
        pending_confirmations = []
        conversation_summary = ""

    async def get_state(_session_id):
        return SavedState()

    monkeypatch.setattr("app.services.chat_service.session_manager.get_state", get_state)
    state = asyncio.run(ChatService().hydrate_state("session-other", OTHER_USER_ID, "查询订单"))

    assert state.user_id == OTHER_USER_ID
    assert state.collected_info["user_id"] == OTHER_USER_ID
    assert state.collected_info["session_id"] == "session-other"
