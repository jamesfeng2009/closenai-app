# diagnosing-bugs (Matt Pocock)

> 层级：诊断层（Matt Pocock）
> 调用命令：`/diagnosing-bugs`
> 调用时机：在 Superpowers `systematic-debugging` 阶段内调用

---

## 触发条件

在 `systematic-debugging` 构建根因链后，需要精确定位到具体文件和代码行时调用。

## 执行方式

1. **基于根因链缩小范围**：根据 systematic-debugging 产出的根因链，确定可能的代码模块

2. **逐文件排查**：
   - 搜索相关关键词（错误消息、变量名、函数名）
   - 检查数据流向（从入口到出口）
   - 检查异常处理路径

3. **定位到行**：
   - 确认具体的文件路径
   - 确认具体的代码行号
   - 记录问题代码片段

4. **验证定位**：
   - 确认修改该行可以解决问题
   - 确认不会引入新的问题

## 产出

- 精确的文件路径 + 行号
- 问题代码片段
- 修复方向建议（供 `/implement` TDD 使用）
