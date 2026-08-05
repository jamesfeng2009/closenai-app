# ClosenAI 认证体系实施计划

## Context（为什么做）

当前 ClosenAI 处于 **P0 设备级匿名身份** 阶段：前端 `initApiUser()` 生成 UUID 存 AsyncStorage，作为 `X-User-Id` 透传，后端 `user_repository.get_or_create_user` 按 user_id 建匿名用户。这套机制无法支撑正式上架：无账号体系、无跨设备同步、无 Apple 登录（App Store 强制支持）、无邮箱/密码登录、无注销合规闭环。

本计划从零搭建正式认证体系，覆盖：**Apple 登录、邮箱验证码登录、用户名密码注册登录、忘记密码、修改用户名/密码、注销账号、access_token + refresh_token**，并新建 `closenai-auth` SKLL 沉淀规范。

### 已确认决策（用户拍板）
| 决策点 | 结论 |
|---|---|
| 登录策略 | 游客可浏览 + 操作跳登录（未登录不产生业务数据，**无需数据迁移**） |
| 邮件服务 | Resend |
| SKILL 归属 | 新建 `closenai-auth` |
| token 体系 | access_token（短期）+ refresh_token（长期，轮转） |

### 关键架构转变
1. **用户创建模式**：从「业务接口 `get_or_create_user` 按需建匿名用户」→「用户仅在注册/Apple/验证码登录时创建」，业务路径改为 `get_user`，不存在抛 `USER_NOT_FOUND`。
2. **鉴权方式**：`X-User-Id` 设备透传废弃，业务接口统一 `Authorization: Bearer <access_token>`；`get_current_user` 生产已强制 JWT，开发环境也改为 JWT 优先（X-User-Id 仅留作开发兜底，不再建用户）。
3. **未登录门控**：前端未登录可进 Tabs 浏览，任何写操作（chat/mood/reflect/memory/profile/iap）拦截跳 `AuthScreen`；GET 接口 401 时空状态展示「登录后查看」。

---

## 架构总览

### Token 体系
- **access_token**：HS256 JWT，`{sub: user_id, type: "access", exp, iat}`，有效期 **30 分钟**，放 `Authorization: Bearer`。
- **refresh_token**：HS256 JWT，`{sub: user_id, jti: token_id, type: "refresh", exp, iat}`，有效期 **30 天**，存 SecureStore。
- **轮转（rotation）**：`/auth/refresh` 验旧 refresh → 吊销旧 + 发新 access+refresh（防重放）。
- **吊销传播**：改密码 / 忘记密码重置 → 吊销该用户全部 refresh token（强制重新登录）；注销账号 → 级联删 refresh_tokens。
- refresh token 哈希存库（`refresh_tokens` 表，`token_hash` 列），不存明文。

### 三种登录方式
| 方式 | 前端 | 后端校验 |
|---|---|---|
| Apple | `expo-apple-authentication` 拿 identityToken | PyJWT + Apple JWKS 验签，验 audience/issuer，取 `sub` 作 apple_id |
| 邮箱验证码 | Resend 发 6 位码 | 码哈希存 Redis（5 分钟 TTL + 60s 冷却） |
| 用户名密码 | 表单 | passlib argon2 哈希校验 |

### 游客门控（前端）
- `AuthContext` 提供 `isLoggedIn` + `requireAuth(action)`。
- 未登录进 Tabs，写操作前调 `requireAuth` → `false` 时 `navigate('Auth')`。
- `api.ts` 401 拦截：access 过期 → `/auth/refresh` 重试；refresh 失败 → 触发 logout 跳 AuthScreen。

---

## 新建 SKILL：`closenai-auth`

路径：`.trae/skills/closenai-auth/SKILL.md`（参考现有 `closenai-iap`/`closenai-llm` 同级独立 skill 风格）。

