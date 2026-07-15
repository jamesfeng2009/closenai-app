# improve-codebase-architecture (Matt Pocock)

> 层级：诊断层（Matt Pocock）
> 调用命令：`/improve-codebase-architecture`
> 调用时机：按需手动调用，通常在代码审查发现架构问题时

---

## 触发条件

当代码审查中发现以下架构问题时调用：
- 违反开闭原则（OCP）：为新增功能修改了已稳定的核心逻辑
- 违反职责单一原则（SRP）：一个类/模块承担了多个职责
- 分层架构违规：路由层包含业务逻辑，或服务层直接操作数据库
- 代码重复：多个模块包含相同或高度相似的逻辑
- 依赖方向错误：repositories 调用 services，或 services 调用 routers

## 执行流程

### 1. 架构扫描

逐模块检查架构合规性：

| 检查项 | 检查方法 | 违规标志 |
|--------|---------|---------|
| OCP | 新增功能是否修改了已有类？ | `git diff` 中修改了非新建文件的核心逻辑 |
| SRP | 一个类是否有多个变化原因？ | 类名包含"And"或方法跨多个业务领域 |
| 分层依赖 | 依赖方向是否正确？ | `routers/` 直接 import `models/`，跳过 services |
| 前后端隔离 | 一次修改是否触及两端？ | 同一 commit 中同时有 `frontend/` 和 `backend/` 改动 |

### 2. 识别扩展点

基于 OCP，识别应抽象为接口/抽象类的扩展点：

```python
# 扩展示例：新增 LLM Provider 时不应修改 LLMService
# 正确做法：实现 LLMProvider 接口
class NewProvider(LLMProvider):
    async def chat(self, model, messages, **kwargs):
        ...
# 然后在依赖注入中注册，不修改 LLMService 代码
```

### 3. 重构建议

- **提取接口**：将具体类依赖改为接口依赖
- **策略模式**：将条件分支改为策略类注册
- **依赖注入**：通过 DI 容器管理生命周期
- **提取超类**：将公共逻辑提取到 BaseRepository / BaseService

### 4. 与 spec 交叉验证

重构建议必须与 `.trellis/spec/code-architecture.md` 中的架构规范一致。如果重构涉及 spec 变更，需通过 `/trellis:update-spec` 更新。

## 产出

- 架构违规清单（文件 + 行号 + 违规类型）
- 重构建议（含代码示例）
- 扩展点识别（接口/抽象类定义）
