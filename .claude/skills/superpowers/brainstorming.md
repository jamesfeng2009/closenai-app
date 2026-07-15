# brainstorming (Superpowers)

> 层级：执行层（Superpowers）
> 替换：Trellis 原生 brainstorm
> 调用命令：`/brainstorming`

---

## 触发条件

当开始一个新功能开发时，在 `/trellis:start` 之后调用此技能。

## HARD-GATE 机制

未通过以下检查项，**不得进入开发阶段**：

- [ ] **功能边界已明确**：做什么 / 不做什么已书面记录
- [ ] **数据模型已定义**：涉及哪些表 / 字段 / 类型已设计
- [ ] **API 契约已设计**：请求 / 响应格式已定义（参见 `api-conventions.md`）
- [ ] **边界条件已识别**：空状态 / 错误处理 / 极端情况已列出
- [ ] **Spec 自审通过**：无遗漏场景，与现有 spec 无矛盾

## 执行流程

1. **读取现有 spec**：读取 `.trellis/spec/` 中与本功能相关的规范文件
2. **调用 /grill-with-docs**：与 spec 文档深度对话，澄清需求细节
3. **设计数据模型**：定义表结构、字段类型、索引（必要时调用 `/domain-modeling`）
4. **设计 API 契约**：定义端点、请求体、响应体、错误码
5. **识别边界条件**：列出所有空状态、错误、极端情况
6. **Spec 自审**：检查是否有遗漏场景，与 PRD 和技术设计文档交叉验证
7. **产出 spec 文件**：写入 `.trellis/tasks/<task-id>/` 目录

## Visual Companion

复杂流程（如 Temporal Memory 状态转换、多步骤 API 编排）应使用图示辅助讨论，确保所有参与者理解一致。

## PRD / 技术设计交叉验证

对于涉及核心业务逻辑的功能，必须与以下文档交叉验证：
- PRD：`closenai.html` 中的功能描述和验收标准
- 技术设计：`closenai-tech-design.html` 中的架构方案和数据模型

## 产出

- spec 文件（写入 `.trellis/tasks/<task-id>/`）
- HARD-GATE 检查清单（全部通过）
- 数据模型定义
- API 契约定义
