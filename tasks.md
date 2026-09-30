# ClosenAI 实施任务分解（tasks.md）

> 拆分依据：PRD v2.1（closenai-v2.1-clean.html，至 2026-09-28 第六批）· 《架构与技术方案-2026-09-06.md》· 《P0-P1行动方案-2026-09-06.md》· UI 原型 closenai-p0-prototype.html（至 2026-09-28 修订）· 《沟通证据与措辞规范-2026-09-28.md》
> 状态核验日期：2026-09-28（代码实测，非文档推断）
> 图例：`[ ]` 未开始 · `[~]` 进行中 · `[x]` 完成 · 🔴 提审阻塞 · ⚠️ 冲突/风险

---

## 进度快照（实测结论）

- Onboarding 文案改写（P0-1）：✅ 已完成（15 语言 i18n + 原型 Screen 7）
- 后端 M1–M7 / P1-3 管线：**全部零实现**（grep 无 journey_events / onboarding_profiles / bedtime / curriculum）
  - ↳ 2026-09-29 更新：M7（占星移除）、M1（初读卡）、M2（第一周剧本）、M3（情感回报进 LEVEL_UNLOCKS）、M4（分享卡深链与归因）、M5（演示档案，纯前端）、M6（Life Archive IAP）已落地，见 A/B 节勾选；↳ 2026-09-30 更新：bedtime / curriculum 已随 `4b87018`（P1-C1/C2 + vercel cron 注册）落地——**M1–M7 / P1 管线至此全部零欠账**
- 工作区在途（未提交）：~~九型人格 service、§5.18 承诺回访、discovery 融合、前端 Reflect/Insights/i18n~~ → 已在 `0ced5ac`/`48d37ed` 提交；2026-09-29 快照：B2 全套已提交，`closenai-prd` submodule 仍有未提交内容（归 D4）
- ⚠️ **方向冲突**：近三次 commit（13313c3/efbd7ec/e89f19c）在**新增**本命星盘 + pyswisseph，而 M7 计划是整体移除占星——✅ 2026-09-28 已按 PRD v2.1「占星已移除」执行 A1，冲突关闭
  - ↳ 2026-09-29 复核：A1 只删了运行时，推导链/响应字段/画像页展示/个人页「主要框架=占星」/15 语言星座文案仍在 → 见 G 节，已全面清理并加回归守卫

---

## A. Sprint 1｜提审阻塞 🔴

- [x] ⚠️ **A1 · M7 占星模块移除**（0.5d）｜架构方案 §二 M7 ｜✅ 2026-09-28 完成
  - [x] 删 `backend/routers/natal.py`、`services/natal_service.py`、`models/natal.py`、`repositories/natal_repository.py`、main.py 注册（含 test_natal_service.py）
  - [x] 删前端 `NatalChartScreen.tsx` + ExploreScreen tile + App.tsx 导航注册 + api.ts/types + i18n 15 语言 natal 键
  - [x] 移除 `pyswisseph` 依赖（requirements.txt + pyproject.toml）
  - [x] DB 表保留（alembic 迁移文件不删）；enneagram 不动
  - 验收实测：运行时代码 grep 无 natal 引用（残留"占星"字样均为提示词内**禁用措辞约束**，属预期）；`from backend.main import app` OK；pytest 217 过（2 个失败为工作区在途的 follow_up 签名/personality 配额改动，与 M7 无关）
  - ✅ 补验：A2 期间已重装 node_modules 并跑 `tsc --noEmit`（exit 0）+ jest 11 套件 88 例全过，M7 删除的前端引用无类型残留
- [x] **A2 · M6 Life Archive IAP**（后端 3d + 前端 2d）｜架构方案 §二 M6 / 行动方案 P0-2 ｜SKU 形态已拍板：维持一次性买断（架构方案 §九-4，2026-09-28）✅ 2026-09-29 完成（代码+测试；sandbox 实机三档验收待提审前）
  - [x] 清理 `iap_service.py:71` `life_archive → pro_plus` 别名（买断走 user_entitlements，不走 _map_tier）
  - [x] Pro 联动权益：订阅期内导出折扣/每月 1 次免费生成（订阅加分，不取消买断）
  - [x] 新表 `user_entitlements` + alembic 迁移
  - [x] `iap_service` non-consumable 分支（App Store Server Notifications v2）
  - [x] `POST /api/v1/share/life-archive`（entitlement 校验 + worker 后台渲染 PDF + 幂等）
    - ⚠️ 偏离：PDF 为**同步渲染**（v1）。档案只编排既有落库内容、零新增 LLM 调用，成本只在本地排版；后台渲染待 E3（arq+Redis）落地后切换 + 完成推送。已在 `routers/share.py` docstring 记录。
  - [x] Paywall 第四区块"永久保存你的档案"（视觉分离订阅区）+ 恢复购买
  - [x] AIGC 章节 GB 45438-2025 显式标识（仅 AI 产出章节 portrait/patterns/insights 逐章标注 + 文档级声明；用户原话与确定性统计不标注）
  - 验收：sandbox 三档 购买→导出→卸载重装恢复
    - 实测覆盖：`backend/tests/test_life_archive.py`（18 例：买断不改 tier / 混合收据双链路 / 恢复幂等 / 跨账号拒发 / 退款回收 / 40304 / 档位向下兼容 / 订阅每月 1 次免费 / 1y 窗口 / PDF 渲染）+ `frontend/__tests__/lifeArchiveConfig.test.ts`（49 例：SKU 对齐 ASC、档位序与后端 SCOPE_RANK 同口径、15 语言文案齐备）
    - ⏳ 真机 sandbox 流程（购买→导出→卸载重装恢复）需 dev build，本机 Expo Go 不可用，留待提审前 D5 阶段
- [x] **A3 · M3 §5.15a 情感回报进 LEVEL_UNLOCKS**（1.5d，依赖 A4 的 journey_events 表先建）✅ 2026-09-29 完成
  - [x] L2「AI 提起你说过的事」/ L3「Monthly Story 预览」/ L4「90 天信」（`LEVEL_REWARDS` + `_merge_level_unlocks` 把回报合到 `LEVEL_UNLOCKS` 首位；zh/en 先行，其余语言按架构方案 §二 M3 跟 i18n 批次）
    - ↳ 2026-09-30 校正：这里的「跟 i18n 批次」**只兑了前端那一半**——13 个语言的 locale 已由 L1 清账；`LEVEL_UNLOCKS` / `LEVEL_REWARDS` / `MILESTONE_NAMES` 是后端 zh/en 二值字典，非 zh 用户至今仍拿英文，见 L1 的 ⏳ 与 #42
  - [x] level 变更检测写 journey_events（kind=level_reward，day_no=LEVEL_THRESHOLDS[level]=7/30/90，scheduled_at=下一个用户本地 push 窗口，payload.reward_key 供 B2 调度管线分派正文）
  - 验收实测：`backend/tests/test_level_reward.py` 7 例（L2/L3/L4 回报键与 day_no、scheduled_at 落本地 push 窗口、同日幂等不重复写行、庆祝消息不重复列回报、等级页逐档 reward）；全量 pytest 243 passed；前端 tsc exit 0 + jest 88 passed
  - ⏳ 交给 B2：level_reward 的**正文渲染**（L2 记忆引用 / L3 月报缩略 / L4 长信）与 memory_refs 引用依据，由 `dispatch_journey_events` 按 payload.reward_key 分派（架构方案 §二 M2/M3 同一张表、同一套调度）

## B. Sprint 2｜过桥获客（P1）

- [x] **B1 · M1 性格初读卡 B01**（后端 2d + 前端 2d + 文案 1d）｜架构方案 §二 M1 ✅ 2026-09-29 完成
  - [x] 表 `onboarding_profiles` + `first_read_service` + 静态 JSON 模板库（零 LLM）
    - 模板库 `backend/data/first_read/{zh,en}.json`：3 题 × 3 选项 + `assertions` 槽位 + `summary_frame` + `closer` + `fillers` + 16 型 `personas`；非 zh 语言回退 en（与措辞规范 §六 决策点 3 的 v1 词表口径一致）
    - 生成率 100%（PRD §5.19.2 硬指标）：0 答案 / 未知题目 / 闸拦截条目一律用 `fillers` 补至 2 条，上限 3 条
  - [x] `POST/GET /onboarding/first-read`、`/share/first-read-card`
    - 出口过闸：新增 `safety_service.filter_template()`（架构方案 §六-1）——**只跑确定性黑名单 + 越界句式，不调 LLM**；静态模板由我方撰写，正则对它有 100% 把握，语义分类器留给不可控产出
    - ⚠️ 偏离：`POST /share/first-read-card` v1 用既有 reportlab 文本管线出 PDF；QR + 水印 + 深链归因（M4/B4）落地时替换内部渲染，**端点签名不变**（已在 `share_service.export_first_read_card_pdf` docstring 记录）
  - [x] OnboardingScreen 快问 3 题 + `FirstReadCardScreen`（UI 原型 Screen 29 已就绪）
    - 引导变 3 步（Step 3 快问），选项 id 与后端模板对齐、文案走 15 语言 i18n；Step 2「跳过」不再直接结束，改为进入 Step 3
    - ⚠️ 游客态约束：引导阶段无 token，`POST /onboarding/first-read` 必失败，因此答案先落 AsyncStorage（`@closenai/pending_first_read`），**登录/注册成功后由 AuthContext 补调**并缓存卡片（沿用 `@closenai/pending_mbti` 既有模式）；POST 失败时暂存不删，下次登录重试
    - 入口：`HomeScreen` 顶部 D0 卡片入口（有缓存卡且未读时展示）→ `FirstReadCardScreen`；CTA「开始书写你自己的」→ Reflect Tab（PRD §5.19.2 漏斗第一环）
  - 验收实测：`backend/tests/test_first_read.py` 22 例（复述自述 / 零 LLM / 空答案兜底 / 上限 3 条 / 未知项跳过 / 按文案反查 / fr 回退 en / personas 无类型不出 summary / zh-en 同构 / 模板逐条过闸 / 违规槽位被丢弃 / upsert 幂等单行 / 语言不漂移 / 40402 / PDF `%PDF` 头）；`frontend/__tests__/firstRead.test.ts` 36 例（暂存 peek-clear 语义 / 脏缓存按 null / 已读标记 / 15 语言 × 26 键齐备）
  - 全量：pytest 315 passed；前端 tsc exit 0、jest 12 套件 124 passed
