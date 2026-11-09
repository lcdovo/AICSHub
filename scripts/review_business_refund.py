"""Reviewed refund material and bounded v3 continuation in the original account."""
from __future__ import annotations
import argparse
import ast
import base64
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import revalidate_business_refund as runner
from scripts.business_acceptance import digest, operations, protected_ledgers, runtime_stamp, score_turn
from scripts.business_evidence_preflight import require_refund_sources
from scripts.test_provenance import snapshot_sources

PHASE=runner.PHASE
REV=PHASE/'refund-reviewed-v3'
DATA=ROOT/'acceptance/business-v2'
read,save,sha=runner.read,runner.save,runner.sha
BASE_ADMISSION=runner.validate_admission


def load_reviewed():
    cases=read(DATA/'refund-cases-reviewed-v3.json')['cases']
    sources=read(DATA/'refund-sources-candidate.json')
    return cases,sources


def require_authorization(auth,proposal):
    if digest({k:v for k,v in proposal.items() if k!='continuation_id'})!=proposal['continuation_id']:
        raise ValueError('reviewed_proposal_changed')
    if (auth.get('confirmed') is not True or auth.get('approved_by')!='user'
            or auth.get('continuation_id')!=proposal['continuation_id']
            or auth.get('max_new_dispatches')!=21 or proposal['max_new_dispatches']!=21
            or proposal['baseline_requests']!=19 or proposal['cases']!=['BA-05','BA-10']
            or not auth.get('user_statement') or auth.get('stream_with_usage_footer_authorized') is not True):
        raise ValueError('fixed_reviewed_continuation_authorization_required')


def validate_admission(binding,proposal):
    BASE_ADMISSION(binding,proposal)
    seen=set()
    for entry in binding.get('refund_evidence',[]):
        path=(ROOT/entry['path']).resolve()
        if not path.is_relative_to((ROOT/'reports').resolve()) or sha(path)!=entry['sha256']:
            raise ValueError('refund_preflight_changed')
        value=read(path);case=value['case_id']
        if (value['mode']!='mock' or value['paid_new']!=0 or value['status']!='AWAITING_SEPARATE_TEXT_REVIEW'
                or value['error'] is not None or value['new_dispatches']!=3 or value['complete_turns']!=2
                or value['source_fingerprint']!=proposal['source_fingerprint'] or case in seen):
            raise ValueError('refund_preflight_not_admitted')
        receipts=read(path.parent/'api-receipts.json')
        expected=0 if case=='BA-05' else 1
        if (receipts[0]['payload']['task_status']!='waiting_confirmation'
                or receipts[0]['effects']!={'preview_count':1,'simulated_applications':0}
                or receipts[1]['effects']!={'preview_count':1,'simulated_applications':expected}):
            raise ValueError('refund_preflight_effects_invalid')
        seen.add(case)
    if seen!={'BA-05','BA-10'}:raise ValueError('both_refund_preflights_required')


def prior_review(case,phase,rev):
    directory=rev/'cases'/case['case_id']
    review=read(directory/'separate-review.json');raw=read(directory/'api-receipts.json')
    if (review.get('passed') is not True or review.get('case_id')!=case['case_id']
            or len(review['turns'])!=len(case['turns']) or len(raw)!=len(case['turns'])):
        raise ValueError('prior_refund_review_incomplete')
    _,sources=load_reviewed()
    for turn,entry,row in zip(case['turns'],review['turns'],raw):
        if entry['turn_id']!=turn['turn_id'] or entry['receipt']['reply']!=row['payload']['reply']:
            raise ValueError('prior_refund_review_unbound')
        if not score_turn(case,turn,entry['receipt'],sources=sources)['passed']:
            raise ValueError('prior_refund_review_failed')


