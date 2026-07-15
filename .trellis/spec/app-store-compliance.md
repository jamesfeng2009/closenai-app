# App Store 合规规范

> 来源：ClosenAI PRD v2.0 第 7.5 节 (Apple App Store 审核合规策略) + 第 7.1 节合规约束
> 状态：P0 阶段执行标准（提审必需）

---

## 1. 年龄分级 12+

### 1.1 分级确认

**年龄分级：12+**

根据 Apple 官方年龄分级标准，ClosenAI 触发 12+ 的条件是"偶尔/轻微的医疗或治疗类内容"（用户讨论情绪压力、焦虑）。

- **不触发 17+**：无限制网页访问、赌博、频繁/强烈的成熟内容均不涉及
- **AI 对话通过 Prompt 过滤敏感内容**
- **产品定位是成长追踪工具，非开放聊天**

### 1.2 未成年人保护

| 措施 | 条款 | 实现方式 | 优先级 |
|------|------|---------|--------|
| 年龄分级 12+ | 1.2.1(a) | App Store Connect 元数据设置 | P0 |
| 对话内容安全过滤 | 1.2.1(a) | 后端 Prompt 敏感词检测 + 输出过滤层 | P1 |
| 无恋爱/NSFW 导向 | 安全审核 + 青少年保护 | 定位"朋友/知己"，关系等级 tooltip | P0 |
| 等级名称无恋爱含义 | 安全审核 | L4 改名"至交"，等级页说明文字 | P0 |

---

## 2. 5.1.2(i) 数据共享弹窗

### 2.1 要求

2025 年 11 月 Apple 更新审核指南，首次将"第三方 AI"纳入数据共享监管范围（5.1.2(i) 条款）。

### 2.2 弹窗设计

**触发时机**：首次进入 AI 对话（Reflect Tab）时弹出。

**弹窗内容必须包含：**

1. **发送内容**：用户输入的对话内容将发送给第三方 AI 服务
2. **处理方**：OpenAI (GPT 系列 API)
3. **用途**：AI 生成个性化回复、成长洞察、性格分析
4. **存储方式**：对话内容加密存储于 ClosenAI 数据库，AI 模型供应商不持久化用户数据
5. **用户控制权**：用户可随时在"我的 → 记忆管理"中查看和删除 AI 记住的内容；注销账号时彻底删除所有数据

**用户选择**：

- **同意**：弹窗不再显示，AI 功能可用
- **拒绝**：AI 功能不可用，但其他功能（情绪签到、Growth Timeline、性格测试）正常使用

### 2.3 实现规范

```typescript
// 首次进入 Reflect Tab 时检查 consent 状态
const hasAIConsent = await checkAIConsent(userId);

if (!hasAIConsent) {
  // 弹出 5.1.2(i) 数据共享告知弹窗
  showDataSharingConsentModal({
    title: "AI 对话数据说明",
    content: [
      "ClosenAI 使用第三方 AI 服务（OpenAI GPT 系列）生成对话回复。",
      "您输入的内容将发送至 OpenAI 进行处理，用于生成个性化回复和成长洞察。",
      "对话内容加密存储，AI 服务商不持久化您的数据。",
      "您可随时在「我的 → 记忆管理」中查看和删除数据，注销账号时彻底删除。"
    ],
    actions: {
      agree: () => { setAIConsent(true); enterReflectTab(); },
      decline: () => { setAIConsent(false); stayOnGrowthTab(); },
    }
  });
}
```

**API 层面**：拒绝 consent 的用户调用 AI 对话接口时返回 403 `AI_CONSENT_REQUIRED`。

---

## 3. AI 内容标识

### 3.1 要求

所有 AI 生成的消息必须标注"AI 生成"标签。

### 3.2 实现

全局 CSS 伪元素统一实现，确保不遗漏：

```css
.ai-message-bubble::after {
  content: "AI 生成";
  display: block;
  font-size: 9px;
  color: #8B8B9E;
  margin-top: 4px;
  padding-top: 2px;
  border-top: 1px solid #1A1A2E;
  text-align: right;
}
```

### 3.3 覆盖范围