- [x] **B2 · M2 第一周剧本 B02**（后端 3d + 前端 1.5d + 文案 1d）｜架构方案 §二 M2 ✅ 2026-09-29 完成
  - [x] 表 `journey_events`（7 种 event_kind，事务内预写 pending）
    - 预写在 onboarding 事务内一次算完（架构方案 §二 M2「不靠 cron 现算」）：D0/D1/D2/D3/D5/D7 六条 pending，`scheduled_at` 按用户本地时区落当日窗口；过期日直接标 `skipped`，**不补发**；同日重复调用幂等（不重复写行）
    - 模板库 `backend/data/journey/{zh,en}.json`，零 LLM；非 zh 语言回退 en（与 B1 词表口径一致）
  - [x] `dispatch_journey_events` 挂 cron + 沉默降频
    - `GET /api/v1/cron/journey`（`verify_cron_secret` 守卫）→ `cron_service.dispatch_journey_events`
    - 剧本事件写 `message_type="proactive"`，因此天然进既有主动消息去重与统一配额池；`deliver` 四条让路判定（今日已发 / 今日已发 LLM 主动消息 / 今日命中危机 / 静默 ≥2 天）→ 命中只 `defer`，**不删行**；反向让路：`journey_repository.has_pending_due` 使 LLM 主动消息在剧本到点时避让
    - `ai_consent` 未同意则整条剧本不发（合规 5.1.2(i)）；D2 是唯一带 push 的 kind（`PUSH_KINDS`，复用 `push_copywriter.build_copy`）
    - level_reward 正文由 `payload.reward_key` 分派进同一套调度（接 A3/#5 遗留项）
  - [x] D7《第一周观察报告》= memory 原话引用 + 周统计（v1 纯模板）
    - D7 需 ≥2 条可引用原话才发，不足则 `skipped`；统计口径进 `payload.evidence`，**不进** memory_refs
  - [x] **引用依据展示**：payload.memory_refs + CI 归属断言 + 前端 ChatBubble「记忆依据 ▾」
    - 硬不变量：`memory_refs` 非空 ⟺ 正文真的引了用户原话；`crisis_intercept` 过滤引用候选（危机内容永不复述回给用户）
    - `GET /chat/history` 暴露 `memory_refs`，归属校验在 `memory_repository.list_by_ids` 内强制 user_id，解析不到的 ID 直接丢弃
    - 前端：`Week1ReportScreen`（D0–D7 时间线 + 原话引用 + 统计）+ `HomeScreen` 红点入口 + `ReflectScreen` 气泡内「记忆依据 ▾」展开；15 语言 journey 文案键齐备
  - 验收实测：`backend/tests/test_journey.py` 25 例（预写六条/幂等/过期 skipped/push 落用户本地窗口、**memory_refs 归属断言**（跨用户 memory 不可被引用）、D2 回填无占位符残留、D5 统计不造假引用、D7 需 2 条原话、危机不复述、频控四条让路 + LLM 反向避让、无 consent 不发、仅 D2 push、模板 zh/en 逐条过闸、events 接口 + acknowledge 清红点 + 越权 404）；`frontend/__tests__/journey.test.ts` 83 例（15 语言 × journey 12 顶层键 + 6 类 `day.*` + 4 类 `status.*` + `reflect.memoryRefs` + 占位符参数守卫；取数判定见 `src/config/journey.ts`）
  - 🔧 前端补测时又抓出两个真实缺陷（均已修，且用变异测试证明用例非空跑）：
    ① `Week1ReportScreen` 判据写死 `status === 'sent'`，而 `journey_repository.acknowledge()` 会把状态推成 `seen`——**第二次打开报告页只剩空态**，报告看完一次就消失；
    ② 时间线按 `day_no ∈ [0,1,2,3,5,7]` 过滤，而 `level_reward` 的 day_no 也是 7（L2 门槛），会把没有文案键的 `journey.day.level_reward` **原样印在屏幕上**；改为按 6 类剧本 kind 过滤
  - 全量：pytest 340 passed；前端 tsc exit 0、jest 13 套件 207 passed；journey i18n 键 15/15 齐备
  - ⏳ 未取证：三个新 UI（Week1ReportScreen / 首页红点 / 「记忆依据 ▾」）**没在设备上跑过**——本机 Expo Go 不可用且工程无 react-native-web 依赖，node 环境无法渲染 RN 组件；当前证据只到类型 + 取数逻辑 + 文案键
  - ⚠️ 偏离 1：D2 文案口径冲突——架构方案 §二 M2 写「D2 走模板直出」，PRD §5.19.3 表格写「复用主动消息四层公式」，实现取**模板直出**（与 M2 零 LLM 总方针一致，且避免剧本占 LLM 配额）
  - ⚠️ 偏离 2：架构方案 §四接口清单只列 `GET /journey/events`，实现补了 `POST /journey/events/acknowledge`——红点徽标必须有消费口，否则 `unseen_count` 永不清零
  - 🔧 过程中修出的真实缺陷：`_format(gating=True)` 曾把原话替换为占位符后**无人回填**，所有含引用的消息会把 `⟦q⟧` 直接展示给用户；改为 `_gate_tpl()`（填充 → 过闸 → 回填）并在 12 处渲染点统一走该入口
- [x] **B3 · M5 演示档案**（前端 2d，零后端）｜架构方案 §二 M5 ✅ 2026-09-29 完成
  - [x] 小 A 30 天数据 + `DemoArchiveScreen` + 隔离 + 演示模式禁 AI 对话入口
    - 数据是**确定性生成**而不是手写 JSON：断言卡要把曲线里的数字印成文案（`weeks` / `n` / `delta`），手写数据 + 手写文案迟早对不上，而这张卡要进商店截图（护城河 §6.1「截图第一张必须是 Pattern 断言卡」）
    - 曲线规则全部可反算：周五恒 1–2（撑起主断言）、周末恒 4、周一~周四 `3 ± 抖动`（步长 4 与星期周期 7 互质，不会锁死成同一形状）、留白只落周二/周四且隔一个取一个 → 恒 4 天、绝不吃掉周五
    - 「算了 → 次日更低」的样本日是**从曲线里挑出来的**（当天 ≥3 且次日严格更低，彼此间隔 ≥5 天），不是先写文案再凑数据
    - 三条 Pattern（friday_dip / hold_it_in / weekend_lift）+ 5 条原话 + 2 条时间线卡片；高光日不许与「算了」撞同一天（同一天又憋着又拿满分，截图一看就是假数据）
    - 屏内三段：概览+纯 View 条形曲线 / Pattern 断言卡（带「依据：第 N 天」）/ 时间线+原话+CTA；顶部常驻「演示模式」徽章 + 返回
    - 指标：`demo_archive_opened`（Onboarding 按钮）/ `completed`（触底，只报一次）/ `start_own`（CTA）/ `closed`（返回）→ 点击率、看完率、重开转化各有分子分母
    - 日期一律用窗口日序而不是 ISO 字符串：RN 里 `toLocaleDateString` 依赖 Intl，Android/Hermes 上不可靠
  - 验收实测：`frontend/__tests__/demoPersona.test.ts` 164 例 = ① 7 个锚点（覆盖一周全部起始星期）× 数字自洽（weeks/样本日/delta/stats 全部由测试**独立反算**再比，不复用被测模块的实现）② 游客态隔离（数据层零 import、屏幕本地依赖白名单只准 colors+数据层+analytics、无 navigation）③ 15 语言 × `demo` 30 键**精确键集** + `onboarding.demoEntry` + 占位符白名单（只 name/n/value/weeks/delta/refs，多一个就红）+ 每个键真被源码引用
  - 🔧 变异验证 8 次（证明守卫不是空跑）：`weeks` 写死 3 → 7 failed；km 漏一个键 → 1 failed；屏幕键名打错 → 1 failed；去掉触底容差 → 1 failed；留白改回固定 dayNo → **10 failed**（周五/间隔不变量真的在管数据）；给数据层加 import：真实 `../services/api` 两次都是**加载期失败**（0 total，RN 依赖不可解析）而不是断言捕获，改用可解析 import 才验证到「数据层零依赖」这条断言本身会红——口径按此记录
  - 全量：jest 15 套件 391 passed；tsc exit 0；pytest 374 passed（零后端改动，与 B3 前同数）；变异后 4 个文件 `cmp` 逐字节复原
  - ⚠️ 偏离 1：§二 M5 原文「`src/data/demoArchive.ts`」→ 实落 `src/config/demoPersona.ts`。jest 配置 `testMatch` 只收 `.test.ts` 且 `testEnvironment: node`，纯数据必须与 .tsx 分离才能断言；`src/config/` 是既有纯模块位（archive.ts / journey.ts / links.ts）
  - ⚠️ 偏离 2：§二 M5 原文「DemoArchiveScreen **复用** InsightsScreen / TimelineScreen 组件渲染」→ 没复用。InsightsScreen 是 317 行单体屏（取数与渲染揉在一起），`src/components/` 只有 AIConsentModal 与 BadgeWall，没有可移植展示件；改为屏内自带卡片渲染 + 纯 View 条形图。未顺手引入 `react-native-svg`（虽在依赖里但全仓零使用，属 E2 范围）
  - ⚠️ 偏离 3：§二 M5 原文「`is_demo` profile 标记」、行动方案 P1-1 第 4 条「不计入真实数据库（用 `is_demo` 字段隔离）」→ **没有建字段**。入口挂在 Onboarding Step 1，那一刻是游客、无 token，任何 api 调用必 401，演示数据根本进不了数据库；改以「数据层零 import」的结构隔离替代，并由上述变异用例守门（谁往演示数据里加请求，谁就红）
  - ⚠️ 偏离 4：P1-1 第 3 条「右上角『用我自己的数据重开』→ 清空演示数据回到空态」→ CTA 用护城河 §6.2 的原话「开始书写你自己的」，但行为是**回引导 Step 2，不是 finish()**：直接完成会跳过 Step 3 三个快问，打断 PRD §5.19.2 的「onboarding → 初读卡生成率 100%」（B1 验收项）；且按偏离 3 无演示数据落库，没有可清空的东西
  - ⚠️ 偏离 5：P1-1 第 4 条「本地打包（`assets/demo_profile.json`）」→ 改为函数生成，理由见上（数字必须与曲线同源）
  - ⚠️ 偏离 6：P1-1 第 2 条列了「3 条 Pattern、2 条时间线卡片、**1 条 AI 断言示例**」四类 → 断言示例没有另造第五类内容：Pattern 卡自带「依据」行，它就是 §5.x 要求的那张断言卡形态
  - ⏳ 未取证：① 该屏**从未在任何设备/模拟器渲染过**——曲线观感、SafeAreaView 顶部避让、30 根条在窄屏的宽度、15 语言长文案换行，全部只有类型与逻辑证据；② 看完率的触底数学有 4 条用例（含"内容不足一屏也算看完"），但 RN 运行时是否真在 `onScroll.nativeEvent` 给 `contentSize` 未取证。RN 0.74 的 `NativeScrollEvent` 只有 `contentSize`，第一版按 `contentContainerSize` 写被 tsc 当场拦下（更高版本才有后者），现已抽成 `isScrollViewAtEnd` 供 node 断言
  - ⚠️ 顺带记录（本任务不改）：四个 `track()` 事件只进本地 SQLite 队列，**游客态能否 flush 成功未验证**——后端 analytics 有 consent 校验，若游客被丢弃，则演示漏斗三项指标在登录前全都拿不到；列进 #17 回归清单