内容覆盖：
- 认证架构（token 体系、三种登录、游客门控、用户创建模式转变）
- 安全约束（token 轮转、refresh 哈希存储、Apple 验签要点、验证码 TTL/频率限制、密码强度、HTTPS-only、敏感字段日志脱敏）
- 数据模型（User 扩展字段、refresh_tokens 表、Redis 验证码 key 设计）
- 端点清单与错误码映射
- 前端 token 存储（SecureStore）、401 自动刷新、登出/注销闭环
- 合规（Apple 强制、数据删除权已由现有 `delete_account` 覆盖）

---

## 后端实现

### 1. 依赖（`backend/requirements.txt` + `pyproject.toml` 同步）
新增：
- `passlib[argon2]>=1.7.4`（密码哈希）
- `resend>=2.0.0`（邮件）
- `httpx>=0.27`（拉 Apple JWKS；或复用已有 HTTP client）

已有：`PyJWT`、`cryptography`（Apple RS256 验签可用）。

### 2. 配置（`backend/config.py` + `backend/.env.example`）
`Settings` 新增字段：
```
jwt_access_expire_minutes: int = 30
jwt_refresh_expire_days: int = 30
resend_api_key: str = ""
apple_client_id: str = ""          # Apple Services ID（验 identityToken audience）
email_from: str = "ClosenAI <noreply@closenai.app>"
```
`.env.example` 同步补 `RESEND_API_KEY` / `APPLE_CLIENT_ID` / `EMAIL_FROM`，并更新 `JWT_SECRET` 注释（区分 access/refresh 仅有效期不同，共用 secret）。

### 3. 数据模型
**`backend/models/user.py`** 扩展字段（均 nullable，因三种登录方式字段集不同）：
```
email: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 唯一索引
username: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 唯一索引
password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
apple_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 唯一索引
email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```
唯一索引在迁移里建（partial index：`WHERE email IS NOT NULL`，允许多个 NULL）。

**`backend/models/auth.py`**（新建）：
```python
class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[str]  # uuid，即 JWT jti
    user_id: Mapped[str]  # FK users.id
    token_hash: Mapped[str]  # sha256(refresh_token)
    expires_at: Mapped[datetime]
    issued_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    replaced_by: Mapped[str | None]  # 轮转链
```

### 4. Alembic 迁移（新文件）
`down_revision` = `alembic heads` 当前单头（实现时确认；若多头先建纯 merge 迁移恢复单头，遵循项目先例 `b3c5d7e9f1a2`）。
- `users` 加 5 列 + 3 个 partial unique index（email/username/apple_id）
- 新建 `refresh_tokens` 表 + 索引（user_id, token_hash, expires_at）
- `downgrade()` 反向 drop

### 5. 错误码（`backend/core/errors.py`）
`ErrorCode` 新增（4xxxx 段，按现有分段规范）：
```
INVALID_CREDENTIALS = 40104
REFRESH_TOKEN_INVALID = 40105
APPLE_AUTH_FAILED = 40106
VERIFICATION_CODE_INVALID = 40005
VERIFICATION_CODE_EXPIRED = 40006
PASSWORD_TOO_WEAK = 40007
EMAIL_ALREADY_USED = 40903
USERNAME_ALREADY_USED = 40904
EMAIL_NOT_REGISTERED = 40404
EMAIL_SEND_RATE_LIMITED = 42902
```

### 6. Redis 客户端（`backend/core/redis.py` 新建）
参考 `backend/services/cron_service.py` 现有 Upstash Redis 用法，抽象统一客户端：
- `setex(key, ttl, value)` / `get(key)` / `del(key)` / `incr_with_ttl(key, ttl)`
- 验证码 key：`email_code:{purpose}:{email}` → hash(code)，TTL 300s
- 发送冷却 key：`email_cooldown:{email}`，TTL 60s
- 频率限制 key：`email_send_count:{email}:{date}`，日上限 10 次

