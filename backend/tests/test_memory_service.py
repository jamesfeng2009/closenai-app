"""memory_service：重复表达加固、三态版本化、软删除恢复、IDOR。"""
import pytest

from backend.repositories import memory_repository
from backend.services.memory_service import memory_service


def _row_from_memory(m, similarity: float) -> dict:
    """由 ORM 对象构造 semantic_search 候选行（mappings 同构 dict）。"""
    return {
        "id": m.id,
        "user_id": m.user_id,
        "content": m.content,
        "category": m.category,
        "status": m.status,
        "source": m.source,
        "conviction_score": m.conviction_score,
        "created_at": m.created_at,
        "superseded_at": m.superseded_at,
        "transition_reason": m.transition_reason,
        "superseded_by": m.superseded_by,
        "emotion_context": m.emotion_context,
        "similarity": similarity,
    }


async def test_duplicate_expression_reinforces_without_new_memory(
    db, make_user, stub_semantic_search
):
    """相似度 ≥0.85 的重复表达：不新建记忆，仅加固旧记忆 conviction（+0.05，封顶 1.0）。"""
    await make_user("u1")
    first = await memory_service.create_memory(
        db, "u1", content="我喜欢独处", category="preference", source="self", conviction=0.5
    )
    orm_first = await memory_repository.get(db, first.id, "u1")
    stub_semantic_search["rows"] = [_row_from_memory(orm_first, 0.92)]

    again = await memory_service.create_memory(
        db, "u1", content="独处让我最放松", category="preference", source="self", conviction=0.5
    )
    assert again.id == first.id
    assert again.conviction == pytest.approx(0.55)
    # 未新建行
    memories = await memory_repository.list(db, "u1")
    assert len(memories) == 1
    assert memories[0].conviction_score == pytest.approx(0.55)


async def test_record_version_gradual(db, make_user, stub_semantic_search):
    """gradual：旧记忆 superseded、新记忆 current、conviction +0.1、内容更新并重新向量化。"""
    await make_user("u1")
    old = await memory_service.create_memory(
        db, "u1", content="我认为努力工作最重要", conviction=0.5
    )
    await memory_service.record_version(
        db, "u1", old.id, "gradual", reason="drift", new_content="我认为工作生活平衡更重要"
    )
    old_after = await memory_repository.get(db, old.id, "u1")
    assert old_after.status == "superseded"
    assert old_after.transition_reason == "drift"
    current = await memory_repository.list(db, "u1")
    assert len(current) == 1
    assert current[0].content == "我认为工作生活平衡更重要"
    assert current[0].conviction_score == pytest.approx(0.6)
    assert old_after.superseded_by == current[0].id


async def test_record_version_sudden_keeps_conviction(db, make_user, stub_semantic_search):
    """sudden：旧记忆 superseded、新记忆 current、conviction 不变。"""
    await make_user("u1")
    old = await memory_service.create_memory(
        db, "u1", content="我不喜欢社交", conviction=0.7
    )
    await memory_service.record_version(
        db, "u1", old.id, "sudden", reason="belief_change", new_content="我开始享受社交"
    )
    old_after = await memory_repository.get(db, old.id, "u1")
    assert old_after.status == "superseded"
    current = await memory_repository.list(db, "u1")
    assert len(current) == 1
    assert current[0].content == "我开始享受社交"
    assert current[0].conviction_score == pytest.approx(0.7)


async def test_revert_restores_superseded_memory(db, make_user, stub_semantic_search):
    """revert：当前记忆标记 reverted，被它取代的旧记忆恢复 current（reverted_from 溯源）。"""
    await make_user("u1")
    old = await memory_service.create_memory(db, "u1", content="旧信念", conviction=0.5)
    await memory_service.record_version(
        db, "u1", old.id, "sudden", reason="belief_change", new_content="新信念"
    )
    new_id = (await memory_repository.list(db, "u1"))[0].id

    await memory_service.record_version(db, "u1", new_id, "revert", reason=None)

    new_after = await memory_repository.get(db, new_id, "u1")
    assert new_after.status == "reverted"
    old_after = await memory_repository.get(db, old.id, "u1")
    assert old_after.status == "current"
    assert old_after.reverted_from == new_id
    assert old_after.superseded_by is None
    # 列表只剩恢复后的旧信念（reverted 不可见）
    current = await memory_repository.list(db, "u1")
    assert [m.id for m in current] == [old.id]


