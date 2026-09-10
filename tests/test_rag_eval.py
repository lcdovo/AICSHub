"""RAG 离线评估脚本回归测试：验证命中判定、指标聚合与报告结构，不依赖 SQLite/LLM。"""
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_rag.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("evaluate_rag", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    # 注册进 sys.modules，否则 dataclass 装饰器按 __module__ 查找时会失败。
    sys.modules[module.__name__] = module
    spec.loader.exec_module(module)
    return module


eval_script = _load_script()


# ---- 命中判定 ----

class TestMatchRank:
    def test_hit_at_first(self):
        assert eval_script.match_rank(["订单查询说明", "退换货政策"], ["订单查询说明"]) == 1

    def test_hit_at_third(self):
        assert eval_script.match_rank(["A", "B", "退换货政策"], ["退换货政策"]) == 3

    def test_partial_title_match(self):
        # 返回标题包含 ground_truth 片段时也算命中
        assert eval_script.match_rank(["退换货政策（2026版）", "其他"], ["退换货政策"]) == 1

    def test_no_hit(self):
        assert eval_script.match_rank(["A", "B"], ["退换货政策"]) == 0

    def test_empty_results(self):
        assert eval_script.match_rank([], ["退换货政策"]) == 0


# ---- 数据集加载 ----

class TestLoadEvalSet:
    def test_valid(self, tmp_path):
        path = tmp_path / "set.json"
        path.write_text('[{"query": "可以退货吗", "ground_truth": ["退换货政策"]}]', encoding="utf-8")
        dataset = eval_script.load_eval_set(path)
        assert dataset[0]["query"] == "可以退货吗"

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            eval_script.load_eval_set(tmp_path / "nope.json")

    def test_missing_ground_truth(self, tmp_path):
        path = tmp_path / "set.json"
        path.write_text('[{"query": "没有答案"}]', encoding="utf-8")
        with pytest.raises(ValueError):
            eval_script.load_eval_set(path)


# ---- 评估执行与指标 ----

def _make_search(responses):
    """构建按 query 返回预设结果的假检索函数。"""

    def search(query, top_k):
        return responses.get(query, {"results": [], "latency_ms": 1, "search_strategy": "hybrid"})

    return search


class TestEvaluateAndSummarize:
    DATASET = [
        {"query": "q_hit_first", "ground_truth": ["DocA"]},
        {"query": "q_hit_second", "ground_truth": ["DocB"]},
        {"query": "q_miss", "ground_truth": ["DocC"]},
    ]

    RESPONSES = {
        "q_hit_first": {"results": [{"title": "DocA"}, {"title": "DocB"}], "latency_ms": 12.0, "search_strategy": "hybrid"},
        "q_hit_second": {"results": [{"title": "DocX"}, {"title": "DocB"}], "latency_ms": 8.0, "search_strategy": "hybrid"},
        "q_miss": {"results": [], "latency_ms": 1.0, "search_strategy": "empty"},
    }

    def test_ranks(self):
        evaluated = eval_script.evaluate_query(_make_search(self.RESPONSES), self.DATASET, top_k=5, with_answer_quality=False)
        ranks = [ev.rank for ev in evaluated]
        assert ranks == [1, 2, 0]

    def test_summary_metrics(self):
        evaluated = eval_script.evaluate_query(_make_search(self.RESPONSES), self.DATASET, top_k=5, with_answer_quality=False)
        summary = eval_script.summarize(evaluated, top_k=5)
        # 三条：命中@1 一条、@3 两条、未命中一条 → recall 1/3、2/3、MRR (1 + 0.5 + 0)/3
        assert summary["total_queries"] == 3
        assert summary["recall"]["recall@1"] == pytest.approx(round(1 / 3, 4))
        assert summary["recall"]["recall@3"] == pytest.approx(round(2 / 3, 4))
        assert summary["recall"]["recall@5"] == pytest.approx(round(2 / 3, 4))
        assert summary["mrr"] == pytest.approx(0.5)
        assert summary["avg_latency_ms"] == pytest.approx(7.0)
        assert summary["empty_result_rate"] == pytest.approx(round(1 / 3, 4))
        assert summary["avg_answer_score"] is None

    def test_answer_quality_column_fills(self):
        dataset = [{"query": "q", "ground_truth": ["DocA"], "expected_answer": "答案"}]
        responses = {"q": {"results": [{"title": "DocA", "content": "答案内容"}], "latency_ms": 1, "search_strategy": "hybrid"}}
        evaluated = eval_script.evaluate_query(_make_search(responses), dataset, top_k=5, with_answer_quality=True)
        # AnswerEvaluator 为启发式评分，返回 0-100 数值即通过
        assert evaluated[0].answer_score is not None
        assert 0 <= evaluated[0].answer_score <= 100


# ---- 报告生成 ----

class TestReport:
    def test_report_structure(self):
        evaluated = eval_script.evaluate_query(_make_search(TestEvaluateAndSummarize.RESPONSES), TestEvaluateAndSummarize.DATASET, top_k=5, with_answer_quality=False)
        summary = eval_script.summarize(evaluated, top_k=5)
        report = eval_script.format_report_md(
            TestEvaluateAndSummarize.DATASET, evaluated, summary,
            with_answer_quality=False, data_path="data/eval/rag_eval_set.json",
        )
        assert "## 汇总指标" in report
        assert "## 逐条明细" in report
        for item in TestEvaluateAndSummarize.DATASET:
            assert item["query"] in report
        assert "q_hit_first" in report and "q_miss" in report

    def test_report_with_quality_column(self):
        dataset = [{"query": "q", "ground_truth": ["DocA"], "expected_answer": "答案"}]
        responses = {"q": {"results": [{"title": "DocA", "content": "答案内容"}], "latency_ms": 1, "search_strategy": "hybrid"}}
        evaluated = eval_script.evaluate_query(_make_search(responses), dataset, top_k=5, with_answer_quality=True)
        summary = eval_script.summarize(evaluated, top_k=5)
        report = eval_script.format_report_md(
            dataset, evaluated, summary, with_answer_quality=True, data_path="x.json",
        )
        assert "answer_score" in report