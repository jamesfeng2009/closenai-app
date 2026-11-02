# ADR 2026-11：关键词增长期的功能边界（KG-03）

- **状态**：`ACCEPTED`（代码面与文档面已落，等评审确认；见台账 `docs/keyword-growth-task-board-2026-11-01.md` §1 KG-03）
- **日期**：2026-11-02
- **决策者**：ARCH（主）＋ GROWTH、BE、FE、LEGAL（陪伴角色那条需法务与安全评审后才能重开）
- **覆盖范围**：只回答一个问题——**为了拿关键词流量，哪些功能做、哪些不做、什么条件下重开**。
  不改变任何已上线契约，不新增端点，不新增表（对齐方案决策 D1 与 D2）。
- **上位依据**：`CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md` §1.2「产品不是什么」
  （`CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md:84`）与 §3「功能优先级」的不做清单（`CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md:130`）。

---

## 1. 五个决定

| 功能 | 决定 | 重开条件 |
|---|---|---|
| 每日情绪记录 | 保留，并提升可见性（不新增采集维度，只把已有的连续与趋势露出来） | —（永久保留；若改采集口径需重新走本 ADR） |
| 三层次情绪分析 | 做小实验：同一个模型、三种深度、每条结论必须带可回查证据 | 4 周读数不达阈值即停（阈值见 `docs/growth/monthly-review-2026-12.md` 的 8 周停/改/扩规则） |
| 温柔陪伴 AI 角色 | 不做（含角色库、人设选择、浪漫向称呼与叙事） | 产品定位经 ARCH＋LEGAL＋安全三方评审改判后方可重开；ASO/商店文案不得先行试探 |
| 匿名树洞 / 社区 | 不做，改为「私人未寄出的信」研究（KG-29） | KG-29 的 5–10 人访谈证明「私密出口」这个需求无法用现有反思/信件满足，且过安全评审 |
| 金句卡 | 只做用户原话卡，不做 AI 金句库 | 不重开（句子必须回到用户自己的记录，这是 KG-30 的端点契约，不是文案偏好） |

---

## 2. 逐条依据

### 决定 1｜每日情绪记录：保留，提升可见性

- 定位依据：Baseline `CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md:130` §3 把功能优先级定义为「先做有证据的自我观察」这一族，情绪记录是这条链的第一段。
- 实现依据：`backend/routers/mood.py` 是已存在的签到/分析入口，`docs/feature-status.md:14` 的 WIRED 清单里
  `reflect.record`、`growth.history`、`insight.pattern` 三条都在，情绪签到是它们的输入源。
- 交付依据：可见性提升已由 KG-27 落地（`docs/growth/mood-stats-audit-2026-11.md`），KG-25 把首日价值路径接上
  （`docs/growth/first-value-path-2026-11.md`）。**所以本决定不需要新代码**，它只是拒绝「为了新关键词再采集一类数据」。
- 边界：不许把「每日情绪记录」包装成临床情绪监测；对外措辞受 KG-01 审计表约束
  （`docs/keyword-claims-audit-2026-11.md` 里 `frontend/src/i18n/locales/en.ts:402` 那条非医疗免责必须保留）。

### 决定 2｜三层次情绪分析：做小实验，必须带证据

- 为什么不直接砍：三层次（快记／常规／深潜）是同一个模型不同深度的**解读粒度**，不是新的数据来源，
  它给「情绪→洞察→行动」这条漏斗中段补一个可 A/B 的变量。KG-28 已按此交付代码面
  （`docs/growth/mood-analysis-2026-11.md`）。
- 为什么不直接开：Baseline `CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md:90` 已把产品排除在
  「对未来做确定性预测」之外，`CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md:164` 又把「没有稳定证据支持的预测性洞察」列为不做项，
  三层次若产出「你会怎样」就是那一类。所以本 ADR 的口径是**深度可变、证据不可省**：
  每条洞察要能回到用户原文，这条由决策 D4 与 `insight_evidence` 表承载，闸在
  `backend/tests/test_evidence_policy.py`。
- 开关与回滚：`backend/config.py:211` 的 `mood_analysis_depths_enabled: bool = False`，
  关时 `backend/routers/mood.py:83` 在任何业务判定之前返回 40402（不暴露功能存在）。
  重开＝在配置里打开这一行；回滚＝删掉配置里这一行，不删数据。
- 停的条件：4 周内深潜档的**证据点击率／回访率**不达 `docs/growth/monthly-review-2026-12.md` 写的阈值 ⇒ 停实验，
  读数仍留 `no_users` 口径，不许用「转化差」解释零流量（KG-19 表的第 5 格已钉这条）。

