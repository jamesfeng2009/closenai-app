"""注销账号：软删除 + QStash 异步物理删除 + orphan 防护回归测试。

覆盖：
- soft_delete_account：status=deleted + 吊销全部 refresh + 清 push token
- get_user / get_by_email / get_by_apple_id / list_all：过滤 deleted 用户
- get_or_create_user：deleted 用户抛 USER_NOT_FOUND（不建 orphan、不 PK 冲突）
- purge_user：物理删除幂等（二次调用 rowcount 全 0）
- 同标识符注销后可重新注册：逻辑层查重跳过 deleted（partial unique index 行为靠 PG 验证）

注：sqlite 测试库用 Base.metadata.create_all 建表，不执行 alembic 迁移的
partial unique index；故 index 约束本身不在 sqlite 断言范围，靠迁移代码审查 +
生产 PG 验证。本测试覆盖逻辑层（get_by_* 过滤 deleted）行为。
"""
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from backend.core.errors import AppException, ErrorCode
from backend.core.time_utils import utc_now
from backend.models.auth import RefreshToken
from backend.models.push import PushToken
from backend.models.user import User
from backend.repositories import auth_repository, user_repository
from backend.services.user_service import user_service


async def _make_user(db, user_id=None, **kwargs):
    """直接建 active 用户（用 create_user，绕过 get_or_create_user 的 deleted 检测）。"""
    user = await user_repository.create_user(
        db,
        user_id=user_id or str(uuid4()),
        **kwargs,
    )
    await db.flush()
    return user


# ── soft_delete_account ──


async def test_soft_delete_marks_user_deleted(db):
    user = await _make_user(db, email="a@x.com", username="a")
    await user_service.soft_delete_account(db, user.id)
    any_user = await user_repository.get_user_any_status(db, user.id)
    assert any_user is not None
    assert any_user.status == "deleted"
    assert any_user.deleted_at is not None


async def test_soft_delete_revokes_all_refresh_tokens(db):
    user = await _make_user(db)
    expires = utc_now() + timedelta(days=30)
    await auth_repository.create_refresh_token(
        db, user_id=user.id, token_hash="h1", expires_at=expires, token_id="t1"
    )
    await auth_repository.create_refresh_token(
        db, user_id=user.id, token_hash="h2", expires_at=expires, token_id="t2"
    )
    await user_service.soft_delete_account(db, user.id)
    result = await db.execute(select(RefreshToken).where(RefreshToken.user_id == user.id))
    tokens = list(result.scalars().all())
    assert len(tokens) == 2
    assert all(t.revoked_at is not None for t in tokens)


async def test_soft_delete_clears_push_tokens(db):
    user = await _make_user(db)
    db.add(
        PushToken(
            id=str(uuid4()),
            user_id=user.id,
            platform="apns",
            device_token="tok",
            timezone="UTC",
        )
    )
    await db.flush()
    await user_service.soft_delete_account(db, user.id)
    result = await db.execute(select(PushToken).where(PushToken.user_id == user.id))
    assert list(result.scalars().all()) == []


async def test_soft_delete_account_rejects_already_deleted(db):
    """重复注销：第二次 _require_user 抛 USER_NOT_FOUND（get_user 过滤 deleted）。"""
    user = await _make_user(db)
    await user_service.soft_delete_account(db, user.id)
    with pytest.raises(AppException) as exc:
        await user_service.soft_delete_account(db, user.id)
    assert exc.value.code == ErrorCode.USER_NOT_FOUND


# ── get_user / get_by_* 过滤 deleted ──


async def test_get_user_excludes_deleted(db):
    user = await _make_user(db)
    await user_repository.soft_delete(db, user.id)
    assert await user_repository.get_user(db, user.id) is None


async def test_get_user_any_status_includes_deleted(db):
    user = await _make_user(db)
    await user_repository.soft_delete(db, user.id)
    any_user = await user_repository.get_user_any_status(db, user.id)
    assert any_user is not None
    assert any_user.status == "deleted"


async def test_get_by_email_excludes_deleted(db):
    user = await _make_user(db, email="dup@x.com")
    await user_repository.soft_delete(db, user.id)
    assert await user_repository.get_by_email(db, "dup@x.com") is None