- [x] **B4 · M4 分享卡体系 B03**（2d，依赖 B1）｜架构方案 §二 M4 ✅ 2026-09-29 完成
  - [x] ShareService 模板 first_read / memory_card / year_in_review（year_in_review 由占位转实装，见下）
    - 品牌区 = 水印 + 下载文案 + **带本人推荐码的深链二维码** + 明文短链（PDF 常被截图转发，扫不动时链接还在）；只加在分享类 PDF，**Life Archive 刻意不加**——买断的个人档案不是引流物料
    - 初读卡全文静态直出、零 LLM → 不打 AIGC 元数据；周期报告与 Life Archive 打（`_render_pdf(aigc=True)` 落进 PDF `/Info`，实测字节可见）
    - 记忆卡：某条记忆的原话 + 写下日期 + 品牌深链；原话**不过措辞闸**（闸是给 AI 产出兜底的，M2 同一结论）；不写"一年前的今天"——周年选取规则属 C4
    - year_in_review 已实装（#35，2026-09-29）：`GET /share/year-in-review` + `/pdf` 取**上一个完整自然年**，首年用户退到当年并如实标 `in_progress`（区间只截到今天）、整年零记录才 40402。零 LLM → 不打 AIGC 标识；数字全部走 COUNT（`mood.list_between` / `chat·memory·growth.count_between`），streak 按用户本地日历日算、`checkin_date` 为 NULL 的回填行回退 created_at；缓存键 `period=year_in_review` + `period_key=year_{yyyy}`（避开 yearly 报告的裸年份键互吃缓存）。⚠️ 两处自定决策、文档无口径：① **不做 Pro 门控**（当引流免费卡，AI 年度长文报告仍走 Pro 40302）；② 缓存键不含 language，同一用户同年换语言拿到的是首次生成的语种
    - 前端无这张卡的入口（`ShareCardScreen` 只有 personality_card）——本轮只补后端能力，未新增展示面；接线属 #17 之后的产品决策
    - B1 登记的品牌区欠账（⚠️ 偏离）在此兑现，端点签名不变
  - [x] 深链 closenai.app/r/{code} + AASA + CF Pages landing（决策点 3）
    - 数据：`users.referral_code`（8 位、29 字符集，排除 0/O/1/I/L/8/9 这些手抄必混的形状）+ `referred_by_code`；迁移 `a7c1e9b4d2f8`（单 head）。**两列都不建外键**——referral 是归因标签不是关系引用，指向的人可能已注销，外键会把归因数据锁成删除约束
    - **惰性生成**：注册路径不预生成；`GET /share/link`、分享卡 PDF 导出、初读卡落库三条路径首次取码即写（读接口带一次写，查重走 SELECT + UNIQUE 兜底并发）
    - 归因入口：三个建号路径（密码注册 / 验证码登录未注册自动建号 / Apple 首登）新增 `ref`，非法码**静默丢弃而不是报错**——推广链接被截断不该让注册失败
    - `GET /share/resolve/{code}`（公开）：只回「码是否有效 + 两个 URL」，不回分享者任何资料；未知码与非法码返回同形结果（否则成了"探测某人是否在用这个 App"的入口）
    - 统一落点：性格名片与周期报告的 `share_url` 由 app_store_url 改为带码深链，且分享文案复用**同一个值**（两个落点并存时归因必断）；Life Archive 的 `share_url` 保持 app_store_url
    - App 侧：`app.json` → `ios.associatedDomains: ["applinks:closenai.app"]`；`App.tsx` 冷启动 `Linking.getInitialURL()` + `url` 事件 → 取码存 AsyncStorage（跨冷启动，内存态必丢）；`api.ts` 三个建号请求带 `ref`、成功即清；`getShareLink()` 供 App 内分享文案统一取链接
    - 静态站点产物（`site/`）：`.well-known/apple-app-site-association`（components 只接管 `/r/*`——写 `"*"` 会把整站变成唤起 App，落地页反而打不开）、`_headers`（Apple 要求 application/json 且不许 3xx，而这个文件刻意没有 .json 后缀）、`_redirects`（200 重写而非跳转，保住 pathname 里的码）、`r/index.html`（中英双语纯静态，按 `navigator.language` 分支）
  - 验收实测：pytest **374 passed**（新增 `test_referral.py` 34 例：码表与归一化 / 惰性生成+幂等+跨用户不撞码 / resolve 不泄露资料 + 未知码与非法码同形 + 注销者失效 / 初读卡 PDF 含二维码且不含 AIGC / `share_url` 落库即带码且 `restore` 复现 / 记忆卡越权 40403 / 年报 40402 / AIGC 元数据只在声明时落进 /Info / 名片与报告的 share_url==深链且文案末尾同落点 / 5 个端点级（share/link、resolve、memory-card 越权、年报、初读卡 PDF 流））；jest **227 passed**（新增 `referral.test.ts` 20 例：路径段、查询参数、子域放行、伪装后缀域与无协议串拒绝、8 类非法输入、peek 读不删、后到覆盖、脏值不带上注册请求、**与 backend/core/referral.py 码表逐字对表**）；tsc exit 0；落地页中英两分支经浏览器渲染取证（DOM 快照正确、console 无报错）
  - 🔧 补测时修掉两个真实缺陷，并变异验证了码表同步用例：① `resolve_referral` 对非法码返回 `…/r/`（尾巴为空的坏链）→ 改回站点根；② `parseReferralFromUrl` 原实现对**任意域名**下的 `/r/xxxx` 都放行，任何人都能把自己的码强绑到别人的安装上 → 加品牌域白名单（仅 closenai.app 及子域）；③ 把 TS 侧字符集加回 `L` 后「与后端码表逐字对表」用例转 ✕，证明该用例不是空跑
  - ⚠️ 偏离 1：架构方案 §二 M4 写「referral_code 注册时生成、share URL 归因落 `share_repository`」，实现取**惰性生成 + 归因落 `users.referred_by_code`**。理由：迁移面对的是已有全量用户，预生成要么给老用户回填随机码、要么让从不分享的人永远挂着 NULL 走两条分支；而 share_repository 是导出缓存表，往里塞归因会把「谁带来了这个新用户」变成一次需要 join 导出记录的查询——归因是安装级标签，跟着 users 一行才与软删除/重注册解耦
  - ⚠️ 偏离 2：架构方案 §二 M4 给 memory_card 的文案是「一年前的今天 + 原话 + 日期」，实现**不写周年框**（只印原话 + 写下那天）。"一年前的今天"是一个选取规则（哪条记忆有资格被分享），属 C4 §10.1 记忆考古；本方法拿不到"被选中的记忆有多旧"的假设，硬写会造出假周年
  - ⚠️ 偏离 3：架构方案 §五-决策点 3 原文「landing 页由 CF Pages 承载、**参数归因（utm + referral code）**」预设了链接参数能跟着用户走完安装，实测口径做不到——**不做剪贴板 handoff、不做手工填码入口**，「落地页 → App Store → 装完打开」这一跳参数必丢（Apple 不向第三方 App 透传任意 URL 参数）。v1 只有「已装 App → Universal Link 唤起」归因完整，**referral 统计口径是下界，不能当分享总数看**
  - ⏳ 未取证（三项都要用户账号，代码侧已就绪）：① 站点未部署——`closenai.app` 当前 **DNS 无解析**（`dig +short closenai.app A` 空、curl 连接失败），需 Cloudflare 账号建 Pages 项目、以 `site/` 为 output；② AASA 的 `TEAMID` 是占位，须换真实 Apple Team ID，且 `.well-known` 必须 HTTPS 无跳转，验 Universal Links 需要 prebuild 产物（Expo Go 不生效）；③ 唤起→取码→注册带 `ref` 整条链路**没在设备上跑过**
  - ✅ 顺带记录的两处欠账已由 #38 收口（2026-09-29）：`/share/resolve/{code}` 加了单 IP 固定窗口限流（默认 60 次/5 分钟，超限 42903→HTTP 429）；`api.ts` 的 `API_BASE` 与 `referral.ts` 的品牌域改走 `EXPO_PUBLIC_API_BASE` / `EXPO_PUBLIC_SITE_HOST` 注入，缺省才回落本机

## C. 内容供给管线（P1）

