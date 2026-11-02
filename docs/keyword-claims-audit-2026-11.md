# 对外承诺全量审计（KG-01 · 2026-11-02）

**范围**：所有能对美国用户/潜在用户看到的英文与落地站文案面——App 内英文 i18n、服务端分享卡/推送文案库、
现网静态站 `web/`、未发布的 Astro 源 `site/`。
**不含**：品牌历史文档（`CLOSENAI_V3_*`、`closenai-prd/`）、内部工作单 `docs/*.md`（台账 §KG-23 验收原文即「品牌历史文档除外」）。
**例外**：`docs/aso/en-US-2026-11.json` 是**待粘贴进 App Store Connect 的商店文案源**，贴上去就是对外第一行承诺，
按可发布面进本表（它不在 `web/`、不在 App 包里，但它是本批唯一新增的对外文案面）。
**方法**：逐条取**当前文件里的原文**（不是记忆里的旧文案），按四类风险判定，给可执行改法与负责人。
每条的 `位置` 与 `原文` 由 `backend/tests/test_keyword_claims_audit.py` 逐行核回源文件——
**引用行号里找不到该原文，闸就红**，所以这张表不允许凭印象抄。

四类风险固定（`docs/keyword-growth-implementation-plan-2026-11-01.md` §4.1）：
**绝对化记忆 / 陪伴平台语言 / 医疗边界 / 无证据预测**。
判定口径：`保留`（现在就合规，含刻意写的免责与否定式）／`改写`（给具体新措辞）／`删除`（不实或做不到的承诺）／
`待法务`（KG-02 未签字，只能先记）。

定位依据：`CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md:84` 的「产品不是什么」，
其中 `:90` 不做占星/算命/确定性预测、`:91` 不做以角色库/浪漫幻想/虚拟关系等级为主引擎的陪伴平台；
`:130` 功能优先级里 `:164` 列明「没有稳定证据支持的预测性洞察」不做、`:168` 列明不为凑功能建社区/公开分享。

---

## 1. 审计表

