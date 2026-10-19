"""注销账号：软删除 + QStash 异步物理删除 + orphan 防护回归测试。

覆盖：
- soft_delete_account：status=deleted + 吊销全部 refresh + 清 push token
- get_user / get_by_email / get_by_apple_id / list_all：过滤 deleted 用户
- get_or_create_user：deleted 用户抛 USER_NOT_FOUND（不建 orphan、不 PK 冲突）
- purge_user：物理删除幂等（二次调用 rowcount 全 0）
- T1-02 对账闸：Base.metadata 每张带 user_id 的表 ∈ PURGE_TARGETS ∪ PURGE_EXEMPT_TABLES，
  且豁免逐条带理由；豁免表的行为证人＝purge_user 刚写的重试行不会被自己删掉
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
from backend.models.chat import ChatMessage
from backend.models.generation_run import AiGenerationRun
from backend.models.memory import Memory
from backend.models.push import PushToken
from backend.models.user import User
from backend.repositories import auth_repository, erasure_repository, user_repository
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


def test_purge_covers_every_user_scoped_table():
    """闸门：Base.metadata 里每张带 user_id 的表都必须在 PURGE_TARGETS 或显式豁免表里。

    建表时漏登记 = 注销后残留用户数据（2026-10-13 实测 O4 的 ai_generation_runs 就漏过一次），
    所以按元数据逐表对账，而不是靠人记得改 purge 循环。

    T1-02 加的第二段判据跟第一段一样重要：豁免表 `PURGE_EXEMPT_TABLES` 是这句话的反面，
    所以它自己也要被核——每一格必须是真实存在、确实带 user_id、且没同时出现在删除清单里，
    理由不许留空。否则「加一张漏登记的表」只要顺手写个空理由就能过闸，这条闸就白立了。
    """
    import backend.models  # noqa: F401  触发全部 ORM 模型注册进 Base.metadata
    from backend.database import Base
    from backend.services.user_service import PURGE_EXEMPT_TABLES, PURGE_TARGETS

    tables = Base.metadata.tables
    user_scoped = {t.name for t in tables.values() if "user_id" in t.columns}
    covered = {key for _, key in PURGE_TARGETS} - {"users"}
    assert user_scoped == covered | set(PURGE_EXEMPT_TABLES), (
        f"仅表有={sorted(user_scoped - covered - set(PURGE_EXEMPT_TABLES))} "
        f"仅清单有={sorted((covered | set(PURGE_EXEMPT_TABLES)) - user_scoped)}"
    )
    # 豁免的每一格都得是「真的带 user_id 的表 + 一个非空理由」
    for name, reason in PURGE_EXEMPT_TABLES.items():
        assert name in user_scoped, f"豁免表 {name} 根本不带 user_id，豁免无意义（该删掉这格）"
        assert name not in covered, f"{name} 既在删除清单又在豁免表：两条判据互相抵消"
        assert isinstance(reason, str) and len(reason.strip()) >= 20, (
            f"豁免表 {name} 的理由太短或为空——豁免必须说清为什么允许残留"
        )
    # 正向证人：删除清单不是空集，且点名两张必须删的表（防「清单被清空后本闸恒绿」）
    assert {"chat_messages", "users"} <= {key for _, key in PURGE_TARGETS}
    assert len(PURGE_TARGETS) >= 40, f"删除清单只剩 {len(PURGE_TARGETS)} 项，不像真的"


async def test_purge_user_does_not_delete_its_own_erasure_retry_row(db, monkeypatch):
    """豁免不是纸面声明：purge_user 跑完后，它刚写的重试行必须还在。

    这条是 PURGE_EXEMPT_TABLES 唯一的行为证人——顺序侥幸（循环在前、写行在后）在
    「先写行后删循环」这种重构下会静默变成残留丢失，所以按真实调用链断言而不是只看清单。
    """

    async def failing_erase(distinct_id: str) -> dict:
        return {"erased": False, "reason": "http_500"}

    monkeypatch.setattr("backend.services.user_service.erase_person_data", failing_erase)
    user = await _make_user(db)
    counts = await user_service.purge_user(db, user.id)

    assert counts["users"] == 1
    assert await user_repository.get_user_any_status(db, user.id) is None
    assert await erasure_repository.pending_user_ids(db) == [user.id]


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
    # O4 生成留痕表同为 user 维度数据：注销必须一起删干净（无残留）
    db.add(
        AiGenerationRun(
            id=str(uuid4()), user_id=user.id, trace_id="t", op="complete", purpose="x"
        )
    )
    await db.flush()
    counts = await user_service.purge_user(db, user.id)
    assert counts["refresh_tokens"] == 1
    assert counts["push_tokens"] == 1
    assert counts["ai_generation_runs"] == 1
    assert counts["users"] == 1
    left = await db.execute(select(AiGenerationRun).where(AiGenerationRun.user_id == user.id))
    assert left.scalars().first() is None


# ── purge 举证日志（J4：不建墓碑表，删除证明走结构化日志）──
#
# 为什么不建墓碑表：重新注册拿到的是全新 user_id，与老 id 之间没有任何合法关联键，
# 一张只有孤立 id 的表没有消费方，却永久留着「谁注销了」这份数据。
# 因此举证口径 = 一行 INFO：user_id + deleted_at + 各表 rowcount + PostHog 结果。


def _purge_logs(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "closenai.user" and "purge 举证" in r.getMessage()
    ]


async def test_purge_user_logs_deletion_proof(db, caplog):
    """删完必须能事后回查：谁、什么时候注销、各表删了几行。"""
    user = await _make_user(db)
    await user_repository.soft_delete(db, user.id)
    deleted_at = (
        await db.execute(select(User.deleted_at).where(User.id == user.id))
    ).scalar_one()

    caplog.clear()
    caplog.set_level("INFO")
    await user_service.purge_user(db, user.id)

    lines = _purge_logs(caplog)
    assert len(lines) == 1
    line = lines[0]
    assert f"user_id={user.id}" in line
    # 时间戳必须在 DELETE 之前取：这是唯一能证明「注销时刻」的字段
    assert f"deleted_at={deleted_at.isoformat()}" in line
    assert "counts=" in line
    assert "users=1" in line
    assert "push_tokens=0" in line


async def test_purge_proof_log_carries_no_user_content(db, caplog):
    """举证日志只准有元数据：对话原文与记忆原文都不得出现在日志里。"""
    secret = "我最害怕的是妈妈知道我有多脆弱"
    user = await _make_user(db)
    db.add(
        ChatMessage(
            id=str(uuid4()), user_id=user.id, message_type="reply",
            role="user", content=secret,
        )
    )
    db.add(Memory(id=str(uuid4()), user_id=user.id, content=secret))
    await db.flush()

    caplog.clear()
    caplog.set_level("INFO")
    counts = await user_service.purge_user(db, user.id)

    assert counts["chat_messages"] == 1
    assert counts["memories"] == 1
    joined = "\n".join(_purge_logs(caplog) + [r.getMessage() for r in caplog.records])
    assert secret not in joined
    # 数字要能对上，否则「删干净」只是自说自话
    assert f"total_rows={sum(counts.values())}" in joined


async def test_purge_proof_log_reports_pending_posthog(db, monkeypatch, caplog):
    """PostHog 未配置 key 时本地照删，但举证行要写明还没抹除（不能显得已删干净）。"""
    async def pending_erase(distinct_id: str) -> dict:
        return {"erased": False, "reason": "not_configured"}

    monkeypatch.setattr("backend.services.user_service.erase_person_data", pending_erase)
    user = await _make_user(db)
    caplog.clear()
    caplog.set_level("INFO")
    await user_service.purge_user(db, user.id)

    assert "posthog=pending:not_configured" in _purge_logs(caplog)[0]


async def test_purge_proof_log_emitted_on_idempotent_retry(db, caplog):
    """QStash 重试第二次也出举证行且 total_rows=0 —— 「确实没有残留」同样需要证据。"""
    user = await _make_user(db)
    await user_service.purge_user(db, user.id)
    caplog.clear()
    caplog.set_level("INFO")
    await user_service.purge_user(db, user.id)

    assert "total_rows=0" in _purge_logs(caplog)[0]


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
