from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import httpx
import pytest

from scripts.m5_live_budget import DispatchLedger, OPERATION, guarded_transport, validate_authorization


def plan():
    return {"plan_id": "synthetic-plan", "proposed_limits": {"max_requests": 3, "max_input_tokens": 10000, "max_output_tokens": 30}}


def body(output=10, **extra):
    return json.dumps({"model": "synthetic", "max_tokens": output, **extra}).encode()


def ledger(tmp_path, **limits):
    return DispatchLedger(tmp_path / "ledger.sqlite", "synthetic-plan", {**plan()["proposed_limits"], **limits},
                          {str(i): 10 for i in range(8)})


@pytest.mark.parametrize("change", [
    {"plan_id": "different"}, {"approved_by": "assistant"}, {"user_statement": ""}, {"approved_at": ""},
    {"max_requests": True}, {"max_requests": 4}, {"max_input_tokens": 10001}, {"max_output_tokens": 31},
])
def test_no_implicit_approval_or_budget_expansion(change):
    value = {"plan_id": "synthetic-plan", "approved_by": "user", "user_statement": "explicit synthetic approval",
             "approved_at": "2026-11-05", **plan()["proposed_limits"], **change}
    with pytest.raises(ValueError):
        validate_authorization(value, plan())


def test_unknown_dispatch_survives_restart_and_is_not_replayed(tmp_path):
    first = ledger(tmp_path)
    first.reserve("0", body())
    second = ledger(tmp_path)
    assert second.snapshot()["requests"] == second.snapshot()["usage_unknown_requests"] == 1
    with pytest.raises(ValueError, match="already_dispatched"):
        second.reserve("0", body())
    assert second.snapshot()["requests"] == 1


@pytest.mark.parametrize("missing", ["database", "marker"])
def test_deleting_one_ledger_component_cannot_reset_quota(tmp_path, missing):
    item = ledger(tmp_path)
    path = item.path if missing == "database" else item.path.with_suffix(".created.json")
    path.unlink()
    with pytest.raises(ValueError, match="no_reset_allowed"):
        ledger(tmp_path)


def test_concurrent_reservations_share_exact_limit(tmp_path):
    item = ledger(tmp_path)
    def attempt(i):
        try:
            item.reserve(str(i), body())
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert sum(results) == item.snapshot()["requests"] == 3


@pytest.mark.parametrize("cap", ["input", "output"])
def test_token_cap_rejects_before_consuming_request(tmp_path, cap):
    item = ledger(tmp_path, **({"max_input_tokens": 1} if cap == "input" else {"max_output_tokens": 1}))
    with pytest.raises(ValueError, match="budget_exhausted"):
        item.reserve("0", body())
    assert item.snapshot()["requests"] == 0


@pytest.mark.parametrize("request_body", [body(output=11), body(output=True), body(stream=True), body(padding="x" * 25000)])
def test_per_operation_and_body_limits_are_enforced(tmp_path, request_body):
    item = ledger(tmp_path)
    with pytest.raises(ValueError):
        item.reserve("0", request_body)
    assert not item.dispatched()


@pytest.mark.asyncio
async def test_http_failure_consumes_attempt_and_cannot_be_retried(tmp_path):
    item = ledger(tmp_path)
    calls = []
    async def fail(request):
        calls.append(request.url)
        raise httpx.ReadTimeout("synthetic")
    async with httpx.AsyncClient(transport=guarded_transport(item, model="synthetic", endpoint="https://provider.invalid/v1",
                                        underlying=httpx.MockTransport(fail))) as client:
        token = OPERATION.set("0")
        try:
            with pytest.raises(httpx.ReadTimeout):
                await client.post("https://provider.invalid/v1/chat/completions", content=body())
            with pytest.raises(ValueError, match="already_dispatched"):
                await client.post("https://provider.invalid/v1/chat/completions", content=body())
        finally:
            OPERATION.reset(token)
    assert len(calls) == 1 and item.snapshot()["usage_unknown_requests"] == 1


@pytest.mark.asyncio
async def test_wrong_url_or_model_never_reaches_transport(tmp_path):
    item = ledger(tmp_path)
    calls = []
    def respond(request):
        calls.append(1)
        return httpx.Response(200, json={})
    async with httpx.AsyncClient(transport=guarded_transport(item, model="synthetic", endpoint="https://provider.invalid/v1",
                                        underlying=httpx.MockTransport(respond))) as client:
        token = OPERATION.set("0")
        try:
            with pytest.raises(ValueError):
                await client.post("https://other.invalid/v1/chat/completions", content=body())
            with pytest.raises(ValueError):
                await client.post("https://provider.invalid/v1/chat/completions", content=body(model="different"))
        finally:
            OPERATION.reset(token)
    assert not calls and not item.dispatched()


@pytest.mark.asyncio
async def test_partial_usage_is_unknown_and_does_not_refund_reservations(tmp_path):
    item = ledger(tmp_path)
    transport = httpx.MockTransport(lambda request: httpx.Response(500, json={"usage": {"prompt_tokens": 10}}))
    async with httpx.AsyncClient(transport=guarded_transport(item, model="synthetic", endpoint="https://provider.invalid/v1", underlying=transport)) as client:
        token = OPERATION.set("0")
        try:
            response = await client.post("https://provider.invalid/v1/chat/completions", content=body())
        finally:
            OPERATION.reset(token)
    assert response.status_code == 500
    assert item.snapshot()["output_reserved"] == 10 and item.snapshot()["usage_unknown_requests"] == 1


def test_plan_binds_all616_cases_and_existing_exhausted_authorization():
    from scripts.evaluate_m5_live import ROOT, operations
    from scripts.evaluate_m5 import Dataset
    dataset = Dataset.model_validate_json((ROOT / "acceptance/m5/adjudicated-v2/dataset.json").read_bytes())
    ops = operations(dataset)
    assert len(ops) == 1027 and sum(ops.values()) == 368128
    assert sum(op.endswith(":generation") for op in ops) == 411
    assert sum(op.endswith(":judge") for op in ops) == 616


def test_missing_new_authorization_does_not_create_ledger_or_provider(tmp_path, monkeypatch):
    from scripts import evaluate_m5_live as module
    target = tmp_path / "plan.json"
    target.write_text(json.dumps(plan()), encoding="utf-8")
    monkeypatch.setattr(module, "PLAN_PATH", target)
    monkeypatch.setattr(module, "LIVE_ROOT", tmp_path / "live")
    with pytest.raises(ValueError, match="new_explicit_user_authorization_required"):
        module.require_authorization(deepcopy(plan()))
    assert not (tmp_path / "live").exists()
