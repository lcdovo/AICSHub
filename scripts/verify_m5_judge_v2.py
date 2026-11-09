"""禁网络Judge v2契约验收与历史v1回放；不创建模型客户端、不消耗供应商额度。"""
from __future__ import annotations

import argparse
import ast
import asyncio
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def hashes(paths: list[Path]) -> dict:
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


async def verify(output: Path) -> dict:
    from app.agent.judge_contracts import assess_v2
    from app.agent.nodes.response_node import ResponseNode, ResponseServices, validate_candidate
    from app.agent.request_understanding import EvidenceBundle
    from app.agent.state import AgentState
    from app.agent.validation import ResponseValidator
    from app.config.config import settings
    from tests.test_m5_judge_scope_cases import CONFIRMED_IDS, scope_fixture

    captured = ROOT / "reports/m5-live/current"
    paths = [ROOT / "reports/m4-closeout/live/authorization-ledger.json", ROOT / "reports/m5-live/authorization.json",
        ROOT / "reports/m5-live/dispatch.sqlite", captured / "report.json", captured / "postfix-offline-replay.json",
        ROOT / "docs/M5-LIVE-PLAN-v1.json", ROOT / "acceptance/m5/adjudicated-v2/dataset.json",
        ROOT / "acceptance/m5/adjudicated-v2/corpus.json"]
    before = hashes(paths)
    live = json.loads((captured / "report.json").read_bytes())
    expected = {row["case_id"]: row["replay_accepted"] for row in json.loads((captured / "postfix-offline-replay.json").read_bytes())["rows"]}
    dataset = json.loads((ROOT / "acceptance/m5/adjudicated-v2/dataset.json").read_bytes())
    by_case = {case["case_id"]: case for case in dataset["cases"]}
    corpus = json.loads((ROOT / "acceptance/m5/adjudicated-v2/corpus.json").read_bytes())
    by_doc = {(doc["chunk_id"], doc["version_id"]): doc for doc in corpus}
    labels = json.loads((captured / "runtime-labels.json").read_bytes())["labels"]
    evidence = {tuple(row["canonical"]): {"id": row["runtime_id"], "version_id": row["runtime_version"],
        "content": by_doc[tuple(row["canonical"])]["content"], "title": by_doc[tuple(row["canonical"])]["title"]} for row in labels}
    allowed = {(item["id"], item["version_id"]) for item in evidence.values()}
    class Snapshot:
        def evidence_is_current(self, items):
            return all((item["id"], item["version_id"]) in allowed for item in items)
    def forbidden():
        raise RuntimeError("verification_cannot_call_model_cache_or_memory")
    settings.LLM_JUDGE_CONTRACT_VERSION = "v1"
    legacy = []
    for row in live["rows"]:
        case = by_case[row["case_id"]]
        items = [evidence[(ref["chunk_id"], ref["version_id"])] for ref in row["evidence_chunks"]]
        state = AgentState(user_id=990005, session_id="m5-live-synthetic", user_message=case["query"],
                           evidence_bundle=EvidenceBundle(items=items))
        state.set_flow(rag_grounded=bool(items))
        state.reply = row.get("generation", {}).get("reply", "") if case["kind"] in {"answerable", "unanswerable"} else case["candidate"]
        for tool in case.get("tool_results", []):
            state.add_tool_call("synthetic_receipt", {}, tool, tool.get("success") is True)
        class RecordedJudge:
            async def judge(self, **kwargs):
                return row["judgement"]
        node = ResponseNode(ResponseServices(forbidden, ResponseValidator, RecordedJudge, forbidden, Snapshot, forbidden))
        accepted = bool(state.reply and await validate_candidate(node, state))
        legacy.append({"case_id": row["case_id"], "accepted": accepted, "previous_offline_accepted": expected[row["case_id"]]})
    v2_cases = []
    for key in CONFIRMED_IDS:
        candidate, original, raw, facts = scope_fixture(key)
        flagged = assess_v2(raw, candidate, facts)
        positive = deepcopy(raw)
        positive["claims"][0].update(text=original, issues=[])
        missed = deepcopy(raw)
        missed["claims"][0]["issues"] = []
        v2_cases.append({"case_id": key, "verdict_source": "hand_authored_contract_fixture_not_model",
            "explicit_scope_issue_rejected": not flagged["publish_allowed"],
            "original_counterpart_allowed": assess_v2(positive, original, facts)["publish_allowed"],
            "unflagged_semantic_mismatch_still_allowed": assess_v2(missed, candidate, facts)["publish_allowed"]})
    after = hashes(paths)
    mismatch = [row["case_id"] for row in legacy if row["accepted"] != row["previous_offline_accepted"]]
    success = (len(legacy) == 616 and not mismatch and all(row["explicit_scope_issue_rejected"]
               and row["original_counterpart_allowed"] for row in v2_cases) and before == after)
    return {"status": "PASS_OFFLINE_CONTRACT_ONLY_SEMANTIC_DETECTION_UNVERIFIED" if success else "FAIL_OFFLINE_CONTRACT",
        "whole_m5_pass": False, "new_provider_calls": 0, "legacy_replayed": len(legacy),
        "legacy_changed_case_ids": mismatch, "legacy_rows": legacy, "v2_known_cases": v2_cases,
        "v2_real_model_status": "NOT_RUN_BUDGET_EXHAUSTED", "business_judge_version": "v1",
        "protected_hashes_before": before, "protected_hashes_after": after, "protected_materials_stable": before == after,
        "limits": ["captured legacy verdicts are not new v2 model outputs",
                   "hand-authored scope labels only verify server contract enforcement",
                   "same evidence quote can still accompany wrong semantic reasoning if model misses issue",
                   "recorded evidence snapshot adapter, not new SQL publication or full deployment acceptance"]}


