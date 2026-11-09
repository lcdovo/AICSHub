"""Fixed, source-bound real-provider acceptance using the original business account."""
from __future__ import annotations
import argparse
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
from scripts.business_acceptance import digest,runtime_stamp,protected_ledgers as historical_ledgers
from scripts.continue_business_acceptance import rows
from scripts.test_provenance import snapshot_sources

PHASE=ROOT/'reports/business-acceptance-v1/live/1534678f1c67f74b780c83ba6233b0bce045d3f00205417b15311f06edfcb042'
STAGE=PHASE/'language-v4'
OUT=ROOT/'reports/refund-language-live-v4'
DATA=ROOT/'acceptance/refund-language-v1'
TESTS=['tests/test_refund_language_contract.py','tests/test_refund_language_acceptance.py','tests/test_task_policy_projection.py','tests/test_business_evidence_preflight.py','tests/test_agent_task_loop.py','tests/test_m4_confirmation_binding.py','tests/test_m4_refund_preview.py','tests/test_m4_refund_action_view.py','tests/test_refund_persistence.py','tests/test_m1_refund_actions.py','tests/test_m2_runs.py','tests/test_agent_reliability.py','tests/test_chat_turns.py','tests/test_business_http_boundary.py','tests/test_item_understanding_wire.py','tests/test_refund_read_plan_scope.py','tests/test_active_runtime.py','tests/test_intent_items.py','tests/test_m4_read_plan.py','tests/test_conditional_understanding.py','tests/test_refund_change_read.py']


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def load_cases():return read(DATA/'cases.json')['cases'],read(DATA/'sources.json')


def protected_ledgers():
    # Only this newly-created evaluation output owns disposable mock ledger copies.
    # Every historical account outside it retains the original hash protection.
    own_prefixes=('reports/refund-language-live-v1/','reports/refund-language-live-v2/','reports/refund-language-live-v3/','reports/refund-language-live-v4/')
    return {name:value for name,value in historical_ledgers().items() if not name.startswith(own_prefixes)}


def verify_publication(sources):
    code="""from app.repositories.knowledge_repository import KnowledgeRepository
import json,hashlib
r=KnowledgeRepository()
try:
 e,items=r.snapshot()
 print(json.dumps({'epoch':e,'items':{i['id']:{'version_id':i['version_id'],'sha256':hashlib.sha256(i['content'].encode()).hexdigest()} for i in items}}))
finally:r.engine.dispose()
"""
    result=subprocess.run(['docker','exec','ai-customer-app-1','python','-c',code],capture_output=True,text=True,encoding='utf-8',check=True)
    current=json.loads(result.stdout)
    if current['epoch']!=sources['epoch'] or any(current['items'].get(i['id'])!={'version_id':i['version_id'],'sha256':i['sha256']} for i in sources['items']):
        raise ValueError('frozen_published_policy_changed')


def require_auth(auth,proposal):
    if digest({k:v for k,v in proposal.items() if k!='continuation_id'})!=proposal['continuation_id']:
        raise ValueError('fixed_language_plan_changed')
    if (auth.get('confirmed') is not True or auth.get('approved_by')!='user'
            or not auth.get('user_statement') or auth.get('continuation_id')!=proposal['continuation_id']
            or auth.get('max_new_dispatches')!=96 or proposal['max_new_dispatches']!=96
            or proposal['baseline_requests']!=25 or auth.get('stream_with_usage_footer_authorized') is not True):
        raise ValueError('bounded_language_authorization_required')


def check_turn(case,turn,row):
    if row['payload'].get('task_status') not in turn['expected_task_states']:
        raise ValueError('unexpected_task_stage:'+str(row['payload'].get('task_status')))
    if row['effects']!={'simulated_applications':turn['applications'],'preview_count':turn['previews']}:
        raise ValueError('unexpected_side_effects')
    if turn.get('waiting_for') and (row.get('task') or {}).get('waiting_for')!=turn['waiting_for']:
        raise ValueError('unexpected_waiting_goal')


def positive_understanding(case,turn_id):
    turn=next(t for t in case['turns'] if t['turn_id']==turn_id)
    short=turn_id=='t1' and case['name'].startswith('short_')
    return {'topic':'refund','request_type':'apply','entities':{'order_id':case['orders'][0]['order_id'],'product':None},
        'conditions':[],'subrequests':[],'service_preference':'unspecified','emotion':'neutral','clarification':None,
        'retrieval_query':case['orders'][0]['order_id']+' 退换货条件','entity_source':'current_message' if turn_id=='t1' else 'history',
        'itemization_version':'intent-items.v1','items':[{'text':turn['message'],'kind':'prepare_refund'}],
        'missing_information':[],'parameters':{'reason':'外壳破损' if short else '收到时商品外壳已经破损',
        'refund_type':case['refund_type'],'complaint_content':None},'continue_task':turn_id!='t1'}


