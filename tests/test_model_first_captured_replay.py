"""真实捕获输出的隔离整图回放；默认用冻结fixture，显式环境开关要求真实报告。"""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select, func

from app.agent.graph import AgentGraph
from app.agent.request_understanding import RequestUnderstanding
from app.agent.state import AgentState, AgentNode
from app.agent.tools import QueryOrderTool, ToolExecutionResult
from app.models.models import Order, RefundAction, RefundApplication, Ticket, HandoffRequestRecord
from tests.test_ticket_idempotency import api_context

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "acceptance/model-first/critical-v2.json").read_bytes())["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
@pytest.mark.parametrize("foreign", [False, True], ids=["owned-order", "foreign-order"])
async def test_frozen_boundary_output_through_actual_graph_and_sql_owner_checks(case, foreign, tmp_path, monkeypatch):
    live = os.environ.get("MODEL_FIRST_CAPTURED_REPLAY") == "critical-v2"
    if live:
        row = json.loads((ROOT / "reports/model-first-admission/critical-v2/current/cases" / (case["case_id"] + ".json")).read_bytes())
        raw = row["provider_output"]["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        try:
            captured = RequestUnderstanding.model_validate_json(raw)
        except ValueError:
            captured = None
    else:
        captured = RequestUnderstanding.model_validate(case["mock"])
    async with api_context(tmp_path / "boundary-replay.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        order_id = case["expected"]["order_id"]
        if order_id:
            async with factory() as db:
                db.add(Order(order_id=order_id, user_id=202 if foreign else 101,
                             status="未发货", status_code="pending", total_amount="0.10", items=[]))
                await db.commit()
        calls = []

        async def invoke(name, **payload):
            calls.append(name)
            assert name == "query_order", "boundary replay prohibits all other tools"
            result = await QueryOrderTool().execute(**payload)
            return ToolExecutionResult(**{k: v for k, v in result.items() if k in ToolExecutionResult.model_fields})

        monkeypatch.setattr("app.agent.mcp_client.invoke_tool", invoke)
        # 本回放核实路由与业务边界，知识检索使用明确的空证据替身。
        monkeypatch.setattr("app.agent.graph.get_knowledge_base", lambda: SimpleNamespace(
            async_search=AsyncMock(return_value={"success": False, "results": [], "meta": {}})))
        graph = AgentGraph()
        graph.checkpointer = InMemorySaver()
        graph.langgraph = graph._build_langgraph()
        service = graph.llm_service
        monkeypatch.setattr(service, "run_structured", AsyncMock(return_value=captured))
        try:
            current = await graph.run(AgentState(session_id="boundary-" + case["case_id"],
                                                 user_id=101, actor_id=101, user_message=case["message"],
                                                 understanding_mode="model"))
            assert current.current_node == AgentNode.END
            assert not current.need_human and not current.pending_confirmations and not current.refund_preview_data()
            assert set(calls) <= {"query_order"}
            if foreign:
                assert all(not item.success for item in current.tool_calls if item.tool_name == "query_order")
                assert "0.10" not in current.reply
            async with factory() as db:
                for entity in (RefundAction, RefundApplication, Ticket, HandoffRequestRecord):
                    assert await db.scalar(select(func.count()).select_from(entity)) == 0
        finally:
            await service.close()
