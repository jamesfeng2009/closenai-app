"""D1 · 证据分级与措辞闸门（《沟通证据与措辞规范-2026-09-28》§一 + 架构方案 §六-4）。

覆盖三块：
- L1 `evidence_policy()` 双语段落内容与注入 wiring（所有面向用户的生成出口）
- L3 确定性黑名单（§5.16 医疗化红线 + §1.4 D 级词汇扩充）
- L3 断言型出口的推理越界句式（§1.2），含规范 §1.4 的 SentenceGate 验收样本
"""
import inspect

import pytest

from backend.services.growth_service import GrowthService
from backend.services.mood_service import MoodService
from backend.services.personality_service import PersonalityService
from backend.services.push.copy import PushCopywriter
from backend.services.reflection_service import ReflectionService
from backend.services.safety_service import safety_service
from backend.services.share_service import ShareService
from backend.services.message_generator import MessageGenerator, message_generator

# ── 注入 wiring：所有面向用户的生成出口必须带 evidence_policy 段落 ──

OUTLETS = [
    (MessageGenerator, "generate_chat_stream"),
    (MessageGenerator, "generate_proactive_message"),
    (MessageGenerator, "generate_follow_up"),
    (MoodService, "_interpret"),
    (MoodService, "_deep_interpretation"),
    (PersonalityService, "refresh"),
    (PersonalityService, "_generate_profile_from_answers"),
    (PersonalityService, "generate_combo_report"),
    (GrowthService, "_generate_next_week_forecast"),
    (GrowthService, "generate_pattern_discovery"),
    (GrowthService, "_generate_report_body"),
    (GrowthService, "_llm_or_fallback"),
    (ReflectionService, "generate_draft"),
    (ShareService, "_generate_tagline"),
    (PushCopywriter, "_llm_body"),
]


@pytest.mark.parametrize("cls,fn_name", OUTLETS, ids=[f"{c.__name__}.{n}" for c, n in OUTLETS])
def test_outlet_injects_evidence_policy(cls, fn_name):
    """L1 段落注入点齐备（架构方案 §六-4：与 boundary 平级、同注入点）。"""
    src = inspect.getsource(getattr(cls, fn_name))
    assert "safety_service.evidence_policy(" in src, f"{cls.__name__}.{fn_name} 缺 evidence_policy 注入"


# ── L1 段落内容 ────────────────────────────────────────────


def test_evidence_policy_bilingual():
    """中英双表，按 language 归一选择（与 boundary 同 _lang_key 约定）。"""
    zh = safety_service.evidence_policy("zh-CN")
    en = safety_service.evidence_policy("en")
    assert zh != en
    assert safety_service.evidence_policy("fr") == en  # 非 zh 一律回退 en（决策点 3）
    assert safety_service.evidence_policy("") == en


def test_evidence_policy_covers_spec_sections():
    """段落必须承载 §1.1 四级证据、§1.2 四类推理错误、§1.3 升格禁令。"""
    zh = safety_service.EVIDENCE_POLICY["zh"]
    for token in ("A 级", "B 级", "C 级", "D 级", "相关当因果", "平均当个人", "标签当解释",
                  "技巧脱离权力", "共现", "第三方"):
        assert token in zh, f"zh 段落缺 {token}"
    en = safety_service.EVIDENCE_POLICY["en"]
    for token in ("level A", "level B", "level C", "level D", "correlation as causation",
                  "averages as individuals", "labels as explanations", "third party"):
        assert token.lower() in en.lower(), f"en 段落缺 {token}"


def test_evidence_policy_is_separate_from_boundary():
    """决策点 1：独立段落，不压缩进 boundary。"""
    assert safety_service.evidence_policy("zh") not in safety_service.boundary("zh")
    assert safety_service.boundary("zh") not in safety_service.evidence_policy("zh")


# ── L3 确定性黑名单 ────────────────────────────────────────


async def test_banned_d_level_vocabulary_blocked_without_llm(mock_llm):
    """D 级词汇（星座配对/爱情语言/PUA）确定性拦截，且不必调用分类器。"""
    for text in ("你们的星座配对度很高。", "用爱情语言的角度看，TA 需要肯定。", "这是一套 PUA 话术。"):
        assert await safety_service.check_text(text, "zh") is False
    assert not mock_llm.calls["complete"]


async def test_banned_medical_wording_blocked(mock_llm):
    """§5.16 ① 医疗化红线词在输出侧正则拦截。"""
    for text in ("建议你去医院接受治疗。", "这属于焦虑症的典型表现。", "我可以帮你疗愈。"):
        assert await safety_service.check_text(text, "zh") is False
    assert not mock_llm.calls["complete"]


async def test_banned_english_uses_word_boundary(mock_llm):
    """en 词表按词边界匹配：命中 depression，但不误杀 retreat。"""
    assert await safety_service.check_text("This looks like depression.", "en") is False
    assert await safety_service.check_text("A weekend retreat to recharge.", "en") is True


async def test_banned_english_allows_everyday_verb_collisions(mock_llm):
    """en 表刻意不含 treat / therapy（误杀率过高，见 BANNED_TERMS 注释）。"""
    for text in ("How they treat you matters.", "Talking to a therapist can help."):
        assert safety_service.banned_hit(text, "en") is None


