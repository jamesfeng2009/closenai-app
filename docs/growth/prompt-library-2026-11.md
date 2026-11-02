# 反思提示库（KG-26）· 2026-11-01

> 台账：`docs/keyword-growth-task-board-2026-11-01.md` §KG-26。
> 方案：`docs/keyword-growth-implementation-plan-2026-11-01.md` §6.1。
> 这一份是**结案证据**：内容源与校验、端点与契约、写侧溯源、前端接线、测试结果、变异自证，
> 以及三处**与方案不一致的偏差**（都带根因，不是随手改）。

## 1. 内容源：一份 JSON，不留第二份

`backend/content/reflection_prompts.json`（唯一源，version `2026-11-1`）：

| 维度 | 实测 |
|---|---|
| 条数 | 30 |
| 簇 | `reflection_method` 8／`long_term_understanding` 8／`action_outcome` 7／`ai_credibility` 7 |
| 关联功能 | `reflect` 18／`goal` 7／`mood` 5 |
| 语言 | en＋zh 双语必填；其余 13 个语言**不在内容 JSON 里**（见 §5） |
| 每条字段 | `id`（`rp-001`… 稳定、不按顺序重排）、`cluster`、`function`、`title`、`question`、`why`、`follow_up` |

`backend/services/prompt_library.py` 加载即校验（`BANNED_PATTERNS:49`、`PromptLibraryError:63`）：
条数、逐簇计数、id 唯一、en/zh 非空、`function` 枚举、禁用词（医疗／诊断／绝对化承诺）。
校验失败是**启动即报错**，不是运行时降级——一条写着「治愈」「保证」的提示进了库，
前端就会把它当产品承诺念给用户。

## 2. 端点与契约

`GET /api/v1/reflection-prompts`（新文件 `backend/routers/reflection_prompts.py`）：

- 不认人。静态内容、无用户数据、不耗 LLM 配额 ⇒ 不查 AI 同意、不进配额门；
  测试 app **故意不覆盖** `get_current_user`，将来有人给它加 auth 依赖，
  `test_prompt_library.py::test_endpoint_is_readable_without_a_token` 会直接 401 而不是被盖掉。
- 语言由 `X-Language` 决定（`get_request_language`）：非 en/zh 回退 en，响应里显式给
  `language_used` + `fallback=true`，前端标「未翻译」而不是让用户自己猜。
- `?cluster=` 只收窄 `prompts`，`total`/`clusters` 仍是全库口径（徽章不能跟着缩水）；
  未登记的簇名返回空 `prompts`，不是 400。
- `route_class=CommitBeforeResponseRoute`（仓库约定：`backend/routers/*.py` 全部声明，
  由 `test_db_commit_boundary.py` 钉；这里那次 commit 是空操作）。

契约面（`scripts/export_contract.py` 重跑后）：`contracts/openapi.json` **108 paths / 117 operations**；
`contracts/endpoints.md` 登记 **117 行** ＝ 116 个 router 端点 ＋ 1 条 app 级（`GET /health`）。
`test_contract_freeze.py` 的计数从 `115 → 116`，并在变更说明里登记这一格。

## 3. 溯源只进写侧

`ReflectionCreate`（`backend/models/schemas.py:184-207`）新增 `prompt_id`／`prompt_version`／`source`，
外加一条空白正文 `field_validator`：只有空格的 `content` 过得了 `max_length`，却会真的写进
`growth_records`，然后进 `/growth/timeline` 的计数和周报的「你写了 N 条」。

`reflection_service.save`（`backend/services/reflection_service.py:122`）的判据：

- `prompt_id` 与 `prompt_version` 必须成对；提示必须存在；版本必须等于**当前库版本**；
  带 `prompt_id` 时 `source` 只能是 `prompt`。四种非法都在**写行之前**拦掉（40001），
  测试反过来证明 `growth_records` 里一行没多。
- 为什么版本不符要红而不是收下：`2026-11-1` 的那条文案和下一版同 id 的文案不是同一个问题，
  静默收下＝把两个版本的保存率混成一列，而这正是这个产品对外承诺「有证据」的那一类数字。
