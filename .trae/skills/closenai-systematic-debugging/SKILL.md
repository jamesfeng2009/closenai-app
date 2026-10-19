---
name: "closenai-systematic-debugging"
description: "Systematic debugging with root cause chain methodology (5 Whys) for ClosenAI. Invoke when bugs recur, fix-and-break cycles happen, or root cause is unclear. Calls closenai-diagnosing-bugs as sub-tool."
---

# Systematic Debugging (系统化调试)

> 层级：执行层
> 调用时机：当 Bug 反复出现、修了又坏、或根因不明确时调用

---

## 触发条件

当 Bug 反复出现、修了又坏、或根因不明确时调用此技能。

## 执行流程

### 1. 根因链构建（5 Whys）

从症状出发，逐层追溯根因：

```
症状：用户收到重复的每日消息
  ↓ 为什么？
原因 A：Cron Job 执行了两次
  ↓ 为什么？
原因 B：QStash 重试机制触发
  ↓ 为什么？
根因（历史案例，Vercel 时代）：函数超时 60s，QStash 认为失败自动重试；
  2026-10-18 起 VPS 常驻后超时不再是这条链的触发器，但「消费端必须幂等」的教训保留
```

**规则**：不停留在表面症状，必须追溯到**根本原因**（即修复后该 Bug 不会再出现）。

### 2. 假设验证

对每个可能的根因提出假设，设计验证方案：

| 假设 | 验证方法 | 预期结果 |
|------|---------|---------|
| QStash 重试 | 检查 QStash 日志中的 delivery_attempts | attempts > 1 |
| 处理超时 | 容器日志（`docker compose logs`）里的 duration | 超出排程预算 |
| Cron 重复 | 宿主 crontab 执行记录（`grep CRON /var/log/syslog`） | 执行间隔 < 1 小时 |

### 3. 最小复现

找到最小复现步骤，确保修复后可验证：

```bash
# 最小复现：模拟函数超时
curl -X POST https://api.closenai.app/api/v1/cron/daily-message \
  -H "Authorization: Bearer $CRON_SECRET" \
  --max-time 65
```

### 4. 调用 `closenai-diagnosing-bugs`

在根因链构建后，调用诊断层技能精确定位到具体文件和代码行。

### 5. 修复验证

使用 `closenai-tdd` 的 Bug 修复 TDD 流程：
1. 写回归测试复现 Bug
2. 修复代码
3. 确认测试通过

## 与 TRAE-debugger 的区别

| 维度 | TRAE-debugger | closenai-systematic-debugging |
|------|-------------|------------------------------|
| 方法 | Debug Server + HTTP 日志 | 根因链（5 Whys） |
| 适用 | 运行时问题、需要实时日志 | 逻辑问题、修了又坏的 Bug |
| 工具 | 重工具（启动 Debug Server） | 轻方法（推理 + 验证） |

两者互补：systematic 定方向，TRAE-debugger 收证据。

## 可调用的子技能

| 阶段 | 调用技能 | 用途 |
|------|---------|------|
| 根因链构建后 | `closenai-diagnosing-bugs` | 精确定位到文件和行号 |
| 修复阶段 | `closenai-tdd` | 回归测试 + 修复 |
| 后端调试 | `closenai-backend` | FastAPI/SSE/Cron 专项知识 |

## 产出

- 根因分析报告：症状 → 根因 → 修复方案 → 验证方案
- 回归测试用例（用于 TDD 修复）