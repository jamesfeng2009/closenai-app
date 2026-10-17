"""R3-③ · gRPC handler 的畸形输入闸：真 server + 真 aio client，端口 0。

为什么必须走真 transport，而不是直接 `await service.Embed(request, fake_context)`：
· 假 context 把 `context.abort()` 变成一次手动 mock，而 R2 的 Go client 判的是**线上字节**
  （`go-core/internal/aiclient/grpc.go` 的三处 decode 校验：embed 计数/维度 :519、
  safety 非空且状态合法 :543、chunk 不得同时带 content 与 memories 且 DONE 不得带 content :581）。
  中间那层 gRPC 编码——状态码怎么上线、detail 里带不带栈、流式收尾怎么发——恰是跨语言最容易骗人的地方。
· 「不抛栈」这条只有真 transport 测得到：假 context 收得到完整 detail，client 侧看到的却是
  gRPC 截断后的消息。

四类失败（超时／429／5xx／畸形）里，畸形与 429/5xx 在这一层判；超时留给 R3-④ 的真进程互操作闸
（Go 侧 deadline 那一层才有）。

变异靶（`scripts/mutate_r3_gates.py` 的 R 组）：把 `INVALID_ARGUMENT` 改成 `INTERNAL`
（畸形输入于是变成可重试，R2 的 permanent/transient 分形被打穿）、把未知 scope 默默按 chat 跑、
把四态外的值猜成 OK、把 `str(ValidationError)` 原样回出去——这里各打红一条。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Callable, List, Optional, Tuple

import grpc
import pytest

from backend.ai.rpc import server as srv
from backend.ai.rpc.server import SCHEMA_VERSION, ClosenAIService, build_server
from backend.gen import closenai_ai_rpc_pb2 as ai_pb2
from backend.gen import closenai_ai_rpc_pb2_grpc as ai_pb2_grpc
from backend.gen import closenai_common_pb2 as common_pb2

STACK_MARKERS = ("Traceback", 'File "', "line ", ".py:")


class _ProviderError(Exception):
    """带 `status_code` 的 provider 异常（openai.APIStatusError 的鸭子形态）。"""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"provider {status_code}")
        self.status_code = status_code


@asynccontextmanager
async def _rpc(
    *,
    embed: Optional[Callable] = None,
    classify: Optional[Callable] = None,
    chat_stream: Optional[Callable] = None,
):
    """起真 server（port 0）+ 真 aio client；能力面按需注入，端口与编码一律不 mock。"""
    service = ClosenAIService(embed=embed, classify=classify, chat_stream=chat_stream)
    server, port = build_server(service, port=0)
    original_model = srv.get_embedding_model
    # 模型名由 provider 配置决定，测试环境里不该跟着 .env 漂；这里只钉「回带了 model 字段」
    srv.get_embedding_model = lambda: "unit-test-embed-model"
    await server.start()
    channel = grpc.aio.insecure_channel(f"localhost:{port}")
    try:
        yield ai_pb2_grpc.ClosenAIStub(channel)
    finally:
        srv.get_embedding_model = original_model
        await channel.close()
        await server.stop(0)


async def _call_error(coro) -> grpc.RpcError:
    """跑一次 unary 调用，断言它必须失败并把 RpcError 交出来。"""
    try:
        await coro
    except grpc.RpcError as exc:
        return exc
    pytest.fail("畸形输入被服务端接了（本该 INVALID_ARGUMENT）")


async def _collect_stream(stub, request) -> Tuple[List[Any], Optional[grpc.RpcError]]:
    chunks: List[Any] = []
    error: Optional[grpc.RpcError] = None
    try:
        async for chunk in stub.StreamChat(request):
            chunks.append(chunk)
    except grpc.RpcError as exc:
        error = exc
    return chunks, error


# ── Embed ────────────────────────────────────────────────


async def test_embed_rejects_schema_version_mismatch():
    async with _rpc() as stub:
        err = await _call_error(
            stub.Embed(ai_pb2.EmbedRequest(schema_version="2", texts=["a"]))
        )
    assert err.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "schema_version" in err.details()
    assert not any(m in err.details() for m in STACK_MARKERS)


async def test_embed_rejects_empty_texts():
    async with _rpc() as stub:
        err = await _call_error(stub.Embed(ai_pb2.EmbedRequest(schema_version=SCHEMA_VERSION)))
    assert err.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "texts" in err.details()


async def test_embed_rejects_zero_dimensions():
    """`dimensions=0` 落不进 HALFVEC(512)，也会被 cron 的列均值当成 0 维（cron_service.py:1027）。"""
    async with _rpc() as stub:
        err = await _call_error(
            stub.Embed(
                ai_pb2.EmbedRequest(schema_version=SCHEMA_VERSION, texts=["a"], dimensions=0)
            )
        )
    assert err.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "dimensions" in err.details()


async def test_embed_absent_dimensions_forwards_none_and_echoes_shape():
    seen = {}
    # 两条向量取值不同，才测得出「顺序即契约」；取 float32 表示不了的十进制，才测得出精度口径
    expected = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    async def _embed(texts, *, dim=None, purpose=None):
        seen["dim"] = dim
        seen["purpose"] = purpose
        return [list(v) for v in expected]

    async with _rpc(embed=_embed) as stub:
        out = await stub.Embed(
            ai_pb2.EmbedRequest(schema_version=SCHEMA_VERSION, texts=["一", "二"])
        )
    assert seen == {"dim": None, "purpose": srv.PURPOSE_FALLBACK_EMBED}
    assert out.dimensions == 3
    assert out.model == "unit-test-embed-model"
    assert len(out.vectors) == 2
    # vectors[i] 对应 texts[i]（proto:120-122）。
    # 用近似比较是**刻意的**：`EmbeddingVector.values` 是 `repeated float`＝wire 上 4 字节，
    # 而 Python 侧 `list[list[float]]` 是 double，上线必然截到 float32（相对误差 ~1e-7）。
    # 落库列本来就是 HALFVEC(512)（`backend/models/memory.py:36`），所以这不改变本仓精度口径；
    # 但谁把这条改成精确相等就会红——这一格是那条精度差的哨兵。
    got = [list(v.values) for v in out.vectors]
    assert got[0] == pytest.approx(expected[0], rel=1e-6)
    assert got[1] == pytest.approx(expected[1], rel=1e-6)


async def test_embed_forwards_explicit_dimensions_and_purpose():
    seen = {}

    async def _embed(texts, *, dim=None, purpose=None):
        seen.update(dim=dim, purpose=purpose)
        return [[0.5] for _ in texts]

    async with _rpc(embed=_embed) as stub:
        await stub.Embed(
            ai_pb2.EmbedRequest(
                schema_version=SCHEMA_VERSION, texts=["a"], dimensions=128, purpose="cron.embed"
            )
        )
    assert seen == {"dim": 128, "purpose": "cron.embed"}


async def test_embed_sets_user_and_trace_for_audit_trail(monkeypatch):
    """跨进程后 `ai_generation_runs` 的 user_id/trace_id 只能由 handler 从请求里塞回 contextvar。

    proto:104-110 点名的正是这一格：今天它们从进程内 contextvar 取（generation_run.py:55-56），
    不塞就等于留痕行写成 NULL。
    """
    recorded = {}
    monkeypatch.setattr(srv, "set_user_id", lambda v: recorded.__setitem__("user", v))
    monkeypatch.setattr(srv, "set_trace_id", lambda v: recorded.__setitem__("trace", v))

    async def _embed(texts, *, dim=None, purpose=None):
        return [[0.1] for _ in texts]

    async with _rpc(embed=_embed) as stub:
        await stub.Embed(
            ai_pb2.EmbedRequest(
                schema_version=SCHEMA_VERSION,
                texts=["a"],
                user_id="u-42",
                trace_id="t-42",
            )
        )
    assert recorded == {"user": "u-42", "trace": "t-42"}


async def test_embed_provider_429_is_resource_exhausted():
    async def _busy(texts, *, dim=None, purpose=None):
        raise _ProviderError(429)

    async with _rpc(embed=_busy) as stub:
        err = await _call_error(
            stub.Embed(ai_pb2.EmbedRequest(schema_version=SCHEMA_VERSION, texts=["a"]))
        )
    # failure.go:60-61 逐字点名「R3 的 handler 按这一码回报」；换成 INTERNAL 就丢了 429 的处置差异
    assert err.code() == grpc.StatusCode.RESOURCE_EXHAUSTED


async def test_embed_provider_5xx_is_internal_without_stack(monkeypatch):
    async def _boom(texts, *, dim=None, purpose=None):
        raise _ProviderError(500)

    logged = []
    monkeypatch.setattr(
        srv.logger, "exception", lambda *a, **k: logged.append(a), raising=False
    )
    async with _rpc(embed=_boom) as stub:
        err = await _call_error(
            stub.Embed(ai_pb2.EmbedRequest(schema_version=SCHEMA_VERSION, texts=["a"]))
        )
    assert err.code() == grpc.StatusCode.INTERNAL
    assert not any(m in err.details() for m in STACK_MARKERS), err.details()
    # 栈留在服务端日志（否则线上无法排障），线上只回脱敏文案
    assert logged, "5xx 没记异常日志"


async def test_embed_count_mismatch_is_internal():
    """条数对不上时不许发一批无法对回原文的字节（proto:120-122 的顺序契约）。"""

    async def _short(texts, *, dim=None, purpose=None):
        return [[0.1]]  # 少回一条

    async with _rpc(embed=_short) as stub:
        err = await _call_error(
            stub.Embed(ai_pb2.EmbedRequest(schema_version=SCHEMA_VERSION, texts=["a", "b"]))
        )
    assert err.code() == grpc.StatusCode.INTERNAL


# ── ClassifySafety ───────────────────────────────────────


def _tri_ok() -> dict:
    return {
        "status": "ok", "reason": "", "matched": [], "categories": [], "advice_blocked": False,
    }


async def test_classify_rejects_schema_version_mismatch():
    async with _rpc() as stub:
        err = await _call_error(
            stub.ClassifySafety(ai_pb2.TextInput(schema_version="9", text="x"))
        )
    assert err.code() == grpc.StatusCode.INVALID_ARGUMENT


async def test_classify_rejects_unknown_scope_instead_of_running_chat():
    """proto:148「映射层不许把空读成全档都判」——空可以，未知值不行：默默按 chat 跑会让
    调用方以为越界句式被判过了。"""
    called = []

    async def _classify(text, language, *, scope="chat"):
        called.append(scope)
        return _tri_ok()

    async with _rpc(classify=_classify) as stub:
        err = await _call_error(
            stub.ClassifySafety(ai_pb2.TextInput(schema_version=SCHEMA_VERSION, text="x", scope="all"))
        )
    assert err.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert called == [], "未知 scope 还是跑了一遍判定"


async def test_classify_empty_scope_maps_to_chat_and_assertion_passes_through():
    seen = []

    async def _classify(text, language, *, scope="chat"):
        seen.append(scope)
        return _tri_ok()

    async with _rpc(classify=_classify) as stub:
        await stub.ClassifySafety(ai_pb2.TextInput(schema_version=SCHEMA_VERSION, text="x"))
        await stub.ClassifySafety(
            ai_pb2.TextInput(schema_version=SCHEMA_VERSION, text="x", scope="assertion")
        )
    assert seen == ["chat", "assertion"]


async def test_classify_forwards_text_and_language():
    seen = {}

    async def _classify(text, language, *, scope="chat"):
        seen.update(text=text, language=language)
        return _tri_ok()

    async with _rpc(classify=_classify) as stub:
        await stub.ClassifySafety(
            ai_pb2.TextInput(schema_version=SCHEMA_VERSION, text="一句测试", language="zh")
        )
    assert seen == {"text": "一句测试", "language": "zh"}


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        (_tri_ok(), common_pb2.SAFETY_STATUS_OK),
        (
            {
                "status": "crisis", "reason": "crisis", "matched": ["不想活"],
                "categories": ["self_harm"], "advice_blocked": False,
            },
            common_pb2.SAFETY_STATUS_CRISIS,
        ),
        (
            {
                # 批 4 · D-G17：非危机的违规面（banned/overreach/model）从此有第四态可落，
                # 不许再借 crisis 表达「拦下」——借用的后果是 Go 把黑名单命中读成危机信号。
                "status": "unsafe", "reason": "banned", "matched": ["治疗"],
                "categories": [], "advice_blocked": True,
            },
            common_pb2.SAFETY_STATUS_UNSAFE,
        ),
        (
            {
                "status": "unavailable", "reason": "model", "matched": [],
                "categories": [], "advice_blocked": False,
            },
            common_pb2.SAFETY_STATUS_UNAVAILABLE,
        ),
    ],
)
async def test_classify_maps_four_states(verdict, expected):
    async def _fixed(text, language, *, scope="chat"):
        return verdict

    async with _rpc(classify=_fixed) as stub:
        out = await stub.ClassifySafety(
            ai_pb2.TextInput(schema_version=SCHEMA_VERSION, text="x")
        )
    assert out.safety.status == expected
    assert out.schema_version == SCHEMA_VERSION
    assert out.reason == verdict["reason"]
    assert list(out.matched) == verdict["matched"]
    assert list(out.safety.categories) == verdict["categories"]
    assert out.advice_blocked is verdict["advice_blocked"]


async def test_classify_out_of_contract_status_is_unavailable_not_ok():
    """D-8 的最后一道：四态外的值不能猜成安全，也不能猜成「拦下的内容」。"""

    async def _weird(text, language, *, scope="chat"):
        return {
            "status": "maybe", "reason": "", "matched": [], "categories": [],
            "advice_blocked": False,
        }

    async with _rpc(classify=_weird) as stub:
        out = await stub.ClassifySafety(
            ai_pb2.TextInput(schema_version=SCHEMA_VERSION, text="x")
        )
    assert out.safety.status == common_pb2.SAFETY_STATUS_UNAVAILABLE


async def test_classify_provider_429_is_resource_exhausted():
    async def _busy(text, language, *, scope="chat"):
        raise _ProviderError(429)

    async with _rpc(classify=_busy) as stub:
        err = await _call_error(
            stub.ClassifySafety(ai_pb2.TextInput(schema_version=SCHEMA_VERSION, text="x"))
        )
    assert err.code() == grpc.StatusCode.RESOURCE_EXHAUSTED


async def test_classify_blank_text_shortcut_lives_server_side(monkeypatch):
    """proto:138-139：空文本判安全这条短路留在服务端，Go 侧不许另判一套。

    这里用**默认能力函数**（真 `check_text_tri`），并把它的模型引用换成必炸的对象，
    证明空白文根本走不到分类器。
    """
    from backend.ai import safety as safety_mod

    class _Explode:
        async def complete(self, *a, **k):
            raise AssertionError("空文本不该调分类器")

    monkeypatch.setattr(safety_mod, "llm", _Explode())
    async with _rpc() as stub:
        out = await stub.ClassifySafety(
            ai_pb2.TextInput(schema_version=SCHEMA_VERSION, text="   ", language="zh")
        )
    assert out.safety.status == common_pb2.SAFETY_STATUS_OK
    assert out.reason == ""


# ── StreamChat ───────────────────────────────────────────


async def test_stream_rejects_schema_version_mismatch():
    async with _rpc() as stub:
        chunks, err = await _collect_stream(
            stub, ai_pb2.StreamChatRequest(schema_version="0", content="hi")
        )
    assert chunks == []
    assert err is not None and err.code() == grpc.StatusCode.INVALID_ARGUMENT


async def test_stream_rejects_overlong_content_without_echoing_it():
    """4000 上限复用 HTTP 的同一个 Pydantic 模型（proto:207-208 不许重抄数字）。

    第二条断言是本用例的正身：`str(ValidationError)` 会把 input 原文整段印出来，而这条字符串
    是 gRPC status detail——Go 侧把它当错误面写进日志（`aiclient.Error.Err`），用户输入于是
    从内容面漏进错误面、在日志里留下一份副本。
    """
    leak = "机密：" + "很长" * 2000  # 4001 字符，超 max_length=4000

    async def _never(*a, **k):
        raise AssertionError("入参校验失败就不该进生成链")
        yield  # pragma: no cover

    async with _rpc(chat_stream=_never) as stub:
        chunks, err = await _collect_stream(
            stub,
            ai_pb2.StreamChatRequest(
                schema_version=SCHEMA_VERSION, user_id="u-1", content=leak
            ),
        )
    assert chunks == []
    assert err is not None and err.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert leak not in err.details()
    assert "content:string_too_long" in err.details()
    assert not any(m in err.details() for m in STACK_MARKERS), err.details()


async def test_stream_rejects_unknown_message_type():
    async with _rpc() as stub:
        chunks, err = await _collect_stream(
            stub,
            ai_pb2.StreamChatRequest(
                schema_version=SCHEMA_VERSION, content="hi", message_type="nonsense"
            ),
        )
    assert chunks == []
    assert err is not None and err.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "message_type" in err.details()


async def test_stream_empty_message_type_defaults_to_reply():
    """空 message_type 由模型的 Field 默认补成 reply，与 HTTP 缺省同形（proto:190）。"""
    seen = {}

    async def _capture(*, user_id, content, message_type, language):
        seen.update(user_id=user_id, content=content, message_type=message_type, language=language)
        yield "token", "好"

    async with _rpc(chat_stream=_capture) as stub:
        await _collect_stream(
            stub,
            ai_pb2.StreamChatRequest(
                schema_version=SCHEMA_VERSION, user_id="u-1", content="hi", language="zh"
            ),
        )
    assert seen == {
        "user_id": "u-1", "content": "hi", "message_type": "reply", "language": "zh",
    }


async def test_stream_chunk_shape_satisfies_go_decoder():
    """Go 的 decode 校验（`grpc.go:581` 三条）必须在真字节上成立，不是在本仓的 Python 对象上。"""

    async def _gen(*, user_id, content, message_type, language):
        yield "memory", [{"id": "m1", "content": "你偏好早起", "category": "preference"}]
        yield "token", "早"
        yield "token", "安"

    async with _rpc(chat_stream=_gen) as stub:
        chunks, err = await _collect_stream(
            stub,
            ai_pb2.StreamChatRequest(schema_version=SCHEMA_VERSION, user_id="u-1", content="hi"),
        )
    assert err is None
    assert [c.finish for c in chunks[-1:]] == [ai_pb2.CHAT_FINISH_DONE]
    for c in chunks:
        assert c.schema_version == SCHEMA_VERSION
        # 中间块不得同时带 content 与 memories
        assert not (c.content and c.memories), "content 与 memories 同块 = 契约违规"
    assert [len(c.memories) for c in chunks] == [1, 0, 0, 0]
    assert chunks[0].memories[0].id == "m1"
    assert chunks[0].memories[0].category == "preference"
    assert "".join(c.content for c in chunks[1:3]) == "早安"
    # DONE 恒为空 content：BLOCKED/DEGRADED 由 Go 侧产生，不是本 server（proto:248-260）
    assert chunks[-1].content == ""
    assert chunks[-1].finish == ai_pb2.CHAT_FINISH_DONE


async def test_stream_midfailure_429_keeps_earlier_chunks_and_reports_exhausted():
    """半途失败：已发的块保留，终止码按 failure.go 那张表回。

    Go 侧 `pump` 在首个终止 finish 后停止读取，且 `StreamChat` 恒不重试（半途的流不能重放），
    所以这里既不能吞掉错误，也不能重发 DONE。
    """

    async def _half(*, user_id, content, message_type, language):
        yield "token", "前半句"
        raise _ProviderError(429)

    async with _rpc(chat_stream=_half) as stub:
        chunks, err = await _collect_stream(
            stub,
            ai_pb2.StreamChatRequest(schema_version=SCHEMA_VERSION, user_id="u-1", content="hi"),
        )
    assert [c.content for c in chunks] == ["前半句"]
    assert err is not None and err.code() == grpc.StatusCode.RESOURCE_EXHAUSTED


async def test_stream_unexpected_failure_is_internal_without_stack():
    async def _bug(*, user_id, content, message_type, language):
        raise RuntimeError("生成链里的一个 bug")
        yield  # pragma: no cover

    async with _rpc(chat_stream=_bug) as stub:
        chunks, err = await _collect_stream(
            stub,
            ai_pb2.StreamChatRequest(schema_version=SCHEMA_VERSION, user_id="u-1", content="hi"),
        )
    assert chunks == []
    assert err is not None and err.code() == grpc.StatusCode.INTERNAL
    assert not any(m in err.details() for m in STACK_MARKERS), err.details()


async def test_stream_never_finishes_without_a_terminal_chunk_when_ok():
    """空生成（模型一个字都没出）也要有 DONE 收尾，否则 Go 侧把流读成断流。"""

    async def _empty(*, user_id, content, message_type, language):
        return
        yield  # pragma: no cover

    async with _rpc(chat_stream=_empty) as stub:
        chunks, err = await _collect_stream(
            stub,
            ai_pb2.StreamChatRequest(schema_version=SCHEMA_VERSION, user_id="u-1", content="hi"),
        )
    assert err is None
    assert [c.finish for c in chunks] == [ai_pb2.CHAT_FINISH_DONE]
