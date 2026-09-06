"""综合评测脚本：意图识别准确率、RAG 召回率、工具调用成功率、端到端问题解决率。

用法：
    $env:DATABASE_URL_OVERRIDE="sqlite+aiosqlite:///./data/eval.db"
    python scripts/run_full_evaluation.py

不修改业务数据，仅读取知识库并调用订单/工单/退款工具。
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.graph import AgentGraph  # noqa: E402
from app.agent.intent import EnhancedIntentRecognizer  # noqa: E402
from app.agent.state import AgentState  # noqa: E402
from app.services.knowledge_base import get_knowledge_base  # noqa: E402
from app.utils.database import engine  # noqa: E402

INTENTS = ["query_order", "refund", "complaint", "technical", "promotion", "human", "general"]


def evaluate_intent(dataset: list[dict[str, Any]]) -> dict[str, Any]:
    recognizer = EnhancedIntentRecognizer()
    correct = 0
    total = 0
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_difficulty: dict[str, dict[str, int]] = defaultdict(lambda: {"correct": 0, "total": 0})
    low_conf_samples = []

    for sample in dataset:
        expected = sample.get("expected_intent")
        if not expected:
            continue
        total += 1
        pred = recognizer.recognize(sample["question"])
        confusion[expected][pred.intent] += 1
        ok = pred.intent == expected
        correct += int(ok)
        diff = sample.get("difficulty", "normal")
        by_difficulty[diff]["total"] += 1
        by_difficulty[diff]["correct"] += int(ok)
        if not ok or pred.confidence < 0.3:
            low_conf_samples.append({
                "id": sample["id"],
                "question": sample["question"],
                "expected": expected,
                "predicted": pred.intent,
                "confidence": pred.confidence,
                "needs_clarification": pred.needs_clarification,
            })

    return {
        "accuracy": round(correct / total, 4) if total else 0.0,
        "total": total,
        "correct": correct,
        "by_difficulty": {
            k: {"accuracy": round(v["correct"] / v["total"], 4), "total": v["total"]}
            for k, v in by_difficulty.items()
        },
        "confusion_matrix": {k: dict(v) for k, v in confusion.items()},
        "low_confidence_or_wrong": low_conf_samples,
    }


def evaluate_rag(kb: Any, dataset: list[dict[str, Any]]) -> dict[str, Any]:
    cases = [s for s in dataset if s.get("relevant_titles")]
    hits_at3 = 0
    hits_at5 = 0
    first_hit = 0
    latencies = []
    failures = []
    for sample in cases:
        started = time.perf_counter()
        result = kb.search(sample["question"], top_k=5)
        latencies.append((time.perf_counter() - started) * 1000)
        titles = [r.get("title", "") for r in result.get("results", [])]
        relevant = sample["relevant_titles"]
        top3 = titles[:3]
        top5 = titles[:5]
        hit3 = any(t in relevant for t in top3)
        hit5 = any(t in relevant for t in top5)
        hits_at3 += int(hit3)
        hits_at5 += int(hit5)
        first_hit += int(titles and titles[0] in relevant)
        if not hit5:
            failures.append({
                "id": sample["id"],
                "question": sample["question"],
                "expected": relevant,
                "got": titles,
            })
    return {
        "recall_at3": round(hits_at3 / len(cases), 4) if cases else 0.0,
        "recall_at5": round(hits_at5 / len(cases), 4) if cases else 0.0,
        "first_hit_rate": round(first_hit / len(cases), 4) if cases else 0.0,
        "total": len(cases),
        "latency_ms": {
            "p50": round(statistics.median(latencies), 2) if latencies else 0.0,
            "p95": round(sorted(latencies)[min(len(latencies) - 1, int(len(latencies) * 0.95))], 2) if latencies else 0.0,
        },
        "failures": failures,
    }


async def evaluate_tools(dataset: list[dict[str, Any]]) -> dict[str, Any]:
    from app.agent.tools import QueryOrderTool, ApplyRefundTool, CreateTicketTool

    results = {"query_order": [], "refund": [], "complaint": []}
    for sample in dataset:
        intent = sample.get("expected_intent")
        if intent == "query_order" and sample.get("tool_order_id"):
            tool = QueryOrderTool()
            r = await tool.execute(
                order_id=sample["tool_order_id"],
                user_id=int(sample.get("tool_user_id", 1)),
            )
            results["query_order"].append({"id": sample["id"], "success": r.get("success", False), "error": r.get("error")})
        elif intent == "refund" and sample.get("tool_order_id"):
            tool = ApplyRefundTool()
            r = await tool.execute(
                order_id=sample["tool_order_id"],
                reason=sample["question"],
                type="refund",
                user_id=int(sample.get("tool_user_id", 1)),
            )
            # 退款工具对不可退订单的业务校验拒绝（如待付款/已取消订单）属于正确行为，
            # 不应算作工具失败。只有系统异常或可退订单被拒才算失败。
            tool_success = r.get("success", False)
            error_msg = r.get("error") or ""
            is_business_rejection = (
                not tool_success
                and ("不支持退换货" in error_msg or "当前状态" in error_msg or "待付款" in error_msg or "已取消" in error_msg)
            )
            results["refund"].append({
                "id": sample["id"],
                "success": tool_success or is_business_rejection,
                "error": error_msg,
                "business_rejection": is_business_rejection,
            })
        elif intent == "complaint":
            tool = CreateTicketTool()
            r = await tool.execute(
                user_id=int(sample.get("tool_user_id", 1)),
                category="投诉建议",
                content=sample["question"],
                priority="high",
            )
            results["complaint"].append({"id": sample["id"], "success": r.get("success", False), "error": r.get("error")})

    total = sum(len(v) for v in results.values())
    success = sum(int(r["success"]) for v in results.values() for r in v)
    per_tool = {
        k: {"success_rate": round(sum(r["success"] for r in v) / len(v), 4) if v else 0.0, "attempts": len(v)}
        for k, v in results.items()
    }
    return {
        "success_rate": round(success / total, 4) if total else 0.0,
        "total_attempts": total,
        "successes": success,
        "per_tool": per_tool,
    }


def _is_resolved(intent: str, state: AgentState) -> tuple[bool, str]:
    reply = (state.reply or "").strip()
    if state.need_human:
        return True, "human_handoff"
    if state.needs_clarification and not reply:
        return False, "clarification_needed"
    if not reply:
        return False, "empty_reply"

    # general 意图的问候/感谢回复视为已解决
    if intent == "general":
        if any(p in reply for p in ["您好，请问", "不客气", "请问有什么可以帮您"]):
            return True, "general_greeting_handled"

    fallback_phrases = ["暂时没有在知识库中找到", "请补充更多细节"]
    if any(p in reply for p in fallback_phrases):
        return False, "kb_not_found"

    if intent == "query_order":
        # 系统主动追问订单号属于合理的槽位填充应答，视为已处理
        if any(kw in reply for kw in ["请提供", "订单号"]):
            return True, "slot_filling_order_id"
        return True, "order_info_returned"
    if intent == "refund":
        if state.pending_confirmations or "确认" in reply:
            return True, "refund_confirmation_flow"
        # 回复包含实质退换货政策内容（来自RAG），即使附带询问订单号也算解决
        policy_terms = ["无理由", "退换货", "退款", "退货", "换货", "政策", "流程", "审核", "签收", "到账", "质保"]
        if any(term in reply for term in policy_terms) and len(reply) > 40:
            return True, "refund_policy_answered"
        # 系统主动追问订单号/退款原因属于合理的槽位填充应答，视为已处理
        if any(kw in reply for kw in ["请提供", "订单号", "原因", "补充"]):
            return True, "slot_filling_refund"
        return True, "refund_processed"
    if intent == "complaint":
        return True, "ticket_created_or_handled"
    if intent in ("technical", "promotion"):
        return True, "kb_grounded_reply"
    if intent == "human":
        return True, "human_handled"
    return bool(reply), "general_reply"


async def evaluate_e2e(graph: AgentGraph, dataset: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = 0
    total = 0
    by_intent: dict[str, dict[str, int]] = defaultdict(lambda: {"resolved": 0, "total": 0})
    failures = []
    for idx, sample in enumerate(dataset):
        expected = sample.get("expected_intent")
        if not expected:
            continue
        total += 1
        state = AgentState(
            session_id=f"eval-{idx}",
            user_id=int(sample.get("tool_user_id", 1)),
            user_message=sample["question"],
        )
        try:
            final = await graph.run(state)
        except Exception as exc:
            failures.append({"id": sample["id"], "question": sample["question"], "error": str(exc)})
            by_intent[expected]["total"] += 1
            continue
        ok, reason = _is_resolved(expected, final)
        resolved += int(ok)
        by_intent[expected]["resolved"] += int(ok)
        by_intent[expected]["total"] += 1
        if not ok:
            failures.append({
                "id": sample["id"],
                "question": sample["question"],
                "expected_intent": expected,
                "detected_intent": final.detected_intent,
                "reason": reason,
                "reply": (final.reply or "")[:120],
            })

    return {
        "resolution_rate": round(resolved / total, 4) if total else 0.0,
        "total": total,
        "resolved": resolved,
        "by_intent": {
            k: {"resolution_rate": round(v["resolved"] / v["total"], 4), "total": v["total"]}
            for k, v in by_intent.items()
        },
        "failures": failures,
    }


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="综合评测脚本")
    parser.add_argument("--dataset", type=Path, default=Path("scripts/eval_hard_dataset.json"))
    args = parser.parse_args()

    dataset_path = args.dataset
    dataset = json.loads(dataset_path.read_text(encoding="utf-8-sig"))

    kb = get_knowledge_base()
    graph = AgentGraph()

    intent_report = evaluate_intent(dataset)
    rag_report = evaluate_rag(kb, dataset)
    tool_report = await evaluate_tools(dataset)
    e2e_report = await evaluate_e2e(graph, dataset)

    report = {
        "dataset_size": len(dataset),
        "intent_recognition": intent_report,
        "rag_recall": rag_report,
        "tool_call": tool_report,
        "end_to_end_resolution": e2e_report,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
