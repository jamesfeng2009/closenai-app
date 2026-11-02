# 内链与 CTA 规范（KG-17 · 2026-11-03）

台账 §KG-17（`docs/keyword-growth-task-board-2026-11-01.md` §3 的 `#### KG-17 · 内链与 CTA 规范` 小节）
的验收原文四条：「爬虫报告：0 断链、0 孤儿页；每个支柱页至少 2 条入链」
（那一行逐字含 `每个支柱页至少 2 条入链`）、「所有 CTA 指向真实下载/等待名单，不指向占位商店链接」
「建立面包屑、导航、页脚」与「每季度跑一次断链与孤儿页检查」。
这四条全是图上能算的，所以本文件的每个数字都由 `scripts/check_site_links.py` 现算，
**不许手抄**——§6 给复跑命令，§2/§3/§4 的表是判据的输出面，改了页面却没重跑，闸就红。

**这一批不部署任何页面。** 发布受 `KG-02`（法务）与 `T7-05`（运营主体）两道 `BLOCKED`
（`site-content/pages.json:12` 的 `publish_blocked_by`），生成物一律带 `noindex`
（`scripts/build_seo_pages.py:520`），`write_all()` 硬拒写 `web/`（`scripts/build_seo_pages.py:555`）。
本闸判的是**支柱面**（内容源＋正文＋生成器在内存里产出的 HTML），
现网面（`web/*.html`）只登记、不修——修法属 KG-23/KG-02 的文案与法务面。

## 1. 判据分两面，不许混

| 面 | 判什么 | 不合格时 |
|---|---|---|
| 支柱面 | 断链、孤儿页（入链 < `INBOUND_MIN` = 2，`scripts/check_site_links.py:44`）、hub 3 跳内不可达（`:47`）、CTA 假按钮/商店域/悬空锚点、面包屑缺失或指错、sitemap 与页面集合不同源、robots 挡 `/en/` | **硬红**，`--check` exit≠0 |
| 现网面 | `web/*.html` 里的裸 `href="#"`、缺 id 的 `#fragment`、指向不存在文件／未发布支柱页的链接、非白名单 `mailto:` | **不判红**，但与 §5 在册表逐条对拉：多了＝新坏没登记，少了＝修好没注销，条数不符＝漂移 |

**入链只数「别的页面正文里的内链」**（`MD_LINK` → `/en/<slug>/`，`scripts/check_site_links.py:80`）。
sitemap、页脚、面包屑、生成器自己拼的 Related 段一律不算——否则每个页面白捡三条入链，
孤儿这个概念就被判据本身抹掉了。这是本闸存在的主要理由：出链看一眼就知道，**入链只有算得出来**。

## 2. 图：现算表

跑法见 §6；下表 7 行 ×3 个数字与 `--json` 的 `inbound`／`hops` 逐格同值
（`backend/tests/test_site_links.py` 拿现算值比这张表，两侧不同源：一侧是人写的表，一侧是爬虫）。

| 页面 | 出链 | 入链 | 距 hub |
|---|---|---|---|
| `ai-journal` | 3 | 2 | 0 |
| `journal-app` | 2 | 2 | 1 |
| `mood-tracker` | 2 | 3 | 2 |
| `self-reflection` | 2 | 4 | 1 |
| `journal-prompts` | 3 | 2 | 1 |
| `self-improvement` | 3 | 2 | 2 |
| `goal-tracker` | 2 | 2 | 3 |

合计 7 页／17 条正文内链；入链下限 2 全部满足（最低 2，最高 `self-reflection` 4）；
hub = `ai-journal`（支柱簇的入口页），最远 `goal-tracker` 恰好 3 跳＝贴在 `HOPS_MAX` 上，
**再删掉任意一条 `self-improvement → goal-tracker` 或 `mood-tracker → goal-tracker` 就会不可达**——
这条余量为零的边是本页集最脆的地方，改动前先跑闸。

本批为满足「每个支柱页至少 2 条入链」补的三条正文内链（都是「按内容本来就该指过去」的位置，
不是硬塞的链接农场）：

- `docs/content/pages/journal-prompts.en.md:42-45` → `/en/journal-app/`（提示库那页解释「这三十条在哪跑」）
- `docs/content/pages/ai-journal.en.md:88-90` → `/en/journal-prompts/`（「追问从哪来」那页列着三十条）
- `docs/content/pages/self-improvement.en.md:61-63` → `/en/ai-journal/`（回看页说明白名单据读自哪条链）

三条同时进了内容源的 `related`（`site-content/pages.json:38`、`:124`、`:146`）——
**Related 段与正文内链同源**，否则生成物底部导航说两页、正文说三页。

## 3. CTA 面

