"""KG-28 三层次情绪分析（方案 §6.2）的后端语义闸。

这个功能唯一对外承诺的是「每条结论都指得回用户自己的原话」，所以这些闸钉的**不是生成**，
而是**校验与失败方向**：

- 引用没逐字命中 ⇒ 结论整条撤掉（降级），不是「换个说法继续说」；
- 证据不足时**不许补造**解释——`pattern=null` 是这条产品线的脸面；
- 危机排在配额之前：额度耗尽的人正在说想伤害自己，回「今天次数用完了」不可接受；
- 开关关 ⇒ 在 AI 同意判定**之前**就 40402，一条 40303 就已经泄露「这里藏了个功能」。

另外两条耦合在本文件之外，所以在这里钉住：
① 服务传给 `llm.complete` 的 `purpose` 必须落在 `quota_service.MOOD_ANALYSIS_PURPOSES` 里，
   两边写岔一个字符的后果是**免费档不限次**（配额永远数不到）；
② `memory_repository.list_own_words` 的 `generation_run_id IS NULL` 与
   `reflection_service.save` 的 `metadata_json.source` 共同定义了「用户自己的话」——
   AI 抽取的记忆、AI 起草的反思都不能当证据，否则是自我引用。
"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.config import settings
from backend.core.auth import get_current_user
from backend.core.errors import AppException, ErrorCode
from backend.core.middleware import register_exception_handlers
from backend.database import get_db
from backend.models.generation_run import AiGenerationRun
from backend.models.journey import SafetyEvent
from backend.models.schemas import MoodAnalysisRequest
from backend.repositories import memory_repository, mood_repository
from backend.routers import mood as mood_router
from backend.services.mood_analysis_service import (
    DEPTH_MIN_SOURCES,
    EXCERPT_MAX_CHARS,
    MIN_QUOTE_CHARS,
    mood_analysis_service,
)
from backend.services.quota_service import MOOD_ANALYSIS_PURPOSES
from backend.services.reflection_service import reflection_service

# 两句「用户自己的话」：都长过 MIN_QUOTE_CHARS，且互不含对方
MEMO = "我这周每天凌晨三点都醒，醒了就再睡不着"
REFL = "我跟他说了一次我其实很委屈，他没有接话"


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(settings, "mood_analysis_depths_enabled", True)


async def _seed(db, make_user, *, emoji="😰", tz="UTC", consent=True, **kw):
    """造一个「今天已签到」的用户 + 两条自有原文（一记忆一反思）。"""
    await make_user("u1", timezone=tz, ai_consent=consent, **kw)
    await mood_repository.create(db, "u1", emoji, "焦虑", "规则引擎解读")
    await memory_repository.create(
        db, "u1", MEMO, "preference", "self", 0.5, None, emotion_context=emoji
    )
    await reflection_service.save(db, "u1", REFL, source="manual")
    await db.flush()


def _answer(pattern_quote: str, *, quotes=None, summary="你这周睡得很碎。",
            alternatives=None, question=None, next_step=None, confidence=0.62,
            description="凌晨三点反复醒来，可能和白天没说出口的那句话有关"):
    """拼一份「模型听话时」的 JSON 输出。"""
    payload = {"summary": summary}
    if description is not None:
        payload["pattern"] = {"description": description, "quote": pattern_quote,
                              "confidence": confidence}
    payload["alternatives"] = alternatives or []
    payload["quotes"] = quotes or []
    if question is not None:
        payload["question"] = question
    if next_step is not None:
        payload["next_step"] = next_step
    return json.dumps(payload, ensure_ascii=False)


def _reply_with(payload_text: str):
    """让 mock_llm 的分析调用吐指定 JSON，安全分类器仍吐默认串。"""
    def _fn(prompt, **kwargs):
        if kwargs.get("purpose", "").startswith("services.mood_analysis."):
            return payload_text
        return "SAFE"
    return _fn


def _analysis_calls(mock_llm):
    return [c for c in mock_llm.calls["complete"]
            if str(c.get("purpose", "")).startswith("services.mood_analysis.")]


async def _seed_run(db, *, user_id="u1", purpose=None, status="ok"):
    db.add(AiGenerationRun(
        id=str(uuid4()), user_id=user_id, op="complete",
        purpose=purpose or MOOD_ANALYSIS_PURPOSES[0], status=status, output_chars=40,
    ))
    await db.flush()


# ═══ A · 前置门的顺序 ═══

async def test_flag_off_rejects_before_any_business_judgement(db, make_user, mock_llm):
    """开关关（默认态）：40402，且**一次 LLM 都不调**、不查签到、不判同意。

    关着的时候这个功能对外不存在，所以它的失败方向不能花钱、也不能泄露任何内部状态。
    """
    assert settings.mood_analysis_depths_enabled is False
    await make_user("u1", timezone="UTC", ai_consent=True)
    with pytest.raises(AppException) as exc:
        await mood_analysis_service.analyze(
            db, "u1", MoodAnalysisRequest(depth="light"), "en"
        )
    assert exc.value.code == ErrorCode.RESOURCE_NOT_FOUND
    assert _analysis_calls(mock_llm) == []


async def test_no_checkin_today_is_40402(db, make_user, mock_llm, flag_on):
    await make_user("u1", timezone="UTC", ai_consent=True)
    with pytest.raises(AppException) as exc:
        await mood_analysis_service.analyze(
            db, "u1", MoodAnalysisRequest(depth="explore"), "en"
        )
    assert exc.value.code == ErrorCode.RESOURCE_NOT_FOUND
    assert _analysis_calls(mock_llm) == []


async def test_analysis_pool_is_independent_and_only_counts_analysis_purposes(
    db, make_user, mock_llm, flag_on
):
    """第三池的三条口径：别的 purpose 不算、status=error 不算、命中本池 purpose 才算满。"""
    await _seed(db, make_user)
    # 一次签到解读的留痕 + 一次失败的调用，都不该占用分析额度
    await _seed_run(db, purpose="services.mood._interpret", status="ok")
    await _seed_run(db, purpose=MOOD_ANALYSIS_PURPOSES[0], status="error")
    assert await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="explore"), "en"
    )
    # 额度 1/天：上面那次成功后（真跑 LLM 时才有留痕，这里 mock 不写行）手动补一行
    await _seed_run(db, purpose=MOOD_ANALYSIS_PURPOSES[1], status="fallback")
    with pytest.raises(AppException) as exc:
        await mood_analysis_service.analyze(
            db, "u1", MoodAnalysisRequest(depth="explore"), "en"
        )
    assert exc.value.code == ErrorCode.QUOTA_EXCEEDED


async def test_every_depth_reports_a_purpose_the_quota_can_count(
    db, make_user, mock_llm, flag_on
):
    """服务与配额两侧的连接点：purpose 字符串必须逐字在册（写岔＝免费档不限次）。"""
    await _seed(db, make_user)
    mock_llm.state["complete_fn"] = _reply_with(_answer(REFL))
    for depth in ("light", "explore", "deep"):
        await mood_analysis_service.analyze(
            db, "u1", MoodAnalysisRequest(depth=depth), "en"
        )
    purposes = [c["purpose"] for c in _analysis_calls(mock_llm)]
    assert purposes == [f"services.mood_analysis.{d}" for d in ("light", "explore", "deep")]
    assert set(purposes) == set(MOOD_ANALYSIS_PURPOSES)
    # 三档的 token 上限与 lite 路由也在这里一起钉（方案 §6.2.2 的那张表）
    assert [c["max_tokens"] for c in _analysis_calls(mock_llm)] == [120, 300, 600]
    assert [c["lite"] for c in _analysis_calls(mock_llm)] == [True, True, False]


# ═══ B · 逐字校验 ═══

async def test_explore_keeps_the_pattern_when_the_quote_is_verbatim(
    db, make_user, mock_llm, flag_on
):
    await _seed(db, make_user)
    mock_llm.state["complete_fn"] = _reply_with(
        _answer(REFL, alternatives=["也可能是你这周本来就睡得少"],
                description="两次没接话之后你开始自己扛")
    )
    resp = await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="explore"), "en"
    )
    assert resp.safety_status == "ok"
    assert resp.pattern is not None and resp.pattern.description
    assert [e.source_type for e in resp.evidence] == ["reflection"]
    assert resp.evidence[0].excerpt == REFL
    assert resp.alternatives == ["也可能是你这周本来就睡得少"]
    assert resp.quota.limit == settings.free_daily_mood_analyses


async def test_paraphrase_quote_drops_the_pattern_entirely(db, make_user, mock_llm, flag_on):
    """改写过的引用（少了「一次」、多了「向」）＝不命中 ⇒ 撤掉结论，不是照发。"""
    await _seed(db, make_user)
    mock_llm.state["complete_fn"] = _reply_with(_answer("我跟向他说了我其实很委屈"))
    resp = await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="explore"), "en"
    )
    assert resp.safety_status == "degraded"
    assert resp.pattern is None
    assert resp.alternatives == []
    assert resp.evidence == []
    # 降级出口只说可核对的事实，且**不含**模型那句解释
    assert "我跟他说了一次" not in resp.summary
    # 请求语言是 en ⇒ 情绪标签跟着走（不是库里那句中文常量）
    assert "Anxious" in resp.summary


async def test_deep_needs_two_independent_sources_not_two_quotes(
    db, make_user, mock_llm, flag_on
):
    """同一行原文引两句，在深潜档里仍是一条 ⇒ 降级（§6.2.2 要的是「独立引用」）。"""
    await _seed(db, make_user)
    head, tail = MEMO[:10], MEMO[10:]
    assert head != tail
    mock_llm.state["complete_fn"] = _reply_with(
        _answer(head, quotes=[tail], alternatives=["A", "B"], next_step={"text": "去聊聊", "action": "reflect"})
    )
    resp = await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="deep"), "en"
    )
    assert DEPTH_MIN_SOURCES["deep"] == 2
    assert resp.safety_status == "degraded"
    assert resp.pattern is None
    # 命中的那条证据仍然给（它是逐字事实），只是不给解释
    assert [e.source_id for e in resp.evidence] == [resp.evidence[0].source_id]
    assert resp.next_step is None


async def test_deep_with_two_sources_shows_both_evidence_cards(db, make_user, mock_llm, flag_on):
    await _seed(db, make_user)
    mock_llm.state["complete_fn"] = _reply_with(
        _answer(MEMO, quotes=[REFL], alternatives=["读法 A", "读法 B"],
                next_step={"text": "把那句说出来", "action": "reflect"})
    )
    resp = await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="deep"), "en"
    )
    assert resp.safety_status == "ok"
    assert {e.source_type for e in resp.evidence} == {"memory", "reflection"}
    assert resp.next_step is not None and resp.next_step.action == "reflect"
    assert len(resp.alternatives) == 2


async def test_ai_extracted_memory_is_not_evidence(db, make_user, mock_llm, flag_on):
    """`generation_run_id` 非空＝这行是 AI 抽取的，不能当「你自己的话」引用（自我引用）。"""
    await _seed(db, make_user)
    await memory_repository.create(
        db, "u1", "你总是把别人的情绪放在第一位", "values", "self", 0.5, None,
        generation_run_id=str(uuid4()),
    )
    mock_llm.state["complete_fn"] = _reply_with(_answer("你总是把别人的情绪放在第一位"))
    resp = await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="explore"), "en"
    )
    assert resp.safety_status == "degraded"
    assert resp.evidence == []


async def test_ai_drafted_reflection_is_not_evidence(db, make_user, mock_llm, flag_on):
    """采纳 AI 草稿那条反思不进证据池——这依赖 KG-28 补上的 `metadata_json.source`。

    这一条同时是那条**接缝**的正向证人：`reflection_service.save` 若不写 source，
    这里就退回「draft 与 manual 同形」，本条会红在「AI 起草的句子被当成原话引用」。
    """
    await make_user("u1", timezone="UTC", ai_consent=True)
    await mood_repository.create(db, "u1", "😢", "难过", "规则引擎解读")
    await reflection_service.save(db, "u1", REFL, source="draft")
    mock_llm.state["complete_fn"] = _reply_with(_answer(REFL))
    resp = await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="explore"), "en"
    )
    assert resp.safety_status == "degraded"
    assert resp.evidence == []


async def test_previous_analysis_summary_is_not_evidence(db, make_user, mock_llm, flag_on):
    """上一轮存下来的**分析正文**（source=mood_analysis）也不进证据池。

    不挡这一格，深潜档会把模型自己写的句子指认成「你自己的原话」，然后据此再造一条结论——
    那是自我引用，而且这条路径是前端「保存为反思」一键就能走到的。
    """
    await make_user("u1", timezone="UTC", ai_consent=True)
    await mood_repository.create(db, "u1", "😢", "难过", "规则引擎解读")
    await reflection_service.save(db, "u1", REFL, source="mood_analysis")
    mock_llm.state["complete_fn"] = _reply_with(_answer(REFL))
    resp = await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="explore"), "en"
    )
    assert resp.safety_status == "degraded"
    assert resp.evidence == []


def test_ai_authored_reflection_sources_match_the_quote_card():
    """「哪些 source 算 AI 写的」在两处必须同一个答案，所以从两份源码里各取一次集合。

    口径分叉的后果是一句话在两张屏幕上互相打脸：金句卡（`share_service`）按这个集合判
    「措辞未必全是你自己的」，证据池（`mood_analysis_service`）按它判能不能称作「你的原话」。
    一边漏一个值，同一句话就会一边被印成「这句话是你自己敲下的」、另一边被引成用户原话。
    期望集合在这里写字面量，不是拿一份代码去比另一份的变量——那两侧就同源了。
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    captures = {
        "share": (
            root / "backend/services/share_service.py",
            r"draft = _reflection_metadata_source\(record\)\s+in \(([^)]*)\)",
        ),
        "mood_analysis": (
            root / "backend/services/mood_analysis_service.py",
            r"if _reflection_source\(r\) in \(([^)]*)\)",
        ),
    }
    found = {}
    for label, (path, pattern) in captures.items():
        text = path.read_text(encoding="utf-8")
        match = re.search(pattern, text)
        assert match, f"{path.name} 里的判据形状变了，本条闸要跟着重读"
        found[label] = frozenset(re.findall(r'"([^"]+)"', match.group(1)))

    expected = frozenset({"draft", "mood_analysis"})
    assert found["share"] == expected, found["share"]
    assert found["mood_analysis"] == expected, found["mood_analysis"]


