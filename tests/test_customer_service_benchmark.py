import json
from pathlib import Path

from scripts.customer_service_benchmark import run_benchmark
from app.agent.intent import EnhancedIntentRecognizer


DATASET_PATH = Path(__file__).parents[1] / "scripts" / "customer_service_benchmark.json"


def test_customer_service_benchmark_dataset_has_required_coverage():
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))

    assert len(dataset) >= 40
    assert sum(item.get("expected_intent") == "technical" for item in dataset) >= 10
    assert sum(item.get("expected_intent") == "promotion" for item in dataset) >= 8
    assert sum(item.get("expected_intent") == "query_order" for item in dataset) >= 10
    assert sum("conversation_id" in item for item in dataset) >= 8
    assert sum(item.get("expected_intent") == "general" for item in dataset) >= 3


def test_customer_service_benchmark_reports_all_requested_metrics():
    dataset = [{
        "id": "sample",
        "question": "优惠券怎么使用？",
        "expected_intent": "promotion",
        "relevant_titles": ["优惠券规则"],
        "tool_order_id": "ORD-DOES-NOT-MATTER",
        "tool_user_id": 1,
    }]

    class FakeKnowledgeBase:
        def search(self, query, top_k=3):
            return {"results": [{"title": "优惠券规则", "content": "规则"}]}

    report = run_benchmark(FakeKnowledgeBase(), dataset)
    metrics = report["metrics"]
    assert set(metrics) >= {
        "intent_accuracy", "rag_hit_rate", "citation_completeness", "tool_call_success_rate"
    }
    assert report["dataset_size"] == 1


def test_explicit_refund_action_takes_priority_over_order_identifier():
    recognizer = EnhancedIntentRecognizer()

    assert recognizer.recognize("查询订单 ORD20260901").intent == "query_order"
    assert recognizer.recognize("我要退款，订单号 ORD20260901，商品不合适").intent == "refund"
