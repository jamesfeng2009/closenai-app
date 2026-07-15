# grill-with-docs (Matt Pocock)

> 层级：诊断层（Matt Pocock）
> 调用命令：`/grill-with-docs`
> 调用时机：在 Superpowers `brainstorming` 阶段内调用

---

## 触发条件

在 `brainstorming` 阶段需要与规范文档深度对话时调用。

## 执行方式

1. **确定目标文档**：根据当前功能，确定需要对话的 spec 文件：
   - 新功能 → `architecture.md` + `api-conventions.md` + 对应业务 spec
   - 数据模型 → `memory-layer.md` + `frontend-sqlite-schema.md`
   - UI 功能 → `ui-design-system.md` + `app-startup-timing.md`
   - 成本相关 → `cost-optimization.md`
   - 域名/部署 → `domain-configuration.md` + `architecture.md`

2. **逐文档提问**：对每个 spec 文件提出以下问题：
   - 这个功能与现有架构是否一致？
   - 现有的数据模型能否支撑这个功能？
   - API 契约是否需要新增或修改？
   - 有没有边界条件需要额外处理？
   - 这个功能会影响成本吗？

3. **记录矛盾点**：如果发现新功能与现有 spec 有矛盾，记录并在 brainstorming 阶段解决。

4. **与 PRD 交叉验证**：对照 `closenai.html` 中的功能描述，确保理解一致。

## 产出

- 需求澄清记录（矛盾点、决策、补充细节）
- 供 brainstorming 的 HARD-GATE 检查使用
