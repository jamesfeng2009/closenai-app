# 关键词月度校验与淘汰表（KG-05）· 2026-11

编制：2026-11-02 ／ 负责人：GROWTH ／ 覆盖：en-US 主投面（ASO ＋ KG-10～KG-16 规划页面）
词量**全部取自台账 §4 的抓取集合**（`docs/keyword-growth-task-board-2026-11-01.md:813` 起的映射表，表头逐字是 `| 关键词簇 |`），
本表不新增、不改写任何数字——这条由 `backend/tests/test_keyword_tracker.py` 两侧对拉：
词集合与台账 §4 必须完全相同，`outdated` 标记的 6 个词也必须一一对上。

---

## 1. 规则（先有判据，再谈词）

| # | 规则 | 机械判据 | 本轮命中 |
|---|---|---|---|
| R1 | 月搜索量 <500 的词不进主投/主页面，只用于长尾内容或产品语言 | 量列解析成数字后与 500 比 | **零命中**：本轮 43 词最小 590（`mood tracker journal`、`goal setting app`），因为词表来自台账 §4 的「有量」集合。R1 要等下月新增长尾词才起作用——**这条规则本轮没被验证过，不许引用成「已生效」** |
| R2 | 连续 8 周无曝光或无有效激活 ⇒ 降级或替换 | KG-19 的 `search_reach` 与 `install` 读数（`docs/metrics-dictionary.md:94` 起） | **未跑**：KG-21（GSC/Bing/商店接入）仍 `BLOCKED`，所以本轮没有任何词因 R2 被淘汰。表里的「降级」都是**意图/法务判定**，不是读数判定 |
| R3 | 竞品品牌词只用于对比页，不进关键词字段 | 关键词字段与 `competitor_brands` 求交为空（`scripts/check_aso_copy.py`） | 命中 4 条（Day One ×2、Rosebud ×2），已逐条标注 |
| R4 | 每个词必须落到一个真实功能或页面，不建造词不对板的落地页 | 落点列必须是「已存在的面」或「台账里真有的 KG 任务」 | 43 条全部有落点或明确写「无」并淘汰 |

## 2. 月度表（43 词）

> 来源统一是 Ubersuggest 美国/英语、抓取日期 2026-10-27、四舍五入；
> **单源**是本表的已知缺陷（见 §4），不是疏漏的写法。
> 状态口径：`保留·主投`／`保留`／`保留·长尾`／`保留·仅限对比页`／`保留·待法务`／`降级·观察`／`淘汰`。

