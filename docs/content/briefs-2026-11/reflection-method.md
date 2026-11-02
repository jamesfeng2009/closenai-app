# 簇简报 · 反思方法（KG-09 · 2026-11）

主体：ClosenAI ／ 编制：2026-11-02 ／ 负责人：GROWTH
台账：`docs/keyword-growth-task-board-2026-11-01.md` §1 的 KG-09 行
上游口径：`docs/keyword-growth-implementation-plan-2026-11-01.md` §8.2 的页面—意图表、§4.7 的内容质量清单（G01–G14 见 `docs/keyword-content-gate.md`）
判定来源：对外承诺一律先对照 `docs/keyword-claims-audit-2026-11.md`，本文件**不新增任何承诺口径**
本簇的簇名不新造：它就是 KG-26 提示库里的 `cluster: reflection_method`（8 条提示），由 `backend/tests/test_content_briefs.py` 与 JSON 现算对拉

> **状态**：简报（写什么、凭什么写），**不是**已发布页面。发布受 KG-02（法务签字）与 T7-05（运营主体）两道闸，
> 两者当前都是 `BLOCKED`，所以本簇全部停在草稿面：不生成 `web/` 下的文件、不改 `web/sitemap.xml`、不部署。

---

## 1. 搜索意图

这三页吃的是一类人：**已经在写点什么，但写完没得到任何东西**。搜索词的共同结构是「怎么做＋有没有用」，
不是「什么是」，也不是「下载」。所以页面的第一屏必须给**可执行的方法**，而不是一句口号＋按钮。

| 意图层 | 用户此刻在想 | 页面必须先答的那一句 | 不能出现的东西 |
|---|---|---|---|
| 方法层 | 「反思到底怎么做才算数」 | 反思的产物是**一条能被后来核对的判断**，不是情绪宣泄 | 「静下心来想一想」这类无动作建议 |
| 材料层 | 「我缺的是提示，不是道理」 | 提示来自**你自己写过的东西**，不是通用清单 | 抄来的 100 条 prompts 列表 |
| 边界层 | 「我这是反思还是反刍」 | 区别在**是否收敛到一个下一步**；越界时给出求助入口 | 任何治疗／诊断口吻 |

关键词侧的对应关系不写在本文档里（避免两处维护）：以 `docs/aso/keyword-tracker-2026-11.md` §2 的落点列为准。

---

## 2. 目标 URL

| slug | 意图 | 必须回答 | 差异化段落 | 任务 |
|---|---|---|---|---|
| `self-reflection` | 怎么反思才不空转 | 从记情绪到找证据的完整一圈 | 每周模板 | KG-12 |
| `journal-prompts` | 想要现成的提示 | 30 条提示**按功能分簇**且可追溯 | 复用 KG-26 JSON | KG-12 |
| `overthinking` | 反刍 vs 反思怎么分 | 三步练习＋什么时候该找真人 | 危机资源 | KG-15 |

三页互相内链，也链向 `action-outcome-review` 簇（复盘是反思的下游）。`overthinking` 由 KG-15 承接，
**法务未签字前只写草稿**（该页 P2，且踩医疗边界）。

---

## 3. H1

| slug | H1（en） | 字符数 |
|---|---|---|
| `self-reflection` | `Self-Reflection That Ends in Evidence` | 37 |
| `journal-prompts` | `30 Journal Prompts Built From What You Already Wrote` | 52 |
| `overthinking` | `Overthinking vs Reflecting: How to Tell the Difference` | 54 |

约束：H1 不堆关键词（G01）、不含禁语（`scripts/check_aso_copy.py:42` 的 8 条词边界正则）、
三条互不相同；字符数由闸按 §3 表里的文本现算再比，不许手填。

---

## 4. 证据

每条主张都要能指回一个**仓内真存在的行**或**外部来源＋日期**。没有证据的主张不进页面。

| # | 主张 | 来源 | 日期 |
|---|---|---|---|
| 1 | 「30 条提示」是真数字，不是修辞 | `backend/content/reflection_prompts.json:37`（`prompts` 数组，实际 30 条，四簇 8-8-7-7） | 2026-11-01 |
| 2 | 提示按**功能**分簇而非按心情分簇 | `backend/content/reflection_prompts.json:41`（`"function"` 字段：reflect 18／goal 7／mood 5，合计 30） | 2026-11-01 |
| 3 | 反思的产物在库里是可核对的记录，不是聊天流水 | `frontend/src/screens/GrowthRecordsScreen.tsx:217`（记录页真在渲染条目） | 2026-11-02 |
| 4 | 「结果回访」是产品里已有的一圈，不是本页新造的承诺 | `frontend/src/screens/GrowthRecordsScreen.tsx:66` | 2026-11-02 |
| 5 | 洞察必须回链到用户原话，否则降级为 `pattern=null` | `docs/keyword-claims-audit-2026-11.md:100`（KG-01 汇总与判定分布） | 2026-11-02 |