def test_high_low_value_not_in_regex_table():
    """「高价值/低价值」不入正则表——该词已用作 Discovery 幅度分级内部术语，只进语义分类与 L1 段落。"""
    assert not any("高价值" in t for t in safety_service.BANNED_TERMS["zh"])
    assert "高价值" in safety_service.EVIDENCE_POLICY["zh"]
    assert safety_service.banned_hit("连续高价值发现", "zh") is None


# ── L3 推理越界句式（scope=assertion）─────────────────────

CAUSAL_SAMPLE = "因为 TA 没有回消息，所以你这周的情绪才更低。"
LABEL_SAMPLE = "你是焦虑型依恋，所以 TA 的冷淡其实是在自我保护。"
CO_OCCURRENCE_SAMPLE = "在你的记录里，周五与低落同时出现过。"
THIRD_PERSON_ONLY = "TA 没回消息的那几天，你的情绪更低。"


def test_attribution_hit_detects_overreach():
    assert safety_service.attribution_hit(CAUSAL_SAMPLE, "zh") == "因果断言指向第三方"
    assert safety_service.attribution_hit(LABEL_SAMPLE, "zh") == "标签推断第三方"


def test_attribution_hit_allows_compliant_wording():
    """§1.1 允许的共现措辞与单纯第三人称叙述不判违规。"""
    assert safety_service.attribution_hit(CO_OCCURRENCE_SAMPLE, "zh") is None
    assert safety_service.attribution_hit(THIRD_PERSON_ONLY, "zh") is None


def test_attribution_hit_english():
    assert safety_service.attribution_hit(
        "He never replies, and that is why you feel unseen.", "en"
    ) == "因果断言指向第三方"
    assert safety_service.attribution_hit(
        "As an avoidant type, she needs space to protect herself.", "en"
    ) == "标签推断第三方"
    assert safety_service.attribution_hit(
        "In your records, Friday and low mood appeared together.", "en"
    ) is None


async def test_attribution_gate_only_in_assertion_scope(mock_llm):
    """对话 scope 不启用正则（正常复述用户叙述会被误杀），断言 scope 启用。"""
    mock_llm.state["complete_text"] = "SAFE"
    assert await safety_service.check_text(CAUSAL_SAMPLE, "zh") is True
    assert await safety_service.check_text(CAUSAL_SAMPLE, "zh", scope="assertion") is False


async def test_check_or_scope_assertion_falls_back(mock_llm):
    """check_or 传 scope：断言型出口命中即回退规则模板。"""
    mock_llm.state["complete_text"] = "SAFE"
    result = await safety_service.check_or(LABEL_SAMPLE, "zh", "模板文案", scope="assertion")
    assert result == "模板文案"
    assert await safety_service.check_or(LABEL_SAMPLE, "zh", "模板文案") == LABEL_SAMPLE


# ── SentenceGate 扩例（规范 §1.4 验收：因果断言/标签推断样本被拦）──


async def _collect(gate, chunks):
    out = []
    for c in chunks:
        async for text in gate.feed(c):
            out.append(text)
    async for text in gate.flush():
        out.append(text)
    return out


async def test_gate_assertion_scope_blocks_causal_sentence(mock_llm):
    """因果断言句在断言型闸门被拦截，改发安全话术。"""
    mock_llm.state["complete_text"] = "SAFE"  # 语义分类器放行，验证确定性句式独立生效
    gate = safety_service.sentence_gate("zh", scope="assertion")
    out = await _collect(gate, [CAUSAL_SAMPLE, CO_OCCURRENCE_SAMPLE])
    assert CAUSAL_SAMPLE not in out
    assert gate.blocked is True
    assert out == [safety_service.safe_reply("zh")]


async def test_gate_assertion_scope_blocks_label_inference(mock_llm):
    """标签推断第三方样本被拦（§1.1 违规输出示例句式）。"""
    mock_llm.state["complete_text"] = "SAFE"
    gate = safety_service.sentence_gate("zh", scope="assertion")
    out = await _collect(gate, [LABEL_SAMPLE])
    assert out == [safety_service.safe_reply("zh")]
    assert gate.blocked is True


async def test_gate_assertion_scope_passes_co_occurrence(mock_llm):
    """合规共现措辞在断言型闸门原样放行。"""
    mock_llm.state["complete_text"] = "SAFE"
    gate = safety_service.sentence_gate("zh", scope="assertion")
    out = await _collect(gate, [CO_OCCURRENCE_SAMPLE])
    assert "".join(out) == CO_OCCURRENCE_SAMPLE
    assert gate.blocked is False


async def test_gate_blocks_banned_term_without_classifier(mock_llm):
    """黑名单句在任何模型调用前即拦（成本与确定性）。"""
    gate = safety_service.sentence_gate("zh", scope="assertion")
    out = await _collect(gate, ["你们的星座配对度很高。"])
    assert out == [safety_service.safe_reply("zh")]
    assert not mock_llm.calls["complete"]


# ── L1 端到端：对话流 system prompt 同时含两段 ─────────────


async def test_chat_stream_prompt_contains_both_paragraphs(db, make_user, mock_llm):
    """真实调用链验证：message_generator.py 注入点同时带 boundary 与 evidence_policy。"""
    await make_user("u1", ai_consent=True)
    mock_llm.state["complete_text"] = "SAFE"
    async for _ in message_generator.generate_chat_stream(
        db=db, user_id="u1", user_input="hello", language="zh"
    ):
        pass
    system_prompt = mock_llm.calls["stream"][0]["system_prompt"]
    assert safety_service.boundary("zh") in system_prompt
    assert safety_service.evidence_policy("zh") in system_prompt
