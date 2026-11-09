"""Fixed v2 item diagnostics; six synthetic good/bad pairs and one bounded full graph."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts.evaluate_multi_intent import DATA, isolated_configuration, fixture_state, candidate, save, provider_stamp
from scripts.m5_live_budget import DispatchLedger, OPERATION, guarded_transport
from scripts.run_multi_intent_live import TwoAccountLedger
from scripts.test_provenance import snapshot_sources

CASES=('MI-15','MI-04','MI-07','MI-08','MI-11','MI-13')
FRAGMENT_CASES=('MI-07','MI-08','MI-13')
PHASE='898af4b30533bac28a82565e61a90e3c8870820c07ea10d75a9d009e9ae008e6'
ACTIVE=ContextVar('item_v2_diagnostic_operation',default=None)


def operations(prefix='item-v2/',graph_limit=6,preserved=(),cases=CASES):
    if not prefix.startswith('item-v2') or not prefix.endswith('/') or not 1<=graph_limit<=6:
        raise ValueError('operation_scope_invalid')
    return {**{key:1024 for key in preserved},
            **{f'{prefix}{case}/{stage}':1024 for case in cases for stage in ('good','bad')},
            **{f'{prefix}E2E-MI-01/http-{index}':1024 for index in range(1,graph_limit+1)}}


def validate(plan_path,authorization_path,*,dry):
    plan=json.loads(plan_path.read_bytes())
    unsigned={key:value for key,value in plan.items() if key!='prepared_id'}
    if hashlib.sha256(json.dumps(unsigned,sort_keys=True).encode()).hexdigest()!=plan['prepared_id']:
        raise ValueError('plan_hash_changed')
    if plan['source_fingerprint']!=snapshot_sources(ROOT)['fingerprint'] or plan['dataset_sha256']!=hashlib.sha256(DATA.read_bytes()).hexdigest():
        raise ValueError('source_or_dataset_changed')
    expected=() if plan.get('scope')=='derived_sources_full_graph_only' else (
        FRAGMENT_CASES if plan.get('source_contract')=='server-fragments.v1' else CASES)
    if (plan['cases']!=list(expected) or plan['judge']!='v2'
            or (plan['max_new_requests'],plan['max_full_graph_requests']) not in {(18,6),(16,4),(10,4),(4,4)}):
        raise ValueError('fixed_diagnostic_plan_changed')
    if plan.get('scope')=='derived_sources_full_graph_only' and (
            plan.get('source_contract')!='server-fragments.v2' or plan['max_new_requests']!=4):
        raise ValueError('derived_sources_plan_invalid')
    if not dry:
        approved=json.loads(authorization_path.read_bytes()) if authorization_path else {}
        if (approved.get('confirmed') is not True or approved.get('prepared_id')!=plan['prepared_id']
                or approved.get('max_new_requests')!=plan['max_new_requests'] or approved.get('reuse_existing_accounts') is not True):
            raise ValueError('explicit_v2_resume_required')
        if plan['provider']!=provider_stamp(): raise ValueError('provider_changed')
        for account in plan['accounts']:
            path=ROOT/account['path']
            if not path.resolve().is_relative_to((ROOT/'reports/multi-intent/live-budget'/PHASE).resolve()):
                raise ValueError('account_path_invalid')
            if not path.exists(): raise ValueError('existing_account_missing')
            import sqlite3
            with sqlite3.connect(path) as db:
                previous=db.execute("SELECT * FROM dispatches WHERE operation NOT LIKE ? ORDER BY operation",
                    (plan.get('operation_prefix','item-v2/')+'%',)).fetchall()
                metadata=json.loads(db.execute('SELECT value FROM metadata WHERE id=1').fetchone()[0])
            if (len(previous)!=account.get('previous_requests',0) or hashlib.sha256(json.dumps(previous,sort_keys=True).encode()).hexdigest()!=
                    account.get('previous_rows_sha256',hashlib.sha256(b'[]').hexdigest())
                    or metadata!={'plan_id':account['id'],'limits':account['limits']}):
                raise ValueError('previous_account_or_authorization_changed')
    return plan


def synthetic_verdict(case,bad=False):
    """Only SDK rehearsal uses fixture labels; never sent to the real model."""
    import re
    from app.agent.intent_items import snapshot
    from app.agent.item_judge_v2 import review_sources
    from app.agent.judge_contracts import evidence_catalog,response_segments
    from app.agent.answer_contracts import tool_evidence_text
    state=fixture_state(case);body,checks=candidate(case,bad=bad)
    context=snapshot(state)
    if bad and case['attack']=='foreign_reference':
        # The judge rejects the wrong answer using its actual allowed source;
        # an adversarial candidate does not authorize forged judge references.
        checks[case['bad_item']]['evidence_ids']=context['items'][case['bad_item']]['evidence_ids'][:1]
    augmented,identities=review_sources(tool_evidence_text(state),context,state.current_evidence())
    catalog=evidence_catalog(state.current_evidence(),augmented)
    claims=[];nonfact=[]
    sections=list(re.finditer(r'【(i[1-4])】(.*?)(?=【i[1-4]】|\Z)',body,re.S))
    for segment in response_segments(body):
        start=body.find(segment['text'])
        owner=next(section.group(1) for section in sections if section.start()<=start<section.end())
        check=next(check for check in checks if check['item_id']==owner)
        wrong=bad and owner==f"i{case['bad_item']+1}"
        if check['evidence_ids']:
            evidence=[{'kind':identities[short][0],'source_id':identities[short][1],
                'version_id':identities[short][2],'quote':catalog[identities[short]]} for short in check['evidence_ids']]
            claims.append({'segment_id':segment['segment_id'],'text':segment['text'],
                'support':'unsupported' if wrong else 'supported','evidence':evidence,
                'issues':['contradiction'] if wrong else []})
        else: nonfact.append({'segment_id':segment['segment_id'],'kind':'insufficient_notice'})
    if bad: checks[case['bad_item']].update(supported=False,conditions_preserved=False)
    full=all(check['status']=='answered' for check in checks)
    value={'schema_version':2,'answerability':'full' if full else 'partial',
        'response_behavior':'answer' if full else 'answer_with_limits','response_validity':'invalid' if bad else 'valid',
        'claims_complete':True,'claims':claims,'nonfact_segments':nonfact,
        'response_issues':['contradiction'] if bad else [],'item_checks':checks}
    from app.agent.item_judge_v2 import to_fragment_verdict,to_sentence_verdict
    return to_sentence_verdict(to_fragment_verdict(value,tool_evidence_text(state),context,state.current_evidence()))


async def run(plan,output,*,dry,plan_path,authorization_path):
    import httpx
    from openai import AsyncOpenAI
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from app.models.models import Base,User
    from app.config.config import settings
    from app.services.llm_service import LLMService
    from app.services.model_gateway import model_context
    from app.agent.validation_llm import LLMRAGJudge
    from app.agent.intent_items import REVIEW_CONTEXT,snapshot,verify_review
    from app.agent.answer_contracts import review_query,tool_evidence_text
    settings.LLM_JUDGE_CONTRACT_VERSION='v2'
    if dry: settings.LLM_API_KEY='synthetic-only'
    cases={case['case_id']:case for case in json.loads(DATA.read_bytes())['cases']}
    temporary=tempfile.TemporaryDirectory(prefix='v2-item-dry-') if dry else None
    accounts=[]
    prefix=plan.get('operation_prefix','item-v2/')
    allowed=operations(prefix,plan['max_full_graph_requests'],plan.get('preserved_operations',[]),plan['cases'])
    for index,account in enumerate(plan['accounts']):
        path=Path(temporary.name)/f'{index}.sqlite' if dry else ROOT/account['path']
        accounts.append(DispatchLedger(path,account['id'],account['limits'],allowed))
    ledger=TwoAccountLedger(*accounts)
    initial=ledger.snapshot()['requests'];captures=[];timings=[];graph_calls=0
    engine=create_async_engine(settings.DATABASE_URL);factory=async_sessionmaker(engine,expire_on_commit=False)
    async with engine.begin() as connection: await connection.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add(User(id=101,username='synthetic-item-v2-diagnostic',role='user',status=True,token_version=0));await db.commit()
    def simulated(request):
        case_id,stage=ACTIVE.get();case=cases[case_id]
        function=json.loads(request.content)['tools'][0]['function']['name']
        if function=='ItemizedTaskUnderstanding':
            value=fixture_state(case).understanding.model_dump();value.pop('task_kind',None)
        elif function=='LLMResponse':
            value={'reply':candidate(case)[0],'intent':fixture_state(case).detected_intent,'confidence':1.,'citations':[],'need_human':False}
        elif 'TaskDecision' in function:
            value={'step':{'action':'search_knowledge','knowledge_focus':'request'}}
        else:
            value=synthetic_verdict(case,bad=stage=='bad')
            if stage=='graph':
                from app.agent.item_judge_v2 import fragments
                from app.agent.intent_items import snapshot
                from app.agent.answer_contracts import tool_evidence_text
                fixture=fixture_state(case)
                old={entry['fragment_id']:entry['short_id'] for entry in fragments(
                    tool_evidence_text(fixture),snapshot(fixture),fixture.current_evidence())}
                text=json.loads(request.content)['messages'][-1]['content']
                marker='[服务器完整证据片段；claims仅选择fragment_id]'
                actual,_=json.JSONDecoder().raw_decode(text.split(marker,1)[1].lstrip())
                new={entry['short_id']:entry['fragment_id'] for entry in actual}
                for claim in [claim for slot in value['segments'].values() if slot['kind']=='facts' for claim in slot['claims']]:
                    for reference in claim['evidence']: reference['fragment_id']=new[old[reference['fragment_id']]]
        return httpx.Response(200,json={'id':'synthetic','object':'chat.completion','created':1,'model':settings.LLM_MODEL,
            'choices':[{'index':0,'message':{'role':'assistant','content':None,'tool_calls':[{'id':'synthetic',
                'type':'function','function':{'name':function,'arguments':json.dumps(value,ensure_ascii=False)}}]},'finish_reason':'tool_calls'}],
            'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150}},request=request)
    guarded=guarded_transport(ledger,model=settings.LLM_MODEL,endpoint=settings.LLM_API_BASE,
        underlying=httpx.MockTransport(simulated) if dry else None)
    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self,request):
            nonlocal graph_calls
            case_id,stage=ACTIVE.get()
            if stage=='graph':
                graph_calls+=1
                if graph_calls>plan['max_full_graph_requests']: raise ValueError('full_graph_request_cap')
                operation=f'{prefix}E2E-MI-01/http-{graph_calls}'
            else: operation=f'{prefix}{case_id}/{stage}'
            if not dry: validate(plan_path,authorization_path,dry=False)
            if ledger.snapshot()['requests']-initial>=plan['max_new_requests']: raise ValueError('diagnostic_request_cap')
            started=time.perf_counter();timing={'operation':operation,'events':[],'outcome':'cancelled_or_failed'}
            async def trace(name,info): timing['events'].append({'stage':name,'ms':round((time.perf_counter()-started)*1000,2)})
            request.extensions['trace']=trace
            token=OPERATION.set(operation)
            try:
                response=await guarded.handle_async_request(request)
                captures.append({'operation':operation,'response':response.json()});save(output/'responses.json',captures)
                timing['outcome']='http_complete'
                return response
            finally:
                OPERATION.reset(token);timing['elapsed_ms']=round((time.perf_counter()-started)*1000,2)
                timings.append(timing);save(output/'http-timings.json',timings)
        async def aclose(self): await guarded.aclose()
    rows=[];graphs=[]
    async with AsyncExitStack() as resources:
        provider=await resources.enter_async_context(httpx.AsyncClient(transport=Transport(),timeout=30,trust_env=False))
        original=LLMService._get_chat_model
        def model(self,config,**kwargs):
            value=original(self,config,**kwargs)
            sdk=AsyncOpenAI(api_key=config.api_key,base_url=config.api_base,http_client=provider,max_retries=0)
            value.root_async_client,value.async_client=sdk,sdk.chat.completions
            return value
        resources.enter_context(patch.object(LLMService,'_get_chat_model',model))
        service=LLMService();resources.push_async_callback(service.close)
        for case_id in plan['cases']:
            case=cases[case_id];state=fixture_state(case);pair=[]
            for bad in (False,True):
                body,_=candidate(case,bad=bad)
                token=ACTIVE.set((case_id,'bad' if bad else 'good'));context_token=REVIEW_CONTEXT.set(snapshot(state))
                try:
                    async with model_context(101,f'v2-{case_id}-{bad}',factory=factory):
                        judgement=await LLMRAGJudge(service).judge(body,review_query(state),state.current_evidence(),tool_evidence_text(state))
                finally: REVIEW_CONTEXT.reset(context_token);ACTIVE.reset(token)
                _,issues=verify_review(state,body,judgement.get('item_checks'))
                accepted=judgement['publish_allowed'] and not issues
                valid=judgement.get('schema_version')==2 and isinstance(judgement.get('item_checks'),list)
                pair.append({'bad':bad,'accepted':accepted,'valid_output':valid,'issues':issues,'judgement':judgement})
            rows.append({'case_id':case_id,'judgements':pair,
                'passed':pair[0]['accepted'] and not pair[1]['accepted'] and all(item['valid_output'] for item in pair)})
            save(output/'component-results.json',rows)
            if not rows[-1]['passed']: break
        if len(rows)==len(plan['cases']) and all(row['passed'] for row in rows):
            from app.agent.graph import AgentGraph
            from app.agent.state import AgentState
            from app.agent.tools import ToolExecutionResult
            case=cases['MI-01']
            class Knowledge:
                def evidence_is_current(self,items): return all(item.get('version_id')=='synthetic-v1' for item in items)
                async def async_search(self,query,top_k):
                    state=fixture_state(case)
                    selected=[entry for index,content in enumerate(case['knowledge']) if content and case['items'][index]['text'] in query
                        for entry in state.current_evidence() if entry['id'].endswith(f'-i{index+1}')]
                    return {'success':True,'results':selected,'meta':{'score_type':'rrf','knowledge_epoch':1},
                        'evidence_bundle':{'schema_version':1,'items':selected,'knowledge_epoch':1}}
            async def read(tasks,principal):
                if principal.user_id!=101 or any(task.tool!='query_order' or task.resource_id!=case['order_id'] for task in tasks):
                    raise ValueError('synthetic_read_outside_scope')
                return [({'user_id':101,'order_id':task.resource_id},ToolExecutionResult(success=True,data={
                    'order_id':task.resource_id,'status_code':'delivered','status':'已签收','total_amount':'99.10'})) for task in tasks]
            with patch('app.agent.graph.get_knowledge_base',lambda:Knowledge()),patch('app.agent.read_plan.run_authorized_reads',read):
                graph=AgentGraph();resources.push_async_callback(graph.llm_service.close)
                state=AgentState(session_id='v2-full-graph',user_id=101,actor_id=101,source_message_id=17,
                    user_message=case['message'],understanding_mode='model',task_loop_enabled=True)
                token=ACTIVE.set(('MI-01','graph'))
                try:
                    async with model_context(101,'v2-full-graph',factory=factory): result=await graph.run(state)
                finally: ACTIVE.reset(token)
            graphs.append({'case_id':'MI-01','passed':bool(result.review_result and result.review_result.passed
                and result.task and result.task.status=='completed'),'reply':result.reply,
                'status':result.task.status if result.task else None,'item_outcomes':result.task.item_outcomes if result.task else {},
                'review':result.review_result.model_dump() if result.review_result else None})
            save(output/'end-to-end-results.json',graphs)
    stable=snapshot_sources(ROOT)['fingerprint']==plan['source_fingerprint']
    report={'mode':'SDK_REHEARSAL_NOT_MODEL_QUALITY' if dry else 'BOUNDED_V2_ITEM_DIAGNOSTIC_NOT_GENERAL_ADMISSION',
        'passed':len(rows)==len(plan['cases']) and all(row['passed'] for row in rows) and len(graphs)==1 and graphs[0]['passed'] and stable,
        'cases_run':len(rows),'planned_component_cases':len(plan['cases']),'components_passed':sum(row['passed'] for row in rows),
        'good_accepts':sum(row['judgements'][0]['accepted'] for row in rows),'bad_false_accepts':sum(row['judgements'][1]['accepted'] for row in rows),
        'full_graph':graphs,'source_stable':stable,'source_fingerprint':plan['source_fingerprint'],
        'new_requests':ledger.snapshot()['requests']-initial,'paid_requests':0 if dry else ledger.snapshot()['requests']-initial,
        'budget':ledger.snapshot(),'production_enabled':False,'final_admission_allowed':False,
        'independent_human_gold':False,'labels':'assistant-curated synthetic; former holdout failures now development diagnostics',
        'comparison':'historical v1 outputs only; not a same-input fresh head-to-head quality estimate'}
    save(output/'report.json',report)
    await engine.dispose()
    if temporary: temporary.cleanup()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared',type=Path,required=True);parser.add_argument('--authorization',type=Path)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--rehearse',action='store_true')
    args=parser.parse_args();args.prepared=args.prepared.resolve();args.output=args.output.resolve()
    if args.authorization: args.authorization=args.authorization.resolve()
    if args.output.exists(): raise ValueError('do_not_overwrite_evidence')
    plan=validate(args.prepared,args.authorization,dry=args.rehearse)
    with isolated_configuration(live=not args.rehearse):
        result=asyncio.run(run(plan,args.output,dry=args.rehearse,plan_path=args.prepared,authorization_path=args.authorization))
    print(json.dumps({'passed':result['passed'],'requests':result['new_requests'],'paid':result['paid_requests']}))
    raise SystemExit(0 if result['passed'] else 1)


if __name__=='__main__': main()
