"""PostgreSQL 连接串的方言归一——同一个环境变量同时喂 Python 与 Go。

坑的实测出处（tasks.md N4 末「env 口径坑」与 N7 末「pytest 基线口径坑」）：SQLAlchemy 要求
scheme 里带驱动，裸 ``postgresql://`` 会回落到本机未安装的 psycopg2；而 Go 的 pgx 只认裸串，
带驱动段直接 ``invalid keyword/value``。2026-10-14 同一份代码只换 scheme 就假红 5 条
（3 条 canonical schema 实库对账 + 2 条事务边界）。两侧原先各自搓 ``.replace("+asyncpg", …)``
且各只认一种前缀，所以「跑哪一侧就按哪一侧的 scheme 给」。

口径：驱动按声明走——``pyproject.toml`` 只声明 ``psycopg[binary]``（无 asyncpg），生产串见
``backend/.env.example``。于是
- Python 侧（sync 反射 / async 引擎）一律归一到 ``postgresql+psycopg://``；
- pgx 与 pg_dump 侧剥掉驱动段，回到裸 ``postgresql://``。
Go 侧的同一规则在 ``go-core/internal/dsn``（cmd/go-api、store 集成测、crosscheck 三处共用）。
"""
from __future__ import annotations

import re

PSYCOPG_SCHEME = "postgresql+psycopg://"
LIBPQ_SCHEME = "postgresql://"

# scheme 里那一段 ``+driver`` 可缺省；``[^:/]*`` 保证只吃 scheme，不碰口令里的 ``+``。
_PG_SCHEME = re.compile(r"^(?:postgres(?:ql)?)(?:\+[^:/]*)?://")


def _target(raw: str) -> str:
    """连接串去掉 scheme 之后的部分（库、用户、参数原样保留）。"""
    match = _PG_SCHEME.match(raw)
    if not match:
        head = raw.split("://", 1)[0] if "://" in raw else "<无 scheme>"
        raise ValueError(
            f"不是带 scheme 的 PostgreSQL 连接串（前缀 {head!r}）——"
            "非 PG 串不做静默改写；连接串其余部分不回显以免泄漏口令"
        )
    return raw[match.end():]


def psycopg_url(raw: str) -> str:
    """给 SQLAlchemy 用：任何 PG scheme 形态都换成 psycopg3 驱动。"""
    return f"{PSYCOPG_SCHEME}{_target(raw)}"


def libpq_url(raw: str) -> str:
    """给 pgx / pg_dump 用：剥掉 SQLAlchemy 的驱动段。"""
    return f"{LIBPQ_SCHEME}{_target(raw)}"
