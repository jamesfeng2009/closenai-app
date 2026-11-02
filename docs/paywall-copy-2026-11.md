# 付费墙文案与绝对化承诺整改（KG-24 / KG-23）· 2026-11-01

> 台账：[`docs/keyword-growth-task-board-2026-11-01.md`](keyword-growth-task-board-2026-11-01.md) 的 KG-24（P1）与 KG-23（P0）。
> 本文件是 KG-24 验收里点名的「证据落点」，同时收纳 KG-23 的 grep 输出——两条改的是同一片可发布文案面，闸门也互相咬合。
> **红线只有一条**：付费墙与落地页不许卖代码里不存在的东西。下面的每一行文案都必须能指回一个可核验的实现位置。

---

## 1. 改前的假账（这是本任务的全部理由）

`git show HEAD:frontend/src/i18n/locales/en.ts` 里 `paywall` 段的旧四条 Pro 权益与四条 Pro+ 权益，和实现的差值：

| 旧文案（改前） | 声称 | 实现真值（可指回的位置） | 差值 |
| --- | --- | --- | --- |
| `Unlimited AI conversations` | 不限对话 | [`backend/config.py:197-198`](../backend/config.py#L197) `pro_daily_chat_rounds = 100` / `pro_plus_daily_chat_rounds = 1000` | Pro 每天 100 轮就会拒；**「unlimited」为假** |
| `Priority model responses` | 更快/优先的模型 | 代码里没有按 tier 分流模型的路径。唯一的 pro 模型开关是 [`backend/config.py:88`](../backend/config.py#L88) `openai_pro_model: str = ""`，**默认空＝不启用**，且注释写明「留空＝未配置，主路径不受任何影响」 | 整条**无实现** |
| `Unlimited personality refreshes` | 不限刷新 | [`backend/services/personality_service.py:563`](../backend/services/personality_service.py#L563) `quota = settings.pro_personality_refreshes if tier == "pro" else 999`；[`backend/config.py:200`](../backend/config.py#L200) `pro_personality_refreshes = 5`（每月） | Pro 是 **5 次/月**，不是不限 |
| `Multiple proactive messages per day` | 每天多条 AI 主动开场 | [`backend/services/cron_service.py:475-477`](../backend/services/cron_service.py#L475)：幂等槽 `proactive:<uid>:<本地日>:0`，注释「slot 恒 0 是刻意的」——**所有档位一天一条** | Pro+ 花钱买的是**已经免费拥有的东西** |
| `100 AI conversations per day`（Pro+ 条目） | Pro+ 100 | 同上 `pro_plus_daily_chat_rounds = 1000` | 数字写小了一个量级，同样是错 |

`config.py:193` 的注释「pro / pro_plus 为高限（≈不限，防异常调用打爆 LLM 成本的熔断上限）」是这些文案的历史来源：设计者心里把高限当不限，于是文案也照心里写。**用户拿到的不是设计者的心证，是那 100 轮。**

另有一处不属于付费墙、但同一红线的：`en.ts:432` 旧值 `It remembers everything you tell it`、`site/src/i18n.ts` 的 `AI companion` / `remembers everything`、`backend/data/copy/zh.json:419` 导出 PDF 水印「它记得你说过的每一句」。见 §4。

---

## 2. 改后：八条权益逐条与代码对齐

文件：[`frontend/src/i18n/locales/en.ts:281-284`](../frontend/src/i18n/locales/en.ts#L281)（Pro 四条）与 `:299-302`（Pro+ 四条）；渲染位置 [`frontend/src/screens/PaywallScreen.tsx:87-98`](../frontend/src/screens/PaywallScreen.tsx#L87)。

### Pro 四条

| 键 | 新文案（en） | 指回的实现 |
| --- | --- | --- |
| `paywall.featureContinuity` | Your entries build on each other: check-ins, reflections and conversations carry your history forward | 记忆/记录链路：`/api/v1/memories`、`GrowthRecordsScreen` 的签到与反思落库；`HomeScreen` 的 AI 开场引用当期记忆 |
| `paywall.featureReports` | Monthly, quarterly and yearly reports drawn from your own records | [`backend/services/growth_service.py:668`](../backend/services/growth_service.py#L668) 月/季/年报按 tier 拦；[`backend/services/share_service.py:761`](../backend/services/share_service.py#L761) 未开通返回 `40302` |
| `paywall.featurePersonalityRefresh` | Your profile is re-drawn as your records change — **5 refreshes a month** | `personality_service.py:563` + `config.py:200`（数字直接取实现值） |
| `paywall.featureTimeline` | 30 / 90 / 365-day look-back: the trend line and the full-year heatmap, unblurred | `InsightsScreen` 的 `<InsightBlurGate locked={!isPro}>` 内的 `MoodLineChart`／`HeatGrid`；`HEAT_FULL_DAYS` 与 `/growth/timeline?days=365` |

键名一并改了：`featureUnlimitedChat → featureContinuity`、`featurePriorityLLM → featureReports`。留旧名＝下一个人还会照着名字把 "unlimited" 写回去，所以**键名本身进了禁语表**（§3 证人②）。

### Pro+ 四条

| 键 | 新文案（en） | 指回的实现 |
| --- | --- | --- |
| `paywall.plusFeatureAllPro` | Everything in Pro | tier 包含关系：`isProTier()` 对 `pro`／`pro_plus` 同真 |
| `paywall.plusFeatureChat` | **1000** conversations a day, for the days you need to think out loud | `config.py:198` `pro_plus_daily_chat_rounds = 1000` |
| `paywall.plusFeatureRefresh` | Profile refreshes raised to **999 a month** | `personality_service.py:563` 的 `else 999` |
| `paywall.plusFeaturePool` | A **1000-message** daily pool covering check-in readings, reflections and the AI's opener | `config.py:196` `pro_plus_daily_messages = 1000`；统一池口径见 `config.py:190-191` 注释 + `backend/services/quota_service.py` |

删除的键：`plusFeatureProactive`（`Multiple proactive messages per day`）。它在实现里**没有任何可卖的东西**——`cron_service.py` 的槽位恒 `:0`，Pro+ 与 free 在这条上完全一致。留着它就是把「一条免费就有的行为」标成付费理由，是这一批里最容易被投诉成「交付失败」（paid but did not receive）的一行。

### 关于台账里那条「更深的历史上下文」

KG-24 的「做什么」列了四个方向，其中「更深的历史上下文」这一格我**没有按字面写文案**，理由：365 天窗对 free 和 Pro 是同一把尺（`/growth/timeline` 的 `days` 参数不分 tier），真正分 tier 的是**模糊门**和**月/季/年报**。所以那一格换成了 `featureTimeline`（回看 + 不再模糊）和 `featureReports`（报告档位），两者都能指回代码；「更深的上下文」目前无 tier 相关实现可指。**如果以后要按字面卖这一条，得先让窗口长度真的分档。**

zh 同步（[`frontend/src/i18n/locales/zh.ts:274-295`](../frontend/src/i18n/locales/zh.ts#L274)）：「你的记录会接着长」「月报、季报、年报，都由你自己的记录汇成」「30 / 90 / 365 天回看：趋势线与全年热力图不再模糊」「每天 1000 条的统一额度」；15 个语言全部改写，不许有语言残留旧数字（§3 证人①）。

---

## 3. 闸门（两条一起钉，防止假绿）

### 3.1 `frontend/__tests__/paywallClaims.test.ts`（KG-24，实测 50 tests）

- **归零闸**：把 `en.ts` / `zh.ts` 的 `paywall` 段切出来（`paywallBlock()` 按 `"paywall": {` 定位），对 15 个语言逐个断言禁语 0 命中：`\bunlimited\b`、`无限|不限`、`priority|优先|優先`、`multiple proactive|多条主动|複数の主動`。
- **正向证人**（缺一即红，防止「改名为绿」）：
  - `RENAMED_FROM = [featureUnlimitedChat, featurePriorityLLM, plusFeatureProactive]` 必须**不出现**，`RENAMED_TO = [featureContinuity, featureReports, plusFeaturePool]` 必须**出现**；
  - 真数字必须落在对应那一条里：Pro 的 `featurePersonalityRefresh` 含 `5`、Pro+ 的 `plusFeatureChat`/`plusFeaturePool` 含 `1000`、`plusFeatureRefresh` 含 `999`。
- 为什么只扫 `paywall` 段：`archiveSubtitle` 里的 "Export everything you've recorded" 是合法表述（导出确实是全部），禁的是把它挂到「记住」或「额度」上。段内扫描＝窄到不会误伤、宽到能覆盖卖点。

### 3.2 `backend/tests/test_absolute_memory_claims.py`（KG-23，40 passed）

闸门放在 pytest 而不是 jest，因为这一条要扫的面跨了 `frontend/src/i18n/locales/*.ts`、`backend/data/copy/*.json`、`site/src/i18n.ts`、`web/*.html` 四个构建目录，而 jest 的根是 `frontend/`，物理上读不到后两个面——第一轮就是这么漏的：`en.ts` 改完了，导出 PDF 的水印还在。

- **归零闸**：14 种语言的「记得 + 全部」搭配正则（刻意写成搭配而非单词表）在上述四面的 0 命中。
- **正向证人**：被扫文件数有下界（少一个目录就红）；`step1Tagline` 等替换后的键在 15 个语言里各存在一次。
- **白名单口径**：不扫 `docs/`、`*.md`、`closenai-prd/`、`CLOSENAI_V3_*`（品牌历史与工作单，台账验收原文写的是「品牌历史文档除外」），不扫 `site/dist/`（构建产物，已在 `site/.gitignore`）。

### 3.3 实测

| 项 | 命令 | 结果（2026-11-01） |
| --- | --- | --- |
| KG-23 | `.venv/bin/python -m pytest backend/tests/test_absolute_memory_claims.py -q` | **40 passed** |
| KG-24 + 25 + 27 相关四套件 | `cd frontend && npx jest __tests__/paywallClaims.test.ts __tests__/moodStats.test.ts __tests__/nextStep.test.ts __tests__/funnel.test.ts` | **4 suites / 148 tests passed** |
| 全量前端 | `npx jest --silent` | 60 suites / 1694 tests 全绿（基线 57 / 1583） |
| 类型 | `cd frontend && npx tsc --noEmit` | 无输出（须从 `frontend/` 内跑；从仓库根跑会假报 TS5101/TS5107） |

**变异自证**（`/tmp/kg_batch1_mutation.py`，7 条串行；每条先跑基线必须绿，改字面量，只跑目标套件，记红数与首个失败用例名，从 `/tmp/kg1_mutation_backup` 备份写回——不用 `git checkout`，再复跑基线并比对文本）：

| 破坏点 | 红的判据 | 红数 |
| --- | --- | --- |
| 把 `en.ts` 的 `featureContinuity` 改回 `Unlimited AI conversations` | paywallClaims | 2 |
| 把 `plusFeaturePool` 的 1000 改成 100 | paywallClaims（数字证人） | 2 |
| `web/index.html` 塞回 `It remembers everything you tell it` | test_absolute_memory_claims | 2 |
| 删除 `zh.json` 水印行的正则命中面（少扫一面） | test_absolute_memory_claims（文件数下界） | 2 |

7 条全部「按预期被打红、归属正确、写回后复跑绿」。

---

## 4. KG-23 的 grep 输出（实测留档）

扫描命令（排除 `.git`／`node_modules`／`dist`／`.venv`）：

```
grep -rniE "remembers everything|remembers every conversation|记得你说过的每|记住你所有" .
```

命中的**全部**是白名单面与闸门自身，代码/文案面 0 命中：

- `CLOSENAI_V3_TASK_BOARD.md:119`、`:1370`、`:1379` — 工作单对旧文案的记录（T7-05 阻塞说明原文）
- `docs/closenai-review-competitive-seo-geo-2026-10-25.md:118`、`docs/keyword-growth-implementation-plan-2026-11-01.md`（多处）、`docs/keyword-growth-task-board-2026-11-01.md:78/142/221/311` — 计划与台账
- `backend/tests/test_absolute_memory_claims.py:7`（docstring 里的案例）与其 `__pycache__/*.pyc` — 闸门自身

替换后的实际值：

- [`frontend/src/i18n/locales/en.ts:442`](../frontend/src/i18n/locales/en.ts#L442) `"step1Tagline": "It remembers what you choose to share"`；`:444` `step1Feature1Desc` 带 "and you can delete any of it"
- [`frontend/src/i18n/locales/zh.ts:435`](../frontend/src/i18n/locales/zh.ts#L435) `"step1Tagline": "你选择分享的内容，它都记得"`；`:437` 带「任何一条都可以删除」
- [`site/src/i18n.ts`](../site/src/i18n.ts) `foot.tag` → `It remembers what you choose to share`；`meta.description` → `It remembers what you choose to share, how you felt, and who you are becoming`
- `backend/data/copy/*.json` 水印行：15 语逐条改为「你选择分享」口径

**「可删除」这一句不是文案装饰**：`step1Feature1Desc` 声称可删，实现侧对应 `MemoriesScreen` 的单条删除 + 账号注销的数据删除路径；`web/privacy.html` 里同样表述。文案承诺的能力必须和隐私政策一致，这是台账 KG-23 验收第三条的意思。

---

## 5. A/B 假设与判停指标（台账 KG-24 验收要求）

**假设 H**：付费墙上「额度数字」换成「长期价值 + 真实数字」后，`paywall → 订阅成功` 的转化率不下降；若下降超过阈值，说明这个人群买的就是额度，价值叙事不是我们的杠杆。

- 对照组 A：旧四条（`Unlimited AI conversations` / `Priority model responses` / `Multiple proactive messages per day`）。**注意：A 组是在卖不存在的能力，所以它只能作为短期读数对照，不许作为「效果更好的旧文案」回滚。** 这一点写在这里是因为回滚是这条实验唯一可能让人动手的理由。
- 实验组 B：本文件 §2 的八条。
- 主指标：`paywall_view → purchase_success` 转化率（事件在 `frontend/src/services/analytics.ts`；`section` 承载页位，`properties` 白名单里没有 `step`，见 [`docs/metrics-dictionary.md`](metrics-dictionary.md)）。
- 次指标：**退款率**与「付费后没拿到」类客服工单数（交付失败）。这两个是这条改动的真实靶子——假文案的代价不体现在转化上，体现在退款和投诉上。
- 判停：
  - B 组转化相对 A 组下降 >20%（相对值）且样本 ≥200 次付费墙曝光 → 停实验，但**修法是把真数字讲得更值（比如把 100 轮换种说法），不是把 "unlimited" 加回去**；
  - 任一窗口退款率上升 → 立即停，回到 B 组以外的文案要先过 §3 的闸；
  - 4 周无 ≥200 曝光 → 记「读数未跑」，不许写「无差异」。
- 前置：灰度需要 `KG-31` 的切流面与埋点生产流量；当前 `§7` 的四段断点未补齐，**这条实验现在跑不起来**，写下来是为了跑起来那天有预设而不是事后挑指标。

---

## 6. 未验证 / 未做（按台账口径如实记）

1. **付费墙截图未出**。本机无法渲染 `.tsx`（jest node 环境），`web/index.html` 与 `site/` 也不含付费墙。§2 的文案 diff 是代码面证据，不是界面证据。真机截图待补，属 KG-08/KG-10 的截图动作。
2. **法务确认未做**。「可删除/可撤回」与隐私政策一致性、绝对化承诺清零，需要人签；本文件只给「实现真值 ↔ 文案」的对照，不代法务结论。
3. **`site/` 仍是未发布的第二源**，`site/astro.config.mjs` 还写着「CF Pages build output = site/dist」，而实际部署路径是 `web/`（`scripts/deploy_web.sh:14/25`）。发布源收敛属 `T7-05`，不在本批点名范围。
4. **A/B 未跑**，§5 是预设。当前无 ≥200 曝光的流量。
5. **禁词残留（本批未顺手修，已记）**：
   - `backend/ai/llm.py:248`、`:306` 默认系统提示词 "…caring AI companion."
   - `backend/services/message_generator.py:112`、`:381` "warm, insightful AI companion"
   - `site/src/i18n.ts:26`、`:59`、`:74` 的 "companion"
   - `web/index.html:527` 「成长陪伴」
   - `frontend/src/i18n/locales/en.ts:446` `step1Feature2Desc` "AI finds patterns in your records — and flags them before you spiral"（"before you spiral" 是可被读成预判/诊断的表述，但不在 §3 的禁语表内）
   - `backend/data/copy/en.json:416` 水印 "it remembers what you told it"（能过 §3 的「记得+全部」搭配闸，语义仍偏绝对）
   这些属「产品定位文案」桶（KG-01/KG-23 的 AI companion 命名面），归 2026-11-01 台账里 P0 决策项；**不并入本批**的理由是：改系统提示词会改变 AI 输出行为，属于需要重跑 AI 侧断言的动作，而不是文案 diff。
6. **付费档「unlimited」措辞未改（2026-11-01 由 KG-26 顺带发现，按口径不并入那一批）**：
   - `frontend/src/i18n/locales/en.ts:343` `reflect.quotaMessage`、`:829` `settings.faqQuotaA`、`:831` `faqSubscriptionA` 三处仍写 "unlimited conversations"。
   - 实现真值：`backend/services/quota_service.py:33-38` 给 `pro`／`pro_plus` 各设了 `pro_daily_messages` 与 `pro_daily_chat_rounds`，即付费档**仍受每日上限**，只是高得多——"unlimited" 不实。
   - 根因：本任务的逐条核对表按「价目那一屏的键」圈定范围（§2 的 8 条），`reflect`／`settings` 里的付费话术不在那张表上，因此没被同一次改写覆盖。
   - 归桶：**KG-24 复评**（台账状态 `REVIEW`，可回炉）。要收口应把 `unlimited|无限|不限` 扩成整份 locale ＋ `backend/data/copy/*.json` 的禁词闸（现在只有 KG-26 那条 `promptLibrary.test.ts` ③ 钉本批 13 个新键），改闸后整体重跑，而不是逐文件手改。
