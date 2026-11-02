# 三层次情绪分析（KG-28）· 2026-11-02

第三批（方案 §6.2）。一句话交付面：**同一套数据、同一个 AI，按 `depth` 只换上下文窗口／输出结构／
token 上限**，轻松・探索・深潜三档共用一条端点 `POST /api/v1/mood/analysis`，由一个默认关闭的开关
`MOOD_ANALYSIS_DEPTHS_ENABLED` 控制；**每条结论都必须逐字引用用户自己写的原话**，引用没过校验就
整条撤掉（`pattern=null`、`safety_status="degraded"`），而不是换个说法继续说。

这一批的唯一对外承诺不是「分析得准」，而是**「指得回你自己写的话」**。所以下面所有的闸都钉在
校验与失败方向上，不钉生成质量。

## 1. 后端：判据的落点

| 面 | 位置 | 判据 |
| --- | --- | --- |
| 端点 | `backend/routers/mood.py:68` `analysis` | 开关判在 `require_ai_consent` **之前**（关着时回 40303 就泄露了「这里藏了个功能」） |
| 三档表 | `backend/services/mood_analysis_service.py:67-74` | 窗口 1/7/30 天、材料 3/8/20 段、最少独立来源 0/1/2 条、替代解释 0/1/2 条、max_tokens 120/300/600 |
| 调用顺序 | 同上 `:130-171` | 今日签到（40402）→ 证据池 → **危机筛查** → 幂等 → 配额（40301）→ 生成 → 逐字校验 |
| 证据池 | 同上 `_collect_materials` | 只收 `memory.source='self'` 且 `generation_run_id IS NULL`（`backend/repositories/memory_repository.py:164` `list_own_words`），反思排除 `source in ("draft","mood_analysis")` |
| 逐字校验 | 同上 `_match_quote:463` | 归一化（空白＋casefold）后必须是池内原文的子串，且长度 ≥`MIN_QUOTE_CHARS=8`；未命中 ⇒ 该条引用连同 pattern 一起丢 |
| 降级 | 同上 `:413-425` | `payload is None` 或（非 light 且无 pattern）⇒ `degraded`，summary 换成只陈述可核对事实的确定性模板（`copy` 的 `mood.analysis.degraded_summary`，按请求语言取） |
| 危机 | 同上 `_crisis_screen:231` | `safety_service.crisis_intercept`（确定性词表，零 LLM），命中即写 `safety_events(source="mood_analysis")` 并下发分地区热线，**不扣额度** |
| 配额 | `backend/services/quota_service.py:57` `MOOD_ANALYSIS_PURPOSES` | 独立第三池，事实源 `ai_generation_runs.purpose`（`_PURPOSE_PREFIX="services.mood_analysis."`，`mood_analysis_service.py:85`）；免费 1／Pro 20／Pro+ 50（`backend/config.py:205-207`）；`status=error` 不扣 |
| 幂等 | `backend/services/mood_analysis_service.py:91-92`＋`_cached:545` | `(user, request_id)` 进程内缓存 600 秒／256 条；生产 py-api `replicas: 1`（`deploy/k8s/40-py-api.yaml:11`）所以够用，多副本会重复扣（兜底＝前端请求期间禁用按钮） |
| 仪表盘 | `backend/services/growth_service.py:197` | `features.mood_analysis_depths` 逐字取 `settings.mood_analysis_depths_enabled`，前端入口只看这一格 |

## 2. 契约与路由面（这条端点留在 py-api）

- 契约计数：**118 router ops ＋ 1 app op ＝ 119 条登记行**；`contracts/openapi.json` 110 paths／119 operations；
  `contracts/endpoints.md` 119 行，其中新增那条是序号 73／文件 `:187`：`POST /api/v1/mood/analysis`，
  端点出处 `backend/routers/mood.py:68`，消费者列 `frontend/src/services/api.ts:475`
  （`analyzeMood` 在 `:474` 声明、`:475` 发请求，扫描器读的是后一行）。本批重跑过 `scripts/export_contract.py`。
- 冻结闸同步：`backend/tests/test_contract_freeze.py:115` 计数 117→118、`:118` 登记行 119，
  `:109` 记下改数的日期与原因。
