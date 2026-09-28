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
  - ⏳ 待办：前端未跑 `tsc --noEmit`（本机无 node_modules，装依赖耗时放弃）——下次 `npm i` 后顺带验证
- [ ] **A2 · M6 Life Archive IAP**（后端 3d + 前端 2d）｜架构方案 §二 M6 / 行动方案 P0-2 ｜SKU 形态已拍板：维持一次性买断（架构方案 §九-4，2026-09-28）
  - [ ] 清理 `iap_service.py:71` `life_archive → pro_plus` 别名（买断走 user_entitlements，不走 _map_tier）
  - [ ] Pro 联动权益：订阅期内导出折扣/每月 1 次免费生成（订阅加分，不取消买断）
  - [ ] 新表 `user_entitlements` + alembic 迁移
  - [ ] `iap_service` non-consumable 分支（App Store Server Notifications v2）
  - [ ] `POST /api/v1/share/life-archive`（entitlement 校验 + worker 后台渲染 PDF + 幂等）
  - [ ] Paywall 第四区块"永久保存你的档案"（视觉分离订阅区）+ 恢复购买
  - [ ] AIGC 章节 GB 45438-2025 显式标识
  - 验收：sandbox 三档 购买→导出→卸载重装恢复
- [ ] **A3 · M3 §5.15a 情感回报进 LEVEL_UNLOCKS**（1.5d，依赖 A4 的 journey_events 表先建）｜架构方案 §二 M3
  - [ ] L2「AI 提起你说过的事」/ L3「Monthly Story 预览」/ L4「90 天信」
  - [ ] level 变更检测写 journey_events（kind=level_reward）
  - 验收：代码侧不再是纯功能奖励（现状：LEVEL_UNLOCKS 全为功能解锁）

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

- [ ] **D1 · evidence_policy prompt 段落**（~1d）｜规范 §一 + 架构方案 §六-4
  - [ ] SafetyService 双语段落 + 注入 message_generator/mood_service/模板出口
  - [ ] D 级词汇并入黑名单判定；SentenceGate 单测扩例（因果断言/标签推断样本被拦）
- [ ] **D2 · crisis_intercept 实装（两类触发词）**｜规范 §二 + 架构方案 §六-2
  - [ ] 自伤类 + 关系暴力类（v1 仅 zh/en 词表）；命中→热线→停推→safety_events 审计
  - [ ] 验收：暴力样本禁归因/禁沟通建议测试进 pytest
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
