# 用户原话金句卡（KG-30）· 2026-11-01

> 台账：`docs/keyword-growth-task-board-2026-11-01.md` §KG-30。
> 方案：`docs/keyword-growth-implementation-plan-2026-11-01.md` §6.3。
> 这一份是**结案证据**：授权边界、三种来源三种答、端点与契约、前端接线、三面文案、
> 测试结果、变异自证，以及五处**与方案不一致的偏差**（都带根因，不是随手改）。

## 1. 边界只有一条：卡上那句话只从库里取

`QuoteCardRequest`（`backend/models/schemas.py:629`）只有两个字段——
`source_type: Literal["memory", "reflection"]` 与 `source_id: str = Field(..., max_length=36)`，
**没有第三个位子放正文**。

为什么这条要做成判据而不是写成注释：端点一旦吃调用方传来的句子，它就不再是
「把你自己写的那句话做成卡」，而是一台**任意文本 → 品牌水印 + 二维码 + 本人推荐码 PDF**
的生成器。转发出去的那张卡带着本人的短链，而句子可能一个字都不是用户写的。

两侧各钉一条（不是一侧代钉）：

| 侧 | 闸 | 变异证据 |
|---|---|---|
| 收 | `test_quote_card.py::test_request_body_cannot_put_words_on_the_card`（往 body 里塞 `source_text`/`content`，再 `pypdf` 抽卡面文字，要求那句**不在** PDF 里） | B9：schema 加回 `source_text` ⇒ `test_schema_has_no_room_for_the_text` 红 |
| 发 | `api.ts` 的 body 只有两个键：`quoteCardWiring.test.ts` 「api.ts 的 body 只有 source_type / source_id 两个键」＋`pdfExportWiring.test.ts` 同口径 | Q4：body 里带上本地那句 ⇒ 该条红 |

屏上确实拿着那句话（`quote_text` 走 route 参数），但**只用于本地排版预览**：
`ShareCardScreen.tsx:90` 的 `if (variant === 'quote') return;` 在任何 `await` 之前退出取数
effect，金句分支的整段 JSX 里没有一次网络调用——预览不生成推荐码，
`export_quote_card_pdf` 里的 `_brand_for`（`share_service.py:1520`）才是惰性生成
`referral_code` 的那一处。**用户按「导出」是唯一的授权点。**

## 2. 三种来源三种答

| 来源 | 判据 | 结果 | 卡上措辞 |
|---|---|---|---|
| 自己写的记忆 | `memory_repository.get`（含 `deleted==False`）命中、`status=="current"`、`generation_run_id` 为空 | 出卡 | `quote.source_memory` + `quote.own_words` |
| AI 抽取的记忆 | `generation_run_id` 非空 | **40405** | 不出卡 |
| 记忆不存在／不是本人／软删／被取代 | `memory is None or status != "current"` | 40403 | 不出卡 |
| 自己保存的反思 | `growth_repository.get_owned`（`:53`）**三行 WHERE**：`id + user_id + record_type` | 出卡 | `quote.source_reflection` + `quote.own_words` |
| AI 草稿／情绪分析稿保存的反思 | `metadata_json.source in ("draft", "mood_analysis")` | 出卡但**改口径** | `quote.draft_note` |
| 别的域的成长记录 id（`daily_insight` 等） | `record_type` 不匹配 | 40402 | 不出卡 |
| 正文空白 | `(content or "").strip()` 为空 | 40402 | 不出卡 |
| `source_type` 取值在 Literal 之外 | Pydantic 拦 | 40001，且**在任何查询之前** | 不出卡 |

三条设计口径：

- **「别人的 id」与「不存在的 id」必须是同一个回答**（40403／40402 各自同文同码），
  否则这条端点变成探测器：拿一串 id 就能问出「谁有这条」。
  `test_other_users_reflection_and_an_unknown_id_answer_the_same` 逐字比两句 message。
- **`record_type` 进 WHERE 而不是取回再判**：`daily_insight`／`weekly_summary` 这些
  AI 产出的行与 `reflection` 住在同一张表里，靠调用方「记得再判一次」迟早会漏。
  变异 B3（拆掉这一行）当堂红。
- **草稿来源给的是看得见的披露，不是 PDF 的 AIGC 元数据标识**（`_aigc_metadata`）：
  metadata 只能标「整份是 AI 产出」，而草稿被改写过再保存的那一类会被误标；
  一行明写在卡上的话，转发出去之后仍然跟着这张卡。

