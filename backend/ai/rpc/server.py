"""R3 · Python 侧 gRPC server（AI 面的进程入口）。

形状只有一条纪律：**handler 只做 `decode → 调能力函数 → encode → return`**。
判断口径一律从 `backend/ai/*` 或 service 层取，这里不许出现第二套业务分支——
一旦复制，Python 侧就会和 Go 领域门（`go-core/internal/domain/gate.go`）各判一份，
而那正是 R0 裁决要避免的事。闸在 `scripts/mutate_r3_gates.py`（把一条判断塞进 handler
必须能打红）。

进程形态（决策点 D-R3-2 的 2026-10-16 定稿：**独立 Deployment**，不是同进程）：
本文件是那个 Deployment 的 `CMD`（`python -m backend.ai.rpc.server`），监听 50051；
HTTP 面仍在 `backend/main.py`，探针也仍在它的 `/health`（见 deploy/k8s/50-ai-rpc.yaml）。

失败面按 `go-core/internal/aiclient/failure.go:53-85` 那张表回报，不自己发明：
· 入参不合契约 → `INVALID_ARGUMENT`（Go 侧 permanent，不重试）
· provider 429 → `RESOURCE_EXHAUSTED`（transient；`failure.go:60` 逐字点名「R3 的 handler 按这一码回报」）
· provider 5xx / 未预期异常 → `INTERNAL`（transient，日志留全栈，线上只回脱敏文案）
超时**不在这里报**：预算由调用方 deadline 决定，服务端抢报 `DEADLINE_EXCEEDED` 只会
让两边各有一套时间口径。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from typing import Callable, Optional

import grpc
from pydantic import ValidationError

from backend.ai.llm import get_embedding_model, llm
from backend.ai.safety import SAFETY_UNAVAILABLE, safety_service
from backend.config import settings
from backend.core.logging import (
    TRACE_SHAPE,
    set_trace_id,
    set_user_id,
    setup_logging,
)
from backend.gen import closenai_ai_rpc_pb2 as ai_pb2
from backend.gen import closenai_ai_rpc_pb2_grpc as ai_pb2_grpc
from backend.gen import closenai_common_pb2 as common_pb2
from backend.services.chat_service import chat_service

logger = logging.getLogger("closenai.ai_rpc")

# 与 Go 侧 `grpc.go:46-49` 的 `schemaVersion = "1"` 同一取值；不符即拒绝而不是兼容。
SCHEMA_VERSION = "1"
DEFAULT_PORT = 50051
# 留痕兜底：调用方没带 purpose 时用它。绝不能让 purpose 由栈帧推导——那会得到
# "server.Embed"，而 `ai_generation_runs.purpose` 的语义是「这次生成为了什么」
# （`closenai_ai_rpc.proto:108-110` 逐字点出这个坑）。ClassifySafety 不需要兜底：
# `check_text_tri` 内部固定写 "safety_service.check_text"，与 HTTP 面同一条 purpose 可对账。
PURPOSE_FALLBACK_EMBED = "rpc.embed"

# 批 T-E：TRACE_SHAPE 只许有一份定义，在 `core/logging.py`（R3 形状闸 test_r3_handler_shape
# 明令 rpc 层不许自己 `import re`，消毒跟着渲染层走）。这里只添键名。
METADATA_TRACE_KEY = "x-trace-id"


def _apply_trace(context, request=None) -> None:
    """入站 trace：metadata 优先于 EmbedRequest.trace_id，两者都要过消毒。

    两条载体的分工（proto:113 的字段只管 Embed；批 T-E 的 metadata 覆盖三个方法、
    且畸形请求也带得到）——都带且不一致时信 metadata，因为那是 Go 客户端拦截器
    （aiclient/trace.go）从 ctx 逐字搬运的，字段值可能被调用方漏填或填旧。

    不合形状的**整条丢弃、不编造**：本进程没有响应头可回（与 HTTP dispatch 的
    「再生成一个新 uuid」不同），凭空造一个只会产出两边 grep 都不到的孤儿 trace；
    而把原文记进日志就是注入面本身，所以警示行只带长度不带值。
    """
    raw = ""
    for key, value in context.invocation_metadata() or ():
        if key == METADATA_TRACE_KEY:
            raw = value
            break
    if not raw and request is not None:
        raw = getattr(request, "trace_id", "") or ""
    if not raw:
        return
    if TRACE_SHAPE.fullmatch(raw):
        set_trace_id(raw)
    else:
        logger.warning("丢弃畸形 trace（len=%d，原文不入日志）", len(raw))


def _schema_error(schema_version: str) -> Optional[str]:
    if schema_version != SCHEMA_VERSION:
        return f"schema_version 应为 {SCHEMA_VERSION!r}，收到 {schema_version!r}"
    return None


def _provider_failure_code(exc: BaseException) -> grpc.StatusCode:
    """把 provider 异常归成 gRPC 码。按 `status_code` 鸭子类型判，不 import openai。

    429 与 5xx 的区别要保住：Go 侧两者都是 transient，但只有 429 会触发
    「别拿退避去加剧限流」那条注释里的处置差异（`failure.go:60-61`）。
    """
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return grpc.StatusCode.RESOURCE_EXHAUSTED
    return grpc.StatusCode.INTERNAL


def _redacted_validation_detail(exc: ValidationError) -> str:
    """入参错的 gRPC detail 只带「字段:错型」，绝不带输入原文。

    为什么单独立一个函数：`str(ValidationError)` 会把 `input` 原文整段印进消息，而这条
    字符串是 gRPC status detail——Go 侧会把它写进日志（`aiclient` 的 Error 里就带 `Err`）。
    用户输入于是从内容面漏进错误面，跟着日志留下一份副本。
    """
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc") or ()) or "?"
        parts.append(f"{loc}:{err.get('type') or 'invalid'}")
    return ",".join(parts) or "invalid"


class ClosenAIService(ai_pb2_grpc.ClosenAIServicer):
    """三个方法，一个都不多（`closenai_ai_rpc.proto:1` 的第一稿纪律；rerank/capability 零调用点）。

    构造参数是测试注入口，不是配置项：跨语言互操作闸要用**真 server**复现 R2 的四类失败
    （超时／429／5xx／畸形），而真 provider 不可编排。生产走默认值，即真能力函数。
    """

    def __init__(
        self,
        *,
        embed: Optional[Callable] = None,
        classify: Optional[Callable] = None,
        chat_stream: Optional[Callable] = None,
    ) -> None:
        self._embed = embed or llm.embed
        self._classify = classify or safety_service.check_text_tri
        self._chat_stream = chat_stream or chat_service.rpc_chat_stream

    async def Embed(self, request, context):  # noqa: N802  名字由 proto 定
        # 批 T-E：trace 在入口就恢复，schema 校验失败的 abort 日志也带得上同一条 trace
        _apply_trace(context, request)
        bad = _schema_error(request.schema_version)
        if bad:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, bad)
            return ai_pb2.EmbedResponse()
        texts = list(request.texts)
        if not texts:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT, "texts 为空：调用方的编程错误，不是「这次没向量」")
            return ai_pb2.EmbedResponse()
        dim: Optional[int] = None
        if request.HasField("dimensions"):
            if request.dimensions == 0:
                # 0 维向量落不进 HALFVEC(512)，且会让 cron 的列均值算出 0（cron_service.py:1027）
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "dimensions=0 不是合法维度")
                return ai_pb2.EmbedResponse()
            dim = int(request.dimensions)

        if request.user_id:
            set_user_id(request.user_id)
        # request.trace_id 不再在这里裸塞（批 T-C 台账欠的那格）：入口的 _apply_trace
        # 已按 metadata 优先＋消毒处理过，走到这里的重复赋值只会绕过消毒。
        purpose = request.purpose or PURPOSE_FALLBACK_EMBED
        try:
            vectors = await self._embed(texts, dim=dim, purpose=purpose)
        except Exception as exc:  # noqa: BLE001
            code = _provider_failure_code(exc)
            logger.exception("Embed 失败 code=%s user=%s", code.value[0], request.user_id)
            await context.abort(code, "embedding 调用失败")
            return ai_pb2.EmbedResponse()

        if len(vectors) != len(texts):
            # 「顺序即契约」（proto:120-122）：条数对不上时 Go 侧只能整批判畸形，
            # 与其发一批无法对回原文的字节，不如如实报内部错。
            logger.error("Embed 返回条数 %d ≠ 请求条数 %d", len(vectors), len(texts))
            await context.abort(grpc.StatusCode.INTERNAL, "向量条数与请求不一致")
            return ai_pb2.EmbedResponse()
        return ai_pb2.EmbedResponse(
            schema_version=SCHEMA_VERSION,
            vectors=[ai_pb2.EmbeddingVector(values=v) for v in vectors],
            dimensions=len(vectors[0]) if vectors and vectors[0] else 0,
            model=get_embedding_model(),
        )

    async def ClassifySafety(self, request, context):  # noqa: N802
        # TextInput 没有 trace_id 字段（proto:136-143）——这一面**只有** metadata 一条载体，
        # 少了对字段兜底的余地，所以批 T-E 的两条挂点（Go 拦截器／这里）任何一侧退化都会在这里显形。
        _apply_trace(context)
        bad = _schema_error(request.schema_version)
        if bad:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, bad)
            return ai_pb2.SafetyResult()
        scope = request.scope or "chat"
        if scope not in ("chat", "assertion"):
            # 空＝chat 是契约写的（proto:148「映射层不许把空读成全档都判」）；
            # 未知值不能默默按 chat 跑——那会让调用方以为越界句式被判过了。
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"未知 scope：{scope!r}")
            return ai_pb2.SafetyResult()

        try:
            verdict = await self._classify(request.text, request.language, scope=scope)
        except Exception as exc:  # noqa: BLE001
            code = _provider_failure_code(exc)
            logger.exception("ClassifySafety 失败 code=%s", code.value[0])
            await context.abort(code, "安全判定调用失败")
            return ai_pb2.SafetyResult()

        status = {
            "ok": common_pb2.SAFETY_STATUS_OK,
            "crisis": common_pb2.SAFETY_STATUS_CRISIS,
            # D-G17（批 4）：非危机的违规面（banned/overreach/model）从 crisis 挪到 unsafe，
            # 这样 Go 侧拦下它时不必把它说成「危机信号」。少这一格的后果不是放行——
            # 落到下面的 None 分支会变成 unavailable，把「拦下内容」说成「判定缺失」。
            "unsafe": common_pb2.SAFETY_STATUS_UNSAFE,
            "unavailable": common_pb2.SAFETY_STATUS_UNAVAILABLE,
        }.get(verdict["status"])
        if status is None:
            # 四态之外的值不能猜成 OK：Go 侧读到的必须是「判定缺失」（D-8）。
            logger.error("ClassifySafety 返回了契约外的状态 %r", verdict.get("status"))
            status = common_pb2.SAFETY_STATUS_UNAVAILABLE
            verdict = {**verdict, "status": SAFETY_UNAVAILABLE}
        return ai_pb2.SafetyResult(
            schema_version=SCHEMA_VERSION,
            safety=common_pb2.Safety(
                status=status, categories=list(verdict.get("categories") or [])
            ),
            reason=verdict.get("reason") or "",
            matched=list(verdict.get("matched") or []),
            advice_blocked=bool(verdict.get("advice_blocked")),
        )

    async def StreamChat(self, request, context):  # noqa: N802
        _apply_trace(context)
        bad = _schema_error(request.schema_version)
        if bad:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, bad)
            return
        if request.user_id:
            set_user_id(request.user_id)
        try:
            # 上限与取值面复用 HTTP 的同一个 Pydantic 模型（proto:207-208 不许在这里重抄数字）
            parsed = chat_service.validate_stream_input(request.content, request.message_type)
        except ValidationError as exc:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT, f"入参不合契约：{_redacted_validation_detail(exc)}"
            )
            return

        try:
            async for kind, payload in self._chat_stream(
                user_id=request.user_id,
                content=parsed.content,
                message_type=parsed.message_type,
                language=request.language,
            ):
                if kind == "memory":
                    # 中间块不许同时带 content 与 memories（`grpc.go:600-602` 判的是契约违规：
                    # SSE 侧这是两条事件，proto 里没有字段能定它们的先后）。
                    yield ai_pb2.ChatChunk(
                        schema_version=SCHEMA_VERSION,
                        memories=[
                            ai_pb2.StreamMemory(
                                id=str(m.get("id", "")), content=str(m.get("content", "")),
                                category=str(m.get("category", "")),
                            )
                            for m in payload
                        ],
                    )
                else:
                    yield ai_pb2.ChatChunk(schema_version=SCHEMA_VERSION, content=str(payload))
            # 收尾恒为 DONE，且不带 content：现网这条流的收尾本来就是 done 事件
            # （`routers/chat.py:132` `_sse_event("done", {})`），DONE 再带 content 会被
            # Go 判畸形（`grpc.go:609-611`）。BLOCKED／DEGRADED 由 Go 侧产生，不是本 server。
            yield ai_pb2.ChatChunk(schema_version=SCHEMA_VERSION, finish=ai_pb2.CHAT_FINISH_DONE)
        except Exception as exc:  # noqa: BLE001
            code = _provider_failure_code(exc)
            logger.exception("StreamChat 失败 code=%s user=%s", code.value[0], request.user_id)
            await context.abort(code, "流式生成失败")


def build_server(service: ClosenAIService, *, port: int = DEFAULT_PORT):
    """建好并绑定，但**不**启动。返回 (server, 实际端口)。

    拆成这一步是给互操作闸用的：`port=0` 拿到随机端口后，Go 侧才有办法连上来。
    """
    server = grpc.aio.server(options=[("grpc.max_concurrent_streams", 100)])
    ai_pb2_grpc.add_ClosenAIServicer_to_server(service, server)
    bound = server.add_insecure_port(f"[::]:{port}")
    if bound == 0:
        raise RuntimeError(f"gRPC 端口绑定失败：{port}")
    return server, bound


async def serve_forever(*, port: int = DEFAULT_PORT) -> None:
    server, bound = build_server(ClosenAIService(), port=port)
    await server.start()
    logger.info("closenai AI gRPC server 已启动，端口 %d", bound)
    loop = asyncio.get_running_loop()
    stopping: asyncio.Future = loop.create_future()

    def _on_signal() -> None:
        if not stopping.done():
            stopping.set_result(None)

    for sig in (signal.SIGTERM, signal.SIGINT):
        # k3s 滚动时先收 SIGTERM：不优雅停服，正在流式输出的那条对话会被客户端读成断流
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _on_signal)

    terminated = asyncio.ensure_future(server.wait_for_termination())
    await asyncio.wait({terminated, stopping}, return_when=asyncio.FIRST_COMPLETED)
    terminated.cancel()
    # 只给 3s 收尾，短于 pod 的 terminationGracePeriodSeconds=30（50-ai-rpc.yaml）；
    # 3s 也是「一条 token 流的合理尾巴」而不是无限等
    await server.stop(grace=3)


def main() -> None:
    setup_logging(getattr(settings, "log_level", "INFO"))
    port = int(os.environ.get("CLOSEN_AI_RPC_PORT", DEFAULT_PORT))
    logging.getLogger("closenai.ai_rpc").info("AI 面独立进程启动，端口 %d", port)
    try:
        asyncio.run(serve_forever(port=port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