def worker(output: Path) -> int:
    tree = ast.parse((ROOT / "app/config/config.py").read_text(encoding="utf-8-sig"))
    for cls in tree.body:
        if isinstance(cls, ast.ClassDef) and cls.name == "Settings":
            for field in cls.body:
                if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
                    os.environ.pop(field.target.id, None)
    os.environ.update(LLM_API_KEY="", LLM_API_BASE="http://offline.invalid/v1", REDIS_ENABLED="false",
        USE_MILVUS="false", LANGFUSE_ENABLED="false", USER_MEMORY_ENABLED="false", LLM_JUDGE_CONTRACT_VERSION="v1")
    blocked = []
    local = threading.local()
    original = socket.socketpair
    def pair(*args, **kwargs):
        local.pair = True
        try:
            return original(*args, **kwargs)
        finally:
            local.pair = False
    socket.socketpair = pair
    def audit(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"} and not getattr(local, "pair", False):
            blocked.append(event)
            raise RuntimeError("offline_judge_verification_network_forbidden")
    sys.addaudithook(audit)
    try:
        result = asyncio.run(verify(output))
    except Exception as exc:
        result = {"status": "FAIL_OFFLINE_CONTRACT", "error_type": type(exc).__name__, "whole_m5_pass": False}
    result["blocked_network_operations"] = len(blocked)
    if blocked:
        result["status"] = "FAIL_NETWORK_GUARD"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result["status"].startswith("PASS_") else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_output:
        return worker(args.worker_output)
    from scripts.test_provenance import snapshot_sources, compare_sources
    output = ROOT / "reports/m5" / datetime.now().strftime("judge-v2-contract-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True)
    before = snapshot_sources(ROOT)
    with tempfile.TemporaryDirectory(prefix="aicshub-judge-v2-") as temporary:
        with (output / "worker.log").open("w", encoding="utf-8") as log:
            process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker-output", str(output / "report.json")],
                                     cwd=temporary, stdout=log, stderr=subprocess.STDOUT, timeout=240)
    result = (json.loads((output / "report.json").read_bytes()) if (output / "report.json").exists()
              else {"status": "FAIL_NO_WORKER_REPORT", "whole_m5_pass": False})
    after = snapshot_sources(ROOT)
    result.update(compare_sources(before, after), worker_exit_code=process.returncode)
    if process.returncode or not result["source_tree_stable"] or not result["source_snapshot_complete"]:
        result["status"] = "FAIL_SOURCE_OR_WORKER"
    for name, value in (("report", result), ("source-start", before), ("source-end", after)):
        (output / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "report": str(output / "report.json")}))
    return 0 if result["status"].startswith("PASS_") else 2


if __name__ == "__main__":
    raise SystemExit(main())
