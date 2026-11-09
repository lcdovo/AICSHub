import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from openai import AsyncOpenAI
import pytest
from sqlalchemy import select

from app.agent.nodes.response_node import ResponseNode, ResponseServices, validate_candidate
from app.agent.request_understanding import EvidenceBundle
from app.agent.state import AgentNode, AgentState
from app.agent.stream_context import stream_event_queue
from app.agent.validation import ResponseValidator
from app.agent.validation_llm import LLMRAGJudge, assess_judgement
from app.config.config import settings
from app.models.models import ModelCallRecord
from app.services.llm_service import LLMService, ModelConfig
from app.services.model_gateway import model_context
from tests.test_chat_turns import Cache
from tests.test_m4_gateway import factory as factory
from tests.test_m5_judge_contract import EVIDENCE, verdict


@pytest.fixture
def v2(monkeypatch):
    monkeypatch.setattr(settings, "LLM_JUDGE_CONTRACT_VERSION", "v2")
    monkeypatch.setattr(settings, "LLM_JUDGE_ENABLED", True)
    monkeypatch.setattr(settings, "USER_MEMORY_ENABLED", False)


def state(reply="", query="合成使用问题"):
    result = AgentState(session_id="judge-v2", user_id=101, user_message=query, reply=reply,
                        evidence_bundle=EvidenceBundle(items=deepcopy(EVIDENCE)))
    result.set_flow(rag_grounded=True)
    return result


def node(raw, response):
    cache = Cache()
    model = SimpleNamespace(is_available=lambda: True, run_structured=AsyncMock(return_value=raw))
    knowledge = SimpleNamespace(evidence_is_current=lambda items: True)
    component = ResponseNode(ResponseServices(lambda: None, ResponseValidator, lambda: LLMRAGJudge(model),
        lambda: cache, lambda: knowledge, lambda: None), generate=AsyncMock(return_value=response))
    return component, cache, model


async def test_unknown_safe_clarification_is_published_but_never_answer_cached(v2):
    reply = "资料不足，请提供具体型号。"
    raw = verdict(answerability="unknown", response_behavior="clarify", claims=[],
                  nonfact_segments=[{"segment_id": 0, "kind": "clarification"}])
    component, cache, model = node(raw, reply)
    current = state()
    queue = asyncio.Queue()
    token = stream_event_queue.set(queue)
    try:
        assert await component(current) == AgentNode.END
    finally:
        stream_event_queue.reset(token)
    assert current.reply == reply and current.review_result.passed
    assert current.evidence_bundle.support == "unknown"
    assert current.collected_info["validation"]["candidate_validity"] == "valid"
    assert current.collected_info["validation"]["question_answerability"] == "unknown"
    assert "_v2_verdict" not in current.collected_info["llm_judge"]
    assert not cache.values and model.run_structured.await_count == 1
    assert "".join(queue.get_nowait()[1] for _ in range(queue.qsize())) == reply


async def test_partial_answer_with_safe_question_is_not_cached(v2):
    reply = EVIDENCE[0]["content"] + "请提供衣标。"
    component, cache, _ = node(verdict(answerability="partial", response_behavior="answer_with_limits",
        nonfact_segments=[{"segment_id": 1, "kind": "clarification"}]), reply)
    current = state()
    assert await component(current) == AgentNode.END
    assert current.review_result.passed and not cache.values
    assert current.evidence_bundle.support == "partial"


async def test_fully_supported_answer_can_still_be_cached(v2):
    component, cache, _ = node(verdict(), EVIDENCE[0]["content"])
    current = state()
    assert await component(current) == AgentNode.END
    assert current.review_result.passed and cache.values


@pytest.mark.parametrize("fault", ["critical_amount", "invalid_citation"])
async def test_v2_allow_cannot_override_existing_business_or_citation_guards(v2, fault):
    reply = "订单ORD950099金额999元。" if fault == "critical_amount" else "已核实。[证据:missing@v1]"
    raw = verdict(reply)
    component, cache, _ = node(raw, reply)
    current = state(reply)
    if fault == "critical_amount":
        current.add_tool_call("query_order", {}, {"success": True, "data": {"order_id": "ORD950099",
            "status": "已发货", "total_amount": "299.00", "currency": "CNY"}}, True)
    assert not await validate_candidate(component, current)
    assert not current.review_result.passed and not cache.values


@pytest.mark.parametrize("changed", ["response", "query", "evidence", "tool"])
async def test_v2_normalized_proof_cannot_be_reused_for_changed_input(v2, changed):
    reply, query = EVIDENCE[0]["content"], "合成问题"
    model = SimpleNamespace(is_available=lambda: True, run_structured=AsyncMock(return_value=verdict()))
    result = await LLMRAGJudge(model).judge(reply, query, EVIDENCE)
    response, question, evidence, tools = reply, query, deepcopy(EVIDENCE), ""
    if changed == "response": response += "另可换新。"
    elif changed == "query": question = "不同问题"
    elif changed == "evidence": evidence[0]["version_id"] = "changed"
    else: tools = json.dumps({"status": "synthetic"})
    assessed = assess_judgement(result, response=response, user_query=question, kb_context=evidence,
                                tool_evidence=tools, contract_version="v2")
    assert not assessed["publish_allowed"] and assessed["support"] == "unknown"


