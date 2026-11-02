# 簇简报 · 行动结果复盘（KG-09 · 2026-11）

主体：ClosenAI ／ 编制：2026-11-02 ／ 负责人：GROWTH
台账：`docs/keyword-growth-task-board-2026-11-01.md` §1 的 KG-09 行
上游口径：`docs/keyword-growth-implementation-plan-2026-11-01.md` §8.2 的页面—意图表、`docs/keyword-content-gate.md` G01–G14
判定来源：对外承诺先对照 `docs/keyword-claims-audit-2026-11.md`
本簇的簇名不新造：它就是 KG-26 提示库里的 `cluster: action_outcome`（7 条提示），由 `backend/tests/test_content_briefs.py` 与 JSON 现算对拉

> **状态**：简报，不是页面。发布受 KG-02（法务）与 T7-05（运营主体）两道 `BLOCKED` 闸；
> 本批只交付「写什么、凭什么」＋可机械校验的提纲，不生成 HTML、不写 `web/`、不改 sitemap。

---

## 1. 搜索意图

`self improvement`／`goal tracker` 是本站**商业意图最强也最容易写成通用货**的两个词。
搜索者的默认预期是「一个能打卡的清单工具」，市面上有几百个。我们的差异只有一句：
**记录的不是「做没做」，而是「当时预期什么、后来实际怎样」**——这句话必须落在页面首屏，
否则这两页会退化成又一个打卡器（也正是 KG-14 验收原文点名要防的）。

| 意图层 | 用户此刻在想 | 页面必须先答的那一句 | 不能出现的东西 |
|---|---|---|---|
| 工具层 | 「能不能提醒我回来核对」 | 到期回访是库里的一等对象，不是推送话术 | 「自动帮你达成目标」 |
| 怀疑层 | 「打卡软件我见过十个了」 | 打卡记的是动作，这里记的是**预期与结果的差** | 贬低友站、伪造评分 |
| 数据层 | 「我的情绪数据有什么用」 | 签到给趋势，趋势给复盘的起点，两页互链 | 任何诊断口吻 |

`mood-tracker` 归本簇的理由：情绪签到在本产品里是**复盘的输入**（7/30 天窗口＋热力格），
不是独立的「心情日记」卖点；把它写成孤立工具页会浪费那条已有实现（见第 4 节第 1、2 条）。

---

## 2. 目标 URL

| slug | 意图 | 必须回答 | 差异化段落 | 任务 |
|---|---|---|---|---|
| `self-improvement` | 行动结果 | Goal→Action→Outcome | 预期 vs 实际 | KG-14 |
| `goal-tracker` | 目标追踪 | 到期回访 | 不是通用打卡 | KG-14 |
| `mood-tracker` | 记录情绪 | 一键签到、7/30 天、热力图 | 非诊断声明 | KG-11 |

---

## 3. H1

| slug | H1（en） | 字符数 |
|---|---|---|
| `self-improvement` | `Self-Improvement You Can Audit` | 30 |
| `goal-tracker` | `A Goal Tracker That Asks How It Actually Went` | 45 |
| `mood-tracker` | `Mood Tracker: Check-Ins, Trends and What Came After` | 51 |

三条各钉一个不同的落点，避免同站三页抢同一个词；`mood-tracker` 的 H1 刻意把「之后发生了什么」写在趋势后面，
让这一页与本簇的「回访」主题同源。

---

## 4. 证据

| # | 主张 | 来源 | 日期 |
|---|---|---|---|
| 1 | 情绪窗口只有 7／30 天两档，页面不许写成「自定义区间」 | `frontend/src/screens/InsightsScreen.tsx:62`（`MOOD_RANGE_OPTIONS = [7, 30]`） | 2026-11-02 |
| 2 | 热力格是签到数据现算的，缺签到时该格为空而不是补值 | `frontend/src/screens/HomeScreen.tsx:85`（`buildHeatCells` 允许该格为 null） | 2026-11-02 |
| 3 | 「结果」在库里默认 `unknown`，即系统**不替用户编造结果** | `backend/models/canonical.py:506`（`result … default="unknown"`） | 2026-11-02 |
| 4 | 结果只接受用户显式陈述／显式动作／结构化事件，沉默不算 | `backend/models/canonical.py:511`（`origin` 字段与不变量 5 的文档说明） | 2026-11-02 |
| 5 | 回访记录在页面上是真渲染的条目，不是设计稿 | `frontend/src/screens/GrowthRecordsScreen.tsx:217` | 2026-11-02 |