### 7. 核心认证模块（`backend/core/auth.py` 扩展）
保留现有 `get_current_user`（生产强制 JWT），新增：
- `hash_password(pw) / verify_password(pw, hash)`：`CryptContext(schemes=["argon2"])` 单例
- `create_access_token(user_id) / create_refresh_token(user_id, token_id)`
- `decode_token(token)` 严格解码 + type 校验
- `verify_apple_identity_token(identity_token)`：拉 Apple JWKS（缓存）→ PyJWT 验签 RS256 → 验 audience=`apple_client_id`、issuer=`https://appleid.apple.com` → 返回 `(apple_id, email, is_private_email)`

### 8. 邮件服务（`backend/services/email_service.py` 新建）
- `send_verification_code(email, code, purpose, language)`：用 `resend` SDK，按 `language` 双语文案（zh 中文 / 其余英文，遵循项目文案双表规范）
- 失败 try/except 安全降级（记录日志，不阻断，但返回错误码让前端提示重试）

### 9. Repository（`backend/repositories/auth_repository.py` 新建 + `__init__.py` 导出实例 `auth_repository`）
- `create_refresh_token` / `get_active_token_by_hash` / `revoke_token` / `revoke_all_for_user`
- `user_repository` 新增：`get_by_email` / `get_by_username` / `get_by_apple_id` / `set_password` / `set_username` / `mark_email_verified`

### 10. 认证服务（`backend/services/auth_service.py` 新建）
方法清单（事务在 service 层 commit，遵循项目约定）：
- `register_with_password(email, username, password)` → 建用户 + hash 密码 + 发 token
- `login_with_password(identifier, password)` → 按 email/username 查 + verify + 发 token
- `send_email_code(email, purpose)` → 频率检查 + 生成码 + Redis 存哈希 + 发邮件
- `login_with_email_code(email, code)` → 验码（get_or_create：已注册直接登，未注册自动建账号并标记 email_verified）
- `login_with_apple(identity_token)` → 验签 + get_or_create by apple_id + 发 token
- `refresh_tokens(refresh_token)` → 验 hash+未吊销+未过期 → 吊销旧+发新（轮转）
- `logout(refresh_token)` → 吊销当前 refresh
- `request_password_reset(email)` → 存在则发重置码（不存在也返回成功防枚举）
- `reset_password(email, code, new_password)` → 验码 + 改密 + 吊销全部 refresh
- `change_password(user_id, old, new)` → 验旧 + 改新 + 吊销全部 refresh
- `update_username(user_id, new_username)` → 查重 + 改

### 11. 认证路由（`backend/routers/auth.py` 新建 + `main.py` 注册）
```
POST /api/v1/auth/register         邮箱+用户名+密码注册
POST /api/v1/auth/login            用户名/邮箱+密码登录
POST /api/v1/auth/send-code        发邮箱验证码（body: email, purpose）
POST /api/v1/auth/login-code       邮箱验证码登录
POST /api/v1/auth/apple            Apple 登录（body: identity_token）
POST /api/v1/auth/refresh          刷新 token
POST /api/v1/auth/logout           登出（吊销 refresh）
POST /api/v1/auth/forgot-password  忘记密码（发重置码）
POST /api/v1/auth/reset-password   重置密码（码+新密码）
# 需登录（Depends(get_current_user)）：
GET   /api/v1/auth/me              当前用户信息
POST  /api/v1/auth/change-password 改密码（旧+新）
PATCH /api/v1/auth/username        改用户名
```
`main.py` 加 `app.include_router(auth.router, prefix="/api/v1", tags=["auth"])`。

### 12. 业务路径适配（模式转变）
`backend/services/user_service.py`：所有 `get_or_create_user(db, user_id)` 调用点（`get_profile`/`set_ai_consent`/`update_settings`/`update_streak`/`get_milestones`/`register_push_token`/`require_ai_consent`）改为「先 `get_user`，不存在抛 `USER_NOT_FOUND`」。`get_or_create_user` 保留仅给 auth_service 新建场景。`delete_account` 的级联删除表列表追加 `RefreshToken`。

---

## 前端实现

