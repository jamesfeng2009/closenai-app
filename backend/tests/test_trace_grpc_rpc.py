"""批 T-E · 闸 G-E2：Python gRPC 服务端的 x-trace-id 恢复面。

形状复用 R3 的**真 transport** 夹具（`test_r3_rpc_server._rpc`，port 0 起真 server）：
metadata 在 aio servicer 里怎么落地、重复键取哪一条，都是假 context 编不出来的线上行为。

判据四格，缺一格就不是「透传」而是「碰巧能跑」：
· 三个方法形态（Embed／ClassifySafety／StreamChat）各逐字adopt 一条 metadata trace；
· 两条载体都在时 **metadata 优先**于 EmbedRequest.trace_id（proto:113 的字段只覆盖 Embed）；
· 没 metadata 时字段兜底仍生效（否则 R3 已交付的留痕面被本批改坏）；
· 畸形值**整条丢弃**且不进日志原文（T-C 在 HTTP 面立的规矩，gRPC 面同守）。
"""
from __future__ import annotations

from typing import List

import pytest

from backend.ai.rpc import server as srv
from backend.ai.rpc.server import SCHEMA_VERSION
from backend.gen import closenai_ai_rpc_pb2 as ai_pb2
from backend.tests.test_r3_rpc_server import _collect_stream, _rpc

META = "x-trace-id"


@pytest.fixture()
def traces(monkeypatch) -> List[str]:
    """捕获所有 set_trace_id 调用：断言「adopt 了什么」而不只是「ctx 里有什么」。"""
    seen: List[str] = []
    monkeypatch.setattr(srv, "set_trace_id", lambda v: seen.append(v))
    return seen


async def _ok_embed(texts, *, dim=None, purpose=None):
    return [[0.1] for _ in texts]


async def _ok_classify(text, language, *, scope="chat"):
    return {"status": "ok", "reason": "", "matched": [], "categories": [], "advice_blocked": False}


async def _ok_stream(*, user_id, content, message_type, language):
    yield "token", "好"


async def test_all_three_method_forms_adopt_metadata(traces):
    async with _rpc(embed=_ok_embed, classify=_ok_classify, chat_stream=_ok_stream) as stub:
        await stub.Embed(
            ai_pb2.EmbedRequest(schema_version=SCHEMA_VERSION, texts=["a"]),
            metadata=[(META, "T-GE2-EMB")],
        )
        await stub.ClassifySafety(
            ai_pb2.TextInput(schema_version=SCHEMA_VERSION, text="x"),
            metadata=[(META, "T-GE2-SAFE")],
        )
        await _collect_stream(
            stub,
            ai_pb2.StreamChatRequest(schema_version=SCHEMA_VERSION, content="hi"),
            metadata=[(META, "T-GE2-STREAM")],
        )
    assert traces == ["T-GE2-EMB", "T-GE2-SAFE", "T-GE2-STREAM"], (
        "三形态缺任何一个＝该方法的透传挂点没了（M-E2 打的正是流面与判定面）"
    )


async def test_metadata_wins_over_request_field(traces):
    async with _rpc(embed=_ok_embed) as stub:
        await stub.Embed(
            ai_pb2.EmbedRequest(
                schema_version=SCHEMA_VERSION, texts=["a"], trace_id="T-FIELD-STALE"
            ),
            metadata=[(META, "T-META-FRESH")],
        )
    assert traces == ["T-META-FRESH"], (
        "字段抢过 metadata＝信了可能漏填/填旧的兜底，拦截器搬运的值反而被丢"
    )


async def test_field_fallback_without_metadata(traces):
    """R3 留痕面（ai_generation_runs.trace_id）今天就靠这个字段，本批不许改坏它。"""
    async with _rpc(embed=_ok_embed) as stub:
        await stub.Embed(
            ai_pb2.EmbedRequest(schema_version=SCHEMA_VERSION, texts=["a"], trace_id="t-42")
        )
    assert traces == ["t-42"]


async def test_malformed_metadata_dropped_without_echoing_raw(traces, monkeypatch):
    warnings: List[str] = []
    monkeypatch.setattr(
        srv.logger, "warning", lambda msg, *a: warnings.append(msg % a), raising=False
    )
    # 取样是**线路上合法、形状上非法**的值：带 \n 的注入根本发不出来
    # （grpc filter_stack 直接拒 Illegal header value），消毒防的是这一类——
    # 斜杠能上线，但绝不能进 contextvar／日志行前缀。
    evil = "evil/../../etc/passwd-MARKER"
    async with _rpc(embed=_ok_embed, classify=_ok_classify) as stub:
        await stub.Embed(
            ai_pb2.EmbedRequest(schema_version=SCHEMA_VERSION, texts=["a"]),
            metadata=[(META, evil)],
        )
        await stub.ClassifySafety(
            ai_pb2.TextInput(schema_version=SCHEMA_VERSION, text="x"),
            metadata=[(META, evil)],
        )
    assert traces == [], "畸形 trace 被 adopt——日志前缀被路径字符污染就是这里进来的"
    assert warnings, "丢弃必须留警示，否则透传断了没人知道"
    assert not any(evil in w or "MARKER" in w for w in warnings), (
        f"警示行回显了原文：{warnings}"
    )


async def test_malformed_request_field_also_dropped(traces):
    async with _rpc(embed=_ok_embed) as stub:
        await stub.Embed(
            ai_pb2.EmbedRequest(
                schema_version=SCHEMA_VERSION,
                texts=["a"],
                trace_id="x" * 129,  # 超 128 上限
            )
        )
    assert traces == []


async def test_no_trace_means_no_set_call(traces):
    async with _rpc(embed=_ok_embed, classify=_ok_classify, chat_stream=_ok_stream) as stub:
        await stub.Embed(ai_pb2.EmbedRequest(schema_version=SCHEMA_VERSION, texts=["a"]))
        await stub.ClassifySafety(ai_pb2.TextInput(schema_version=SCHEMA_VERSION, text="x"))
        await _collect_stream(
            stub, ai_pb2.StreamChatRequest(schema_version=SCHEMA_VERSION, content="hi")
        )
    assert traces == [], "无 trace 时不许编造（服务端没有响应头可回，造出来是孤儿 trace）"


async def test_shape_literal_matches_http_side():
    """同一逻辑头在两个入口进程：消毒形状漂移＝同一 id 一边收一边丢，全链路 grep 断在缝里。

    批 T-E 落定后这不只是「pattern 恰好相同」：两侧必须**是同一个对象**（都来自
    `core/logging.py`）。R3 形状闸不许 rpc 层自己 `import re`，所以这一格红＝有人开了第二份定义。
    """
    from backend.core import middleware as http_mw

    assert srv.TRACE_SHAPE is http_mw.TRACE_SHAPE
