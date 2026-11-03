"""将 ``文档/`` 下的 Markdown 文档按当前 RAG 策略导入知识库。

示例：``python scripts/import_rag_documents.py``
脚本通过 KnowledgeBaseService 写入持久化数据库，排队knowledge_index任务，独立worker验证索引后才发布。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.knowledge_base import get_knowledge_base


def _category_for(name: str) -> str:
    if name == "FAQ.md":
        return "faq"
    if any(word in name for word in ("退换货", "质保", "售后", "投诉")):
        return "售后服务"
    if any(word in name for word in ("会员", "优惠", "满减")):
        return "会员优惠"
    if any(word in name for word in ("物流", "配送")):
        return "物流服务"
    if any(word in name for word in ("订单", "下单", "发票", "支付")):
        return "订单服务"
    return "产品与服务"


def import_documents(folder: Path, clear: bool = False) -> dict:
    kb = get_knowledge_base()
    if clear:
        kb.clear_all()
    files = sorted(folder.glob("*.md"))
    results = []
    for path in files:
        content = path.read_text(encoding="utf-8")
        result = kb.add_document(
            title=path.stem,
            content=content,
            category=_category_for(path.name),
            keywords=[],
            source=f"文档/{path.name}",
        )
        results.append({"file": path.name, **result})
    stats = kb.get_stats()
    return {"files": len(files), "queued": sum(r.get("success", False) for r in results), "status":"queued", "results": results, "stats": stats}


def main() -> None:
    parser = argparse.ArgumentParser(description="导入文档目录到 RAG 知识库")
    parser.add_argument("--folder", type=Path, default=Path("文档"))
    parser.add_argument("--clear", action="store_true", help="导入前清空现有知识库")
    args = parser.parse_args()
    print(json.dumps(import_documents(args.folder, args.clear), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
