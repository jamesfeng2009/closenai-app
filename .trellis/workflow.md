# ClosenAI 日常工作流规范

本文档定义了 ClosenAI 项目的日常工作流程，所有开发工作必须遵循以下流程。

---

## 〇、技能框架组合

本项目采用三层技能框架组合，各层职责清晰、互不冲突：

```
Trellis（框架层 — 项目骨架）
├── SessionStart hook        ← 保留：注入项目状态 + spec 引用
├── 任务状态机                ← start / before-dev / finish-work / update-spec
├── 记忆层 + 多平台配置
│
├── Superpowers（执行层 + 设计层 — 强化纪律）
│   ├── brainstorming              ← 替换 Trellis brainstorm（HARD-GATE + Visual Companion + Spec 自审）
│   ├── requesting-code-review     ← 替换 Trellis check（git SHA diff + 三级反馈 + 两阶段审查）
│   ├── test-driven-development    ← 新增：TDD 纪律（红-绿-重构）
│   ├── systematic-debugging       ← 新增：深度调试（根因链 + 假设验证）
│   ├── verification-before-completion ← 新增：完成前合规验证
│   └── subagent-driven-development    ← 新增：无人值守批量任务（按需）
│
└── Matt Pocock（诊断层 — 精准工具）
    ├── grill-with-docs            ← 与文档深度对话（brainstorming 阶段内调用）
    ├── diagnosing-bugs            ← Bug 根因定位（systematic-debugging 阶段内调用）
    ├── improve-codebase-architecture
    └── domain-modeling
```

### 冲突解决原则

| 冲突点 | 采用方案 | 原因 |
|--------|---------|------|
| Brainstorming | **Superpowers** | HARD-GATE 确保需求充分讨论；Visual Companion 可视化辅助；Spec 自审减少遗漏 |
| SessionStart Hook | **Trellis** | Superpowers Memory Layer 会注入大量历史信息，消耗 token 预算；Trellis 仅注入项目状态 + spec 引用，更轻量 |
| Code Review | **Superpowers** | 基于 git SHA diff 精确定位变更；三级反馈（阻断/建议/赞美）；两阶段审查（自动 + 人工）更系统化 |

### Spec 产出路径

Superpowers `brainstorming` 产出的 spec 文件写入 **`.trellis/tasks/`** 目录（而非 `docs/superpowers/specs/`），与 Trellis 任务状态机保持一致。

---

## 一、新功能开发流程

```
/trellis:start
    |
/brainstorming (Superpowers)
    ├── /grill-with-docs (Matt Pocock)  ← 阶段内调用，与文档深度对话
    └── 产出 spec → 写入 .trellis/tasks/
    |
/trellis:before-dev
    |
/implement (TDD: 红 → 绿 → 重构)
    |
/requesting-code-review (Superpowers)
    ├── Phase 1: 自动审查（git SHA diff + 规范检查）
    └── Phase 2: 人工审查（三级反馈）
    |
/verification-before-completion (Superpowers)
    |
/trellis:finish-work
    |
/trellis:update-spec
```

### 各阶段说明

#### 1. /trellis:start
启动一个新的工作单元，创建工作上下文，明确本次工作的范围和目标。SessionStart hook 自动注入项目状态和 spec 引用。

#### 2. /brainstorming (Superpowers)
**替换 Trellis 原生 brainstorm**。采用 HARD-GATE 机制，确保需求在进入开发前被充分讨论：

- **HARD-GATE**：未通过以下检查不得进入开发阶段
  - [ ] 功能边界已明确（做什么 / 不做什么）
  - [ ] 数据模型已定义（涉及哪些表 / 字段）
  - [ ] API 契约已设计（请求 / 响应格式）
  - [ ] 边界条件已识别（空状态 / 错误 / 极端情况）
  - [ ] Spec 自审通过（无遗漏场景）
- **Visual Companion**：复杂流程用图示辅助讨论
- **阶段内调用 /grill-with-docs**：与 `.trellis/spec/` 中的现有规范深度对话，确保新功能与架构一致
- **产出**：spec 文件写入 `.trellis/tasks/` 目录

#### 3. /trellis:before-dev
开发前的准备工作：检查规范文件、确认上下文完整、规划实现步骤。

#### 4. /implement (TDD)
执行实际编码实现，遵循 **测试驱动开发** 纪律：

1. **红**：先写失败测试（描述期望行为）
2. **绿**：写最少代码使测试通过
3. **重构**：优化代码结构，保持测试绿色

**每次 /implement 必须从全新上下文窗口开始**，确保不受之前上下文污染。

#### 5. /requesting-code-review (Superpowers)
**替换 Trellis 原生 check**。采用两阶段审查机制：

