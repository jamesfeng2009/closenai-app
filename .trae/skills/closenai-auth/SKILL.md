---
name: "closenai-auth"
description: "ClosenAI authentication: JWT access/refresh tokens with rotation, Apple Sign-In, email verification code login, username/password registration, password reset, change username/password, account deletion, guest browsing gate. Invoke when working on login, registration, token refresh, Apple auth, email codes, or session management."
---

# ClosenAI Authentication

认证体系规范：JWT（access + refresh 轮转）+ Apple 登录 + 邮箱验证码登录 + 用户名密码注册登录 + 忘记密码 + 修改用户名/密码 + 注销账号 + 游客门控

## 架构总览

### 关键架构转变（从 P0 设备身份 → 正式认证）
- **用户创建模式**：旧「业务接口 `get_or_create_user` 按需建匿名用户」→ 新「用户仅在注册/Apple/验证码登录时创建」。业务路径改用 `get_user`，不存在抛 `USER_NOT_FOUND`。`get_or_create_user` 仅保留给 auth_service 新建场景。
- **鉴权方式**：旧 `X-User-Id` 设备透传废弃 → 新 `Authorization: Bearer <access_token>`。`get_current_user` 生产强制 JWT。
- **前端身份**：旧 `initApiUser()` 生成 UUID 存 AsyncStorage → 新 `loadSession()` 从 SecureStore 读 JWT token。

### Token 体系
| Token | 算法 | 有效期 | 存储 | 用途 |
|-------|------|--------|------|------|
| **access_token** | HS256 | 30 分钟 | 内存 + SecureStore | `Authorization: Bearer` 请求头，业务接口鉴权 |
| **refresh_token** | HS256 | 30 天 | SecureStore | 轮转换取新 access+refresh，防重放 |

- access payload：`{sub: user_id, type: "access", iat, exp}`
- refresh payload：`{sub: user_id, jti: token_id, type: "refresh", iat, exp}`
- **轮转（rotation）**：`/auth/refresh` 验旧 refresh → 吊销旧 + 发新 access+refresh
- **吊销传播**：改密码 / 忘记密码重置 → 吊销该用户全部 refresh token（强制全部设备重新登录）；注销账号 → 级联删 refresh_tokens
- refresh token 哈希存库（`refresh_tokens.token_hash` = sha256(明文)），**明文不落库**

### 三种登录方式
| 方式 | 前端 | 后端校验 |
|-----|------|---------|
| **Apple** | `expo-apple-authentication` 拿 identityToken | PyJWT + Apple JWKS 验签 RS256，验 audience/issuer，取 `sub` 作 apple_id |
| **邮箱验证码** | Resend 发 6 位码 | 码哈希存 Redis（5 分钟 TTL + 60s 冷却 + 日上限 10 次） |
| **用户名密码** | 表单 | passlib argon2 哈希校验 |

## 游客门控（前端）

- **策略**：游客可进 Tabs 浏览，任何写操作（chat/mood/reflect/memory/profile/iap）拦截跳 `AuthScreen`。未登录不产生业务数据，**无需数据迁移**。
- `AuthContext`（`frontend/src/context/AuthContext.tsx`）提供 `isLoggedIn` + `useRequireAuth()` hook。
- 未登录进 Tabs，写操作前调 `requireAuth()` → `false` 时弹提示 + `navigate('Auth')`。
- Tab 页 `useFocusEffect` 数据加载：未登录跳过请求（避免 401 噪声），显示空状态或游客引导。
- `api.ts` 401 拦截：`if (res.status === 401 && accessToken)` → access 过期调 `/auth/refresh`（单飞防并发）→ 重试；refresh 失败 → `clearAuth() + notifySessionReset()`。游客（无 accessToken）不进此分支，直接抛 ApiError(401) 由各屏 catch 显示空状态。

