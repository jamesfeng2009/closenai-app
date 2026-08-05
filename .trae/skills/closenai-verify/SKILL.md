---
name: "closenai-verify"
description: "Pre-completion verification checklist for ClosenAI: 5-dimension check (code quality, functionality, architecture, security, spec sync). Invoke BEFORE saying 'done' — must pass all checks."
---

# Verification Before Completion (完成前验证)

> 层级：执行层
> 调用时机：当准备说"完成了"时，必须先通过此验证清单

---

## 触发条件

当准备说"完成了"时，**必须先通过此验证清单**。

## 验证清单

### 代码质量
- [ ] 所有测试通过（`pytest backend/tests/` 或 `npm test`）
- [ ] 无 lint 错误（`ruff check backend/` 或 `npx eslint frontend/`）
- [ ] 无未处理的 TODO / FIXME 标记
- [ ] 无 console.log / print 调试语句残留

### 验证命令（必须实际运行，不接受"应该没问题"）

所有验证命令**必须真实执行并贴出结果**，禁止凭推断声称通过。本项目标准验证套件：

```bash
# 1. 测试套件（0 失败）
.venv/bin/python -m pytest backend/tests/ -q
# 2. 后端编译
python -m compileall -q backend
# 3. 前端类型（0 错误）
cd frontend && npx tsc --noEmit
# 4. schema 变更时：alembic 必须单头
cd backend && ../.venv/bin/python -m alembic heads
# 5. 关键模块 import 冒烟（本次改动涉及的模块）
.venv/bin/python -c "from backend.services import ...; print('ok')"
```

- [ ] 测试套件 0 失败
- [ ] compileall / tsc 通过
- [ ] schema 变更时 alembic 单头、迁移文件名已记录
- [ ] i18n key 变更时 15 个语言文件已同步（zh/en 正式翻译，小语种可用英文）

### 功能完整性
- [ ] 功能符合 brainstorming 产出的 spec 描述
- [ ] 与 PRD 中的验收标准交叉验证（对照 `closenai.html` 对应章节）
- [ ] 边界条件已处理（空状态、错误输入、极端情况）
- [ ] 错误消息对用户友好（不暴露技术细节）

### 文件位置
- [ ] 所有 `.py` 文件在 `backend/` 下（不允许在项目根目录）
- [ ] 所有 `.tsx`/`.ts` 文件在 `frontend/` 下
- [ ] 路由文件在 `backend/routers/`，服务文件在 `backend/services/`
- [ ] 页面组件在 `frontend/src/screens/`，可复用组件在 `frontend/src/components/`

### 架构合规
- [ ] **前后端隔离原则**未被破坏（未在一次修改中同时触及 `frontend/` 和 `backend/`）
- [ ] **OCP**：新增功能通过扩展实现，未修改已稳定的核心逻辑
- [ ] **SRP**：每个新增类/模块职责单一
- [ ] **四层架构**依赖方向正确（routers → services → repositories → models）

### 安全性
- [ ] 无 SQL 注入风险（使用参数化查询）
- [ ] 无 XSS 风险（前端输出转义）
- [ ] 认证检查到位（所有非公开端点有 `Depends(get_current_user)`）
- [ ] 敏感信息未硬编码（API Key、JWT Secret 等通过环境变量读取）

### ClosenAI 专项检查
- [ ] LLM 调用有 fallback（Rule Engine 模板兜底）
- [ ] 推送 token 在 logout 时清除
- [ ] IAP 收据经后端校验（非前端判断）
- [ ] 记忆注入不增加对话响应 > 500ms
- [ ] 免费用户配额在服务端校验（非前端写死）

### 规范同步
- [ ] 规范文件已同步更新（或确认无需更新）
- [ ] API 变更已更新相关文档
- [ ] 数据模型变更已更新相关 spec

## 可调用的子技能

| 检查项 | 调用技能 |
|--------|---------|
| 架构合规深度检查 | `closenai-improve-architecture` |
| 安全审查 | `TRAE-security-review` |
| 代码审查 | `closenai-code-review` |

## 结果

- 全部通过 → 进入 HILT Gate 3
- 有未通过项 → 返回修复，然后重新验证

## ⏸️ 完成后 → HILT Gate 3（DONE 审批）

验证全部通过后，**必须**调用 `closenai-hilt` Gate 3，向用户展示最终验证结果并等待审批：

- ✅ 用户通过 → 标记完成，可以提交
- ❌ 用户不通过 → 回到对应阶段修复
- 🔄 用户要先部署测试 → 帮助部署，然后回到 Gate 3

**禁止在 Gate 3 未通过时说"完成了"。**