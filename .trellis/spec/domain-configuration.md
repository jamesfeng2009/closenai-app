# 域名配置规范

> 来源：ClosenAI 技术方案设计 v1.1 (2026-07-15)
> 状态：待执行（后端部署到 Vercel 时统一配置）
> 关联文件：architecture.md §2.4、api-conventions.md §1.1

---

## 1. 域名总览

| 域名 | 用途 | 部署位置 | DNS 记录 |
|------|------|---------|---------|
| `closenai.app` | 落地页 / App Store 引导页 / 隐私政策 | Vercel（独立静态项目） | CNAME → cname.vercel-dns.com |
| `api.closenai.app` | FastAPI 后端 API | Vercel（Serverless Functions） | CNAME → cname.vercel-dns.com |
| `www.closenai.app` | 落地页 www 重定向 | Vercel（自动重定向到根域） | CNAME → cname.vercel-dns.com |

### 1.1 为什么用子域名而非路径

| 对比项 | `api.closenai.app`（子域名）✅ | `closenai.app/api/`（路径）❌ |
|--------|-------------------------------|-------------------------------|
| 部署独立 | Vercel 独立项目，独立部署 | 需在同一项目内路由 |
| SSL 证书 | Vercel 自动签发（每子域独立） | 共用主域名证书 |
| 移动端 CORS | 移动 App 无 Origin 限制，天然适合 | 同源但无优势 |
| 未来扩展 | 可加 `app.closenai.app` 等 | 路径越来越复杂 |
| CDN / 缓存 | API 和静态资源独立缓存策略 | 混在一起难以区分 |
| Vercel 配置 | 每个项目绑定自己的域名 | 需 rewrite 规则 |

### 1.2 主域名 `closenai.app` 的内容

落地页项目（可后续创建），包含：
- 产品介绍页（App Store 引导下载）
- 隐私政策页面（App Store 审核必需）
- 服务条款页面
- 健康研究声明（如有）

> P0 阶段优先级低，可先用 Vercel 默认的 `.vercel.app` 域名，上线前再绑定 `closenai.app`。

---

## 2. Cloudflare DNS 配置

> **执行时机**：后端首次部署到 Vercel 后，在 Cloudflare 控制台配置。

### 2.1 DNS 记录

在 Cloudflare 的 DNS 管理页面添加以下记录（假设域名 `closenai.app` 已托管到 Cloudflare）：

| 类型 | 名称 | 目标 | 代理状态 | TTL |
|------|------|------|---------|-----|
| CNAME | `@` | `cname.vercel-dns.com` | 仅 DNS（灰色云） | 自动 |
| CNAME | `www` | `cname.vercel-dns.com` | 仅 DNS（灰色云） | 自动 |
| CNAME | `api` | `cname.vercel-dns.com` | 仅 DNS（灰色云） | 自动 |

### 2.2 代理状态说明

**必须设为"仅 DNS"（灰色云），不能用"已代理"（橙色云）**：

| 状态 | 效果 | 是否使用 |
|------|------|---------|
| 仅 DNS（灰色云） | DNS 解析到 Vercel IP，Vercel 负责 SSL + CDN | ✅ 使用 |
| 已代理（橙色云） | 流量经过 Cloudflare 代理，可能与 Vercel 冲突 | ❌ 不使用 |

> Vercel 需要直接控制 SSL 证书签发和边缘节点。如果开启 Cloudflare 代理（橙色云），Vercel 无法验证域名所有权，SSL 证书签发会失败。

### 2.3 Cloudflare SSL 设置

| 设置项 | 推荐值 | 原因 |
|--------|--------|------|
| SSL/TLS 加密模式 | **关闭**（Off） | Vercel 负责全链路 HTTPS，Cloudflare 不参与 SSL |
| 始终使用 HTTPS | 关闭 | 由 Vercel 控制 |
| 自动 HTTPS 重写 | 关闭 | 由 Vercel 控制 |

