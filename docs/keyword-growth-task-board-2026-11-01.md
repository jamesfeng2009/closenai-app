# ClosenAI 关键词增长执行台账（2026-11-01）

> **用途：** 把 2026-10-27 的美国关键词调研拆成可跟踪任务，覆盖 ASO、SEO/GEO 内容、归因测量，以及为兑现关键词承诺必须补的产品面。
> **来源：** `docs/closenai-review-competitive-seo-geo-2026-10-25.md`、`product-positioning-2026-09-06.md`、`错位竞争再定位-2026-10-21.md`、`CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md`、`docs/feature-status.md`、2026-10-27 的 Ubersuggest 美国/英语关键词数据。
> **建立日期：** 2026-11-01｜**任务数：** 31（A 3 / B 5 / C 10 / D 4 / E 8 / F 1）
> **进度口径：** 只有 `DONE` 算完成；`TODO / DOING / REVIEW / BLOCKED` 都算未完成。

---

## 0. 使用规则

### 0.1 状态枚举（唯一口径）

| 状态 | 含义 | 谁可以改 |
|---|---|---|
| `TODO` | 已定义、未开工 | 任务负责人 |
| `DOING` | 已开工，有分支/草稿/PR | 任务负责人 |
| `BLOCKED` | 被外部账号、法务、主台账依赖阻塞，须写清阻塞项 | 任务负责人 |
| `REVIEW` | 交付物完成，等评审/验收 | 任务负责人 |
| `DONE` | 验收证据齐全 + 发布/上线完成 + 失败恢复路径存在 | 评审人 |
| `CANCELLED` | 明确不做，须写理由与重开条件 | 方案负责人 |

### 0.2 一条任务只有一个状态

1. **状态只写在 §1 总表的「状态」列。** §3 任务详情不重复写状态，避免同一任务出现两个状态。
2. **一行一个任务。** 禁止「TODO + DOING」「REVIEW/DONE」并列，禁止用 ✅/🚧 等表情代替状态。
3. **状态变化必须带证据。** 在 §1「验收摘要/证据」列写日期、URL、截图、测试名或提交号；只有一句「已完成」不算。
4. **§2 是 §1 的只读汇总，不是第二状态源。** 每次改完 §1 后重算 §2 的数字与任务 ID。
5. **任务定义变更走 ADR。** 只改状态、证据、目标日期，不改任务边界；要改边界先改本文件对应的详情并在变更记录里写原因。

### 0.3 `DONE` 的证据门槛

增长任务不套用后端「四件套」的字面形式，但必须有等价证据：

- **入口：** 页面 URL、商店字段、文案位置或可点击入口真实存在；
- **执行：** 已发布/已提交/已上线，不是只在本地草稿；
- **交付：** 用户能看到，或数据平台能查到对应记录；
- **失败恢复：** 页面 404/回滚、商店审核被拒、文案下架的恢复路径已写明；
- **证据：** 至少一条可复核的 URL、截图、导出文件或测试输出，附日期。

详情末尾追加一行：

```text
> 结案 2026-MM-DD｜证据：<URL/截图/测试名>｜发布：<页面/商店字段>｜回滚：<路径>
```

### 0.4 角色

| 角色 | 含义 |
|---|---|
| `ARCH` | 方案/产品负责人：定位、取舍、ADR、跨团队裁决 |
| `GROWTH` | 增长：ASO、内容、关键词、渠道、月度复盘 |
| `FE` | 前端：App 内入口、文案、实验开关、事件埋点 |
| `BE` | Python 后端：提示库、分析深度、数据接口 |
| `PLAT` | 平台/DevOps：商店账号、搜索控制台、站点发布、归因链接 |
| `LEGAL` | 法务/外部顾问：商标、对比页、隐私与医疗边界 |

### 0.5 与主台账的边界

- 本表**不替代** `CLOSENAI_V3_TASK_BOARD.md`，只收「关键词/ASO/内容/归因/支撑关键词承诺的产品补齐」。
- 主台账的 `T7-04`（生产现场验收）、`T7-05`（唯一官网与法务阻断）、`T7-07`（种子用户）、`T7-08`（技术 SEO）、`T7-09`（GEO）、`T7-10`（搜索测量）、`T7-11`（付费扩张闸门）**不重复立任务**，只作为本表的外部前置写进「依赖」列。
- **依赖列里的 `（发布闸）` / `（验证闸）` 只阻塞上线或真实验证，不阻塞草稿与内部准备**；这类任务可以保持 `TODO/DOING`。没有标注的依赖是硬前置，硬前置未满足时任务应为 `BLOCKED`。
- 本表任何页面/商店文案在发布前，都要先满足 `T7-05` 的唯一发布源与法务主体要求；否则只能写草稿，不能上线。

---

## 1. 任务总表

