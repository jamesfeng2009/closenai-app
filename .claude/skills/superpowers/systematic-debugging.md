# systematic-debugging (Superpowers)

> 层级：执行层（Superpowers）
> 调用命令：`/systematic-debugging`
> 内部调用：`/diagnosing-bugs`（Matt Pocock 诊断层）

---

## 触发条件

当 Bug 反复出现、修了又坏、或根因不明确时调用此技能。

## 执行流程

### 1. 根因链构建

从症状出发，逐层追溯根因：

```
症状：用户收到重复的每日消息
  ↓ 为什么？
原因 A：Cron Job 执行了两次
  ↓ 为什么？
原因 B：QStash 重试机制触发
  ↓ 为什么？
根因：Vercel 函数超时 60s，QStash 认为失败自动重试
```

**规则**：不停留在表面症状，必须追溯到**根本原因**（即修复后该 Bug 不会再出现）。

### 2. 假设验证

对每个可能的根因提出假设，设计验证方案：

| 假设 | 验证方法 | 预期结果 |
|------|---------|---------|
| QStash 重试 | 检查 QStash 日志中的 delivery_attempts | attempts > 1 |
| 函数超时 | 检查 Vercel 日志中的 duration | duration > 60s |
| Cron 重复 | 检查 Vercel Cron 执行日志 | 执行间隔 < 1 小时 |

### 3. 最小复现

找到最小复现步骤，确保修复后可验证：

```bash
# 最小复现：模拟函数超时
curl -X POST https://api.closenai.app/api/v1/cron/daily-message \
  -H "Authorization: Bearer $CRON_SECRET" \
  --max-time 65
```

### 4. 调用 /diagnosing-bugs

在根因链构建后，调用 Matt Pocock 的 `/diagnosing-bugs` 精确定位到具体文件和代码行。

### 5. 产出

- 根因分析报告：症状 → 根因 → 修复方案 → 验证方案
- 回归测试用例（用于 TDD 修复）
