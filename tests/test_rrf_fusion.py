"""RRF 混合检索融合测试：两路候选按 Reciprocal Rank Fusion 融合重排。

覆盖：
1. 单路为空时保留另一路候选并统一RRF分数
2. 两路都存在时正确按 RRF 分排序
3. 交叉命中的文档分数正确累加，最终按 final_score 降序
4. 完整候选进入后续重排/证据预算
"""
from app.services.knowledge_base.ranking import fuse_candidates


def test_single_vector_returns_as_is():
    vector = [{"id": "d1", "title": "退货", "vector_score": 0.9}]
    merged = fuse_candidates(vector, [], 60)
    assert merged[0]["id"] == "d1" and merged[0]["vector_score"] == .9
    assert merged[0]["final_score"] == 1 / 61


def test_single_bm25_returns_as_is():
    bm25 = [{"id": "d2", "title": "订单", "bm25_score": 8.0}]
    merged = fuse_candidates([], bm25, 60)
    assert merged[0]["id"] == "d2" and merged[0]["bm25_score"] == 8.0
    assert merged[0]["final_score"] == 1 / 61


def test_dual_results_sorted_by_rrf():
    vector = [
        {"id": "a", "title": "A", "vector_score": 0.9},
        {"id": "b", "title": "B", "vector_score": 0.8},
    ]
    bm25 = [
        {"id": "c", "title": "C", "bm25_score": 9.0},
        {"id": "a", "title": "A", "bm25_score": 3.0},
    ]
    merged = fuse_candidates(vector, bm25, 60)

    # a 在两路都命中，RRF 分最高，排第一
    assert merged[0]["id"] == "a"
    assert merged[0]["final_score"] > merged[1]["final_score"]
    # b 与 c 按各自单路得分排序
    assert {m["id"] for m in merged} == {"a", "b", "c"}


def test_duplicate_doc_scores_stack():
    vector = [{"id": "a", "title": "A", "vector_score": 0.7}]
    bm25 = [{"id": "a", "title": "A", "bm25_score": 5.0}]
    merged = fuse_candidates(vector, bm25, 60)
    assert len(merged) == 1
    assert merged[0]["id"] == "a"
    # RRF: 1/(60+1) + 1/(60+1) = 2/61 约 0.032786...
    assert abs(merged[0]["final_score"] - (2 / 61)) < 1e-6


def test_full_candidate_pool_reaches_rerank_budget():
    vector = [
        {"id": f"v{i}", "title": f"V{i}", "vector_score": 0.9} for i in range(10)
    ]
    bm25 = [
        {"id": f"b{i}", "title": f"B{i}", "bm25_score": 9.0} for i in range(10)
    ]
    merged = fuse_candidates(vector, bm25, 60)
    # Fusion keeps all candidates; production rerank/evidence budgets apply afterward.
    assert len(merged) == 20
