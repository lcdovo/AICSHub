"""客服回答离线评测脚本回归测试：启发式评分、LLM judge 降级与报告结构。

复用 test_rag_eval.py 的脚本加载方式，不依赖真实 LLM/数据库：
1. 数据集加载与字段校验
2. 启发式评分逐维输出
3. LLM 不可用时 judge 保持空（降级路径）
4. 汇总与报告结构
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_answers.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("evaluate_answers", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module.__name__] = module
    spec.loader.exec_module(module)
    return module


eval_script = _load_script()


SAMPLE = {
    "intent": "query_order",
    "user_query": "订单 ORD20260801 现在到哪了？",
    "reference_reply": "您的订单 ORD20260801 已从顺丰发货，状态为运输中。",
    "expected_facts": ["ORD20260801", "顺丰"],
    "evidence": "订单 ORD20260801 状态=运输中",
}


class TestLoadEvalSet:
    def test_valid(self, tmp_path):
        path = tmp_path / "set.json"
        path.write_text('[{"user_query": "可以退货吗", "reference_reply": "可以。"}]', encoding="utf-8")
        dataset = eval_script.load_eval_set(path)
        assert dataset[0]["user_query"] == "可以退货吗"

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            eval_script.load_eval_set(tmp_path / "nope.json")

    def test_missing_reference_reply(self, tmp_path):
        path = tmp_path / "set.json"
        path.write_text('[{"user_query": "问题"}]', encoding="utf-8")
        with pytest.raises(ValueError):
            eval_script.load_eval_set(path)


class TestHeuristic:
    def test_scores_populated(self):
        samples = eval_script.score_heuristic([SAMPLE], limit=0)
        assert len(samples) == 1
        h = samples[0].heuristic
        assert 0 <= h["overall_score"] <= 100
        # 维度键与线上 EvaluationResult 同口径
        for key in ("accuracy_score", "completeness_score", "safety_score", "relevance_score"):
            assert key in h

    def test_limit_applies(self):
        samples = eval_script.score_heuristic([SAMPLE, SAMPLE], limit=1)
        assert len(samples) == 1

    def test_response_carried(self):
        samples = eval_script.score_heuristic([SAMPLE], limit=0)
        assert samples[0].response == SAMPLE["reference_reply"]


class TestJudgeDegradation:
    async def test_llm_unavailable_keeps_judge_empty(self, monkeypatch):
        class UnavailableLLM:
            def is_available(self):
                return False

        # score_judge 内部按需 import LLMService，须 patch 其所在模块
        monkeypatch.setattr(
            "app.services.llm_service.LLMService", lambda: UnavailableLLM()
        )
        samples = eval_script.score_heuristic([SAMPLE], limit=0)
        await eval_script.score_judge(samples)
        assert samples[0].judge == {}
        assert samples[0].overall_gap is None


class TestSummarizeAndReport:
    def _samples(self):
        samples = eval_script.score_heuristic([SAMPLE], limit=0)
        # 模拟一条完成 LLM 质检
        samples[0].judge = {
            "accuracy": 95.0, "completeness": 90.0, "relevance": 96.0,
            "safety": 100.0, "overall": 96.0, "evaluation_comment": "准确完整",
        }
        samples[0].overall_gap = 10.0
        return samples

    def test_summary(self):
        summary = eval_script.summarize(self._samples())
        assert summary["total"] == 1
        assert summary["judged"] == 1
        assert summary["heuristic_overall_avg"] is not None
        assert summary["judge_overall_avg"] == 96.0

    def test_report_structure(self):
        samples = self._samples()
        summary = eval_script.summarize(samples)
        report = eval_script.format_report_md([SAMPLE], samples, summary, data_path="data/eval/cs_eval_set.json")
        assert "## 汇总指标" in report
        assert "## 逐条明细" in report
        assert SAMPLE["user_query"][:36] in report


class TestJudgeContract:
    def test_score_bounds(self):
        score = eval_script._JudgeScore(
            accuracy=100, completeness=50, relevance=80, safety=90, overall=82
        )
        assert 0 <= score.overall <= 100

    def test_score_rejects_out_of_range(self):
        with pytest.raises(ValueError):
            eval_script._JudgeScore(
                accuracy=150, completeness=50, relevance=80, safety=90, overall=82
            )