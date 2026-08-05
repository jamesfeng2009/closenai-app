---
name: "closenai-subagent-dev"
description: "Parallel subagent-driven development for ClosenAI: decompose large tasks, dispatch to subagents, collect and verify results. Invoke when facing 5+ independent sub-tasks like batch API endpoint testing or code skeleton generation."
---

# Subagent-Driven Development (子代理驱动开发)

> 层级：执行层
> 调用时机：按需调用，适用于大量重复性任务

---

## 触发条件

当存在以下场景时考虑使用：
- 批量为多个 task 生成初始代码骨架
- 批量执行 API 端点的 TDD 测试编写
- 批量检查所有 spec 文件与代码的一致性
- 任何可分解为 5+ 个独立子任务的工作

## 执行流程

### 1. 任务分解

将大任务分解为独立的子任务，每个子任务：
- 有明确的输入和输出
- 不依赖其他子任务的执行结果
- 可独立验证成功/失败

```
主任务：为所有 P0 API 端点编写集成测试
├── 子任务 1：P0-F01 chat API 测试
├── 子任务 2：P0-F02 memories API 测试
├── 子任务 3：P0-F03 mood-checkins API 测试
├── 子任务 4：P0-F04 reflections API 测试
├── 子任务 5：P0-F05 growth API 测试
├── 子任务 6：P0-F06 personality API 测试
└── 子任务 7：P0-F07 growth-records API 测试
```

### 2. 子代理调度

使用 `Agent` 工具为每个子任务启动一个子代理（subagent），提供：
- 任务描述（明确的目标和输出格式）
- 相关 spec 文件路径（作为唯一信息源）
- 可调用的领域技能（如 `closenai-backend`、`closenai-supabase`、`closenai-tdd`）
- 验证标准（如何判断任务完成）

#### 子任务描述五要素（强制）

每个子任务描述**必须**包含以下五项，缺一项不派发：

1. **必读清单**：完成任务必须读取的文件/文档路径（spec、prd、现有实现）。新旧实现并存（如 token.ts vs token_legacy.ts 式陷阱）时必须明确指定当前版本路径，禁止子代理自行甄别
2. **禁区声明**：禁止改动的文件或文件区域。并行派发时即使同文件不冲突，也要按方法/行段划清各自区域（先例：message_generator.py 按方法分区）
3. **项目约定**：事务单点 commit/rollback、LLM 失败降级、文案中英双表等本场任务涉及的工程规则（详见 project_memory「工程规则」）
4. **验证命令**：必须实际运行的命令（pytest/tsc/compileall/import 冒烟），不接受"应该没问题"
5. **报告格式**：每个实现项的文件+行号+改动摘要（evidence_ref）；schema 变更必须报告 alembic 迁移文件名

### 3. 结果收集与验证

- 收集所有子代理的产出
- 逐个验证是否符合 spec 和验证标准
- 合并结果，处理冲突（如多个子代理修改了同一文件）

#### 收集时强制动作

1. **证据抽查**：不接受只有结论的摘要。从每个子代理报告中抽 1-2 处行号 Read 复核实际代码
2. **交叉污染检查**：并行任务完成后，Grep 检查各禁区是否被其他任务触碰（先例：A 任务在 cron_service.py 写的代码缺 import，由 B 任务发现并报告）
3. **全量验证**：所有子代理完成后跑标准验证套件（见 closenai-verify 的验证命令清单），而非仅各子代理自报验证

### 4. 适用限制

| 适用场景 | 不适用场景 |
|---------|-----------|
| 独立模块的代码骨架生成 | 有复杂依赖关系的核心逻辑 |
| 批量测试用例编写 | 需要深度讨论的设计决策 |
| 批量文件检查/转换 | 涉及安全敏感的代码 |
| 文档批量生成 | 需要人工审查的架构变更 |

## 产出

- 子任务执行报告（每个子代理的成功/失败状态）
- 合并后的最终产出
- 验证结果清单