| 位置 | 原文 | 风险类型 | 判定 | 改法 | 负责人 |
|---|---|---|---|---|---|
| `frontend/src/i18n/locales/en.ts:456` | It remembers what you choose to share | 绝对化记忆 | 保留 | KG-23 已改判的口径本体（限定在「你选择分享的」），`test_absolute_memory_claims.py:139` 逐字钉住，不许回退 | FE |
| `frontend/src/i18n/locales/en.ts:458` | Each check-in and reflection you save adds to its picture of you | 绝对化记忆 | 保留 | 主语是「你保存下来的」，且同句给了删除出口，与真实能力一致 | FE |
| `frontend/src/i18n/locales/en.ts:460` | AI finds patterns in your records — and flags them before you spiral | 无证据预测 | 改写 | 删掉 `before you spiral`（＝在你崩溃之前预警，是预测承诺，且我们没有任何前瞻性证据）。改成：AI 把你写过的东西里反复出现的模式回给你看 | FE+ARCH |
| `frontend/src/i18n/locales/en.ts:43` | What do you want ClosenAI to remember? | 绝对化记忆 | 保留 | 输入框占位符，问的是用户要记什么，不是能力承诺 | FE |
| `frontend/src/i18n/locales/en.ts:106` | The AI is just getting to know you | 陪伴平台语言 | 改写 | L 等级描述写成了关系递进叙事（Baseline `:91`）。改成能力口径：目前只有几条你的记录可回看 | FE+ARCH |
| `frontend/src/i18n/locales/en.ts:107` | The AI starts proactively following up with you | 陪伴平台语言 | 改写 | `proactively following up` 是拟人关系语。改成：到点会给你发一条与你记录相关的提醒（每日 1 条，见 §1.3 权益表） | FE+PLAT |
| `frontend/src/i18n/locales/en.ts:108` | The AI references memories and discovers your patterns | 绝对化记忆 | 保留 | 说的是「引用你已有的记忆」，与 `insight_evidence` 回链实现一致 | BE |
| `frontend/src/i18n/locales/en.ts:109` | The AI understands you deeply and grows with you | 陪伴平台语言 | 改写 | `understands you deeply` 是不可验证的心灵描写。改成：可回看的记录越多，能对照的时间跨度越长 | FE+ARCH |
| `frontend/src/i18n/locales/en.ts:247` | Keep chatting with me — the more we talk, the better I understand you. | 陪伴平台语言 | 改写 | 第一人称关系号召＋理解度承诺。改成：你写得越多，回看时能对照的记录越完整 | FE |
| `frontend/src/i18n/locales/en.ts:150` | discovered in 7 days | 无证据预测 | 改写 | 「7 天就能发现」是对时间的承诺，我们没有 7 天有效性的读数。改成：随着你的记录逐步成形 | FE+GROWTH |
| `frontend/src/i18n/locales/en.ts:121` | Deleting your account permanently removes all data. | 绝对化记忆 | 改写 | 同文件 `:426` 写的是 24 小时内永久删除，两处口径不一致。统一成：注销后所有记录在 24 小时内永久删除且不可恢复 | PLAT+LEGAL |
| `frontend/src/i18n/locales/en.ts:307` | Export everything you've recorded in ClosenAI | 绝对化记忆 | 保留 | `everything` 挂的是「你记录下来的」，属导出范围描述，不是记忆完备性承诺（KG-23 已定 this 口径） | FE |
| `frontend/src/i18n/locales/en.ts:474` | 30 seconds. It only repeats back what you picked — it predicts nothing. | 无证据预测 | 保留 | 否定式承诺（明写不预测），是本次审计要的**正向证人**：全库保留这类句子 | FE |
| `frontend/src/i18n/locales/en.ts:527` | not a prediction, just your own words | 无证据预测 | 保留 | 同上，洞察卡页脚的免责句，与 Baseline `:164` 一致 | FE |
| `frontend/src/i18n/locales/en.ts:589` | Observation only · no predictions, no promises | 无证据预测 | 保留 | 反思流程末步的注脚，明写「只观察、不预测、不许诺」，与 Baseline `:164` 一致，属要长期保留的否定式承诺 | FE |
| `frontend/src/i18n/locales/en.ts:354` | 16 Types | 陪伴平台语言 | 保留 | ASO/文案保守版已用 `16 Types` 替代 `MBTI`（注册商标风险归 KG-02），全站沿用这个写法 | CONTENT+LEGAL |
| `frontend/src/i18n/locales/en.ts:402` | not for medical, legal, or financial decisions | 医疗边界 | 保留 | 非医疗免责声明本体，措辞不可弱化 | LEGAL |
| `frontend/src/i18n/locales/en.ts:407` | AI content is for self-exploration and entertainment only | 医疗边界 | 改写 | 免责的后半句把产品降格成「娱乐」，与 §1.3 与商店口径冲突，也可能被读成「所以不用当真」。改成：AI 内容用于自我观察，不构成诊断或治疗 | LEGAL+ARCH |
| `backend/data/copy/en.json:421` | it remembers what you told it | 绝对化记忆 | 改写 | 分享卡**水印**，也是商店截图取景位置。缺「你选择」限定，与 App 内 `:456` 不同口径。改成：it remembers what you chose to share | BE+CONTENT |
| `backend/data/copy/en.json:57` | Predictive insights | 无证据预测 | 改写 | L4 权益名直接叫「预测性洞察」，撞 Baseline `:164`（无稳定证据的预测性洞察不做）。改成：Evidence-backed insights | BE+CONTENT |
| `backend/data/copy/en.json:97` | Next week forecast | 无证据预测 | 改写 | `forecast` 是预报语。改成：Next week, from your own commitments | BE |
| `backend/data/copy/en.json:287` | your AI companion. I hear you're an | 陪伴平台语言 | 改写 | 对话开场白自称 AI 伴侣（KG-23 已把落地站改成 growth journal，这两处当时登记为「发现未修」，本行归位）。改成：your AI growth journal | BE+ARCH |
| `backend/data/copy/en.json:288` | your AI companion — great to meet you! | 陪伴平台语言 | 改写 | 无 MBTI 分支的第二条开场白，同样自称伴侣。改成：Hi — ClosenAI here, your AI growth journal. Great to meet you! | BE+ARCH |
| `backend/data/copy/en.json:294` | Hi, I'm your AI friend. | 陪伴平台语言 | 改写 | 无 MBTI 分支的开场白自称朋友。改成：Hi — ClosenAI here, your growth journal | BE |
| `backend/data/copy/en.json:307` | "default_name": "friend" | 陪伴平台语言 | 改写 | 推送默认称呼是 `friend`，把产品身份写进通知里。改成：用应用名 ClosenAI 作默认称呼 | BE+PLAT |
| `backend/data/copy/en.json:319` | I've been thinking of you | 陪伴平台语言 | 改写 | 「我一直在想你」是恋爱语，且出现在**主动推送标题**上。改成：你有一条记录到了回看的时候 | BE+GROWTH |
| `backend/data/copy/en.json:69` | 30-Day Confidant | 陪伴平台语言 | 改写 | 徽章名把连续记录天数命名成「知己」等级。改成：30-Day Streak | FE |
| `backend/data/copy/en.json:71` | One-Year Companion | 陪伴平台语言 | 改写 | 同上一年的徽章直接把产品叫「伴侣」。改成：One Year of Entries | FE |
| `backend/data/copy/en.json:74` | After {days} days together, our connection has grown to | 陪伴平台语言 | 改写 | 「我们一起度过的日子，我们的关系长大了」＝关系叙事。改成：你连续记录了 {days} 天，可回看的深度到了「{label}」这一层 | FE |
| `backend/data/copy/en.json:66` | they are not romantic. | 陪伴平台语言 | 保留 | 已有的非浪漫免责声明（正向证人）：等级名描述的是 AI 对你记录的处理深度 | BE |
| `backend/data/copy/en.json:279` | Last time you mentioned | 绝对化记忆 | 保留 | 引用的是上一条具体记录原文，可回查、可删，不是「记得一切」 | BE |
| `backend/data/copy/en.json:292` | I've kept it in mind. | 绝对化记忆 | 改写 | 「我放在心上」是心灵描写。改成：这条你上次写过，要不要继续 | BE |
| `backend/data/copy/en.json:15` | Feeling a bit anxious is completely normal. | 医疗边界 | 待法务 | 情绪共情语，不诊断不治疗；但 `completely normal` 会被读成对状态的临床判断，归 KG-02 复核措辞 | LEGAL |
| `backend/data/copy/en.json:253` | Insightful Healer | 医疗边界 | 改写 | 九型人格副类型名含 `Healer`（治疗者），与同文件「不是治疗」声明自相冲突。改成：Insightful Counselor 之外的非医疗词（如 Insightful Advocate） | CONTENT+LEGAL |
| `backend/data/copy/en.json:436` | Idealistic Healer | 医疗边界 | 改写 | INFP 副类型名，与 `en.ts:402` 的「不用于医疗决定」自相冲突。改成：Idealistic Advocate（15 语的对应副类型名一起换，`Healer` 一族在 `:253` 与本行各一处） | CONTENT+LEGAL |
| `backend/data/copy/en.json:415` | I'll stay right here with you through this moment. | 医疗边界 | 待法务 | 危机场景里陪伴式承诺；危机文案改动必须与热线流程一起过法务，不在本批拍板 | LEGAL+BE |
| `web/index.html:527` | 成长陪伴 | 陪伴平台语言 | 改写 | **现网**功能卡标题，台账点名锚。定位已改判为成长日记，「陪伴」与 Baseline `:91` 冲突。改成：成长回音（或 成长记录） | GROWTH+CONTENT |
| `web/index.html:523` | AI 基于你的长期数据生成性格画像 | 绝对化记忆 | 改写 | 「长期数据」暗示全量采集。改成：AI 依据你写下的反思与情绪记录生成性格画像 | GROWTH+CONTENT |
| `web/index.html:645` | 性格画像不限 | 绝对化记忆 | 删除 | **权益虚标**：Pro+ 的真实刷新上限是每月 999 次（`backend/services/personality_service.py:563`，台账 §1.3 第 94 行），不是不限。改成：性格画像每月 999 次刷新 | GROWTH+BE |
| `web/index.html:648` | 每日多条主动消息 | 无证据预测 | 删除 | **权益虚标**：主动消息每日条数不随档提升，免费/Pro/Pro+ 都是 1 条/天（台账 §1.3 第 98 行）。这条要么删掉，要么改成真实高限差异（统一池每日 100/1000） | GROWTH+BE |
| `web/index.html:644` | 每日 100 次 AI 回应 | 绝对化记忆 | 改写 | 写在 **Pro+** 卡上，但 Pro+ 的统一池高限是 1000/天（`backend/config.py:196`）；档位文案与实现不符（这里还低报了）。改成与档一致：每日 1000 次 | GROWTH+BE |
| `web/index.html:647` | 完整记忆能力 | 绝对化记忆 | 改写 | 「完整」是不可验证的能力总称，且与 KG-23 改判后的口径冲突。改成：长期记忆与回看 | GROWTH+CONTENT |
| `web/index.html:646` | 深度版全部能力 | 绝对化记忆 | 改写 | `全部` 绝对化，且「深度版」未在任何权益表定义。改成：深度洞察与深度反思模式 | GROWTH+CONTENT |
| `web/index.html:634` | 完整记忆能力 | 绝对化记忆 | 改写 | Pro 卡上的同一句，与 Pro+ 卡 `:647` 一起换成可验证的能力名：长期记忆与回看 | GROWTH+CONTENT |
| `site/src/i18n.ts:13` | The journal that remembers <em>you</em>. | 绝对化记忆 | 改写 | 台账点名锚（hero H1）。`remembers you` 是「记得你这个人」的绝对化。改成：The journal that remembers what you write. | CONTENT |
| `site/src/i18n.ts:14` | it keeps them, and it grows with you. | 绝对化记忆 | 改写 | 前面列举了三件小事后接 `it keeps them`＝全量保留叙事，后半是关系成长语。改成：它把这些留成可回看的记录，你随时能删 | CONTENT |
| `site/src/i18n.ts:12` | iOS · 15 languages · AI growth journal | 陪伴平台语言 | 保留 | KG-23 改判后的定位行（`AI growth journal`），`test_absolute_memory_claims.py:123` 要求这一表述在位 | CONTENT |
| `site/src/i18n.ts:26` | A companion that keeps the whole picture | 陪伴平台语言 | 改写 | 一句里两个问题：`companion` 自称伴侣、`whole picture` 是完备性承诺。改成：A journal that keeps your entries | CONTENT |
| `site/src/i18n.ts:27` | Not a chat window that forgets. | 绝对化记忆 | 改写 | 否定式绝对化（暗示永不遗忘）。改成：Not just a chat window — it stays searchable | CONTENT |
| `site/src/i18n.ts:29` | Everything you share becomes memory with confidence scores. | 绝对化记忆 | 改写 | `Everything ... becomes memory` 与实现不符（记忆有置信分、会被取代与衰减）。改成：What you share can become memory, scored and revisited | CONTENT+BE |
| `site/src/i18n.ts:20` | Remembers across days, weeks and years | 绝对化记忆 | 保留 | 时间跨度是已实现能力（365 天时间线、周年回看），不涉及「记得一切」 | CONTENT |
| `site/src/i18n.ts:41` | And when a year passes, it remembers that too. | 绝对化记忆 | 保留 | 有对应实现（`anniversary_one` 周年卡），句子只主张这一件事 | CONTENT |
| `site/src/i18n.ts:31` | MBTI and Enneagram, grounded in what you actually said | 陪伴平台语言 | 待法务 | `grounded in what you actually said` 合规；风险在 **MBTI 商标**用法，须随 KG-02 决定是保留还是改 `16 types`（App 内已用保守版） | LEGAL+CONTENT |
| `site/src/i18n.ts:40` | the person you were becoming. | 无证据预测 | 改写 | 「你正在成为的那个人」是对成长方向的断言，无证据。改成：以及你当时写下的原话 | CONTENT |
| `site/src/i18n.ts:52` | a portrait, not a label. | 陪伴平台语言 | 保留 | 已经在做「画像≠标签」的限定，属可保留措辞 | CONTENT |
| `site/src/i18n.ts:59` | A companion that remembers must first be trusted to remember. | 陪伴平台语言 | 改写 | 隐私段小标题里再次自称 companion。改成：A journal that keeps your entries must first be trusted with them | CONTENT |
| `site/src/i18n.ts:72` | Your memories stay. Come back after two weeks or two months and it picks up where you left off | 绝对化记忆 | 保留 | 只主张「记录还在、能接着看」，与导出/删除实现一致 | CONTENT |
| `site/src/i18n.ts:74` | Is it a replacement for therapy? | 医疗边界 | 保留 | FAQ 的**提问**，答句是「No」；保留这种自问自答式免责 | LEGAL |
| `site/src/i18n.ts:78` | Say something worth remembering. | 绝对化记忆 | 保留 | 祈使句，无能力承诺 | CONTENT |
| `site/src/i18n.ts:81` | How did the interview go? You were nervous about it last week. | 绝对化记忆 | 保留 | 演示对话（mock UI），展示的是回看用户自己写过的内容 | CONTENT |
| `site/src/i18n.ts:83` | Told you it would pay off. | 无证据预测 | 改写 | mock 对话里 AI 说「我说过会有回报的」＝事后追认预测。改成：You finished it — that's what your entries show | CONTENT |
| `site/src/i18n.ts:90` | ClosenAI — The journal that remembers you | 绝对化记忆 | 改写 | 台账点名锚（`<title>`），同 `site/src/i18n.ts:13` 的问题；这条还会进搜索摘要，须与 KG-04 字段同口径。改成：ClosenAI — The journal that remembers what you write | CONTENT+GROWTH |
| `site/src/i18n.ts:91` | It remembers what you choose to share, how you felt, and who you are becoming. | 绝对化记忆 | 改写 | 前半合规，后半 `who you are becoming` 是无证据的完整画像断言。改成：…how you felt, and how your own words have changed | CONTENT |
| `site/src/i18n.ts:79` | It remembers what you choose to share | 绝对化记忆 | 保留 | 台账点名锚（页脚 tagline），与 App 内 `en.ts:456` 同口径，可保留 | CONTENT |
| `site/src/legal.ts:187` | not end-to-end encrypted | 绝对化记忆 | 保留 | 明确的**反向**声明（我们没做端到端加密就别写）——这是本表要的诚实证人 | LEGAL+PLAT |
| `site/src/legal.ts:234` | It is not therapy, counselling, or medical | 医疗边界 | 保留 | 非医疗声明本体 | LEGAL |
| `site/src/legal.ts:235` | it can misremember emphasis, and it can be confidently off | 绝对化记忆 | 保留 | 主动承认模型会记错，与绝对化承诺相反 | LEGAL+ARCH |
| `site/public/r/index.html:124` | 它记得你说过的每一句。 | 绝对化记忆 | 删除 | **现网外的分享落地页源**（`site/` 按 D0 未部署）仍在写 KG-23 已判删的绝对化句。改成：你写下的，它替你能回看 | CONTENT |
| `site/public/r/index.html:131` | It remembers every word you said. | 绝对化记忆 | 删除 | 同一张分享落地页的英文标题，与 `site/public/r/index.html:124` 一起改。改成：It keeps what you wrote — yours to revisit, yours to delete | CONTENT |
| `docs/aso/en-US-2026-11.json:36` | The journal that remembers you | 绝对化记忆 | 待法务 | **台账 §KG-04 推荐的商店副标题 A，与本表对同句 `site/src/i18n.ts:13`/`site/src/i18n.ts:90` 的「改写」判定直接冲突**（都缺「你选择分享的／你写下的」限定）。商店文案一旦贴进 ASC 就是对外第一行承诺，比 App 内文案更没有回头改的余地，所以这里不替法务下结论，只把冲突钉住：法务二选一——要么副标题 A 改成 ≤30 的限定式（`It remembers what you write` 实测 27/30），要么出具「`remembers you` 不构成绝对化能力承诺」的书面口径并同步 `site/src/i18n.ts:13`。当前 KG-06 `BLOCKED`（未提交），冲突面暂时只在文案包内。 | LEGAL+GROWTH |

