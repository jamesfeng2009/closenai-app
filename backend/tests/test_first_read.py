"""
B1 · M1 性格初读卡（PRD §5.19.2 B01 / 架构方案 §二 M1）。

覆盖三件事：
1. 静态直出：断言 = 复述用户自述，零 LLM 调用，稀疏作答也必出 2–3 条（生成率 100%）。
2. 措辞闸：模板库自身逐条过 filter_template（§六-1），且违规条目确实被丢弃。
3. 存取与分享：POST 幂等 upsert、GET 按生成时语言复现、分享 PDF 出口。
"""
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from backend.core.auth import get_current_user
from backend.core.middleware import register_exception_handlers
from backend.database import get_db
from backend.models.journey import OnboardingProfile
from backend.repositories import onboarding_repository
from backend.routers import onboarding as onboarding_router
from backend.routers import share as share_router
from backend.services import first_read_service as first_read_module
from backend.services.first_read_service import (
    MAX_ASSERTIONS,
    MIN_ASSERTIONS,
    first_read_service,
    load_template,
)
from backend.ai.safety import safety_service

ZH_ANSWERS = [
    {"q": "conflict", "option": "speak_now"},
    {"q": "uncertainty", "option": "list_out"},
    {"q": "night", "option": "sharpest"},
]


def _zh_option_texts() -> dict:
    template = load_template("zh")
    return {o["id"]: o["text"] for q in template["questions"] for o in q["options"]}


# ── 1. 静态直出 ──

def test_three_answers_yield_three_assertions_that_echo_the_user():
    card = first_read_service.build_first_read(ZH_ANSWERS, "INTJ", "zh")
    options = _zh_option_texts()

    assert len(card["assertions"]) == 3
    # 断言必须包含用户自己选的那句原文（§5.19.2：初读卡只说用户给过的话）
    for answer, assertion in zip(ZH_ANSWERS, card["assertions"]):
        assert options[answer["option"]] in assertion
    assert card["closer"]
    assert card["card_payload"]["card_type"] == "first_read"


def test_build_makes_zero_llm_calls(mock_llm):
    """静态 JSON 直出（架构方案 §二 M1）：整条生成链路不调模型。"""
    first_read_service.build_first_read(ZH_ANSWERS, "INTJ", "zh")

    assert not mock_llm.calls["complete"]
    assert not mock_llm.calls["stream"]


def test_no_answers_still_produces_a_card():
    """生成率硬指标：一道没答也出 MIN_ASSERTIONS 条（fillers 兜底），永不空卡。"""
    card = first_read_service.build_first_read([], None, "zh")

    assert len(card["assertions"]) == MIN_ASSERTIONS
    assert all(a.strip() for a in card["assertions"])


def test_assertions_capped_at_max():
    card = first_read_service.build_first_read(
        ZH_ANSWERS + [{"q": "conflict", "option": "hold_in"}], "INTP", "zh"
    )

    assert len(card["assertions"]) == MAX_ASSERTIONS


def test_unknown_question_and_option_are_skipped():
    card = first_read_service.build_first_read(
        [{"q": "nope", "option": "speak_now"}, {"q": "conflict", "option": "nope"}],
        None,
        "zh",
    )

    assert len(card["assertions"]) == MIN_ASSERTIONS


def test_option_resolved_by_text_when_id_missing():
    text = load_template("zh")["questions"][0]["options"][1]["text"]
    card = first_read_service.build_first_read(
        [{"q": "conflict", "option": "", "text": text}], None, "zh"
    )

    assert text in card["assertions"][0]


def test_unsupported_language_falls_back_to_en_template():
    """模板 v1 只有 zh/en（措辞规范 §六 决策点 3 同口径）：fr 走 en 而非报错。"""
    card = first_read_service.build_first_read(
        [{"q": "conflict", "option": "speak_now"}], "ENTJ", "fr"
    )

    assert card["card_payload"]["language"] == "en"
    assert "say it right then" in card["assertions"][0]


def test_summary_only_for_known_persona():
    known = first_read_service.build_first_read([], "INTJ", "zh")
    skipped = first_read_service.build_first_read([], None, "zh")

    assert "INTJ" in known["summary"]
    assert load_template("zh")["personas"]["INTJ"] in known["summary"]
    assert skipped["summary"] == ""


def test_both_locales_share_the_same_key_structure():
    """en.json 与 zh.json 必须同构：题目/选项 id、槽位、16 型 personas 一一对应。"""
    zh, en = load_template("zh"), load_template("en")

    assert [q["id"] for q in zh["questions"]] == [q["id"] for q in en["questions"]]
    for zh_q, en_q in zip(zh["questions"], en["questions"]):
        assert [o["id"] for o in zh_q["options"]] == [o["id"] for o in en_q["options"]]
    assert zh["assertions"].keys() == en["assertions"].keys()
    for q_id in zh["assertions"]:
        assert zh["assertions"][q_id].keys() == en["assertions"][q_id].keys()
    assert len(en["personas"]) == len(zh["personas"]) == 16


# ── 2. 措辞闸 ──

