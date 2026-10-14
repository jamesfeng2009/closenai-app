# ClosenAI 实施任务分解（tasks.md）

> 拆分依据：PRD v2.1（closenai-v2.1-clean.html，至 2026-09-28 第六批）· 《架构与技术方案-2026-09-06.md》· 《P0-P1行动方案-2026-09-06.md》· UI 原型 closenai-p0-prototype.html（至 2026-09-28 修订）· 《沟通证据与措辞规范-2026-09-28.md》· 《V3方案裁决与分层-2026-10-12.md》v2.0（N/O/P 节立项依据）
> 状态核验日期：2026-09-28（代码实测，非文档推断）
> 图例：`[ ]` 未开始 · `[~]` 进行中 · `[x]` 完成 · ☐ 快照里的「未做」叙述（等价 `[ ]`，不作为任务行状态） · 🔴 提审阻塞 · ⚠️ 冲突/风险 · ⛔ 本行不再单独执行（被别的任务承接，编号保留不重排）
> **单状态硬规则（2026-10-14 用户点名：「一个 task 不能同时 ⚠️✅，不然你根本不知道到底有没有做完」）**：
> ① 一行只允许一个状态——`[x]`/✅ 的行**不许**再挂 ⚠️、「待…」或「仅剩 …」；`[ ]` 的行**不许**写 ✅ 已完成。
> ② 一条工作做了一半 ⇒ **拆成两行**：已完成那行 `[x]` 带日期与实测，未完成那行 `[ ]` 独立编号（用 a/b 后缀，**不重排既有编号**）、各自给验收。
> ③ 风险/冲突（⚠️）只挂在**未完成**的行上；已关闭的冲突改写为「已结案」并去掉 ⚠️。
> ④ 已被别的任务取代的行 ⇒ 标「⛔ 本行不再单独执行，交付物由 X 承担」，不写 `[x]`（它没做完，是不做了）。

---

## 进度快照（实测结论）

- Onboarding 文案改写（P0-1）：✅ 已完成（15 语言 i18n + 原型 Screen 7）
- 后端 M1–M7 / P1-3 管线：**全部零实现**（grep 无 journey_events / onboarding_profiles / bedtime / curriculum）
  - ↳ 2026-09-29 更新：M7（占星移除）、M1（初读卡）、M2（第一周剧本）、M3（情感回报进 LEVEL_UNLOCKS）、M4（分享卡深链与归因）、M5（演示档案，纯前端）、M6（Life Archive IAP）已落地，见 A/B 节勾选；↳ 2026-09-30 更新：bedtime / curriculum 已随 `4b87018`（P1-C1/C2 + vercel cron 注册）落地——**M1–M7 / P1 管线至此全部零欠账**
- 工作区在途（未提交）：~~九型人格 service、§5.18 承诺回访、discovery 融合、前端 Reflect/Insights/i18n~~ → 已在 `0ced5ac`/`48d37ed` 提交；2026-09-29 快照：B2 全套已提交，`closenai-prd` submodule 仍有未提交内容（归 D4）
- **方向冲突（已结案，2026-09-28）**：近三次 commit（13313c3/efbd7ec/e89f19c）曾**新增**本命星盘 + pyswisseph，而 M7 计划是整体移除占星 ⇒ 已按 PRD v2.1「占星已移除」执行 A1，冲突关闭；残留登记见 A1b
  - ↳ 2026-09-29 复核：A1 只删了运行时，推导链/响应字段/画像页展示/个人页「主要框架=占星」/15 语言星座文案仍在 → 见 G 节，已全面清理并加回归守卫
- 🆕 **2026-10-12 外部架构方案评审 + 分层改判**：工作区新增 `ClosenAI_Personal_Intelligence_Runtime_V3/`（22 篇，主张 Go Core + PostgreSQL + RabbitMQ + Protobuf + Outbox/Inbox）。我先裁「只采语义层、不采运行时拆分」，用户当日改判「**Go 对外提供 HTTP 接口与后端服务，Python 主要负责 agent 与 AI**」→ **运行时分层采纳**，立项 **N 节（Go Core N0–N9）/ O 节（Python AI Runtime O1–O7）/ P 节（并行与风险）**；依据见 `closenai-prd/V3方案裁决与分层-2026-10-12.md`（v2.0，含改判登记、实测迁移面与不照抄清单）
  - ↳ E1 原口径就地作废并回写改判；架构方案 §五-5「承诺=memories 增列不建新表」在 Go 权威侧反转（见 N2）
  - ↳ 迁移面实测：97 个 HTTP 端点 / 28,801 行 Python / 24 个 alembic 迁移 / 全仓 0 个 `.go`；前端只依赖 REST + 1 条 SSE ⇒ **契约不变即客户端零改动，提审主线与迁移并行（P1）**
  - ↳ 领域缺口不重复立项：AI 直写权威态（3 处）、无 draft/candidate/confirmed、无 evidence/pattern/insight/outcome 实体、不记录 AI 产出由哪个模型生成、无服务端幂等去重、LLM 零重试 → 全部并入 N2–N7 与 O3/O4，**直接在 Go 建，不在 Python 补一遍**（P5）
  - ✅ **2026-10-13 D-1…D-8 全部拍板**（用户「按照默认值来处理」＝按 §七 建议默认值整体生效，范围＝该表条目），逐条结论见 N0 节 ↳ 登记；代码侧已开工的第一条是 **O4 生成留痕**（已落地并过测试）
  - ✅ **2026-10-13 N0 契约冻结完成**：`contracts/{openapi.json,endpoints.md,frontend-compat.md}` + 生成脚本 `scripts/export_contract.py` + 验收测试 `backend/tests/test_contract_freeze.py`（10 条，变异自证可失败）；计数对账 **98 = 97 router 端点 + 1 app 级 `GET /health`**（`backend/main.py:170`），差额根因结案、不是漏登记；架构方案增补 §十一（目标态分层 / 按域 strangler / 回滚口径）
  - ✅ **2026-10-13 N2 canonical schema 完成并过测试**：`c8a2e4b6d0f2` 建 **21 张权威表**（alembic 迁移数 24 → **26**，head `b7d9e1f3a5c8` → `c8a2e4b6d0f2`，单头线性）、`backend/models/canonical.py` 21 个 ORM 声明、`PURGE_TARGETS` +20 条（D-5 的连带义务）、Go 侧 sqlc **55 条查询**（§37 九问逐问可查）。验收＝`test_canonical_schema.py` **23 passed**（含空库 upgrade/downgrade 往返、实库逐列对账、10 条变异全部打红）+ `canonical_integration_test.go` 5 个真 PG Test + `go test ./...` 全绿；全量 `pytest backend/tests` **617 passed / 1 既有失败**。抓到并修掉 1 个真缺陷（指纹唯一索引被建成普通索引），详见 N2 节
  - ✅ **2026-10-13 N1a Go Core 第一纵切已落代码并过测试**（回应用户「我怎么没有看到任何 go 的代码？」）：新增 `go-core/`——**22 个 `.go` 文件、2,738 行 Go**（含测试）、`go mod tidy` 后 **3 直接依赖**（stdlib mux 裁定与 D-7 结案见 N1a 节）；`go test ./...` **25 Test + 22 HTTP golden 逐字节**全绿，`backend/tests/test_go_auth_interop.py` **10 passed**（含同库互读：Python 写的行 Go 能登录、Go 写的行 Python 能验密）
- ☐ **N1b k3s 真实 apply 未做**：`go-api`/`py-api` 打到共享集群 + Ingress 灰度 0%，两道外部前置（显式授权 + 镜像凭据）未齐，不得擅自 apply

---

## 未做项索引（2026-10-14 拆解，每行只有一个状态）

> 四条桶把「还没做的功能」摊开了，但散在 §A–§R 里不好跟进 ⇒ 这里做一次**只含未完成项**的索引，右侧是跟踪器标题。
> ⚠️ 编号口径：本文件历史文本里出现的 `#NN` 是**旧跟踪器会话的编号**，与当前跟踪器 id **不一一对应**（当前 id 见跟踪器标题）；跟进一律以「本节编号 + 跟踪器标题」为准，别按 `#NN` 猜。

| 桶 | 未做项（tasks.md 编号） | 跟踪器条目标题 |
| --- | --- | --- |
| ① 用户可见但不成立 | R6（三道门无生产者 + 现网回填，**这是①的正解**）、N8（前端改调新端点） | `R6 · 三道门接生产者 + 现网数据回填`、`N8 97 端点按域切流` |
| ② AI 独立化 | O0、O3、O5、O6、R0–R5、R7、O7 | `O0 · AI 独立目录归堆`、`O3 · Python 停止直写权威态`、`O5 · 派生渲染（PDF）`、`O6 · FastAPI HTTP 层退役`、`R0…R7`、`O7 · 双运行时运维面` |
| ③ 上线/运维件 | N1b、K1–K6、E3、P2、Go 防爆破、vercel cron | `N1b · 共享 k3s 真实 apply`、`K1…K6`、`E3 · QStash → arq + Redis`、`P2 · IAP 的 Go JWS 验签 PoC`、`Go 登录接 Redis 防爆破`、`vercel.json 的 /api/v1/cron/journey` |
| ④ 质量/内容欠账 | L 节遗留、A1b、D4、D5、E4，加三条**只存在于跟踪器**的登记项（测试隔离 flake / 名片 AIGC / 门限现网标定）；第四条「锚点漂移」2026-10-14 结案，见 N2 节 ✅ 行 | `L 节遗留 · 13 语言 reflect 两键的母语审校欠账`、`A1b · .trellis 文档面占星残留清理`、`pytest 跨文件共享 "u1" 库态的隔离缺口`、`名片/分享卡 PDF 的 AIGC 隐式标识`、`N4 门限的现网分布标定`、`D4 · PRD docx 同步`、`D5 · ASC 提审元数据`、`E4 · Expo SDK 51 → 53+`、`修 test_commitment 周配额那条既有失败` |

- 已按单状态规则拆开的混态行（同日）：`A1`→`A1`[x]+`A1b`[ ]、`A2` 的实机部分移出 ✅ 行指向 `K6`、`E1` 去 ⚠️、`N1`→`N1a`[x]+`N1b`[ ]、快照「方向冲突」改「已结案」、`L2` 的复数缺口去 ⚠️（已由 L4 结案）、`O1`/`O2`→⛔（交付物由 `R1`/`R4`+`R5` 承担）、`O7` 去掉与 `R4`/`R7` 重复的子项。
- 外部阻塞（不是代码）：`K1–K6` 需账号/域名/真机，`N1b` 需 apply 授权与镜像凭据，`P2` 需 sandbox 实机——这三类我没权限，只能备齐命令与判据。
- 🆕 2026-10-14 收口 `year_in_review`（#64/#65）时**发现但不顺手修**的两条，各带出处：
  - [ ] **`export_report` 的缓存键同样不含语种**｜`share_service.py:736 period_key = self._period_key(period, user.timezone)`，而 `_period_key`（`:1119`）只按本地日历给出 `2026-W41` / `2026-08` / `2026`｜载荷是语种相关的（`:744 _build_weekly_report(db, user, language)`、`:746 growth_service.generate_report(..., language)`）⇒ 同一用户同一周期换 UI 语言拿到的是首次生成语种，与年报 ② 修前同型。归属：与年报一批（share 域），改法＝键加 `copy.locale_for(language)` 后缀 + 复用 `mutate_share_lang_key_gate.py` 的四条变异口径
  - [ ] **登录成功那一刻不 drain 游客期队列**｜`frontend/src/context/AuthContext.tsx` 全文不含 `flushAnalytics`（已被 `analyticsGuestQueue.test.ts` 钉成可见事实，接上时那条会红提醒同步改口径）｜后果：游客期攒的事件要等**下次冷启动或回前台**才上送，同日登录的会话内拿不到｜归属：埋点/H3 批次，改法＝login 成功路径 fire-and-forget 一次 flush（不在 `registerForPushNotifications` 那类阻塞位）

---

## A. Sprint 1｜提审阻塞 🔴

- [x] **A1 · M7 占星模块移除**（0.5d）｜架构方案 §二 M7｜✅ 2026-09-28 完成（实测范围＝运行时代码 + 前端 + 15 语言文案；文档面的残留不属本行，另立 A1b）
  - [x] 删 `backend/routers/natal.py`、`services/natal_service.py`、`models/natal.py`、`repositories/natal_repository.py`、main.py 注册（含 test_natal_service.py）
  - [x] 删前端 `NatalChartScreen.tsx` + ExploreScreen tile + App.tsx 导航注册 + api.ts/types + i18n 15 语言 natal 键
  - [x] 移除 `pyswisseph` 依赖（requirements.txt + pyproject.toml）
  - [x] DB 表保留（alembic 迁移文件不删）；enneagram 不动
  - 验收实测：运行时代码 grep 无 natal 引用（残留"占星"字样均为提示词内**禁用措辞约束**，属预期）；`from backend.main import app` OK；pytest 217 过（2 个失败为工作区在途的 follow_up 签名/personality 配额改动，与 M7 无关）
  - ✅ 补验：A2 期间已重装 node_modules 并跑 `tsc --noEmit`（exit 0）+ jest 11 套件 88 例全过，M7 删除的前端引用无类型残留
- [ ] **A1b · `.trellis/` 文档面占星残留清理**（0.3d）｜A1 只覆盖代码面，spec/tasks 的 prd 文档仍是「占星当功能」的旧设计｜实测残留：`.trellis/tasks/P1-F04-social-export/prd.md:5`「性格类型+占星组合卡片」、`.trellis/tasks/P0-F01-ai-daily-message/prd.md:53`「占星行运事件」与 `:59`「占星仅作为增强维度」、`.trellis/spec/architecture.md`、`.trellis/spec/ui-design-system.md`
  - ⚠️ 判据要分开两类命中：**禁止性表述**（如 `P1-F01-push-notification/prd.md:72`「推送文案不含『运势/预测/幸运数字』」）是合规约束，**保留不动**；**把占星当功能维度**的旧设计才是待改写对象
  - 验收：`grep -rn "natal\|pyswisseph\|星盘" .trellis/` 归 0；`grep -rn "占星" .trellis/` 剩余每条都逐字列在「禁止/不主动」语境白名单里并给出行号
- [x] **A2 · M6 Life Archive IAP**（后端 3d + 前端 2d）｜架构方案 §二 M6 / 行动方案 P0-2｜SKU 形态已拍板：维持一次性买断（架构方案 §九-4，2026-09-28）｜✅ 2026-09-29 完成（范围＝代码 + 测试；sandbox 实机购买属未完成，独立行 K6 跟进，不在本行状态内）
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
    - ✅ 偏离已兑现（2026-10-14 复核）：`POST /share/first-read-card` 的 PDF 早期只有 reportlab 文本管线，现内部渲染已接品牌区（水印 + 下载文案 + 带本人推荐码的深链二维码），**端点签名一字未变**。实测出处：`backend/services/share_service.py:253 brand = await self._brand_for(...)` → `:255-257 self._render_pdf(..., brand=brand)`，docstring `:242-243` 写明「B1 登记的偏离已在此兑现」
  - [x] OnboardingScreen 快问 3 题 + `FirstReadCardScreen`（UI 原型 Screen 29 已就绪）
    - 引导变 3 步（Step 3 快问），选项 id 与后端模板对齐、文案走 15 语言 i18n；Step 2「跳过」不再直接结束，改为进入 Step 3
    - ✅ 游客态约束（实装 + 2026-10-14 加闸）：引导阶段无 token，`POST /onboarding/first-read` 必失败，因此答案先落 AsyncStorage（`@closenai/pending_first_read`），**登录/注册成功后由 AuthContext 补调**并缓存卡片（沿用 `@closenai/pending_mbti` 既有模式）；POST 失败时暂存不删，下次登录重试。出处：`AuthContext.tsx:57-76 flushPendingOnboarding`（取暂存 → `postFirstRead` → `saveFirstReadCard` → `clearPendingFirstRead`，整段 fire-and-forget），`:91` 登录、`:96` 冷启动已有会话各挂一次。验收：`__tests__/firstRead.test.ts` 新增 4 条源码闸（顺序、clear 必在 POST 之后、无暂存早退、`void` 前缀逐调用点定位）；变异 **F25/F26/F27** 各打红一次（把 clear 提到 POST 前 / 删早退 / 去 void），其中 F27 第一次跑**没红**，暴露断言写成整文件 `toContain` 会被冷启动那处顶包，已改为按 `login` 函数体切片后才验证成功——口径按此记录
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
  - ✅ 偏离 1（2026-10-14 结案并补闸）：D2 文案口径冲突——架构方案 §二 M2 写「D2 走模板直出」，PRD §5.19.3 表格写「复用主动消息四层公式」，实现取**模板直出**（与 M2 零 LLM 总方针一致，且避免剧本占 LLM 配额）。口径已转成可复判面：`backend/services/journey_service.py` 的 `_render_day2` 只读 `self._t(lang)["day2_first_outreach"]`、三层回退 `with_mood → with_quote → plain`，函数体内不出现任何 LLM 入口。验收＝`backend/tests/test_journey.py` 新增 3 例：`test_day2_is_template_direct_and_never_touches_the_llm`（渲染 D2 全程不碰 `message_generator`）、`test_day2_prefers_yesterday_mood_over_quote`（三层不退成两层）、`test_day2_falls_back_to_plain_question_without_any_signal`（无信号时的兜底不得造假断言）；`pytest -k day2` 实测 **5 passed**。变异自证：D6 把 `MessageGenerator` 接回 D2 渲染路径 → 红 ✓；D7 `if yesterday_emoji:` 短路 → 红 ✓；D8 兜底分支换成「编造的原话」→ 红 ✓。
  - ✅ 偏离 2（2026-10-14 结案）：架构方案 §四接口清单只列 `GET /journey/events`，实现补了 `POST /journey/events/acknowledge`——红点徽标必须有消费口，否则 `unseen_count` 永不清零。既有覆盖即闸：`test_acknowledge_clears_the_badge`（消费后 `unseen_count == 0`）+ `test_acknowledge_hides_other_users_events`（他人 event_id 走 404，不静默成功）。
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
  - 验收实测：`frontend/__tests__/demoPersona.test.ts` 164 例（2026-10-14 本批 +4 → **168**，新 4 例见下方 M5 偏离 1/5/6 结案行）= ① 7 个锚点（覆盖一周全部起始星期）× 数字自洽（weeks/样本日/delta/stats 全部由测试**独立反算**再比，不复用被测模块的实现）② 游客态隔离（数据层零 import、屏幕本地依赖白名单只准 colors+数据层+analytics、无 navigation）③ 15 语言 × `demo` 30 键**精确键集** + `onboarding.demoEntry` + 占位符白名单（只 name/n/value/weeks/delta/refs，多一个就红）+ 每个键真被源码引用
  - 🔧 变异验证 8 次（证明守卫不是空跑）：`weeks` 写死 3 → 7 failed；km 漏一个键 → 1 failed；屏幕键名打错 → 1 failed；去掉触底容差 → 1 failed；留白改回固定 dayNo → **10 failed**（周五/间隔不变量真的在管数据）；给数据层加 import：真实 `../services/api` 两次都是**加载期失败**（0 total，RN 依赖不可解析）而不是断言捕获，改用可解析 import 才验证到「数据层零依赖」这条断言本身会红——口径按此记录
  - 全量：jest 15 套件 391 passed；tsc exit 0；pytest 374 passed（零后端改动，与 B3 前同数）；变异后 4 个文件 `cmp` 逐字节复原
  - ✅ 偏离 1（2026-10-14 结案并补闸）：§二 M5 原文「`src/data/demoArchive.ts`」→ 实落 `src/config/demoPersona.ts`。jest 配置 `testMatch` 只收 `.test.ts` 且 `testEnvironment: node`，纯数据必须与 .tsx 分离才能断言；`src/config/` 是既有纯模块位（archive.ts / journey.ts / links.ts）。闸：`__tests__/demoPersona.test.ts`「数据层住 src/config/demoPersona.ts，src/data/ 与 .tsx 都不许出现」断言 `src/config/demoPersona.tsx`、`src/data/demoArchive.ts`、`src/data/` 三者均不存在，且屏内 import 路径逐字为 `'../config/demoPersona'`；变异 F1 建出 `src/data/demoArchive.ts` → 红 ✓。
  - ✅ 偏离 2（2026-10-14 结案）：§二 M5 原文「DemoArchiveScreen **复用** InsightsScreen / TimelineScreen 组件渲染」→ 没复用。InsightsScreen 是 317 行单体屏（取数与渲染揉在一起），`src/components/` 只有 AIConsentModal 与 BadgeWall，没有可移植展示件；改为屏内自带卡片渲染 + 纯 View 条形图。闸（既有）：「展示层除 analytics 外不发请求，也没有任何路由跳转能力」把屏内**本地** import 白名单钉成 `['../config/demoPersona','../services/analytics','../theme/colors']`，因此"复用某个 Screen 组件"这类本地引用一旦出现即红（口径限制：该断言只过滤 `..` 开头的本地路径，第三方包不在其列）。**2026-10-14 勘误**：原句「未顺手引入 `react-native-svg`（虽在依赖里但全仓零使用，属 E2 范围）」的后半句已过期——E2 批次落地后 `src/components/charts/HeatGrid.tsx:8` 与 `MoodLineChart.tsx:8` 成为其首个消费方；M5 当时「没有顺手引入」这个动作本身不变。
  - ✅ 偏离 3（2026-10-14 结案）：§二 M5 原文「`is_demo` profile 标记」、行动方案 P1-1 第 4 条「不计入真实数据库（用 `is_demo` 字段隔离）」→ **没有建字段**。入口挂在 Onboarding Step 1，那一刻是游客、无 token，任何 api 调用必 401，演示数据根本进不了数据库；改以「数据层零 import」的结构隔离替代。闸（既有）：「数据层零依赖：不 import 任何东西（连 react 都不需要）」断言数据层 `from '…'` 集合为空、且源码不匹配 `AsyncStorage|fetch\(|axios|XMLHttpRequest`——比"不含 api import"更强；屏侧由偏离 2 的本地 import 白名单兜住；变异自证 B3 段已登记（往数据层加真实 `../services/api` 是加载期失败 0 total，改用可解析 import 后断言本身转红）。本批未为该条新增用例——它已被更严的「结构上不可能发请求」白名单覆盖。
  - ✅ 偏离 4（2026-10-14 结案）：P1-1 第 3 条「右上角『用我自己的数据重开』→ 清空演示数据回到空态」→ CTA 用护城河 §6.2 的原话「开始书写你自己的」，但行为是**回引导 Step 2，不是 finish()**：直接完成会跳过 Step 3 三个快问，打断 PRD §5.19.2 的「onboarding → 初读卡生成率 100%」（B1 验收项）；且按偏离 3 无演示数据落库，没有可清空的东西。闸：`demoPersona.test.ts:294`「入口挂在 Onboarding Step 1，CTA 只回引导、不 finish（保住初读卡生成率）」。
  - ✅ 偏离 5（2026-10-14 结案并补闸）：P1-1 第 4 条「本地打包（`assets/demo_profile.json`）」→ 改为函数生成，理由见上（数字必须与曲线同源）。闸：「没有打包 JSON 资产，数字一律由 buildDemoArchive 现算」断言 `frontend/assets/` 与 `demo_profile.json` 不存在、数据层源码不匹配 `demo_profile|\.json|require\(|JSON\.parse|fs\.readFile`，且 pattern/memories/timeline 的每个 `dayNo` 都能在 `days` 集合里找到；变异 F2 建出 `frontend/assets/demo_profile.json` → 红 ✓，F3 让数据层指向打包资产 → 红 ✓。
  - ✅ 偏离 6（2026-10-14 结案并补闸）：P1-1 第 2 条列了「3 条 Pattern、2 条时间线卡片、**1 条 AI 断言示例**」四类 → 断言示例没有另造第五类内容：Pattern 卡自带「依据」行，它就是 §5.x 要求的那张断言卡形态。闸：「屏内只有概览 / Pattern / 时间线+原话四类卡片，没有第五类断言示例」用正则取 `cardTitle` 出现序列并断言 == `['overviewTitle','patternTitle','timelineTitle','memoryTitle']`，同时 `DEMO_KEYS` 不含 assertion/example 类键；「每条 Pattern 都渲染 `demo.patternBasis` 依据行，且依据非空」断言 `patterns.map` 切片内 `basis(p)` 恰 1 次、含 `<Text style={styles.patternBasis}>{basis(p)}</Text>`、三条 Pattern 的 `evidence` 全非空；变异 F4 塞第五类卡片 → 红 ✓，F5 删依据行 → 红 ✓。
  - ⏳ 未取证：① 该屏**从未在任何设备/模拟器渲染过**——曲线观感、SafeAreaView 顶部避让、30 根条在窄屏的宽度、15 语言长文案换行，全部只有类型与逻辑证据；② 看完率的触底数学有 4 条用例（含"内容不足一屏也算看完"），但 RN 运行时是否真在 `onScroll.nativeEvent` 给 `contentSize` 未取证。RN 0.74 的 `NativeScrollEvent` 只有 `contentSize`，第一版按 `contentContainerSize` 写被 tsc 当场拦下（更高版本才有后者），现已抽成 `isScrollViewAtEnd` 供 node 断言
  - ✅ 顺带记录的口径已取证并钉死（2026-10-14，#65）：四个 `track()` 事件只进本地 SQLite 队列。实测三条：① 游客**不发**上送——`App.tsx:169`（冷启动首帧）与 `App.tsx:179`（回前台）两个 flush 点都在 `if (isLoggedIn())` 门内，所以不会出现「游客必 401 → 队列被误判成已发送」；② 事件**不丢**——`analytics.ts` 全程不读登录态（闸用例对该文件断言不含 `isLoggedIn|getAccessToken|SecureStore`），只看本地 consent；③ 真正上送在登录后的下一次冷启动/回前台，按 `occurred_at` 补口径。验收：`__tests__/analyticsGuestQueue.test.ts` 8 passed（游客入队 4 点位、登录后一次 drain、`dropped` 也清队列、401 留队列且下次带全量）；变异 F21/F22 各去掉一处 `isLoggedIn()` 门 → 当场红
