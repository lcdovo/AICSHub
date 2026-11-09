"""Fixed atomic-policy recheck; preserve old accounts and two reserved calls."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts import run_item_v2_acceptance as original
from scripts.evaluate_multi_intent import isolated_configuration,provider_stamp,save
from scripts.m5_live_budget import DispatchLedger
from scripts.run_multi_intent_live import TwoAccountLedger
from scripts.test_provenance import snapshot_sources

PREFIX='atomic-v1/'
GRAPHS={'MI-01':4,'MI-04':5,'MI-06':5,'MI-16':6}
REGRESSIONS=ROOT/'acceptance/multi-intent/v2-generation-regressions-v1.json'
BASE_DATASET=original.dataset


def dataset():
    data=BASE_DATASET()
    return {**data,'cases':[*data['cases'],*json.loads(REGRESSIONS.read_bytes())['cases']]}


def scope_dataset():
    data=dataset()
    return {**data,'cases':[*data['cases'],*json.loads((ROOT/'acceptance/multi-intent/v2-scope-regressions-v1.json').read_bytes())['cases']]}


class ReservedLedger:
    """A stricter runtime cap; never rewrites immutable account authorization."""
    def __init__(self,ledger,keep):
        if type(keep) is not int or not 0<=keep<ledger.limits['max_requests']:
            raise ValueError('invalid_reserved_calls')
        self.inner,self.keep=ledger,keep

    @property
    def limits(self): return {**self.inner.limits,'max_requests':self.inner.limits['max_requests']-self.keep}
    def dispatched(self): return self.inner.dispatched()
    def snapshot(self): return {**self.inner.snapshot(),'limits':self.limits,'reserved_not_usable':self.keep}
    def reserve(self,operation,body):
        if self.inner.snapshot()['requests']>=self.limits['max_requests']:
            raise ValueError('reserved_calls_not_authorized_for_dispatch')
        self.inner.reserve(operation,body)
    def settle(self,operation,response): self.inner.settle(operation,response)


def operations(data,*args,**kwargs):
    prefix=args[0] if args else kwargs.get('prefix',PREFIX)
    if prefix not in {PREFIX,'atomic-v2/','clause-v1/','scope-v1/','selectors-v1/','nonfact-v1/','extract-v1/'}: raise ValueError('unknown_atomic_revision')
    result={f'{prefix}{case["case_id"]}/{stage}':2048 for case in data['cases'] for stage in ('good','bad')}
    result.update({f'{prefix}E2E-{case}/http-{index}':2048 for case,cap in GRAPHS.items() for index in range(1,cap+1)})
    result.update({f'{prefix}DEPLOY-MIXED/http-{index}':2048 for index in range(1,5)})
    return result


def old_rows(path,prefix=PREFIX):
    with sqlite3.connect(path) as db:
        return db.execute('SELECT * FROM dispatches WHERE operation NOT LIKE ? ORDER BY operation',(prefix+'%',)).fetchall()


def validate(path,authorization=None,*,rehearse=False):
    plan=json.loads(Path(path).read_bytes())
    unsigned={k:v for k,v in plan.items() if k!='prepared_id'}
    if hashlib.sha256(json.dumps(unsigned,sort_keys=True).encode()).hexdigest()!=plan['prepared_id']:
        raise ValueError('frozen_plan_changed')
    if snapshot_sources(ROOT)['fingerprint']!=plan['source_fingerprint']:
        raise ValueError('frozen_source_changed')
    resumed=plan.get('resume_existing_only') is True
    cap_ok=(plan['max_planned_new_requests']==68 and plan['operation_prefix']==PREFIX and not resumed) or (
        resumed and plan['operation_prefix']=='atomic-v2/' and plan['max_planned_new_requests']==15
        and plan.get('previous_revision_dispatches')==52 and len(plan['existing_accounts'])==3) or (
        resumed and plan['operation_prefix']=='clause-v1/' and plan['max_planned_new_requests']==10
        and plan.get('previous_revision_dispatches')==61 and len(plan['existing_accounts'])==4
        and plan['existing_accounts'][-1]['limits']=={'max_requests':3,'max_input_tokens':3*24576,'max_output_tokens':3*2048}) or (
        resumed and plan['operation_prefix']=='scope-v1/' and plan['max_planned_new_requests']==72
        and plan.get('previous_revision_dispatches')==67 and len(plan['existing_accounts'])==5
        and plan['existing_accounts'][-1]['limits']=={'max_requests':68,'max_input_tokens':68*24576,'max_output_tokens':68*2048}) or (
        resumed and plan['operation_prefix']=='selectors-v1/' and plan['max_planned_new_requests']==72
        and plan.get('previous_revision_dispatches')==87 and len(plan['existing_accounts'])==6
        and plan['existing_accounts'][-1]['limits']=={'max_requests':20,'max_input_tokens':20*24576,'max_output_tokens':20*2048}) or (
        resumed and plan['operation_prefix']=='nonfact-v1/' and plan['max_planned_new_requests']==20
        and plan.get('previous_revision_dispatches')==139 and len(plan['existing_accounts'])==6
        and plan['existing_accounts'][-1]['limits']=={'max_requests':20,'max_input_tokens':20*24576,'max_output_tokens':20*2048}
        and plan['graph_http_limits']=={'MI-01':4,'MI-04':4,'MI-06':4,'MI-16':4}) or (
        resumed and plan['operation_prefix']=='extract-v1/' and plan['max_planned_new_requests']==14
        and plan.get('previous_revision_dispatches')==153 and len(plan['existing_accounts'])==7
        and plan['existing_accounts'][-1]['limits']=={'max_requests':8,'max_input_tokens':8*24576,'max_output_tokens':8*2048}
        and plan['graph_http_limits']=={'MI-01':3,'MI-04':2,'MI-06':2,'MI-16':3})
    if (plan['new_limits']!={'max_requests':60,'max_input_tokens':60*24576,'max_output_tokens':60*2048}
            or not cap_ok or (plan['graph_http_limits']!=GRAPHS and plan['operation_prefix'] not in {'nonfact-v1/','extract-v1/'}) or plan['reserved_calls']!=2):
        raise ValueError('fixed_authorization_scope_changed')
    if plan['provider']!=provider_stamp() and not rehearse: raise ValueError('provider_configuration_changed')
    for relative,digest in plan['inputs'].items():
        p=(ROOT/relative).resolve()
        if not p.is_relative_to(ROOT) or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
            raise ValueError('frozen_material_changed')
    for relative,digest in plan['protected_ledgers'].items():
        p=(ROOT/relative).resolve()
        if not p.is_relative_to(ROOT/'reports') or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
            raise ValueError('historical_ledger_changed')
    for account in plan['existing_accounts']:
        p=(ROOT/account['path']).resolve()
        if not p.is_relative_to(ROOT/'reports/multi-intent/live-budget'):
            raise ValueError('existing_account_outside_scope')
        if hashlib.sha256(json.dumps(old_rows(p,plan['operation_prefix']),sort_keys=True).encode()).hexdigest()!=account['previous_rows_sha256']:
            raise ValueError('prior_dispatch_rows_changed')
    if not rehearse:
        approved=json.loads(Path(authorization).read_bytes()) if authorization else {}
        if (approved.get('prepared_id')!=plan['prepared_id'] or approved.get('approved_by')!='user'
                or approved.get('confirmed') is not True or approved.get('limits')!=plan['new_limits']):
            raise ValueError('explicit_sixty_request_authorization_required')
        if plan['operation_prefix']=='clause-v1/' and (approved.get('three_request_supplement_confirmed') is not True
                or approved.get('three_request_supplement_limits')!=plan['existing_accounts'][-1]['limits']):
            raise ValueError('explicit_three_request_supplement_required')
        if plan['operation_prefix']=='scope-v1/' and (approved.get('scope_supplement_confirmed') is not True
                or approved.get('scope_supplement_limits')!=plan['existing_accounts'][-1]['limits']):
            raise ValueError('explicit_scope_revalidation_supplement_required')
        if plan['operation_prefix'] in {'selectors-v1/','nonfact-v1/'} and (approved.get('selector_supplement_confirmed') is not True
                or approved.get('selector_supplement_limits')!=plan['existing_accounts'][-1]['limits']):
            raise ValueError('explicit_selector_revalidation_supplement_required')
        if plan['operation_prefix']=='extract-v1/' and (approved.get('extract_supplement_confirmed') is not True
                or approved.get('extract_supplement_limits')!=plan['existing_accounts'][-1]['limits']):
            raise ValueError('explicit_extract_revalidation_supplement_required')
    return plan


def account_ledger(plan,operations_map,*,rehearse=False,path=None):
    if rehearse:
        return DispatchLedger(path,'offline-synthetic-only',{'max_requests':100,
            'max_input_tokens':100*24576,'max_output_tokens':100*2048},operations_map)
    accounts=[]
    for account in plan['existing_accounts']:
        p=ROOT/account['path']
        with sqlite3.connect(p) as db:
            preserved=[row[0] for row in db.execute('SELECT operation FROM dispatches')]
        allowed={**operations_map,**{key:2048 for key in preserved}}
        ledger=DispatchLedger(p,account['id'],account['limits'],allowed)
        accounts.append(ReservedLedger(ledger,account['keep']) if account['keep'] else ledger)
    if not plan.get('resume_existing_only'):
        new_path=ROOT/'reports/multi-intent/live-budget'/plan['phase_prepared_id']/'atomic-supplements'/plan['prepared_id']/'dispatch.sqlite'
        accounts.append(DispatchLedger(new_path,plan['prepared_id'],plan['new_limits'],operations_map))
    combined=accounts[0]
    for ledger in accounts[1:]: combined=TwoAccountLedger(combined,ledger)
    return combined


async def evaluate(plan,output,*,dry,plan_path,authorization):
    # The unified evaluator owns model calls, graph execution and pass criteria.
    # Only frozen preparation and ledger selection differ from the old plan.
    adapted={**plan,'max_http_requests':60,'max_input_bytes_reserved':60*24576,
        'max_output_tokens_reserved':60*2048,'end_to_end_cases':list(GRAPHS),'max_end_to_end_http_per_case':6,
        'judge_output_protocol':'json_object'}
    prefix=plan['operation_prefix']
    selected=scope_dataset if prefix in {'scope-v1/','selectors-v1/','nonfact-v1/','extract-v1/'} else dataset
    real_operations=operations(selected(),prefix)
    allowed=({**real_operations,**{f'{prefix}{case["case_id"]}/understanding':2048 for case in selected()['cases']}}
             if dry else real_operations)
    def ledger_factory(path,*args): return account_ledger(plan,allowed,rehearse=dry,path=path)
    def checked(*args,**kwargs): return {**validate(*args,**kwargs),**adapted}
    with patch.object(original,'dataset',selected),patch.object(original,'validate',checked),patch.object(original,'limits',lambda *a,**k:allowed),patch(
            'scripts.m5_live_budget.DispatchLedger',ledger_factory):
        report=await original.evaluate(adapted,output,dry=dry,plan_path=plan_path,authorization=authorization)
    report['captured_regressions']=[case['case_id'] for case in selected()['cases'] if case['case_id'].startswith(('V2G-','V2S-'))]
    save(output/'report.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared',type=Path,required=True)
    parser.add_argument('--authorization',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--rehearse',action='store_true')
    args=parser.parse_args()
    args.prepared=args.prepared.resolve();args.output=args.output.resolve()
    if args.authorization: args.authorization=args.authorization.resolve()
    if args.output.exists(): raise ValueError('preserve_prior_evidence')
    plan=validate(args.prepared,args.authorization,rehearse=args.rehearse)
    with isolated_configuration(live=not args.rehearse):
        report=asyncio.run(evaluate(plan,args.output,dry=args.rehearse,plan_path=args.prepared,authorization=args.authorization))
    print(json.dumps({'passed':report['passed'],'cases':report['cases'],'new_requests':report['new_dispatches'],
        'paid':report['paid_http_requests'],'graphs':len(report['end_to_end'])}))
    raise SystemExit(0 if report['passed'] else 1)


if __name__=='__main__': main()
