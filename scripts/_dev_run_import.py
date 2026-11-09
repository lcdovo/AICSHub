"""Temporary runner: preheat embedding model, then clear + re-import all docs.
All output is printed line-by-line so progress is visible via the log file.
"""
from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, "/app")

from pathlib import Path

from app.services.knowledge_base import get_knowledge_base

FOLDER = Path("/app") / "\u6587\u6863"


def category_for(name: str) -> str:
    if name == "FAQ.md":
        return "faq"
    if any(w in name for w in ("退换货", "质保", "售后", "投诉")):
        return "售后服务"
    if any(w in name for w in ("会员", "优惠", "满减")):
        return "会员优惠"
    if any(w in name for w in ("物流", "配送")):
        return "物流服务"
    if any(w in name for w in ("订单", "下单", "发票", "支付")):
        return "订单服务"
    return "产品与服务"


def main() -> int:
    kb = get_knowledge_base()
    print("BACKEND:", kb.backend, "DEGRADED:", kb.degraded, kb.degraded_reason, flush=True)
    print("EMBEDDING:", kb._embedding_service.backend_name, flush=True)

    # 1) Warm the embedding model with a realistic long text so the cold-start
    #    model load (can take >30s) never hits the API client's timeout.
    long_text = ("智能手表产品说明与使用指南。" * 120)
    t0 = time.time()
    vec = kb._embedding_service.encode(long_text)
    print("WARM_OK dim=%d %.1fs" % (len(vec), time.time() - t0), flush=True)

    # 2) Clear everything, then import every markdown file.
    files = sorted(FOLDER.glob("*.md"))
    print("FILES:", len(files), flush=True)
    kb.clear_all()
    print("CLEARED", flush=True)

    results = []
    t_start = time.time()
    for path in files:
        content = path.read_text(encoding="utf-8")
        t0 = time.time()
        try:
            r = kb.add_document(
                title=path.stem,
                content=content,
                category=category_for(path.name),
                keywords=[],
                source="文档/" + path.name,
            )
            print("DOC_OK %s %s %.1fs" % (path.name, r, time.time() - t0), flush=True)
            results.append({"file": path.name, **r})
        except Exception as e:
            print("DOC_FAIL %s %s" % (path.name, e), flush=True)
            results.append({"file": path.name, "success": False, "error": str(e)})

    print("TOTAL_ELAPSED_MIN: %.1f" % ((time.time() - t_start) / 60), flush=True)
    stats = kb.get_stats()
    summary = {
        "files": len(files),
        "success": sum(1 for x in results if x.get("success")),
        "results": results,
        "stats": stats,
    }
    print("RESULT_OK", json.dumps(summary, ensure_ascii=True, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())