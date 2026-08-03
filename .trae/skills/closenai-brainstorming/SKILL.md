---
name: "closenai-brainstorming"
description: "Feature brainstorming with HARD-GATE mechanism for ClosenAI. Invoke when starting a new feature development, before any implementation. Calls grill-with-docs and domain-modeling as sub-tools."
---

# Brainstorming (功能头脑风暴)

> 层级：执行层
> 调用时机：当开始一个新功能开发时，在任何实现之前调用

---

## HARD-GATE 机制

**未通过以下检查项，不得进入开发阶段：**

- [ ] **功能边界已明确**：做什么 / 不做什么已书面记录
- [ ] **数据模型已定义**：涉及哪些表 / 字段 / 类型已设计
- [ ] **API 契约已设计**：请求 / 响应格式已定义
- [ ] **边界条件已识别**：空状态 / 错误处理 / 极端情况已列出
- [ ] **文件位置已规划**：每个新建/修改文件明确放在 `backend/` 或 `frontend/` 的哪个子目录
- [ ] **Spec 自审通过**：无遗漏场景，与现有 spec 无矛盾

## 执行流程

1. **读取现有 spec**：读取相关规范文件
2. **调用 `closenai-grill-with-docs`**：与 PRD/技术设计文档深度对话，澄清需求细节
3. **设计数据模型**：定义表结构、字段类型、索引（必要时调用 `closenai-domain-modeling`）
4. **设计 API 契约**：定义端点、请求体、响应体、错误码
5. **识别边界条件**：列出所有空状态、错误、极端情况
6. **Spec 自审**：检查是否有遗漏场景，与 PRD 和技术设计文档交叉验证

## PRD / 技术设计交叉验证

对于涉及核心业务逻辑的功能，必须与以下文档交叉验证：

| 文档 | 路径 | 用途 |
|------|------|------|
| PRD | `/Users/fengyu/Downloads/myproject/workspace/closenai-app/closenai-prd/zodiacsync-prd/closenai.html` | 功能描述和验收标准 |
| 技术设计 | `/Users/fengyu/Downloads/myproject/workspace/closenai-app/closenai-prd/zodiacsync-prd/closenai-tech-design.html` | 架构方案和数据模型 |
| 收费计划 | `/Users/fengyu/Downloads/myproject/workspace/closenai-app/closenai-prd/zodiacsync-prd/monetization-plan.md` | 配额和定价影响 |
| UI 原型 | `/Users/fengyu/Downloads/myproject/workspace/closenai-app/closenai-prd/ui-prototype/closenai-p0-prototype.html` | UI 交互参考 |

## 可调用的子技能

| 阶段 | 调用技能 | 用途 |
|------|---------|------|
| 需求澄清 | `closenai-grill-with-docs` | 与 spec 文档深度对话 |
| 复杂数据模型 | `closenai-domain-modeling` | Temporal Memory 等领域建模 |
| 数据库设计 | `closenai-supabase` | pgvector 表结构、索引设计 |
| 后端设计 | `closenai-backend` | FastAPI 路由、SSE、Cron Job |
| LLM 集成 | `closenai-llm` | Rule Engine、Prompt 设计 |
| 前端设计 | `closenai-frontend` | React Native 组件、主题 |
| 支付设计 | `closenai-iap` | IAP 订阅、收据校验 |

## 产出

- HARD-GATE 检查清单（全部通过）
- 数据模型定义
- API 契约定义
- 边界条件列表

## ⏸️ 完成后 → HILT Gate 1（SPEC 审批）

本阶段完成后，**必须**调用 `closenai-hilt` Gate 1，向用户展示方案并等待审批：

- ✅ 用户通过 → 进入 `closenai-tdd` 实现阶段
- ❌ 用户不通过 → 返回本阶段调整
- ⏸️ 用户暂停 → 等待用户指令

**禁止在 Gate 1 未通过时开始写代码。**