- 落库：`metadata_json = {"source","prompt_id","prompt_version","cluster","function"}`，
  簇名同时进 `tags`（成长记录按簇筛，与 metadata 同源，不各写一份口径）。

## 4. 前端

- 纯逻辑模块 `src/services/reflectionPrompts.ts`：`groupByCluster`（簇顺序跟服务端、
  未登记的簇单列不丢行、注册但为空的簇 tab 不塌）、`nextPrompt`（换一条，跳过空白，
  带 stale id 保护）、`saveableText`、`showsFallbackNotice`（只在「回退且回到的不是设备语言」时提示）、
  `traceabilityFor`。面板只做渲染，不算这些（有闸钉着）。
- `src/components/PromptLibrarySheet.tsx`：loading／失败+重试／空态三态齐全；每个可点控件都声明
  `accessibilityRole="button"`（闸按数量相等判）；簇 tab 用 `accessibilityState.selected` 表达选中态；
  取数只在首次打开，切簇走本地分组。
- `src/screens/ReflectScreen.tsx`：入口「需要提示？」、本地先拦空白再发请求、40001（版本过期）
  清掉选中并提示重选、换提示**不覆盖**用户已经动过的正文。
  本轮把两条保存出口（采纳草稿／照提示自己写）收成 `persistReflection`（`:402`），
  `first_reflection_saved` 的发射点因此从两处变成一处（`:409`）——
  `funnel.test.ts:198` 的「一个台阶只由一个文件调用」是判据，不是巧合。
- `api.ts`：`listReflectionPrompts(cluster?)` 写成**两个分支各一条字面量**，不拼 `${query}`——
  契约扫描（`scripts/export_contract.py::frontend_calls`）只认 `request<T>(` 后面紧跟的路径字面量并按 `?` 截断，
  拼出来的路径会在 `endpoints.md` 的悬空检查里挂红。
- 文案：15 个语言 × 13 个键（`reflect.prompt*`），由一次性脚本 `scripts/add_prompt_reflect_i18n.py`
  （带 run-once 锚点断言，跑第二遍会自己 assert 失败）插入 `saveDraft` 之后。
  德语按 `reflect` 块的 **非正式 "du"** 口径写（不是 `errors` 块的 "Sie"）；
  非 en/zh 语言在面板上标「未翻译」。
- 埋点：`prompt_library_open`／`prompt_selected`／`prompt_saved`，
  `feature='prompt_library'`，属性只用白名单里的 `feature/action/section/result`，**不传提示正文**。

## 5. 站点复用做成闸门，不做口头约定

方案 §6.1.5 的「禁止在站点里手写第二份 30 条」原来是散文。现在是可跑的闸：

- `scripts/export_prompts.py` 从同一个 JSON 渲染 `docs/content/drafts/journal-prompts.{en,zh}.md`；
  库校验不过就**拒绝产出**（不是产出半份）。
- `--check` 逐语言比对，漂移非零退出；`test_prompt_library.py::test_site_drafts_are_regenerated_from_the_library_file`
  跑脚本本体（不在测试里重抄渲染逻辑＝不留第二真源），并要求输出里出现每语言的正面证人
  `journal-prompts.<lang>.md 一致`——反向归零闸没有正向证人就会扫空集永远绿。

## 6. 三处与方案不一致（带根因的勘误）

**A. 端点路径（2026-11-01，本批）**
方案 §6.1.2 写 `GET /api/v1/reflections/prompts`，实交付 `GET /api/v1/reflection-prompts`，
并因此新建一个 router 文件而不是往 `reflection.py` 里加一条。

根因：Ingress 按**域**做 `pathType: Prefix` 切流，`reflections` 是 `PATHLESS_KEYS`
（`test_n8_batch2_contract.py`，「键先行、path 不上刀」唯一登记表）里的域。
写在它下面的一条静态内容路径，会在放量那一刀跟着整段搬去 Go，而 Go 侧没有内容面 ⇒
上线当天静默 404。这不是假想：两步变异实测（§7 第 2 行）——
改装饰器再重跑 `export_contract.py`，`test_k8s_manifests.py::test_cutover_keys_without_ingress_path_stay_unrouted`
当堂红（该文件 15 passed → 14 passed / 1 failed）。
**这条闸只在契约快照跟上后才会红**，所以路径改动必须连 `export_contract.py` 一起跑。

