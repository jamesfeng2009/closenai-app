"""N1 部署纵切的容器级验收：两个镜像跑起来后跨语言互认（灰度期的真实故障面）。

覆盖 test_go_auth_interop.py 覆盖不到的东西——那边只验编解码与同一张表的行读，
这里验**两个 HTTP 进程共享一个 JWT_SECRET 与一个 PG** 时的端到端行为：

  1. Python 注册的用户，Go 用同一个 token 能取回 profile
  2. Go 注册的用户，Python 用同一个 token 能取回 profile
  3. GET /api/v1/auth/me 两侧响应字段集逐字一致（golden 用例只拍 register 校验，不拍这个）
  4. 篡改签名两侧同时拒绝
  5. 「写后跨进程读」必须一次命中（attempts == 1）。2026-10-13 之前这里是
     `backend/database.py` 的 commit 落在依赖 teardown、跑在响应发出之后所带来的
     可见性窗口；现由 `backend/core/db_route.py` 把提交挪到响应字节发出之前。

前置：本机 docker + 两个本地镜像 + 一个可达的 PostgreSQL。任一缺失一律 skip，
不算失败——CI 上没有 Docker 时这条不应变红。

  docker build --build-arg GO_IMAGE=docker.m.daocloud.io/library/golang:1.27-alpine \
      -f go-core/Dockerfile -t closenai-go-api:0.1.0-local go-core
  docker build --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.13-slim \
      -f deploy/Dockerfile.py-api -t closenai-api:0.1.0-local .
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass

import pytest

GO_IMAGE = os.environ.get("GO_API_IMAGE", "closenai-go-api:0.1.0-local")
PY_IMAGE = os.environ.get("PY_API_IMAGE", "closenai-api:0.1.0-local")
PG_CONTAINER = os.environ.get("PG_CONTAINER", "closenai-pg-dev")
PG_DB = os.environ.get("PG_DB", "closenai")
PG_USER = os.environ.get("PG_USER", "postgres")
PG_PASSWORD = os.environ.get("PG_PASSWORD", "devpw")
# 容器视角下的宿主机地址；OrbStack 与 Docker Desktop 都注入 host.docker.internal。
PG_HOST_IN_CONTAINER = os.environ.get("PG_HOST_IN_CONTAINER", "host.docker.internal")
PG_PORT = os.environ.get("PG_PORT", "55432")

SMOKE_SECRET = "n1-service-interop-secret-0123456789"


def _docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _image_exists(tag: str) -> bool:
    return _docker("image", "inspect", tag).returncode == 0


def _pg_reachable() -> bool:
    probe = _docker(
        "exec", PG_CONTAINER, "psql", "-U", PG_USER, "-d", PG_DB, "-Atc", "select 1"
    )
    return probe.returncode == 0 and probe.stdout.strip() == "1"


@pytest.fixture(scope="module")
def stack():
    if shutil.which("docker") is None:
        pytest.skip("docker 不在 PATH")
    for tag in (GO_IMAGE, PY_IMAGE):
        if not _image_exists(tag):
            pytest.skip(f"本机没有镜像 {tag}（构建命令见模块 docstring）")
    if not _pg_reachable():
        pytest.skip(f"容器 {PG_CONTAINER} 里的 PG 不可达，跨服务验收需要真库")

    pg_creds = f"{PG_USER}:{PG_PASSWORD}@{PG_HOST_IN_CONTAINER}:{PG_PORT}/{PG_DB}"
    urls = {
        "go": f"postgresql://{pg_creds}",
        # Python 侧走 psycopg3 driver，scheme 不同但同一个库同一个用户。
        "py": f"postgresql+psycopg://{pg_creds}",
    }
    handles = {}
    for key, image in (("go", GO_IMAGE), ("py", PY_IMAGE)):
        port = _free_port()
        run = _docker(
            "run", "-d", "--name", f"closenai-{key}-interop-{os.getpid()}",
            "-p", f"{port}:8000",
            "-e", f"DATABASE_URL={urls[key]}",
            "-e", f"JWT_SECRET={SMOKE_SECRET}",
            "-e", "ENVIRONMENT=development",
            image,
        )
        if run.returncode != 0:
            _teardown(handles)
            pytest.fail(f"启动 {image} 失败: {run.stderr[-500:]}")
        handles[key] = (run.stdout.strip(), port)

    base = {k: f"http://127.0.0.1:{port}" for k, (_, port) in handles.items()}
    try:
        _wait_ready(base["go"], "/healthz")
        _wait_ready(base["py"], "/health")
        yield base, handles
    finally:
        _teardown(handles)


def _teardown(handles: dict) -> None:
    for key, (cid, _) in handles.items():
        _docker("rm", "-f", cid)


def _wait_ready(base: str, path: str, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + path, timeout=5) as r:
                if r.status == 200:
                    return
                last = f"HTTP {r.status}"
        except Exception as exc:  # 容器还在起，或 PG 尚未可达
            last = repr(exc)
        time.sleep(1)
    raise AssertionError(f"{base}{path} 在 {timeout}s 内未就绪: {last}")


@dataclass
class Resp:
    status: int
    body: dict


def _call(base: str, path: str, payload: dict | None = None, token: str | None = None,
          allow_error: bool = False) -> Resp:
    headers = {"content-type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        base + path, data=data, headers=headers, method="POST" if data else "GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return Resp(r.status, json.loads(r.read()))
    except urllib.error.HTTPError as e:
        if allow_error:
            return Resp(e.code, json.loads(e.read()))
        raise AssertionError(f"{base}{path} -> HTTP {e.code}") from None


def _register(base: str, tag: str) -> tuple[str, str, dict]:
    stamp = f"{tag}-{uuid.uuid4().hex[:10]}@example.test"
    r = _call(base, "/api/v1/auth/register", {
        "email": stamp,
        "username": stamp.split("@")[0].replace("-", "_"),
        "password": "passw0rd",
    })
    assert r.status == 200 and r.body["code"] == 200, r.body
    return r.body["data"]["access_token"], r.body["data"]["user"]["id"], r.body["data"]["user"]


def _purge_emails(emails: list[str]) -> None:
    if not emails:
        return
    quoted = ",".join(f"'{e}'" for e in emails)
    _docker(
        "exec", PG_CONTAINER, "psql", "-U", PG_USER, "-d", PG_DB, "-c",
        f"DELETE FROM refresh_tokens WHERE user_id IN (SELECT id FROM users WHERE email IN ({quoted}))",
        "-c", f"DELETE FROM users WHERE email IN ({quoted})",
    )


def test_cross_service_token_interop_and_payload_parity(stack):
    base, _ = stack
    go, py = base["go"], base["py"]
    made: list[str] = []
    try:
        py_token, py_id, py_user = _register(py, "interop-py")
        made.append(py_user["email"])

        # 写后跨进程读必须**一次命中**。重试循环只为把「几次才见到」写进失败信息。
        attempts, me = 0, None
        for attempts in range(1, 31):
            try:
                me = _call(go, "/api/v1/auth/me", token=py_token)
                if me.status == 200:
                    break
            except AssertionError:
                pass
            time.sleep(0.1)
        assert me is not None and me.status == 200 and me.body["data"]["id"] == py_id, (
            f"Go 侧 {attempts} 次都读不到 Python 刚注册的用户"
        )
        assert attempts == 1, (
            f"跨进程『写后立刻读』重试了 {attempts} 次 —— 提交又跑回响应之后了，"
            "修复见 backend/core/db_route.py，闸见 backend/tests/test_db_commit_boundary.py"
        )

        go_token, go_id, go_user = _register(go, "interop-go")
        made.append(go_user["email"])
        back = _call(py, "/api/v1/auth/me", token=go_token)
        assert back.status == 200 and back.body["data"]["id"] == go_id, back.body

        # /auth/me 的字段集是前端 api.ts:866 直接消费的，切流后不许变。
        assert sorted(go_user) == sorted(py_user) == ["email", "email_verified", "id",
                                                     "name", "tier", "username"]
        go_me = _call(go, "/api/v1/auth/me", token=go_token)
        py_me = _call(py, "/api/v1/auth/me", token=py_token)
        assert sorted(go_me.body["data"]) == sorted(py_me.body["data"])

        # 信封键序也是冻结面的一部分（contracts/endpoints.md）。
        assert list(go_me.body) == ["code", "message", "data", "trace_id", "user_id"]
        assert list(py_me.body) == ["code", "message", "data", "trace_id", "user_id"]

        tampered = py_token[:-6] + "AAAAAA"
        for label, url in (("go", go), ("py", py)):
            bad = _call(url, "/api/v1/auth/me", token=tampered, allow_error=True)
            assert bad.status == 404 and bad.body["code"] == 40401, (label, bad.status, bad.body)
    finally:
        _purge_emails(made)


def test_both_runtimes_serve_the_frozen_health_envelope(stack):
    base, _ = stack
    go_health = _call(base["go"], "/health")
    py_health = _call(base["py"], "/health")
    assert go_health.body["data"] == py_health.body["data"], "GET /health 必须逐字一致"
    assert list(go_health.body)[:3] == ["code", "message", "data"]

    # /healthz 是 Go 侧新增的进程就绪探针，不在 98 条契约登记里，Python 侧本就不该有。
    z = _call(base["go"], "/healthz")
    assert z.status == 200 and z.body["data"]["status"] == "ok"
    pz = _call(base["py"], "/healthz", allow_error=True)
    assert pz.status == 404, f"py-api 不应提供 /healthz，实际 {pz.status}"