### 会话重置（`frontend/src/services/session.ts`）
- `resetSession()`：清 token + 用户级缓存（dashboard_cache），**保留引导标记**（游客可继续浏览）。用于登出 / token 失效。
- `resetSessionFull()`：清 token + 引导标记 + 缓存，回到引导页。用于注销账号。
- 事件源统一在 `api.ts`（`onSessionReset` / `notifySessionReset`），`session.ts` re-export `onSessionReset` 供 App 订阅。

## 数据模型

### User 扩展字段（`backend/models/user.py`，均 nullable，三种登录方式字段集互补）
```python
email: Mapped[str | None]            # partial unique index WHERE email IS NOT NULL AND deleted_at IS NULL
username: Mapped[str | None]         # partial unique index（同上，软删除记录不占唯一约束位）
password_hash: Mapped[str | None]    # argon2
apple_id: Mapped[str | None]         # partial unique index（同上）
email_verified_at: Mapped[datetime | None]  # 验证码登录时标记
# 账号生命周期（软删除：注销即时失效，QStash 异步物理删除）
status: Mapped[str]                   # 'active' / 'deleted'，default 'active'
deleted_at: Mapped[datetime | None]   # 注销时间；partial unique index 加 deleted_at IS NULL → 同 email/apple_id 注销后可重新注册
```

### 注销账号（软删除 + QStash 异步物理删除）

注销分两步：**软删除即时失效**（同步事务，毫秒级返回）+ **QStash 异步物理删除**（回调 `/cron/purge-user`）。

```
DELETE /user/account → auth_service.delete_account
  1. user_service.soft_delete_account：status='deleted' + deleted_at=now + revoke_all_for_user + clear_push_tokens
  2. await db.commit()（确保软删除落库后再投递 QStash，防回调早于 commit 行锁冲突）
  3. _publish_purge_task → QStash publish /cron/purge-user（失败不阻断注销，记日志）
→ 前端 resetSessionFull 回引导页
→ QStash 回调 POST /cron/purge-user（verify_qstash_signature 验签）→ user_service.purge_user 物理删 13 表（幂等）
```

**orphan 防护**（注销后 30 分钟 access_token 窗口期）：
- `get_user` / `get_by_email` / `get_by_apple_id` / `get_by_username` / `list_all` 全部加 `WHERE status='active'` 过滤 → deleted 用户查不到
- `get_or_create_user` 改造：查不到时先查 `get_user_any_status`，命中 deleted → 抛 `USER_NOT_FOUND`（不建 orphan、不 PK 冲突）
- 前端 `api.ts`：已登录态下 `USER_NOT_FOUND`(40401) → `clearAuth + notifySessionReset` 登出
- `get_user_any_status`（不过滤 status）供 `get_or_create_user` 检测 deleted + `purge_user` 用

**竞态安全**：异步物理删除按 user_id 删，与新记录（重新注册的新 user_id）隔离；软删除记录不占 partial unique index 位，同标识符可立即重新注册。

**QStash 未配置兜底**：开发环境无 QStash 时 purge 不投递（记日志），软删除仍即时生效；物理删除待 P1 `/cron/purge-deleted-users` 定时扫描兜底。

### refresh_tokens 表（`backend/models/auth.py`）
```python
class RefreshToken(Base):
    id: str           # uuid，即 JWT jti
    user_id: str      # FK users.id
    token_hash: str   # sha256(refresh_token 明文)
    expires_at: datetime
    issued_at: datetime
    revoked_at: datetime | None      # 吊销时间
    replaced_by: str | None          # 轮转链（新 token id）
```

### Redis 验证码 key 设计（`backend/core/redis.py`）
- 验证码：`email_code:{purpose}:{email}` → hash(code)，TTL 300s
- 发送冷却：`email_cooldown:{email}`，TTL 60s
- 频率限制：`email_send_count:{email}:{date}`，日上限 10 次

## 端点清单（`backend/routers/auth.py`）