def test_old_contract_and_forged_fields_cannot_be_upgraded_to_v2(v2):
    old = {"grounded": True, "fact_score": 1.0, "has_unsupported_claim": False, "refuse_recommended": False}
    assert not assess_judgement(old, response="合成", kb_context=EVIDENCE, contract_version="v2")["publish_allowed"]
    raw = verdict()
    raw["publish_allowed"] = True  # 不属于供应商schema，不可信。
    assert not assess_judgement(raw, response=EVIDENCE[0]["content"], kb_context=EVIDENCE, contract_version="v2")["publish_allowed"]


def test_cache_keys_separate_judge_versions_and_policies(monkeypatch):
    component, _, _ = node(verdict(), EVIDENCE[0]["content"])
    current = state()
    monkeypatch.setattr(settings, "LLM_JUDGE_CONTRACT_VERSION", "v1")
    first = component._build_answer_cache_key(current)
    monkeypatch.setattr(settings, "LLM_JUDGE_CONTRACT_VERSION", "v2")
    assert component._build_answer_cache_key(current) != first


@pytest.mark.parametrize("failure", ["disabled", "unavailable", "none", "invalid", "timeout", "exception"])
async def test_v2_dependency_failures_stay_unknown_and_never_publish(v2, monkeypatch, failure):
    monkeypatch.setattr(settings, "LLM_JUDGE_ENABLED", failure != "disabled")
    async def structured(*args, **kwargs):
        if failure == "timeout": await asyncio.sleep(1)
        if failure == "exception": raise RuntimeError("synthetic-private-error")
        if failure == "invalid": return {"schema_version": 2, "publish_allowed": True}
        return None
    service = SimpleNamespace(is_available=lambda: failure != "unavailable", run_structured=AsyncMock(side_effect=structured))
    judge = LLMRAGJudge(service)
    monkeypatch.setattr(judge, "TIMEOUT_SECONDS", .01)
    result = await judge.judge(EVIDENCE[0]["content"], "合成问题", EVIDENCE)
    assert result["support"] == "unknown" and not result["publish_allowed"]
    assert "synthetic-private-error" not in str(result)


def test_default_judge_contract_remains_v1():
    from app.config.config import Settings
    assert Settings.model_fields["LLM_JUDGE_CONTRACT_VERSION"].default == "v1"


@pytest.mark.parametrize("bad", [False, True])
async def test_actual_sdk_function_call_schema_and_sql_metering_offline(v2, factory, monkeypatch, bad):
    monkeypatch.setattr(settings, "LLM_MAX_TOKENS", 1024)
    monkeypatch.setattr(settings, "LLM_DAILY_USER_TOKEN_BUDGET", 100000)
    service = LLMService()
    service.models = [ModelConfig("synthetic", api_key="synthetic-key", api_base="https://offline.invalid/v1",
        model="synthetic-judge", thinking_mode="disabled", structured_output_method="function_calling")]
    service.local_model = None
    service.RETRY_DELAY = 0
    sent = []
    def respond(request):
        body = json.loads(request.content)
        sent.append(body)
        schema = body["tools"][0]["function"]
        assert schema["name"] == "JudgeVerdictV2"
        assert schema["parameters"]["additionalProperties"] is False
        assert body["max_completion_tokens"] == 1024
        payload = verdict()
        if bad: payload["claims"][0]["evidence"][0]["version_id"] = "wrong"
        return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion", "created": 1,
            "model": "synthetic-judge", "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{"id": "synthetic-tool", "type": "function",
                "function": {"name": "JudgeVerdictV2", "arguments": json.dumps(payload, ensure_ascii=False)}}]}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        original = service._get_chat_model
        def get_model(config, **kwargs):
            model = original(config, **kwargs)
            model.async_client = AsyncOpenAI(api_key="synthetic-key", base_url=config.api_base,
                http_client=client, max_retries=0).chat.completions
            return model
        monkeypatch.setattr(service, "_get_chat_model", get_model)
        try:
            async with model_context(101, "synthetic-judge-v2", factory=factory):
                result = await LLMRAGJudge(service).judge(EVIDENCE[0]["content"], "合成问题", EVIDENCE)
            assert result["publish_allowed"] is (not bad)
            assert len(sent) == 1
            async with factory() as db:
                records = (await db.scalars(select(ModelCallRecord))).all()
                assert len(records) == 1 and records[0].user_id == 101 and records[0].purpose == "llm_judge"
                assert records[0].input_tokens == 20 and records[0].output_tokens == 30
                assert records[0].status == "succeeded"  # HTTP/schema成功不等于证据审查通过。
        finally:
            await service.close()
