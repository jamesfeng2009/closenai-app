"""safety_service：三层防御之 L3 输出后置过滤（唯一事实源）。

- check_text：全量检测（非流式路径），lite 模型分类，fail-open。
- sentence_gate：流式按句缓冲闸门，命中截断并替换安全话术。
- SAFETY_BOUNDARY：L1 prompt 安全边界段落，所有面向用户 LLM 调用必须注入。
"""
import asyncio

from backend.services.message_generator import message_generator
from backend.ai.safety import SafetyService, safety_service


# ── check_text ─────────────────────────────────────────────


async def test_check_text_safe(mock_llm):
    """LLM 判 SAFE → True。"""
    mock_llm.state["complete_text"] = "SAFE"
    assert await safety_service.check_text("今天天气不错", "zh") is True


async def test_check_text_unsafe(mock_llm):
    """LLM 判 UNSAFE → False。"""
    mock_llm.state["complete_text"] = "UNSAFE"
    assert await safety_service.check_text("hate speech content", "en") is False


async def test_check_text_fail_open_on_exception(mock_llm):
    """检测调用自身异常 → fail-open True（不阻断主流程）。"""
    def _boom(prompt, **kwargs):
        raise RuntimeError("lite model down")

    mock_llm.state["complete_fn"] = _boom
    assert await safety_service.check_text("任意文本", "zh") is True


async def test_check_text_fail_open_on_timeout(mock_llm, monkeypatch):
    """检测超时 → fail-open True。"""
    from backend.ai.llm import llm

    async def _slow(prompt, **kwargs):
        await asyncio.sleep(5)
        return "SAFE"

    # 直接替换 complete（complete_fn 经 fake_complete 包装不会真正挂起）
    monkeypatch.setattr(llm, "complete", _slow)
    monkeypatch.setattr(SafetyService, "CHECK_TIMEOUT", 0.05)
    assert await safety_service.check_text("任意文本", "zh") is True


async def test_check_text_unparseable_treated_safe(mock_llm):
    """模型返回非 SAFE/UNSAFE 内容 → 视为安全（fail-open 语义）。"""
    mock_llm.state["complete_text"] = "我不知道"
    assert await safety_service.check_text("任意文本", "zh") is True


async def test_check_text_empty_input(mock_llm):
    """空文本不调用 LLM 直接判安全。"""
    assert await safety_service.check_text("", "zh") is True
    assert await safety_service.check_text("   ", "en") is True
    assert not mock_llm.calls["complete"]


async def test_check_text_uses_lite_model(mock_llm):
    """检测必须走 lite 模型（成本控制）。"""
    mock_llm.state["complete_text"] = "SAFE"
    await safety_service.check_text("hello", "en")
    assert mock_llm.calls["complete"][0]["lite"] is True


# ── sentence_gate ──────────────────────────────────────────


async def _collect(gate, chunks):
    """逐 chunk 喂入并收集放行文本，最后 flush。"""
    out = []
    for c in chunks:
        async for text in gate.feed(c):
            out.append(text)
    async for text in gate.flush():
        out.append(text)
    return out


async def test_gate_passes_safe_stream(mock_llm):
    """全安全流：所有句子原样放行，blocked=False。"""
    mock_llm.state["complete_text"] = "SAFE"
    gate = safety_service.sentence_gate("zh")
    chunks = ["今天天气很好。", "我们聊了", "很多！感觉不错。"]
    out = await _collect(gate, chunks)
    assert "".join(out) == "今天天气很好。我们聊了很多！感觉不错。"
    assert gate.blocked is False


async def test_gate_first_sentence_unsafe(mock_llm):
    """首句即违规 → 不下发原句，仅下发安全话术。"""
    mock_llm.state["complete_text"] = "UNSAFE"
    gate = safety_service.sentence_gate("zh")
    out = await _collect(gate, ["仇恨言论第一句。", "正常第二句。"])
    assert "仇恨言论第一句。" not in out
    assert len(out) == 1
    assert out[0] == safety_service.safe_reply("zh")
    assert gate.blocked is True


async def test_gate_mid_stream_blocked(mock_llm):
    """第 2 句命中 → 已放行第 1 句保留 + 安全话术替换，后续不再放行。"""
    calls = {"n": 0}

    def _classify(prompt, **kwargs):
        calls["n"] += 1
        return "SAFE" if calls["n"] == 1 else "UNSAFE"

    mock_llm.state["complete_fn"] = _classify
    gate = safety_service.sentence_gate("en")
    out = await _collect(gate, ["Good sentence. ", "Violent threat here. ", "More text. "])
    assert "Good sentence. " in out
    assert "Violent threat here. " not in out
    assert safety_service.safe_reply("en") in out
    assert "More text. " not in out
    assert gate.blocked is True


