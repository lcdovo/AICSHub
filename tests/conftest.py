"""第 1 步本地 profile 测试配置。"""
import os

os.environ.setdefault("DATABASE_URL_OVERRIDE", "sqlite+aiosqlite:///./tests/.test_customer_service.db")
os.environ.setdefault("USE_MILVUS", "false")
os.environ.setdefault("MOCK_LLM_ENABLED", "true")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("DEBUG", "false")
os.environ.setdefault("AUTH_SECRET_KEY", "test-secret-key-for-local-suite-32chars")
os.environ.setdefault("AUTH_DEMO_ACCOUNTS_ENABLED", "true")
os.environ.setdefault("DEMO_USER_PASSWORD", "user12345")
os.environ.setdefault("DEMO_ADMIN_PASSWORD", "admin12345")