处理方式：不改闸、不加例外，把路径挪出切流前缀。前端调用点与后端路由声明**两侧各钉一条**
（`promptLibrary.test.ts` ②「提示库的路径不在 `/reflections` 前缀下」），任何一侧被「顺手归位」都会红。

**B. 读侧字段（2026-11-01，本批）**
方案 §6.1.3 要 `ReflectionResponse` 加 `prompt_id`／`source`，实交付**不加**——响应仍是
`id／content／related_memory_ids／created_at` 四格，前后端类型逐字段同序。

根因：`reflections` 的读面形状与 Go 的 `reflectionPayload` 字节对齐，由
`test_n8_batch5_contract.py::test_reflections_falsy_split_exact_type_and_limit_match_go`
（`:450` 的字段序比对）钉住。Python 单方面加两格＝给下一刀留一个「Go 没有这两格」的静默降级。
溯源目前按提示统计时**从 `growth_records` 的行里取**，不从 API 取。
归桶：N8 切流域——要读出提示来源，得 Go 侧同批改 `metadata_json` 解析＋重录字节夹具，那是另一票。

**C. `source` 的语义**
草稿采纳那条路保存时仍是 `manual`（草稿是 AI 生成的，不是照提示写的），
前端只有「照提示自己写并保存」带上 `prompt_id`/`prompt_version`/`source=prompt`。

## 7. 测试与变异自证

绿的实测计数：

| 跑什么 | 结果 |
|---|---|
| `.venv/bin/python -m pytest backend/tests/test_prompt_library.py backend/tests/test_reflection_prompt_save.py -q` | **46 passed**（32＋14） |
| 同批相关的既有闸：`test_n8_batch5_contract.py`／`test_k8s_manifests.py`／`test_contract_freeze.py` 合跑 | **83 passed, 1 skipped**（与上两文件合跑口径） |
| `cd frontend && npx jest promptLibrary --silent` | **38 passed** |
| `cd frontend && npx jest --silent` | **1732 passed / 61 suites** |
| `cd frontend && npx tsc --noEmit` | 无输出（0 错） |
| `.venv/bin/python -m pytest backend/tests -q` | 1754 passed / 90 skipped / **2 failed**（§8） |

变异自证（每组都「红的就是那条判据本身」；恢复用备份写回，不用 `git checkout`；不并发）：

| # | 靶 | 变异 | 实测红 |
|---|---|---|---|
| 1 | 站点草稿同步闸 `test_site_drafts_are_regenerated_from_the_library_file` | 改 `reflection_prompts.json` 里 rp-001 的一句 en 正文 | 后端 1 条红，报错逐语言指名 `journal-prompts.en.md 与 JSON 不一致`，同时给出 `zh 一致` 的正面证人 |
| 2 | N8 切流路径闸（**两步**） | `reflection_prompts.py` 的装饰器改回 `/reflections/prompts` | 第一步（只改源码）：前端 ② 的路径闸 1 条红，`test_k8s_manifests.py` 仍 15 passed——闸读契约快照，不读源码；第二步（重跑 `export_contract.py`）：`test_cutover_keys_without_ingress_path_stay_unrouted` 红，指名 `GET /api/v1/reflections/prompts 在 Go 的声明表里没有带方法的注册` |
| 3 | 响应形状：Go 字节对齐闸 | `ReflectionResponse` 加 `prompt_id` | `test_reflections_falsy_split_exact_type_and_limit_match_go` 1 条红（diff 右侧多一项 'prompt_id'） |
| 4 | 响应形状：前端不领先于后端 | `types/index.ts` 的 `ReflectionItem` 加 `prompt_id?` | `promptLibrary.test.ts` ② 的「响应形状两侧都不带溯源字段，且字段序一致」1 条红（`toEqual` 长度差 1） |
| 5 | 漏斗发射点单点 | 在 `onSaveDraft` 里再写一次 `trackFunnelOnce('first_reflection_saved')` | `funnel.test.ts:198`「每一个一次性台阶只由一个文件调用」＋ `promptLibrary.test.ts` ④ 收成一条 — 共 2 条红（94 跑／92 过） |
| 6 | 提示库版本闸 | 删掉 `reflection_service.save` 里的版本比较 | `test_bad_traceability_is_rejected_before_any_row_is_written[提示库版本已更新]`＋`test_endpoint_returns_40001_wrapper_for_stale_prompt_version` 2 条红 |
| 7 | 空白正文闸 | 删掉 `ReflectionCreate._content_not_blank` 的 raise | `test_blank_content_is_rejected_at_the_schema_edge`＋`test_endpoint_rejects_blank_with_40001_and_writes_nothing` 2 条红 |
| 8 | 内容校验（禁用词） | 往 rp-001 的 zh 标题塞「保证治愈」 | 12 条红（加载即 `PromptLibraryError`，端点与草稿闸全灭）——证明这条闸不是只红一处，而是**内容不合格时整面墙都不给过** |