| 内容类型 | 标注方式 | 示例 |
|----------|---------|------|
| AI 对话消息 | 气泡底部"AI 生成"标签 | 所有 Reflect Tab 中的 AI 回复 |
| AI 主动消息 | 气泡底部"AI 生成"标签 | 每日主动消息 |
| 成长洞察 | 洞察卡片标注"AI 生成"标识 | Weekly Insight、Daily Discovery |
| 性格画像 | AI 观察部分标注"AI 分析" | 性格画像详情页 |
| 情绪签到解读 | AI 解读消息标注"AI 生成" | 签到后的 AI 回复 |

**数据层面**：所有 AI 生成消息的 API 响应包含 `is_ai_generated: true` 字段，前端据此渲染标识。

---

## 4. 免责声明

### 4.1 位置

设置页 → "关于 AI" section。

### 4.2 免责声明内容

```
关于 AI

ClosenAI 使用 AI 技术生成对话回复、成长洞察和性格分析。

免责声明：
AI 生成内容可能不准确，不用于医疗、法律或财务决策。
ClosenAI 是个人成长追踪工具，AI 生成内容仅供自我探索和娱乐参考。
如您遇到心理健康问题，请寻求专业医疗帮助。
```

### 4.3 其他法律文案位置

| 文案 | 位置 | 内容 |
|------|------|------|
| 隐私政策 | 设置页 → 隐私政策 | 数据收集、使用、存储、删除政策 |
| 服务条款 | 设置页 → 服务条款 | 使用条款、订阅条款 |
| 内容定位声明 | AI 输出过滤层 | 所有内容定位"娱乐/自我探索" |
| 禁止措辞 | AI 输出过滤层 | 无"精准预测/改运/医疗建议"措辞 |

---

## 5. 模型来源披露

### 5.1 位置

设置页 → "关于 AI" → "AI 模型来源"。

### 5.2 披露内容

```
AI 模型来源：GPT 系列 API (OpenAI)

ClosenAI 的 AI 功能由 OpenAI GPT 系列模型提供支持。
包括但不限于：GPT-4o（深度推理）、GPT-4o-mini（日常对话）。
所有 AI 生成内容均标注"AI 生成"标识。
```

### 5.3 合规约束汇总

| 约束 | 条款 | 实现方式 |
|------|------|---------|
| 所有内容定位"娱乐/自我探索" | Guideline 1.1.6 | 文案审核，AI 输出限制 |
| 无"精准预测/改运/医疗建议"措辞 | 1.1.6 | AI 输出过滤层 |
| AI 不得给出"辞职/分手"等极端建议 | 安全审核 | AI 输出限制 + 过滤 |
| 无恋爱/NSFW 导向 | 安全审核 + 青少年保护 | 定位"朋友/知己"，关系等级 tooltip |
| IAP 走苹果内购 | Guideline 1.1.1 | 苹果内购系统 |
| UGC 审核机制 | Guideline 1.2 | 举报/拉黑/AI+人工审核/封禁 |
| 记忆数据加密存储 | 用户隐私保护 | 数据库行级隔离 + 加密 |
| 用户注销时彻底删除记忆 | GDPR/隐私合规 | 注销流程级联删除 |

---

## 6. 4.3(b) Task-based 定位策略

### 6.1 核心风险

ClosenAI 最大的 4.3(b) 风险在于"AI 聊天"这个核心功能定位。2025 年 AI Companion 赛道爆发，大量套壳 GPT API 的聊天 App 涌入 App Store。苹果审核员对这类 App 的 4.3(b) 审查阈值已经很低。

### 6.2 4.3(b) 风险评估

| 功能模块 | 对应饱和类目 | 风险等级 | 缓解策略 |
|----------|-------------|---------|---------|
| AI 对话 | AI 聊天 Chatbot | 高 | 降级为第 3 个 Tab，首页改为成长概览 |
| 性格画像 | 性格测试/MBTI | 中 | AI 推断方式差异化，非传统问卷测试 |
| 情绪签到 | Mood Tracker | 中 | 与 AI 对话结合，非独立情绪记录工具 |
| Growth Timeline | 日记/记录类 | 低 | 市面罕见，天然差异化 |
| Growth Metrics | 自我提升类 | 低 | 6 维度 AI 计算，传统 App 无此功能 |
| Insight Level | — | 低 | L1-L4 等级系统，市面无同类 |

### 6.3 产品层面的审核安全设计

4.3(b) 的判断往往在审核员打开 App 的前 3 分钟形成。以下设计确保审核员的第一印象是"成长追踪工具"而非"AI 聊天 App"：

