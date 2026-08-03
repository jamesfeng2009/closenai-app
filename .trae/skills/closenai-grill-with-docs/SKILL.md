---
name: "closenai-grill-with-docs"
description: "Deep dialogue with ClosenAI spec/PRD documents to clarify requirements and find contradictions. Invoke within brainstorming phase when new feature needs cross-validation with existing specs."
---

# Grill with Docs (与文档深度对话)

> 层级：诊断层
> 调用时机：在 `closenai-brainstorming` 阶段内调用

---

## 触发条件

在 brainstorming 阶段需要与规范文档深度对话时调用。

## 执行方式

### 1. 确定目标文档

根据当前功能，确定需要对话的文档：

| 场景 | 需要对照的文档 |
|------|--------------|
| 新功能 | 架构方案 + API 规范 + 对应业务 spec |
| 数据模型 | 技术设计中的数据模型章节 + `closenai-supabase` |
| UI 功能 | UI 原型 + 设计系统 |
| 成本相关 | 收费计划 + 成本优化文档 |
| 支付/IAP | IAP 清单 + 收费计划 + `closenai-iap` |

关键文档路径：
- PRD: `/Users/fengyu/Downloads/myproject/workspace/closenai-app/closenai-prd/zodiacsync-prd/closenai.html`
- 技术设计: `/Users/fengyu/Downloads/myproject/workspace/closenai-app/closenai-prd/zodiacsync-prd/closenai-tech-design.html`
- 收费计划: `/Users/fengyu/Downloads/myproject/workspace/closenai-app/closenai-prd/zodiacsync-prd/monetization-plan.md`
- IAP 清单: `/Users/fengyu/Downloads/myproject/workspace/closenai-app/closenai-prd/zodiacsync-prd/app-store-iap-checklist.md`
- UI 原型: `/Users/fengyu/Downloads/myproject/workspace/closenai-app/closenai-prd/ui-prototype/closenai-p0-prototype.html`

### 2. 逐文档提问

对每个文档提出以下问题：
- 这个功能与现有架构是否一致？
- 现有的数据模型能否支撑这个功能？
- API 契约是否需要新增或修改？
- 有没有边界条件需要额外处理？
- 这个功能会影响成本吗？

### 3. 记录矛盾点

如果发现新功能与现有 spec 有矛盾，记录并在 brainstorming 阶段解决。

### 4. 与 PRD 交叉验证

对照 `closenai.html` 中的功能描述，确保理解一致。

## 产出

- 需求澄清记录（矛盾点、决策、补充细节）
- 供 brainstorming 的 HARD-GATE 检查使用