# 注销账号：软删除 + QStash 异步物理删除 + orphan 用户防护

## Context

当前注销实现（`user_service.delete_account`）在一个同步事务里物理删除 13 张表，存在两个问题：

1. **同步删除慢**：活跃用户对话/记忆上千条，删 13 表 2-5 秒，Vercel Serverless 10s 超时有风险，用户点注销要等数秒才退出。
2. **30 分钟 access_token 窗口期重建 orphan 用户**：access_token 无状态，注销后 30 分钟仍能通过 `get_current_user` 验签；业务路径 `get_or_create_user`（28 处调用点）会因用户不存在而**新建空 orphan 用户**，违背"注销后数据全部删除"。

**目标**：注销秒级返回 + 立即失效（软删除）+ 异步物理清理（QStash）+ 窗口期内 deleted 用户被拦截不建 orphan。

**方案**：B（软删除 + QStash 异步物理删除）先行，C（28 处 `get_or_create_user → get_user` 治本迁移）作为收尾逐步做。B 单独可用：通过改造 `get_or_create_user` 检测 deleted 状态，让 28 处调用点在窗口期内安全抛 `USER_NOT_FOUND`，不建 orphan、不 PK 冲突。

## 方案总览

```
用户点注销
  ↓
DELETE /user/account（同步事务，毫秒级）
  1. users.status = 'deleted', users.deleted_at = now
  2. auth_repository.revoke_all_for_user（吊销全部 refresh，防刷新 access）
  3. clear_push_tokens（防推送打扰）
  ↓
立即 200 返回 → 前端 resetSessionFull 回引导页
  ↓
QStash publish → POST /cron/purge-user（verify_qstash_signature 验签）
  → user_service.purge_user 物理删 13 表（幂等）
  → 失败 QStash 自动重试
```

**窗口期行为**（30 分钟内 access_token 仍有效）：
- 业务请求 → `get_current_user` 验签通过 → service 调 `get_or_create_user` 或 `get_user`
- `get_user` 加 `status='active'` 过滤 → deleted 用户查不到
- `get_or_create_user` 改造：查不到时先查含 deleted 的记录，命中 deleted → 抛 `USER_NOT_FOUND`；完全不存在 → 原建用户逻辑（dev 兼容）
- 前端拦截 `USER_NOT_FOUND`(40401) → `clearAuth + notifySessionReset` 登出

## 改动清单

### 1. DB 迁移（新文件 `alembic/versions/xxxx_account_soft_delete.py`，down_revision = `d1e3f5a7b9c2`）

```python
def upgrade():
    # 加字段
    op.add_column('users', sa.Column('status', sa.String(20), server_default='active', nullable=False))
    op.add_column('users', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    # 重建 3 个 partial unique index（加 deleted_at IS NULL 条件，软删除记录不占唯一约束位）
    op.execute("DROP INDEX IF EXISTS ix_users_email_unique")
    op.execute("CREATE UNIQUE INDEX ix_users_email_unique ON users (email) WHERE email IS NOT NULL AND deleted_at IS NULL")
    op.execute("DROP INDEX IF EXISTS ix_users_username_unique")
    op.execute("CREATE UNIQUE INDEX ix_users_username_unique ON users (username) WHERE username IS NOT NULL AND deleted_at IS NULL")
    op.execute("DROP INDEX IF EXISTS ix_users_apple_id_unique")
    op.execute("CREATE UNIQUE INDEX ix_users_apple_id_unique ON users (apple_id) WHERE apple_id IS NOT NULL AND deleted_at IS NULL")
    # cron 兜底扫描索引（只索引 deleted 行，省空间）
    op.execute("CREATE INDEX ix_users_deleted ON users (deleted_at) WHERE deleted_at IS NOT NULL")
```

**SQLite 测试库兼容**：partial index + `server_default` 在 SQLite 3.8+ 均支持，无需特殊处理。

### 2. `backend/models/user.py` — 加字段