| ID | 工作流 | 任务 | Owner | 优先级 | 状态 | 依赖 | 验收摘要/证据 | 目标 |
|---|---|---|---|---|---|---|---|---|
| KG-01 | A 定位与承诺 | 全量对外承诺审计（绝对化记忆、AI companion、关系等级、医疗边界） | ARCH | P0 | `REVIEW` | — | 2026-11-02 交付 `docs/keyword-claims-audit-2026-11.md`：六列表 **70 行**（`:25` 表头），风险类型固定四类——绝对化记忆 32、陪伴平台语言 20、医疗边界 8、无证据预测 10；判定分布 `保留 24／改写 38／删除 4／待法务 4`，**汇总数字不许手写**，由 `test_summary_counts_match_the_table`（`backend/tests/test_keyword_claims_audit.py:184`）从表里现算再比。闸的三条硬判据：①**引用即自证**——每行「位置」必须打到真实文件的真实行且该行含「原文」（`:78`），行号漂／句子改了／文件没了都红；②**台账点名锚必在册**——`REQUIRED_ANCHORS`（`:137`）按 needle 现算行号钉住 `en.ts` 的 `step1Tagline`、`web/index.html` 的「成长陪伴」与「性格画像不限」、`site/src/i18n.ts` 两处、`site/public/r/index.html:131` 的 `It remembers every word you said.`；③**正向证人**——≥20 行「保留」＋三条否定式免责在册（`:170`），否则「整行删掉」也能让归零闸绿。「改法」列禁占位符与「同上」（`:107`）。实测：本闸 **15 passed**、三张闸合跑 36 passed／1 skipped、变异 C 族 **9/9 打红**＋GA1 绿、全量后端 **1847 passed／2 failed／91 skipped**。附带挖出两条**权益虚标**（`web/index.html:645`「性格画像不限」、`:648`「每日多条主动消息」，可指回 `backend/config.py:193-200` 与 `personality_service.py:563` 证伪）与一处口径冲突（App FAQ「对话已加密」vs 站点法务源「非端到端加密」）。**未**：38 条「改写」＋4 条「删除」是工单，本批一行文案未动；4 条「待法务」等 KG-02；`site/public/**` 不在 KG-23 禁语闸的扫描面内（已在 `:145` §4 登记）。见 `docs/growth/kg01-kg03-kg18-closure-2026-11-02.md` | 2026-11-05 |
| KG-02 | A 定位与承诺 | 法务审核包与签字（MBTI/Enneagram、竞品名、对比页、隐私与医疗声明） | ARCH+LEGAL | P0 | `BLOCKED` | KG-01、T7-05 | 法务意见书/签字邮件；商标使用说明；对比页合规清单 | 2026-11-19 |
| KG-03 | A 定位与承诺 | ADR：新功能取舍（三层次分析、私人未寄出的信、金句卡、AI 角色、匿名社区） | ARCH | P0 | `REVIEW` | — | 2026-11-02 交付 `docs/adr/2026-11-keyword-feature-scope.md`：§1 五个决定表（`:15`）——每日情绪记录**保留**、三层次分析**做小实验**（4 周读数不达标即停）、温柔陪伴 AI 角色**不做**、匿名树洞/社区**不做**（改私人未寄出的信研究）、金句卡**只做用户原话卡**；§2 逐条依据（`:25`）、§3 后果（`:86`）、§4 重开流程（`:99`）。与 Baseline §1.2/§3 对齐靠**引用即自证**：通篇禁裸 `:NNN`（`test_keyword_scope_adr.py:67`），承重锚逐条 needle 核对（`:104`）——`backend/config.py:211` 的 `mood_analysis_depths_enabled: bool = False`、`backend/routers/mood.py:83` 的 40402 前置、`backend/routers/share.py:115`、`backend/tests/test_absolute_memory_claims.py:59`、`backend/data/copy/en.json:287`/`:294`。两条「不做」做成可检查事实：社区面检索 ≥30 文件且命中 0（`:120`，检索面为空不算证）、落地站仍禁 `AI companion`（`:138`）。实测：本闸 **16 passed**、三张闸合跑 36 passed／1 skipped、变异 D 族 **6/6 打红**＋GA2 绿、全量后端 1847 passed。钉住 D1/D2 边界（不新增端点、不新建表，`:158`）。**未**：商标与医疗边界结论等 KG-02 法务签字，ADR 里两条「不做」目前只有架构＋内容依据；实验读数等 KG-28 的 4 周。见 `docs/growth/kg01-kg03-kg18-closure-2026-11-02.md` | 2026-11-05 |
| KG-04 | B ASO | en-US ASO 文案包（标题/副标题/关键词字段 + 字符校验） | GROWTH | P0 | `REVIEW` | KG-01 | 2026-11-02 交付：唯一事实源 `docs/aso/en-US-2026-11.json`（标题 29/30、副标题 A 30/30 与 B 28/30、关键词 99/100 与 100/100、Play 短描述 69/80，A/B 两版各带假设与判停）＋可读版 `docs/aso/en-US-2026-11.md`＋校验器 `scripts/check_aso_copy.py`（ASC 上限取脚本字面量 `:36`＝30/30/100，**不由被检对象声明**）＋闸 `backend/tests/test_aso_copy_pack.py` **31 passed**（长度、关键词与标题用词重叠、竞品品牌词、8 条禁语词边界正则、判停可判性＝`:75` 起现算；md §1 表与 §2 粘贴块两侧对拉；12 条参数化用例逐条制造不合格输入）。CLI 全绿 exit=0、`--self-test` 用 31 字符标题证明会红。**A 版副标题与 KG-01 审计判定冲突**：同句在 `docs/keyword-claims-audit-2026-11.md:96` 判 `待法务`，故 KG-02 签字前 A 版不单独提交、B 版与保守版关键词可提交。变异 F1–F10 **10/10 打红**＋GA1 绿（首轮 8/10，两条是判据覆盖面不足，已当场补强，根因见 `docs/growth/kg04-kg05-kg08-closure-2026-11-02.md` §4）。见 `docs/growth/kg04-kg05-kg08-closure-2026-11-02.md` | 2026-11-08 |
| KG-05 | B ASO | 关键词月度校验与淘汰规则（≥500 门槛、来源、日期、替换） | GROWTH | P1 | `REVIEW` | KG-04 | 2026-11-02 交付 `docs/aso/keyword-tracker-2026-11.md`：§1 四条规则 R1–R4（R1 本轮**零命中**、R2 本轮**未跑**、R3 命中 4 条、R4 全覆盖）、§2 43 词×8 列（词/量/来源/日期/意图/落点/状态/理由与下次动作）、§3 分布 6/25/3/9、§4 四条已知缺陷、§5 下次复核。闸 `backend/tests/test_keyword_tracker.py` **14 passed**：词集合与月量与 `outdated` 标记**与台账 §4 现算对拉**、保留词落点必须真存在或挂真 KG 任务号、竞品词与 `docs/aso/en-US-2026-11.json` 的品牌清单对拉、R1/R2 的未验证状态**按行**声明且 R1 最小值与 R3 命中数现算、§3 分布与 §4 的 12 个 URL 数现算。变异 G1–G13 **13/13 打红**＋GA2 绿。**验收原文「至少两种工具交叉核对」未达**：43 词全部单源（Ubersuggest 美国/英语 2026-10-27），第二来源要 Apple Search Ads / Keyword Planner / GSC 账户，KG-21 仍 `BLOCKED`；R1、R2 本轮没有读数验证。见 `docs/growth/kg04-kg05-kg08-closure-2026-11-02.md` | 2026-12-01 |
| KG-06 | B ASO | App Store Connect 提交与副标题 A/B | PLAT+GROWTH | P0 | `BLOCKED` | KG-04、T7-05（发布闸） | 提交截图、审核状态、A/B 配置；两周后读数与结论 | 2026-11-20 |
| KG-07 | B ASO | Google Play 商店页（短描述/完整描述/关键词密度） | PLAT+GROWTH | P2 | `BLOCKED` | KG-04、T7-05 | Play Console 草稿或上线截图；短描述 ≤80 字符；无医疗/绝对化承诺 | 2027-01-15 |
| KG-08 | B ASO | 截图与预览创意简报（首屏讲“记得你/结果回访”） | GROWTH+FE | P1 | `REVIEW` | KG-01、KG-04 | 2026-11-02 交付 `docs/aso/screenshot-brief-2026-11.md`：六帧表（每帧主张≤40 字符＋对应真实界面源＋取图条件＋证据标注）、三条硬规矩、与副标题 A 的关系、预览视频本轮不做与重开条件、未做四条。闸 `backend/tests/test_screenshot_brief.py` **20 passed**：六帧满格无占位、主张长度现算、首帧复用 KG-04 的 8 条禁语尺子且必须限定式（不用 `remembers everything`）、10 条界面锚点**逐条打开真文件核该行**（`MemoriesScreen.tsx:127`、`GrowthRecordsScreen.tsx:217`、`PrivacySecurityScreen.tsx:81` 等）、至少一帧落在结果回访且该帧界面源必须指到 `GrowthRecordsScreen.tsx`、首帧与待法务的副标题 A 必须是不同句（两面对拉）、开头引用的审计条数与审计表现算对拉。变异 H1–H9＋H3b **10/10 打红**＋GA3 绿。**截图一张未取**（本机无 RN/Expo 渲染条件，逐帧写了取图条件）；预览视频本轮不做。见 `docs/growth/kg04-kg05-kg08-closure-2026-11-02.md` | 2026-11-15 |
| KG-09 | C SEO/GEO | 四簇内容简报与证据包（反思方法/长期自我理解/行动结果复盘/AI 洞察可信度） | GROWTH | P1 | `REVIEW` | KG-01；KG-02（发布闸） | 4 份 brief：搜索意图、目标 URL、H1、证据、内链、法务边界；每份先写 1 篇支柱页提纲｜2026-11-02 落 `docs/content/briefs-2026-11/` 四份（八节固定标题），闸 `backend/tests/test_content_briefs.py` 40 passed，变异红闸 17/17＋绿闸 3/3。簇面刻意不新造：四簇就是 `backend/content/reflection_prompts.json` 的 cluster（8-8-7-7），两侧对拉由该闸判。**未**：简报是「写什么、凭什么写」，页面正文属 KG-10～KG-14。三面判据与当场打红见 `docs/growth/kg09-kg14-pages-closure-2026-11-03.md` | 2026-11-12 |
| KG-10 | C SEO/GEO | `/ai-journal` + `/journal-app` 支柱页 | GROWTH | P1 | `REVIEW` | KG-09；T7-05（发布闸）、KG-02（发布闸） | 两页 200；独立 title/description/H1/canonical；hreflang + x-default；无绝对化记忆承诺｜2026-11-03 交付：两页条目在唯一内容源 `site-content/pages.json`，正文 `docs/content/pages/ai-journal.en.md`／`docs/content/pages/journal-app.en.md`，生成物落 `docs/content/build/`（带 `noindex`、未部署、脚本硬拒 `web/`）；闸 `backend/tests/test_seo_page_source.py`＋`scripts/build_seo_pages.py --check`，变异红闸 27/27＋绿闸 5/5。**验收未达的一条**：「两页 200」要发布，而 KG-02／T7-05 仍 `BLOCKED` ⇒ 本机只证到「页面源过闸＋canonical/hreflang/x-default/sitemap 在生成物里」，不许引用成已上线。判据、变异 27/27＋5/5 与偏差登记见 `docs/growth/kg09-kg14-pages-closure-2026-11-03.md` | 2026-11-30 |
| KG-11 | C SEO/GEO | `/mood-tracker` 支柱页 | GROWTH | P1 | `REVIEW` | KG-09；T7-05（发布闸）、KG-02（发布闸） | 覆盖 mood tracker/mood journal/mood tracking app；讲签到、热力图、趋势；明确非诊断｜2026-11-03 交付：`docs/content/pages/mood-tracker.en.md`——三个变体各自真出现在正文叙述行（只在标题堆词＝红，G07）、7/30 天两档写死不写成自定义区间（`frontend/src/screens/InsightsScreen.tsx:62`）、缺签到那格留空不补值（`frontend/src/screens/HomeScreen.tsx:85`）、非医疗句在场且正文不含 `diagnos*`／`therap*`／`treat*` 词干。**同 KG-10 的限制**：页面 200 待发布。G07 堆词判据的实测见 `docs/growth/kg09-kg14-pages-closure-2026-11-03.md` | 2026-11-30 |
| KG-12 | C SEO/GEO | `/self-reflection` + `/journal-prompts` 支柱页 | GROWTH | P1 | `REVIEW` | KG-09；T7-05（发布闸）、KG-02（发布闸） | 30 条原创提示；每条有目的、示例、追问；不抄提示清单；内链到反思功能｜2026-11-03 交付：`docs/content/pages/self-reflection.en.md`（每周五行模板）＋`docs/content/pages/journal-prompts.en.md`——正文只留一个插入点，30 条由 `scripts/build_seo_pages.py` 从 `backend/content/reflection_prompts.json` 现算展开（逐条带 `why` 目的／`question` 示例／`follow_up` 追问与 id 回溯，按 `function` 分 18-7-5 目录锚）；正文手抄提示 id＝红。**限制**：「逐条作者与日期」只到库级（`version` 字段）＋页级 `author`，已作为限制写进 `docs/keyword-content-gate.md` §3 的抽检行，不装作已达。现算展开的实现与限制见 `docs/growth/kg09-kg14-pages-closure-2026-11-03.md` | 2026-12-15 |
| KG-13 | C SEO/GEO | `/self-discovery` + `/personality-test` 支柱页 | GROWTH+LEGAL | P1 | `TODO` | KG-09；T7-05（发布闸）、KG-02（发布闸） | 说明 MBTI/Enneagram 是框架不是诊断；强调“从你的记录生成，不是一次性测验”；商标用词过法务 | 2026-12-15 |
| KG-14 | C SEO/GEO | `/self-improvement` + `/goal-tracker` 支柱页 | GROWTH | P1 | `REVIEW` | KG-09；T7-05（发布闸）、KG-02（发布闸） | 讲 Goal → Action → Outcome 与“预期 vs 实际”；不是通用打卡器；含一个可执行模板｜2026-11-03 交付：`docs/content/pages/self-improvement.en.md`（预期 vs 实际四格＋方案 §KG-14 点名的那句英文主张）＋`docs/content/pages/goal-tracker.en.md`（到期回访当差异化段落、14 天内可核对的每周模板）；「系统不替你判定成败」用 `backend/models/canonical.py:506` 的 `unknown` 默认值当机械依据，不写成形容词。两页模板不重文（G02 哈希查重），同页互链。**同 KG-10 的限制**：页面 200 待发布。`unknown` 默认值那条机械依据见 `docs/growth/kg09-kg14-pages-closure-2026-11-03.md` | 2026-12-31 |
| KG-15 | C SEO/GEO | `/overthinking` 支柱页 | GROWTH+LEGAL | P2 | `TODO` | KG-09；T7-05（发布闸）、KG-02（发布闸） | 区分反刍与有证据的反思；给 3 步练习；危机求助提示；无治疗承诺 | 2026-12-31 |
| KG-16 | C SEO/GEO | 竞品对比页（Rosebud / Day One 替代） | GROWTH+LEGAL | P1 | `TODO` | KG-09；KG-02（发布闸）、KG-31（发布闸） | 两页；只写可核验的公开差异；标来源与日期；不贬损、不伪造评分；法务通过 | 2027-01-15 |
| KG-17 | C SEO/GEO | 内链与 CTA 规范（每页到下载/等待名单/相关页） | GROWTH | P1 | `REVIEW` | KG-09 | 每页 ≥2 条相关内链 + 1 个 CTA；无孤儿页；断链 0；面包屑与导航可达｜2026-11-03 交付爬虫闸 `scripts/check_site_links.py`（判据与在册表见 `docs/seo/internal-links-2026-11.md`，pytest 面 `backend/tests/test_site_links.py` **12 passed**，结案 `docs/growth/kg17-links-closure-2026-11-03.md`）：入链只数**别的页面正文里的内链**（sitemap／页脚／面包屑／生成器拼的 Related 段一律不算，否则每页白捡三条、孤儿这个概念被判据本身抹掉）。实测 7 页／17 条正文内链／每页入链 2–4 条（下限 2 达标）／3 跳内从 `ai-journal` 全可达／CTA 只有 `waitlist` 一个真动作且 `#cta` 解析到 `web/index.html:689` 的真 id／7 页生成物 0 条裸 `href="#"`；变异 harness **红闸 15/15＋绿闸 3/3、红闸失手 0 条**。**首跑当场打红的四类**：`ai-journal`／`journal-app`／`journal-prompts` 三页各只有 1 条入链（补三条真·按内容该指的正文内链，同批把 `related` 与正文对齐成同一集合）、生成器把面包屑 Home 指向不存在的 `/en/`（7 页各一条断链，改在 `scripts/build_seo_pages.py:530`）、现网面 `web/index.html` 的 5 条裸 `href="#"`。**闸自己的三处误报也一并修了**（合法 `#fragment` 被当成裸 `#`、站内绝对路径没映射到真文件、offender 去重把「同链坏 5 次」和「坏 1 次」看成一样）——判据先修对，再拿它打人。**三条硬限制**：① 未部署（`KG-02`／`T7-05` 仍 `BLOCKED`，生成物带 `noindex`），判的是草稿面图不是线上图；② 验收句「CTA 在移动端可点、无遮挡」需真机渲染，本闸只判 href 与锚点解析 ⇒ 这条没取证，所以本行只到 `REVIEW`；③ 台账「建立面包屑、导航、页脚」里的**页脚**在支柱页生成物里没有，发布时由模板层补，已登记不装作做了。现网面 5 条裸锚**只登记不修**（归 KG-23／KG-02 的文案与法务面，且 `web/` 从未进版本控制） | 2026-12-31 |
| KG-18 | C SEO/GEO | 内容质量闸（原创、来源、日期、非医疗、无虚假案例） | GROWTH+ARCH | P0 | `REVIEW` | KG-09 | 2026-11-02 交付 `docs/keyword-content-gate.md`（§1 十四项 G01–G14 五列表 `:14`：检查项／**机械判据（谁来看）**／不合格怎么办／归属；§2 与既有闸的关系 `:33`；§3 抽检记录 `:42`；§4 PR 模板同步口径 `:58`）＋新建 `.github/pull_request_template.md`（为什么改／验收与证据／G01–G14／风险与回滚／未做与残余）。闸的特别处是**两侧同源**：`test_pr_template_mirrors_the_checklist`（`backend/tests/test_keyword_content_gate.py:92`）把清单与模板的 ID 集合**和逐项名称**对拉，任一边改名即红；方案 §4.7 点名的 12 个主题逐条 needle 化（`NAMED_TOPICS` `:32`＋判据 `:73`），改名／漏项即红；判据不许写「—」、归属必须是仓库里真存在的角色（`:79`）。**会随页面落地自动收紧的判据**：`test_sampling_covers_two_pages_once_pages_exist`（`:142`）只认 `site-content/pages.json` 的 `"slug":`（`_page_count()` `:116`）。实测：本闸 **6 passed／0 skipped**（2026-11-03 内容源落地 7 页 ⇒ `_page_count()` 从 0 变 7，那条判据当场自己收紧成「要 ≥2 行真抽检」，本批随之回填 2 行（`ai-journal`／`journal-prompts`）；其余 5 页过了机械判据但**没做过人工逐页通读**，所以不进表——闸绿 ≠ 抽检过；已把 `docs/content/drafts/journal-prompts.*.md` 排除的理由写进 `:51`，防止拿 KG-26 的导出凑「抽检 2 页通过」这种假交付）、三张闸（审计＋ADR＋本清单）合跑 **38 passed／0 skipped**（第五批当时是 36 passed／1 skipped，那条 skip 正是本闸的抽检判据）、六张闸（再加简报／页面源／台账）合跑 **105 passed／1 skipped**、变异 E 族 **7/7 打红**＋GA3 绿、第七批的页面源 harness **27/27 红＋5/5 绿**、全量后端 1981 passed。**未**：其余 5 页的人工通读、真实 PR 走一遍清单（等首篇支柱页提交评审）；G04 的「页面主体名与 `web/privacy.html` 一致」仍未判——运营主体要等 T7-05，本批生成物一律带 `noindex` 且脚本硬拒 `web/`。见 `docs/growth/kg01-kg03-kg18-closure-2026-11-02.md`，第七批的回填与两处判据补强见 `docs/growth/kg09-kg14-pages-closure-2026-11-03.md` | 2026-11-19 |
| KG-19 | D 测量 | 关键词渠道指标字典与漏斗 | GROWTH+BE | P0 | `REVIEW` | T7-06、KG-04 | 2026-11-02 代码面交付：`docs/metrics-dictionary.md:94` 起新增「关键词渠道七格」9 列表 × 7 行（`search_reach/store_page_view/install/first_value/first_evidence_insight/first_action/due_outcome`），核心七格表一列未动、新表排在其后且表头唯一；后四格指回 `mood_checkins`+`growth_records`、`insights`+`insight_evidence`、`personal_commitments`、`outcomes`，引用区间由闸用 `_step_def_line()` 找 `key="…"` 那行自证（`metrics_export.py:69-78/:79-88/:89-98`）；前三格明写只能控制台人工导出、`search_reach` 标 KG-21 `BLOCKED`、零流量按 `no_users` 报不按 0。`test_keyword_funnel_doc.py` **8 passed**、与 `test_metrics_export.py` 合跑 **26 passed**、变异 **K 族 9/9 打红 + GK1 绿**。未：真实商店/站长数据接入（KG-21 仍 BLOCKED，所以本表是**口径就位、数据待接**，不许被引用成「漏斗已有读数」）、`docs/metrics-dictionary.md:36` 那处既有行号漂移按规矩只登记不顺手修。见 `docs/growth/keyword-funnel-doc-2026-11.md` | 2026-11-08 |
| KG-20 | D 测量 | UTM/深链归因规范（商店 campaign link、延迟深链、隐私） | GROWTH+PLAT | P0 | `REVIEW` | KG-19 | 2026-11-02 代码面交付：`docs/growth/attribution-2026-11.md` 立口径（做/不做对照、**不上送**的三条理由含 22 个白名单键逐条列出、UTM 五键、campaign link 只认 `pt/ct/mt` 且不复制 Apple 取值表、首触/最近触/captures/保留期、延迟深链天花板＝只「已装唤起」这条完整、新装必断故读数是**下界**、回滚删两文件）；实现 `frontend/src/services/attribution.ts`（品牌域一票否决与 `referral.ts` 同源、48 字符与形状闸、PII 六类正则＋base64 只判解码原文、路径规范化拒穿越、首触不覆盖、**读回重走同一套清洗**、`attributionForDiagnostics()` 出诊断行）；`__tests__/attribution.test.ts` **31 passed**、与 `referral.test.ts` 合跑 64 passed、tsc **exit=0**、变异 **A 族 11/11 打红**、绿闸 GA1/GA2 保持绿（GA2 把字符集放宽到允许 `@`，邮箱仍被 PII 层挡＝纵深不是死代码）。边界证人：不 import analytics、不调 `track`、不碰推荐码解析。**未**：深链发射点未接线（生产仍零本机归因）、`@closenai/attribution` 未进删除清理清单、无第二来源交叉核对。勘误：A10 首轮 exit=0，根因是我的用例先撞上「首字符必须小写」缺区分度，`attribution.test.ts:191` 补小写穿越串后复跑打红，全量 **20/20 红 + 3/3 绿** | 2026-11-19 |
| KG-21 | D 测量 | GSC/Bing/百度与商店数据接入 | PLAT+GROWTH | P1 | `BLOCKED` | T7-08、KG-10 | 域名验证截图；sitemap 提交记录；基线导出 CSV；Bing AI Performance 可见则留档 | 2026-11-26 |
| KG-22 | D 测量 | 月度复盘与停/扩规则 | GROWTH+ARCH | P1 | `TODO` | KG-19、KG-20、KG-21 | 复盘模板；20–30 个问题基线；8 周停/改/扩阈值；首份月度报告与决定 | 2026-12-31 |
| KG-23 | E 产品补齐 | 绝对化记忆承诺整改（App onboarding + 官网 + 商店） | FE+GROWTH | P0 | `REVIEW` | KG-01 | 2026-11-01 代码面清零（en.ts:442/zh.ts:435 改「你选择分享的内容」＋site/web/copy 三面同步），`backend/tests/test_absolute_memory_claims.py` **40 passed**，grep 输出留档于 `docs/paywall-copy-2026-11.md` §4；禁词残留清单同文件 §6.5。未：商店截图/元数据同步、法务「可删除」一致性确认（挂 KG-02） | 2026-11-08 |
| KG-24 | E 产品补齐 | 付费墙价值文案改写（从配额改为长期价值） | FE+ARCH | P1 | `REVIEW` | KG-01；KG-02（发布闸） | 2026-11-01 八条权益逐条指回实现（`en.ts:281-284`／`:299-302` → `config.py:196-200`、`personality_service.py:563`、`growth_service.py:668`）；删 `plusFeatureProactive`（`cron_service.py:477` 全天一条槽）；`paywallClaims.test.ts` **50 passed**＋2 条变异打红；A/B 假设与判停写在 `docs/paywall-copy-2026-11.md` §5。未：截图、A/B 读数（需 KG-31 流量） | 2026-11-15 |
| KG-25 | E 产品补齐 | 首日价值路径（首次签到→首条证据洞察→首个行动→结果回访） | FE+ARCH | P0 | `REVIEW` | T7-07（验证闸） | 2026-11-01 漏斗补至 9 步（`frontend/src/services/funnel.ts:29-38`）＋HomeScreen 下一步指导卡（`HomeScreen.tsx:559-571`）；`funnel.test.ts`/`nextStep.test.ts` 断言改精确数组后全绿；九步表、死胡同审计、失败恢复路径见 `docs/growth/first-value-path-2026-11.md`。未：5 名内部用户走通与 7 日循环读数（需 KG-31 生产流量） | 2026-11-30 |
| KG-26 | E 产品补齐 | 反思提示库（journal prompts，30 条） | FE+BE+GROWTH | P1 | `REVIEW` | KG-09、KG-03 | 2026-11-01 代码面交付：`backend/content/reflection_prompts.json` 30 条／四簇 8-8-7-7／en+zh（`function` reflect18-goal7-mood5），`GET /api/v1/reflection-prompts`（**不放 `/reflections` 前缀下**，N8 Prefix 会把子路径带走，见结案文档 §6A）＋写侧溯源（版本不符 40001、空白正文 schema 闸）＋`PromptLibrarySheet`／Reflect 入口／15 语言×13 键／埋点三面；站点草稿改由 `scripts/export_prompts.py --check` 做成漂移闸。`test_prompt_library.py`＋`test_reflection_prompt_save.py` **46 passed**、`promptLibrary.test.ts` **38 passed**、全量 jest **1732 passed**、tsc 0 错、8 组变异自证。未：真机截图（本机渲染不了 `.tsx`，屏的闸是源码扫描）、保存率读数（需生产流量）、非 en/zh 13 语言正文。全部证据与三处偏差见 `docs/growth/prompt-library-2026-11.md` | 2026-12-15 |
| KG-27 | E 产品补齐 | 情绪统计可见性审计（周视图/热力图/趋势） | FE | P1 | `REVIEW` | — | 2026-11-01 审计与改造完成：空态/失败态/有数据三态（`InsightsScreen.tsx:1004-1054`，空态在模糊门**外**）、7/30 天切换（`:62-64`）、Explore＋Profile 双入口＋`Insights?initialSection=mood` 定位滚动；`recentDays` 移入 `components/charts/transforms.ts` 并钉死「不碰本地时区」；`moodStats.test.ts` **33 passed**＋4 条变异打红；审计表见 `docs/growth/mood-stats-audit-2026-11.md`。未：真机截图；jest 环境不能换时区（实测 `getHours()` 仍为 8）已记为交付限制，改用结构闸＋手算跨年网格 | 2026-11-30 |
| KG-28 | E 产品补齐 | 三层次情绪分析实验（轻松/探索/深潜） | FE+BE+ARCH | P1 | `REVIEW` | KG-03 | 2026-11-02 代码面完成：`POST /api/v1/mood/analysis`（118 router＋1 app＝119 登记行；Ingress 不用改，mood 只切两条 Exact，新端点自动留 py-api，判据 `test_k8s_manifests.py:305`）；三档只换窗口/材料/来源数/max_tokens（不是三个人格）；探索与深潜的每条结论必须逐字引用用户原话，未命中⇒`pattern=null`＋确定性降级；危机排在配额之前且不调模型；独立第三配额池；10 分钟进程内幂等。默认关（`MOOD_ANALYSIS_DEPTHS_ENABLED`），入口只看 `features.mood_analysis_depths`。测试：后端 30 条＋本批相关闸 82 passed、全量后端 1803 passed（2 条既有红灯非本批）、前端 `moodAnalysis.test.ts` 49 条＋全量 1819 passed／63 suites、tsc 0 错、变异自证 **23/23 红＋2/2 绿**。详见 `docs/growth/mood-analysis-2026-11.md`（9 处与方案 §6.2 的偏差都带根因）。未：4 周读数与真机走查（需 KG-31 流量） | 2027-01-15 |
| KG-29 | E 产品补齐 | 私人“未寄出的信”模式研究（替代匿名树洞） | ARCH+GROWTH | P2 | `TODO` | KG-03；T7-07（验证闸） | 5–10 人访谈纪要；需求/风险/成本结论；决定做/不做；不做则写重开条件 | 2027-01-15 |
| KG-30 | E 产品补齐 | 用户原话金句卡（只用用户自己的话） | FE+ARCH | P2 | `REVIEW` | KG-03 | 2026-11-01 代码面交付：`POST /api/v1/share/quote-card`（请求体**只有** `source_type`/`source_id`，句子一律服务端从库取——端点不当任意文本→带本人推荐码 PDF 的生成器）；AI 抽取的记忆走 **40405**（不是 40403，见结案文档 §6A）、软删／被取代 40403、`daily_insight` 冒充反思 40402、`get_owned` 三行 WHERE（id+user+record_type）、AI 草稿保存的那句出卡但改措辞（`quote.draft_note`）；280 上限两侧同值同裁法；前端两个入口（记忆详情／反思行）＋卡页 `quote` 分支，预览不发请求、导出是唯一授权点，`FUNNEL_SOURCES` 加 `'quote'`；文案三面 `backend/data/copy` 15×6、locale 15×6、`pdf.failed.notOriginal` 15。`test_quote_card.py` **16 passed**、`quoteCardWiring.test.ts` **34 passed**、全量 jest **1770 passed / 62 suites**、tsc 0 错、变异自证 **红闸 22/22＋绿闸 2/2**（其中两条是开发中真踩到的：文案键双命名空间、高棉/泰语 40405 与 40403 同文）。未：真机 PDF 导出与截图（原生 `expo-sharing` 本机跑不了）、分享率读数。全部证据与 5 处偏差见 `docs/growth/quote-card-2026-11.md` | 2027-01-31 |
| KG-31 | F 上线闸门 | 承诺生产化闸（memory/insight/reflection 灰度开量） | PLAT+BE+FE | P0 | `BLOCKED` | T7-04、T7-07 | 切流键开量记录；7 天生产流量与失败率；回滚演练；不再以“零流量”交付 | 2026-12-31 |