- [x] **B4 · M4 分享卡体系 B03**（2d，依赖 B1）｜架构方案 §二 M4 ✅ 2026-09-29 完成
  - [x] ShareService 模板 first_read / memory_card / year_in_review（year_in_review 由占位转实装，见下）
    - 品牌区 = 水印 + 下载文案 + **带本人推荐码的深链二维码** + 明文短链（PDF 常被截图转发，扫不动时链接还在）；只加在分享类 PDF，**Life Archive 刻意不加**——买断的个人档案不是引流物料
    - 初读卡全文静态直出、零 LLM → 不打 AIGC 元数据；周期报告与 Life Archive 打（`_render_pdf(aigc=True)` 落进 PDF `/Info`，实测字节可见）
    - 记忆卡：某条记忆的原话 + 写下日期 + 品牌深链；原话**不过措辞闸**（闸是给 AI 产出兜底的，M2 同一结论）；**正文**不写"一年前的今天"——周年选取规则属 C4（2026-10-14 勘误：本条只覆盖正文侧，标题侧由 `_memory_card_title` 按真实 `created_at` 判周年，详见 B4 偏离 2 的结案行）；周期/年度报告同带品牌区
    - year_in_review 已实装（#35，2026-09-29）：`GET /share/year-in-review` + `/pdf` 取**上一个完整自然年**，首年用户退到当年并如实标 `in_progress`（区间只截到今天）、整年零记录才 40402。零 LLM → 不打 AIGC 标识；数字全部走 COUNT（`mood.list_between` / `chat·memory·growth.count_between`），streak 按用户本地日历日算、`checkin_date` 为 NULL 的回填行回退 created_at；缓存键 `period=year_in_review` + `period_key=year_{yyyy}_{locale}`（`year_` 前缀避开 yearly 报告的裸年份键互吃缓存）。
    - ✅ 原登记的两处自定决策、文档无口径，2026-10-14 收口：① **不做 Pro 门控**（当引流免费卡，AI 年度长文报告仍走 Pro 40302）——由 `frontend/__tests__/yearInReviewEntry.test.ts` 的函数体切片闸钉死（年报路径不许出现 `isPro`/`showProAlert`/`Paywall`/`40302`）；② 缓存键**已含语种**（`share_service.py:490 _year_in_review_key`，语种走 `copy.locale_for` 归一 ⇒ zh 与 zh-CN 命中同一条，未知名回退 en），验收＝`test_year_in_review.py` **24 passed** + `scripts/mutate_share_lang_key_gate.py` **4 条变异全红**（去语种后缀 / 用 raw language 不归一 / 去 `year_` 前缀 / 删缓存早退），备份写回逐字节 `filecmp` 一致。
    - ✅ 前端入口已接线（#64，2026-10-14；此前登记「`ShareCardScreen` 只有 personality_card、前端零入口」）：`InsightsScreen` 年度热力图卡下方新增分享按钮 → `getYearInReview()`（`api.ts:616`，`language` 按当前 UI 语言透传、`year` 可选不传由后端定默认年）→ `trackFunnelShare('share_tapped'/'share_completed', 'year_review')` → `Share.share({ message: card.share_text })`，仅 `res.action === Share.sharedAction` 记 completed；40402 走 `insights.yearReviewEmpty` 空态、与请求失败文案分开。载荷字段面与后端 `_build_year_in_review` 的 return 键 + 端点补挂键**逐字对表**（两侧各 ≥12 键，防解析器空跑假绿）；15 语言 × 两键齐备且被源码引用。验收＝**jest 35 套件 892 passed / 0 failed**（基线 851 → +4 `firstRead.test.ts` 补调闸 → +1 `funnel.test.ts`（同屏双物料按 source 定位）→ +28 `yearInReviewEntry.test.ts` → +8 `analyticsGuestQueue.test.ts`；中途实测 **856** 是把两份新文件临时移开后复跑得到的，用来证明增量数字）＋ **全量后端 pytest 1 failed / 612 passed / 11 skipped**（唯一红是既有 #62，无新增失败；GO_BIN 不在 PATH 时同一套是 606/17，差额全是 `test_go_auth_interop.py` 的 8 条 go 用例）＋ `tsc --noEmit` exit 0；变异自证 **27 条红闸全打红 ＋ 2 条绿闸保持绿**（`scripts/mutate_frontend_gates.py`：F16–F24 覆盖 #64/#65，F25–F27 覆盖 B1 登录补调；G2 证明函数体增长不变红；备份写回 `filecmp` 逐字节一致）。F27 第一次**没红**，根因是断言写成整文件 `toContain`、被冷启动那处同形调用顶包，改成按 `login` 函数体切片后才验证成功——弱断言的教训按此登记。
    - B1 登记的品牌区欠账（原记为偏离，现于本节兑现）：初读卡/名片/报告的 PDF 统一加 水印 + 下载文案 + 带码深链二维码 + 明文短链，端点签名不变
  - [x] 深链 closenai.app/r/{code} + AASA + CF Pages landing（决策点 3）
    - 数据：`users.referral_code`（8 位、29 字符集，排除 0/O/1/I/L/8/9 这些手抄必混的形状）+ `referred_by_code`；迁移 `a7c1e9b4d2f8`（单 head）。**两列都不建外键**——referral 是归因标签不是关系引用，指向的人可能已注销，外键会把归因数据锁成删除约束
    - **惰性生成**：注册路径不预生成；`GET /share/link`、分享卡 PDF 导出、初读卡落库三条路径首次取码即写（读接口带一次写，查重走 SELECT + UNIQUE 兜底并发）
    - 归因入口：三个建号路径（密码注册 / 验证码登录未注册自动建号 / Apple 首登）新增 `ref`，非法码**静默丢弃而不是报错**——推广链接被截断不该让注册失败
    - `GET /share/resolve/{code}`（公开）：只回「码是否有效 + 两个 URL」，不回分享者任何资料；未知码与非法码返回同形结果（否则成了"探测某人是否在用这个 App"的入口）
    - 统一落点：性格名片与周期报告的 `share_url` 由 app_store_url 改为带码深链，且分享文案复用**同一个值**（两个落点并存时归因必断）；Life Archive 的 `share_url` 保持 app_store_url
    - App 侧：`app.json` → `ios.associatedDomains: ["applinks:closenai.app"]`；`App.tsx` 冷启动 `Linking.getInitialURL()` + `url` 事件 → 取码存 AsyncStorage（跨冷启动，内存态必丢）；`api.ts` 三个建号请求带 `ref`、成功即清；`getShareLink()` 供 App 内分享文案统一取链接
    - 静态站点产物（`site/`）：2026-10-14 更正口径——自 28a9fc3 起 `site/` 是 Astro 工程，下面这些**静态件住在 `site/public/`**（构建时整目录拷进 `site/dist/`，对外 URL 一字不变）：`.well-known/apple-app-site-association`（components 只接管 `/r/*`——写 `"*"` 会把整站变成唤起 App，落地页反而打不开）、`_headers`（Apple 要求 application/json 且不许 3xx，而这个文件刻意没有 .json 后缀）、`_redirects`（200 重写而非跳转，保住 pathname 里的码）、`r/index.html`（中英双语纯静态，按 `navigator.language` 分支）
  - 验收实测：pytest **374 passed**（新增 `test_referral.py` 34 例：码表与归一化 / 惰性生成+幂等+跨用户不撞码 / resolve 不泄露资料 + 未知码与非法码同形 + 注销者失效 / 初读卡 PDF 含二维码且不含 AIGC / `share_url` 落库即带码且 `restore` 复现 / 记忆卡越权 40403 / 年报 40402 / AIGC 元数据只在声明时落进 /Info / 名片与报告的 share_url==深链且文案末尾同落点 / 5 个端点级（share/link、resolve、memory-card 越权、年报、初读卡 PDF 流））；jest **227 passed**（新增 `referral.test.ts` 20 例：路径段、查询参数、子域放行、伪装后缀域与无协议串拒绝、8 类非法输入、peek 读不删、后到覆盖、脏值不带上注册请求、**与 backend/core/referral.py 码表逐字对表**）；tsc exit 0；落地页中英两分支经浏览器渲染取证（DOM 快照正确、console 无报错）
  - 🔧 补测时修掉两个真实缺陷，并变异验证了码表同步用例：① `resolve_referral` 对非法码返回 `…/r/`（尾巴为空的坏链）→ 改回站点根；② `parseReferralFromUrl` 原实现对**任意域名**下的 `/r/xxxx` 都放行，任何人都能把自己的码强绑到别人的安装上 → 加品牌域白名单（仅 closenai.app 及子域）；③ 把 TS 侧字符集加回 `L` 后「与后端码表逐字对表」用例转 ✕，证明该用例不是空跑
  - ✅ 偏离 1（2026-10-14 结案）：架构方案 §二 M4 写「referral_code 注册时生成、share URL 归因落 `share_repository`」，实现取**惰性生成 + 归因落 `users.referred_by_code`**。理由：迁移面对的是已有全量用户，预生成要么给老用户回填随机码、要么让从不分享的人永远挂着 NULL 走两条分支；而 share_repository 是导出缓存表，往里塞归因会把「谁带来了这个新用户」变成一次需要 join 导出记录的查询——归因是安装级标签，跟着 users 一行才与软删除/重注册解耦。闸（既有）：`test_referral.py` 的码表与归一化 / 惰性生成幂等 + 跨用户不撞码 / 三条建号路径带 `ref` 且非法码静默丢弃 / 归因落库与 `restore` 复现；本批该文件由 34 例扩到 **46 passed**。
  - ✅ 偏离 2（2026-10-14 复判并勘误）：架构方案 §二 M4 给 memory_card 的文案是「一年前的今天 + 原话 + 日期」，实现按**两侧分治**：**正文** `_memory_card_lines`（`backend/services/share_service.py:421-431`）恒为 `[intro, "", 「原话」, "", 日期]`、永不提周年；**标题** `_memory_card_title`（`:309-335`）在真实 `created_at` 与今天同月日、且年差 ≥1 时印「一年前的今天」/「{n} 年前的今天」（en "A Year Ago Today" / "N Years Ago Today"），否则回原模板。**原登记「实现不写周年框」表述有误——它只覆盖正文侧**；C3/C4 的周年标题根治（见本文件 C4 节）已把标题侧接上，判定只依赖记忆行真实时间戳 + 用户时区、与请求方传什么无关，因此"造不出假周年"由放宽判定改为由本地日界 + 年差 ≥1 + 缺时间戳早退三条共同守住。反向决策（标题也去掉周年）会推翻 C3 既有实现，属产品拍板、不在本批范围。验收＝`backend/tests/test_referral.py` 新增 7 个测试函数 / **12 例**（文件由 34 → 46 passed）：一年与多年都 format、五种非周年（年差 0 / 月不同 / 早一天 / 晚一天 / 未来）一律回原模板且不含周年词、时区铁律（today `2026-10-14T12:00Z` + created `2025-10-13T20:00Z`：Asia/Shanghai 判周年而 UTC 不判）、naive `created_at` 按 UTC 解释、`created_at is None` 回原模板且正文日期位为空、正文 zh/en 两侧都不含周年词。变异自证 D1（放宽成 `years_ago >= 0`）/ D2（标题不按用户本地日界）/ D3（多年不 format，直出 `{n}`）/ D4（删掉缺时间戳兜底）/ D5（把周年词写进正文）逐条打红 ✓。
  - ✅ 偏离 3（2026-10-14 结案并补闸）：架构方案 §五-决策点 3 原文「landing 页由 CF Pages 承载、**参数归因（utm + referral code）**」预设了链接参数能跟着用户走完安装，实测口径做不到——**不做剪贴板 handoff、不做手工填码入口**，「落地页 → App Store → 装完打开」这一跳参数必丢（Apple 不向第三方 App 透传任意 URL 参数）。v1 只有「已装 App → Universal Link 唤起」归因完整，**referral 统计口径是下界，不能当分享总数看**。下界口径成立的前提是这条路上没有旁门，本批把它转成 5 条负向闸（`frontend/__tests__/referral.test.ts`，文件 **33 passed**）：① `package.json` 的 deps/devDeps 不含 `expo-clipboard`、`@react-native-clipboard/clipboard`，递归扫 `src/`（排除 i18n）+ `App.tsx` 不命中 `Clipboard.(setString|getString|hasContents|readOnly)`；② 取码口只有 `App.tsx` 的 `Linking.getInitialURL()` 与 `Linking.addEventListener('url')`，`savePendingReferral(` 在 `App.tsx` 内恰 **1** 处（唯一收口 `ingestReferral`），注册页源码不出现 `savePendingReferral|parseReferralFromUrl`；③ 注册页源码不出现 `referral|ref_?[Cc]ode|邀请码|推荐码`，`api.ts` 三个建号请求的 `ref` 只能来自 `await peekPendingReferral()`（不许 `Linking` / `route.params` / `props.`）；④ `referral.ts` 头部注释「统计口径是**下界**」「不做剪贴板」「手工填码」在位（改数据口径的人先撞它）；⑤ `site/public/r/index.html` 无 `<input` / `<textarea` / `<form` 且无 Clipboard 读写。变异自证 F6（删下界注释）/ F7（`api.ts` 另开 `globalThis.Linking` 取码口）/ F8（`App.tsx` 加 `Clipboard.setString(code)`）逐条打红 ✓。
  - ✅ 本批偏离闸收口实测（2026-10-14）：`scripts/mutate_deviation_gates.py` 变异自证 **18 条 = 红闸 16/16 全打红 + 绿闸 2/2 保持绿**（D1–D8 覆盖 M2 偏离 1 + B4 偏离 2，F1–F8 覆盖 M5 偏离 1/5/6 + B4 偏离 3；绿闸 G1/G2 各往被守文件插 12 行注释，证明守卫数的是行为而不是行数）。恢复一律 `shutil.copy2` 写回 + `filecmp.cmp(shallow=False)` 逐字节校验、新建件按 unlink，**不用 git checkout**；跑完 `git status --porcelain` 只剩 4 个测试文件 + 新脚本，产品件全部复原。全量：前端 **35 套件 901 passed / 0 failed**（上一基线 892 → `demoPersona.test.ts` +4、`referral.test.ts` +5）、`tsc --noEmit` exit 0；后端 **1 failed / 627 passed / 11 skipped**（GO_BIN 在 PATH 口径，基线 612 → +15 全来自本批新增），唯一红为既有 #62 `test_commitment.py::test_weekly_quota_defers_third_follow_up`，本批对该文件零改动。
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
    - ✅ 2026-10-14 前端收口（原「属前端批次」欠账结清）：新建全屏屏 `frontend/src/screens/BedtimeReviewScreen.tsx` + `App.tsx` 注册路由 `BedtimeReview`（参数表 + Stack.Screen 三处齐）——三步同屏常驻（第 1 步过了不消失，收成一行引用）、半程按 `record.steps` 最深一步续接（重进不重发 step1，避免覆盖用户上一句原话）、step2/step3 进阶段自动生成且 `generating` 去重、危机分支按 `intercepted` 判型后**原地停住**并逐字渲染服务端转介文案与热线（不 Alert、不自造危机话术）、403/40303 走 `<AIConsentModal>` 而不是报错、`maxLength` 与后端 `max_length=1000` 同值；首页晚间兜底卡按「设备本地 ≥22:00 且今日未完成」出卡（UI 原型「未授权推送则显示为首页顶部卡片」；判据不依赖推送授权状态——前端只有系统级 permission，`src/services/push.ts` 只落库不发通知，判不出这条推送是否真送达）
    - ✅ 2026-10-14 验收实测：取数判定住 `frontend/src/config/dailyCards.ts`（jest 跑 node 环境渲染不了 RN 组件，只有纯函数能被断言）；新增 `__tests__/dailyCards.test.ts` **20 例** + `__tests__/dailyCardWiring.test.ts` **30 例**（含四条路径逐字核 `contracts/openapi.json`、源码每个 `t('键')` 在 en 表命中、输入上限回读后端 schema）；`i18nLocales.test.ts` 的 `PLURAL_KEYS` 扩到 9 个复数键；前端 jest **953 passed / 0 failed**（37 套件，基线 901/35 → +52 例/+2 套件）、`tsc --noEmit` exit 0；变异自证 `scripts/mutate_daily_card_gates.py` 红闸 **18/18** 打红 + 绿闸 **2/2** 保持绿（copy2+filecmp 逐字节写回）；后端本批零改动，全量 pytest **627 passed / 1 failed（#62 既有）/ 11 skipped** 与基线逐数相等
  - 验收：晚间 DAU ≥35%、完成率 ≥40%、D7 ≥3pt（上线后观测）
- [x] **C2 · §5.17 Growth Curriculum**（每周日 21:00 预生成 7 天主题，Rule 优先 + LLM 周规划）✅ 2026-09-30 完成
    - 生成：`GET /cron/curriculum`（vercel.json 每小时）→ `dispatch_curriculum_generation`：用户本地周日 21 点桶 → `curriculum_service.generate_weekly`：近 30 天素材收集（Memory + pattern_discovery records，零 LLM）→ LLM 周规划**一次 lite 调用**出 7 卡 JSON（ref 编号 → 服务端映射素材行，硬不变量：refs 全部归属该用户）→ Rule 卡轮转兜底（LLM 失败/解析失败/单卡违规逐卡回退）；卡内容出口确定性闸 = 全局 filter_template + **curriculum 专属红线词表**（运势/占卜/注定…全局表只有复合词）+ check_or(assertion) 语义层；零素材不生成（内容全部来自用户数据）
    - 存储（无新表，PRD 研发标注）：复用 journey_events，event_kind=`curriculum`，**day_no 编码 ISO 周序*7+周内序**（(user,kind,day_no) 幂等键跨周不冲突），payload={title,assertion,question,read_at}，scheduled_at=各展示日本地零点；生成即 `mark_sent`+`acknowledge`（首页池拉取式消费：不推送、不进报告页红点 count_unseen、不被剧本 cron list_due 扫描）
    - 读取：`GET /curriculum/week`（当周 7 卡：revealed=展示日≤今天、read、memory_refs、unread_count；未生成的天不出现在 days）+ `POST /curriculum/{event_id}/read`（payload.read_at，越权 40403，幂等）；均挂 require_ai_consent；目标周规则：本周一，周三及以后生成下周一（cron 48h 补偿窗口语义）
    - 与睡前复盘共用预生成管线：周日晚旅程生成结果作为下周睡前复盘素材池（PRD §5.17 关系段）
    - 验收实测：`backend/tests/test_curriculum.py` **13 例**（LLM 周规划被采用+refs 映射+ scheduled_at 七天落位 / 幂等 skip 零重跑 / 零素材 skip 零 LLM / LLM 坏 JSON 回退 Rule / 单卡"运势"违规回退其余保留 / get_week revealed+unread+mark_read / 空周 / 越权 40403+幂等 / cron 周日命中+跨时区不命中 / 非周日非 21 点不生成 / day_no 跨周编码单调 / kind 注册）；全量 pytest **499 passed**；conftest `_TIME_PATCH_TARGETS` 登记 curriculum_service；固定未来日期冻结（2026-11-01 周日 21:30，curriculum 链路无 created_at 窗口依赖，记忆素材行手动挪窗口）
    - ✅ 2026-10-14 首页次位卡前端收口：`HomeScreen` 出「🗺️ 本周主题旅程 · 第 N 天」卡——`GET /curriculum/week` 取数、`pickThemeCard` 只在 `revealed && !read` 里取 `max(date)`（全周读完整卡隐藏，不端旧卡骚扰）、7 格进度由 `weekCells` 按后端同一份 `day_no % 7` 编码还原（未生成的天补 future 而不是少一格）、未读 badge 直取 `unread_count`；点击 `POST /curriculum/{event_id}/read` + 本地乐观收敛 `unread_count`，标记失败不拦跳转（下次仍能看到这张卡，比进不去对话好）；埋点 `track('curriculum_card_opened', {feature:'curriculum', action:'open'})`
    - ⚠️ 卡片点击率 / 由卡片进入对话的占比：指标上线后观测（本批只出埋点，无观测面板）
- [x] **C3 · §5.18 承诺回访收尾**（后端）✅ 2026-09-30 完成
  - [x] memories.labels 承诺标签 + Rule 抽取（`commitment_service`，零 LLM）+ 频控退出 + 禁打卡边界验收
    - 捕获：chat 流尾部 `on_user_message`（负向退出优先 → Rule 抽取意图句）→ `create_memory`(goal/self) + labels JSON `{"labels":["commitment"],"commitment":{kind,follow_up_count,muted}}`；重复表达 dedup 命中不重复预写；危机内容不捕获
    - 调度：捕获预写首条 journey_events（kind=commitment_follow_up，habit +7d / event +1d，本地推送窗口）；投递成功后 `on_follow_up_delivered` 计数 +1 并按同型间隔预写下一条（同承诺 ≤2 次）；`schedule(dedupe=False)` 绕过 day_no 幂等键（不同承诺同日捕获冲突），防重写由 payload.memory_id 查重保证
    - 频控：每用户每周 ≤2 条（本地自然周周一起，超出 defer +7d，pending 永不删行）；负向回复（"没做，别问了"/stop asking…）→ 最近被回访承诺 muted，pending 到点 render skip（沉默陪伴）
    - 渲染：四层公式模板（问候→承诺回放→关心提问→开放 CTA），zh/en `data/journey/*.json`；memory_refs 硬不变量照常；投递 payload 合并预写键（memory_id/captured_day/excerpt 不被 set_payload 覆盖）；发送即 acknowledge（消费面在对话流，不进报告页红点）；PUSH_KINDS 增补（push 深链对话页）
    - 出参：MemoryResponse.labels 透传（无标签 None）
    - 验收实测：`backend/tests/test_commitment.py` 15 例（habit/event 抽取与调度 / 无关消息不捕获 / 危机不捕获 / 重复表达单回访 / 投递渲染+refs+发送即已读 / 负向退出 mute+skip / 无历史负向不误伤 / 周频控第三条顺延一周 / 同承诺 ≤2 后不预写 / zh-en 模板禁打卡词表 / 无成长记录落库+labels JSON 键集+payload 键集 schema 断言 / labels 出参）；全量 pytest **459 passed**；alembic 单头 a7c1e9b4d2f8
    - ⚠️ 2026-10-14 偏离口径定稿但**证明有缺口**，显式登记不结案：第二条回访按**实际投递日**+同型间隔（habit +7d / event +1d）而非「捕获日+14/3」，代码落点 `commitment_service._schedule_follow_up` 计划时刻 = `local_today(tz) + interval_days`（backend/services/commitment_service.py:311-320，注释在 :86-87）——顺延后节律自然后移、避免追赶补发。缺口：`test_commitment.py:268 test_same_commitment_max_two_follow_ups` 里捕获日 2026-10-08（:26 `FROZEN_NOW`）、首条 10-15 投递、第二条断言落 10-22 窗口（:280-281），而「捕获日+14」同样等于 10-22 ⇒ **该用例区分不了两种口径，不能当这条偏离的验收证据**。唯一能区分的只有「首条被周频控顺延后再投递」那条路径，而它正是 #62 现在红着的 `test_weekly_quota_defers_third_follow_up`。欠账归 #62：修那条时一并补一条「顺延后 投递日+7 ≠ 捕获日+14」的断言，别把这条偏离当成已验收
- [x] **C4 · §10.1 记忆考古分享链路**（行动方案 P1-2：回忆卡模板 + 分享出口）✅ 2026-09-30 完成
  - [x] 候选端点 `GET /share/on-this-day`（首页每日主位「N 年前的今天，你在想什么」数据源）
    - 逐往年窗口查询近 3 年（`ARCHAEOLOGY_MAX_YEARS`）：按用户本地时区取今天月日 → `local_day_start_utc` 本地日界窗口 → `memory_repository.list_between(limit=5)`；候选按年份升序（1 年前优先），字段 {memory_id, content, years_ago, created_at}；零 LLM；新用户/无周年 → 空候选不报错
    - 2/29 非闰年 `date()` ValueError 跳过该年（不崩溃不造假数据）；UTC+8 类本地日界覆盖测试（2025-10-31 16:00 UTC 的记忆被 Asia/Shanghai 用户在 11-01 命中）
  - [x] 回忆卡模板补周年标题：`export_memory_card_pdf` 由服务端按真实 `created_at` 自动判定（同月日且年份差 ≥1 → 「一年前的今天」/「N 年前的今天」/ en "A Year Ago Today" / "N Years Ago Today"；否则保持「你说过的话」）——**根治 C3 偏离 2**：任何路径都造不出假周年（判定只依赖记忆行真实时间戳 + 用户时区，与请求方传什么无关）；PDF 测试用 pypdf 解文本断言（pypdf 入 pyproject dev 依赖组）
  - 验收实测：`backend/tests/test_on_this_day.py` **7 例**（候选年份升序+归属隔离+新用户空态 / UTC+8 本地日界 / PDF 周年标题 zh+en / 非周年保持原标题）；全量 pytest **506 passed**（基线 499 + 7）；OpenAPI 路由注册核验
  - ✅ 2026-10-14 回忆卡第四模板 + 情绪色环收口（把 E2 那条「色环本批不做」的立项兑掉）：
    `ShareCardScreen` 的 `VARIANTS` 加 `'memory'`（`types/index.ts` 的 `ShareCardVariant` 与 `App.tsx` 的
    `ShareCard: { variant?, memory_id? }` 同步）；首页展开态每条考古卡给「做成回忆卡」按钮，带 `memory_id` 跳入
    （`HomeScreen.tsx:411-422`）并打 `memory_card_opened`。memory 分支走 `getOnThisDay()` 而不是 `getPersonalityCard()`
    ——回忆卡是免费引流出口，走错就撞 40302 Pro 门；按 `memory_id` 命中、否则取首条有原话的候选、缺参只回 classic 不猜记忆。
    卡面四要素＝日期大字（`memoryCardDate(服务端 date, years_ago)`，不碰 `created_at`、不看设备时钟）+ 当年原文
    （`memoryCardExcerpt` 截 `MEMORY_CARD_EXCERPT_MAX=220`）+ 情绪色环 + App 水印；分享出口仍是「原话 + 带本人推荐码深链」
    两段（`memoryCardShareMessage`），深链只在真要分享时取、防连点、失败走屏内 error 出口不开新 Alert 站点。
    - 色环规格（2026-10-14 拍板「静态五段环 + 当前段高亮」，①–⑤ 逐条写在 `frontend/src/config/memoryRing.ts` 抬头）：
      五段等分 360/5=72°、起始 −90°（12 点）顺时针、段序号＝情绪值；色相 0°→120° 线性（SATURATION 72 / LIGHTNESS 55，
      与 `HEAT_COLORS`、后端 `MOOD_COLORS` 的「低落偏红、开心偏绿」同向）；当前段 opacity 1 且外半径 +2、其余 0.28；
      当日无签到 → 五段全 `rgba(255,255,255,0.18)` 且中心不放 emoji；无 Animated（分享卡要能被截图，补间帧不可复现）。
      换算全部住纯函数，SVG 组件 `charts/MoodRing.tsx` 只消费 `ringSegments()`/`annularSectorPath()`，不自算几何。
    - 数据源：`share_service.on_this_day` 的候选加 `mood`——用**同一条本地日界窗口**（`local_day_start_utc`）取当日签到，
      首条为准、`label` 缺失按 `users.language` 兜底、分值复用 `MOOD_MAP` 同一张表（表外 emoji 折中 3）、无签到返回 `None`；
      前端零额外请求，也就不新增第二个同意门/配额面。
    - 验收实测：后端 `backend/tests/test_on_this_day.py` **13 例**（原 7 + mood 6：候选带 mood / 取当日首条而非别条 /
      label 空兜底 / 签到随本地日界 / 别日签到负向 / 表外 emoji 折中 3）；前端新增 `__tests__/memoryRing.test.ts` **31 例**
      + `__tests__/memoryCardWiring.test.ts` **51 例**；全量 jest 39 套件 **1035 passed**（基线 37 套件 953）、`tsc --noEmit`
      exit 0、契约冻结 `test_contract_freeze.py` **10 passed**（`contracts/openapi.json` 字节未变，无需重导）；
      新文案 6 键（`home.archiveRecallCard` + `share.memory` / `memoryEmpty` / `memoryMoodNone` / `memoryMoodA11y` /
      `memoryShareFailed`）× 15 语言，`i18nLocales.test.ts` **107 passed**；变异自证
      `python3 scripts/mutate_memory_card_gates.py` → **红闸 31/31 打红、绿闸 2/2 保持绿**，源文件逐字节复原无差异。
  - ✅ 2026-10-14 首页每日主位前端收口：`HomeScreen` 首屏顶部出「🕰️ N 年前的今天」卡——`GET /share/on-this-day` 取数，`archiveCards()` 保服务端年份升序 + 丢空原话 + 截 `ARCHIVE_CARD_LIMIT=3`（首页不是列表页），折叠态只端首条、展开逐条并给日期行（`created_at` 缺失即不显日期，不拿「今天」或年份差反推一个像样的落库日）与分享出口；周年标题走 i18next CLDR 复数键 `home.archiveTitle`（15 语言按各自类别档恰好齐备：en/de/es/fr/it/pt one+other、ru one/few/many/other、其余单形态只 other），前端不自算日期；零候选整卡不出；分享清单 `<ShareManifest>` 两项与实际发出去的两段同源（`trustPresentation.test.ts` 已从三条分享路径扩到四条）
  - ⚠️ 2026-10-14 偏离登记（新增，未收口）：考古卡的分享出口是「原话 + 带本人推荐码的深链」文本分享（`Share.share`），**没有接 `POST /share/memory-card` 的 PDF**——`frontend/package.json` 22 个运行时依赖实测无 `expo-file-system` / `expo-sharing` / `react-native-share`，落 PDF 要新增原生依赖并牵动 #52 的 SDK 升级，不在前端批次里偷偷塞原生依赖；PDF 物料接入归 #50 K6（真机系统分享面板取证）与 #58（名片/分享卡 PDF 的 AIGC 隐式标识）。`dailyCardWiring.test.ts` 有负向闸钉住「首页源码不出现 `/share/memory-card`」，接上那天要同时改这条闸

## D. 规范工程（2026-09-28 第六批，无排期依赖，可插队）

- [x] **D1 · evidence_policy prompt 段落**（~1d）｜规范 §一 + 架构方案 §六-4 ｜2026-09-29 完成
  - [x] SafetyService 双语段落 + 注入 message_generator/mood_service/模板出口
  - [x] D 级词汇并入黑名单判定；SentenceGate 单测扩例（因果断言/标签推断样本被拦）
  - 实测：`pytest backend/tests` **276 passed**（基线 243 + 新增 `test_evidence_policy.py` 33 例）。
  - ✅ 2026-10-14 复核（注入点范围）：按架构方案 §六-4「及未来所有面向用户的生成出口」扩到现存 **15 处**出口——
    `backend/tests/test_evidence_policy.py:23` 的 `OUTLETS` 表 AST 实测恰好 15 个元组（:42 parametrize 逐条成例）。
    构成：D1 首批 3 处（`MessageGenerator.generate_chat_stream`、`MoodService._interpret`/`_deep_interpretation`，
    即架构方案 §六-4 点名的 message_generator / mood_service 注入点）+ 本条扩到 12 处（personality 画像×2/组合报告、
    growth 下周预测/模式发现/报告正文/`_llm_or_fallback`（覆盖每日洞察、周报、主题卡）、reflection 草稿、share 名片标语、
    push 文案、承诺回访 follow_up、主动消息）。
  - ✅ 2026-10-14 复核（判定分级）：确定性黑名单（§5.16 医疗化词 + §1.4 D 级词，`safety_service.py:224` `BANNED_TERMS`）
    对全部出口生效；**推理越界句式只在 `scope="assertion"`**（`safety_service.py:242` 口径注释）生效，对话流不启用——
    实测非测试代码里 `scope="assertion"` 调用点 **12 处**（share_service.py:1161、curriculum_service.py:316、
    growth_service.py:502/758/786/872、message_generator.py:248/392、reflection_service.py:91、mood_service.py:234、
    personality_service.py:517、bedtime_service.py:311），覆盖洞察/模式/报告/剧本/主动消息/回访/分享卡。
    聊天里"因为他没回你"是正常复述用户叙述，正则拦截会毁掉可用性，该情形交给 L1 段落 + 语义分类器。
  - ✅ 2026-10-14 复核（两处刻意豁免，理由在 `safety_service.py:219-223` 注释里）：「高价值/低价值」不入正则表
    （已用作 Discovery 幅度分级内部术语，`discovery_service.py:24`、`test_discovery_fusion.py:304` 即误杀样本）；
    en 表不含 `treat`/`therapy`（`BANNED_TERMS["en"]` 实测无这两项，普通动词与求助语境高频，正则误杀率过高）。
    两者仍进 L1 段落与 `_CHECK_PROMPT` 语义判定。
  - ✅ 2026-10-14 勘误（原记「未做：架构方案 §六-1 的 `filter_template()` 静态模板闸」）：**这条是台账陈旧，不是能力缺口**。
    `filter_template()` 已实装并在生产路径被调用：定义 `backend/services/safety_service.py:412-429`（只跑
    `banned_hit`/`attribution_hit`、**零 LLM**，命中返回 `""` 由调用方降级，命中按 error 级记日志）；模板渲染出口三处
    ——`first_read_service.py:75`（M1 初读卡）、`journey_service.py:414`（M2 第一周剧本 + M3 level_reward 正文，
    经该文件 `_gate_tpl()`（:425，填充→过闸→回填原话）覆盖 trial_d5/day0-day7/level_reward/commitment_follow_up
    共 13 个渲染点）、`curriculum_service.py:77`（§5.17 主题卡 Rule 兜底）。
    原句「M1/M2/M3 模板尚不存在」在 2026-09-29 成立、现已过期：B1/B2 文案渲染出口落地后函数随之实现，
    只是没回头改这条台账。模板库自身逐条过闸的负向测试也已存在（`test_first_read.py:147-154`、
    `test_journey.py:502`、`test_bedtime.py:279/431`、`test_trial_d5.py:173/212`）。
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
  - ↳ **2026-10-14 结案（本行原为「⚠️ 未做」，实为台账陈旧）**：两条挂载都已落地并有测试钉住——
    §5.16 睡前复盘 → `services/bedtime_service.py:118`（`test_bedtime.py:145 test_step1_crisis_intercept_blocks_and_audits`）；
    D0–D7 剧本渲染 → `services/journey_service.py:456`（引用原话前拦）与 `:851`（候选记忆回放前拦），
    `test_journey.py:318/:396` 各钉一条；另有 `message_generator.py:85`、`commitment_service.py:180` 两处出口。
    剩下的「15 语言词表」不是欠账而是范围决定：决策点 3 明确 crisis 词表不进 v1（非 zh/en 走英文词表 + 转介文案按 `users.language` 出稿）。
  - 实测：新增 `backend/tests/test_crisis_intercept.py` **17 例**（含规范 §二 2.3 三条验收）；
    全量 `pytest backend/tests` **293 passed**。
