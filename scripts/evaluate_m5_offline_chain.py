"""M5 v2生产组件离线诊断：临时SQL、BM25冷/热、固定候选及Judge不可用；不调用模型。"""
from __future__ import annotations

import argparse
import ast
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from statistics import mean
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_package(directory: Path, development: Path) -> tuple:
    from scripts.evaluate_m5 import Dataset, validate_splits
    paths = [directory / name for name in ("dataset.json", "corpus.json", "manifest.json")]
    inputs = {path.name: path.read_bytes() for path in paths}
    manifest = json.loads(inputs["manifest.json"])
    if any(manifest["files"][name] != sha256(inputs[name]) for name in ("dataset.json", "corpus.json")):
        raise ValueError("package_hash_mismatch")
    dataset = Dataset.model_validate_json(inputs["dataset.json"])
    if dataset.label_source != "assistant_reviewed" or dataset.schema_version != 2:
        raise ValueError("expected_v2_assistant_reviewed_material")
    validate_splits(Dataset.model_validate_json(development.read_bytes()), dataset)
    corpus = json.loads(inputs["corpus.json"])
    keys = [(item["chunk_id"], item["version_id"]) for item in corpus]
    if len(set(keys)) != len(keys) or any(not item["content"].strip() for item in corpus):
        raise ValueError("invalid_corpus")
    if any(ref.key() not in set(keys) for case in dataset.cases for ref in case.relevant):
        raise ValueError("missing_or_wrong_evidence_version")
    if manifest["total_cases"] != len(dataset.cases) or manifest["case_counts"] != dict(Counter(c.kind for c in dataset.cases)):
        raise ValueError("package_count_mismatch")
    paths.append(development)
    return dataset, corpus, {str(path): sha256(path.read_bytes()) for path in paths}


class IndexOnlyEmbedding:
    """只为临时SQL索引提供占位向量，任何查询编码都视作评测错误。"""
    _dim = 3
    backend_name = "offline-index-placeholder-not-a-model"

    def __init__(self):
        self.indexed_texts = 0
        self.query_calls = 0

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        self.indexed_texts += len(texts)
        return [[1.0, 0.0, 0.0] for _ in texts]

    def encode_query(self, text: str) -> list[float]:
        self.query_calls += 1
        raise RuntimeError("bm25_must_not_encode_query")


def populate(kb, corpus: list[dict]) -> dict:
    from app.models.models import BackgroundJob
    from app.services.knowledge_base.index_worker import index_job
    mapping = {}
    for doc in corpus:
        created = kb.add_document(doc["title"], doc["content"])
        with kb._repository.transaction() as db:
            job = db.get(BackgroundJob, created["job_id"])
            job.status, job.lease_token = "running", "m5-offline"
            job.lease_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=60)
            job.attempts += 1
        index_job(kb._repository, kb._embedding_service, None, created["job_id"], "m5-offline")
        _, snapshot = kb._repository.snapshot()
        chunks = [c for c in snapshot if c["version_id"] == created["version_id"]]
        # 当前标签是一文一块，不能将未来多块文档静默映成任意首块。
        if len(chunks) != 1 or chunks[0]["content"] != doc["content"]:
            raise ValueError("canonical_chunk_mapping_not_exact")
        mapping[(doc["chunk_id"], doc["version_id"])] = chunks[0]
    return mapping


def retrieval_baseline(kb, dataset, mapping: dict, *, evidence_k: int) -> dict:
    from scripts.evaluate_m5 import Chunk, RelevantChunk, ranking_metrics
    reverse = {(item["id"], item["version_id"]): key for key, item in mapping.items()}
    rows = []
    for case in dataset.cases:
        if case.kind != "answerable":
            continue
        kb._query_cache.clear()
        for temperature in ("cold", "hot"):
            began = time.perf_counter()
            try:
                result = kb.search(case.query, top_k=20, recall_k=20, rerank_k=20, retrieval_mode="bm25")
                if bool(result["from_cache"]) != (temperature == "hot"):
                    raise ValueError("unexpected_cache_origin")
                if result["degraded"] or result["meta"]["routes"]["dense"]["ids"] or result["meta"]["reranker"] != "none":
                    raise ValueError("unexpected_model_or_degraded_route")
                ranked = [Chunk(chunk_id=reverse[(c["id"], c["version_id"])][0],
                                version_id=reverse[(c["id"], c["version_id"])][1]) for c in result["results"]]
                relevant = [RelevantChunk.model_validate(c.model_dump()) for c in case.relevant]
                metrics = ranking_metrics(relevant, ranked)
                evidence = result["results"][:evidence_k]
                text = "\n".join(c["content"] for c in evidence)
                rows.append({"case_id": case.case_id, "cache": temperature, "error": None,
                    "ranked_chunks": [c.model_dump() for c in ranked], **metrics,
                    "required_evidence_text_present": all(condition in text for condition in case.required_conditions),
                    "metadata": result["meta"], "latency_ms": (time.perf_counter() - began) * 1000})
            except Exception as exc:
                rows.append({"case_id": case.case_id, "cache": temperature, "error": type(exc).__name__,
                    "ranked_chunks": [], **ranking_metrics(case.relevant, []),
                    "required_evidence_text_present": False, "latency_ms": (time.perf_counter() - began) * 1000})
    summaries = []
    for temperature in ("cold", "hot"):
        group = [row for row in rows if row["cache"] == temperature]
        summaries.append({"cache": temperature, "count": len(group), "failed": sum(row["error"] is not None for row in group),
            **{key: mean(row[key] for row in group) if group else None for key in
               ("recall_at_20", "ndcg_at_5", "mrr", "evidence_hit_at_20", "required_evidence_text_present")}})
    return {"mode": "bm25", "evidence_k": evidence_k, "rows": rows, "summaries": summaries,
            "condition_coverage": None, "claim_support": None,
            "limit": "required_evidence_text_present measures source text availability in top-k only; no generated answer or semantic condition coverage"}