---

## 2. 进度快照

> 由 §1 汇总；只用于看总数，不作为第二状态源。

| 状态 | 数量 | 任务 |
|---|---|---|
| `TODO` | 5 | KG-13、KG-15、KG-16、KG-22、KG-29 |
| `DOING` | 0 | — |
| `BLOCKED` | 5 | KG-02、KG-06、KG-07、KG-21、KG-31 |
| `REVIEW` | 21 | KG-01、KG-03、KG-04、KG-05、KG-08、KG-09、KG-10、KG-11、KG-12、KG-14、KG-17、KG-18、KG-19、KG-20、KG-23、KG-24、KG-25、KG-26、KG-27、KG-28、KG-30（2026-11-01 第一批 KG-23/24/25/27＋第二批 KG-26/KG-30＋第三批 KG-28 代码面完成＋测试绿＋变异自证；2026-11-02 第四批 KG-19/KG-20、第五批 KG-01/KG-03/KG-18、第七批 KG-09～KG-14、第八批 KG-17 同口径，等评审与真机/商店侧验收） |
| `DONE` | 0 | — |
| `CANCELLED` | 0 | — |
| **合计** | **31** |  |

按工作流：

| 工作流 | 总数 | TODO | REVIEW | BLOCKED | DONE |
|---|---:|---:|---:|---:|---:|
| A 定位与承诺 | 3 | 0 | 2 | 1 | 0 |
| B ASO | 5 | 0 | 3 | 2 | 0 |
| C SEO/GEO | 10 | 3 | 7 | 0 | 0 |
| D 测量 | 4 | 1 | 2 | 1 | 0 |
| E 产品补齐 | 8 | 1 | 7 | 0 | 0 |
| F 上线闸门 | 1 | 0 | 0 | 1 | 0 |