- [ ] **D3 · 说话人锚定纪律**（纸面预留，随"截图复盘收窄版"立项才实施；立项前不动代码）｜规范 §三
- [ ] **D4 · PRD docx 同步**：第六批修订从 html 回灌 closenai.docx（编辑器导出）
- [ ] **D5 · ASC 提审元数据**：副标题 "It remembers everything you tell it"；关键词去 tracker/journal（提交时操作清单）

## E. 技术栈执行项（2026-09-28 定稿）

- [x] **E1 · 换语言议题结案**｜✅ 2026-10-12 关闭：原口径「后端维持 Python/FastAPI、无换语言议程」（依据＝负载 IO-bound、4c8G 预算已验证）**已被 D-1 反转**为「Go 对外 HTTP + Python 专职 AI」，反转登记见 ↳ 与 §N 立项
  - ↳ **2026-10-12 改判（本条原口径作废）**：用户拍板「Go 负责对外提供 HTTP 接口以及后端服务，Python 主要负责 agent 以及 AI 相关的内容」→ 采纳 V3 运行时分层，立项 **N 节（Go Core）+ O 节（Python AI Runtime）+ P 节（并行与风险）**。原理由（IO-bound、预算已验证）不作废但不再单独决定选型；迁移面实测与切流策略见 N 节抬头。本节 E3（QStash→arq）的队列结论随 D-7 在 O2 重判
- [x] **E2 · 前端补 `react-native-svg`**——趋势曲线/热力图还原 ✅ 2026-09-30 完成
  - 依赖已在 package.json（15.2.0）但全仓零使用（M5 偏离 2 遗留）；本批首个消费方落地
  - [x] `src/components/charts/transforms.ts` 纯函数（jest node 环境可测，SVG 组件不进测试——沿 M5 偏离 2 口径）：`toLineSegments`（折线分段：value=0 未签到断线不插值，返回 segments≥2 点 + singles 孤点，x 按有值序列索引均分）+ `toHeatMatrix`（GitHub 式矩阵：列=自然周周一起始、行=星期几，首列按首日星期补前导 null，缺失日 null 格，非法日期/空输入回 7 行空矩阵不崩溃）+ `HEAT_COLORS` 自 InsightsScreen 上收共享
  - [x] `MoodLineChart.tsx`（Polyline 连段 + Circle 标孤点，金色主线）+ `HeatGrid.tsx`（SVG Rect rx=3）接入 InsightsScreen 热力图卡：近 7 天折线（X=日期 Y=情绪值 1-5，PRD §P0 情绪趋势可视化）+ 热力图默认近 90 天、「查看全年」toggle 切 365 天横向滑动（PRD 默认/展开视图口径）
  - 验收实测：`__tests__/chartTransforms.test.ts` **8 例**（连续段/断线成两段+孤点/全空回空/断口两侧 x 均分不挤压/首列对齐/缺失日 null/跨周换列/空与非法日期）；tsc 0 + jest **847 passed**
  - ↳ **2026-10-14 结案（本行原为「⚠️ 色环本批不做」）**：色环已随 C4 回忆卡第四模板立项落地。
    视觉规格定稿（静态五段环 + 当前段高亮）写在 `frontend/src/config/memoryRing.ts` 的抬头注释①–⑤，
    组件 `frontend/src/components/charts/MoodRing.tsx` 只消费换算结果；数据源是 `GET /share/on-this-day`
    候选自带的 `mood`（与记忆共用同一条本地日界窗口，前端零额外请求）。沿本条口径：几何/颜色仍是
    `memoryRing.test.ts` 里的纯函数断言，SVG 组件不进渲染测试。
- [ ] **E3 · 部署迁移 QStash → arq + Redis Pod**（k3s 清单见架构方案 §十；inline 回退保留）
- [ ] **E4 · Expo SDK 升级（51 → 53+）**——提审后技术债第一项（连带校验 react-native-iap 兼容性）

## F. 变现动线（§5.20/§5.21，状态待核验后补拆）

- [x] **F1 · 洞察可视化分级**（模糊 vs 清晰，锁呈现不锁原始数据）✅ 2026-09-30 完成（现况核验后拆定）
  - [x] tier 数据源：`/growth/dashboard` 响应加 `is_pro`（growth_service 以 `user.tier != FREE_TIER` 同源判定）——HomeScreen 是缓存秒开屏（SWR），不能靠并行 profile 请求（升级用户会先模糊后清晰的闪变，或反向），dashboard 每次聚焦刷新天然携带最新 tier；`GrowthDashboard.is_pro` 前端类型 optional（旧缓存缺省按免费，宁先锁后解）
  - [x] 判定口径统一：`src/services/insightTier.ts` `isProTier(tier)`——Insights 由内联 `!== 'free'` 切换至此（非 free 一律解锁，后端新增档位向前兼容；tier 未知按免费锁定）；Home 的 dashboard.is_pro（布尔）直接用
  - [x] `InsightBlurGate.tsx`（expo-blur ~13.0.3，`npx expo install` SDK 51 配套）：locked 时 BlurView 低强度覆盖（intensity 30 ≈ PRD「CSS blur 4px」原生观感）+ 整层可点 → 调用方导航 Paywall + 常驻「解锁高清」金色角标；free 态聚合最小洞察（weekly_insight 卡）保留在闸门外
  - [x] 落点（PRD §5.20 模糊对象仅限洞察层）：① Home「Growth This Week」指标+迷你热力图包闸（header/提示行不模糊，点卡进 Insights 不变、点模糊区进 Paywall）② Insights「Pattern 卡」免费 = 模糊样张 + 角标、**不调 LLM**（样张静态模板零成本，升级才触发生成）③ Insights「情绪趋势 + 年度热力图」卡包闸（免费锁呈现，原始签到数据在 Reflect 页永远可看可查）
  - 验收实测：后端 `test_dashboard_is_pro_flag`（free→False / pro→True）+ 全量 pytest **507 passed**；前端 `__tests__/insightTier.test.ts` 4 例（free/pro/pro_plus/未知 tier 边界）；i18n 15 语言新增 unlockHd/patternPreview/heatmapFullYear/heatmap90（#39 英文兜底守卫通过：13 小语种全部正式翻译非英文）
  - ↳ **2026-10-14 结案（本行原为「⚠️ 划归 F2」）**：两条都已在 F2 批次落地——
    Paywall 左右对比区块 `frontend/src/screens/PaywallScreen.tsx:448-459`（免费列 BlurView 样张 + 金框 Pro 列），
    首次解锁「揭幕」过渡 `frontend/src/screens/HomeScreen.tsx:265-277`（本地一次性标记）与 `:720-723`（遮罩层）。
    实测口径：这两处**没有专门的接线闸**（jest node 环境渲染不了 BlurView/Animated，前一批只在 F2 台账里记了落点），
    补结构闸属测试资产桶、不在本批范围内。
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
  - ↳ **2026-10-14 结案（本行原挂 ⚠️）**：改口径这件事已落地并被变异守住。`comboCard.test.ts` 原要求非 zh 语言的
    `share.proGateMessage` 含英文 token `/16-type/` 与 `/Enneagram/`——那是**只有英文兜底存在时**才可能过的代理断言，
    真翻译之后 ms（`Sembilan Personaliti`）与 th（`นพลักษณ์`）的正经译名会被它判成漏写 → 改成「含数字 16 +
    该语言的 Enneagram 译名」逐语言术语表（`comboCard.test.ts:114 ENNEAGRAM_TERM`，:136-137 逐语言断言）。
    回退成旧口径会当场打红：`scripts/mutate_frontend_gates.py` 的 **F29**（2026-10-14 实测 3/3 打红的一条即它）。
  - 全量：jest 29 套件 **809 passed**（本轮 +92）；tsc exit 0；pytest **444 passed**（后端零改动）

- [x] **L2 · 13 个语言的母语审校轮**（把 L1 那句「未经母语者审校」兑掉）｜✅ 2026-09-30 完成
  - 做法：逐语言导出 249 键的 `{en, current}` 对照表 → 13 个语言各跑一遍母语审校（只许改值、不许动占位符 / `\n` / 品牌词）→ 回稿固定四字段 `changes / reasons / same_as_english_is_correct / inconsistencies`
  - 回稿先过机器闸（`/tmp/i18n/validate_review.py`）再进写回器：键必须在 249 内、`{{占位符}}` 名称与个数不变、`\n` 个数不变、MBTI 四字母码不变、品牌词（ClosenAI / App Store / Apple / Google Gemini / `Pro` / `Pro+`）按**词界**判定不许丢（`Protagonist` 不算 `Pro`，这条最初误伤 3 个键）、「与英文逐字同」只认同形词白名单
  - 落库：**13 个语言 205 处值**（每语言 11–25 处），全部经「恒等自检 → 写回 → 重新解析文件逐键比对现值」三段自证，写入数与复核命中数一致
  - 审校抓出的**机器闸结构性盲区**（前一轮的字节同字闸看不到）：为躲「不许与英文同字」而造的生造词——ms `Sembilan Personaliti`、vi/km/id 的 `9 Kiểu Tính Cách`/`បញ្ហាបី`-式拼凑、it `Distinctivi`、fr `Distinctif`。这类词的正确写法本就与英文同字，所以同形词豁免表从 5 语言 7 键扩到 **9 语言 37 键**，逐条写理由，且「不许留死条目」那条断言继续保证它们此刻真的同字
  - 连带改动：`i18nLocales.test.ts` 的「非拉丁文字必含目标文字」闸此前只豁免 `SHARED_BY_DESIGN`，与同形词表互相矛盾（km 的 `Enneagram` 被它判成漏翻）→ 两道闸改为**共用同一张豁免表**；`comboCard.test.ts` 的 ms 译名从生造词改成 `Enneagram`
  - 反向验证 7 条全部 CAUGHT、逐字节复原（脚本 `/tmp/i18n/review_guard_mutation.js`，恢复只走进程内备份写回）：豁免键被改回非英文 / 非豁免键被写成逐字英文 / 豁免表塞死条目 / ja 写成拉丁借词却未豁免 / 文字集闸不再共用豁免 / comboCard 术语表回退 / `{{count}}` 被丢
    - ↳ **2026-10-14 勘误（证明缺口，不是结论改动）**：`/tmp/i18n/` 已消失，那 7 条当场不可重跑，属于「只剩口头声明」。
      可重跑的只有前 4 条的等价变异（`scripts/mutate_frontend_gates.py` 的 F1 非豁免键写成逐字英文、F2 拉丁借词未豁免、
      F5 豁免条目变死条目——它同时覆盖「豁免键改回非英文」）；后 3 条本批固化成 **F28/F29/F30**（前两条破坏守卫自身，
      证明它真的在消费 HOMOGRAPHS / ENNEAGRAM_TERM；第三条破坏数据，证明占位符闸抓得到丢掉的 `{{count}}`）。
      实测：`python3 scripts/mutate_frontend_gates.py --only F28,F29,F30` → **3/3 打红**、逐字节复原无差异；全量 30 条锚点 `--check` 各命中一次
  - 本轮**当时没修**的一类结构缺口（审校 3 个语言同时点名，不是措辞问题）：8 个含 `{{count}}`/`{{days}}` 的键在所有语言都只有单数形态，`de` 因此出现「1 Gespräche」、`it` 出现「1 messaggi」——i18next 的 `key_one`/`key_other` 复数变形一个都没建。修它要动 15 个 locale 的键结构 + 调用点传参口径，属独立批次，登记为 L1 之外的待办，#17 提审前不要把它当已修 → ✅ 已由 **L4** 修复（2026-09-30）
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
  - 待做：提审拿到 id 后，`backend/config.py` 的 `app_store_url` 与 `site/public/r/index.html` 的 `APP_STORE_URL` **两处同改**（当前均为占位 id6740000000；2026-10-14 更正路径口径，28a9fc3 起落地页在 `site/public/` 下）
- [ ] **K5 · 密钥类：Sentry DSN、PostHog 两个 key、JWT_SECRET/CRON_SECRET 生产值**（H1/H3/A 节）
  - 代码侧已留配置口并且缺值时降级可用；上线前必须注入，否则崩溃上报与埋点静默空转
- [ ] **K6 · 真机取证三档**（A2 / E3）
  - Life Archive IAP sandbox 三档购买、唤起→取码→注册带 `ref` 全链路、分享卡系统分享面板——都要设备安装 prebuild 包后手工验
- [ ] **K7 · Android 侧 assetlinks.json**（若上 Android）
  - iOS 有 `site/.well-known/apple-app-site-association`，Android App Links 需等价的 `/.well-known/assetlinks.json`（含 SHA-256 指纹，只能由签名产物算出）——当前**未生成**，v1 只上 iOS 时可搁置
  - ⚠️ 顺带：`.git/config` 的 origin/gitee URL 内嵌明文 token，属仓库外动作（轮换凭证 + 改走 SSH/credential helper）

## N. Go Core Runtime｜对外 HTTP + 权威后端服务（2026-10-12 改判立项；D-1…D-8 已于 2026-10-13 按默认值拍板）

> 改判依据：用户 2026-10-12 指示「**Go 负责对外提供 HTTP 接口以及后端服务，Python 主要负责 agent 以及 AI 相关的内容**」→ 采纳 V3 的运行时分层（00/05/06/07–09/14–18）。tasks.md E1 原口径已就地回写为 ↳ 改判。
> 迁移面实测（2026-10-12 计数）：**97 个 HTTP 端点**（17 个 router：cron 16 / share 12 / growth 12 / auth 12 / personality 9 / user 8 / memories 6 / subscription 4 / reflection 3 / mood 3 / onboarding 2 / journey 2 / curriculum 2 / chat 2 / bedtime 2 / discovery 1 / analytics 1）、**28,801 行 Python**（其中 `backend/services/` 10,510 行）、**24 个 alembic 迁移**、PostgreSQL 方言已定（Supabase + pgvector `halfvec(512)`）。
> 关键有利事实：前端只依赖 REST + 一条 SSE（`backend/routers/chat.py:53` `/chat/stream`）——**端点与响应结构不变 ⇒ 已打包/已提审的 App 零改动，Go 与 Python 的替换对客户端透明**。这是本次分层能与产品主线并行的前提，也是 N0 必须先冻结契约的原因（见 P1）。
> 工时口径：N0–N9 ≈ 8–10 周、O1–O7 ≈ 4–5 周（单人，含测试与灰度）。估算依据＝上面三个实测计数，不是感觉值。

- [x] **N0 · 契约冻结与改判登记**（1d）｜前置，阻塞 N/O 全部｜✅ 2026-10-13 完成（过测试）
  - [x] FastAPI `openapi.json` 导出为 `contracts/openapi.json` 基线；97 端点逐条登记 method/path/状态码/响应字段/是否 SSE
    - ✅ `scripts/export_contract.py` 一条命令生成两份产物；`contracts/endpoints.md` 登记 **98 条**（97 router + app 级 `GET /health` `backend/main.py:170`），每条含 handler `file:line`、鉴权、SSE/PDF/JSON 形态、openapi 响应码、请求体模型字段+必填、**前端调用点**（自动对账 `frontend/src`，53 有调用 / 48 已接线 / 5 封装未接线）
    - ↳ 2026-10-13 计数结案：V3 评审时「97 vs openapi 98」的差额 = `@app.get("/health")` 不挂 router，**非漏登记**
    - ↳ 2026-10-13 缺口：98 个端点的 200 响应在 openapi 里全是空 schema（`grep -c "response_model=" backend/routers/*.py` = 0，handler 返回 `success(dict)`）⇒ openapi 只冻结请求侧+信封，字段基线另立 `contracts/frontend-compat.md`；补 `response_model` 归 N2，禁在 N0 逐端点加注解（与 N8 按域切流重复劳动）
  - [x] 前端「必须逐字兼容」清单：`frontend/src/services/api.ts` 全部调用点 + 本地 SQLite 同步（`services/syncService.ts`、`services/coldStart.ts`）消费的字段
    - ✅ `contracts/frontend-compat.md`：出口与头（`api.ts:38-43/145-154`）、信封与错误分支（`code===200` 硬判、401 单飞刷新、`40401/40301/40302/40303/40402` 行为表、实测无 429 退避）、SSE 4 事件与双失败路径、PDF 文件名、**5 组本地同步落库映射（改名即静默丢数据）**、17 行端点字段消费点、逐字兼容最小集结论
    - ↳ 2026-10-13 前提勘误：本条原写「`syncService.ts`/`coldStart.ts` 消费的字段」，实测两者**都不发请求**——`coldStart.ts` 是纯本地门闸（全文件 0 处 fetch），`syncService.ts:8` 的 `resolveConflict()`（LWW）**全仓无 import**；真正的同步是屏级全量覆写（`HomeScreen.tsx:41-58`、`ReflectScreen.tsx:129-160`），已按实况登记，缺陷只记不顺手修（归前端本地层，非 N/O 主线）
    - ↳ 2026-10-13 零消费事实：信封 `trace_id`/`user_id` 前端只在 `api.ts:5` 注释里出现过一次；SSE `memory_context` 后端在发（`chat.py:103`）但 `ReflectScreen.tsx:286-316` 未传回调
  - [x] 架构方案增补：分层目标态、strangler 按域切流（禁 big-bang）、回滚口径；E1 改判回写；D-1…D-8 逐条拍板（建议默认值见 `closenai-prd/V3方案裁决与分层-2026-10-12.md`）
    - ✅ `closenai-prd/架构与技术方案-2026-09-06.md` 追加 **§十一（v1.1 增补，2026-10-13）**：11.1 目标态分层表（谁持有什么 / 明确不做）、11.2 契约冻结面与冻结下来的硬事实、11.3 按域 strangler（Ingress 初始 0%、切流次序=前端未接线优先、同域禁两套权威写入口、禁一次性搬数据）、11.4 回滚口径（HTTP 权重切回不发版 / alembic 仅正向 / candidate 不进 canonical 故 AI 侧回滚不破坏一致性 / purge 逐表 rowcount 不依赖级联）、11.5 未消除的硬未知
    - ✅ E1 改判回写 + §五-5 反转在本节 11.3 第 5 条就地登记（指向 N2）
    - ✅ 2026-10-13 **D-1…D-8 拍板完成**（用户「按照默认值来处理」＝§七 建议默认值整体生效，备选一律不采），逐条结论：
      - **D-2** 建薄版 `source_records`（只登记来源类型/时间/指针，不复制原始内容；`evidence.source_ref{table,id}` 指既有行，避免双写历史）
      - **D-3** L3 回链读路径改判单独立批（N9），**不并入 N8 切流**；旧 `memory_refs` 口径留回退开关
      - **D-4** 频次/quiet hours/置信门限以 PRD §5.19 + `journey_events` deliver 现行频控链为唯一事实源，V3 的 1/24h·3/7d·8/30d 与 22:00–08:00 仅作交叉核对，**不生效**
      - **D-5** 新表删除策略沿用 `user_service.py:346/353` purge 口径（逐表 rowcount、幂等、可审计），**不引入** V3 02 §27 的 12 处 `ON DELETE CASCADE`
      - **D-6** embedding/向量迁出 canonical → Python 自有 `ai_embeddings(owner_table, owner_id, model, vector halfvec(512))`，Go 不持向量；解掉「V3 00 §8 允许 Python 持派生物」与「06 §5 禁直连 canonical」的对撞
      - **D-7** RabbitMQ 承载 Go↔Python，arq/Redis 仅保留纯 Python 内部批任务（禁两套队列长期并存）；schema 以 PostgreSQL 快照为基线交接 Go 迁移工具——**工具二选一（golang-migrate / atlas）留到 N1 起手时按 Go 侧依赖实测定**，本条已拍的是「不用 alembic 继续长下去」
      - **D-8** 危机判定 + 最终措辞 policy 归 Go，按句缓冲的流式实现留 Python；跨进程后 **fail-open 改 fail-closed 或降级模板**（超时不得等于放行）
    - ↳ 拍板落地后仍需实测的硬未知不变：Apple JWS 的 Go 实现（P2）、4c8G 上双运行时的内存实测（O7）
  - 验收：端点清单条数 == grep router 装饰器计数（97），无未登记端点
    - ✅ 2026-10-13 `backend/tests/test_contract_freeze.py`（10 条全绿）：条数 == 97+1==98、登记表与 `app.openapi()` **集合相等**（既不漏也不多）、每行 handler `file:line` 反查源码确为装饰器行、openapi 基线**逐字节**一致、SSE 唯一性与 4 事件名、PDF 计数、业务码与 `ErrorCode` 逐值对齐、信封 5 键
    - ✅ 变异自证（备份写回恢复，不用 `git checkout`）：① 新增未登记路由 → 2 条失败；② 删登记表一行 → 2 条失败；恢复后 10 条全绿、全量 569 passed

- [x] **N1a · Go Core 骨架 + 第一纵切 + 跨语言互认 + 部署件**（2d）｜V3 06 §2 包边界、21 §Phase A–C｜✅ 2026-10-14 收口登记（范围＝`go-core/` 代码与测试、token 双向互认、两个 Dockerfile、`deploy/k8s/` 清单与 dry-run 闸。**集群里的真实 `kubectl apply` 不在本行状态内**，独立行 N1b 跟进）
  - [x] go.mod + 路由库（D-7 选定后登记）+ pgx v5 + sqlc + 迁移工具（golang-migrate / atlas，同属 D-7）
    - ✅ **D-7 结案（2026-10-13，全部为实测计数，非感觉值）**：本仓 Go 侧依赖足迹 `go mod tidy` 后 **3 直接依赖 + 7 间接 = `go list -m all` 24 条**（argon2id v1.0.0 / golang-jwt v5.3.1 / pgx v5.11.0 + pgpassfile、pgservicefile、puddle、x/{crypto,sys,text,sync}）
    - ↳ 路由库裁定：**N1 采 Go 1.22+ 标准库 `http.ServeMux`（0 依赖）**。取证口径＝模块数（`go list -m all | wc -l`，含 main）：stdlib mux **1** / chi **2** / gin **56**；迁移工具 atlas **24** / golang-migrate **209**。N1 的对外面只有 5 条路由 + 一条 SSE 在 N8，gin/atlas 的足迹换不来任何东西
    - ↳ **N1 不引入 Go 迁移工具，sqlc 顺延 N2**：DDL 属主仍是 `backend/alembic`（24 个迁移，head `b7d9e1f3a5c8`），Go 侧只读 `go-core/db/schema/0001_legacy_auth.sql` 快照 + 手写 SQL（3 个语句常量组合出 5 条查询：按 email/username/id 读 + 插 users + 插 refresh_tokens，`internal/store/users.go:21-34`）。原 D-7 的「alembic 不再长下去」指的是 **canonical 新表**（N2）落地时的归属，不是把既有 24 个迁移重写
    - ↳ 快照可复现：`scripts/export_pg_schema.py --docker closenai-pg-dev --check` ⇒ 与提交件 DDL 主体逐字一致（159 行）。pg_dump 16.9+ 每次插入随机 `\restrict <token>`，脚本剔除后才可复现
    - ↳ 环境勘误（2026-10-13，归 K 节）：本机 `go` 的 PATH 是混合安装（`/usr/local/go` bin=1.27.1 而 VERSION/src=1.24.1，编译报 `does not match go tool version`）⇒ 所有 Go 命令须 `export GOROOT=/Users/fengyu/sdk/go1.27.1` 或给 pytest 传 `GO_BIN`
  - [x] 第一条纵切 = `GET /healthz` + `POST /auth/register|login`，且 JWT 与 Python 侧**互认**（同 secret、同算法、同 claim 结构；argon2 哈希参数逐字一致）
    - ✅ 路由（`go-core/internal/httpapi/server.go`）：`GET /health`、`POST /api/v1/auth/register`、`POST /api/v1/auth/login`、`GET /api/v1/auth/me` + 兜底 404；`GET /healthz` 为运维探针**新增**（不在 98 端点契约内）
    - ✅ 布局裁定（V3 05 §2 写 `/internal/api/http`、06 §2 写 `/internal/api/`、两者都无 `cmd/`）：采 `cmd/go-api` + `internal/{httpapi,auth,store,config,apierr}` + `db/schema` + `testdata` + `tools/crosscheck`，共 2,738 行 Go。↳ 与 V3 目录名的差异登记于此，N2 起沿用，禁再改名
    - ✅ 契约逐字节：`go-core/testdata/http_golden.json` **22 条**由真实 FastAPI 进程内生成（`scripts/gen_go_http_goldens.py`，含信封键序、`code→HTTP` 映射、`参数错误: {loc} {msg}` 文案、`input` 回显原文、单数 "1 character"、`json_invalid` 字符偏移），Go 测试重放并要求 **响应体字节相等**；漂移闸门 `--check` 已接 pytest
    - ✅ PG 实测（隔离容器 `closenai-pg-dev`，alembic head 库）：`go test ./internal/store/` 2 条 + `cmd/go-api` 真起服务 curl 过 health/register/login/me；**偏索引语义也验了**——`ix_users_email_unique/username_unique`（`WHERE deleted_at IS NULL`）撞 23505 时映射回 40903/40904，注销账号不阻塞重注册
    - ↳ 缺口 1（**auth 域不可切流，归 N8 该批**）：Go `Login` 未接 Redis 暴力破解计数（`auth_service.py` 的 `LOGIN_MAX_FAILURES=5`/300s 锁定）。切 auth 域前必须补，否则同一账号两侧防爆破强度不一致
    - ↳ 缺口 2（归 N1 剩余 k3s 那一步）：Go `Verify` 只校签名+`exp`，Python `jwt.decode` 还校 `iat`——**未来 `iat` 会被判 ImmatureSignatureError→40102**。⇒ 两容器必须同源 NTP；测试用基线冻结在 2030-01-01 因此 pytest 侧只关 `verify_iat`
    - ↳ 缺口 3（归 N8 契约 diff）：Go 的 404 body 与 `/healthz` 是登记表新增，禁给新路径灰度权重；`json_invalid` 的 `ctx.error` 只覆盖 CPython 的 "Expecting value"/"Extra data" 两类消息
  - [x] k3s **清单与镜像**（Deployment/Service/ConfigMap/Ingress 五件 + 两个 Dockerfile + dry-run 闸）；真实 apply ⇒ 见 N1b
    - ✅ 2026-10-13 清单与镜像全部落地并过测试：`go-core/Dockerfile`（多阶段，20s 出 **20MB** 镜像）、`deploy/Dockerfile.py-api`（3m12s 出 **373MB**）、`deploy/k8s/{00-namespace,10-config,30-go-api,40-py-api,50-ingress}.yaml`
    - ✅ 验收 1：`backend/tests/test_k8s_manifests.py` **13 passed**（其中 `kubectl apply --dry-run=server -f deploy/k8s/` 打真实 API server，7 件资源全绿）。4 处变异逐条自证可失败：Ingress backend 改 go-api→违反 0%、ConfigMap 加未读变量名→跨语言漂移、Service targetPort 改名→选不到容器、探针 `/healthz`→`/health`→探针对错端点
    - ✅ 验收 2（容器级，超出原口径）：`backend/tests/test_go_py_service_interop.py` **2 passed**——两个镜像同 secret 同 PG 跑起来后：Python 注册的用户 Go 用同一 token 取回 profile、Go 注册的用户 Python 同样取回、**`GET /auth/me` 两侧字段集逐字一致**（`email/email_verified/id/name/tier/username`，golden 用例只拍 register 校验、不拍这条）、信封键序一致、篡改签名两侧同判 404/40401。变异（只给 Go 侧换 secret）→ 该条失败
    - ↳ 灰度 0% 的实现口径：清单**刻意不写** `ingressClassName` 与 controller 专有注解——目标集群 k3s 默认 Traefik、本机 OrbStack 无 IngressClass，写死注解必有一侧失效；切流＝把该 path 的 `backend.service.name` 从 `py-api` 改 `go-api`，回滚＝改回来（架构方案 §11.4）
    - ↳ 环境勘误 2（2026-10-13，归 K 节）：本机 `docker.io` 与 `gcr.io` 直连均 **Bad Gateway**，只有 `docker.m.daocloud.io` 可达 ⇒ 基础镜像做成 `ARG GO_IMAGE / ARG PYTHON_IMAGE`（默认值保持上游可移植）。运行层因此**弃 `gcr.io/distroless/static-nonroot`**、改 `alpine:3.20` + `apk add ca-certificates`（裸 alpine 无 CA bundle，生产 DATABASE_URL 走 TLS 会连不上 Supabase），并以 `USER 65532:65532` 数字 uid 满足 `runAsNonRoot`
    - ✅ **缺陷修复（2026-10-13 同日登记并修完，原拟「归 N8 的 auth 批」已作废——实测证明它不是灰度期问题，单进程就在犯）**：`backend/database.py` 的 `get_db` 把 `await session.commit()` 写在 `yield` **之后**，而 FastAPI ≥0.106 起 `yield` 依赖的后置代码由 `AsyncExitStackMiddleware` 在 `await self.app(...)` 之后关闭，即**响应已发出之后** ⇒ `POST /auth/register` 返回 200 时那一行可能尚未提交。
      - 取证（本机两容器共享同一 `JWT_SECRET` 与 `closenai-pg-dev`，带时间戳）：跨进程 register 于 `+61.0ms` 返回 200，另一进程 `GET /auth/me` 第 1 次 `+64.3ms` 判 404/40401、`+121.1ms` 才 200；**同进程自读同样复现**（`+54.7ms` 返回 200 → `+64.8ms` 判 404 → `+90.6ms` 才 200）。窗口 ≈ **响应后 3–60ms**，与读方是不是 Go 无关 ⇒ 先前登记的「实测重试 4 次」「单进程无害」两句均作废
      - ↳ 定性：**不该改成 Go**。Go 读到未提交行判 404 是正确行为；`get_db` 是全 98 端点共用的请求级边界，切一个域过去不消除窗口
      - ✅ 修法（走 FastAPI 官方扩展点，不逐 handler 加 commit）：新增 `backend/core/db_route.py::CommitBeforeResponseRoute`，在 handler 产出 Response **之后、该 Response 发送之前**提交；`get_db` 把 session 挂到 `request.state.db_session` 交出去，自身收尾提交改为 `in_transaction()` 条件提交（保留给 SSE——chat 在响应体流式期间才写库，那次是唯一提交点），去重靠同一判断，读请求不增加 BEGIN/COMMIT 往返；17 个 `APIRouter()` 全部声明 `route_class`
      - ✅ 验收 `backend/tests/test_db_commit_boundary.py`（**4 passed**）：① 闸门——每个 router 必须声明 `route_class`（漏挂不报错、只会静默退回旧时序，所以必须拍）；② 闸门——`get_db` 与路由层的交接面不许漂移；③④ 性质——手工驱动一次裸 ASGI 调用，在 `send()` 收到 `http.response.start` 的那一刻用**另一条连接**按 PG MVCC 读那一行，读到才算「提交在首字节之前」。不用 TestClient/httpx：它们把含 teardown 的整个 ASGI 调用跑完才返回，区分不了提交在前还是在后。③ 打最小 app，④ 打 `backend.main.app`（17 次 `include_router` + CORS + `TraceIdMiddleware`，后者是 BaseHTTPMiddleware 会拷 scope，正是 `request.state` 交接最容易断的地方）
      - ✅ 变异自证 2 处（备份写回恢复，`diff -q` 逐字核验）：摘掉 `auth.py` 的 route_class → 闸门 + 边界条同时红（`visible=False`）；保留声明但撤掉包装里的 commit → 边界条红。⇒ 测试拍的确实是提交时序，不是在等 teardown 跑完
      - ✅ 全量终态：`pytest backend/tests` **597 passed / 1 failed / 1 skipped**（Go 侧 `go test ./...` 同步全绿，PG 清场 `users=0 / refresh_tokens=0`）——唯一那条 `test_commitment.py::test_weekly_quota_defers_third_follow_up` 是本修之前就存在的失败（tasks.md:509 已记根因；该用例 0 处 HTTP、不经 `get_db`，与本修无关）。修复过程中 `test_contract_freeze.py` 曾因 17 个 router 各插入 1 行 import 使 handler 行号整体 +1 而红 ⇒ 按口径重跑 `scripts/export_contract.py` 重生登记表（98 条不变，仅 `file:line` 位移）后转绿，未手改表
      - ✅ 部署件同步：py-api 镜像重建后再跑 `test_go_py_service_interop.py`（**2 passed**），其中新增断言「跨进程写后立刻读 `attempts == 1`」——正是本条取证时量到 404 的那条路径，现在一次命中
      - ↳ 环境勘误 3（2026-10-13，归 K 节）：该容器级闸的库口令改为 **env-only**（`PG_PASSWORD` 无默认值，未设置时两条 skip 而非 fail）——口令不该跟着测试文件进公开远端。实测：不带口令 `2 skipped`、带口令 `2 passed`
      - ↳ 远端口径（2026-10-13）：`origin` 由内嵌明文 token 的 HTTPS URL 改为 SSH（`git@github.com:jamesfeng2009/closenai-app.git`）。取证：两枚 token（GitHub 54 字符 / Gitee 39 字符）经 `git log --all -S` 与全树 grep 核验**从未进过任何提交、跟踪文件或工作树未跟踪文件**，只存在于 `.git/config`，因此无需改写历史；`gitee` 远端仍是 HTTPS+token，是否一并改 SSH 未获点名、不动
    - ↳ 本行不含的动作：`kubectl apply` 打到共享 k3s + 镜像推送凭证 ⇒ 独立任务 **N1b**（需显式授权，与 K5 同批）。本机 namespace `closenai` 是 dry-run 用，用完已回收
  - 验收：Python 签发的 access token 被 Go 验证通过的跨语言用例进 CI；`sqlc generate` 成功
    - ✅ 2026-10-13 前半条达成，且**双向**：Python→Go 用 `go-core/testdata/python_auth_baseline.json`（`scripts/gen_go_auth_fixtures.py` 调真实 `create_access_token/create_refresh_token/hash_password` 生成）断言 access/refresh/expired/wrong-secret/tampered 五类 + argon2 三哈希（含中文密码）；Go→Python 由 `backend/tests/test_go_auth_interop.py`（**10 passed**）跑 `tools/crosscheck` 验 `decode_token` claims 逐字同构、refresh 当访问凭证用两侧都判 40102、`check_needs_rehash is False`
    - ✅ 同库同行互认（灰度期真实风险面）：Python 写的行 Go 能登录、Go 写的行 Python 能验密——`_pwd_hasher.check_needs_rehash()` 为 False 证明参数不漂移、登录不会无声重写哈希
    - ↳ 后半条 **改判**：`sqlc generate` 不属 N1（D-7 结案：N1 手写 5 条 SQL，alembic 保 DDL），随 N2 canonical 新表一起做，验收口径以本条登记为准
    - ↳ 测试账（2026-10-13 实测）：`go test ./...` **25 个 Test 函数 + 22 golden 子用例**全绿（`internal/auth`、`internal/httpapi`、`internal/store`）；`test_go_auth_interop.py` 10 passed，其中 8 条缺 Go 工具链则 skip、2 条缺 `TEST_DATABASE_URL` 则 skip，**宿主无 Go 也不会红**