- **不需要改 Ingress，也不需要 Go 注册**：`deploy/k8s/50-ingress.yaml` 里 mood 域切给 go-api 的是
  两条 **Exact**（`/mood/history`、`/mood/trend`），Exact 只搬那两条路径本身，新增端点自动落回
  `/api/v1` Prefix → py-api。这条判据由 `backend/tests/test_k8s_manifests.py:305`
  `test_mood_analysis_stays_on_py_api` 正向钉住（模拟最长前缀路由 ⇒ 赢家必须是 py-api；并断言
  不存在任何以 `/api/v1/mood` 开头的 Prefix 规则、`("POST", path)` 不在 Go 路由表里）。
  反例是 KG-26 踩过的 `/reflections` 前缀——那一批整域 Prefix，新端点会跟着搬去 Go 然后当场 404。
- Go 侧零改动：`scripts/gen_go_ai_side.py --check` 只镜像 `msg.fallback` 与 mood 标签，
  `mood.analysis.*` 这几格 copy 键不产生 Go 漂移。

## 3. 前端：入口、面板与三档参数

- 纯逻辑 `frontend/src/services/moodAnalysis.ts`（新）：`DEPTH_SPECS` 三档表、`showMoodAnalysisEntry`
  （`=== true`，旧本地缓存没有 `features` ⇒ 不显示）、四码→答复映射、`quotaRemaining`、
  `correctionContent`（**不裁**文案自带的冒号分隔符）、埋点组包（只出白名单键）。
- 面板 `frontend/src/components/MoodAnalysisSheet.tsx`（新，`PromptLibrarySheet` 同形）：三档选择、
  focus 输入、结论／替代解释／证据卡（就地展开）、降级说明、额度读数、「这不像我」→存为反思、
  「存为反思」直接存 summary。**危机那条路径不挂保存与纠正**（把热线文案存成一条「用户自己的反思」
  是这条产品线最难看的那种错）。
- 入口 `frontend/src/screens/HomeScreen.tsx:685`：`showMoodAnalysisEntry(dashboard?.features) && !!dashboard?.today_mood`
  两个条件缺一不可；面板挂在 `:800`，`onOpenSettings`→`Tabs/Profile`（40303 时跳出，不在面板里套第二层 Modal）、
  `onOpenMemories`→`Memories`。
- 上送封装 `frontend/src/services/api.ts`：新增 `analyzeMood(body)`（路径字面量紧贴 `request<>`，
  契约扫描器读得到）；`saveReflection` 第三参放宽为 `PromptTraceability | ReflectionOrigin | null`
  （`ReflectionOrigin = { source: 'draft' | 'mood_analysis' }`）。
- 文案：15 语言 × 30 键（`frontend/src/i18n/locales/*.ts` 顶层 `moodAnalysis` 段），由两个一次性脚本
  追加——`scripts/add_mood_analysis_i18n.py`（29 键）与 `scripts/add_mood_analysis_quota_key_i18n.py`
  （第 30 键 `quotaDone`）。后端 copy 面另加 `mood.analysis.degraded_summary` 一格 × 15 份
  `backend/data/copy/*.json`。

## 4. 测试（实测计数）

| 跑法 | 实测 |
| --- | --- |
| `backend/tests/test_mood_analysis.py` | **30 passed** |
| 合同／清单／仪表盘三张闸（`test_contract_freeze` + `test_contract_ledger_truth` + `test_k8s_manifests` + `test_growth_service`） | 与本批新闸合跑 **82 passed, 1 skipped** |
| 后端全量 `pytest backend/tests -q` | **1803 passed, 2 failed, 90 skipped**（2 条红灯见 §6） |
| `frontend/__tests__/moodAnalysis.test.ts` | **49 passed**（8 个 describe：纯逻辑／三档表对齐后端／15×30 文案／契约字段名／面板源码扫描／HomeScreen 接线／封装路径／白名单双侧） |
| `frontend/__tests__/i18nLocales.test.ts` | **107 passed**（新段的键集、占位符、逐字英文、文字种类都在其判据内） |
| 前端全量 `npx jest --silent` | **1819 passed / 63 suites** |
| `npx tsc --noEmit` | **0 错误** |
| `go-core` `go test -p 1 ./...` | 全绿（本批零 Go 改动，跑的是回归面） |

