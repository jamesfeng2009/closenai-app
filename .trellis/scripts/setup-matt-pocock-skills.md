# Matt Pocock Skills 诊断层说明

本文件说明 Matt Pocock 技能集在本项目三层技能框架中的定位和使用方式。

> **三层框架**：Trellis（框架层）+ Superpowers（执行层）+ Matt Pocock（诊断层）
> 详见 `workflow.md` §〇 技能框架组合

---

## 1. 定位：诊断层

Matt Pocock skills 在三层框架中属于**诊断层**，作为 Superpowers 执行层的**精准工具**被调用：

```
Superpowers 执行层
├── brainstorming      ← 阶段内调用 grill-with-docs
├── systematic-debugging ← 阶段内调用 diagnosing-bugs
└── TDD / code-review  ← 可调用 improve-codebase-architecture / domain-modeling
```

**核心原则**：Matt Pocock skills 不是独立步骤，而是 Superpowers 各阶段内的子工具。

---

## 2. 四个核心技能

### 2.1 grill-with-docs（与文档深度对话）

- **用途**：与 `.trellis/spec/` 中的规范文件深度对话，澄清需求细节
- **调用时机**：在 Superpowers `brainstorming` 阶段内调用
- **调用方式**：`/grill-with-docs`（在 brainstorming 同一上下文窗口内）
- **适用场景**：新功能开发时，确认数据模型、API 设计、边界条件与现有 spec 的一致性

### 2.2 diagnosing-bugs（Bug 根因定位）

- **用途**：精准定位 Bug 到具体文件和代码行
- **调用时机**：在 Superpowers `systematic-debugging` 阶段内调用
- **调用方式**：`/diagnosing-bugs`（在 systematic-debugging 同一上下文窗口内）
- **适用场景**：Bug 修复时，在构建根因链后用此工具精确定位代码位置

### 2.3 improve-codebase-architecture（架构改进）

- **用途**：评估和改进现有代码架构，识别违反 OCP/SRP 的代码
- **调用时机**：按需手动调用，通常在代码审查发现架构问题时
- **调用方式**：`/improve-codebase-architecture`
- **适用场景**：重构现有模块、评估分层架构合规性

### 2.4 domain-modeling（领域建模）

- **用途**：为复杂业务领域建立类型模型，确保领域概念在代码中正确表达
- **调用时机**：按需手动调用，通常在 brainstorming 阶段设计复杂数据模型时
- **调用方式**：`/domain-modeling`
- **适用场景**：Temporal Memory 数据模型设计、成长指标体系建模

---

## 3. 与 Superpowers 的配合关系

| Superpowers 阶段 | 调用的 Matt Pocock 技能 | 说明 |
|------------------|----------------------|------|
| `brainstorming` | `grill-with-docs` | 与 spec 文档深度对话，确保需求与架构一致 |
| `brainstorming`（复杂数据模型时） | `domain-modeling` | 为 Temporal Memory 等复杂领域建立类型模型 |
| `systematic-debugging` | `diagnosing-bugs` | 在根因链构建后，精准定位代码位置 |
| `requesting-code-review`（发现架构问题时） | `improve-codebase-architecture` | 评估架构违规，提出重构建议 |

### 配合原则

1. **诊断层是子工具，不是独立步骤**：Matt Pocock skills 始终在 Superpowers 阶段内被调用，不独立成步骤
2. **不与 Superpowers 冲突**：如遇冲突，以 Superpowers 的 HARD-GATE / TDD / 系统化流程为准
3. **可选使用**：根据实际需要选择调用，不是每个步骤都必须调用
4. **上下文一致**：在 Superpowers 阶段的同一上下文窗口内调用，不切换上下文

---

## 4. 安装方式

Matt Pocock skills 来源于 GitHub 仓库 [`mattpocock/skills`](https://github.com/mattpocock/skills)。

### 4.1 Claude Code 安装

```bash
# 克隆仓库到本地
git clone https://github.com/mattpocock/skills.git ~/skills/matt-pocock

# 在 Claude Code 中引用
# 在项目根目录的 .claude/settings.json 中添加 skills 路径
```

### 4.2 其他 AI 编码助手

将 `~/skills/matt-pocock` 中的 skill 文件复制到对应 AI 助手的 skills 目录中，或通过 `CLAUDE.md` 中的技能框架组合章节引用。

### 4.3 验证

安装后，在对话中尝试调用 `/grill-with-docs` 或 `/diagnosing-bugs`，确认技能可用。

---

## 5. 速查表

| 场景 | 调用命令 | 所属层 | 在哪个 Superpowers 阶段 |
|------|---------|--------|----------------------|
| 与 spec 文档对话 | `/grill-with-docs` | Matt Pocock | brainstorming |
| Bug 根因定位 | `/diagnosing-bugs` | Matt Pocock | systematic-debugging |
| 架构评估 | `/improve-codebase-architecture` | Matt Pocock | 按需 |
| 领域建模 | `/domain-modeling` | Matt Pocock | brainstorming（复杂数据模型时） |
