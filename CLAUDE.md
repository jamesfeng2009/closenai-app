# CLAUDE.md — AI 编码助手说明文件

本文件为 AI 编码助手（Claude Code / Cursor / Trellis 等）提供项目上下文和行为规范。所有 AI 辅助开发必须遵循本文件的约定。

---

## 项目简介

ClosenAI 是一款**个人成长追踪工具**，帮助用户通过反思记录追踪信念演变和成长轨迹。

**重要澄清**：ClosenAI **不是** AI 聊天工具。虽然产品包含 Reflection Mode（反思模式）的对话交互，但其核心价值在于：
- 帮助用户记录和追踪个人成长
- 识别信念演变、转变检测、回归识别和渐变漂移
- 通过 Temporal Memory 提供跨时间的反思关联

不要将本项目当作通用聊天机器人来设计或实现。

---

## 技术栈

### 后端（backend/）
- **语言**：Python 3.12+
- **框架**：FastAPI
- **数据验证**：Pydantic v2
- **ORM**：SQLAlchemy 2.0（如需要数据库）
- **异步支持**：async/await
- **流式响应**：Server-Sent Events (SSE)
- **LLM**：Google Gemini 2.5 Flash / Flash-Lite（通过 OpenAI 兼容端点调用）

### 前端（frontend/）
- **框架**：React Native
- **构建工具**：Expo（Expo Router）
- **语言**：TypeScript
- **状态管理**：按需选择（推荐 Zustand）
- **导航**：Expo Router（文件路由）
- **本地数据库**：expo-sqlite（离线优先策略）

---

## 目录结构

```
closenai-app/
|-- .trellis/                  # Trellis 工作流配置
|   |-- workflow.md            # 日常工作流规范
|   |-- workspace/             # 个人工作空间（git忽略）
|   |-- scripts/               # 辅助脚本
|   `-- spec/                  # 规范文件（实现唯一信息源）
|-- backend/                   # 后端代码（Python + FastAPI）
|   |-- routers/               # HTTP 路由层
|   |-- services/              # 业务逻辑层
|   |-- repositories/          # 数据访问层
|   |-- models/                # 数据模型（Pydantic + SQLAlchemy）
|   |-- config.py              # 配置管理
|   |-- main.py                # FastAPI 入口
|   `-- requirements.txt       # Python 依赖
|-- frontend/                  # 前端代码（React Native + Expo）
|   |-- src/
|   |   |-- screens/           # 页面组件
|   |   |-- components/        # 通用组件
|   |   |-- services/          # API 调用服务
|   |   |-- theme/             # 主题（颜色、字体等）
|   |   `-- types/             # TypeScript 类型定义
|   |-- app.json               # Expo 配置
|   |-- App.tsx                # 应用入口
|   `-- package.json           # Node 依赖
|-- CONTEXT.md                 # 领域术语表（必读）
|-- CLAUDE.md                  # 本文件
|-- README.md                  # 项目说明
`-- .gitignore
```

---

## 技能框架组合

本项目采用 **三层技能框架组合**，各层职责清晰、互不冲突：

### 架构总览

```
Trellis（框架层 — 项目骨架）
├── SessionStart hook        ← 保留：注入项目状态 + spec 引用
├── 任务状态机                ← start / before-dev / finish-work / update-spec
├── 记忆层 + 多平台配置
│
├── Superpowers（执行层 + 设计层 — 强化纪律）
│   ├── brainstorming              ← 替换 Trellis brainstorm
│   ├── requesting-code-review     ← 替换 Trellis check
│   ├── test-driven-development    ← TDD 纪律
│   ├── systematic-debugging       ← 深度调试
│   ├── verification-before-completion ← 完成前验证
│   └── subagent-driven-development    ← 无人值守批量任务（按需）
│
└── Matt Pocock（诊断层 — 精准工具）
    ├── grill-with-docs            ← 与文档深度对话
    ├── diagnosing-bugs            ← Bug 根因定位
    ├── improve-codebase-architecture
    └── domain-modeling
