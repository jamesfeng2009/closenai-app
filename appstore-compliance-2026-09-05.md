# ClosenAI App Store 合规边界与合规内的变现路径

> 日期：2026-09-05
> 依据：Apple App Store Review Guidelines 现行原文（1.1.4 / 1.2 / 4.3(b)，4.3(b) 为 2026-06-08 修订版）
> 核查对象：ClosenAI PRD v2.0、`frontend/src/` 全量代码、`app.json`

---

## 零、先纠正前提：Apple 没有禁止 dating

你的担心方向对，但理由需要修正。Apple 从未禁止 dating 类 App——Tinder、Bumble、Hinge 都在架上。真正的规定是两条：

**1.1.4（禁什么）**
> "Overtly sexual or pornographic material... This includes **'hookup' apps** and other apps that may include pornography or be used to facilitate prostitution, or human trafficking and exploitation."

禁的是**露骨色情内容**和**约炮类 App**，不是 dating 本身。

**4.3(b)（多难进）**
> "Don't submit apps that are indistinguishable from what's already widely available... Certain kinds of apps, such as **dating**, flashlight, sound effects, wallpaper, simple timers, and **fortune telling**, are well established on the App Store and we will not accept new submissions unless they offer a **meaningfully different or improved experience**. We may remove these apps from the App Store going forward if they are not updated, improved, or do not attract customers."

所以 dating 是**饱和类目 + 高门槛**，不是禁区。

**为什么这个区分重要：** 如果你以为"Apple 禁止 dating"而做的规避是被动的、防御性的，你会倾向于把产品做得越来越"中性"、越来越"工具化"，最终失去情绪价值。**正确的理解是：你不是被禁止卖情绪，你只是不能靠浪漫幻想和算命去卖情绪。** 这中间有很大一片合规的空间。

---

## 一、真正的雷在 4.3(b) 的另一半：fortune telling

4.3(b) 点名的六个饱和类目里，**fortune telling 和 dating 并列**，而 ClosenAI 在这一项上的暴露度远高于 dating。

### 1.1 代码里的实际暴露点

| 位置 | 内容 | 风险 |
|------|------|------|
| `frontend/src/screens/NatalChartScreen.tsx`（22KB 完整页面） | 本命星盘 | 探索 Tab 一级入口，审核员可见 |
| `api.ts:606` `getNatalChartDaily()` | **今日参考（综合 / 事业 / 财运 + 幸运项）** | 教科书式算命，且带"财运" |
| `types/index.ts:202` | `PrimaryFramework = 'mbti' \| 'enneagram' \| 'astrology'` | 星座被提升为**主要性格框架**之一，与 MBTI 并列 |
| `ProfileScreen.tsx:36,488` | 主要框架三选一含 astrology | 同上 |
| `services/db/migrations.ts:18,175` | `zodiac_sign` 入库 | — |

PRD §1.3 自己就标注过 Co-Star / Hint 的"4.3b 占星"风险为**高**，且明确写了 ClosenAI 的差异点是"主动发现模式 vs 被动接受预测"。

**但当时的缓解方案（把占星藏进探索 Tab、用户主动触发、引导流程零占星信号）是在 2026 年 6 月之前设计的。4.3(b) 修订后，这个缓解力度不够了——因为 Apple 现在明确点名了 fortune telling，审核员会带着这个词去看你的探索 Tab。**

### 1.2 为什么这不只是合规问题，也是产品问题

保留星盘会让 ClosenAI 自我矛盾：
- PRD 核心叙事："AI 帮你从**自己的数据**中发现模式"
- 星盘功能："AI 根据**出生时刻的行星位置**告诉你今天的财运"

这是两个相反的世界观，同时出现在同一款 App 里。审核员看到的是定位混乱，用户看到的也是。

### 1.3 三个处置方案（含逐文件改动清单）

> 只有 A / B / C 三个方案。三者唯一的分歧点是：**"出生时刻 → 性格与未来解读"这条链路还留不留。**

#### 判断基准：4.3(b) 的判定标准不是"有没有占星"，是"相对 Co-Star 有没有明显不同的体验"

Co-Star、The Pattern 在架上，所以有星盘 ≠ 必拒。但 ClosenAI 的星盘相对它们没有任何差异化，这就是风险来源。

---

#### 方案 A · 完全移除（推荐）

**一句话：** 删除一切占星痕迹，性格框架只保留 MBTI + 九型。数据库表保留（不写反向迁移），只删代码与 UI。

