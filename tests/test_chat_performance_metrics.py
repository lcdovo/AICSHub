from app.utils.tracking import MetricsCollector


def test_health_http_samples_do_not_dilute_chat_p95():
    metrics = MetricsCollector()
    for _ in range(1000):
        metrics.record_response(1)
    metrics.record_chat(latency_ms=1000, accepted=True)
    metrics.record_chat(latency_ms=3000, accepted=False)
    result = metrics.get_all_metrics()
    assert result["response_time"]["p95_ms"] == 1
    assert result["chat_response_time"]["p95_ms"] == 3000
    assert result["chat_response_time"]["count"] == 2
    assert result["chat_response_time"]["rejected"] == 1
    assert "app_chat_latency_p95_ms 3000" in metrics.to_prometheus()


def test_local_events_count_only_real_dispatches_and_bounded_windows():
    metrics = MetricsCollector()
    events = [{"purpose":"rerank", "dispatched":True, "queue_ms":1, "inference_ms":25},
        {"purpose":"retrieval", "dispatched":False, "queue_ms":7, "admission_scope":"redis", "status":"admitted"},
        {"purpose":"embedding_batch", "dispatched":False},
        {"purpose":"retrieval", "dispatched":False, "queue_ms":500, "admission_scope":"not_acquired", "status":"busy"}]
    metrics.record_local_model_events(events)
    result = metrics.get_all_metrics()["local_models"]
    assert result["dispatches"] == result["rerank_dispatches"] == 1
    assert result["busy"] == 1
    assert result["admission_queue"]["p95_ms"] == 500
    for index in range(2100):
        metrics.record_chat(latency_ms=index, accepted=True)
    assert metrics.get_all_metrics()["chat_response_time"]["count"] == 2000
    metrics.reset()
    assert metrics.get_all_metrics()["chat_response_time"]["count"] == 0
    assert metrics.get_all_metrics()["local_models"]["dispatches"] == 0
