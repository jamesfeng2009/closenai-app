"""A5 · 记忆注入位次（conviction / 时间衰减）。

台账 `tasks.md` A5 行登记的口径缺口是「conviction 不进检索排序、也没有时间衰减」。
本批按同一条决策清单交付（原文见 tasks.md A5 的 ✅ 块），要点三条：

1. **默认口径不动**：`.trellis/spec/memory-layer.md:114-125` 逐字规定 `ORDER BY embedding
   <=> $query_vector LIMIT 5`，`.trellis/spec/context-engineering.md:232` 又写「长期记忆的
   选择已在 §2.2 定义，不重复」⇒ 加权式在文档面零出处。`settings.memory_inject_rank` 默认
   `distance`，此时 `get_memory_context` 的顺序与今天逐字相同。
2. **只动位次、不动成员集合**：`weighted` 只置换已取回的那批行，谁进 top-5 仍由 SQL 的
   距离序决定——top-5 是回归识别（`similarity > 0.80` 触发 revert）的取数窗口。
3. **重排放在回归循环之后**：revert 的副作用序列按距离序走完，开不开加权都不多不漏一次。

下面 10 条把这三条钉死；权重数字本身是占位（标定归跟踪器 #59），所以这里只断言
**单调性与不变量**，不断言具体名次以外的数值。
"""
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.config import settings
from backend.models.schemas import MemoryResponse
from backend.repositories.memory_repository import MemoryRepository
from backend.services.memory_service import (
    _inject_rank_score,
    memory_service,
    order_for_injection,
)

# `backend.services` / `backend.repositories` 的包 __init__ 把同名属性重导出成了单例，
# 所以源码闸一律按「函数/类定义所在文件」取源码，不按包属性名取。
MS_SRC = Path(inspect.getfile(order_for_injection)).read_text(encoding="utf-8")
REPO_SRC = Path(inspect.getfile(MemoryRepository)).read_text(encoding="utf-8")

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


def _resp(
    idx: int,
    *,
    similarity: float,
    conviction: float = 0.5,
    age_days: float = 0.0,
    status: str = "current",
) -> MemoryResponse:
    return MemoryResponse(
        id=f"m{idx}",
        user_id="u1",
        content=f"信念 {idx}",
        category="values",
        status=status,
        source="self",
        conviction=conviction,
        created_at=NOW - timedelta(days=age_days),
        similarity=similarity,
    )


def _row(
    idx: int,
    *,
    similarity: float,
    conviction: float = 0.5,
    age_days: float = 0.0,
    status: str = "current",
    superseded_by: str | None = None,
) -> dict:
    """与 memory_repository.semantic_search 的 mappings 行同构（含可空列）。"""
    return {
        "id": f"m{idx}",
        "user_id": "u1",
        "content": f"信念 {idx}",
        "category": "values",
        "status": status,
        "source": "self",
        "conviction_score": conviction,
        "created_at": NOW - timedelta(days=age_days),
        "superseded_at": None,
        "superseded_by": superseded_by,
        "transition_reason": None,
        "emotion_context": None,
        "similarity": similarity,
    }


def _ids(responses):
    return [r.id for r in responses]


# ── 默认口径不变 ──────────────────────────────────────────────


def test_default_rank_is_distance_and_is_the_shipped_default():
    """默认值本身是闸：把 config 里的默认改成 weighted 会当场红。"""
    assert settings.memory_inject_rank == "distance"
    rows = [
        _resp(1, similarity=0.90, conviction=0.2),
        _resp(2, similarity=0.89, conviction=0.95, age_days=900),
        _resp(3, similarity=0.88, conviction=0.5),
    ]
    assert _ids(order_for_injection(rows, now=NOW)) == ["m1", "m2", "m3"]


async def test_get_memory_context_keeps_distance_order_by_default(
    db, make_user, stub_semantic_search
):
    """端到端（service → 检索桩）确认：不改 env 时，注入顺序 == 检索返回顺序。"""
    await make_user()
    stub_semantic_search["rows"] = [
        _row(1, similarity=0.90, conviction=0.2),
        _row(2, similarity=0.89, conviction=0.95, age_days=800),
        _row(3, similarity=0.85, conviction=0.5, age_days=3),
    ]
    out = await memory_service.get_memory_context(db, "u1", "最近的状态怎么样？", limit=3)
    assert _ids(out) == ["m1", "m2", "m3"]