async def test_a_quote_shorter_than_the_floor_is_not_evidence(
    db, make_user, mock_llm, flag_on
):
    """短引用（"三点"）在任何一段话里都能撞上，它证明不了模式 ⇒ 判不命中。"""
    await _seed(db, make_user)
    short = "三点"
    assert len(short) < MIN_QUOTE_CHARS
    mock_llm.state["complete_fn"] = _reply_with(_answer(short))
    resp = await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="explore"), "en"
    )
    assert resp.safety_status == "degraded"


async def test_light_never_returns_a_pattern_even_when_the_model_offers_one(
    db, make_user, mock_llm, flag_on
):
    """轻松档按契约不产出 pattern：0 条证据约束下的任何「模式」都是猜。"""
    await _seed(db, make_user)
    mock_llm.state["complete_fn"] = _reply_with(
        _answer(MEMO, summary="凌晨三点醒着的那段时间不好熬。",
                question="那一晚你在想的那件事，后来有下文吗？")
    )
    resp = await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="light"), "en"
    )
    assert resp.safety_status == "ok"
    assert resp.pattern is None
    assert resp.alternatives == []
    assert resp.next_step is not None and resp.next_step.action == "reflect"
    # 「有就带」：引用命中时证据仍然给
    assert [e.source_type for e in resp.evidence] == ["memory"]