> 重算 2026-11-01：第一批 KG-23／KG-24／KG-25／KG-27 代码面交付，按 §0.1 的「交付物完成，等评审」置 `REVIEW`；`DONE` 需发布/上线与截图证据，本机不可证，未自封。
> 同日第二批 KG-26 同口径置 `REVIEW`（`docs/growth/prompt-library-2026-11.md`：46＋38 条本批测试、全量 jest 1732 passed、tsc 0 错、8 组变异自证、3 处与方案的偏差都带根因）。§1 行 96／快照 21-5／工作流 E 3-5 同步改。KG-29／KG-30／KG-28 仍是 `TODO`，不顺手推进。
> 同日第二批的 **KG-30** 同口径置 `REVIEW`（`docs/growth/quote-card-2026-11.md`：后端 16 条＋前端新闸 34 条、全量 jest 1770 passed / 62 suites、tsc 0 错、契约 116→117（登记表 118 行、§7.1 PDF 6 行）、变异自证 22/22 红＋2/2 绿、5 处与方案 §6.3 的偏差都带根因）。§1 行 100／快照 20-6／工作流 E 2-6 同步改。KG-28 是第三批、KG-31 是最后一批，仍未动。
> 2026-11-02 第三批 **KG-28** 同口径置 `REVIEW`（`docs/growth/mood-analysis-2026-11.md`：后端新增 30 条＋本批相关闸合跑 82 passed／全量后端 1803 passed（2 条既有红灯与 KG-28 无关：`metrics_export.py` 的 canonical 引用、R5 跨进程六条要真库＋真 broker）、前端 `moodAnalysis.test.ts` 49 条＋全量 jest 1819 passed／63 suites、tsc 0 错、契约 117→118（登记表 119 行）、变异自证 **23/23 红＋2/2 绿**、9 处与方案 §6.2 的偏差都带根因）。§1 行 98／快照 19-7／工作流 E 1-7 同步改。**KG-31 仍未动，且它不是代码面能收尾的一批**：Go＋Python 双侧、部署面、7 天生产流量与 §7 四段断点，本机无生产流量 ⇒ 只能给判据与阻塞清单，不能自封 `REVIEW`。
> 2026-11-02 第四批 **KG-19／KG-20** 同口径置 `REVIEW`（`docs/growth/keyword-funnel-doc-2026-11.md` ＋ `docs/growth/attribution-2026-11.md`：`test_keyword_funnel_doc.py` 8 passed、与 `test_metrics_export.py` 合跑 26 passed；`attribution.test.ts` 31 passed、与 `referral.test.ts` 合跑 64 passed、tsc exit=0；同一 harness 串行跑 **红闸 20/20（K 族 9＋A 族 11）＋绿闸 3/3**；全量后端 **1811 passed / 2 failed / 90 skipped**（那 2 条是既有红灯，与本批无关：`test_canonical_schema.py` 的 canonical 引用、`test_r5_interop_broker.py` 要真库＋真 broker）、全量前端 jest **1850 passed / 64 suites**；3 处带根因勘误：K 族首轮 2 条误红是判据太粗、A10 首轮 exit=0 是用例缺区分度）。§1 行 89／90／快照 17-9／工作流 D 1-2 同步改。**KG-19 的表是口径就位、数据待接**（KG-21 仍 `BLOCKED`），KG-20 的**深链发射点尚未接线** ⇒ 本机归因在生产上仍是零，两条都不许引用成「已有读数」。
> 2026-11-02 第五批 **KG-01／KG-03／KG-18** 同口径置 `REVIEW`（`docs/growth/kg01-kg03-kg18-closure-2026-11-02.md`：三张新闸 `test_keyword_claims_audit.py` 15 passed＋`test_keyword_scope_adr.py` 16 passed＋`test_keyword_content_gate.py` 5 passed／1 skipped，合跑 **36 passed／1 skipped**；同一 harness 串行 **红闸 22/22（C 族 9＋D 族 6＋E 族 7）＋绿闸 3/3、红闸失手 0 条**；全量后端 **1847 passed／2 failed／91 skipped**（比第四批基线正好 +36 passed +1 skip，那 2 条既有红灯与本批无关）。三张闸共同的风格是**引用即自证＋正向证人**：审计表每行「位置」必须打到真实文件的真实行且含「原文」，ADR 通篇禁裸 `:NNN`，清单与 PR 模板**两侧同源**对拉。变异当场暴露并补强两处判据缺陷：① C5 首轮 exit=0（占位符判据只抓整格，`…改成：待补` 绕过）；② `_page_count()` 把 `docs/content/drafts/` 的 KG-26 导出当页面数——若不修，就会拿它凑出「抽检 2 页通过」的**假交付**。§1 行 71／73／88／快照 14-12／工作流 A 0-2、C 9-1 同步改。**三条硬限制**：KG-01 的 38 条「改写」＋4 条「删除」是**工单**，本批一行产品文案未动（改了会撞「原文必须在被引行」，须同批更新表行）；KG-18 的抽检判据当前 **skip＝未跑，不算绿**（`site-content/pages.json` 还不存在，等任务 #12）；3 条「待法务」等 KG-02 签字（该行是 2026-11-02 第四批当日口径；KG-04 的商店副标题入表后为 4 条，以 §2 与 KG-01 行为准）。附带成果：发现现网 `web/index.html:645`「性格画像不限」与 `:648`「每日多条主动消息」两条**权益虚标**（可指回 `backend/config.py:193-200`、`backend/services/personality_service.py:563` 证伪），以及 App FAQ「对话已加密」与站点法务源「非端到端加密」的口径冲突（归 KG-02）。

> 2026-11-02 第六批 **批次 3 收尾：把台账自身也做成靶**（本批不改任何任务状态，KG-04／KG-05／KG-08 仍是第五批末的 `REVIEW`）。新增第 4 张闸 `backend/tests/test_keyword_task_board.py`：§2 的六条状态行（条数＋任务清单＋合计）、「按工作流」五列、§3 详情小节集合、`REVIEW`／`DONE` 的证据列必须指向仓内真存在的文件、全文 `路径:行` 完整引用，一律**从 §1 现算再比**；再加一条「下游抄台账的行号」证人（该闸 `:199`，2026-11-03 第七批补强后行号下移一格）。**这一行的插入当场验证了那条判据**：台账 §3 起全体下移两行，`docs/aso/en-US-2026-11.md`／`docs/aso/keyword-tracker-2026-11.md`／`docs/keyword-claims-audit-2026-11.md` 三处对 `:224`／`:805` 的引用立刻被打红，本批随之同步成 `:226`／`:807`。实测：台账闸 **9 passed／1 skipped**（`DONE` 那支条件性未跑＝不算绿）、五张闸合跑 **90 passed／1 skipped**；同一 harness 串行 **红闸 42/42（F 10＋G 13＋H 10＋B 9）＋绿闸 4/4、红闸失手 0 条**；全量后端 **1922 passed／2 failed／92 skipped**（比第五批基线 1847/2/91 正好 +75 passed +1 skip，那 2 条既有红灯与本批无关）。结案：`docs/growth/kg04-kg05-kg08-closure-2026-11-02.md` §3、§4 与 §5 第 7–9 条。

> 2026-11-03 第七批 **KG-09／KG-10／KG-11／KG-12／KG-14** 同口径置 `REVIEW`（结案 `docs/growth/kg09-kg14-pages-closure-2026-11-03.md`：唯一内容源 `site-content/pages.json` 七页＋正文 `docs/content/pages/`＋生成器 `scripts/build_seo_pages.py`（`--check` 是 CI 入口，`write_all()` 见 `scripts/build_seo_pages.py:555`，硬拒 `web/`）＋新闸 `backend/tests/test_seo_page_source.py` **18 passed**；与 KG-18 清单闸（6）、KG-09 简报闸（40）、台账闸（9）四张合跑 **73 passed／1 skipped**，六张闸（再加承诺审计 16 与 ADR 16）合跑 **105 passed／1 skipped**（那 1 条 skip 是台账闸的 `DONE` 分支条件性未跑＝不算绿）；同一 harness 串行 **红闸 27/27＋绿闸 5/5、红闸失手 0 条**；全量后端 **1981 passed／2 failed／91 skipped**（比第六批基线 1922／2／92 正好 +40 简报闸 +18 页面闸 +1 条 skip 变绿；那 2 条既有红灯与本批无关）。**闸在写正文的过程中当场打红 6 处真文案缺陷**：`treats` 与 `diagnosis` 命中共享尺子的词干、mood-tracker 的 H1 两个逗号撞 G01 的逗号上限、两页缺 `not medical` 的字面命中、两处中文混进英文正文（最后这条已补成判据 `backend/tests/test_seo_page_source.py:331`）。KG-18 那句「随页面落地自动收紧」不是空话：`_page_count()` 从 0 变 7，那条判据当场从 skip 变成「要 ≥2 行真抽检」，本批回填 2 行并明写其余 5 页**未经人工逐页通读**（闸绿 ≠ 抽检过）。**四处实现与文档的偏差带根因登记**（草稿目录、G03 的证据形态、生成器不落 `web/`、只出 en），**两处意外发现只登记不顺手修**：`web/` 整目录从未进版本控制 ⇒ G14 的「可回滚」对现网面是空话（归 PLAT+ARCH）；`.gitignore` 的 `build/` 让生成物不入库。**本批自己造成的破坏已自证并回滚**：P20 红靶原先直写提示 id 字面量，撞上 `backend/tests/test_prompt_library.py:292` 那条「id 不许在库外硬编码」（它的扫描面含 `scripts/`），改成拼接后该闸 32 passed、全量回到既有 2 failed。**这行的插入暴露出那条「下游抄台账行号」证人的一处空档（已当场补强）**：§3 起全体下移两行后，`docs/aso/en-US-2026-11.md:62` 与 `docs/keyword-claims-audit-2026-11.md:159` 的 `:226`、`docs/aso/keyword-tracker-2026-11.md:4` 的 `:807` 三处引用全漂，而 `backend/tests/test_keyword_task_board.py:199` 原先只判「那一行非空＋同节＋同任务」，漂进同小节的相邻行它照旧给绿（实测插入后 9 passed／1 skipped＝假绿，第六批那句「当场打红」是过度声明）。本批把判据补成「引用同一行必须带一个**逐字内容锚**落在那一行」，三处引用同步成 `:228`／`:809` 并各加锚（`副标题 A`、`| 关键词簇 |`），补强后的闸在引用未改前先红、改完全绿；变异靶 P26／P27 专打「同小节内漂两格」这一类，B8／B9 的锚点同批改。§1 行 79-84／88／快照 6-20／工作流 C 4-6 同步改。**三条硬限制**：KG-02 与 T7-05 仍 `BLOCKED` ⇒ 台账验收的「两页 200」未达，生成物一律带 `noindex` 且未部署，不许引用成已上线；KG-13／KG-15／KG-16 卡在法务签字与外部可核验来源，本批不推；KG-17 的断链与孤儿页爬虫闸属下一批。