- **Phase 1 — 自动审查**：
  - 基于 git SHA diff 精确定位本次变更
  - 检查规范符合性（OCP / SRP / 分层架构）
  - 检查前后端隔离原则
  - 检查测试覆盖率
- **Phase 2 — 人工审查（三级反馈）**：
  - 🔴 **阻断级**：必须修复才能合并（安全漏洞、数据丢失风险、架构违规）
  - 🟡 **建议级**：推荐修复但非阻断（代码可读性、性能优化、命名规范）
  - 🟢 **赞美级**：做得好的地方（鼓励良好实践）

#### 6. /verification-before-completion (Superpowers)
完成前合规验证，确保交付质量：

- [ ] 所有测试通过
- [ ] 无 lint 错误
- [ ] 功能符合 spec 描述
- [ ] 无未处理的 TODO / FIXME
- [ ] 前后端隔离原则未被破坏
- [ ] 规范文件已同步更新（或确认无需更新）

#### 7. /trellis:finish-work
收尾工作：整理代码、更新文档、准备提交。

#### 8. /trellis:update-spec
更新规范文件，确保 `.trellis/spec/` 下的文档反映最新的系统状态。

---

## 二、Bug 修复流程

```
/systematic-debugging (Superpowers)
    ├── /diagnosing-bugs (Matt Pocock)  ← 阶段内调用，精准定位根因
    └── 产出根因分析报告
    |
/trellis:start
    |
/implement (TDD: 红 → 绿 → 重构)
    |
/requesting-code-review (Superpowers)
    |
/verification-before-completion (Superpowers)
    |
/trellis:update-spec
```

### 各阶段说明

#### 1. /systematic-debugging (Superpowers)
**替换单一 /diagnosing-bugs**，采用系统化调试方法：

- **根因链构建**：从症状出发，逐层追溯根因（不停留在表面症状）
- **假设验证**：对每个可能的根因提出假设，设计验证方案
- **最小复现**：找到最小复现步骤，确保修复后可验证
- **阶段内调用 /diagnosing-bugs**：利用 Matt Pocock 的精准诊断工具定位具体文件和代码行
- **产出**：根因分析报告（症状 → 根因 → 修复方案 → 验证方案）

#### 2. /trellis:start
启动 Bug 修复工作单元，明确修复范围。

#### 3. /implement (TDD)
执行修复实现，先写回归测试（复现 Bug），再修复代码。从全新上下文窗口开始。

#### 4. /requesting-code-review (Superpowers)
同新功能流程的代码审查。

#### 5. /verification-before-completion (Superpowers)
同新功能流程的合规验证。

#### 6. /trellis:update-spec
如果 Bug 涉及行为变更或发现规范缺失，更新规范文件。

---

## 三、上下文卫生规则

### brainstorming 到 tickets 保持同一上下文
从 `/brainstorming` 到生成 tickets（任务卡）的过程中，必须在**同一上下文窗口**内完成。这确保了需求讨论的完整性和一致性，避免信息在上下文切换中丢失。`/grill-with-docs` 作为 brainstorming 的子步骤在同一上下文内调用。

### 每个 /implement 从全新上下文开始
每次执行 `/implement` 时，必须**从全新的上下文窗口开始**。这样可以：
- 避免之前讨论的噪声干扰实现
- 确保实现只依赖规范文件，而非对话记忆
- 减少上下文窗口膨胀导致的性能下降

### 上下文隔离原则
| 阶段 | 上下文要求 |
|------|-----------|
| brainstorming 到 tickets | 同一上下文窗口 |
| 每个 /implement | 全新上下文窗口 |
| /requesting-code-review | 全新上下文窗口 |
| /verification-before-completion | 全新上下文窗口 |
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

---

## 六、技能调用速查

| 场景 | 调用的技能 | 所属层 |
|------|-----------|--------|
| 启动新工作单元 | `/trellis:start` | Trellis |
| 需求讨论与澄清 | `/brainstorming` + `/grill-with-docs` | Superpowers + Matt Pocock |
| 开发前准备 | `/trellis:before-dev` | Trellis |
| 编码实现 | `/implement`（TDD 纪律） | Superpowers TDD |
| 代码审查 | `/requesting-code-review` | Superpowers |
| 完成前验证 | `/verification-before-completion` | Superpowers |
| 收尾工作 | `/trellis:finish-work` | Trellis |
| 更新规范 | `/trellis:update-spec` | Trellis |
| Bug 调试 | `/systematic-debugging` + `/diagnosing-bugs` | Superpowers + Matt Pocock |
| 架构改进 | `/improve-codebase-architecture` | Matt Pocock |
| 领域建模 | `/domain-modeling` | Matt Pocock |
| 批量任务 | `/subagent-driven-development` | Superpowers（按需） |
