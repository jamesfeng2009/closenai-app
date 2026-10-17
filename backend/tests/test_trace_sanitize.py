"""G-C3（工作单批 T-C，Python 侧）：入站 X-Trace-Id 消毒。

判据两层：
1. `sanitize_trace_id` 的形状表——什么会被拒的直接证据（含 `abc\\n` 这种
   Python `$` 会放过、fullmatch 必须拦住的形态）；
2. 走真实 `TraceIdMiddleware` 的功能层——不合形值不回显、不进 contextvar，
   日志 formatter 渲染出的行里不含 CR/LF（伪造日志行的注入面）。

变异：把 dispatch 里的 sanitize_trace_id(...) 摘掉改回 raw ⇒ 本闸红（M-C3 同类）。
"""
import logging
import re
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.core.logging import ContextFilter, get_trace_id
from backend.core.middleware import TRACE_HEADER, TraceIdMiddleware, sanitize_trace_id

OK = [
    "T-42",
    "0f6c3a9b-1111-2222-3333-444455556666",
    "a" * 128,
    "a.b_c-d",
]
BAD = {
    "空串": "",
    "CR+LF 注入": "evil\r\nINJECTED: 1",
    "裸 LF": "evil\nfake",
    "裸 CR": "evil\rfake",
    "尾随换行": "abc\n",
    "控制字符": "ab\x00cd",
    "超长 129": "a" * 129,
    "空格": "a b",
}


@pytest.mark.parametrize("value", OK)
def test_compliant_values_pass_verbatim(value):
    assert sanitize_trace_id(value) == value


@pytest.mark.parametrize("value", BAD.values(), ids=BAD.keys())
def test_malformed_values_are_zeroed(value):
    assert sanitize_trace_id(value) == ""


def test_none_is_zeroed():
    assert sanitize_trace_id(None) == ""


def _build_app() -> FastAPI:
    """最小 app：端点把「处理请求时 contextvar 里的 trace」回写进响应体。"""
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)

    @app.get("/probe")
    async def probe():
        return {"in_ctx": get_trace_id()}

    return app


async def _probe(raw: str | None) -> dict:
    transport = ASGITransport(app=_build_app())
    headers = {TRACE_HEADER: raw} if raw is not None else {}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/probe", headers=headers)
    return {"status": r.status_code, "echo": r.headers.get(TRACE_HEADER), "in_ctx": r.json()["in_ctx"]}


@pytest.mark.asyncio
async def test_compliant_header_roundtrips_everywhere():
    out = await _probe("T-GC3-PY")
    assert out["status"] == 200
    assert out["echo"] == "T-GC3-PY", "回执头必须逐字回显采用值"
    assert out["in_ctx"] == "T-GC3-PY", "contextvar 里必须是同一个值（日志行与回执同源的根）"


@pytest.mark.asyncio
async def test_injected_header_not_echoed_not_in_logs():
    evil = "abc\r\nX-Fake: 1"
    out = await _probe(evil)
    assert out["status"] == 200
    adopted = out["in_ctx"]
    assert adopted != evil and evil not in adopted
    assert "\r" not in adopted and "\n" not in adopted, "注入字符不得进入采用值"
    assert out["echo"] == adopted, "回执与日志的 trace 必须同源"
    # 采用值本身必须仍是合形的（重生成走 uuid4，也要过同一形状闸的语义）
    assert re.fullmatch(r"[A-Za-z0-9._-]{1,128}", adopted)
    # 渲染出来的日志行里不许出现被注入的那一行，物理行数也不许被头值撑成两行
    rendered = _render_one_line(adopted)
    assert "\r" not in rendered
    assert rendered.count("\n") <= 2, "formatter 的换行之外多出行 ⇒ 头值伪造了日志行"
    assert "X-Fake" not in rendered


@pytest.mark.asyncio
async def test_missing_header_still_gets_fresh_trace():
    out = await _probe(None)
    adopted = out["in_ctx"]
    assert adopted and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", adopted)
    # uuid4 逐字：Python 侧缺头重生成的形状与 backend 旧行为一致，不另造方言
    assert adopted == str(uuid.UUID(adopted))


def _render_one_line(trace_id: str) -> str:
    """用生产同款 formatter+ContextFilter 渲染一行：判「日志行长什么样」的最终证据。

    ContextFilter 读的是 contextvar，所以这里先把待渲染值塞回去——
    判据链是「中间件采用什么 ⇒ 行里出现什么」，adopted 值正是中间件采用的那个。
    """
    from backend.core.logging import set_trace_id, setup_logging

    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    setup_logging()
    set_trace_id(trace_id)
    try:
        handler = root.handlers[0]
        filt = [f for f in handler.filters if isinstance(f, ContextFilter)]
        assert filt, "根 handler 必须挂 ContextFilter（批 T-A 的同一条前提）"
        record = logging.LogRecord("closenai.test", logging.INFO, "f", 1, "一行测试日志", None, None)
        filt[0].filter(record)
        assert record.trace_id == (trace_id or "-"), record.trace_id
        return handler.formatter.format(record)
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        set_trace_id("")


def test_formatter_line_for_dash_trace():
    """无请求上下文（cron/worker 启动期）渲染出的行也必须带 trace 格子，grep 规则才统一。"""
    line = _render_one_line("")
    assert "trace_id=-" in line
