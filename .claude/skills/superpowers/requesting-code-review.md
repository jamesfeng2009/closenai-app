# requesting-code-review (Superpowers)

> 层级：执行层（Superpowers）
> 替换：Trellis 原生 check
> 调用命令：`/requesting-code-review`

---

## 触发条件

在 `/implement` 完成后调用此技能，进行代码审查。

## 两阶段审查机制

### Phase 1 — 自动审查

1. **获取 git diff**：基于 git SHA 精确定位本次变更范围
   ```bash
   git diff <base-sha>..<head-sha> --stat
   git diff <base-sha>..<head-sha>
   ```
2. **规范符合性检查**：
   - [ ] OCP（开闭原则）：新增功能是否通过扩展而非修改实现
   - [ ] SRP（职责单一原则）：每个类/模块是否只有一个变化原因
   - [ ] 四层架构：routers → services → repositories → models 依赖方向正确
   - [ ] 前后端隔离：本次修改未同时触及 frontend/ 和 backend/
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

## 产出

- 审查报告（含三级反馈列表）
- 阻断级问题必须全部修复后才能进入 `/verification-before-completion`
