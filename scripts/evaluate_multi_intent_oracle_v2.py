"""Two bounded real Judge calls on current published policy; not an API E2E run."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.evaluate_multi_intent import isolated_configuration,fixture_state,DATA,save,provider_stamp
from scripts.m5_live_budget import DispatchLedger,guarded_transport,OPERATION
from scripts.multi_intent_oracle import VERSION,score,load_oracle
from scripts.test_provenance import snapshot_sources

CASES=('KB-PRESENT','KB-MISSING')
OPS={f'oracle-v2/{key}/judge':2048 for key in CASES}


def fixture(case_id,snapshot):
    """Explicitly synthetic understanding/order context; retrieval source is real."""
    from copy import deepcopy
    case=deepcopy(next(c for c in json.loads(DATA.read_bytes())['cases'] if c['case_id']==('MI-01' if case_id=='KB-PRESENT' else 'MI-04')))
    case.update(case_id=case_id,items=case['items'][:2],knowledge=case['knowledge'][:2],
        message='查询订单ORD960001，退货包装有什么要求。' if case_id=='KB-PRESENT' else '查我的订单状态，退货包装有什么要求。')
    states=['answered','answered'] if case_id=='KB-PRESENT' else ['limited','answered']
    case.update(expected_item_statuses={f'i{i+1}':[s] for i,s in enumerate(states)},
        expected_task_statuses=['completed' if case_id=='KB-PRESENT' else 'waiting_input'],
        review_status='assistant_adjudicated_current_policy_synthetic_context_not_human_gold')
    state=fixture_state(case)
    from app.agent.intent_items import observe,evidence_digest
    state.accept_retrieval(snapshot['retrieval'])
    rows=state.current_evidence()
    observe(state,'i2',[evidence_digest(row) for row in rows])
    return case,state


def valid_fixture(state,body):
    """Transport exercise only. Does not pretend these semantic labels are real."""
    from app.agent.item_judge_selectors import layout
    from app.agent.intent_items import snapshot
    context=snapshot(state);segments,owners=layout(context,body)
    value={'schema_version':2,'answerability':'full' if state.task.order_id else 'partial',
        'response_behavior':'answer' if state.task.order_id else 'answer_with_limits','response_validity':'valid',
        'claims_complete':True,'response_issues':[],'source_contract_version':'server-selectors.v1','segments':{},'item_checks':[]}
    for entry in segments:
        owner=owners[entry['segment_id']];item=next(c for c in context['items'] if c['item_id']==owner)
        refs=[r for r in item['evidence_ids'] if r.startswith(('T','K'))]
        if not refs:refs=[r for r in item['evidence_ids'] if r.startswith('L')]
        value['segments'][f's{entry["segment_id"]}']={'kind':'facts','support':'supported','evidence':refs,'issues':[]}
    for item in context['items']:
        missing=item['kind']=='query_order' and not state.task.order_id
        value['item_checks'].append({'item_id':item['item_id'],'status':'limited' if missing else 'answered',
            'supported':True,'conditions_preserved':True,'answerability':'unanswerable' if missing else 'answerable',
            'answer_segment_id':next(i for i,owner in owners.items() if owner==item['item_id'])})
    return value


def validate(prepared,*,live=False):
    plan=json.loads((prepared/'plan.json').read_bytes())
    raw={k:v for k,v in plan.items() if k!='prepared_id'}
    if hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()!=plan['prepared_id']:raise ValueError('plan_changed')
    if snapshot_sources(ROOT)['fingerprint']!=plan['source_fingerprint']:raise ValueError('source_changed')
    if plan['cases']!=list(CASES) or plan['max_requests']!=2 or plan['oracle_version']!=VERSION:raise ValueError('fixed_two_call_scope_changed')
    for path,digest in plan['inputs'].items():
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=digest:raise ValueError('frozen_inputs_changed')
    for path,digest in plan['protected_ledgers'].items():
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=digest:raise ValueError('historical_ledger_changed')
    with sqlite3.connect(ROOT/plan['account']['path']) as db:
        rows=db.execute("SELECT * FROM dispatches WHERE operation NOT LIKE 'oracle-v2/%' ORDER BY operation").fetchall()
    if len(rows)!=7 or hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()!=plan['account']['previous_rows_sha256']:
        raise ValueError('reserved_account_history_changed')
    if live:
        auth=json.loads((prepared/'authorization.json').read_bytes())
        if auth.get('confirmed') is not True or auth.get('prepared_id')!=plan['prepared_id'] or auth.get('use_reserved_two') is not True:
            raise ValueError('explicit_reserved_two_authorization_required')
        if provider_stamp()!=plan['provider']:raise ValueError('provider_changed')
    return plan


async def evaluate(plan,out,*,dry=False):
    import httpx
    from openai import AsyncOpenAI
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from app.config.config import settings
    from app.models.models import Base,User
    from app.services.llm_service import LLMService
    from app.services.model_gateway import model_context
    from app.agent.intent_items import snapshot,REVIEW_CONTEXT,verify_review,finalize
    from app.agent.item_evidence_reply import evidence_reply
    from app.agent.answer_contracts import review_query,tool_evidence_text
    from app.agent.validation_llm import LLMRAGJudge
    settings.LLM_JUDGE_CONTRACT_VERSION='v2';settings.LLM_JUDGE_V2_OUTPUT_PROTOCOL='json_object';settings.LLM_MAX_TOKENS=2048
    if dry:settings.LLM_API_KEY='synthetic-only'
    material=json.loads((ROOT/plan['policy_snapshot']).read_bytes())
    temporary=tempfile.TemporaryDirectory() if dry else None
    account=plan['account']
    path=Path(temporary.name)/'dispatch.sqlite' if dry else ROOT/account['path']
    preserved=[]
    if not dry:
        with sqlite3.connect(path) as db:preserved=[r[0] for r in db.execute('SELECT operation FROM dispatches')]
    ledger=DispatchLedger(path,'offline-only' if dry else account['id'],
        {'max_requests':2,'max_input_tokens':49152,'max_output_tokens':4096} if dry else account['limits'],
        {**{key:2048 for key in preserved},**OPS})
    initial=ledger.snapshot()['requests'];captures=[];request_shapes=[];active={}
    async def synthetic(request):
        value=valid_fixture(active['state'],active['body'])
        return httpx.Response(200,json={'id':'synthetic','object':'chat.completion','created':1,'model':settings.LLM_MODEL,
            'choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(value,ensure_ascii=False)},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':100,'completion_tokens':100,'total_tokens':200}},request=request)
    inner=guarded_transport(ledger,model=settings.LLM_MODEL,endpoint=settings.LLM_API_BASE,
        underlying=httpx.MockTransport(synthetic) if dry else None)
    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self,request):
            if ledger.snapshot()['requests']-initial>=2:raise ValueError('fixed_two_call_cap')
            request_shapes.append({'operation':OPERATION.get(),'bytes':len(request.content)})
            reply=await inner.handle_async_request(request)
            captures.append({'operation':OPERATION.get(),'http_status':reply.status_code,'response':reply.json()})
            save(out/'responses.json',captures);return reply
        async def aclose(self):await inner.aclose()
    engine=create_async_engine(settings.DATABASE_URL);factory=async_sessionmaker(engine,expire_on_commit=False)
    async with engine.begin() as db:await db.run_sync(Base.metadata.create_all)
    async with factory() as db:db.add(User(id=101,username='synthetic-oracle-audit',role='user',status=True,token_version=0));await db.commit()
    rows=[];service=LLMService()
    async with httpx.AsyncClient(transport=Transport(),timeout=30,trust_env=False) as provider:
        original=service._get_chat_model
        def model(config,**kwargs):
            value=original(config,**kwargs)
            sdk=AsyncOpenAI(api_key=config.api_key,base_url=config.api_base,http_client=provider,max_retries=0)
            value.root_async_client,value.async_client=sdk,sdk.chat.completions;return value
        try:
            with patch.object(service,'_get_chat_model',model):
                for case_id in CASES:
                    case,state=fixture(case_id,material);body=evidence_reply(state)
                    if not body:raise ValueError('current_evidence_reply_unavailable')
                    active.update(state=state,body=body)
                    token=REVIEW_CONTEXT.set(snapshot(state));operation=OPERATION.set(f'oracle-v2/{case_id}/judge')
                    try:
                        async with model_context(101,'oracle-v2-'+case_id,factory=factory):
                            result=await LLMRAGJudge(service).judge(body,review_query(state),state.current_evidence(),tool_evidence_text(state))
                    finally:REVIEW_CONTEXT.reset(token);OPERATION.reset(operation)
                    reviews,issues=verify_review(state,body,result.get('item_checks'))
                    if result.get('publish_allowed') and not issues:state.task_runtime.item_reviews=reviews;finalize(state)
                    scored=score(case,protocol_valid=bool(result.get('item_checks')) and result.get('response_validity')!='unknown',
                        response_supported=result.get('publish_allowed') is True,item_checks=result.get('item_checks'),
                        task_status=state.task.status,require_task=True,binding_issues=issues)
                    rows.append({'case_id':case_id,'passed':scored['passed'],'reply':body,'oracle_score':scored,
                        'judge':result,'task_status':state.task.status,'policy_sources':[{'id':r['id'],'version_id':r['version_id']} for r in state.current_evidence()]})
                    save(out/'results.json',rows)
                    if not scored['passed']:break
        finally:await service.close();await engine.dispose()
    report={'passed':len(rows)==2 and all(r['passed'] for r in rows),'cases_run':len(rows),'paid_requests':0 if dry else ledger.snapshot()['requests']-initial,
        'synthetic_http':ledger.snapshot()['requests']-initial if dry else 0,'request_shapes':request_shapes,
        'scope':'independent_real_judge_current_published_policy_synthetic_understanding_and_order_not_API_E2E',
        'oracle_version':VERSION,'label_origin':load_oracle()['review_origin'],'budget':ledger.snapshot(),
        'new_requests':ledger.snapshot()['requests']-initial,'business_source_modified':False}
    save(out/'report.json',report)
    if temporary:temporary.cleanup()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--prepared',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--rehearse',action='store_true')
    args=parser.parse_args();prepared=args.prepared.resolve();out=args.output.resolve()
    if out.exists():raise ValueError('preserve_old_results')
    plan=validate(prepared,live=not args.rehearse)
    with isolated_configuration(live=not args.rehearse):report=asyncio.run(evaluate(plan,out,dry=args.rehearse))
    print(json.dumps({k:report[k] for k in ['passed','cases_run','paid_requests','synthetic_http']}))
    raise SystemExit(0 if report['passed'] else 1)


if __name__=='__main__':main()
