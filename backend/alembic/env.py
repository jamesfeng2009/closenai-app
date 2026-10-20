"""
Alembic 迁移环境配置。

使用异步 engine 连接 Supabase Transaction pooler。
从 backend.config 读取 DATABASE_URL，避免硬编码密钥。
"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from backend.config import settings
from backend.core import env_isolation
from backend.models import Base  # noqa: F401  导入所有模型，确保 autogenerate 发现

# T6-04（2026-10-20）· 这道闸在 migrate 入口**自己调一次**，不靠 `backend.models`
# 顺手 import 到 `backend/database.py` 的那一句：alembic 建的是它**自己的** engine
# （下面 async_engine_from_config，NullPool），与 py-api 那个不是同一条连接；
# 而 migrate job 是「连上生产库并改它的结构」这一步——DDL 比业务写更难回滚。
# 那句 import 一旦漂了（有人删掉 models 导入、或改用 sync env.py），这道闸就静默没了。
env_isolation.require_db_isolation(
    settings.environment, settings.database_url, settings.prod_db_fingerprints)

config = context.config

# 从环境变量注入数据库连接串
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL，不连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """异步模式：连接数据库执行迁移。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()