第 5 条是本簇最关键的一句：**页面对「AI 给了什么」的描述必须与后端实际行为一致**，
这也是 KG-01 审计把绝对化记忆判「改写」的原因（同表 §1 起于 `docs/keyword-claims-audit-2026-11.md:23`）。

---

## 5. 内链与 CTA

| 页面 | 相关内链（≥2） | CTA | CTA 文案口径 |
|---|---|---|---|
| `self-reflection` | `journal-prompts`、`self-improvement` | 等待名单 | 商店链接未上线前**不写「立即下载」**（G09 禁假按钮） |
| `journal-prompts` | `self-reflection`、`mood-tracker` | 功能入口 | 指向 App 内 Reflect 面；App 未开放则退回等待名单 |
| `overthinking` | `self-reflection`、`ai-journal` | 危机求助＋等待名单 | 危机段落排在内链之前，且**不夹带产品推荐** |

规则：内链目标必须都在内容源的页面集合里（断链＝红，G08）；反向也要有入口，否则 KG-17 的爬虫会把孤儿页判红。

---

## 6. 法务边界

- **非医疗**：三页都要出现「不是医疗／不是诊断」句（G05 的机械判据是英文命中 `not medical`、中文命中「不是医疗」），
  正文不得出现 `therapy`／`treat*`／`diagnos*`（`scripts/check_aso_copy.py:42`）。
- **危机资源**：`overthinking` 必须给一条与产品无关的求助路径，且危机段落先于任何 CTA 出现——
  这条与 KG-28 的「危机排在配额之前且不调模型」同一口径。
- **承诺口径**：不得写「AI 记得你的一切／会分析你」；记忆一律限定为「你选择分享的内容」，
  依据是 `docs/keyword-claims-audit-2026-11.md:23` 起的判定表与 KG-23 已改的 onboarding 文案。
- **商标与框架**：本簇不涉及；人格框架的用词归 `long-term-self-understanding` 简报（MBTI／Enneagram 待法务）。
- **未决**：`docs/keyword-claims-audit-2026-11.md:129` 那批「四类之外」的登记（加密口径、永久保留等）随 KG-02 复核，
  本簇页面在签字前不引用那些句子。

---

## 7. 支柱页提纲（先写 1 篇）

**本篇给 `self-reflection`**（差异化段落＝**每周模板**，与方案 §8.2 该行同词）。

- H2 1｜反思空转的三个特征：只有情绪没有事实、只有当天没有跨度、只有结论没有下一步。
- H2 2｜一圈的四个环节：记情绪 → 找到当时的原话 → 写下预期 → 到期核对（对应产品里的 `growth_records` 与 `outcomes`）。
- H2 3｜**每周模板**（本页的原创交付物，G02）：五行——这周实际发生了什么／我当时怎么判断／我做了什么／结果怎样／下周改一处。
- H2 4｜模板与「提示」的分工：提示解决「不知道问什么」，模板解决「问了没下文」，两页互链。
- H2 5｜三条反面例子（自己写的，不引用他人案例，G06 无虚假案例）：情绪复盘变成自我指责／目标写成愿望／把 AI 摘要当成结论。
- H2 6｜边界与求助：连续两周出现睡眠／自伤相关表述时，页面把求助入口排在 CTA 前面。
- H2 7｜CTA：等待名单（一句说明为什么现在没有商店按钮），不放假下载按钮。

提纲只到 H2 层，H3 留给页面草稿批（KG-12）；本簇另外两页的提纲排在同一批之后，避免一次铺开四页却都停在半程。

---

## 8. 本轮不做

1. 不生成 HTML、不写 `web/` 与 `web/sitemap.xml`、不部署（发布闸 KG-02／T7-05 仍 `BLOCKED`）。
2. 不写 `overthinking` 的正文（P2＋医疗边界，等 KG-15 的法务口径）。
3. 不做外链建设、不做 faq schema 之类结构化数据（方案 §5 的「本阶段明确不做」）。
4. 不做多语言：本簇只出英文页面提纲，中文站点的语言决策未定（`docs/keyword-growth-implementation-plan-2026-11-01.md` §8.1 只允许一个内容源）。