async def test_get_by_username_excludes_deleted(db):
    user = await _make_user(db, username="dupuser")
    await user_repository.soft_delete(db, user.id)
    assert await user_repository.get_by_username(db, "dupuser") is None


async def test_get_by_apple_id_excludes_deleted(db):
    user = await _make_user(db, apple_id="apple-123")
    await user_repository.soft_delete(db, user.id)
    assert await user_repository.get_by_apple_id(db, "apple-123") is None


async def test_list_all_excludes_deleted(db):
    u_active = await _make_user(db, "u-active")
    u_deleted = await _make_user(db, "u-deleted")
    await user_repository.soft_delete(db, u_deleted.id)
    users = await user_repository.list_all(db)
    ids = {u.id for u in users}
    assert u_active.id in ids
    assert u_deleted.id not in ids


# ── get_or_create_user deleted 拦截（核心 orphan 防护）──


async def test_get_or_create_user_rejects_deleted(db):
    """deleted 用户 → 抛 USER_NOT_FOUND，不建 orphan、不 PK 冲突。"""
    user = await _make_user(db)
    await user_repository.soft_delete(db, user.id)
    with pytest.raises(AppException) as exc:
        await user_repository.get_or_create_user(db, user.id)
    assert exc.value.code == ErrorCode.USER_NOT_FOUND
    # 确认没有新建额外行（仍只有 1 条 deleted 记录）
    any_user = await user_repository.get_user_any_status(db, user.id)
    assert any_user.status == "deleted"


async def test_get_or_create_user_creates_when_absent(db):
    """完全不存在 → 正常建用户（dev 兼容路径未被破坏）。"""
    user = await user_repository.get_or_create_user(db, "u-new")
    assert user.id == "u-new"
    assert user.status == "active"


# ── purge_user 幂等 ──


async def test_purge_user_idempotent(db):
    user = await _make_user(db)
    # 第一次物理删除：users 行 rowcount=1
    counts1 = await user_service.purge_user(db, user.id)
    assert counts1["users"] == 1
    # 第二次：行已删，rowcount 全 0
    counts2 = await user_service.purge_user(db, user.id)
    assert all(v == 0 for v in counts2.values())
    assert await user_repository.get_user_any_status(db, user.id) is None


async def test_purge_user_deletes_related_data(db):
    user = await _make_user(db)
    expires = utc_now() + timedelta(days=30)
    await auth_repository.create_refresh_token(
        db, user_id=user.id, token_hash="h1", expires_at=expires, token_id="t1"
    )
    db.add(
        PushToken(
            id=str(uuid4()),
            user_id=user.id,
            platform="apns",
            device_token="tok",
            timezone="UTC",
        )
    )
    await db.flush()
    counts = await user_service.purge_user(db, user.id)
    assert counts["refresh_tokens"] == 1
    assert counts["push_tokens"] == 1
    assert counts["users"] == 1


# ── 同标识符注销后可重新注册（逻辑层查重跳过 deleted）──


async def test_reregister_same_email_after_delete(db):
    """同 email 注销后：get_by_email 查不到 deleted → create_user 可建新用户（新 id）。"""
    old = await _make_user(db, email="reuse@x.com", username="old")
    await user_repository.soft_delete(db, old.id)
    # 注册查重：get_by_email 过滤 deleted → None（不报 EMAIL_ALREADY_USED）
    assert await user_repository.get_by_email(db, "reuse@x.com") is None
    # 建新用户（新 user_id，同 email）
    new = await user_repository.create_user(
        db, user_id=str(uuid4()), email="reuse@x.com", username="new"
    )
    await db.flush()
    assert new.id != old.id
    assert new.email == "reuse@x.com"
    assert new.status == "active"
    # 旧 deleted 记录仍在（待 QStash 物理删除）
    old_any = await user_repository.get_user_any_status(db, old.id)
    assert old_any.status == "deleted"


