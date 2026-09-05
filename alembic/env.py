from logging.config import fileConfig
from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import engine_from_config
from app.models.models import Base
from app.config.config import settings

config = context.config
_sync_url = settings.DATABASE_URL.replace("+aiosqlite", "").replace("+aiomysql", "+pymysql")
config.set_main_option("sqlalchemy.url", _sync_url.replace("%", "%%"))
# 项目 ini 保持最小配置；应用自身负责日志初始化，迁移不强制读取 logger 段。
target_metadata = Base.metadata

def run_migrations_offline():
    context.configure(url=_sync_url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction(): context.run_migrations()

def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool, future=True)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction(): context.run_migrations()

if context.is_offline_mode(): run_migrations_offline()
else: run_migrations_online()