@pytest.mark.parametrize("locale", ["zh", "en"])
def test_every_template_line_passes_the_wording_gate(locale):
    """模板库自身逐条过确定性闸（架构方案 §六-1）；槽位用假值填充后一起过。"""
    template = load_template(locale)
    texts = [template["summary_frame"].format(persona="INTJ", trait="x"), template["closer"]]
    texts += list(template["fillers"])
    texts += [o["text"] for q in template["questions"] for o in q["options"]]
    for slots in template["assertions"].values():
        texts += [body.format(option="x") for body in slots.values()]
    texts += [f"x {v} y" for v in template["personas"].values()]

    for text in texts:
        assert safety_service.filter_template(text, locale) == text, text


def test_filter_template_drops_banned_and_attribution():
    assert safety_service.filter_template("这件事需要治疗。", "zh") == ""
    assert safety_service.filter_template("因为 TA 冷淡，所以你焦虑。", "zh") == ""
    assert safety_service.filter_template("   ", "zh") == ""
    assert safety_service.filter_template("深夜是你慢下来的时间。", "zh") != ""


def test_violating_template_entry_is_dropped(monkeypatch):
    """槽位本身违规时被丢弃并由 filler 补足条数——闸是生效的，不是摆设。"""
    template = json.loads(json.dumps(load_template("zh")))
    template["assertions"]["conflict"]["speak_now"] = "你需要接受心理治疗。"
    monkeypatch.setitem(first_read_module._TEMPLATES, "zh", template)

    card = first_read_service.build_first_read(
        [{"q": "conflict", "option": "speak_now"}], None, "zh"
    )

    assert len(card["assertions"]) == MIN_ASSERTIONS
    assert all("治疗" not in a for a in card["assertions"])


# ── 3. 存取 ──

async def test_submit_then_restore_roundtrip(db, make_user):
    await make_user("u1")
    card = await first_read_service.submit(db, "u1", "INTJ", ZH_ANSWERS, "zh")

    restored = await first_read_service.restore(db, "u1")

    assert restored["id"] == card["id"]
    assert restored["assertions"] == card["assertions"]
    assert restored["closer"] == card["closer"]
    assert restored["language"] == "zh"
    assert restored["persona_type"] == "INTJ"


async def test_resubmit_upserts_single_row(db, make_user):
    """重复提交覆盖同一行（repository.upsert 语义），不产生第二条。"""
    await make_user("u1")
    first = await first_read_service.submit(db, "u1", "INTJ", ZH_ANSWERS, "zh")
    second = await first_read_service.submit(
        db, "u1", "INFP", [{"q": "conflict", "option": "hold_in"}], "zh"
    )
    rows = (await db.execute(select(OnboardingProfile))).scalars().all()
    profile = await onboarding_repository.get(db, "u1")

    assert second["id"] == first["id"]
    assert len(rows) == 1
    assert profile.persona_type == "INFP"


async def test_restore_keeps_the_language_it_was_generated_in(db, make_user):
    """重进 App 按生成时语言复现，不随偏好切换漂移（models/journey.py 注释）。"""
    await make_user("u1", language="en")
    await first_read_service.submit(db, "u1", "INTJ", ZH_ANSWERS, "zh")

    restored = await first_read_service.restore(db, "u1")

    assert restored["language"] == "zh"
    assert any("你" in a for a in restored["assertions"])


async def test_restore_without_profile_returns_none(db, make_user):
    await make_user("u1")

    assert await first_read_service.restore(db, "u1") is None


# ── 4. API ──

@pytest.fixture
def app(db):
    app = FastAPI()
    app.include_router(onboarding_router.router, prefix="/api/v1", tags=["onboarding"])
    app.include_router(share_router.router, prefix="/api/v1", tags=["share"])
    # 与 main.py 同口径：AppException → 业务码 + HTTP 段位，参数校验 → 400/40001
    register_exception_handlers(app)

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: "u1"
    return app


async def _post(app, url, payload):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(url, json=payload)


async def _get(app, url):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(url)


async def test_post_endpoint_returns_card_and_persists(app, db, make_user):
    await make_user("u1")

    resp = await _post(
        app, "/api/v1/onboarding/first-read",
        {"answers": ZH_ANSWERS, "persona_type": "intj", "language": "zh"},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["assertions"]) == 3
    assert data["persona_type"] == "INTJ"
    assert await onboarding_repository.get(db, "u1") is not None


async def test_get_endpoint_restores_and_404s_before_generation(app, make_user):
    await make_user("u1")

    missing = await _get(app, "/api/v1/onboarding/first-read")
    await _post(
        app, "/api/v1/onboarding/first-read",
        {"answers": ZH_ANSWERS, "persona_type": None, "language": "zh"},
    )
    got = await _get(app, "/api/v1/onboarding/first-read")

    assert missing.status_code == 404
    assert missing.json()["code"] == 40402
    assert got.json()["data"]["assertions"]


async def test_post_rejects_malformed_persona(app, make_user):
    await make_user("u1")

    resp = await _post(
        app, "/api/v1/onboarding/first-read",
        {"answers": [], "persona_type": "not a type", "language": "zh"},
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == 40001


async def test_share_endpoint_renders_pdf(app, make_user):
    await make_user("u1")
    await _post(
        app, "/api/v1/onboarding/first-read",
        {"answers": ZH_ANSWERS, "persona_type": "INTJ", "language": "zh"},
    )

    resp = await _post(app, "/api/v1/share/first-read-card", {})

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"


async def test_share_endpoint_404_without_card(app, make_user):
    await make_user("u1")

    resp = await _post(app, "/api/v1/share/first-read-card", {})

    assert resp.json()["code"] == 40402
