"""KG-30 金句卡（POST /share/quote-card）：只把**用户自己写下的原话**做成可分享的卡。

四条判据各管一件会被静默做错的事：

1. **正文不进请求体。** 端点收 `source_type/source_id`，句子一律服务端从库取。收下调用方
   传上来的文本，这个端点就变成「任意文本 → 品牌水印 + 二维码 + 本人推荐码」的生成器，
   而它对外说的是「这是你自己写的话」——转出去的卡还挂着本人短链。所以这里用一条
   **注入测试**（请求体多带一个 `source_text`）证明卡上印的是库里那句，不是请求里那句。
2. **AI 抽取的记忆不出卡**（`generation_run_id` 非空 → 40405，不是 40403）。
   复用 40403 会让界面念出「记忆已经不在了」，而记忆明明在——只是那句不是用户写的。
3. **归属与类型同进 WHERE。** `daily_insight`／`weekly_summary` 与 `reflection` 住在同一张
   `growth_records`，靠调用方「记得再判一次」迟早会漏；别人的 id 与不存在的 id 给同一个回答。
4. **AI 草稿存下来的反思改口径。** 卡上不印「你的原话」，印 `quote.draft_note`：用户确实按了
   保存，可那句可能一个字都不是他写的。**看得见的披露**优先于 PDF 的 AIGC 元数据标识
   （`_aigc_metadata` 只能标「整份是 AI 产出」，草稿被改写过再保存的那一类会被误标）。

截断（`QUOTE_MAX_CHARS`）也在服务端：前端本地预览按同一个数字裁，两边不一样就会出现
「预览一句、导出的卡是另一句」。
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.core import copy
from backend.core.auth import get_current_user
from backend.core.errors import AppException, ErrorCode
from backend.core.middleware import register_exception_handlers
from backend.database import get_db
from backend.models.schemas import QuoteCardRequest
from backend.repositories import growth_repository, memory_repository
from backend.routers import share as share_router
from backend.services.share_service import share_service

UTC = timezone.utc

USER_KW = dict(timezone="UTC", language="zh", ai_consent=True)

# 2026-03-05 10:00 UTC；UTC 用户读到的日期大字就是这一天（不做二次时区换算）
WHEN = datetime(2026, 3, 5, 10, 0, tzinfo=UTC)


@pytest.fixture
def app(db):
    app = FastAPI()
    app.include_router(share_router.router, prefix="/api/v1", tags=["share"])

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: "u1"
    # 40405/40403 在外面长成什么样子由同一套 handler 决定；不装它就只测到「抛了」
    register_exception_handlers(app)
    return app


async def _post_quote(app, payload) -> "object":
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/v1/share/quote-card", json=payload)


def _pdf_text(pdf: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


async def _seed_memory(db, user_id: str, content: str, **kw):
    return await memory_repository.create(
        db,
        user_id,
        content=content,
        category="preference",
        source=kw.pop("source", "self"),
        conviction=0.6,
        **kw,
    )


async def _seed_reflection(db, user_id: str, content: str, *, metadata=None, record_type="reflection"):
    return await growth_repository.create(
        db,
        user_id,
        record_type=record_type,
        content=content,
        metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None,
    )


# ═══ 1. 出卡的两条路 ═══


async def test_own_memory_makes_a_card_with_the_original_line(db, make_user):
    await make_user("u1", **USER_KW)
    memory = await _seed_memory(db, "u1", "我不想再为了显得合群而假装开心")
    memory.created_at = WHEN
    await db.flush()

    pdf = await share_service.export_quote_card_pdf(db, "u1", "memory", memory.id)
    text = _pdf_text(pdf)

    assert pdf[:5] == b"%PDF-"
    assert "我不想再为了显得合群而假装开心" in text
    # 日期按用户本地时区（UTC 用户 → 同一天），且明写「你自己写下的」
    assert "2026-03-05" in text
    assert copy.t("share", "quote.own_words", "zh") in text
    assert copy.t("share", "quote.draft_note", "zh") not in text


async def test_own_reflection_makes_a_card(db, make_user):
    await make_user("u1", **USER_KW)
    record = await _seed_reflection(db, "u1", "今天我第一次说出「我不想去」")

    pdf = await share_service.export_quote_card_pdf(db, "u1", "reflection", record.id)
    text = _pdf_text(pdf)

    assert "今天我第一次说出" in text
    assert copy.t("share", "quote.own_words", "zh") in text


# ═══ 2. 正文只从库里取 ═══


async def test_schema_has_no_room_for_the_text(db, make_user):
    """请求体只有「来源是哪一条」。多一个正文字段＝端点开始收用户传的句子。"""
    assert set(QuoteCardRequest.model_fields) == {"source_type", "source_id"}


async def test_request_body_cannot_put_words_on_the_card(app, db, make_user):
    """注入测试：请求体里带一句「假原话」，卡上印的仍是库里那一句。

    这条是判据 1 的行为面：`model_fields` 少了 `source_text` 只证明 schema 不认它，
    而 Pydantic 默认忽略多余字段——真正的证人必须是「发出去的 PDF 里没有那句话」。
    """
    await make_user("u1", **USER_KW)
    memory = await _seed_memory(db, "u1", "库里真正的那一句")
    memory.created_at = WHEN
    await db.flush()

    resp = await _post_quote(
        app,
        {
            "source_type": "memory",
            "source_id": memory.id,
            "source_text": "这是调用方塞进来的一句，不是我写过的",
            "content": "这是调用方塞进来的一句，不是我写过的",
        },
    )

    assert resp.status_code == 200, resp.content
    text = _pdf_text(resp.content)
    assert "库里真正的那一句" in text
    assert "调用方塞进来" not in text
    assert resp.headers["content-disposition"] == 'attachment; filename="closenai_quote_card.pdf"'
    assert resp.headers["content-type"] == "application/pdf"


async def test_illegal_source_type_is_40001_before_any_query(app, db, make_user):
    await make_user("u1", **USER_KW)
    memory = await _seed_memory(db, "u1", "一句原话")

    resp = await _post_quote(app, {"source_type": "daily_insight", "source_id": memory.id})

    assert resp.status_code == 400
    assert resp.json()["code"] == ErrorCode.INVALID_PARAMS


# ═══ 3. AI 产出不冒充原话 ═══


async def test_ai_extracted_memory_is_rejected_with_its_own_code(db, make_user):
    """`generation_run_id` 非空 → 40405，且消息里要说清是 AI 整理的。

    40403 的那句「记忆已经不在了」在这种情形下是假话：记忆在，只是那句不是用户写的。
    """
    await make_user("u1", **USER_KW)
    memory = await _seed_memory(
        db, "u1", "AI 从对话里整理出来的一句", generation_run_id="run-0001"
    )

    with pytest.raises(AppException) as exc_info:
        await share_service.export_quote_card_pdf(db, "u1", "memory", memory.id)

    assert exc_info.value.code == ErrorCode.QUOTE_SOURCE_NOT_ORIGINAL == 40405
    assert "AI" in exc_info.value.message


async def test_ai_draft_reflection_keeps_the_card_but_changes_the_wording(db, make_user):
    """草稿保存（metadata.source=draft）：卡照出，但那一行改口，不印「你的原话」。"""
    await make_user("u1", **USER_KW)
    record = await _seed_reflection(
        db, "u1", "这周我好像一直在拖延", metadata={"source": "draft", "cluster": "关系"}
    )

    pdf = await share_service.export_quote_card_pdf(db, "u1", "reflection", record.id)
    text = _pdf_text(pdf)

    assert "这周我好像一直在拖延" in text
    assert copy.t("share", "quote.draft_note", "zh") in text
    assert copy.t("share", "quote.own_words", "zh") not in text


async def test_mood_analysis_reflection_also_discloses(db, make_user):
    """情绪分析生成的那条同口径（source=mood_analysis）：也算 AI 起草，不冒充原创。"""
    await make_user("u1", **USER_KW)
    record = await _seed_reflection(
        db, "u1", "最近容易累", metadata={"source": "mood_analysis"}
    )

    pdf = await share_service.export_quote_card_pdf(db, "u1", "reflection", record.id)

    assert copy.t("share", "quote.draft_note", "zh") in _pdf_text(pdf)


async def test_broken_metadata_json_does_not_500_the_card(db, make_user):
    """历史行人手改坏的 metadata 当「没有溯源」：一张卡不该因为一格脏 JSON 就 500。"""
    await make_user("u1", **USER_KW)
    record = await _seed_reflection(db, "u1", "一句正常的话")
    record.metadata_json = "{\"source\": "
    await db.flush()

    pdf = await share_service.export_quote_card_pdf(db, "u1", "reflection", record.id)

    assert copy.t("share", "quote.own_words", "zh") in _pdf_text(pdf)


# ═══ 4. 归属与类型 ═══


async def test_other_users_memory_is_40403(db, make_user):
    await make_user("u1", **USER_KW)
    await make_user("u2", **USER_KW)
    theirs = await _seed_memory(db, "u2", "别人写的一句话")

    with pytest.raises(AppException) as exc_info:
        await share_service.export_quote_card_pdf(db, "u1", "memory", theirs.id)

    assert exc_info.value.code == ErrorCode.MEMORY_NOT_FOUND == 40403


async def test_superseded_memory_is_40403(db, make_user):
    """已被替代／软删的那条不出卡：与记忆卡、记忆详情同一口径（40403）。"""
    await make_user("u1", **USER_KW)
    memory = await _seed_memory(db, "u1", "一条已经被替代的话", status="superseded")

    with pytest.raises(AppException) as exc_info:
        await share_service.export_quote_card_pdf(db, "u1", "memory", memory.id)

    assert exc_info.value.code == ErrorCode.MEMORY_NOT_FOUND


async def test_other_users_reflection_and_an_unknown_id_answer_the_same(db, make_user):
    """别人的反思 id 与不存在的 id 都是 40402——不能靠这条端点探测谁有什么。"""
    await make_user("u1", **USER_KW)
    await make_user("u2", **USER_KW)
    theirs = await _seed_reflection(db, "u2", "别人存下的一句")

    codes = []
    for target in (theirs.id, "00000000-0000-0000-0000-000000000000"):
        with pytest.raises(AppException) as exc_info:
            await share_service.export_quote_card_pdf(db, "u1", "reflection", target)
        codes.append(exc_info.value.code)

    assert set(codes) == {ErrorCode.RESOURCE_NOT_FOUND}


async def test_daily_insight_id_is_not_a_reflection(db, make_user):
    """同表里的 AI 产出行（daily_insight）拿不到卡：record_type 进 WHERE，不靠调用方补判。"""
    await make_user("u1", **USER_KW)
    insight = await _seed_reflection(
        db, "u1", "每日洞察写出来的一段话", record_type="daily_insight"
    )

    with pytest.raises(AppException) as exc_info:
        await share_service.export_quote_card_pdf(db, "u1", "reflection", insight.id)

    assert exc_info.value.code == ErrorCode.RESOURCE_NOT_FOUND == 40402


async def test_blank_line_is_not_a_card(db, make_user):
    await make_user("u1", **USER_KW)
    memory = await _seed_memory(db, "u1", "   \n  ")

    with pytest.raises(AppException) as exc_info:
        await share_service.export_quote_card_pdf(db, "u1", "memory", memory.id)

    assert exc_info.value.code == ErrorCode.RESOURCE_NOT_FOUND


# ═══ 5. 服务端裁切（与前端预览同一个数字）═══


async def test_over_long_line_is_truncated_by_the_server(db, make_user):
    await make_user("u1", **USER_KW)
    memory = await _seed_memory(db, "u1", "耐" * 279 + "尾巴")

    pdf = await share_service.export_quote_card_pdf(db, "u1", "memory", memory.id)
    text = _pdf_text(pdf)

    assert text.count("耐") == share_service.QUOTE_MAX_CHARS - 1
    assert "尾巴" not in text
    assert "…" in text


async def test_quote_max_chars_is_the_number_the_frontend_mirrors():
    """280 这个数字有两份（后端 `QUOTE_MAX_CHARS` / 前端 `QUOTE_CARD_MAX_CHARS`），
    前端那一侧的闸在 `pdfExportWiring.test.ts` 逐字比对；这里钉住后端不许顺手改。"""
    assert share_service.QUOTE_MAX_CHARS == 280
