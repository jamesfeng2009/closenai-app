# KG-09／KG-10／KG-11／KG-12／KG-14 结案：簇简报落到唯一内容源，支柱页正文与生成器进闸

主体：ClosenAI ／ 编制：2026-11-03 ／ 负责人：GROWTH（闸与判据：GROWTH+ARCH）
台账：`docs/keyword-growth-task-board-2026-11-01.md:79`（KG-09 行）、`:80`（KG-10）、`:81`（KG-11）、`:82`（KG-12）、`:84`（KG-14）
上游口径：`docs/keyword-growth-implementation-plan-2026-11-01.md` §8.1 唯一内容源、§8.2 页面—意图表、§8.3 发布闸；清单 `docs/keyword-content-gate.md` G01–G14
状态：五个任务一律 `REVIEW`。**发布没发生**——KG-02（法务）与 T7-05（运营主体）仍是 `BLOCKED`，生成物带 `noindex`，一行没进 `web/`。

---

## 1. 交付物

| 面 | 文件 | 内容 |
|---|---|---|
| 简报（KG-09） | `docs/content/briefs-2026-11/` 四份 | 八节固定标题；簇名与 KG-26 提示库的 `cluster` 同词，不新造 |
| 唯一内容源 | `site-content/pages.json` | 7 页（`:28` 起），头部写死 `output_dir`（`:10`）、`generated_on`（`:11`）、`publish_blocked_by`（`:12`）、`hreflang`（`:13`）、`cta_definitions`（`:18`） |
| 正文 | `docs/content/pages/*.en.md` 七份 | 600 行；每份恰好一个 `# H1`、一条 `not medical`、本模板那句逐字在场、≥2 条内链、无禁语、无品牌词、无中文 |
| 生成器 | `scripts/build_seo_pages.py` | `violations()`（`:149`）判 G01–G14 的机械面；`write_all()`（`:555`）硬拒 `web/`；`--self-test`（`:578`）是正向归零证人 |
| 闸 | `backend/tests/test_seo_page_source.py` | 18 条，引用即自证＋三面不同源对拉 |
| 变异 harness | `scripts/mutate_kg10_kg14_page_gates.py` | 红闸 27 条＋绿闸 5 条 |
| 抽检回填 | `docs/keyword-content-gate.md:54` 起 2 行 | `ai-journal`／`journal-prompts`，逐列写清哪几项只到草稿面 |

提示页（`journal-prompts`）正文只有一个 `<!--prompt-library-->` 插入点，30 条由 `scripts/build_seo_pages.py:377`（`render_prompts`）从 `backend/content/reflection_prompts.json` 现算展开——**手抄＝红**（判据 `backend/tests/test_seo_page_source.py:276`）。

---

## 2. 实测计数（2026-11-03 本机，串行跑，非并发）

- `./.venv/bin/python scripts/build_seo_pages.py --check` → exit=0，「页面源判据全绿：7 页／7 条 sitemap 行（--check 未写文件）」；`--self-test` → exit=0（`Download now` 的 CTA 被拒）。
- `backend/tests/test_seo_page_source.py` **18 passed**；`test_keyword_content_gate.py` **6 passed／0 skipped**；`test_content_briefs.py` **40 passed**；`test_keyword_task_board.py` **9 passed／1 skipped**（skip＝`DONE` 分支条件性未跑，**不算绿**）。
- 上述四张合跑 **73 passed／1 skipped**；再加承诺审计（16）与 ADR（16）＝六张合跑 **105 passed／1 skipped**。
- 变异 harness 串行：本批 **红闸 27/27＋绿闸 5/5、红闸失手 0 条**（`--check` 32 条锚点各命中一次）；复跑前三批 **KG-09 简报 17/17＋3/3**、**KG-01/03/18 26/26＋4/4**、**KG-04/05/08 42/42＋4/4**，全部失手 0 条。
- 全量后端 **1981 passed／2 failed／91 skipped**（106s）。那 2 条既有红灯与本批无关：`test_canonical_schema.py::test_python_reaches_canonical_models_only_through_deletion`、`test_r5_interop_broker.py::test_case_plan_fully_ran`。skip 比第六批的 92 少 1，正是 KG-18 那条抽检判据从「skip＝未跑」自己变成真跑。
- 前端：**本批没碰 `frontend/`**，无 jest／tsc 新增面，不报数。

---

## 3. 判据钉在四层，为什么这样钉

