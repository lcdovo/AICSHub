"""Run unchanged backend against a NEW temporary database for UI acceptance."""
import asyncio
import os
from pathlib import Path
import secrets
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
QA_DIR = Path(tempfile.mkdtemp(prefix="aicshub-frontend-qa-"))
os.environ.update({
    "ENVIRONMENT": "local", "DEBUG": "false",
    "DATABASE_URL_OVERRIDE": f"sqlite+aiosqlite:///{(QA_DIR / 'qa.db').as_posix()}",
    "AUTH_SECRET_KEY": secrets.token_urlsafe(48),
    "AUTH_DEMO_ACCOUNTS_ENABLED": "false", "REDIS_ENABLED": "false",
    "USE_MILVUS": "false", "LANGFUSE_ENABLED": "false",
    "MCP_ENABLED": "false", "LLM_API_KEY": "", "LLM_BACKUP_BASE": "",
    "LLM_LOCAL_BASE": "", "EMBEDDING_API_BASE": "", "EMBEDDING_API_KEY": "",
    "RERANKER_API_BASE": "", "RERANKER_LOCAL_BASE": "",
    "LOGIN_RATE_LIMIT_PER_MINUTE": "1000", "CHAT_RATE_LIMIT_PER_MINUTE": "1000",
})

async def prepare():
    from app.utils.database import init_database, async_session
    from app.models.models import User
    from app.security import hash_password
    from app.services.embedding_service import BaseEmbedding, get_embedding_service
    import hashlib
    class AcceptanceEmbedding(BaseEmbedding):
        name = 'acceptance-deterministic'
        dim = 1024
        def encode(self, text: str) -> list[float]:
            vector = [0.0] * self.dim
            for token in text:
                vector[int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % self.dim] += 1.0
            return vector
        def encode_query(self, text: str) -> list[float]:
            return self.encode(text)
    # QA-only dependency substitute. No fake data enters the frontend or production runtime.
    embedding = get_embedding_service()
    embedding._embedding = AcceptanceEmbedding()
    embedding._backend_name = 'acceptance-deterministic'
    await init_database()
    async with async_session() as db:
        db.add_all([
            User(id=101, username="qa_customer", nickname="验收用户", level="normal", role="user", password_hash=hash_password("Local-QA-only-2026!")),
            User(id=999, username="qa_admin", nickname="验收管理员", level="enterprise", role="admin", password_hash=hash_password("Local-QA-only-2026!")),
        ])
        await db.commit()

if __name__ == "__main__":
    os.chdir(ROOT)
    asyncio.run(prepare())
    print(f"Isolated UI acceptance database: {QA_DIR}", flush=True)
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8100, log_level="warning")