async def test_gate_check_failure_fail_open(mock_llm):
    """句检测异常 → 该句放行（fail-open），流不中断。"""
    def _boom(prompt, **kwargs):
        raise RuntimeError("classifier down")

    mock_llm.state["complete_fn"] = _boom
    gate = safety_service.sentence_gate("zh")
    out = await _collect(gate, ["第一句。", "第二句。"])
    assert "".join(out) == "第一句。第二句。"
    assert gate.blocked is False


async def test_gate_force_split_long_sentence(mock_llm):
    """无句读超长文本按 200 字强制切句检测。"""
    mock_llm.state["complete_text"] = "SAFE"
    gate = safety_service.sentence_gate("zh")
    long_text = "啊" * 450
    out = await _collect(gate, [long_text])
    assert "".join(out) == long_text
    # 450 字 → 至少 2 次检测（200+200+50）
    assert len(mock_llm.calls["complete"]) >= 2


async def test_gate_blocked_stops_further_output(mock_llm):
    """blocked 后继续 feed 不再产出任何文本。"""
    mock_llm.state["complete_text"] = "UNSAFE"
    gate = safety_service.sentence_gate("zh")
    out = await _collect(gate, ["违规句。", "后续句子。", "还有更多。"])
    assert len(out) == 1  # 仅安全话术
    assert gate.blocked is True


# ── L1 安全边界段落 ────────────────────────────────────────


def test_safety_boundary_bilingual():
    """L1 prompt 段落中英双表，按 language 选择。"""
    assert "安全" in safety_service.SAFETY_BOUNDARY["zh"]
    assert "safety" in safety_service.SAFETY_BOUNDARY["en"].lower()


def test_safe_reply_bilingual():
    """替换话术 zh/en 双表。"""
    assert safety_service.safe_reply("zh") != safety_service.safe_reply("en")


# ── generate_chat_stream 集成 ──────────────────────────────


async def test_chat_stream_safe_flow(db, make_user, mock_llm):
    """流式对话安全：token 原样流出，入库为完整回复。"""
    await make_user("u1", ai_consent=True)
    mock_llm.state["complete_text"] = "SAFE"

    async def fake_stream(prompt, **kwargs):
        for part in ["今天过得", "怎么样？", "想聊聊吗。"]:
            yield part

    mock_llm.state["complete_fn"] = lambda prompt, **kwargs: "SAFE"
    import backend.ai.llm as llm_mod
    # stream 需要逐句输出；直接用 monkeypatch 由 conftest 的 calls 记录
    chunks = []
    orig_stream = llm_mod.llm.stream
    llm_mod.llm.stream = fake_stream
    try:
        async for c in message_generator.generate_chat_stream(
            db=db, user_id="u1", user_input="你好", language="zh"
        ):
            chunks.append(c)
    finally:
        llm_mod.llm.stream = orig_stream

    text = "".join(chunks)
    assert "今天过得怎么样？" in text
    assert "想聊聊吗。" in text


async def test_chat_stream_unsafe_replaced(db, make_user, mock_llm):
    """流式对话违规：输出被安全话术替换，入库为替换后文本。"""
    await make_user("u1", ai_consent=True)

    async def fake_stream(prompt, **kwargs):
        yield "暴力恐吓内容。"
        yield "更多违规内容。"

    mock_llm.state["complete_fn"] = lambda prompt, **kwargs: "UNSAFE"
    import backend.ai.llm as llm_mod
    chunks = []
    orig_stream = llm_mod.llm.stream
    llm_mod.llm.stream = fake_stream
    try:
        async for c in message_generator.generate_chat_stream(
            db=db, user_id="u1", user_input="jailbreak", language="zh"
        ):
            chunks.append(c)
    finally:
        llm_mod.llm.stream = orig_stream

    text = "".join(chunks)
    assert "暴力恐吓内容。" not in text
    assert safety_service.safe_reply("zh") in text


async def test_chat_stream_l1_prompt_injected(db, make_user, mock_llm):
    """L1：流式对话 system prompt 必须含安全边界段落。"""
    await make_user("u1", ai_consent=True)
    mock_llm.state["complete_text"] = "SAFE"

    async for _ in message_generator.generate_chat_stream(
        db=db, user_id="u1", user_input="hello", language="en"
    ):
        pass

    assert mock_llm.calls["stream"], "llm.stream 应被调用"
    system_prompt = mock_llm.calls["stream"][0].get("system_prompt", "")
    assert safety_service.SAFETY_BOUNDARY["en"] in system_prompt
