#!/usr/bin/env python
"""R3-④ · 跨语言互操作闸用的**真 server** 驱动程序（不是生产入口）。

为什么单开一个脚本而不是给 `backend.ai.rpc.server` 加测试开关：
生产入口的职责是「按配置起真能力面」，把「按 env 切换成故障面」塞进去等于让线上多一条
可以被环境变量改写的代码路径——那比多一个脚本贵得多。本文件只做三件事：
按 `R3_INTEROP_MODE` 组一个 servicer、绑 0 号端口、把端口打给 stdout 后停在那里等信号。

被验对象全是真的：真 `grpc.aio` server、真 `backend/ai/rpc/server.py` 的 handler 与
encode/abort 逻辑、真 `backend/gen` 生成物。只有最底下的能力函数（provider 调用）是编排出来的，
因为真 provider 不可编排（429 不能想调就调、超时不能说 2 秒就 2 秒）。
`malformed` / `malformed_done` 两种模式换一个故意的坏 servicer：要测的是 **Go 的 decode
校验能不能在真字节上把契约违规判住**，所以违反必须由服务端做出，而不是由 Python 的 handler
造好再拦下。

跑法（由 `backend/tests/test_r3_grpc_server_interop.py` 自动起停，手工跑法留在这里）：

    R3_INTEROP_MODE=rate425 .venv/bin/python scripts/r3_interop_server.py
    → stdout 第一行 "PORT 51234"
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

# 本文件由 pytest 用 `python scripts/r3_interop_server.py` 直接起，不走 -m，
# 所以要自己把仓根塞进 sys.path（与 pyproject 的 pythonpath=["."] 同一口径）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import grpc

from backend.ai.rpc.server import SCHEMA_VERSION, ClosenAIService, build_server
from backend.gen import closenai_ai_rpc_pb2 as ai_pb2
from backend.gen import closenai_ai_rpc_pb2_grpc as ai_pb2_grpc
from backend.gen import closenai_common_pb2 as common_pb2

MODE = os.environ.get("R3_INTEROP_MODE", "ok")
# 慢模式的等待时长：必须**大于** Go 侧 safetyDeadline(800ms)、小于 embedDeadline(10s)，
# 这样同一条慢能力能同时验证「安全面超时＝拦下」与「向量面在预算内＝成功」两格。
SLEEP_SECONDS = 2.0


class _ProviderError(Exception):
    """带 `status_code` 的 provider 异常（openai.APIStatusError 的鸭子形态）。"""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"provider {status_code}")
        self.status_code = status_code


# ── 能力面（真 handler 之下的那层）────────────────────────


async def _embed(texts, *, dim=None, purpose=None):
    if MODE == "slow":
        await asyncio.sleep(SLEEP_SECONDS)
    elif MODE == "rate429":
        raise _ProviderError(429)
    elif MODE == "err500":
        raise _ProviderError(500)
    width = int(dim) if dim else 3
    return [[0.1 * (i + 1) + 0.01 * j for j in range(width)] for i in range(len(texts))]


async def _classify(text, language, *, scope="chat"):
    if MODE == "slow":
        await asyncio.sleep(SLEEP_SECONDS)
    elif MODE == "rate429":
        raise _ProviderError(429)
    elif MODE == "err500":
        raise _ProviderError(500)
    if "不想活" in text:
        return {
            "status": "crisis", "reason": "crisis", "matched": ["不想活"],
            "categories": ["self_harm"], "advice_blocked": False,
        }
    return {
        "status": "ok", "reason": "", "matched": [], "categories": [],
        "advice_blocked": False,
    }


async def _chat_stream(*, user_id, content, message_type, language):
    if MODE == "slow":
        await asyncio.sleep(SLEEP_SECONDS)
        yield "token", "慢到的这一句永远不该被 Go 读到"
        return
    if MODE == "rate429":
        yield "token", "前半句"
        raise _ProviderError(429)
    if MODE == "err500":
        raise _ProviderError(500)
    yield "memory", [{"id": "m1", "content": "你偏好早起", "category": "preference"}]
    for piece in ("今天", "过得", "怎样"):
        yield "token", piece


# ── 故意的坏 servicer（违反的是 Go 的 decode 校验，不是自家 handler 的护栏）──


class _BadService(ai_pb2_grpc.ClosenAIServicer):
    """只犯「Go 侧 decode 判得住」的那几种错，且一律**成功返回**而不是 abort。

    为什么不能用 abort：abort 走的是状态码面（R2 的 classify 表），而这一档要钉的是
    `grpc.go:519/543/581` 那三处 decode 校验在真字节上确实会红、且红成 permanent。
    """

    async def Embed(self, request, context):  # noqa: N802
        # 条数 ≠ 请求条数：顺序即契约当场没法对回原文（grpc.go:523）
        return ai_pb2.EmbedResponse(
            schema_version=SCHEMA_VERSION,
            vectors=[ai_pb2.EmbeddingVector(values=[0.1, 0.2, 0.3])],
            dimensions=3,
            model="bad-embed-model",
        )

    async def ClassifySafety(self, request, context):  # noqa: N802
        # 三态之外的枚举值：Go 不许按 proto3「容忍未知值」把它读成 OK（grpc.go:547-551）
        return ai_pb2.SafetyResult(
            schema_version=SCHEMA_VERSION,
            safety=common_pb2.Safety(status=common_pb2.SAFETY_STATUS_UNSPECIFIED),
        )

    async def StreamChat(self, request, context):  # noqa: N802
        if MODE == "malformed_done":
            yield ai_pb2.ChatChunk(schema_version=SCHEMA_VERSION, content="半句")
            # DONE 再带 content＝两个真相源（grpc.go:609-611；现网 done 事件不带载荷）
            yield ai_pb2.ChatChunk(
                schema_version=SCHEMA_VERSION,
                finish=ai_pb2.CHAT_FINISH_DONE,
                content="收尾又不为空",
            )
            return
        # 一个块里同时有 content 与 memories：SSE 侧这是两条事件，proto 定不了序（grpc.go:600-602）
        yield ai_pb2.ChatChunk(
            schema_version=SCHEMA_VERSION,
            content="同时出现",
            memories=[ai_pb2.StreamMemory(id="m1", content="你偏好早起", category="preference")],
        )


def _build() -> tuple:
    if MODE in ("malformed", "malformed_done"):
        server, port = build_server(_BadService(), port=0)
    else:
        service = ClosenAIService(
            embed=_embed, classify=_classify, chat_stream=_chat_stream
        )
        server, port = build_server(service, port=0)
    return server, port


async def main() -> None:
    server, port = _build()
    await server.start()
    # 握手协议只有一行：Go/pytest 侧读到这行才开始拨号，端口是 OS 给的随机值
    print(f"PORT {port}", flush=True)

    loop = asyncio.get_running_loop()
    stopping = loop.create_future()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: stopping.done() or stopping.set_result(None))
        except NotImplementedError:  # pragma: no cover  Windows
            pass
    await stopping
    await server.stop(0)


if __name__ == "__main__":
    if MODE not in ("ok", "slow", "rate429", "err500", "malformed", "malformed_done"):
        print(f"未知 R3_INTEROP_MODE：{MODE}", file=sys.stderr, flush=True)
        raise SystemExit(2)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
