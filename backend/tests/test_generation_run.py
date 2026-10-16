"""
AI 生成留痕（O4）测试：ai_generation_runs 三入口记录、异常与模板回退路径、
派生行 generation_run_id 回链、以及分享 PDF 的 AIGC 元数据回指具体生成事件。

口径（backend/core/generation_run.py 模块注释）：
- 一次调用一行留痕，含 provider/model/purpose/耗时/token 与结局；
- 派生行的 generation_run_id 只在「产出可用」时写——异常调用与闸门回退均为空；
- 留痕写失败不阻断生成主流程。

测试基建：autouse 的 mock_llm 把 llm.complete/stream/embed 整体替换成假实现，本文件要跑
真实入口，故 fake_llm fixture 把三个函数换回模块实现、把 _get_client() 指向假 OpenAI
客户端（不出网），并把留痕用的 SessionLocal 指向本用例的内存库。
"""
import io
import json
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401  注册全部表到 Base.metadata
from backend.core import generation_run
from backend.ai import llm as llm_mod
from backend.ai.llm import llm
from backend.core.logging import set_trace_id, set_user_id
from backend.database import Base
from backend.models.generation_run import AiGenerationRun
from backend.models.growth import GrowthRecord
from backend.models.memory import Memory
from backend.services.share_service import share_service

CONTENT = "本周你记录了三次情绪，周二那天的措辞最平静。"


class _FakeStream:
    """openai SDK 的 stream=True 形态：await create() 得到可 async 迭代的流。"""

    def __init__(self, pieces):
        self.pieces = pieces

    def __aiter__(self):
        async def _gen():
            for piece in self.pieces:
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=piece))]
                )

        return _gen()


class FakeClient:
    """假 OpenAI 客户端：chat.completions / embeddings 两张脸，可控结局。"""

    pieces = ("abc", "def")

    def __init__(self, *, mode: str = "ok", content: str = CONTENT, dim: int = 8):
        self.mode = mode
        self.content = content
        self.dim = dim
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )
        self.embeddings = SimpleNamespace(create=self._embed)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.mode == "boom":
            raise RuntimeError("provider exploded")
        if kwargs.get("stream"):
            return _FakeStream(self.pieces)
        text = "" if self.mode == "empty" else self.content
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
        )

    async def _embed(self, **kwargs):
        self.calls.append(kwargs)
        if self.mode == "boom":
            raise RuntimeError("embed exploded")
        n = len(kwargs.get("input") or [])
        return SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=[0.5] * self.dim) for i in range(n)],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=0),
        )


