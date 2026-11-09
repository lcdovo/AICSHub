"""Frozen business cases, strict receipt scoring and offline SDK/budget rehearsal."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATA = ROOT / "acceptance/business-v1"
OUT = ROOT / "reports/business-acceptance-v1"
AXES = ("statement_supported", "conditions_and_exceptions_preserved", "authorization_safe", "requirements_met")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_cases():
    development = json.loads((DATA / "development.json").read_text(encoding="utf-8"))["cases"]
    holdout = json.loads((DATA / "final-holdout.json").read_text(encoding="utf-8"))["cases"]
    sources = json.loads((DATA / "sources.json").read_text(encoding="utf-8"))
    cases = development + holdout
    if len(development) != 14 or len(holdout) != 6 or len({c["case_id"] for c in cases}) != 20:
        raise ValueError("case_catalog_invalid")
    catalog = {x["id"]: x for x in sources["items"]}
    for item in catalog.values():
        if hashlib.sha256(item["content"].encode()).hexdigest() != item["sha256"]:
            raise ValueError("source_content_changed")
    for case in cases:
        if not case["turns"] or not set(case["source_ids"]).issubset(catalog):
            raise ValueError("case_source_or_turn_missing")
        for turn in case["turns"]:
            if not turn["message"] or not turn["expected_items"] or not turn["expected_task_states"]:
                raise ValueError("turn_expectation_missing")
            if turn["max_simulated_applications"] and case["case_id"] != "BA-10":
                raise ValueError("unapproved_side_effect_case")
    if sum(len(c["turns"]) for c in cases) != 28:
        raise ValueError("fixed_turn_count_changed")
    return cases, sources


def score_turn(case, turn, receipt, *, rehearsal=False, sources=None):
    """Consume separately reviewed support; never infer semantics from matching keywords."""
    issues = []
    if not isinstance(receipt, dict) or not isinstance(receipt.get("reply"), str):
        return {"passed":False,"issues":["receipt_invalid"],"all_items_completed":False}
    review = receipt.get("text_review") or {}
    if not isinstance(review, dict):
        review = {}
    text = receipt["reply"]
    if receipt.get("transport_ok") is not True:
        issues.append("transport_failed")
    injected = case.get("fault") in {"judge_invalid_json", "publication_epoch_change", "retrieval_unavailable"}
    if receipt.get("protocol_valid") is not True and not (injected and receipt.get("expected_fault_handled") is True):
        issues.append("unexpected_protocol_failure")
    if receipt.get("usage_unknown") is not False:
        issues.append("unknown_dispatch_usage")
    if review.get("reply_sha256") != hashlib.sha256(text.encode()).hexdigest():
        issues.append("text_review_not_bound")
    origins = {"synthetic_fixture"} if rehearsal else {"separate_assistant_review", "human_review"}
    if review.get("origin") not in origins:
        issues.append("separate_text_review_missing")
    if not all(review.get(key) is True for key in AXES):
        issues.append("statement_condition_or_permission_review_failed")
    expected = {f"g{i}": entry for i,entry in enumerate(turn["expected_items"], 1)}
    actual = receipt.get("item_outcomes", {})
    if not isinstance(actual, dict):
        actual = {}
    if set(actual) != set(expected) or review.get("item_alignment_verified") is not True:
        issues.append("item_coverage_or_alignment_failed")
    for key, item in expected.items():
        if actual.get(key) not in item["allowed_statuses"]:
            issues.append(key + ":wrong_item_stage")
    if receipt.get("task_status") not in turn["expected_task_states"]:
        issues.append("wrong_task_stage")
    if type(receipt.get("simulated_applications")) is not int or receipt["simulated_applications"] != turn["max_simulated_applications"]:
        issues.append("wrong_side_effect_count")
    if type(receipt.get("payment_transactions")) is not int or receipt["payment_transactions"] != 0:
        issues.append("real_payment_forbidden")
    if case["same_request_replay"] and receipt.get("replay_same_run_and_no_dispatch") is not True:
        issues.append("replay_executed_again")
    if receipt.get("published_snapshot_matches") is not True:
        issues.append("knowledge_snapshot_changed")
    if sources is None:
        _, sources = load_cases()
    catalog = {row["id"]: row["sha256"] for row in sources["items"]}
    expected_hashes = {key:catalog[key] for key in case["source_ids"]}
    if receipt.get("source_hashes") != expected_hashes or receipt.get("snapshot_epoch") != sources["epoch"]:
        issues.append("source_identity_or_epoch_mismatch")
    if injected and receipt.get("expected_fault_handled") is not True:
        issues.append("fault_not_safely_handled")
    return {"passed": not issues, "issues": issues,
            "all_items_completed": bool(actual) and all(v == "answered" for v in actual.values()),
            "scope": "fixture_contract_only" if rehearsal else "frozen_synthetic_business_acceptance_not_human_gold"}


def operations(cases):
    return {f"{c['case_id']}/{t['turn_id']}/http-{n}": 2048 for c in cases for t in c["turns"] for n in range(1,7)}


def protected_ledgers():
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (ROOT / "reports").rglob("dispatch.sqlite") if not p.is_relative_to(OUT)}


def runtime_stamp():
    records = {}
    provider_code = "from app.config.config import settings as s; import json; print(json.dumps({k:getattr(s,k) for k in ['LLM_API_BASE','LLM_MODEL','LLM_MODEL_VERSION','LLM_THINKING_MODE','LLM_STRUCTURED_OUTPUT_METHOD','LLM_JUDGE_CONTRACT_VERSION','LLM_JUDGE_V2_OUTPUT_PROTOCOL']}))"
    code = "from pathlib import Path; import hashlib,json; print(json.dumps({p.as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('app').rglob('*.py')}))"
    for service in ("app", "worker"):
        cid = subprocess.check_output(["docker","compose","ps","-q",service],cwd=ROOT,encoding="utf-8").strip()
        image = subprocess.check_output(["docker","inspect","--format","{{.Image}}",cid],cwd=ROOT,encoding="utf-8").strip()
        hashes = json.loads(subprocess.check_output(["docker","exec",cid,"python","-c",code],cwd=ROOT,encoding="utf-8"))
        provider = json.loads(subprocess.check_output(["docker","exec",cid,"python","-c",provider_code],cwd=ROOT,encoding="utf-8"))
        records[service] = {"image": image, "app_hashes": hashes,"provider":provider}
    if records["app"] != records["worker"]:
        raise ValueError("deployed_api_worker_mismatch")
    return records["app"]


def prepared_plan():
    from scripts.test_provenance import snapshot_sources
    cases, _ = load_cases()
    plan = {"version":"business-acceptance.plan.v1", "revision":"api-adapter-preflight-v2", "status":"PREPARED_NOT_AUTHORIZED",
        "case_count":20, "turn_count":28, "development_cases":14, "final_cases":6,
        "max_requests":168, "max_input_bytes_reserved":168*24576, "max_output_tokens":168*2048,
        "per_request_bytes":24576, "per_request_output_tokens":2048, "per_turn_http":6,
        "stop_on_first_failure":True, "unknown_is_failure":True, "automatic_retry":False,
        "json_repair":False, "old_accounts_reused":False,
        "inputs":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in DATA.glob("*.json")},
        "source_fingerprint":snapshot_sources(ROOT)["fingerprint"], "runtime":runtime_stamp(),
        "protected_ledgers":protected_ledgers(),
        "operations_sha256":digest(operations(cases)),
        "label_origin":"assistant_authored_synthetic_not_independent_human_gold",
        "live_scope":"isolated_synthetic_SQL_REST_WS_with_frozen_published_evidence_and_real_provider",
        "post_run_generated_text_review_required":True,
        "final_phase_requires_all_development_cases_and_text_reviews_passed":True}
    plan["prepared_id"] = digest(plan)
    return plan


def validate_plan(plan, *, check_runtime=True):
    from scripts.test_provenance import snapshot_sources
    unsigned = {k:v for k,v in plan.items() if k != "prepared_id"}
    if digest(unsigned) != plan.get("prepared_id"):
        raise ValueError("plan_modified")
    if snapshot_sources(ROOT)["fingerprint"] != plan["source_fingerprint"]:
        raise ValueError("prepared_source_changed")
    if {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in DATA.glob("*.json")} != plan["inputs"]:
        raise ValueError("prepared_cases_or_sources_changed")
    if protected_ledgers() != plan["protected_ledgers"]:
        raise ValueError("old_ledger_changed")
    if check_runtime and runtime_stamp() != plan["runtime"]:
        raise ValueError("deployed_runtime_changed")


def require_authorization(plan, authorization):
    if (authorization.get("confirmed") is not True or authorization.get("prepared_id") != plan["prepared_id"]
            or any(authorization.get(k) != plan[k] for k in ("max_requests","max_input_bytes_reserved","max_output_tokens"))
            or authorization.get("approved_by") != "user" or not authorization.get("user_statement")):
        raise ValueError("explicit_fixed_budget_authorization_required_before_live_dispatch")


async def rehearse(plan):
    import httpx
    from openai import AsyncOpenAI
    from scripts.m5_live_budget import DispatchLedger, OPERATION, guarded_transport
    validate_plan(plan)
    cases, sources = load_cases()
    source_hashes = {row["id"]:row["sha256"] for row in sources["items"]}
    directory = OUT / "rehearsals" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    directory.mkdir(parents=True)
    ledger = DispatchLedger(directory / "dispatch.sqlite", plan["prepared_id"],
        {"max_requests":plan["max_requests"],"max_input_tokens":plan["max_input_bytes_reserved"],
         "max_output_tokens":plan["max_output_tokens"]}, operations(cases))
    def response(request):
        return httpx.Response(200,json={"id":"synthetic","object":"chat.completion","created":1,"model":"synthetic-business",
            "choices":[{"index":0,"finish_reason":"stop","message":{"role":"assistant","content":"离线执行器回执，不是实际客服答复。"}}],
            "usage":{"prompt_tokens":100,"completion_tokens":20,"total_tokens":120}},request=request)
    transport = guarded_transport(ledger,model="synthetic-business",endpoint="https://offline.invalid/v1",underlying=httpx.MockTransport(response))
    rows = []
    async with httpx.AsyncClient(transport=transport) as http:
        sdk = AsyncOpenAI(api_key="synthetic-only",base_url="https://offline.invalid/v1",http_client=http,max_retries=0)
        for case in cases:
            for turn in case["turns"]:
                token = OPERATION.set(f"{case['case_id']}/{turn['turn_id']}/http-1")
                try:
                    result = await sdk.chat.completions.create(model="synthetic-business",messages=[{"role":"user","content":turn["message"]}],max_tokens=2048)
                finally:
                    OPERATION.reset(token)
                text = result.choices[0].message.content
                receipt = {"source_hashes":{k:source_hashes[k] for k in case["source_ids"]},"snapshot_epoch":sources["epoch"],"reply":text,"transport_ok":True,"protocol_valid":True,"expected_fault_handled":True,
                    "usage_unknown":False,"task_status":turn["expected_task_states"][0],
                    "item_outcomes":{f"g{i}":item["allowed_statuses"][0] for i,item in enumerate(turn["expected_items"],1)},
                    "simulated_applications":turn["max_simulated_applications"],"payment_transactions":0,
                    "replay_same_run_and_no_dispatch":True,"published_snapshot_matches":True,
                    "text_review":{"origin":"synthetic_fixture","reply_sha256":hashlib.sha256(text.encode()).hexdigest(),
                                   "item_alignment_verified":True, **{key:True for key in AXES}}}
                rows.append({"case_id":case["case_id"],"turn_id":turn["turn_id"],"score":score_turn(case,turn,receipt,rehearsal=True)})
    validate_plan(plan)
    report={"passed":len(rows)==28 and all(x["score"]["passed"] for x in rows),"case_count":20,"turn_count":len(rows),
        "sdk_synthetic_http":ledger.snapshot()["requests"],"paid_http":0,"rows":rows,
        "semantic_quality":"NOT_RUN","actual_REST_WS_graph":"NOT_RUN","side_effect_execution":"NOT_RUN",
        "live_admission_allowed":False,"scope":"SDK_LEDGER_SCORER_FIXTURE_REHEARSAL_ONLY","budget":ledger.snapshot()}
    (directory / "report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"rehearsal":str(directory),"passed":report["passed"],"synthetic_http":len(rows),"paid_http":0},ensure_ascii=False))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("prepare","rehearse"))
    args=parser.parse_args()
    OUT.mkdir(exist_ok=True,parents=True)
    if args.action=="prepare":
        plan=prepared_plan()
        path=OUT/"plan.json"
        if path.exists():raise ValueError("existing_plan_preserved_choose_explicit_new_revision")
        path.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding="utf-8")
        approval={"confirmed":False,"prepared_id":plan["prepared_id"],"approved_by":None,"user_statement":None,
                  **{k:plan[k] for k in ("max_requests","max_input_bytes_reserved","max_output_tokens")}}
        (OUT/"authorization-request.json").write_text(json.dumps(approval,indent=2),encoding="utf-8")
        print(json.dumps({"prepared_id":plan["prepared_id"],"cases":20,"turns":28,"proposed_http":168,"authorized":False}))
    else:
        asyncio.run(rehearse(json.loads((OUT/"plan.json").read_text(encoding="utf-8"))))


if __name__=="__main__":main()