```python
status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

### 3. `backend/repositories/user_repository.py` — 查询过滤 + soft_delete

- **`get_user`**：加 `WHERE User.status == 'active'` 过滤（deleted 查不到）
- **新增 `get_user_any_status(db, user_id, for_update=False)`**：不过滤 status，供 `get_or_create_user` 检测 deleted + `purge_user` 用
- **`get_or_create_user` 改造**：
  ```python
  user = await self.get_user(db, user_id, for_update=for_update)  # active 过滤
  if user:
      return user
  # 查不到：区分"已注销"vs"完全不存在"
  any_user = await self.get_user_any_status(db, user_id)
  if any_user and any_user.status == 'deleted':
      raise AppException(ErrorCode.USER_NOT_FOUND, "账号已注销 / Account deleted")
  # 完全不存在：原建用户逻辑（dev 匿名兼容；生产不应走到这，因 get_current_user 已校验）
  ... 原建用户逻辑
  ```
- **`get_by_email` / `get_by_username` / `get_by_apple_id`**：加 `WHERE status == 'active'`（注册查重天然跳过 deleted 记录，同标识符可重新注册）
- **`list_all`**：加 `WHERE status == 'active'`（cron 不再处理 deleted 用户）
- **新增 `soft_delete(db, user_id)`**：`UPDATE users SET status='deleted', deleted_at=now WHERE id=? AND status='active'`，返回 rowcount（0 表示已注销或不存在，幂等）

### 4. `backend/services/user_service.py` — soft_delete + purge

- **`delete_account` 改名 `soft_delete_account`**：
  ```python
  async def soft_delete_account(self, db, user_id) -> dict:
      await self._require_user(db, user_id)  # 确认存在且 active
      await user_repository.soft_delete(db, user_id)
      await auth_repository.revoke_all_for_user(db, user_id)  # 吊销全部 refresh
      await self.clear_push_tokens(db, user_id)  # 清推送 token
      return {"deleted": True}
  ```
- **保留 `purge_user` 方法**（原 `delete_account` 的物理删除逻辑，13 表级联删，幂等：rowcount 0 跳过）：
  ```python
  async def purge_user(self, db, user_id) -> dict:
      # 物理删 13 表（原 delete_account 逻辑，按 user_id 删，已删则 0 rowcount）
      ... 原逻辑
  ```

### 5. `backend/services/auth_service.py` — QStash 投递

新增 `delete_account` 编排方法（或放 user_service，但 QStash 投递属认证侧职责，放 auth_service 更内聚）：
```python
async def delete_account(self, db, user_id) -> dict:
    result = await user_service.soft_delete_account(db, user_id)
    # QStash 投递异步物理删除（仿 cron_service._publish_user_task 模式）
    await self._publish_purge_task(user_id)
    return result

async def _publish_purge_task(self, user_id: str) -> None:
    """投递物理删除任务到 QStash（失败不阻断注销，记日志；cron 兜底清理）。"""
    if not (settings.qstash_token and settings.backend_base_url):
        logger.warning("QStash 未配置，purge 任务未投递 user=%s（依赖 cron 兜底）", user_id)
        return
    base = (settings.qstash_url or "https://qstash.upstash.io").rstrip("/")
    callback = f"{settings.backend_base_url.rstrip('/')}/api/v1/cron/purge-user"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{base}/v2/publish/{callback}",
                json={"user_id": user_id},
                headers={"Authorization": f"Bearer {settings.qstash_token}", "Content-Type": "application/json"},
            )
        if not (200 <= resp.status_code < 300):
            logger.warning("QStash purge publish 失败 user=%s status=%s", user_id, resp.status_code)
    except Exception as exc:
        logger.warning("QStash purge publish 异常 user=%s: %s", user_id, exc)
```
- **QStash 未配置兜底**：开发环境无 QStash，purge 不投递；后续可加 `/cron/purge-deleted-users` 定时扫描 `deleted_at` 超过 1 小时的记录物理删除（P1，本 plan 不实现，SKILL 记为待办）

### 6. `backend/routers/user.py` — 注销端点

`DELETE /user/account` 改调 `auth_service.delete_account`（编排 soft_delete + QStash）。或保持调 `user_service.soft_delete_account`，QStash 投递放 router 层。**推荐前者**（编排内聚在 service）。

### 7. `backend/routers/cron.py` — 新增 purge-user 端点

```python
@router.post("/cron/purge-user")
async def purge_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: UserService = Depends(get_user_service),
):
    """QStash 回调：物理删除已软删除用户的全量数据（幂等）。"""
    body = await verify_qstash_signature(request)
    payload = json.loads(body)
    user_id = payload.get("user_id")
    if not user_id:
        raise AppException(ErrorCode.BAD_REQUEST, "user_id required")
    counts = await service.purge_user(db, user_id)
    return success({"user_id": user_id, "counts": counts})