- [ ] **N1b · 共享 k3s 真实 apply（灰度仍 0%）**（0.5d）｜N1a 只交付到清单与 dry-run｜⚠️ 两道外部前置：用户显式授权 `kubectl apply`（本机无 kubeconfig，集群是共享的、跑着别的项目）+ 镜像推送凭据（与 K5 同批）
  - 验收：`kubectl -n <ns> get deploy,svc,ing` 出 `go-api`/`py-api` 两件；Ingress 该 path 的 `backend.service.name` 仍为 `py-api`（切流前 0%）；经 Ingress 一次真实 `GET /healthz` 返回 200；回滚＝`kubectl delete -f deploy/k8s/` 且不触碰他项目命名空间
- [x] **N2 · Canonical Schema（个人模型权威表）**（4d）｜V3 01 §27 V1 Scope、02 §5–§26 ｜✅ 2026-10-13 完成（21 表 + sqlc 55 查询 + 23 条闸 + 5 个 Go 集成测试 + 10 条变异自证，全过测试）｜缺口实测：fact/goal/commitment/pattern/insight/outcome/feedback/model_version **全部无表**，pattern/insight 只是 `models/growth.py:19–20` 的 `record_type` 枚举值、分级塞 `metadata_json.insight_level`(:27)
  - ✅ **2026-10-13 交付并过测试**：`backend/alembic/versions/c8a2e4b6d0f2_add_canonical_runtime_tables.py`（512 行）建 **21 张** canonical 表 + 53 条普通索引 + 2 条唯一索引；alembic head `b7d9e1f3a5c8` → `c8a2e4b6d0f2`，单头线性。`ai_generation_runs` 沿用 O4 已交付 DDL，不重复建。
  - [x] 新表：`personal_facts`、`personal_goals`、`personal_commitments`、`evidence`、`patterns`、`insights`、`proactives`、`outcomes`、`user_feedback`、`personal_model_versions`、`ai_generation_runs`、`inbox_messages`（`source_records` 取舍见 D-2）
    - ✅ 实际范围＝点名 12 张 + V3 02 §5–§26 的同批依赖表，共 21 张：另加 `personal_models`（02 §5，`personal_model_versions/patterns/insights` 都要挂它，缺表则 §37「当前在用的是哪个模型」无从回答）、`personal_model_events`（§37「改了什么」）、`source_records`（D-2 薄版）、`fact_evidence`/`pattern_evidence`/`insight_evidence`/`insight_patterns`/`proactive_insights`/`proactive_patterns`/`outcome_links`（回链与 §37「为何相信」的载体）
    - ↳ D-2 落法：`source_records` 只做指针（`source_table`+`source_id`+时间+`UNIQUE (user_id, source_table, source_id)`），**不复制原始内容**；`evidence.source_ref` 仍指既有行，避免双写历史
    - ↳ **D-5 的连带义务（原任务未写，实测才发现躲不掉）**：不引入 CASCADE ⇒ 每张 canonical 表都必须带 `user_id` 并进 `PURGE_TARGETS`，否则 7 张关联表与 `personal_model_versions` 永远删不净。已在 `backend/services/user_service.py` 登记 **20 条**（`inbox_messages` 是投递元数据、无个人内容，故不带 user_id 也不入清单）
    - ↳ V3 文档自身缺列、按下游约束补齐 6 处（逐处出处写在迁移 docstring）：`patterns.scope`←01 §5.2、`insights.claim_type`←03 §14、`outcomes.outcome_type`←04 §15、`evidence.observation`←03 §4、`generation_run_id`/`reject_reason`/`model_version`←不变量 8 + N3/N6 需要、`patterns.fingerprint`←08 §3 + 18 §11
    - ↳ 与 V3 原文的 4 处分歧按本项目裁定执行，不照抄：① 不用 enum（裁决 §五-4，状态列全 VARCHAR + Go 侧合法集合校验）② 不引入 `ON DELETE CASCADE`（D-5）③ PK 用 `VARCHAR(36)` + 应用侧 uuid4，不用 `UUID + gen_random_uuid()`（全库既有 PK 皆如此，pgcrypto 未装）④ JSON 用 TEXT，不用 JSONB
  - [x] **建表即改判（冲突已结案，2026-10-12 拍板反转）**：《架构与技术方案-2026-09-06》§五-5「承诺=memories 增列，不建新表」在 Go 权威侧反转（canonical 模型独立重建），登记 ↳ 2026-10-12 改判
    - ✅ 已建 `personal_commitments`（02 §16），旧 `memories` 表结构一字未动
  - [x] `memories`/`chat_messages`/`growth_records` 保留为 source 层与展示层；新旧关系用「新表增量 + 旧表不动」，禁止一次性搬数据
    - ✅ 迁移正文只有 `CREATE TABLE`/`CREATE INDEX`，**0 条 INSERT/UPDATE/ALTER 旧表**；`source_records` 是指针表，数据搬运归 N4/N9
  - [x] 不用 Postgres enum 类型（V3 02 §3）：沿用 `models/journey.py:67–73` 既有约定，改 String + Go 侧合法集合校验
    - ✅ 库级事实已拍：`pg_type typtype='e'` 0 行、`information_schema.columns data_type='jsonb'` 0 行、`contype='f'` 的 FK 只允许 `refresh_tokens` 那一条既有
  - [x] sqlc 交接（D-7 顺延项）：`go-core/sqlc.yaml`（schema=0001 快照 + 0002 快照，pgx/v5）+ `go-core/db/queries/canonical.sql` **55 条查询** → `go-core/internal/store/canonicaldb/`（`canonical.sql.go` 2,095 行 + `models.go` 320 行）；`go-core/db/schema/0002_canonical_runtime.sql`（986 行）由 `scripts/export_pg_schema.py` 从升级后的 PG 导出，`--check` 可复现
  - 验收：sqlc query 覆盖 V3 02 §37「Minimum V1 Queries」八问（我们知道什么／为何相信／置信多少／上次何时确认／改了什么／当前在用／用户纠正了什么／干预后发生了什么）
    - ↳ 计数勘误（2026-10-13）：§37 实为 **9 问**（原文小标题 What do we know / Why do we know / What patterns exist / Why does a pattern exist / What did the AI tell the user / Why / What did the user correct / What changed / What happened after intervention），本条原写「八问」少计 1；「干预后发生了什么」在原文属 Outcomes 一节，故按 9 问验收
    - ✅ 2026-10-13 `backend/tests/test_canonical_schema.py` **23 passed**（12 条静态/契约闸 + 11 条 §37 逐问参数化），三条实库闸靠本机容器 `closenai-pg-dev`：
      - `TEST_DATABASE_URL=postgresql://…/closenai`（head 库；scheme 给裸串即可，Python 侧由 `backend/tests/_pg_env.py` 补 psycopg3 驱动——2026-10-14 归一口径，见 N4 节末 ✅ 行）：21 表逐列对账 ORM 声明 + 唯一键/普通索引按 (唯一性, 列元组) 对账 + 迁移声明的每条索引名字在库里都在 + 库级 enum/jsonb/FK/CASCADE 口径
      - `N2_MIGRATE_DATABASE_URL=…/n2_roundtrip`（一次性空库）：`upgrade head` → 断言 head 与 21 表 → `downgrade -1` → 断言回到 `b7d9e1f3a5c8` 且 21 表清空 → 再 `upgrade head`
      - `sqlc diff` 退出码 0（生成物与 `.sql` 无漂移；宿主缺 sqlc 则 skip，不红）
    - ✅ Go 侧 `go-core/internal/store/canonical_integration_test.go`（690 行，**5 个 Test**）打真 PG：§37 逐问、02 §32 乐观并发（陈旧 bump 必 `ErrNoRows`、同 `(personal_model_id, version)` 必拒）、读路径按 user 隔离（越权 4 条 `Get*` 全 `ErrNoRows`）、幂等（同一 source 指针复用同一 id、同指纹第二条 pattern 被拒、inbox 按 `(message_id, consumer)` 去重）、投递状态机（`MarkProactiveDelivered` 二次 0 行、sent 后不许 expire）
    - ✅ 变异自证 **12 条**（`scripts/mutate_n2_gates.py`，备份写回 + `diff -q` 逐字核验，不用 `git checkout`）**全部打红**（N2 批首落 10 条；2026-10-14 锚点结案时按现断言扩至 12，见下一条）：加 FK+CASCADE／改挂中间 revision／摘 `personal_models.user_id`／摘 `personal_goals` 的 user_id 前导索引／摘一用户一模型唯一索引／把 `personal_facts` 移出 `PURGE_TARGETS`／新增越权读 canonical 的模块／重命名 §37 查询／把实库列改可空／实库建 FK+CASCADE 表
    - ✅ **锚点漂移已结案（2026-10-14 本批修，跟踪器 #56 同日置完成）**：本行原登记的是 **10 条里 8 条打红、2 条「not found」**——`test_migration_graph_is_single_head_with_n2_on_top` 已被 N7 批重命名成 `..._with_n7_on_top`（`backend/tests/test_canonical_schema.py:178`），`test_personal_model_version_and_inbox_unique_keys_are_declared` 的断言已拆进 `test_idempotency_unique_keys_are_declared`(:255) 与 `personal_model_versions` 的 UNIQUE 断言(:260)。⇒ 上一条「全部打红」是 **N2 批当时的实测**，那两条闸此前空转（改坏迁移头/唯一键不会红）。修法与结果：① nodeid 换成现名，且**按现断言逐半边各破坏一次**——unique-keys 那条现在有两个独立断言（`patterns` 指纹唯一 + `inbox_messages` 的 `UNIQUE (message_id, consumer)`），只测第一条就等于第二条没人守，因此条数 10→**12**；② 迁移头那条的锚点改指 N7 的件（`d9b1f3a5c7e9` 的 `down_revision`），因为现断言认的是「单头且 n2 在上、n7 在最上」；③ `pg_dsn()` 与本批新方言闸对齐，给**裸 `postgresql://`**（脚本注释写明 Python 侧补驱动、Go 侧剥驱动各在何处）。复跑实测：**12/12 打红、0 异常，4 个被改文件 `diff -q` 逐字节复原**
    - ✅ 全量回归：`pytest backend/tests` **617 passed / 1 failed / 4 skipped**（唯一那条 `test_commitment.py::test_weekly_quota_defers_third_follow_up` 是 2026-10-13 已登记的既有缺陷，与本批无关，见 §P 与 tasks.md:509）；`go test ./...` 全绿
    - ✅ **真缺陷 1 处（本批测试抓到，已修并过测试）**：`ix_patterns_user_fingerprint_unique` 在迁移里被 `CREATE INDEX` 建成了**普通索引**，而 `models/canonical.py` 声明的是 unique ⇒ 同一指纹的第二条 pattern 直接写进库，N3 的幂等前提失效。由 Go 侧 `TestCanonicalIdempotency` 首先报红。修法：唯一索引单列 `_UNIQUE_INDEXES` 组、用 `CREATE UNIQUE INDEX`，并补「(唯一性, 列元组) 逐表对账」闸把它变成跑一次就红（`IF NOT EXISTS` 不会重建已存在的索引，两个库都做过 `downgrade -1 && upgrade head` 后才一致，快照与生成物随之重导）
    - ↳ 闸门自身的首跑勘误（2026-10-13，三条都是「测试写错」而非「实现写错」，登记以免被误读成实现返工）：① 手搓正则解析 `revision` 只认 `revision: str = '…'`，漏了仓里另外 5 个用 `revision = "…"` 的迁移，误报 9 个 head ⇒ 改用 alembic 自己的 `ScriptDirectory.get_heads()` 离线解析 ② 扫描 DDL 禁词时把 SQL 注释算进去了，注释里提到被否掉的 `JSONB` 就自我打脸 ⇒ 先剔 `--` 行 ③ `confdeltype <> 'a'` 会把主键/CHECK 全判成 ON DELETE（catalog 的 `"char"` 对非 FK 填空格而非 NULL）⇒ 必须限定 `contype='f'`
    - ↳ 索引口径决定：21 张表**一律不加外键**。取证：全库仅 1 条 FK（`refresh_tokens_user_id_fkey`），加 FK 会让 purge 的表顺序变成隐性硬依赖，而现有闸只校验覆盖不校验顺序；引用完整性归 N3 Domain Gate（V3 02 §27 也把源失效传播算作领域工作流而非库级约束）。`user_id` 不单独建索引——每张表都有以 user_id 打头的复合索引，闸 `test_every_canonical_table_has_a_user_id_leading_index` 拍的就是这条，重复建单列索引只是写放大


- [x] **N3 · Domain Gate + Candidate 语义**（3d）｜V3 01 §11 AI Write Boundary、§25、§22 不变量 3/7 ｜缺口实测：`growth_service.py:410`、`mood_service.py:183/224`、`personality_service.py:265` 三处 AI 产出直写权威态；全仓状态字段（`journey_events.status`/`memories.status`/`receipts.status`/`safety_events.review_status`）全是投递或生命周期态，**无一处 draft/candidate/confirmed**
  - ✅ **2026-10-13 交付并过测试**：`go-core/internal/domain/{candidate.go 92, policy.go 127, gate.go 383, service.go 453} + idgen/idgen.go 21` 行；持久化面 `internal/store/canonical_repo.go` 353 行（`domain.Store`/`Transactor` 的唯一实现，一个 `Admit` 钉在一个事务里）；`db/queries/canonical.sql` +3 条（55→**58**），`canonicaldb/canonical.sql.go` 重导至 2,184 行
  - [x] `candidate → active / rejected`；reject 必带机器可读 reason 码 + `generation_run_id`
    - ✅ G-1 词表 **11 码**全部落 `policy.go`，Gate 产出的每个码由 `TestGateProducesOnlyRegisteredReasons` 反查（不在表内 / 超 `VARCHAR(64)` / 重复 ⇒ 红；且 9 个可构造码必须被 `arch_test.go` 的变异用例真实触达，防「加码没人产」也防「码在表里但闸失效」）
    - ↳ 落库口径按 kind 分岔（**V3 原文的缺口，本批裁定**）：02 §3 的 `insight_status` 九态里**没有 `rejected`**，故被拒的 insight 停 `draft` + `reject_reason` + `gate_detail`，被拒的 fact/pattern 用各自词表里存在的 `rejected`。出处：`closenai-prd/V3方案裁决与分层-2026-10-12.md` 七-2 勘误（2026-10-13）
    - ↳ 原任务那句「三类样本必须 reject 且**不产生权威行**」按 G-2 改为「不产生**可用**权威行」：拒绝一律留痕（可审计 + N4 的拒绝签名抑制要负样本），`status` 与 `reject_reason` 就是「不可用」的机器表达
  - [x] Gate 校验序：schema → 证据存在性与归属 → 置信 → claim type → 时间窗 → 措辞/安全 → 重复
    - ✅ 七关按 G-3 顺序短路，`Outcome.Checks` 只装**已通过**的关（顺序契约由 `TestGate_OrderIsContract` 钉：低置信+因果+危机+重复样本必须落 `confidence_below_threshold`，Checks=`schema,evidence`）；Checks+reason+detail 序列化成 `gate_detail`，是 N4 九关 golden 的复现依据
    - ↳ V3 03 §14 的九关在此落七关：evidence 算分与 user history（拒绝签名抑制）归 N4，不半做
    - ↳ 三条判定口径（V3 未给数、按可确定性执行的最小集合拍）：D 级下限只拦 insight（03:158-159）；因果动词只在候选引用 ≥2 条证据时判（规范 §1.2 的原意是「连接两条 Pattern」，做成通用黑名单会误杀单条观察）；绝对量词在有界窗口下一律拦（11 个中英词表逐条有测试）
    - ✅ 跨用户证据与不存在证据**分码**（`evidence_not_owned` vs `evidence_not_found`）：`ListEvidenceForGate` 故意不按 user_id 过滤才区分得开，归属判断在领域层做
  - [x] canonical 唯一写入口 = Domain Service；HTTP handler 与 MQ consumer 都只能经它（不变量 7）
    - ✅ G-7 包级依赖闸 `internal/domain/arch_test.go` 213 行：`go/parser` 扫 import（不 exec `go list`——本机 Go 工具链混合安装，见 §环境勘误），断言 ① domain 除 `internal/idgen` 外不 import 本项目任何包，且不 import `net/http`/grpc/chi/gin/rabbitmq/redis/pgx/database/sql 这批传输与持久化面 ② 全模块只有 `internal/store`（含生成物自身）能 import `canonicaldb`；两条都带「扫到 0 条 import 即红」的失效自检
  - [x] 人工确认链保留：`reflection_service.py:89` draft→`save()`:106 归 `user_confirmed` 证据（V3 01 §8）
    - ✅ 语义落 `factStatus()`：`origin ∈ {direct, user_confirmed}` 的 fact 直接 `active`（03:169-173 的显式路径），其余 `candidate`；Python 侧那两处写入本批**未动**（O3 才收口，N3 只把 Go 侧入口建起来）
  - 验收：「无证据洞察」「跨用户 id」「低置信因果句」三类样本必须 reject 且不产生权威行
    - ✅ `go test ./internal/domain` **20 个顶层 Test / 93 个子用例**全绿（gate 表驱动覆盖七关 + 顺序 + Policy env 覆盖）；`internal/store/domain_integration_test.go` 576 行 **9 个 Test 打真 PG**（同包合计 16 顶层 PASS）：fact 接纳的列级对账（`DECIMAL(5,4)` 写 0.8000/0.9999 不丢精度）、三类拒绝各留一行且模型版本不动、insight 的 `gate_detail` 含七关名 + `insight_evidence`/`insight_patterns` 回链、指纹命中走 reactivate（`patterns` 仍 1 行、`evidence_count` 重算成 3、不产生重复回链）、`invalidated` 之后同 key 重新放行、跨用户证据 victim 侧零污染、版本冲突整事务回滚（三张表 0 行）、缺 `personal_models` 行时自建且一人一行
    - ✅ 变异自证 **13/13 打红**（`scripts/mutate_n3_gates.py`，备份→写坏→跑那一条→备份写回 + `filecmp.shallow=False` 核验，不用 `git checkout`）：证据归属关掉／置信与安全换序／时间关掉／D 级下限归零／拒绝不落行／事件改名／insight 拒绝态改 `rejected`（单测与真库 DDL CHECK 双红）／reactivate 掉回链／`ErrVersionConflict` 不映射／httpapi 偷引 `canonicaldb`／domain 引 `net/http`／查重 SQL 把 `invalidated` 算活行
    - ✅ **闸门自身 2 处缺陷（本批变异抓到，已修，登记以免被误读成实现返工）**：① `TestDomainDoesNotDependOnTransportLayers` 的谓词只拦「本项目 `internal/` 前缀」的 import，`net/http` 能大摇大摆过去——第 12 条变异首跑是 GREEN-FAIL，补 import 黑名单后按名字打红，测试名从此名副其实 ② `sqlc` 有按 mtime 的解析缓存，`restore_files()` 用 `shutil.copy2` 会把 mtime 拨回旧值，紧接着的 `sqlc generate` 会拿旧解析静默 no-op，第 13 条变异因此假绿过一次；修法是改 SQL 前后 `os.utime` touch 并断言生成物真的含变异——**闸门没破坏成功就不能算打红**，这条口径对后面 N4-N9 的变异脚本同样成立
    - ↳ ② 的适用面 2026-10-14 复核并结案：**只对真的重跑 `sqlc generate` 的脚本成立**。`grep -c utime` 实测 n3/n4/n5/n6/n7/n9 各 1、**n2 为 0**，而 n2 的 `.sql` 变异只被纯读文本的 `test_sqlc_queries_cover_minimum_v1` 消费、n2 脚本根本不跑 codegen ⇒ n2 不存在这条假绿，无需补 `os.utime`。同日把这条口径升级成「**受害测试没跑起来也不能算绿**」：`scripts/mutate_pg_scheme_gates.py` 的 `outcome()` 把 `[build failed]`／`imported and not used`／`collected 0 items`／整轮 skip 单列一档，判据来自首跑的三条异常——G4 撤掉剥段调用后 `dsn` 成了未使用 import，编译失败让互认 fixture 把整组判 skip、退出码 0（旧 `verdict()` 只会说「破坏后仍然绿，等于没拍住」而说不出原因）；P9 则是 `pytest.skip` 冒泡把用例本身记成 skip（实测 `issubclass(Skipped, Failed)` 为 **False**）；GR1 红的原因是漂移闸扫到了变异脚本自己的被禁字面量，于是补了第三类豁免（变异锚点，带理由 + 死条目核验）。三条都按根因修完复跑：**19 条红闸全打红 / 2 条绿闸保持绿 / 0 异常**
    - ✅ 全量回归：`pytest backend/tests` **609 passed / 1 failed / 12 skipped**（唯一失败 `test_commitment.py::test_weekly_quota_defers_third_follow_up` 是 2026-10-13 已登记的既有缺陷，见 tasks.md:509，本批按「发现的缺陷不顺手修」不动）；`go test ./...` 带/不带 `TEST_DATABASE_URL` 两种口径都全绿（缺 env 时集成测自动跳过）；`go vet ./internal/...` 无输出；`gofmt -l` 仅剩 N2 既有文件 `canonical_integration_test.go`（刻意不动）