async def test_soft_delete_hides_and_restores_superseded(db, make_user, stub_semantic_search):
    """软删除 current 记忆：列表不可见；被它 supersede 的旧记忆自动恢复 current。"""
    await make_user("u1")
    old = await memory_service.create_memory(db, "u1", content="旧信念", conviction=0.5)
    await memory_service.record_version(
        db, "u1", old.id, "sudden", reason="belief_change", new_content="新信念"
    )
    new_id = (await memory_repository.list(db, "u1"))[0].id

    deleted = await memory_repository.delete(db, new_id, "u1")
    assert deleted is True

    visible = await memory_service.list_memories(db, "u1")
    visible_ids = [m.id for m in visible]
    assert new_id not in visible_ids
    assert old.id in visible_ids
    assert (await memory_repository.get(db, old.id, "u1")).status == "current"
    # 软删除行自身不可读
    assert await memory_repository.get(db, new_id, "u1") is None


async def test_get_memory_idor_returns_none(db, make_user, stub_semantic_search):
    """IDOR：用他人 user_id 读取记忆返回 None（强制归属校验）。"""
    await make_user("u1")
    memory = await memory_service.create_memory(db, "u1", content="私密记忆")
    assert await memory_service.get_memory(db, memory.id, "other-user") is None
    assert await memory_service.get_memory(db, memory.id, "u1") is not None


async def test_pending_mark_cleared_on_calm_day_reaffirm(db, make_user, stub_semantic_search):
    """B13 回归：冷静日重申同一信念（≥0.85）→ 解除 pending_high_emotion 悬置标记。"""
    from backend.services.memory_service import EMOTION_CONTEXT_PENDING

    await make_user("u1")
    first = await memory_service.create_memory(
        db, "u1", content="我喜欢独处", category="preference", source="self", conviction=0.5
    )
    # 模拟：此前高情绪日检测到矛盾，旧信念被悬置标记
    await memory_repository.update_emotion_context(db, first.id, "u1", EMOTION_CONTEXT_PENDING)
    orm_first = await memory_repository.get(db, first.id, "u1")
    stub_semantic_search["rows"] = [_row_from_memory(orm_first, 0.92)]

    await memory_service.create_memory(
        db, "u1", content="独处让我最放松", category="preference", source="self", conviction=0.5
    )
    after = await memory_repository.get(db, first.id, "u1")
    assert after.emotion_context is None  # 标记已消解
    assert after.status == "current"  # 信念重申成功，不被取代


async def test_pending_mark_kept_on_high_negative_day(
    db, make_user, stub_semantic_search, monkeypatch
):
    """B13 回归：高情绪日重申仅加固 conviction，不消解 pending 标记（情绪过滤仍生效）。"""
    from backend.services.memory_service import EMOTION_CONTEXT_PENDING

    await make_user("u1")
    first = await memory_service.create_memory(
        db, "u1", content="我喜欢独处", category="preference", source="self", conviction=0.5
    )
    await memory_repository.update_emotion_context(db, first.id, "u1", EMOTION_CONTEXT_PENDING)
    orm_first = await memory_repository.get(db, first.id, "u1")
    stub_semantic_search["rows"] = [_row_from_memory(orm_first, 0.92)]

    async def _high_negative(db, user_id):
        return True

    monkeypatch.setattr(memory_service, "_is_high_negative_today", _high_negative)
    await memory_service.create_memory(
        db, "u1", content="独处让我最放松", category="preference", source="self", conviction=0.5
    )
    after = await memory_repository.get(db, first.id, "u1")
    assert after.emotion_context == EMOTION_CONTEXT_PENDING