**前端（约 10 个文件）**

| 文件 | 动作 |
|------|------|
| `screens/NatalChartScreen.tsx`（504 行） | 删除 |
| 导航栈中的 `NatalChart` 路由注册 | 删除 |
| `screens/ExploreScreen.tsx:93` `onNatalPress` + 星盘卡片 | 删除 |
| `services/api.ts:27, 593–610` | 删除 `NatalChart` / `NatalChartInput` / `NatalChartDaily` 类型导入与 3 个接口函数 |
| `types/index.ts:202` | `PrimaryFramework` 改为 `'mbti' \| 'enneagram'` |
| `types/index.ts:430,441,447` | 删除 `NatalChart` / `NatalChartInput` / `NatalChartDaily` |
| `types/index.ts:214,260,362` | 删除 `zodiac` 字段 |
| `screens/ProfileScreen.tsx:36,488` | 框架数组改为 `['mbti', 'enneagram']` |
| `screens/PersonalityScreen.tsx:733–737, 826–828` | 删除太阳星座展示与 combo 中的 zodiac |
| `services/db/personalityProfileRepository.ts:7` | 删除 `zodiac_sign` |
| `i18n/locales/{en,ja,de,it,es,zh}.ts` | 删除 `zodiac.*`、`natal.*`、`frameworks.astrology` 键 |

**后端（约 6 个文件 + 1 处注册）**

| 文件 | 动作 |
|------|------|
| `routers/natal.py`（75 行） | 删除 + 移除 `main.py` 中的 `include_router` |
| `services/natal_service.py`（410 行） | 删除 |
| `models/natal.py`（62 行） | 删除 |
| `repositories/natal_repository.py`（45 行） | 删除 |
| `models/schemas.py:209,266` | 删除 `zodiac` |
| `services/personality_service.py:175–183` | 删除 `zodiac_sign_for` 及 12 处调用（207/222/238/509/522/530/540/553/560/564/568/573） |
| `services/share_service.py:147` | 删除 `"zodiac": None` |
| `alembic/versions/b8d0e2f4a6c8_add_natal_charts.py` | **保留不动**（详见下方说明） |

> **为什么不写反向迁移：** 迁移链不可回退，`migrations.ts` 18/175 的 `zodiac_sign` 列也已随历史迁移固化。保留空表与空列的工程风险、回归成本都高于收益；且审核员看不到数据库。若将来要恢复功能，数据仍在。**这是方案 A 里唯一应该"留"的东西。**

**代价：** 约 16 个文件，其中只有 3 个需要动脑（`NatalChartScreen` 删 + `personality_service` 12 处清理 + 6 语种 i18n），其余是机械删除。
**收益：** 4.3(b) 触发点归零 + 叙事统一 + 工作量完全确定。

---

#### 方案 B · 改造为"非预测型"（成本高、结果不确定）

**一句话：** 保留生日/出生时刻输入与行星计算，把输出从"预测未来"改成"统计观察"，并全程规避占星术语。

**必做：**

1. `getNatalChartDaily()` 整个删除 —— "今日综合/事业/财运 + 幸运色/数字/时刻"是教科书式算命，改造不了，只能删
2. `natal_service.py`（410 行）的 AI prompt 全部重写：禁止出现 财运 / 幸运 / 运势 / 今日 / 未来 / 预测 / 将
3. `NatalChartScreen.tsx`（504 行）文案全改：去掉 行星 / 宫位 / 黄经 / 星座符号 ♈ 等占星语汇（这些视觉符号本身就是信号）
4. 功能改名，建议 "Seasonal rhythm · 出生季节与情绪节律"
5. UI 显著位置常驻标注："娱乐性参考，非预测"
6. **`astrology` 作为 `PrimaryFramework` 选项仍必须移除**（这是独立风险点，方案 B 与 A 在此处动作相同）

**为什么我不推荐：**
- 工作量比 A 大：A 是删 504+410 行，B 是重写 504+410 行，外加 6 语种文案重写
- 结果不确定：审核员看到"出生时刻 → 性格/情绪解读"这条链路，仍可能判定为 fortune telling 变体。你无法证明"统计观察"不是"算命的另一种说法"
- 叙事仍轻微矛盾：即便去掉预测，"行星位置影响性格"与"从你自己的数据中发现模式"依然是两个世界观

---

#### 方案 C · 保留但降级（不推荐）

**一句话：** 代码全留，只把探索 Tab 的星盘卡片移到设置深处的"实验功能"，默认关闭。