async def test_reregister_same_apple_id_after_delete(db):
    """同 apple_id 注销后：get_by_apple_id 查不到 deleted → 可建新账号。"""
    old = await _make_user(db, apple_id="apple-reuse")
    await user_repository.soft_delete(db, old.id)
    assert await user_repository.get_by_apple_id(db, "apple-reuse") is None
    new = await user_repository.create_user(
        db, user_id=str(uuid4()), apple_id="apple-reuse"
    )
    await db.flush()
    assert new.id != old.id


# ── require_user（C 方案：业务路径统一入口）──


async def test_require_user_returns_active(db):
    user = await _make_user(db)
    result = await user_repository.require_user(db, user.id)
    assert result.id == user.id


async def test_require_user_rejects_deleted(db):
    user = await _make_user(db)
    await user_repository.soft_delete(db, user.id)
    with pytest.raises(AppException) as exc:
        await user_repository.require_user(db, user.id)
    assert exc.value.code == ErrorCode.USER_NOT_FOUND


async def test_require_user_rejects_absent(db):
    with pytest.raises(AppException) as exc:
        await user_repository.require_user(db, "nonexistent-id")
    assert exc.value.code == ErrorCode.USER_NOT_FOUND


async def test_require_user_supports_for_update(db):
    """for_update=True 不破坏行锁路径（SQLite 忽略该子句，不应报错）。"""
    user = await _make_user(db)
    result = await user_repository.require_user(db, user.id, for_update=True)
    assert result.id == user.id


# ── list_deleted_stale（P1 cron 兜底扫描）──


async def _set_deleted_at(db, user_id, hours_ago):
    """手动回拨 deleted_at（soft_delete 默认 now，测试需构造超时场景）。"""
    stale_time = utc_now() - timedelta(hours=hours_ago)
    await db.execute(
        update(User).where(User.id == user_id).values(deleted_at=stale_time)
    )
    await db.flush()


async def test_list_deleted_stale_filters_by_hours(db):
    u_stale = await _make_user(db)
    u_fresh = await _make_user(db)
    await user_repository.soft_delete(db, u_stale.id)
    await user_repository.soft_delete(db, u_fresh.id)
    await _set_deleted_at(db, u_stale.id, hours_ago=2)  # 超 1h
    # u_fresh 刚软删除（deleted_at ≈ now），不超 1h

    stale_users = await user_repository.list_deleted_stale(db, stale_hours=1)
    stale_ids = {u.id for u in stale_users}
    assert u_stale.id in stale_ids
    assert u_fresh.id not in stale_ids


async def test_list_deleted_stale_excludes_active(db):
    """active 用户不应被兜底扫描命中。"""
    u_active = await _make_user(db)
    stale_users = await user_repository.list_deleted_stale(db, stale_hours=0)
    assert u_active.id not in {u.id for u in stale_users}


# ── purge_deleted_users（P1 cron 批量物理删除）──


async def test_purge_deleted_users_batch(db):
    u1 = await _make_user(db)
    u2 = await _make_user(db)
    u_active = await _make_user(db)  # active，不应被删
    await user_repository.soft_delete(db, u1.id)
    await user_repository.soft_delete(db, u2.id)
    await _set_deleted_at(db, u1.id, hours_ago=2)
    await _set_deleted_at(db, u2.id, hours_ago=2)

    results = await user_service.purge_deleted_users(db, stale_hours=1)
    purged_ids = {r["user_id"] for r in results}
    assert u1.id in purged_ids
    assert u2.id in purged_ids
    assert u_active.id not in purged_ids
    # 物理删除确认
    assert await user_repository.get_user_any_status(db, u1.id) is None
    assert await user_repository.get_user_any_status(db, u2.id) is None
    assert await user_repository.get_user(db, u_active.id) is not None


async def test_purge_deleted_users_skips_fresh(db):
    """刚软删除（未超时）的用户不被兜底清理（给 QStash 重试留窗口）。"""
    u_fresh = await _make_user(db)
    await user_repository.soft_delete(db, u_fresh.id)  # deleted_at ≈ now
    results = await user_service.purge_deleted_users(db, stale_hours=1)
    assert u_fresh.id not in {r["user_id"] for r in results}
    # 记录仍在（待 QStash 处理）
    assert await user_repository.get_user_any_status(db, u_fresh.id) is not None