async def test_degraded_summary_follows_the_request_language(db, make_user, mock_llm, flag_on):
    """降级出口是**没有 AI 产出时**也要说的一句话，15 语言都得有（文案库证人）。"""
    await _seed(db, make_user)
    mock_llm.state["complete_fn"] = _reply_with(_answer("完全不在这两句里的另一种说法"))
    for language, witness in (("zh", "我还没有足够多你自己的原话"), ("en", "name a pattern")):
        resp = await mood_analysis_service.analyze(
            db, "u1", MoodAnalysisRequest(depth="explore"), language
        )
        assert resp.safety_status == "degraded", language
        assert witness in resp.summary, (language, resp.summary)


async def test_out_of_range_confidence_is_clamped_not_rejected(
    db, make_user, mock_llm, flag_on
):
    """越界值夹紧而不是整档降级（展示用的把握程度），但响应里不许出现 1.4。"""
    await _seed(db, make_user)
    mock_llm.state["complete_fn"] = _reply_with(_answer(REFL, confidence=1.4))
    resp = await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="explore"), "en"
    )
    assert resp.pattern is not None
    assert resp.pattern.confidence == 1.0


async def test_material_excerpt_is_truncated_to_the_contract_cap(db, make_user, mock_llm, flag_on):
    """长原文截到 EXCERPT_MAX_CHARS：响应里的 excerpt 与校验用的是同一串，不另开一份。"""
    await _seed(db, make_user)
    long_text = "我" * (EXCERPT_MAX_CHARS + 200)
    await memory_repository.create(db, "u1", long_text, "preference", "self", 0.5, None)
    mock_llm.state["complete_fn"] = _reply_with(_answer("我" * MIN_QUOTE_CHARS))
    resp = await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="explore"), "en"
    )
    hits = [e for e in resp.evidence if len(e.excerpt) == EXCERPT_MAX_CHARS]
    assert hits, resp.evidence
    # prompt 里喂给模型的编号行同样被截断（否则 500 字符这个上限只是展示层的装饰）
    prompt = _analysis_calls(mock_llm)[-1]["prompt"]
    assert long_text not in prompt


