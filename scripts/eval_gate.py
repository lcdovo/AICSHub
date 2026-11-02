"""评测门禁：意图准确率 + 端到端解决率/p95 的单命令阈值检查。

用途：任何 prompt/路由/检索/缓存改动前后跑一遍，防止改动悄悄退化质量。
不改业务代码，只编排现有 evaluate_intents / evaluate_e2e 两个评测脚本并对结果设门禁。

用法（仓库根目录）：
    python scripts/eval_gate.py                      # 全量默认阈值
    python scripts/eval_gate.py --skip-e2e           # 只跑意图门禁（离线环境）
    python scripts/eval_gate.py --min-resolution-rate 0.85 --max-p95-ms 4000

退出码：全部通过 0；任一门禁失败或评测脚本异常 1。CI/预提交可直接依赖退出码。
"""
import argparse
import asyncio
import sys
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
# 评测脚本以脚本方式编写，通过模块名复用其函数，不复制逻辑。
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def run_intent_gate(data: str, limit: int, min_accuracy: float) -> tuple[bool, str]:
    import evaluate_intents

    data_path = Path(data)
    if not data_path.is_absolute():
        data_path = REPO_ROOT / data_path
    dataset = evaluate_intents.load_eval_set(data_path)
    results = evaluate_intents.evaluate_dataset(dataset, limit)
    summary = evaluate_intents.summarize(results)
    accuracy = summary["accuracy"]
    correct = sum(1 for r in results if r.correct)
    ok = accuracy >= min_accuracy
    return ok, f"意图准确率 {accuracy:.2%}（阈值 ≥{min_accuracy:.2%}，{correct}/{summary['total']}）"


async def _e2e_summary(data: str, limit: int, runs: int) -> dict:
    import evaluate_e2e

    data_path = Path(data)
    if not data_path.is_absolute():
        data_path = REPO_ROOT / data_path
    dataset = evaluate_e2e.load_eval_set(data_path)
    samples = await evaluate_e2e.run_all(evaluate_e2e._evaluate_samples, dataset, limit, runs)
    return evaluate_e2e.summarize(samples, samples[0].llm_available if samples else False)


def run_e2e_gate(data: str, limit: int, runs: int, min_resolution: float, max_p95_ms: float) -> List[tuple[bool, str]]:
    summary = asyncio.run(_e2e_summary(data, limit, runs))
    results: List[tuple[bool, str]] = []
    rate = summary["resolution_rate"]
    results.append((
        rate >= min_resolution,
        f"端到端解决率 {rate:.2%}（阈值 ≥{min_resolution:.2%}，{summary['resolved']}/{summary['total']}）",
    ))
    if summary["delay_available"]:
        p95 = summary["delay_p95_ms"]
        results.append((
            p95 <= max_p95_ms,
            f"端到端 p95 {p95:.0f}ms（阈值 ≤{max_p95_ms:.0f}ms，范围 {summary['delay_scope']}）",
        ))
    else:
        results.append((True, "延迟样本为空，p95 门禁跳过"))
    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--intent-data", default="data/eval/intent_eval_set.json")
    parser.add_argument("--e2e-data", default="data/eval/cs_eval_set.json")
    parser.add_argument("--intent-limit", type=int, default=0, help="意图集只评前 N 条（0=全部）")
    parser.add_argument("--e2e-limit", type=int, default=0, help="e2e 集只评前 N 条（0=全部）")
    # runs=1 冷启动：避免答案缓存污染门禁数据（项目教训：缓存命中会掩盖真实延迟/质量）。
    parser.add_argument("--runs", type=int, default=1, help="e2e 每条样本重复次数（默认 1 冷启动）")
    parser.add_argument("--min-intent-accuracy", type=float, default=0.99)
    parser.add_argument("--min-resolution-rate", type=float, default=0.80)
    parser.add_argument("--max-p95-ms", type=float, default=5000.0)
    parser.add_argument("--skip-intent", action="store_true")
    parser.add_argument("--skip-e2e", action="store_true")
    args = parser.parse_args(argv)

    gate_results: List[tuple[str, bool, str]] = []
    if not args.skip_intent:
        try:
            ok, msg = run_intent_gate(args.intent_data, args.intent_limit, args.min_intent_accuracy)
            gate_results.append(("intent", ok, msg))
        except Exception as exc:
            gate_results.append(("intent", False, f"意图评测执行异常: {exc}"))
    if not args.skip_e2e:
        try:
            for ok, msg in run_e2e_gate(args.e2e_data, args.e2e_limit, args.runs, args.min_resolution_rate, args.max_p95_ms):
                gate_results.append(("e2e", ok, msg))
        except Exception as exc:
            gate_results.append(("e2e", False, f"e2e 评测执行异常: {exc}"))

    print("=== 评测门禁结果 ===")
    failed = 0
    for name, ok, msg in gate_results:
        print(f"[{'PASS' if ok else 'FAIL'}] ({name}) {msg}")
        failed += 0 if ok else 1
    if not gate_results:
        print("未启用任何门禁（--skip-intent 与 --skip-e2e 同时指定）")
        return 1
    print(f"门禁总计: {len(gate_results) - failed}/{len(gate_results)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