> 2026-11-03 第八批 **KG-17** 同口径置 `REVIEW`（判据与在册表 `docs/seo/internal-links-2026-11.md`，爬虫闸 `scripts/check_site_links.py`（`--check`／`--json`／`--self-test`），pytest 面 `backend/tests/test_site_links.py` **12 passed**，结案 `docs/growth/kg17-links-closure-2026-11-03.md`）。**闸的特别处只有一条**：入链**只数别的页面正文里的内链**——sitemap、页脚、面包屑、生成器自己拼的 Related 段一律不算，否则每页白捡三条、「孤儿页」这个概念会被判据本身抹掉（`backend/tests/test_site_links.py:92` 用 `sum(inbound.values()) == edges` 当正向证人）。实测：7 页／17 条正文内链／每页入链 2–4 条（下限 2）／3 跳内从 `ai-journal` 全可达／CTA 只有 `waitlist` 一个真动作且 `#cta` 解析到 `web/index.html` 的真 id／7 页生成物 0 条裸 `href="#"`；七张闸（页面源／内容清单／简报／台账／承诺审计／ADR／本闸）合跑 **117 passed／1 skipped**（那 1 条 skip 仍是台账闸的 `DONE` 分支＝不算绿）；同一 harness 串行 **红闸 15/15＋绿闸 3/3、红闸失手 0 条**；全量后端 **1993 passed／2 failed／91 skipped**（那 2 条既有红灯与本批无关）。**首跑当场打红四类**：`ai-journal`／`journal-app`／`journal-prompts` 三页各只有 1 条入链、生成器把面包屑 Home 指向不存在的 `/en/`（`scripts/build_seo_pages.py:530`，7 页各一条断链）、`site-content/pages.json` 的 `related` 与正文链接不是同一集合、现网面 `web/index.html` 5 条裸 `href="#"`。**闸自己的三处误报也一并修了**（合法 `#fragment` 被当成裸 `#`、站内绝对路径没映射到真文件、offender 去重把「同链坏 5 次」和「坏 1 次」看成一样）——判据先修对，再拿它打人。**本批自己造成的破坏**：并发跑了 `scripts/mutation_sample.py` 与全量后端，两侧互脏，出现 4 条假红；串行重跑才回到既有 2 failed ⇒ 这是「变异脚本严禁并发跑」的第二次实证，登记不辩解。**三条硬限制**：① 未部署（`KG-02`／`T7-05` 仍 `BLOCKED`，生成物带 `noindex`），判的是草稿面图不是线上图；② 验收句「CTA 在移动端可点、无遮挡」需真机渲染，本闸只判 href 与锚点解析 ⇒ 这条没取证，所以本行只到 `REVIEW`；③ 台账「建立面包屑、导航、页脚」里的**页脚**在支柱页生成物里没有，发布时由模板层补，已登记不装作做了。现网面 5 条裸锚**只登记不修**（归 KG-23／KG-02 的文案与法务面，且 `web/` 从未进版本控制）。§1 行 87／快照 5-21／工作流 C 3-7 同步改。**这行同样使 §3 起全体下移两行**，三处下游抄台账的引用（`docs/aso/en-US-2026-11.md`／`docs/keyword-claims-audit-2026-11.md` 的 `:228`、`docs/aso/keyword-tracker-2026-11.md` 的 `:809`）与两处 harness 的 board 锚点（P26／P27／GN5、B8／B9）同批改，`docs/seo/internal-links-2026-11.md` 因此改用**小节引用**而非行号。KG-13／KG-15／KG-16 仍 `TODO`（等法务签字与外部可核验来源，本批不推），KG-22／KG-29 未动。

> 2026-11-03 第九批 **KG-22／KG-29 的「模板与判据」面（本批不改任何任务状态，两行仍 `TODO`）**（结案 `docs/growth/kg22-kg29-template-closure-2026-11-03.md`）。KG-22 交付 `docs/growth/monthly-review-2026-12.md`（台账 §KG-22 的「证据落点」原本就指向它）：24 条问题基线（**20–30 这个区间读自台账再比**，不是本文件抄的）、记录字段照台账点名的七项逐条验、四条停/改/扩阈值全部挂在 `docs/metrics-dictionary.md:106` 起的关键词渠道七格键名上（另造键名即红）、结论模板四格当前 **0 行填写**、与 `T7-10` 的合并口径写成可判禁令（结论只写一次／两份不许同时各有一张决定表）。**它仍 `TODO` 的理由**：验收要的是「首份报告与决定」，而数据源 `KG-21` 仍 `BLOCKED` ⇒ 结论表任何一行只能是无中生有。KG-29 交付 `docs/research/unsent-letter-2026-12.md`：H1–H4 **预注册**判据（≥4/6、≥2 先写死，防访谈完再定通过线）、招募 5 场起算／10 场封顶对上台账的 5–10、12 题提纲、匿名化五条（含「不与用户 ID 关联」＋沿用 `KG-20` 的三条不上送禁令）、成本五维**全部 `未估`**、决定矩阵只许三种出口（含公开面那一格必须先推翻台账 §5.2）、重开条件三条合取。**红线那条不打在文档的自我声明上，打在契约快照**：现读 110 条路径里没有 feed／社区／关注／陌生人面，`/api/v1/cron/follow-up` 与 `/api/v1/insights/{insight_id}/feedback` 是字面巧合、判据里点名排除，`/api/v1/share/*` 那 13 条全是**用户自己的记录**出卡＋单条 `resolve/{code}` ⇒ 红线当前未破，且加出浏览面就会被打红。实测：两张新闸 `backend/tests/test_monthly_review_template.py` **13 passed** ＋ `backend/tests/test_unsent_letter_research.py` **11 passed**；九张闸合跑 **141 passed／1 skipped**（那 1 条 skip 仍是台账闸的 `DONE` 分支＝不算绿）；同一 harness 串行 **红闸 23/23（M 族 12＋U 族 11）＋绿闸 4/4、红闸失手 0 条**；全量后端 **2 failed／2017 passed／91 skipped**（比第八批基线 1993／2／91 正好 +13＋11，两条既有红灯＝`test_canonical_schema.py` 的 canonical 引用与 `test_r5_interop_broker.py` 要真库＋真 broker，与本批无关）（`scripts/mutate_kg22_kg29_gates.py`）。**首跑失手 4 条，全被判据太粗**，补强后才各自打到自己的目标测试：① 状态正则只认「仍 `TODO`」，靶改成「已 `REVIEW`」时它红在「载体没了」那一支而不是状态对拉；② `T7-07` 用「未到位／BLOCKED／未解」三选一判，被同节的「依赖未解」兜住而假绿；③ 红线原句在 §6 也出现，只判「全文含这句」就漏了「必须落在取证处」——改成落 §7 且同批要求契约路径在册。**补强后自己那一栏也被打红一次**：`docs/growth/monthly-review-2026-12.md:49` 那句「共 24 条：`§4 原词` 16 条、`同簇问法` 8 条」与表格实数（18／6，24 行）不符——**读者抄走的是句子不是表格**，所以当场改成 18／6，并把它钉成判据 `backend/tests/test_monthly_review_template.py:97`（三个数字逐条现数表格，`:109-111` 是三条断言）＋红靶 `M12`；这条新判据随即把绿闸 GN2 判红（它只改表格不改句子），**绿闸的前提因此重写**：改表必须同批改计数句，GN2 现在两段一起打补丁才给绿。**这行的插入使 §3 起再下移两行**：三处下游抄台账行号（`docs/aso/en-US-2026-11.md` 与 `docs/keyword-claims-audit-2026-11.md` 的 `:232`、`docs/aso/keyword-tracker-2026-11.md` 的 `:813`）与 harness 锚点 P26／P27／GN5、B8／B9 同批改；本批两份新文档一律用**节号引用**而非台账行号。**三条硬限制**：① 两行仍 `TODO`，不许引用成「复盘已做过／访谈已做过」，纪要与结论一份都没有；② KG-22 §1 的 18 个「已落地」指内容源里有这一页（24 行里 6 行未落地，现数表格），**不是线上有**（`KG-02`／`T7-05` 仍 `BLOCKED`）；③ KG-29 的成本五维全空是设计不是漏填，招募渠道要等 `T7-07`。

---

## 3. 任务详情

### A. 定位与承诺

#### KG-01 · 全量对外承诺审计

**做什么**

- 盘出所有面向美国用户的承诺位：App onboarding、付费墙、官网首页/FAQ、商店标题/副标题/截图、分享文案。
- 重点审计四类高风险表述：
  1. “remembers everything / remembers every conversation”这类绝对化记忆；
  2. “AI companion / AI friend / relationship level”这类陪伴平台语言；
  3. “therapy / therapist / diagnose / treat”这类医疗边界；
  4. “predict / will happen”这类没有证据支持的预测承诺。
- 逐条给出保留/改写/删除，并指定负责人。

**验收**

- 一份清单覆盖至少 30 条对外文案，每条有文件位置、原文、判定、改法。
- 能指回 Baseline §1.2（不是角色库/浪漫幻想/关系等级平台，`CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md:91`）与 §3（不做社区/公开分享，`:160-170`）。
- 英文 onboarding 的 `step1Tagline`（`frontend/src/i18n/locales/en.ts:432`）和官网 hero（`site/src/i18n.ts:13`）必须在清单里。

**证据落点**

- `docs/keyword-claims-audit-2026-11.md`
- 相关截图/链接附在清单末尾。

#### KG-02 · 法务审核包与签字

**做什么**

- 准备四类材料给法务/外部顾问：
  - MBTI/Enneagram 名称与商标使用说明（尤其“MBTI”是注册商标，ASO 保守版改用“16 types”）；
  - Rosebud、Day One 等竞品对比页的 nominative fair use 说明；
  - 隐私政策与“数据不用于训练/可导出/可删除”的实际配置对照；
  - 非医疗声明：AI 内容不构成诊断/治疗/法律/财务建议。
- 法务签字前，所有对比页、人格测试页、overthinking 页只能写草稿。

**验收**

- 法务意见书或签字邮件归档；每类材料的结论可执行（保留/改写/删除）。
- 对比页与人格页各有一份“已审可用”的措辞清单。
- 运营主体、联系方式、生效日期与 `T7-05` 的结论一致，不用占位符。

**证据落点**

- `docs/legal/keyword-content-review-2026-11.*`（意见书或邮件导出）
- `docs/keyword-content-gate.md` 的“法务前置”章节。

**阻塞项**

- 依赖 `T7-05` 确认唯一官网发布源与运营主体；依赖外部法务档期。

#### KG-03 · ADR：新功能取舍

**做什么**

- 用一个 ADR 固定本阶段对五个功能的取舍：
  1. 每日情绪记录：已有，保留并提升可见性；
  2. 三层次分析（轻松/探索/深潜）：做小实验，不做新人格/新引擎；
  3. 温柔陪伴 AI 角色：不做；
  4. 匿名树洞/社区：不做，改为私人“未寄出的信”研究；
  5. 金句卡：只做用户原话卡，不做 AI 金句库。
- 每个决定写：理由、替代方案、成本、风险、重开条件。