`promptLibrary.test.ts` 有一条 KG-26 的闸逐字比 `saveReflection` 的第三参类型串，本批把那个联合类型
扩了一档，因此把该断言改成 `PromptTraceability | ReflectionOrigin | null` 并写下改的理由——
它钉的是「可省 ＋ 展开进上送体」，不是联合类型的宽度。

## 5. 变异自证

`scripts/mutate_kg28_gates.py`（串行；`copy2` 备份写回＋`filecmp` 逐字节复核；**不用 `git checkout`**）。
每条变异只喂它该打红的那一张闸，并要求报错原文里出现该用例的名片段，否则不算「打红」。

实测：**红族 23 条全部打红（23/23），绿族 2 条全部保持绿（0 误红）**，基线三条先跑齐（30 passed／
49 passed+1 skipped／49 passed）。

- P1 深潜档「最少 2 条独立来源」改成 0 ⇒ `test_deep_needs_two_independent_sources_not_two_quotes` 红
- P2 证据池只排 `draft` 不排 `mood_analysis`（自我引用回来）⇒ 后端两条红（行为闸＋跨文件同表闸）
- P3 `_PURPOSE_PREFIX` 打错一个字母（配额永远数不到 ⇒ 免费档不限次）⇒ purpose 同表闸红
- P4 路由层同意判定挪到开关之前 ⇒ `test_http_returns_40402_while_the_flag_is_off` 红
- P5 配额整块挪到危机筛查之前 ⇒ `test_crisis_is_checked_before_the_quota` 红
- P6 引用长度下限 8→1 ⇒ 短引用闸红
- P7 逐字未命中也硬指一条原文 ⇒ `test_paraphrase_quote_drops_the_pattern_entirely` 红
- P8／P8b 新增一条没登台账的端点 ⇒ 计数冻结闸**与**台账逐行重扫真值闸各自红（两个证人）
- P9 把 mood 域整段 Prefix 切给 go-api ⇒ `test_mood_analysis_stays_on_py_api` 红
- P10 仪表盘 `features` 写死 True ⇒ 开关镜像闸红
- F1–F12 前端：三档表漂移、入口判据放宽、分隔符被裁、额度文案借键、危机不再挡保存、来源标记丢失、
  正文塞进埋点、封装路径打错、入口少一个条件、文案天数与参数不一致、`quotaDone` 键删一格、
  zh 冒号丢失 ⇒ 各自红
- G1／G2 正向证人：服务源码与前端纯逻辑各加 12 行注释 ⇒ 两套判据照旧全绿

## 6. 两个既有红灯（不是本批造成的）

- `backend/tests/test_canonical_schema.py::test_python_reaches_canonical_models_only_through_deletion`
   offender＝`backend/services/metrics_export.py`（本批没碰这个文件）。
- `backend/tests/test_r5_interop_broker.py::test_case_plan_fully_ran`  六条跨进程用例一条没跑到
  （要真库＋真 broker 的 env；本机没起）。

## 7. 与方案 §6.2 不一致的偏差（带日期的根因勘误）

1. **2026-11-02 请求体没有 `checkin_id`**（§6.2.1 列了这条）。入口分析的永远是「今天的签到」，
   接受任意 id 就得加归属校验并另配一套证据窗口，而没有任何调用方会传别的值——
   加一个没人验证的入参等于给契约加一条空判据。判据写进 `backend/models/schemas.py:131` 的类注释，
   并由 `moodAnalysis.test.ts`「请求体刻意没有 checkin_id」钉住（防止后来人把它当遗漏补上）。
2. **2026-11-02 证据源只有 `memory` 与 `reflection`，没有 `mood_checkin`**（§6.2.3 期望三类）。
   `mood_checkins` 表**没有备注列**（用户签到时打的那句话实际沉淀成一条 preference 记忆），
   签到行能提供的只有 emoji，而 emoji 不是可以逐字引用的原话。
3. **2026-11-02 危机检测走 `crisis_intercept`，不走 `check_text_tri`**。后者在 mock LLM 下 fail-closed，
   而这条路径的判据是「零 LLM、零额外调用、命中即出」；确定性词表已经能给出 `kind` 与命中词，
   够写 `safety_events`。副作用：危机词表**按语言分列**（`CRISIS_TRIGGERS[kind][lang]`），
   测试里 focus 的语言必须与 `language` 参数一致，否则整条危机路径静默不触发。
