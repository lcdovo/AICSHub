from app.services.llm_governance import SensitiveDataRedactor
from app.utils.tracking import StructuredLogger


def test_nested_logs_redact_credentials_and_personal_data(caplog):
    import logging
    marker = "synthetic-private-secret"
    values = {"authorization": "Bearer " + marker, "nested": [{"email": "synthetic@example.test", "phone": "13800138000"}],
              "text": "Bearer abc.def.ghi mysql://user:password@host/db 地址：合成街道1号",
              "token_usage": {"total_tokens": 10}, "amount": "0.10", "resource_id": "synthetic-id"}
    with caplog.at_level(logging.INFO):
        StructuredLogger("privacy-test").log_tool("trace", "query_order", True, payload=values)
    assert marker not in caplog.text and "synthetic@example.test" not in caplog.text
    assert "13800138000" not in caplog.text and "user:password" not in caplog.text
    assert "合成街道" not in caplog.text
    cleaned = SensitiveDataRedactor().redact_value(values)
    assert cleaned["token_usage"]["total_tokens"] == 10
    assert cleaned["amount"] == "0.10" and cleaned["resource_id"] == "synthetic-id"


def test_trace_redacts_before_observer_export():
    from unittest.mock import Mock
    from app.utils.tracking import TraceSpan
    span = TraceSpan("synthetic-trace", "synthetic-span")
    observer = Mock()
    span._otel = observer
    span.set_attribute("authorization", "Bearer synthetic-private-token")
    span.add_event("synthetic", {"email": "private@example.test", "amount": "0.10"})
    span.end("error", "mysql://user:password@host/db")
    serialized = str(span.to_dict()) + str(observer.mock_calls)
    assert "synthetic-private-token" not in serialized
    assert "private@example.test" not in serialized and "user:password" not in serialized
    assert span.events[0]["attributes"]["amount"] == "0.10"


async def test_persisted_trace_and_evaluation_reports_redact_without_changing_message(tmp_path, monkeypatch):
    from sqlalchemy import select
    from app.agent.state import AgentState
    from app.models.models import Session, Message, EvaluationResult, AgentTrace
    from app.services.chat_service import ChatService
    from tests.test_ticket_idempotency import api_context
    async with api_context(tmp_path / "privacy.db") as (_, factory):
        state = AgentState(session_id="session-101", user_id=101, user_message="联系 synthetic@example.test")
        state.reply = "订单金额0.10，联系 synthetic@example.test"
        state.trace = [{"node": "synthetic", "input": {"authorization": "Bearer synthetic-private-token"},
                        "output": {"email": "synthetic@example.test", "amount": "0.10"}}]
        async with factory() as db:
            session = await db.get(Session,"session-101")
            await ChatService().persist_result(db, session, state, state.user_message, "synthetic-trace")
            await db.commit()
        async with factory() as db:
            report = await db.scalar(select(EvaluationResult))
            trace = await db.scalar(select(AgentTrace))
            message = await db.scalar(select(Message))
            assert "synthetic@example.test" not in report.user_query + report.response + str(trace.output_data)
            assert "synthetic-private-token" not in str(trace.input_data)
            assert trace.output_data["amount"] == "0.10" and "0.10" in report.response
            assert message.content == state.reply