async def review_candidates(dataset, mapping: dict, knowledge) -> dict:
    from app.agent.citation_guard import citation_issues
    from app.agent.nodes.response_node import ResponseNode, ResponseServices, validate_candidate
    from app.agent.request_understanding import EvidenceBundle
    from app.agent.state import AgentState
    from app.agent.validation import ResponseValidator
    from app.agent.validation_llm import LLMRAGJudge
    validator = ResponseValidator()
    judge = LLMRAGJudge(None)  # 无供应商实例，实际生产unknown路径；绝不读取预期标签返回判决。

    def forbidden_service():
        raise RuntimeError("generation_memory_or_cache_not_allowed_in_review")

    node = ResponseNode(ResponseServices(forbidden_service, lambda: validator, lambda: judge,
                                        forbidden_service, lambda: knowledge, forbidden_service))
    rows = []
    for case in dataset.cases:
        if case.kind not in {"review_negative", "review_positive"}:
            continue
        row = {"case_id": case.case_id, "kind": case.kind, "p0": case.p0,
               "candidate_sha256": sha256((case.candidate or "").encode()),
               "error": None, "deterministic_accepted": False, "gate_accepted_without_judge": False}
        try:
            if not case.candidate:
                raise ValueError("missing_candidate")
            evidence = [mapping[ref.key()] for ref in case.relevant]
            validation = validator.validate(case.candidate, case.query, "general", case.tool_results)
            citations = citation_issues(case.candidate, evidence)
            row["deterministic_accepted"] = validation.passed and not citations
            row["issue_codes"] = sorted({i["type"] for i in validation.issues} | set(citations))
            state = AgentState(session_id="m5-offline-review", user_id=101, user_message=case.query,
                               reply=case.candidate, evidence_bundle=EvidenceBundle(items=evidence))
            state.set_flow(rag_grounded=bool(evidence))
            for tool in case.tool_results:
                state.add_tool_call("synthetic_receipt", {}, tool, tool.get("success") is True)
            row["gate_accepted_without_judge"] = await validate_candidate(node, state)
            row["judge_support"] = state.collected_info.get("llm_judge", {}).get("support", "not_applicable")
            row["gate_issue_codes"] = sorted({i["type"] for i in state.collected_info["validation"]["issues"]})
        except Exception as exc:
            row["error"] = type(exc).__name__
        rows.append(row)
    negative = [r for r in rows if r["kind"] == "review_negative"]
    positive = [r for r in rows if r["kind"] == "review_positive"]
    return {"rows": rows, "candidate_count": len(rows), "failed": sum(r["error"] is not None for r in rows),
        "deterministic_false_accept": mean(bool(r["error"]) or r["deterministic_accepted"] for r in negative) if negative else None,
        "deterministic_false_reject": mean(bool(r["error"]) or not r["deterministic_accepted"] for r in positive) if positive else None,
        "p0_deterministic_false_accept_ids": [r["case_id"] for r in negative if r["p0"] and (r["error"] or r["deterministic_accepted"])],
        "unknown_judge_policy_accepted_ids": [r["case_id"] for r in rows if r.get("judge_support") == "unknown" and r["gate_accepted_without_judge"]],
        "whole_m5_pass": False, "judge_quality": "NOT_RUN_NO_MODEL",
        "limit": "deterministic-only error rates diagnose component gaps; unavailable Judge rejection is an availability fallback, not semantic quality success; no execution of business tools"}