### 决定 3｜温柔陪伴 AI 角色：不做

- 定位依据：Baseline `CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md:91` 明写「不是以角色库、浪漫幻想或虚拟关系等级为主引擎的陪伴平台」；
  `CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md:84` 的「产品不是什么」是这一条的上位判据。
- 这与流量现实冲突：`AI companion` 是英语区搜索量大、转化路径最短的词之一。
  **本 ADR 明确拒绝用文案蹭这个词**——KG-01 审计表里所有 `companion`/`friend`/`Healer` 类句子判的是改写，
  KG-23 的禁语闸已经在扫 `AI companion`（`backend/tests/test_absolute_memory_claims.py:59`）。
- 残留的口子（登记，不在本批顺手修）：`backend/data/copy/en.json:287` 与 `backend/data/copy/en.json:294` 的对话开场白仍自称
  `your AI companion` / `your AI friend`，已按带日期勘误记入 `docs/keyword-claims-audit-2026-11.md` §4 第 2 条，
  负责人 BE＋CONTENT。
- 重开条件（写死，不许口头重开）：产品定位经 ARCH＋LEGAL＋安全三方书面评审改判 ⇒ 才允许立项角色库；
  在此之前，ASO 标题/副标题、商店截图、落地站都不得出现「陪伴角色」叙事。

### 决定 4｜匿名树洞 / 社区：不做，改为私人信件研究

- 定位依据：Baseline `CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md:168` 把「仅为了增加功能数量而建设复杂的社区、社交关系或公开分享体系」列为不做项。
- 代码事实：仓库里没有任何社区面——`backend/routers/` 与 `frontend/src/screens/` 全量检索
  `community` / `tree_hole` / 树洞，命中 0 个文件（本 ADR 不假装做过这个功能，也不假装需要下线它）。
- 替代路径：真正的需求是「把话说出去但不用发给任何人」，这是 KG-29 的「未寄出的信」研究方向
  （`docs/research/unsent-letter-2026-12.md`），属私密单人写作，不产生任何社交关系面。
- 重开条件：KG-29 的 5–10 人真实访谈证明私密出口不满足需求，**且**过安全评审（匿名 UGC 的审核、
  未成年人、自伤内容三条都得有答案）。两者缺一就不重开；访谈纪要不得伪造。

### 决定 5｜金句卡：只做用户原话卡

- 契约事实：`backend/routers/share.py:115` 的 `POST /api/v1/share/quote-card` 请求体只有
  `source_type` 与 `source_id`，句子一律由服务端从库里取——端点设计上就不是「任意文本 → 带推荐码的 PDF」。
- 为什么不做 AI 金句库：金句库的内容不是用户自己的话，一旦配上推荐码就变成**用我们的话替用户做承诺**，
  与 KG-01 审计表的整条判据方向相反，也撞 Baseline `CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md:168` 的「公开分享体系」那条不做项。
- 重开条件：不重开。若未来要出「官方文案卡」，那是一个新的产品面，须另写 ADR，不在本 ADR 里预留。

---

## 3. 后果

1. **放弃了两个大流量词族**（`AI companion`、`anonymous community`）。这是有意识的取舍：
   它们带来的安装会与 §1.2 的定位直接冲突，退款/差评/商店审核风险由 GROWTH 承担不了。
2. **本 ADR 不改契约**：无新端点、无新表、无新列（对齐决策 D1、D2）；
   台账 §KG-03 的验收只要求「五个决定各有理由和重开条件」。
3. **两条「不做」变成了可检查的句子**：`AI companion` 的禁语闸与「社区 0 命中」的检索证人
   都在 `backend/tests/test_keyword_scope_adr.py` 里，未来谁想蹭这个词得先让闸红一次并说明理由。
4. **一处遗留冲突交给 KG-02**：`frontend/src/i18n/locales/en.ts:120` 说「对话已加密」，而站点法务源说不是端到端加密；
   本 ADR 只登记，不拍板（见 `docs/keyword-claims-audit-2026-11.md` §3）。

---

## 4. 重开流程（谁、看什么、多久）

| 步骤 | 谁 | 输入 | 时限 |
|---|---|---|---|
| 提出重开 | 任何人 | 本 ADR 的对应「重开条件」原文 | 随时 |
| 证据评审 | ARCH＋GROWTH | 生产读数（不是本机 SQLite 样本）与 KG-22 的月度复盘 | 每月一次，随复盘会 |
| 定位改判 | ARCH＋LEGAL＋安全 | 书面结论，写明改判后的定位句 | 仅决定 3 需要 |
| 落地 | 对应模块负责人 | 新 ADR 或本 ADR 的修订记录（附日期） | 评审通过后一批内 |