**改动量：** 最小，仅 `ExploreScreen.tsx:93` 一处。

**为什么不推荐 —— 4.3(b) 2026-06-08 修订后，"藏起来"这个策略失效了：**

1. 修订后的条款允许 **下架已上架的 App**（"if they are not updated, improved, or do not attract customers"）。藏起来只影响首次审核概率，不影响长期暴露
2. `astrology` 作为 `PrimaryFramework` 与 MBTI 并列，在 `ProfileScreen.tsx:36` —— 这是用户主流程，**藏不住**
3. 一旦被任何渠道曝光（评论区、关键词命中、审核员深度测试），就从"低概率"变成"确定拒"

**方案 C 的真实成本不是"低"，是"延迟支付 + 利息"。**

> 注意：Co-Star、The Pattern 这类占星 App 确实在架上。所以有星盘 ≠ 必拒。问题是 **ClosenAI 的星盘相对它们没有"明显不同或更好的体验"**，而这正是 4.3(b) 的判定标准。

---

#### 方案 D · 拆分处理（用户提出：保留身份星盘，只删预测类）

> 2026-09-06 由用户提出。方向是对的（按风险分级），但**切错了地方**。以下为评估结论。

**用户论点：** 本命星盘属"自我认知"，与成长追踪同源，且是年轻用户的病毒钩子（Co-Star 的成长引擎）；真正该删的是每日运势/推运/塔罗等"被动读取预测"。

**评估一：Co-Star 的病毒引擎恰恰是用户要删的那部分（论点被证伪）**

多方来源一致确认，Co-Star 的增长引擎是 **Day at a Glance 每日推送**，不是本命星盘：

- 「Co-Star 早期真正让它爆发的，其实不是复杂的占星功能。而是 Push Notification」
- 「它最成功的产品不是星盘，而是那一句每天弹出来的话」
- 「Instagram 上能刷到大量用户自发晒出的 Day at a Glance 截图……本质上都是在替 Co-Star 免费打广告」

那些被疯传的句子——"每个人的痛苦都有自己的节奏""别联系前任了""今天可以多加芝士""今天最好不要去看前任 Instagram"——**全部属于用户归类为"预测类"要删除的内容**。

本命星盘是一次性消费：输入出生信息 → 看结果 → 结束。没有回访理由，也就没有病毒性。

**→ 拆分方案的后果是：保留了风险（星盘），删除了收益（每日预测的传播力）。**

**评估二："身份星盘"在 4.3(b) 下仍是红区（风险被低估）**

- Apple 中文公告的点名是「**占卜/星座运势**相关应用」，占卜（命理排盘）与星座运势并列
- 开发者社群总结的 4.3(b) 高频拒审类目首项即「**算命占星**/八字紫薇国学」，并明确列出典型踩坑：**「命理类 App 只做基础排盘功能，没有拓展独有业务」**
- ClosenAI 的本命星盘正是"基础排盘"：输入出生时空 → 输出行星位置 + AI 解读，相对 Co-Star / TimePassages 无任何独有业务

**实证案例（台湾独立开发者，2026 年）：** 一款算命 App **连续四次被拒 4.3(b)**，最终过审的关键动作是「**把那個直接輸入生日算八字的頁面直接給刪了**」。换言之，**"输入出生信息 → 得到解读"这个交互本身**就是 4.3(b) 的典型触发点，与是否叫"身份解读"无关。

**评估三：账号级风险（最大的隐性成本）**

> 「连续 3 轮 4.3 申诉失败，不要再反复硬提审，会提升 3.2(f) 账户终止条款风险，**可能直接封禁开发者账号**」

这是分级保留方案最被低估的风险：若因星盘被反复拒，赌注不是"晚几周上架"，而是开发者账号。且 4.3(b) 明确「审核拥有自由裁量权」，改入口路径这类缓解措施的有效性不可预测。

**评估四：这个钩子的商业价值低于预期**

Co-Star 490 万 MAU，但：
- 占星赛道收入排**第三**（前两名是 CHANI、测测星座）——流量之王、变现老三
- 「超过八成收入来自**一次性好奇**」，「付费项目单价低、复购弱」
- 分成后月流水 40–50 万美元，下载与收入增速均已趋缓

对比 Tipsy Chat 年化约 3.5 亿人民币（约 4900 万美元）——**占星赛道的天花板显著低于 AI 情绪聊天赛道**。

#### 结论：改推荐「拆分 + 移植」

