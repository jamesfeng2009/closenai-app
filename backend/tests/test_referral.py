"""
B4 · M4 分享卡体系（架构方案 §二 M4 / 决策点 3）。

覆盖四件事：
1. 推荐码本体：字符集排除易混字符、格式校验、非法码不阻断注册。
2. 惰性生成：从不分享的用户不占码；分享卡导出这个"读"接口会写一次 referral_code。
3. 深链归因闭环：落地页解析推荐码 → 新用户注册时落 referred_by_code。
4. 三张分享卡：初读卡/记忆卡带品牌区（水印 + 二维码），年报卡的每个数字都要指回落库事实；
   AIGC 隐式标识只落在 AI 产出的 PDF 上。
"""
import pytest
from datetime import datetime, timezone
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.config import settings
from backend.core import copy
from backend.core.errors import AppException, ErrorCode
from backend.core.referral import (
    REFERRAL_ALPHABET,
    new_referral_code,
    normalize_referral_code,
    referral_link,
)
from backend.core.auth import get_current_user
from backend.database import get_db
from backend.models.memory import Memory
from backend.models.user import User
from backend.repositories import user_repository
from backend.routers import share as share_router
from backend.services.auth_service import auth_service
from backend.services.first_read_service import first_read_service
from backend.services.share_service import share_service

from backend.core.middleware import register_exception_handlers

ZH_ANSWERS = [
    {"q": "conflict", "option": "speak_now"},
    {"q": "uncertainty", "option": "list_out"},
    {"q": "night", "option": "sharpest"},
]


# ── 1. 推荐码本体 ──

def test_alphabet_excludes_ambiguous_characters():
    """手抄/打印场景的易混对（0/O、1/I/L、8/B、9/G）里，去掉数字侧与 I/L/O，保留字母侧。"""
    for ch in "0189ILO":
        assert ch not in REFERRAL_ALPHABET, ch
    assert len(set(REFERRAL_ALPHABET)) == len(REFERRAL_ALPHABET) == 29


def test_new_code_is_8_chars_from_alphabet_and_stable_under_normalize():
    code = new_referral_code()
    assert len(code) == 8
    assert set(code) <= set(REFERRAL_ALPHABET)
    assert normalize_referral_code(code) == code


@pytest.mark.parametrize(
    "raw",
    [
        "abcd2345",       # 小写 → 归一为大写
        "  ABCD2345  ",   # 深链参数常带空白
    ],
)
def test_normalize_accepts_and_uppercases(raw):
    assert normalize_referral_code(raw) == "ABCD2345"


@pytest.mark.parametrize(
    "raw",
    [None, "", "ABCD234", "ABCD23456", "ABCD234!", "ABCD1230", "ABCDIIII", "你好世界"],
)
def test_normalize_rejects_invalid_shapes(raw):
    """长度/字符集不符一律 None：宁可不归因，也不把脏输入写进 users 表。"""
    assert normalize_referral_code(raw) is None


def test_referral_link_is_brand_path():
    assert referral_link("https://closenai.app/", "ABCD2345") == "https://closenai.app/r/ABCD2345"


# ── 2. 惰性生成 ──

async def test_code_absent_until_first_share(db, make_user):
    """注册不预生成：只有真正取过分享链接的用户才占一个码。"""
    user = await make_user("u1")
    assert user.referral_code is None

    first = await share_service.get_share_link(db, "u1")
    second = await share_service.get_share_link(db, "u1")

    assert first["referral_code"] == second["referral_code"]
    assert normalize_referral_code(first["referral_code"]) == first["referral_code"]
    assert second["link"] == referral_link(settings.site_url, second["referral_code"])


async def test_each_user_gets_a_distinct_code(db, make_user):
    await make_user("u1")
    await make_user("u2")
    a = await share_service.get_share_link(db, "u1")
    b = await share_service.get_share_link(db, "u2")
    assert a["referral_code"] != b["referral_code"]


# ── 3. 归因闭环 ──

async def test_register_persists_referred_by_code(db):
    resp = await auth_service.register_with_password(
        db, "ref-attr@example.com", "refattr", "Passw0rd!", "zh", ref="abcd2345"
    )
    user = await user_repository.get_user(db, resp.user.id)
    assert user.referred_by_code == "ABCD2345"


async def test_register_with_garbage_ref_still_succeeds(db):
    """非法 ref 丢弃而不是 400：推广链接被截断不该让用户注册失败。"""
    resp = await auth_service.register_with_password(
        db, "ref-bad@example.com", "refbad", "Passw0rd!", "en", ref="!!!"
    )
    user = await user_repository.get_user(db, resp.user.id)
    assert user.referred_by_code is None


