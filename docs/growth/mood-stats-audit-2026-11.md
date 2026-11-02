# 情绪统计可见性审计（KG-27）· 2026-11-01

> 台账：`docs/keyword-growth-task-board-2026-11-01.md` §KG-27。
> 方案：`docs/keyword-growth-implementation-plan-2026-11-01.md` §5.4。
> 约束复述：**不新建情绪数据模型、不改服务端契约**——本轮确实一个端点都没加、一个 schema 都没动。

## 1. 改之前的现状（逐条实测）

| 位置 | 改前状态 | 问题 |
|---|---|---|
| `frontend/src/screens/HomeScreen.tsx:61` | `HEAT_DAYS = 14`，首页只铺近 14 天 | 首页那份是「快捷条」，不是统计视图 |
| `frontend/src/screens/InsightsScreen.tsx`（改前 911 行） | 整张情绪卡挂在 `{!!timeline?.days?.length && ( ... )}` 上 | **新用户/拉取失败的人看到的是一片空白**，连「先签到一次」都没有 |
| 同上（改前 923 行） | `MoodLineChart days={recentDays(timeline.days, 7)}` 写死 7 天 | 台账要的 30 天视图不可达 |
| 同上（改前 58 行） | `const TREND_DAYS = 7;` 摘要窗写死，且跟着首屏 `Promise.all` 一次取完 | 切窗要连着换摘要，否则「30 天的折线 + 7 天的结论」 |
| 同上（改前 90/365） | 热力图有 90/365 切换，在 `InsightBlurGate locked={!isPro}` 内 | 保留：Pro 分界不因这一条改变（见 §5） |
| `frontend/src/screens/ProfileScreen.tsx` / `ExploreScreen.tsx` | **没有任何一条通向情绪统计的入口**（`Insights` 只从洞察流里进） | 台账点名的「从 Explore/Profile 可达」缺失 |

服务端侧不用改的理由（取证，不是推定）：

- `backend/routers/mood.py:67-69` → `days: int = Query(14, ge=1, le=90)`，30 在窗口内；
- Go 侧逐字镜像同一档：`go-core/internal/domain/mood.go:48-52`
  （`MoodTrendMinDays = 1` / `MoodTrendMaxDays = 90`），并且越界有闸：
  `go-core/internal/httpapi/mood_handlers_test.go:192-196` 对 `days=0`/`days=91` 报 400；
- 折线的数据源本来就不是 `/mood/trend` 而是 `GET /growth/timeline`（一次拉 365 天），
  所以 7→30 的切换在客户端只是**截不同的尾巴**，一个请求都不用多发。

## 2. 改了什么（前端五处）

1. **窗口切换**：`MOOD_RANGE_OPTIONS = [7, 30]`（`InsightsScreen.tsx:62-64`），
   两枚 chip 在卡头，`accessibilityState={{ selected }}` 给读屏；
   折线与趋势摘要**共用同一个 `moodRange`**：
   `const moodDays = recentDays(timeline?.days ?? [], moodRange)` → `<MoodLineChart days={moodDays} …>`，
   摘要走 `loadMoodSummary(moodRange)`。
2. **摘要单独取**：`load()` 的首屏 `Promise.all` 从 4 个降到 3 个；
   摘要改由 `loadMoodSummary` 取——切窗时先 `setTrend(null)`（不留上一窗的残句），
   再用 `summarySeq` 只认最后一次返回（连点两下时两次请求可能反序到达）。
3. **三态分开说**（`InsightsScreen.tsx:1004-1054`）：
   - `!timeline`（拉取失败）→ 「情绪记录没加载出来」+ **重试**；
   - `!anyMoodData`（整年一条没记）→ 「还没有情绪记录」+ 引导文案 + **现在去签到**；
   - 有数据 → 折线 + 摘要 + 热力图；其中当前窗口为空但整年有记录时，
     另给一句「近 7/30 天没有签到记录」，不静默画一张空图。
4. **入口两条**：`ExploreScreen.tsx`「更多探索」新增 📊 格子、`ProfileScreen.tsx`
   「AI 与记忆」段新增一行，都走 `navigation.navigate('Insights', { initialSection: 'mood' })`；
   `App.tsx:82` 把 `Insights` 的参数声明成 `{ initialSection?: 'mood' } | undefined`，
   InsightsScreen 用 `onLayout` 拿到卡片偏移后 `scrollTo` 一次（`moodScrolled` 防重复滚）。
5. **空态判据上收成纯函数**：`recentDays` 与 `hasMoodInWindow` 移到
   `src/components/charts/transforms.ts`——`hasMoodInWindow` 的阈值直接用折线的 `VALUE_MIN`，
   于是「图上是空的」与「屏上说还没记录」是同一条规则，不会各写一个 `>= 1`。

## 3. 一个判据、不许两处真源

