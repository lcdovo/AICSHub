"""Freeze a proposed v2 acceptance plan offline; never read credentials or dispatch HTTP."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts.evaluate_multi_intent import DATA,isolated_configuration,fixture_state,candidate,save,provider_stamp
from scripts.evaluate_item_judge_v2 import synthetic_verdict
from scripts.test_provenance import snapshot_sources

FRESH=ROOT/'acceptance/multi-intent/v2-fresh-cases-v1.json'


def offline_cases():
    from app.agent.intent_items import snapshot,verify_review
    from app.agent.validation_llm import assess_judgement
    from app.agent.answer_contracts import review_query,tool_evidence_text
    known=json.loads(DATA.read_bytes())['cases']
    fresh=json.loads(FRESH.read_bytes())['cases']
    results=[]
    for case in [*known,*fresh]:
        state=fixture_state(case)
        pair=[]
        for bad in (False,True):
            body,_=candidate(case,bad=bad)
            verdict=synthetic_verdict(case,bad=bad)
            result=assess_judgement(verdict,response=body,kb_context=state.current_evidence(),
                tool_evidence=tool_evidence_text(state),user_query=review_query(state),
                contract_version='v2',item_context=snapshot(state))
            _,issues=verify_review(state,body,result.get('item_checks'))
            pair.append({'bad':bad,'accepted':bool(result['publish_allowed'] and not issues),
                'valid_output':result.get('schema_version')==2 and isinstance(result.get('item_checks'),list)})
        results.append({'case_id':case['case_id'],'passed':pair[0]['accepted'] and not pair[1]['accepted']
            and all(item['valid_output'] for item in pair),'checks':pair})
    return results


def prepare(output:Path):
    if output.exists(): raise ValueError('preserve_existing_preparation')
    with isolated_configuration(): results=offline_cases()
    if not all(row['passed'] for row in results):
        save(output/'offline.json',results)
        raise ValueError('offline_contract_replay_failed')
    inputs={str(path.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(path.read_bytes()).hexdigest() for path in [DATA,FRESH]}
    protected={str(path.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (ROOT/'reports').rglob('dispatch.sqlite')}
    plan={'plan_id':'item-v2-final-acceptance-proposal-v1','status':'PROPOSED_NOT_AUTHORIZED',
        'provider':provider_stamp(),
        'judge':'v2','source_contract':'server-fragments.v2','source_fingerprint':snapshot_sources(ROOT)['fingerprint'],
        'inputs':inputs,'known_regression_cases':16,'fresh_synthetic_cases':4,
        'component_calls':{'understanding':20,'good_judge':20,'bad_judge':20,'total':60},
        'full_graph_cases':['MI-01','MI-04','MI-06','MI-16'],'full_graph_max_each':4,'full_graph_max_total':16,
        'deployed_synthetic_chat_max_http':4,'max_http_requests':80,
        'max_json_bytes_per_request':24576,'max_input_reserved_utf8_bytes':80*24576,
        'max_output_tokens_per_request':2048,'max_output_reserved_tokens':80*2048,
        'new_account_required':True,'old_accounts_used':122,'old_accounts_limit':122,
        'old_other_stage_nine_not_reused':True,'automatic_sdk_retries':False,
        'stop_on_first_component_or_graph_failure':True,'unknown_is_failure':True,
        'no_automatic_failed_operation_replay':True,'no_json_repair':True,
        'judge_deadline_seconds':10,'task_deadline_seconds':20,
        'gates':{'all_20_plans_item_coverage':True,'all_20_correct_candidates_accepted':True,
            'incorrect_candidates_accepted':0,'all_judge_bindings_valid':True,
            'all_four_graphs_pass':True,'generated_text_separate_review_pass':True},
        'deployment_gate':'only after offline/full-graph/semantic gates and separate generated-text review; smoke failure rollback',
        'background_worker_paid_jobs':'must prevent unbudgeted background model dispatch during acceptance/deployment',
        'labels':'assistant_curated_synthetic_not_independent_human_gold',
        'admission_scope':'local synthetic acceptance; no general semantic accuracy or production capacity claims',
        'protected_ledgers':protected}
    plan['prepared_id']=hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()
    save(output/'plan.json',plan)
    save(output/'offline.json',{'passed':True,'cases':results,'provider_requests':0,'quality':'NOT_MEASURED'})
    save(output/'source.json',snapshot_sources(ROOT))
    print(json.dumps({'prepared_id':plan['prepared_id'],'offline_cases':20,'provider_requests':0,'proposed_max':80}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();prepare(args.output.resolve())
