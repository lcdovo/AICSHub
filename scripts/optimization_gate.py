"""Step-one fixed live semantic gate; no deployment/performance run before admission."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.refund_language_acceptance import PHASE,material,validate_bundle,read,save,sha,protected_ledgers
from scripts.business_acceptance import digest,runtime_stamp
from scripts.continue_business_acceptance import rows
from scripts.test_provenance import snapshot_sources
OUT=ROOT/'reports/optimization-gate-v1'
STAGE=PHASE/'optimization-gate-v1'
DATA=ROOT/'acceptance/optimization-gates-v1/cases.json'


def auth(value,plan):
    if (value.get('confirmed') is not True or value.get('approved_by')!='user'
            or value.get('plan_id')!=plan['plan_id'] or value.get('max_new')!=8
            or not value.get('user_statement') or plan['max_new']!=8
            or digest({k:v for k,v in plan.items() if k!='plan_id'})!=plan['plan_id']):
        raise ValueError('fixed_step_one_authorization_required')


def prepare():
    if STAGE.exists() or OUT.exists():raise ValueError('existing_plan_no_reset')
    old_plan=read(PHASE/'plan.json');runtime=runtime_stamp()
    if runtime!=old_plan['runtime']:raise ValueError('runtime_drift')
    with sqlite3.connect(PHASE/'dispatch.sqlite') as db:
        old=[r[0] for r in db.execute('SELECT operation FROM dispatches ORDER BY operation')]
    if len(old)!=91:raise ValueError('expected91')
    snap=snapshot_sources(ROOT);files=material(snap);OUT.mkdir();bundle=OUT/'bundle';bundle.mkdir()
    for name,expected in files.items():
        source=ROOT/name
        if source.is_symlink() or sha(source)!=expected:raise ValueError('source_changed')
        target=bundle/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    validate_bundle(bundle,files)
    cases=read(DATA)['cases'];allowed={r:2048 for r in old};allowed.update({'OPT1/'+c['case_id']:2048 for c in cases})
    stops=['STOP.json','continuation-v1/STOP.json','refund-scope-v2/STOP.json','language-v1/STOP.json','language-v2/STOP.json','language-v3/STOP.json']
    plan={'baseline':91,'max_new':8,'max_input_bytes':8*24576,'max_output_tokens':8*2048,
          'runtime':runtime,'old_operations':old,'old_rows_sha256':digest(rows(PHASE/'dispatch.sqlite',old)),
          'old_ledgers':protected_ledgers(),'old_stops':{p:sha(PHASE/p) for p in stops},'allowed':allowed,
          'data_sha256':sha(DATA),'source_fingerprint':snap['fingerprint'],'bundle':files,
          'stop_on_first_failure':True,'no_retry':True,'scope':'real model Judge components; synthetic evidence; not API E2E or retrieval capacity',
          'next_steps':'deployment and P95 strictly gated on independent semantic review; not run on failed step1'}
    plan['plan_id']=digest(plan);save(STAGE/'plan.json',plan)
    save(STAGE/'authorization.json',{'confirmed':True,'approved_by':'user','user_statement':'逐个进行；沿用此前授权所需额度','max_new':8,'plan_id':plan['plan_id']})
    print('PREPARED='+plan['plan_id'])


async def execute():
    root=Path('/evaluation');stage=root/'optimization-gate-v1';plan=read(stage/'plan.json')
    auth(read(stage/'authorization.json'),plan)
    from scripts.business_http_boundary import BudgetLedger,budget_transport
    from scripts.m5_live_budget import OPERATION
    original=read(root/'plan.json')
    ledger=BudgetLedger(root/'dispatch.sqlite',original['prepared_id'],
      {'max_requests':168,'max_input_tokens':original['max_input_bytes_reserved'],'max_output_tokens':original['max_output_tokens']},plan['allowed'])
    if ledger.snapshot()['requests']!=91 or (stage/'STOP.json').exists():raise ValueError('no_retry_or_account_drift')
    if sha(ROOT/'acceptance/optimization-gates-v1/cases.json')!=plan['data_sha256']:raise ValueError('material_changed')
    import httpx
    from openai import AsyncOpenAI
    from app.services.llm_service import LLMService,ModelConfig
    from app.agent.validation_llm import LLMRAGJudge
    from app.agent.critical_facts import critical_fact_issues
    from app.config.config import settings
    service=LLMService();events=[];fatal=[]
    def record(kind,value):
        if kind=='failure':fatal.append(value)
        events.append({'kind':kind,**value});save(stage/'provider-events.json',events)
    raw=httpx.AsyncHTTPTransport(retries=0)
    protected=budget_transport(ledger,model=settings.LLM_MODEL,endpoint=settings.LLM_API_BASE,underlying=raw,on_event=record)
    class Bound(httpx.AsyncBaseTransport):
        async def handle_async_request(self,request):
            auth(read(stage/'authorization.json'),plan)
            if fatal or (stage/'STOP.json').exists():raise ValueError('previous_failure_stopped')
            if digest(rows(root/'dispatch.sqlite',plan['old_operations']))!=plan['old_rows_sha256']:raise ValueError('old_rows_changed')
            if ledger.snapshot()['requests']-91>=8:raise ValueError('step_budget_exhausted')
            return await protected.handle_async_request(request)
        async def aclose(self):await protected.aclose()
    client=httpx.AsyncClient(transport=Bound(),trust_env=False,timeout=30)
    service._http_clients.append(client)
    service.models=[ModelConfig('primary',api_key=settings.LLM_API_KEY,api_base=settings.LLM_API_BASE,
        model=settings.LLM_MODEL,thinking_mode=settings.LLM_THINKING_MODE,structured_output_method=settings.LLM_STRUCTURED_OUTPUT_METHOD)]
    original_model=service._get_chat_model
    def model(config,**kw):
        target=original_model(config,**kw);sdk=AsyncOpenAI(api_key=config.api_key,base_url=config.api_base,http_client=client,max_retries=0)
        target.root_async_client,target.async_client=sdk,sdk.chat.completions;return target
    service._get_chat_model=model
    results=[];status='RUNNING';error=None
    try:
        for case in read(ROOT/'acceptance/optimization-gates-v1/cases.json')['cases']:
            token=OPERATION.set('OPT1/'+case['case_id'])
            try:verdict=await LLMRAGJudge(service).judge(case['candidate'],case['question'],case['sources'],'')
            finally:OPERATION.reset(token)
            issues=critical_fact_issues(case['candidate'],[{'success':True,'data':{'order_id':'ORD950099','status_code':'delivered','total_amount':'0.10','currency':'CNY'}}] if case['case_id'] in {'OG-01','OG-02','OG-04'} else [])
            result={'case_id':case['case_id'],'verdict':verdict,'deterministic_issues':issues,'expected_publish':case['expected_publish']}
            results.append(result);save(stage/'results.json',results)
            if (fatal or ledger.snapshot()['usage_unknown_requests'] or verdict.get('response_validity')=='unknown'
                    or verdict.get('support')=='unknown' and not case['expected_publish']
                    or verdict.get('publish_allowed') is not case['expected_publish']
                    or case['expected_publish'] and issues):
                status='STOPPED';error='semantic_or_protocol_failure:'+case['case_id'];save(stage/'STOP.json',result);break
        else:status='AWAITING_SEPARATE_REVIEW'
    except BaseException as exc:
        status='STOPPED';error=type(exc).__name__;save(stage/'STOP.json',{'error':error})
    finally:
        await service.close()
        report={'status':status,'error':error,'budget':ledger.snapshot(),'new_http':ledger.snapshot()['requests']-91,'results':len(results),
                'source_fingerprint':plan['source_fingerprint'],'scope':plan['scope'],'deployment':'NOT_RUN','performance':'NOT_RUN'}
        save(stage/'closeout.json',report);print(json.dumps(report))
    return 0 if status=='AWAITING_SEPARATE_REVIEW' else 2


def live():
    plan=read(STAGE/'plan.json');auth(read(STAGE/'authorization.json'),plan)
    if snapshot_sources(ROOT)['fingerprint']!=plan['source_fingerprint'] or runtime_stamp()!=plan['runtime']:raise ValueError('source_or_runtime_drift')
    validate_bundle(OUT/'bundle',plan['bundle'])
    if protected_ledgers()!=plan['old_ledgers']:raise ValueError('old_ledgers_changed')
    if (STAGE/'STOP.json').exists():raise ValueError('stopped')
    binding=read(STAGE/'offline-binding.json')
    for role,entry in binding.items():
        path=ROOT/entry['path'];m=read(path)
        if sha(path)!=entry['sha256'] or m['exit_code']!=0 or not m['source_tree_stable'] or m['blocked_network_operations']!=0:raise ValueError('offline_not_admitted')
        snap=read(path.parent/'source-start.json')
        for prefix in ['app/','scripts/','tests/','acceptance/']:
            if {k:v for k,v in snap['files'].items() if k.startswith(prefix)}!={k:v for k,v in plan['bundle'].items() if k.startswith(prefix)}:raise ValueError('offline_source_changed')
    if set(binding)!={'host','image'}:raise ValueError('both_offline_roles_required')
    nonce=uuid.uuid4().hex;private=OUT/'private';private.mkdir(exist_ok=True);envpath=private/(nonce+'.env');container='aicshub-opt-gate-'+nonce
    with (STAGE/'running.lock').open('x') as stream:stream.write(nonce)
    key=None
    try:
        from dotenv import get_key
        key=get_key(ROOT/'.env','LLM_API_KEY')
        if not key:raise ValueError('missing_key')
        env={**plan['runtime']['provider'],'LLM_API_KEY':key,'LLM_GATEWAY_ENABLED':'false','LLM_JUDGE_ENABLED':'true',
             'LLM_MAX_RETRIES':'0','LLM_MAX_TOKENS':'2048','LLM_PROMPT_VERSION':'optimization.gate.v1',
             'LLM_LOCAL_BASE':'','LLM_LOCAL_MODEL':'','LLM_BACKUP_BASE':'','LLM_BACKUP_MODEL':'',
             'LANGFUSE_ENABLED':'false','PYTHONPATH':'/candidate:/app','PYTHONIOENCODING':'utf-8'}
        if any('\n' in str(v) or '\r' in str(v) for v in env.values()):raise ValueError('multiline_env')
        envpath.write_text('\n'.join(k+'='+str(v) for k,v in env.items()),encoding='utf-8')
        cmd=['docker','run','--rm','--name',container,'--label','aicshub.eval.owner='+nonce,'--network','bridge','--user','root','--workdir','/tmp','--entrypoint','python','--env-file',str(envpath),
             '--mount',f'type=bind,source={OUT/"bundle"},target=/candidate,readonly','--mount',f'type=bind,source={PHASE},target=/evaluation',plan['runtime']['image'],'/candidate/scripts/optimization_gate.py','--execute']
        result=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',timeout=180)
        (STAGE/'container.log').write_text((result.stdout+result.stderr).replace(key,'<REDACTED>'),encoding='utf-8')
        print(result.stdout[-1800:]);return result.returncode
    finally:
        found=subprocess.run(['docker','inspect','--format','{{index .Config.Labels "aicshub.eval.owner"}}',container],capture_output=True,text=True)
        if found.returncode==0 and found.stdout.strip()==nonce:subprocess.run(['docker','rm','-f',container],capture_output=True,check=True)
        envpath.unlink(missing_ok=True);(STAGE/'running.lock').unlink(missing_ok=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--prepare',action='store_true');parser.add_argument('--execute',action='store_true');parser.add_argument('--live',action='store_true');args=parser.parse_args()
    if args.prepare:prepare()
    elif args.execute:raise SystemExit(asyncio.run(execute()))
    elif args.live:raise SystemExit(live())
