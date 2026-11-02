# 首日价值路径（KG-25）· 2026-11-01

> 台账：`docs/keyword-growth-task-board-2026-11-01.md` §KG-25。
> 方案：`docs/keyword-growth-implementation-plan-2026-11-01.md` §5.2/§7。
> 这一份是**结案证据**：路径定义、埋点台阶、死胡同审计、失败恢复、测试结果、以及哪些还**没验证**。

## 1. 路径原文与本轮补的台阶

PRD §5.19.5 的五段：初读卡 → 第一条记录 → 首次签到 → 第一周报告 → 分享。
本轮把这五段在**代码里数得出来**，并补上台账点名的 5 个新台阶（`onboarding_completed` /
`first_reflection_saved` / `first_evidence_insight` / `first_action` / `first_outcome`）。

九个一次性台阶（`frontend/src/services/funnel.ts:29-38`，事件名一律 `funnel_<step>`）：

| 台阶 | 触发点 | 判据（什么时候才算达成） |
|---|---|---|
| `onboarding_completed` | `src/screens/OnboardingScreen.tsx:152` | 引导三步走完、初读卡已存、`onComplete()` 之前 |
| `first_read_opened` | `src/screens/FirstReadCardScreen.tsx:58` | `if (card)`——空卡不算看到初读卡 |
| `first_record_created` | `src/screens/ReflectScreen.tsx:303` | `if (messages.length === 0)`——发送前对话为空，即这轮的第一条 |
| `first_reflection_saved` | `src/screens/ReflectScreen.tsx:409`（`persistReflection` 内，唯一发射点） | 两条出口（采纳草稿／照提示自己写）保存成功之后；2026-11-01 KG-26 把原来两处调用收成一处，见 `docs/growth/prompt-library-2026-11.md` §4 |
| `first_checkin` | `src/screens/HomeScreen.tsx:336` | `await moodCheckin()` 成功之后（catch 之前），本设备首次 |
| `first_evidence_insight` | `src/screens/InsightsScreen.tsx:210` | 洞察列表里**至少一条 `evidence_count > 0`**——没有依据回链的洞察不算看到证据 |
| `first_action` | `src/screens/InsightsScreen.tsx:397` | `await createAction(...)` 返回之后 |
| `first_outcome` | `src/screens/GrowthRecordsScreen.tsx:217` | 行动结果回执 `changed` 拿到之后（结果回访真的落库） |
| `week1_report_opened` | `src/screens/Week1ReportScreen.tsx:58` | `if (report)`——报告真的投递到才计数 |

两个可重复环路事件：`funnel_share_tapped` / `funnel_share_completed`，按 `source` 分物料
（`first_read` / `personality_card` / `period_report` / `year_review` / `memory` / `life_archive`）。

口径要点（为什么这么定）：

- **前九段一次性**：签到/发消息每天都重复，不门闸的话分母被 DAU 淹没，段间转化率没法读。
- **分享不一次性**：分享次数是病毒环路的分子，要按物料累计。
- 属性只用 `feature` / `source` 两个键——`step` **不在**双端白名单里
  （`frontend/src/services/analytics.ts` 与 `backend/services/analytics_service.py` 的
  `ALLOWED_PROPERTY_KEYS` 各一份），新键要么两处一起加、要么静默丢整条属性。
  引导卡点击用的 `next_step_tapped` 同理把台阶放在 `section` 上，不放 `step`。

## 2. 门闸＝唯一真源

`trackFunnelOnce` 写的 AsyncStorage 键 `@closenai/funnel_once_<step>`（`funnel.ts:onceKey`）
既是**埋点门闸**，也是**引导卡的完成判据**（`src/services/nextStep.ts:readCompletedSteps`）。
不新加 `/next-step` 端点，也不另立一套「做过没有」的键：那样界面喊你去写反思、
PostHog 那边 `funnel_first_reflection_saved` 早就报过，两处会各说一套。

## 3. 「今天可以做什么」引导卡

位置：Home 屏 Streak 条之上（`src/screens/HomeScreen.tsx:559-573`），`loop_done` 时整张收掉。

