"""隔离SQLite上的生产分块/词法检索诊断；合成向量，不调用模型。"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def source_fingerprint() -> dict[str, str]:
    paths = [*sorted((ROOT / "app/services/knowledge_base").glob("*.py")),
             ROOT / "app/repositories/knowledge_repository.py", ROOT / "app/services/reranker_service.py",
             ROOT / "app/config/config.py", Path(__file__).resolve()]
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


class SyntheticEmbedding:
    _dim = 3
    backend_name = "rag-structure-synthetic-v1"

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        return [[1., 0., 0.] for _ in texts]

    def encode_query(self, text: str) -> list[float]:
        raise AssertionError("lexical diagnostic must not call query embedding")


def evaluate() -> dict:
    from app.config.config import settings
    from app.models.models import Base, BackgroundJob
    from app.repositories.knowledge_repository import KnowledgeRepository
    from app.services.knowledge_base import KnowledgeBaseService
    from app.services.knowledge_base.index_worker import index_job

    sources = source_fingerprint()
    # 包含标题独有型号、层级上下文、表格单位、长FAQ例外、前言、精确型号。
    documents = [
        ("ZX900投影仪", "# 产品说明\n## 接口\n支持连接HDMI，最大输入分辨率1920×1080。"),
        ("ZX800投影仪", "# 产品说明\n## 接口\n仅支持USB，不能连接HDMI。"),
        ("室内摄像头", "适用范围：仅限室内，禁止室外淋雨使用。\n# 手册\n## 安装\n### 供电\n使用12V原装适配器。"),
        ("费用说明", "## 服务费用\n| 项目 | 金额（元） |\n| --- | --- |\n" + "| 安装服务 | 50 |\n" * 18),
        ("退货FAQ", "## 售后\nQ: 定制商品能退吗？\nA: " + "普通商品需保持包装完好。" * 12 + "但定制商品不适用无理由退货。\nQ: 保修多久？\nA: 一年。"),
        ("打印机排障", "# Printer-P7\n## 错误码\nE-102表示缺纸，补充纸张后重试。\n## 清洁\n先断电再清洁。"),
    ]
    cases = [
        ("ZX900 HDMI", "ZX900投影仪", "1920×1080"),
        ("ZX800 USB", "ZX800投影仪", "不能连接HDMI"),
        ("摄像头供电", "室内摄像头", "12V"),
        ("摄像头室外淋雨", "室内摄像头", "禁止室外"),
        ("安装服务金额", "费用说明", "50"),
        ("定制商品无理由退货", "退货FAQ", "不适用无理由退货"),
        ("Printer-P7 E-102", "打印机排障", "缺纸"),
        ("打印机清洁", "打印机排障", "先断电"),
    ]
    overrides = {"USE_MILVUS": False, "RAG_USE_RERANKER": False,
                 "RERANKER_API_BASE": "", "RERANKER_LOCAL_BASE": "",
                 "CHUNK_SIZE": 160, "CHUNK_OVERLAP": 20, "CHUNK_SPLIT_PATTERN": "heading",
                 "RAG_CONTEXT_NEIGHBORS": 1, "RAG_EVIDENCE_MAX_CHARS": 8000}
    previous = {key: getattr(settings, key) for key in overrides}
    for key, value in overrides.items():
        setattr(settings, key, value)
    try:
        with tempfile.TemporaryDirectory(prefix="rag-structure-") as directory:
            repository = KnowledgeRepository("sqlite:///" + (Path(directory) / "knowledge.db").as_posix())
            try:
                Base.metadata.create_all(repository.engine)
                kb = KnowledgeBaseService(repository=repository, embedding=SyntheticEmbedding())
                for title, body in documents:
                    created = kb.add_document(title, body)
                    with repository.transaction() as db:
                        job = db.get(BackgroundJob, created["job_id"])
                        job.status, job.lease_token = "running", "structure-diagnostic"
                        job.lease_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=60)
                        job.attempts += 1
                    index_job(repository, kb._embedding_service, None, created["job_id"], "structure-diagnostic")
                    if kb.document_detail(created["document_id"])["status"] != "published":
                        raise RuntimeError("synthetic_index_failed")
                rows = []
                for query, title, phrase in cases:
                    for temperature in ("miss", "hit"):
                        began = time.perf_counter()
                        result = kb.search(query, top_k=3, retrieval_mode="bm25")
                        latency = (time.perf_counter() - began) * 1000
                        evidence = result["results"]
                        relevant = [c for c in evidence if c["title"] == title]
                        rows.append({"query": query, "cache": temperature,
                                     "cache_origin_correct": result["from_cache"] == (temperature == "hit"),
                                     "document_hit_at_3": bool(relevant),
                                     "required_source_text_present": any(phrase in c["evidence_text"] for c in relevant),
                                     "latency_ms": round(latency, 3), "metadata": result["meta"]})
                return {"status": "SYNTHETIC_LEXICAL_DIAGNOSTIC_NOT_ACCEPTANCE",
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "corpus_sha256": hashlib.sha256(json.dumps(documents, ensure_ascii=False).encode()).hexdigest(),
                        "source_sha256": sources, "source_stable": sources == source_fingerprint(),
                        "documents": len(documents), "cases": len(cases), "rows": rows,
                        "document_hit_at_3": sum(r["document_hit_at_3"] for r in rows) / len(rows),
                        "required_source_text_present": sum(r["required_source_text_present"] for r in rows) / len(rows),
                        "latency_p95_ms": {state: sorted(r["latency_ms"] for r in rows if r["cache"] == state)[math.ceil(.95 * len(cases)) - 1]
                                           for state in ("miss", "hit")},
                        "snapshot_builds": kb._retrieval_runtime.snapshot_builds,
                        "lexical_index_builds": kb._retrieval_runtime.lexical_builds,
                        "real_model_calls": 0, "whole_m5_pass": False,
                        "limits": "8 synthetic lexical cases; source text availability is not generated claim support, semantic recall, or GPU capacity acceptance"}
            finally:
                repository.engine.dispose()
    finally:
        for key, value in previous.items():
            setattr(settings, key, value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; use a new filename")
    report = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({key: value for key, value in report.items() if key not in {"rows", "limits", "source_sha256"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
