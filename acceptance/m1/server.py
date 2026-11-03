"""生产前端构建的隔离验收服务；同一runtime状态可供真正进程重启重放。"""
import ast
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
runtime = ROOT / "reports/m1-closeout/runtime"
state = json.loads((runtime / "state.json").read_text(encoding="utf-8"))
if not state["prefix"].startswith("aicshub-m1-acceptance-"):
    raise RuntimeError("Only owned test resources allowed")
import subprocess
for cid in state["containers"].values():
    label = subprocess.check_output(["docker", "inspect", "--format", '{{index .Config.Labels "aicshub.test.owner"}}', cid], text=True).strip()
    if label != "m1-closeout":
        raise RuntimeError("Refuse non-test resource")
tree = ast.parse((ROOT / "app/config/config.py").read_text(encoding="utf-8-sig"))
for cls in tree.body:
    if isinstance(cls, ast.ClassDef) and cls.name == "Settings":
        for node in cls.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                os.environ.pop(node.target.id, None)
ui_path = runtime / "ui-state.json"
if ui_path.exists():
    ui = json.loads(ui_path.read_text(encoding="utf-8"))
else:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    ui = {"database": "aicshub_m1_ui_" + uuid.uuid4().hex[:12], "port": port}
    ui_path.write_text(json.dumps(ui), encoding="utf-8")
os.environ.update(DATABASE_URL_OVERRIDE=f"mysql+aiomysql://root:{state['secret']}@127.0.0.1:{state['ports']['mysql']}/{ui['database']}",
    REDIS_ENABLED="true",REDIS_HOST="127.0.0.1",REDIS_PORT=state["ports"]["redis"],
    USE_MILVUS="false",USER_MEMORY_ENABLED="false",LANGFUSE_ENABLED="false",MCP_ENABLED="false",
    AUTH_SECRET_KEY="synthetic-m1-ui-secret-only",AUTH_DEMO_ACCOUNTS_ENABLED="false",LLM_API_KEY="",DEBUG="false",
    LOGIN_RATE_LIMIT_PER_MINUTE="1000",CHAT_RATE_LIMIT_PER_MINUTE="1000")
os.chdir(tempfile.mkdtemp(prefix="aicshub-m1-ui-"))


async def prepare():
    from sqlalchemy import create_engine, text
    from app.config.config import settings
    from app.utils.database import engine, async_session
    from app.models.models import Base, User, Order
    from app.security import hash_password
    from datetime import datetime, timedelta
    url = settings.DATABASE_URL
    admin = create_engine(url.rsplit("/",1)[0].replace("+aiomysql","+pymysql") + "/aicshub_m1_acceptance")
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as db:
        db.execute(text(f"CREATE DATABASE IF NOT EXISTS `{ui['database']}` CHARACTER SET utf8mb4"))
    admin.dispose()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as db:
        if await db.get(User,101) is None:
            for uid, name, role in [(101,"m1_customer","user"),(202,"m1_other","user"),(999,"m1_admin","admin")]:
                db.add(User(id=uid,username=name,nickname=name,role=role,password_hash=hash_password("Synthetic-M1-only!2026")))
            await db.flush()
            for oid in ("ORD900001","ORD900002"):
                db.add(Order(order_id=oid,user_id=101,status="已签收",status_code="delivered",items=[],
                             total_amount="0.10",received_at=datetime.utcnow()-timedelta(days=1)))
            await db.commit()
    await engine.dispose()


def app():
    from fastapi import FastAPI
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from app.routers import chat, sessions, tickets, refunds, users
    instance = FastAPI()
    for route in (chat.router,sessions.router,tickets.router,refunds.router,users.router):
        instance.include_router(route)
    build = ROOT / "reports/m1-closeout/frontend-build"
    instance.mount("/static", StaticFiles(directory=build), name="static")
    @instance.get("/")
    async def index():
        return FileResponse(build / "index.html")
    return instance


if __name__ == "__main__":
    asyncio.run(prepare())
    import uvicorn
    print(f"M1 isolated UI: http://127.0.0.1:{ui['port']}/ pid={os.getpid()}",flush=True)
    uvicorn.run(app(),host="127.0.0.1",port=ui["port"],log_level="warning")
