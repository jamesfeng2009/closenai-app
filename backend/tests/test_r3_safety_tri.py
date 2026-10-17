"""R3-① · `check_text_tri()` 的四态判定闸（D-8 在 Python 侧的落点）。

为什么这批要新写一个函数而不是改 `check_text`：`check_text` 的 fail-open 是 HTTP 面的既有
口径（`backend/ai/safety.py:393-395`，用户发送路径零阻塞），现网 5+ 个调用点靠它。
跨进程后的消费方换成 Go 领域门，「超时＝放行」会变成「拿不到判定就当安全」——
所以四态走新方法，旧方法一个字节不动。本文件同时钉住两侧：**新面必须拦、旧面必须不变**。

四态是 2026-10-17（批 4 · D-G17）补的：原三态里 banned/overreach/model 只能落 crisis，
Go 侧读起来就成了「用户处在危险中」。本文件那三条命中面用例因此改成断 `unsafe`，
而 crisis 那条（`test_crisis_beats_banned_and_carries_categories`）一个字没动——
**它才是这次改判的正证**：真危机仍走危机，被挪走的只是非危机的违规面。

变异靶（`scripts/mutate_r3_gates.py` 的 M 组）：把 `unavailable` 改成 `ok`、
把不可解析当 SAFE、或者顺手把 `check_text` 也改成 fail-closed，这里各打红一次。
"""
from __future__ import annotations

import asyncio

import pytest

from backend.ai.llm import llm
from backend.ai.safety import (
    SAFETY_CRISIS,
    SAFETY_OK,
    SAFETY_UNAVAILABLE,
    SAFETY_UNSAFE,
    SafetyService,
    safety_service,
)


class _Boom(Exception):
    """带 status_code 的 provider 异常（openai.APIStatusError 的鸭子形态）。"""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"provider {status_code}")
        self.status_code = status_code


async def _complete_returning(value):
    async def _fake(*args, **kwargs):
        return value
    return _fake


# ── 四态本体 ─────────────────────────────────────────────


async def test_blank_text_is_ok(monkeypatch):
    """空/纯空白仍走 :362-363 那条短路——判定面不许把「没内容」说成不可用。"""
    called = []

    async def _spy(*a, **k):
        called.append(1)
        return "SAFE"

    monkeypatch.setattr(llm, "complete", _spy)
    out = await safety_service.check_text_tri("   ", "zh")
    assert out["status"] == SAFETY_OK
    assert called == []


async def test_timeout_is_unavailable_not_ok(monkeypatch):
    """D-8 的正身：超时回 unavailable。改成 ok 会让 Go 侧 gate.go:396 那条 reject 消失。"""

    async def _slow(*a, **k):
        await asyncio.sleep(safety_service.CHECK_TIMEOUT + 0.2)
        return "SAFE"

    monkeypatch.setattr(llm, "complete", _slow)
    out = await safety_service.check_text_tri("一句正常的话", "zh")
    assert out["status"] == SAFETY_UNAVAILABLE, "超时被当成了放行（D-8 复活）"
    assert out["reason"] == "model"


async def test_provider_exception_is_unavailable(monkeypatch):
    async def _boom(*a, **k):
        raise _Boom(429)

    monkeypatch.setattr(llm, "complete", _boom)
    out = await safety_service.check_text_tri("一句正常的话", "en")
    assert out["status"] == SAFETY_UNAVAILABLE


async def test_unparsable_verdict_is_unavailable(monkeypatch):
    """分类器没给出可判定的答案＝判定缺失。

    今天 :389 的 `"UNSAFE" not in verdict` 会把这一格读成安全，三态面必须反过来。
    """
    monkeypatch.setattr(llm, "complete", await _complete_returning("maybe"))
    out = await safety_service.check_text_tri("一句正常的话", "en")
    assert out["status"] == SAFETY_UNAVAILABLE


async def test_model_unsafe_is_unsafe_with_reason(monkeypatch):
    """模型判 UNSAFE 是「内容违规」，不是「用户处在危险中」⇒ 落 unsafe（批 4 · D-G17）。

    三态时代这一格挤在 crisis 里，Go 侧的 reject 措辞会把它说成危机信号；
    reason 仍是 "model"，消费方按面降级的判据没变。
    """
    monkeypatch.setattr(llm, "complete", await _complete_returning("UNSAFE"))
    out = await safety_service.check_text_tri("一句正常的话", "en")
    assert out["status"] == SAFETY_UNSAFE
    assert out["reason"] == "model"


async def test_model_safe_is_ok(monkeypatch):
    monkeypatch.setattr(llm, "complete", await _complete_returning("SAFE"))
    out = await safety_service.check_text_tri("今天天气不错", "zh")
    assert out == {
        "status": SAFETY_OK, "reason": "", "matched": [], "categories": [],
        "advice_blocked": False,
    }


async def test_banned_term_is_unsafe_reason_banned(monkeypatch):
    """黑名单命中不调模型（与 check_text 同序），但面要带出来供消费方按面降级。

    D-G17：它是「内容违规」不是「危机」——落 unsafe 后 Go 仍必拦（第 6 关），
    只是不再被说成危机信号。
    """
    called = []

    async def _spy(*a, **k):
        called.append(1)
        return "SAFE"

    monkeypatch.setattr(llm, "complete", _spy)
    out = await safety_service.check_text_tri("建议接受心理治疗", "zh")
    assert out["status"] == SAFETY_UNSAFE
    assert out["reason"] == "banned"
    assert out["matched"] == ["治疗"]
    assert called == []