def prepare():
    if REV.exists():raise ValueError('reviewed_revision_exists_no_reset')
    previous=read(PHASE/'refund-scope-v2/proposal.json')
    runtime=runtime_stamp()
    if runtime!=previous['runtime']:raise ValueError('business_baseline_changed')
    cases,sources=load_reviewed()
    require_refund_sources(cases,sources,read(DATA/'refund-source-review-candidate.json'))
    # The original frozen labels are retained: safe handoff cannot count as a successful preview.
    contract=read(DATA/'refund-contract-reviewed.json')
    if contract['live_cases']!=['BA-05','BA-10'] or contract['safe_handoff_is_preview_success'] is not False:
        raise ValueError('review_contract_invalid')
    with sqlite3.connect(PHASE/'dispatch.sqlite') as db:
        old=[r[0] for r in db.execute('SELECT operation FROM dispatches ORDER BY operation')]
        unknown=db.execute('SELECT count(*) FROM dispatches WHERE input_actual IS NULL OR output_actual IS NULL').fetchone()[0]
    if len(old)!=19 or unknown:raise ValueError('expected_nineteen_known_dispatches')
    # Recheck reviewed fragments against the current SQL publication, without touching its contents.
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
        raise ValueError('reviewed_publication_changed')
    result=subprocess.run(['docker','exec','ai-customer-app-1','python','-c',
        "import base64;from pathlib import Path;print(base64.b64encode(Path('app/agent/task_loop.py').read_bytes()).decode())"],capture_output=True,text=True,check=True)
    original=base64.b64decode(result.stdout.strip())
    import hashlib
    if hashlib.sha256(original).hexdigest()!=runtime['app_hashes']['app/agent/task_loop.py']:
        raise ValueError('deployed_task_source_changed')
    work=(ROOT/'app/agent/task_loop.py').read_text(encoding='utf-8')
    names={'DECISION_KNOWLEDGE_MAX_BYTES','project_decision_knowledge','decision_payload'}
    def selected(node):
        return (isinstance(node,ast.FunctionDef) and node.name in names) or (isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in names for t in node.targets))
    source_nodes=[n for n in ast.parse(work).body if selected(n)]
    replacement='\n\n'.join(ast.get_source_segment(work,n) for n in source_nodes)+'\n'
    old_tree=ast.parse(original.decode('utf-8'))
    target=next(n for n in old_tree.body if isinstance(n,ast.FunctionDef) and n.name=='decision_payload')
    lines=original.splitlines(keepends=True)
    patched=b''.join(lines[:target.lineno-1])+replacement.encode()+b''.join(lines[target.end_lineno:])
    new_tree=ast.parse(patched.decode('utf-8'))
    for tree in [old_tree,new_tree]:tree.body=[n for n in tree.body if not selected(n)]
    if ast.dump(old_tree)!=ast.dump(new_tree):raise ValueError('unrelated_task_overlay_change')
    REV.mkdir();(REV/'overlay').mkdir()
    (REV/'overlay/task_loop.py').write_bytes(patched)
    shutil.copy2(PHASE/'refund-scope-v2/overlay/request_understanding.py',REV/'overlay/request_understanding.py')
    expected=dict(runtime['app_hashes'])
    for name in ['task_loop.py','request_understanding.py']:expected['app/agent/'+name]=sha(REV/'overlay'/name)
    allowed=operations(cases)
    allowed.update({r:2048 for r in old})
    allowed.update({'R3/'+c+'/'+t+'/http-'+str(i):2048 for c in ['BA-05','BA-10'] for t in ['t1','t2'] for i in range(1,7)})
    proposal={'baseline_requests':19,'max_new_dispatches':21,'cases':['BA-05','BA-10'],
       'old_operations':old,'old_rows_sha256':digest(runner.rows(PHASE/'dispatch.sqlite',old)),
       'cached_replay_only_exact_body_hashes':{},'allowed_operations':allowed,'operation_prefix':'R3',
       'runtime':runtime,'candidate_app_hashes':expected,'overlay_sha256':sha(REV/'overlay/request_understanding.py'),
       'source_fingerprint':snapshot_sources(ROOT)['fingerprint'],'protected_ledgers':protected_ledgers(),
       'historical_stops':{p:sha(PHASE/p) for p in ['STOP.json','continuation-v1/STOP.json','refund-scope-v2/STOP.json']},
       'source_review':'acceptance/business-v2/refund-source-review-candidate.json',
       'reviewed_material':{p.relative_to(ROOT).as_posix():sha(p) for p in DATA.glob('*.json')},
       'versions':{'REQUEST_UNDERSTANDING_VERSION':'understanding.read-plan-scope.v2','LLM_PROMPT_VERSION':'refund.policy-projection.v3'},
       'stop_on_first_failure':True,'automatic_retry':False,'deployment':False,
       'scope':'material+whole-fragment decision projection; previous results historical; isolated synthetic SQL, frozen published evidence, substituted Redis'}
    proposal['continuation_id']=digest(proposal)
    save(REV/'proposal.json',proposal)
    save(REV/'authorization.json',{'confirmed':True,'approved_by':'user','user_statement':'进行下一步',
       'continuation_id':proposal['continuation_id'],'max_new_dispatches':21,'stream_with_usage_footer_authorized':True,
       'interpretation':'review material and bounded continuation; original remaining 21 of previous 24 cap, original 168 account; first failure stops; no deployment'})
    save(REV/'publication-check.json',{'epoch':current['epoch'],'reviewed_fragments':len(sources['items']),'all_matched':True})
    print('PREPARED='+proposal['continuation_id'])


def configure():
    runner.REV=REV
    runner.load_cases=load_reviewed
    runner.require_revision_authorization=require_authorization
    runner.validate_admission=validate_admission
    runner.prior_review=prior_review


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=['prepare','mock','live'],required=True)
    parser.add_argument('--case',default='BA-05')
    parser.add_argument('--variant',choices=['case','single-rest','single-ws','multi-rest','multi-ws'],default='case')
    args=parser.parse_args()
    if args.mode=='prepare':prepare()
    else:
        configure()
        raise SystemExit(runner.run(args.mode,args.case,args.variant))