- [x] **C1 · §5.16 睡前复盘后端管线**（UI Screen 26 已就绪，后端零实现）✅ 2026-09-30 完成
  - [x] 22:00–23:30 推送任务 + 首页卡兜底；三步流程；措辞过闸；危机转介挂载
    - 三步 API：`GET /bedtime/today`（completed/三步内容/当日摘要参考 digest 零 LLM/昨日归档）+ `POST /bedtime/step`（step1 一句话可跳过零 LLM / step2「值得记下的」LLM lite+模板回退+`check_or(assertion)` / step3「留给明天」只观察不预测）；均挂 `require_ai_consent`；逐步落 `growth_records`（record_type=`bedtime_review`，三步内容存 metadata_json，step2 引用落 related_memory_ids）；step2/step3 幂等（已生成直接返回，不重复耗 LLM）；复盘非对话轮：不写对话流、不计配额
    - memory_refs 硬不变量：refs 唯一来源是 `memory_repository.list_between` 当日窗口（归属该用户的真实行）；无素材时直接模板回退（零 LLM），refs=[]
    - 危机转介：step1 输入 `crisis_intercept` 命中 → `SafetyEvent(source="bedtime_review")` 审计 + 返回转介载荷，不落复盘记录、不走 LLM；cron 侧近 24h 命中即停推
    - Cron：`GET /cron/bedtime`（verify_cron_secret，vercel.json 每小时）→ `dispatch_bedtime_reminders`：本地 22 点桶（每小时 cron 命中一桶天然每日一条）+ daily 推送档 + 已完成复盘不打扰（半程仍提醒）+ 逐用户独立事务；push_copywriter 新增 `bedtime` 场景 zh/en 模板（温和不施压，过 filter_template）
    - 语言：模板/LLM 以 `users.language` 为事实源（zh* 中文模板，其余英文双表）
    - 验收实测：`backend/tests/test_bedtime.py` **27 例**（step1 落库+digest 零 LLM / 跳过 / 改口覆盖 / 危机拦截+审计+零落库 / step2 refs 硬不变量+related_memory_ids / 无素材模板零 LLM / 幂等 / step3 完成+红线词钉死（预测/注定/运势/predict…）/ get_today 昨日归档+空态 / cron 本地窗口+时区不命中 / 已完成跳过 / 半程仍提醒 / 推送偏好 / 危机停推 / bedtime 文案+场景过闸 / 不写对话流不产生副产物记录）；全量 pytest **486 passed**；OpenAPI 三端点注册核验；conftest `_TIME_PATCH_TARGETS` 登记 bedtime_service（动态冻结口径：取「真实今天 22:30」使 SQLite 真实墙钟 created_at 天然命中当日窗口）
    - ⚠️ 前端 Screen 26 对接与首页卡兜底属前端批次，本条只覆盖后端管线
  - 验收：晚间 DAU ≥35%、完成率 ≥40%、D7 ≥3pt（上线后观测）
- [x] **C2 · §5.17 Growth Curriculum**（每周日 21:00 预生成 7 天主题，Rule 优先 + LLM 周规划）✅ 2026-09-30 完成
    - 生成：`GET /cron/curriculum`（vercel.json 每小时）→ `dispatch_curriculum_generation`：用户本地周日 21 点桶 → `curriculum_service.generate_weekly`：近 30 天素材收集（Memory + pattern_discovery records，零 LLM）→ LLM 周规划**一次 lite 调用**出 7 卡 JSON（ref 编号 → 服务端映射素材行，硬不变量：refs 全部归属该用户）→ Rule 卡轮转兜底（LLM 失败/解析失败/单卡违规逐卡回退）；卡内容出口确定性闸 = 全局 filter_template + **curriculum 专属红线词表**（运势/占卜/注定…全局表只有复合词）+ check_or(assertion) 语义层；零素材不生成（内容全部来自用户数据）
    - 存储（无新表，PRD 研发标注）：复用 journey_events，event_kind=`curriculum`，**day_no 编码 ISO 周序*7+周内序**（(user,kind,day_no) 幂等键跨周不冲突），payload={title,assertion,question,read_at}，scheduled_at=各展示日本地零点；生成即 `mark_sent`+`acknowledge`（首页池拉取式消费：不推送、不进报告页红点 count_unseen、不被剧本 cron list_due 扫描）
    - 读取：`GET /curriculum/week`（当周 7 卡：revealed=展示日≤今天、read、memory_refs、unread_count；未生成的天不出现在 days）+ `POST /curriculum/{event_id}/read`（payload.read_at，越权 40403，幂等）；均挂 require_ai_consent；目标周规则：本周一，周三及以后生成下周一（cron 48h 补偿窗口语义）
    - 与睡前复盘共用预生成管线：周日晚旅程生成结果作为下周睡前复盘素材池（PRD §5.17 关系段）
    - 验收实测：`backend/tests/test_curriculum.py` **13 例**（LLM 周规划被采用+refs 映射+ scheduled_at 七天落位 / 幂等 skip 零重跑 / 零素材 skip 零 LLM / LLM 坏 JSON 回退 Rule / 单卡"运势"违规回退其余保留 / get_week revealed+unread+mark_read / 空周 / 越权 40403+幂等 / cron 周日命中+跨时区不命中 / 非周日非 21 点不生成 / day_no 跨周编码单调 / kind 注册）；全量 pytest **499 passed**；conftest `_TIME_PATCH_TARGETS` 登记 curriculum_service；固定未来日期冻结（2026-11-01 周日 21:30，curriculum 链路无 created_at 窗口依赖，记忆素材行手动挪窗口）
    - ⚠️ 首页次位卡片前端对接（主位"一年前的今天"）属前端批次；卡片点击率/对话占比指标上线后观测
- [x] **C3 · §5.18 承诺回访收尾**（后端）✅ 2026-09-30 完成
  - [x] memories.labels 承诺标签 + Rule 抽取（`commitment_service`，零 LLM）+ 频控退出 + 禁打卡边界验收
    - 捕获：chat 流尾部 `on_user_message`（负向退出优先 → Rule 抽取意图句）→ `create_memory`(goal/self) + labels JSON `{"labels":["commitment"],"commitment":{kind,follow_up_count,muted}}`；重复表达 dedup 命中不重复预写；危机内容不捕获
    - 调度：捕获预写首条 journey_events（kind=commitment_follow_up，habit +7d / event +1d，本地推送窗口）；投递成功后 `on_follow_up_delivered` 计数 +1 并按同型间隔预写下一条（同承诺 ≤2 次）；`schedule(dedupe=False)` 绕过 day_no 幂等键（不同承诺同日捕获冲突），防重写由 payload.memory_id 查重保证
    - 频控：每用户每周 ≤2 条（本地自然周周一起，超出 defer +7d，pending 永不删行）；负向回复（"没做，别问了"/stop asking…）→ 最近被回访承诺 muted，pending 到点 render skip（沉默陪伴）
    - 渲染：四层公式模板（问候→承诺回放→关心提问→开放 CTA），zh/en `data/journey/*.json`；memory_refs 硬不变量照常；投递 payload 合并预写键（memory_id/captured_day/excerpt 不被 set_payload 覆盖）；发送即 acknowledge（消费面在对话流，不进报告页红点）；PUSH_KINDS 增补（push 深链对话页）
    - 出参：MemoryResponse.labels 透传（无标签 None）
    - 验收实测：`backend/tests/test_commitment.py` 15 例（habit/event 抽取与调度 / 无关消息不捕获 / 危机不捕获 / 重复表达单回访 / 投递渲染+refs+发送即已读 / 负向退出 mute+skip / 无历史负向不误伤 / 周频控第三条顺延一周 / 同承诺 ≤2 后不预写 / zh-en 模板禁打卡词表 / 无成长记录落库+labels JSON 键集+payload 键集 schema 断言 / labels 出参）；全量 pytest **459 passed**；alembic 单头 a7c1e9b4d2f8
    - ⚠️ 偏离：第二条回访间隔按**实际投递日**+同型间隔（habit 隔周/event 隔天）而非"捕获日+14/3"——顺延后节律自然后移，避免追赶补发
- [x] **C4 · §10.1 记忆考古分享链路**（行动方案 P1-2：回忆卡模板 + 分享出口）✅ 2026-09-30 完成
  - [x] 候选端点 `GET /share/on-this-day`（首页每日主位「N 年前的今天，你在想什么」数据源）
    - 逐往年窗口查询近 3 年（`ARCHAEOLOGY_MAX_YEARS`）：按用户本地时区取今天月日 → `local_day_start_utc` 本地日界窗口 → `memory_repository.list_between(limit=5)`；候选按年份升序（1 年前优先），字段 {memory_id, content, years_ago, created_at}；零 LLM；新用户/无周年 → 空候选不报错
    - 2/29 非闰年 `date()` ValueError 跳过该年（不崩溃不造假数据）；UTC+8 类本地日界覆盖测试（2025-10-31 16:00 UTC 的记忆被 Asia/Shanghai 用户在 11-01 命中）
  - [x] 回忆卡模板补周年标题：`export_memory_card_pdf` 由服务端按真实 `created_at` 自动判定（同月日且年份差 ≥1 → 「一年前的今天」/「N 年前的今天」/ en "A Year Ago Today" / "N Years Ago Today"；否则保持「你说过的话」）——**根治 C3 偏离 2**：任何路径都造不出假周年（判定只依赖记忆行真实时间戳 + 用户时区，与请求方传什么无关）；PDF 测试用 pypdf 解文本断言（pypdf 入 pyproject dev 依赖组）
  - 验收实测：`backend/tests/test_on_this_day.py` **7 例**（候选年份升序+归属隔离+新用户空态 / UTC+8 本地日界 / PDF 周年标题 zh+en / 非周年保持原标题）；全量 pytest **506 passed**（基线 499 + 7）；OpenAPI 路由注册核验
  - ⚠️ 首页每日主位前端对接属前端批次；本条只覆盖后端候选端点 + 分享卡模板

## D. 规范工程（2026-09-28 第六批，无排期依赖，可插队）

- [x] **D1 · evidence_policy prompt 段落**（~1d）｜规范 §一 + 架构方案 §六-4 ｜2026-09-29 完成
  - [x] SafetyService 双语段落 + 注入 message_generator/mood_service/模板出口
  - [x] D 级词汇并入黑名单判定；SentenceGate 单测扩例（因果断言/标签推断样本被拦）
  - 实测：`pytest backend/tests` **276 passed**（基线 243 + 新增 `test_evidence_policy.py` 33 例）。
  - ⚠️ 注入点范围：按架构方案 §六-4「及未来所有面向用户的生成出口」扩到现存 **15 处**出口（测试 `OUTLETS` 表可逐
    项核验）——personality 画像×2/组合报告、growth 下周预测/模式发现/报告正文/`_llm_or_fallback`（覆盖每日洞察、
    周报、主题卡）、reflection 草稿、share 名片标语、push 文案、承诺回访 follow_up、主动消息。
  - ⚠️ 判定分级：确定性黑名单（§5.16 医疗化词 + §1.4 D 级词）对全部出口生效；**推理越界句式只在
    `scope="assertion"`**（洞察/模式/报告/主动消息/回访/分享卡）生效，对话流不启用——聊天里"因为他没回你"是
    正常复述用户叙述，正则拦截会毁掉可用性，该情形交给 L1 段落 + 语义分类器。
  - ⚠️ 两处刻意不入正则表（理由已写进 `safety_service` 注释）：「高价值/低价值」（已用作 Discovery 幅度分级
    内部术语，`discovery_service.py:24`、`test_discovery_fusion.py:304` 即误杀样本）与 en 的 `treat`/`therapy`
    （普通动词与求助语境高频）。两者仍进 L1 段落与 `_CHECK_PROMPT` 语义判定。
  - 未做：架构方案 §六-1 的 `filter_template()` 静态模板闸——M1/M2/M3 模板尚不存在，随 B1/B2 文案渲染出口一并落地，
    此项不预先建未被调用的函数。