| 方法 | 路径 | 鉴权 | 说明 |
|-----|------|------|------|
| POST | `/auth/register` | 公开 | 邮箱+用户名+密码注册 |
| POST | `/auth/login` | 公开 | 用户名/邮箱+密码登录 |
| POST | `/auth/send-code` | 公开 | 发邮箱验证码（body: email, purpose=register\|login\|reset） |
| POST | `/auth/login-code` | 公开 | 邮箱验证码登录（未注册自动建账号） |
| POST | `/auth/apple` | 公开 | Apple 登录（body: identity_token） |
| POST | `/auth/refresh` | 公开 | 刷新 token（轮转：吊销旧+发新） |
| POST | `/auth/logout` | 公开 | 登出（吊销当前 refresh） |
| POST | `/auth/forgot-password` | 公开 | 忘记密码（防枚举：统一返回成功） |
| POST | `/auth/reset-password` | 公开 | 重置密码（码+新密码；改后吊销全部 refresh） |
| GET | `/auth/me` | ✅ JWT | 当前用户信息 |
| POST | `/auth/change-password` | ✅ JWT | 改密码（旧+新；改后吊销全部 refresh） |
| PATCH | `/auth/username` | ✅ JWT | 改用户名（查重） |

## 错误码映射（`backend/core/errors.py`）

| 错误码 | 枚举 | 含义 |
|-------|------|------|
| 40005 | VERIFICATION_CODE_INVALID | 邮箱验证码不正确 |
| 40006 | VERIFICATION_CODE_EXPIRED | 邮箱验证码已过期 |
| 40007 | PASSWORD_TOO_WEAK | 密码强度不足（≥8 位含字母+数字） |
| 40101 | TOKEN_EXPIRED | access token 过期 |
| 40102 | TOKEN_INVALID | token 无效 |
| 40103 | MISSING_TOKEN | 缺少 token |
| 40104 | INVALID_CREDENTIALS | 用户名/邮箱或密码错误 |
| 40105 | REFRESH_TOKEN_INVALID | refresh token 无效/已吊销/已过期 |
| 40106 | APPLE_AUTH_FAILED | Apple identityToken 验签失败 |
| 40404 | EMAIL_NOT_REGISTERED | 邮箱未注册（reset 内部使用，对外不泄露） |
| 40903 | EMAIL_ALREADY_USED | 邮箱已被注册 |
| 40904 | USERNAME_ALREADY_USED | 用户名已被占用 |
| 42902 | EMAIL_SEND_RATE_LIMITED | 验证码发送频率超限 |

## 安全约束

- **refresh token**：仅存哈希，明文不落库；轮转 + 吊销链防重放
- **Apple identityToken**：必须验签 + audience + issuer，**禁止信任前端传来的 email**（以 Apple payload email 为准）
- **验证码**：6 位、TTL 5 分钟、60s 冷却、日上限 10 次；验证成功即删 key
- **密码**：argon2 哈希；强度校验（≥8 位，含字母+数字）；改密/重置后吊销全部 refresh
- **forgot-password**：不泄露邮箱是否注册（统一返回成功，防枚举）
- **敏感字段**：`password_hash` / `token_hash` 禁止进日志；contextvar 日志只透传 user_id
- **HTTPS**：所有认证端点 HTTPS；生产 `ENVIRONMENT=production` 强制 JWT（fail-closed）
- **注销账号**：软删除即时失效（status='deleted' + 吊销全部 refresh + 清 push token）+ QStash 异步物理删除（`/cron/purge-user` 回调，幂等）。partial unique index 加 `deleted_at IS NULL` 允许同标识符重新注册。详见「注销账号」小节

## 前端实现要点

### Token 存储（SecureStore）
- `ACCESS_TOKEN_KEY = '@closenai/access_token'`
- `REFRESH_TOKEN_KEY = '@closenai/refresh_token'`
- `USER_KEY = '@closenai/auth_user'`（用户摘要 JSON）
- 启动 `loadSession()` 从 SecureStore 恢复；登录成功 `setAuth()` 持久化；登出/失效 `clearAuth()` 清除

