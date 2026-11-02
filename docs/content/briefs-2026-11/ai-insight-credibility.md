# 簇简报 · AI 洞察可信度（KG-09 · 2026-11）

主体：ClosenAI ／ 编制：2026-11-02 ／ 负责人：GROWTH
台账：`docs/keyword-growth-task-board-2026-11-01.md` §1 的 KG-09 行
上游口径：`docs/keyword-growth-implementation-plan-2026-11-01.md` §8.2 的页面—意图表、`docs/keyword-content-gate.md` G01–G14
判定来源：本簇的每一句对外描述都先过 `docs/keyword-claims-audit-2026-11.md`，未过判定的句子不写
本簇的簇名不新造：它就是 KG-26 提示库里的 `cluster: ai_credibility`（7 条提示），由 `backend/tests/test_content_briefs.py` 与 JSON 现算对拉

> **状态**：简报，不是页面。发布受 KG-02（法务）与 T7-05（运营主体）两道 `BLOCKED` 闸；
> 对比页还额外依赖 KG-31（核心循环生产化），所以本簇四页全部停在草稿面。

---

## 1. 搜索意图

这一簇吃的是**信任问题**，不是功能问题。搜 `ai journal`／`journal app` 的人已经被「AI 帮你记一切」这类话轰炸过一轮，
他们的默认假设是：这东西会把我说过的话拿去训练、会替我下结论、会把我当数据。
所以我们能赢的角度只有一个：**把「AI 到底做了什么、没做什么」写清楚**，并且写得可核对。

| 意图层 | 用户此刻在想 | 页面必须先答的那一句 | 不能出现的东西 |
|---|---|---|---|
| 定义层 | 「AI journal 到底是啥」 | 它是**带证据回链的记录工具**，不是聊天机器人 | 「记得你的一切」 |
| 选择层 | 「为什么不直接用 ChatGPT 记」 | 聊天工具记的是对话，这里记的是**能回访的条目** | 贬低友站、伪造评分 |
| 对比层 | 「和 Rosebud／Day One 差在哪」 | 只写**能公开核验的差异**并标来源与日期 | 未经证实的功能断言 |
| 恐惧层 | 「数据安全吗」 | 本轮**不写加密口径**（存在未决冲突，见 §6） | 「端到端加密」 |

---

## 2. 目标 URL

| slug | 意图 | 必须回答 | 差异化段落 | 任务 |
|---|---|---|---|---|
| `ai-journal` | 什么是 AI journal | 和普通日记差在哪；「记得你」≠「记得一切」 | 记录→情绪→反思→行动结果 | KG-10 |
| `journal-app` | 选一个 journal app | 为什么不是聊天机器人 | 证据回链 | KG-10 |
| `compare/rosebud-alternative` | 对比 | 只写可核验差异 | 来源＋日期 | KG-16 |
| `compare/day-one-alternative` | 对比 | 同上 | 来源＋日期 | KG-16 |

竞品品牌词**只允许出现在 `/compare/` 面**（R3 两侧对拉的既有判据，见 `docs/aso/keyword-tracker-2026-11.md` §1 规则表），
`ai-journal`／`journal-app` 两页正文与 ASO 关键词字段都不许含品牌词。

---

## 3. H1

| slug | H1（en） | 字符数 |
|---|---|---|
| `ai-journal` | `What an AI Journal Should Show You` | 34 |
| `journal-app` | `Choosing a Journal App That Doesn't Just Talk Back` | 50 |
| `compare/rosebud-alternative` | `Rosebud Alternative: A Comparison We Can Verify` | 47 |
| `compare/day-one-alternative` | `Day One Alternative: What Actually Differs` | 42 |

两条对比页的 H1 都以 `A Comparison We Can Verify`／`What Actually Differs` 收尾：**能核验**是对比页的唯一卖点；
品牌名只出现在对比页的 H1 与正文，绝不进入关键词字段（G07＋R3）。

---

## 4. 证据

| # | 主张 | 来源 | 日期 |
|---|---|---|---|
| 1 | 洞察是有独立实体与来源字段的，不是回复文案 | `backend/models/canonical.py:357`（`class Insight`） | 2026-11-02 |
| 2 | 每条洞察都挂着证据行，回链是数据结构而不是承诺 | `backend/models/canonical.py:399`（`insight_evidence` 表名与索引） | 2026-11-02 |
| 3 | 记忆面展示的是用户原话，不是模型改写后的结论 | `frontend/src/screens/MemoriesScreen.tsx:127`（`quote_text: item.content`） | 2026-11-02 |
| 4 | 「记得你」的绝对化写法已被判「改写」，页面必须用限定式 | `docs/keyword-claims-audit-2026-11.md:23` 起的判定表与 `:100` 的分布 | 2026-11-02 |
| 5 | 竞品品牌词的名单是既有资产，不新造对比对象 | `docs/aso/en-US-2026-11.json:22`（`competitor_brands`） | 2026-11-02 |
| 6 | 现网「每日多条主动消息」与实现口径不符，属已登记缺陷 | `web/index.html:648` 与 `docs/keyword-claims-audit-2026-11.md:145` 起的缺陷登记 | 2026-11-02 |

