# KG-19 · 关键词渠道七格：交付与自证（2026-11-02）

口径本体不在本文，在 `docs/metrics-dictionary.md` 的「## 关键词渠道七格（KG-19）」那张表；
闸在 `backend/tests/test_keyword_funnel_doc.py`。本文只记这一批做了什么、实测多少、哪里有根因勘误。

对应任务：`docs/keyword-growth-task-board-2026-11-01.md` §3 KG-19；实现口径：
`docs/keyword-growth-implementation-plan-2026-11-01.md` §4.8。

## 1. 交付物

| 文件 | 内容 |
|---|---|
| `docs/metrics-dictionary.md`（末尾新增一节） | 9 列固定表头 × 7 行：`search_reach / store_page_view / install / first_value / first_evidence_insight / first_action / due_outcome` |
| `backend/tests/test_keyword_funnel_doc.py`（新建，8 条） | 文档闸：解析 markdown 表 + 回指服务端源码，逐条可打红 |

**不改核心七格表**：本任务要求「另起一张表」，所以核心表（`metrics_export.py` 那七个 `StepDef`）一列未动。
新表必须排在核心表**之后**且表头唯一，否则 `test_metrics_export.py` 的定位会被抢走——这条已经做成变异靶 K7。

## 2. 后四格指回服务端已有表（不是新造的口径）

| 格 | 指回 |
|---|---|
| `first_value` | `mood_checkins` + `growth_records`，引用 `backend/services/metrics_export.py:69-78` |
| `first_evidence_insight` | `insights` + `insight_evidence`，引用 `:79-88` |
| `first_action` | `personal_commitments`，引用 `:89-98` |
| `due_outcome` | `outcomes` |

前三个区间不是手抄：闸用 `_step_def_line()` 在 `metrics_export.py` 里找 `key="<格子名>"` 那一行，
**引用区间必须真打到它**（`test_cited_line_ranges_reach_the_real_definition`）。这是为了挡「行号漂了还自称同源」。

前三格（`search_reach` / `store_page_view` / `install`）来自商店与站长后台，仓库里没有源：
表里明写只能控制台人工导出，`search_reach` 另标 KG-21 仍 `BLOCKED`。
`first_evidence_insight` 现在零流量，按 `no_users` 报，不按 0 报（0 会被读成「转化差」）。

## 3. 实测计数

| 项 | 实测 |
|---|---|
| `backend/tests/test_keyword_funnel_doc.py` | **8 passed** |
| 与 `backend/tests/test_metrics_export.py` 合跑 | **26 passed**（两张表共存、定位不串台） |
| 全量后端 `pytest backend/tests` | **1811 passed / 2 failed / 90 skipped**（本批前 1803，差值＝新增 8 条；2 条失败是本批之前就已登记的既有红灯：`test_canonical_schema.py` 的 canonical 引用、`test_r5_interop_broker.py` 要真库＋真 broker，与 KG-19 无关） |
| 变异 harness（K 族 9 条打在文档上） | **9/9 打红** |
| 绿闸 GK1（表上方加 12 行引用注释） | **保持绿** |

跑法与 KG-20 共用 `scripts/mutate_kg19_kg20_gates.py`；全量 **红闸 20/20、绿闸 3/3** 的合并计数见
`docs/growth/attribution-2026-11.md` §9。

## 4. 带根因的偏差与勘误

1. **首轮 2 条误红（我的判据太粗，不是文档有问题）**
   - 「实质列不许太短」原写成 `len(value) >= 6`，误伤 `商店页浏览`（5 字）这类标签列与 `GO+FE`。
     改法：长度闸只对 `SUBSTANTIVE_COLUMNS`（定义/来源/分群/查询入口/数据质量检查）生效且阈值 ≥10；
     「窗口」单独立一条 `WINDOW_PATTERN` 判据——它要的是**时间边界存在**，不是字数够。
   - 表名 needle 原来只在「查询入口」一格里找，`insight_evidence` 写在「来源」格就漏判。
     改法：合并「来源 + 查询入口」两格再查，且 needle 带反引号前缀（`` `insights` ``），
     防止 `GET /api/v1/insights` 这种端点串冒充表名过关。

2. **既有陈旧引用不顺手修**：`docs/metrics-dictionary.md:36` 里原有的 `frontend/src/types/index.ts:358`
   经核验已漂到现在 `:420`。这是本批之前就存在的文档行号漂移，按「发现的缺陷归位、不顺手改」只在此登记，
   本批只保证**新表**的引用区间由闸自证；核心表那行的修不修属文档维护面，另议。

3. **仍欠的读数**：`search_reach` / `store_page_view` / `install` 三格在本机没有任何自动化来源，
   KG-21（商店数据接入）仍 `BLOCKED`。所以这张表现在是**口径就位、数据待接**，
   不许被引用成「漏斗已有读数」。

## 5. 回滚

删 `backend/tests/test_keyword_funnel_doc.py` 与 `docs/metrics-dictionary.md` 末尾那一节即回到本批之前：
无服务端列变化、无契约变化、无前端变化。
