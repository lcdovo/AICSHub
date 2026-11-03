from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from app.agent.state import AgentState, AgentNode
from app.agent.nodes.response_node import ResponseNode, ResponseServices
from app.agent.nodes.handoff_node import HandoffNode, HandoffDependencies
from app.agent.tools import ToolExecutionResult
from app.agent.stream_context import stream_event_queue
from tests.test_chat_turns import Cache, Validator
from tests.test_m4_evidence_contract import result as retrieval_result


def services(model, validator, cache=None, knowledge=None):
    cache = cache or Cache()
    knowledge = knowledge or SimpleNamespace(evidence_is_current=lambda _: True)
    judge = SimpleNamespace(DEFAULT_THRESHOLD=.45, judge=AsyncMock(return_value=None))
    memory = SimpleNamespace(get_user_profile=AsyncMock(return_value={"memory_source": "none"}))
    return ResponseServices(lambda: model, lambda: validator, lambda: judge,
                            lambda: cache, lambda: knowledge, lambda: memory), cache


async def test_response_component_generates_and_reviews_without_graph():
    model = SimpleNamespace(chat=AsyncMock(side_effect=[("合成草稿一", "technical", 3), ("合成最终回答", "technical", 3)]),
                            get_last_call_metadata=lambda: {"degraded": False})
    validator = Validator((False, True))
    dependencies, _ = services(model, validator)
    node = ResponseNode(dependencies)
    state = AgentState(session_id="s", user_id=101, user_message="合成问题", detected_intent="technical")
    assert await node(state) == AgentNode.END
    assert state.reply == "合成最终回答" and state.review_result.passed
    assert validator.responses == ["合成草稿一", "合成最终回答"]
    assert model.chat.await_count == 2
    assert all(call.kwargs["user_id"] == 101 for call in model.chat.call_args_list)


async def test_component_buffers_rejected_provider_stream_and_publishes_only_validated_final():
    import asyncio

    queue = asyncio.Queue()
    drafts = ["合成不通过草稿", "合成通过回答"]
    observed = []

    class Model:
        async def stream_chat(self, **payload):
            assert queue.empty()
            observed.append(payload)
            yield drafts[len(observed) - 1]

        def get_last_call_metadata(self):
            return {"degraded": False, "tokens": 3}

    dependencies, _ = services(Model(), Validator((False, True)))
    node = ResponseNode(dependencies)
    state = AgentState(session_id="s", user_id=101, user_message="合成问题", detected_intent="technical")
    token = stream_event_queue.set(queue)
    try:
        assert await node(state) == AgentNode.END
    finally:
        stream_event_queue.reset(token)
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert "".join(event[1] for event in events) == "合成通过回答"
    assert len(observed) == 2


async def test_injected_snapshot_checker_still_invalidates_during_generation_and_does_not_cache(monkeypatch):
    monkeypatch.setattr("app.agent.nodes.response_node.settings.USER_MEMORY_ENABLED", False)
    cache = Cache()
    knowledge = SimpleNamespace(evidence_is_current=Mock(side_effect=[True, False]))
    model = SimpleNamespace(chat=AsyncMock(return_value=("合成原证据草稿", "technical", 3)),
                            get_last_call_metadata=lambda: {"degraded": False})
    dependencies, _ = services(model, Validator(), cache, knowledge)
    node = ResponseNode(dependencies)
    state = AgentState(session_id="s", user_id=101, user_message="合成问题")
    assert state.accept_retrieval(retrieval_result())
    state.collected_info["rag_grounded"] = True
    assert await node(state) == AgentNode.END
    assert "知识版本已变化" in state.reply and not state.current_evidence()
    assert cache.values == {}


async def test_handoff_component_rejects_foreign_receipt_without_graph():
    invoke = AsyncMock(return_value=ToolExecutionResult(success=True, data={
        "request_id": "foreign", "status": "pending", "user_id": 202, "session_id": "other"}))
    authorize = Mock(return_value="synthetic-internal-authorization")
    node = HandoffNode(HandoffDependencies(invoke, authorize))
    state = AgentState(session_id="s", user_id=101, actor_id=101, source_message_id=7, user_message="转人工")
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert not state.tool_calls[0].success and "无法确认" in state.reply
    authorize.assert_called_once()
    assert invoke.call_args.kwargs["user_id"] == 101


async def test_main_graph_node_registry_works_without_legacy_graph_node_bindings(monkeypatch):
    from app.agent.graph import AgentGraph
    from langgraph.checkpoint.memory import InMemorySaver

    graph = AgentGraph()
    for name in ["_node_start", "_node_intent_recognition", "_node_route_decision",
                 "_node_response_generation", "_node_end"]:
        monkeypatch.setattr(graph, name, AsyncMock(side_effect=AssertionError("legacy graph node called")))
    graph._register_nodes()
    assert all(getattr(node, "__self__", None) is not graph for node in graph.nodes.values())
    graph.checkpointer = InMemorySaver()
    graph.langgraph = graph._build_langgraph()
    try:
        state = await graph.run(AgentState(session_id="s", user_id=101, user_message="你好"))
        assert state.current_node == AgentNode.END and "您好" in state.reply
    finally:
        await graph.llm_service.close()