- [x] **N4 · Evidence 确定性算分 + Insight 九关**（4d）｜V3 03 §4–§7、§13–§15 ｜缺口实测：A/B/C/D 分级**只活在 prompt 里**（`safety_service.evidence_policy()`，注入 `message_generator.py:148`、`mood_service.py:178/218`），代码不算分
  - ✅ **2026-10-13 交付并过测试**：算分面 `internal/domain/evidence_score.go` 399 行（五因子 + A/B/C/D + `Combined` 聚合与冲突）；写入口面 `internal/domain/service_evidence.go` 434 行（`AdmitEvidence` 算分落库 + `ReevaluateInsight` 重算撤回）；`gate.go` 383→503 行（补第 7 关 language、第 9 关 user history，校验序改回 03 §14 原序）、`policy.go` 127→260 行；持久化面 `internal/store/canonical_repo.go` 353→466 行；`db/queries/canonical.sql` 58→**59** 条（`ListEvidenceForInsight` 补 `e.role AS evidence_role` 与 `observed_at`），`canonicaldb` 重导
  - [x] 算分 = source_reliability × explicitness × specificity × repetition × temporal_relevance → A(≥0.85)/B/C/D
    - ✅ strength **只可能由 Go 算**：`EvidenceCandidate` 的字段面里没有 strength（03:108「LLM confidence is one input, never the final score」），调用方给的置信度原样进 `metadata_json.caller`，落库分是 `ScoreEvidence` 的产物；`TestService_AdmitEvidence_StrengthIsComputedHere` 与真库 `TestAdmitEvidenceScoresInDBAndKeepsProvenance` 双向钉（库内 `DECIMAL(5,4)` 值＝结论值，且不等于提案的 0.99）
    - ✅ `metadata_json` 同步行留五因子取值、mode、独立观测计数与丢弃计数、A/B/C/D 档 + 调用方原文（02 §37「Why do we know it」要能指回分数怎么来的）
    - ✅ **与 V3 字面口径的必要偏离（已拍板结案：默认几何平均）**：五因子连乘在小数域量纲塌缩——03 §5 表里最正常的一档（ai_extraction 0.85 × explicit 1.00 × specific 1.00 × 单次 0.60 × 30 天内 1.00）连乘只有 **0.51 ⇒ C 级**，而 §6 说 C 只能内部探索、D 不得面向用户，即 V3 的默认值会把绝大多数正常证据判成不可展示。默认 `ScoreMode=geomean`（连乘开 5 次方，单调性与连乘一致、保持 [0,1]），`CLOSEN_EVIDENCE_SCORE_MODE=product` 可一键切回字面口径回测；偏离已登记 `closenai-prd/V3方案裁决与分层-2026-10-12.md`。⇒ 本行的决策面已闭合；**未闭合的是另一条任务**（真实分布的现网标定，跟踪器 #59「N4 门限的现网分布标定」pending），不在本行范围内
    - ✅ **标定回测随批交付**（G-5，本机无真实用户数据 ⇒ 合成分布而非 prod 分布）：`go-core/tools/calibrate/main.go` 枚举 8 来源 × 4 明确度 × 3 具体度 × {1,2,3,5} 条独立 × {0,15,45,100,200,400} 天＝**每口径 2304 组合**，数值全部取 `domain.DefaultPolicy()`（即 env 覆盖后的实际生效值，脚本里没有第二套权重表）；`scripts/calibrate_evidence_score.py` 出直方图：**geomean A 9.8%/B 39.9%/C 40.1%/D 10.2%（C+D 50.3%）vs product A 0.9%/B 2.1%/C 6.4%/D 90.7%（C+D 97.0%）**，基准档 0.8740(A) vs 0.5100(C)
    - ↳ 具体度三档、Repetition 基数/步长、衰减档、`InsightStaleAfterDays=90`、`InsightMinIndependentForDerived=2` 都是 **V3 未给数**的自定默认值：一律 Policy 注入 + env 覆盖，env 名逐个写在 `policy.go`，标定脚本换 env 重跑即换一版权重回测。真实分布的 prod 回测归 **跟踪器 #59**（独立 pending 行，本节末 ① 同指）
  - [x] evidence diversity：同一 source/同日不得计独立重复（V3 03 §6 末段）
    - ✅ 核验式去重：`verifyIndependent`（只读，逐条查存在与归属，越权/幻觉**硬报错**）→ `UpsertSourceRecord` → `countIndependent`（更早、不同 source_record、不同自然日才 `+1`；被丢弃的只计数不拒整条），提案方给的「独立条数」一个都不采信
    - ✅ **本批发现的真实缺陷 1（已修并过测试，2026-10-13）**：`Combined` 首版用 `source|day` **组合键**去重，把「同一天、不同来源」计成两次独立观测 ⇒ 违反 03 §6:161-163 的 **or** 关系，推断事实会靠同一天的两条记录假晋升 `active`。改成 `seenSource` / `seenDay` 两个集合各自判重，由 `mutate_n4_gates.py` 第 1、2 条变异（改回组合键）打红自证：真库 `TestAdmitFactStaysCandidateWhenEvidenceSharesADay` + 单测 `TestEvidencePolicy_Combined`
    - ✅ **本批发现的真实缺陷 2（已修并过测试，2026-10-13）**：`AdmitEvidence` 首版「先登记 source 指针、后核验引用归属」，越权提案在事务里留下半状态登记（回滚掩盖了次序本身的问题，也违反不变量 9 的方向）。改为核验在前、写在后，`mutate_n4_gates.py` 第 5 条（把核验挪到写入之后）打红 `TestService_AdmitEvidence_IndependenceIsVerifiedNotProposed/越权引用别人的证据直接报错`
  - [x] 九关顺序固定：schema → evidence → temporal → claim type → confidence → safety → language → duplicate → user history
    - ✅ N3 落的七关是这条序列的**前缀**，但 G-3 的定序把 temporal 放在 confidence 之后 ⇒ 本批把 `Gate.Check` 改回 03 §14:361-371 原序，`TestGate_OrderIsContract` 的 6 个子例连同 `allChecks` 一起重写（Checks 是 N4 golden 的复现依据，序一变必须跟着变）
    - ✅ 新增第 7 关 language（`LabelTerms` 标签词/诊断句式 + 敏感域必须留限定语）与第 9 关 user history。分工写明不建第二套规则：claim type 判**提案自称的类型**与共现写成因果的措辞，language 判**没有类型标签可依据的语言面越界**（自称 observation 却出现 03 §8 的标签词）
    - ✅ G-1 词表 11→**14 码**：`language_overreach`、`suppressed_by_user_history`、`evidence_conflict`。第三个不复用 `evidence_below_floor`——「支撑不够」和「有反面证据」是两回事，合并会让 O 侧把冲突样本统计成误判；`arch_test.go` 的闸只断言「Gate 产出的码 ⊆ 表」，加码不破坏既有契约，而新三码都由 `TestGateProducesOnlyRegisteredReasons` 的构造用例真实触达
    - ↳ 第 9 关只对 pattern 生效且**只认** `user_feedback(target_type='pattern', feedback_type='reject')`：Gate 自己写的 `rejected` 行是「领域层没接纳」，不是用户否决，拿它当抑制签名会让一次误判永久封掉一个指纹（SQL 见 `ListEvidenceForGate` 旁的 `RecentUserRejection`）
    - ↳ 抑制路径**不插行**：库里已有同指纹 pattern，再插一条直接撞 `ix_patterns_user_fingerprint_unique`（23505），而这一关要的恰恰是「别再生成」——G-2 的「拒绝也留痕」在这里落成 `candidate_suppressed` 事件 + `status=suppressed`，不是第二条权威行
  - [x] V1 允许 claim / 默认 restricted：沿用 N3 的 `AllowedClaimTypes`/`RestrictedClaimTypes`，本批未放宽（03 §14）
    - ✅ 派生 claim（claim_type ≠ observation）除门限外还加**证据条数**口径：需 ≥2 条**已算分且互相独立**的证据（`InsightMinIndependentForDerived`）；未算分的行不参与 D 级判定（算分是本批职责，拿别人的猜测当输入会让 §6 的档级失效）
    - ↳ `observation` 是对所引证据的复述，一条 A 级支撑即可——`TestService_ReevaluateInsight_ObservationSurvivesWeakIndependentCount` 与 `TestGate_ClaimType/单条观察里的「是因为」不误杀` 一起把这条边界钉住（前者是 N4 的，后者是 N3 的，本批把样本改成 observation 才语义正确）
  - [x] 时间窗措辞：N3 已落 `absoluteQuantifiers` 词表关，本批只补时序口径——独立重复要求被引用的观测**更早**，晚于本条的是后续佐证不算第二次重复（`countIndependent`，用例「晚于本条的引用不计入」）
  - [x] 撤回/过期：同一 claim 证据变弱 → 旧 insight retract/expire
    - ✅ `ReevaluateInsight` 判定序：冲突 ⇒ `retracted`+`evidence_conflict`；跌到 D ⇒ `retracted`+`evidence_below_floor`；派生 claim 跌到 C ⇒ 同上；都不成立但窗口过 `InsightStaleAfterDays` ⇒ `expired`；否则状态与模型版本都不动（02 §32「无变化不 ++」由 `TestService_ReevaluateInsight_KeepsStrongSupport` 钉）
    - ✅ **用户已表态的行系统不得撤回**：只有 `validated|published|acknowledged` 参与重算，`confirmed/corrected/dismissed/retracted/expired/draft` 原样返回（03 §12 的用户动作结果不能被覆盖）
    - ✅ `expired` 的 `reject_reason` **留 NULL**：拒绝码的语义是「Gate 拒了它」，过期是 §11 的生命周期退化，那条 insight 当初是合法发布的；写码会让 O 侧把过期算成误判
    - ↳ 「+ 新 candidate 生成」不在 Go 做：本批只发 `insight_retracted`/`insight_expired` 事件，重新提案是 Python 的职责（不变量 7 的方向：Go 判终局，不产提案）
    - ✅ 重算读 **`evidence.role`** 而不是 `insight_evidence.role`——Gate 写回链时恒填 supporting，冲突是证据属性不是某次引用的属性；真库用例专门把回链写成 supporting、证据行写成 contradicting 来验这条（`mutate_n4_gates.py` 第 10 条改回 `ie.role` 打红）
  - [x] fact 晋升口径补全（03 §7:175-181）：`origin ∉ {direct, user_confirmed}` 的推断事实需 ≥2 条已算分独立观测 + `combined ≥ 0.75` + 无冲突才 `active`，否则停 `candidate`；有冲突**不拒绝候选**（拒绝是 Gate 的事），只是不给晋升资格
    - ↳ N3 的 `TestAdmitCreatesPersonalModelWhenMissing` 期望随之改变：两条分日的 0.9 证据现在**够格**晋升 `active`，该用例改为断言 active，另建 `TestAdmitFactStaysCandidateWhenEvidenceSharesADay` 守住 candidate 分支
  - 验收：V3 03 §19 golden tests 本地化为 Go 表驱动用例；因果断言样本 100% 被拦或降格
    - ✅ `go test ./internal/domain` **42 个顶层 Test / 175 个子用例**全绿（`evidence_score_test.go` 219 行表驱动含 geomean 期望值与 product 对照、五种未知/非法输入必报错、`ClassOf` 端点、`Combined` 同源/同日/冲突/未算分四组；`service_evidence_test.go` 610 行含 12 个畸形提案全部不写库、role 默认、framework_inference 必落 D、抑制不插行、fact 晋升四组）
    - ✅ `internal/store/evidence_integration_test.go` 467 行 **7 个 Test 打真 PG**（同包合计 24 顶层 PASS）：算分落库精度与 metadata 可追溯 + 同指针复用登记、越权引用报错且零残留、同日只算一次而跨日算两次（且分差方向正确）、同日两条把事实压在 candidate、冲突撤回到 `retracted`+`evidence_conflict`+版本 7→8+两条留痕行、过期只写状态不写拒绝码、用户表态行零改写、抑制不插第二条 pattern 且冷却窗外恢复 reactivate
    - ✅ 变异自证 **15/15 打红**（`scripts/mutate_n4_gates.py`，备份→写坏→跑那一条→备份写回 + `filecmp.shallow=False`，不用 `git checkout`）：组合键去重（×2 目标）／geomean→product／独立条数采信提案／核验挪到写入之后／冲突被平均掉／expired 写拒绝码／覆盖用户表态／抑制改成插行（真库撞 23505）／重算读回链 role／fact 晋升门槛降到 1／派生 claim 独立证据下限归零／language 关空转／safety 与 language 换序／metadata 丢算分过程。**复跑 N3 的 13 条同样 13/13 打红**（定序改动没让既有闸门假绿）
    - ✅ 全量回归：`go vet ./internal/... ./tools/...` 无输出、`gofmt -l` 干净、`go test ./...` 在带与不带 `TEST_DATABASE_URL` 两种口径下均全绿（缺 env 时集成测自动跳过）；`pytest backend/tests` 打本机 dev 库（`127.0.0.1:55432`，口令 env-only 不回显）**1 failed / 609 passed / 12 skipped in 20.0s**——唯一失败仍是 2026-10-13 已登记的既有缺陷 `test_commitment.py::test_weekly_quota_defers_third_follow_up`，本批按「发现的缺陷不顺手修」不动；不带 env 的口径是 604/1/17，env 门控一打开那 5 条 DB 闸全过
    - ✅ **env 口径坑已硬修复（2026-10-14 本批，跟踪器无编号——用户点名这批的欠账收口）**：原来「同一个变量喂不了两侧、跑哪侧按哪侧给」不是环境事实，是**缺一条归一规则**。现在两边各有一个唯一落点，且都只认「scheme 里的可选驱动段」而不是某一种前缀：
      - **Python 侧**`backend/core/pg_scheme.py`（新）＝规则本体：`psycopg_url()` 把任何 PG scheme 形态归一到 `postgresql+psycopg://`（`pyproject.toml` 只声明 `psycopg[binary]`，无 asyncpg），`libpq_url()` 剥驱动段给 pg_dump/pgx；非 PG 串 **fail-closed 抛 ValueError 且不回显连接串**（口令不进终端/CI 日志）。入口是 `backend/tests/_pg_env.py` 的 `pg_test_url(env_var, purpose)`——读 env、归一、缺 env 时按 `CLOSEN_REQUIRE_PG` 决定 skip 还是 fail
      - **Go 侧**`go-core/internal/dsn`（新包，`Postgres()`）＝同一条规则的剥驱动方向；三处建池/接线点改为共用它：`cmd/go-api/main.go`（删掉私有 `postgresURL()`）、`tools/crosscheck/main.go`、`internal/store/store_integration_test.go`（原来只认 asyncpg 的 `strings.Replace`）。`dsn_test.go` 除了表驱动与真过 `pgxpool.ParseConfig` 的正向测，还有**反向锚点**（原样串必须被 pgx 拒——证明这层翻译不是装饰）与 `TestEveryPoolConstructorTakesATranslatedURL`（`filepath.WalkDir` 扫 `pgxpool.(New|NewWithConfig|ParseConfig)` 所在文件必须同文件含 `dsn.Postgres`，且断言遍历本身非空，遍历失效也红）
      - **三处消费者切换**：`test_canonical_schema.py`、`test_db_commit_boundary.py`、`test_go_auth_interop.py` 各自的 `_pg_url()`/`_asyncpg_url()` 删除，一律走 `pg_test_url()`；迁移往返件用第二个变量 `N2_MIGRATE_DATABASE_URL`，且**传给 alembic 子进程的也是归一后的串**（原来传原始 env，裸串会让 alembic 同样回落 psycopg2）；`scripts/export_pg_schema.py` 的 pg_dump 侧走 `libpq_url`
      - **反漂移闸**（`backend/tests/test_pg_env_dialect.py`，**17 例，本文件不需要 PG**）：禁第二处硬编码 scheme 翻译、禁实库用例绕开 `_pg_env` 直接读 env、归一后的串必须真解析出装了的驱动（`create_engine(...).dialect.driver == "psycopg"`）、两侧必须落在**同一个库**（`libpq_url(psycopg_url(x)) == postgresql://TARGET`）、缺 env 的 skip/fail 两条各一条测、豁免表不许留死条目（`backend/database.py` 那条按驱动分设 pgbouncer 参数是合法例外、`scripts/mutate_pg_scheme_gates.py` 那条是变异锚点，都带理由）
      - **实测计数**（2026-10-14 复跑，口径写全以便复现）：`test_pg_env_dialect.py` + 三处消费者（`test_canonical_schema.py`、`test_db_commit_boundary.py`、`test_go_auth_interop.py`）在**裸 `postgresql://` / `+psycopg` / `+asyncpg` 三种 scheme 下同为 54 passed / 0 skipped**（前置：`N2_MIGRATE_DATABASE_URL` 指向一次性空库，否则迁移往返那条按守卫静默 skip ⇒ 变成 53/1）；带 `GO_BIN` 的互认文件单独跑在 `+psycopg` 与裸串下各 **10 passed** ⇒ 这就是「一个变量喂两侧」的证据；Go 侧 `go test ./internal/dsn ./internal/store` 三种 scheme 全 ok、`gofmt -l .` 干净、`go vet ./...` 无输出；变异自证 `scripts/mutate_pg_scheme_gates.py` **19 条红闸全打红 / 2 条绿闸保持绿 / 0 异常**（P1–P14 + G1–G5 + GR1/GR2；判定档「受害测试没跑起来也不能算绿」的口径升级登记在 N3 节末 ↳）
    - ↳ 原始坑的取证记录（保留，供复盘「为什么曾经假红」）：Python 侧给裸 `postgresql://…` 时 SQLAlchemy 回落到**未安装的 psycopg2** 方言，而三处实库测试当时都是原样透传 env ⇒ N4 首跑假红 **7 条**（3 schema + 2 commit 边界 + 2 跨语言互认），换 `+psycopg` 后全绿，故是取证手段的口径错误、不是回归；反向地，Go 的 pgx 吃到带驱动段的串当场 `invalid keyword/value`（已探针实测），所以当时只能「跑哪一侧就按哪一侧的 scheme 给」。另两条同源登记一并结案：N7 末「pytest 基线口径坑」（见 :692→现 N7 节 ✅ 行）与 `requirements.txt:13,15` 的失效引用（仓库根无该文件，方言口径改以 `pyproject.toml` 声明为准）
    - ⚠️ **N4 末欠账两条（仍未做，本批不接手；2026-10-14 把归属从「G-5 本节末」改为跟踪器条目，避免只存在于口头）**：① 合成分布只能证明「默认值不会系统性把正常证据压成不可展示」，**不能**替代 prod 真实分布的标定 ⇒ `#59 N4 门限的现网分布标定（合成分布不作数）`（pending）；② `AdmitEvidence`/`ReevaluateInsight` 只有领域入口，**没有 HTTP/MQ 触发面**（谁在什么时候调重算）⇒ `#21 N8 97 端点按域切流` + `#36 R6 三道门接生产者 + 现网数据回填`（异步面的物理落点在 `#35 R5`）

- [x] **N5 · Proactive 资格门 + 频次/疲劳**（3d）｜V3 04 §3–§11、§19 ｜缺口实测：`journey_events` 已具 outbox 形状（事务内预写 pending、cron 拉 `list_due`、mark_sent/skip/defer 不删行，`models/journey.py:4–5,32`），投递侧有频控（每日一条／主动消息互斥／危机停推／沉默降频），但**发送前无资格评估、无最小证据要求、无疲劳度量**；F2 的 `trial_d5` 证明任何新 kind 可直接挤进同一管线
  - ✅ **2026-10-13 交付并过测试**：资格面 `internal/domain/proactive_gate.go` 588 行（十一关 + PRD 四层公式 + `hitLayers`/`eligibilityScore`）、频次疲劳面 `frequency.go` 297 行、数字表 `proactive_policy.go` 172 行（逐条 env 化）、写入口面 `service_proactive.go` 516 行（`AdmitProactive` 事务内评估落库 + `RecheckProactive` 发送时刻二次校验）、类型面 `proactive.go` 264 行；持久化面 `internal/store/canonical_repo.go` 466→**673** 行；`db/queries/canonical.sql` 59→**66** 条（Proactive 相关 11 条），`canonicaldb` 重导
  - [x] trigger 必填（V3 04 §3 七类）+ 分类型最小证据与置信（04 §6）+ hard gate（safety 不参与加权，失败即 block）+ **发送时刻二次校验**（04 §5 末句）
    - ✅ 七类枚举在 `proactive.go:18-24`（memory_recall / pattern_followup / goal_followup / commitment_followup / reflection_prompt / outcome_check / milestone）；缺 trigger 或落在枚举外由第 1 关 schema 直接 reject，不进后面十关
    - ✅ 分类型下限：`ConfidenceMinFor` 按 trigger 取（memory_recall 0.75 / pattern_followup 0.80 / 其余 0.70），敏感域再抬到 ≥0.85（04:139 的「更严」至少不低于 N3 已拍的敏感下限）；最小证据按类型分叉——memory_recall ≥1 条、强证据强度 ≥0.65、pattern_followup 需 ≥2 条**独立**观测（复用 N4 的独立观测口径，不采信提案方自报条数）、reflection_prompt ≥1 条
    - ✅ safety 是 hard gate：不参与任何加权，危机/敏感处置的输入缺失即 `safety_unresolved` 拦下（fail-closed，D-8 / 01 不变量 10）。`eligibility_score` 只由命中层数决定，safety 从不进分子——「加权平均里 safety 占 20%」是 V3 04:77-85 的六因子口径，七-4 P-8 已判不采纳
    - ✅ `RecheckProactive` 就是 04:104 的发送时刻二次校验：终态守卫 → 到期检查 → 重解析 packet → **重跑十一关**（频次用当时的真实历史）→ 才 `MarkProactiveSent`。三处 fail-closed：safety 输入缺失、偏好行读不到、`eligibility_detail` 解析不出，一律判「不能发」而不是「放行」
  - [x] 数值口径（与 V3 的冲突已由 D-4 结案）：以 PRD §5.19 与 `journey_events` deliver 现行频控链为唯一事实源；V3 的 1/24h·3/7d·8/30d 与 quiet hours 22:00–08:00 属其自造默认值，仅交叉核对
    - ✅ 生效值逐条带出处：每日 1 条（closenai.html:596 + `journey_service.py:272-282`）、free 5 / pro 100 / pro+ 1000（closenai.html:590 + `config.py:138-143`）、沉默 2/5/14 阶梯（closenai.html:674）、回访本周 2 条 + 同承诺 2 次（closenai.html:2953）、静默窗只拦本地 **00:00–08:00** 且 22:30 放行（覆盖 V3 04 §8 的 22:00 起，七-4 P-5——PRD 的睡前复盘就发在 22 点，closenai.html:2902）
    - ↳ **V3 的 3/7d·8/30d 未采纳**：PRD 没有这两个数，写进政策等于造出项目里不存在的门；`MinIntervalHours` 因此默认 **0（该门不设限）** 并在注释标为取证缺口，`CLOSEN_PROACTIVE_MIN_INTERVAL_HOURS` 可开。同理 `QuietStartHour==QuietEndHour` 语义是「关掉静默门」，不是「全天静默」
    - ↳ 窗口一律按**用户本地时区**：日窗 `localDayStartUTC`、周窗本地周一 00:00 起**整 7 天**；不可解析时区退 **UTC 判定**（不误放行、也不报错拦死），有正向 + 退路两侧用例
  - [x] 与现有 deliver 频控链**合并为一套管线**，不新建第二套调度（架构方案 §五-2 共表共管线原则）
    - ✅ 关联点是 `proactives.journey_event_id`（`proactive.go:165-166,215`；INSERT/读回见 `canonical.sql:333-347`）：一行 proactive 挂在既有 `journey_events` 的 outbox 行上，由同一条 cron 拉取，本批**没有**新增 scheduler、也没新增 event kind
    - ✅ 计数口径与现行链对齐：只数 **`sent_at` 非空**的行（同 `journey_repository.py:710-716` 的 `has_sent_on`，P-10）——eligible/scheduled 还没吵到用户，计入会让发送时刻的二次校验把同一封消息算成两条。「用户主动发起的对话不计入主动额度」（04:151）由数据面天然满足：`proactives` 只有 AI 主动行，用户消息在 `chat_messages`；沉默阶梯同理只认 `role='user'`
    - ✅ 承诺回访的归属核验：commitment 没有回链表（见 `canonical.sql` 的 `ListProactiveHistory` 注释），改用 `eligibility_detail` 里 packet 的 `ref_ids`；packet 解析不出来时**算命中同一承诺**——这是反骚扰上限，证明不了它是别的承诺就不能再多发一次（宁可少发，不能骚扰）
  - [x] 疲劳只降频——**禁止因参与度下滑而增频**（04 §9 铁律）；沉默不推断（04 §19），到期置 expired
    - ✅ 阶梯只有降向：normal → care（沉默 2 天）→ weekly（5 天，每周至多 1 次）→ stopped（14 天，停推等用户主动回归）。`CheckFrequency` 里没有任何分支会因参与度低而放宽门；`CadenceStopped` 是终局判定而不是「换个时间再发」
    - ✅ 沉默天数在无用户发言时退**注册日**（`journey_service.py:370-378` 的同一 fallback），未来时刻按 0 截；真库 `TestProactiveSilenceFallsBackToRegistrationWhenOnlyAssistantSpoke` 同时钉住「只有 assistant 说话」与「补一条 user 消息」两种输入的阶梯差
    - ✅ 到期在发送时刻判：`expires_at` 已过 ⇒ `expired`、**不补发**（04 §19，变异 #18）。`sent/expired/cancelled/responded` 是终态，Go 侧守卫 + SQL 侧 `status IN ('candidate','eligible','scheduled')` 条件更新双闸（08 §7）
  - 验收：证据不足 candidate 被 block 且理由可解释；现有 journey 全部 kind 与 F2 trial_d5 行为不回退
    - ✅ `go test ./internal/domain -run TestProactive` **89 条 PASS**（`proactive_gate_test.go` 561 行含 40 条表驱动子例、`frequency_test.go` 380 行、`service_proactive_test.go` 562 行）；N5 新增的 9 个拒因码全部由 `TestGateProducesOnlyRegisteredReasons` 反查闸真实触达，未出现「注册了却产不出」或「产出了却没注册」
    - ✅ `internal/store/proactive_integration_test.go` 547 行 **6 个 Test 打真 PG**（本机 dev 库 `127.0.0.1:55432`，口令 env-only 不回显）：① Admit 全绿候选 ⇒ score=1.0 + packet 11 关/4 层/锚点 + pattern 与 insight 各 1 + **模型版本不动**（`personal_model_versions` 0 行，02:1069 Admit 不 ++，只 append `personal_model_events`）；② Recheck ⇒ `sent_at` 落库后同用户第二条被 `frequency_cap` 拦（detail 含「本地日内已发」）、重复 Recheck 报 `ErrProactiveTerminal`、直连 store 的三条守卫各返回 0 行；③ `ListProactiveRefStates` 七表 UNION（ghost id 不返回、空 ids 短路）；④ 偏好面 `ai_consent=false` 与 `status='deleted'` ⇒ `block_reason=="preference_off"`；⑤ 沉默退注册日 ⇒ `fatigue_stopped` 且失败路径也带 `Cadence/SilentDays`；⑥ freshness 面用户 reject 反馈指向被引 goal ⇒ 新候选 `cancelled` + `reject_reason=="stale_trigger"`，队列里那条重评也 cancelled 且 `sent_at` 仍 NULL
    - ✅ 变异自证 **22/22 打红**（`scripts/mutate_n5_gates.py`，备份→写坏→跑那一条→备份写回 + `filecmp.shallow=False`，不用 `git checkout`；复跑于 2026-10-13 与 2026-10-14 各实测输出「22/22 条变异打红」）：周窗 7 天／本地日窗起点前挪／只数 sent_at／承诺归属核不出算命中／沉默 14→30／静默窗起点 0→21／layers 与 confidence 换序／第②层只认活行／第③层锚点今天或未来／第④层封闭问句／分数 /4→/3／MinLayers 2→1／safety 缺失 fail-closed／ai_consent 一票否决／证据归属核验／引用行查不到⇒cancelled／Go 侧终态守卫／发送时刻到期／SQL 侧 status 白名单（需 sqlc 重导 + 片段消失断言）／INSERT 漏 `block_reason`／INSERT 漏 `reject_reason`／沉默阶梯去掉 `role='user'`
    - ✅ **本批发现的真实缺陷 1（已修并过测试，2026-10-13）**：`CheckFrequency` 的周窗首版写成 `weekStart.AddDate(0, 0, 1)` ⇒ 「本周回访 2 条」退化成「本**天** 2 条」，周上限形同虚设。真库集成测首跑即红，改为整 7 天（对齐 `commitment_service.py:250-256` 的「本周」）并由变异 #1 自证
    - ✅ **本批发现的真实缺陷 2（已修并过测试，2026-10-13）**：`InsertProactive` 的语句面**没列 `block_reason`/`reject_reason`**（两列在 `db/schema/0002_canonical_runtime.sql:347-370` 存在），于是生成时刻被挡的候选两列恒空——它们不会再走 `SetProactiveVerdict`（那是发送时刻的改写），04 §22 的 Precision 直接没有分母。补两列 + `sqlc generate` 重导 + repo 映射，由变异 #20/#21 自证。列分工按 P-9：`IsReject ⇒ reject_reason`（本身没资格），否则 ⇒ `block_reason`（此刻不能发）
    - ✅ **本批发现的覆盖缺口 3（已补测并过测试，2026-10-13）**：变异 #8（让第②层也认失效行）首跑 **GREEN-FAIL** —— 不是代码错，是整跑资格门时 freshness 关（第 10 关）先把「引用失效行」判成 `cancelled`，`HitLayers` 恒空，那条路径证明不了第②层自己是否只认活行。补函数级隔离用例 `TestProactive_LayerTwoOnlyCountsLiveRows`（invalidated 不命中 + active 正向对照 + rejected/expired/outdated 三态），#8 改指它后 RED
    - ↳ **登记给 N6–N9 的两条口径（2026-10-14 结案：两条都不是缺陷，是当时容易被误读的语义，现已各有一条测守着）**：① `blocked` **不在** `isTerminalProactive` 里，但三条 SQL 守卫只放行 `candidate/eligible/scheduled`，所以对已 blocked 的行再写结论得 0 行 ⇒ `ErrProactiveTerminal`——这是 fail-closed 语义而不是崩溃，复盘时别误读（N7 的终态守卫用例族仍在守着这条 fail-closed 语义）；② `Cadence`/`SilentDays` 是**输入派生**事实，`Check` 已在入口赋值，失败路径同样带上下文（否则只看得到拒因、看不到当时处于哪一档）——由 `TestProactiveSilenceFallsBackToRegistrationWhenOnlyAssistantSpoke` 与 n5 变异「沉默阶梯去掉 `role='user'`」共同钉住（该批 22 条变异 2026-10-14 复跑仍全红）
    - ↳ **sqlc 分号口径已固化（2026-10-14 结案）**：生成物会**去掉语句末尾的分号**，所以变异 #19 的断言不能用 `marker="… RETURNING sent_at;"`，得改成「被删片段消失 + 变异片段存在」的双侧断言。⇒ 这条从本批起是**脚本写法本身**：N6/N7/N9 的 SQL 类变异都按「在该条命名查询的生成文本内判 absent/marker」写（N9 还专门登记了「拿整个生成文件做 absent 会永远判不过＝闸门空转」）。2026-10-14 复跑 `scripts/mutate_n5_gates.py` 实测 **22/22 打红**（本批对方言/接线面的改动没有让这批闸门空转，被改文件已逐字节复原）
    - ✅ 全量回归：`go vet ./internal/... ./tools/...` 无输出、`gofmt -l internal/ db/ tools/` 干净、`go test ./...` 带 `TEST_DATABASE_URL` 全绿（auth / domain / httpapi / store）；`pytest backend/tests` 打本机 dev 库（`postgresql+psycopg://` 口径，见 N4 末 env 坑登记）**1 failed / 609 passed / 12 skipped in 19.19s**，不带 env 为 604/1/17；唯一失败仍是 2026-10-13 已登记的既有缺陷 `test_commitment.py::test_weekly_quota_defers_third_follow_up`，按「发现的缺陷不顺手修」不动；`test_trial_d5.py` **11 passed**、`test_journey.py` 在全量里未转红 ⇒ 现有 journey 全部 kind 与 F2 行为不回退
    - ⚠️ **N5 末欠账（仍未做）**：`AdmitProactive`/`RecheckProactive` 只有领域入口，**没有 HTTP/MQ 触发面**（谁在生成时刻调 Admit、cron 何时调 Recheck）——与 N4 末第 ② 条同因 ⇒ `#21 N8 97 端点按域切流` + `#36 R6 三道门接生产者 + 现网数据回填`（异步面落点在 `#35 R5`）；本批不半做接线