## 3. 端点与契约

`POST /api/v1/share/quote-card`（`backend/routers/share.py:115`）→ `StreamingResponse`
＋ `Content-Disposition: attachment; filename="closenai_quote_card.pdf"`。

`scripts/export_contract.py` 重跑后：

| 面 | 实测 |
|---|---|
| `contracts/openapi.json` | **109 paths / 118 operations**（逐字节相等闸仍绿） |
| `contracts/endpoints.md` 登记行 | **118 行** ＝ 117 个 router 端点 ＋ 1 条 app 级（`GET /health`） |
| §7.1 PDF 表 | **6 行**（新增 `POST /api/v1/share/quote-card` → `closenai_quote_card.pdf`，出处逐字写行号） |
| §四 错误码表 | 40405 自动在册（该表由 `ErrorCode` 反推，HTTP 404） |
| §七 Content-Disposition | 由 `backend/routers/share.py` 的字面量自动登记，不需手写 |
| 前端列 | 扫描 `api.ts` 的 `request<ArrayBuffer>(` 调用点，命中 `src/services/api.ts:1020` |

`test_contract_freeze.py:106` 登记了这一格变更（`116 → 117`）。
`share` **不是** N8 切流域 ⇒ 新子路径不需要 Go 声明表登记，这一点与 KG-26 那次踩坑相反
（那条的根因是 `reflections` 在 `PATHLESS_KEYS` 里、Prefix 会把子路径带走，
见 `docs/growth/prompt-library-2026-11.md` §6A）；本批不动 Ingress，也不动 Go。

## 4. 280 这个数两侧同值，且裁法同形

后端 `share_service.py:487` `QUOTE_MAX_CHARS = 280`，前端 `config/pdfFiles.ts:36`
`QUOTE_CARD_MAX_CHARS = 280`；两侧的裁法都是「trim → 取前 279 → 再去尾空白 → 加 `…`」，
所以超长时不留半个字。闸是**跨仓取值**：`pdfExportWiring.test.ts` 用正则从
`share_service.py` 读出那个数字再与前端常量比（不是「拿前端比前端的注释」），
变异 Q6 把它改成 120 当堂红。

为什么值得单独钉：这是本批唯一「两个仓各自算一遍同一个数」的地方，
不一致的观感是**预览一句、导出的卡是另一句**——用户会以为导出功能篡改了他的话。

## 5. 前端接线：两个入口、一个卡页分支

- 类型：`ShareCardVariant` 加 `'quote'`（`src/types/index.ts`）、新增
  `QuoteSourceType = 'memory' | 'reflection'`，与后端 Literal **同集合**（跨仓闸比的是
  `schemas.py` 里那行字面量）。
- 路由参数：`App.tsx` 的 `ShareCard` 参数表加四个可选键（`source_type?/source_id?/quote_text?/quote_date?`），
  仍是可选 ⇒ 旧调用点不传参照样进名片。
- 入口两处，各只有一处（闸按计数钉）：
  - `MemoriesScreen.tsx:122` 详情弹层的「分享这句话」→ `source_type: 'memory'`；
  - `ExploreScreen.tsx:209-219`「我的反思」每一行 → `source_type: 'reflection'`，
    且 `{!!r.summary?.trim() && (` 让**空正文那条根本不长按钮**（点下去只会拿到一句「这句是空的」）。
  - 不新增列表页（方案 §6.3.4 同口径）。
- 卡页 `variant === 'quote'` 分支：卡面＝引导句 + 引号包边的那句 + 日期 + ClosenAI 水印；
  清单两项＝`share.quoteManifestQuote` + `share.manifestLink`；清单之后一行披露
  `share.quoteNote`（「按导出之前什么都不会被分享／卡上带你的推荐链接／已存进设备的卡收不回来」）。
- 导出：`ShareCardScreen.tsx:193` `onExportQuotePdf`——闸门
  `if (!params?.source_type || !params.source_id || pdfBusy || sharing) return;`、
  `disabled={!canExportQuote || pdfBusy || sharing}`、`finally` 复位，
  两拍埋点 `trackFunnelShare('share_tapped'|'share_completed', 'quote')`，
  成功判据是 `await exportPdf(bytes, QUOTE_CARD_PDF_FILE)`（不是 `Share.share`，见 §6D）。
