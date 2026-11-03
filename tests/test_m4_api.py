"""真实REST/WS、SQL与业务图，只有理解模型/知识后端为合成替身。"""

import asyncio
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select, func
from tests.test_session_creation import session_api as session_api
from tests.test_chat_auth import receive_turn
from app.models.models import Ticket, RefundApplication, ChatRun
from app.agent.request_understanding import RequestUnderstanding


@pytest.mark.parametrize("transport", ["rest", "ws"])
@pytest.mark.parametrize(
    "message,topic",
    [
        ("订单ORD960001退款多久能到账？", "refund"),
        ("不要退款，只问订单ORD960001政策。", "refund"),
        ("垃圾耳机，我只是问保修。", "technical"),
    ],
)
def test_model_inquiries_and_negative_emotion_never_create_business_action(
    session_api, monkeypatch, transport, message, topic
):
    import app.routers.chat as chat
    from app.agent.graph import AgentGraph
    from app.config.config import settings
    from langgraph.checkpoint.memory import InMemorySaver
    from app.security import create_access_token

    client, factory, _, _, users = session_api
    monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_MODE", "model")
    graph = AgentGraph()
    graph.checkpointer = InMemorySaver()
    graph.langgraph = graph._build_langgraph()
    result = RequestUnderstanding(
        topic=topic,
        request_type="inquiry",
        entities={
            "order_id": "ORD960001" if "ORD960001" in message else None,
            "product": "耳机" if "耳机" in message else None,
        },
        conditions=[],
        subrequests=[],
        service_preference="unspecified",
        emotion="angry" if "垃圾" in message else "neutral",
        clarification=None,
        retrieval_query=message,
        entity_source="current_message",
    )
    monkeypatch.setattr(
        graph.llm_service, "run_structured", AsyncMock(return_value=result)
    )
    monkeypatch.setattr(chat.chat_service, "process", graph.run)
    monkeypatch.setattr(chat.chat_service, "process_stream", graph.run_stream)

    class KB:
        async def async_search(self, **kwargs):
            return {
                "success": True,
                "results": [],
                "meta": {"degraded": True},
                "execution_time_ms": 1,
            }

    monkeypatch.setattr("app.agent.graph.get_knowledge_base", lambda: KB())
    sid = client.post("/api/v1/chat/sessions?user_id=101").json()["data"]["id"]
    body = {
        "user_id": 101,
        "session_id": sid,
        "message": message,
        "client_message_id": "m4-inquiry",
    }
    if transport == "rest":
        assert client.post("/api/v1/chat/send", json=body).status_code == 200
    else:
        with client.websocket_connect(
            "/api/v1/chat/stream?access_token=" + create_access_token(users[0])
        ) as ws:
            ws.send_json(body)
            events = receive_turn(ws)
            assert events[-1]["type"] == "stream_end"

    async def inspect():
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(Ticket)) == 0
            assert (
                await db.scalar(select(func.count()).select_from(RefundApplication))
                == 0
            )
            run = await db.scalar(select(ChatRun))
            assert run.request_data["_understanding_mode"] == "model"
        await graph.llm_service.close()

    asyncio.run(inspect())
