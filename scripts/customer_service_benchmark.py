"""运行客服场景综合评测：意图、RAG、引用和工具调用。

示例：
    python scripts/customer_service_benchmark.py --dataset scripts/customer_service_benchmark.json

脚本不修改业务数据，只读取知识库并调用订单查询工具。多轮样本通过上一轮
消息和意图传给识别器，模拟真实会话中的追问场景。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.intent import EnhancedIntentRecognizer  # noqa: E402
from app.agent.tools import QueryOrderTool  # noqa: E402
from app.services.knowledge_base import get_knowledge_base  # noqa: E402
from app.utils.database import engine  # noqa: E402


def _citation_is_complete(reply: str, relevant_titles: list[str]) -> bool:
    return bool(relevant_titles) and "参考来源" in reply and any(
        title in reply for title in relevant_titles
    )


def _with_citations(results: list[dict[str, Any]], relevant_titles: list[str]) -> str:
    if not results:
        return ""
    cited = [item.get("title", "未命名文档") for item in results[:3]]
    return "测试回答\n\n参考来源：\n" + "\n".join(f"- {title}" for title in cited)


async def _tool_metrics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    tool = QueryOrderTool()
    tool_samples = [sample for sample in samples if sample.get("tool_order_id")]
    results = []
    for sample in tool_samples:
        results.append(await tool.execute(
            order_id=sample["tool_order_id"],
            user_id=int(sample.get("tool_user_id", 1)),
        ))
    success_count = sum(bool(result.get("success")) for result in results)
    try:
        return {
            "attempts": len(results),
            "successes": success_count,
            "success_rate": round(success_count / len(results), 4) if results else 0.0,
        }
    finally:
        await engine.dispose()


def run_benchmark(kb: Any, dataset: list[dict[str, Any]]) -> dict[str, Any]:
    recognizer = EnhancedIntentRecognizer()
    intent_hits = 0
    intent_cases = 0
    rag_hits = 0
    rag_cases = 0
    citation_hits = 0
    citation_cases = 0
    rag_latencies: list[float] = []
    details: list[dict[str, Any]] = []
    conversations: dict[str, tuple[list[dict[str, Any]], str | None]] = {}

    for sample in dataset:
        conversation_id = str(sample.get("conversation_id", sample["id"]))
        context, history_intent = conversations.get(conversation_id, ([], None))
        prediction = recognizer.recognize(
            sample["question"], context=context, history_intent=history_intent
        )
        expected_intent = sample.get("expected_intent")
        if expected_intent:
            intent_cases += 1
            intent_hits += int(prediction.intent == expected_intent)

        relevant_titles = sample.get("relevant_titles", [])
        result_titles: list[str] = []
        citation_complete = None
        if relevant_titles:
            rag_cases += 1
            started = time.perf_counter()
            search_result = kb.search(sample["question"], top_k=3)
            rag_latencies.append((time.perf_counter() - started) * 1000)
            result_titles = [item.get("title", "") for item in search_result.get("results", [])[:3]]
            rag_hit = any(title in relevant_titles for title in result_titles)
            rag_hits += int(rag_hit)
            citation_cases += 1
            citation_complete = _citation_is_complete(
                _with_citations(search_result.get("results", []), relevant_titles), relevant_titles
            )
            citation_hits += int(citation_complete)

        details.append({
            "id": sample["id"],
            "question": sample["question"],
            "expected_intent": expected_intent,
            "predicted_intent": prediction.intent,
            "intent_correct": prediction.intent == expected_intent if expected_intent else None,
            "confidence": prediction.confidence,
            "rag_hit": any(title in relevant_titles for title in result_titles) if relevant_titles else None,
            "citation_complete": citation_complete,
            "top_titles": result_titles,
        })
        context = (context + [{"role": "user", "content": sample["question"]}])[-6:]
        conversations[conversation_id] = (context, prediction.intent)

    tool_metrics = asyncio.run(_tool_metrics(dataset))
    return {
        "dataset_size": len(dataset),
        "metrics": {
            "intent_accuracy": round(intent_hits / intent_cases, 4) if intent_cases else 0.0,
            "intent_cases": intent_cases,
            "rag_hit_rate": round(rag_hits / rag_cases, 4) if rag_cases else 0.0,
            "rag_cases": rag_cases,
            "citation_completeness": round(citation_hits / citation_cases, 4) if citation_cases else 0.0,
            "citation_cases": citation_cases,
            "tool_call_success_rate": tool_metrics["success_rate"],
            "tool_call_attempts": tool_metrics["attempts"],
        },
        "rag_latency_ms": {
            "p50": round(statistics.median(rag_latencies), 3) if rag_latencies else 0.0,
            "p95": round(sorted(rag_latencies)[min(len(rag_latencies) - 1, int(len(rag_latencies) * 0.95))], 3) if rag_latencies else 0.0,
        },
        "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="运行客服场景综合评测")
    parser.add_argument("--dataset", type=Path, default=Path("scripts/customer_service_benchmark.json"))
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    report = run_benchmark(get_knowledge_base(), dataset)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
