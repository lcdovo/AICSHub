"""Frozen-image business acceptance continuation; no live call without both authorizations."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EVAL = Path('/evaluation')
CONT = EVAL / 'continuation-v1'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('mock', 'live'), required=True)
    parser.add_argument('--case', required=True)
    parser.add_argument('--variant', choices=('case','single-rest','single-ws','multi-rest','multi-ws'), default='case')
    args = parser.parse_args()
    from scripts.business_acceptance import load_cases, operations, require_authorization, digest
    from scripts.business_http_boundary import BudgetLedger, budget_transport
    from scripts.m5_live_budget import OPERATION
    from scripts.continue_business_acceptance import require_continuation_authorization
    plan = json.loads((EVAL/'plan.json').read_text())
    original_auth = json.loads((EVAL/'authorization.json').read_text())
    require_authorization(plan, original_auth)
    proposal = json.loads((EVAL/'continuation-proposal.json').read_text())
    authorization = json.loads((CONT/'authorization.json').read_text())
    require_continuation_authorization(authorization,proposal)
    if digest({k:v for k,v in plan.items() if k!='prepared_id'}) != plan['prepared_id']:
        raise ValueError('original_plan_changed')
    for name, expected in plan['inputs'].items():
        if hashlib.sha256((ROOT/'acceptance/business-v1'/name).read_bytes()).hexdigest() != expected:
            raise ValueError('case_material_changed')
    actual = {p.as_posix().removeprefix('/app/'):hashlib.sha256(p.read_bytes()).hexdigest()
              for p in Path('/app/app').rglob('*.py')}
    if actual != plan['runtime']['app_hashes']:
        raise ValueError('image_app_changed')
    cases, sources = load_cases()
    case = next(c for c in cases if c['case_id']==args.case)
    if args.mode=='live' and args.variant!='case':
        raise ValueError('live_case_override_forbidden')
    if args.variant!='case':
        case = {**case, 'transport':'rest' if args.variant.endswith('rest') else 'websocket'}
    name = case['case_id'] if args.variant=='case' else args.variant
    directory = CONT/'cases'/name
    directory.mkdir(parents=True, exist_ok=False)
    def save(name, value):
        path=directory/name
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix(path.suffix+'.tmp')
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(path)
    def old_rows():
        keys = tuple(authorization['old_operations'])
        with sqlite3.connect(EVAL/'dispatch.sqlite') as db:
            return db.execute('SELECT * FROM dispatches WHERE operation IN ('+','.join('?' for _ in keys)+') ORDER BY operation',keys).fetchall()
    if digest(old_rows()) != proposal['old_nine_rows_sha256']:
        raise ValueError('original_nine_rows_changed')
    historic = set(authorization['old_operations'])
    allowed = {k:v for k,v in operations(cases).items() if k in historic or k.split('/')[0] not in {'BA-01','BA-02','BA-03'}}
    ledger = BudgetLedger(EVAL/'dispatch.sqlite',plan['prepared_id'],
        {'max_requests':168,'max_input_tokens':plan['max_input_bytes_reserved'],'max_output_tokens':plan['max_output_tokens']},
        allowed,stream_authorized=True)
    if args.mode=='mock' and args.variant.startswith('multi'):
        allowed.update({'MOCK/'+args.variant+'/t1/http-'+str(n):2048 for n in range(1,7)})
        ledger.operations=allowed
    baseline = ledger.snapshot()['requests']
    if args.mode=='live' and (CONT/'STOP.json').exists():
        raise ValueError('continuation_stopped')
    import importlib.util
    spec=importlib.util.find_spec('app')
    if spec is None or [str(Path(p).resolve()) for p in spec.submodule_search_locations or []]!=['/app/app']:
        raise ValueError('frozen_app_import_shadowed')
    import httpx
    from openai import AsyncOpenAI
    from app.config.config import settings
    from app.services.llm_service import ModelConfig
    from scripts.business_api_adapter import api_case
    model, endpoint = settings.LLM_MODEL, settings.LLM_API_BASE
    turn_ref=[None];events=[];fatal=[];counts={};captures=[];carried=[]
    def record(kind, value):
        # Provider response bodies contain synthetic business data only; never save headers.
        def redact(value):
            if isinstance(value,dict):return {k:('<REDACTED>' if k.lower() in {'api_key','authorization','access_token','refresh_token'} else redact(v)) for k,v in value.items()}
            if isinstance(value,list):return [redact(v) for v in value]
            return value.replace(settings.LLM_API_KEY,'<REDACTED>') if isinstance(value,str) and settings.LLM_API_KEY else value
        events.append({'kind':kind,'turn_id':turn_ref[0],**redact(value)})
        if kind=='failure':fatal.append(events[-1])
        if kind in {'json_response','sse_chunk'}:captures.append(events[-1]);save('provider-responses.json',captures)
        save('events.json',events)
    def mark_failure(reason):record('failure',{'reason':reason})
    original_responses = json.loads((EVAL/'cases/BA-04/provider-responses.json').read_text())
    replay={r['operation']:r for r in original_responses}
    def simulate_multi(request):
        data=json.loads(request.content);name=(data.get('tools') or [{}])[0].get('function',{}).get('name')
        if name and 'Understanding' in name:
            value={'topic':'query_order','request_type':'query','entities':{'order_id':'ORD980101','product':None},
             'conditions':[],'subrequests':[{'topic':'query_order','request_type':'query'},{'topic':'refund','request_type':'inquiry'}],
             'service_preference':'unspecified','emotion':'neutral','clarification':None,
             'retrieval_query':'退货时原包装损坏可以用什么替代包装','entity_source':'current_message',
             'read_tasks':[{'tool':'query_order','resource_id':'ORD980101'}],
             'parameters':{},'missing_information':[],'continue_task':False,
             'itemization_version':'intent-items.v1','items':[{'text':'查一下ORD980101的状态','kind':'query_order'},
               {'text':'原盒坏了能用什么包装','kind':'knowledge'}]}
        elif name and 'TaskDecision' in name:
            payload=json.loads(data['messages'][-1]['content'])
            value={'step':{'action':'search_knowledge','knowledge_focus':'request'}} if 'search_knowledge' in payload['allowed_actions'] else {'step':{'action':'finish'}}
        elif name=='LLMResponse':
            value={'item_replies':{'i1':'订单ORD980101目前已签收。','i2':'如原包装损坏，请使用厚实纸箱。'}}
        elif data.get('response_format')=={'type':'json_object'}:
            from app.agent.intent_items import REVIEW_CONTEXT
            context=REVIEW_CONTEXT.get()
            content=data['messages'][-1]['content']
            segments=json.loads(content.split('[v2候选句子]',1)[1])
            import re
            response_text='\n'.join(x['text'] for x in segments)
            from app.agent.item_judge_selectors import layout
            _,owners=layout(context,response_text)
            slots={};checks=[]
            for segment in segments:
                owner=owners[segment['segment_id']]
                item=next(i for i in context['items'] if i['item_id']==owner)
                refs=item['evidence_ids']
                refs=[r for r in refs if context['catalog'][r]['kind'] in {'knowledge','query_order'}][:1]
                slots['s'+str(segment['segment_id'])]={'kind':'facts','support':'supported','evidence':refs,'issues':[]}
            for item in context['items']:
                checks.append({'item_id':item['item_id'],'status':'answered','supported':True,
                    'conditions_preserved':True,'answerability':'answerable',
                    'answer_segment_id':next(s['segment_id'] for s in segments if owners[s['segment_id']]==item['item_id'])})
            value={'schema_version':2,'source_contract_version':'server-selectors.v1','answerability':'full',
                'response_behavior':'answer','response_validity':'valid','claims_complete':True,
                'segments':slots,'response_issues':[],'item_checks':checks}
        else:raise ValueError('unexpected_mock_purpose')
        message={'role':'assistant','content':json.dumps(value,ensure_ascii=False) if not name else None}
        if name:message['tool_calls']=[{'id':'mock','type':'function','function':{'name':name,'arguments':json.dumps(value,ensure_ascii=False)}}]
        return httpx.Response(200,json={'id':'mock','object':'chat.completion','created':1,'model':model,
            'choices':[{'index':0,'finish_reason':'tool_calls' if name else 'stop','message':message}],
            'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150}},request=request)
    mock_texts = {
        't1':'请先确认耳机已开机（从充电盒取出即开机）。\n完成后请告诉我是否仍无法配对。',
        't2':'请检查手机蓝牙是否开启。\n完成后请告诉我是否仍无法配对。',
    }
    def simulated(request):
        data=json.loads(request.content)
        if case['case_id']!='BA-04':return simulate_multi(request)
        purpose=(data.get('tools') or [{}])[0].get('function',{}).get('name','')
        turn=turn_ref[0]
        if 'Understanding' in purpose:
            value=json.loads(original_responses[0]['response']['choices'][0]['message']['tool_calls'][0]['function']['arguments'])
            value['continue_task']=turn=='t2'
            if turn=='t2':
                value['entity_source']='history'
                value['items']=[{'text':'我按上一轮做了，还是连不上。','kind':'troubleshoot'}]
                value['retrieval_query']='蓝牙降噪耳机仍然无法配对，请提供下一步排查'
                value['subrequests']=[]
        elif 'TaskDecision' in purpose:
            value={'step':{'action':'ask_user','question':'diagnostic_feedback'}}
        elif data.get('stream') is True:
            common={'id':'mock-stream','object':'chat.completion.chunk','created':1,'model':model}
            chunks=[{**common,'choices':[{'index':0,'delta':{'role':'assistant','content':mock_texts[turn]},'finish_reason':None}]},
                    {**common,'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]},
                    {**common,'choices':[],'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150}}]
            frames=[('data: '+json.dumps(x,ensure_ascii=False)+'\n\n').encode() for x in chunks]+[b'data: [DONE]\n\n']
            class Bytes(httpx.AsyncByteStream):
                def __init__(self):self.frames=iter(frames)
                def __aiter__(self):return self
                async def __anext__(self):
                    try:return next(self.frames)
                    except StopIteration:raise StopAsyncIteration
            return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Bytes(),request=request)
        elif purpose=='LLMResponse':
            value={'reply':mock_texts[turn],'intent':'technical','confidence':1.0,'citations':[],'need_human':False}
        elif data.get('response_format')=={'type':'json_object'}:
            content=data['messages'][-1]['content']
            segments=json.JSONDecoder().raw_decode(content.split('[v2候选句子]',1)[1].lstrip())[0]
            source=next(x for x in sources['items'] if x['id']==sources['aliases']['earphone_reset'])
            value={'schema_version':2,'answerability':'full','response_behavior':'answer','response_validity':'valid',
                'claims_complete':True,'response_issues':[],
                'claims':[{'segment_id':segments[0]['segment_id'],'text':segments[0]['text'],'support':'supported','issues':[],
                    'evidence':[{'kind':'knowledge','source_id':source['id'],'version_id':source['version_id'],'quote':source['content']}]}],
                'nonfact_segments':[{'segment_id':s['segment_id'],'kind':'clarification'} for s in segments[1:]]}
        else:raise ValueError('unsupported_single_mock_call')
        message={'role':'assistant','content':None if purpose else json.dumps(value,ensure_ascii=False)}
        if purpose:message['tool_calls']=[{'id':'mock','type':'function','function':{'name':purpose,'arguments':json.dumps(value,ensure_ascii=False)}}]
        return httpx.Response(200,json={'id':'mock','object':'chat.completion','created':1,'model':model,
            'choices':[{'index':0,'finish_reason':'tool_calls' if purpose else 'stop','message':message}],
            'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150}},request=request)
    real=httpx.AsyncHTTPTransport(retries=0)
    original_real=real.handle_async_request
    class Owned(httpx.AsyncBaseTransport):
        async def handle_async_request(self,request):return await original_real(request)
        async def aclose(self):await real.aclose()
    underlying=httpx.MockTransport(simulated) if args.mode=='mock' else Owned()
    protected=budget_transport(ledger,model=model,endpoint=endpoint,underlying=underlying,on_event=record)
    class Boundary(httpx.AsyncBaseTransport):
        async def handle_async_request(self,request):
            require_authorization(plan,json.loads((EVAL/'authorization.json').read_text()))
            require_continuation_authorization(json.loads((CONT/'authorization.json').read_text()),proposal)
            if fatal:raise ValueError('previous_failure_blocks_further_dispatch')
            if digest(old_rows())!=proposal['old_nine_rows_sha256']:raise ValueError('old_rows_changed')
            if args.mode=='live':
                binding=json.loads((CONT/'execution-binding.json').read_text())
                if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=binding['executor_sha256']:
                    raise ValueError('executor_changed')
            turn=turn_ref[0]
            if turn not in {t['turn_id'] for t in case['turns']}:raise ValueError('unbound_turn')
            counts[turn]=counts.get(turn,0)+1
            if counts[turn]>6:mark_failure('per_turn_http_cap');raise ValueError('per_turn_http_cap')
            operation=case['case_id']+'/'+turn+'/http-'+str(counts[turn])
            if args.mode=='mock' and args.variant.startswith('multi'):
                operation='MOCK/'+args.variant+'/'+turn+'/http-'+str(counts[turn])
            body=await request.aread()
            expected=proposal['cached_replay_only_exact_body_hashes'].get(operation)
            if expected:
                if hashlib.sha256(body).hexdigest()!=expected:
                    mark_failure('carried_input_not_equivalent');raise ValueError('carried_input_not_equivalent')
                row=replay[operation]
                record('carried_response',{'operation':operation,'body_sha256':expected,'new_provider_dispatch':False})
                carried.append(operation)
                return httpx.Response(row['http_status'],json=row['response'],request=request)
            if ledger.snapshot()['requests']-9>=148:
                mark_failure('continuation_http_cap');raise ValueError('continuation_http_cap')
            if ledger.snapshot()['usage_unknown_requests']:
                mark_failure('previous_unknown_usage');raise ValueError('previous_unknown_usage')
            token=OPERATION.set(operation)
            try:return await protected.handle_async_request(request)
            finally:OPERATION.reset(token)
        async def aclose(self):await protected.aclose()
    async def block_async(*a,**kw):raise RuntimeError('unguarded_async_http_forbidden')
    def block_sync(*a,**kw):raise RuntimeError('unguarded_sync_http_forbidden')
    httpx.AsyncHTTPTransport.handle_async_request=block_async
    httpx.HTTPTransport.handle_request=block_sync
    def install(service,patch):
        service.MAX_RETRIES=0;service.local_model=None
        service.models=[ModelConfig('primary',api_key=settings.LLM_API_KEY,api_base=endpoint,model=model,
            thinking_mode=settings.LLM_THINKING_MODE,structured_output_method=settings.LLM_STRUCTURED_OUTPUT_METHOD)]
        original=service._get_chat_model
        client=httpx.AsyncClient(transport=Boundary(),trust_env=False,timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS)
        service._http_clients.append(client)
        decorated=set()
        def wrapped(config,**kwargs):
            target=original(config,**kwargs)
            if kwargs.get('streaming'):
                target.stream_usage=True
            if id(target) not in decorated:
                sdk=AsyncOpenAI(api_key=config.api_key,base_url=config.api_base,http_client=client,max_retries=0)
                target.root_async_client,target.async_client=sdk,sdk.chat.completions
                decorated.add(id(target))
            return target
        patch.setattr(service,'_get_chat_model',wrapped)
        structured=service.run_structured
        async def checked(*args,**kwargs):
            result=await structured(*args,**kwargs)
            record('structured_result',{'purpose':kwargs.get('tags'),'schema':args[2].__name__,'valid':result is not None})
            if result is None:mark_failure('structured_output_unavailable')
            return result
        patch.setattr(service,'run_structured',checked)
        import app.agent.request_understanding as understanding_module
        original_semantics=understanding_module.validate_semantics
        def checked_semantics(*args,**kwargs):
            try:return original_semantics(*args,**kwargs)
            except ValueError as exc:
                mark_failure('understanding_semantics_rejected:'+str(exc))
                raise
        patch.setattr(understanding_module,'validate_semantics',checked_semantics)
        import app.agent.nodes.response_node as response_module
        import app.agent.answer_publication as publication_module
        for module,name in ((response_module,'validate_candidate'),(publication_module,'publish_answer')):
            original_review=getattr(module,name)
            def wrap_review(original_review,name):
                async def observed(*args,**kwargs):
                    result=await original_review(*args,**kwargs)
                    state=args[1]
                    save('turns/'+turn_ref[0]+'/'+name+'-state.json',{'reply':state.reply,
                        'review_result':state.review_result.model_dump() if state.review_result else None,
                        'llm_judge':state.collected_info.get('llm_judge'),'validation':state.collected_info.get('validation'),
                        'task':state.task.model_dump() if state.task else None,
                        'evidence':state.current_evidence()})
                    return result
                return observed
            patch.setattr(module,name,wrap_review(original_review,name))
        original_close=service.close
        async def close():
            try:await original_close()
            except BaseException as exc:mark_failure('model_cleanup_error:'+type(exc).__name__)
        patch.setattr(service,'close',close)
    rows=[];error=None;status='AWAITING_SEPARATE_TEXT_REVIEW'
    try:
        work=directory/'isolated';work.mkdir()
        with api_case(case,work,sources,install_model=install) as (send,records):
            for turn in case['turns']:
                turn_ref[0]=turn['turn_id']
                row=send(turn);rows.append(row);save('api-receipts.json',rows)
                if case['same_request_replay']:
                    before=ledger.snapshot()['requests'];again=send(turn,replay=True)
                    row['replay_verified']=again['payload'].get('run_id')==row['payload'].get('run_id') and ledger.snapshot()['requests']==before
                    save('api-receipts.json',rows)
                    if not row['replay_verified']:raise ValueError('replay_has_new_run_or_dispatch')
                if fatal:raise ValueError('provider_or_protocol_failure')
                if ledger.snapshot()['usage_unknown_requests']:raise ValueError('unknown_usage')
                actual_stage=row['payload'].get('task_status') or (row['task'] or {}).get('status')
                if actual_stage not in turn['expected_task_states']:raise ValueError('unexpected_task_stage:'+str(actual_stage))
                if row['effects']['simulated_applications']!=turn['max_simulated_applications']:raise ValueError('unexpected_side_effect_count')
        if fatal:raise ValueError('cleanup_failed')
    except BaseException as exc:
        status='STOPPED';error=type(exc).__name__+':'+str(exc)
        (CONT/'STOP.json').write_text(json.dumps({'case_id':case['case_id'],'reason':error}))
    finally:
        report={'source_fingerprint':os.environ.get('AICSHUB_EVAL_SOURCE_FINGERPRINT'),
            'execution_hashes':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['scripts/business_http_boundary.py','scripts/business_acceptance_execute.py','scripts/continue_business_acceptance.py']},
            'mode':args.mode,'case_id':case['case_id'],'variant':args.variant,'status':status,'error':error,
            'budget':ledger.snapshot(),'new_dispatches':ledger.snapshot()['requests']-baseline,
            'carried_responses':carried,'complete_turns':len(rows),'expected_turns':len(case['turns']),
            'scope':'frozen app image; real API/SQL; frozen published evidence; substituted Redis; not production capacity',
            'semantic_review':'NOT_RUN','paid_new':ledger.snapshot()['requests']-baseline if args.mode=='live' else 0}
        save('report.json',report)
        print('CASE_RESULT='+json.dumps(report))
    return 0 if status=='AWAITING_SEPARATE_TEXT_REVIEW' else 2


if __name__=='__main__':raise SystemExit(main())