判定顺序（`nextStep.ts:decideNextStep`）：今天没签到 → `checkin`；否则按
`reflect → insight → action → outcome` 找第一个没完成的格；都完成 → `loop_done`。

落点（`nextStep.ts:STAGE_TARGET`）：

| 台阶 | 落点 | 路由在路由表里 |
|---|---|---|
| checkin / reflect | `Tabs` → `Reflect` tab | `App.tsx:107` |
| insight / action | `Insights` | `App.tsx:309` |
| outcome | `GrowthRecords` | `App.tsx:284` |

## 4. 死胡同审计（本轮做的三件事）

1. **每一格都点得动**：五格的落点逐个对着 `App.tsx` 的路由表核过（上表），
   `nextStep.test.ts` 里有一条用例扫这三张路由名与 `name="Reflect"` 这个 tab 名，
   引导不许指向不存在的屏。
2. **不许出现「报了但没人报」的漂移**：`funnel.test.ts` 现在扫全 `src/screens/*.tsx`，
   要求 ① 枚举外的 step/source 一个都不许出现 ② 九个一次性台阶**每一个都有调用点**
   ③ 同一个 step 不许有两个调用文件（两处写同一格＝这条台阶的口径分裂）。
3. **空态与不可达要说得清**：`first_evidence_insight` 依赖「洞察带依据回链」，
   现网 Go 侧 evidence→pattern→insight 的发射链还没通（KG-31，T7-04/T7-07 未结案），
   所以这一格**在生产上暂时点不亮**。这一条不藏：见 §6 未验证清单，也写进台账 §14 的依赖修订。

## 5. 失败恢复（至少一处，实际四处）

| 断点 | 恢复动作 | 出处 |
|---|---|---|
| 签到失败/配额耗尽 | 40301 → 升级引导弹层；其余错误显式 `showError`，不静默 | `src/screens/HomeScreen.tsx:onMoodPress` |
| 洞察列表加载失败 | 「加载失败 + 重试」按钮，重新 `load()` | `src/screens/InsightsScreen.tsx:696-702` |
| 行动列表加载失败 | 两处重试按钮（列表与结果弹层各一） | `src/screens/GrowthRecordsScreen.tsx:279,371` |
| 结果记录失败 | **不关弹层**：行动已 completed 而结果没记上，关掉就是骗用户说记过了 | `src/screens/GrowthRecordsScreen.tsx:218-222` |
| 情绪统计拉不到 | 本轮 KG-27 补的失败态 + 重试 | `src/screens/InsightsScreen.tsx`（`moodLoadFailedTitle`） |

## 6. 测试结果与闸门自证（2026-11-01 实测）

```
npx jest funnel        → 56 passed
npx jest nextStep      → 9 passed
npx jest --silent      → 60 套件 / 1694 用例全绿
npx tsc --noEmit       → 0 错
```

变异自证（`/tmp/kg_batch1_mutation.py`，串行、只从备份写回、写回后 cmp 字节一致并复跑）：

| 闸门 | 注入的破坏 | 结果 |
|---|---|---|
| `funnel.test.ts` | 从 `ONE_TIME_STEPS` 删掉 `first_outcome` | 红 2 条（枚举计数 + 全仓 step 白名单）｜写回一致｜复跑绿 |
| `nextStep.test.ts` | 把判定顺序改成 `reflect→action→insight→outcome` | 红 2 条（顺序用例 + 不跳过用例）｜写回一致｜复跑绿 |

## 7. 未验证（不伪造）

- **5 名内部用户走通记录**：`未验证`。本机没有可分发的真机池，也没有真实新用户；
  台账写的是「没有真实用户时写未验证」。要取证需要一台装了对应语言包的 iPhone + TestFlight 分发。
- **屏内文案 15 语言真机渲染**：`未验证`（i18n 键齐备由 `i18nLocales.test.ts` 107 条覆盖，
  排版长度只在源码层守卫，未见截图）。
- **`first_evidence_insight` 的现网可达性**：`未验证`，且现网 Go 链路未通（§4.3）。
- **段间转化率读数**：`未跑`——PostHog 侧需要真实流量与事件落库，属 KG-31/T7 的面。
