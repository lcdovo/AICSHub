"""复核来源、固定分母与关键边界诊断的离线回归；不访问真实供应商。"""
import copy
import json

import pytest

from app.agent.request_understanding import RequestUnderstanding
from app.agent.state import AgentState
from scripts import evaluate_model_first_admission as base
from scripts import evaluate_model_first_quality as quality
from scripts import revalidate_model_first as critical


def fixture_row(case):
    state = AgentState(session_id="fixture", user_id=990007, user_message=case["message"],
                       understanding_mode="model", messages=case.get("history", []),
                       detected_intent=case.get("previous_intent", "general"),
                       collected_info={"order_id": case["known_order"]} if case.get("known_order") else {})
    return {"case_id": case["case_id"], "latency_ms": 1,
            **base.assess_case(case, RequestUnderstanding.model_validate(case["mock"]), state)}


@pytest.mark.parametrize("case", quality.load_data()["cases"], ids=lambda c: c["case_id"])
def test_reviewed_fixture_has_valid_sources_and_expected_execution(case):
    assert fixture_row(case)["passed"]


@pytest.mark.parametrize("case", json.loads(critical.DATA.read_bytes())["cases"], ids=lambda c: c["case_id"])
def test_critical_fixture_has_no_write_plan(case):
    row = fixture_row(case)
    assert row["passed"] and row["protected"] and row["business_tools_executed"] == 0


def test_quality_metrics_and_provenance():
    data = quality.load_data()
    result = quality.score([fixture_row(c) for c in data["cases"]], data)
    assert result["local_quality_pass"]
    assert result["metrics"]["macro_f1"] == 1
    assert not result["whole_admission_pass"] and not result["formal_admission_gold"]
    assert result["metrics"]["condition_samples"] >= 50
    assert result["metrics"]["multi_read_samples"] >= 50


def test_missing_invalid_and_timeouts_stay_in_all_denominators():
    data = quality.load_data()
    rows = [fixture_row(c) for c in data["cases"]]
    for index in range(20):
        rows[index] = {"case_id": rows[index]["case_id"], "schema_valid": False, "protected": True}
    result = quality.score(rows, data)
    assert result["metrics"]["schema_validity"] == 280 / 300
    assert result["metrics"]["entity_accuracy"] == 280 / 300
    assert result["metrics"]["p95_ms"] > 3000
    assert not result["local_quality_pass"]
    empty = quality.score([], data)
    assert empty["metrics"]["schema_validity"] == 0
    assert not empty["local_quality_pass"]


def test_duplicate_or_unknown_result_cannot_pass():
    data = quality.load_data()
    row = fixture_row(data["cases"][0])
    with pytest.raises(ValueError, match="duplicate_or_unexpected"):
        quality.score([row, row], data)
    with pytest.raises(ValueError, match="duplicate_or_unexpected"):
        quality.score([{**row, "case_id": "unknown"}], data)


def test_no_condition_denominator_never_passes():
    data = copy.deepcopy(quality.load_data())
    for case in data["cases"]:
        case["expected"].pop("condition", None)
        case["expected"]["required_reads"] = []
    result = quality.score([fixture_row(c) for c in data["cases"]], data)
    assert not result["gates"]["condition"] and not result["gates"]["multi_read"]


def test_rejected_semantics_are_protected_but_functionally_wrong():
    case = json.loads(critical.DATA.read_bytes())["cases"][0]
    value = RequestUnderstanding.model_validate(case["mock"])
    value.entities.order_id = "ORD999999"
    state = AgentState(session_id="test", user_id=990007, user_message=case["message"])
    row = base.assess_case(case, value, state)
    assert row["protected"] and not row["passed"] and not row["schema_valid"]


def test_quality_cannot_dispatch_without_delegation(tmp_path, monkeypatch):
    monkeypatch.setattr(quality, "DELEGATION", tmp_path / "missing.json")
    monkeypatch.setattr(base, "authorized_limits", lambda: {"max_requests": 372})
    with pytest.raises(FileNotFoundError):
        quality.quality_authorized()
    assert not (tmp_path / "dispatch.sqlite").exists()


def test_quality_cannot_freeze_before_critical_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(quality, "quality_authorized", lambda: {})
    monkeypatch.setattr(base, "prepared_plan", lambda: {"source_fingerprint": "frozen"})
    report = tmp_path / "critical.json"
    report.write_text(json.dumps({"critical_pass": False}))
    monkeypatch.setattr(quality, "CRITICAL", report)
    with pytest.raises(ValueError, match="critical_gate_missing"):
        quality.build_quality_prepared()
