"""告警规则回归测试：build_alert_input 字段映射 + AlertManager 各规则触发。"""
import pytest

from app.utils.tracking import AlertManager, build_alert_input


def _metrics(success_rate=99.0, handoff_rate=5.0, avg_ms=800.0, total=100, low_score=2):
    return {
        "success_rate": success_rate,
        "handoff_rate": handoff_rate,
        "response_time": {"avg_ms": avg_ms},
        "evaluation": {"total": total, "low_score": low_score},
    }


def test_build_alert_input_maps_fields():
    alert_input = build_alert_input(_metrics())
    assert alert_input["error_rate"] == pytest.approx(1.0)
    assert alert_input["handoff_rate"] == pytest.approx(5.0)
    assert alert_input["avg_response_time"] == pytest.approx(800.0)
    assert alert_input["intent_accuracy"] == pytest.approx(98.0)


def test_no_alerts_on_healthy_metrics():
    alerts = AlertManager().check_alerts(build_alert_input(_metrics()))
    assert alerts == []


def test_error_rate_rule_triggers_p0():
    alerts = AlertManager().check_alerts(build_alert_input(_metrics(success_rate=90.0)))
    rules = {a["rule"] for a in alerts}
    assert "system_error_rate" in rules
    assert any(a["level"] == "P0" for a in alerts)


def test_avg_response_time_rule_triggers():
    alerts = AlertManager().check_alerts(build_alert_input(_metrics(avg_ms=8000.0)))
    rules = {a["rule"] for a in alerts}
    assert "avg_response_time" in rules


def test_intent_accuracy_rule_triggers():
    alerts = AlertManager().check_alerts(build_alert_input(_metrics(total=100, low_score=40)))
    rules = {a["rule"] for a in alerts}
    assert "intent_accuracy" in rules


def test_handoff_rate_rule_triggers():
    alerts = AlertManager().check_alerts(build_alert_input(_metrics(handoff_rate=50.0)))
    rules = {a["rule"] for a in alerts}
    assert "human_handoff_rate" in rules
