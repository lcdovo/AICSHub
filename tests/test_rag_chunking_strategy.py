from app.services.knowledge_base import DocumentChunker
from app.services.knowledge_base import KnowledgeBaseService


def test_heading_chunk_keeps_section_and_adds_source_prefix():
    chunker = DocumentChunker(split_pattern="heading")
    chunks = chunker.chunk_document(
        "# 商品政策\n\n## 二、退换货流程\n第一步提交申请。\n第二步寄回商品。\n\n## 三、质保\n质保期为一年。",
        {"title": "售后政策", "category": "售后"},
    )
    assert len(chunks) == 2
    assert chunks[0]["content"].startswith("文档：售后政策\n章节：二、退换货流程\n")
    assert "第一步提交申请。\n第二步寄回商品。" in chunks[0]["content"]
    assert "三、质保" in chunks[1]["content"]


def test_long_heading_falls_back_to_sentence_split():
    body = "。".join(["这是很长的章节内容"] * 100) + "。"
    chunks = DocumentChunker(split_pattern="heading").chunk_document(
        f"## 长章节\n{body}", {"title": "长文档"}
    )
    assert len(chunks) > 1
    assert all("文档：长文档" in item["content"] for item in chunks)


def test_faq_is_one_question_and_answer_per_chunk():
    content = """# FAQ
## 退款
### 常见问题
**Q：退款多久到账？**
A：通常 3-7 个工作日。
**Q：退款退到哪里？**
A：原支付账户。
"""
    chunks = DocumentChunker(split_pattern="heading").chunk_document(
        content, {"title": "FAQ", "category": "faq"}
    )
    assert len(chunks) == 2
    assert all("文档：FAQ" in item["content"] and "章节：退款 / 常见问题" in item["content"] for item in chunks)
    assert all(item["content"].count("**Q：") == 1 for item in chunks)


def test_rerank_boosts_faq_match_and_domain_categories():
    kb = KnowledgeBaseService()
    candidates = [
        {"id": "faq", "title": "FAQ", "category": "faq", "content": "文档：FAQ\n章节：退款\n**Q：退款多久到账？**\nA：3-7个工作日。", "final_score": 0.2},
        {"id": "policy", "title": "退换货政策", "category": "售后", "content": "退换货和质保政策", "final_score": 0.2},
    ]
    result = kb._rerank_results("退款多久到账？", candidates, 2)
    assert result[0]["id"] == "faq"
    assert result[0]["final_score"] >= 0.7
