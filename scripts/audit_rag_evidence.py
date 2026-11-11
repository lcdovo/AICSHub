"""只读检索核查：捕获真实候选并对照邻块组装，不调用聊天/理解/Judge。

默认BM25召回加当前重排；--modes hybrid bm25 会调用当前配置的Embedding和Reranker，
只能在确认它们为本地服务后使用。输出包含知识正文，按运行证据保存。
本工具不判断语义正确性，也不修改配置、索引、知识或历史报告。
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.parse import urlsplit


CASE_IDS = ("MI-01", "MI-06", "MI-12", "MI-13", "MI-14", "MI-16", "V2N-02", "V2N-04")
CONTEXT_PROBES = (
    ("MI-12-context", "退款到账的工作日时效，节假日怎么算"),
    ("V2N-02-context", "Type-C接口视频输出，使用前要看什么标识"),
    ("MI-13-power", "耳机无法开机，充电、电源与指示灯排查"),
    ("device-negative", "打印机卡纸怎么排查"),
)


def require_local_endpoint(value: str) -> None:
    """Fail before model construction when a configured endpoint is external."""
    if not value:
        return
    hostname = urlsplit(value).hostname or ""
    if hostname in {"localhost", "embedding-local", "reranker", "host.docker.internal"}:
        return
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        raise ValueError("audit_requires_local_model_endpoints") from None
    if not (address.is_loopback or address.is_private):
        raise ValueError("audit_requires_local_model_endpoints")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modes", nargs="+", choices=("bm25", "hybrid"), default=["bm25"])
    parser.add_argument("--scope", choices=("whole", "items", "probes"), default="whole")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from app.config.config import settings
    from app.services.knowledge_base import get_knowledge_base
    from app.services.knowledge_base import versioned_search
    from app.services.knowledge_base.evidence_assembly import assemble_evidence

    if "hybrid" in args.modes:
        require_local_endpoint(settings.EMBEDDING_API_BASE)
    if settings.RAG_USE_RERANKER:
        require_local_endpoint(settings.RERANKER_API_BASE)
        require_local_endpoint(settings.RERANKER_LOCAL_BASE)
    cases_path = root / "acceptance/multi-intent/scenarios-reviewed-v2.json"
    cases_bytes = cases_path.read_bytes()
    cases = json.loads(cases_bytes)["cases"]
    kb = get_knowledge_base()
    records = []
    queries = []
    for case in cases:
        if case["case_id"] in CASE_IDS:
            if args.scope == "whole":
                queries.append((case["case_id"], None, case["message"]))
            elif args.scope == "items":
                queries.extend((case["case_id"], index, item["text"])
                    for index, item in enumerate(case["items"], 1)
                    if item["kind"] in {"knowledge", "troubleshoot"})
    if args.scope == "probes":
        queries = [(case_id, None, query) for case_id, query in CONTEXT_PROBES]
    for case_id, item_index, query in queries:
        for mode in args.modes:
            captured = {}

            def capture(candidates, chunks, **kwargs):
                clean = lambda row: {k: v for k, v in row.items() if not k.startswith("_")}
                captured["ranked_candidates"] = [clean(row) for row in candidates]
                captured["published_chunk_count"] = len(chunks)
                captured["assembly_parameters"] = kwargs
                captured["without_neighbors"] = assemble_evidence(
                    candidates, chunks, **{**kwargs, "neighbors": 0})[0]
                captured["top5_diagnostic"] = assemble_evidence(
                    candidates, chunks, **{**kwargs, "top_k": 5})[0]
                return assemble_evidence(candidates, chunks, **kwargs)

            # Only discard this short-lived audit process's cache, never shared data.
            with patch.object(kb._query_cache, "get", return_value=None):
                with patch.object(versioned_search, "assemble_evidence", capture):
                    result = kb.search(query, retrieval_mode=mode)
            if "ranked_candidates" not in captured:
                raise RuntimeError(f"assembly_not_reached:{case_id}:{mode}")
            records.append({"case_id": case_id, "item_index": item_index, "query": query,
                            "mode": mode, "actual": result, **captured})
    print(json.dumps({"scope": "retrieval_only_no_understanding_generation_or_judge",
        "case_sha256": hashlib.sha256(cases_bytes).hexdigest(), "query_scope": args.scope,
        "query_limitations": "fixed_oracle_text_without_live_understanding_or_task_product_prefix",
        "config": {key: getattr(settings, key) for key in (
            "RAG_TOP_K", "RAG_RECALL_K", "RAG_RERANK_K", "RAG_CONTEXT_NEIGHBORS",
            "RAG_EVIDENCE_MAX_CHARS", "RAG_USE_RERANKER", "RERANKER_LOCAL_BATCH_SIZE")},
        "source_hashes": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("app/services/knowledge_base/evidence_assembly.py",
                         "app/services/knowledge_base/versioned_search.py")},
        "records": records}, ensure_ascii=False))


if __name__ == "__main__":
    main()
