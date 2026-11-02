# KG-17 结案：内链与 CTA 闸（2026-11-03，第八批）

台账行：`docs/keyword-growth-task-board-2026-11-01.md` §1 的 KG-17 行、§3 的「KG-17 · 内链与 CTA 规范」小节。
状态：**`REVIEW`**（交付物完成、判据在场并实测红绿；`DONE` 需部署后的真机与线上图，本机不可证，未自封）。

一句话：把「内链与 CTA」从一句形容词做成一台**会自己打红的爬虫**，并且让它数入链的方式
刚好能看见孤儿页——顺带在这次首次运行里打红了 4 类真缺陷，其中 3 类是上一批（第七批）交付的页面源。

---

## 0. 交付物

| 文件 | 作用 | 入口 |
|---|---|---|
| `docs/seo/internal-links-2026-11.md` | 判据说明书＋图表现算表＋现网面在册表 | 台账 §KG-17 的「证据落点」点名的就是它 |
| `scripts/check_site_links.py` | 爬虫闸（解析内容源与生成物，输出违规清单） | `--check`／`--json`／`--self-test` |
| `backend/tests/test_site_links.py` | 把上面两件的结论钉进 CI（12 条） | `pytest backend/tests/test_site_links.py` |
| `scripts/mutate_kg17_link_gates.py` | 变异自证：15 红靶＋3 绿靶 | 串行，禁止并发 |
| `scripts/build_seo_pages.py:530` | 面包屑 Home 的目标（本次修的就是这一行） | 生成器 |

三面关系：**内容源** `site-content/pages.json` → **正文** `docs/content/pages/*.en.md` → **生成物**（内存里拼的 HTML）。
生成物不落盘：`docs/content/build/` 被 `.gitignore` 的 `build/` 排除，所以 pytest 面直接 `import` 生成器在内存里拼，
再重新解析 HTML——不读任何被 gitignore 的目录（这条本身也是判据，见 `backend/tests/test_site_links.py:205`）。

---

## 1. 判据分四面

### 1.1 图面（孤儿页只有算入链才看得见）

`scripts/check_site_links.py:80` `body_links()` **只承认「别的页面正文里的内链」**：形如 `](/en/<slug>/)` 的 markdown 链接。
被明确排除的入边来源：sitemap、页脚、面包屑、生成器自己拼的 Related 段。
理由写在闸的 docstring 里：如果这些都算入链，每页白捡三条，`孤儿页` 这个概念会被判据本身抹掉——
一台永远数不出孤儿的爬虫比没有爬虫更糟。

- 下限：`scripts/check_site_links.py:44` `INBOUND_MIN = 2`，**读自台账**（见 1.4）。
- 可达：`scripts/check_site_links.py:47` `HOPS_MAX = 3`，起点 `:48` `HUB_SLUG = "ai-journal"`。
- 正向证人：`backend/tests/test_site_links.py:92` 判 `sum(inbound.values()) == edges`。
  不等 ⇒ 有边的目标不在页面集合里 ⇒ 入链在漏算，孤儿判据会假绿。

### 1.2 CTA 面（真动作 vs 假按钮）

四条并列，`scripts/check_site_links.py:131`～`:149`：

1. `page["cta"]` 必须在 `cta_definitions` 里；
2. 文案命中 `FAKE_CTA_WORDS`（`:51`，`download now|get the app|free download`）即红——现在没有商店链接，
   所以「立即下载」本身就是假按钮；
3. host 命中 `STORE_HOSTS`（`:50`）或 `.appstore.com` 即红；
4. href 必须是站内动作，且锚点 `#frag` 要能在目标页面里解析到真 `id`。

当前配置里 `cta_definitions` 只有 `waitlist` 一个键，href 为 `https://closenai.app/#cta`，
锚点解析到 `web/index.html:689` 的 `<section class="final-cta" id="cta">`。
这条锚点由 `backend/tests/test_site_links.py:189` **独立读 `web/index.html` 重新查**，
不复用爬虫的返回值（断言两侧不许同源）。

