"""KG-26 的保存溯源：反思要能指回「由库里哪条提示、哪个版本产生」。

为什么版本不符要红而不是收下：以后按提示统计保存率时，`2026-11-1` 的那条文案和下一版
的同 id 文案不是同一个问题。静默收下＝把两个版本的数混成一列，而这正是这个产品对外
承诺「有证据」的那一类数字。所以 `40001` 是**故意的失败方向**：前端提示重新选择，
用户那一次点击不会白掉（内容还在输入框里）。

`_to_response` 的读侧同样要测，但口径是**读不回**：KG-26 的溯源只进写侧。
`ReflectionResponse` 的字段和 Go 的 `reflectionPayload` 逐字节对齐（`test_n8_batch5_contract.py`
里那条 JSON 字段序闸就是证人），在这里加两个字段等于把 N8 切流的一刀提前挪到本任务，
而那需要 Go 侧同批改 `metadata_json` 解析＋重录字节夹具。所以这里钉住「响应形状不变」。
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.core.auth import get_current_user
from backend.core.errors import AppException, ErrorCode
from backend.core.middleware import register_exception_handlers
from backend.database import get_db
from backend.models.growth import GrowthRecord
from backend.repositories import growth_repository
from backend.routers import reflection as reflection_router
from backend.services import prompt_library
from backend.services.reflection_service import reflection_service

VERSION = "2026-11-1"


@pytest.fixture
def app(db):
    app = FastAPI()
    app.include_router(reflection_router.router, prefix="/api/v1", tags=["reflections"])

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: "u1"
    # 线上把 AppException 包成 {code:40001,...} 的是同一套 handler；测试 app 不装它，
    # 40001 就会以异常形式冒出 ASGI，测到的是「抛了」而不是「对外长什么样」。
    register_exception_handlers(app)
    return app


async def _post_save(app, payload):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/v1/reflections", json=payload)


async def _record(db, record_id) -> GrowthRecord:
    return await db.get(GrowthRecord, record_id)


# ═══ 服务层 ═══

async def test_save_with_prompt_lands_traceability_in_metadata_and_tags(db, make_user):
    await make_user("u1")
    resp = await reflection_service.save(
        db,
        "u1",
        "今天会议我提前把要说的三句写下来了。",
        prompt_id="rp-017",
        prompt_version=VERSION,
        source="prompt",
    )
    await db.flush()
    record = await _record(db, resp.id)
    meta = json.loads(record.metadata_json)
    assert meta == {
        "source": "prompt",
        "prompt_id": "rp-017",
        "prompt_version": VERSION,
        "cluster": "action_outcome",
        "function": "goal",
    }
    assert record.record_type == "reflection"
    # tags 里是簇名＝成长记录可以按簇筛；它和 metadata 同源，不许各自写一份口径。
    assert record.tags == "action_outcome"
    # 响应面不带溯源字段（见文件头）：这里的正面证人是「行里有、响应里没有」这一对。
    assert not hasattr(resp, "prompt_id")
    assert set(type(resp).model_fields) == {
        "id",
        "content",
        "related_memory_ids",
        "created_at",
    }


async def test_manual_save_records_only_the_source(db, make_user):
    """不带提示的保存路径：响应字段一个不多，`tags` 仍为空，但 metadata 里现在有 `source`。

    2026-11-02（KG-28）改判：这一条原来断的是 `metadata_json is None`。
    理由不是「顺手多写一列」，是那格 None 让**自己写的**和**采纳 AI 草稿的**在库里同形，
    于是 KG-30 的金句卡按 metadata 判「这句是不是 AI 起草的」永远落到 False，
    会把 AI 起草的句子印成「这句话是你自己敲下的」；KG-28 的证据池也靠这一格
    把 AI 起草的反思挡在「用户原话」之外。`tags` 保持 None：只有提示库那条分支按簇打标。
    """
    await make_user("u1")
    resp = await reflection_service.save(db, "u1", "自己写的一条。")
    await db.flush()
    record = await _record(db, resp.id)
    assert json.loads(record.metadata_json) == {"source": "manual"}
    assert record.tags is None
    assert resp.id == record.id


async def test_draft_source_is_distinguishable_from_manual_in_the_row(db, make_user):
    """`source="draft"` 与 `source="manual"` 必须在库里分得开——读侧的判据都指这一格。

    消费者有两个，都在本文件之外，所以这条闸写在这里：
    - `share_service._reflection_metadata_source` → 金句卡的「措辞未必全是你自己的」披露；
    - `mood_analysis_service._reflection_source` → AI 起草的反思不进「用户原话」证据池。
    """
    await make_user("u1")
    draft = await reflection_service.save(db, "u1", "AI 起草、我按了保存。", source="draft")
    mine = await reflection_service.save(db, "u1", "我自己敲的一句。", source="manual")
    await db.flush()
    draft_row = await _record(db, draft.id)
    mine_row = await _record(db, mine.id)
    assert json.loads(draft_row.metadata_json) == {"source": "draft"}
    assert json.loads(mine_row.metadata_json) == {"source": "manual"}
    assert draft_row.metadata_json != mine_row.metadata_json


@pytest.mark.parametrize(
    "kwargs,message_part",
    [
        ({"prompt_id": "rp-999", "prompt_version": VERSION, "source": "prompt"}, "提示不存在"),
        ({"prompt_id": "rp-001", "prompt_version": "2027-01-1", "source": "prompt"}, "提示库版本已更新"),
        ({"prompt_id": "rp-001", "source": "prompt"}, "必须成对给出"),
        ({"prompt_version": VERSION, "source": "prompt"}, "必须成对给出"),
        ({"prompt_id": "rp-001", "prompt_version": VERSION, "source": "manual"}, "必须是 prompt"),
    ],
)
async def test_bad_traceability_is_rejected_before_any_row_is_written(
    db, make_user, kwargs, message_part
):
    """反面证人：如果这些情形静默收下，`growth_records` 会长出读不出来的行。"""
    await make_user("u1")
    with pytest.raises(AppException) as exc:
        await reflection_service.save(db, "u1", "一条内容", **kwargs)
    assert exc.value.code == ErrorCode.INVALID_PARAMS
    assert message_part in str(exc.value.message)
    rows = await growth_repository.list_by_type(db, "u1", "reflection", limit=10)
    assert rows == []


async def test_unknown_prompt_id_is_rejected_even_if_version_matches_library(
    db, make_user
):
    """`prompt_version` 用**当前库版本**（不是字面量）时仍须因 id 不存在而 40001。

    这条和上一条是一对：只钉字面版本，等于允许「版本对、id 编」的行进来。
    """
    await make_user("u1")
    current = prompt_library.version()
    with pytest.raises(AppException) as exc:
        await reflection_service.save(
            db, "u1", "一条内容", prompt_id="rp-031", prompt_version=current, source="prompt"
        )
    assert exc.value.code == ErrorCode.INVALID_PARAMS
    assert current == VERSION  # 库版本本身也是被断言的一侧，不许漂


async def test_list_row_keeps_traceability_in_the_column_and_the_response_stays_thin(
    db, make_user
):
    """溯源在 `metadata_json` 列里读得回来；列表响应**不**透传它——这两件事都要钉住。

    前一句是真需求（按提示统计保存率时直接从行里取），后一句是边界：
    响应字段是 Go/Python 字节对齐的面， KG-26 不许单方面加宽。
    """
    await make_user("u1")
    saved = await reflection_service.save(
        db, "u1", "按提示写的一条", prompt_id="rp-024", prompt_version=VERSION, source="prompt"
    )
    await growth_repository.create(db, "u1", "reflection", "无 metadata 的历史行")
    await db.flush()

    items = {r.content: r for r in await reflection_service.list(db, "u1", limit=10)}
    assert set(items) == {"按提示写的一条", "无 metadata 的历史行"}
    for item in items.values():
        assert set(type(item).model_fields) == {
            "id",
            "content",
            "related_memory_ids",
            "created_at",
        }
        assert not hasattr(item, "prompt_id") and not hasattr(item, "source")

    row = await _record(db, saved.id)
    meta = json.loads(row.metadata_json)
    assert (meta["prompt_id"], meta["source"], meta["cluster"]) == (
        "rp-024",
        "prompt",
        meta["cluster"],
    )
    assert meta["cluster"] == prompt_library.get("rp-024")["cluster"]


# ═══ 端点层 ═══

async def test_endpoint_returns_40001_wrapper_for_stale_prompt_version(app, make_user):
    await make_user("u1")
    resp = await _post_save(
        app,
        {
            "content": "用旧版提示写的一条",
            "prompt_id": "rp-002",
            "prompt_version": "2020-01-01",
            "source": "prompt",
        },
    )
    body = resp.json()
    assert body["code"] == 40001
    assert "提示库版本已更新" in body["message"]


async def test_endpoint_saves_with_prompt_and_keeps_the_response_shape(app, make_user):
    """带提示保存成功 200，且 `data` 的键**只有**那四个。

    这里之所以盯键集合而不是盯值：前端 `saveReflection` 的返回直接喂进时间线渲染，
    后端多给一个字段不会报错，但 Go 接管这一刀之后会少两个字段——
    同一个 App 在切流前后渲染不一致，正是 N8 字节对齐要避免的那类事故。
    """
    await make_user("u1")
    resp = await _post_save(
        app,
        {
            "content": "今晚只发一句，不等回复。",
            "prompt_id": "rp-017",
            "prompt_version": VERSION,
            "source": "prompt",
        },
    )
    body = resp.json()
    assert body["code"] == 200, body
    assert set(body["data"]) == {"id", "content", "related_memory_ids", "created_at"}
    schema = app.openapi()["components"]["schemas"]["ReflectionCreate"]
    assert {"prompt_id", "prompt_version", "source"} <= set(schema["properties"])


def test_blank_content_is_rejected_at_the_schema_edge():
    """空内容闸（KG-26 验收点名的「空内容有测试」）。

    只过 `max_length` 的空白正文会真写进 `growth_records`，然后进时间线计数与周报的
    「你写了 N 条」——用户回看时那一条是空的。所以拦在 schema，而不是拦在某个入口。
    """
    from pydantic import ValidationError

    from backend.models.schemas import ReflectionCreate

    for blank in ("", "   ", "\n\t "):
        with pytest.raises(ValidationError):
            ReflectionCreate(content=blank)
    # 正面一侧：一个空格都不多的正文照常收。
    assert ReflectionCreate(content="今天写了一句。").content == "今天写了一句。"


async def test_endpoint_rejects_blank_with_40001_and_writes_nothing(app, make_user, db):
    await make_user("u1")
    resp = await _post_save(app, {"content": "   "})
    body = resp.json()
    assert body["code"] == ErrorCode.INVALID_PARAMS
    rows = await growth_repository.list_by_type(db, "u1", "reflection", limit=10)
    assert rows == []


def test_schema_declares_traceability_on_the_write_side_only():
    """契约面：请求带三条溯源字段，响应一条不加（前端 types 与此同源）。

    两侧都断言才有意义：只钉请求侧，等于允许有人顺手把响应也加宽——
    而响应面是 Go `reflectionPayload` 的字节对齐面，加字段是 N8 的那一票要做的决定。
    """
    from backend.models.schemas import ReflectionCreate, ReflectionResponse

    fields = ReflectionCreate.model_fields
    assert {"prompt_id", "prompt_version", "source"} <= set(fields)
    assert fields["source"].default == "manual"
    assert list(ReflectionResponse.model_fields) == [
        "id",
        "content",
        "related_memory_ids",
        "created_at",
    ]
    with pytest.raises(Exception):  # noqa: B017 — pydantic 校验 Literal 取值
        ReflectionCreate(content="x", source="ai_made_me_do_it")