| | 建议 |
|---|---|
| 每日运势 / 推运 / 塔罗 / 幸运项 | **删除**（与用户判断一致，风险最高） |
| 本命星盘 | **删除**（风险被低估：基础排盘 = 4.3(b) 典型踩坑，且有封号风险） |
| Co-Star 的传播机制 | **移植**（见下） |

**移植方案：** Co-Star 飞轮的后三步——断言式文案 → 截图传播 → 自然下载——**与占星无关，可独立存在**。ClosenAI 用已实现的能力驱动同一飞轮，且更差异化：

| Co-Star | ClosenAI | 优势 |
|---------|----------|------|
| 出生星盘 + 每日星象 | 用户的对话与签到数据（已有） | 无需出生信息，无 4.3(b) 触发 |
| 毒舌断言（占星师规则 + LLM） | 基于真实模式的断言（P1-F03 已实现） | **是真的，不是编的** |
| 截图传播 | 截图传播（ShareCard 已实现） | 已有分享卡片能力 |
| 自然下载 | 自然下载 | 同 |

**这个移植同时拿到四件事：** 合规（非 fortune telling）、病毒性（断言式、可截图）、差异化（Co-Star 做不到——它没有用户真实数据）、定位一致（"主动发现模式"而非"被动接受预测"）。

**示例文案对照：**
- Co-Star：「每个人的痛苦都有自己的节奏」（占星推导，无从验证）
- ClosenAI：「你已经连续三个周日晚上提到睡不着了」（真实数据，无从反驳）

后者更容易被截图，因为它无法被反驳。

---

### 1.4 关键判断：合规分界线不是"预测 vs 身份"，是"占星 vs 数据"

用户的拆分逻辑按"预测类 / 身份类"切，但 Apple 的判定不按这个维度。真正的分界：

| | 红区 | 安全区 |
|---|---|---|
| 依据 | 出生时刻 / 行星位置 | 用户自己的历史数据 |
| 叙事 | 宇宙告诉你 | 你的数据告诉你 |
| 交互 | 输入生日 → 得到解读 | 持续记录 → 发现模式 |
| 可验证性 | 不可验证（信仰） | 可验证（有据可查） |

**ClosenAI 的 PRD 早就站在安全区这一边**（「主动发现模式 ≠ 被动接受预测」，PRD §1.3 自述的 vs Co-Star 差异点）。占星功能是这个定位里唯一自相矛盾的部分。

---

## 二、第二个变化：合规现在是持续义务，不是一次性门槛

4.3(b) 修订后新增：

> "We may remove these apps from the App Store going forward if they are not updated, improved, or do not attract customers."

**含义：**
- 过审 ≠ 安全。已上架的 App 若不更新、不改进、或"无法吸引用户"，可能被下架
- Apple 未公布具体宽限期；开发者社群反馈的移除通知通常给出约 90 天窗口，但**该数字不在条款中，不可依赖**
- 对独立开发者的实际要求：**保持规律更新节奏 + 留存可查的活跃度/收入数据**，收到通知时作为申诉证据

**落地动作：**
- 上架后建立固定发版节奏（建议 ≥ 每 6 周一个版本）
- 保留 App Store Connect 的下载/留存/收入趋势截图，按季度归档
- 收到 App Store Improvements 通知视为紧急事项处理

---

## 三、第三个 Gap：1.2 UGC 四件套，ClosenAI 缺三件

**1.2 现行要求**（UGC / 社交类 App 必备）：

> "apps with user-generated content or social networking services must include:
> - A method for filtering objectionable material from being posted to the app
> - A mechanism to report offensive content and timely responses to concerns
> - The ability to block abusive users from the service
> - Published contact information so users can easily reach you"

**2026-02-06 补充更新**：random or anonymous chat 明确纳入 1.2 管辖。

**ClosenAI 的现状（代码核查）：**

| 1.2 要求 | 状态 | 位置 |
|---------|------|------|
| 内容过滤 | ❌ 无 | 未发现任何对 AI 回复的过滤层 |
| 举报机制 | ❌ 无 | `HelpFeedbackScreen.tsx` 是反馈表单，不是针对内容的举报入口 |
| 屏蔽用户 | ➖ 不适用 | 1:1 与 AI 对话，无用户间连接 |
| 公开联系方式 | ⚠️ 待确认 | 需确认 HelpFeedback 页有可复制的邮箱/URL |

