"""实库测试取 env 的唯一入口：归一方言 + 把「缺 env 就静默 skip」变成可关的可见降级。

规则本体在 `backend/core/pg_scheme.py`（Python 侧）与 `go-core/internal/dsn`（Go 侧），
本模块只负责测试语义：

- `pg_test_url()` 缺 `TEST_DATABASE_URL` 时默认 skip——宿主没有本机库不该把 CI 打红；
- 但 skip 与 passed 在总数上看不出来。2026-10-14 N7 末实测：漏导 env 是
  1 failed / 604 passed / 17 skipped，带 env 是 609 passed，差的正是 5 条实库用例
  ⇒ 声明「全量回归同基线」时导出 `CLOSEN_REQUIRE_PG=1`，这 5 条就从 skip 变成 fail。
"""
from __future__ import annotations

import os

import pytest

from backend.core.pg_scheme import psycopg_url

REQUIRE_ENV = "CLOSEN_REQUIRE_PG"


def pg_test_url(env_var: str = "TEST_DATABASE_URL", *, purpose: str = "实库对账") -> str:
    """返回 psycopg3 形态的测试库串；env 缺失按 CLOSEN_REQUIRE_PG 决定 skip 还是 fail。"""
    raw = os.environ.get(env_var, "")
    if not raw:
        message = f"{env_var} 未设置（{purpose}）"
        if os.environ.get(REQUIRE_ENV):
            pytest.fail(
                f"{message}——{REQUIRE_ENV}={os.environ[REQUIRE_ENV]} 要求实库用例真跑："
                "静默 skip 会让「同基线」这句话失去取证，先把 env 导上"
            )
        pytest.skip(message)
    return psycopg_url(raw)
