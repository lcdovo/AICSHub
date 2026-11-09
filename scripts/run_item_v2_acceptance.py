"""Execute the fixed newly authorized v2 local acceptance through the shared evaluator."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts import run_multi_intent_live as shared
from scripts.evaluate_multi_intent import DATA,isolated_configuration,provider_stamp,save
from scripts.prepare_item_v2_acceptance import FRESH
from scripts.test_provenance import snapshot_sources


def dataset():
    return {'review_origin':'assistant_curated_synthetic_not_independent_human_gold',
        'cases':[*json.loads(DATA.read_bytes())['cases'],*json.loads(FRESH.read_bytes())['cases']]}


def limits(data,*args,**kwargs):
    operations={f'{case["case_id"]}/{stage}':2048 for case in data['cases']
        for stage in ('understanding','good','bad')}
    operations.update({f'E2E-{case}/http-{index}':2048 for case in ['MI-01','MI-04','MI-06','MI-16'] for index in range(1,5)})
    operations.update({f'DEPLOY-MIXED/http-{index}':2048 for index in range(1,5)})
    if len(operations)!=80: raise ValueError('fixed_eighty_operations_required')
    prefix=args[0] if args else kwargs.get('prefix','')
    if prefix:
        if prefix not in {'json-object-v1/','safe-id-v1/','sentences-v1/','failed-read-v1/','operation-limit-v1/','read-only-v1/','information-limit-v1/','marker-v1/','layout-v1/'}: raise ValueError('unknown_revision_prefix')
        original=dict(operations)
        operations.update({'json-object-v1/'+key:value for key,value in original.items()})
        if prefix in {'sentences-v1/','failed-read-v1/','operation-limit-v1/','read-only-v1/','information-limit-v1/','marker-v1/','layout-v1/'}: operations.update({'safe-id-v1/'+key:value for key,value in original.items()})
        if prefix in {'failed-read-v1/','operation-limit-v1/','read-only-v1/','information-limit-v1/','marker-v1/','layout-v1/'}: operations.update({'sentences-v1/'+key:value for key,value in original.items()})
        if prefix in {'operation-limit-v1/','read-only-v1/','information-limit-v1/','marker-v1/','layout-v1/'}: operations.update({'failed-read-v1/'+key:value for key,value in original.items()})
        if prefix in {'read-only-v1/','information-limit-v1/','marker-v1/','layout-v1/'}: operations.update({'operation-limit-v1/'+key:value for key,value in original.items()})
        if prefix in {'information-limit-v1/','marker-v1/','layout-v1/'}: operations.update({'read-only-v1/'+key:value for key,value in original.items()})
        if prefix in {'marker-v1/','layout-v1/'}: operations.update({'information-limit-v1/'+key:value for key,value in original.items()})
        if prefix=='layout-v1/':
            operations.update({'marker-v1/'+key:value for key,value in original.items()})
            operations.update({f'marker-v1/E2E-{case}/http-{index}':2048 for case,limit in {'MI-04':5,'MI-06':5,'MI-16':6}.items() for index in range(1,limit+1)})
        operations.update({prefix+key:value for key,value in original.items()})
        if prefix in {'marker-v1/','layout-v1/'}:
            operations.update({f'{prefix}E2E-{case}/http-{index}':2048
                for case,limit in {'MI-04':5,'MI-06':5,'MI-16':6}.items() for index in range(1,limit+1)})
    return operations


def validate(path,authorization=None,*,rehearse=False):
    value=json.loads(Path(path).read_bytes())
    unsigned={key:item for key,item in value.items() if key!='prepared_id'}
    if hashlib.sha256(json.dumps(unsigned,sort_keys=True).encode()).hexdigest()!=value['prepared_id']:
        raise ValueError('frozen_plan_changed')
    if value['source_fingerprint']!=snapshot_sources(ROOT)['fingerprint']:
        raise ValueError('frozen_source_changed')
    expected_inputs={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in [DATA,FRESH]}
    if value['inputs']!=expected_inputs: raise ValueError('frozen_material_changed')
    if (value['max_http_requests']!=80 or value['max_input_reserved_utf8_bytes']!=80*24576
            or value['max_output_reserved_tokens']!=80*2048 or value['judge']!='v2'
            or value['source_contract'] not in {'server-fragments.v2','server-sentences.v1'} or value['known_regression_cases']!=16
            or value['fresh_synthetic_cases']!=4 or value['full_graph_max_each']!=4
            or value['full_graph_cases']!=['MI-01','MI-04','MI-06','MI-16']):
        raise ValueError('fixed_authorization_scope_changed')
    for relative,digest in value['protected_ledgers'].items():
        target=(ROOT/relative).resolve()
        if not target.is_relative_to((ROOT/'reports').resolve()) or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
            raise ValueError('historical_ledger_changed')
    if value.get('phase_prepared_id'):
        import sqlite3
        phase=value['phase_prepared_id']
        if len(phase)!=64 or any(c not in '0123456789abcdef' for c in phase): raise ValueError('phase_identity_invalid')
        ledger=ROOT/'reports/multi-intent/live-budget'/phase/'dispatch.sqlite'
        if value.get('operation_prefix') not in {'json-object-v1/','safe-id-v1/','sentences-v1/','failed-read-v1/','operation-limit-v1/','read-only-v1/','information-limit-v1/','marker-v1/','layout-v1/'} or value.get('judge_output_protocol')!='json_object':
            raise ValueError('fixed_revision_protocol_invalid')
        with sqlite3.connect(ledger) as db:
            previous=db.execute('SELECT * FROM dispatches WHERE operation NOT LIKE ? ORDER BY operation',
                (value['operation_prefix']+'%',)).fetchall()
        count=80 if value['operation_prefix'] in {'information-limit-v1/','marker-v1/','layout-v1/'} else 50 if value['operation_prefix']=='read-only-v1/' else 45 if value['operation_prefix']=='operation-limit-v1/' else 31 if value['operation_prefix']=='failed-read-v1/' else 21 if value['operation_prefix']=='sentences-v1/' else 18 if value['operation_prefix']=='safe-id-v1/' else 3
        if len(previous)!=count or hashlib.sha256(json.dumps(previous,sort_keys=True).encode()).hexdigest()!=value['previous_rows_sha256']:
            raise ValueError('previous_dispatch_rows_changed')
        if value.get('carried_evidence'):
            artifact=(ROOT/value['carried_evidence']['path']).resolve()
            if (not artifact.is_relative_to((ROOT/'reports').resolve()) or hashlib.sha256(artifact.read_bytes()).hexdigest()!=
                    value['carried_evidence']['sha256']): raise ValueError('segmented_evidence_changed')
    if not rehearse:
        approved=json.loads(Path(authorization).read_bytes()) if authorization else {}
        if (approved.get('confirmed') is not True or approved.get('prepared_id')!=value['prepared_id']
                or approved.get('max_requests')!=80 or approved.get('max_input_tokens')!=80*24576
                or approved.get('max_output_tokens')!=80*2048 or approved.get('approved_by')!='user'):
            raise ValueError('new_eighty_request_authorization_required')
        if value['provider']!=provider_stamp(): raise ValueError('provider_configuration_changed')
        if value.get('phase_prepared_id') and (approved.get('same_phase_resume_confirmed') is not True
                or approved.get('phase_prepared_id')!=value['phase_prepared_id']):
            raise ValueError('explicit_same_phase_resume_required')
        if value.get('carried_evidence') and approved.get('segmented_resume_confirmed') is not True:
            raise ValueError('explicit_segmented_resume_required')
        if value.get('supplemental_limits'):
            expected={'max_requests':14,'max_input_tokens':14*24576,'max_output_tokens':14*2048}
            if (value['supplemental_limits']!=expected or approved.get('supplemental_limits')!=expected
                    or approved.get('supplemental_confirmed') is not True):
                raise ValueError('explicit_fourteen_request_supplement_required')
        if value.get('supplemental_account_id'):
            import sqlite3
            account=value['supplemental_account_id']
            if len(account)!=64 or any(c not in '0123456789abcdef' for c in account): raise ValueError('supplement_identity_invalid')
            target=ROOT/'reports/multi-intent/live-budget'/value['phase_prepared_id']/'supplements'/account/'dispatch.sqlite'
            with sqlite3.connect(target) as db:
                previous=db.execute('SELECT * FROM dispatches WHERE operation NOT LIKE ? ORDER BY operation',
                    (value['operation_prefix']+'%',)).fetchall()
            if value.get('supplemental_previous_rows_sha256'):
                if hashlib.sha256(json.dumps(previous,sort_keys=True).encode()).hexdigest()!=value['supplemental_previous_rows_sha256']:
                    raise ValueError('supplement_history_changed')
            elif previous: raise ValueError('supplement_history_changed')
        if value.get('extra_supplemental_limits'):
            expected={'max_requests':6,'max_input_tokens':6*24576,'max_output_tokens':6*2048}
            if (value['extra_supplemental_limits']!=expected or approved.get('extra_supplemental_limits')!=expected
                    or approved.get('extra_supplemental_confirmed') is not True):
                raise ValueError('explicit_six_request_supplement_required')
        if value.get('continuation_limits'):
            expected={'max_requests':9,'max_input_tokens':9*24576,'max_output_tokens':9*2048}
            if (value['continuation_limits']!=expected or approved.get('continuation_limits')!=expected
                    or approved.get('continuation_confirmed') is not True):
                raise ValueError('explicit_nine_request_supplement_required')
            import sqlite3
            account=value['extra_supplemental_account_id']
            if len(account)!=64 or any(c not in '0123456789abcdef' for c in account): raise ValueError('extra_account_identity_invalid')
            target=ROOT/'reports/multi-intent/live-budget'/value['phase_prepared_id']/'extra-supplements'/account/'dispatch.sqlite'
            with sqlite3.connect(target) as db:
                previous=db.execute('SELECT * FROM dispatches WHERE operation NOT LIKE ? ORDER BY operation',
                    (value['operation_prefix']+'%',)).fetchall()
            if value.get('extra_previous_rows_sha256'):
                if hashlib.sha256(json.dumps(previous,sort_keys=True).encode()).hexdigest()!=value['extra_previous_rows_sha256']:
                    raise ValueError('extra_account_history_changed')
            elif previous: raise ValueError('extra_account_history_changed')
        if value.get('final_supplemental_limits'):
            expected={'max_requests':9,'max_input_tokens':9*24576,'max_output_tokens':9*2048}
            if (value['final_supplemental_limits']!=expected or approved.get('final_supplemental_limits')!=expected
                    or approved.get('final_supplemental_confirmed') is not True
                    or value.get('graph_http_limits')!={'MI-01':4,'MI-04':5,'MI-06':5,'MI-16':6}):
                raise ValueError('explicit_final_nine_request_supplement_required')
            account=value['continuation_account_id']
            if len(account)!=64 or any(c not in '0123456789abcdef' for c in account): raise ValueError('continuation_identity_invalid')
            target=ROOT/'reports/multi-intent/live-budget'/value['phase_prepared_id']/'continuations'/account/'dispatch.sqlite'
            with sqlite3.connect(target) as db:
                previous=db.execute('SELECT * FROM dispatches WHERE operation NOT LIKE ? ORDER BY operation',
                    (value['operation_prefix']+'%',)).fetchall()
            if value.get('continuation_previous_rows_sha256'):
                if hashlib.sha256(json.dumps(previous,sort_keys=True).encode()).hexdigest()!=value['continuation_previous_rows_sha256']:
                    raise ValueError('continuation_history_changed')
            elif previous: raise ValueError('continuation_history_changed')
        if value.get('layout_supplemental_limits'):
            expected={'max_requests':9,'max_input_tokens':9*24576,'max_output_tokens':9*2048}
            if (value['layout_supplemental_limits']!=expected or approved.get('layout_supplemental_limits')!=expected
                    or approved.get('layout_supplemental_confirmed') is not True):
                raise ValueError('explicit_layout_nine_request_supplement_required')
            account=value['final_supplemental_account_id']
            if len(account)!=64 or any(c not in '0123456789abcdef' for c in account): raise ValueError('final_account_identity_invalid')
            target=ROOT/'reports/multi-intent/live-budget'/value['phase_prepared_id']/'final-supplements'/account/'dispatch.sqlite'
            with sqlite3.connect(target) as db:
                previous=db.execute('SELECT * FROM dispatches WHERE operation NOT LIKE ? ORDER BY operation',
                    (value['operation_prefix']+'%',)).fetchall()
            if previous: raise ValueError('final_account_history_changed')
    # The immutable authorization stays at path; these aliases adapt the existing runner.
    return {**value,'max_input_bytes_reserved':value['max_input_reserved_utf8_bytes'],
        'max_output_tokens_reserved':value['max_output_reserved_tokens'],
        'max_end_to_end_http_per_case':4,'end_to_end_cases':value['full_graph_cases'],
        'max_planned_new_requests':20 if value.get('operation_prefix')=='layout-v1/' else 16 if value.get('operation_prefix') in {'information-limit-v1/','marker-v1/'} else 46 if value.get('operation_prefix')=='read-only-v1/' else 49 if value.get('operation_prefix')=='operation-limit-v1/' else 61 if value.get('supplemental_account_id') else 69 if value.get('supplemental_limits') else 62 if value.get('carried_evidence') else 76}


async def evaluate(plan,output,*,dry,plan_path,authorization):
    import httpx
    from app.config.config import settings
    from app.agent.item_judge_v2 import fragments
    from app.agent.intent_items import snapshot
    from app.agent.answer_contracts import tool_evidence_text
    from scripts.evaluate_item_judge_v2 import synthetic_verdict
    from scripts.evaluate_multi_intent import fixture_state
    from scripts.m5_live_budget import guarded_transport as original_transport
    data=dataset();cases={case['case_id']:case for case in data['cases']}
    settings.LLM_JUDGE_CONTRACT_VERSION='v2'
    settings.LLM_JUDGE_V2_OUTPUT_PROTOCOL=plan.get('judge_output_protocol','function_calling')
    settings.LLM_MAX_TOKENS=2048

    def transport(ledger,*,model,endpoint,underlying=None):
        if underlying is None:
            return original_transport(ledger,model=model,endpoint=endpoint)
        original=underlying
        async def synthetic(request):
            payload=json.loads(request.content)
            is_json=payload.get('response_format')=={'type':'json_object'}
            function='ItemJudgeVerdictV2' if is_json else payload['tools'][0]['function']['name']
            if function=='LLMResponse' and 'item_replies' in payload['tools'][0]['function']['parameters']['properties']:
                case_id,_=shared.ACTIVE.get();case=cases[case_id]
                message={'role':'assistant','content':None,'tool_calls':[{'id':'synthetic','type':'function',
                    'function':{'name':function,'arguments':json.dumps({'intent':fixture_state(case).detected_intent,
                        'item_replies':{f'i{index+1}':text for index,text in enumerate(case['good_answers'])}},ensure_ascii=False)}}]}
                return httpx.Response(200,json={'id':'synthetic','object':'chat.completion','created':1,'model':model,
                    'choices':[{'index':0,'message':message,'finish_reason':'tool_calls'}],
                    'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150}},request=request)
            if function!='ItemJudgeVerdictV2': return await original.handle_async_request(request)
            case_id,stage=shared.ACTIVE.get()
            case=cases[case_id];value=synthetic_verdict(case,bad=stage=='bad')
            selectors='server-selectors.v1' in json.dumps(payload)
            if selectors:
                from app.agent.item_judge_selectors import fixture_selectors
                from scripts.evaluate_multi_intent import candidate
                fixture=fixture_state(case)
                value=fixture_selectors(value,candidate(case,bad=stage=='bad')[0],snapshot(fixture),
                    tool_evidence_text(fixture),fixture.current_evidence())
                if stage=='graph':
                    import re
                    text=payload['messages'][-1]['content']
                    segment_rows,_=json.JSONDecoder().raw_decode(text.split('[v2候选句子]',1)[1].lstrip())
                    item_rows,_=json.JSONDecoder().raw_decode(text.split('[逐项要求及限定来源]',1)[1].lstrip())
                    actual={row['item_id']:row for row in item_rows['items']}
                    value['segments']={};owners={};owner=None
                    for segment in segment_rows:
                        marker=re.search(r'【(i[1-4])】',segment['text'])
                        if marker:owner=marker.group(1)
                        if owner not in actual:raise ValueError('synthetic_segment_owner_missing')
                        owners[segment['segment_id']]=owner
                        refs=actual[owner]['evidence_ids']
                        factual=[key for key in refs if key.startswith(('K','T'))]
                        if not factual:factual=[key for key in refs if key.startswith('L')]
                        value['segments'][f's{segment["segment_id"]}']=({'kind':'facts','support':'supported',
                            'evidence':factual[:1],'issues':[]} if factual else {'kind':'insufficient_notice'})
                    for check in value['item_checks']:
                        check['answer_segment_id']=next(index for index,key in owners.items() if key==check['item_id'])
            elif stage=='graph':
                fixture=fixture_state(case)
                old={item['fragment_id']:item['short_id'] for item in fragments(
                    tool_evidence_text(fixture),snapshot(fixture),fixture.current_evidence())}
                text=payload['messages'][-1]['content']
                marker='[服务器完整证据片段；claims仅选择fragment_id]'
                actual,_=json.JSONDecoder().raw_decode(text.split(marker,1)[1].lstrip())
                new={item['short_id']:item['fragment_id'] for item in actual}
                for claim in [claim for slot in value['segments'].values() if slot['kind']=='facts' for claim in slot['claims']]:
                    for ref in claim['evidence']: ref['fragment_id']=new[old[ref['fragment_id']]]
            message=({'role':'assistant','content':json.dumps(value,ensure_ascii=False)} if is_json else
                {'role':'assistant','content':None,'tool_calls':[{'id':'synthetic','type':'function',
                    'function':{'name':function,'arguments':json.dumps(value,ensure_ascii=False)}}]})
            return httpx.Response(200,json={'id':'synthetic','object':'chat.completion','created':1,'model':model,
                'choices':[{'index':0,'message':message,'finish_reason':'stop' if is_json else 'tool_calls'}],
                'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150}},request=request)
        return original_transport(ledger,model=model,endpoint=endpoint,underlying=httpx.MockTransport(synthetic))

    with patch.object(shared,'validated_plan',validate),patch.object(shared,'operation_limits',limits),patch(
            'scripts.m5_live_budget.guarded_transport',transport):
        report=await shared.evaluate(plan,data,output,dry=dry,prepared_path=plan_path,authorization_path=authorization)
    report.update(scope='FIXED_V2_LOCAL_SYNTHETIC_ACCEPTANCE_NOT_GENERAL_PRODUCTION_QUALITY',
        new_phase=True,judge='v2',generated_text_review='PENDING_SEPARATE_REVIEW' if report['passed'] else 'NOT_ELIGIBLE',
        deployment='NOT_RUN',final_admission_allowed=False)
    rows=json.loads((output/'component-results.json').read_bytes())
    fresh=[row for row in rows if row['split']=='fresh_synthetic']
    report['split_metrics']['fresh_synthetic']={'cases_run':len(fresh),
        'planned_cases':4,'good_accepts':sum(row['judgements'][0]['accepted'] for row in fresh),
        'bad_false_accepts':sum(row['judgements'][1]['accepted'] for row in fresh),
        'all_items_covered':sum(row['plan']['all_items_covered'] for row in fresh)}
    save(output/'report.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared',type=Path,required=True);parser.add_argument('--authorization',type=Path)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--rehearse',action='store_true')
    args=parser.parse_args();args.prepared=args.prepared.resolve();args.output=args.output.resolve()
    if args.authorization: args.authorization=args.authorization.resolve()
    if args.output.exists(): raise ValueError('preserve_prior_evidence')
    plan=validate(args.prepared,args.authorization,rehearse=args.rehearse)
    with isolated_configuration(live=not args.rehearse):
        report=asyncio.run(evaluate(plan,args.output,dry=args.rehearse,plan_path=args.prepared,authorization=args.authorization))
    print(json.dumps({'passed':report['passed'],'cases':report['cases'],'new_requests':report['new_dispatches'],
        'paid':report['paid_http_requests'],'full_graphs':len(report['end_to_end'])}))
    raise SystemExit(0 if report['passed'] else 1)


if __name__=='__main__': main()
