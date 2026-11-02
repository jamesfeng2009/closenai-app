# 关键词内容质量闸（KG-18 · 2026-11-02）

**用途**：任何一页面向搜索/商店流量的公开内容（KG-10～KG-17 的支柱页、对比页、博客页）在**合并前**必须逐项过这张表。
**谁执行**：作者自查 → PR  reviewer 复勾 → `backend/tests/test_keyword_content_gate.py` 把机械可判的那几项钉住。
**为什么要有这张表**：台账 §KG-18 的验收原文是「一页检查清单 + PR 模板；任意支柱页可按清单逐项打勾；抽检 2 页通过」。
清单与 PR 模板**必须同源**——两边 ID 集合由闸比对，漂了就红。

**清单本身不判「写得好不好」**，它判的是**能不能被追责**：每一项都要有一个机器能算的判据，或一个明确的人。

---

## 1. 十四项检查清单

| ID | 检查项 | 机械判据（谁来看） | 不合格怎么办 | 归属 |
|---|---|---|---|---|
| G01 | 一个具体问题 | 内容源该页的 `question` 非空、≥12 字、以问号结尾；H1 不是关键词堆叠（同页 H1 字符里逗号/顿号 ≤1） | 删掉页面或改成回答一个真问题；不许用同义词凑长度 | CONTENT |
| G02 | 一个原创模板或例子 | `template` 非空，且**不与站内其它页同文**（闸按整段哈希查重） | 用我们自己产品里真实会用的模板；没有就不发这页 | CONTENT |
| G03 | 来源与日期 | `sources[]` 每条有 `title`、`url`（https）与 `date`（`YYYY-MM-DD` 或 `YYYY-MM`）；引用外部研究必须指到原文，不指二手摘要 | 补出处；补不出来就把该句删掉（对齐方案决策 D4） | CONTENT+ARCH |
| G04 | 作者与运营主体 | `author` 不是占位符；页面主体名与现网 `web/privacy.html` 的运营主体一致 | 主体信息缺失属 KG-02/`T7-05` 未结案 ⇒ 本页只能留草稿 | LEGAL+CONTENT |
| G05 | 非医疗声明 | 页面含非医疗句（英文命中 `not medical`，中文命中「不是医疗」）；且正文不出现 `therapy`/`treat`/`diagnose` 的**主张式**用法 | 加免责句；把医疗词改成观察/记录语 | CONTENT+LEGAL |
| G06 | 无虚假案例 | `testimonials` 为空，或每条带 `verified: true`（真实用户提供）；正文不得出现编造的人物＋数字（闸扫「I went from…to…」式故事句） | 删故事，改成「示例模板」并明写这是模板不是结果 | CONTENT |
| G07 | 无批量同义词堆词 | `keyword_variants` ≤5 个；每个变体在正文出现 ≥1 次（不许只在标题里堆）；同一句里不并列三个以上同义变体 | 砍到真正有人搜的词，剩下的写进正文 | GROWTH+CONTENT |
| G08 | 至少 2 条内链 | 内链 ≥2 且目标 slug 都在内容源的页面集合里（**断链＝红**）；孤儿页反向也要过 KG-17 的爬虫闸 | 补内链或把目标页一起做；不许指向不存在的 URL | CONTENT |
| G09 | 1 个 CTA，且不做假按钮 | `cta` 非空且 ∈ {下载、等待名单、暂未开放说明、功能入口}；没有 App Store 链接时**必须**是等待名单/暂未开放，按钮文案不得写「Download now」 | 改 CTA 为真实可达动作 | GROWTH+FE |
| G10 | canonical | 每页 `canonical` 指向自身 URL，域名为品牌域 `closenai.app`（API 域不承载分享与落地路径） | 重生成；canonical 缺失不许发布 | PLAT+CONTENT |
| G11 | hreflang 互指 | 每页语言集合含 `en` 与 `x-default`；每个 hreflang 目标 URL 都在生成清单里存在；单向指认＝红 | 补齐对应语言页或删掉该 hreflang | PLAT+CONTENT |
| G12 | sitemap 行 | 该页在 `sitemap.xml` 恰好出现一次（0 次＝没提交，2 次＝重复） | 重跑生成脚本；不要手工改 sitemap | PLAT |
| G13 | 承诺口径过 KG-01 | 页面文案里「记得/陪伴/预测/治疗」四类风险词必须先对照 `docs/keyword-claims-audit-2026-11.md`：判「删除」的句子不得复活，判「改写」的必须用改后写法 | 回到审计表改判；不许私下软化禁语 | CONTENT+ARCH |
| G14 | 可回滚 | 只经 `scripts/build_seo_pages.py` 生成、草稿落 `docs/content/drafts/`；删页后不留内链孤儿，回滚步骤写进页面记录 | 先补生成脚本与回滚说明，再谈发布 | PLAT+ARCH |

