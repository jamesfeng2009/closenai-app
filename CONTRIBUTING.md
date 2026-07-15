# ClosenAI 贡献指南

---

## 团队角色

| 角色 | 负责人 | 负责目录 | 核心 spec |
|------|--------|---------|-----------|
| 后端开发 | @jamesfeng2009 | `backend/` | architecture.md, code-architecture.md, api-conventions.md, memory-layer.md, cost-optimization.md, domain-configuration.md |
| 前端开发 | @frontend-dev | `frontend/` | ui-design-system.md, app-startup-timing.md, frontend-sqlite-schema.md, app-store-compliance.md |
| 共同维护 | 双方 | `.trellis/workflow.md`, `CLAUDE.md`, `CONTEXT.md`, `.claude/` | — |

---

## Git 分支策略

### 分支命名

```
main                          # 生产分支，始终可部署
├── feat/backend/P0-F02-memory-layer    # 后端功能分支
├── feat/frontend/P0-F05-growth-dashboard # 前端功能分支
├── fix/backend/sse-timeout              # 后端修复分支
├── fix/frontend/tab-navigation          # 前端修复分支
└── docs/update-api-conventions          # 文档分支
```

### 分支规则

1. **从 main 创建分支**：所有功能分支从 `main` 创建
2. **前后端独立分支**：前端和后端的功能在不同分支上开发，不交叉
3. **PR 合并到 main**：通过 PR Review 后合并，不直接 push 到 main
4. **PR Review 要求**：
   - 后端 PR：由后端开发者自行 Review（或 AI 助手 Review）
   - 前端 PR：由前端开发者自行 Review（或 AI 助手 Review）
   - 共同维护文件 PR：需要双方 Review

### API 契约变更流程

当 API 发生变更时，**必须按以下顺序操作**：

```
1. 后端开发者修改 API → 创建后端 PR
2. 后端开发者更新 .trellis/spec/api-conventions.md → 同一 PR 或单独 docs PR
3. 通知前端开发者 API 变更内容
4. 前端开发者更新前端 API 调用代码 → 创建前端 PR
```

**禁止**：前端开发者直接修改后端 API 代码，或后端开发者直接修改前端 UI 代码。

---

## 开发工作流

### 后端开发者工作流

```
/trellis:start
    ↓
/brainstorming（读取 architecture.md, api-conventions.md, memory-layer.md）
    ↓
/trellis:before-dev
    ↓
/implement（TDD: 红→绿→重构，只在 backend/ 目录操作）
    ↓
/requesting-code-review（检查 OCP/SRP/分层架构）
    ↓
/verification-before-completion
    ↓
/trellis:finish-work → 创建 PR
    ↓
/trellis:update-spec（更新 api-conventions.md 等）
```

### 前端开发者工作流

```
/trellis:start
    ↓
/brainstorming（读取 ui-design-system.md, app-startup-timing.md, frontend-sqlite-schema.md）
    ↓
/trellis:before-dev
    ↓
/implement（TDD: 红→绿→重构，只在 frontend/ 目录操作）
    ↓
/requesting-code-review（检查 UI 规范、性能指标、离线策略）
    ↓
/verification-before-completion
    ↓
/trellis:finish-work → 创建 PR
    ↓
/trellis:update-spec（更新 ui-design-system.md 等）
```

---

## Spec 文件归属

| Spec 文件 | 维护者 | 说明 |
|-----------|--------|------|
| `architecture.md` | 后端 | 技术架构、部署、LLM 选型 |
| `code-architecture.md` | 后端 | 后端分层架构、OCP/SRP 实现 |
| `api-conventions.md` | 后端 | API 契约定义（前端依赖但不可直接修改） |
| `memory-layer.md` | 后端 | Temporal Memory 数据模型和算法 |
| `cost-optimization.md` | 后端 | LLM 成本、TTL 策略 |
| `domain-configuration.md` | 后端 | 域名、DNS、SSL |
| `ui-design-system.md` | 前端 | UI 主题、组件规范、页面布局 |
| `app-startup-timing.md` | 前端 | 冷热启动、离线策略、性能指标 |
| `frontend-sqlite-schema.md` | 前端 | 本地数据库表结构、同步策略 |
| `app-store-compliance.md` | 前端 | App Store 审核、隐私政策 |

**规则**：前端开发者如需修改后端 spec（如 API 契约），必须通过 PR Review 由后端开发者批准。

---

## 沟通约定

1. **API 变更通知**：后端修改 API 后，在 PR 描述中写明变更内容，并 @ 前端开发者
2. **数据模型变更**：任何涉及数据库表结构的变更，双方都需知晓
3. **Spec 变更**：修改 spec 文件时，PR 描述中说明变更原因和影响范围
4. **依赖阻塞**：如果前端等待后端 API 就绪，在后端 PR 中标注 "BLOCKING: frontend P0-F05"

---

## 验证

每次提交前运行技能验证脚本：

```bash
node .trellis/scripts/verify-skills.js
```
