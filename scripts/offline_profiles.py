"""Versioned, non-secret configuration and coverage for isolated regression."""
ACTIVE_CONFIG = {
    "REQUEST_UNDERSTANDING_MODE": "model",
    "REQUEST_UNDERSTANDING_PERCENTAGE": "100",
    "AGENT_TASK_LOOP_ENABLED": "true",
    "AGENT_TASK_REFUND_PREVIEW_ENABLED": "true",
    "AGENT_ITEM_COMPLETION_ENABLED": "true",
    "LLM_GATEWAY_ENABLED": "true",
    "LLM_JUDGE_ENABLED": "true",
    "LLM_JUDGE_CONTRACT_VERSION": "v2",
    "LLM_JUDGE_V2_OUTPUT_PROTOCOL": "json_object",
    "LLM_MAX_RETRIES": "0",
    "LLM_MAX_TOKENS": "2048",
    "RERANKER_LOCAL_BATCH_SIZE": "20",
}
ACTIVE_TESTS = [
    "tests/test_active_runtime.py",
    "tests/test_intent_items.py",
    "tests/test_item_judge_v2.py",
    "tests/test_judge_json_protocol.py",
    "tests/test_item_reply.py",
    "tests/test_agent_task_loop.py",
    "tests/test_agent_reliability.py",
    "tests/test_rerank_request_count.py",
]
COVERAGE = {
    "understanding": "model interpretation, clarification and invalid results",
    "task_loop": "bounded decisions, deadlines, tools and current-turn confirmation",
    "item_review": "v2 claim/item binding, stale/cross-item evidence and publication",
    "provider_protocol": "real SDK with MockTransport: valid/invalid/truncated JSON",
    "transport": "buffered item output; synthetic SDK and graph components, not paid E2E",
}
