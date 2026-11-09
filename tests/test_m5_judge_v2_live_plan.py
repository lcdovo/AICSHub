from copy import deepcopy
import json

import pytest

from scripts.build_m5_judge_v2_cases import build_cases
from scripts.evaluate_m5_judge_v2_live import authorization, load_dataset, mock_payload
from app.agent.judge_contracts import assess_v2


def test_frozen_counts_p0_and_known_scope_inputs_are_separate_from_new_clusters():
    data = load_dataset()
    assert data == build_cases()
    assert data["counts"] == {"smoke": 6, "known": 30, "new_negative": 100, "new_positive": 100, "boundary": 20}
    assert len(data["cases"]) == 256 and sum(case["p0"] for case in data["cases"]) == 20
    assert len({case["cluster_id"] for case in data["cases"] if case["phase"].startswith("new_")}) == 25
    assert not data["independent_human_labels"] and not data["whole_m5_pass"]
    assert all(case["category"] != "known_scope" or "不只" in case["candidate"] for case in data["cases"])


def test_all_protocol_mock_outputs_are_explicit_fixtures_with_valid_quote_bindings():
    for case in load_dataset()["cases"]:
        raw = mock_payload(case)
        result = assess_v2(raw, case["candidate"], case["evidence"], case["tool_evidence"], case["question"])
        assert result["publish_allowed"] == case["expected_allowed"], case["case_id"]


def test_missing_bounded_authorization_creates_no_paid_ledger(tmp_path, monkeypatch):
    import scripts.evaluate_m5_judge_v2_live as module
    plan = {"plan_id": "synthetic", "proposed_limits": {"max_requests": 256, "max_input_tokens": 4000000, "max_output_tokens": 262144}}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setattr(module, "PLAN", path)
    monkeypatch.setattr(module, "RUN", tmp_path / "paid")
    with pytest.raises(ValueError, match="explicit_new_request_and_token_limits_required"):
        authorization(plan)
    assert not (tmp_path / "paid").exists()


@pytest.mark.parametrize("change", [{"max_requests": 257}, {"max_input_tokens": 4000001}, {"max_output_tokens": 262145}, {"approved_by": "assistant"}])
def test_generic_or_expanded_authorization_cannot_initialize_dispatch(tmp_path, monkeypatch, change):
    import scripts.evaluate_m5_judge_v2_live as module
    plan = {"plan_id": "synthetic", "proposed_limits": {"max_requests": 256, "max_input_tokens": 4000000, "max_output_tokens": 262144}}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    directory = tmp_path / "paid"
    directory.mkdir()
    approval = {"plan_id": "synthetic", "approved_by": "user", "approved_at": "2026-11-06", "user_statement": "synthetic explicit approval", **plan["proposed_limits"], **change}
    (directory / "authorization.json").write_text(json.dumps(approval), encoding="utf-8")
    monkeypatch.setattr(module, "PLAN", path)
    monkeypatch.setattr(module, "RUN", directory)
    with pytest.raises(ValueError):
        authorization(deepcopy(plan))
    assert not (directory / "dispatch.sqlite").exists()


@pytest.mark.asyncio
async def test_failed_smoke_prevents_every_bulk_provider_dispatch(tmp_path, monkeypatch):
    import scripts.evaluate_m5_judge_v2_live as module
    from app.config.config import settings
    changes = {"LLM_API_KEY": "synthetic-key", "LLM_API_BASE": "https://offline.invalid/v1", "LLM_MODEL": "synthetic",
        "LLM_JUDGE_CONTRACT_VERSION": "v2", "LLM_JUDGE_ENABLED": True, "LLM_GATEWAY_ENABLED": True,
        "LLM_STRUCTURED_OUTPUT_METHOD": "function_calling",
        "LLM_MAX_TOKENS": 1024, "LLM_MAX_CONCURRENCY": 1, "LLM_MAX_COST_USD": 1,
        "LLM_DAILY_USER_TOKEN_BUDGET": 1000000, "LLM_DAILY_USER_COST_USD": 100,
        "LLM_BACKUP_BASE": "", "LLM_LOCAL_BASE": ""}
    for name, value in changes.items():
        monkeypatch.setattr(settings, name, value)
    original = module.mock_payload
    def bad_first(case):
        if case["case_id"] == "smoke-condition":
            return {"schema_version": 2, "publish_allowed": True}  # 严格schema必须拒绝。
        return original(case)
    monkeypatch.setattr(module, "mock_payload", bad_first)
    monkeypatch.chdir(tmp_path)
    data = load_dataset()
    plan = {"plan_id": "synthetic-plan", "operations": {case["case_id"] + ":judge": 1024 for case in data["cases"]}}
    result = await module.pipeline(plan, tmp_path / "results", {
        "max_requests": 256, "max_input_tokens": 4000000, "max_output_tokens": 262144}, True)
    assert result["status"] == "STOPPED_SMOKE_FAILED"
    assert result["ledger"]["requests"] == result["evaluated_cases"] == 6
    assert all(row["phase"] == "smoke" for row in result["rows"])
    assert result["external_model_calls"] == 0 and not result["whole_m5_pass"]


@pytest.mark.asyncio
async def test_other_structured_protocol_is_rejected_before_creating_paid_ledger(tmp_path, monkeypatch):
    import scripts.evaluate_m5_judge_v2_live as module
    from app.config.config import settings
    monkeypatch.setattr(settings, "LLM_STRUCTURED_OUTPUT_METHOD", "json_schema")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="requires_function_calling"):
        await module.pipeline({}, tmp_path / "results", {}, True)
    assert not list(tmp_path.glob("*.sqlite"))


@pytest.mark.asyncio
async def test_source_drift_stops_before_any_next_dispatch(tmp_path, monkeypatch):
    import scripts.evaluate_m5_judge_v2_live as module
    from app.config.config import settings
    monkeypatch.setattr(settings, "LLM_STRUCTURED_OUTPUT_METHOD", "function_calling")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("scripts.test_provenance.snapshot_sources", lambda root: {"fingerprint": "changed", "unsupported_paths": []})
    data = load_dataset()
    plan = {"plan_id": "synthetic", "source_fingerprint": "frozen",
            "operations": {case["case_id"] + ":judge": 1024 for case in data["cases"]}}
    result = await module.pipeline(plan, tmp_path / "results", {
        "max_requests": 256, "max_input_tokens": 4000000, "max_output_tokens": 262144}, True)
    assert result["status"] == "STOPPED_SOURCE_DRIFT_BEFORE_NEXT_DISPATCH"
    assert result["ledger"]["requests"] == result["evaluated_cases"] == 0
