---
name: "closenai-domain-modeling"
description: "Domain modeling for complex business entities in ClosenAI: Temporal Memory, Growth Metrics, Personality Profile. Invoke during brainstorming when designing data models with 3+ entity relationships."
---

# Domain Modeling (领域建模)

> 层级：诊断层
> 调用时机：在 brainstorming 阶段设计复杂数据模型时按需调用

---

## 触发条件

当需要为复杂业务领域建立类型模型时调用：
- **Temporal Memory** 数据模型设计（信念演变追踪）
- **成长指标体系** 建模（多维度成长追踪）
- **性格画像** 数据模型（多版本快照）
- 任何涉及 3 个以上实体关联的领域建模

## 执行流程

### 1. 识别领域实体

从 PRD 和技术设计文档中提取核心业务概念：

| 来源 | 提取方法 |
|------|---------|
| PRD | 提取功能描述中的名词 |
| 技术设计 | 提取数据模型章节中的表名和字段 |
| 现有 spec | 检查已有定义 |

参考文档路径：
- PRD: `/Users/fengyu/Downloads/myproject/workspace/closenai-app/closenai-prd/zodiacsync-prd/closenai.html`
- 技术设计: `/Users/fengyu/Downloads/myproject/workspace/closenai-app/closenai-prd/zodiacsync-prd/closenai-tech-design.html`

### 2. 定义类型关系

使用 TypeScript 接口描述领域模型（前端后端共享）：

```typescript
// 领域模型示例：Temporal Memory
interface Memory {
  id: string;
  user_id: string;
  category: 'values' | 'relationship' | 'goal' | 'preference' | 'factual';
  content: string;
  status: 'current' | 'superseded' | 'reverted';
  conviction_score: number;  // 0.0 - 1.0
  source: 'self_reflection' | 'ai_inference' | 'quoted_other';
  embedding: number[];       // 512-dim halfvec (DashScope text-embedding-v4, MRL 截断)
  created_at: Date;
  superseded_at: Date | null;
  reverted_at: Date | null;
}

interface MemoryTransition {
  id: string;
  from_memory_id: string;
  to_memory_id: string;
  transition_type: 'gradual' | 'sudden' | 'revert';
  detected_at: Date;
  evidence: string;
}
```

### 3. 验证模型完整性

- [ ] 所有 PRD 中提到的业务概念都有对应的类型定义
- [ ] 类型之间的关系（一对一、一对多、多对多）已明确
- [ ] 枚举值与 spec 中定义的一致
- [ ] 字段类型与数据库 schema 一致

也可调用 `closenai-supabase` 技能交叉验证数据库 schema。

### 4. 与 spec 同步

领域模型产出后，更新到对应的 spec 文件：
- Temporal Memory → memory-layer 相关文档
- 成长指标 → architecture 相关文档
- 前端类型定义 → `frontend/src/types/`

## 产出

- TypeScript 类型定义文件
- 实体关系说明（文字或图示）
- 与 spec 的同步检查清单