def test_unknown_mode_falls_back_to_distance_and_says_so(caplog):
    """写错 env 不能悄悄改掉位次：按默认口径处理 + 留一条 warning。"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "memory_inject_rank", "conviction_first")
        rows = [_resp(1, similarity=0.80, conviction=0.1), _resp(2, similarity=0.79, conviction=1.0)]
        with caplog.at_level("WARNING", logger="closenai.memory"):
            assert _ids(order_for_injection(rows, now=NOW)) == ["m1", "m2"]
        assert "memory_inject_rank" in caplog.text


def test_zero_weights_degrade_to_input_order_in_weighted_mode():
    """w_c=w_r=0 时加权分退化为相似度 ⇒ 稳定排序保证与距离序逐字一致。"""
    rows = [
        _resp(1, similarity=0.90, conviction=0.1, age_days=1000),
        _resp(2, similarity=0.85, conviction=1.0),
        _resp(3, similarity=0.80, conviction=1.0),
    ]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "memory_inject_rank", "weighted")
        mp.setattr(settings, "memory_inject_conviction_weight", 0.0)
        mp.setattr(settings, "memory_inject_recency_weight", 0.0)
        assert _ids(order_for_injection(rows, now=NOW)) == ["m1", "m2", "m3"]


# ── weighted 模式的单调性 ─────────────────────────────────────


def test_weighted_rank_prefers_higher_conviction_at_equal_similarity():
    """同相似度、同龄 ⇒ conviction 高者靠前（这正是缺口原句里「0.9 与 0.2 同位次」的反面）。"""
    rows = [
        _resp(1, similarity=0.88, conviction=0.2),
        _resp(2, similarity=0.88, conviction=0.95),
    ]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "memory_inject_rank", "weighted")
        assert _ids(order_for_injection(rows, now=NOW)) == ["m2", "m1"]


def test_weighted_rank_prefers_recent_at_equal_conviction():
    """同相似度、同 conviction ⇒ 新的靠前；半衰期取默认 90 天时 400 天老的那条明显吃亏。"""
    rows = [
        _resp(1, similarity=0.88, conviction=0.7, age_days=400),
        _resp(2, similarity=0.88, conviction=0.7, age_days=1),
    ]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "memory_inject_rank", "weighted")
        assert _ids(order_for_injection(rows, now=NOW)) == ["m2", "m1"]


def test_recency_term_halves_after_one_half_life_and_ignores_naive_times():
    """衰减项的数值口径：满一个半衰期 ⇒ 新近项恰减半；naive created_at 按 UTC 解释不抛错。"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "memory_inject_rank", "weighted")
        mp.setattr(settings, "memory_inject_recency_weight", 1.0)
        mp.setattr(settings, "memory_inject_conviction_weight", 0.0)
        fresh = _inject_rank_score(_resp(1, similarity=0.5, conviction=0.0), NOW)
        half = _inject_rank_score(
            _resp(1, similarity=0.5, conviction=0.0, age_days=settings.memory_inject_decay_half_life_days),
            NOW,
        )
        # similarity 0.5 + 新近项：当天 1.0，满一个半衰期 0.5
        assert fresh == pytest.approx(1.5)
        assert half == pytest.approx(1.0)
        naive = _resp(2, similarity=0.5, conviction=0.0)
        naive.created_at = naive.created_at.replace(tzinfo=None)
        assert _inject_rank_score(naive, NOW) == pytest.approx(fresh)


def test_weighted_ties_keep_input_order_not_id_order():
    """同分必须保持**输入顺序**（输入顺序就是距离序）⇒ 不许换成按 id 之类的确定性平局键。

    这里刻意把 id 大的那条放在输入队列前面：若实现改用 id 打破平局，顺序会反过来。
    """
    rows = [
        _resp(3, similarity=0.88, conviction=0.7),
        _resp(2, similarity=0.88, conviction=0.7),
    ]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "memory_inject_rank", "weighted")
        assert _ids(order_for_injection(rows, now=NOW)) == ["m3", "m2"]