async def test_register_without_ref_leaves_column_null(db):
    resp = await auth_service.register_with_password(
        db, "ref-none@example.com", "refnone", "Passw0rd!", "en"
    )
    user = await user_repository.get_user(db, resp.user.id)
    assert user.referred_by_code is None


async def test_resolve_valid_code_reveals_no_referrer(db, make_user):
    await make_user("u1")
    link = await share_service.get_share_link(db, "u1")
    resolved = await share_service.resolve_referral(db, link["referral_code"])

    assert resolved["valid"] is True
    # 公开端点：只回答"该跳去哪"，不回分享者身份
    assert set(resolved) == {"valid", "app_store_url", "universal_link"}
    assert resolved["universal_link"] == link["link"]


async def test_resolve_does_not_distinguish_unknown_from_malformed(db, make_user):
    """枚举防护：不存在的有效格式码与非法码同样返回 200 + valid=false，不报错、不泄露差异。"""
    unknown = await share_service.resolve_referral(db, "AAAA2222")
    malformed = await share_service.resolve_referral(db, "***")

    assert unknown["valid"] is malformed["valid"] is False
    assert set(unknown) == set(malformed)
    # 非法格式不 echo 成 `…/r/` 坏链：落地页拿不到尾巴为空的链接
    assert malformed["universal_link"] == settings.site_url


async def test_resolve_rejects_deleted_referrer(db, make_user):
    """软删除用户不再可解析：注销的人不该继续给别人带量。"""
    user = await make_user("u1")
    link = await share_service.get_share_link(db, "u1")
    user.status = "deleted"
    await db.flush()

    assert (await share_service.resolve_referral(db, link["referral_code"]))["valid"] is False


# ── 4. 分享卡 ──

async def test_first_read_pdf_generates_code_and_embeds_qr(db, make_user):
    """初读卡 PDF 的品牌区含二维码（图元），副作用是首次导出即落库 referral_code。"""
    await make_user("u1")
    await first_read_service.submit(db, "u1", "INTJ", ZH_ANSWERS, "zh")

    pdf = await share_service.export_first_read_card_pdf(db, "u1")

    assert pdf[:4] == b"%PDF"
    assert b"/Subtype /Image" in pdf           # 深链二维码
    assert b"AIGC" not in pdf                  # 静态直出、零 LLM → 不打 AI 标识
    user = await user_repository.get_user(db, "u1")
    assert normalize_referral_code(user.referral_code) == user.referral_code


async def test_first_read_stores_code_bearing_share_url(db, make_user):
    """share_url 在落库时就带码：restore 按落库 payload 复现，事后补会丢。"""
    await make_user("u1")
    submitted = await first_read_service.submit(db, "u1", None, ZH_ANSWERS, "zh")
    restored = await first_read_service.restore(db, "u1")

    code = normalize_referral_code(submitted["share_url"].rsplit("/", 1)[-1])
    assert code is not None
    assert restored["share_url"] == referral_link(settings.site_url, code)


async def test_memory_card_pdf_owns_by_user(db, make_user):
    await make_user("u1")
    await make_user("u2")
    mine = Memory(id="m-own", user_id="u1", content="我答应自己每周跑一次。")
    theirs = Memory(id="m-other", user_id="u2", content="别人的记忆。")
    db.add_all([mine, theirs])
    await db.flush()

    pdf = await share_service.export_memory_card_pdf(db, "u1", "m-own")
    assert pdf[:4] == b"%PDF"
    assert b"/Subtype /Image" in pdf

    with pytest.raises(AppException) as exc_info:
        await share_service.export_memory_card_pdf(db, "u1", "m-other")
    assert exc_info.value.code == ErrorCode.MEMORY_NOT_FOUND


# ── 记忆卡周年口径：标题按本地日界判周年，正文永不提周年 ──
#
# 这两侧的口径是**不同**的（B4 偏离 2 的落点）：
#   标题 _memory_card_title —— 同月日且年差 ≥1 才写「N 年前的今天」（C3 偏离 2 根治）；
#   正文 _memory_card_lines —— 恒为 intro + 原话 + 日期，不含任何周年词。
# 所以这里的闸要同时防两种漂移：标题把假周年放出去、正文哪天又混进周年词。

ANNIV_ZH = ("一年前的今天", "年前的今天")


def _memory(content: str, created: datetime | None) -> Memory:
    """内存态 Memory：title/lines 只读属性，不入库就不受 server_default 影响。"""
    mem = Memory(id="m-card", user_id="u1", content=content)
    if created is not None:
        mem.created_at = created
    else:
        mem.created_at = None
    return mem