| 策略 | 具体设计 | 4.3(b) 防御效果 |
|------|---------|----------------|
| 首页非聊天 | 默认首页 = 成长概览仪表盘（Today's Reflection + Growth Metrics + Timeline + Insight + AI 单行入口） | 审核员前 3 秒看到的是仪表盘，不是聊天界面 |
| Tab 栏顺序 | 成长(第一位) \| 探索 \| Reflect(第三位) \| 我的 | 默认落在成长页，非 AI 对话页 |
| App Store 截图 | 首张 = Growth Dashboard 首页，AI 截图放最后一张 | 审核员在看 App Store 页面时就感知到差异化 |
| App 分类 | Lifestyle（非 Entertainment / Social Networking） | 避开"社交/娱乐"类目审查 |
| App 元数据 | 标题含"Growth & Self Discovery"，不含"companion/friend/chat" | 避免被关键词归入饱和类目 |
| AI 对话降级 | 对话功能从"首屏默认"降级为"首页单行卡片入口"（成长记录延续式文案） | 对话不再是产品第一印象，而是成长系统的输入方式之一 |

### 6.4 App Store 元数据策略

| 字段 | 建议值 | 禁用值 |
|------|--------|--------|
| App 标题 | ClosenAI: Growth & Self Discovery | ~AI Friend / AI Companion / AI Chat |
| 副标题 | Track your growth with AI insights | ~Chat with AI friend / Your AI companion |
| 分类 | Lifestyle | ~Entertainment / Social Networking |
| 关键词 | growth, personality, mood, insight, tracker, journal, self discovery | ~companion, friend, chat, dating, girlfriend |
| 截图首张 | Growth Dashboard 首页 | ~聊天界面截图 |

### 6.5 Review Notes 模板

提交审核时的 Review Notes：

```
ClosenAI is NOT an AI chatbot or AI companion app. It is an AI-powered
personal growth tracking system. Unlike Replika (chat-focused) or
Character.AI (roleplay-focused), ClosenAI's core value is its Growth System:

1. 6 AI-computed Growth Metrics (Emotional Stability, Social Connection,
   Confidence, Stress Level, Curiosity, Purpose)
2. Insight Level System (L1 Observation -> L2 Pattern -> L3 Forecast
   -> L4 Coaching)
3. Growth Timeline (365-day GitHub-style heatmap)

The AI conversation is one input channel for data collection, not the core
product. The app's default screen is a Growth Dashboard, not a chat interface.

Demo account: [提供含 30 天成长数据的 demo 账号]
```

### 6.6 申诉策略

如果收到 4.3(b) 拒审，申诉信应包含：

1. **表明身份**：App ID + 产品定位说明
2. **核心举证**：列出与 Replika/Character.AI 的差异对比表（功能/设计/交互/数据输出 4 维度）
3. **视频演示**：附 2 分钟产品演示视频链接，重点展示 Growth System
4. **请求复核**：申请与审核人员电话沟通

**禁止事项**：
- 不要立即重新提交（会导致账号被标记）
- 不要只改 UI 换皮（对 4.3(b) 无效）
- 不要换 Bundle ID（苹果会检测同账号关联）
- 不要只写"我是原创"（需要"对比差异"而非"声称原创"）

### 6.7 品类安全（规避 Social Networking / Dating）

- P0 是纯用户-AI 交互——无用户间匹配、无用户发现、无用户间私信、无用户主页对外可见
- App Category 选 Lifestyle（与 Replika 一致）
- 关系等级"初识→熟悉→知己→至交"全部是友谊词汇，无恋爱含义
- 用户间社交功能（P2）在首次过审后再上线

---

## 7. 多语言支持

| 项目 | 说明 |
|------|------|
| P0 支持语言 | 中文 (zh)、英文 (en)、印尼文 (id) |
| 性格题库 | 三语同步（版本号管理） |
| AI 对话语言 | 跟随 App 语言设置，0 延迟切换 |
| 推送文案 | 按用户 App 语言生成 |

---

## 8. 数据安全合规

- 所有 AI 对话内容端到端加密传输（TLS 1.3）
- 记忆库数据使用数据库行级安全隔离
- 第三方 AI 模型调用不传输用户身份信息（仅传匿名 ID + 对话内容）
- 用户注销时级联删除：账号、对话历史、记忆库、情绪签到、成长记录
- App 本地存储中的会话在注销时显式清除（防止残留）
