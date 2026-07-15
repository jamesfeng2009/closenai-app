# ClosenAI

个人成长追踪工具 — 通过反思记录追踪信念演变和成长轨迹。

> ClosenAI 不是 AI 聊天工具。它是一个帮助用户记录反思、追踪成长维度变化、识别信念演变的个人成长平台。

---

## 技术栈

### 后端
- Python 3.11+
- FastAPI（Web 框架）
- Pydantic v2（数据验证）
- SSE（流式响应）

### 前端
- React Native
- Expo
- TypeScript
- React Navigation（底部 Tab 导航）

---

## 目录结构

```
closenai-app/
|-- .trellis/                  # Trellis 工作流配置
|   |-- workflow.md            # 日常工作流规范
|   |-- workspace/             # 个人工作空间（git忽略）
|   |-- scripts/               # 辅助脚本
|   |   `-- setup-matt-pocock-skills.md
|   `-- spec/                  # 规范文件
|-- backend/                   # 后端（Python + FastAPI）
|   |-- routers/               # HTTP 路由层
|   |-- services/              # 业务逻辑层
|   |-- repositories/          # 数据访问层
|   |-- models/                # 数据模型
|   |-- config.py              # 配置管理
|   |-- main.py                # 应用入口
|   `-- requirements.txt       # Python 依赖
|-- frontend/                  # 前端（React Native + Expo）
|   |-- src/
|   |   |-- screens/           # 页面（Growth/Explore/Reflect/Profile）
|   |   |-- components/        # 通用组件
|   |   |-- services/          # API 调用
|   |   |-- theme/             # 主题（颜色等）
|   |   `-- types/             # 类型定义
|   |-- App.tsx                # 应用入口
|   |-- app.json               # Expo 配置
|   |-- package.json           # Node 依赖
|   `-- tsconfig.json          # TypeScript 配置
|-- CONTEXT.md                 # 领域术语表
|-- CLAUDE.md                  # AI 编码助手说明
|-- README.md                  # 本文件
`-- .gitignore
```

---

## 开发环境搭建

### 后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 启动开发服务器
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

### 前端

```bash
cd frontend
npm install

# 启动 Expo 开发服务器
npx expo start
```

---

## 开发工作流

本项目使用 Trellis 工作流管理开发过程。详见 [.trellis/workflow.md](.trellis/workflow.md)。

### 新功能开发流程

```
/trellis:start -> /grill-with-docs -> /trellis:before-dev -> /implement -> /trellis:check -> /trellis:finish-work -> /trellis:update-spec
```

### Bug 修复流程

```
/diagnosing-bugs -> /trellis:start -> /implement -> /trellis:update-spec
```

### 上下文卫生

- `/grill-with-docs` 到生成 tickets：保持同一上下文窗口
- 每个 `/implement`：从全新上下文窗口开始

### 前后端分离

- 前端改动只在 `frontend/` 目录
- 后端改动只在 `backend/` 目录
- API 变更需同步更新两端

---

## Trellis 使用说明

### 核心概念

- **spec/**：规范文件目录，是 `/implement` 阶段的唯一信息来源
- **workspace/**：个人工作空间，存放开发日志和临时文件（git 忽略）
- **workflow.md**：定义日常工作流程

### 常用命令

| 命令 | 用途 |
|------|------|
| `/trellis:start` | 启动新工作单元 |
| `/grill-with-docs` | 与文档深度对话，澄清需求 |
| `/trellis:before-dev` | 开发前准备检查 |
| `/implement` | 执行编码实现（全新上下文） |
| `/trellis:check` | 检查实现质量 |
| `/trellis:finish-work` | 收尾工作 |
| `/trellis:update-spec` | 更新规范文件 |
| `/diagnosing-bugs` | 诊断 Bug 根因 |

### AI 编码助手配置

详见 [CLAUDE.md](CLAUDE.md)。

### 领域术语

详见 [CONTEXT.md](CONTEXT.md)，所有开发对话中使用统一术语。

---

## 许可证

私有项目，未授权。
