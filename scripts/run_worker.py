"""独立SQL任务worker；不随web进程自动投递，不默认接入真实外部渠道。"""
import argparse
import asyncio
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


async def sweep_runs(factory, limit=50):
    from sqlalchemy import select
    from datetime import datetime
    from app.models.models import ChatRun
    from app.services.run_execution import recover, FINAL
    async with factory() as db:
        ids=(await db.execute(select(ChatRun.id).where(ChatRun.status.not_in(FINAL),
            ChatRun.lease_expires_at<=datetime.utcnow()).limit(limit))).scalars().all()
    for rid in ids:
        await recover(factory,rid)
    return len(ids)


async def run(args):
    from app.utils.database import async_session, close_redis, engine
    from app.services.background_jobs import tick,import_outbox,SimulatedAdapter
    adapter=SimulatedAdapter() if args.simulate else None
    try:
        while True:
            await sweep_runs(async_session)
            await import_outbox(async_session)
            # 顺序有界消费，摘要/通知按kind隔离；不在聊天请求内解析/投递。
            for _ in range(args.limit):
                if not await tick(async_session,kind=args.kind,adapter=adapter): break
            if args.once: break
            await asyncio.sleep(args.poll)
    finally:
        await close_redis()
        await engine.dispose()


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--once",action="store_true")
    parser.add_argument("--simulate",action="store_true",help="只生成明确标记的模拟投递回执")
    parser.add_argument("--kind",choices=["summary","notification","knowledge_index"])
    parser.add_argument("--limit",type=int,choices=range(1,101),default=10)
    parser.add_argument("--poll",type=float,default=1.0)
    args=parser.parse_args()
    if args.poll<0.05: parser.error("poll须至少0.05秒")
    asyncio.run(run(args))