- 失败：走屏内 `error`，不弹裸 `e.message`，不开新的 Alert 站点（本页 `Alert.alert` 站点数
  仍由 `pdfExportWiring`/`trustPresentation` 盯在一处）；40405 念自己那句（§6A）。
- 漏斗：`FUNNEL_SOURCES` 加 `'quote'`（`src/services/funnel.ts:55`），
  `funnel.test.ts:77-79` 的精确数组同步为 7 项。

## 6. 与方案不一致的偏差（带根因的勘误）

**A. AI 抽取的记忆给 40405，不给方案写的 40403（2026-11-01，本批）**
方案 §6.3.2 的表格让这种情况回 40403。实交付新码
`QUOTE_SOURCE_NOT_ORIGINAL = 40405`（`backend/core/errors.py:64`，HTTP 404）。

根因：40403 的既有文案是「这条记忆已经不在了，卡片无法导出」，而这种情况**记忆好好地在那儿**，
念它就是对用户说谎。前端要能分辨「这句不是你的原话」和「我点错了/它没了」，
所以新码新文案（`pdf.failed.notOriginal` × 15 语言）。
代价：契约 §四 由 `ErrorCode` 自动反推，新码免手写登记表；`pdfFiles.ts` 的镜像常量与
`pdfFailureKey` 分支各有闸（变异 Q5 拆掉分支 ⇒ `pdfExportWiring` 的「四个码各映射到自己的文案键」红）。

**B. 反思草稿（含情绪分析稿）出卡但改措辞，不像方案那样 40402 拒掉**
方案 §6.3.2 让 `mood_analysis` 一律 40402。实交付：`record_type == "reflection"` 且本人的行
都出卡，但来源是 `draft`／`mood_analysis` 时印 `quote.draft_note`
（「这句出自 AI 起草、你按下保存的反思，措辞未必全是你自己的」）。

根因：方案的 40402 那一栏真正要挡的是**不是 reflection 的那一类行**（`daily_insight` 等），
这个仍然挡着（靠 WHERE 里的 `record_type`）。而「AI 起草、用户按了保存」的正文已经是
用户自己时间线上的反思记录，再拒一次等于**替用户决定他保存的东西不算数**；
改成看得见的披露既守住「不冒充原创」，又不撤回用户的保存动作。
变异 B6（`draft = False`）⇒ 两条披露用例红。

**C. 文案加满 15 个语言，不做方案允许的回退**
方案 §6.3.2 结尾说「不加 15 语言也可以，PDF 文案走用户语言回退」。实交付
`backend/data/copy/*.json` 15 份 × `share.quote` 6 键全加。

根因：`test_copy_library.py` 的键集闸逐文件比对 15 份 JSON 的键集合与 `{placeholder}` 集合，
**缺一份就红**；留回退就要同时给这条闸开例外，得不偿失。
前端同理：15 份 locale × `share.quote*` 6 键 ＋ `pdf.failed.notOriginal`（`pdf.*` 现 8 键），
由 `quoteCardWiring` ⑦/`pdfExportWiring` ⑦/`i18nLocales.test.ts` 三面钉。
三面各由一次性脚本落盘（`scripts/add_quote_copy.py`／`add_quote_i18n.py`／
`add_quote_pdf_key_i18n.py`，都带 run-once 锚点断言与「15 份都在册」的正面证人）。
Go 侧无漂移：`scripts/gen_go_ai_side.py --check` 只抄 `msg.fallback` 与 mood 键，
`test_r2_ai_side_copies` 仍绿。

**D. 金句卡不进 `funnel.test.ts` 的 `shareSites` 那张表**
方案 §6.3.4 让更新「`:263` 的 source 列表」。实交付只改了 `:77-79` 的 `FUNNEL_SOURCES` 精确数组。

根因：`shareSites`（`:250-258`）钉的是**文本分享**那几处，它的邻接断言要求
`share_completed` 前 500 字符内出现 `await Share.share(`；金句卡的「分享出去」是
PDF 落盘 + 系统面板（`exportPdf` → `Sharing.shareAsync`），把这条塞进那张表会**为了一条
不存在的调用而造假**，或者逼我把闸改松。所以金句卡的两拍埋点单由 `quoteCardWiring`
钉在 `await exportPdf(bytes, QUOTE_CARD_PDF_FILE)` 的邻接上。「全仓 step/source 不越枚举」
那道闸仍把 `'quote'` 收在合法集合里。