另外两条在本批开发过程中**自然抓到过**（不是补跑的仪式）：
`reflectionPrompts.ts` 里写死 `rp-001` 被「库里不许出现硬编码提示 id」闸打红（改 `'probe'` 占位后绿）；
`api.ts` 拼 `${query}` 被契约扫描当悬空路径打红（拆成两条字面量后绿）。

## 8. 两个既有红灯（不是本批造成的）

- `backend/tests/test_canonical_schema.py::test_python_reaches_canonical_models_only_through_deletion`
  ——`backend/services/metrics_export.py` 相对 HEAD **未改**，且该闸文件在本次会话开始时的工作区快照里
  就已经是 modified；与本批无关。
- `backend/tests/test_r5_interop_broker.py::test_case_plan_fully_ran`
  ——跨进程 Go 用例在本机从未跑过（属 R 系列取证缺口，见 `docs` 里的取证能力缺口记录）。

这两条按「发现的缺陷不顺手修」登记，不并入本批结案。

## 9. 还没验的（别把这份当上线证据）

- **真机截图没有**。本机 jest 环境渲染不了 `.tsx`，屏与组件的闸全是**源码扫描＋纯逻辑单测**，
  面板三态、入口可达、保存成功/失败的真机观感要在 Expo 设备上补。
- 13 个非 en/zh 语言**没有正文**，走 en 回退并在面板标「未翻译」；这是设计不是缺陷，
  但对 `journal prompts` 这类西语长尾搜索词，落地页文案要另外补（挂 KG-12/KG-13 桶）。
- 「哪条提示带来保存」的**读数还没有**：事件已发（`prompt_saved` 带 `section`＝簇名），
  metadata 已落库，但本机没有生产流量，转化率要在 PostHog ＋ canonical 表上接完才有数。
- 站点草稿目前只落在 `docs/content/drafts/`，还没接进 `web/` 构建产出（属 KG-12/KG-13）。

## 10. 顺手发现、按规矩没动的缺陷

`frontend/src/i18n/locales/en.ts:343`（`reflect.quotaMessage`）、`:829`（`faqQuotaA`）、`:831`（`faqSubscriptionA`）
仍写 **"unlimited conversations"**，而 `backend/services/quota_service.py:33-38` 给
`pro`／`pro_plus` 设了 `pro_daily_messages`／`pro_daily_chat_rounds` ——付费档**仍受每日上限**，这句不实。

- 根因：KG-24 的「价目表逐条改实」只覆盖了价目那一屏的键，`reflect`／`settings` 里的付费话术没进同一张核对表。
- 归桶：**KG-24 复评**（状态 `REVIEW`，可回炉），不并入 KG-26 结案。
- 顺带一句结构性缺口：全站文案没有 `unlimited` 禁词闸——本批那条禁词闸只钉新加的 13 个键
  （`promptLibrary.test.ts` ③）。要收口应在 KG-24 复评时把闸扩到整份 locale ＋ `backend/data/copy/*.json`，
  而不是逐文件手改。
