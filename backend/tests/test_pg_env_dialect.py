"""方言归一的取证：一个 `TEST_DATABASE_URL` 必须同时喂 Python 与 Go，且缺 env 不许静默降级。

对应 tasks.md N4 末「env 口径坑」（裸串回落 psycopg2 ⇒ 假红 5 条；pgx 只认裸串 ⇒ 同一变量
喂不了两侧）与 N7 末「pytest 基线口径坑」（漏导 env 时 5 条从 passed 变 skipped，总数不变）。

本文件**不需要 PG**：验的是串的形状与 SQLAlchemy 能否解析出已安装的方言（``create_engine``
不连接）。实库行为由三处消费方（test_canonical_schema / test_db_commit_boundary /
test_go_auth_interop）在同一 env 下各自真跑，Go 侧的剥驱动段由 ``go-core/internal/dsn`` 自测。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.core.pg_scheme import libpq_url, psycopg_url

ROOT = Path(__file__).resolve().parents[2]
# 占位目标（本机 dev 库的 host/库名形态），不含任何真凭据
TARGET = "u:p@127.0.0.1:55432/closenai"
SCHEME_RE = re.compile(r"^[a-z+]+://")


def test_bare_scheme_gets_the_declared_driver():
    """裸 postgresql:// 是 Go/pg_dump 的形态，Python 侧必须补上声明过的 psycopg3 驱动。"""
    assert psycopg_url(f"postgresql://{TARGET}") == f"postgresql+psycopg://{TARGET}"


def test_asyncpg_scheme_is_translated_to_the_declared_driver():
    """历史跑法示例教人传 +asyncpg，而 pyproject 只声明 psycopg[binary] ⇒ 一律归一。"""
    assert psycopg_url(f"postgresql+asyncpg://{TARGET}") == f"postgresql+psycopg://{TARGET}"


def test_psycopg_scheme_is_idempotent():
    assert psycopg_url(f"postgresql+psycopg://{TARGET}") == f"postgresql+psycopg://{TARGET}"


def test_legacy_postgres_scheme_is_canonicalised():
    assert psycopg_url(f"postgres+asyncpg://{TARGET}") == f"postgresql+psycopg://{TARGET}"


def test_driver_segment_never_eats_the_credential_or_parameters():
    """口令里的 ``+`` 与 ``?`` 参数必须原样留在 driver 段之后。"""
    raw = "postgresql+psycopg://u:p+w%40x@127.0.0.1:55432/closenai?sslmode=disable"
    assert psycopg_url(raw) == raw
    assert libpq_url(raw) == (
        "postgresql://u:p+w%40x@127.0.0.1:55432/closenai?sslmode=disable"
    )


def test_non_postgres_url_is_rejected_without_leaking_the_password():
    with pytest.raises(ValueError) as excinfo:
        psycopg_url("mysql+pymysql://u:sup3rs3cr3tpw@127.0.0.1/app")
    message = str(excinfo.value)
    assert "sup3rs3cr3tpw" not in message, f"错误信息回显了连接串内容：{message}"
    assert "mysql+pymysql" in message