- [x] **D2 · crisis_intercept 实装（两类触发词）**｜规范 §二 + 架构方案 §六-2 ｜2026-09-29 完成
  - [x] 自伤类 + 关系暴力类（v1 仅 zh/en 词表）；命中→热线→停推→safety_events 审计
  - [x] 验收：暴力样本禁归因/禁沟通建议测试进 pytest
  - 挂点三处（同一 `crisis_intercept` 判定，共用 `safety_events` 审计表）：
    ① 用户回复路径 `message_generator.generate_chat_stream` 入口——命中即**不进 LLM**，因此结构上不可能输出
    沟通行为归因或关系修复建议（规范 §二 铁律），只下发关怀 + 地区热线并写审计；
    ② 主动消息入口 `generate_proactive_message`（本地日界内命中 → 返回 None 静默）；
    ③ cron 队列级 `_process_user`（含沉默唤回轻关心在内的营销/升级类整日挂起）。
  - 热线解析：按 `users.timezone` 推断（cn 12356 / us 988 / ca 988 / 其余兜底当地紧急号码）——
    服务端无地区列（`backend/models/user.py:25-27` 仅 timezone/language），时区是当前唯一可用信号，已写成配置表
    `HOTLINES` 便于后续按 IP/门店配置替换。
  - 误报控制：词表取多字信号词，`(?<!动)打我` 负向后视专防"打动我"；测试含 4 条误报样本（含中英）必须不触发。
    自伤类优先于暴力类（同句双命中按生命风险排序）。
  - 转介文案自身过黑名单（`test_crisis_copy_passes_wording_blacklist` 保证文案不含 §5.16 ① 医疗化词）。
  - ⚠️ 未做（随各自立项接入同一函数）：§5.16 睡前复盘挂载 → C1；D0–D7 剧本渲染挂载 → B2；
    15 语言词表 → 决策点 3 明确不在 v1。
  - 实测：新增 `backend/tests/test_crisis_intercept.py` **17 例**（含规范 §二 2.3 三条验收）；
    全量 `pytest backend/tests` **293 passed**。
- [ ] **D3 · 说话人锚定纪律**（纸面预留，随"截图复盘收窄版"立项才实施；立项前不动代码）｜规范 §三
- [ ] **D4 · PRD docx 同步**：第六批修订从 html 回灌 closenai.docx（编辑器导出）
- [ ] **D5 · ASC 提审元数据**：副标题 "It remembers everything you tell it"；关键词去 tracker/journal（提交时操作清单）

## E. 技术栈执行项（2026-09-28 定稿）

- [ ] **E1 · 后端维持 Python/FastAPI**——无换语言议程（负载 IO-bound；4c8G 预算已验证）
- [x] **E2 · 前端补 `react-native-svg`**——趋势曲线/热力图还原 ✅ 2026-09-30 完成
  - 依赖已在 package.json（15.2.0）但全仓零使用（M5 偏离 2 遗留）；本批首个消费方落地
  - [x] `src/components/charts/transforms.ts` 纯函数（jest node 环境可测，SVG 组件不进测试——沿 M5 偏离 2 口径）：`toLineSegments`（折线分段：value=0 未签到断线不插值，返回 segments≥2 点 + singles 孤点，x 按有值序列索引均分）+ `toHeatMatrix`（GitHub 式矩阵：列=自然周周一起始、行=星期几，首列按首日星期补前导 null，缺失日 null 格，非法日期/空输入回 7 行空矩阵不崩溃）+ `HEAT_COLORS` 自 InsightsScreen 上收共享
  - [x] `MoodLineChart.tsx`（Polyline 连段 + Circle 标孤点，金色主线）+ `HeatGrid.tsx`（SVG Rect rx=3）接入 InsightsScreen 热力图卡：近 7 天折线（X=日期 Y=情绪值 1-5，PRD §P0 情绪趋势可视化）+ 热力图默认近 90 天、「查看全年」toggle 切 365 天横向滑动（PRD 默认/展开视图口径）
  - 验收实测：`__tests__/chartTransforms.test.ts` **8 例**（连续段/断线成两段+孤点/全空回空/断口两侧 x 均分不挤压/首列对齐/缺失日 null/跨周换列/空与非法日期）；tsc 0 + jest **847 passed**
  - ⚠️ 色环（行动方案「回忆卡·情绪色环」）依赖 ShareCardScreen 回忆卡第四模板（C4 前端批次），届时再建 SVG 色环组件，本批不做；无需单独设计文档，随第四模板立项时 brainstorming 一并出视觉规格（占色环弧段映射、情绪值→色相、静态/动画口径）
- [ ] **E3 · 部署迁移 QStash → arq + Redis Pod**（k3s 清单见架构方案 §十；inline 回退保留）
- [ ] **E4 · Expo SDK 升级（51 → 53+）**——提审后技术债第一项（连带校验 react-native-iap 兼容性）

## F. 变现动线（§5.20/§5.21，状态待核验后补拆）

- [x] **F1 · 洞察可视化分级**（模糊 vs 清晰，锁呈现不锁原始数据）✅ 2026-09-30 完成（现况核验后拆定）
  - [x] tier 数据源：`/growth/dashboard` 响应加 `is_pro`（growth_service 以 `user.tier != FREE_TIER` 同源判定）——HomeScreen 是缓存秒开屏（SWR），不能靠并行 profile 请求（升级用户会先模糊后清晰的闪变，或反向），dashboard 每次聚焦刷新天然携带最新 tier；`GrowthDashboard.is_pro` 前端类型 optional（旧缓存缺省按免费，宁先锁后解）
  - [x] 判定口径统一：`src/services/insightTier.ts` `isProTier(tier)`——Insights 由内联 `!== 'free'` 切换至此（非 free 一律解锁，后端新增档位向前兼容；tier 未知按免费锁定）；Home 的 dashboard.is_pro（布尔）直接用
  - [x] `InsightBlurGate.tsx`（expo-blur ~13.0.3，`npx expo install` SDK 51 配套）：locked 时 BlurView 低强度覆盖（intensity 30 ≈ PRD「CSS blur 4px」原生观感）+ 整层可点 → 调用方导航 Paywall + 常驻「解锁高清」金色角标；free 态聚合最小洞察（weekly_insight 卡）保留在闸门外
  - [x] 落点（PRD §5.20 模糊对象仅限洞察层）：① Home「Growth This Week」指标+迷你热力图包闸（header/提示行不模糊，点卡进 Insights 不变、点模糊区进 Paywall）② Insights「Pattern 卡」免费 = 模糊样张 + 角标、**不调 LLM**（样张静态模板零成本，升级才触发生成）③ Insights「情绪趋势 + 年度热力图」卡包闸（免费锁呈现，原始签到数据在 Reflect 页永远可看可查）
  - 验收实测：后端 `test_dashboard_is_pro_flag`（free→False / pro→True）+ 全量 pytest **507 passed**；前端 `__tests__/insightTier.test.ts` 4 例（free/pro/pro_plus/未知 tier 边界）；i18n 15 语言新增 unlockHd/patternPreview/heatmapFullYear/heatmap90（#39 英文兜底守卫通过：13 小语种全部正式翻译非英文）
  - ⚠️ Paywall 左右对比区块 + 首次解锁「揭幕」过渡动画（§5.20 设计规范另两条）划归 F2（F2 本就要动 Paywall，试用文案与对比区块一体做）
- [x] **F2 · Pro 7 天试用 + D5/D7 损失转化触点**（依赖 B2 剧本触点）✅ 2026-09-30 完成
  - [x] D5 触点载体：journey 新 kind `trial_d5`（`models/journey.py` 白名单登记 + `PUSH_KINDS` 加盟）——复用 deliver 频控链（每日一条/主动消息互斥/危机停推/沉默降频全部自动生效，与剧本 D5 同日竞争由频控 1 顺延）
  - [x] 预写：`iap_service._validate` 判定 `is_trial`（收据 `is_trial_period`）→ `_schedule_trial_touchpoint` → `journey_service.schedule_trial_d5`（试用开始 = `expires_at - TRIAL_LENGTH_DAYS=7` 反推，落点 = 开始日+5 天的本地推送窗口；时刻已过不追溯补发；幂等靠 `(user, trial_d5, day_no=5)` 仓库查重——Apple intro offer 一人一次；预写失败仅告警降级不阻断购买主流程）
  - [x] 渲染（零 LLM 模板直出）：`_render_trial_d5` 盘点窗口 7 天——{n} 条记录（消息+签到）+ {m} 个情绪模式（签到情绪词去重，≤3 个，`mood_label_for` 按 users.language 双语）；无签到回退 `with_stats`（纯记录数）、零记录走 `plain`（不编造数字）；`evidence.source=trial_inventory`；模板 zh/en 三变体全过措辞闸（**盘点式正向损失：只说"留着/继续生长"，无倒计时与催付表述**）
  - [x] 24h 到期提醒文案盘点化（`TRIAL_REMINDER_TEMPLATES` zh/en）："试用明天结束……都会一直留着——试用结束后，它们也会继续生长"（替代旧"续订即可继续享受完整权益"交易话术，禁恐吓口径落地）
  - [x] Paywall intro 文案：`src/services/introOffer.ts` 纯函数 `parseIntroTrialOffer`（react-native-iap `FREETRIAL` + numberOfPeriods + subscriptionPeriod，DAY/WEEK/MONTH/YEAR 折算天数，无 intro/付费优惠/解析失败→null）；免费用户 + 选中 SKU 带 intro → 主按钮「开始 X 天免费试用」+ 副行「试用结束后 $Y」（`paywall.trialButton/trialThen`）
  - [x] Paywall 对比区块（F1 划归）：免费用户可见「Free 模糊样张（BlurView intensity 30 盖字 + 🔒 解锁高清角标）vs Pro 高清全文（金框）」双列卡，样张 `paywall.compareSample` 真实洞察句
  - [x] 揭幕动画（§5.20 划归）：HomeScreen `dashboard.is_pro` 首次 true + settingsRepo 标记未打 → 一次性庆祝遮罩（深色底 + ✨ + `home.revealUnlocked`，淡入 400ms→停留 2.2s→淡出 600ms，`pointerEvents="none"` 不可交互；本地标记失败跳过不影响主流程）
  - [x] D7 尾部 CTA：Week1ReportScreen 并行拉 `getSubscriptionStatus()`，`is_trial && is_active` → 报告尾部 `journey.trialTail`（"试用结束后，这些模式会继续生长"）+「解锁完整高清洞察」按钮跳 Paywall（正向口径，不倒计时）
  - 验收实测：后端 `test_trial_d5.py` 11 例（kind 登记/预写落点/幂等/过期不补发/iap 触点三态/渲染 zh+en 数字与模式/无数据 plain/模板过闸/频控顺延/到期提醒文案）+ 全量 pytest **518 passed**；前端 `__tests__/introOffer.test.ts` 4 例（7 天/week 折算/PAYASYOUGO 排除/非法输入 null）+ tsc **exit 0** + jest **851 passed**（含 #39 i18n 守卫：7 新 key × 15 语言，13 小语种全正式翻译非英文兜底）；journey 时间线白名单未动（trial_d5 走对话流与 commitment_follow_up 同路径，无需时间线 key）