| 会被读成两处的东西 | 这一轮的处理 |
|---|---|
| 「这段情绪算高还是低」 | 只用后端 `summary`（`mood_service.py` 三档，按 `X-Language` 出文案），前端不重算平均值 |
| 折线的日期轴 | 只用 `/growth/timeline` 的 `days`；`/mood/trend` 的 `labels` 是 `%m-%d` 不带年份，不拿来当轴 |
| 窗口长度 | 一个 `moodRange` 同时决定折线截多少天与摘要请求的 `days` |
| 「有没有记录」 | `hasMoodInWindow`（与折线断线同一阈值），不在屏上另写 `values.length > 0` |

## 4. 跨年与时区（台账要求「有测试或明确限制」）

测试：`frontend/__tests__/moodStats.test.ts`（33 条用例）。

- 跨年截窗：`2025-12-28 … 2026-01-03` 这一周，`recentDays(..., 5)` 必须给回
  12-30/12-31/01-01/01-02/01-03；用例里带**反向证人**——用「一年中的第几天」排序会
  把 1 月排到 12 月前面，断言那条错序确实与正确结果不同，防用例自己变成空跑。
- 跨年格子：同一周手工推出的期望矩阵（周日 12-28 独占第一列，其余六天进第二列）逐格对比，
  值也带上（12-31=5、01-01=1，跨年不重置）。
- 结构性判据：`recentDays` 的实现里不许出现 `new Date` / `getTime` / `toLocale` / `getDay()`，
  只做 `YYYY-MM-DD` 字典序；`toHeatMatrix` 必须用 `getUTCDay` / `toISOString`，
  且日期串解析必须带 `Z`。
- **明确限制（记在这，不当已验证）**：jest 沙箱里改不动 `process.env.TZ`——
  实测设成 `Pacific/Kiritimati`（UTC+14）后 `new Date('2026-01-01T00:00:00Z').getHours()`
  仍返回 8（宿主 `Asia/Shanghai`），所以「同一份数据在 UTC+14 与 UTC-12 铺出相同矩阵」这句
  只能在实现层保证 + 本机（+8）手算格子来兜，**跨时区真机对比未跑**。
  这条限制同时也是护栏生效的条件：本机 +8 下，只要有人把 `T00:00:00Z` 改成不带 `Z` 的本地解析，
  UTC 日会往前挪一天，手算矩阵用例立刻红。

## 5. 保留的付费分界（不在本轮动它）

热力图与折线本体仍在 `InsightBlurGate locked={!isPro}` 内（`insightTier.isProTier`），
`monthly/quarterly/yearly` 报告仍走 Pro 门（`growth_service.py:668`、`share_service.py:761` → 40302）。
本轮只把**入口、7/30 控件、空态与失败态**放到门外：免费用户看得见「去签到」和「拉取失败可重试」，
看不见的是聚合图本身。如果之后要让免费用户看到 7 天折线，那是 KG-24 的权益改动，另开任务。

## 6. 测试与闸门自证（2026-11-01 实测）

```
npx jest moodStats           → 33 passed
npx jest --silent            → 60 套件 / 1694 用例全绿（基线 57/1583 → +3 套件 +111 用例）
npx tsc --noEmit             → 0 错
```

变异自证（`/tmp/kg_batch1_mutation.py`，串行、备份写回、写回后 cmp 一致并复跑）：

| 注入的破坏 | 被打红的用例 | 结果 |
|---|---|---|
| `recentDays` 换成「月日数字」排序 | 4 条（跨年截窗 + 乱序 + 卡片常驻组内的窗口用例） | 红 → 写回一致 → 复跑绿 |
| `hasMoodInWindow` 恒为真 | 2 条（全 0 判空 + 阈值同源） | 红 → 写回一致 → 复跑绿 |
| 把卡片重新挂回 `{!!timeline?.days?.length && (…)}` | 1 条（「旧的那个死条件已经不在了」） | 红 → 写回一致 → 复跑绿 |

另外，`orphanEndpoints.test.ts`（D-G9 那条「不换折线数据源」的旧闸）已按新形状重写：
现在它钉的是「`moodDays = recentDays(timeline?.days ?? [], moodRange)` +
`<MoodLineChart days={moodDays}>` + `days=\{trend` 一条都不许出现」，
并新增 `summaryBody()` 抽取断言（游客守卫、`setTrend(null)`、`summarySeq`），
不再是「文件里某处出现过 getMoodTrend」这种弱判据。

## 7. 未验证 / 未拍（不伪造）

- **入口截图、空态截图、7/30 两张图**：`未拍`。本机没有 iOS 模拟器取证流程（见
  `docs` 里的取证能力缺口记录），台账要的「截图与测试」这轮只交了测试。
  补法：TestFlight 包 + 一台干净账号（无签到）与一台有 40 天记录的账号各截两张。
- **免费用户看到的模糊态**：`未验证`（需要非 Pro 真机账号）。
- **`/mood/trend` 的契约归属**：Python 路由上带 `deprecated=True`（`backend/routers/mood.py:67`），
  这是 N8 批 5 的「path 还没上、先不标废弃」的既登记状态，**不是本轮新引入的风险**；
  但放量前若这条 path 迁走，摘要窗口的取数处要跟着改，记在这里备忘。