- [x] **N6 · Outcome 闭环 + 版本化 + 源失效传播**（3d）｜V3 04 §15–§18、01 §18–§21、§22 不变量 5/6/9 ｜缺口实测：全仓无 outcome 表，最接近只有 `user_milestones`(`models/growth.py:36–46`)；用户反馈只出进程进 PostHog（`analytics_service.py:76 _relay_to_posthog`），本地无事件表；`personality_profiles.version`(`models/personality.py:23`) 是画像自增版本非个人模型版本
  - [x] `outcomes` 九类（goal_progress/goal_completed/commitment_completed/behavior_change/user_reported_change/proactive_helpful/proactive_not_helpful/pattern_confirmed/pattern_disconfirmed）+ `outcome_links` + `user_feedback` 六态（answered/confirmed/corrected/rejected/ignored/dismissed，**ignored ≠ 负向**）
  - [x] Outcome 权威来源仅三种：用户显式陈述／显式动作完成／结构化产品事件；LLM 推断只作 candidate（不变量 5）
  - [x] Outcome → Pattern 重算 → 生命周期更新；**单个反例不清除历史证据**（04 §16）→ 触发受影响 Insight 重校验
  - [x] 用户纠正永远产生可审计事件（不变量 6），与 `memory_service._detect_conflict`(:270) 现有冲突链合并
  - [x] `personal_model_versions`：canonical 变更后 ++ 并记 reason；派生 claim 记录所依赖版本（陈旧检测）
  - [x] 删除策略沿用 `user_service.py:346/353` purge 口径（逐表 rowcount=0 天然幂等、结构化 rowcount 审计），**不引入** V3 02 §27 的 12 处 `ON DELETE CASCADE`（D-5）；单条证据撤回只让「仅依赖它」的派生 claim 进 revalidate，不硬删
  - 实现口径（全部裁定在 `closenai-prd/V3方案裁决与分层-2026-10-12.md` 七-5 O-1…O-9 + 七-5.1）：
    - ✅ 词表四套各有唯一事实源（O-1）：`outcome_type` 九类逐字取 04:288-297；`outcomes.status`＝02:176-180 三值 **+ `candidate`**（04:285「LLM inference is a candidate only」在 02 词表里无处落，`recorded` 的字面是「已记录为事实」，不加这一值就得让调用方每次都记得再看 `origin` 列）；`result` 六值取 02:182-189；`origin`＝04:279-285 三条权威来源 + `llm_inference`，**词表里没有 silence**（不变量 5 的数据面）。`feedback_type` 用 **02:156-163 六值**而不是 tasks 原文写的 04 §14 六态——那是「用户怎么回一条主动消息」的**响应态**，两套口径互不兼容（O-1 取证段）
    - ✅ 04 §14 的六个响应态拆三处、不建第二列（O-2）：`answered` ⇒ 就是 N5 已交付的 `proactives.status='responded'`；`ignored` ⇒ **不写任何行**（04:274「ignored ≠ negative outcome」，写一行等于把沉默登记成表态）；其余四态 ⇒ `user_feedback` + 原词留 `metadata_json.response_state`，其中 `dismissed` 映射 `forget_from_model`（01:673 逐字）
    - ✅ `origin` 决定 `status` 由函数表达而非文档约定：`OutcomeStatusForOrigin` 让非权威来源**不可能**产出 `recorded`；`recorded` ⇒ `BumpVersion` + `personal_model_versions.reason='outcome_recorded'`，`candidate` ⇒ **不 ++、不传播**，只写 `outcome_candidate` 事件（O-5）
    - ✅ Outcome→Pattern 只降不删（O-4）：`confidence ×= (1−0.10)`（env `CLOSEN_OUTCOME_DISCONFIRM_STEP`），`evidence_count`／证据回链／`observed_from-to` **一律不动**＝04:313「单个反例不清除历史证据」的可执行形式；削穿 D 级下界 0.45 才走 `weakened`（**不是** `rejected`）；NULL 置信度不动（未知不是 0，削成更低的未知是假动作）；outcome 自动升 `confirmed` 默认关（`CLOSEN_PATTERN_AUTO_CONFIRM=0`，01:697「must be conservative」）
    - ✅ 生命周期转移是**领域行为**（07 §3）：pattern 状态改写全部经 `NextPatternStatus`，用户表态先经 `PatternActionForFeedback` 转成动作——**不存在第二张「表态→状态字符串」直写表**；fact/insight/outcome 走 `StatusForDerivedTarget`，每格取值都按 02 §3 五套词表逐格核验（O-9 矩阵）。非法转移不做成异常：`Changed=false` + `Detail` 说明，对齐 02 §32「无变化不 ++」
    - ✅ 不变量 6：`RecordUserFeedback` **每一条**都写 `user_feedback` 行 + `personal_model_events` 行，即使状态改不动；重复判据是「该目标的**最近一条**是否同型」（表上刻意无唯一约束，历史要留），所以先 no、后 yes、再 no 是新的信息而不是重复（07 §6 `ErrDuplicateFeedback`）
    - ✅ 越权引用在**任何写入之前**失败并整笔回滚（不变量 9）：`verifyTargets` 复用 `ListRefStates` 七表 UNION，一次读同时拿到「归属」与「目标当前状态」，后者供 outcome 的幂等完成（已 `completed` 不再刷 `completed_at`）与不可写预检复用
    - ✅ 目标完成 ⇒ 只取消 `eligibility_detail` 里 packet `ref_ids` 显式指着它的排队触达（`cancelled` + `block_reason='stale_trigger'` + 可解析的取消 detail）；**packet 解析失败的行不取消**（沉默地撤掉一封可能无关的触达，比漏撤代价高）。N5 已交付的 freshness 链没被替换，只是多了一个触发点
    - ✅ 源失效传播（O-6，含 2026-10-13 改判）：`ListDerivedSupportForEvidence` 同取 `total_support`/`live_support`（「活」＝所在 `source_records.status <> 'deleted'`），仅依赖者 fact⇒`invalidated`、pattern⇒`weakened`（同带第 **24** 个拒因 `evidence_withdrawn`），整笔只 ++ 一次；有其他活支撑的行只留痕。**不硬删、不级联删**（O-7：本项目 DDL 实测 **0** 处 `ON DELETE CASCADE`，V3 02 篇有 29 处，D-5 已结案）
    - ✅ **insight 分支只发 `insight_revalidation_requested` 事件、不改状态**（2026-10-14 结案：根因已修、口径未变）——根因是 N4 的既有缺陷（`ReevaluateInsight` 把 insight 行自己的 `model_version` 当乐观锁期望值，同事务二次 ++ 必撞 `personal model version conflict`），已带日期登记在七-5.1 第 2 条并归 **N7**（消费者要在同一笔事务里多次改写，这条不修会让幂等重放间歇失败）。按「发现的缺陷不顺手修」，本批不改 N4 语义，真正的改写由调用方在独立事务里调 `ReevaluateInsight`。⇒ 机械错误已由 **N7-14** 修掉（`TestN7ReevaluateTakesCurrentModelVersion` + n7 变异 #15 双向钉），「只发事件不改状态」这条**设计口径保持不变**：谁消费这个事件、什么时候消费，归下面的欠账行（`#35 R5` / `#36 R6`），不在本批范围内
    - ✅ **本批自己引入、必须当场修的连带项（已修并过测试，2026-10-13）**：`UpdatePatternLifecycle` 原先直写 `last_supported_at = $7`，而生命周期转移不是「新的支撑观测」，写 NULL 会把 N3 建立的这一列抹掉——04:231 的 `recent_correction` 与 N5 的 freshness 关都读它。改 `COALESCE($7, last_supported_at)`，真库用例 + 变异 #15 双向钉住
  - 验收：outcome 落库后 pattern 分数与生命周期变化的集成测试；GDPR purge 覆盖全部新表且计数可查
    - ✅ `go test ./internal/domain -run 'TestOutcome|TestNextPattern|TestPatternAction|TestStatusForDerived|TestDisconfirmed|TestCheckOutcome|TestCheckFeedback|TestOnlyDependent|TestVersionReason|TestRecordOutcome|TestRecordUserFeedback|TestPropagate'` **35 个 Test 全 PASS**（`outcome.go` 680 行 + `service_outcome.go` 697 行的判定面；`outcome_test.go` 428 行为四套词表逐字对照 V3 原文 + 14 行表驱动的 `NextPatternStatus` 矩阵 + 五类 `OnlyDependent` 计数组合，`service_outcome_test.go` 682 行为 18 个事务级用例，`outcome_fake_test.go` 142 行补齐 13 个 N6 桩方法，`var _ Store = (*fakeStore)(nil)` 编译期闸仍在）
    - ✅ `internal/store/outcome_integration_test.go` 808 行 **12 个 Test 打真 PG**（本机 dev 库 `127.0.0.1:55432`，口令 env-only 不回显）：① recorded ⇒ `outcomes`+`outcome_links`+`personal_models.current_version` ++1+`personal_model_versions.reason`+事件行，且 `goal_progress` **不**完成目标；② `llm_inference` ⇒ `candidate`、版本不动、pattern 不动、只出 `outcome_candidate` 事件；③反例 0.72→**0.6480**（DECIMAL(5,4) 不截尾）、`evidence_count` 仍 1、`pattern_evidence` 回链仍 1、状态仍 `supported`、`score_detail` 带 `"step":0.1`；④ 0.48→0.432 才 `weakened` + `evidence_withdrawn`；⑤完成目标 ⇒ `personal_goals='completed'` + 被 `ref_ids` 指着的触达 `cancelled/stale_trigger` + 无关触达不动 + 二次完成不推后 `completed_at`（`target_completed` 事件仍 1 条）；⑥越权引用他人 goal ⇒ `ErrInvalidOwnership` 且 outcomes/outcome_links/版本账本三处计数**逐字不变**、victim 目标仍是 `active`；⑦ confirm ⇒ `confirmed`+`confirmed_at`+**`last_supported_at` 不被抹掉**+分数与证据数不变+`pattern_confirmed` 留痕+`metadata_json` 带响应态原词；⑧重复同型 ⇒ `ErrDuplicateFeedback` 且行数 0 增，夹一条 `correct` 后允许且落 `weakened`；⑨ `correct` 把 recorded outcome 改 `corrected`，终态行第二次表态只留痕（`user_feedback` 2 行）；⑩源撤回 ⇒ fact `invalidated`/pattern `weakened`/insight 只出重校验请求且**只 ++ 一次**、evidence/source/facts/insights 四表行数不变（不硬删）；⑪加第二条活支撑后 ⇒ 只有 fact 进失效、pattern 进 `Monitored` 且回链不拆；⑫ N6 三张新表按 `user_id` 计数可查 → 逐表 delete 归零 → 第二轮 delete 仍 0（幂等）。Python 侧词表闸 `test_canonical_schema.py:171-178` + `test_account_deletion.py:179-189` 仍在，保证任何带 `user_id` 的表都进 `PURGE_TARGETS`
    - ✅ sqlc 交接：`db/queries/canonical.sql` **71** 条命名查询（N5 收口 66 ⇒ N6 +5：`LatestFeedbackForTarget`/`MarkOutcomeStatus`/`ListDerivedSupportForEvidence`/`ListInsightsForPattern`/`ListQueuedProactives`），`sqlc generate` 重导后 `canonical.sql.go` md5 与仓库内文件逐字节相同（幂等），store 侧 13 个新方法**只搬类型不做决策**（07:183-186）
    - ✅ 变异自证 **21/21 打红**（`scripts/mutate_n6_gates.py` 351 行，备份→写坏→只跑那一条→备份写回 + `filecmp(shallow=False)`，不用 `git checkout`；末尾复跑两个包证明恢复干净）：origin⇒status／candidate 是否 ++／candidate 是否传播／反例清 evidence_count／D 级下界／×(1−step) 方向／`OnlyDependent` 双条件／SQL 三个 UNION 分支的活支撑判据（需 sqlc 重导 + 片段消失断言）／归属核验／完成幂等／只撤显式 ref_ids／重复判定／`ORDER BY created_at DESC`／终态 outcome 预检／`last_supported_at` 的 COALESCE（需 sqlc 重导）／02 词表原值／correct⇒weakened／未知状态不得复活／沉默不构成 outcome／`fact_corrected` 留痕词／candidate 与 recorded 的事件类型分叉。守卫类变异一律用「`&& false` 且保住条件里的变量」写法，避免 `declared and not used` 让测试因编不过而假红
    - ✅ 全量回归：`gofmt -l .` 干净、`go vet ./...` 无输出、`go test ./...` 带 `TEST_DATABASE_URL` 全绿（auth/domain/httpapi/store，**174 个 Test PASS**）；`pytest backend/tests` **1 failed / 609 passed / 12 skipped in 23.33s**＝与 N5 收口时同一基线，唯一失败仍是已登记的既有缺陷 `test_commitment.py::test_weekly_quota_defers_third_follow_up`，按「发现的缺陷不顺手修」不动
    - ⚠️ **N6 末欠账（仍未做，本批不半做接线）**：`RecordOutcome`/`RecordUserFeedback`/`PropagateEvidenceInvalidation` 只有领域入口，**无 HTTP/MQ 调用方**（O-8）⇒ `#21 N8 97 端点按域切流` + `#36 R6 三道门接生产者`（异步面落点 `#35 R5`）；`_relay_to_posthog` 继续只做 analytics 镜像。重复提交的表达是 `ErrDuplicateFeedback`（error 路径），因此 `FeedbackReceipt` 里不留 `Duplicate/DuplicateOfID` 两个空转字段（本批删除，N9 读路径若要「被谁压掉」再按事件面取）

- [x] **N7 · Outbox / Inbox 幂等（Go 侧）**（2d）｜V3 00 §6–§7、18 ｜缺口实测：服务端无 message_id 去重（`journey_repository.py:32–41` 应用层按 `(user_id,event_kind,day_no)` 查重，表上刻意不加唯一约束）；`cron_service.py:458/472` 的 `message_id` 只供前端本地落库
  - [x] 口径：**at-least-once + 幂等消费，不声称 exactly-once**（V3 00 §6）
  - [x] 事务内一并写 canonical 状态 + outbox 行；outbox worker 独立发布
  - [x] `inbox_messages(message_id unique)` INSERT 唯一冲突 ⇒ 提交并 ACK、跳过业务副作用
  - 验收：重复投递无二次业务副作用；commit 后崩溃重投不产生重复权威行
    - ✅ 决策点 14 格全部按默认值生效并成文（`closenai-prd/V3方案裁决与分层-2026-10-12.md` 七-6 表 N7-1…N7-14）。取证面比 tasks 原文宽：V3 **02 篇对 outbox/inbox 零着墨**（`grep -ic outbox` = 0），字段面只有 **18 §3 的 13 列**，路由/重试/并发/配置四面分别来自此前未读的 **14**（拓扑 `14:41-46`、retry 阶梯 `14:128-140`、「broker 不是真源」`14:178-183`）、**17**（worker 七步 `17:49-60`、「COMMIT→ACK」`17:91-101`、毒消息 `17:165-177`）、**21**（`21:106` Phase C 验收原文「transaction works **without RabbitMQ**」）
    - ✅ 落点＝**新迁移** `backend/alembic/versions/d9b1f3a5c7e9_add_outbox_messages.py`（`down_revision='c8a2e4b6d0f2'`，实测 `:41`）而不是改已应用的 0002 件。列面＝18 §3 十三列逐字 + **3 个有意偏离列**（`user_id` 进 `PURGE_TARGETS`/D-5、`correlation_id`/`causation_id` 补 17 §6/§7 而 18 §3 漏掉的关联面）；`status` 不加 enum、零 `ON DELETE CASCADE`（O-7）、`UNIQUE (message_id)` + `ix_outbox_messages_user` + `ix_outbox_messages_claim` 三条都在 `models/canonical.py:609-611`
    - ✅ 领域层唯一事实源＝路由表 `outboxRouting`（`delivery.go:37-48`，**10 项**：fact.created／pattern.created×2／pattern.updated×2／insight.created／insight.retracted／insight.expired／outcome.recorded／feedback.recorded／evidence.withdrawn，只有两条 pattern 路由是 `14:66-70` 逐字，其余按同一条 `<聚合>.<变更>` 命名法本地补齐并登记）。判据两条缺一不进表；**17 处** `st.AppendEvent` 一律改走 domain 的 `emit()`，事件行语义一字未动，store 只按 `EnqueueOutbox(row)` 写数（07:183-186）
    - ✅ 消费侧幂等闸在领域层：`ConsumeIdempotent` 的事务形状照 18 §8 逐格（`BEGIN → INSERT inbox → 唯一冲突即零行 ⇒ COMMIT+跳过 fn → fn → MarkInboxProcessed → COMMIT`），`claimed` **唯一**由 `INSERT…ON CONFLICT DO NOTHING RETURNING id` 是否 0 行表达（`canonical_repo.go:814-827`），没有第二步 `SELECT` 因此两个 worker 不会双写；`ConsumeResult.Ack` 由返回值告诉调用方，瞬时失败 ⇒ 不 ACK 重投（17 §5）
    - ✅ 发布面 `internal/messaging/outbox/outbox.go` 211 行 + `outbox_test.go` **9 个 Test**：认领用 18 §5 的 `FOR UPDATE SKIP LOCKED` + `available_at` 兼作租约到期时刻（崩溃 worker 的孤儿行到期自动被别的 worker 重取，17:60），**只有 broker confirm 才置 published**（18 §6／14 §11），失败按 14 §8 原文阶梯 `30s→2m→10m`、到 `CLOSEN_OUTBOX_MAX_ATTEMPTS=8` 转 `dead_lettered` 且不阻塞队列（17 §9）。**本批不引 AMQP**（`21:106` 的分阶段验收 + 14:178-183「broker 不是真源」+ O-8 不半做接线），RabbitMQ 实现归 O2，测试用可注入失败的内存 fake publisher
    - ✅ 保留期（18 §13 只给「must be designed」无数 ⇒ 本地拍、env 各自可覆盖）：published 30 天／inbox processed 90 天，`dead_lettered`/`failed` **不在自动清理范围**（它们是 17 §9 的 failure record，得留给人看）；只走显式入口 `CleanupDeliveryRecords`，配置面七个 env 实测在库：`CLOSEN_OUTBOX_{BATCH_SIZE,LEASE_SECONDS,MAX_ATTEMPTS,RETRY_SECONDS,SCHEMA_VERSION,RETENTION_DAYS}` + `CLOSEN_INBOX_RETENTION_DAYS`
    - ✅ 真库集成测 `internal/store/outbox_integration_test.go` 1115 行 **15 个 Test 打本机 dev PG**（`127.0.0.1:55432`，口令 env-only 从不回显）：权威写入同事务落 outbox、未路由事件不入表、`message_id`＝事件行 PK 且重放稳定（18 §10）、correlation/causation 三级取值、租约过期被重认领、并发认领互不重叠（`TestN7ConcurrentClaimsAreDisjoint`）、published 必须有租约、退避到死信、重复投递零业务副作用、瞬时失败连 inbox 行一起回滚且不 ACK、永久失败行在回滚后仍留 attempt_count/error、超宽 consumer 库里零行、保留期只搬走尘埃落定的投递。另两条本批补的闸：`TestN7MessageDedupIsNotBusinessDedup`（18 §11／N7-12：两个**不同** `message_id`、同一指纹 ⇒ 第二条被 `patterns.fingerprint` 抑制而不是被 inbox 闸放行，不建第二张指纹表）与 `TestN7ReevaluateTakesCurrentModelVersion`（`evidence_integration_test.go:331`，见下条）
    - ✅ **N7-14：修掉七-5.1 第 2 条登记的 N4 既有缺陷**（只修机械错误、不动 N6 口径）：`reevaluateWithin` 原先把 **insight 行自己的 `model_version` 快照**当 `BumpVersion` 的乐观锁期望值 ⇒ 同事务二次 ++ 必撞 `personal model version conflict` 并整笔回滚，会让 N7 消费者的幂等重放间歇失败；期望值改取模型行当前版本。**这条只有真库测得出来**——fake store 的 `putInsight` 把两边刻意同步（`service_evidence_test.go:303`），回退后单测永远不红；新用例先造前置（insight 落库后再 Admit 一条 fact 顶高 `current_version`，用一条 SELECT 同时取快照与现值，**前置造不出就 Fatal 而不是假绿**），再断言撤回成功、`insights.model_version == current+1`、版本账本恰 1 行
    - ✅ sqlc 交接：`go-core/db/queries/canonical.sql` **79** 条命名查询（N6 收口 71 ⇒ N7 +8：`RecordInboxFailure`/`InsertOutboxMessage`/`ClaimOutboxBatch`/`MarkOutboxPublished`/`MarkOutboxRetry`/`MarkOutboxDeadLettered`/`DeletePublishedOutboxBefore`/`DeleteProcessedInboxBefore`），`sqlc generate` 重导幂等；`internal/domain/arch_test.go:61-67` 的 `github.com/rabbitmq` 禁依赖清单继续生效（不变量 7：幂等闸与路由表都在领域层，`internal/messaging/*` 只持 `Publisher` 接口和搬运）
    - ✅ 变异自证 **18/18 打红**（`scripts/mutate_n7_gates.py` 331 行，手法逐字沿用 N6：备份→写坏→只跑受害的那一条→`copy2` 写回 + `filecmp(shallow=False)`，**不用 `git checkout`**；末尾复跑 domain/store/messaging 三包证明恢复干净）。覆盖：未路由事件也入表／路由表项被删／`message_id` 每次新造（违反 18 §10）／causation 丢失／`available_at <= now()` 租约谓词／published 不看认领态／`ErrNoRows` 映射反了／已发布行被重新认领／重试判级差一／业务回调错误被吞／瞬时误判成永久拒绝／`ON CONFLICT DO NOTHING`⇒`DO UPDATE`（幂等闸失效）／inbox attempt_count 不 ++／清理去终态限定／N7-14 回退成快照期望值／payload 空串未归一／退避阶梯索引错位／保留期比较方向反转。**两条首跑打不红、查根因后才成立**：① 受害面选错——`errors.As` 在条件里也会给 `rej` 赋值，拿「永久拒绝」用例去测「分类闸被 disabled」必然假绿，改指瞬时那一侧；② 数据形状使守卫不可观察——非终态行 `published_at IS NULL`，`NULL < $1` 永假，所以「去掉终态限定」现役数据下测不出来，补一条「failed 却带 40 天前 `published_at`」的埋行把它变成可观察（这条埋行现役语句造不出，防的是以后有人拿时间戳当状态判据，18 §13 认的是状态）。`FOR UPDATE SKIP LOCKED` **刻意不作变异项**：改成 `FOR UPDATE` 只会等锁挂死而不是变红，互斥性由 `TestN7ConcurrentClaimsAreDisjoint` 直接钉
    - ✅ 全量回归：`gofmt -l .` 干净、`go vet ./...` 无输出、`go test ./... -count=1 -v` 带 `TEST_DATABASE_URL` **217 个顶层 Test PASS／0 FAIL／0 SKIP**（auth/domain/httpapi/messaging·outbox/store 五包全 ok；N6 收口 174 ⇒ N7 +43）；`pytest backend/tests` **1 failed / 609 passed / 12 skipped in 24.38s**＝与 N5/N6 收口时同一基线，唯一失败仍是已登记的既有缺陷 `test_commitment.py::test_weekly_quota_defers_third_follow_up`（按「发现的缺陷不顺手修」不动）
    - ✅ **pytest 基线口径坑已硬修复（2026-10-14 本批，与 :615 那条 env 坑同一批、同一套闸）**：本行原登记的是「同一份代码，漏导 `TEST_DATABASE_URL` 时是 1 failed / 604 passed / 17 skipped，总数不变而 **5 条从 passed 变 skipped**」——这句话当时只是**口头自证**，所以「同基线」可以悄悄丢掉实库闸。现在有两处机械改动：① `backend/tests/_pg_env.py` 的 `pg_test_url()` 是**所有**实库 env 的唯一入口，缺 env 默认 skip、`CLOSEN_REQUIRE_PG=1` 时改判 fail（报错文案写明「静默 skip 会让『同基线』这句话失去取证」）；② 空串等同没导（`if not raw`，不是 `if raw is None`），否则 `export TEST_DATABASE_URL=` 会被报成「配错了」而不是「没配」。⇒ 本行原句里那句「Go 的 pgx 只认 `postgresql://` 而 Python 侧要 `postgresql+asyncpg://`：同一个变量喂不了两侧，跑哪侧按哪侧给」**已作废**，口径改为：env 给任何一种 PG scheme 都行，Python 补 psycopg3 驱动、Go/pg_dump 剥驱动段，各只有一处落点（见 :615 与 N4 节）。scheme 口径的同源更正也在 `test_go_auth_interop.py`/`test_canonical_schema.py`/`test_db_commit_boundary.py` 三份 docstring 与 `scripts/export_pg_schema.py` 就地改掉——本批实测 `requirements.txt` 在仓库根**不存在**（旧文的 :13,15 引用失效），方言唯一事实源是 `pyproject.toml` 的 `psycopg[binary]`
    - ↳ 实测计数（2026-10-14 本机 dev 库 `127.0.0.1:55432`，口令 env-only 从不回显；`GO_BIN`/`GOROOT` 点名 go1.27.1）：
      - **A｜裸 scheme + env 到位（不加开关）**：`pytest backend/tests` = **1 failed / 658 passed / 3 skipped in 31.22s**（唯一红仍是既有缺陷 `test_commitment.py::test_weekly_quota_defers_third_follow_up`＝#62；3 条 skip 全是常驻环境条件：2 条要 `PG_PASSWORD`、1 条要集群里真有 `closenai` 命名空间）
      - **B｜同一 env + `CLOSEN_REQUIRE_PG=1`**：**1 failed / 658 passed / 3 skipped**，与 A **逐字同计数** ⇒ 这句话从此是机械事实：DB 闸在两种口径下都是真跑的，没有一条靠 skip 凑数
      - **C｜没导 env + `CLOSEN_REQUIRE_PG=1`**：**9 failed / 650 passed / 3 skipped**，其中 8 条正是 A/B 里那 8 条实库闸（3 schema + 1 迁移往返 + 2 commit 边界 + 2 跨语言互认），650 + 8 = 658 与 A 对得上 ⇒ 开关确实把「静默降级」变成了红
      - **Go 侧同 env 三种写法**：`postgresql://` / `postgresql+psycopg://` / `postgresql+asyncpg://` 各跑 `go test ./... -count=1` ⇒ **7 个包 ok / 0 FAIL / 0 「TEST_DATABASE_URL not set」**（三种写法都不需要额外导出别的变量）；`gofmt -l .` 干净、`go vet ./...` 无输出
      - ↳ **A/B/C 三条口径的共同前置（2026-10-14 复跑时实测出来的）**：迁移往返那条要求**一次性空库**，跑过一次它自己就 `upgrade head` 回到 41 表，于是第二次运行按守卫报「不是空库」——这是守卫在起作用不是回归。跑法：`docker exec closenai-pg-dev dropdb -U postgres --force n2_rt_probe && docker exec closenai-pg-dev createdb -U postgres n2_rt_probe`，然后 `N2_MIGRATE_DATABASE_URL=postgresql://…/n2_rt_probe`。**更要紧的是这条前置本身的失败模式**：只导 `TEST_DATABASE_URL` 而漏导 `N2_MIGRATE_DATABASE_URL` 时，A 口径当场变成 **1 failed / 657 passed / 4 skipped**（往返那条静默 skip，总数不变），本批实测复现过一次——这正是 `CLOSEN_REQUIRE_PG=1` 存在的理由：同一次漏导在 B/C 口径下是红而不是 skip
    - ⚠️ **三面欠账（仍未做，2026-10-14 把归属从 O1/O2 改为跟踪器条目）**：① `evidence.accepted` 是 `14:68` 逐字列出的 routing key，但 Go 侧**没有对应事件类型**（`AdmitEvidence` 不写事件行），加事件＝改 N4 语义 ⇒ 归 `#36 R6`（接线时一起判，别在 N7 半做）；② inbox 消费面与 outbox `Worker` **今天没有生产调用方**，只有领域/包入口 + 测试 ⇒ `#35 R5 Python⇄RabbitMQ（消费下发 + candidate 回投 + Go inbound consumer）`，触发面另有 `#21 N8`（与 N4 末②、N5 末、N6 末是同一条欠账，四个批各登记一次是因为四条门都要它）；③ `messaging/{amqp,protobuf,envelope}`（`06:60-72`）⇒ `#34 R4`（AMQP）与 `#31 R1`（proto codegen），payload 现在仍是事件行 JSON 原文 + `schema_version` 恒 1，切 protobuf 是 additive（不动表、不动路由表、不动幂等闸）