**验收**

- ADR 能直接回答“为什么不做 AI 角色/树洞”，并引用 Baseline §1.2/§3 与 `docs/feature-status.md` 的生产化现状。
- 三层次实验明确“同一个 AI、不同深度、必须带证据引用”，避免被理解成三个人格。
- 金句卡明确“只取用户原话、需授权、可删除”，避免变成鸡汤内容库。

**证据落点**

- `docs/adr/2026-11-keyword-feature-scope.md`

---

### B. ASO

#### KG-04 · en-US ASO 文案包

**做什么**

- 按 Apple 限制产出并校验：
  - 标题：`ClosenAI: AI Journal & Growth`（29/30）
  - 副标题 A：`The journal that remembers you`（30/30）
  - 副标题 B：`It asks how it actually went`（28/30）
  - 关键词字段：`mood tracker,self improvement,self discovery,personality test,mbti,enneagram,reflection,goals,diary`（99/100）
  - 保守版：`mood tracker,self improvement,self discovery,personality test,16 types,reflection,goals,diary,memory`（100/100）
- 附禁用词清单：`therapy`、`treat`、`diagnose`、`remembers everything`、`AI companion`、`AI friend`、`best`、`#1`。
- 副标题 A/B 各写一句假设与判停指标。

**验收**

- 字符数用脚本或 ASC 预览复核，不靠手数。
- 关键词字段只放搜索词，不重复标题/副标题已有词，不用竞品品牌词。
- 文案包可直接粘贴到 App Store Connect，不需二次改写。

**证据落点**

- `docs/aso/en-US-2026-11.md`

#### KG-05 · 关键词月度校验与淘汰规则

**做什么**

- 每月复核一次词表：月搜索量、来源、抓取日期、意图、与功能匹配度、当前排名/曝光。
- 规则：
  - 月搜索量 <500 的词不进入主投/主页面，只用于长尾内容或产品语言；
  - 连续 8 周无曝光或无有效激活的词，降级或替换；
  - 竞品品牌词只用于对比页，不放进关键词字段；
  - 每个词必须能落到一个真实功能或页面，不建造词不对板的落地页。
- 用 Ubersuggest + Apple Search Ads（若可用）+ Google Keyword Planner 交叉核对，至少两个来源。

**验收**

- 一张月度表，含本轮 40+ 词；每个保留词有落点，每个淘汰词有理由。
- 数据日期与来源写清；`*` 标记工具提示“outdated”的词。
- 与 §4 的映射表同步更新。

**证据落点**

- `docs/aso/keyword-tracker-2026-11.md`

#### KG-06 · App Store Connect 提交与副标题 A/B

**做什么**

- 在 ASC 提交 KG-04 的标题/副标题/关键词字段。
- 配置副标题 A/B（若 ASC 产品页优化可用）；不可用时用两次版本迭代做前后对比，并在报告里说明不是严格 A/B。
- 记录审核状态、被拒原因、修改记录。

**验收**

- 提交截图 + 审核通过截图；A/B 配置或迭代计划。
- 两周后读数：产品页浏览、安装、激活；结论写“保留/换回/继续测”。
- 若被拒，恢复路径明确（改文案/申诉/回滚）。

**证据落点**

- `docs/aso/asc-submission-2026-11.md`

**阻塞项**

- 缺 App Store Connect 权限/上架状态；依赖 `T7-05` 的法务主体与唯一官网。

#### KG-07 · Google Play 商店页

**做什么**

- 准备 Play 短描述（≤80 字符）、完整描述、截图、关键词密度。
- 短描述建议：`An AI journal that remembers you. Track mood and revisit your growth.`（69 字符）
- 完整描述围绕四个可验证功能：记忆、情绪、反思、行动结果；不写未实现能力。

**验收**

- Play Console 草稿或上线截图；短描述字符数复核。
- 完整描述无医疗/绝对化/竞品贬损表述；法务前置同 KG-02。
- 若暂不上架，保留可复用文案包，不伪造商店链接。

**证据落点**

- `docs/aso/google-play-2026-11.md`

**阻塞项**

- 缺 Play Console 账号/上架计划；依赖 `T7-05`。

#### KG-08 · 截图与预览创意简报

**做什么**

- 6 帧脚本，每帧一句主张 + 一个真实界面 + 一条证据：
  1. 记得你选择分享的内容（记忆列表/引用）；
  2. 今天的情绪（签到 + 热力图）；
  3. 一条有依据的洞察（引用原文）；
  4. 你上次说要做的事，后来怎么样了（Goal/Action/Outcome）；
  5. 你的变化（时间线/回顾）；
  6. 隐私与导出（可删/可带走）。
- 不把“AI companion”“remembers everything”放进首帧。

**验收**

- 简报含每帧文案、界面来源、所需截图、法务风险标注。
- 首帧主张与 KG-04 副标题 A/B 不冲突；至少一帧对应“结果回访”差异化。
- 设计交付后逐帧核对，不用占位截图上线。

**证据落点**

- `docs/aso/screenshot-brief-2026-11.md`

---

### C. SEO/GEO 内容

> 所有页面任务都先写草稿；发布前必须过 `KG-02` 法务与 `T7-05` 唯一源。不得在 `web/` 与 `site/` 同时维护第二套英文内容。

#### KG-09 · 四簇内容简报与证据包

**做什么**

- 四簇各一份 brief：
  1. 反思方法：如何写每周反思、如何避免只记情绪不找证据；
  2. 长期自我理解：普通聊天记忆与可纠正长期记录的区别；
  3. 行动结果复盘：Goal → Action → Outcome、预期 vs 实际；
  4. AI 洞察可信度：为什么结论要能回到原文、如何纠正 AI。
- 每份 brief 写：目标问题、搜索意图、目标 URL、H1、要引用的产品界面、证据来源、内链、法务边界。
- 先各写 1 篇支柱页提纲，不直接批量生成同义词页。

**验收**

- 4 份 brief + 4 个提纲；每篇只回答一个具体问题，给模板/例子/边界。
- 不编造用户案例、研究数据、专家背书；没有样本就不写统计结论。
- 每篇至少 1 条内链到真实功能页或另一篇支柱页。

**证据落点**

- `docs/content/briefs-2026-11/`

#### KG-10 · `/ai-journal` + `/journal-app` 支柱页

**做什么**

- 两页分别回答：
  - 什么是 AI journal，和普通日记差在哪；
  - 为什么“记得你”不等于“记得一切”；
  - 记录、情绪、反思、行动结果如何连起来；
  - 隐私：可导出、可删除、可撤回。
- 标题/描述/H1 各自唯一；避免两页互相蚕食，用内链区分意图。

**验收**

- 两页 200；title/description 唯一；canonical 自指；en/zh hreflang + x-default。
- 无 `remembers everything`、无 `AI companion` 作为主定位；不承诺未上线功能。
- 有至少 1 个真实产品界面截图或可核验的界面描述；CTA 到下载/等待名单。
- 进 sitemap，GSC 可抓取。

**证据落点**

- 页面 URL + 截图 + `curl -I` 状态码 + sitemap 行。

#### KG-11 · `/mood-tracker` 支柱页

**做什么**

- 覆盖 `mood tracker`、`mood tracking app`、`mood journal`、`mood tracker journal`。
- 讲清楚：一键签到、7 天/30 天视图、热力图、趋势一句话；情绪记录不是诊断。
- 给一个“这周情绪复盘”的三步模板，链接到反思提示库。

**验收**

- 页面 200；标题/描述含 mood tracker 语义但不堆词；canonical/hreflang 正确。
- 明确非医疗声明；不写“治疗焦虑/抑郁”。
- 至少 2 条内链（反思、AI journal）；CTA 可达。

**证据落点**

- 页面 URL + 截图 + 断链检查输出。

#### KG-12 · `/self-reflection` + `/journal-prompts` 支柱页

**做什么**

- `/self-reflection`：什么是自我反思、如何从“记情绪”走到“找证据”、每周反思模板。
- `/journal-prompts`：30 条原创提示，分四组（反思方法、长期自我理解、行动结果、AI 洞察可信度）；每条给目的、示例、追问。
- 不复制网上提示清单；不批量生成同义提示。

**验收**

- 30 条提示逐条可追溯（作者/日期/用途）；无医疗承诺。
- 两页 200、唯一 title/description、canonical/hreflang；提示页有目录与内链。
- 至少 1 个 CTA 到 Reflect 功能或等待名单。

**证据落点**

- 页面 URL + 提示清单文件 + 截图。

#### KG-13 · `/self-discovery` + `/personality-test` 支柱页

**做什么**

- 解释“从你的记录生成的画像”与“一次性测验”的区别；MBTI/Enneagram 只作为框架，不作为诊断或定论。
- 明示：结果可纠正、可随记录变化；不是 20 题测完就结束。
- 如无免费测试，不用“free test”做诱饵；用“查看你的画像如何生成”。

**验收**

- 两页 200；法务确认 MBTI/Enneagram 用词；保守版用“16 types”。
- 不伪造测试题/评分/用户案例；不承诺“准确看透你”。
- 内链到人格页、记忆页、隐私页。

**证据落点**

- 页面 URL + 法务批注 + 截图。

#### KG-14 · `/self-improvement` + `/goal-tracker` 支柱页

**做什么**

- 主线：Goal → Action → Outcome；重点讲“预期 vs 实际”的回访，不是通用打卡。
- 给一个模板：我想改变什么 → 本周做什么 → 到期实际发生了什么 → 下次改什么。
- 英文主张可用：`Other journals ask how you feel. ClosenAI asks how it actually went.`

**验收**

- 两页 200；主张与产品实际能力一致；不承诺“保证达成目标”。
- 至少 1 个真实功能截图/界面描述；内链到 AI journal、mood tracker。
- 页面有“到期回访”这一差异化段落，不能只写目标设置。

**证据落点**

- 页面 URL + 截图 + 内链检查。

#### KG-15 · `/overthinking` 支柱页

**做什么**

- 区分反刍（反复想、无新证据）与反思（找证据、形成下一步）。
- 给 3 步练习：写下循环念头 → 找一条支持/反对的证据 → 设一个最小行动或暂不行动。
- 明确不是治疗；给危机求助提示。

**验收**

- 页面 200；法务/临床边界审核通过；无“治疗/治愈/诊断”承诺。
- 至少 1 条内链到反思提示库；CTA 不诱导付费。
- 不编造心理学研究数据；引用需可核验。

**证据落点**

- 页面 URL + 法务批注 + 截图。

#### KG-16 · 竞品对比页（Rosebud / Day One 替代）

**做什么**

- 两页：`/compare/rosebud-alternative`、`/compare/day-one-alternative`。
- 只写可核验差异：证据回链、结果回访、导出/删除；不写竞品“没有”除非有公开来源。
- 标来源与日期；不伪造评分、用户数、价格。

**验收**

- 法务通过；竞品名仅作 nominative use；无贬损。
- 两页 200、唯一 meta、canonical/hreflang；每页至少 2 条来源链接与日期。
- 依赖 `KG-31`：核心循环未生产化前，不发布“已在生产可用”的对比结论。

**证据落点**

- 页面 URL + 来源清单 + 法务批注。

#### KG-17 · 内链与 CTA 规范

**做什么**

- 定义每页的 2–3 条相关内链与 1 个主 CTA；建立面包屑、导航、页脚。
- 确保没有孤儿页；所有 CTA 指向真实下载/等待名单，不指向占位商店链接。
- 每季度跑一次断链与孤儿页检查。

**验收**

- 爬虫报告：0 断链、0 孤儿页；每个支柱页至少 2 条入链。
- CTA 在移动端可点、无遮挡；等待名单不可用时显示说明而不是假按钮。
- 与 `T7-08` 的技术 SEO 底座不冲突。

**证据落点**

- `docs/seo/internal-links-2026-11.md` + 爬虫输出。

#### KG-18 · 内容质量闸

**做什么**

- 一页检查清单：是否回答一个具体问题、是否有原创模板/例子、来源与日期、作者/运营主体、非医疗声明、无虚假案例/统计、无批量同义词、内链、canonical、hreflang、CTA。
- 把清单放进 PR 模板；任何支柱页合并前逐项打勾。