①**三面同词**（`backend/tests/test_seo_page_source.py:112`）：H1 必须是「简报 §3＝内容源 `h1`＝正文首行」同一句，模板句必须出现在**自己那页**正文（`:128`）。抄录关系一旦靠人记就会漂，所以三侧各读一次原文，不共享变量。
②**证据必须打得开**（`:139`）：每条 `ref` 若是 `路径:行` 就真去读那一行，行号不许越界、该行不许空白；另有 `checked >= 15` 的正向证人，防止「一条都没核到也算绿」。本批 16 条证据**全部**是仓内真行（0 条外部 URL，见 §5 第 2 条偏差）。
③**共享尺子不许另抄一份**（`:176`）：禁语直接 import `scripts/check_aso_copy.py:41` 的 `BANNED_PATTERNS`，品牌词读 `docs/aso/en-US-2026-11.json:22`；G13（不许复用已被判「改写／删除」的句子）读 `docs/keyword-claims-audit-2026-11.md:145` 起的判定表原文。**另一侧是文档正文，不是脚本自己的常量**，所以不存在「拿 settings 比 settings」的恒真断言。
④**发布闸用代码守，不用提醒守**（`:236`）：`publish_blocked_by` 非空 ⇒ `write_all()` 拒写 `web/`、生成物带 `noindex`、`violations()` 对输出目录落 `web/` 直接判红。这条判据自己也被打过（P21／P22）。

另两条是这次踩出来的：**生成物要重读不许信返回值**（`:251`，写盘后重新解析 HTML 比对 canonical／hreflang／title／sitemap）；**语言面要钉死**（`:331`，本批只出 `en`，正文混进中文＝红）。

---

## 4. 变异自证（27 红／5 绿）

红靶分五组：三面同词（P1–P5）、证据与日期（P6–P7）、禁语与关键词堆叠（P8–P13）、内链与假按钮（P14–P18）、提示页手抄（P19–P20）、发布闸与语言面（P21–P23）、抽检表（P24–P25）、下游抄台账的行号（P26–P27）。
每条除 `exit≠0` 外还要求**名片段命中**，红的必须是那条判据本身。绿闸 5 条：追加一节说明（GN1）、`generated_on` 挪一天（GN2）、CTA 说明写更长（GN3）、抽检表加第三行（GN4）、正确的新行号引用（GN5）——证明判据看的是关系，不是文档长度或固定行数。

**开发过程中闸当场打红的真实缺陷（6 处，不是事后补写的靶）**：
1. `ai-journal.en.md:80` 与 `journal-app.en.md:41` 的 `treats` 命中共享尺子的 `\btreat\w*`；
2. `mood-tracker` 的 description 里 `diagnosis` 命中 `\bdiagnos\w*`；
3. `mood-tracker` 的 H1 有两个逗号，撞 G01「逗号／顿号 ≤1」——**没改判据，改了文案**（去掉 serial comma，52→51 字符），简报 §3、内容源、正文、变异锚四处同批同步；
4–5. `journal-prompts`、`self-improvement` 缺 `not medical` 的字面命中；
6. 两处中文混进英文正文（`the回访`、`## The回访 mechanic`）——这条补成了新判据 `test_seo_page_source.py:331`。

---

## 5. 偏差与勘误（带日期与根因；按口径只登记，不顺手修别的）

