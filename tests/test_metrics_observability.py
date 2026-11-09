from app.utils.tracking import MetricsCollector


def test_http_health_and_chat_latency_are_separate_and_windowed():
    metrics = MetricsCollector()
    for _ in range(1000): metrics.record_response(2)
    metrics.record_chat(latency_ms=4000, accepted=True)
    metrics.record_chat(latency_ms=9000, accepted=False)
    result = metrics.get_all_metrics()
    assert result["response_time"]["p95_ms"] == 2
    assert result["chat_response_time"]["p95_ms"] == 9000
    assert result["chat_response_time"]["rejected"] == 1
    assert "app_chat_latency_p95_ms 9000" in metrics.to_prometheus()


def test_gpu_events_count_dispatched_calls_and_report_admission_queue():
    metrics = MetricsCollector()
    metrics.record_local_model_events([
        {"purpose":"embedding", "dispatched":True, "inference_ms":40},
        {"purpose":"rerank", "dispatched":True, "inference_ms":60},
        {"purpose":"retrieval", "dispatched":False, "admission_scope":"redis", "queue_ms":12, "status":"admitted"},
        {"purpose":"retrieval", "dispatched":False, "admission_scope":"not_acquired", "queue_ms":50, "status":"busy"}])
    data = metrics.get_all_metrics()["local_models"]
    assert data["dispatches"] == 2 and data["busy"] == 1
    assert data["admission_queue"]["p95_ms"] == 50