4. **2026-11-02 开关的 env 名是裸 `MOOD_ANALYSIS_DEPTHS_ENABLED`**（方案里写过 `CLOSEN_` 前缀）。
   `backend/config.py:274` 的 `model_config` 只有 `env_file`，没有 `env_prefix`；
   项目里其余开关同样不带前缀，`.env.example` 也不列 `*_ENABLED` 这类实验开关——本批按既有约定，
   **没动 `deploy/k8s/` 的 ConfigMap**（默认关＝线上行为零变化，开量时才加那一行，回滚＝删那一行）。
5. **2026-11-02 方案里的 `insight_pipeline_enabled` 那格 features 没随本批上**：本批只加
   `mood_analysis_depths` 一个键（`GrowthFeatures`）。把没实现的第二把旋钮一起写进契约，
   就是 §5 里 P10 那种「前端显示入口而后端 40402」的成因。
6. **2026-11-02 证据出处走面板内就地展开，不做 route-param 深链**（§6.2.6 提过跳记忆页定位那条）。
   实测只有 `ShareCardScreen` 读 `route.params`，`MemoriesScreen` 不读 ⇒ 深链要新写一段读参逻辑，
   而收益只是少一次滚动。改成「就地展开服务端给的那几段 ＋ 一个『打开我的记忆列表』出口」，
   口径与 `InsightsScreen` 已有的证据展开一致。
7. **2026-11-02 「这不像我」在面板内存成反思，不回 `ReflectScreen` 预填**。预填要跨屏传草稿，
   而纠正正文本身就是用户的一句原话——存成一条带 `source='mood_analysis'` 标记的反思，
   下一轮它会被证据池排除（P2 那条判据），比留在输入框里更可核对。
8. **2026-11-02 `quota.used` 在 mock LLM 下恒为 0**：autouse 的 `mock_llm` 绕开真实生成路径，
   因此不写 `ai_generation_runs`。配额类用例改成直接种 `AiGenerationRun` 行——
   读数口径（按 purpose、`statuses=("ok","fallback")`、`error` 不扣）仍由
   `test_analysis_pool_is_independent_and_only_counts_analysis_purposes` 钉住。
9. **2026-11-02 记录但未修的两条相邻缺陷**（按「发现的缺陷不顺手修」的口径归桶）：
   ① `backend/services/mood_service.py:39-57` 有一段重复的 `MOOD_MAP` 定义；
   ② `backend/ai/safety.py` 的 `HOTLINES["fallback"]` 是中文串，对任何非中英语言都直出中文
   ——桶＝安全面文案，需要单开一批按语言给号码。

## 8. 还没验的（别把这份当上线证据）

1. **4 周读数没跑，也没法跑**。方案 §6.2.5 要的读数是「深潜档 7 日回访率 vs 对照」，
   分桶键＝`features.mood_analysis_depths`，实验臂＝`deep` 完成且证据≥2 条。
   这条判据需要**生产流量**，而当前 KG-31 仍是 `BLOCKED`（屏已到位、流量为零）。
   本节只算设计，不算读数；读数出来的那一格应写回本文件的新日期段。
2. **真机 UI 没验**：`MoodAnalysisSheet` 只在源码层与纯逻辑层判过；RN 的 Modal＋ScrollView 滚动
   （`flexShrink` 那格）与 15 语言的长文案换行需要一次真机走查。
3. **开关从未在生产打开过**：`MOOD_ANALYSIS_DEPTHS_ENABLED=1` 只在测试 fixture 里翻过。
4. **幂等在多副本下不成立**：进程内缓存。今天 py-api 是 1 副本所以够用，扩副本那天这条判据要重读
   （`test_same_request_id_does_not_run_the_model_twice` 只证单进程形状）。
5. **分地区热线只有三类**：`Asia/Shanghai`→12356、`America/*`→988、`Canada/*`→988.ca，
   其余落 fallback（见 §7 第 9 条②）。美国市场的关键词流量正是本功能的目标面，
   这一格建议在放量前补齐逐国号码。