# ═══ C · 危机排在配额之前 ═══

async def test_crisis_short_circuits_without_the_model_and_writes_an_event(
    db, make_user, mock_llm, flag_on
):
    await _seed(db, make_user)
    # 危机词表是**分语言**的（safety.CRISIS_TRIGGERS[kind][lang]），请求语言 en 就只查 en 那组。
    # 用中文短语配 en 会一条都不命中，测试就测不到危机分支了。
    resp = await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="deep", focus="I want to die"), "en"
    )
    assert resp.safety_status == "crisis"
    assert resp.pattern is None and resp.evidence == [] and resp.alternatives == []
    assert resp.quota is None, "危机出口不许带计数器"
    assert _analysis_calls(mock_llm) == [], "危机分支不该花钱、不该占额度"
    rows = (await db.execute(SafetyEvent.__table__.select())).mappings().all()
    assert [r["source"] for r in rows] == ["mood_analysis"]
    assert rows[0]["kind"] == "self_harm"
    # tz=UTC 落 resolve_hotline 的兜底档（"如 112 / 911"）——按字面量断言，
    # 不读 safety_service 的常量，否则断言两侧同源、恒真。
    assert "112" in resp.summary
    assert resp.next_step is not None and resp.next_step.action == "none"


async def test_crisis_is_checked_before_the_quota(db, make_user, mock_llm, flag_on):
    """额度已经耗尽 + 命中危机 ⇒ 给危机文案，不给 40301。

    这一条钉的是**调用顺序**：把 `_crisis_screen` 挪到配额之后，测试不会报语法错，
    只会让一个正在说想伤害自己的人读到「今天次数用完了」。
    """
    await _seed(db, make_user)
    await _seed_run(db, purpose=MOOD_ANALYSIS_PURPOSES[0], status="ok")
    resp = await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="explore", focus="I want to die"), "en"
    )
    assert resp.safety_status == "crisis"
    assert _analysis_calls(mock_llm) == []