1. **2026-11-03｜草稿目录不是方案 §4.6 写的 `docs/content/drafts/`**，实落 `docs/content/pages/`。根因：`drafts/` 里已有 `scripts/export_prompts.py` 导出的同名 `journal-prompts.en.md`（KG-26 提示库的导出物），两处同名会互相覆盖。理由写进 `site-content/pages.json` 的 `_comment`，没改方案文档。归属：方案文档维护批。
2. **2026-11-03｜G03 的「来源与日期」实现成 `路径:行` 或 https 二选一**，清单原文只写 https 形态。本批 16 条证据全是仓内真行（0 条外部）。根因：支柱页的差异化主张本来就来自自家代码与库里字段，要求它们写成 URL 只会逼人编一个链接。**这条是判据比原文更宽，不是更严**，需要 KG-18 的清单文字回头对齐。归属：GROWTH+ARCH（清单 §1 G03 那格）。
3. **2026-11-03｜方案 §8.1 说生成器写 `web/` 并改 `web/sitemap.xml`，脚本改为硬拒**（`scripts/build_seo_pages.py:555`）。根因：§8.3 的发布闸（KG-02＋T7-05）没结案，写进 `web/` 就是绕闸。归属：方案文档维护批。
4. **2026-11-03｜台账 §KG-10 的验收写「hreflang＋x-default」，本批只出 `en`**，`x-default` 与 `en` 同指一条 URL。根因：中文页面是否上本站属简报 §8.4 未决决策，未决就不生成。这条已写在 `docs/keyword-content-gate.md:56` 的抽检「不合格项」列里，不装作已达。归属：KG-02／GROWTH。
5. **2026-11-03｜本批自己造成的破坏（已回滚）**：变异靶 P20 原本直写提示 id 字面量，撞上 `backend/tests/test_prompt_library.py:292` 那条「提示 id 不许在库外硬编码」——它的扫描面含 `scripts/`，所以靶没打进别人的闸，先弄红了一条既有闸（全量出现第 3 条红灯）。改成拼接后该闸 32 passed、全量回到既有 2 failed。**教训**：靶要打得动目标闸，写靶前得先确认它不落在别的闸的扫描面上。归属：本批已修，无需另派。
6. **2026-11-03｜台账闸的「下游抄行号」证人有一处空档（已当场补强）**：本批在 §2 插两行说明后，§3 起全体下移两行，三处下游引用（`docs/aso/en-US-2026-11.md:62`、`docs/keyword-claims-audit-2026-11.md:159` 的 `:226`；`docs/aso/keyword-tracker-2026-11.md:4` 的 `:807`）全漂，而 `backend/tests/test_keyword_task_board.py:199` 原先只判「那一行非空＋同节＋同任务」，**漂进同小节的相邻真行它照旧给绿**（实测插入后 9 passed／1 skipped＝假绿）。补强成「引用同一行必须带一个**逐字内容锚**落在那一行」后：引用未改前该闸 1 failed（名片段「不含逐字内容锚」），三处引用同步成 `:228`／`:809` 并各加锚（`副标题 A`、`| 关键词簇 |`）后复跑 9 passed／1 skipped；P26／P27 专打这一类漂移。**同时勘误第六批那句「这一行的插入当场验证了那条判据」——它是过度声明，当时的证人抓不到同小节内的漂移。**归属：本批已补；`backend/tests/test_keyword_task_board.py` 的这条判据从此不许再按「行号非空」自证。
7. **2026-11-03｜两处意外发现，只登记不动**：① `web/` 整目录从未进版本控制（`git ls-files web`＝0，`git log -- web` 无记录，且 `web/` 不在 `.gitignore`）⇒ 清单 G14 的「可回滚」对现网面是空话，没有基线就没有回滚。归属：PLAT+ARCH（先决定现网面的版本化口径）。② `.gitignore:9` 的 `build/` 把生成目录 `docs/content/build/` 整体忽略，所以本批的 HTML 生成物不入库；判据不读仓内 `build/`，只跑 `--check` 与测试内临时目录。归属：GROWTH（若要留档需先加例外）。

---

## 6. 未做什么（这四条不许被引用成「已交付」）

1. **没有发布，也没有「两页 200」**：KG-02／T7-05 未解，生成物带 `noindex`、`sitemap.xml` 只在 `docs/content/build/`，没提交 GSC（KG-21 `BLOCKED`）。
2. **抽检只有 2 页**：其余 5 页过了同一套机械判据，但**没做过人工逐页通读**——闸绿 ≠ 抽检过。
3. **没走一遍真实 PR**：`.github/pull_request_template.md` 的 G01–G14 要与清单同名靠的是闸（`backend/tests/test_keyword_content_gate.py:92`），首篇支柱页提交评审才算真跑过一次。
4. **KG-12 的「逐条作者／日期」只到库级**：`backend/content/reflection_prompts.json:2` 的 `version` ＋页级 `author`，逐条署名还没补，已记在 `docs/keyword-content-gate.md:57` 那行。
5. **对比页（KG-16）不出正文与提纲**：可核验差异要逐条外部来源与日期，本轮一次外部抓取都没做（还额外等 KG-31）。

---

## 7. 回滚

判据与产物三处，删干净即可，不影响运行时代码：
`backend/tests/test_seo_page_source.py`＋`scripts/build_seo_pages.py`＋`scripts/mutate_kg10_kg14_page_gates.py`（闸面），
`site-content/pages.json` 的 7 条页条目与 5 个头部字段（内容源），
`docs/content/pages/*.en.md`＋`docs/content/briefs-2026-11/`＋`docs/keyword-content-gate.md:54` 起的 2 行抽检（正文与抽检）。
台账侧：§1 的 KG-09／10／11／12／14／18 六行状态回 `TODO`、§2 快照与「按工作流」C 行同步回退——**回退后必须复跑 `backend/tests/test_keyword_task_board.py`**，它按 §1 现算，抄漏一处就红。