## G. 占星残留清理（2026-09-29，A1 的欠账）

- [x] **G1 · 星座代码全面移除**（后端 + 前端 + 15 语言文案）｜PRD v2.1「占星已移除」｜✅ 2026-09-29 完成
  - 背景：A1（2026-09-28）声明"占星模块移除"，实测只删掉了 pyswisseph 运行时与本命星盘页面，**推导链与展示面全留着**——`zodiac_sign_for()` 仍在由生日推星座、`GET /personality/profile` 仍回 `zodiac`、Pro 组合报告仍把「太阳星座：未知（未填生日）」喂进 LLM prompt、个人页「主要框架」三选项里仍有**占星**。定位声明与代码不一致，属提审风险
  - 后端：删 `_ZODIAC_PERIODS` + `zodiac_sign_for`（`services/personality_service.py`）；`PersonalityProfileResponse.zodiac` / `ComboReportResponse.zodiac` 下线（`models/schemas.py`）；组合报告由「三框架」改「16 型 × 九型」两框架（prompt 与 `_fallback_combo` 同步）；组合名片 `dimensions` 去 zodiac（`services/share_service.py`）；路由与注释措辞同步
  - 前端：`PrimaryFramework` 收为 `'mbti' | 'enneagram'`（`types/index.ts:243`）；`ProfileScreen` 框架选择器去占星选项；`PersonalityScreen` 画像页星座块 + 组合报告星座 meta 删除；`ExploreScreen`/`PersonalityScreen` 的 `zodiac_sign ↔ zodiac` 映射拆除；SQLite `users` / `personality_profile` 建表去 `zodiac_sign` 列；`personalityProfileRepository` 行类型同步
  - 文案：15 语言 × 4 处（`zodiac` 12 键块、`personality.zodiacLabel`、`profile.frameworkOption.astrology`、`comboDesc` 改两框架），逐文件脚本化并断言每步恰好命中一次
  - **刻意保留（重要）**：`services/safety_service.py` 黑名单里的「星座配对 / 星座运势 / zodiac compatibility / star sign」与 `tests/test_evidence_policy.py` 的对应断言——那是 D1 措辞闸门的**禁语清单**，删掉等于拆掉"不许讲星座"的执行力。守卫因此只扫前端与 i18n，不扫后端
  - 回归守卫：新增 `frontend/__tests__/noAstrology.test.ts` **39 例**（15 语言键集 + 15 语言译文值 + 6 个源文件标识符扫描 + `PrimaryFramework` 枚举集 + SQLite 列）。变异验证 2 处均被捕获且用 `cmp` 逐字节复原：① 把 `'astrology'` 塞回 `PrimaryFramework` → 2 failed；② 往 `zh.ts` 塞回 `"zodiac": { aries }` → 1 failed
  - 全量：pytest **374 passed**；jest 16 套件 **430 passed**；tsc exit 0
  - ⚠️ 偏离：PRD P0-F06 §8 原文写组合报告 = 「16 型人格 × 星座 × 九型」，实现取两框架——与 PRD v2.1 总纲「占星已移除」一致，取总纲
  - ⏳ 未取证：改后未在真机上看过画像页与组合报告（本机 Expo Go 不可用）；证据止于类型 + 单测 + 文案键
  - ✅ **数据层两个待决项已由文档拍过，按文档执行**（`appstore-compliance-2026-09-05.md` 方案 A，:65）：「删除一切占星痕迹，性格框架只保留 MBTI + 九型。**数据库表保留（不写反向迁移），只删代码与 UI**」→ `natal_charts` 不 drop（历史迁移文件也不动，删了会让已 stamp 的库 `alembic upgrade` 报错）；`users.birthday` 列保留
    - ✅ **已执行（2026-09-30 核实更正）**：`birthday` 字段已随 `ca28e6a`（G1–G4）从请求 schema 删除（`git log -S` 追踪确认），前端从无生日输入；仅存 DB 列 `users.birthday`（方案 A：不写反向迁移）。App 已不收集生日，5.1.1 数据最小化风险解除——原「未执行」表述过时
  - 依据（为什么非删不可）：4.3(b) 2026-06-08 修订把 **fortune telling 与 dating 并列点名**（合规文档 :19 引原文）；判定标准是「相对 Co-Star 有没有明显不同的体验」而非「有没有占星」（:57）；实证——算命 App 连续四次被拒 4.3(b)，过审关键动作是**删掉"输入生日直接出解读"的页面**（:167），即触发点是该交互本身；反复硬提审会抬升 **3.2(f) 账号终止**风险（:171）。收益：4.3(b) 触发点归零（:99）

## L. 15 语言文案真翻译（2026-09-30，三节「存在但降级」清账 #39）

- [x] **L1 · 13 个语言的英文兜底清账**（前端 locale 全量）｜✅ 2026-09-30 完成
  - 实测起点（脚本摊平 501 键逐语言比对）：`es/ko/ja/ru/id/ms/th/de/fr/it/pt/vi/km` **各有 267–271 个键的值逐字等于英文**（zh 只有 19 个，且全是语言自称呼 / `Pro` / `{{hour}}:00` / 版本号这类本该留原文的）；另有 `relationship.rewardTitle` 这 1 个键在 13 个文件里**根本不存在**。两类缺口都不报错——`fallbackLng: 'en'`（`src/i18n/index.ts:47`）让它们在真机上直接显英文，所以「支持 15 种语言」此前只在 en/zh 上成立
  - **筛子口径换过一次**：第一版按「13 个语言同时同字」挑待译键，漏掉单语言残留（`ms.home.emotion`、`id/ms.explore.logician`、`km.reflect.quotaTitle/quotaUpgrade`，以及 `km.reflect.quotaMessage` 一整句只差几个词的英文）→ 正确口径是**逐语言比对**，守卫测试也按逐语言写
  - 落库量：13 × 249 键 + 3 处单语言补译 + 1 处陈旧文案刷新；写回用自研就地替换器（只动被点名键的字面量，缩进 / 行内对象 / 键序 / 未点名键一律不碰）。替换器先过**恒等自检**（把文件现值当译文写回 → 15 个文件逐字节不变）再过**覆盖率断言**（扫描器看到的叶子键集合必须与 JS 求值摊平完全一致，501/501）——后者当场抓出扫描器对三层嵌套键把父段重复一次（`relationship.relationship.badgeName.*`），否则 50 个键会被静默跳过并造成重复键
  - 顺手抓到一处**语义陈旧**，字节同字闸放不过它：`profile.deleteConfirmMessage` 的 en 源在 I1/J 批次改成「账号立即失效 + 24 小时内彻底删除」两段式，13 个语言还停在旧的一段式（zh 已同步）→ 由「换行个数逐键相等」这条断言钉住，13 个语言按各自既有术语重写
  - ⚠️ 已知边界：译文由模型批量产出、**未经母语者审校**。机器可验的部分（键集 / 占位符 / 换行 / 文字集 / 不与英文同字）已全绿；措辞自然度只能靠人，列入 #17 提审前抽检
    - ↳ 2026-09-30：这句已由 **L2** 处理——13 语言各跑一遍母语审校，落库 205 处措辞修订；但它同时暴露出一个机器闸看不到、也不是措辞问题的结构缺口（`{{count}}` 无复数形态），已由 **L4** 修复
  - ⏳ **不在这一次范围内的同类降级**：后端直出文案仍是 zh/en 二值字典（`lang = "zh" if … else "en"` 归一，实测 11 个 service 带 `"zh":` 块：`user_service`（等级名 / 解锁表 / 徽章名）、`growth_service`、`mood_service`、`reflection_service`、`share_service`、`push/copy`、`cron_service`、`message_generator`、`personality_service`、`enneagram_service`、`safety_service`）——非 zh 用户的**服务端生成内容**仍拿英文。它与前端 locale 不是一个成本口径（部分要 LLM 产出、部分要模板表扩列），另立新项 #42，不混进本轮
    - ↳ 2026-09-30：已开工为下方 **M 节（#42）**，M1（文案库架构 + 5 个 service 迁移）完成
  - 回归守卫：新增 `frontend/__tests__/i18nLocales.test.ts` **92 例** = ① 14 语言键集与 en 精确相等 ② 值非空 ③ `{{占位符}}` 名称与个数逐键相等 ④ 换行个数逐键相等 ⑤ 无逐字等于英文的值，除非落在逐条写了理由的同字白名单（语言自称呼 / `Pro` `Pro+` / `{{hour}}:00` / 版本号 / 厂商名 / 演示角色名）+ 同形词豁免表（es/fr/de/it/pt 的 `Social` `Details` `Stress` —— 这些语言本来就这么写），且**豁免表不许留死条目**（源文案一改就不再同字，留着等于给以后的漏翻预留合法位置） ⑥ 6 个非拉丁语言每个值必须含目标文字 ⑦ 任何语言都不许出现「连续 ≥3 个英文虚词」的整句英文
  - 反向验证 7 条全部 CAUGHT、逐字节复原无 RESTORE-FAIL（恢复只走备份写回，不碰 `git checkout`）：英文兜底值 / 整键缺失 / 占位符改名 / 整句英文混进 km / 两段式并成一段 / 豁免表留死条目 / 非拉丁语言留纯英文标签。脚本 `/Users/fengyu/Downloads/myproject/tmp/i18n_guard_mutation.js`
  - ⚠️ 改了一条既有断言的口径：`comboCard.test.ts` 原要求非 zh 语言的 `share.proGateMessage` 含英文 token `/16-type/` 与 `/Enneagram/`——那是**只有英文兜底存在时**才可能过的代理断言，真翻译之后 ms（`Sembilan Personaliti`）与 th（`นพลักษณ์`）的正经译名会被它判成漏写 → 改成「含数字 16 + 该语言的 Enneagram 译名」逐语言术语表
  - 全量：jest 29 套件 **809 passed**（本轮 +92）；tsc exit 0；pytest **444 passed**（后端零改动）

