"""离线客服回答评测脚本：启发式评分 vs LLM-as-judge 对比。

为什么做：线上 EvaluationResult 只落库启发式规则分（准确/完整/安全/相关性），
无法刻画"语义正确性"；本脚本用同一组客服问答样本，同时跑两套评分——
    1) 启发式   `AnswerEvaluator.evaluate`（与线上同一条评测逻辑，保证口径一致）
    2) LLM-as-judge `LLMService.run_structured` 打分（语义级质检）
并输出两种评分的差异分布，用于判断启发式是否可以作为线上质量的合理代理，
也为启发式阈值调优提供证据。

用法：
    python scripts/evaluate_answers.py
    python scripts/evaluate_answers.py --data data/eval/cs_eval_set.json --limit 5
    python scripts/evaluate_answers.py --report-dir reports

前置条件：需要配置真实 LLM 凭证（.env 的 LLM_*）；LLM 不可用时脚本正常输出
启发式评分并标注 judge=N/A，仍可运行不报错。
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

JUDGE_SYSTEM_PROMPT = (
    "你是客服回答质检员。针对用户问题，评估客服回答的质量，从四个维度打分（0-100）：\n"
    "1) accuracy：事实是否准确、与可用证据一致（无证据时按常识判断）；\n"
    "2) completeness：是否完整覆盖了用户问题的关键诉求；\n"
    "3) relevance：是否与用户问题相关、没有答非所问；\n"
    "4) safety：是否包含敏感、违规或可能误导的内容。\n"
    "最后给出 overall 总分和一句 evaluation_comment（50字内）。"
    "只输出结构化结果，不要输出其他内容。"
)


class _JudgeScore(BaseModel):
    """LLM-as-judge 输出的评分契约（与启发式维度对齐）。"""

    accuracy: float = Field(ge=0, le=100)
    completeness: float = Field(ge=0, le=100)
    relevance: float = Field(ge=0, le=100)
    safety: float = Field(ge=0, le=100)
    overall: float = Field(ge=0, le=100)
    evaluation_comment: str = Field(default="")


@dataclass
class SampleEval:
    """单条样本的两套评分结果。"""

    intent: str = ""
    user_query: str = ""
    response: str = ""  # 被测客服回答（reference_reply）
    heuristic: Dict[str, float] = field(default_factory=dict)  # 现有规则评分逐维
    judge: Dict[str, Any] = field(default_factory=dict)  # LLM 评分 + 评语
    overall_gap: Optional[float] = None


def load_eval_set(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"评测数据集不存在: {path}")
    dataset = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(dataset, list) or not dataset:
        raise ValueError(f"评测数据集必须是非空数组: {path}")
    for item in dataset:
        if not str(item.get("user_query", "")).strip():
            raise ValueError(f"数据集存在空 user_query: {item}")
        if "reference_reply" not in item or not str(item.get("reference_reply", "")).strip():
            raise ValueError(f"数据集条目缺少 reference_reply: {item}")
    return dataset


def score_heuristic(dataset: List[Dict[str, Any]], limit: int) -> List[SampleEval]:
    """用现有 AnswerEvaluator（与线上同口径）计算启发式分。"""
    from app.services.evaluation import get_answer_evaluator

    evaluator = get_answer_evaluator()
    samples: List[SampleEval] = []
    for item in dataset[:limit] if limit > 0 else dataset:
        response = str(item["reference_reply"])
        query = str(item["user_query"]).strip()
        result = evaluator.evaluate(response=response, user_query=query)
        samples.append(SampleEval(
            intent=str(item.get("intent", "general")),
            user_query=query,
            response=response,
            heuristic=result.to_dict(),
        ))
    return samples


async def score_judge(samples: List[SampleEval]) -> None:
    """用 LLM-as-judge 补充语义级评分；LLM 不可用时置空。"""
    from app.services.llm_service import LLMService

    llm_service = LLMService()
    if not llm_service.is_available():
        return
    for sample in samples:
        prompt = (
            f"用户问题：{sample.user_query}\n"
            f"客服回答：{sample.response}"
        )
        verdict = await llm_service.run_structured(
            JUDGE_SYSTEM_PROMPT, prompt, _JudgeScore, tags=["offline_judge"]
        )
        if verdict is None:
            continue
        sample.judge = verdict.model_dump()
        if sample.heuristic.get("overall_score") is not None and verdict.overall is not None:
            sample.overall_gap = round(
                verdict.overall - float(sample.heuristic["overall_score"]), 1
            )


def summarize(samples: List[SampleEval]) -> Dict[str, Any]:
    heuristics = [s.heuristic.get("overall_score", 0) for s in samples if s.heuristic]
    judged = [s for s in samples if s.judge]
    judge_scores = [s.judge.get("overall", 0) for s in judged]
    gaps = [s.overall_gap for s in judged if s.overall_gap is not None]
    total = len(samples) or 1
    return {
        "total": len(samples),
        "judged": len(judged),
        "heuristic_overall_avg": round(sum(heuristics) / len(heuristics), 1) if heuristics else None,
        "judge_overall_avg": round(sum(judge_scores) / len(judge_scores), 1) if judge_scores else None,
        "avg_gap": round(sum(gaps) / len(gaps), 1) if gaps else None,
        "max_gap": max(gaps) if gaps else None,
        "sample_with_gap_gt_10": len([g for g in gaps if abs(g) > 10]),
    }


def format_report_md(
    dataset: List[Dict[str, Any]],
    samples: List[SampleEval],
    summary: Dict[str, Any],
    *,
    data_path: str,
) -> str:
    lines = ["# 客服回答离线评测报告（启发式 vs LLM-as-judge）", ""]
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 数据集：`{data_path}`（{summary['total']} 条）")
    lines.append(f"- 完成 LLM 质检：{summary['judged']} 条")
    lines.append("- 启发式：`AnswerEvaluator.evaluate`（与线上 EvaluationResult 同口径）")
    lines.append("- LLM-as-judge：`LLMService.run_structured` 四维打分（准确/完整/相关/安全）")
    lines.append("")

    lines.append("## 汇总指标")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    for k, v in summary.items():
        lines.append(f"| {k} | {v} |")
    lines.append("")

    lines.append("## 逐条明细")
    lines.append("| intent | user_query | heuristic | judge | gap | comment |")
    lines.append("|---|---|---|---|---|---|")
    for item, sample in zip(dataset, samples):
        h_score = sample.heuristic.get("overall_score", "-")
        j_score = sample.judge.get("overall", "-") if sample.judge else "N/A"
        gap = str(sample.overall_gap) if sample.overall_gap is not None else "-"
        comment = (sample.judge.get("evaluation_comment", "") or "")[:40] if sample.judge else "-"
        lines.append(
            f"| {sample.intent} | {item['user_query'][:36]} | {h_score} | {j_score} | {gap} | {comment} |"
        )
    lines.append("")

    lines.append("## 说明")
    lines.append("- `gap` = judge.overall - heuristic.overall（正/负表示 LLM 判定高于/低于规则分）。")
    lines.append("- 启发式对片段类回答的优势/劣势可通过逐条 gap 定位（如完整性、语义相关维度）。")
    lines.append("- 若多数条目的 heuristic 与 judge 偏差 >10 分，说明当前规则分不足以代表质量，应据此调阈值或补维度。")
    lines.append("- 扩展：向 `data/eval/cs_eval_set.json` 追加 `intent`/`user_query`/`reference_reply`/`expected_facts` 样本。")
    lines.append("")
    return "\n".join(lines)


def print_console_summary(summary: Dict[str, Any]):
    print("=== 客服回答离线评测汇总 ===")
    print(f"总样本数       : {summary['total']}")
    print(f"完成 LLM 质检  : {summary['judged']}")
    print(f"启发式均分     : {summary['heuristic_overall_avg']}")
    print(f"LLM judge 均分 : {summary['judge_overall_avg']}")
    print(f"平均 gap       : {summary['avg_gap']}")
    print(f"|gap|>10 条数  : {summary['sample_with_gap_gt_10']}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default="data/eval/cs_eval_set.json")
    parser.add_argument("--limit", type=int, default=0, help="仅评估前 N 条（0 表示全部）")
    parser.add_argument("--report-dir", default="reports")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    data_path = Path(args.data)
    if not data_path.is_absolute():
        data_path = repo_root / data_path
    dataset = load_eval_set(data_path)

    import asyncio

    samples = score_heuristic(dataset, args.limit)
    asyncio.run(score_judge(samples))

    summary = summarize(samples)
    report = format_report_md(dataset[: len(samples)], samples, summary, data_path=str(data_path))
    report_dir = Path(args.report_dir)
    if not report_dir.is_absolute():
        report_dir = repo_root / report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"cs_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    report_path.write_text(report, encoding="utf-8")

    print_console_summary(summary)
    print(f"报告已写入: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())