### AuthContext（`frontend/src/context/AuthContext.tsx`）
- `useAuth()`：`{ user, isLoggedIn, login, logout, deleteMyAccount, refreshUser }`
- `useRequireAuth()`：写操作前调用，未登录弹提示 + `navigate('Auth')`，返回 boolean
- 订阅 `onSessionReset`：token 失效时自动置未登录态

### 屏幕
- `AuthScreen.tsx`：登录/注册/验证码登录三模式 + Apple 登录（仅 iOS）+ 忘记密码入口 + 游客浏览跳过
- `ForgotPasswordScreen.tsx`：2 步重置（邮箱发码 → 码+新密码）
- `ProfileScreen.tsx`：游客态显示登录 CTA；登录态显示修改用户名/修改密码 Modal + 退出登录 + 注销账号

### i18n
- `auth` 段 + `errors.sessionExpired` / `errors.sendCodeFailed` + `profile.sectionAccount`
- 15 语言文件同步（zh/en 正式翻译，其余可用英文）
- 改完 `npm run tsc` 必须 exit 0

## 关键文件清单

| 文件 | 职责 |
|-----|------|
| `backend/core/auth.py` | JWT 创建/解码、密码哈希、Apple 验签、`get_current_user` |
| `backend/services/auth_service.py` | 认证业务逻辑（注册/登录/验证码/refresh 轮转/改密/注销） |
| `backend/services/email_service.py` | Resend 邮件发送（双语文案） |
| `backend/repositories/auth_repository.py` | refresh token CRUD |
| `backend/routers/auth.py` | 认证端点 |
| `backend/models/auth.py` | RefreshToken 模型 |
| `frontend/src/services/api.ts` | token 存储 + 401 自动刷新 + auth API 函数 |
| `frontend/src/services/session.ts` | 会话重置编排 |
| `frontend/src/context/AuthContext.tsx` | 认证状态 + requireAuth hook |
| `frontend/src/screens/AuthScreen.tsx` | 登录/注册/验证码/Apple 统一入口 |
| `frontend/src/screens/ForgotPasswordScreen.tsx` | 忘记密码 2 步重置 |

## 业务路径适配

- `backend/services/user_service.py`：`delete_account` 已拆为 `soft_delete_account`（软删除+吊销refresh+清push）+ `purge_user`（物理删 13 表，QStash 回调，幂等）+ `purge_deleted_users`（cron 兜底批量物理删除）
- `backend/repositories/user_repository.py`：`get_user` / `get_by_*` / `list_all` 加 `status='active'` 过滤；`get_or_create_user` 检测 deleted 抛 `USER_NOT_FOUND`；新增 `require_user`（get_user + 抛错，业务统一入口）/ `get_user_any_status` / `soft_delete` / `list_deleted_stale`
- **C 方案（已完成）**：27 处业务调用点 `get_or_create_user → require_user`（chat/iap/growth/personality/mood/reflection/discovery/share/message_generator）。`require_user` 不存在/已注销抛 `USER_NOT_FOUND`，业务路径不再按需建用户，消除 orphan 隐患。`conftest.py` 保留 `get_or_create_user`（测试建用户）
- **P1（已完成）**：`GET /cron/purge-deleted-users` 兜底扫描端点（`verify_cron_secret` 鉴权，扫描 `deleted_at` 超 1h 记录，逐用户 SAVEPOINT 批量物理删除，单用户失败不连锁）。`stale_hours=1` 给 QStash 正常重试留窗口
- 新增 repository 必须在 `backend/repositories/__init__.py` 导出实例

## 测试

- pytest 套件在 `backend/tests/`（asyncio_mode=auto，sqlite 内存库，mock_llm autouse）
- 改认证代码后跑：`.venv/bin/python -m pytest backend/tests/ -q`
- Apple 验签用 mock JWKS；Redis 用 stub
- 前端：`cd frontend && npm run tsc`（exit 0）
- 迁移：`.venv/bin/alembic heads`（确认单头）
