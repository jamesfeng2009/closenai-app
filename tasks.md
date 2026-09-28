# ClosenAI 实施任务分解（tasks.md）

> 拆分依据：PRD v2.1（closenai-v2.1-clean.html，至 2026-09-28 第六批）· 《架构与技术方案-2026-09-06.md》· 《P0-P1行动方案-2026-09-06.md》· UI 原型 closenai-p0-prototype.html（至 2026-09-28 修订）· 《沟通证据与措辞规范-2026-09-28.md》
> 状态核验日期：2026-09-28（代码实测，非文档推断）
> 图例：`[ ]` 未开始 · `[~]` 进行中 · `[x]` 完成 · 🔴 提审阻塞 · ⚠️ 冲突/风险

---

## 进度快照（实测结论）

- Onboarding 文案改写（P0-1）：✅ 已完成（15 语言 i18n + 原型 Screen 7）
- 后端 M1–M7 / P1-3 管线：**全部零实现**（grep 无 journey_events / onboarding_profiles / bedtime / curriculum）
- 工作区在途（未提交）：九型人格 service、§5.18 承诺回访（follow_up 迁移+测试）、discovery 融合、前端 Reflect/Insights/i18n 改动
- ⚠️ **方向冲突**：近三次 commit（13313c3/efbd7ec/e89f19c）在**新增**本命星盘 + pyswisseph，而 M7 计划是整体移除占星——提审前必须二选一，默认按 PRD v2.1"占星已移除"执行 M7

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

- [ ] **B1 · M1 性格初读卡 B01**（后端 2d + 前端 2d + 文案 1d）｜架构方案 §二 M1
  - [ ] 表 `onboarding_profiles` + `first_read_service` + 静态 JSON 模板库（零 LLM）
  - [ ] `POST/GET /onboarding/first-read`、`/share/first-read-card`
  - [ ] OnboardingScreen 快问 3 题 + `FirstReadCardScreen`（UI 原型 Screen 29 已就绪）
- [ ] **B2 · M2 第一周剧本 B02**（后端 3d + 前端 1.5d + 文案 1d）｜架构方案 §二 M2
  - [ ] 表 `journey_events`（7 种 event_kind，事务内预写 pending）
  - [ ] `dispatch_journey_events` 挂 cron + 沉默降频
  - [ ] D7《第一周观察报告》= memory 原话引用 + 周统计（v1 纯模板）
  - [ ] **引用依据展示**：payload.memory_refs + CI 归属断言 + 前端 ChatBubble"记忆依据 ▾"（UI 原型 Screen 1 已加，前端未落地）
  - 验收：pytest 强制 memory_refs 真实存在且属该用户
- [ ] **B3 · M5 演示档案**（前端 2d，零后端）｜架构方案 §二 M5
  - [ ] `demoArchive.ts` 小 A 30 天 + `DemoArchiveScreen` + is_demo 隔离 + 演示模式禁 AI 对话入口
- [ ] **B4 · M4 分享卡体系 B03**（2d，依赖 B1）｜架构方案 §二 M4
  - [ ] ShareService 模板 first_read / memory_card / year_in_review 占位
  - [ ] 深链 closenai.app/r/{code} + AASA + CF Pages landing（决策点 3）

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