def _title(memory: Memory, user, today: datetime | None, tz: str | None, language: str) -> str:
    return share_service._memory_card_title(memory, user, today, tz, language)


async def test_memory_card_title_one_year_is_anniversary_first_form(db, make_user):
    """整一年（同月日、年差 1）走 anniversary_one，不是 anniversary_n.format(1)。"""
    user = await make_user("u1", timezone="Asia/Shanghai")
    today = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    created = datetime(2025, 10, 14, 12, 0, tzinfo=timezone.utc)

    for language, expected in (("zh", "一年前的今天"), ("en", "A Year Ago Today")):
        title = _title(_memory("每周跑一次。", created), user, today, None, language)
        assert title == expected
        assert title == copy.t("share", "memory.anniversary_one", language)


async def test_memory_card_title_multi_year_anniversary_formats_the_count(db, make_user):
    """年差 ≥2 走 anniversary_n，{{n}} 必须被替换成真实年数（直出花括号就是坏卡）。"""
    user = await make_user("u1", timezone="UTC")
    today = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    created = datetime(2023, 10, 14, 12, 0, tzinfo=timezone.utc)

    assert _title(_memory("每周跑一次。", created), user, today, "UTC", "zh") == "3 年前的今天"
    assert _title(_memory("每周跑一次。", created), user, today, "UTC", "en") == "3 Years Ago Today"


@pytest.mark.parametrize(
    "created,label",
    [
        (datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc), "同月日但年差 0"),
        (datetime(2025, 9, 14, 12, 0, tzinfo=timezone.utc), "年差 1 但月份不同"),
        (datetime(2025, 10, 13, 12, 0, tzinfo=timezone.utc), "年差 1 但早一天"),
        (datetime(2025, 10, 15, 12, 0, tzinfo=timezone.utc), "年差 1 但晚一天"),
        (datetime(2027, 10, 14, 12, 0, tzinfo=timezone.utc), "created 在未来：年差为负"),
    ],
)
async def test_memory_card_title_never_fabricates_an_anniversary(
    db, make_user, created, label
):
    """邻近界都不算周年：差一天/差一月/今年/未来都回原模板，绝不写「N 年前的今天」。"""
    user = await make_user("u1", timezone="UTC")
    today = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)

    title = _title(_memory("每周跑一次。", created), user, today, "UTC", "zh")
    assert title == "你说过的话", label
    assert title == copy.t("share", "memory.title", "zh")
    for word in ANNIV_ZH:
        assert word not in title


async def test_memory_card_title_uses_the_user_local_day_boundary(db, make_user):
    """时区铁律：同一对 UTC 时刻，本地日界不同 → 周年判定不同。

    created=2025-10-13T20:00Z 在上海是 10-14（与 today 的本地日 10-14 同月日 → 周年），
    在 UTC 是 10-13（与 today 不同日 → 不是周年）。tz 参数缺省时取用户资料里的时区。
    """
    shanghai = await make_user("u1", timezone="Asia/Shanghai")
    utc_user = await make_user("u2", timezone="UTC")
    today = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    created = datetime(2025, 10, 13, 20, 0, tzinfo=timezone.utc)

    assert _title(_memory("每周跑一次。", created), shanghai, today, None, "zh") == "一年前的今天"
    assert _title(_memory("每周跑一次。", created), utc_user, today, None, "zh") == "你说过的话"


async def test_memory_card_title_treats_naive_created_at_as_utc(db, make_user):
    """历史脏行（created_at 无 tzinfo）按 UTC 解释，不拿本地墙上时间乱判周年。"""
    user = await make_user("u1", timezone="UTC")
    today = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)

    assert _title(_memory("每周跑一次。", datetime(2025, 10, 14, 12, 0)), user, today, "UTC", "zh") == "一年前的今天"
    assert _title(_memory("每周跑一次。", datetime(2025, 10, 13, 23, 0)), user, today, "UTC", "zh") == "你说过的话"


async def test_memory_card_title_without_created_at_falls_back_to_template(db, make_user):
    """created_at 缺失时不崩、不猜：回原模板，正文日期位为空串。"""
    user = await make_user("u1", timezone="UTC")
    memory = _memory("每周跑一次。", None)

    assert _title(memory, user, datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc), "UTC", "zh") == "你说过的话"
    assert share_service._memory_card_lines(memory, "zh")[-1] == ""