- [x] **L2 · 13 个语言的母语审校轮**（把 L1 那句「未经母语者审校」兑掉）｜✅ 2026-09-30 完成
  - 做法：逐语言导出 249 键的 `{en, current}` 对照表 → 13 个语言各跑一遍母语审校（只许改值、不许动占位符 / `\n` / 品牌词）→ 回稿固定四字段 `changes / reasons / same_as_english_is_correct / inconsistencies`
  - 回稿先过机器闸（`/tmp/i18n/validate_review.py`）再进写回器：键必须在 249 内、`{{占位符}}` 名称与个数不变、`\n` 个数不变、MBTI 四字母码不变、品牌词（ClosenAI / App Store / Apple / Google Gemini / `Pro` / `Pro+`）按**词界**判定不许丢（`Protagonist` 不算 `Pro`，这条最初误伤 3 个键）、「与英文逐字同」只认同形词白名单
  - 落库：**13 个语言 205 处值**（每语言 11–25 处），全部经「恒等自检 → 写回 → 重新解析文件逐键比对现值」三段自证，写入数与复核命中数一致
  - 审校抓出的**机器闸结构性盲区**（前一轮的字节同字闸看不到）：为躲「不许与英文同字」而造的生造词——ms `Sembilan Personaliti`、vi/km/id 的 `9 Kiểu Tính Cách`/`បញ្ហាបី`-式拼凑、it `Distinctivi`、fr `Distinctif`。这类词的正确写法本就与英文同字，所以同形词豁免表从 5 语言 7 键扩到 **9 语言 37 键**，逐条写理由，且「不许留死条目」那条断言继续保证它们此刻真的同字
  - 连带改动：`i18nLocales.test.ts` 的「非拉丁文字必含目标文字」闸此前只豁免 `SHARED_BY_DESIGN`，与同形词表互相矛盾（km 的 `Enneagram` 被它判成漏翻）→ 两道闸改为**共用同一张豁免表**；`comboCard.test.ts` 的 ms 译名从生造词改成 `Enneagram`
  - 反向验证 7 条全部 CAUGHT、逐字节复原（脚本 `/tmp/i18n/review_guard_mutation.js`，恢复只走进程内备份写回）：豁免键被改回非英文 / 非豁免键被写成逐字英文 / 豁免表塞死条目 / ja 写成拉丁借词却未豁免 / 文字集闸不再共用豁免 / comboCard 术语表回退 / `{{count}}` 被丢
  - ⚠️ 本轮**没修**的一类结构缺口（审校 3 个语言同时点名，不是措辞问题）：8 个含 `{{count}}`/`{{days}}` 的键在所有语言都只有单数形态，`de` 因此出现「1 Gespräche」、`it` 出现「1 messaggi」——i18next 的 `key_one`/`key_other` 复数变形一个都没建。修它要动 15 个 locale 的键结构 + 调用点传参口径，属独立批次，登记为 L1 之外的待办，#17 提审前不要把它当已修 → ✅ 已由 **L4** 修复（2026-09-30）
  - 全量：jest 30 套件 **820 passed**；tsc exit 0；后端零改动

- [x] **L3 · #34 洞察依据回链（后端一行未动）**｜✅ 2026-09-30 完成
  - 实测边界（读路径盘点）：`/chat/history`（`backend/routers/chat.py:169-187`）与 `/journey/events`（`journey_service.py:745-766`）**已逐条下发** `memory_refs`，前端 `ReflectScreen` 的「记忆依据 ▾」本来就在那两处渲染；而洞察侧三个端点响应里**一个引用 id 都没有**——`WeeklyInsightResponse` 只有 `level/content/period`（`schemas.py:161-165`），pattern 只有 `{content}`（`growth.py:131-140`），`/growth/records` 把库里有的 `related_memory_ids` 列在出参时剥掉了（`growth_service.py:523-529` vs `models/growth.py:23`）
  - 所以「后端不动」能做到的与不能做到的，按事实分两半实现：
    - **id 级（真回链）**：`/reflections/generate` 本来就返回 `related_memory_ids`，只是前端一直没消费 → 新增 `frontend/src/services/evidence.ts:resolveMemoryRefs()` 用已有 `GET /memories` 按 id 解析成 `MemoryRef`，接进 `ReflectScreen` 既有的展开器；解析不到的 id 丢弃（与 `chat.py` 同口径），不猜
    - **期间级（组依据）**：周洞察没有 id，只能以响应里的 `period` 为锚点聚合 `[period-7d, period+1d)` 内创建的记忆（`memoriesWithinDays()`），展开时并排显示既有的 `journey.evidenceNote`「引用只来自你写过的原话；统计类观察不编造引用」——**不断言某句对应某条**，因为线上数据不支持
  - 复用既有文案键（`reflect.memoryRefs` / `journey.evidenceNote`，15 语言齐备），本轮**没有新增任何 i18n 键**，避免给刚清完账的 13 个语言再欠一笔
  - 做不到的一部分如实点名：逐句对应关系要后端把 `related_memory_ids` 带进 `/growth/insights` 出参（读路径已有列，只是没出参），属 #42 之外的后端授权项，未动
    - ↳ 2026-09-30：已由 **M1** 捎带完成——周报写入聚合本周各记录引用的记忆 id，GET 出参带回（见下方 M 节）
  - 回归守卫：新增 `frontend/__tests__/insightEvidence.test.ts` **11 例**（无 id 不发请求 / 顺序与去重 / 丢弃解析不到的 id / 空白折叠同后端口径 / limit=100 / 裸日期锚点含当天 / 带时区右端排除未来记录 / 锚点解析失败返回空 / 两屏接线在位 / 用到的键 15 语言齐备且占位符不变）
  - 全量：jest 30 套件 **820 passed**；tsc exit 0；`backend/` 零 diff

- [x] **L4 · `{{count}}` 复数形态（L2 点名的结构缺口清账）**｜✅ 2026-09-30 完成
  - 范围：8 个含 `{{count}}`/`{{days}}` 的键 × 15 语言 = `relationship.toNextLevel` / `relationship.dayChats` / `home.milestoneStreak` / `personality.quotaLeft` / `personality.signalsCount` / `personality.stageEta` / `share.streak` / `insights.chatCount`，共 120 处键改为 i18next CLDR 变体（en/de/es/fr/it/pt = `_one`+`_other`；ru = `_one`/`_few`/`_many`/`_other` 四档；zh/ja/ko/th/vi/km/id/ms 单形态语言 = 仅 `_other`）
  - 机制实证（决定键结构的关键前提）：读 `node_modules/i18next`（v23.16.8）Translator 源码——count 命中的解析链只有 **[基础键, 命中类别键] 两级**，缺类别键时**不会**回退到 `_other`，而是裸键名直出 → 每语言必须备齐自己的全部类别（`compatibilityJSON: 'v4'` 走 `Intl.PluralRules`；`count=0` 会先试 `_zero` 再落 `_other`，`quotaLeft` 的 0 值场景由此覆盖，无需 `_zero` 键）
  - 文案分档规则（不是机械复制）：有性数一致的键补单数档（es `Queda/Quedan`、it `Ne resta/restano`、pt `Resta/Restam`、fr 顺手把 `restant(s)` 括号 hack 拆成正经两档）；标签-冒号式与缩写式本就不随数变（ru 四档同值、de/fr/it 部分同值）——同值也必须落满类别集，否则该档裸键名直出
  - 调用点：3 处 `{{days}}` 键补传 `count`（`RelationshipTimelineScreen.tsx:256` / `HomeScreen.tsx:246` / `PersonalityScreen.tsx:637`），i18next 要求 `{ days, count }` 同时传才驱动类别选择；其余 5 处调用点本就传 count，零改动
  - 守卫升级：`i18nLocales.test.ts` 新增「复数键的 CLDR 类别齐备」15 语言 × 8 键断言（变体集恰好等于该语言类别集，缺档=裸键名、多档=死键，含 en 自身）；键集与占位符两道闸改**复数逻辑键归一**（`_one`/`_few`/`_many`/`_other` 后缀归并 + 去重——不去重的话双形态语言会各多 8 个重复逻辑键，与单形态语言全红）；同形词豁免表 fr 的 `insights.chatCount` 拆成 `_one`/`_other` 两键（法语 conversation 本就与英文同形）
  - 🔧 过程记录：批量写回脚本有两处 bug（单形态语言漏给 `other` 档导致键行被替换成空、变体间双重逗号），修复脚本按空标记序回填 + 行级定点削逗号，只动被点名行——15 文件里 L2 审校的既有未提交改动（ru `rewardTitle`/等级名等）逐字节保留，`git diff` 规模与「本批次键 + 既有改动」之和吻合
  - 全量：tsc exit 0；jest 30 套件 **835 passed**（i18nLocales 92→107）；后端零改动

## M. #42 后端文案 15 语言化（L 节点名的那批 zh/en 二值字典清账，2026-09-30 开工）

> 范围：L1 ⏳ 盘点的 11 个 service `"zh":` 块——非 zh 用户的**服务端生成内容**（标签 / 模板 / 题库 / 散文）拿英文的降级。
> 分 3 小批串行：M1 标签与结构化文案（架构 + 5 service）→ M2 消息与推送模板 → M3 长文与题库（LLM 产出 / 超长表按批拆）。

