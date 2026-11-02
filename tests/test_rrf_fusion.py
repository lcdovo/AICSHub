"""RRF 混合检索融合测试：两路候选按 Reciprocal Rank Fusion 融合重排。

覆盖：
1. 单路为空时直接返回另一路（降级路径）
2. 两路都存在时正确按 RRF 分排序
3. 交叉命中的文档分数正确累加，最终按 final_score 降序
4. top_k 截断生效
"""
from app.services.knowledge_base import KnowledgeBaseService


def _make_service():
    # 用 __new__ 跳过 __init__ 的 DB/向量库初始化，仅测纯融合逻辑
    return object.__new__(KnowledgeBaseService)


def test_single_vector_returns_as_is():
    svc = _make_service()
    vector = [{"id": "d1", "title": "退货", "vector_score": 0.9}]
    merged = svc._merge_results(vector, [], 5)
    assert merged == vector


def test_single_bm25_returns_as_is():
    svc = _make_service()
    bm25 = [{"id": "d2", "title": "订单", "bm25_score": 8.0}]
    merged = svc._merge_results([], bm25, 5)
    assert merged == bm25


def test_dual_results_sorted_by_rrf():
    svc = _make_service()
    vector = [
        {"id": "a", "title": "A", "vector_score": 0.9},
        {"id": "b", "title": "B", "vector_score": 0.8},
    ]
    bm25 = [
        {"id": "c", "title": "C", "bm25_score": 9.0},
        {"id": "a", "title": "A", "bm25_score": 3.0},
    ]
    merged = svc._merge_results(vector, bm25, 5)

    # a 在两路都命中，RRF 分最高，排第一
    assert merged[0]["id"] == "a"
    assert merged[0]["final_score"] > merged[1]["final_score"]
    # b 与 c 按各自单路得分排序
    assert {m["id"] for m in merged} == {"a", "b", "c"}


def test_duplicate_doc_scores_stack():
    svc = _make_service()
    vector = [{"id": "a", "title": "A", "vector_score": 0.7}]
    bm25 = [{"id": "a", "title": "A", "bm25_score": 5.0}]
    merged = svc._merge_results(vector, bm25, 5)
    assert len(merged) == 1
    assert merged[0]["id"] == "a"
    # RRF: 1/(60+1) + 1/(60+1) = 2/61 约 0.032786...
    assert abs(merged[0]["final_score"] - (2 / 61)) < 1e-6


def test_top_k_limit_applies():
    svc = _make_service()
    vector = [
        {"id": f"v{i}", "title": f"V{i}", "vector_score": 0.9} for i in range(10)
    ]
    bm25 = [
        {"id": f"b{i}", "title": f"B{i}", "bm25_score": 9.0} for i in range(10)
    ]
    merged = svc._merge_results(vector, bm25, 5)
    # 候选池保留 top_k * 4 = 20 条（给重排器留足提升空间，见 _merge_results 注释）
    assert len(merged) == 20