唯一 CTA 定义在 `site-content/pages.json:18-24`：`waitlist`，
`href` = `https://closenai.app/#cta`（`:21`），label「Join the ClosenAI closed-test list」，
note 明写「没有商店链接，所以这刻意不是下载按钮」。闸判四件事：

1. `cta_definitions` 的键集只许是 `{waitlist}`——有商店链接之前不许出现第二个「真动作」；
2. label 与正文都不许命中假按钮词（`download now` / `get the app` / `free download`）；
3. 不许出现商店域名或短链（`apps.apple.com`、`apple.co`、`play.google.com`、`bit.ly`、`goo.gl`）；
4. CTA 的站内路径与锚点必须在 `web/` 的真文件里解析得到：`/#cta` → `web/index.html`，
   而 `<section class="final-cta" id="cta">` 逐字在 `web/index.html:689`（不是「应该存在」，是现读现验）。

**生成物 HTML 面还判裸 `href="#"`**：7 页现在 0 条。

## 4. 面包屑、导航与可达性

生成器的面包屑在 `scripts/build_seo_pages.py:530`，本批从 `{domain}/en/` 改成 `{domain}/`：
`/en/` 既不是站点根、也不是页面集合里的 slug ⇒ 7 页各一条断链（首跑实测抓到，已修）。
改后面包屑首项必须解析到 `web/index.html`（`WEB_ROUTES` 的 `/` 映射），文件不存在就判红。

页脚在 `web/index.html` 的 `:708` 起（logo／更新日志／博客／X 四条是 §5 的在册缺陷）；
支柱页生成物**没有页脚**——这是草稿面的刻意范围，发布时由模板层补，不在本闸判据里，
登记为限制（§7）。

## 5. 现网面在册缺陷

下表与 `--json` 的 `web_offenders` 逐条对拉（同文件、同 href、同**条数**）。
**这些不在本批修**：`web/index.html` 属 KG-23/KG-02 的文案与法务面，且现网面从未进版本控制
（第七批结案文档 §5 已登记，归 PLAT+ARCH），改动要走那条线。

| 文件 | href | 条数 | 逐字位置 | 归哪个桶 | 判据 |
|---|---|---|---|---|---|
| `web/index.html` | `#` | 5 | `:463`、`:708` 两处 logo；`:731` 更新日志；`:732` 博客；`:733` X / Twitter | PLAT（占位导航未接线） | 裸 `href="#"`：点了不回任何东西 |

同文件里 `:470`、`:482` 的 `href="#cta"` **不是**缺陷——`id="cta"` 在 `:689` 真的存在，闸判得过。
`:696` 是 `<button type="submit">`（等待名单表单），也不算链接缺陷。

修完一条就从表里删一行；不删，闸会报「已不坏了却不注销」。

## 6. 复跑命令与执行口径

```bash
./.venv/bin/python scripts/check_site_links.py            # 人读报告（含每页出/入链与跳数）
./.venv/bin/python scripts/check_site_links.py --check     # CI 入口：只输出不合格项
./.venv/bin/python scripts/check_site_links.py --json      # 机器可读：计数 + 不合格项（§2/§5 的对拉面）
./.venv/bin/python scripts/check_site_links.py --self-test # 两条证人：清零入链必判孤儿、清空 §5 必判失效
./.venv/bin/python -m pytest backend/tests/test_site_links.py -q   # 把上面四条判据钉进 pytest
```

台账「每季度跑一次」的执行口径（`docs/keyword-growth-task-board-2026-11-01.md` §KG-17「做什么」第三条）：
**不是**等人记得跑——`--check` 与 `test_site_links.py` 都在 CI 的 backend job 里，
任何一次页面改动都会重跑；季度这条因此是「**人工翻一遍 §5 有没有该注销的**」的节奏，
不是判据的执行前提。上一轮的季度人工复核：**2026-11-03（本文件首跑）**。

## 7. 硬限制（不许引用成已完成）

1. **未部署**：`noindex` ＋ 发布闸 `KG-02`／`T7-05` 仍 `BLOCKED` ⇒ 本文件判的是草稿面图，
   不是线上图。Google 看见的仍然是只有 3 条 URL 的 `web/sitemap.xml`。
2. **「CTA 在移动端可点、无遮挡」未取证**：需要真机/浏览器渲染，本闸只判 href 与锚点解析。
   这一条留 `REVIEW` 不转 `DONE` 的理由之一，挂在 KG-02 结案后的取证轮。
3. **只出 en**：`hreflang.langs` = `["en"]`，13 个语言副本没落地页面 ⇒ 多语言内链面为零，不在本闸射程。
4. **现网面缺陷只登记不修**（§5），且 `web/` 不在版本控制里，本文件引用的 `web/index.html` 行号
   只在这台机器上可核——这是第七批结案文档已登记的 PLAT 缺陷，不是本批新造的。
