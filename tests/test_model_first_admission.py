"""新授权冒烟执行器的离线校验，不派发真实供应商请求。"""
import json

import pytest

from scripts import evaluate_model_first_admission as evaluator
from app.agent.request_understanding import RequestUnderstanding
from app.agent.state import AgentState


def cases():
    return json.loads(evaluator.DATA.read_bytes())["cases"]


def test_scope_limits_reserve_all_phases_without_reusing_old_operations():
    ops = evaluator.operations()
    assert len(ops) == 372 and sum(ops.values()) == 186000
    assert len([key for key in ops if key.startswith("smoke-")]) == 6
    assert len([key for key in ops if key.startswith("startup-")]) == 6
    assert max(ops.values()) == 500


@pytest.mark.parametrize("case", cases(), ids=lambda case: case["case_id"])
def test_protocol_fixtures_match_actual_schema_and_sources(case):
    state = AgentState(session_id="test", user_id=990007, user_message=case["message"])
    row = evaluator.assess_case(case, RequestUnderstanding.model_validate(case["mock"]), state)
    assert row["passed"] and row["schema_valid"]
    assert row["business_tools_executed"] == 0


def test_missing_output_is_failure_not_a_valid_empty_entity_result():
    case = cases()[0]
    state = AgentState(session_id="test", user_id=990007, user_message=case["message"])
    row = evaluator.assess_case(case, None, state)
    assert not row["passed"] and not row["schema_valid"]


def test_invalid_source_stops_semantic_acceptance():
    case = cases()[0]
    value = RequestUnderstanding.model_validate(case["mock"])
    value.entities.order_id = "ORD999999"
    state = AgentState(session_id="test", user_id=990007, user_message=case["message"])
    row = evaluator.assess_case(case, value, state)
    assert not row["passed"] and not row["schema_valid"]


def test_missing_new_authorization_cannot_initialize_a_live_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluator, "RUN", tmp_path)
    with pytest.raises(FileNotFoundError):
        evaluator.authorized_limits()
    assert not (tmp_path / "dispatch.sqlite").exists()


def test_rehearsal_cannot_overwrite_live_report(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluator, "RUN", tmp_path)
    with pytest.raises(ValueError, match="mock_output"):
        evaluator.worker({}, tmp_path / "current", True)


async def test_full_sdk_mock_rehearsal_is_six_calls_but_not_model_quality(tmp_path, monkeypatch):
    from app.config.config import settings

    config = {
        "LLM_API_KEY": "synthetic-key", "LLM_API_BASE": "https://token.uino.com/v1",
        "LLM_MODEL": "deepseek-flash", "LLM_STRUCTURED_OUTPUT_METHOD": "function_calling",
        "LLM_THINKING_MODE": "disabled", "LLM_BACKUP_BASE": "", "LLM_BACKUP_MODEL": "",
        "LLM_LOCAL_BASE": "", "LLM_LOCAL_MODEL": "", "LLM_GATEWAY_ENABLED": True,
        "LLM_MAX_TOKENS": 500, "LLM_MAX_COST_USD": 1.,
        "LLM_DAILY_USER_TOKEN_BUDGET": 1000000, "LLM_DAILY_USER_COST_USD": 100.,
    }
    for key, value in config.items():
        monkeypatch.setattr(settings, key, value)
    limits = {"max_requests": 372, "max_input_tokens": 9142272, "max_output_tokens": 186000}
    row = await evaluator.pipeline({"budget_scope_id": "unit-scope", "prepared_id": "unit-prepared"},
                                   tmp_path, limits, True)
    assert row["status"] == "MOCK_PROTOCOL_ONLY" and row["smoke_pass"]
    assert row["ledger"]["requests"] == 6 and row["external_model_calls"] == 0
    assert row["ledger"]["output_reserved"] == 3000
    assert not row["whole_admission_pass"] and not row["bulk_live_allowed"]
    assert not row["default_changed"] and not row["independent_human_labels"]
    # 同一操作重放读取原输出，不增加任何派发。
    repeated = await evaluator.pipeline({"budget_scope_id": "unit-scope", "prepared_id": "unit-prepared"},
                                        tmp_path, limits, True)
    assert repeated["ledger"]["requests"] == 6