**验收**

- 清单文件 + PR 模板落地；抽检 2 页可按清单复现结论。
- 发现不合格页面时有明确处置：改写/合并/noindex/下线。
- 不把“发布数量”当质量指标。

**证据落点**

- `docs/keyword-content-gate.md`

---

### D. 测量

#### KG-19 · 关键词渠道指标字典与漏斗

**做什么**

- 在现有 `docs/metrics-dictionary.md` 上补关键词渠道七格：
  曝光/搜索点击 → 商店页浏览 → 安装 → 首次签到/首次反思 → 首条有证据洞察 → 首次行动 → 到期结果回访。
- 每格写：定义、来源（ASC/GSC/PostHog/业务表）、窗口、分群（语言/国家/平台/页面）、负责人。
- 明确禁止只报事件发送量；区分设备排队、服务端接收、中继成功、丢弃、重试去重。

**验收**

- 字典更新并评审；每格有可执行 SQL/查询入口或平台路径。
- 与 `T7-06` 的漏斗口径不冲突；冲突处以主台账为准并注明。
- 至少一条“数据质量”检查：事件丢失/重复/未知状态。

**证据落点**

- `docs/metrics-dictionary.md` diff + 评审记录。

#### KG-20 · UTM/深链归因规范

**做什么**

- 定义 UTM 分类（source/medium/campaign/content/term）与命名规则。
- 配置 App Store campaign link；如使用延迟深链，写明 provider、归因窗口、隐私边界。
- 给测试矩阵：iOS/Android、已安装/未安装、有无参数、失效参数。

**验收**

- 规范文档 + 测试链接表；URL 不含邮箱/用户 ID 等 PII。
- 归因不是确定性的地方明确写“模型/窗口”，不把估算写成事实。
- 至少跑通 1 条 landing → store → install → 激活的链路（或写明未跑原因）。

**证据落点**

- `docs/growth/attribution-2026-11.md`

#### KG-21 · GSC/Bing/百度与商店数据接入

**做什么**

- 验证域名、提交 sitemap、导出基线；Bing Webmaster Tools 若可见 AI Performance 则留档；中文内容另接百度搜索资源平台。
- 商店侧接 ASC Analytics / Play Console；能导出的先导出。

**验收**

- 验证截图、sitemap 提交记录、基线 CSV；权限归属写清。
- sitemap 只含 200、canonical、可索引 URL；不含重定向/404/参数页。
- 与 `T7-08` 的技术 SEO 修复同步，不重复建第二套 sitemap。

**证据落点**

- `docs/seo/search-console-baseline-2026-11/`

**阻塞项**

- 缺 GSC/Bing/百度/商店账号权限；依赖 `T7-08`。

#### KG-22 · 月度复盘与停/扩规则

**做什么**

- 固定一组 20–30 个真实问题，按月记录：是否提及 ClosenAI、引用 URL、答案摘要、语言/地区/引擎/日期。
- 定义停/改/扩规则（内部建议值，首月校准，不是行业基准）：
  - 8 周无曝光：改写标题/合并/降级；
  - 有曝光无激活：检查页面承诺与产品首日路径；
  - 有激活无二次循环：先修产品，不扩内容；
  - 付费渠道 CAC 超过内部上限：暂停。
- 每月输出一页结论：保留/改写/下线/加投。

**验收**

- 复盘模板 + 首份报告；每个决定有数据依据与负责人。
- 不把单次 AI 回答当排名；记录波动与日期。
- 与 `T7-10` 的月度复盘合并，不产生第二份互相矛盾的结论。

**证据落点**

- `docs/growth/monthly-review-2026-12.md`

---

### E. 产品补齐

#### KG-23 · 绝对化记忆承诺整改

**做什么**

- 改掉以下位置：
  - `frontend/src/i18n/locales/en.ts:432`：`It remembers everything you tell it`
  - `site/src/i18n.ts:13`、`:79`、`:90`：hero/footer/meta 的同类表述
  - `site/src/i18n.ts:12`、`:14`、`:91`：把 `AI companion` 改成更准确的 `AI journal / growth journal`（美国定位）
- 新表述方向：`It remembers what you choose to share` / `The journal that remembers you`；落点必须与真实记忆/删除能力一致。
- 同步检查 onboarding 三特性是否暗示“全部记住”。

**验收**

- 全库 grep `remembers everything`、`remembers every conversation` 为 0（品牌历史文档除外，需列白名单）。
- en/zh 文案同步；i18n 测试绿；商店截图/元数据同步更新。
- 法务确认“可删除/可撤回”与隐私政策一致。

**证据落点**

- 文案 diff + grep 输出 + i18n 测试输出 + 更新后的截图。

#### KG-24 · 付费墙价值文案改写

**做什么**

- 重写 `frontend/src/i18n/locales/en.ts:272-275` 四条，从“配额/性能”改为“长期价值”：
  - 记忆连续性：你的对话不是一次性；
  - 更深的历史上下文；
  - 画像随记录变化，不是一次性测验；
  - 30/90/365 天回看。
- 不承诺“预测未来”“无限记忆”“保证成长”；Pro+ 同理，不堆次数。

**验收**

- en/zh 同步；四条都能指回一个真实功能；无未实现能力。
- 付费墙截图；A/B 假设与判停指标写明（转化/退款/交付失败）。
- 法务确认无绝对化承诺。

**证据落点**

- `docs/paywall-copy-2026-11.md` + 截图。

#### KG-25 · 首日价值路径

**做什么**

- 把新用户 7 天路径缩到最短：首次签到 → 首次反思（有提示）→ 首条有证据的洞察/确认 → 首个行动 → 到期结果回访。
- 审计现有 Onboarding/Home/Reflect/Insights 的死胡同：空白页、无提示、无下一步、错误吞掉。
- 加一条“今天可以做什么”的下一步引导，不新增大型功能。

**验收**

- 5 名内部用户走通；漏斗事件齐；至少 1 条失败恢复（网络失败/空内容/重复提交）。
- 7 日内完成一次完整循环的比例有基线；没有真实用户时写“未验证”，不伪造。
- 与 `T7-07` 的种子用户实验共用同一套事件。

**证据落点**

- `docs/growth/first-value-path-2026-11.md` + 事件表 + 测试输出。

#### KG-26 · 反思提示库（30 条）

**做什么**

- 做 30 条原创提示，分四组：反思方法、长期自我理解、行动结果、AI 洞察可信度。
- 每条：标题、问题、为什么问、追问、可关联功能（反思/情绪/目标）。
- 入口放在 Reflect：`需要提示？`；选中后可编辑、保存为反思。
- en/zh 首发；其他语言后补。

**验收**

- 30 条逐条可追溯；无医疗/诊断表述；不复制外部清单。
- Reflect 入口可达；保存成功/失败/空内容有测试；埋点记录使用与保存。
- 与 `journal prompts` 内容页共用同一批提示，避免两套文案。

**证据落点**

- `docs/growth/prompt-library-2026-11.md`（内容清单、端点与契约计数、写侧溯源判据、8 组变异自证、
  以及 3 处与方案 §6.1 不一致的根因：路径挪出 `/reflections` 前缀、响应面不加溯源字段、草稿路 `source=manual`）。
  Reflect 截图**未拍**（本机渲染不了 `.tsx`，屏的闸是源码扫描）——这条就是它没升 `DONE` 的原因。

#### KG-27 · 情绪统计可见性审计

**做什么**

- 审计现有能力：`HomeScreen` 的一键签到与热力图、`InsightsScreen` 的 `MoodLineChart` 与 7 天趋势。
- 补齐入口与空态：从 Explore/Profile 可达；无数据时给“先签到一次”的引导；7 天/30 天切换不产生第二时间真源。
- 不新建情绪数据模型，不改服务端契约。

**验收**

- 审计结论 + 入口截图；空态/有数据态各一张。
- 7 天/30 天视图可用；跨年/时区边界有测试或明确限制。
- 不把情绪统计包装成诊断；文案与 KG-01 一致。

**证据落点**

- `docs/growth/mood-stats-audit-2026-11.md` + 截图。

#### KG-28 · 三层次情绪分析实验

**做什么**

- 在现有情绪/反思入口加“分析深度”选择器：
  - 轻松：一句话回应 + 一个问题，不展开分析；
  - 探索：连接 1–2 条记录，提出一个可能模式，请用户确认/纠正；
  - 深潜：多记录时间线 + 至少两种解释 + 一个可执行下一步；每条结论必须有原文引用。
- 同一个 AI、同一套数据，不做三个人格/三个角色；默认轻松，用户可切换。
- 用 flag 控量（名称待定，例如 `CLOSEN_MOOD_ANALYSIS_DEPTH`），关闭时保持现有行为。

**验收**

- flag 关闭 = 现有行为；开启 = 三档可选；无新表、无跨用户读取。
- 探索/深潜的每条结论可回到原文；用户可否认/纠正。
- 4 周读数：选择率、确认/纠正率、行动创建率、7 日回访、成本/延迟；不达标则停。
- 文案不承诺“看透思维模式”，只承诺“帮你找证据、看模式”。

**证据落点**

- `docs/experiments/mood-depth-2026-12.md` + 埋点 + 测试。

#### KG-29 · 私人“未寄出的信”模式研究

**做什么**

- 用研究替代匿名树洞：写一封不发送的信，无 feed、无回复、无陌生人；可保存为私人反思、可删除、可加情绪标签。
- 先做 5–10 名目标用户访谈，验证“树洞需求”是否真的需要公开/匿名社交，还是只需要私密出口。
- 产出做/不做的决定与成本估算（审核、举报、危机干预、匿名性）。

**验收**

- 访谈纪要匿名化；结论能回答“为什么不用树洞也能满足需求”。
- 若决定做，另立实现任务并补隐私/安全评审；若不做，写重开条件。
- 不在未验证前上线任何 UGC/公开分享入口。

**证据落点**

- `docs/research/unsent-letter-2026-12.md`

#### KG-30 · 用户原话金句卡

**做什么**

- 只从用户自己的记录/反思中取原话，生成可分享卡片；不做 AI 金句库，不做通用鸡汤。
- 明示授权、可预览、可删除、可关闭分享；卡片带“来自你的记录”而非 AI 生成标识。
- 与现有分享卡（人格/里程碑/档案）合流，避免多套分享入口。

**验收**

- 授权与删除路径可走通；无原话时不生成占位金句。✅ 服务端判据（40402 空白正文不出卡）＋前端（空正文那条不给按钮）
- 分享卡不包含他人内容/跨用户数据；导出/删除后卡片失效。✅ 归属与 `record_type` 同进 WHERE；软删／被取代 40403
- 埋点：生成/分享/取消/删除；测试覆盖空内容与越权。△ 两拍分享埋点在册（`share_tapped`/`share_completed`，source=`quote`）；**取消**这一档金句卡走的是系统面板、没有 resolve 后可判的 action，未埋（不造假）
- 不做 AI 金句库。✅ 后端只有 `POST` 一条金句端点，无 GET 列表/随机那条路（闸钉）

**证据落点**

- `docs/growth/quote-card-2026-11.md`（授权边界为何只在服务端、三种来源三种答、契约 117/1/118 与 §7.1 六行、
  280 的两侧镜像、22/22 红闸＋2/2 绿闸变异自证，以及 5 处与方案 §6.3 不一致的根因：
  40405 代 40403、草稿来源改措辞而不拒、15 语言全加、不进 `shareSites`、前端不做 AI 抽取预筛）。
  真机 PDF 导出**未做**（`expo-file-system`＋`expo-sharing` 是原生路径，本机 node 环境跑不了）——
  这条就是它没升 `DONE` 的原因。

---

### F. 上线闸门

#### KG-31 · 承诺生产化闸

**做什么**

- 把关键词/内容承诺与实际生产状态对齐：memory、insight、reflection 的切流键开量，验证真实流量，而不是“管道通了、流量为零”。
- 以 `docs/feature-status.md` 为准：`insight.pattern` 仍是 `WIRED`（`:30`），`PRODUCTION_ENABLED=0`。
- 准备回滚：切流键归零、页面文案回退、商店描述回退。

