"""离线意图识别评估脚本：量化规则识别器的判准与混淆。

为什么做：意图识别是 13 节点 LangGraph 的路由起点，识别错了后面全错；
          本脚本用标注测试集逐条跑线上同一条规则链路
          （``EnhancedIntentRecognizer.recognize``），输出总体准确率、
          按意图精确率/召回率/F1、混淆矩阵与置信度分布，据此定位
          关键词/否定词窗口/澄清阈值的调优方向。

用法：
    python scripts/evaluate_intents.py
    python scripts/evaluate_intents.py --data data/eval/intent_eval_set.json --limit 20
    python scripts/evaluate_intents.py --report-dir reports

前置条件：纯规则识别、无 LLM 依赖，任意环境可离线运行。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# 与 app.agent.state.AgentIntent 保持一致（脚本内不引 app 常量，避免循环依赖）。
INTENTS = ["query_order", "refund", "complaint", "technical", "promotion", "human", "general"]
CONF_BUCKETS = [(0.0, 0.4), (0.4, 0.7), (0.7, 1.0)]


@dataclass
class IntentEval:
    """单条样本的识别结果。"""

    expected: str = ""
    message: str = ""
    predicted: str = ""
    confidence: float = 0.0
    needs_clarification: bool = False
    correct: bool = False


def load_eval_set(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"评测数据集不存在: {path}")
    dataset = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(dataset, list) or not dataset:
        raise ValueError(f"评测数据集必须是非空数组: {path}")
    for item in dataset:
        if not str(item.get("message", "")).strip():
            raise ValueError(f"数据集存在空 message: {item}")
        if "expected_intent" not in item or not str(item.get("expected_intent", "")).strip():
            raise ValueError(f"数据集条目缺少 expected_intent: {item}")
    return dataset


def evaluate_dataset(dataset: List[Dict[str, Any]], limit: int) -> List[IntentEval]:
    """逐条调用线上同一条规则识别链路。"""
    from app.agent.intent import EnhancedIntentRecognizer

    recognizer = EnhancedIntentRecognizer()
    results: List[IntentEval] = []
    for item in dataset[:limit] if limit > 0 else dataset:
        message = str(item["message"]).strip()
        context = item.get("context")
        history_intent = item.get("history_intent")
        result = recognizer.recognize(
            message=message,
            context=context if isinstance(context, list) else None,
            history_intent=history_intent if isinstance(history_intent, str) and history_intent else None,
        )
        predicted = result.intent if result.intent else "general"
        expected = str(item["expected_intent"]).strip()
        results.append(IntentEval(
            expected=expected,
            message=message,
            predicted=predicted,
            confidence=float(result.confidence or 0.0),
            needs_clarification=bool(result.needs_clarification),
            correct=predicted == expected,
        ))
    return results


def summarize(results: List[IntentEval]) -> Dict[str, Any]:
    total = len(results) or 1
    correct = sum(1 for r in results if r.correct)
    accuracy = correct / total

    # 按意图的精确率/召回率/F1（expected 为真值视角，predicted 为预测视角）。
    per_intent: Dict[str, Dict[str, float]] = {}
    for intent in INTENTS:
        tp = sum(1 for r in results if r.correct and r.expected == intent)
        fp = sum(1 for r in results if not r.correct and r.predicted == intent)
        fn = sum(1 for r in results if r.expected == intent and r.predicted != intent)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_intent[intent] = {
            "support": tp + fn,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
        }

    # 混淆矩阵（expected x predicted）。
    matrix: Dict[str, Dict[str, int]] = defaultdict(lambda: Counter())
    for r in results:
        matrix[r.expected][r.predicted] += 1

    # 置信度分布分桶。
    conf_dist = Counter()
    for r in results:
        for lo, hi in CONF_BUCKETS:
            if lo <= r.confidence < hi:
                conf_dist[f"{lo}-{hi}"] += 1
                break
        else:
            conf_dist["0.7-1.0"] += 1

    clarification_needed = sum(1 for r in results if r.needs_clarification)
    clarification_hits = sum(
        1 for r in results if r.needs_clarification and r.expected in ("unknown", "general")
    )

    return {
        "total": len(results),
        "accuracy": round(accuracy, 4),
        "per_intent": per_intent,
        "confusion_matrix": {k: dict(v) for k, v in matrix.items()},
        "confidence_distribution": dict(conf_dist),
        "need_clarification_rate": round(clarification_needed / total, 4),
        "clarification_appropriate": round(clarification_hits / max(clarification_needed, 1), 4),
    }


def _build_optimization_advice(results: List[IntentEval]) -> List[str]:
    """基于实测结果的可执行调优建议。"""
    advice: List[str] = []
    summary = summarize(results)
    if summary["accuracy"] < 0.9:
        advice.append(
            f"总准确率 {summary['accuracy']:.1%} 未达 90%，优先处理混淆矩阵热点。"
        )
    confusion = summary["confusion_matrix"]
    mistakes: List[tuple] = []
    for expected, rows in confusion.items():
        for predicted, cnt in rows.items():
            if predicted != expected and cnt > 0:
                mistakes.append((cnt, expected, predicted))
    for cnt, expected, predicted in sorted(mistakes, reverse=True)[:5]:
        advice.append(
            f"「{expected}→{predicted}」误判 {cnt} 条：可补充 {predicted} 的判别关键词或调整权重，"
            "或在意图识别节点增加该组合的规则优先级。"
        )
    conf_dist = summary["confidence_distribution"]
    low = conf_dist.get("0.0-0.4", 0)
    total = summary["total"] or 1
    if low / total > 0.3:
        advice.append(
            f"置信度<0.4 占比 {low}/{total}，过低消息多落 CLARIFICATION：可放宽过短消息澄清条件，"
            "或为 general/unknown 增加context 推断兜底。"
        )
    if summary["need_clarification_rate"] > 0.4:
        advice.append(
            f"澄清触发率 {summary['need_clarification_rate']:.1%} 偏高：可上调 needs_clarification 置信度阈值。"
        )
    if not advice:
        advice.append("当前规则识别器在测试集上表现良好，无需调整；建议扩充多轮上下文样本进一步压测。")
    return advice


def format_report_md(
    dataset: List[Dict[str, Any]],
    results: List[IntentEval],
    summary: Dict[str, Any],
    *,
    data_path: str,
) -> str:
    lines = ["# 意图识别离线评测报告", ""]
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 数据集：`{data_path}`（{summary['total']} 条）")
    lines.append("- 链路：`EnhancedIntentRecognizer.recognize`（规则加权 + 上下文推断，与线上一致）")
    lines.append("")

    lines.append("## 汇总指标")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| accuracy | {summary['accuracy']:.2%} |")
    lines.append(f"| need_clarification_rate | {summary['need_clarification_rate']:.2%} |")
    lines.append(f"| clarification_appropriate | {summary['clarification_appropriate']:.2%} |")
    lines.append("")
    lines.append("| 置信度分布 | 条数 |")
    lines.append("|---|---|")
    for k, v in sorted(summary["confidence_distribution"].items()):
        lines.append(f"| {k} | {v} |")
    lines.append("")

    lines.append("## 按意图 P/R/F1")
    lines.append("| intent | support | precision | recall | f1 |")
    lines.append("|---|---|---|---|---|")
    for intent, m in summary["per_intent"].items():
        if m["support"] == 0:
            continue
        lines.append(f"| {intent} | {m['support']} | {m['precision']:.3f} | {m['recall']:.3f} | {m['f1']:.3f} |")
    lines.append("")

    lines.append("## 混淆矩阵（行=期望，列=预测）")
    lines.append("| expected\\predicted | " + " | ".join(INTENTS) + " |")
    lines.append("|---|---" + "|---" * len(INTENTS) + "|")
    expected_ordered = [i for i in INTENTS if i in summary["confusion_matrix"]]
    for exp in expected_ordered:
        row = summary["confusion_matrix"][exp]
        lines.append(f"| {exp} | " + " | ".join(str(row.get(pred, 0)) for pred in INTENTS) + " |")
    lines.append("")

    lines.append("## 逐条明细")
    lines.append("| expected | predicted | conf | clarify | correct | message |")
    lines.append("|---|---|---|---|---|---|")
    for item, r in zip(dataset, results):
        lines.append(
            f"| {r.expected} | {r.predicted} | {r.confidence:.2f} | {'Y' if r.needs_clarification else '-'} "
            f"| {'✓' if r.correct else '✗'} | {str(item['message'])[:32]} |"
        )
    lines.append("")

    lines.append("## 优化建议")
    for tip in _build_optimization_advice(results):
        lines.append(f"- {tip}")
    lines.append("")
    return "\n".join(lines)


def print_console_summary(summary: Dict[str, Any]):
    print("=== 意图识别离线评测汇总 ===")
    print(f"总样本数      : {summary['total']}")
    print(f"准确率        : {summary['accuracy']:.2%}")
    print(f"澄清触发率    : {summary['need_clarification_rate']:.2%}")
    print(f"澄清适当率    : {summary['clarification_appropriate']:.2%}")
    for intent, m in summary["per_intent"].items():
        if m["support"] > 0:
            print(f"  {intent:<12} P={m['precision']:.3f} R={m['recall']:.3f} F1={m['f1']:.3f} (n={m['support']})")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default="data/eval/intent_eval_set.json", help="评测数据集 JSON 路径")
    parser.add_argument("--limit", type=int, default=0, help="仅评估前 N 条（0 表示全部）")
    parser.add_argument("--report-dir", default="reports", help="报告输出目录")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    data_path = Path(args.data)
    if not data_path.is_absolute():
        data_path = repo_root / data_path

    dataset = load_eval_set(data_path)
    results = evaluate_dataset(dataset, args.limit)
    summary = summarize(results)

    report = format_report_md(
        dataset[: len(results)], results, summary, data_path=str(data_path)
    )
    report_dir = Path(args.report_dir)
    if not report_dir.is_absolute():
        report_dir = repo_root / report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"intent_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    report_path.write_text(report, encoding="utf-8")

    print_console_summary(summary)
    print(f"报告已写入: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())