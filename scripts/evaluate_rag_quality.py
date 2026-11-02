"""RAG 生成质量离线评估脚本：RAGAS 风格两维指标（faithfulness / answer relevance）。

为什么做：检索指标（Recall@k/MRR，见 evaluate_rag.py）只衡量"召回对不对"，
无法回答"LLM 用它生成的回答忠不忠实、切不切题"。本脚本在检索之外补一维
生成质量，作为 RAG 链路调参（片段清洗、top_k、prompt）的回归指标。

怎么做：对每条评测项，用 LLM 结构化判定两个 0-1 维度——
- faithfulness（忠实度）：参考回答/期望要点中的关键论断，能否由检索证据（contexts）支撑；
- answer_relevance（相关性）：回答与用户查询是否直接相关、未跑题。
复用线上 LLMService（同一套模型/温度/降级），不引入外部评测依赖。

用法：
    python scripts/evaluate_rag_quality.py                          # 默认 data/eval/rag_quality_set.json
    python scripts/evaluate_rag_quality.py --data x.json --limit 10  # 快速跑前 10 条
    python scripts/evaluate_rag_quality.py --top-k 3                 # 检索证据取 top_k

数据集格式（JSON 数组）：
    [
      {"query": "智能手表防水等级是多少",
       "reference": "支持 IP68 防水，游泳可佩戴。",
       "contexts": ["IP68：可日常防水溅/游泳佩戴。"]}   # 可选；缺失时自动走线上检索
    ]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class _FaithfulnessResult(BaseModel):
    """faithfulness 判定契约：reference 中的关键论断能否由 contexts 支撑。"""

    score: float = Field(default=0.0, ge=0.0, le=1.0, description="论断支撑比例 0-1")
    unsupported_claims: List[str] = Field(default_factory=list, description="证据中找不到支撑的论断（可为空）")


class _RelevanceResult(BaseModel):
    """answer relevance 判定契约：reference 与 query 是否直接相关。"""

    score: float = Field(default=0.0, ge=0.0, le=1.0, description="相关性 0-1")
    reason: str = Field(default="", description="一句话判定理由")

    def to_score(self) -> float:
        return self.score


@dataclass
class ItemEval:
    query: str = ""
    reference: str = ""
    context_count: int = 0
    faithfulness: Optional[float] = None
    relevance: Optional[float] = None
    error: str = ""


def _load_dataset(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"评估数据集不存在: {path}")
    dataset = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(dataset, list) or not dataset:
        raise ValueError(f"评估数据集必须是非空数组: {path}")
    for item in dataset:
        if not str(item.get("query", "")).strip():
            raise ValueError(f"数据集存在空 query: {item}")
    return dataset


def _fetch_contexts(query: str, top_k: int) -> List[str]:
    """复用线上同一条检索链路取证据片段；失败返回空列表交由评分降级。"""
    try:
        from app.services.knowledge_base import get_knowledge_base

        result = get_knowledge_base().search(query, top_k=top_k)
        return [str(r.get("content", "")) for r in (result.get("results", []) or []) if r.get("content")]
    except Exception as exc:  # 检索或连接失败时以空证据继续，让 LLM 判低分
        print(f"  检索证据失败（{exc}），按空证据评分")
        return []


def _score_faithfulness(llm, query: str, reference: str, contexts: List[str]) -> float:
    if not reference.strip():
        return None  # 无参考回答则无法判定忠实度
    evidence = "\n".join(f"[证据{i + 1}] {c[:400]}" for i, c in enumerate(contexts))
    if not evidence.strip():
        evidence = "（无可用证据）"
    system_prompt = (
        "你是 RAG 评测裁判。判断'参考回答'中的每一个关键论断是否都能由'检索证据'支撑。"
        "评分规则：score=0.0 表示大部分论断无证据支撑；0.5 表示部分支撑；1.0 表示全部论断均有证据支撑。"
        "仅依据给定证据判定，不得依赖外部知识，不得臆造。"
    )
    try:
        result = llm.run_structured(
            system_prompt,
            f"用户查询：{query}\n\n检索证据：\n{evidence}\n\n参考回答：{reference}\n\n请输出 JSON 格式的判定结果。",
            _FaithfulnessResult,
            tags=["rag_eval_faithfulness"],
        )
        return round(max(0.0, min(1.0, float(result.score))), 2)
    except Exception as exc:
        print(f"  faithfulness 判定失败: {exc}")
        return None


def _score_relevance(llm, query: str, reference: str) -> float:
    if not reference.strip():
        return None
    system_prompt = (
        "你是 RAG 评测裁判。判断'参考回答'是否直接回答了'用户查询'，不跑题、不遗漏核心诉求。"
        "评分规则：score=0.0 表示完全不相关；0.5 表示部分相关；1.0 表示紧密切题且覆盖核心诉求。"
    )
    try:
        result = llm.run_structured(
            system_prompt,
            f"用户查询：{query}\n\n参考回答：{reference}\n\n请输出 JSON 格式的判定结果。",
            _RelevanceResult,
            tags=["rag_eval_relevance"],
        )
        return round(max(0.0, min(1.0, float(result.score))), 2)
    except Exception as exc:
        print(f"  answer_relevance 判定失败: {exc}")
        return None


def summarize(evaluated: List[ItemEval]) -> Dict[str, Any]:
    faith = [e.faithfulness for e in evaluated if e.faithfulness is not None]
    relev = [e.relevance for e in evaluated if e.relevance is not None]
    return {
        "total": len(evaluated),
        "faithfulness": {"count": len(faith), "avg": round(sum(faith) / len(faith), 3) if faith else None},
        "answer_relevance": {"count": len(relev), "avg": round(sum(relev) / len(relev), 3) if relev else None},
        "failures": sum(1 for e in evaluated if e.error),
    }


def format_report_md(dataset: List[Dict[str, Any]], evaluated: List[ItemEval], summary: Dict[str, Any],
                     data_path: str, top_k: int) -> str:
    lines = ["# RAG 生成质量评估报告（RAGAS 风格）", ""]
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 数据集：`{data_path}`（{summary['total']} 条）")
    lines.append(f"- 证据视口：`top_k={top_k}`（缺失 contexts 自动检索）")
    lines.append(f"- 评分模型：LLM 结构化判定（tags: rag_eval_faithfulness / rag_eval_relevance）")
    lines.append("")
    lines.append("## 汇总指标")
    lines.append("| 指标 | 条目数 | 均值 |")
    lines.append("|---|---|---|")
    f, r = summary["faithfulness"], summary["answer_relevance"]
    lines.append(f"| faithfulness（忠实度） | {f['count']} | {f['avg'] if f['avg'] is not None else '-'} |")
    lines.append(f"| answer_relevance（相关性） | {r['count']} | {r['avg'] if r['avg'] is not None else '-'} |")
    lines.append(f"| 判定失败条目 | {summary['failures']} | - |")
    lines.append("")
    lines.append("## 逐条明细")
    lines.append("| query | reference | 证据数 | faithfulness | answer_relevance | error |")
    lines.append("|---|---|---|---|---|---|")
    for item, ev in zip(dataset, evaluated):
        lines.append("| " + " | ".join([
            str(ev.query)[:40],
            str(ev.reference)[:30],
            str(ev.context_count),
            str(ev.faithfulness if ev.faithfulness is not None else "-"),
            str(ev.relevance if ev.relevance is not None else "-"),
            ev.error[:20],
        ]) + " |")
    lines.append("")
    lines.extend([
        "## 说明",
        "- `faithfulness`：参考回答中的关键论断可由检索证据支撑的比例（近似 RAGAS faithfulness，0-1 越高越忠实）。",
        "- `answer_relevance`：参考回答与用户查询的相关程度（近似 RAGAS answer relevance，0-1 越高越切题）。",
        "- 两项均需数据项提供 `reference`；缺失该字段时该项跃过（记为 `-`）。",
        "- `contexts` 缺省时自动走知识库线上检索链路取证据；空库/检索失败按空证据评分，faithfulness 会偏低。",
        "",
    ])
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default="data/eval/rag_quality_set.json", help="评估数据集 JSON 路径")
    parser.add_argument("--top-k", type=int, default=5, help="证据视口（contexts 缺省的检索条数）")
    parser.add_argument("--limit", type=int, default=0, help="仅评估前 N 条（0 表示全部）")
    parser.add_argument("--report-dir", default="reports", help="报告输出目录")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    data_path = Path(args.data)
    if not data_path.is_absolute():
        data_path = repo_root / data_path

    dataset = _load_dataset(data_path)
    if args.limit > 0:
        dataset = dataset[: args.limit]

    from app.services.llm_service import LLMService

    llm = LLMService()
    evaluated: List[ItemEval] = []
    for item in dataset:
        query = str(item["query"]).strip()
        reference = str(item.get("reference", "") or "").strip()
        contexts = item.get("contexts")
        if not isinstance(contexts, list) or not contexts:
            contexts = _fetch_contexts(query, args.top_k)
        ev = ItemEval(query=query, reference=reference, context_count=len(contexts))
        try:
            ev.faithfulness = _score_faithfulness(llm, query, reference, contexts)
            ev.relevance = _score_relevance(llm, query, reference)
        except Exception as exc:
            ev.error = str(exc)
        evaluated.append(ev)
        print(f"  [{len(evaluated)}/{len(dataset)}] {query[:32]} faith={ev.faithfulness} rel={ev.relevance}")

    summary = summarize(evaluated)

    report = format_report_md(dataset, evaluated, summary, data_path=str(data_path), top_k=args.top_k)
    report_dir = Path(args.report_dir)
    if not report_dir.is_absolute():
        report_dir = repo_root / report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"rag_quality_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    report_path.write_text(report, encoding="utf-8")

    print("\n=== RAG 生成质量评估汇总 ===")
    print(f"条目数          : {summary['total']}")
    print(f"faithfulness    : {summary['faithfulness']['avg'] if summary['faithfulness']['avg'] is not None else '-'}"
          f"  ({summary['faithfulness']['count']} 条)")
    print(f"answer_relevance: {summary['answer_relevance']['avg'] if summary['answer_relevance']['avg'] is not None else '-'}"
          f"  ({summary['answer_relevance']['count']} 条)")
    print(f"判定失败        : {summary['failures']}")
    print(f"报告已写入: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())