- [x] **M1 · 文案库架构 + 5 个 service 迁移**｜✅ 2026-09-30 完成
  - 架构：`backend/core/copy.py` 统一 loader + `backend/data/copy/{locale}.json` ×15（单文件分 namespace：`mood` / `levels` / `growth` / `mbti` / `enneagram`）。`locale_for()` 前缀归一（zh*→zh、未知→en）；进程缓存；`t(namespace, key, language)` 两级回退 **locale→en→键名**（永不静默空串，缺键看得见）；`t_list()` 同口径回退空数组；`_comment` 下划线键不进守卫比对
  - 迁移（原 zh/en 二值字典删除，键名保留为事实源）：`mood_service`（6 emoji 标签 + 规则解读 `mood_interpret_for`）、`user_service`（`level_label` / `level_unlocks`（回报在前功能在后）/ `reward_text` / `milestone_name`，LEVEL_REWARD_KEYS 保留 reward_key 机器键）、`growth_service`（insight_type 8 键 + period 4 键，未知类型回退原串）、`personality_service`（MBTI 16 码；原 zh 表保留为合法集合校验）、`enneagram_service`（type 9 + option 3；新增 `VALID_TYPES` 合法集合）
  - 守卫：`backend/tests/test_copy_library.py` **6 例**（15 文件存在且 JSON 有效 / 键集形状与 en 一致（剔 `_comment` 递归比对 + 数组长度）/ 非 en 无逐字英文，同形外来语按语言白名单（de `Protagonist` 等 7 词、fr `Entrepreneur`/`Consul`、es/id/ms/it/pt `Virtuoso`——本语言就这么写）/ 非拉丁语言（zh/ko/ja/ru/th/km）真文字断言（ja 需汉字+假名）/ `locale_for` 归一 / 回退链）
  - 捎带完成 L3 点名的授权项：`/growth/insights` 出参加 `related_memory_ids`——`generate_weekly_insight` 写入时聚合本周各记录 `related_memory_ids` 并集（去重截 5，与 reflection 同口径），`WeeklyInsightResponse` 新增字段，GET 解析 `split(",")` 带回（`test_growth_service.py` +2 例：并集去重 / 截 5 + 无引用 None）
  - 验收：pytest **526 passed**（+6 copy_library +2 weekly refs，518 起步）；既有断言最小改动（`test_enneagram_service` 改用 `VALID_TYPES`、`test_level_reward` 改走 `level_unlocks()`，口径不变）
- [x] **M2 · 消息与推送模板**｜✅ 2026-09-30 完成
  - 文案库新增 3 namespace（15 文件同步，值全部真翻译非英文兜底）：`msg.*`（兜底回复 4 条 / 跟进降级 / 里程碑祝贺 7·30·100·365 / Day1 开场（mbti/通用 + `{tail}` 尾段占位，15 语言各自控制空格标点）/ 免费主动消息三开场 / 再 engage / 漂移提醒 / 试用提醒）、`style.*`（warm/rational/philosophical 风格指令 + 安全注脚——原来只有 zh/en，es 用户一直拿英文风格 prompt）、`push.*`（6 场景模板池，`{title, body|null}` 数组 + default_name + unknown_mood）
  - 迁移：`message_generator`（FALLBACK/FOLLOW_UP/MILESTONE/`_day1_opener`/`_build_free_proactive` 全删常量改走 copy 库；`conversation_style_prompt` 改 15 语言，非法风格仍回退默认；`_STYLE_PROMPTS`/`_STYLE_SAFETY_NOTE` 删除）、`push/copy.py`（`PUSH_TEMPLATES` 删除，新增 `templates_for(scenario, language)` 公共取池函数，场景缺失回退 proactive 池；`_display_name`/未知情绪占位走文案库）、`cron_service`（`REENGAGE_TEMPLATES`/`TRIAL_REMINDER_TEMPLATES` 删除，`_drift_alert_text` 改模板 format，`_reengage_template`/`_trial_reminder_template` 保留函数壳改走 copy.t）
  - 守卫升级（`test_copy_library.py`）：新增**占位符一致性**测试——`{name}/{mbti}/{tail}/{mood}/{milestone}…` 集合逐键跨语言与 en 一致（漏写会 KeyError 崩溃或漏注数据）；新增全语言渲染冒烟（msg/style/push 全键 format 不崩）；bedtime 模板过闸测试从 zh/en 参数化扩为 15 语言
  - 既有测试最小改动：`test_trial_d5` 试用提醒过闸改为循环 15 locale 的 `_trial_reminder_template(loc)`（口径不变）；`test_bedtime` 改用 `templates_for()`
  - 验收：pytest **541 passed**（+1 占位符守卫 +1 冒烟，bedtime 参数化 2→15）
- [x] **M3 · 长文与题库**｜✅ 2026-09-30 完成
  - 已迁（文案库 → copy namespace）：safety 危机文案+safe_reply（过闸验证）、enneagram 36 题题库（`q.{type}.{round}` key 引用制）+ combo 关联表 24 组合 + completed_message、share_service 全部行内散文（brand 水印/16 型 archetype/combo 解读/初读卡/记忆卡周年标题/年报统计行/Life Archive 章节/AIGC 标识与声明/weekly 报告，共 70 键）、reflection 3 键、bedtime step 回退、curriculum rule_cards、email 邮件主题与正文、mood 解读回退、growth 34 键（greeting/指标/趋势/降级模板）、user_service 等级晋升庆祝（levels.celebrate 2 键）
  - 甄别不迁（prompt 注入/词表/纯标点）：growth LLM prompt 段、safety 词表与 SAFETY_BOUNDARY、commitment 意图词表、curriculum 闸词表、各 AppException 双语错误消息（前端按错误码 i18n）、列表连接符与 separator（代码侧 `_is_zh` 二值）
  - 顺带修复：growth `_greeting` 的 `language == "zh"` 精确匹配 bug（zh-TW 等变体错走英文，locale_for 归一解决）
  - 测试同步：引用被删常量的测试改走 `copy_lib.t()` / service 方法并升级 15 语言覆盖（enneagram 题库结构、bedtime step、crisis 文案、safe_reply）；守卫测试保持 8 个全绿
  - 验收：pytest **541 passed**；一次性脚本（3 批 /tmp/m3_*.py）跑完即删
- [x] **M4 · Gate 2 审查（closenai-code-review）**｜✅ 2026-09-30 完成
  - 🔴 修复：personality_service 4 处直出文案是 M1 盘点漏网（行内三元非 `"zh":` 块，盘点筛子只抓字典块）——`refresh` 无新数据 message / `answer` 完成 message / 评估 observations 兜底 / `_fallback_combo` 降级正文 + 九型描述（`{n} 号`/`Type {n}`/`未知` 三态），共 6 键 × 15 语言迁 `personality` namespace；`_fallback_combo` 签名 zh bool → language；en 值逐字保持原文（test_personality L62 断言不动）；死变量 `zh` 清理；pytest 541 passed
  - 🟡 豁免登记：`discovery_service._compare` 8 条文案——`getDailyDiscovery` 前端零调用（api.ts:583 仅定义，dailyDiscoveryRepo 无 screen 消费），实际链路仅 message_generator 主动消息融合（LLM 按用户语言重写）→ prompt 素材豁免；若未来开放 `/discovery/daily` 直出消费需迁文案库
  - 其余 zh/en 残留逐点甄别均属豁免类：mood `_deep_trigger_days`/growth header/信念标签（prompt 素材）、`_YEAR_IN_REVIEW_EMPTY`（AppException 双语错误）、`_TAGLINE_STYLES`（LLM 风格指令）、commitment 词表、separator/连接符（纯标点代码侧二值）

## K. 外部待执行清单（代码侧已就绪，缺的是账号/域名/设备，不是代码）

> 这一节是 2026-09-29 收口「做到代码就绪 + 附待执行清单」这条授权时登记的。
> 判据：每条都对应一个**已在代码里留好接口**的动作，做完只需回填值；未做完期间 App 表现为
> 「按开发口径可用、按生产口径不可用」。逐条都要用户账号或真机，代理无法代为完成。

- [ ] **K1 · Cloudflare Pages 部署 `site/`**（B4/#38）
  - ✅ 域名拍板（2026-09-30 用户确认）：**closenai.app 为主域名**——K1/K3/K4 全部围绕它执行
  - 待做：用 closenai.app 所属 CF 账号建 Pages 项目，output 目录 = `site/`；`closenai.app` 当前 **DNS 无解析**（`dig +short closenai.app A` 空、curl 连接失败，实测于 2026-09-29 与 2026-09-30 复测一致）
  - 验收：`curl -sSI https://closenai.app/.well-known/apple-app-site-association` 返回 200 且 `Content-Type: application/json`、无 3xx
- [ ] **K2 · 真实 Apple Team ID 注入 AASA**（#38）
  - 待做：`APPLE_TEAM_ID=<10 位> node site/scripts/build-aasa.mjs` 后再部署；把同一步加进 Pages 的 Build command
  - 守卫：`node site/scripts/build-aasa.mjs --check` 在未替换占位时**非零退出**（占位串的表现是 Universal Links 静默不唤起，两端都不报错）
  - 需 prebuild 产物验证；Expo Go 不生效
- [ ] **K3 · 生产 API 域注入**（#38 / E3 / D5）
  - 待做：给构建配 `EXPO_PUBLIC_API_BASE=https://<生产域>/api/v1`（见 `frontend/.env.example`）；缺失时前端回落 localhost，即「漏配看得见」而不是静默打本机
  - 若给落地页用上 `/share/resolve`，同时决定 `EXPO_PUBLIC_SITE_HOST`（预览域与生产域不同值时必须注入）
- [ ] **K4 · 真实 App Store 链接回填两处**（D5）
  - 待做：提审拿到 id 后，`backend/config.py` 的 `app_store_url` 与 `site/r/index.html` 的 `APP_STORE_URL` **两处同改**（当前均为占位 id6740000000）
- [ ] **K5 · 密钥类：Sentry DSN、PostHog 两个 key、JWT_SECRET/CRON_SECRET 生产值**（H1/H3/A 节）
  - 代码侧已留配置口并且缺值时降级可用；上线前必须注入，否则崩溃上报与埋点静默空转
- [ ] **K6 · 真机取证三档**（A2 / E3）
  - Life Archive IAP sandbox 三档购买、唤起→取码→注册带 `ref` 全链路、分享卡系统分享面板——都要设备安装 prebuild 包后手工验
- [ ] **K7 · Android 侧 assetlinks.json**（若上 Android）
  - iOS 有 `site/.well-known/apple-app-site-association`，Android App Links 需等价的 `/.well-known/assetlinks.json`（含 SHA-256 指纹，只能由签名产物算出）——当前**未生成**，v1 只上 iOS 时可搁置
  - ⚠️ 顺带：`.git/config` 的 origin/gitee URL 内嵌明文 token，属仓库外动作（轮换凭证 + 改走 SSH/credential helper）

## 依赖与顺序总览

```
A1(冲突先决) ─┐
A2 ───────────┼─→ Sprint 1 完成 → 提审
A3 ←依赖─ B2 表(journey_events 先建, Sprint1 D9) ─┘
B1 → B4;  B2 → F2;  C3 在途收尾可并行;  D1/D2/D4/D5 随时可插队;  E3 在部署前
```

> 更新规则：完成打 [x] 并在行尾追加日期；改变范围时在对应节新增行，不重排既有任务编号（对齐"数量固定、就地编辑"的既有约定）。
