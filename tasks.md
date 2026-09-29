# ClosenAI 实施任务分解（tasks.md）

> 拆分依据：PRD v2.1（closenai-v2.1-clean.html，至 2026-09-28 第六批）· 《架构与技术方案-2026-09-06.md》· 《P0-P1行动方案-2026-09-06.md》· UI 原型 closenai-p0-prototype.html（至 2026-09-28 修订）· 《沟通证据与措辞规范-2026-09-28.md》
> 状态核验日期：2026-09-28（代码实测，非文档推断）
> 图例：`[ ]` 未开始 · `[~]` 进行中 · `[x]` 完成 · 🔴 提审阻塞 · ⚠️ 冲突/风险

---

## 进度快照（实测结论）

- Onboarding 文案改写（P0-1）：✅ 已完成（15 语言 i18n + 原型 Screen 7）
- 后端 M1–M7 / P1-3 管线：**全部零实现**（grep 无 journey_events / onboarding_profiles / bedtime / curriculum）
  - ↳ 2026-09-29 更新：M7（占星移除）、M1（初读卡）、M2（第一周剧本）、M3（情感回报进 LEVEL_UNLOCKS）、M4（分享卡深链与归因）、M5（演示档案，纯前端）、M6（Life Archive IAP）已落地，见 A/B 节勾选；bedtime / curriculum 仍零实现
- 工作区在途（未提交）：~~九型人格 service、§5.18 承诺回访、discovery 融合、前端 Reflect/Insights/i18n~~ → 已在 `0ced5ac`/`48d37ed` 提交；2026-09-29 快照：B2 全套已提交，`closenai-prd` submodule 仍有未提交内容（归 D4）
- ⚠️ **方向冲突**：近三次 commit（13313c3/efbd7ec/e89f19c）在**新增**本命星盘 + pyswisseph，而 M7 计划是整体移除占星——✅ 2026-09-28 已按 PRD v2.1「占星已移除」执行 A1，冲突关闭

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
  - [x] ShareService 模板 first_read / memory_card / year_in_review 占位
    - 品牌区 = 水印 + 下载文案 + **带本人推荐码的深链二维码** + 明文短链（PDF 常被截图转发，扫不动时链接还在）；只加在分享类 PDF，**Life Archive 刻意不加**——买断的个人档案不是引流物料
    - 初读卡全文静态直出、零 LLM → 不打 AIGC 元数据；周期报告与 Life Archive 打（`_render_pdf(aigc=True)` 落进 PDF `/Info`，实测字节可见）
    - 记忆卡：某条记忆的原话 + 写下日期 + 品牌深链；原话**不过措辞闸**（闸是给 AI 产出兜底的，M2 同一结论）；不写"一年前的今天"——周年选取规则属 C4
    - year_in_review 为 P2 占位：端点存在但恒 40402，前端可按同一链路接"尚未开放"而不是当 bug
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
  - ⚠️ 顺带记录：`/share/resolve/{code}` 目前**无调用方且无限流**——落地页刻意做成纯静态不依赖后端，而 `api.ts` 的 `API_BASE` 仍是 localhost（生产 API 域未配，属 E3/D5）。提审前若要让落地页用上这个端点，得先配生产域 + 加限流

## C. 内容供给管线（P1）

- [ ] **C1 · §5.16 睡前复盘后端管线**（UI Screen 26 已就绪，后端零实现）
  - [ ] 22:00–23:30 推送任务 + 首页卡兜底；三步流程；措辞过闸；危机转介挂载
  - 验收：晚间 DAU ≥35%、完成率 ≥40%、D7 ≥3pt
- [ ] **C2 · §5.17 Growth Curriculum**（每周日 21:00 预生成 7 天主题，Rule 优先 + LLM 周规划）
- [~] **C3 · §5.18 承诺回访**（工作区在途：follow_up_sent 迁移 + test_follow_up + ReflectScreen 改动——待提交与收尾）
  - [ ] memories.labels 承诺标签 + 频控退出 + 禁打卡边界验收
- [ ] **C4 · §10.1 记忆考古分享链路**（行动方案 P1-2：回忆卡模板 + 分享出口）

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
- [ ] **E2 · 前端补 `react-native-svg`**——趋势曲线/热力图/色环还原缺口
- [ ] **E3 · 部署迁移 QStash → arq + Redis Pod**（k3s 清单见架构方案 §十；inline 回退保留）
- [ ] **E4 · Expo SDK 升级（51 → 53+）**——提审后技术债第一项（连带校验 react-native-iap 兼容性）

## F. 变现动线（§5.20/§5.21，状态待核验后补拆）

- [ ] **F1 · 洞察可视化分级**（模糊 vs 清晰，锁呈现不锁原始数据）——先核验代码现状再拆
- [ ] **F2 · Pro 7 天试用 + D5/D7 损失转化触点**——依赖 B2 剧本触点

## 依赖与顺序总览

```
A1(冲突先决) ─┐
A2 ───────────┼─→ Sprint 1 完成 → 提审
A3 ←依赖─ B2 表(journey_events 先建, Sprint1 D9) ─┘
B1 → B4;  B2 → F2;  C3 在途收尾可并行;  D1/D2/D4/D5 随时可插队;  E3 在部署前
```

> 更新规则：完成打 [x] 并在行尾追加日期；改变范围时在对应节新增行，不重排既有任务编号（对齐"数量固定、就地编辑"的既有约定）。