### 1.3 在册面对拉（现网面的缺陷要逐条对上台账）

- 爬虫侧：`scripts/check_site_links.py:212` `web_offenders()` 返回 `{(文件, href): 次数}`。
- 文档侧：`:260` `ledger_entries()` 只认 `docs/seo/internal-links-2026-11.md` 的 `## 5. 现网面在册缺陷` 小节里的表行。
- 对拉：`backend/tests/test_site_links.py:156` **逐条**比（键与次数都要等），`:205` 再加一条计数证人
  `sum(offenders.values()) == sum(ledger.values()) == 5`。

「在册」的意思是**登记而不修**：这 5 处属于现网面 `web/index.html` 的占位导航，归 KG-23／KG-02 的文案与法务面，
而且 `web/` 整目录从未进版本控制（第七批已登记）。把未修的缺陷写成已修是假交付；
把它们登记进一张会被逐条对拉的表，才是这台闸愿意长期背着这笔债的方式。

### 1.4 引用即自证（判据数字不许自己说了算）

`backend/tests/test_site_links.py:170` 从台账 §KG-17 小节（按标题定位，不按行号）把「每个支柱页至少 **2** 条入链」
的数字抠出来，与 `INBOUND_MIN` 对拉；同条再判每页 `related` 数量落在 2–3 且 `set(related) == 正文链接集合`。
所以「把下限调松让闸变绿」这条路会被这条判据当场打红（红靶 L11），「JSON 加了 related、正文没链」也会（L12）。

---

## 2. 实测计数（全部本机串行跑的原始输出）

| 面 | 命令 | 结果 |
|---|---|---|
| 爬虫闸 | `python scripts/check_site_links.py --check` | 内链/CTA/孤儿页判据全绿：**7 页／17 条出链／入链下限 2／3 跳内从 ai-journal 全可达**，exit 0 |
| 爬虫自证 | `python scripts/check_site_links.py --self-test` | 两条 ✓ 证人（把一页做成孤儿、把 CTA 做成假按钮），exit 0 |
| pytest 面 | `pytest backend/tests/test_site_links.py` | **12 passed** |
| 七张闸合跑 | 页面源＋内容清单＋简报＋台账＋承诺审计＋ADR＋本闸 | **117 passed／1 skipped**（那 1 条 skip 是台账闸的 `DONE` 分支＝不算绿） |
| 变异自证 | `python scripts/mutate_kg17_link_gates.py` | **红闸 15/15＋绿闸 3/3、红闸失手 0 条**，恢复 ✓（基线先跑，逐条 `copy2` 备份＋`filecmp` 校验写回） |
| 生成器 | `python scripts/build_seo_pages.py --check` | 页面源判据全绿：7 页／7 条 sitemap 行（`--check` 未写文件） |
| 全量后端 | `pytest backend/tests` | **1993 passed／2 failed／91 skipped**；2 条既有红灯与本批无关（`test_canonical_schema.py` 的 canonical 引用、`test_r5_interop_broker.py` 要真库＋真 broker） |

每页入链实测（`docs/seo/internal-links-2026-11.md` §2 现算表，与爬虫输出由 `:137` 对拉）：
`ai-journal` 3／`journal-app` 2／`mood-tracker` 2／`self-reflection` 2／`journal-prompts` 3／`self-improvement` 3／`goal-tracker` 2。

---

## 3. 首跑打红的四类真缺陷

这台闸第一次运行的产出不是「全绿」，而是四条红——每条都改了，改的都是第七批交付的东西：

1. **三页各只有 1 条入链**（`ai-journal`／`journal-app`／`journal-prompts`）：孤儿页判据的靶心。
   补法不是随便塞链接，而是按内容补三条真该指的正文内链：
   `docs/content/pages/journal-prompts.en.md:44`、`docs/content/pages/ai-journal.en.md:89`、
   `docs/content/pages/self-improvement.en.md:62`；同批把 `site-content/pages.json` 的 `related`
   与正文对齐成同一集合（`:38`／`:124`／`:146`）。