@pytest.fixture
async def run_store(monkeypatch):
    """留痕表所在内存库：替换 generation_run.SessionLocal，返回会话工厂。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    monkeypatch.setattr(generation_run, "SessionLocal", factory)
    yield factory
    await engine.dispose()


@pytest.fixture
def fake_llm(monkeypatch, run_store):
    """走真实 llm 入口 + 假客户端；返回假客户端以便设置产出。"""
    monkeypatch.setattr(llm, "complete", llm_mod.complete)
    monkeypatch.setattr(llm, "stream", llm_mod.stream)
    monkeypatch.setattr(llm, "embed", llm_mod.embed)
    client = FakeClient()
    monkeypatch.setattr(llm_mod, "_client", client)
    set_user_id("u-run")
    set_trace_id("t-run")
    return client


async def _runs(factory) -> list[dict]:
    async with factory() as session:
        rows = (await session.execute(AiGenerationRun.__table__.select())).mappings().all()
    return [dict(r) for r in rows]


# ── 三入口留痕 ──


async def test_complete_writes_run_row(fake_llm, run_store):
    text = await llm.complete("生成一条周报", language="zh", lite=True, max_tokens=120)
    assert text == fake_llm.content

    rows = await _runs(run_store)
    assert len(rows) == 1
    row = rows[0]
    assert row["op"] == "complete"
    assert row["status"] == "ok"
    assert row["user_id"] == "u-run"
    assert row["trace_id"] == "t-run"
    assert row["lite"] is True
    assert row["language"] == "zh"
    assert row["model"] and row["provider"]
    assert row["input_chars"] == len("生成一条周报")
    assert row["output_chars"] == len(text)
    assert (row["prompt_tokens"], row["completion_tokens"]) == (11, 7)
    assert isinstance(row["latency_ms"], int)
    # 「当前可用生成」指针指向这条 run，派生行据此回链
    assert llm.current_run_id() == row["id"]


async def test_purpose_is_derived_from_call_site(fake_llm, run_store):
    await llm.complete("正文", language="zh")
    rows = await _runs(run_store)
    # 未显式传 purpose 时由调用帧推导，20+ 个调用点因此零改动
    assert rows[0]["purpose"] == "test_generation_run.test_purpose_is_derived_from_call_site"


async def test_explicit_purpose_wins(fake_llm, run_store):
    await llm.complete("正文", language="zh", purpose="manual.purpose")
    rows = await _runs(run_store)
    assert rows[0]["purpose"] == "manual.purpose"


async def test_error_path_records_and_reraises(monkeypatch, run_store):
    monkeypatch.setattr(llm, "complete", llm_mod.complete)
    monkeypatch.setattr(llm_mod, "_client", FakeClient(mode="boom"))
    set_user_id("u-run")
    with pytest.raises(RuntimeError):
        await llm.complete("正文", language="zh")
    rows = await _runs(run_store)
    assert rows[0]["status"] == "error"
    assert rows[0]["error_type"] == "RuntimeError"
    assert rows[0]["output_chars"] == 0
    # 异常调用没有可用产出：指针清空，派生行不会链到一次失败的生成
    assert llm.current_run_id() is None


async def test_empty_output_clears_run_pointer(fake_llm, run_store):
    fake_llm.content = ""
    assert await llm.complete("正文", language="zh") == ""
    rows = await _runs(run_store)
    assert rows[0]["status"] == "ok" and rows[0]["output_chars"] == 0
    assert llm.current_run_id() is None


async def test_stream_records_full_output(fake_llm, run_store):
    chunks = [c async for c in llm.stream("正文", language="en")]
    assert "".join(chunks) == "abcdef"
    rows = await _runs(run_store)
    assert rows[0]["op"] == "stream"
    assert rows[0]["status"] == "ok"
    assert rows[0]["output_chars"] == 6
    assert llm.current_run_id() == rows[0]["id"]


async def test_stream_error_records_partial_output(monkeypatch, run_store):
    monkeypatch.setattr(llm, "stream", llm_mod.stream)
    monkeypatch.setattr(llm_mod, "_client", FakeClient(mode="boom"))
    set_user_id("u-run")
    with pytest.raises(RuntimeError):
        async for _ in llm.stream("正文", language="en"):
            pass
    rows = await _runs(run_store)
    assert rows[0]["op"] == "stream"
    assert rows[0]["status"] == "error"
    assert rows[0]["error_type"] == "RuntimeError"


async def test_embed_records_vector_meta(fake_llm, run_store):
    vectors = await llm.embed(["a", "bb"])
    assert len(vectors) == 2
    rows = await _runs(run_store)
    assert rows[0]["op"] == "embed"
    assert json.loads(rows[0]["meta"]) == {"texts": 2, "dim": 8}
    assert rows[0]["input_chars"] == 3
    # 向量不是用户可见内容：embed 不建立生成指针
    assert llm.current_run_id() is None


async def test_embed_error_records(fake_llm, run_store):
    fake_llm.mode = "boom"
    with pytest.raises(RuntimeError):
        await llm.embed(["a"])
    rows = await _runs(run_store)
    assert rows[0]["status"] == "error"


async def test_recorder_failure_does_not_break_generation(fake_llm, monkeypatch):
    def broken_factory():
        raise RuntimeError("db down")

    monkeypatch.setattr(generation_run, "SessionLocal", broken_factory)
    assert await llm.complete("正文", language="zh") == fake_llm.content
    assert llm.current_run_id() is None


# ── 辅助调用与闸门回退 ──


async def test_classifier_call_does_not_steal_run_pointer(fake_llm, run_store):
    """正文生成 → 安全分类器自检：指针必须仍是正文那次（否则派生行链错）。"""
    from backend.ai.safety import safety_service

    await llm.complete("正文", language="zh")
    content_run = llm.current_run_id()
    assert await safety_service.check_text("一句正常的话", "zh") is True
    assert llm.current_run_id() == content_run

    rows = await _runs(run_store)
    classifier = next(r for r in rows if r["purpose"] == "safety_service.check_text")
    assert classifier["status"] == "ok"


async def _unsafe(text, language, scope="chat"):
    return False


async def test_check_or_marks_fallback_and_clears_pointer(fake_llm, run_store, monkeypatch):
    """闸门丢弃 AI 产出、改发模板：留痕置 fallback 且指针清空（落库正文不是 AI 写的）。"""
    from backend.ai.safety import safety_service

    monkeypatch.setattr(safety_service, "check_text", _unsafe)
    await llm.complete("正文", language="zh")
    content_run = llm.current_run_id()
    assert content_run

    out = await safety_service.check_or("AI 产出", "zh", "模板兜底文案", scope="assertion")
    assert out == "模板兜底文案"
    assert llm.current_run_id() is None
    rows = await _runs(run_store)
    row = next(r for r in rows if r["id"] == content_run)
    assert row["status"] == "fallback"
    assert row["fallback_reason"] == "safety_gate"


# ── 派生行回链 ──


async def test_extracted_memory_links_to_run(
    fake_llm, run_store, db, make_user, monkeypatch
):
    from backend.services.memory_service import memory_service

    fake_llm.content = json.dumps(
        [{"content": "我想开始跑步", "category": "goal", "source": "self", "conviction": 0.8}],
        ensure_ascii=False,
    )
    await make_user("u1")
    stored = await memory_service.extract_and_store(db, "u1", "我想开始跑步", language="zh")
    assert stored == 1

    row = (await db.execute(Memory.__table__.select())).mappings().all()[0]
    runs = await _runs(run_store)
    extract_run = next(r for r in runs if r["purpose"].endswith("extract_and_store"))
    assert row["generation_run_id"] == extract_run["id"]


async def _safe(text, language, scope="chat"):
    return True


async def test_growth_daily_insight_links_run_or_stays_null(
    fake_llm, run_store, db, make_user, monkeypatch
):
    """正文可用 → 回链 run；被闸门拦截回退模板 → generation_run_id 为空。"""
    from backend.services.growth_service import growth_service
    from backend.ai.safety import safety_service

    await make_user("u1")
    monkeypatch.setattr(safety_service, "check_text", _safe)
    content = await growth_service.generate_daily_insight(db, "u1", language="zh")
    rows = (await db.execute(GrowthRecord.__table__.select())).mappings().all()
    assert len(rows) == 1
    assert rows[0]["generation_run_id"] in {r["id"] for r in await _runs(run_store)}

    # 闸门拦截：落库正文来自模板，不该回链到被丢弃的那次生成
    await db.execute(GrowthRecord.__table__.delete())
    await db.commit()
    monkeypatch.setattr(safety_service, "check_text", _unsafe)
    fallback_content = await growth_service.generate_daily_insight(db, "u1", language="zh")
    rows = (await db.execute(GrowthRecord.__table__.select())).mappings().all()
    assert len(rows) == 1
    assert rows[0]["generation_run_id"] is None
    assert fallback_content != content


async def test_personality_profile_links_to_run(fake_llm, run_store, db, make_user):
    from backend.services.personality_service import personality_service
    from backend.models.personality import PersonalityProfile

    fake_llm.content = "INTP|观察1；观察2|独特之处一句"
    await make_user("u1", language="zh")
    resp = await personality_service.refresh(db, "u1")
    assert resp.success is True

    row = (await db.execute(PersonalityProfile.__table__.select())).mappings().all()[0]
    runs = await _runs(run_store)
    infer_run = next(r for r in runs if r["purpose"].endswith("refresh"))
    assert row["generation_run_id"] == infer_run["id"]


# ── AIGC 标识取用 run 元数据 ──


async def test_summarize_runs_only_counts_ok_rows(run_store):
    async with run_store() as session:
        session.add(
            AiGenerationRun(
                id="r-ok", op="complete", status="ok", provider="dashscope",
                model="qwen-plus", purpose="t",
            )
        )
        session.add(
            AiGenerationRun(
                id="r-fb", op="complete", status="fallback", provider="dashscope",
                model="qwen-turbo", purpose="t", fallback_reason="safety_gate",
            )
        )
        await session.commit()
        summary = await generation_run.summarize_runs(session, ["r-ok", "r-fb"])

    assert summary["run_ids"] == ["r-ok"]
    assert summary["models"] == ["dashscope/qwen-plus"]


def test_collect_run_ids_flattens_and_dedupes():
    assert generation_run.collect_run_ids("a, b", None, "a", "") == ["a", "b"]
    assert generation_run.collect_run_ids() == []


async def test_pdf_metadata_carries_run_provenance(run_store):
    async with run_store() as session:
        session.add(
            AiGenerationRun(
                id="r-meta", op="complete", status="ok", provider="dashscope",
                model="qwen-plus", purpose="t",
            )
        )
        await session.commit()
        summary = await generation_run.summarize_runs(session, ["r-meta"])

    from pypdf import PdfReader

    pdf = share_service._render_pdf("月度报告", ["正文一行"], aigc=True, aigc_runs=summary)
    info = PdfReader(io.BytesIO(pdf)).metadata
    assert "GB 45438-2025" in str(info["/Subject"])
    assert "dashscope/qwen-plus" in str(info["/Creator"])
    assert "r-meta" in str(info["/Keywords"])

    # 无留痕时声明照旧，但不编造模型名
    plain = share_service._render_pdf("报告", ["正文"], aigc=True)
    info2 = PdfReader(io.BytesIO(plain)).metadata
    assert "GB 45438-2025" in str(info2["/Subject"])
    assert "AI:" not in str(info2.get("/Creator") or "")