---

## 2. 汇总

| 风险类型 | 条数 | 其中判定为「改写/删除」 |
|---|---:|---:|
| 绝对化记忆 | 32 | 17 |
| 陪伴平台语言 | 20 | 15 |
| 医疗边界 | 8 | 3 |
| 无证据预测 | 10 | 7 |
| **合计** | **70** | **42** |

判定分布：`保留` 24／`改写` 38／`删除` 4／`待法务` 4。（这两组数字由 `test_keyword_claims_audit.py`
从表里现算再比，手填错一位就红——本表初稿手写的 59/28/17/7/7 就是被这台机器纠出来的。）

**四条硬结论**

1. **App 内文案基本已合规**（KG-23/KG-24 那两批改的就是这里），残余问题集中在
   `backend/data/copy/en.json` 的**开场白、徽章名、推送标题**，以及 L4 权益名 `Predictive insights`。
2. **现网 `web/index.html` 有两条权益虚标**（`:645` 性格画像不限、`:648` 每日多条主动消息），
   可对着 `backend/config.py:193-200`、`backend/services/personality_service.py:563` 与台账 §1.3 逐条证伪——
   这两条不是措辞问题，是**卖了一个不存在的权益**，优先级高于任何文案美化。
3. **`site/` 里 KG-23 的改判没有同步过来**：`site/src/i18n.ts` 仍有 `companion`／`whole picture`／`Everything you share becomes memory`，
   `site/public/r/index.html:124/:131` 仍是「记得你说的每一句」。按 D0 `site/` 不部署，所以现网没有这些话；
   但只要按 KG-09～KG-17 生成新页面并复制这些模板，就会把已判删的句子上线。**因此本轮站点任务（KG-10～KG-17）的草稿面
   一律以本表为准，不以 `site/src/i18n.ts` 为准。**