**验收**

- 切流键开量记录 + 7 天生产流量/失败率/延迟；至少一条真实用户路径跑通。
- 回滚演练记录；告警可达。
- 未过此闸，`KG-16` 对比页与任何付费获客保持不发布/不投放。

**证据落点**

- `docs/growth/production-gate-2026-12.md` + 监控截图。

**阻塞项**

- 依赖 `T7-04`（生产现场验收）与 `T7-07`（种子用户）结案；当前主台账两项均为 `BLOCKED`。

---

## 4. 关键词 → 落点映射

> 月搜索量为 Ubersuggest 美国/英语、2026-10-27 抓取，四舍五入；`*` 表示工具标记 outdated。正式投放前用 Apple Search Ads / Google Keyword Planner 复核。

| 关键词簇 | 代表词（月搜索） | 意图 | 对应功能 | 落点 | 任务 |
|---|---|---|---|---|---|
| AI 日记 | `ai journal` 1.9K、`ai journal app` 720、`journal app` 9.9K、`journaling app` 9.9K* | 找能回应的日记工具 | 反思、记忆、时间线 | `/ai-journal`、`/journal-app`、ASO 标题 | KG-04、KG-10 |
| 情绪 | `mood tracker` 4.4K、`mood tracking app` 3.6K、`mood journal` 1K、`mood tracker journal` 590 | 记录/看情绪趋势 | 一键签到、热力图、趋势 | `/mood-tracker`、ASO 关键词 | KG-11、KG-27 |
| 自我反思 | `self reflection` 14.8K、`self reflection journal` 1.3K、`self reflection questions` 720 | 找反思方法/问题 | 反思、提示库、洞察 | `/self-reflection`、`/journal-prompts` | KG-12、KG-26 |
| 提示词 | `journal prompts` 40.5K*、`journal prompts for self reflection` 1.3K、`journal prompts for self discovery` 1.3K、`journal prompts for anxiety` 1K | 找可用的提示 | 反思提示库 | `/journal-prompts`、Reflect 入口 | KG-12、KG-26 |
| 自我发现 | `self discovery app` 1.9K、`personality test online` 9.9K、`mbti test free` 40.5K、`enneagram test online` 5.4K | 找自我理解/人格工具 | 人格画像、洞察、时间线 | `/self-discovery`、`/personality-test`、ASO 关键词 | KG-13 |
| 个人成长 | `self improvement app` 2.4K、`personal growth` 8.1K、`personal growth goals` 2.9K、`personal growth journal` 720 | 找成长方法/工具 | 目标、行动、结果回访 | `/self-improvement`、`/goal-tracker` | KG-14 |
| 目标 | `goal tracker` 2.4K、`goal setting app` 590、`goal tracker templates` 880* | 找目标追踪 | Goal → Action → Outcome | `/goal-tracker` | KG-14 |
| 过度思考 | `overthinking` 18.1K、`overthinking a relationship` 720、`anxiety journal` 4.4K、`anxiety journal prompts` 1.3K | 找停止反刍的方法 | 反思、提示、情绪记录 | `/overthinking` | KG-15 |
| 竞品 | `day one journal app` 12.1K、`day one journal` 2.9K、`rosebud app` 720、`rosebud application` 880 | 找替代/比较 | 证据回链、结果回访、导出 | `/compare/*` | KG-16 |
| 陪伴（不投） | `ai companion` 18.1K、`ai companionship` 14.8K、`ai companion app` 4.4K、`ai friend` 12.1K | 找角色/陪伴 | 无对应产品定位 | 不做落地页 | §5 |
| 其他大词（不主投） | `gratitude journal` 74K、`self care app` 8.1K*、`self care apps` 5.4K、`mbti test` 165K*、`enneagram test` 201K* | 泛需求/免费测试 | 功能不匹配或意图不符 | 不建造词不对板页 | KG-05 |

---

## 5. 本阶段明确不做的功能（不是任务）

> 这一节不占任务 ID，也不参与 §2 计数；避免把“不做”伪装成 `TODO`。

### 5.1 温柔陪伴的 AI 角色 / 角色库

**不做。** 现有 `Warm & Caring` 聊天语气（`frontend/src/i18n/locales/en.ts:360` 一带）已经覆盖“温柔”；再加角色/人设/角色库会把产品拉进 Replika、Character.AI 的意图池，和 Baseline §1.2「不是以角色库、浪漫幻想或虚拟关系等级为主引擎的陪伴平台」（`CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md:91`）冲突。

**重开条件：** 如果未来要独立做陪伴产品，另立产品线、另做商店页与合规评估，不塞进 ClosenAI 的成长叙事。

### 5.2 匿名树洞 / 社区 / 公开分享

**本阶段不做。** Baseline §3 明确不做「复杂的社区、社交关系或公开分享体系」（`:160-170`）。匿名 UGC 需要审核、举报、拉黑、危机干预、匿名性保证、App Store 1.2 审核、法务与运营人力；独运营者当前没有这部分产能。

**替代：** 私人“未寄出的信”（KG-29）——无 feed、无回复、无陌生人，可删除、可转反思。先访谈验证，不先写代码。

### 5.3 AI 金句库 / 通用鸡汤卡

**不做 AI 生成金句。** 只做“用户原话卡”（KG-30）：来自用户自己的记录，有授权、可删除、可分享。否则会稀释“有证据”的定位，也容易变成不可核验的内容农场。

### 5.4 其他不投方向

- `ai therapist` / `ai therapy`：医疗边界与监管风险，不做。
- 占星/算命/确定性预测：Baseline §1.2 明确排除。
- 公开排行榜/关系等级作为获客主张：现有关系时间线可以保留为产品内体验，但不作为美国获客主叙事。
- 批量同义词落地页：违反 KG-18 内容质量闸。

---

## 6. 执行顺序（30/60/90）

### 第 0–30 天（2026-11-01 → 2026-11-30）：先修承诺，再谈流量

1. **P0 决策与文案：** KG-01、KG-03、KG-04、KG-08、KG-18、KG-19、KG-20、KG-23、KG-24、KG-25、KG-27。
2. **法务与账号：** 启动 KG-02；申请 ASC/Play/GSC 权限（KG-06/07/21 的阻塞项）。
3. **内容底座：** KG-09 四簇 brief；KG-10、KG-11 两篇支柱页草稿。
4. **生产闸：** 推动 `T7-04`/`T7-07`；KG-31 未过前，所有付费流量保持 0。

**出口标准：** `remembers everything` 清零；ASO 文案包可提交；两篇支柱页草稿过内容闸；指标字典与归因规范可用；首日路径有 5 人走通记录。

### 第 31–60 天（2026-12-01 → 2026-12-31）：拿到第一轮真实读数

1. **ASO：** KG-05 月度校验、KG-06 提交与 A/B；有审核结果。
2. **内容：** KG-12、KG-13、KG-14 三组页面；KG-17 内链；KG-21 搜索控制台基线。
3. **产品：** KG-26 提示库、KG-28 三层次实验（flag 小流量）、KG-22 首份月度复盘。
4. **判断：** 如果 8 周无曝光/无激活，按 KG-22 改写或下线，不扩页面数量。

**出口标准：** 至少 2 个页面有真实曝光；至少 1 条渠道链路可归因；三层次实验有初步读数；没有“有流量无激活”的页面继续加投。

### 第 61–90 天（2027-01-01 → 2027-01-31）：只在有证据时扩

1. **内容：** KG-15、KG-16；KG-16 必须在 KG-31 生产闸通过后发布。
2. **实验：** KG-28 4 周结论；KG-29 私人信件研究；KG-30 原话卡（若研究支持）。
3. **扩张：** 只有核心循环出现自然复用、付费交付稳定、退款可解释，才讨论付费获客；否则继续修产品。

**出口标准：** 每个保留页面有“曝光→激活→二次循环”的数据；没有数据的页面不加投；KG-31 有 7 天生产记录与回滚演练。

---

## 7. 关键词数据附录

### 7.1 核心匹配词（≥500，优先）

| 关键词 | 月搜索（US） | 备注 |
|---|---:|---|
| `ai journal` | 1,900 | 核心 |
| `ai journal app` | 720 | 核心 |
| `ai journaling` | 2,400* | 核心 |
| `ai powered journaling app` | 720 | 核心 |
| `journal app` | 9,900 | 大词，需差异化 |
| `journaling app` | 9,900* | 大词 |
| `best journal app` | 2,900* | 比较意图 |
| `mood tracker` | 4,400 | 核心 |
| `mood tracking app` | 3,600 | 核心 |
| `mood journaling` | 1,300* | 核心 |
| `mood journal` | 1,000 | 核心 |
| `mood tracker journal` | 590 | 长尾 |
| `self reflection` | 14,800 | 大词 |
| `self reflection journal` | 1,300 | 核心 |
| `self reflection journal prompts` | 1,300 | 提示词 |
| `self reflection questions` | 720 | 提示词 |
| `journal prompts` | 40,500* | 大词，竞争高 |
| `journal prompts for mental health` | 5,400 | 需非医疗边界 |
| `journal prompts for self reflection` | 1,300 | 核心 |
| `journal prompts for self discovery` | 1,300 | 核心 |
| `journal prompts for self growth` | 720 | 核心 |
| `journal prompts for anxiety` | 1,000 | 需非医疗边界 |
| `self improvement app` | 2,400 | 核心 |
| `self discovery app` | 1,900 | 核心 |
| `personal growth journal` | 720 | 核心 |
| `personal development app` | 590 | 核心 |
| `personal growth` | 8,100 | 大词 |
| `personal growth goals` | 2,900 | 核心 |
| `goal tracker` | 2,400 | 核心 |
| `goal setting app` | 590 | 核心 |
| `goal tracker templates` | 880* | 内容词 |
| `mbti test free` | 40,500 | 免费测试意图，谨慎 |
| `personality test online` | 9,900 | 核心 |
| `enneagram test online` | 5,400 | 核心 |
| `enneagram test free results` | 1,600 | 免费意图 |
| `anxiety journal` | 4,400 | 需非医疗边界 |
| `anxiety journal prompts` | 1,300 | 需非医疗边界 |
| `overthinking` | 18,100 | 内容词 |
| `overthinking therapy` | 4,400 | 不投（治疗意图） |
| `overthinking a relationship` | 720 | 内容词 |
| `day one journal app` | 12,100 | 竞品对比 |
| `day one journal` | 2,900 | 竞品对比 |
| `rosebud app` | 720 | 竞品对比 |
| `rosebud application` | 880 | 竞品对比 |

### 7.2 低于 500 的词（不主投，只用于产品语言/长尾）

| 关键词 | 月搜索（US） | 处置 |
|---|---:|---|
| `weekly review` | 390 | 产品语言/站内内容，不进 ASO |
| `weekly reflection` | 320 | 同上 |
| `decision journal` | 170 | 长尾内容备选 |
| `ai diary` | 110 | 长尾内容备选 |
| `emotion tracker` | 260 | 由 `mood tracker` 覆盖 |
| `personal growth app` | 210 | 由 `self improvement app` 覆盖 |
| `self awareness app` | 30 | 不做 |
| `ai journal that writes back` | 70 | 产品语言，不做落地页 |

### 7.3 明确不投的陪伴池

| 关键词 | 月搜索（US） | 原因 |
|---|---:|---|
| `ai companion` | 18,100 | 意图是角色/陪伴，与 Baseline §1.2 冲突 |
| `ai companionship` | 14,800 | 同上 |
| `ai companion app` | 4,400 | 同上 |
| `ai friend` | 12,100 | 同上 |

### 7.4 数据边界

- 以上为第三方工具估算，不等于实际流量；`*` 为工具标记 outdated，需复核。
- Apple Search Ads popularity、Google Keyword Planner 精确区间、Search Console 真实曝光都还没接入；这些是 KG-05/KG-21 的工作。
- 关键词量会波动；本表只用于排序与取舍，不作为投放承诺。
