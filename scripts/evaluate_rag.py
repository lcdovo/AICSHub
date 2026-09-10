"""RAG 离线评估脚本：量化混合检索效果，指导 top_k / 阈值 / 重排调参。

为什么做：检索质量决定 RAG 回答质量，需要量化证据决定 top_k、相似度阈值与是否启用重排；
怎么做：固定评估数据集，跑线上同一条检索链路（KnowledgeBaseService.search），
        逐条计算 Recall@1/3/5 与 MRR，支持不同 top_k 视口对比；
结论示例：在某配置下 Recall@5 达 Y%，据此把线上 top_k 定为 N。

用法：
    python scripts/evaluate_rag.py                          # 默认 top_k=5
    python scripts/evaluate_rag.py --top-k 3 --limit 10     # 视口 3、快速跑前 10 条
    python scripts/evaluate_rag.py --with-answer-quality    # 输出启发式回答质量列（复用 AnswerEvaluator）

前置条件：知识库中已导入文档。空库时脚本正常输出全 0 指标并给出提示，
可先调用 POST /api/v1/knowledge/documents/seed 导入默认种子文档。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# 报告评估使用的视口集合（search 取 max(视口集合) 结果，再按 k 切分）。
RANK_KS = (1, 3, 5)


@dataclass
class QueryEval:
    """单条查询的检索评估结果。"""

    query: str = ""
    ground_truth: List[str] = field(default_factory=list)
    rank: int = 0  # 首个 ground_truth 命中结果所在的 1-based 排名，未命中为 0
    top_result: str = ""
    latency_ms: float = 0.0
    strategy: str = ""
    answer_score: Optional[float] = None  # 仅 --with-answer-quality 时填充


def load_eval_set(path: Path) -> List[Dict[str, Any]]:
    """加载评估数据集，逐条校验 query 与 ground_truth 字段。"""
    if not path.exists():
        raise FileNotFoundError(f"评估数据集不存在: {path}")
    dataset = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(dataset, list) or not dataset:
        raise ValueError(f"评估数据集必须是非空数组: {path}")
    for item in dataset:
        if not str(item.get("query", "")).strip():
            raise ValueError(f"数据集存在空 query: {item}")
        gt = item.get("ground_truth")
        if not isinstance(gt, list) or not gt or not all(str(t).strip() for t in gt):
            raise ValueError(f"ground_truth 必须是非空字符串数组: {item}")
    return dataset


def match_rank(result_titles: List[str], ground_truth: List[str]) -> int:
    """返回首个 ground_truth 标题命中结果的 1-based 排名；未命中返回 0。

    命中判据为标题包含或被包含（精确 ORD 与"订单查询说明"这类片段标题通用）。
    """
    for idx, title in enumerate(result_titles, start=1):
        name = str(title or "").strip()
        if not name:
            continue
        for gt in ground_truth:
            target = str(gt).strip()
            if target and (target in name or name in target):
                return idx
    return 0


def evaluate_query(search: Callable[[str, int], Dict[str, Any]], dataset: List[Dict[str, Any]],
                   top_k: int, with_answer_quality: bool) -> List[QueryEval]:
    """对数据集逐条执行检索并计算命中排名。

    search(query, top_k) -> {"results": [{"title", ...}], "latency_ms", "search_strategy"}
    """
    horizon = min(max(top_k, max(RANK_KS)), 50)
    evaluated: List[QueryEval] = []
    for item in dataset:
        query = str(item["query"]).strip()
        ground_truth = [str(t).strip() for t in item["ground_truth"]]
        result = search(query, horizon)
        results = result.get("results", []) or []
        titles = [r.get("title", "") for r in results]

        ev = QueryEval(
            query=query,
            ground_truth=ground_truth,
            rank=match_rank(titles, ground_truth),
            top_result=titles[0] if titles else "",
            latency_ms=float(result.get("latency_ms", 0) or 0),
            strategy=str(result.get("search_strategy", "") or ""),
        )

        if with_answer_quality and results:
            from app.services.evaluation import get_answer_evaluator

            top_chunk = str(results[0].get("content", "") or "")[:500]
            expected = item.get("expected_answer")
            if expected:
                score = get_answer_evaluator().evaluate(
                    response=top_chunk,
                    user_query=query,
                    expected_answer=str(expected),
                )
                ev.answer_score = round(float(score.overall_score), 1)
        evaluated.append(ev)
    return evaluated


def summarize(evaluated: List[QueryEval], top_k: int) -> Dict[str, Any]:
    """聚合整组指标：Recall@1/3/5、MRR、平均耗时、空结果比例。"""
    total = len(evaluated) or 1
    hits = {k: 0 for k in RANK_KS}
    mrr_sum = 0.0
    latency_sum = 0.0
    empty = 0
    for ev in evaluated:
        if ev.rank > 0:
            for k in RANK_KS:
                if ev.rank <= k:
                    hits[k] += 1
            mrr_sum += 1.0 / ev.rank
        lat = ev.latency_ms or 0.0
        latency_sum += lat
        if not ev.top_result:
            empty += 1

    answer_scores = [ev.answer_score for ev in evaluated if ev.answer_score is not None]
    latencies = sorted(ev.latency_ms for ev in evaluated if ev.latency_ms > 0)
    def _pctile(vals: List[float], p: float) -> float:
        if not vals:
            return 0.0
        idx = max(0, min(len(vals) - 1, int(round(p / 100.0 * (len(vals) - 1)))))
        return round(vals[idx], 2)

    summary = {
        "top_k_horizon": min(top_k, 50),
        "total_queries": len(evaluated),
        "recall": {f"recall@{k}": round(hits[k] / total, 4) for k in RANK_KS},
        "mrr": round(mrr_sum / total, 4),
        "avg_latency_ms": round(latency_sum / total, 2),
        "p50_latency_ms": _pctile(latencies, 50),
        "p95_latency_ms": _pctile(latencies, 95),
        "empty_result_rate": round(empty / total, 4),
        "avg_answer_score": round(sum(answer_scores) / len(answer_scores), 1) if answer_scores else None,
    }
    return summary


def format_report_md(
    dataset: List[Dict[str, Any]],
    evaluated: List[QueryEval],
    summary: Dict[str, Any],
    *,
    with_answer_quality: bool,
    data_path: str,
) -> str:
    """生成 Markdown 评估报告。"""
    lines = ["# RAG 离线评估报告", ""]
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 数据集：`{data_path}`（{summary['total_queries']} 条）")
    lines.append(f"- 评估视口：`top_k={summary['top_k_horizon']}`（按 RANK_KS 切分计算 Recall@k）")
    lines.extend(["- 检索链路：`KnowledgeBaseService.search`（与线上同一条混合检索链路）", ""])

    lines.append("## 汇总指标")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    for k, v in sorted(summary.items()):
        if k == "top_k_horizon":
            continue
        lines.append(f"| {k} | {v} |")
    lines.append("")

    lines.append("## 逐条明细")
    header = "| query | ground_truth | rank | recall@1 | recall@3 | recall@5 | MRR | top_result | latency_ms |"
    separator = "|---|---|---|---|---|---|---|---|---|---|"
    if with_answer_quality:
        header += " answer_score |"
        separator += "---|"
    lines.extend([header, separator])
    for item, ev in zip(dataset, evaluated):
        rank = ev.rank
        cells = [
            ev.query[:40],
            ",".join(ev.ground_truth),
            str(rank),
            "1" if 0 < rank <= 1 else "0",
            "1" if 0 < rank <= 3 else "0",
            "1" if 0 < rank <= 5 else "0",
            f"{1.0 / rank:.3f}" if rank else "0.000",
            ev.top_result[:28],
            f"{ev.latency_ms:.1f}",
        ]
        if with_answer_quality:
            cells.append(str(ev.answer_score if ev.answer_score is not None else "-"))
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    lines.extend([
        "## 说明",
        "- `rank`：首个 ground_truth 命中结果在返回列表中的 1-based 位置，0 表示未命中。",
        "- `recall@k`、`MRR` 为逐条计算的查询级指标（非文档级），衡量「检索结果是否包含正确答案」。",
        "- `answer_score` 为可选列：基于命中片段与 `expected_answer` 的启发式评分（复用 `AnswerEvaluator`），不代表 LLM 判定。",
        "- 如何扩展：向 `data/eval/rag_eval_set.json` 追加 `query` 与 `ground_truth`（标题需与知识库文档一致），或通过数据集的 `expected_answer` 开启回答质量列。",
        "",
    ])
    return "\n".join(lines)


def print_console_summary(summary: Dict[str, Any]):
    print("=== RAG 离线评估汇总 ===")
    recall = summary["recall"]
    print(f"查询数          : {summary['total_queries']}")
    for k in RANK_KS:
        print(f"Recall@{k}        : {recall[f'recall@{k}']:.2%}")
    print(f"MRR             : {summary['mrr']:.3f}")
    print(f"平均耗时        : {summary['avg_latency_ms']}ms")
    print(f"p50 耗时        : {summary['p50_latency_ms']}ms")
    print(f"p95 耗时        : {summary['p95_latency_ms']}ms")
    print(f"空结果比例      : {summary['empty_result_rate']:.2%}")
    if summary.get("avg_answer_score") is not None:
        print(f"回答质量均分    : {summary['avg_answer_score']}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default="data/eval/rag_eval_set.json", help="评估数据集 JSON 路径")
    parser.add_argument("--top-k", type=int, default=5, choices=[1, 3, 5], help="评估视口（用于 recall@k 切分）")
    parser.add_argument("--limit", type=int, default=0, help="仅评估前 N 条（0 表示全部）")
    parser.add_argument("--with-answer-quality", action="store_true", help="输出启发式回答质量列")
    parser.add_argument("--report-dir", default="reports", help="报告输出目录")
    args = parser.parse_args(argv)

    # 脚本可直接在仓库根目录运行，也兼容任意 cwd。
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    data_path = Path(args.data)
    if not data_path.is_absolute():
        data_path = repo_root / data_path

    dataset = load_eval_set(data_path)
    if args.limit > 0:
        dataset = dataset[: args.limit]

    # 复用线上同一条检索链路，保证评估与生产行为一致。
    from app.services.knowledge_base import get_knowledge_base

    def search(query: str, top_k: int) -> Dict[str, Any]:
        return get_knowledge_base().search(query, top_k=top_k)

    evaluated = evaluate_query(search, dataset, args.top_k, args.with_answer_quality)
    summary = summarize(evaluated, args.top_k)

    if summary["empty_result_rate"] == 1.0 and summary["total_queries"] > 0:
        print("警告：全部查询无检索结果。请先通过 POST /api/v1/knowledge/documents/seed 导入种子文档。")

    report = format_report_md(
        dataset, evaluated, summary, with_answer_quality=args.with_answer_quality,
        data_path=str(data_path),
    )
    report_dir = Path(args.report_dir)
    if not report_dir.is_absolute():
        report_dir = repo_root / report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"rag_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    report_path.write_text(report, encoding="utf-8")

    print_console_summary(summary)
    print(f"报告已写入: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())