第 3、4 条是这两页最硬的差异化证据：**数据结构里就没有「自动判定成功」这一列**。
这句话可以写进正文，因为它指得到字段名，不是形容词。

---

## 5. 内链与 CTA

| 页面 | 相关内链（≥2） | CTA | 说明 |
|---|---|---|---|
| `self-improvement` | `goal-tracker`、`self-reflection` | 等待名单 | 无商店链接 ⇒ 不放「立即下载」（G09） |
| `goal-tracker` | `self-improvement`、`mood-tracker` | 等待名单 | 同上 |
| `mood-tracker` | `goal-tracker`、`self-reflection` | 等待名单 | 同上；不链向任何「治疗／咨询」类表述 |

内链目标必须都在内容源页面集合里（G08 断链＝红）；本簇三页互相成环，避免任何一页成孤儿页（KG-17 的爬虫面）。

---

## 6. 法务边界

- **非医疗**：`mood-tracker` 必须有明确的非诊断声明（G05 的英文命中 `not medical`、中文命中「不是医疗」）；
  全簇正文禁 `therapy`／`treat*`／`diagnos*`（`scripts/check_aso_copy.py:42` 的词边界正则）。
- **不承诺结果**：禁止「用了就会改变／提升 X%」；库里 `result` 默认 `unknown` 正是这条的机械依据，
  页面只能描述机制，不能描述功效。
- **无虚假案例**（G06）：本簇页面**不使用任何用户案例、人名、截图数字**；
  真实的原话金句属 KG-30，且需要 `verified: true` 才能出现在站外。
- **危机段落**：`mood-tracker` 的正文若出现自伤相关表述，求助入口排在 CTA 前，且不夹带产品推荐。
- **承诺口径**：「记得你」一律限定为「你选择分享的内容」，依据 `docs/keyword-claims-audit-2026-11.md:23` 起的判定表。

---

## 7. 支柱页提纲（先写 1 篇）

**本篇给 `self-improvement`**（差异化段落＝**预期 vs 实际**，与方案 §8.2 该行同词）。

- H2 1｜打卡软件记的是「做了」，这里记的是「当时以为会怎样」——为什么差别决定了有没有用。
- H2 2｜Goal → Action → Outcome 三格各是什么，用一句话各定义，不引入新术语。
- H2 3｜**预期 vs 实际**的四格小表（本页原创模板，G02）：我预期／实际发生／差在哪／下次改一处。
- H2 4｜为什么结果可以是「还不知道」：系统不替你判定成败（指回第 4 节第 3 条的 `unknown` 默认值）。
- H2 5｜一个可执行模板：每周挑 1 条，写下预期，到期回访——这是 KG-14 验收点名要的「一个可执行模板」。
- H2 6｜与 `goal-tracker` 的分界：本页讲机制与判断，那一页讲提醒与回访节奏，避免两页同文（G02 哈希查重）。
- H2 7｜边界：不是医疗、不是心理治疗；出现危机相关表述时求助入口在前。
- H2 8｜CTA：等待名单一句说明（现在没有商店按钮，为什么不放假按钮）。

提纲只到 H2，H3 与正文留给 KG-14 的页面草稿批；本簇另外两页提纲同批延后，理由是同一句话在四页里重复会触发 G02 的同文查重。

---

## 8. 本轮不做

1. 不生成 HTML／不写 `web/`／不改 `web/sitemap.xml`／不部署（KG-02、T7-05 未解）。
2. 不写任何数字型功效表述（如「坚持 21 天」），也不引用外部研究的数字——KG-18 的 G03 要求每条外部引用带可核的 URL 与日期，本轮没有。
3. 不做「目标 AI 自动生成」这类功能宣传：ADR 决定 3 与方案 §5 的「不做清单」把它列在不做的功能里。
4. 不动 `docs/content/drafts/journal-prompts.*.md`（那是 KG-26 提示库的导出物，不是面向搜索的页面）。
