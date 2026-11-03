from scripts.evaluate_m4_live import score


def test_invalid_output_cannot_get_entity_credit_for_empty_expected_entities():
    row = dict(split="independent", expected={"topic": "general", "request_type": "clarify"},
               parsed=None, latency_ms=12)
    result = score([row] * 300)
    assert result["entities_accuracy"] == 0
    assert result["schema_failures"] == 300
    assert not result["gate"]


def test_failures_stay_in_latency_and_accuracy_denominators():
    good = dict(split="independent", expected={"topic": "general", "request_type": "inquiry"},
                parsed={"topic": "general", "request_type": "inquiry", "entities": {}}, latency_ms=5)
    bad = {**good, "parsed": None, "latency_ms": 30000}
    result = score([good] * 270 + [bad] * 30)
    assert result["samples"] == 300
    assert result["entities_accuracy"] == .9
    assert result["p95_ms"] == 30000
    assert not result["gate"]
