# ClosenAI 日常工作流规范

本文档定义了 ClosenAI 项目的日常工作流程，所有开发工作必须遵循以下流程。

---

## 一、新功能开发流程

```
/trellis:start
    |
/grill-with-docs
    |
/trellis:before-dev
    |
/implement
    |
/trellis:check
    |
/trellis:finish-work
    |
/trellis:update-spec
```

### 各阶段说明

#### 1. /trellis:start
启动一个新的工作单元，创建工作上下文，明确本次工作的范围和目标。

#### 2. /grill-with-docs
与文档进行深度对话，澄清需求细节，确保对要实现的功能有完整理解。在此阶段应充分讨论技术方案、数据模型、API 设计等。

#### 3. /trellis:before-dev
开发前的准备工作：检查规范文件、确认上下文完整、规划实现步骤。

#### 4. /implement
执行实际编码实现。**每次 /implement 必须从全新上下文窗口开始**，确保不受之前上下文污染。

#### 5. /trellis:check
检查实现是否符合规范，运行测试，验证功能正确性。

#### 6. /trellis:finish-work
收尾工作：整理代码、更新文档、准备提交。

#### 7. /trellis:update-spec
更新规范文件，确保 `.trellis/spec/` 下的文档反映最新的系统状态。

---

## 二、Bug 修复流程

```
/diagnosing-bugs
    |
/trellis:start
    |
/implement
    |
/trellis:update-spec
```

### 各阶段说明

#### 1. /diagnosing-bugs
诊断 Bug 根因，收集复现步骤，分析问题来源。在此阶段应定位到具体的文件和代码行。

#### 2. /trellis:start
启动 Bug 修复工作单元，明确修复范围。

#### 3. /implement
执行修复实现。从全新上下文窗口开始。

#### 4. /trellis:update-spec
如果 Bug 涉及行为变更或发现规范缺失，更新规范文件。

---

## 三、上下文卫生规则

### grill-to-tickets 保持同一上下文
从 `/grill-with-docs` 到生成 tickets（任务卡）的过程中，必须在**同一上下文窗口**内完成。这确保了需求讨论的完整性和一致性，避免信息在上下文切换中丢失。

### 每个 /implement 从全新上下文开始
每次执行 `/implement` 时，必须**从全新的上下文窗口开始**。这样可以：
- 避免之前讨论的噪声干扰实现
- 确保实现只依赖规范文件，而非对话记忆
- 减少上下文窗口膨胀导致的性能下降

### 上下文隔离原则
| 阶段 | 上下文要求 |
|------|-----------|
| grill-with-docs 到 tickets | 同一上下文窗口 |
| 每个 /implement | 全新上下文窗口 |
| /trellis:check | 全新上下文窗口 |
| /trellis:update-spec | 全新上下文窗口 |

---

## 四、前后端分离规则

### 目录隔离
- **前端改动**：只能在 `frontend/` 目录下进行
- **后端改动**：只能在 `backend/` 目录下进行
- 禁止在一次 /implement 中同时修改前后端代码

### API 变更同步
当涉及 API 变更时，必须**同步更新两端**：
1. 先在 `backend/` 中定义/修改 API 接口
2. 在 `.trellis/spec/` 中更新 API 规范文档
3. 再在 `frontend/` 中更新对应的 API 调用代码

### 独立部署
- 后端和前端独立部署，互不依赖构建过程
- 后端 API 版本管理通过 URL 前缀（如 `/api/v1/`）实现
- 前端通过环境变量配置 API 地址

---

## 五、规范文件维护

所有规范文件存放在 `.trellis/spec/` 目录下。每次工作完成后，通过 `/trellis:update-spec` 确保规范文件与代码保持同步。

规范文件是 `/implement` 阶段的唯一信息来源，不依赖对话记忆。