**E. 前端不做「这条是 AI 抽取的，所以不给按钮」的预筛**
入口一律给，能不能出卡由服务端答。

根因（两条都实测过）：① `generation_run_id` **不在** `MemoryResponse` 里——
`test_n8_batch6_contract.py:295-297` 逐字节钉着那张响应的字段集与 Go 的 `memoryPayload` 同序，
Python 单方面加一格＝给下一刀留静默降级；② 详情里的 `detail.source` 是 LLM 打的标签，
不是溯源位，拿它当「谁写的」的判据会造出第二真源。
所以 MemoriesScreen 的入口注释里明写这两条，40405 由服务端说了算。
归桶：要让前端能预筛，得 Go 侧同批改 `MemoryResponse` ＋重录字节夹具，那是另一票。

## 7. 测试与变异自证

绿的实测计数：

| 跑什么 | 结果 |
|---|---|
| `pytest backend/tests/test_quote_card.py -q` | **16 passed** |
| `pytest backend/tests/test_quote_card.py backend/tests/test_copy_library.py backend/tests/test_contract_freeze.py -q` | **34 passed**（16＋8＋10） |
| `npx jest quoteCardWiring --silent` | **34 passed** |
| `npx jest pdfExportWiring funnel --silent`（改闸后回归） | **102 passed**（46＋56） |
| `cd frontend && npx jest --silent` | **1770 passed / 62 suites**（本批前 1732/61） |
| `cd frontend && npx tsc --noEmit` | 无输出（0 错） |
| `.venv/bin/python -m pytest backend/tests -q` | **1770 passed / 90 skipped / 2 failed**（§8；本批前 1754 passed） |
| `npx jest memoryCardWiring --silent` | 绿（四道字面量闸随 `ShareCardVariant`/`VARIANTS`/参数表/默认变体同步改，各带日期注） |

变异自证：`scripts/mutate_quote_card_gates.py`，**基线先跑**（后端 16 passed、前端三文件 136 passed），
随后 24 条逐条串行跑，每条「写坏 → 只跑受害那份测试 → 要求输出里出现那条判据的名字 → 备份写回」，
恢复用 `copy2 + filecmp` 逐字节校验，**不用 `git checkout`**、**不并发**
（见 [[feedback-mutation-restore-no-git-checkout]]、[[feedback-mutation-gates-no-concurrency]]）。

合计 **红闸 22/22、绿闸 2/2**，跑完 `git status` 无残留变异。

| # | 靶 | 变异 | 受害闸 |
|---|---|---|---|
| B1 | AI 抽取的记忆不出卡 | `if memory.generation_run_id:` → `if False:` | `test_ai_extracted_memory_is_rejected_with_its_own_code` |
| B2 | 软删／被取代也出卡 | `if memory is None or memory.status != "current":` → 只判 `None` | `test_superseded_memory_is_40403` |
| B3 | 别的域当反思出卡 | `get_owned` 的 WHERE 拆掉 `record_type` | `test_daily_insight_id_is_not_a_reflection` |
| B4 | 越权读别人的反思 | 同一块 WHERE 拆掉 `user_id` | `test_other_users_reflection_and_an_unknown_id_answer_the_same` |
| B5 | 超长不再服务端裁 | `shown = text` | `test_over_long_line_is_truncated_by_the_server` |
| B6 | 草稿来源不改措辞 | `draft = False` | `test_ai_draft_reflection_keeps_the_card_but_changes_the_wording` |
| B7 | 落盘文件名漂 | `closenai_quote_card.pdf` → `closenai_quote.pdf` | `test_request_body_cannot_put_words_on_the_card`（逐字节比 header） |
| B8 | 文案键双命名空间 | `"quote.source_memory"` → `"share.quote.source_memory"` | `test_own_memory_makes_a_card_with_the_original_line` |
| B9 | 请求体能塞正文 | `QuoteCardRequest` 加回 `source_text` | `test_schema_has_no_room_for_the_text` |
| Q1 | 类型表去掉 `'quote'` | `ShareCardVariant` 少一项 | 「ShareCardVariant 含 'quote'」 |
| Q2 | 来源类型与后端不同集合 | `QuoteSourceType` 加 `'chat'` | 「QuoteSourceType 只有 memory / reflection」 |
| Q3 | 预览即发请求 | 删 `if (variant === 'quote') return;` | 「取数 effect 里金句分支在任何 await 之前 return」 |
| Q4 | 导出把本地那句发上去 | body 加 `source_text: quoteText` | 「api.ts 的 body 只有 source_type / source_id 两个键」 |
| Q5 | 40405 没自己的文案 | 删 `pdfFailureKey` 的那一行 | 「四个码各映射到自己的文案键」 |
| Q6 | 280 两侧不同值 | 前端常量改 120 | 「金句卡正文上限与后端同值」 |
| Q7 | 埋点来源不在枚举 | `FUNNEL_SOURCES` 去掉 `'quote'` | `funnel.test.ts`「九段台阶 + 两个分享环路事件都在枚举里」 |
| Q8 | 清单虚报第三项 | `ShareManifest` 多加一项 | 「清单两项＝那句原话 + 深链」 |
| Q9 | 删掉导出前披露 | 删 `quoteNote` 那一行 | 「导出前那一句…长在卡外、清单之后」 |
| Q10 | 入口不带正文 | `MemoriesScreen` 去掉 `quote_text` | 「记忆详情→金句卡」 |
| Q11 | 空正文也给按钮 | `{!!r.summary?.trim() && (` → `{true && (` | 「空正文那条不给按钮」 |
| Q12 | 某语言缺键 | 删越南语 `share.quoteNote` | 「share.quoteNote：15 语言都有且非空」 |
| Q13 | 两个码念同一句话 | 高棉语 `notOriginal` 退回 `memoryGone` 那句 | 「40405 的文案 × 15」 |
| G1 | 反向绿闸 | 往金句卡 JSX 分支插 12 行注释 | `quoteCardWiring` 仍全绿（锚点切片不是脆的） |
| G2 | 反向绿闸 | 往 `export_quote_card_pdf` 的 docstring 插 8 行 | `test_quote_card.py` 仍 16 passed |

