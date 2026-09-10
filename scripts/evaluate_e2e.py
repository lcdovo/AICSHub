"""端到端客服链路评测脚本：量化「问题解决率」与「端到端延迟 p95」。

为什么做：拆开的意图准确率 / 检索质量 / 回答质量都无法回答业务问题——
          "用户问一句，系统最终有没有解决"。本脚本复用线上同一条 LangGraph
          链路（``AgentGraph.run``），用客服评测集的 user_query 逐条驱动真实
          执行，聚合出端到端问题解决率与 p95 延迟。
如何判定"解决"（三级降级，全部复用线上设施、不新造评测器）：
  1) 首选 LLM-as-judge：问"客服回答是否解决了用户诉求"，取 resolved + reason；
  2) LLM 不可用 → ``AnswerEvaluator.overall_score >= 60`` 判已解决（同线上阈值）；
  3) 再降级 → 命中 ``need_human`` / ``human_handoff`` 或回复为空 → 未解决。
注：running 失败（如工具缺数据、外部依赖不可用）按「未解决(链路降级)」计数，
    脚本不中断。LLM 不可用时 p95 降级为检索级延迟，见报告显著标注。

用法：
    python scripts/evaluate_e2e.py
    python scripts/evaluate_e2e.py --data data/eval/cs_eval_set.json --limit 5 --runs 2
    python scripts/evaluate_e2e.py --report-dir reports

前置条件：建议配置 LLM 凭证以启用端到端语义判定与完整链路；无凭证时脚本
          仍可运行，但解决判定退化为启发式且 p95 仅覆盖检索段。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# 与线上 AnswerEvaluator 的低分阈值一致（app/services/evaluation.py）。
HEURISTIC_PASS = 60.0


class _ResolvedVerdict(BaseModel):
    """LLM-as-judge 的"是否解决"判定契约。"""

    resolved: bool
    reason: str = Field(default="", max_length=200)


JUDGE_RESOLVED_PROMPT = (
    "你是客服质检员。针对用户问题，判断客服回答是否解决了用户的诉求。"
    "若回答给出明确答复、操作步骤或正确转人工，则 resolved=true；"
    "若答非所问、拒绝、含糊或回答为空，则 resolved=false。"
    "reason 用 50 字内的一句话说明理由。"
    "只输出结构化结果，不要输出其他内容。"
)


@dataclass
class E2ESample:
    """单条样本的端到端执行结果。"""

    user_query: str = ""
    intent: str = ""
    reply: str = ""
    resolved: bool = False
    resolve_source: str = ""  # judge / heuristic / human_handoff / empty / degraded
    reason: str = ""
    latency_ms: float = 0.0
    need_human: bool = False
    llm_available: bool = True


def load_eval_set(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"评测数据集不存在: {path}")
    dataset = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(dataset, list) or not dataset:
        raise ValueError(f"评测数据集必须是非空数组: {path}")
    for item in dataset:
        if not str(item.get("user_query", "")).strip():
            raise ValueError(f"数据集存在空 user_query: {item}")
    return dataset


async def _judge_resolved(sample_query: str, reply: str) -> Optional[Dict[str, Any]]:
    """LLM-as-judge 判定是否解决；LLM 不可用或失败返回 None。"""
    from app.services.llm_service import LLMService

    llm_service = LLMService()
    if not llm_service.is_available():
        return None
    verdict = await llm_service.run_structured(
        JUDGE_RESOLVED_PROMPT,
        f"用户问题：{sample_query}\n客服回答：{reply if reply else '（无回答）'}",
        _ResolvedVerdict,
        tags=["offline_e2e_judge"],
    )
    if verdict is None:
        return None
    return {"resolved": bool(verdict.resolved), "reason": verdict.reason[:80]}


def _heuristic_resolve(reply: str, sample_query: str) -> Optional[Dict[str, Any]]:
    """AnswerEvaluator 兜底判定（LLM 不可用时使用）。"""
    from app.services.evaluation import get_answer_evaluator

    if not reply.strip():
        return None
    result = get_answer_evaluator().evaluate(response=reply, user_query=sample_query)
    return {
        "resolved": result.overall_score >= HEURISTIC_PASS,
        "reason": f"heuristic overall={result.overall_score:.1f}",
    }


async def run_one_sample(
    item: Dict[str, Any], graph, runs: int, sample_idx: int
) -> E2ESample:
    """驱动一条真实链路执行（可选多轮计时取中位数）。"""
    from app.agent.state import AgentState

    query = str(item["user_query"]).strip()
    state = AgentState(
        session_id=f"e2e-eval-{sample_idx}",
        user_id=1,
        user_message=query,
    )
    latencies: List[float] = []
    last_reply = ""
    last_need_human = False
    for _ in range(max(runs, 1)):
        started = time.perf_counter()
        try:
            result = await graph.run(AgentState(**state.model_dump()))
        except Exception as exc:  # 链路内部异常：不中断，按降级计数
            return E2ESample(
                user_query=query,
                intent=str(item.get("intent", "")),
                reply="",
                resolved=False,
                resolve_source="degraded",
                reason=f"链路异常: {str(exc)[:60]}",
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        latencies.append((time.perf_counter() - started) * 1000)
        last_reply = getattr(result, "reply", "") or ""
        last_need_human = bool(getattr(result, "need_human", False))
    latencies.sort()
    latency = latencies[len(latencies) // 2]  # 多轮取中位数，减少抖动

    return E2ESample(
        user_query=query,
        intent=str(item.get("intent", "")),
        reply=last_reply,
        need_human=last_need_human,
        latency_ms=round(latency, 3),
    )


async def resolve_sample(sample: E2ESample, llm_available: bool) -> None:
    """给样本打"是否解决"标签（三级降级）。"""
    sample.llm_available = llm_available
    if llm_available:
        verdict = await _judge_resolved(sample.user_query, sample.reply)
        if verdict is not None:
            sample.resolved = verdict["resolved"]
            sample.resolve_source = "judge"
            sample.reason = verdict["reason"]
            return
    if sample.reply.strip():
        heuristic = _heuristic_resolve(sample.reply, sample.user_query)
        if heuristic is not None:
            sample.resolved = heuristic["resolved"]
            sample.resolve_source = "heuristic"
            sample.reason = heuristic["reason"]
            return
    # 再降级：人工交接 / 空回复。
    if sample.need_human:
        sample.resolved = False
        sample.resolve_source = "human_handoff"
        sample.reason = "命中人工交接"
    else:
        sample.resolved = False
        sample.resolve_source = "empty"
        sample.reason = "回复为空或链路未产出"


def _pctile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(p / 100.0 * (len(ordered) - 1)))))
    return round(ordered[idx], 2)


async def run_all(evaluate_async, dataset: List[Dict[str, Any]], limit: int, runs: int) -> List[E2ESample]:
    """执行全部样本并完成解决判定。"""
    from app.services.llm_service import LLMService

    llm_available = LLMService().is_available()
    samples = await evaluate_async(dataset, limit, runs)
    for sample in samples:
        await resolve_sample(sample, llm_available)
    return samples


def summarize(samples: List[E2ESample], llm_was_available: bool) -> Dict[str, Any]:
    resolved = sum(1 for s in samples if s.resolved)
    reason_dist = Counter(s.resolve_source for s in samples)
    fail_reasons = Counter(
        (s.resolve_source, s.reason[:24]) for s in samples if not s.resolved
    )
    latencies = [s.latency_ms for s in samples if s.latency_ms > 0]

    # 按意图聚合解决率，用于定位"哪个意图链路最弱"。
    per_intent: Dict[str, Dict[str, Any]] = {}
    for s in samples:
        bucket = per_intent.setdefault(s.intent or "unknown", {"total": 0, "resolved": 0, "unresolved": 0})
        bucket["total"] += 1
        bucket["resolved" if s.resolved else "unresolved"] += 1
    for bucket in per_intent.values():
        bucket["rate"] = round(bucket["resolved"] / max(bucket["total"], 1), 4)

    return {
        "total": len(samples),
        "resolved": resolved,
        "resolution_rate": round(resolved / max(len(samples), 1), 4),
        "resolve_source_dist": dict(reason_dist),
        "per_intent": per_intent,
        "unresolved_reasons": {
            f"{src}:{reason}": cnt for (src, reason), cnt in fail_reasons.most_common(8)
        },
        "delay_p50_ms": _pctile(latencies, 50),
        "delay_p95_ms": _pctile(latencies, 95),
        "delay_available": len(latencies),
        "llm_available": llm_was_available,
        "delay_scope": "end-to-end" if llm_was_available else "degraded-search-only",
    }


def _build_optimization_advice(summary: Dict[str, Any], samples: List[E2ESample]) -> List[str]:
    advice: List[str] = []
    if summary["resolution_rate"] < 0.8:
        advice.append(
            f"端到端解决率 {summary['resolution_rate']:.1%} 偏低（目标≥80%），"
            "先按未解决原因分布定位环节："
        )
        for key, cnt in list(summary["unresolved_reasons"].items())[:5]:
            advice.append(f"  - {key}：{cnt} 条")
    elif summary["resolution_rate"] >= 0.9:
        advice.append(f"解决率 {summary['resolution_rate']:.1%} 达标，可保持现链路。")
    judge_failed = [s for s in samples if not s.resolved and s.resolve_source == "judge"]
    if judge_failed:
        advice.append(
            f"LLM-judge 判定未解决 {len(judge_failed)} 条：检查回复是否答非所问或遗漏 key 诉求，"
            "可在 response_generation 增加诉求覆盖检查。"
        )
    human_failed = [s for s in samples if s.resolve_source == "human_handoff"]
    if human_failed:
        advice.append(
            f"命中人工交接 {len(human_failed)} 条：属正常兜底路径，若占比过高需回查"
            "意图识别/工具执行失效根因。"
        )
    p95 = summary["delay_p95_ms"]
    if summary["delay_available"] and p95 > 5000:
        advice.append(
            f"端到端 p95={p95:.0f}ms 超过 5s 预算：先用 trace 定位耗时节点"
            "（LLM 生成 vs 检索 vs 工具），再采取并发检索、缓存或模型降档。"
        )
    elif summary["delay_available"]:
        advice.append(
            f"端到端 p95={p95:.0f}ms 在预算内，可关注 p50={summary['delay_p50_ms']:.0f}ms 的常规链路。"
        )
    if not advice:
        advice.append("当前链路解决率与延迟均达标，建议扩充评测集样本继续压测。")
    return advice


def format_report_md(
    dataset: List[Dict[str, Any]],
    samples: List[E2ESample],
    summary: Dict[str, Any],
    *,
    data_path: str,
) -> str:
    lines = ["# 端到端链路评测报告（问题解决率 + p95）", ""]
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 数据集：`{data_path}`（{summary['total']} 条）")
    lines.append("- 链路：`AgentGraph.run`（13 节点官方 LangGraph，与线上一致）")
    lines.append(f"- LLM 可用：{'是' if summary['llm_available'] else '否'}")
    lines.append("")
    if not summary["llm_available"]:
        lines.append("> 警告：LLM 不可用，解决判定退化为启发式，p95 仅覆盖检索段（非全链路）。")
        lines.append("")

    lines.append("## 汇总指标")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| resolution_rate | {summary['resolution_rate']:.2%} |")
    lines.append(f"| resolved / total | {summary['resolved']} / {summary['total']} |")
    lines.append(f"| resolve_source_dist | {summary['resolve_source_dist']} |")
    lines.append(f"| delay_p50_ms | {summary['delay_p50_ms']} |")
    lines.append(f"| delay_p95_ms | {summary['delay_p95_ms']} |")
    lines.append(f"| delay_scope | {summary['delay_scope']} |")
    lines.append("")

    lines.append("## 未解决原因分布")
    lines.append("| 原因 | 条数 |")
    lines.append("|---|---|")
    for k, v in summary["unresolved_reasons"].items():
        lines.append(f"| {k} | {v} |")
    if not summary["unresolved_reasons"]:
        lines.append("| （无） | 0 |")
    lines.append("")

    lines.append("## 按意图解决率")
    lines.append("| intent | resolved | total | rate |")
    lines.append("|---|---|---|---|")
    for intent, m in sorted(summary["per_intent"].items()):
        lines.append(f"| {intent} | {m['resolved']} | {m['total']} | {m['rate']:.2%} |")
    lines.append("")

    lines.append("## 逐条明细")
    lines.append("| intent | resolved | src | latency_ms | reason | user_query |")
    lines.append("|---|---|---|---|---|---|")
    for item, s in zip(dataset, samples):
        lines.append(
            f"| {s.intent} | {'✓' if s.resolved else '✗'} | {s.resolve_source} | {s.latency_ms:.0f} "
            f"| {s.reason[:30]} | {str(item['user_query'])[:30]} |"
        )
    lines.append("")

    lines.append("## 优化建议")
    for tip in _build_optimization_advice(summary, samples):
        lines.append(f"- {tip}")
    lines.append("")
    return "\n".join(lines)


def print_console_summary(summary: Dict[str, Any]):
    print("=== 端到端链路评测汇总 ===")
    print(f"总样本数          : {summary['total']}")
    print(f"问题解决率        : {summary['resolution_rate']:.2%} ({summary['resolved']}/{summary['total']})")
    print(f"判定来源分布      : {summary['resolve_source_dist']}")
    print(f"端到端 p50        : {summary['delay_p50_ms']}ms")
    print(f"端到端 p95        : {summary['delay_p95_ms']}ms ({summary['delay_scope']})")
    if not summary["llm_available"]:
        print("注: LLM 不可用，解决判定为启发式，p95 为检索级。")


async def _evaluate_samples(dataset: List[Dict[str, Any]], limit: int, runs: int) -> List[E2ESample]:
    """按批构造图实例并执行（复用线上同一条 AgentGraph）。"""
    from app.agent.graph import AgentGraph

    graph = AgentGraph()
    samples: List[E2ESample] = []
    scope = dataset[:limit] if limit > 0 else dataset
    for idx, item in enumerate(scope):
        samples.append(await run_one_sample(item, graph, runs, idx))
    return samples


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default="data/eval/cs_eval_set.json", help="评测数据集 JSON 路径")
    parser.add_argument("--limit", type=int, default=0, help="仅评估前 N 条（0 表示全部）")
    parser.add_argument("--runs", type=int, default=1, help="每条样本重复执行次数（取中位数延迟）")
    parser.add_argument("--report-dir", default="reports", help="报告输出目录")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    data_path = Path(args.data)
    if not data_path.is_absolute():
        data_path = repo_root / data_path

    dataset = load_eval_set(data_path)
    samples = asyncio.run(run_all(_evaluate_samples, dataset, args.limit, args.runs))
    summary = summarize(samples, samples[0].llm_available if samples else False)

    report = format_report_md(
        dataset[: len(samples)], samples, summary, data_path=str(data_path)
    )
    report_dir = Path(args.report_dir)
    if not report_dir.is_absolute():
        report_dir = repo_root / report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"e2e_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    report_path.write_text(report, encoding="utf-8")

    print_console_summary(summary)
    print(f"报告已写入: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())