4. **审计表里没有一条是「靠删键过关」**：`:456`、`:474`、`:527`、`:589`、`copy/en.json:66`、`legal.ts:187/:235`
   这些 `保留` 行本身就是证人——如果哪天有人为了过禁语闸把免责句整体删掉，`test_keyword_claims_audit.py` 会红。

---

## 3. 四类之外的登记（随 KG-02 法务包复核，不进本表判定）

这几条不属于固定四类，但同样是对外承诺，KG-02 隐私对照必须逐条核：

- `frontend/src/i18n/locales/en.ts:120` —「Conversations are encrypted and stored in the ClosenAI database」；
  与 `site/src/legal.ts:187`「stored as readable rows … not end-to-end encrypted」**口径相反**。
  两处一个是 App 内 FAQ、一个是站点法务源，用户可能同时看到。**这是本次审计发现的真实冲突，归 KG-02 隐私对照**，
  按「发现的缺陷不顺手修」不在本批改文案。
- `frontend/src/i18n/locales/en.ts:306` —「Keep your archive forever」（永久保留承诺）。
- `frontend/src/i18n/locales/en.ts:415` —「never what you write, your conversations, or your memories」（`never` 型绝对化，需与埋点白名单实态对照）。
- `frontend/src/i18n/locales/en.ts:426` —「permanently deleted within 24 hours」（与 `:121` 的「permanently removes all data」需统一，见本表第 11 行）。
- `frontend/src/i18n/locales/en.ts:815` —「Sign-in tokens and payment receipts are never included」（导出面绝对化，需 `export_service` 实证）。
- `web/privacy.html:59`、`:157`、`:195`、`:221`／`web/terms.html:59`、`:93`、`:155`、`:159` — 法务文本，判定全部为「待法务复核」，本批不动。