> 因为我们使用"仅 DNS"模式，Cloudflare 不参与流量代理，SSL 设置实际上不生效。但为避免误操作开启代理后出问题，建议保持上述设置。

---

## 3. Vercel 域名绑定

### 3.1 后端项目绑定 `api.closenai.app`

在 Vercel 后端项目（closenai-app）中操作：

1. 进入项目 Settings → Domains
2. 添加 `api.closenai.app`
3. Vercel 显示需要验证的 TXT 记录（用于验证域名所有权）
4. 在 Cloudflare DNS 中添加 Vercel 要求的 TXT 记录
5. 验证通过后，Vercel 自动签发 SSL 证书（Let's Encrypt）
6. 状态变为 "Valid Configuration" 后域名可用

### 3.2 落地页项目绑定 `closenai.app`（后续）

1. 在 Vercel 创建新的静态项目（落地页）
2. 进入项目 Settings → Domains
3. 添加 `closenai.app` 和 `www.closenai.app`
4. 设置 `www.closenai.app` 重定向到 `closenai.app`

### 3.3 Vercel 环境变量

在 Vercel 后端项目的 Settings → Environment Variables 中配置：

| 变量名 | 值 | 环境 | 说明 |
|--------|-----|------|------|
| `CORS_ORIGINS` | `["https://closenai.app"]` | Production | 生产环境 CORS 白名单 |
| `API_BASE_URL` | `https://api.closenai.app` | Production | 后端自身 URL（用于 QStash 回调） |
| `GEMINI_API_KEY` | `AIza...` | Production | Gemini API 密钥 |
| `JWT_SECRET` | `<随机64字符>` | Production | JWT 签名密钥 |
| `SUPABASE_URL` | `https://xxx.supabase.co` | Production | Supabase 项目 URL |
| `SUPABASE_SERVICE_KEY` | `eyJ...` | Production | Supabase Service Key |
| `REDIS_URL` | `rediss://default:xxx@xxx.upstash.io:6379` | Production | Upstash Redis URL |

> 开发环境（Development）的 `CORS_ORIGINS` 保持 `["http://localhost:8081","http://localhost:19006"]`。

---

## 4. 后端 CORS 配置

### 4.1 config.py 更新

```python
# backend/config.py

class Settings(BaseSettings):
    # ... 其他配置 ...

    # CORS — 通过环境变量配置，支持生产/开发环境切换
    cors_origins: List[str] = ["http://localhost:8081", "http://localhost:19006"]

    # API 基础 URL（用于 QStash 回调等内部调用）
    api_base_url: str = "http://localhost:8000"
```

生产环境通过 Vercel 环境变量 `CORS_ORIGINS` 覆盖：

```
# Vercel Environment Variable (Production)
CORS_ORIGINS=["https://closenai.app"]
API_BASE_URL=https://api.closenai.app
```

### 4.2 main.py CORS 中间件

```python
# backend/main.py — 已有配置，无需修改

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### 4.3 移动端 CORS 说明

React Native / Expo App 发出的 HTTP 请求**不携带 Origin 头**，不受同源策略限制。因此：

- **iOS / Android App**：CORS 不生效，可直接调用 `https://api.closenai.app`
- **Web 版本（未来）**：需要 CORS 白名单，因此保留 `allow_origins` 配置
- **Expo Web 开发**：`http://localhost:8081` 已在开发环境白名单中

---

## 5. 前端 API 地址配置

### 5.1 环境变量

```bash
# frontend/.env.development
EXPO_PUBLIC_API_BASE_URL=http://localhost:8000

# frontend/.env.production
EXPO_PUBLIC_API_BASE_URL=https://api.closenai.app
```

### 5.2 前端代码引用

```typescript
// frontend/src/services/api.ts

const API_BASE_URL = process.env.EXPO_PUBLIC_API_BASE_URL;

export async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${getToken()}`,
      ...options.headers,
    },
  });

  if (!response.ok) {
    const error = await response.json();
    throw new ApiError(error);
  }

  return response.json();
}
```

### 5.3 SSE 流式请求

```typescript
// frontend/src/services/chat.ts