async def test_crisis_reply_uses_the_region_inferred_from_the_timezone(
    db, make_user, mock_llm, flag_on
):
    await _seed(db, make_user, tz="America/New_York")
    resp = await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="light", focus="I want to die"), "en"
    )
    assert resp.safety_status == "crisis"
    assert "988" in resp.summary


# ═══ D · 幂等与失败方向 ═══

async def test_same_request_id_does_not_run_the_model_twice(
    db, make_user, mock_llm, flag_on
):
    """双击（免费档一天只有 1 次）不该把额度吃掉第二次。"""
    await _seed(db, make_user)
    mock_llm.state["complete_fn"] = _reply_with(_answer(REFL))
    first = await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="explore", request_id="rq-1"), "en"
    )
    second = await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="explore", request_id="rq-1"), "en"
    )
    assert len(_analysis_calls(mock_llm)) == 1
    assert second.analysis_id == first.analysis_id
    third = await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="explore", request_id="rq-2"), "en"
    )
    assert third.analysis_id != first.analysis_id
    assert len(_analysis_calls(mock_llm)) == 2


async def test_llm_exception_degrades_instead_of_raising(db, make_user, mock_llm, flag_on):
    """provider 挂了 ⇒ 降级出口，不是 500。

    这一条的存在理由：分析是「锦上添花」的那一格，它失败时用户仍然应该看到
    今天签到的事实，而不是一个错误码——错误方向要和「证据不足」同一档。
    """
    await _seed(db, make_user)

    def _boom(prompt, **kwargs):
        if str(kwargs.get("purpose", "")).startswith("services.mood_analysis."):
            raise RuntimeError("provider down")
        return "SAFE"

    mock_llm.state["complete_fn"] = _boom
    resp = await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="deep"), "en"
    )
    assert resp.safety_status == "degraded"
    assert resp.pattern is None
    assert "凌晨三点" not in resp.summary


