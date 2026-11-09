"""Bounded deployed HTTP pilot: fresh synthetic principal per sample, no retries.

The deployed SQL model gateway is the paid-call reservation ledger. The local
SQLite journal reserves each HTTP sample before dispatch, conservatively at six
provider calls, and never erases failed/unknown samples.
"""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
import time
import uuid

SCENARIOS={
 'general':['你好','谢谢'],
 'query_order':['请查询订单{order}的状态。'],
 'refund':['退货需要保留原包装和配件吗？'],
 'complaint':['请介绍投诉处理流程和处理时限，我现在只咨询，不创建投诉。'],
 'technical':['打印机卡纸应该如何安全处理？'],
 'promotion':['目前有哪些优惠活动，参与活动有哪些条件？'],
 'human':['请转人工客服。'],
 'multi_intent':['查询订单{order}的状态，并说明退货包装要求和运费承担条件。'],
}


def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


async def main(out: Path):
    sys.path.insert(0,'/app')
    from sqlalchemy import select,func
    from app.models.models import User,Order,ModelCallRecord,HandoffRequestRecord,HandoffStatus,RefundApplication,Ticket
    from app.utils.database import async_session,engine
    from app.security import create_access_token
    from app.config.config import settings
    from app.agent.redis_checkpoint import RedisCheckpointSaver
    import httpx
    plan=json.loads((out/'plan.json').read_text());auth=json.loads((out/'authorization.json').read_text())
    if (auth.get('confirmed') is not True or auth.get('plan_id')!=digest(plan) or auth.get('max_http')!=160
            or plan['max_http']!=160 or plan['max_provider_reservations']!=960 or plan['samples_per_scenario']!=20):
        raise ValueError('fixed_performance_authorization_required')
    if not settings.LLM_GATEWAY_ENABLED or settings.LLM_MAX_RETRIES!=0 or settings.LLM_MAX_TOKENS>2048:
        raise ValueError('model_gateway_limits_required')
    if (out/'dispatch.sqlite').exists() or (out/'STOP.json').exists():raise ValueError('existing_pilot_no_retry')
    with sqlite3.connect(out/'dispatch.sqlite') as journal:
        journal.execute('CREATE TABLE attempts (operation TEXT PRIMARY KEY, status TEXT NOT NULL, user_id INTEGER, run_id TEXT)')
    rows=[];users=[];status='RUNNING';error=None;saver=RedisCheckpointSaver()
    def reserve(operation,uid):
        current=json.loads((out/'authorization.json').read_text())
        if current!=auth:raise ValueError('authorization_revoked_or_changed')
        with sqlite3.connect(out/'dispatch.sqlite') as journal,journal:
            if journal.execute('SELECT count(*) FROM attempts').fetchone()[0]>=160:raise ValueError('sample_budget_exhausted')
            journal.execute('INSERT INTO attempts VALUES (?,?,?,NULL)',(operation,'reserved_before_http',uid))
    try:
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',timeout=65,trust_env=False) as client:
            for number in range(20):
                for kind,messages in SCENARIOS.items():
                    async with async_session() as db:
                        user=User(username='perfopt-'+kind+'-'+uuid.uuid4().hex[:10],role='user',status=True,token_version=0)
                        db.add(user);await db.flush();uid=user.id;token=create_access_token(user);users.append(uid)
                        order='ORD'+str(980000000+uid)
                        db.add(Order(order_id=order,user_id=uid,status='已签收',status_code='delivered',total_amount='0.10',currency='CNY',items=[],received_at=datetime.utcnow()))
                        await db.commit()
                    operation=kind+'/'+str(number+1);reserve(operation,uid)
                    start=time.perf_counter();response=await client.post('/api/v1/chat/send',headers={'Authorization':'Bearer '+token},
                        json={'user_id':uid,'message':messages[number%len(messages)].format(order=order),'client_message_id':'perfopt-'+uuid.uuid4().hex})
                    elapsed=(time.perf_counter()-start)*1000;data=response.json()
                    row={'scenario':kind,'sample':number+1,'user_id':uid,'elapsed_ms':round(elapsed,3),'http_status':response.status_code,
                         **{k:data.get(k) for k in ['run_id','run_status','task_status','intent','reply','session_id','response_time_ms']}}
                    if response.status_code!=200:raise ValueError('http_failure:'+str(response.status_code))
                    async with async_session() as db:
                        calls=(await db.execute(select(ModelCallRecord).where(ModelCallRecord.user_id==uid))).scalars().all()
                        row['model_calls']=[{'id':c.id,'purpose':c.purpose,'status':c.status,'input_tokens':c.input_tokens,
                            'output_tokens':c.output_tokens,'error_code':c.error_code,'reserved_tokens':c.reserved_tokens} for c in calls]
                    checkpoint=await saver.aget_tuple({'configurable':{'thread_id':data['session_id']}})
                    state=checkpoint.checkpoint.get('channel_values',{}).get('state') if checkpoint else None
                    if not isinstance(state,dict) and hasattr(state,'__dict__'):state=vars(state)
                    if not isinstance(state,dict) or state.get('user_id')!=uid:raise ValueError('missing_matched_review_checkpoint')
                    review=state.get('review_result');review=review.model_dump() if hasattr(review,'model_dump') else review
                    info=state.get('collected_info') or {};judge=info.get('llm_judge') or {}
                    task=state.get('task');task=vars(task) if not isinstance(task,dict) and hasattr(task,'__dict__') else task
                    row.update(review=review,judge_reason=judge.get('reason'),answerability=judge.get('answerability'),
                        response_behavior=judge.get('response_behavior'),understanding_failure=info.get('understanding_failure'),
                        retrieval_metadata=info.get('kb_search_meta'),waiting_for=(task or {}).get('waiting_for') if isinstance(task,dict) else None)
                    rows.append(row);save(out/'rows.json',rows)
                    with sqlite3.connect(out/'dispatch.sqlite') as journal,journal:
                        journal.execute('UPDATE attempts SET status=?,run_id=? WHERE operation=?',('http_complete',data.get('run_id'),operation))
                    print('SAMPLE='+json.dumps({'scenario':kind,'sample':number+1,'elapsed_ms':row['elapsed_ms'],'review':review.get('status') if review else None,'calls':len(calls)}),flush=True)
                    if (not review or review.get('passed') is not True or len(calls)>6
                            or any(c.status!='succeeded' or c.input_tokens is None or c.output_tokens is None for c in calls)
                            or info.get('understanding_failure')):
                        raise ValueError('unexpected_review_or_provider_failure:'+operation)
            status='MEASUREMENT_COMPLETE_AWAITING_SEPARATE_REVIEW'
    except BaseException as exc:
        status='STOPPED';error=type(exc).__name__+':'+str(exc);save(out/'STOP.json',{'error':error,'complete_http_samples':len(rows)})
    finally:
        async with async_session() as db:
            for uid in users:
                user=await db.get(User,uid)
                if user:user.status=False;user.disabled_at=datetime.utcnow();user.token_version+=1
            handoffs=(await db.execute(select(HandoffRequestRecord).where(HandoffRequestRecord.user_id.in_(users)))).scalars().all() if users else []
            for item in handoffs:
                if item.status in {HandoffStatus.PENDING,HandoffStatus.ASSIGNED}:item.status=HandoffStatus.CANCELLED;item.resolution='synthetic performance pilot cleanup';item.resolved_at=datetime.utcnow()
            await db.commit()
            calls=(await db.execute(select(ModelCallRecord).where(ModelCallRecord.user_id.in_(users)))).scalars().all() if users else []
            refunds=await db.scalar(select(func.count()).select_from(RefundApplication).where(RefundApplication.user_id.in_(users))) if users else 0
            tickets=await db.scalar(select(func.count()).select_from(Ticket).where(Ticket.user_id.in_(users))) if users else 0
        groups={}
        for kind in SCENARIOS:
            g=[r for r in rows if r['scenario']==kind];times=sorted(r['elapsed_ms'] for r in g)
            groups[kind]={'n':len(g),'all_response_p95_ms':times[math.ceil(len(times)*.95)-1] if times else None,
                'review_accepted':sum(bool((r.get('review') or {}).get('passed')) for r in g),
                'stable_tail_estimate':False,'scope':'complete REST response from container client; no browser/public network; sequential; reviewed output is not independent human gold'}
        report={'status':status,'error':error,'summary':groups,'http_samples':len(rows),'provider_sql_reservations':len(calls),
            'unknown_or_unsuccessful_calls':sum(c.status!='succeeded' or c.input_tokens is None or c.output_tokens is None for c in calls),
            'users_disabled':len(users),'synthetic_handoffs_cancelled':len(handoffs),'refund_applications':refunds,'complaint_tickets':tickets,
            'scope':'new deployed image; actual SQL/Redis/Milvus/local models/provider; not frontend or production capacity'}
        save(out/'closeout.json',report)
        from app.utils.database import get_redis
        redis=await get_redis()
        if redis:await redis.aclose()
        await engine.dispose();print('FINAL='+json.dumps(report),flush=True)
    return 0 if status.startswith('MEASUREMENT_COMPLETE') else 2


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    raise SystemExit(asyncio.run(main(Path(args.output))))