def test_translated_url_resolves_to_an_installed_driver():
    """裸串假红的根因是「方言解析不出已安装的驱动」，所以这里真解析一次（不连接）。

    psycopg2 没装：补上驱动段后 create_engine 仍能构造；asyncpg 未在依赖里声明：同理。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import create_async_engine

    for raw in (f"postgresql://{TARGET}", f"postgresql+asyncpg://{TARGET}"):
        url = psycopg_url(raw)
        assert url.startswith("postgresql+psycopg://")
        sync_engine = create_engine(url)
        assert sync_engine.dialect.driver == "psycopg"
        sync_engine.dispose()


async def test_translated_async_engine_disposes_cleanly():
    from sqlalchemy.ext.asyncio import create_async_engine

    for raw in (f"postgresql://{TARGET}", f"postgresql+asyncpg://{TARGET}"):
        engine = create_async_engine(psycopg_url(raw))
        assert engine.dialect.is_async is True
        await engine.dispose()


def test_both_stacks_point_at_the_same_database():
    """Python 补驱动、Go 剥驱动，必须落在同一个库上——否则互认测打的是两个实例。"""
    for raw in (
        f"postgresql://{TARGET}",
        f"postgresql+psycopg://{TARGET}",
        f"postgresql+asyncpg://{TARGET}",
        f"postgres+psycopg2://{TARGET}",
    ):
        assert libpq_url(psycopg_url(raw)) == f"postgresql://{TARGET}"
        assert psycopg_url(libpq_url(raw)) == f"postgresql+psycopg://{TARGET}"


# ── 缺 env 的降级必须可见 ──


def test_pg_test_url_skips_when_env_is_missing(monkeypatch):
    import _pg_env

    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv(_pg_env.REQUIRE_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception, match="TEST_DATABASE_URL 未设置"):
        _pg_env.pg_test_url()


def test_pg_test_url_treats_empty_env_as_missing(monkeypatch):
    import _pg_env

    monkeypatch.setenv("TEST_DATABASE_URL", "")
    monkeypatch.delenv(_pg_env.REQUIRE_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception):
        _pg_env.pg_test_url()


def test_pg_test_url_fails_instead_of_skipping_when_required(monkeypatch):
    """CLOSEN_REQUIRE_PG=1 时 skip 改判 fail：这是「同基线」这句话的自证开关。

    不用 `pytest.raises(pytest.fail.Exception)`：`Skipped` 不是 `Failed` 的子类，
    开关失效时它会直接冒泡、把这条用例本身记成 skip——「没跑起来」就会被看成「没红」。
    """
    import _pg_env

    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv(_pg_env.REQUIRE_ENV, "1")
    try:
        _pg_env.pg_test_url()
    except BaseException as exc:  # noqa: BLE001  pytest 的 outcome 异常不是 Exception 子类
        assert isinstance(exc, pytest.fail.Exception), (
            f"降级开关没生效，仍然是 {type(exc).__name__}（skip 冒充了红）")
        assert "静默 skip" in str(exc), f"报错没说明为什么必须导 env：{exc}"
    else:
        pytest.fail("没有 TEST_DATABASE_URL 却正常返回了连接串")


def test_pg_test_url_normalises_an_exported_bare_url(monkeypatch):
    import _pg_env

    monkeypatch.setenv("TEST_DATABASE_URL", f"postgresql://{TARGET}")
    monkeypatch.delenv(_pg_env.REQUIRE_ENV, raising=False)
    assert _pg_env.pg_test_url() == f"postgresql+psycopg://{TARGET}"


def test_pg_test_url_honours_a_second_env_var(monkeypatch):
    """迁移往返用的是一次性空库（N2_MIGRATE_DATABASE_URL），闸口同一条规则。"""
    import _pg_env

    monkeypatch.setenv("N2_MIGRATE_DATABASE_URL", f"postgresql+asyncpg://{TARGET}")
    monkeypatch.delenv(_pg_env.REQUIRE_ENV, raising=False)
    assert _pg_env.pg_test_url("N2_MIGRATE_DATABASE_URL", purpose="迁移往返") == (
        f"postgresql+psycopg://{TARGET}"
    )


# ── 反漂移：归一只许有一条路 ──

# 归一器本体：规则的唯二落点（Python 侧 pg_scheme / Go 侧 internal/dsn）与其语料表。
TRANSLATORS = {
    ROOT / "backend" / "core" / "pg_scheme.py",
    ROOT / "backend" / "tests" / "_pg_env.py",
    ROOT / "backend" / "tests" / "test_pg_env_dialect.py",
    ROOT / "go-core" / "internal" / "dsn" / "dsn.go",
    ROOT / "go-core" / "internal" / "dsn" / "dsn_test.go",
}
# 允许提到 asyncpg 但不是在做 scheme 翻译的地方，逐条带理由（豁免表不许留死条目）。
MENTION_EXCEPTIONS = {
    ROOT / "backend" / "database.py": (
        "生产引擎按驱动分设 pgbouncer 参数（psycopg→prepare_threshold=None / "
        "asyncpg→statement_cache_size=0），不是 scheme 归一"
    ),
}
# 变异自证的锚点文件：红闸要把接线退回「只认 asyncpg 的旧写法」，锚点就得逐字引用被禁写法。
MUTATION_HARNESSES = {
    ROOT / "scripts" / "mutate_pg_scheme_gates.py": (
        "G5 的变异把 store 接线改回 strings.Replace(asyncpg→裸串)，逐字引用是被禁写法的唯一用途"
    ),
}


def test_no_hand_rolled_driver_translation_survives():
    """两侧原先各搓一次 strings.Replace / .replace("+asyncpg"…)，且各只认一种前缀，
    于是「换个 scheme 就假红」。归一下沉后，再出现第二处硬编码 scheme 就是漂移。
    """
    exempt = TRANSLATORS | set(MENTION_EXCEPTIONS) | set(MUTATION_HARNESSES)
    offenders = []
    for pattern in ("backend/**/*.py", "scripts/**/*.py", "go-core/**/*.go"):
        for path in ROOT.glob(pattern):
            if path in exempt or "/.venv/" in str(path):
                continue
            if "+asyncpg" in path.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"这些地方还硬编码 asyncpg scheme：{offenders}"


def test_scheme_mention_exception_is_still_true():
    """豁免条目必须是当下事实：那条 asyncpg 分支一旦被删，这里就红，免得留下口头豁免。"""
    for path, reason in MENTION_EXCEPTIONS.items():
        assert path.exists(), f"豁免指向不存在的文件：{path}"
        text = path.read_text(encoding="utf-8")
        assert "+asyncpg" in text, f"豁免已失效（{reason}），请删掉 {path} 这条"
        assert "statement_cache_size" in text, f"{path} 不再按驱动分设连接参数，豁免应删"
    for path, reason in MUTATION_HARNESSES.items():
        assert path.exists(), f"变异锚点文件不存在：{path}（{reason}），豁免应删"
        assert "postgresql+asyncpg://" in path.read_text(encoding="utf-8"), (
            f"{path.name} 已不再引用被禁写法，豁免应删（{reason}）")


def test_real_db_tests_take_the_env_only_through_the_helper():
    """实库测试必须走 pg_test_url（归一 + skip/fail 开关），不许再自己读 env。"""
    readers = []
    quote = '["\']'
    direct_read = re.compile(
        rf"os\.environ\.get\(\s*{quote}(TEST_DATABASE_URL|N2_MIGRATE_DATABASE_URL)"
    )
    for path in (ROOT / "backend" / "tests").glob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        if direct_read.search(text):
            readers.append(path.name)
    assert not readers, f"这些用例绕开 _pg_env 直接读实库 env：{readers}"