@pytest.mark.parametrize("language,words", (("zh", ANNIV_ZH), ("en", ("Ago Today",))))
async def test_memory_card_body_never_mentions_the_anniversary(db, make_user, language, words):
    """真周年的那张卡，正文五个位置一个不多一个不少：intro / 空 / 「原话」 / 空 / 日期。"""
    user = await make_user("u1", timezone="UTC")
    created = datetime(2025, 10, 14, 12, 0, tzinfo=timezone.utc)
    memory = _memory("  我答应自己每周跑一次。  ", created)
    today = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)

    assert _title(memory, user, today, "UTC", language) != copy.t("share", "memory.title", language)
    assert share_service._memory_card_lines(memory, language) == [
        copy.t("share", "memory.intro", language),
        "",
        "「我答应自己每周跑一次。」",
        "",
        "2025-10-14",
    ]
    for word in words:
        assert word not in "".join(share_service._memory_card_lines(memory, language))


async def test_year_in_review_never_fabricates_a_card(db, make_user):
    """年报卡已实装，但仍不许给零记录的用户出卡：没有落库事实就没有数字可印。"""
    await make_user("u1")
    with pytest.raises(AppException) as exc_info:
        await share_service.export_year_in_review_pdf(db, "u1")
    assert exc_info.value.code == ErrorCode.RESOURCE_NOT_FOUND


def test_aigc_marker_lands_in_pdf_info_only_when_declared():
    """AIGC 隐式标识（GB 45438-2025）写进 /Info；不声明的卡不带。"""
    with_mark = share_service._render_pdf("报告", ["内容"], aigc=True)
    without = share_service._render_pdf("报告", ["内容"])
    assert b"Contains AI-generated content" in with_mark
    assert b"AIGC" in with_mark
    assert b"Contains AI-generated content" not in without


async def test_brand_copy_is_language_specific(db, make_user):
    await make_user("u1", language="zh")
    await make_user("u2", language="en")

    zh = await share_service._brand_for(db, "u1", True)
    en = await share_service._brand_for(db, "u2", False)

    assert zh["watermark"] != en["watermark"]
    assert zh["link"].startswith("https://closenai.app/r/")


# ── 5. API ──

@pytest.fixture
def app(db):
    app = FastAPI()
    app.include_router(share_router.router, prefix="/api/v1", tags=["share"])
    register_exception_handlers(app)

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: "u1"
    return app


async def _get(app, url):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(url)


async def _post(app, url, payload=None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(url, json=payload or {})


async def test_share_link_endpoint(app, make_user):
    await make_user("u1")
    resp = await _get(app, "/api/v1/share/link")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["link"].endswith(f"/r/{data['referral_code']}")


async def test_personality_card_deep_links_to_owner_code(db, make_user):
    """名片的 share_url 与分享文案必须同一个落点：两个链接并存时归因必断。"""
    await make_user("u1", name="小见")
    assert (await user_repository.get_user(db, "u1")).referral_code is None

    card = await share_service.export_personality_card(db, "u1", "zh", "minimal")

    code = normalize_referral_code(card["share_url"].rsplit("/", 1)[-1])
    assert code is not None
    assert card["share_url"] == referral_link(settings.site_url, code)
    assert card["share_url"] in card["share_text"]
    assert settings.app_store_url not in card["share_text"]


async def test_report_share_url_carries_code(db, make_user):
    await make_user("u1")
    report = await share_service.export_report(db, "u1", "weekly", "zh")

    assert normalize_referral_code(report["share_url"].rsplit("/", 1)[-1]) is not None


async def test_resolve_endpoint_is_public_and_negative_for_bad_code(app, make_user):
    """公开解析端点不要求登录，且非法码返回 200 + valid=false（不是 4xx，防探测）。"""
    resp = await _get(app, "/api/v1/share/resolve/ZZZZZZZZ")
    assert resp.status_code == 200
    assert resp.json()["data"]["valid"] is False


async def test_memory_card_endpoint_requires_ownership(app, make_user):
    await make_user("u1")
    missing = await _post(app, "/api/v1/share/memory-card", {"memory_id": "nope"})
    assert missing.status_code == 404
    assert missing.json()["code"] == 40403


async def test_year_in_review_endpoint_returns_business_code(app, make_user):
    """新用户还没有任何记录：端点返回业务码而不是 500，也不出空卡。"""
    await make_user("u1")
    resp = await _get(app, "/api/v1/share/year-in-review/pdf")
    assert resp.json()["code"] == 40402


async def test_first_read_pdf_endpoint_streams_pdf(app, db, make_user):
    await make_user("u1")
    await first_read_service.submit(db, "u1", None, ZH_ANSWERS, "zh")

    resp = await _post(app, "/api/v1/share/first-read-card")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"