- [ ] **N8 · 97 端点按域切流（strangler）**（8–12d，可分批）｜核心风险控制＝契约不变 ⇒ 客户端零改动
  - [ ] 切流序（低耦合 → 高风险）：analytics/discovery → mood/reflection/bedtime/curriculum → memories/journey/onboarding → growth/personality → user → **auth、subscription(IAP)、share(PDF) 与 chat(SSE) 最后单独一批**
  - [ ] 每域四步：Go 实现 → 同一份 openapi 打契约测试 → Ingress 灰度 1%→100% → Python 该路由标 deprecated
  - [ ] `routers/cron.py` 的 16 个 cron 端点与 k3s CronJob 一并切（E3 的 arq/Redis 结论并入 O2/D-7，不两套并行）
  - 验收：每域切流前后响应契约 diff 为空；`jest 851 passed` + `tsc --noEmit` **不改动任何前端代码即通过**（这就是「客户端无感」的可证伪判据）。2026-10-14 注：这条判据当日上午曾因三类前端根因不成立（830/21），下午修毕并复跑 **851 passed / 0 failed ＋ tsc exit 0**，判据重新成立；起算基线以修毕后的 851 为准
    - ↳ 2026-10-14 晚二次更正基线：#64/#65 追加 `yearInReviewEntry.test.ts`（28）与 `analyticsGuestQueue.test.ts`（8）后，实测 **35 套件 888 passed / 0 failed ＋ `npx tsc --noEmit` exit 0**；N8 的「不改前端即通过」判据起算基线改为 **888**
    - ↳ 同日全量后端实测：`.venv/bin/pytest backend/tests` = **1 failed / 612 passed / 11 skipped**（GO_BIN 未上 PATH 时是 606/17 skipped，差额全是 `test_go_auth_interop.py` 的 8 条 go 用例，属环境条件不是代码回归）；唯一红仍是既有的 `test_commitment.py::test_weekly_quota_defers_third_follow_up`（归 #62），本批零新增失败
  - ✅ **批 1 = analytics + discovery 四步全做完（2026-10-14，用户点名「N8 的任务请帮我实现，并通过测试，最后 git push」）**；N8 整体仍是 pending——98 端点里只切了 2 条，其余批次与 cron 面见下面「剩余归属」
    - ①**Go 实现**：`internal/httpapi/analytics_batch.go`（POST `/api/v1/analytics/batch`）＋ `discovery_daily.go`（GET `/api/v1/discovery/daily`）＋ `batch1.go`（依赖面 `Batch1{Users,Moods,Relay,Gate}`、两级语言优先、`pyISO`）＋ `pyjson.go`/`pyspeedate.go`（CPython 值语义与 speedate 错误词的复刻层）＋ `internal/domain/{analytics,discovery,rows}.go`（白名单表、`CompareWeeks` 逐字移植、行结构）＋ `internal/store` 两条只读取数。**未新造错误码**：读面故障落 §四 已有的 `50003`，用户不存在落 `40401`
    - ②**同一份 openapi 打契约测试**＝录制/回放闸：`scripts/gen_n8_contract_goldens.py` 用**真 FastAPI + 真 PG**（`httpx.ASGITransport`）把响应录成原始字节 → `go-core/testdata/n8_contract.json`（occurred_at 语料 **553** 条 + validation **64** 条全 400 + seeded **46** 条 200/404，`masked_fields` 只有 `generated_at`）→ Go 侧 `-run TestN8` 逐字节回放，**实测 663 条比对全绿**。依赖顺序也是被测对象：validation 组挂的是「一读库就把测试弄红」的假件，证 Python 侧校验失败根本不进 store
    - ↳ 掩码只遮 `generated_at` 的**值**，格式另开一条闸：`TestN8PyISOFormMatchesPydanticSerializer` 的四个期望串是 2026-10-14 用响应面同一个序列化器（`TypeAdapter(datetime).dump_json`）实测抄下来的（UTC 写 `Z`、微秒固定 6 位、µs=0 整段省略）。取证更正：**`jsonable_encoder` 给的是 `+00:00`，与真实字节 `Z` 不同**——FastAPI 的 `response_model` 走 pydantic-core 序列化，比对基准拿错函数就会整批翻车
    - ③**Ingress 灰度 1%→100% 的机制**：k3s 自带 Traefik 的普通 Ingress 只能整条 path 二选一，百分比权重要 `TraefikService` CRD——往那台共享生产集群装 CRD 属另一回事且本机无 kubeconfig、写出来无法自证 ⇒ 落点改为「Ingress 只做**域级一刀**（`deploy/k8s/50-ingress.yaml` 把 `/api/v1/analytics`、`/api/v1/discovery` 指 go-api），百分比在 go-api 侧」（`internal/cutover`：`fnv32a(user_id)%100` 粘滞 + `httputil.ReverseProxy` 逐字转交 py-api）。于是**0% 是真 0%**（Go 只多一跳代理，业务逻辑一行没跑），放量＝改 ConfigMap 里那两个数，回滚＝改回 0，不发版不动数据
    - ↳ **本批未对共享 k3s 生产集群执行任何 `kubectl apply`**（那需单独授权）。交付的是清单 + `backend/tests/test_k8s_manifests.py` 的纯结构断言（**12 passed / 1 skipped**，skip 的那条是「本机无可达集群」）；放量判据已写成可证伪：go-bound path 必须匹配 `/api/v1/<域>`、该域必须在 Go mux 里真的注册了、`CLOSEN_CUTOVER_<DOMAIN>` 必须存在且 0..100 且**当前为 0**——改这一处就是放量的登记点
    - ④**Python 该域标 deprecated**：`routers/analytics.py:29`、`routers/discovery.py:26` 各加 `deprecated=True`，**只标不删路由**（回滚要求 Python 还答得上）。重跑 `scripts/export_contract.py`：`contracts/openapi.json` +2 处 `"deprecated": true`，`endpoints.md` 计数仍 **98** 条不变、`test_contract_freeze.py` **10 passed**
    - 跨语言逐字闸另开 `backend/tests/test_n8_batch1_contract.py` **9 passed**：双向差集的 `supported_languages`、匿名化白名单（并断言 `reflection_text/memory_content/message/mood_emoji` **不在**表里）、`maxItems==100`、7/14 窗口与 cutoff、`posthog_host` 默认值、夹具覆盖面非空与播种面覆盖四类判定。**为什么单开一份**：Go 常量抄错时红的是 Go 的测，Python 的 CI 看不见——这条闸把漂移提前一格搬到改 Go 的人本地
    - 变异自证 **41/41 打红**（`scripts/mutate_n8_gates.py`，串行、备份 `copy2` 写回 + `filecmp(shallow=False)`，**不用 `git checkout`**；末尾复跑 httpapi/domain/cutover/config 四包 + Python 两份闸文件全绿）。覆盖：Content-Type 主类型闸／空 body⇒missing／`null` 的归类／too_long 短路／上限数字／信封键序／consent 丢弃分支／校验先于读库的顺序／`json_invalid` 的 input／float repr 精度／两级语言优先的不对称性／语言表与白名单**从对侧**打红／`Z` 与微秒补零／优先级首格与三个阈值／默认 emoji 分值／窗口方向／404 分支／nil 闸兜底／0% 与未登记域／越界与缺上游／转交的 Host 与头／配置默认与拒绝／ConfigMap 从 0 改 1／兜底入口改指 go-api／deprecated 撤与提前标／路由路径漂移／掩码正则改窄／夹具键名漂移
    - ↳ **两条刻意不做**（脚本头部写了理由）：`pyBytesRepr` 的引号选择与非 ASCII→`\xNN` 两支**没有观测点**（夹具里非 JSON 请求体全是 ASCII 且含双引号，改了打不出红）⇒ 批 2 要往生成脚本补一条「非 ASCII 的 `text/plain` 体」用例；`anonymizeProperties` 的过滤动作本身在外发**内容**上，测试不联网也不看内容，改它打不出红，可证的那半是 #14（两侧的表必须逐字相同）
    - ↳ **两条本批取证到的缺陷，已当场处理并登记**：(a) `n8RecordingRelay` 无锁并发 append——同一夹具两次跑出 **116 与 119** 两个外发计数，「外发条数＝响应声称的条数」成了随机闸；加 mutex 后 `-count=1` 连跑 3 次全绿、`-race` 干净。(b) `cutover.New` 里显式 re-Set `X-Trace-Id` 那一格**删掉打不出红**（ReverseProxy 的 `pr.Out` 本就是 `pr.In` 的克隆，头一定带过去）＝冗余防御不是闸，按 N9 口径已删，变异改打在「转交处删头」的可观测面上
    - ⚠️ **四条已登记发散／近似（切流期不许当没看见）**：① occurred_at 语料里 `"92233720368547758.7"` Python **接受**（µs 换算走浮点回绕成 `4892-10-07T21:52:49.312625+00:00`）、Go 拒；`"922337203685477580.7"` 两侧都拒但错误词不同（`datetime_from_date_parsing`/invalid date separator vs dates after 9999）——两条都在 `n8SpeedateDivergences` 白名单里，且各有一条闸要求「Python 侧形态若变了就红」。② Python 侧 `float('nan')` 进 body 会让序列化抛 `allow_nan=False` ⇒ **500**，Go 侧同形（`writePyInvalid` 编不出来落 `50001`），但没有真库观测点，属推定一致。③ 外发是 fire-and-forget：posthog-python 入队即算成功、Go 侧并发发完等总时限且**计数不看发送结果**，两侧 `relayed` 都只数「收下的条数」；差异在平台抖动时才显现，灰度期要看的是 relayed 而不是送达。④ `isJSONContentType` 是对 `email.message` 解析的**等价实现**不是同一个库：`application/json, application/json` 这类畸形串两侧可能判不同，夹具未覆盖，批 2 补
  - ✅ 四面全量回归实测（2026-10-14，口径写全以便复现；口令一律 env-only，从不回显）：
    - Go：`gofmt -l ./internal ./cmd` 干净、`go vet ./...` 无输出、`go test ./... -count=1` 带 `TEST_DATABASE_URL=postgresql://…` + `CLOSEN_REQUIRE_PG=1` **全 ok／0 FAIL**（前置：`export GOROOT=/Users/fengyu/sdk/go1.27.1`，本机 PATH 上的 go 是混合安装）
    - 后端：`.venv/bin/python -m pytest backend/tests` 带 `TEST_DATABASE_URL` + `GO_BIN` **1 failed / 667 passed / 3 skipped in 26.41s**＝唯一红仍是既有 `test_commitment.py::test_weekly_quota_defers_third_follow_up`（#62），**零新增失败**。与 :713 那行 612/11 的差额是**口径不同不是回归**：那次没导 `TEST_DATABASE_URL`，11 条实库用例是 skip；本次它们真跑，故 passed 涨、skipped 掉。↳ 另记一条环境前提：`CLOSEN_REQUIRE_PG=1` 时 `test_canonical_schema.py::test_alembic_roundtrip_on_empty_database` 会从静默 skip 变 fail，它要求**一次性空库**——`docker exec closenai-pg-dev createdb -U postgres n8b1_roundtrip` 现建现用（用完 dropdb，本批已 drop），拿已有的 `n2_roundtrip`（41 张表）会红在「不是空库」的守卫上，那条守卫是对的，别绕
    - 前端：**39 套件 / 1035 passed / 0 failed** ＋ `npx tsc --noEmit` **exit 0**，且 `git status --porcelain frontend` **为空**＝N8 的「不改任何前端代码即通过」这条判据本批成立（基线数从 :712 的 888 涨到 1035 是 #64/#65 之后其它批追加用例所致，与前端产品件无关）
  - ⚠️ **剩余归属（2026-10-14 登记，别只存在于口头）**：批 2 `mood/reflection/bedtime/curriculum` → 批 3 `memories/journey/onboarding` → 批 4 `growth/personality` → 批 5 `user` → 批 6 **auth、subscription(IAP)、share(PDF)、chat(SSE) 单独一批**（auth 有 N2 登记的 Redis 防爆破缺口未补）。`routers/cron.py` 的 16 个 cron 端点与 k3s CronJob 一并切＝**未动**，它要先解 E3 的 arq/Redis 结论（归 O2/D-7）；每批四步的机制、录制脚本与变异脚本口径都已在批 1 走通，可直接复用
  - ↳ 陈旧勘误（只登记不顺手改）：`contracts/endpoints.md` 的「前端调用点」列里 `api.ts` 的行号自 **N0 起就漂了约 128 行**，本批重跑导出脚本时被顺带修正——**这不是本批引入的**，是 N0 之后前端改调而表格未重导；后果写清楚：行号只当导航，端点集合与计数仍由 `test_contract_freeze.py` 逐字守，未受影响

- [x] **N9 · 权威读路径回链**（2d）｜与 L3（#34 洞察依据回链）联动，受 D-3 约束｜✅ 2026-10-14 完成（Go 四件 + 21 个新 Test + 23/23 变异打红，全过测试；Python 与前端零改动）｜缺口实测：`insight_evidence` 表只有 `insight_id/evidence_id/user_id/role`（`0002:84-89`）**没有 memory_id**，用户素材回链只能经 D-2 薄指针 `evidence.source_record_id → source_records(source_table, source_id)`
  - ✅ **改判登记（这条必须先于代码被读到）**：tasks.md L3/#34 当初交付的口径是「读路径聚合」——把 memory id 冗余内嵌进 `journey_events.memory_refs`(`models/journey.py:57`) 与 `growth_records.related_memory_ids`(:23)，读时由前端拿 id 去 `GET /memories` 逐条解析。V3 分层改判后，权威读路径改为 **`insight_evidence` → `evidence` → `source_records(source_table, source_id)` 薄指针**（D-2），理由是：内嵌 id 列表是写时快照，源失效后它不会变少（N6 的传播只落在 `source_records.status`，`canonical.sql:528-565`），而依据展开要的正是「今天还站得住的凭据」。**旧口径一行未删、前端一行未改**，两条路并存；回退开关 `CLOSEN_INSIGHT_EVIDENCE_READ=off` 让新读路径单独关得掉（D-3「留回退开关」的落点），Go 侧灰度今天仍是 0%，所以现网用户看到的仍是旧口径
  - ✅ 交付面＝**Go 侧四件、Python 与前端零改动**（N9-1）：`go-core/db/queries/canonical.sql` +3 条只读命名查询（一跳 `ListLiveEvidenceForInsight`、同快照双计数 `CountInsightEvidenceSupport`、二跳一条 join `ListLiveEvidenceForInsightPatterns`），`internal/store/canonical_read.go` 158 行池面只读仓储（**只搬列不做判断**，07:183-186），`internal/domain/evidence_read.go` 324 行 `InsightExplainer`（归属→可见性→取数的顺序、裁剪、memory_ref 构造、withdrawn 钳位都在这里），`internal/httpapi/handlers_evidence.go` 216 行一个对外 GET。**N4 的算分入口 `ListEvidenceForInsight` 一字未动**（N9-7：新读面用 additive 查询，避免改坏 N4 已钉过的变异）
  - ✅ 端点＝`GET /api/v1/personal-model/insights/{id}/evidence`（05:379 的逐字路径落本项目前缀，N9-2）。**不进 `contracts/endpoints.md` §五**（N9-10）：那张表由 `scripts/export_contract.py` 从 FastAPI 生成、`test_contract_freeze.py` 断言「表行数 == 路由装饰器数」＋openapi 字节逐字，塞进 Go 专有端点会直接打红 N0 契约闸；沿用 N1 的 `/healthz` 先例（就地注释），并在 §八 加了一条**登记缺口**——后果写明了：该表自此只是「Python 侧契约面」的完备清单，N8 切流须另行核对 Go mux
  - ✅ 身份与错误面（N9-4）：user_id **只**取 `Middleware.RequireBearer`，五条查询逐条带 `WHERE user_id = $auth`；「不存在 / 越权 / 状态不可见」三种情形回**同一个** `40402`，kill switch 关闭走信封 404（与「这条路径从未存在」同形）。回 403 或给两条文案＝替旁人确认这颗 id 是真的，端点会退化成 id 枚举 oracle
  - ✅ 语义数值全部可指回出处（N9-5/6/8/11/12/13）：可见集合复用 `canonical.sql:56` 的 `('published','acknowledged','confirmed')` 并加**字面比对闸**（`TestN9VisibleStatusesMatchSQLLiteral` 正则读 SQL，解析不到即 Fatal，防词表各长一半）；「活」判据 `s.status <> 'deleted'` 与 N6 逐字同一、`withdrawn_count = total − live` 由同一条 COUNT 的同一快照算出（依据变少必须可解释）；`memory_ref` 只在 `source_table='memories'` 且 `source_id` 非空时给出，三键 `memory_id/excerpt/created_at` 逐字沿用 `contracts/frontend-compat.md:91`——前端 `resolveMemoryRefs()` 是「解析不到即丢弃」，空主键递过去等于让回链静默消失；裁剪上限 `CLOSEN_EVIDENCE_MAX_ITEMS=20`/`CLOSEN_PATTERN_MAX_ITEMS=6` 是本项目自定值（V3 读路径一处没给数），超限只标 `truncated` 并保留全量 `count`，**excerpt 一字不截断**；`evidence_summary.strength` 回**数值**（活支撑均值，NULL 不进分母）而不采 V3 示例的 `"strong"`——实测该字面全仓 0 处、无词表无阈值，造映射就是 D-4 拒过的「V3 自造默认值」
  - ✅ **诚实登记（N9-9 的推论）**：Go 不回读 legacy `memories/journey_events/growth_records` 三表（解析归 Python、`LastUserActivityAt` 先例只读 users、避免双真源）。因此**这条端点对现网数据今天返回的是空依据**——`AdmitEvidence` 尚无生产调用方（与 N6 末、N7 末同一条欠账），canonical 里没有可展开的 evidence 行。数据回填归 **O2**，前端改调归 **N8**；本批的可观察性由真库测提供，不靠现网数据撑
  - ✅ sqlc 交接：命名查询 **82** 条（N7 收口 79 ⇒ +3），`sqlc generate` 重导幂等；`internal/store/canonicaldb` 仍只被 `internal/store` 引用（`arch_test.go` 的不变量 7 依赖闸在本批改动后仍绿）
  - ✅ 测试面：domain 单测 `evidence_read_test.go` 342 行 **11 个 Test**（fake reader 带调用计数，「二跳只往返一次」是数出来的不是注释说的）＋ httpapi 线上形状测 `handlers_evidence_test.go` 251 行 **5 个 Test**（断言全打在响应**字节**上：五键信封前缀/后缀、`"memory_ref":{"memory_id":"m1"` 逐字项、响应里不许出现 `"strong"`、两种 404 体逐字相同）＋ config 开关取值测 **1 个**（默认 on、`false/OFF/0/"off "` 一律拒绝启动）＋ 真库测 `canonical_read_integration_test.go` 312 行 **4 个 Test**（2 条活→`MarkSourceRecordDeleted`→1 条活且计数 {2,1}、端到端 `Explain` 得 count 1/withdrawn 1＋memory_ref；越权者五个方法全空而**本人必须读通**，否则「谁都读不到」会冒充作用域闸；二跳 2 pattern 各 1 行、失效后只剩 1 行；NULL 强度/时间窗不许搬成有值）
  - ✅ 变异自证 **23/23 打红**（`scripts/mutate_n9_gates.py` 391 行，手法逐字沿用 N7：备份→写坏→只跑受害那一条→`copy2` 写回＋`filecmp(shallow=False)`，**不用 `git checkout`**；末尾复跑 domain/store/httpapi/config 四包证明恢复干净）。覆盖：空 user 守卫／可见性闸／Go 词表加 `draft`（打的是 SQL 字面对齐闸本身）／withdrawn 钳位反向／NULL 强度进分母／超限不标 truncated／pattern 上限失效／空依据组退化成 nil／memory_ref 判据两半各反转／一跳与二跳**各自**去活判据／COUNT 的 `FILTER` 失效／一跳归属谓词 `AND`⇒`OR`／仓储把「查不到」吞成不透明错误／NULL 强度与 NULL 时间窗伪装成有值／handler 丢不可见分支／40402→403／kill switch 判据失效／`memory_id` JSON tag 改名／开关默认值翻转／on|off 白名单删除。**SQL 四条的核验口径**：`absent/marker` 只在**该条命名查询的生成文本**内判定——`s.status <> 'deleted'` 这条字面一跳二跳都有，拿整个生成文件做 absent 会永远判不过＝闸门空转；且 SQL 与生成物两侧都 `os.utime` 后再 `sqlc generate`（N2 登记的 mtime 缓存坑）。**六条刻意不做**（脚本头部逐条写了理由）：excerpt 不截断无代码面可破坏、`isVisibleStatus` 空串守卫改恒假行为不变（冗余防御不是闸）、`var _ InsightEvidenceReader` 是编译期断言打不红、`optionalFloat` 四位舍入在 0.9/0.6 下与两位等价、`s.evidence == nil` 那半边只能造 panic 而非断言红、二跳「只往返一次」由接口签名保证改 N+1 不是一个锚点
  - ✅ 全量回归：`gofmt -l .` 干净、`go vet ./...` 无输出、`go test ./... -count=1 -v` 带 `TEST_DATABASE_URL=postgresql://…` **238 个顶层 Test PASS／0 FAIL／0 SKIP**（N7 收口 217 ⇒ +21：domain 11＋httpapi 5＋config 1＋store 4）；`pytest backend/tests` 带 `TEST_DATABASE_URL=postgresql+asyncpg://…` **1 failed / 609 passed / 12 skipped in 21.82s**＝与 N7 收口逐字同基线，唯一失败仍是既有缺陷 `test_commitment.py::test_weekly_quota_defers_third_follow_up`（不动）。↳ 该行是**当时实测的留痕**，其中的 `+asyncpg` 写法不作口径：同日方言归一落地后三种 scheme 等价（见 N4 节末 ✅ 行与本节末 B/C 计数）
  - ⚠️ **两处回归异常（本批取证所得）**：
    - 2026-10-14 pytest **首跑 2 failed / 608 passed**，多出来的是 `test_growth_service.py::test_weekly_insight_carries_memory_ref_union`。取证：单跑该条 1 passed、单跑该文件 **22 passed**、紧接着复跑全量回到 `1 failed / 609 passed` ⇒ 这是共享真库上跨文件的 `u1` 状态泄漏类**间歇 flake**（`get_latest(db, "u1", …)` 按 user_id 取最新，而 "u1" 这个字面 id 被多个测试文件复用），**不是 N9 造因**——N9 的交付面全在 `go-core/` 与 `scripts/`，本批一个新 `.py` 都没写；工作区里 `backend/models/__init__.py`、`backend/services/user_service.py` 那两处 M 是 N1/N2 批遗留，而 N7 收口的 609 基线正是带着它们测出来的，前提相同才可比。归属＝测试隔离桶（O/P 节 CI 侧），N8 之前不碰
    - 2026-10-14 前端 **jest 基线破了**：33 套件 **830 passed / 21 failed**（登记时的基线是 851 passed），`tsc --noEmit` 仍 **exit 0**。`git status --porcelain frontend` 为空 ⇒ 失败在 **HEAD 就存在**，与 N9 无关，根因三类各自取证：① `referral.test.ts` 三条 ENOENT 读 `site/r/index.html`、`site/_redirects`、`site/.well-known/apple-app-site-association`——`site/` 已迁 Astro（28a9fc3），静态件搬到 `site/public/` 而测试路径没跟；② `i18nLocales.test.ts` 13 条「不留英文兜底」＋4 条「非拉丁文字必须真的落成该文字」，diff 逐字指向 `explore.reflectionsTitle`/`explore.reflectionsEmpty` 两个键——317b390 给 15 语言写回该键时非英语 locale 填的是英文原文；③ `feedbackStates.test.ts` 1 条源码扫描断言缺 `setSavingId(null)`。**连带后果**：N8 的验收判据「jest 851 passed ＋不改前端即通过」（本文件 :605）当时不成立。与 N9 有关的那一面是干净的：`insightEvidence.test.ts` **11/11 绿**，说明回链消费口径没被动过
    - ✅ 2026-10-14 同日晚些，用户「请帮我处理」点名这三笔 ⇒ 已修，复跑实测 **33 套件 851 passed / 0 failed ＋ `tsc --noEmit` exit 0**（与登记基线逐字同数）。逐类落点：
      - ①**测试侧改路径，产品件一行没动**：`referral.test.ts` 的三条读换成 `site/public/`（新增 `sitePublic()` helper，注释写死 28a9fc3 的 WHY）。取证更正登记在案：**不是产品缺件**——`_redirects` 里 `/r/* → /r/index.html 200` 且在、AASA `components[0]["/"] == "/r/*"`、落地页码类 `[ABCDEFGHJKMNPQRSTUVWXYZ234567]{8}` 在，全部住在 `site/public/`（构建整目录拷进已 gitignore 的 `dist/`），**对外 URL 一字不变**
      - ②**一份映射表整体重跑，不逐文件手改**：13 个 locale（es/pt/fr/de/it/ru/ja/ko/th/vi/km/id/ms）的 `explore.reflectionsTitle`/`reflectionsEmpty` 写成各语言真翻译，名词沿用各语言**已审校**的 `reflect.saveDraft`（es reflexión / ja 振り返り / th การสะท้อน / km ការឆ្លុះបញ្ចាំង …）；`git diff --stat`＝13 文件 26 增 26 删。**诚实登记**：这 26 条译文是模型按既有名词尽力译写，**母语审校仍欠**（归 #39 的 L2 桶，与 2026-09-30 那轮同源）
      - ③**红的是窗不是代码**：`ReflectScreen.tsx:362` 的 `setSavingId(msg.id)` 与 `:385-387` 的 `finally { setSavingId(null) }` 本来就在，是固定 16 行窗被 317b390 插进的 13 行本地落库挤出窗外。测试侧新增 `fnBlock()`（needle 行切到函数体收尾 `^ {2}};`），**收尾找不到就红而不是退回整份文件**——整份文件里那条复位无处不在，退回整份就等于守卫空转
      - ✅ 变异自证 **15/15 打红＋1 条应保持绿**（`scripts/mutate_frontend_gates.py`，手法逐字沿用 N7：备份→写坏→只跑受害那一份测试→`copy2` 写回＋`filecmp(shallow=False)`，**不用 `git checkout`**）。红闸覆盖：locale 值改回英文／改成**不与英文逐字相等的拉丁文**（专打文字种类闸，证明它与逐字相等闸互相抓不到）／塞连续 3 个英文虚词／丢 `{{hour}}` 占位符／把同形词真译掉（打的是「豁免表不许留死条目」）／并进一行／CLDR 单形态语言多出 `chatCount_one`／键名打错一字母／`_redirects` 200⇒301／AASA `/r/*`⇒`*`／落地页码表少一个字符／删 `setSavingId(null)`／删连点守卫／删 `disabled`／`keyboardVerticalOffset` 90⇒100。绿闸 G1：往 `onSaveDraft` 的 try 里插 20 行（就是造成假红的那种增长），`feedbackStates.test.ts` **仍全绿**＝假红根因确实修掉了，不是把断言改松。跑完 `git status --porcelain site frontend` 只剩本批应有的改动、复跑全量 851/0

## O. Python AI Runtime｜agent 与 AI 能力专职（2026-10-12 改判立项）

> 边界（V3 06 §4/§5 + 用户指示）：Python 只做 agent/AI——NLP、抽取、模式检测、洞察草稿、主动消息草稿、embedding、语义检索、LLM 编排、评测；**不得直连 canonical 表**，产出一律 candidate 经契约回投 Go。
> 复用度实测：这一步是**改造不是重写**——`backend/core/llm.py:149/202/259`（complete/stream/embed；⚠️ 2026-10-14 校正：旧文本写的 99/121/147 是 O4 插留痕代码前的行号）、`message_generator.py`(459)、`growth_service.py`(959)、`personality_service.py`(605)、`memory_service.py`(472)、`mood_service.py`(354)、`discovery_service.py`(141)、`reflection_service.py`(145) 的 LLM/向量部分即 AI Runtime 的既有资产。V3 21 §Phase G 的「建 Python AI Runtime」在本项目工时≈0。

- [ ] **O0 · AI 独立目录归堆（用户 2026-10-14 点名：「我希望 AI 可以独立一个目录方便后面维护」）**（1d）｜不依赖 N8/R1，现在就能做
  - [ ] `backend/ai/`：搬入 `core/llm.py`、`core/generation_run.py` 与 R3 表里那 14 个 AI 文件的**能力层**（抽取/嵌入/草稿/分类/评测），`routers/`、`models/`、`repositories/`、`alembic/` **明确不搬**（DDL 属主与契约源仍在原位，裁决七-6 与 O6 口径不变）
  - [ ] 边界闸 `backend/tests/test_ai_boundary.py`：`backend/ai/**` 不许 import `backend/routers`、不许直连 canonical 的写面（唯一合法出口是 candidate 回投，R5）；闸本身按变异自证打红一次才算立起来
  - [ ] ⚠️ 代价登记（这条是我自己的判断，不是取证结论）：动 import 面会牵动 pytest 609 例的路径与 `test_python_reaches_canonical_models_only_through_deletion` 的**按模块名枚举**逻辑 ⇒ 必须同批改闸名与 allow-list，并复跑全量回到同基线；目录归堆**只买可维护性，不买到解耦**——真正的独立是 R3/R5 之后的「换进程」
  - 验收：`npx tsc` 无关；`pytest backend/tests` 复跑 **≥609 passed / 1 failed（既有缺陷）/ 12 skipped** 同基线；`go test ./...` 不动仍 238 PASS；边界闸变异自证打红
- ⛔ **O1 · 跨语言契约冻结**（原 2d）｜**本行不再单独执行**：交付物由 **R1** 承担（契约源要同时喂 gRPC 与 MQ 两个面），编号保留不重排｜V3 10/11/15
  - ↳ 原三条子项（三份 `.proto`、双端 codegen、跨语言契约测）＝**R1 的子项**，跟进与验收只看 R1，本处不重复挂 `[ ]`
  - ↳ 顺序约束保留登记：V3 README「Package Scope」要求「先从实际 domain API 冻结 contract」；**N2/N3 已于 2026-10-13 定稿，该前置今日已满足**，故 R1 无阻塞
- ⛔ **O2 · RabbitMQ 拓扑 + Python worker**（原 4d）｜**本行不再单独执行**：交付物拆进 **R4**（broker + Go AMQP Publisher）与 **R5**（Python ⇄ MQ + candidate 回投），跟进只看 R4/R5｜V3 12/14/17｜缺口实测保留：全仓无任何消息队列，现为 QStash HTTP 回调 + inline 批处理（`cron_service.py:168–188/287/320`），Redis 仅作 KV/限流（`core/redis.py:56–139`）
  - ↳ 原三条子项（exchange/DLQ/confirms/prefetch 与 handler 零业务分支、幂等键走 message property、与 E3 的关系在 D-7 拍板）分别落在 R4、R5 与 D-7 的登记里，本处不重复挂 `[ ]`
- [ ] **O3 · Python 停止写权威态**（3d）｜与 N3 配对
  - [ ] 三处直写点改为发 candidate；`memory_service._detect_conflict`(:270) 的 LLM 矛盾判定从「立即生效（新建 + 旧行 supersede）」改为 candidate 回投
  - [ ] LLM 补 `max_retries` + 失败分类（超时/429/5xx/畸形输出）；畸形输出走 N3 reject 而非重试
  - [ ] 安全闸归属见 **D-8**：建议「危机判定 + 最终措辞 policy 归 Go（policy 属权威侧），按句缓冲的流式实现留 Python（infra）」；现状 `safety_service.py:356 check_text`/`:420 sentence_gate`/`:393 fail-open` 需要拆分，**fail-open 语义在跨进程后必须重新定义**（超时不得等于放行）