```

### 各层职责

| 层级 | 框架 | 职责 | 关键能力 |
|------|------|------|---------|
| 框架层 | Trellis | 项目骨架、任务状态机、记忆层、多平台 | SessionStart hook、spec/tasks 目录结构、跨会话记忆 |
| 执行层 | Superpowers | 强化开发纪律、系统化流程 | HARD-GATE brainstorm、TDD、系统化调试、两阶段代码审查 |
| 诊断层 | Matt Pocock | 精准诊断工具、按需调用 | 文档对话、Bug 定位、架构改进、领域建模 |

### 冲突解决

| 冲突点 | 采用方案 | 原因 |
|--------|---------|------|
| Brainstorming | **Superpowers** | HARD-GATE 确保需求充分讨论；Visual Companion 可视化辅助；Spec 自审减少遗漏 |
| SessionStart Hook | **Trellis** | Superpowers Memory Layer 会注入大量历史信息消耗 token；Trellis 仅注入项目状态 + spec 引用，更轻量 |
| Code Review | **Superpowers** | git SHA diff 精确定位；三级反馈（阻断/建议/赞美）；两阶段审查更系统化 |

### Superpowers 红旗概念（Red Flags）

开发过程中遇到以下信号时，必须停下来使用对应技能：

| 红旗信号 | 触发技能 | 说明 |
|---------|---------|------|
| 需求模糊、边界不清 | `brainstorming` | 不要在需求不明确时直接写代码 |
| 写完代码没有测试 | `test-driven-development` | 先写失败测试，再写实现 |
| Bug 反复出现、修了又坏 | `systematic-debugging` | 停止打补丁，构建根因链 |
| 准备说"完成了" | `verification-before-completion` | 完成前必须通过合规验证清单 |
| 代码审查走形式 | `requesting-code-review` | 使用 git SHA diff + 三级反馈机制 |
| 大量重复性任务 | `subagent-driven-development` | 考虑用子代理批量处理 |

### Spec 产出路径

Superpowers `brainstorming` 产出的 spec 文件写入 **`.trellis/tasks/`** 目录（而非 `docs/superpowers/specs/`），与 Trellis 任务状态机保持一致。

---

## 开发原则

### 开闭原则（Open-Closed Principle, OCP）
软件实体（类、模块、函数等）应该对扩展开放，对修改关闭。

- 新增功能时，优先通过**新增**代码实现，而非修改现有代码
- 使用接口/抽象类定义契约，通过继承或组合扩展行为
- 禁止为了新功能而修改已稳定的核心逻辑

### 职责单一原则（Single Responsibility Principle, SRP）
一个类/模块/函数应该只有一个引起它变化的原因。

- 每个模块只做一件事
- 路由层不包含业务逻辑
- 服务层不直接操作数据库
- 数据访问层不包含业务规则

### 测试驱动开发（TDD）
所有功能实现遵循红-绿-重构循环：

1. **红**：先写失败测试，描述期望行为
2. **绿**：写最少代码使测试通过
3. **重构**：优化代码结构，保持测试绿色

### 前后端分离原则
- 前端和后端是独立的代码库，各自有自己的依赖和构建流程
- 前端改动只能在 `frontend/` 目录
- 后端改动只能在 `backend/` 目录
- API 变更需同步更新两端，并更新 `.trellis/spec/` 中的 API 规范
- 禁止在一次实现中同时修改前后端代码

---

## 规范文件引用

实现任何功能前，必须先阅读以下规范文件（位于 `.trellis/spec/` 目录）：

- `.trellis/spec/` — 系统规范文档（架构、API、数据模型等）
- `.trellis/workflow.md` — 日常工作流程

**规范文件是 /implement 阶段的唯一信息来源**，不依赖对话记忆。

---

## 术语表引用

所有对话中必须使用 `CONTEXT.md` 中定义的统一术语。在编写代码注释、文档、API 命名时，严格遵循术语表。

参见：[CONTEXT.md](./CONTEXT.md)

---

## 后端分层架构

后端严格遵循四层架构，每层职责清晰：

| 层级 | 目录 | 职责 | 禁止事项 |
|------|------|------|---------|
| Routers | `routers/` | HTTP 请求/响应处理 | 不含业务逻辑 |
| Services | `services/` | 业务逻辑 | 不直接操作数据库 |
| Repositories | `repositories/` | 数据访问 | 不含业务逻辑 |
| Models | `models/` | 数据模型定义 | 不含任何逻辑 |

依赖方向：`routers -> services -> repositories -> models`

---

## AI 助手行为约定

1. **先读规范，再写代码**：每次开始工作前，先阅读相关规范文件
2. **使用统一术语**：全程使用 CONTEXT.md 中定义的术语
3. **遵循工作流**：严格按照 `.trellis/workflow.md` 中定义的流程工作
4. **前后端隔离**：不要在一次修改中同时触及前后端代码
5. **保持简洁**：遵循开闭原则和职责单一原则，不过度设计
6. **TDD 优先**：所有功能实现遵循红-绿-重构循环
7. **红旗意识**：遇到红旗信号时停下来使用对应技能，不要硬写
8. **完成前验证**：说"完成"之前必须通过 verification-before-completion 清单