**关键判断：AI 生成内容是否算 UGC，Apple 没有明确定论。** 但 2026 年 2 月的更新方向是**扩大 1.2 的解释范围**，且补齐这三件的成本极低。

**建议（防御性补齐，成本低）：**
1. 在 AI 回复气泡上增加长按 → "举报此回复"，写入一条 `content_reports` 记录（后端已有 repository 分层模式，加一张表成本很低）
2. 后端对 LLM 输出加一层关键词/规则过滤（`services/` 层已有 quota_service 等统一入口，可在 chat_service 的 SSE 出口处统一挂载）
3. HelpFeedback 页显著位置放置可复制的联系邮箱，并在 App Store Connect 的"App 信息 → 联系信息"同步
4. 隐私政策中写明内容审核与举报处理流程

---

## 四、合规边界内，怎么把 ARPU 找回来

这是核心问题。**不能做浪漫幻想、不能做算命，那还能卖什么情绪？**

答案是：情绪赛道里至少有三大付费引擎，dating 和 fortune telling 只是其中被强监管的两种。剩下三种完全合规，且已被验证能支撑高 ARPU。

| 引擎 | 用户买的是什么 | 合规 | 参照 ARPU | ClosenAI 对应能力 |
|------|--------------|------|----------|------------------|
| **被看见（witnessing）** | "有人记得我、见证了我的人生" | ✅ 完全合规 | — | **Personal Dataset + Temporal Memory（已实现）** |
| **掌控感（agency）** | "我对自己的生活有掌控，不再被情绪推着走" | ✅ 完全合规 | Calm $180/年 | **P1-F03 预测性洞察（已实现）** |
| **仪式感（ritual）** | "人生阶段的纪念物" | ✅ 完全合规 | Life Archive 类 | **Year in Review / My Story（UI 已设计，代码未实现）** |

**关键洞察：Calm 卖的是"掌控感 + 仪式感"，年 ARPU $180，高于 Replika 的 $96–120，而 Replika 卖的正是你放弃的"关系养成"。**

**也就是说：你放弃的那块（浪漫/养成），并不是情绪赛道 ARPU 最高的那块。任务导向型自我觉察的 ARPU 反而更高。**

### 4.1 三个引擎怎么落地（都基于已实现的能力）

**引擎一 · 被看见 —— 记忆考古**
- 载体：P1-F07 已实现的"N 天前今日"回忆卡片
- 动作：产品化为首页每日主位——**"一年前的今天，你在想什么"**
- 为什么这能付费：它零输入成本（解决内容供给死循环）、情绪价值高、**且天然随时间增值**
- 免费/付费切分：免费看 30 天内，Pro 看 90 天，Pro+ 看全部 + AI 生成"这一年的你"叙事

**引擎二 · 掌控感 —— 预测性洞察**
- 载体：P1-F03 的"下周预测"段（已实现，数据不足时自动省略）
- 动作：把它从"洞察页的一段文字"提升为 **Pro 档的核心卖点**
- Paywall 文案从"优先模型"改为：**"不只告诉你发生了什么，还告诉你接下来可能发生什么"**
- 为什么这能付费：焦虑的本质是失控，可预测性直接对冲焦虑。这是 Calm 卖 $180/年的底层逻辑

**引擎三 · 仪式感 —— Life Archive 一次性内购**
- 载体：UI 原型已设计（Monthly Story $2.99 / Year in Review $9.99 / My Story $29.99），**代码完全未实现**
- 动作：做成年度仪式感商品，绑定到具体时点推送（满 30 天 / 满 365 天 / 1 月 1 日）
- 为什么这能付费：Tipsy 的模式证明"订阅做留存 + 一次性/消耗品做 ARPU 天花板"才是正解；行业一次性收入占比 7%，但对情绪型产品是关键的 ARPU 突破口
- 后端已有 `share` 导出缓存表（迁移 `c4d6e8f0a2b4`）与名片变体（classic/minimal/combo）能力，实现成本可控

### 4.2 关系养成引擎：不是"要不要加回"，而是"奖励设计错了"

**先纠正一个前提：关系养成从未被删除。** `user_service.py:55–112` 完整实现了等级阶梯（L1 初识 → L4 至交）、晋升门槛（0/7/30/90 天）、解锁表、里程碑徽章（7/30/100/365 天）、Streak 冻结券（免费 1 / Pro 3）。结构上比 Crushie AI 更完整。

被删掉的只有**恋爱标签**（L4 由"灵魂伴侣"改为"至交"，并加了 `LEVEL_NOTE` 声明"不涉及恋爱关系"），**不是养成机制本身**。