---

## 4. 缺陷登记（带日期与根因，不在本批顺手修）

1. **2026-11-02｜台账锚点行号已漂**：`docs/keyword-growth-implementation-plan-2026-11-01.md` §4.1 写的是
   `en.ts:432` 的 `step1Tagline` 与 `en.ts:436` 的 `step1Feature2Desc`，实测现在在
   `en.ts:456` 与 `en.ts:460`（KG-25 首日路径改 onboarding 时整体上移了 24 行）。
   本表按**当前真实行号**写，并由闸逐行自证；方案文档那两处陈旧行号属文档维护面，另批改。
2. **2026-11-02｜KG-23 的禁语闸扫不到 `site/public/**`**：`backend/tests/test_absolute_memory_claims.py` 的
   `_surfaces()` 收的是 `frontend/src/i18n/locales`、`backend/data/copy`、`site/src/i18n.ts`、`web/*.html` 四个面，
   **不含 `site/public/`**；同时英文正则 `remembers (everything|all |every conversation)` 挡不住
   `remembers every word` 这个变体。所以本表 `:124/:131` 那两条判「删除」，但**没有任何机器闸会拦住它复活**。
   归属：KG-23 的守卫面补齐（另批：把 `site/public/**` 进扫描面 + 英文正则加 `every word`），
   本批不动那个文件，免得把 KG-23 的 `REVIEW` 状态搅浑。