@pytest.mark.parametrize("junk", ["", "no json here", "{not valid", "[1,2]"])
async def test_unparseable_output_degrades(db, make_user, mock_llm, flag_on, junk):
    await _seed(db, make_user)
    mock_llm.state["complete_fn"] = _reply_with(junk)
    resp = await mood_analysis_service.analyze(
        db, "u1", MoodAnalysisRequest(depth="explore"), "en"
    )
    assert resp.safety_status == "degraded"
    assert resp.pattern is None


# ═══ E · HTTP 面 ═══

@pytest.fixture
def app(db):
    app = FastAPI()
    app.include_router(mood_router.router, prefix="/api/v1", tags=["mood"])

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: "u1"
    register_exception_handlers(app)
    return app


async def _post(app, payload, language="en"):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/v1/mood/analysis", json=payload,
                                 headers={"X-Language": language})


async def test_http_returns_40402_while_the_flag_is_off(app, make_user, mock_llm):
    await make_user("u1", timezone="UTC", ai_consent=False)
    resp = await _post(app, {"depth": "explore"})
    assert resp.status_code == 404
    assert resp.json()["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)
    # 40303 会泄露「这里有个要同意才能用的功能」，所以关着时它**不该**先跑
    assert _analysis_calls(mock_llm) == []


async def test_http_requires_ai_consent_when_the_flag_is_on(app, make_user, mock_llm, flag_on):
    await make_user("u1", timezone="UTC", ai_consent=False)
    resp = await _post(app, {"depth": "light"})
    assert resp.json()["code"] == int(ErrorCode.AI_CONSENT_REQUIRED)


async def test_http_success_envelope_carries_the_contract_fields(
    app, db, make_user, mock_llm, flag_on
):
    """`app` 的 get_db 覆盖与本用例的 `db` 是同一个会话（pytest 按用例缓存 fixture）。"""
    await _seed(db, make_user)
    mock_llm.state["complete_fn"] = _reply_with(_answer(REFL))
    resp = await _post(app, {"depth": "explore"}, language="zh")
    body = resp.json()
    # success() 的信封是 code=200（不是 0）：与其余端点同一份契约，别在这里另起一套。
    assert body["code"] == 200, body
    data = body["data"]
    assert set(data) == {
        "analysis_id", "depth", "created_at", "summary", "pattern", "alternatives",
        "evidence", "next_step", "safety_status", "generation_run_id", "quota",
    }
    assert data["depth"] == "explore"
    assert data["pattern"] is not None
    assert data["evidence"][0]["excerpt"] == REFL
    assert data["quota"]["limit"] == settings.free_daily_mood_analyses
    # mock_llm 不写留痕行 ⇒ used 仍是 0；这一格在生产里由 record_run 即时提交后读回
    assert data["generation_run_id"] is None


async def test_http_rejects_unknown_depth_with_40001(app, make_user, flag_on):
    await make_user("u1", timezone="UTC", ai_consent=True)
    resp = await _post(app, {"depth": "psychedelic"})
    assert resp.json()["code"] == int(ErrorCode.INVALID_PARAMS)