- [x] **O4 · 生成留痕 `ai_generation_runs`**（1.5d）｜V3 02 §25、01 §20、§22 不变量 8 ｜缺口实测：`llm.py:99` 无留痕、`config.py:51` 四家 provider 可热切、`llm.py:77` 已明文警告「切换 provider/模型后存量向量需重嵌入」但无列可查
  - [x] 表在 Go 侧（canonical），Python 每次调用带 provider/model/purpose/tokens/latency/status ｜✅ 2026-10-13 落地：schema 先按 canonical 口径建在现有 PG（`models/generation_run.py` + 迁移 `b7d9e1f3a5c8`，单头校验 `alembic heads` 通过），N2 直接沿用同一份 DDL
  - [x] `ShareService` 的 GB 45438-2025 AIGC 标识段落改从 run 元数据取模型名（合规事实源统一）｜✅ 2026-10-13：`_aigc_metadata()` 把 `provider/model` 写进 PDF `/Creator`、run id 写进 `/Keywords`，报告与 Life Archive 两条导出链都从落库行的 `generation_run_id` 反查（`test_pdf_metadata_carries_run_provenance`）
  - [x] **建议不等 Go 迁移、先在现 Python 后端落这一条**（提审合规在前，1.5d，Go 侧 N2 直接沿用同一 schema）｜✅ 2026-10-13 即按此执行
  - 实现口径（`core/generation_run.py` 模块注释）：一次调用一行留痕（complete/stream/embed，异常路径也写 `status=error`）；`purpose` 由调用帧推导（`module.func`），20+ 个调用点因此零改动；派生行回链 `generation_run_id` 只在产出可用时写 → **空值即「这行不是 AI 写的」**，`status=fallback`（闸门丢弃、改发模板）由 `safety_service.check_or` 落；安全分类器自检带 `track=False`，不抢占指针
  - 回链覆盖的三张直接落库 AI 正文的表：`memories`（对话抽取）、`personality_profiles`（MBTI 推断）、`growth_records`（日报/周报/月报，周报是「正文段+预测段」两次生成，逗号存多值同 `related_memory_ids` 口径）
  - 验收：新增 `backend/tests/test_generation_run.py` **18 passed**；全量 `pytest` **558 passed**（基线 540 + 18），唯一红项为下面登记的既有日期漂移用例
  - ↳ 2026-10-13 发现未顺手修（归 **T 桶·测试基建**）：`test_commitment.py::test_weekly_quota_defers_third_follow_up` 在 2026-10-12 还是绿的，今天起失败。根因是频控 2「今日已有主动消息」被误命中——`chat_messages.created_at` 用 DB `server_default=now()`，而 `time_travel` 只冻 Python 时钟；`journey_repository.count_today` 的窗口只设下界不设上界，于是「昨天真实时刻写入的行」在今天的冻结日查询里仍算今日。修法二选一：给 `count_today` 补上界，或让用例显式写 `created_at`
  - ↳ 2026-10-13 发现未顺手修（归 **合规桶**）：性格名片标语走 LLM（`share_service.py:1120` 一带），但那条 `_render_pdf` 未传 `aigc=True` → 对外传播的名片 PDF 无隐式标识，与报告/档案不同口径。属 O4 之外的既有缺口，需要单拍一次「名片是否算 AI 产出物」
  - ↳ 2026-10-13 已知边界（不视为缺陷）：① `mood_checkins` 的 AI 解读文本、`journey_events.payload` 未加 `generation_run_id`（本轮只覆盖三张正文表）；② 流式 `sentence_gate` 的**逐句**丢弃不置 fallback，只有 `check_or` 的整段回退会置；③ 调用方中途放弃流式迭代则无留痕行（`stream` docstring 已写明）
  - ↳ 2026-10-13 **补欠账（O4 自己的漏项，非顺手修）**：`ai_generation_runs` 建表时**没进注销 purge 清单** ⇒ 注销后残留用户维度数据，违反 `purge_user` docstring 的承诺「覆盖全部用户数据表，确保无残留」。已补：清单提为模块常量 `user_service.py:PURGE_TARGETS`（含新表）、`test_purge_user_deletes_related_data` 断言 `ai_generation_runs=1` 且行消失、并加**闸门** `test_purge_covers_every_user_scoped_table`（按 `Base.metadata` 枚举所有带 `user_id` 的表逐表对账，17 张全覆盖）。闸门变异自证：摘掉新表 ⇒ 该用例失败，恢复 ⇒ 全绿
- [ ] **O5 · 派生渲染留 Python**（2d）｜reportlab 是 Python-only（`share_service.py:19–25` 用 `UnicodeCIDFont('STSong-Light')`，全仓无 Go 对案）
  - [ ] Life Archive / 分享卡 / 年报 PDF 归 Python worker；Go 只出 HTTP + entitlement 校验 + 任务下发 + 完成推送（架构方案 §二 M6 的「大文件走后台任务」形态不变）
  - [ ] ⚠️ 若强行把 PDF 塞进 Go Core，会卡住 N8 的 share 域切流——故显式划出边界
- [ ] **O6 · FastAPI HTTP 层退役**（2d，N8 全切完后）
  - [ ] 删路由与 ASGI 入口，保留 services 的 AI 部分 + worker 入口；`vercel.json` 的 serverless 声明与 `main.py:105` lifespan（常驻 `_purge_sweeper` 在 serverless 下本就不可靠）一并退役
  - [ ] 24 个 alembic 迁移的交接：以 PostgreSQL schema 快照为基线交给 Go 迁移工具（D-7），`backend/alembic/` 归档不删（对齐「无反向迁移、历史表不 drop」既有原则）
- [ ] **O7 · 双运行时的运维面**（2d）｜V3 20、21 §Phase J｜范围收窄后**只剩运维**：部署清单与 e2e smoke 归 **R7**，4c8G 内存实测归 **R4**（原第一条子项与它们重复，已去重）
  - [ ] 指标埋点逐条对上 V3 03 §20 / 04 §22：`accept`/`reject`/`invalid_evidence`/`stale_candidate`/`dismissal`/`fatigue` + DLQ 深度与 `oldest_pending`
  - [ ] DLQ 运维手册（replay **工具**在 R7，这里写**操作步骤与判据**）、告警阈值、迁移回滚策略（沿架构方案 §11.4：改 Ingress backend 名即回滚）
  - [ ] tracing 把 `message_id`/`correlation_id`/`causation_id` 跨进程串起来（三列已在 `OutboxRow`，缺的是 Go→MQ→Python→MQ→Go 一跳不丢）
  - 验收：一条消息从 Go 决策 → MQ → Python 回投 → inbox 落地，三个 id 在日志里可逐跳串联；DLQ 里有非空样本时，手册的 replay 命令能重放成功且**不重复生效**（inbox 拒）

## R. 跨语言通信面｜gRPC 同步 + RabbitMQ 异步（2026-10-14 用户立项并同日复述确认：「go 与 python 服务之间的通信，同步的走 grpc，异步的走 rabbitmq，这个是没有问题的」⇒ **划分即定案**；gRPC 的**方法面**另按实测调用点收窄，见 R0/R3）

> **D-9 拍板（按 V3 允许的范围，不是全盘换成 gRPC）**：**同步低延迟**走 gRPC + Protobuf，**状态转移与批量派生**走 RabbitMQ + Protobuf，两者共用同一份 `.proto` 契约源。
> 出处逐字：V3 `06_SYSTEM_COMPONENTS_AND_BOUNDARIES.md:164-170`「Use **gRPC + Protobuf** when a synchronous low-latency RPC is actually required」并逐条列出四类＝embedding service / reranking service / model capability check / low-latency inference；本项目第五类是对话 token 流（裁决文档 §八 风险 3「Go 出 `/chat/stream`、token 流由 Python 产生，需一条同步低延迟通道」——本节的 gRPC server streaming 就是那条通道的落点，风险 3 在此结案）。⚠️ **V3「允许」≠ 本仓「存在」**：实测有调用点的只有 `Embed`、`ClassifySafety`（属 low-latency inference）、`StreamChat` 三个；reranking 与 model capability check 全仓 0 命中，按 R0 判据不进首批方法表（2026-10-14 实测，见 R3）。
> ⚠️ **同时采纳 V3 `06:173` 的反面约束**：「Do not introduce gRPC merely because both runtimes use Protobuf」⇒ 判据落成一句可复审的硬要求：**每个 gRPC 方法必须能回答「为什么不能等 MQ 回投」**，答不出就改 MQ。异步链（洞察草稿、主动消息、回填、失效传播、outcome 记录）刻意**不**改成 gRPC——它们是「Go 决策、Python 提案」的跨事务边界工作，改同步就等于把 N7 的 outbox/inbox 可靠性与幂等面作废。
>
> **现状实测（本节的每一条缺口都有锚点，别按想象拆任务）**
> - Go 侧投递面**只差 broker 实现**：`internal/messaging/outbox/outbox.go:29` Repository（`ClaimOutboxBatch`/`MarkOutboxPublished`）、`:57` `Publisher` 接口、`:76` `Worker.Run/RunOnce`、`:124` fail→退避/死信——**N7-7 刻意不引 AMQP**（裁决七-6 逐字「不引。交付面＝`internal/messaging/` 的编排面 + 一个 Publisher 接口」）
> - 路由表已在代码里：`internal/domain/delivery.go:37-46` 十条事件类型（`fact_admitted→fact.created`、`pattern_admitted/reactivated/lifecycle→pattern.*`、`insight_admitted/retracted/expired→insight.*`、`outcome_recorded`、`user_feedback_recorded`、`evidence_withdrawn`）；`OutboxRow` 列面＝18 §3 十三列 + N7-1 三列偏离（`user_id`/`correlation_id`/`causation_id`）
> - Python→Go 的 **candidate 队列今天不存在**；Go 的 inbox 消费面（`inbox_messages` 表 + N7-2 SQL + `ConsumeIdempotent`）**没有生产调用方**（N7 三面欠账②，`tasks.md:600`）
> - payload 现在仍是事件行 JSON 原文 + `schema_version` 恒 1（三面欠账③）⇒ 切 protobuf 是 **additive**，不动表、不动路由表
> - Python 的 AI 资产＝`core/llm.py:149/202/259`（complete/stream/embed，⚠️ O 节旧文写的 99/121/147 是 O4 插留痕前的旧行号）+ 14 个调用文件（`personality_service.py` 605 行 4 处、`growth_service.py` 959 行 4 处、`message_generator.py` 459 行 3 处、`memory_service.py` 472 行 3 处含 `:465 embed`、`safety_service.py` 440 行 2 处含 `:356`、`mood/bedtime/enneagram/reflection/push.copy/curriculum/cron/share` 各 1 处）
> - 依赖面实测：`go-core/go.mod` 只有 argon2id / jwt / pgx（**无 grpc、无 amqp**）；`backend/requirements.txt` 有 fastapi/pgvector/httpx/openai（**无 grpcio、无 aio-pika**）⇒ R1/R2/R4 都要新增依赖，且要过 D-7 那套「选型按实测计数」的口径

- [ ] **R0 · 通信面裁决落表（0.5d，先文档后代码）**｜上面那段就是默认值，等一句「按默认值」生效
  - [ ] `contracts/rpc/ownership.md`：**三面**一张表列尽——① 同步 gRPC 方法（首批三个：Embed / ClassifySafety / StreamChat）② 异步**事件**路由（`delivery.go:37-46` 现有十条 + `evidence.accepted` 待补）③ 异步**命令**路由（`ai.*.requested`，走 outbox 复用但不进事件表），每行逐条给「为什么是这个面」；rerank / model capability check 作为「V3 允许但本仓零调用点」单列在表外，注明加入门槛
  - [ ] 判据进 §R 顶部（V3 `06:173`），并把裁决文档 §八 风险 3 标为「已由 D-9/R2 结案」
  - 验收：文档里每个方法/队列都能指回一个既有调用点或既有领域入口（指不出的从表里删掉）；裁决文档 D 表新增 D-9 行
- [ ] **R1 · `.proto` 单一契约源 + 双端 codegen**（2d，＝O1 的落地形态）｜V3 10/11/15/16
  - [ ] `contracts/proto/closenai_common.proto`（`memory_ref` 三键、`SourcePointer`、`Envelope`）、`closenai_ai.proto`（Fact/Pattern Candidate、Insight/Proactive Draft＝10 §5–§8 的字段面）、`closenai_events.proto`（11 §2 事件信封）、`closenai_ai_rpc.proto`（service 定义）。⚠️ **一份 proto、两个承载面**：`*_rpc.proto` 的 service 只含 R3 的三个方法；Candidate/Draft 消息在 `closenai_ai.proto` 里定义但**由 MQ 投递**，gRPC 侧不重复定义、也不返回它们
  - [ ] 每条消息带 `schema_version`，演进只 additive（10 §10、V3 00 §9 原则 12）
  - [ ] Go codegen（`protoc-gen-go` + `protoc-gen-go-grpc`）与 Python codegen（`grpcio-tools`）都进 CI；生成物入仓还是现生成——按 D-7 的口径实测一次再拍
  - [ ] ⚠️ 顺序约束（V3 README「Package Scope」）：这条在 N2/N3 定稿后才不返工，**今天已满足**（candidate 字段面由 N3/N4/N5/N6 定稿、`memory_ref` 三键在 `contracts/frontend-compat.md:91` 已逐字）
  - 验收：跨语言 fixture 测——同一 payload Go marshal → Python unmarshal 且字段逐字相等（反向同理）；**改 `.proto` 不重生成就红**（生成物字节/哈希闸）；缺 `schema_version` 红。`scripts/mutate_r1_gates.py` 变异自证（备份写回，禁 `git checkout`）
- [ ] **R2 · Go 侧 gRPC client（AI Orchestrator 出口）**（2d）｜V3 13 §Go AI Orchestrator
  - [ ] `go-core/internal/aiclient/`：接口 + gRPC 实现 + fake；方法面＝R3 收窄后的**三个**（`Embed`/`ClassifySafety`/`StreamChat`，多一个都不加，判据是 V3 `06:173`）；每条调用带 deadline、重试上限、熔断与**降级模板**（复用 N5 的 fallback 语义与 `status=fallback` 留痕）
  - [ ] **D-8 的落点在这里**：`safety_service.py:356` 今天超时＝fail-open 放行（`:10/:48/:211` 三处写明），跨进程后必须改成 **fail-closed 或降级模板**——超时不得等于放行。危机判定与最终措辞 policy 归 Go，按句缓冲的流式实现留 Python
  - [ ] 对话 token 流：Go `/chat/stream` 的 SSE 由 gRPC **server streaming** 透传（结案 §八 风险 3）；首字延迟与背压需一条实测数字，不接受「应该差不多」
  - [ ] 依赖新增走 D-7 式取证：`go list -m all` 计数 + 二进制体积/内存前后差实测，写进本节
  - 验收：Go 单测用 fake 覆盖超时/429/5xx/畸形输出四类（畸形输出走 N3 reject 而非重试）；跨语言真 server 的互认测（沿 N1 的 go/py 双容器互认口径）；**变异自证必须打红「超时被当放行」这条路径**
- [ ] **R3 · Python 侧 gRPC server（AI 面的进程入口）**（2d）｜V3 12 §Python AI Runtime
  - [ ] `backend/ai/rpc/server.py`：handler 只做 `decode → 调 backend/ai/* → encode → return`，**业务分支零内嵌**（与 O2 同一纪律，违反就进 R6 的闸）
  - [ ] 首批方法**只有三个**，逐个指回实测调用点：`Embed`(`memory_service.py:465`、`cron_service.py:1024`)、`ClassifySafety`(`safety_service.py:356 check_text`——必须在内容交到用户手上**之前**判完)、`StreamChat`(`routers/chat.py:55` → `:108 generator.generate_chat_stream` → `message_generator.py:122 llm.stream`，用户在线等 token)
  - [ ] ⚠️ **2026-10-14 自查纠正**（我第一版把 `ExtractFactCandidate`/`DraftInsight`/`DraftProactive` 写进了 gRPC 方法表，与本节顶部「异步链刻意不改成 gRPC」以及 R5/R6 自相矛盾）：这三类的产物**要过 Go 领域门（N3/N4/N5）才能落权威态**，同步返回等于让 Python 决定写入时机 ⇒ 一律走 MQ：Go 下发 `ai.*.requested` → Python 回投 `*.candidate`（R5、R6）。另：V3 `06:165-167` 逐字允许的两类（**reranking service / model capability check**）在本仓实测**零调用点**（全仓 `rerank|capability` 不区分大小写 0 命中）⇒ 按 R0 的「指不回调用点就从表里删」判据，不进首批，等出现真实调用点再加
  - [ ] 留痕照旧：每次调用一行 `ai_generation_runs`（O4 已交付，`core/generation_run.py`），provider/model/purpose/tokens/latency/status 不因换协议而丢
  - 验收：pytest 走 grpc test harness；每个方法一条「畸形输入 → `InvalidArgument` 且不抛栈」；`backend/ai` 的边界闸（O0）必须同时覆盖 rpc 目录
- [ ] **R4 · RabbitMQ 拓扑 + Go 的 AMQP Publisher（补齐 N7 唯一缺的那层）**（3d）｜V3 14、17 ｜缺口实测：`outbox.go:57` 的 `Publisher` 今天只有 fake
  - [ ] exchange/queue/binding/DLQ 全部按 14 篇落地：topic 路由（`14:62`）、显式 retry 队列而非无限 requeue（`14:126`）、NACK/requeue 与 reject→DLQ 分流（`14:111-115`）、**publisher confirms**（`14:163`：确认后才 `MarkOutboxPublished`，未确认则 outbox 仍是重试权威——这条正是 N7-10 的既有口径）
  - [ ] `internal/messaging/amqp/` 实现 `Publisher`；`Worker.Run` 在 `cmd/go-api/main.go` 起，带独立 kill switch（沿 N9 的 `CLOSEN_*` 开关风格，灰度可控）
  - [ ] **命令面与事件面同一条 outbox、不同 exchange**（R3 把三类草稿挪去 MQ 之后必须补上这条，否则「Go 下发任务」没有可靠投递通道）：`ai.*.requested` 是命令不是事件，**不进 `domain/delivery.go:37-46` 的十条路由**——那张表的进门判据逐字是「权威模型内容变了」（`delivery.go:31-36` 注释），命令不满足；默认形态＝在决策事务内 `EnqueueOutbox` 一条 `ai_*_requested` 行、路由到独立 task exchange，从而**复用** outbox 的租约/退避/死信与 confirm-后置 published（丢一条命令＝用户永远等不到那条洞察，不可接受）。追加的是新事件类型，不改十条既有语义 ⇒ additive
  - [ ] prefetch 与并发消费分区（`14:150`）；指标（accepted/rejected/dlq/oldest_pending）与 tracing（message/correlation/causation，列已在 `OutboxRow`）
  - [ ] **4c8G 内存实测**（§八 风险 2）：`go-api`+`python-worker`+`rabbitmq`+`redis` 四件事常驻实测，没做完不进 N8
  - 验收：真 broker 集成测——发布途中 kill broker ⇒ outbox 仍 pending ⇒ 重启后重投且**不重复生效**（消费侧靠 inbox）；confirm 丢失不许置 published；DLQ 直入且禁 requeue。`scripts/mutate_r4_gates.py` 打红「无 confirm 也置 published」
- [ ] **R5 · Python ⇄ RabbitMQ（消费 Go 下发、回投 candidate）**（3d）｜V3 14、17、18 ｜缺口实测：这条队列今天**单向都不存在**
  - [ ] Python 消费 `ai.*.requested`（Go 下发任务），handler 只 decode → AI → encode → publish `*.candidate`；幂等键走 **message property 而非 routing key**（V3 14 §7）
  - [ ] Python **只发 candidate，不写权威表**（这是 O3 的物理落点；三处直写点改造，含 `memory_service.py:270 _detect_conflict` 从「立即新建+旧行 supersede」改为 candidate 回投）
  - [ ] Go 消费 inbound `*.candidate` → 路由进已有领域入口（`AdmitCandidate`/`AdmitEvidence`/`AdmitProactive`…），经 `ConsumeIdempotent` + `inbox_messages`（表和 SQL 都在，缺的是 consumer）
  - [ ] 畸形消息直入 DLQ、禁 requeue 循环
  - 验收：跨用户/重复投递/乱序三条集成测；重复消息第二次必须被 inbox 拒且**领域入口不被调用**（用带调用计数的 fake 断言，沿 N9 domain 测手法）；变异自证 `AND`⇒`OR` 级真实破坏
- [ ] **R6 · 把三道门接上生产者（§一 那三个缺口的正解）**（4d，依赖 R4+R5）｜缺口逐字：`tasks.md:553`、`:581`、`:600`①、`:600`②
  - [ ] `AdmitEvidence` 接线：Python 抽取产物经 candidate 回投后写 evidence，并**补 `evidence.accepted` 事件类型与路由**（N7 三面欠账①：`14:68` 列了这个 routing key 但 Go 没有对应事件；加事件＝改 N4 语义，所以必须在这一批一起判）
  - [ ] `AdmitProactive`/`RecheckProactive` 触发面：cron 从「Python 直发」改为「Go 门判定 + 下发 `ai.proactive_draft.requested`」，Python 只出草稿（现役 `cron_service.py:168-188/287/320` 的 inline + QStash 形态在此退役，与 E3 一并判，不留两套队列——D-7）
  - [ ] `RecordOutcome`/`RecordUserFeedback`/`PropagateEvidenceInvalidation` 调用方：反馈走 Go 的 HTTP 面（前端改调归 N8），源失效传播由 Go 自发事件，Python 不参与判定
  - [ ] **现网回填**：Python 把 legacy `memories/journey_events/growth_records` 的素材写进 `source_records`/`evidence`（一次性 backfill worker，幂等、可重跑、可按 user 限流）——这是 O2 里那条「现网数据面回填」的落点
  - 验收（这条是**用户可见效果的 DoD**，不是「代码写完」）：N9 端点 `GET /api/v1/personal-model/insights/{id}/evidence` 在真库上返回**非空依据**并给出实测样本；`withdrawn_count` 在源失效传播后正确递增；主动消息经 Go 门后频次/疲劳指标可观测。仍受 kill switch 与灰度 0% 保护，切流前不改现网
- [ ] **R7 · 双协议部署与运维面**（2d）｜V3 20、21 §Phase J，与 O7 合并执行不重复拆
  - [ ] k3s 清单加 `python-rpc` Service（ClusterIP，**不出 Ingress**：gRPC 只在集群内，对外仍只有 Go 的 HTTP/SSE）+ `rabbitmq` StatefulSet；mTLS/NetworkPolicy 与 `CLOSEN_*` 开关面
  - [ ] DLQ replay 工具 + 契约兼容测进 CI（V3 03 §20 / 04 §22 指标逐条对上）
  - 验收：`kubectl apply` 前只出 manifests（本机无 kubeconfig、共享集群需显式授权——既有口径）；两运行时 + broker 起来后一条端到端 smoke：请求 → outbox → MQ → Python → candidate → inbox → Go 门 → 读端点非空

## P. 迁移期并行与风险（只登记事实，不改本节范围）

**P1 · 提审窗口不受影响（结论：可并行）**：App Store 包只认 REST/SSE 契约与域名，N8 切流对客户端透明 ⇒ A/B 节提审主线与 Go 迁移**并行，无需延后提审**。前提＝N0 契约冻结先完成。
- [ ] ⚠️ **P2 · IAP/Apple 验签是唯一硬未知**：`iap_service.py`(716 行) 承载 App Store Server API v2 的 JWS 验签、订阅状态、退款与通知（`SUBSCRIBED/DID_RENEW/REFUND/CONSUMPTION_REQUEST`）。Go 有苹果官方 app-store-server-library 支持，但**本项目能否覆盖既有全部分支、sandbox 实机验证是否通过，我尚未验证**——N8 的 subscription 批次开工前必须先跑 PoC，验证前不要把 subscription 域排进切流日程。
- [ ] ⚠️ **P3 · 测试资产不可迁移**：后端 pytest 541 例是 Python 单测，Go 侧等价覆盖需重建（V3 21 §12 的测试矩阵可直接当 Go 测试清单用）。jest 851 例不用动——它测前端，正好充当契约回归哨兵（N8 的验收判据就靠它）。
  - ↳ 2026-10-14 实测计数更正（只更数字、不改本节结论）：前端 **888 passed / 35 套件**，后端 **612 passed + 1 既有红（#62）+ 11 skipped**（GO_BIN 上 PATH 口径）
- [ ] ⚠️ **P4 · 单人双语言的长期代价（登记为已知，不重新论证）**：两套 codegen（Protobuf + OpenAPI）、两套 CI、两套部署、两套依赖审计、两套安全评审。按你的分层执行，此项只作成本留痕。
- [ ] **P5 · 最大的排期陷阱：领域内核不要在 Python 补一遍**：N2–N6（evidence/pattern/insight/proactive/outcome/candidate gate）**直接在 Go 建**。若先给 Python 实现一版再迁，同一套业务状态机会写两遍，N8 切流时还要对齐两份实现——成本翻倍。上一轮 N 节（原「领域内核，Python 内实现」）的全部缺口清单已并入本节 N2–N6，不重复立项。
- [x] **P6 · 双语言 env 方言归一 + 缺 env 的可见性闸**（0.5d）｜✅ 2026-10-14 完成并过测试｜立项理由＝N4 末「env 口径坑」与 N7 末「pytest 基线口径坑」这两条欠账一直靠口头口径（「跑哪侧按哪侧给」）绕过，而双语言迁移期真正的风险面正是**取证手段本身在换栈时静默失效**：同一个 `TEST_DATABASE_URL` 要么让 SQLAlchemy 回落未安装的 psycopg2（假红）、要么让 pgx 当场 `invalid keyword/value`，要么让 8 条实库闸悄悄变成 skip 而总数不变。
  - [x] 一个变量喂两侧＝两侧各只有**一处**归一落点，且都只认「scheme 里的可选驱动段」而不是某一种前缀
    - ✅ Python 侧 `backend/core/pg_scheme.py`（`psycopg_url`/`libpq_url`）＋入口 `backend/tests/_pg_env.py::pg_test_url()`；Go 侧新包 `go-core/internal/dsn`（`Postgres()`），三处接线点（`cmd/go-api`、`tools/crosscheck`、`internal/store` 集成测）全部改走它，私有 `postgresURL()` 与 `strings.Replace(…, "+asyncpg"…)` 删除
  - [x] 缺 env 不许静默降级
    - ✅ `CLOSEN_REQUIRE_PG=1` 把 `pytest.skip` 改判 `pytest.fail`；空串等同没导；Go 侧建池点有 `filepath.WalkDir` 结构闸（绕过归一器即红，遍历失效也红）
  - [x] 反漂移闸与变异自证
    - ✅ `backend/tests/test_pg_env_dialect.py` **17 例**（含「禁第二处硬编码 scheme」「禁绕 helper 直读 env」「两侧必须落同一个库」「豁免表不许留死条目」）；`scripts/mutate_pg_scheme_gates.py` **19 条红闸全打红 / 2 条绿闸保持绿 / 0 异常**，判定档新增「**受害测试没跑起来不能算绿**」（口径升级登记在 N3 节末 ↳）
  - [x] 验收计数（本机 dev 库 `127.0.0.1:55432`，口令 env-only 从不回显）
    - ✅ A 裸 scheme：`1 failed / 658 passed / 3 skipped`；B 加 `CLOSEN_REQUIRE_PG=1`：**与 A 逐字同计数**；C 不导 env + 开关：**9 failed**，其中 8 条正是那 8 条实库闸（650+8=658 与 A 对得上）；Go 侧三种 scheme 各跑 `go test ./...` 均 **7 包 ok / 0 FAIL / 0 「not set」**；`gofmt -l .` 干净、`go vet ./...` 无输出。逐条展开在 N4 节末与 N7 节末
  - ↳ 同期结案：`#56` n2 变异的两条空转锚点（条数 10→**12**，复跑 12/12 打红、0 异常、4 文件逐字节复原）；N3 ②「sqlc mtime 缓存」对 n2 的适用性核查（n2 不跑 codegen ⇒ 无需补 `os.utime`）
  - ↳ **本批不接手**（发现但不顺手修，且未因本批变化）：`#57` pytest 跨文件共享 `"u1"` 库态的隔离 flake、`#62` `test_commitment.py::test_weekly_quota_defers_third_follow_up` 那条既有红——A/B/C 三种口径下它都仍是那一条原样红

## 依赖与顺序总览

```
A1(冲突先决) ─┐
A2 ───────────┼─→ Sprint 1 完成 → 提审
A3 ←依赖─ B2 表(journey_events 先建, Sprint1 D9) ─┘
B1 → B4;  B2 → F2;  C3 在途收尾可并行;  D1/D2/D4/D5 随时可插队;  E3 在部署前
N0(契约冻结+改判登记) ─→ N1(Go 骨架·auth 跨语言互认) ─→ N2(canonical schema) ─→ N3(Domain Gate/candidate) ─→ N8(97 端点按域切流) ─→ O6(FastAPI HTTP 退役)
N4(evidence 算分+insight 九关) / N5(proactive 资格门) / N6(outcome+版本化+删除传播) / N7(outbox-inbox) 依赖 N2-N3；N9(读路径回链)＝改判 L3
O0(AI 独立目录+边界闸·不依赖任何批，随时可做) ｜ O4(生成留痕 ✅) O5(PDF 留 Python) ｜ O1/O2 已由 D-9 改判拆进 §R
R0(通信面裁决) ─→ R1(.proto 源+双端 codegen；旧 O1，N2/N3 已定稿故今日解锁) ─→ { R2(Go gRPC client·D-8 fail-closed) ／ R3(Python gRPC server) ／ R4(AMQP Publisher+拓扑·4c8G 实测) }
                                          R4+R1 ─→ R5(candidate 回投+Go inbound consumer；旧 O2 的异步面、O3 的物理落点) ─→ R6(三道门接生产者+现网回填) ─→ R7(双协议部署·e2e smoke)
gRPC 只承担**同步**面，首批按实测调用点收窄为三个方法（`Embed`/`ClassifySafety`/`StreamChat`）；V3 06 §6 允许的 rerank 与 capability check 本仓 0 调用点故不入表；状态转移、批量派生与 Go→Python 的**任务下发命令**刻意留 MQ（否则作废 N7 的 outbox/inbox）
P2(IAP 的 Go 验签 PoC) 是 subscription 域切流前置；P5＝领域内核只在 Go 建一遍
N/O 不阻塞提审主线（P1）；D-1…D-8 拍板前不动代码
```

> 更新规则：完成打 [x] 并在行尾追加日期；改变范围时在对应节新增行，不重排既有任务编号（对齐"数量固定、就地编辑"的既有约定）。
