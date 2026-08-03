---
name: "closenai-code-review"
description: "ClosenAI-specific code review with two-phase mechanism: auto checks (OCP/SRP/layer/ isolation) + three-tier human review (🔴blocker 🟡suggestion 🟢praise). Invoke after implementation is complete, before verification."
---

# Code Review (代码审查)

> 层级：执行层
> 调用时机：在实现完成后调用，在 `closenai-verify` 之前

---

## 两阶段审查机制

### Phase 1 — 自动审查

1. **获取 git diff**：基于 git SHA 精确定位本次变更范围
   ```bash
   git diff <base-sha>..<head-sha> --stat
   git diff <base-sha>..<head-sha>
   ```

2. **规范符合性检查**：
   - [ ] **OCP（开闭原则）**：新增功能是否通过扩展而非修改实现
   - [ ] **SRP（职责单一原则）**：每个类/模块是否只有一个变化原因
   - [ ] **四层架构**：routers → services → repositories → models 依赖方向正确
   - [ ] **前后端隔离**：本次修改未同时触及 `frontend/` 和 `backend/`

3. **测试覆盖率检查**：
   - [ ] 新增功能有对应的测试用例
   - [ ] 边界条件已被测试覆盖
   - [ ] 测试全部通过

### Phase 2 — 人工审查（三级反馈）

| 级别 | 标识 | 含义 | 处理方式 |
|------|------|------|---------|
| 阻断级 | 🔴 | 安全漏洞、数据丢失风险、架构违规 | 必须修复才能合并 |
| 建议级 | 🟡 | 代码可读性、性能优化、命名规范 | 推荐修复但非阻断 |
| 赞美级 | 🟢 | 做得好的地方 | 鼓励良好实践 |

## 审查范围

- 代码逻辑正确性
- 错误处理完整性
- 安全性（SQL 注入、XSS、认证绕过等）
- 性能（N+1 查询、不必要的 LLM 调用等）
- 规范一致性（命名、目录结构、分层依赖）

## 与 TRAE-code-review 的区别

| 维度 | TRAE-code-review | closenai-code-review |
|------|-----------------|---------------------|
| 范围 | 通用代码审查 | ClosenAI 专项 |
| 架构检查 | 无 | OCP/SRP/四层架构/前后端隔离 |
| 反馈机制 | 标准反馈 | 🔴🟡🟢 三级反馈 |
| 测试检查 | 无 | 测试覆盖率 + 边界条件 |

两者互补：先用 `TRAE-code-review` 做通用审查，再用 `closenai-code-review` 做 ClosenAI 专项审查。

## 可调用的子技能

若发现架构问题，可调用 `closenai-improve-architecture` 做深度架构评估。

## 产出

- 审查报告（含三级反馈列表）
- 阻断级问题必须全部修复后才能进入 `closenai-verify`

## ⏸️ 完成后 → HILT Gate 2（REVIEW 审批）

审查通过后，**必须**调用 `closenai-hilt` Gate 2，向用户展示改动摘要和审查结果并等待审批：

- ✅ 用户通过 → 进入 `closenai-verify` 验收阶段
- ❌ 用户不通过 → 返回 `closenai-tdd` 修复或重构
- 📝 用户要看代码 → 展示关键代码片段

**禁止在 Gate 2 未通过时进入验收。**