### 1. 依赖（`frontend/package.json`）
新增：
- `expo-secure-store`（token 安全存储，iOS Keychain）
- `expo-apple-authentication`（Apple 登录）

### 2. `frontend/app.json`
- `plugins` 追加 `"expo-apple-authentication"`
- `ios` 追加 `"usesAppleSignIn": true`

### 3. 认证服务（`frontend/src/services/auth.ts` 新建）
- SecureStore 存 `access_token` / `refresh_token` / `user` 摘要
- `loadSession()`：启动读 token → 设置 `isLoggedIn`
- `login/register/loginCode/appleLogin` → 存 token
- `logout()` → 调 `/auth/logout` + 清 SecureStore + `clearPushToken` + 触发 session reset
- `getAuthHeaders()` → `{Authorization: Bearer access}`

### 4. API client 改造（`frontend/src/services/api.ts`）
- `getHeaders()`：`Authorization: Bearer` 替代 `X-User-Id`（`cachedUserId`/`initApiUser`/`resetApiUser` 设备身份逻辑移除，改读 SecureStore token）
- `request()` 包装 401 拦截：access 过期 → 调 `/auth/refresh`（单飞，防并发多次刷新）→ 重试原请求；refresh 失败 → `logout()` + 跳 AuthScreen
- `streamChat` 的 XHR 头同步改 `Authorization`
- 新增 auth API 函数：`register/login/sendCode/loginCode/appleLogin/refreshToken/logoutApi/forgotPassword/resetPassword/changePassword/updateUsername/getMe`

### 5. 认证状态（`frontend/src/services/session.ts` 扩展 或新建 `AuthContext`）
- 暴露 `isLoggedIn`、`requireAuth(action)`、`login`、`logout`
- `requireAuth`：未登录 → `navigate('Auth')` 返回 false
- `resetSession` 改为：清 token + 清缓存 + 通知订阅者

### 6. 新增屏幕
- `frontend/src/screens/AuthScreen.tsx`：登录/注册/验证码登录/Apple 登录统一入口 +「忘记密码」入口 +「游客浏览」跳过按钮
- `frontend/src/screens/ForgotPasswordScreen.tsx`：邮箱 → 验证码 → 新密码（3 步或单页分段）
- （修改用户名/密码用 Modal 或 `ProfileScreen` 内分段，不单独建屏）

### 7. `frontend/App.tsx` 改造
- 启动 `Promise.all`：`initI18n` + `loadSession()`（替代 `initApiUser`）+ `onboarded`
- `RootStackParamList` 加 `Auth` / `ForgotPassword`
- 渲染：`ready` → `!onboarded` ? `Onboarding` : 主导航（含 `Auth` 路由）
- 未登录默认进 Tabs（游客浏览），写操作由 `requireAuth` 拦截跳 `Auth`

### 8. `ProfileScreen.tsx` 改造
- 「退出登录」`onLogout`：改调 `logout()`（API 吊销 refresh + 清 token + 跳 Auth），不再只是 `resetSession`
- 新增「修改密码」「修改用户名」入口（Modal 或跳路由）
- 文案：当前 `profile.logoutConfirmMessage` 提到"创建新设备身份"需改为"清除本机登录状态"

### 9. 游客操作拦截
- 各写操作（HomeScreen 发消息、ReflectScreen 签到/保存、MemoriesScreen 删除、ProfileScreen 改设置、Paywall 购买）前调 `requireAuth`
- Tab 页 `useFocusEffect` 数据加载：未登录跳过请求，显示「登录后查看」空状态

### 10. i18n（`frontend/src/i18n/locales/` 15 个文件同步）
- 新增 `auth` 段：`login/register/forgotPassword/appleLogin/emailCode/username/password/confirmPassword/sendCode/resetPassword/changePassword/usernameTaken/emailTaken/passwordTooWeak/guestBrowse` 等
- `errors` 段补：`invalidCredentials/emailAlreadyUsed/usernameAlreadyUsed/codeInvalid/codeExpired/emailNotRegistered/appleFailed/refreshFailed/passwordTooWeak`
- zh/en 正式翻译，其余 13 语种可用英文；改完 `npm run tsc` 必须 exit 0