```

### 8. `frontend/src/services/api.ts` — USER_NOT_FOUND 拦截登出

`request` 函数在解析 body 后，增加 USER_NOT_FOUND 拦截（已登录态下用户不存在 = 已注销）：
```typescript
if (isEnvelope(body) && body.code === 40401 && accessToken) {
  await clearAuth();
  notifySessionReset();
  throw new ApiError(40401, i18n.t('errors.sessionExpired'));
}
```
位置：在 `if (!res.ok)` 分支之前或之内，确保 404 响应也能触发。`deleteAccount()` 函数本身不变（接口路径不变）。

### 9. `.trae/skills/closenai-auth/SKILL.md` 更新

- "安全约束"段：注销条目从"级联删除"改为"软删除即时失效 + QStash 异步物理删除 + partial unique index 加 `deleted_at IS NULL` 允许同标识符重新注册"
- 新增"注销与 orphan 防护"小节：说明窗口期行为、`get_or_create_user` deleted 检测、前端 USER_NOT_FOUND 拦截
- "业务路径适配"段：标注 C 方案（28 处 `get_or_create_user → get_user`）为"进行中，B 方案已让 deleted 用户安全抛错，C 为语义清理"
- 数据模型段：User 加 `status` / `deleted_at` 字段说明

## 关键复用点

| 现成实现 | 位置 | 复用场景 |
|---------|------|---------|
| `auth_repository.revoke_all_for_user` | [auth_repository.py:68](file:///Users/fengyu/Downloads/myproject/workspace/closenai-app/backend/repositories/auth_repository.py#L68) | soft_delete 吊销全部 refresh |
| `_publish_user_task` QStash 投递模式 | [cron_service.py:259](file:///Users/fengyu/Downloads/myproject/workspace/closenai-app/backend/services/cron_service.py#L259) | `_publish_purge_task` 仿写 |
| `verify_qstash_signature` 验签 | [cron.py:46](file:///Users/fengyu/Downloads/myproject/workspace/closenai-app/backend/routers/cron.py#L46) | `/cron/purge-user` 端点鉴权 |
| `clear_push_tokens` | [user_service.py:268](file:///Users/fengyu/Downloads/myproject/workspace/closenai-app/backend/services/user_service.py#L268) | soft_delete 清推送 |
| `delete_account` 物理删 13 表逻辑 | [user_service.py:273](file:///Users/fengyu/Downloads/myproject/workspace/closenai-app/backend/services/user_service.py#L273) | 改名 `purge_user`，逻辑不变 |
| 前端 `clearAuth + notifySessionReset` | [api.ts:108](file:///Users/fengyu/Downloads/myproject/workspace/closenai-app/frontend/src/services/api.ts#L108) | USER_NOT_FOUND 拦截登出 |

## 竞态安全

| 时序 | 行为 |
|---|---|
| 注销 → QStash 跑完 → 再注册同 Apple | 旧记录已物理删，全新用户，partial index 不冲突 |
| 注销 → QStash 未跑 → 立即再注册同 Apple | 旧记录 status=deleted（不占唯一约束位），新记录 user_id=B 正常创建；QStash 按 user_id=A 删旧，不影响 B |
| 注销 → 30min 内 access_token 请求 | get_user/get_or_create_user 检测 deleted → USER_NOT_FOUND → 前端登出 |

## 验证

### 后端
```bash
cd backend
.venv/bin/python -m pytest backend/tests/ -q          # 现有测试不回归
.venv/bin/alembic heads                                # 确认单头（新迁移）
.venv/bin/alembic upgrade head                         # 迁移可应用
.venv/bin/python -c "from backend.services.user_service import UserService; from backend.services.auth_service import AuthService"  # import 冒烟
```

### 新增测试（`backend/tests/test_account_deletion.py`）
- `soft_delete_account`：注销后 status=deleted、refresh 全吊销、push_token 清空
- `get_user` 过滤：deleted 用户查不到
- `get_or_create_user` deleted 拦截：deleted 用户 → 抛 USER_NOT_FOUND，不建 orphan、不 PK 冲突
- `purge_user` 幂等：二次调用 rowcount 全 0
- 注册查重：同 email 注销后可重新注册（partial index 不冲突）
- `get_by_email/apple_id/username` 过滤 deleted

### 前端
```bash
cd frontend && npm run tsc        # exit 0
```

### 端到端（手动）
1. 登录 → 注销 → 应秒级返回引导页（不等待）
2. 注销后立即用旧 access_token 请求 `/user/profile` → 404 USER_NOT_FOUND → 前端自动登出
3. 注销后用同 Apple 账号重新登录 → 成功建新用户
4. QStash 配置环境下，等几秒后查 DB 确认旧用户数据物理删除

## 待办（C 方案，本 plan 不实现，SKILL 记录）

- 28 处 `get_or_create_user` 调用点迁移为 `get_user` + `_require_user`（chat/iap/growth/personality/mood/reflection/discovery/share）。B 方案已让这些点在 deleted 场景安全抛错，C 为语义清理，可逐步做。
- `/cron/purge-deleted-users` 兜底扫描端点（QStash 未配置/投递失败时清理 `deleted_at` 超 1 小时的记录）。
