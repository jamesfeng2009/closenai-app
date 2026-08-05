"""
数据库连接与会话管理。

职责：
- 创建异步 SQLAlchemy engine（连接 Supabase Transaction pooler）
- 定义 Base 元数据基类
- 提供 get_db 依赖供 FastAPI 路由注入会话

禁止：在此模块中编写任何业务逻辑。
"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from backend.config import settings


class Base(DeclarativeBase):
    """所有 SQLAlchemy ORM 模型的基类。"""


# pgbouncer（Supabase Transaction pooler，端口 6543）事务模式下必须禁用 prepared statements：
# psycopg3 → prepare_threshold=None；asyncpg → statement_cache_size=0（按实际驱动选择）
_connect_args: dict = {}
if settings.database_url.startswith("postgresql+psycopg"):
    _connect_args["prepare_threshold"] = None
elif settings.database_url.startswith("postgresql+asyncpg"):
    _connect_args["statement_cache_size"] = 0

# 异步 engine：Vercel Serverless 场景使用 Transaction pooler 连接串
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    connect_args=_connect_args,
)

# 异步会话工厂
SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：为每个请求提供一个数据库会话，成功自动提交，异常回滚。"""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise