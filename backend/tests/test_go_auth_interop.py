"""
N1-4 跨语言认证互认验收（Python ⇄ Go）。

被验对象：`go-core`（Go Core 第一条纵切）与 `backend/core/auth.py`（权威实现）。

四个方向都要闭合，缺一个都会在灰度切换时变成线上 401：
1. Go 签发的 access/refresh token → Python `decode_token` 必须解出同样的 claims，
   且 refresh 当访问凭证用时两侧都给 40102。
2. Go 的 argon2id 哈希 → Python 校验通过 **且 `check_needs_rehash` 为 False**
   （参数一旦漂移，老用户登录会被无声改写哈希）。
3. Go 的 refresh token sha256 → 与 Python `hash_refresh_token` 逐字相等（同库同行）。
4. Python 侧产物 → Go 侧校验，由 `go-core/internal/auth/interop_test.go` 用
   `go-core/testdata/python_auth_baseline.json` 断言；本文件只补「基线未腐烂」。

另含 `go test ./...` 全量与 HTTP goldens 漂移闸门。
最后两条需要 `TEST_DATABASE_URL`（alembic upgrade head 后的隔离库）：Python 写的行
Go 能登录、Go 写的行 Python 能验密，这是灰度切流期间唯一的真实风险面；未设置时 skip。
Go 工具链缺失时前 8 条 skip（宿主 CI 不因本机环境失败）。

跑法（本机 go 的 PATH 是混合安装，须点名 GOROOT；scheme 给裸 `postgresql://` 即可，
Python 侧由 `_pg_env` 补 psycopg3 驱动、Go 侧由 `internal/dsn` 剥驱动段，同一个变量喂两侧）：
    GO_BIN=/path/to/go1.27.1/bin/go \\
    TEST_DATABASE_URL='postgresql://postgres:***@127.0.0.1:55432/closenai' \\
    .venv/bin/python -m pytest backend/tests/test_go_auth_interop.py
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

from _pg_env import pg_test_url
from backend.config import settings
from backend.core import auth as auth_module
from backend.core.errors import AppException, ErrorCode

ROOT = Path(__file__).resolve().parents[2]
GO_CORE = ROOT / "go-core"
# 只在测试进程与 Go 子进程之间使用，不落任何真实配置。
CROSSCHECK_SECRET = "n1-4-crosscheck-secret-0123456789abcdef"
CROSSCHECK_PASSWORD = "cross检查0Pass"

BASELINE = GO_CORE / "testdata" / "python_auth_baseline.json"
GOLDENS = GO_CORE / "testdata" / "http_golden.json"


def _go_candidates() -> list[str]:
    """按可靠性排序的 go 可执行文件候选。

    本机存在混合安装（/usr/local/go 的 bin 与 GOROOT 版本不一致），`go version`
    能过但编译会报 "does not match go tool version"，因此候选必须实测后才采用。
    """
    raw = [
        os.environ.get("GO_BIN"),
        os.environ.get("GOROOT") and str(Path(os.environ["GOROOT"]) / "bin" / "go"),
        shutil.which("go"),
    ]
    out: list[str] = []
    for item in raw:
        if item and item not in out:
            out.append(item)
    return out


def _run(go_bin: str, args: list[str], timeout: int) -> subprocess.CompletedProcess:
    env = dict(os.environ, JWT_SECRET=CROSSCHECK_SECRET)
    return subprocess.run(
        [go_bin, *args],
        cwd=GO_CORE,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _require_go() -> str:
    """返回第一台能真正编译 crosscheck 工具的 go；全都不可用则 skip。"""
    failures = []
    for candidate in _go_candidates():
        probe = _run(candidate, ["run", "./tools/crosscheck", "-h"], 180)
        # `go run ... -h` 让 flag 包打印用法并以 2 退出，工具未编译成功时也是 2，
        # 因此以 stderr 是否出现用法行为准。
        if "Usage of" in probe.stderr or "-user-id" in probe.stderr:
            return candidate
        failures.append(f"{candidate}: rc={probe.returncode} {probe.stderr.strip()[:200]}")
    if not failures:
        pytest.skip("未找到 go 可执行文件（可用 GO_BIN/GOROOT 指定）")
    pytest.skip("go 工具链不可用：\n" + "\n".join(failures))


@pytest.fixture(scope="module")
def go_bin() -> str:
    return _require_go()


@pytest.fixture(scope="module")
def crosscheck_payload(go_bin: str) -> dict:
    proc = _run(
        go_bin,
        ["run", "./tools/crosscheck", "-user-id", "crosscheck-u1",
         "-password", CROSSCHECK_PASSWORD],
        180,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture
def shared_secret(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", CROSSCHECK_SECRET)
    monkeypatch.setattr(settings, "jwt_algorithm", "HS256")
    return CROSSCHECK_SECRET


def test_go_test_suite_passes(go_bin: str):
    """Go 侧全量测试：goldens 逐字节 + 包内断言必须与 Python 同批绿灯。"""
    proc = _run(go_bin, ["test", "./..."], 600)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_go_access_token_decodes_in_python(crosscheck_payload, shared_secret):
    claims = auth_module.decode_token(crosscheck_payload["access_token"])
    assert set(claims) == {"sub", "type", "iat", "exp"}
    assert claims["sub"] == "crosscheck-u1"
    assert claims["type"] == "access"
    assert claims["exp"] - claims["iat"] == 30 * 60
    assert auth_module._decode_user_id_from_token(crosscheck_payload["access_token"]) == "crosscheck-u1"


def test_go_refresh_token_shape_and_rejection(crosscheck_payload, shared_secret):
    claims = auth_module.decode_token(crosscheck_payload["refresh_token"])
    assert set(claims) == {"sub", "jti", "type", "iat", "exp"}
    assert claims["jti"] == crosscheck_payload["refresh_token_id"]
    assert claims["type"] == "refresh"
    assert claims["exp"] - claims["iat"] == 30 * 24 * 3600

    # refresh 不得作为业务凭证：Python 严格解码路径给 40102，与 Go 中间件一致。
    with pytest.raises(AppException) as exc:
        auth_module._decode_user_id_strict(crosscheck_payload["refresh_token"])
    assert exc.value.code == ErrorCode.TOKEN_INVALID
    assert auth_module._decode_user_id_from_token(crosscheck_payload["refresh_token"]) is None


def test_go_tampered_token_rejected_in_python(crosscheck_payload, shared_secret):
    head, payload, signature = crosscheck_payload["access_token"].split(".")
    bad = f"{head}.{payload}{'A' if signature[-1] != 'A' else 'B'}{signature[2:]}"
    with pytest.raises(AppException) as exc:
        auth_module.decode_token(bad)
    assert exc.value.code == ErrorCode.TOKEN_INVALID


def test_go_refresh_hash_matches_python(crosscheck_payload, shared_secret):
    assert auth_module.hash_refresh_token(crosscheck_payload["refresh_token"]) == (
        crosscheck_payload["refresh_token_hash"]
    )
    assert len(crosscheck_payload["refresh_token_hash"]) == 64


def test_go_password_hash_verifies_without_rehash(crosscheck_payload):
    digest = crosscheck_payload["password_hash"]
    assert digest.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert auth_module.verify_password(CROSSCHECK_PASSWORD, digest) is True
    assert auth_module.verify_password(CROSSCHECK_PASSWORD + "1", digest) is False
    # True 表示 Go 参数与 argon2-cffi 默认值漂移，登录会静默重写哈希。
    assert auth_module._pwd_hasher.check_needs_rehash(digest) is False


def test_python_baseline_fixtures_not_stale():
    """Go 断言用的基线由当前 Python 实现生成，不是历史快照。"""
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert baseline["secret"] == "cross-language-test-secret"
    assert baseline["algorithm"] == "HS256"

    import jwt as pyjwt

    # 基线的 iat/exp 冻结在 2030-01-01（这样 Go 断言不会随时间失效），
    # pyjwt 的 iat 校验会把未来签发时间判为 immature，因此此处只关 iat 校验，
    # 签名与 exp 仍严格校验。真实 token 的 iat 由两侧共享时钟产生，见 tasks.md N1 勘误。
    decode_opts = {"verify_iat": False}
    access = baseline["tokens"]["access"]
    claims = pyjwt.decode(
        access["jwt"], baseline["secret"], algorithms=[baseline["algorithm"]],
        options=decode_opts,
    )
    assert claims["sub"] == access["sub"] and claims["exp"] == access["exp"]
    assert claims["iat"] == baseline["frozen_now_epoch"]

    refresh = baseline["tokens"]["refresh"]
    claims = pyjwt.decode(
        refresh["jwt"], baseline["secret"], algorithms=[baseline["algorithm"]],
        options=decode_opts,
    )
    assert claims["jti"] == refresh["jti"]
    assert auth_module.hash_refresh_token(refresh["jwt"]) == (
        baseline["refresh_token_hash"]["sha256_hex"]
    )

    for entry in baseline["argon2id"]["hashes"]:
        assert entry["hash"].startswith(baseline["argon2id"]["expected_prefix"])
        assert auth_module.verify_password(entry["password"], entry["hash"]) is entry["verify"]
        assert auth_module._pwd_hasher.check_needs_rehash(entry["hash"]) is False


def test_http_goldens_not_drifted():
    """FastAPI 是黄金响应来源；契约一变 Go 侧逐字节断言必须立刻报警。"""
    assert GOLDENS.exists(), "先运行 scripts/gen_go_http_goldens.py"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_go_http_goldens.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ── 同一张表的跨语言读写（灰度期真实风险，需 TEST_DATABASE_URL）──


def _with_loop(fn):
    import asyncio

    return asyncio.run(fn())


async def _create_python_user(url: str, email: str, username: str, password: str) -> str:
    """按 Python 现行写入口落一行，供 Go 读。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from backend.models.user import User

    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine)() as session:
            user_id = str(uuid4())
            session.add(User(
                id=user_id, email=email, username=username,
                password_hash=auth_module.hash_password(password),
            ))
            await session.commit()
            return user_id
    finally:
        await engine.dispose()


async def _fetch_hash(url: str, user_id: str) -> str:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine)() as session:
            return await session.scalar(
                text("SELECT password_hash FROM users WHERE id = :id"), {"id": user_id}
            )
    finally:
        await engine.dispose()


async def _purge(url: str, user_ids: list[str]) -> None:
    """沿用 purge 口径逐表删除，测试不留行。"""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine)() as session:
            for user_id in user_ids:
                # refresh_tokens 有指向 users 的外键，先删子表。
                await session.execute(
                    text("DELETE FROM refresh_tokens WHERE user_id = :id"), {"id": user_id}
                )
                parents = await session.execute(
                    text("DELETE FROM users WHERE id = :id"), {"id": user_id}
                )
                assert parents.rowcount == 1, f"用户行未删除: {user_id}"
            await session.commit()
    finally:
        await engine.dispose()


def _crosscheck_auth(go_bin: str, url: str, identifier: str, password: str) -> dict:
    proc = _run(
        go_bin,
        ["run", "./tools/crosscheck", "-mode=auth", "-database-url", url,
         "-login-identifier", identifier, "-password", password],
        180,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_go_reads_row_written_by_python(go_bin: str, shared_secret):
    """灰度切换前注册的用户，切换后必须能在 Go Core 登录。"""
    url = pg_test_url(purpose="跨语言互认，需 alembic upgrade head 后的隔离库")
    stamp = time.time_ns()
    email, username, password = f"py2go-{stamp}@example.test", f"py2go_{stamp}", "passw0rd"
    created = _with_loop(lambda: _create_python_user(url, email, username, password))
    ids = [created]
    try:
        payload = _crosscheck_auth(go_bin, url, username, password)
        ids.append(payload["go_registered_user_id"])
        assert payload["login_user_id"] == created
        claims = auth_module.decode_token(payload["login_access_token"])
        assert claims["sub"] == created and claims["type"] == "access"
        stored = _with_loop(lambda: _fetch_hash(url, created))
        assert auth_module.verify_password(password, stored) is True
        # Go 只读不写哈希：Python 侧的参数不该被换掉。
        assert auth_module._pwd_hasher.check_needs_rehash(stored) is False
    finally:
        _with_loop(lambda: _purge(url, ids))


def test_python_reads_row_written_by_go(go_bin: str, shared_secret):
    """Go Core 注册的用户，Python 侧必须能校验其密码并解出其 token。"""
    url = pg_test_url(purpose="跨语言互认，需 alembic upgrade head 后的隔离库")
    password = "go2py-passw0rd"
    payload = _crosscheck_auth(go_bin, url, "", password)
    go_user_id = payload["go_registered_user_id"]
    try:
        stored = _with_loop(lambda: _fetch_hash(url, go_user_id))
        assert stored and stored.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
        assert auth_module.verify_password(password, stored) is True
        # True ⇒ 参数漂移，Go 注册的账号在 Python 首次登录就会被无声重写哈希。
        assert auth_module._pwd_hasher.check_needs_rehash(stored) is False
        claims = auth_module.decode_token(payload["go_registered_access_token"])
        assert claims["sub"] == go_user_id
        assert auth_module.hash_refresh_token(payload["refresh_token"]) == (
            payload["refresh_token_hash"]
        )
    finally:
        _with_loop(lambda: _purge(url, [go_user_id]))