def test_zero_half_life_falls_back_instead_of_crashing():
    """半衰期被 env 配成 0/负数 ⇒ 取下限，不许 ZeroDivisionError 把整条注入链路打死。"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "memory_inject_rank", "weighted")
        mp.setattr(settings, "memory_inject_decay_half_life_days", 0)
        rows = [
            _resp(1, similarity=0.88, conviction=0.7, age_days=500),
            _resp(2, similarity=0.88, conviction=0.7, age_days=0),
        ]
        assert _ids(order_for_injection(rows, now=NOW)) == ["m2", "m1"]


def test_weighted_rank_permutes_but_never_changes_membership():
    """成员集合不变量：重排是置换，不是筛选——长度与 id 集合都必须逐字相同。"""
    rows = [
        _resp(1, similarity=0.90, conviction=0.1, age_days=700),
        _resp(2, similarity=0.89, conviction=1.0, age_days=2),
        _resp(3, similarity=0.80, conviction=0.9, age_days=1),
        _resp(4, similarity=0.79, conviction=0.3),
    ]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "memory_inject_rank", "weighted")
        out = order_for_injection(rows, now=NOW)
    assert sorted(_ids(out)) == sorted(_ids(rows))
    assert len(out) == len(rows)


async def test_weighted_rank_keeps_revert_call_sequence(
    db, make_user, stub_semantic_search, monkeypatch
):
    """副作用不变量：revert 的调用序列在两种模式下必须逐字相同（重排在回归循环之后）。"""
    await make_user()
    calls = []

    async def fake_record_version(_db, user_id, memory_id, transition_type, reason=None, **_kw):
        calls.append((user_id, memory_id, transition_type))

    monkeypatch.setattr(memory_service, "record_version", fake_record_version)
    stub_semantic_search["rows"] = [
        _row(1, similarity=0.87, conviction=0.10, age_days=400),
        _row(2, similarity=0.86, conviction=0.95, status="superseded", superseded_by="m9"),
        _row(3, similarity=0.85, conviction=0.95, status="superseded", superseded_by="m8"),
    ]
    # 两条 superseded 都 > 0.80 ⇒ 各触发一次 revert；m1 距离最近但低信念又老，
    # 正是「weighted 该把它挪后、但不该影响 revert 序列」的那一格。
    monkeypatch.setattr(settings, "memory_inject_rank", "distance")
    out_distance = await memory_service.get_memory_context(
        db, "u1", "我现在的想法", limit=3, include_superseded=True
    )
    seq_distance = list(calls)
    assert [c[1] for c in seq_distance] == ["m9", "m8"]
    assert _ids(out_distance) == ["m1", "m2", "m3"]

    calls.clear()
    monkeypatch.setattr(settings, "memory_inject_rank", "weighted")
    out_weighted = await memory_service.get_memory_context(
        db, "u1", "我现在的想法", limit=3, include_superseded=True
    )
    assert calls == seq_distance, "加权模式不得改变 revert 的调用序列"
    # 位次确实变了（m2/m3 的高 conviction + 新近把 m1 的低 conviction 挤下去）
    assert _ids(out_weighted) != _ids(out_distance)
    assert sorted(_ids(out_weighted)) == sorted(_ids(out_distance))
    # 回归标注跟着各自的行走，不因重排而错位
    assert {r.id: r.regressed for r in out_weighted} == {
        r.id: r.regressed for r in out_distance
    }


# ── 边界：没接的地方必须没接 ──────────────────────────────────


def test_semantic_search_sql_order_by_is_still_pure_distance():
    """SQL 侧源码闸：加权未标定前不许进 ORDER BY，谁改进 SQL 谁先红。"""
    start = REPO_SRC.index("async def semantic_search")
    body = REPO_SRC[start : REPO_SRC.index("async def version_out")]
    assert "ORDER BY embedding <=> CAST(:q AS halfvec)" in body
    for banned in ("conviction", "created_at", "exp(", "half_life", "memory_inject"):
        assert banned not in body, f"检索 SQL 里出现了未标定的排序项：{banned}"


def test_public_search_path_is_not_reordered():
    """响应字节面闸：`service.semantic_search` 不接重排，只有注入路径接。"""
    search = MS_SRC[
        MS_SRC.index("    async def semantic_search(") : MS_SRC.index("    async def get_memory_context(")
    ]
    assert "order_for_injection" not in search
    inject = MS_SRC[
        MS_SRC.index("    async def get_memory_context(") : MS_SRC.index("    async def record_version(")
    ]
    assert "order_for_injection(responses)" in inject
