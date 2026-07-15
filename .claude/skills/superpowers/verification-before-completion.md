# verification-before-completion (Superpowers)

> 层级：执行层（Superpowers）
> 调用命令：`/verification-before-completion`

---

## 触发条件

当准备说"完成了"时，必须先通过此验证清单。

## 验证清单

### 代码质量
- [ ] 所有测试通过（`pytest backend/tests/` 或 `npm test`）
- [ ] 无 lint 错误（`ruff check backend/` 或 `npx eslint frontend/`）
- [ ] 无未处理的 TODO / FIXME 标记
- [ ] 无 console.log / print 调试语句残留

### 功能完整性
- [ ] 功能符合 spec 描述（对照 `.trellis/tasks/<task-id>/prd.md`）
- [ ] 与 PRD 中的验收标准交叉验证（对照 `closenai.html` 对应章节）
- [ ] 边界条件已处理（空状态、错误输入、极端情况）
- [ ] 错误消息对用户友好（不暴露技术细节）

### 架构合规
- [ ] 前后端隔离原则未被破坏（未在一次修改中同时触及 frontend/ 和 backend/）
- [ ] OCP：新增功能通过扩展实现，未修改已稳定的核心逻辑
- [ ] SRP：每个新增类/模块职责单一
- [ ] 四层架构依赖方向正确（routers → services → repositories → models）

### 安全性
- [ ] 无 SQL 注入风险（使用参数化查询）
- [ ] 无 XSS 风险（前端输出转义）
- [ ] 认证检查到位（所有非公开端点有 `Depends(get_current_user)`）
- [ ] 敏感信息未硬编码（API Key、JWT Secret 等通过环境变量读取）

### 规范同步
- [ ] 规范文件已同步更新（或确认无需更新）
- [ ] API 变更已更新 `api-conventions.md`
- [ ] 数据模型变更已更新相关 spec

## 结果

- 全部通过 → 进入 `/trellis:finish-work`
- 有未通过项 → 返回 `/implement` 修复，然后重新验证
