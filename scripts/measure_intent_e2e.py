"""Measure deployed REST full-response latency using synthetic isolated users."""
from __future__ import annotations
import asyncio
from collections import Counter
from datetime import datetime
import json
import math
from pathlib import Path
import time
import uuid

import httpx
from sqlalchemy import select
from app.models.models import User, Order, ModelCallRecord, AgentTrace
from app.utils.database import async_session, engine
from app.security import create_access_token

OUTPUT = Path('/tmp/intent-p95-20261108')
SCENARIOS = {
    'general': ['你好', '谢谢', '您好'],
    'query_order': ['请查询订单{order}的状态。', '帮我看看订单{order}的物流状态。'],
    'refund': ['退货需要保留原包装和配件吗？', '请说明退换货需要保留哪些包装和配件。'],
    'complaint': ['请介绍投诉处理流程和处理时限，我现在只咨询，不创建投诉。'],
    'technical': ['打印机卡纸应该如何安全处理？', '打印机出现卡纸，该怎么排查？'],
    'promotion': ['目前有哪些优惠活动，参与活动有哪些条件？'],
    'human': ['请转人工客服。'],
    'multi_intent': ['查询订单{order}的状态，并说明退货包装要求和运费承担条件。'],
}

def quantile(values, q):
    values = sorted(values)
    return round(values[math.ceil(len(values)*q)-1], 2) if values else None

def summary(rows):
    from scripts.latency_results import summarize
    return summarize(rows)

async def main():
    raise RuntimeError('legacy_live_measurement_disabled_use_source_bound_budgeted_acceptance')
    OUTPUT.mkdir(exist_ok=False)
    users = {}; rows = []; created = []
    started = datetime.utcnow()
    try:
        async with async_session() as db:
            for kind in SCENARIOS:
                user=User(username='latency-'+kind+'-'+uuid.uuid4().hex[:8],role='user',status=True,token_version=0)
                db.add(user); await db.flush()
                order='ORD'+str(970000000+user.id)
                db.add(Order(order_id=order,user_id=user.id,status='已签收',status_code='delivered',total_amount='.10',items=[],currency='CNY',received_at=datetime.utcnow()))
                users[kind]=(user.id,create_access_token(user),order); created.append(user.id)
            await db.commit()
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',timeout=75,trust_env=False) as client:
            for i in range(20):
                for kind, messages in SCENARIOS.items():
                    uid,token,order=users[kind]
                    body={'user_id':uid,'message':messages[i%len(messages)].format(order=order),
                        'client_message_id':'p95-'+uuid.uuid4().hex}
                    began=time.perf_counter()
                    row={'scenario':kind,'sample':i+1,'user_id':uid}
                    try:
                        response=await client.post('/api/v1/chat/send',json=body,headers={'Authorization':'Bearer '+token})
                        row.update(elapsed_ms=(time.perf_counter()-began)*1000,http_status=response.status_code)
                        data=response.json()
                        if response.status_code==200:
                            row.update({key:data.get(key) for key in ['intent','run_id','run_status','task_status','session_id','trace_id','response_time_ms','reply']})
                            async with async_session() as db:
                                calls=(await db.execute(select(ModelCallRecord).where(ModelCallRecord.user_id==uid,ModelCallRecord.created_at>=began_date))).scalars().all() if False else []
                                calls=(await db.execute(select(ModelCallRecord).where(ModelCallRecord.user_id==uid,ModelCallRecord.run_id==data.get('run_id')))).scalars().all()
                                row['model_calls']=len(calls);row['model_purposes']=dict(Counter(c.purpose for c in calls))
                                traces=(await db.execute(select(AgentTrace).where(AgentTrace.request_trace_id==data.get('trace_id')))).scalars().all()
                                row['nodes']=[{'node':t.node_name,'duration_ms':t.duration_ms,'output':t.output_data} for t in traces]
                            row['review_accepted']=None  # Never infer verdict from words in business text.
                        else:row['error']=data
                    except Exception as exc:
                        row.update(elapsed_ms=(time.perf_counter()-began)*1000,error_type=type(exc).__name__)
                    rows.append(row)
                    (OUTPUT/'rows.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
                (OUTPUT/'summary.json').write_text(json.dumps(summary(rows),ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps({'round':i+1,'samples':len(rows),'summary':summary(rows)},ensure_ascii=False),flush=True)
    finally:
        async with async_session() as db:
            for uid in created:
                user=await db.get(User,uid)
                if user:user.status=False;user.disabled_at=datetime.utcnow();user.token_version+=1
            await db.commit()
            calls=(await db.execute(select(ModelCallRecord).where(ModelCallRecord.user_id.in_(created),ModelCallRecord.created_at>=started))).scalars().all()
            budget={'requests':len(calls),'statuses':dict(Counter(c.status for c in calls)),
                'purposes':dict(Counter(c.purpose for c in calls)),
                'input_tokens':sum(c.input_tokens or 0 for c in calls),'output_tokens':sum(c.output_tokens or 0 for c in calls)}
        (OUTPUT/'closeout.json').write_text(json.dumps({'summary':summary(rows),'budget':budget,'users_disabled':created,
            'scope':'REST HTTP complete response, sequential interleaved 20 per scenario; fresh sessions, warm service/cache possible; no load capacity claim'},ensure_ascii=False,indent=2),encoding='utf-8')
        await engine.dispose()

if __name__=='__main__':asyncio.run(main())
