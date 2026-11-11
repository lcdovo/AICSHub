"""独立SQL任务worker；不随web进程自动投递，不默认接入真实外部渠道。"""
import argparse
import asyncio
import json
import logging
import os
import signal
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

logger = logging.getLogger(__name__)
SCHEMA_REVISION = "0014_user_memory_entries"


async def verify_schema(factory):
    from sqlalchemy import inspect, text
    async with factory() as db:
        revision = await db.scalar(text("SELECT version_num FROM alembic_version"))
        connection = await db.connection()
        present = await connection.run_sync(lambda conn: all(
            inspect(conn).has_table(name)
            for name in ("agent_capability_calls", "user_memory_entries")
        ))
        if revision != SCHEMA_REVISION or not present:
            raise RuntimeError("worker_schema_migration_required")


def heartbeat(path):
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"pid": os.getpid(), "process_started": process_identity(os.getpid()), "updated_at": time.time(),
        "schema_revision": SCHEMA_REVISION}), encoding="utf-8")
    temporary.replace(path)


def process_identity(pid):
    if sys.platform.startswith("linux"):
        return Path(f"/proc/{pid}/stat").read_text().rpartition(") ")[2].split()[19]
    return None


def healthy(path, max_age=120):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - value["updated_at"]
        if value["schema_revision"] != SCHEMA_REVISION or not 0 <= age <= max_age:
            return False
        pid = value["pid"]
        if type(pid) is not int or pid <= 0:
            return False
        if sys.platform.startswith("linux") and not Path(f"/proc/{pid}").exists():
            return False
        if value.get("process_started") != process_identity(pid):
            return False
        return True
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return False


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
    path = getattr(args, "heartbeat_file", None)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = []
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop.set)
            signals.append(signum)
        except (NotImplementedError, RuntimeError):
            pass
    if path:
        path.unlink(missing_ok=True)
    try:
        await verify_schema(async_session)
        while not stop.is_set():
            try:
                await sweep_runs(async_session)
                await import_outbox(async_session)
                heartbeat(path)
                # 顺序有界消费；API与索引进程使用同一Redis模型准入命名空间。
                for _ in range(args.limit):
                    if stop.is_set():
                        break
                    consumed = await tick(async_session,kind=args.kind,adapter=adapter)
                    heartbeat(path)
                    if not consumed:
                        break
            except Exception as exc:
                logger.error("Worker cycle failed: %s", type(exc).__name__)
                if args.once:
                    raise
            if args.once: break
            try:
                await asyncio.wait_for(stop.wait(), timeout=args.poll)
            except TimeoutError:
                pass
    finally:
        for signum in signals:
            loop.remove_signal_handler(signum)
        await close_redis()
        await engine.dispose()


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--once",action="store_true")
    parser.add_argument("--simulate",action="store_true",help="只生成明确标记的模拟投递回执")
    parser.add_argument("--kind",choices=["summary","notification","knowledge_index"])
    parser.add_argument("--limit",type=int,choices=range(1,101),default=10)
    parser.add_argument("--poll",type=float,default=1.0)
    parser.add_argument("--heartbeat-file", type=Path, default=Path("/tmp/aicshub-worker.json"))
    parser.add_argument("--healthcheck", action="store_true")
    parser.add_argument("--health-max-age", type=float, default=120)
    args=parser.parse_args()
    if args.poll<0.05: parser.error("poll须至少0.05秒")
    if args.health_max_age <= 0: parser.error("health-max-age须大于0")
    if args.healthcheck:
        raise SystemExit(0 if healthy(args.heartbeat_file, args.health_max_age) else 1)
    asyncio.run(run(args))