---

## 安全与合规约束
- refresh_token 仅存哈希，明文不落库；轮转 + 吊销链防重放
- Apple identityToken 必须验签 + audience + issuer，禁止信任前端传来的 email（以 Apple payload email 为准）
- 验证码：6 位、TTL 5 分钟、60s 冷却、日上限 10 次；验证成功即删 key
- 密码：argon2 哈希；强度校验（≥8 位，含字母+数字）；改密/重置后吊销全部 refresh
- `forgot-password` 不泄露邮箱是否注册（统一返回成功）
- 敏感字段（password_hash/token_hash）禁止进日志；`contextvar` 日志只透传 user_id
- 所有认证端点 HTTPS；生产 `ENVIRONMENT=production` 强制 JWT（已有 fail-closed 逻辑）
- 注销账号沿用现有 `delete_account` 级联删除（追加 refresh_tokens 表），满足数据删除权

---

## 实施阶段（建议顺序）

**Phase 1 — 后端认证内核**（可独立验证）
依赖/配置/模型/迁移/errors/redis/core(auth+密码+apple)/email_service/auth_repository/auth_service/auth_router + main 注册 + 业务路径 `get_or_create_user`→`get_user` 适配。

**Phase 2 — 前端认证骨架**
依赖/app.json/auth.ts/api.ts 改造/AuthContext/AuthScreen/导航门控/token 自动刷新/ProfileScreen 登出改造。

**Phase 3 — 补全与联调**
忘记密码屏/修改用户名密码/Apple 登录真机联调（需 Apple Services ID 配置）/i18n 15 语言同步。

**Phase 4 — SKILL + 测试 + 验证**
写 `closenai-auth` SKILL.md；补 pytest（register/login/refresh 轮转/apple mock/验证码 TTL/改密吊销）；`tsc --noEmit`；alembic heads 单头；本地端到端跑通。

---

## 验证方案

### 后端
- `.venv/bin/python -m pytest backend/tests/ -q`（新增 auth 测试套件，asyncio + sqlite 内存库；Apple 验签用 mock JWKS，Redis 用 fakeredis 或 stub）
- `.venv/bin/alembic heads`（确认单头）；`.venv/bin/alembic upgrade head`（迁移可执行）
- `.venv/bin/python -c "import backend.routers.auth, backend.services.auth_service, backend.core.auth"`（import 冒烟）
- 本地 `uvicorn backend.main:app --reload`，用浏览器/Postman 跑通：注册→登录→/auth/me→refresh→改密→logout→forgot/reset 全链路

### 前端
- `cd frontend && npm run tsc`（exit 0）
- Expo dev build（非 Expo Go，因 Apple 登录需原生模块）真机：游客浏览→点写操作跳 Auth→邮箱注册→登录→token 自动刷新→改用户名/密码→退出→忘记密码重置→注销
- Apple 登录需配置 Apple Services ID + 本地 push 证书环境，真机测

### 端到端关键路径
1. 未登录进 Tabs，点「发消息」→ 跳 AuthScreen
2. 邮箱验证码注册 → 收到 Resend 邮件 → 验码登录 → 进 Tabs 可操作
3. access 过期后自动 refresh 续期（无感）
4. 改密码 → 旧 refresh 全失效 → 其他设备被踢
5. 注销账号 → 全表级联删除（含 refresh_tokens）

---

## 待实现时确认的细节
- `alembic heads` 当前单头（写迁移 down_revision 前先确认）
- `cron_service.py` 里 Upstash Redis 的具体调用方式，`core/redis.py` 统一封装时对齐
- Apple Services ID / Apple Key 配置（需 App Store Connect + Developer Account，由用户配置）
- Resend 域名验证（发件域 closenai.app 需在 Resend 配置 DNS）