第 6 条的用处是**反向约束**：本簇页面在主动消息这件事上只能写机制（有配额、可调），
不能沿用官网现网那句还没修好的权益表述。

---

## 5. 内链与 CTA

| 页面 | 相关内链（≥2） | CTA | 说明 |
|---|---|---|---|
| `ai-journal` | `journal-app`、`self-reflection` | 等待名单 | 无商店链接 ⇒ 不做假按钮（G09） |
| `journal-app` | `ai-journal`、`mood-tracker` | 等待名单 | 同上 |
| `compare/rosebud-alternative` | `ai-journal`、`journal-prompts` | 等待名单 | 对比页**不链向任何友站**的下载页 |
| `compare/day-one-alternative` | `ai-journal`、`goal-tracker` | 等待名单 | 同上 |

对比页的内链一律回到自家支柱页；反向的入口由 `ai-journal` 的「我们和别的服务有什么不同」段落给出，
避免对比页成为只出不进的孤岛（KG-17 的孤儿页判据）。

---

## 6. 法务边界

- **绝对化记忆**：一律限定为「你选择分享的内容」；禁语清单与词边界正则见 `scripts/check_aso_copy.py:42`（8 条，
  含 `remembers everything`／`AI companion`／`AI friend`／`best`／`#1`）。
- **不做陪伴口吻**：ADR 决定 3 明确「不做陪伴角色」，所以本簇不写「你的朋友／你的伙伴」类措辞，
  也不写「它会主动关心你」。
- **对比页三条硬规矩**：只写可公开核验的差异；每条断言标来源 URL 与抓取日期；不贬损、不伪造评分与用户数。
  KG-16 额外依赖 KG-31——**核心循环未生产化前不写「已在生产可用」**。
- **加密与保留期**：App FAQ 的「对话已加密」与站点法务源的「非端到端加密」口径冲突尚未结（`docs/keyword-claims-audit-2026-11.md:129`），
  本簇四页一律不提加密、不提保留期。
- **危机段落**：`ai-journal`／`journal-app` 都会描述「回看你写过的东西」，正文一旦出现自伤相关表述，
  求助入口排在 CTA 前且不夹带产品推荐；这一条与 KG-28「危机排在配额之前且不调模型」同口径。
- **权益措辞**：`web/index.html:645`／`:648` 两句在修完前不复用。

---

## 7. 支柱页提纲（先写 1 篇）

**本篇给 `ai-journal`**（差异化段落＝**记录→情绪→反思→行动结果**，与方案 §8.2 该行同词）。

- H2 1｜先给定义：AI journal 是**能回链到你原话的记录系统**，不是会聊天的日记本。
- H2 2｜四段一圈（本页骨架）：记录条目 → 情绪签到 → 反思与洞察 → 行动结果回访；每段写清「谁产生的这句话」。
- H2 3｜「记得你」与「记得一切」的差别：只保留你写的与显式陈述的，沉默不推断（数据结构里的 `origin` 约束）。
- H2 4｜AI 做了什么、没做什么（一张两列表，本页原创，G02）：做了什么＝从条目里抽证据、排回看顺序；
  没做＝不替你判定成败、不假装是真人、不训练第三方模型。
- H2 5｜怎么核对一条洞察：展开证据行 → 看到原话与日期 → 不同意可以推翻（指回第 4 节第 2 条）。
- H2 6｜与 `journal-app` 的分工：那页回答「为什么不是聊天机器人」，这页回答「AI 具体做什么」。
- H2 7｜边界与非医疗声明；危机相关表述时求助入口排在 CTA 前。
- H2 8｜CTA：等待名单＋一句「为什么现在没有商店按钮」。

对比页两篇（KG-16）**本轮不出提纲**：可核验差异需要逐条外部来源与日期，而外部抓取还没做（属 KG-21 的账户类阻塞）。

---

## 8. 本轮不做

1. 不生成 HTML／不写 `web/`／不改 `web/sitemap.xml`／不部署（KG-02、T7-05 未解）。
2. 不写对比页正文与提纲：缺可核验的公开来源（KG-16 还额外等 KG-31）。
3. 不提加密、不提数据保留期、不提训练政策细节（口径未决，归 KG-02 隐私对照）。
4. 不引用任何用户数／评分／下载量数字：本轮没有可指回来源的数（G03），编造即违反 G06。