def worker(output: Path) -> int:
    # 子进程已处于空临时目录，先清除所有业务配置再导入应用。
    tree = ast.parse((ROOT / "app/config/config.py").read_text(encoding="utf-8-sig"))
    for cls in tree.body:
        if isinstance(cls, ast.ClassDef) and cls.name == "Settings":
            for field in cls.body:
                if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
                    os.environ.pop(field.target.id, None)
    os.environ.update(DATABASE_URL_OVERRIDE="sqlite+aiosqlite:///isolated.db", REDIS_ENABLED="false",
        USE_MILVUS="false", LANGFUSE_ENABLED="false", USER_MEMORY_ENABLED="false", LLM_API_KEY="",
        LLM_API_BASE="http://offline.invalid/v1", RAG_USE_RERANKER="false", AUTH_DEMO_ACCOUNTS_ENABLED="false")
    blocked = []
    local = threading.local()
    original_pair = __import__("socket").socketpair

    def socketpair(*args, **kwargs):
        local.pair = True
        try:
            return original_pair(*args, **kwargs)
        finally:
            local.pair = False

    def audit(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"} and not getattr(local, "pair", False):
            blocked.append(event)
            raise RuntimeError("offline_network_forbidden")

    __import__("socket").socketpair = socketpair
    sys.addaudithook(audit)
    report = {"status": "INCOMPLETE", "layer": "T0_PRODUCTION_COMPONENTS_SYNTHETIC_SQL",
        "whole_m5_pass": False, "external_model_calls": 0, "generated_answers": 0,
        "business_changed": False, "performance_gate": "DEFERRED_BY_USER_NOT_PASSED"}
    repo = None
    inputs = {}
    try:
        dataset, corpus, inputs = load_package(ROOT / "acceptance/m5/adjudicated-v2", ROOT / "acceptance/m5/development.json")
        from app.models.models import Base
        from app.repositories.knowledge_repository import KnowledgeRepository
        from app.services.knowledge_base import KnowledgeBaseService
        from app.config.config import settings
        repo = KnowledgeRepository("sqlite:///knowledge.db")
        Base.metadata.create_all(repo.engine)
        embedding = IndexOnlyEmbedding()
        kb = KnowledgeBaseService(repository=repo, embedding=embedding)
        mapping = populate(kb, corpus)
        report["retrieval"] = retrieval_baseline(kb, dataset, mapping, evidence_k=settings.RAG_TOP_K)
        report["review"] = asyncio.run(review_candidates(dataset, mapping, kb))
        report["canonical_to_runtime_labels"] = [{"canonical": list(key), "runtime": [value["id"], value["version_id"]]} for key, value in mapping.items()]
        report["placeholder_indexed_texts"] = embedding.indexed_texts
        report["query_embedding_calls"] = embedding.query_calls
        failed = (any(s["failed"] for s in report["retrieval"]["summaries"]) or report["review"]["failed"]
                  or bool(report["review"]["unknown_judge_policy_accepted_ids"]) or embedding.query_calls)
        report["status"] = "FAIL_DIAGNOSTIC_EXECUTION" if failed else "DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE"
    except Exception as exc:
        report.update(status="FAIL_DIAGNOSTIC_EXECUTION", error_type=type(exc).__name__)
    finally:
        if repo is not None:
            repo.engine.dispose()
        report["blocked_network_operations"] = len(blocked)
        report["input_hashes"] = {Path(path).relative_to(ROOT).as_posix(): value for path, value in inputs.items()}
        report["input_materials_stable"] = bool(inputs) and all(Path(path).is_file() and sha256(Path(path).read_bytes()) == value for path, value in inputs.items())
        if blocked or not report["input_materials_stable"]:
            report["status"] = "FAIL_NETWORK_OR_MATERIAL_GUARD"
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report["status"] == "DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_output:
        return worker(args.worker_output)
    from scripts.test_provenance import snapshot_sources, compare_sources
    output = ROOT / "reports/m5" / datetime.now().strftime("offline-chain-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True)
    before = snapshot_sources(ROOT)
    worker_code = None
    failure = None
    try:
        with tempfile.TemporaryDirectory(prefix="aicshub-m5-offline-chain-") as temporary:
            with (output / "worker.log").open("w", encoding="utf-8") as log:
                result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker-output", str(output / "report.json")],
                                        cwd=temporary, stdout=log, stderr=subprocess.STDOUT, timeout=240)
                worker_code = result.returncode
    except Exception as exc:
        failure = type(exc).__name__
    after = snapshot_sources(ROOT)
    report = (json.loads((output / "report.json").read_bytes()) if (output / "report.json").is_file()
              else {"status": "FAIL_WORKER_NO_REPORT", "whole_m5_pass": False})
    report.update(compare_sources(before, after), worker_exit_code=worker_code)
    if failure or worker_code != 0:
        report.update(status="FAIL_WORKER", error_type=failure or "WorkerNonzeroExit")
    if not report["source_tree_stable"] or not report["source_snapshot_complete"]:
        report["status"] = "FAIL_SOURCE_DRIFT"
    for name, data in (("source-start", before), ("source-end", after), ("report", report)):
        (output / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(output / "report.json")}))
    return 0 if report["status"] == "DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