| 关键词 | 月搜索量 | 来源 | 抓取日期 | 搜索意图 | 落点（词对的那块板） | 状态 | 理由与下次动作 |
|---|---|---|---|---|---|---|---|
| `ai journal` | 1.9K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 找能回应的日记工具 | `/en/ai-journal`（KG-10）＋ASO 标题 `docs/aso/en-US-2026-11.md` | 保留·主投 | 标题已收录 `AI Journal`；页面等 KG-10 落地，下月用第二来源复核量 |
| `ai journal app` | 720 | Ubersuggest 美国/英语·单源 | 2026-10-27 | 同上，明确要 App | `/en/ai-journal`（KG-10）App 段 | 保留·长尾 | 与主词同页承接，不另建页 |
| `journal app` | 9.9K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 泛找日记类 App | `/en/journal-app`（KG-10） | 保留·主投 | 量大但意图泛；首屏必须讲「会回看你自己写过的东西」而不是又一个日记壳 |
| `journaling app` | 9.9K（工具标 outdated） | Ubersuggest 美国/英语·单源 | 2026-10-27 | 同上 | `/en/journal-app`（KG-10） | 保留·主投 | outdated：投放前必须用 Apple Search Ads 重取，否则量不可信 |
| `mood tracker` | 4.4K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 记录并查看情绪趋势 | ASO 关键词字段 `docs/aso/en-US-2026-11.json`＋`/en/mood-tracker`（KG-11） | 保留·主投 | 已进关键词字段第 1 项；功能面是 `HomeScreen.tsx:85` 的签到热力与 `InsightsScreen.tsx:999` 的 90 天热力图 |
| `mood tracking app` | 3.6K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 同上，要 App | `/en/mood-tracker`（KG-11） | 保留 | 不进关键词字段：`tracking`/`app` 与 `mood tracker` 重叠，白占字符 |
| `mood journal` | 1K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 情绪＋书写载体 | `/en/mood-tracker`（KG-11）二级段 | 保留·长尾 | 由页面 H2 覆盖，不单独建页 |
| `mood tracker journal` | 590 | Ubersuggest 美国/英语·单源 | 2026-10-27 | 同上（长尾） | `/en/mood-tracker`（KG-11）FAQ | 保留·长尾 | 590 贴近 R1 门槛；下月跌破 500 就退出主页面词，只留 FAQ 措辞 |
| `self reflection` | 14.8K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 找反思方法 | `/en/self-reflection`（KG-12） | 保留·主投 | 意图与信息面吻合；页面必须先给方法再谈产品 |
| `self reflection journal` | 1.3K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 反思＋载体 | `/en/self-reflection`（KG-12） | 保留 | 同页承接 |
| `self reflection questions` | 720 | Ubersuggest 美国/英语·单源 | 2026-10-27 | 要一份问题清单 | `/en/journal-prompts`（KG-12）＋提示库 `backend/content/reflection_prompts.json` | 保留 | 有 30 条原创提示可承接（KG-26 已交付），不是空壳页 |
| `journal prompts` | 40.5K（工具标 outdated） | Ubersuggest 美国/英语·单源 | 2026-10-27 | 找可直接用的提示 | `/en/journal-prompts`（KG-12） | 保留·主投 | 本轮最大词面但 outdated；提示库真存在（30 条）⇒ 词对板，量待第二来源 |
| `journal prompts for self reflection` | 1.3K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 定向提示 | `/en/journal-prompts`（KG-12）反思簇 | 保留 | 提示库 function=reflect 8 条对板 |
| `journal prompts for self discovery` | 1.3K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 定向提示 | `/en/journal-prompts`（KG-12）发现簇 | 保留 | 画像页等 KG-13；本轮先落在提示页，避免词不对板 |
| `journal prompts for anxiety` | 1K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 焦虑自助书写 | `/en/overthinking`（KG-15） | 保留·待法务 | 焦虑措辞在医疗边界邻域；措辞与危机求助提示需随 KG-02 一起定 |
| `self discovery app` | 1.9K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 找自我理解工具 | `/en/self-discovery`（KG-13） | 保留 | 页面主打「从你的记录生成的画像」，与一次性测验切开 |
| `personality test online` | 9.9K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 想在线测人格 | `/en/personality-test`（KG-13）＋ASO 关键词字段 | 保留·待法务 | 已在关键词字段；页面必须写「不是一次性测验」，商标用词等 KG-02 |
| `mbti test free` | 40.5K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 要免费一次性测验 | `/en/personality-test`（KG-13） | 降级·观察 | 意图不符（本品非测验）＋MBTI 是他人商标；关键词字段用保守版 `16 types`（`docs/aso/en-US-2026-11.md`） |
| `enneagram test online` | 5.4K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 同上 | `/en/personality-test`（KG-13） | 降级·观察 | 意图是一次性测验，与「从你自己的记录生成画像」相反；Enneagram 是他人商标词面，措辞随 KG-02；连续 8 周无曝光按 R2 退出 |
| `self improvement app` | 2.4K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 找自我提升工具 | `/en/self-improvement`（KG-14）＋ASO 关键词字段 | 保留 | 已在关键词字段；差异点是「目标→行动→结果回访」不是打卡 |
| `personal growth` | 8.1K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 泛学成长方法 | `/en/self-improvement`（KG-14） | 保留 | 信息意图为主；页面给方法，产品作次要 CTA |
| `personal growth goals` | 2.9K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 定成长目标 | `/en/goal-tracker`（KG-14） | 保留 | 与 `goal tracker` 同页承接 |
| `personal growth journal` | 720 | Ubersuggest 美国/英语·单源 | 2026-10-27 | 成长＋书写 | `/en/self-improvement`（KG-14） | 保留·长尾 | 同页二级段承接 |
| `goal tracker` | 2.4K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 追踪目标 | `/en/goal-tracker`（KG-14）＋ASO 关键词 `goals` | 保留 | 词面对应 `personal_commitments` 与 outcomes 四选一（`GrowthRecordsScreen.tsx:66`） |
| `goal setting app` | 590 | Ubersuggest 美国/英语·单源 | 2026-10-27 | 定目标类工具 | `/en/goal-tracker`（KG-14） | 保留·长尾 | 590 贴近 R1 门槛，下月复看 |
| `goal tracker templates` | 880（工具标 outdated） | Ubersuggest 美国/英语·单源 | 2026-10-27 | 要现成模板 | `/en/goal-tracker`（KG-14）模板节 | 降级·观察 | **只有真做出可复制的模板才保留**；没有模板就是词不对板，按 R4 退出 |
| `overthinking` | 18.1K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 想停止反刍 | `/en/overthinking`（KG-15） | 保留 | 页面必须区分「反刍」与「有证据的反思」并给危机求助提示；无治疗承诺 |
| `overthinking a relationship` | 720 | Ubersuggest 美国/英语·单源 | 2026-10-27 | 关系方向的反刍 | `/en/overthinking`（KG-15） | 保留·长尾 | 关系意图容易滑向陪伴诉求，而 ADR 决定 3 不做陪伴角色（`docs/adr/2026-11-keyword-feature-scope.md`） |
| `anxiety journal` | 4.4K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 焦虑书写 | `/en/overthinking`（KG-15） | 保留·待法务 | 医疗边界措辞随 KG-02；不得写成缓解焦虑的疗法 |
| `anxiety journal prompts` | 1.3K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 焦虑定向提示 | `/en/journal-prompts`（KG-12）焦虑簇 | 保留·待法务 | 焦虑措辞属医疗边界邻域，不得写成疗法；提示库有焦虑簇可承接，页面措辞随 KG-02 定稿 |
| `day one journal app` | 12.1K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 了解/替换 Day One | `/en/compare/day-one-alternative`（KG-16） | 保留·仅限对比页 | R3：竞品品牌词禁进 ASO 关键词字段；对比页只用可核验公开差异＋来源日期 |
| `day one journal` | 2.9K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 同上 | `/en/compare/day-one-alternative`（KG-16） | 保留·仅限对比页 | 竞品品牌词只能出现在对比页正文（R3），逐条标来源与抓取日期；nominative fair use 说明等 KG-02 签字 |
| `rosebud app` | 720 | Ubersuggest 美国/英语·单源 | 2026-10-27 | 找 Rosebud 替代 | `/en/compare/rosebud-alternative`（KG-16） | 保留·仅限对比页 | 竞品品牌词禁进 ASO 关键词字段（R3）；差异点只写「证据回链＋结果回访＋可导出」，每条配可核验来源与抓取日期，不贬损、不伪造评分 |
| `rosebud application` | 880 | Ubersuggest 美国/英语·单源 | 2026-10-27 | 同上 | `/en/compare/rosebud-alternative`（KG-16） | 保留·仅限对比页 | 竞品品牌词禁进关键词字段（R3）；只在对比页写可核验的公开差异并标抓取日期，法务口径同 KG-16 |
| `ai companion` | 18.1K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 找角色/陪伴 | 无（不建造词对板页） | 淘汰 | 定位改判：ADR 决定 3 不做陪伴角色；`AI companion` 在 KG-04 禁语清单里，任何面都不写 |
| `ai companionship` | 14.8K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 同上 | 无（不建造词对板页） | 淘汰 | 定位改判：ADR 决定 3 不做陪伴角色；买这个词等于把产品拉进 Replika/Character.AI 的意图池，与 `AI companion` 禁语同族 |
| `ai companion app` | 4.4K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 同上 | 无（不建造词对板页） | 淘汰 | 定位改判：ADR 决定 3 不做陪伴角色；`AI companion` 在 KG-04 禁语清单里，商店与站点面都不写 |
| `ai friend` | 12.1K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 要一个 AI 朋友 | 无（不建造词对板页） | 淘汰 | 定位改判：ADR 决定 3 不做陪伴角色；`AI friend` 本身就在 KG-04 禁语清单，任何对外面都不写 |
| `gratitude journal` | 74K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 感恩日志 | 无（不建造词对板页） | 淘汰 | 功能不对板：本品没有感恩日志主路径，为 74K 造页就是 R4 禁的那类页 |
| `self care app` | 8.1K（工具标 outdated） | Ubersuggest 美国/英语·单源 | 2026-10-27 | 泛自我照顾 | 无（不建造词对板页） | 淘汰 | 意图过泛且常滑向疗愈/治疗暗示（医疗边界）；量还标 outdated |
| `self care apps` | 5.4K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 同上 | 无（不建造词对板页） | 淘汰 | 意图过泛，且「自我照顾」常滑向疗愈/治疗暗示（医疗边界）；本品无对应主路径，按 R4 不造页 |
| `mbti test` | 165K（工具标 outdated） | Ubersuggest 美国/英语·单源 | 2026-10-27 | 免费一次性测验 | 无（不建造词对板页） | 淘汰 | 全表最大量，但意图与产品相反（我们不做一次性测验）＋商标待 KG-02；不进任何面 |
| `enneagram test` | 201K（工具标 outdated） | Ubersuggest 美国/英语·单源 | 2026-10-27 | 同上 | 无（不建造词对板页） | 淘汰 | 全表第二大词面，但意图是一次性免费测验、与产品相反，Enneagram 又是商标词面（等 KG-02），量还标 outdated |

