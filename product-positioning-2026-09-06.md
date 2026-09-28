# ClosenAI 产品主张与对外表达方案

> 日期：2026-09-06
> 依据：PRD v2.0 §1.1–1.2、`frontend/src/i18n/locales/`、`app.json`、`PaywallScreen.tsx`
> 前提：已确认合规为硬约束，采用被看见 / 掌控感 / 仪式感三个引擎，排除浪漫幻想与算命

---

## 零、核心结论

**要突出的不是"记录"，是"被记住"。**

PRD §1.2 已经把这件事想清楚了：

> 下载理由：每天早上，你的 AI 会主动来找你——不是因为它被设置了定时，而是因为它记得你昨天说了什么
> 留存理由：你的 AI 会发现你自己没注意到的模式——并在你陷入循环之前告诉你
> 长期理由：一年后，你的 AI 能告诉你你是如何成长的——因为它从头到尾都在

**但这三句话一个字都没落到 App 上。** 实际对外表达全部退化为工具语言：

| 接触点 | 实际文案 | 主语 | 动作 |
|--------|---------|------|------|
| 首屏标语 | Record every day · See your growth | 你 | 记录 |
| 引导三特性 | Daily Reflection / Weekly Insight / Growth Timeline | — | 功能名 |
| 商店副标题 | Track your growth with AI insights | 你 | 追踪 |
| 首页主位 | Growth This Week + Mood/Social/Confidence/Stress/Curiosity | — | 指标 |
| 付费墙 | Unlimited chat · Priority model · More refreshes | — | 配额 |

**主语从"AI 记得"变成了"你去记录"，这是从情绪产品退化成日记工具。**

后果直接体现在定价参照系上：Daylio 类的日记/追踪工具年 ARPU 约 $20–30，而 Calm（掌控感+仪式感）$180、Youper/Wysa $120–150。**你的表达决定了用户拿你和谁比。**

---

## 一、四个接触点的改写方案

### 1.1 App Store 元数据（最高优先级，影响 ASO 与归类）

| 字段 | 现状 | 建议 |
|------|------|------|
| 标题 | `ClosenAI`（`app.json`） | `ClosenAI: AI That Remembers You` |
| 副标题 | Track your growth with AI insights | **A/B 两版**：<br>抽象版：`The AI that remembers your life`<br>场景版：`Clear your mind in 3 minutes` |
| 关键词 | growth, personality, mood, insight, **tracker**, **journal** | 删 `tracker` `journal`（这两个词把 App 钉进日记品类）；加 `remembers`, `knows me`, `patterns`, `self discovery`, `life log` |
| 分类 | Lifestyle | 保持（Health & Fitness 会触发更多健康类审核） |

> `app.json` 的 `name` 与 PRD 元数据的「ClosenAI: Growth & Self Discovery」不一致，需同步。

**为什么副标题要 A/B：** 抽象版锚定"记忆"这个差异化点，场景版锚定"睡前整理"这个高频场景。PRD §1.1 自己标过这个待验证项，上架后第一优先级做。

### 1.2 首屏标语与引导三特性

**标语**
- 现状：`Record every day · See your growth`
- 建议：`It remembers what you told it`
- 备选：`Your AI remembers. You don't have to repeat yourself.`

**引导三特性**（现状是功能名，改为用户获得）

| 现状 | 建议 |
|------|------|
| Daily Reflection — Check in with one tap every day | **Talk to it once a day.** It listens, and it remembers |
| Weekly Insight — AI helps you see patterns | **It notices patterns you miss.** And tells you before you loop again |
| Growth Timeline — Track your journey over time | **A year from now**, it can show you how you changed |

**disclaimer** 现状 "Understand your growth journey through daily check-ins and reflections" 是纯工具表述，建议改为 PRD §1.2 的长期理由："一年后，你的 AI 能告诉你你是如何成长的——因为它从头到尾都在"。

### 1.3 首页主位（解决内容供给死循环的关键）

现状首页第一屏是 `Growth This Week` + 五项指标（Mood / Social / Confidence / Stress / Curiosity）。这是**仪表盘语言**，读起来像体检报告。

**建议：主位换成 AI 主动给的三类内容，指标区降到第二屏。**