const API_BASE_URL = process.env.EXPO_PUBLIC_API_BASE_URL;

export async function streamChat(
  content: string,
  onToken: (token: string) => void,
  onDone: () => void,
  onError: (error: Error) => void,
) {
  const response = await fetch(`${API_BASE_URL}/api/v1/chat/messages/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${getToken()}`,
    },
    body: JSON.stringify({ content }),
  });

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const text = decoder.decode(value);
      const lines = text.split('\n');

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = JSON.parse(line.slice(6));
          switch (data.type) {
            case 'token':
              onToken(data.content);
              break;
            case 'done':
              onDone();
              break;
            case 'error':
              onError(new Error(data.message));
              break;
          }
        }
      }
    }
  } catch (error) {
    onError(error as Error);
  }
}
```

---

## 6. SSL / TLS

### 6.1 证书管理

Vercel 自动为绑定的域名签发和续期 SSL 证书（Let's Encrypt）：

| 域名 | 证书签发方 | 自动续期 | 到期前续期时间 |
|------|----------|---------|--------------|
| `closenai.app` | Vercel (Let's Encrypt) | ✅ | 30 天前 |
| `api.closenai.app` | Vercel (Let's Encrypt) | ✅ | 30 天前 |
| `www.closenai.app` | Vercel (Let's Encrypt) | ✅ | 30 天前 |

### 6.2 HTTPS 强制

Vercel 默认启用 HTTPS 强制重定向：
- `http://api.closenai.app` → `https://api.closenai.app`（301 重定向）
- 前端所有请求必须使用 `https://` 协议

### 6.3 HSTS

Vercel 自动添加 HSTS 头：
```
Strict-Transport-Security: max-age=63072000; includeSubDomains; preload
```

---

## 7. 域名验证清单

部署到 Vercel 后，按以下清单验证域名配置：

- [ ] Cloudflare DNS 添加 3 条 CNAME 记录（@、www、api）
- [ ] CNAME 记录均为"仅 DNS"模式（灰色云）
- [ ] Vercel 后端项目绑定 `api.closenai.app`，状态为 "Valid Configuration"
- [ ] Vercel 环境变量 `CORS_ORIGINS` 已设置
- [ ] Vercel 环境变量 `API_BASE_URL` 已设置
- [ ] `curl https://api.closenai.app/health` 返回 `{"status": "ok"}`
- [ ] `curl -I https://api.closenai.app` 返回 200 且包含 HTTPS 头
- [ ] 前端 App 切换到 `EXPO_PUBLIC_API_BASE_URL=https://api.closenai.app` 可正常调用 API
- [ ] SSE 流式接口 `POST /api/v1/chat/messages/stream` 在生产环境正常工作

---

## 8. 未来扩展

### 8.1 Web 面板（P2+）

如果后续上线 Web 版用户面板：

| 域名 | 用途 |
|------|------|
| `app.closenai.app` | Web 用户面板（React Web 应用） |

Web 版需要加入 CORS 白名单：
```
CORS_ORIGINS=["https://closenai.app","https://app.closenai.app"]
```

### 8.2 VPS 迁移后的域名变更

当后端从 Vercel 迁移到 VPS 时：

1. Cloudflare DNS 将 `api` 记录从 CNAME 改为 A 记录，指向 VPS IP
2. 开启 Cloudflare 代理（橙色云），启用 Cloudflare SSL
3. Vercel 项目保留一段时间作为回退，确认无问题后删除

| 迁移前 | 迁移后 |
|--------|--------|
| CNAME `api` → `cname.vercel-dns.com`（灰色云） | A `api` → `VPS_IP`（橙色云） |
| Vercel 管理 SSL | Cloudflare 管理 SSL（Full Strict） |
| Vercel CDN 边缘节点 | Cloudflare CDN 边缘节点 |