2. **面包屑 Home 指向不存在的 `/en/`**：生成器 `scripts/build_seo_pages.py:530` 拼的是 `{domain}/en/`，
   而 `/en/` 这个语言根目录从未被任何页面生成 ⇒ 7 页各一条断链。根因是第七批只判了「有没有面包屑」，
   没判「面包屑首项解析到哪」。现改成 `{domain}/`，并由 `backend/tests/test_site_links.py:116` 独立重新解析 HTML 钉住。
3. **`related` 与正文不是同一集合**：JSON 里写了三条、正文只链两条。这类漂移不会断链、不会 404，
   只会让「内链规范」这句验收变成表格里的一行形容词。
4. **现网面 `web/index.html` 5 条裸 `href="#"`**：`:463`、`:708` 两处 logo；`:731` 更新日志；`:732` 博客；`:733` X / Twitter。
   **只登记不修**（理由见 1.3）。

---

## 4. 闸自己的三处误报（判据先修对，再拿它打人）

首跑除了打红真缺陷，也打红了三个不该红的东西。三处都是判据写错，不是内容写错：

| 误报 | 根因 | 修法 |
|---|---|---|
| 合法 `#fragment` 被当成裸 `#` | 只按前缀 `#` 判，没区分「点了不回任何东西」和「锚点写错」 | 拆成两条判据：裸 `#` 与「带片段但文档里没这个 id」（`scripts/check_site_links.py:127`～`:129`） |
| 站内绝对路径没映射到真文件 | `/en/xxx/` 这类站内路径直接当外部 URL 判，映射缺失 ⇒ 误报断链 | 补 `WEB_ROUTES`（`:56`）做路径→文件映射 |
| offender 去重把「同链坏 5 次」看成「坏 1 次」 | 返回集合而不是计数 ⇒ 在册表的数量证人失效 | `web_offenders()`（`:212`）改成 `{(file, href): count}` |

第三条尤其要紧：如果闸只返回集合，`:205` 那条 `sum(...) == 5` 的正向证人就永远对不上，
而「5 条已登记」这句话也退化成「有若干条已登记」。

---

## 5. 与台账的偏差与勘误（带日期，逐条归口）

1. **2026-11-03｜`HOPS_MAX = 3` 是我加的，台账没有这句话。** 台账 §KG-17 验收只写「0 断链、0 孤儿页；每个支柱页至少 2 条入链」，
   「3 跳内从 hub 可达」是我给「导航可达」找的机械形态，属实现选择不是承诺。它比台账严，所以不会造成假交付，
   但引用时只能说「本闸判 3 跳」，不能说「台账要求 3 跳」。
2. **2026-11-03｜「每季度跑一次」的执行口径改了。** 台账 §KG-17 做什么里写的是季度检查，本批把它做成**每次 PR 都跑**
   （pytest 面进 `backend/tests`，harness 进 `scripts/mutation_sample.py` 的 glob 发现面）。
   理由是这台闸判的是草稿面图，季度节奏对草稿面没意义；真要按季度跑的是**部署后的线上图**，那要等 KG-02／T7-05。
3. **2026-11-03｜「CTA 在移动端可点、无遮挡」这条没取证。** 需要真机渲染，本闸只判 href 与锚点解析
   ⇒ 台账三条验收里有一条未被机械覆盖，这也是本行只到 `REVIEW` 的直接原因之一。
4. **2026-11-03｜台账「建立面包屑、导航、页脚」里的页脚在支柱页生成物里没有。** 草稿面只有面包屑（`web/index.html` 有页脚，
   生成物没有），发布时由模板层补。已登记不装作做了。
