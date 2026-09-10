"""退款确认链路测试：预览阶段不执行工具，明确确认后才调用 apply_refund。

通过 object.__new__ 构造 AgentGraph（绕过 __init__ 的 LLM/存储依赖），
并 monkeypatch 模块级 get_langchain_tool，保证测试不依赖外部服务。
"""
from app.agent.graph import AgentGraph
from app.agent.state import AgentState, IntentType, AgentNode


class FakeTool:
    """假退款工具：仅记录调用，返回成功结果。"""

    async def ainvoke(self, args):
        return {
            "success": True,
            "data": {"refund_id": "R12345678", "amount": 99.0, "next_steps": ["等待平台审核"]},
            "execution_time_ms": 12,
            "retry_count": 0,
        }


def _make_graph():
    return object.__new__(AgentGraph)


def _refund_state(message: str, confirm_refund: bool = False) -> AgentState:
    state = AgentState(session_id="s1", user_id=1, user_message=message)
    state.detected_intent = IntentType.REFUND
    state.collected_info.update({
        "order_id": "ORD123456",
        "reason": "商品破损",
        "refund_type": "refund",
        "user_id": 1,
        "session_id": "s1",
    })
    if confirm_refund:
        state.collected_info["confirm_refund"] = True
    return state


async def test_refund_preview_requires_confirmation(monkeypatch):
    monkeypatch.setattr("app.agent.graph.get_langchain_tool", lambda name: FakeTool())
    graph = _make_graph()
    state = _refund_state("ORD123456 我要退款，商品破损")

    next_node = await graph._node_tool_execution(state)

    assert next_node == AgentNode.CONFIRMATION
    assert state.pending_confirmations == ["apply_refund"]
    assert state.collected_info.get("refund_preview") is not None
    assert len(state.tool_calls) == 0  # 未确认前不得执行工具


async def test_refund_confirmed_executes_tool(monkeypatch):
    monkeypatch.setattr("app.agent.graph.get_langchain_tool", lambda name: FakeTool())
    graph = _make_graph()
    state = _refund_state("确认", confirm_refund=True)

    next_node = await graph._node_tool_execution(state)

    assert next_node == AgentNode.RESULT_VERIFICATION
    assert len(state.tool_calls) == 1
    assert state.tool_calls[0].tool_name == "apply_refund"
    assert state.tool_calls[0].success
    assert "refund_id" in state.collected_info.get("apply_refund_result", {})


async def test_refund_tool_failure_routes_to_human_after_retries(monkeypatch):
    class FailingTool:
        async def ainvoke(self, args):
            return {
                "success": False,
                "data": None,
                "error": "退款服务暂时不可用",
                "execution_time_ms": 20,
                "retry_count": 3,
            }

    monkeypatch.setattr("app.agent.graph.get_langchain_tool", lambda name: FailingTool())
    graph = _make_graph()
    state = _refund_state("确认", confirm_refund=True)

    next_node = await graph._node_tool_execution(state)

    assert next_node == AgentNode.HUMAN_HANDOFF
    assert state.need_human is True