两条在本批开发过程中**自然抓到过**（不是补跑的仪式，所以上面 B8/Q13 是把它们钉成可重跑的靶）：

- 文案键写成 `share.quote.source_memory`（双命名空间）⇒ 卡面印裸键名、日期整段消失，
  由 `test_own_memory_makes_a_card_with_the_original_line` 的 `assert "2026-03-05" in text` 打红；
- `quoteCardWiring` 首跑抓到 `km`／`th` 两种语言的 `pdf.failed.notOriginal` **逐字等于**
  `memoryGone`（40405 白分），改成各语言自己的「这句是 AI 整理的」后绿——
  这就是 Q13 那条闸的来源。

## 8. 两个既有红灯（不是本批造成的）

- `test_canonical_schema.py::test_python_reaches_canonical_models_only_through_deletion`
- `test_r5_interop_broker.py::test_case_plan_fully_ran`（跨进程 Go 用例本机从未跑过）

两条在本批开工前的全量基线里就是红的，计数与结案前一致（1770 passed 中不含它们）。
按「发现的缺陷不顺手修」登记，不并入本批结案。

## 9. 还没验的（别把这份当上线证据）

- **真机没有截图／没导出过一次真 PDF**。`expo-file-system` + `expo-sharing` 是原生路径，
  本机 jest 的 node 环境跑不了；屏与卡面的闸全是源码扫描＋纯函数单测。
  PDF 里中文 CID 字体、引号包边、280 字满行的实际排版都要在 Expo 设备上看一眼。
- **预览与卡面可能不同句**（设计取舍，不是已知 bug 的遮掩）：预览那句来自入口带进来的
  `quote_text`，卡上那句是服务端按 id 现取的。用户在两处之间改过那条原话，预览就是旧的。
  要消除只能让卡页二次取数（＝预览即发请求，与 §1 的授权口径冲突），或后端回吐正文
  （＝响应形状改动）。归桶：等 KG-31 之后再看读数，真有用户反馈再立票。
- **分享率读数还没有**：`share_tapped/share_completed('quote')` 已发，但本机无生产流量，
  「金句卡是不是比回忆卡更能带人进来」要在 PostHog ＋ canonical 表上接完才有数。
- 13 个非 en/zh 语言的 `share.quote*`／`pdf.failed.notOriginal` 是**我写的译文**，
  没有母语者复核；键集与占位符有闸，措辞自然度没有。
- `PDF 卡上的深链是否真能落地归因`：短链由 `referral_link(settings.site_url, code)` 生成，
  与其余三张卡同一条路，本批未单独验归因面（挂 KG-31 的流量票）。