5. **2026-11-03｜现网面 5 条裸锚未修。** 归 KG-23／KG-02；且 `web/` 从未进版本控制，改了也无法回滚取证（第七批登记过同一件事）。
6. **2026-11-03｜本批自己造成的破坏：并发跑了变异采样与全量后端。** 我在后台全量 `pytest backend/tests` 还没结束时
   跑了 `scripts/mutation_sample.py --files docs/content/pages/ai-journal.en.md`（它真的会执行 harness、改文件再写回），
   两侧互脏，出现 **4 条假红**（含我新加的两条）。串行重跑后回到既有 **2 failed／1993 passed／91 skipped**。
   这是「变异脚本严禁并发跑」的第二次实证，记在这里不辩解：本批报的计数一律取串行那一次。
7. **2026-11-03｜台账行号漂移的处置方式改了。** 第七批的教训是「台账里插一行说明，下面全体下移」，
   而 `docs/seo/internal-links-2026-11.md` 与 `docs/growth/kg17-links-closure-2026-11-03.md`（本文件）
   对台账一律用**小节引用**（§KG-17／§1／§2），不写 `md:NNN`；本文件对代码与 JSON 的引用仍写完整行号，
   因为那些文件不会因为写结案文档而移动。同批仍同步了三处下游抄台账行号（`docs/aso/en-US-2026-11.md:62`、
   `docs/keyword-claims-audit-2026-11.md:159` 的 `:230`，`docs/aso/keyword-tracker-2026-11.md:4` 的 `:811`）
   与两个 harness 的锚点（P26／P27／GN5、B8／B9），台账闸随之 9 passed。

---

## 6. 未做与残余

- **未部署**：`KG-02`（法务签字）与 `T7-05` 仍 `BLOCKED`，生成物一律带 `noindex`（`scripts/build_seo_pages.py:520`），
  且生成器硬拒写 `web/`（`:555`）。所以本闸判的是**草稿面图**，不许引用成「线上 0 断链」。
- **移动端可点与遮挡**：未取证（见 5.3）。
- **页脚**：生成物没有（见 5.4）。
- **多语言**：只有 en，`G11` 的重开条件不变。
- **线上 sitemap/robots**：`:236` 只判「现网面的 sitemap/robots 不许提前把草稿面判成已上线」，不判线上收录状态。
- **其余 5 页的人工逐页通读**：仍欠（KG-18 的抽检表只回填了 2 行）。闸绿 ≠ 抽检过。
- **`web/` 的 5 条裸锚**：见 5.5。
- **KG-13／KG-15／KG-16**：仍 `TODO`，卡在法务签字与外部可核验来源，本批不推、不造草稿凑状态。

---

## 7. 复跑与回滚

```bash
# 三面各跑一次（串行；跑 harness 时不许并任何其他写盘任务）
.venv/bin/python scripts/build_seo_pages.py --check
.venv/bin/python scripts/check_site_links.py --check
.venv/bin/python scripts/check_site_links.py --self-test
.venv/bin/python -m pytest backend/tests/test_site_links.py -q
.venv/bin/python scripts/mutate_kg17_link_gates.py --check          # 锚点命中校验
.venv/bin/python scripts/mutate_kg17_link_gates.py                 # 15 红＋3 绿
```

回滚顺序（本批无数据库、无线上动作）：
① 删 `backend/tests/test_site_links.py` 与 `scripts/mutate_kg17_link_gates.py`；
② `scripts/check_site_links.py` 与 `docs/seo/internal-links-2026-11.md` 撤下；
③ 本文件与台账 §1 KG-17 行、§2 快照／工作流 C／第八批说明行撤回，三处下游行号引用与两个 harness 锚点同步回退；
④ 内容面三处正文内链与 `pages.json` 的 `related` 可单独回退——但回退后 `check_site_links.py --check` **会红**
（三页回到 1 条入链），这是设计如此：内链规范被回退时就该有人看见红。
⑤ `scripts/build_seo_pages.py:530` 的面包屑修复**不要回退**：那是一条真断链，回退等于把 7 页各重新弄坏一条。
