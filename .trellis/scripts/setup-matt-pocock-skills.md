# Matt Pocock Skills 安装指南

本指南介绍如何安装 Matt Pocock 的 TypeScript/React 技能集，并说明其与 Trellis 工作流的配合方式。

---

## 安装命令

使用 Claude Code 的 skill 安装命令：

```bash
# 安装 Matt Pocock 技能集
claude skill install matt-pocock-skills
```

或者通过 npx：

```bash
npx @anthropic/matt-pocock-skills install
```

安装后，技能会注册到 Claude Code 的技能系统中，可在对话中通过 `/skill` 命令调用。

---

## 推荐的 6 个核心技能

### 1. TypeScript Best Practices
- **用途**：强制 TypeScript 最佳实践，包括类型安全、泛型使用、类型推断优化
- **调用方式**：在 `/implement` 阶段自动激活
- **适用场景**：前端 TypeScript 代码编写

### 2. React Component Design
- **用途**：指导 React 组件设计，包括组件拆分、Props 设计、状态管理
- **调用方式**：手动 `/skill react-component-design`
- **适用场景**：创建新的 React Native 组件

### 3. API Type Safety
- **用途**：确保前后端 API 类型安全，自动生成 TypeScript 类型定义
- **调用方式**：在 API 变更时手动调用
- **适用场景**：前后端接口对齐

### 4. Error Handling Patterns
- **用途**：标准化错误处理模式，包括错误边界、异步错误捕获、用户友好提示
- **调用方式**：手动 `/skill error-handling`
- **适用场景**：实现错误处理逻辑

### 5. Testing Strategies
- **用途**：指导测试策略，包括单元测试、集成测试、组件测试的编写规范
- **调用方式**：在 `/trellis:check` 阶段手动调用
- **适用场景**：编写和审查测试代码

### 6. Performance Optimization
- **用途**：识别和优化性能瓶颈，包括 React 渲染优化、列表虚拟化、图片懒加载
- **调用方式**：手动 `/skill performance`
- **适用场景**：性能优化和代码审查

---

## 与 Trellis 的配合方式

Matt Pocock Skills 与 Trellis 工作流互补使用：

| Trellis 阶段 | 推荐使用的 Skill | 说明 |
|-------------|-----------------|------|
| /grill-with-docs | — | 需求讨论阶段，不涉及编码 |
| /trellis:before-dev | TypeScript Best Practices | 检查类型安全规范 |
| /implement | React Component Design, API Type Safety | 编码时自动激活或手动调用 |
| /trellis:check | Testing Strategies, Error Handling Patterns | 审查测试和错误处理 |
| /trellis:finish-work | Performance Optimization | 最终性能审查 |
| /trellis:update-spec | — | 更新规范文件，不涉及编码 |

### 配合原则

1. **Trellis 管流程，Skills 管质量**：Trellis 定义工作流程和阶段，Matt Pocock Skills 在各阶段提供编码质量保障
2. **不冲突**：Skills 的建议应与 `.trellis/spec/` 中的规范一致，如有冲突以 spec 为准
3. **可选使用**：Skills 是辅助工具，不是强制步骤。根据实际需要选择使用
4. **上下文独立**：Skills 的使用不依赖之前的对话上下文，与 Trellis 的上下文卫生规则一致

---

## 验证安装

安装完成后，运行以下命令验证：

```bash
claude skill list
```

确认上述 6 个技能已出现在列表中。如未出现，检查安装日志或重新安装。