def negative_understanding(case):
    if case['name']=='change_order':
        return read(ROOT/'tests/fixtures/refund-change-read-wire.json')
    value=positive_understanding(case,'t2');value.update(request_type='clarify',continue_task=False,
        clarification='请明确本轮希望处理的操作；当前没有提交申请。')
    value['items']=[{'text':case['turns'][1]['message'],'kind':'unsupported'}]
    if case['name']=='change_order':
        value['entities']['order_id']='ORD981099';value['entity_source']='current_message'
    return value


def material(snapshot):
    return {k:v for k,v in snapshot['files'].items() if k.startswith(('app/','scripts/','tests/','acceptance/','alembic/'))
            or k in {'pytest.ini','requirements.txt','requirements.lock','alembic.ini'}}


def validate_bundle(bundle,expected):
    actual={p.relative_to(bundle).as_posix():sha(p) for p in bundle.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    if actual!=expected or any(p.is_symlink() for p in bundle.rglob('*')):raise ValueError('frozen_candidate_changed')


def verify(proposal,live=False):
    require_auth(read(STAGE/'authorization.json'),proposal)
    if snapshot_sources(ROOT)['fingerprint']!=proposal['source_fingerprint']:raise ValueError('candidate_source_changed')
    if runtime_stamp()!=proposal['runtime']:raise ValueError('business_runtime_changed')
    if protected_ledgers()!=proposal['protected_ledgers']:raise ValueError('old_ledgers_changed')
    if digest(rows(PHASE/'dispatch.sqlite',proposal['old_operations']))!=proposal['old_rows_sha256']:
        raise ValueError('original_twenty_five_rows_changed')
    if any(sha(ROOT/p)!=v for p,v in proposal['materials'].items()):raise ValueError('language_material_changed')
    if any(sha(PHASE/p)!=v for p,v in proposal['old_stops'].items()):raise ValueError('old_stop_changed')
    bundle=OUT/'bundle';validate_bundle(bundle,proposal['bundle_files'])
    if live:verify_publication(load_cases()[1])
    if live and (STAGE/'STOP.json').exists():raise ValueError('language_stage_stopped')


def prepare():
    if STAGE.exists():raise ValueError('stage_already_exists_no_reset')
    cases,sources=load_cases()
    if len(cases)!=7 or sum(len(c['turns']) for c in cases)!=16:raise ValueError('fixed_catalog_changed')
    for item in sources['items']:
        if hashlib.sha256(item['content'].encode()).hexdigest()!=item['sha256']:raise ValueError('source_hash_changed')
    verify_publication(sources)
    original=read(PHASE/'plan.json');runtime=runtime_stamp()
    if runtime!=original['runtime']:raise ValueError('deployed_baseline_changed')
    with sqlite3.connect(PHASE/'dispatch.sqlite') as db:
        old=[r[0] for r in db.execute('SELECT operation FROM dispatches ORDER BY operation')]
        unknown=db.execute('SELECT count(*) FROM dispatches WHERE input_actual IS NULL OR output_actual IS NULL').fetchone()[0]
    if len(old)!=67 or unknown:raise ValueError('expected_sixty_seven_known_rows')
    previous=read(PHASE/'language-v1/proposal.json')
    if digest(rows(PHASE/'dispatch.sqlite',previous['old_operations']))!=previous['old_rows_sha256']:
        raise ValueError('original_twenty_five_changed')
    if protected_ledgers()!=previous['protected_ledgers']:raise ValueError('original33_changed')
    snapshot=snapshot_sources(ROOT);files=material(snapshot)
    if snapshot['unsupported_paths']:raise ValueError('unsupported_source')
    OUT.mkdir(exist_ok=True);stage=OUT/('building-'+uuid.uuid4().hex);stage.mkdir()
    for name,expected in files.items():
        source=ROOT/name
        if source.is_symlink() or sha(source)!=expected:raise ValueError('source_changed_during_freeze')
        target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    validate_bundle(stage,files);stage.rename(OUT/'bundle')
    allowed={r:2048 for r in old}
    allowed.update({'LANG4/'+c['case_id']+'/'+t['turn_id']+'/http-'+str(i):2048
                    for c in cases for t in c['turns'] for i in range(1,7)})
    stops=['STOP.json','continuation-v1/STOP.json','refund-scope-v2/STOP.json','language-v1/STOP.json','language-v2/STOP.json','language-v3/STOP.json']
    proposal={'baseline_requests':25,'max_new_dispatches':96,'operation_prefix':'LANG4','cases':[c['case_id'] for c in cases],
       'old_operations':old,'old_rows_sha256':digest(rows(PHASE/'dispatch.sqlite',old)),
       'allowed_operations':allowed,'cached_replay_only_exact_body_hashes':{},
       'source_fingerprint':snapshot['fingerprint'],'bundle_files':files,
       'candidate_app_hashes':{k:v for k,v in files.items() if k.startswith('app/')},
       'runtime':runtime,'protected_ledgers':protected_ledgers(),'old_stops':{p:sha(PHASE/p) for p in stops},
       'materials':{p.relative_to(ROOT).as_posix():sha(p) for p in DATA.glob('*.json')},
       'stop_on_first_failure':True,'automatic_retry':False,'json_repair':False,'deployment':False,
       'versions':{'REQUEST_UNDERSTANDING_VERSION':'understanding.item-read-plan.v2','LLM_PROMPT_VERSION':'refund-language.readonly-v4'},
       'scope':'frozen full worktree candidate, isolated synthetic SQL/REST/WS; frozen published policies, substituted Redis; not deployed image/capacity'}
    proposal['continuation_id']=digest(proposal)
    save(STAGE/'proposal.json',proposal)
    save(STAGE/'authorization.json',{'confirmed':True,'approved_by':'user',
        'user_statement':'进行吧，有错误记得先修复，授权所有需要的额度','max_new_dispatches':96,
        'continuation_id':proposal['continuation_id'],'stream_with_usage_footer_authorized':True,
        'scope':'fixed seven cases in original168 account; first failure stops dispatch, offline repair before a reviewed source revision; no raw retry or unlimited quota'})
    print('PREPARED='+proposal['continuation_id'])


def admission(proposal):
    binding=read(STAGE/'execution-binding.json')
    if binding['source_fingerprint']!=proposal['source_fingerprint'] or binding['executor_sha256']!=sha(ROOT/'scripts/refund_language_execute.py'):
        raise ValueError('admission_source_changed')
    roles=set();seen=set()
    for item in binding['evidence']:
        path=(ROOT/item['path']).resolve()
        if not path.is_relative_to((ROOT/'reports').resolve()) or sha(path)!=item['sha256']:raise ValueError('admission_evidence_changed')
        value=read(path)
        if item['role'] in {'host','image'}:
            if value.get('exit_code')!=0 or not value.get('source_tree_stable') or not value.get('source_snapshot_complete') or value.get('blocked_network_operations')!=0:
                raise ValueError('offline_admission_failed')
            snapshot=read(path.parent/'source-start.json')
            for prefix in ['app/','scripts/','tests/','acceptance/']:
                if {k:v for k,v in snapshot['files'].items() if k.startswith(prefix)}!={k:v for k,v in proposal['bundle_files'].items() if k.startswith(prefix)}:
                    raise ValueError('offline_source_mismatch')
            if item['role']=='image' and value['image_id']!=proposal['runtime']['image']:raise ValueError('wrong_image')
            roles.add(item['role'])
        elif item['role']=='mock':
            if (value['mode']!='mock' or value['paid_new']!=0 or value['error'] is not None
                    or value['status']!='AWAITING_SEPARATE_TEXT_REVIEW' or value['case_id'] in seen
                    or value['source_fingerprint']!=proposal['source_fingerprint']):raise ValueError('sdk_admission_failed')
            case=next(c for c in load_cases()[0] if c['case_id']==value['case_id'])
            receipts=read(path.parent/'api-receipts.json')
            if len(receipts)!=len(case['turns']):raise ValueError('mock_turn_missing')
            for turn,row in zip(case['turns'],receipts):check_turn(case,turn,row)
            seen.add(case['case_id'])
    if roles!={'host','image'} or seen!=set(proposal['cases']):raise ValueError('complete_admission_required')


def run(mode,case_id):
    proposal=read(STAGE/'proposal.json');verify(proposal,live=mode=='live')
    cases,_=load_cases();case=next(c for c in cases if c['case_id']==case_id)
    if mode=='live':
        admission(proposal)
        for prior in cases[:cases.index(case)]:
            review=read(STAGE/'cases'/prior['case_id']/'separate-review.json')
            raw=read(STAGE/'cases'/prior['case_id']/'api-receipts.json')
            if review.get('passed') is not True or review.get('receipt_sha256')!=digest(raw):raise ValueError('prior_separate_review_missing')
        if (STAGE/'cases'/case_id).exists():raise ValueError('case_already_started_no_retry')
        evaluation=PHASE
    else:
        evaluation=OUT/('mock-'+case_id+'-'+uuid.uuid4().hex[:8]);evaluation.mkdir()
        for name in ['dispatch.sqlite','dispatch.created.json','plan.json','authorization.json']:shutil.copy2(PHASE/name,evaluation/name)
        for name in ['proposal.json','authorization.json']:save(evaluation/STAGE.name/name,read(STAGE/name))
        (evaluation/'cases/BA-04').mkdir(parents=True)
        shutil.copy2(PHASE/'cases/BA-04/provider-responses.json',evaluation/'cases/BA-04/provider-responses.json')
    original=read(PHASE/'plan.json')
    from scripts.offline_profiles import ACTIVE_CONFIG
    env={**original['runtime']['provider'],**ACTIVE_CONFIG,**proposal['versions'],
       'DATABASE_URL_OVERRIDE':'sqlite+aiosqlite:////tmp/language-evaluation.db',
       'PYTHONPATH':'/candidate:/app','PYTHONIOENCODING':'utf-8','REDIS_ENABLED':'false','USE_MILVUS':'false',
       'AUTH_DEMO_ACCOUNTS_ENABLED':'false','USER_MEMORY_ENABLED':'false','LANGFUSE_ENABLED':'false','MCP_ENABLED':'false',
       'LLM_BACKUP_BASE':'','LLM_BACKUP_MODEL':'','LLM_LOCAL_BASE':'','LLM_LOCAL_MODEL':'',
       'LLM_REQUEST_TIMEOUT_SECONDS':'30','LLM_DAILY_USER_TOKEN_BUDGET':str(original['max_input_bytes_reserved']+original['max_output_tokens']),
       'LLM_DAILY_USER_COST_USD':'5.6','LLM_MAX_COST_USD':'0.2','LLM_INPUT_PRICE_PER_1K_USD':'0.002',
       'LLM_OUTPUT_PRICE_PER_1K_USD':'0.008','LLM_PRICE_VERIFIED':'false','AICSHUB_EVAL_SOURCE_FINGERPRINT':proposal['source_fingerprint'],'AICSHUB_LANGUAGE_STAGE':STAGE.name}
    private=OUT/'private';private.mkdir(exist_ok=True)
    nonce=uuid.uuid4().hex;envpath=private/(nonce+'.env');container='aicshub-language-'+nonce
    lock=STAGE/'running.lock';key=None
    if mode=='live':
        with lock.open('x') as stream:stream.write(nonce)
    try:
        if mode=='live':
            from dotenv import get_key
            key=get_key(ROOT/'.env','LLM_API_KEY')
            if not key:raise ValueError('missing_provider_key')
        env['LLM_API_KEY']=key or 'synthetic-only'
        if any('\n' in str(v) or '\r' in str(v) for v in env.values()):raise ValueError('invalid_environment')
        envpath.write_text('\n'.join(k+'='+str(v) for k,v in env.items())+'\n',encoding='utf-8')
        code="""import subprocess,sys,os
subprocess.run([sys.executable,'-m','pip','install','--no-index','--find-links=/wheels','aiosqlite==0.22.1'],check=True)
os.execv(sys.executable,[sys.executable,'/candidate/scripts/refund_language_execute.py','--mode',sys.argv[1],'--case',sys.argv[2]])
"""
        cmd=['docker','run','--rm','--name',container,'--label','aicshub.eval.owner='+nonce,'--network','none' if mode=='mock' else 'bridge',
           '--user','root','--workdir','/tmp','--entrypoint','python','--env-file',str(envpath),
           '--mount',f'type=bind,source={OUT/"bundle"},target=/candidate,readonly',
           '--mount',f'type=bind,source={evaluation},target=/evaluation',
           '--mount',f'type=bind,source={ROOT/"reports/offline-wheel-cache"},target=/wheels,readonly',
           proposal['runtime']['image'],'-c',code,mode,case_id]
        result=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',timeout=240)
        output=result.stdout+result.stderr
        if key:output=output.replace(key,'<REDACTED>')
        (evaluation/STAGE.name/('container-'+case_id+'.log')).write_text(output,encoding='utf-8')
        for line in result.stdout.splitlines():
            if line.startswith('CASE_RESULT='):print(line)
        if result.returncode and not (evaluation/STAGE.name/'cases'/case_id/'report.json').exists():raise RuntimeError('container_setup_failed')
        print('EVALUATION_ROOT='+str(evaluation));return result.returncode
    except BaseException as exc:
        if mode=='live':save(STAGE/'STOP.json',{'case_id':case_id,'reason':'host_failure:'+type(exc).__name__})
        raise
    finally:
        found=subprocess.run(['docker','inspect','--format','{{index .Config.Labels "aicshub.eval.owner"}}',container],capture_output=True,text=True)
        if found.returncode==0 and found.stdout.strip()==nonce:subprocess.run(['docker','rm','-f',container],capture_output=True,check=True)
        envpath.unlink(missing_ok=True)
        if mode=='live' and lock.exists() and lock.read_text()==nonce:lock.unlink()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['prepare','mock','live'],required=True);parser.add_argument('--case',default='LC-01')
    args=parser.parse_args()
    if args.mode=='prepare':prepare()
    else:raise SystemExit(run(args.mode,args.case))