---

## 2. 与既有闸的关系（不重复造轮子）

| 项 | 已有闸 | 这张清单加什么 |
|---|---|---|
| G13 承诺口径 | `backend/tests/test_absolute_memory_claims.py`（禁语＋扫描面证人） | 把「新增页面必须先过审计表」变成合并动作，禁语闸只扫代码面、扫不到 `docs/content/drafts/` |
| G01/G02 结构 | 方案 §4.6 的页面固定六段结构 | 逐段给了机械判据与不合格处理 |
| G08/G10/G11/G12 | KG-17 内链与发布闸（`scripts/check_site_links.py`，本批之后建） | 清单这一侧管「作者要不要自查」，脚本那一侧管「机器拦不拦」 |
| G14 | 决策 D0（不部署 `site/`）＋ §8.3 发布闸 | 发布闸未过之前，本清单的所有页只能停在草稿 |

## 3. 抽检记录（验收口径：任意支柱页可逐项打勾；抽检 ≥2 页通过）

抽检表由 `test_keyword_content_gate.py::test_sampling_covers_two_pages_once_pages_exist` 校验：
**内容源（`site-content/pages.json`）一旦有页，就必须在这张表里有一行**，且每行的勾必须覆盖 G01～G14 全部 ID。
本页写下来时（2026-11-02）内容源还不存在，所以这张表是空的——
**空表不算交付**，KG-10～KG-14 的页面草稿落地时必须回填，否则那条闸会红。

一个刻意的排除，写在这里防止以后再争：`docs/content/drafts/journal-prompts.en.md` 与 `.zh.md` **不算页面**。
它们是 KG-26 反思提示库的导出物（`scripts/export_prompts.py` 从 `backend/content/reflection_prompts.json` 生成），
没有 canonical/hreflang/sitemap/CTA，把它们算进「抽检 2 页」就凑出了一个假交付。
它们将来若要变成 `/en/journal-prompts` 那一条 KG-12 页面，得先按本清单重做，再进抽检表。

| 页面 | 抽检日期 | 抽检人 | G01…G14 | 不合格项与处理 |
|---|---|---|---|---|
| `ai-journal` | 2026-11-03 | Qoder 自查 ＋ `scripts/build_seo_pages.py` 复算 | 逐项打勾 G01/G02/G03/G04/G05/G06/G07/G08/G09/G10/G11/G12/G13/G14 | G04 运营主体未确认（T7-05 `BLOCKED`）⇒ 只到草稿、生成物带 `noindex`、脚本拒绝写 `web/`；G11 本轮只出 `en`（中文页决策未定，见简报 §8.4）⇒ `x-default` 与 `en` 同指一条 URL；G12 只进生成清单，未提交 GSC（KG-21 `BLOCKED` ⇒ 「两页 200」这条验收**未达**，不许引用成已发布） |
| `journal-prompts` | 2026-11-03 | Qoder 自查 ＋ 提示库现算对拉 | 逐项打勾 G01/G02/G03/G04/G05/G06/G07/G08/G09/G10/G11/G12/G13/G14 | 上面三条同判；另记一条：台账 §KG-12 要「30 条逐条可追溯（作者/日期/用途）」——本批逐条有 `id`／`function`／`why`（用途）与追问，**作者与日期只在库级**（`backend/content/reflection_prompts.json` 的 `version: 2026-11-1`）＋页级 `author` 字段，所以「逐条带作者与日期」这条**只到库级，记为限制**，等提示库补逐条署名后回头把本表判据收紧。正文不手抄 30 条，只留插入点，展开由生成器现算 |

**回填说明（2026-11-03）**：内容源 `site-content/pages.json` 落地 7 页后，`_page_count()` 从 0 变 7，
本表当场从「合法空表」变成「要求 ≥2 真行」——这条收紧是 `test_sampling_covers_two_pages_once_pages_exist`
自己算出来的，不是人工记得的。其余 5 页（`journal-app`／`mood-tracker`／`self-reflection`／`self-improvement`／`goal-tracker`）
过了同一套机械判据（`--check` 全绿），但**没做过人工逐页通读**，所以不进本表——
按本表口径「闸绿 ≠ 抽检过」，缺的是人读那一道。

## 4. PR 模板同步口径

`.github/pull_request_template.md` 里的清单块必须与本表**同一组 ID、同一名检查项**。
两边不一致＝闸红（`test_pr_template_mirrors_the_checklist`）。改一处必须同步另一处，
这是为了防止「清单长成 14 项、PR 模板还停在 6 项」这种谁都没错但没人守的状态。