**真正的问题在 `LEVEL_UNLOCKS`：四级奖励全是功能，没有一条情感回报。**

| 等级 | 现状解锁 | 建议补充的情感回报 |
|------|---------|------------------|
| L1（0 天） | 基础对话、情绪签到 | — |
| L2（7 天） | AI 主动跟进、记忆引用 | **"AI 第一次提起你上周说过的那件事"** |
| L3（30 天） | 深度性格分析、模式发现、AI 周报 | **首份 Monthly Story 预览**（引导后续付费） |
| L4（90 天） | 季度报告、预测性洞察、深度反思模式 | **AI 手写的一封信："这 90 天我看到的你"** |

**为什么这一层值钱：** Crushie AI 做到 $4.29/MAU/月，靠的不是功能多，是升级时用户感受到"关系变了"。功能解锁只激活理性，情感回报才激活**留存和口碑**。而这四个补充项里，有三个直接复用已实现的能力（记忆引用、分享导出、叙事生成），实现成本远低于新建一个功能。

**同时，这也是解决"内容供给死循环"的最低成本路径**——AI 侧主动给出的情感回报，不依赖用户当天有没有输入。

---

### 4.3 顺带修正：Paywall 现在卖的东西不值钱

`PaywallScreen.tsx` 当前四条权益——无限对话、优先模型、画像刷新、时间线——**全是"次数/配额"**。

行业数据很明确：Character.AI 年 ARPU 仅约 $10，变现最弱，恰恰因为卖的是"更多对话"。

**建议映射（保留权益实质，改表达）：**

| 现有 | 改为 |
|------|------|
| `featureUnlimitedChat` | "AI 记得你每一次对话，并从中发现你的模式" → **被看见** |
| `featurePriorityLLM` | "不只告诉你发生了什么，还告诉你接下来可能发生什么" → **掌控感** |
| `featurePersonalityRefresh` | "你的性格不是一次测试，是持续演化的轨迹" → **被看见** |
| `featureTimeline` | 保留（免费 30 天 / Pro 90 天 / Pro+ 365 天已是深度阶梯） |
| **新增** | "每年一次，AI 把你这一年的成长写成故事" → **仪式感**（需先实现 Life Archive） |

---

## 五、执行清单

### 上架前必须

- [ ] **决策星盘功能**：方案 A（移除）/ B（改造为非预测型）/ C（降级，不推荐）/ D（拆分，评估见 §1.3，结论为删除全部 + 移植传播机制）
- [ ] 移除或重构 `astrology` 作为 `PrimaryFramework` 的选项（与 MBTI 并列会放大 4.3(b) 信号）
- [ ] 补齐 1.2 三件套：内容过滤 / 举报入口 / 公开联系方式
- [ ] 隐私政策补充内容审核与举报处理流程
- [ ] 统一三处价格（`monetization-plan.md` v1.1 / UI 原型 / 代码），消除二义性
- [ ] `app.json` 的 `name` 同步为 PRD 元数据「ClosenAI: Growth & Self Discovery」
- [ ] 完成年龄分级问卷（App 涉及情绪/心理健康话题，建议保守作答，预期 13+ 或 16+）

### 上架后 1–2 月

- [ ] 建立 ≥ 每 6 周的发版节奏，季度归档活跃度与收入截图（4.3(b) 下架风险的对冲证据）
- [ ] 实现 Life Archive 一次性内购 + 时点推送
- [ ] 重写 Paywall 文案：配额 → 被看见 / 掌控感 / 仪式感
- [ ] 记忆考古产品化为首页每日主位

### 需要验证

- [ ] App Store 副标题 A/B：抽象定位「Track your growth with AI insights」vs 具体场景「3 分钟清空大脑，睡个好觉」（睡前复盘）
- [ ] Pro+ 年付定价 A/B：$99.99 vs $119（依据：Calm $180/年、Youper/Wysa $120–150/年）

---

## 六、一句话结论

**你不需要通过放弃情绪来换取合规——你需要的是换一个情绪引擎。**

放弃浪漫幻想和算命，换来的是：合规安全 + 叙事统一（不再自我矛盾）+ 一个 ARPU 天花板更高的参照系（Calm $180/年 > Replika $96–120/年）。这笔交易是划算的，前提是你把省下来的产品精力投到**被看见 / 掌控感 / 仪式感**这三个引擎上，而不是投到"让工具更像工具"上。