| 主位卡片 | 内容 | 复用能力 | 解决什么 |
|---------|------|---------|---------|
| 记忆考古 | "一年前的今天，你在想什么" | P1-F07 回忆卡片（已实现） | 零输入成本的内容供给 |
| 模式发现 | "AI 发现：你每次加班后第二天都会提到睡眠" | P1-F03 预测性洞察（已实现） | 证明 AI 真的在看你 |
| 情感回报 | 等级晋升时 AI 的信（L2/L3/L4） | `relationship_level` + 叙事生成（已实现） | 养成循环的情感层 |

**指标区不删，只是降级。** 它承担"掌控感"的一部分，但作为首屏主位太冷。

### 1.4 付费墙四条（ARPU 直接影响）

现状四条**全是配额与性能**，卖的是"更快的马"：

```
Unlimited AI conversations      → 配额
Priority model responses        → 性能（这是 ChatGPT Plus 的语言，不要模仿）
More personality refreshes      → 配额
365-day growth timeline         → 深度（唯一有阶梯感的）
```

**建议重映射（保留权益实质，换表达）：**

| 键名 | 现状 | 建议 |
|------|------|------|
| `featureUnlimitedChat` | Unlimited AI conversations | **It remembers every conversation, and finds your patterns in them**（被看见） |
| `featurePriorityLLM` | Priority model responses | **Not just what happened — what's likely to happen next**（掌控感） |
| `featurePersonalityRefresh` | More personality refreshes | **Your personality isn't a test result. It's a path**（被看见） |
| `featureTimeline` | 365-day growth timeline | 保留，改为 **Look back 30 / 90 / 365 days**（深度阶梯，免费 30 / Pro 90 / Pro+ 365） |
| 新增 | — | **Once a year, your AI writes the story of how you grew**（仪式感，需先实现 Life Archive） |

**Pro+ 四条同理**，现状 `100 AI conversations per day` / `Unlimited personality refreshes` / `Multiple proactive messages per day` 全是次数，改为深度与频次的关系表达。

---

## 二、优先级排序

| 顺序 | 动作 | 成本 | 为什么先做 |
|------|------|------|-----------|
| 1 | 改 `app.json` 标题 + 商店元数据关键词（删 tracker/journal） | 极低 | 决定用户拿你和谁比，也影响 4.3(b) 归类 |
| 2 | 重写付费墙四条文案 | 低（6 语种 i18n） | 直接作用于 ARPU，无需改代码逻辑 |
| 3 | 首屏标语 + 引导三特性文案 | 低（6 语种 i18n） | 影响新用户首日留存 |
| 4 | 首页主位改记忆考古 | 中（组件位置调整，复用已实现能力） | 解决内容供给死循环 |
| 5 | 等级系统补情感回报层 | 中（`user_service.py` + 触发逻辑） | 让已有的养成机制真正产生情感反馈 |

**1–3 是纯文案改动，可以立刻做，不需要任何功能开发。** 建议上架前完成 1，上架后两周内完成 2–3。

---

## 三、一条判断标准

写任何面向用户的文案时问一句：**这句话的主语是"你"还是"它"？**

- 主语是"你"、动词是"记录/追踪/打卡" → 日记工具语言，参照系是 Daylio（$20–30/年）
- 主语是"它"、动词是"记得/发现/提醒" → 关系语言，参照系是 Calm（$180/年）

**你的护城河（Personal Dataset + 长期记忆）只有在"它记得"这个框架里才值钱。一旦回到"你去记录"，用户拿你和 Daylio 比，而 Daylio 是免费的那个参照系。**

---

## 四、一个必须避免的过度纠正

改写时不要走到另一个极端——把 AI 写成主体、写成"我懂你"。PRD v2.2 的定位升级说明已经论证过：

- "懂你"是静态声明，竞品也能声称
- 正确的框架是**动态过程**："我和你一起成长"

所以准确的表述是：**AI 记得（AI 侧动作）+ 你从中看见自己（用户侧获得）**，两个半句缺一不可。

- ❌ `I understand you`（静态、自夸、可被任何竞品复制）
- ❌ `Record your life`（工具、无情感回报）
- ✅ `It remembers what you told it, so you can see what you missed`