3. **2026-11-02｜`Predictive insights` 是已上线的权益名**：`backend/data/copy/en.json:57` 在 L4 权益清单里，
   改它要同步付费墙、商店描述与 `docs/feature-status.md`，属跨面改动，归 KG-24 之后的权益文案批。
4. **2026-11-02｜台账 §KG-04 与 §KG-01 自相矛盾**：台账 `docs/keyword-growth-task-board-2026-11-01.md:232` 那条 `副标题 A`
   把 `The journal that remembers you` 写成推荐文案（2026-11-01 初稿），而本表把同一句在
   `site/src/i18n.ts:13`/`:90` 判成「改写」（2026-11-02 审计）。
   **根因**：KG-04 的文案先于 KG-01 审计进台账，审计口径没回头校对 ASO 面。
   本批的处理是「不替法务拍板、只把冲突钉住」：新表行 `docs/aso/en-US-2026-11.json:36` 判 `待法务`，
   KG-04 文案包 §3 同步写了风险标注与「KG-02 签字前 A 版不单独提交」。
   归属：KG-02 二选一后再改台账 §KG-04 的验收行；本批不动台账那句推荐文案。

---

## 5. 谁来改、按什么顺序

| 批次 | 范围 | 负责人 |
|---|---|---|
| 本批之后第一批 | `web/index.html` 两条权益虚标（`:645`、`:648`）＋档位数字与 `config.py` 对齐（`:644`） | GROWTH+BE |
| 本批之后第一批 | `backend/data/copy/en.json` 开场白／徽章名／推送标题／水印（陪伴语与绝对化） | BE+CONTENT |
| 随 KG-10～KG-17 | `site/src/i18n.ts` 与 `site/public/r/index.html` 的已判删句子（生成新页时一并不带入） | CONTENT |
| 随 KG-02 签字 | `:253`/`:436` 的 `Healer`、MBTI 商标、加密口径冲突、危机文案 | LEGAL |