async def test_crisis_beats_banned_and_carries_categories(monkeypatch):
    monkeypatch.setattr(llm, "complete", await _complete_returning("SAFE"))
    out = await safety_service.check_text_tri("我已经不想活了", "zh")
    assert out["status"] == SAFETY_CRISIS
    assert out["reason"] == "crisis"
    assert out["categories"] == ["self_harm"]
    assert out["matched"] == ["不想活"]
    # §二 铁律只绑关系暴力类；自伤类不许被顺手带上 advice_blocked
    assert out["advice_blocked"] is False


async def test_relationship_violence_blocks_advice(monkeypatch):
    out = await safety_service.check_text_tri("他动手打我", "zh")
    assert out["categories"] == ["relationship_violence"]
    assert out["advice_blocked"] is True


async def test_overreach_only_checked_in_assertion_scope(monkeypatch):
    """scope 面沿用 :368 的判据：对话流不判越界（否则「因为他没回你」这类复述全被拦）。

    命中的那一格按 D-G17 落 unsafe（推理越界是内容问题，不是危险信号）。
    """
    text = "因为他没回你，所以你注定焦虑"
    monkeypatch.setattr(llm, "complete", await _complete_returning("SAFE"))
    chat = await safety_service.check_text_tri(text, "zh", scope="chat")
    assert chat["status"] == SAFETY_OK
    assertion = await safety_service.check_text_tri(text, "zh", scope="assertion")
    assert assertion["status"] == SAFETY_UNSAFE
    assert assertion["reason"] == "overreach"


# ── 旧面不许跟着变 ───────────────────────────────────────


async def test_check_text_still_fails_open(monkeypatch):
    """`check_text` 的 fail-open 是 HTTP 面既有行为，本批一个字节都不该动它。

    它和上面那条超时用例是一对：只留一条的话，「改错哪一侧」都能过。
    """
    async def _boom(*a, **k):
        raise _Boom(500)

    monkeypatch.setattr(llm, "complete", _boom)
    assert await safety_service.check_text("一句正常的话", "zh") is True


async def test_tri_shares_prompt_and_timeout_with_check_text(monkeypatch):
    """两条路径必须发给模型同一份 prompt、同一套参数，否则会出现「RPC 面比 HTTP 面更宽松」的漂移。

    钉的是调用形态而不是复制判断逻辑：`_CHECK_PROMPT:262` 与 `CHECK_TIMEOUT:212` 各只有一份，
    行号引用同时被 `closenai_ai_rpc.proto:157-158` 与 Go 侧 `grpc.go:270` 指着；
    `purpose` 也沿用 :381 那个固定值，所以 `ai_generation_runs` 里两条路能对账。
    """
    calls = []

    async def _spy(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return "SAFE"

    monkeypatch.setattr(llm, "complete", _spy)

    text = "今天天气不错"
    assert await safety_service.check_text(text, "zh") is True
    assert await safety_service.check_text_tri(text, "zh") == {
        "status": SAFETY_OK, "reason": "", "matched": [], "categories": [],
        "advice_blocked": False,
    }

    assert len(calls) == 2, "两条路径各自只该调一次分类器"
    http_call, rpc_call = calls
    assert http_call == rpc_call, (
        "safety 的 HTTP 面与 RPC 面发出的检测请求不一致，"
        f"\n  http: {http_call}\n  rpc : {rpc_call}"
    )
    prompt, kwargs = rpc_call
    assert prompt == f"{SafetyService._CHECK_PROMPT}{text}"
    assert kwargs["purpose"] == "safety_service.check_text"
    assert kwargs["lite"] is True and kwargs["track"] is False
    assert kwargs["temperature"] == 0.0 and kwargs["max_tokens"] == 10


async def test_tri_timeout_budget_follows_class_constant(monkeypatch):
    """检测预算读的是 `CHECK_TIMEOUT` 这份类常量，不是私有硬编码值。

    为什么钉这一格：Go 侧 `ClassifySafety` 的客户端超时是外层预算，Python 侧这份是内层；
    内层若被换成硬编码数字，两条预算解耦后「我们的检测没跑完」会被误报成 transport 故障。
    变异面：把 `timeout=self.CHECK_TIMEOUT` 写成 `timeout=5` —— 下面第二条断言立刻红。
    """
    async def _hang(*a, **k):
        await asyncio.sleep(30)
        return "SAFE"

    monkeypatch.setattr(llm, "complete", _hang)
    monkeypatch.setattr(safety_service, "CHECK_TIMEOUT", 0.2)

    started = asyncio.get_running_loop().time()
    out = await safety_service.check_text_tri("一句正常的话", "zh")
    elapsed = asyncio.get_running_loop().time() - started

    assert out["status"] == SAFETY_UNAVAILABLE
    assert elapsed < 3.0, f"CHECK_TIMEOUT 改成 0.2 后耗时仍 {elapsed:.1f}s，说明预算被硬编码了"
