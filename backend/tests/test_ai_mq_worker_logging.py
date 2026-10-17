"""批 T-A 闸 G-A1：``python -m backend.ai.mq.worker`` 必须装上与请求进程同源的日志配置。

判据本体＝「根 handler 挂着 ContextFilter 且格式串含 trace_id」；
变异 M-A1（把 setup_logging 换回裸 basicConfig）打红的正是这一条。
"""
from __future__ import annotations

import logging

import pytest

from backend.ai.mq import worker
from backend.ai.mq.config import SwitchError
from backend.core.logging import ContextFilter


@pytest.fixture()
def restore_root():
    """setup_logging 会清空根 handler——测完必须还原，免得污染同会话的其它用例。"""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    yield root
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def test_worker_main_installs_context_filter(restore_root, monkeypatch):
    # 用开关非法逼 main() 在装好日志后、进 asyncio 前早退：
    # 既真实走到 setup_logging 那一步，又不需要 broker。
    def boom():
        raise SwitchError("仅闸测：让 main() 装完日志就早退")

    monkeypatch.setattr(worker, "load_switch", boom)

    rc = worker.main([])
    assert rc == 2  # SwitchError 分支的退出码本身也是既有契约

    root = restore_root
    assert len(root.handlers) == 1, "setup_logging 的幂等形状：根上只有一个 handler"
    handler = root.handlers[0]
    assert any(isinstance(f, ContextFilter) for f in handler.filters), (
        "worker 进程的根 handler 必须挂 ContextFilter，否则日志行没有 trace_id 字段"
    )

    # 正向证人（归零闸的反证）：真格式化一条，字段必须在输出里逐字可见。
    record = logging.LogRecord(
        name="gate", level=logging.INFO, pathname=__file__, lineno=1,
        msg="witness", args=(), exc_info=None,
    )
    for f in handler.filters:
        assert f.filter(record)
    rendered = handler.formatter.format(record)
    assert "trace_id=-" in rendered, rendered
