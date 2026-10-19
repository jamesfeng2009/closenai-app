"""
ClosenAI 后端测试基建。

Fixtures:
- db: sqlite+aiosqlite 内存库会话（每用例独立建表，结束销毁）。

  pgvector HALFVEC 列在 sqlite 的兼容方案（已验证可行，无需改写列类型）：
  SQLite 对未知类型名 HALFVEC(512) 按 type affinity 宽松建表；pgvector
  的 bind/result processor 会把 list[float] 序列化为文本存储、读取时还原，
  因此 ORM 建表与读写均可原样工作（含 embedding 列）。
  唯一不兼容的是 memory_repository.semantic_search 内 PG 专属算子 <=>
  （余弦距离）——sqlite 上无法执行向量检索，用 stub_semantic_search
  在仓库层注入候选行，语义相似度排序本身不在 sqlite 断言范围内。

- mock_llm (autouse): 替换 llm.complete / embed / stream 为可控假实现，
  杜绝测试触网。返回 SimpleNamespace(state, calls)：
  state["complete_text"] / state["complete_fn"] 控制补全输出，
  state["embed_vectors"] 控制向量输出（缺省返回 512 维固定向量）；
  calls 记录每次调用用于断言。

- time_travel: 冻结“现在”。服务层以 `from ... import utc_now` 绑定函数名，
  必须逐模块打补丁（补丁点见 _TIME_PATCH_TARGETS）。用法：
      time_travel.set(datetime(2026, 8, 5, 12, tzinfo=timezone.utc))

- make_user: 造用户工厂（get_or_create + 属性覆盖 + flush）。
"""
import importlib
from datetime import date, datetime, time as dt_time, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.models  # noqa: F401  注册全部表到 Base.metadata
from backend.ai.llm import llm
from backend.core.time_utils import safe_zoneinfo
from backend.database import Base
from backend.repositories import memory_repository, user_repository

EMBEDDING_DIM = 512


@pytest.fixture
async def db():
    """每用例独立的 sqlite 内存库会话。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def make_user(db):
    """造用户：不存在则创建，随后用 kwargs 覆盖字段并 flush。"""

    async def _make(user_id: str = "u-test", **overrides):
        user = await user_repository.get_or_create_user(db, user_id)
        for key, value in overrides.items():
            setattr(user, key, value)
        await db.flush()
        return user

    return _make


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch):
    """全局 LLM mock（autouse）：llm 为跨模块共享单例，属性级替换即全局生效。"""
    state = {
        "complete_text": "MOCK_LLM_REPLY",
        "complete_fn": None,  # 可选：fn(prompt, **kwargs) -> str
        "embed_vectors": None,  # 可选：显式指定返回向量
    }
    calls = {"complete": [], "embed": [], "stream": []}

    async def fake_complete(prompt, **kwargs):
        calls["complete"].append({"prompt": prompt, **kwargs})
        if state["complete_fn"] is not None:
            return state["complete_fn"](prompt, **kwargs)
        return state["complete_text"]

    async def fake_embed(texts, dim=None):
        calls["embed"].append(list(texts))
        if state["embed_vectors"] is not None:
            return state["embed_vectors"]
        return [[0.01] * EMBEDDING_DIM for _ in texts]

    async def fake_stream(prompt, **kwargs):
        calls["stream"].append({"prompt": prompt, **kwargs})
        yield state["complete_text"]

    monkeypatch.setattr(llm, "complete", fake_complete)
    monkeypatch.setattr(llm, "embed", fake_embed)
    monkeypatch.setattr(llm, "stream", fake_stream)
    return SimpleNamespace(state=state, calls=calls)


@pytest.fixture
def stub_semantic_search(monkeypatch):
    """替换 pgvector 语义检索（sqlite 无 <=> 算子）为内存候选集。

    rows 元素为 dict，键与 memory_repository.semantic_search 的 mappings 行一致：
    id/user_id/content/category/status/source/conviction_score/created_at/
    superseded_at/transition_reason/similarity（可附 superseded_by）。
    """
    state = {"rows": []}

    async def fake_search(
        db, user_id, query_embedding, limit=10, include_superseded=False, category=None
    ):
        return [dict(r) for r in state["rows"]][:limit]

    monkeypatch.setattr(memory_repository, "semantic_search", fake_search)
    return state


# 时间补丁点：模块 → 该模块内 import 绑定的时间函数名
_TIME_PATCH_TARGETS = {
    "backend.services.quota_service": ("local_day_start_utc",),
    "backend.services.journey_service": (
        "local_day_start_utc",
        "local_now",
        "local_today",
        "utc_now",
    ),
    "backend.services.first_read_service": ("local_today", "utc_now"),
    "backend.services.commitment_service": ("local_today",),
    "backend.services.memory_service": ("local_day_start_utc", "utc_now"),
    "backend.services.mood_service": ("local_day_start_utc", "local_today"),
    "backend.services.user_service": ("local_today", "utc_now"),
    "backend.services.export_service": ("utc_now",),
    "backend.services.personality_service": ("local_day_start_utc", "local_now", "utc_now"),
    "backend.services.growth_service": ("local_day_start_utc", "local_now", "local_today", "utc_now"),
    "backend.services.bedtime_service": ("local_day_start_utc", "local_today"),
    "backend.services.curriculum_service": ("local_day_start_utc", "local_now", "local_today", "utc_now"),
    "backend.services.cron_service": ("local_day_start_utc", "local_now", "local_today", "utc_now"),
    "backend.services.share_service": ("local_day_start_utc", "local_now", "local_today", "utc_now"),
    "backend.services.message_generator": ("utc_now", "local_day_start_utc"),
    "backend.services.discovery_service": ("utc_now",),
    "backend.services.reflection_service": ("local_day_start_utc",),
    "backend.services.push.copy": ("local_today",),
    "backend.repositories.mood_repository": ("local_day_start_utc", "utc_now"),
    "backend.repositories.journey_repository": ("utc_now",),
    "backend.repositories.chat_repository": ("local_day_start_utc",),
    "backend.repositories.growth_repository": ("utc_now",),
    "backend.repositories.personality_repository": ("utc_now",),
}


class TimeTravel:
    """冻结时间工具：set() 后所有补丁模块读到的“现在”即设定值。"""

    def __init__(self, state):
        self._state = state

    def set(self, now: datetime) -> None:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        self._state["now"] = now


@pytest.fixture
def time_travel(monkeypatch):
    state = {"now": datetime.now(timezone.utc)}

    def fake_utc_now() -> datetime:
        return state["now"]

    def fake_local_now(tz_name=None) -> datetime:
        return state["now"].astimezone(safe_zoneinfo(tz_name))

    def fake_local_today(tz_name=None) -> date:
        return fake_local_now(tz_name).date()

    def fake_local_day_start_utc(tz_name=None, day: date | None = None) -> datetime:
        zone = safe_zoneinfo(tz_name)
        d = day or fake_local_now(tz_name).date()
        return datetime.combine(d, dt_time.min, tzinfo=zone).astimezone(timezone.utc)

    fakes = {
        "utc_now": fake_utc_now,
        "local_now": fake_local_now,
        "local_today": fake_local_today,
        "local_day_start_utc": fake_local_day_start_utc,
    }
    for module_name, func_names in _TIME_PATCH_TARGETS.items():
        module = importlib.import_module(module_name)
        for func_name in func_names:
            monkeypatch.setattr(module, func_name, fakes[func_name])
    return TimeTravel(state)