## 3. 分布（由本表现算，别手填）

| 状态 | 词数 |
|---|---|
| 保留·主投 | 6 |
| 保留（含长尾/待法务/仅限对比页） | 25 |
| 降级·观察 | 3 |
| 淘汰 | 9 |

`test_summary_distribution_is_recomputed_from_the_table` 会从表里重算这四格；改一行状态没同步汇总就红。
（汇总刻意只分四档，因为「保留」的子标签是**落点约束**而不是另一档状态。）

## 4. 本轮已知缺陷（如实写，不当已达标）

1. **只有单源**。验收原文要「至少两个来源交叉核对」，本轮只有 Ubersuggest（美国/英语、2026-10-27）。
   Apple Search Ads 需要投放账户（无）、Google Keyword Planner 需要广告账户（无）、
   GSC 真实曝光属 KG-21（`BLOCKED`）。**所以 §2 的所有「量」都只是候选排序依据，不是投放预算依据。**
2. **R1（<500 门槛）本轮零命中、R2（8 周读数）本轮未跑**（见 §1 表右列）。
   两条规则本轮没被任何一行验证过，不许写进「已生效」。
3. **6 个词的量被工具标 outdated**（journaling app、journal prompts、goal tracker templates、
   self care app、mbti test、enneagram test）——本表逐条在量列写了，不靠脚注。
4. **落点多为规划页面**：`/en/ai-journal`、`/en/mood-tracker` 等 12 个 URL 目前不存在，
   挂在 KG-10～KG-16 上。任务未结案前，本表等于「词已对位、板还没钉」；
   如果 KG-10～KG-16 有任何一条被砍，对应词必须回到本表改状态，**不许留成隐性词**。

## 5. 下次复核

- 时间：2026-12-01（月度）。动作：重取量（目标第二来源 ≥1）、按 KG-19 读数跑 R2、
  把已落地页面从「规划」改成实际 URL、逐行更新 §2 的落点列。
- 判停：一个词连续两个月既无曝光又无激活 ⇒ 状态改「淘汰」，并在 §3 分布上可见（闸会重算）。
- 回滚：本表与闸都是新增文件，删掉即回到台账 §4 单表状态，不影响任何运行时代码。
