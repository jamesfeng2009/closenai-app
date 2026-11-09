# 截图与预览创意简报（KG-08）· en-US 商店 6 帧

编制：2026-11-02 ／ 负责人：GROWTH＋FE ／ 适用：App Store en-US 截图位（6 帧）＋ Google Play 复用
上游口径：`docs/keyword-claims-audit-2026-11.md`（KG-01，70 条承诺审计）、
`docs/aso/en-US-2026-11.md`（KG-04 文案包）、`docs/adr/2026-11-keyword-feature-scope.md`（KG-03 决定 3：不做陪伴角色）。

> **状态**：简报就绪，**截图未取**。RN 屏在本机跑不起来（无模拟器/真机渲染），
> 所以本文件给的是「拍哪一屏、屏的哪一块、拍之前要先满足什么条件」，不是成品图。
> 台账 §KG-08 的验收原文＝「6 帧脚本：每帧一句主张 + 对应真实界面 + 证据；首帧不用 remembers everything」，
> 本简报把「真实界面」写成**仓内路径:行**，由 `backend/tests/test_screenshot_brief.py` 逐行核回——
> 指不到真存在的界面，就不算一帧。

---

## 1. 六帧

主张列是**要印在图上的那句话**（≤40 字符：截图文字在 6.7 英寸屏上超过这个长度就要换行，换行就吃掉画面）。

| 帧 | 主张（图上那句话） | 对应真实界面 | 界面源（仓内路径:行） | 取图条件（拍之前必须成立） | 证据与合规标注 |
|---|---|---|---|---|---|
| 1 | `It remembers what you choose to share` | 记忆列表与单条原话引用 | `frontend/src/screens/MemoriesScreen.tsx:163` | 账号内至少 3 条**用户保存过的**记忆，且每条能看到原文引用 | KG-23 的限定式口径本体（不是 remembers everything）；`backend/tests/test_absolute_memory_claims.py:139` 逐字钉过这句的 App 内版本 |
| 2 | `One tap a day, and the trend shows up` | 今日签到＋首页热力格 | `frontend/src/screens/HomeScreen.tsx:85` | 连续 7 天有签到数据，热力格不得整片空（空热力图等于演示「没在用」） | 热力格数据源是本地 `mood_checkins` 同步（`frontend/src/screens/HomeScreen.tsx:32`），不谎称云端统计 |
| 3 | `See the entries an insight came from` | 洞察卡展开的依据条目 | `frontend/src/screens/InsightsScreen.tsx:210` | 拍**有依据**的洞察（`evidence_count > 0`），空依据洞察不能当封面 | 这句说的是「界面给了回链入口」，不是「所有洞察都有依据」；依据回链的真实口径见 I4（灰度中），文案不许升格成保证 |
| 4 | `You said you'd do it. How did it go?` | 行动—结果回访（四选一） | `frontend/src/screens/GrowthRecordsScreen.tsx:267` | 有一条已记录行动＋其结果反馈的条目 | 结果四选一取自服务端枚举（`frontend/src/screens/GrowthRecordsScreen.tsx:69`），前端不自造档位；这帧是**竞品未占的差异化**，台账 §KG-08 点名至少一帧讲结果回访 |
| 5 | `Look back through what you wrote` | 时间线回顾 | `frontend/src/screens/RelationshipTimelineScreen.tsx:79` | 时间线上 ≥14 天有内容，且画面里只有测试账号文字 | 说的是「能回看自己写过的」，不承诺「AI 看懂你的变化」；无证据预测属 KG-01 四类风险之一 |
| 6 | `Export it, delete it — it's yours` | 导出任务＋删除账户入口 | `frontend/src/screens/PrivacySecurityScreen.tsx:81` | 导出任务能真跑出文件（`backend/services/export_service.py`），删除入口真在页上 | 这帧两句都必须是**已实现能力**：导出＝后台任务，删除＝`frontend/src/screens/PrivacySecurityScreen.tsx:261` 的入口；未经真机验证前不上线 |

## 2. 三条硬规矩（来自审计与 ADR，不是审美偏好）

1. **首帧禁绝对化记忆**：不许出现 `remembers everything`／`remembers all`／`remembers every word`
   （`scripts/check_aso_copy.py` 的同一条禁语正则在这张闸里复用，两面对拉）。
2. **任何一帧都不写陪伴角色**：`AI companion`／`AI friend` 是 KG-04 禁语清单里的两条，
   ADR 决定 3 已把陪伴角色判成「不做」；截图是商店页的一部分，不是 App 内文案，改起来更贵。
3. **画面里的文字必须是测试账号里真存在的句子**，不许用设计稿假 UI。
   台账 §KG-08 验收原文即「不用占位截图上线」；本表「取图条件」列就是为了拍之前先满足它。

## 3. 与 KG-04 副标题的关系

首帧主张（`It remembers what you choose to share`）**刻意不等于**副标题 A
（`The journal that remembers you`）。原因写在 `docs/aso/en-US-2026-11.md` §3：
副标题 A 与 KG-01 审计对同句的「改写」判定冲突，已登记为 `待法务`
（`docs/keyword-claims-audit-2026-11.md:96`）。截图面先按合规口径落地，
这样即使法务最终改了副标题 A，六帧画面不必重拍。

## 4. 预览视频（App Store Preview）——本轮不做

理由：预览视频要 15–25 秒真机录屏，本机无渲染与录制条件；
且第 3 帧依据回链仍在灰度，录进去等于把一个未放量能力当成默认体验宣传。
**重开条件**：I4 依据回链放量＋有一台可录屏的真机。

## 5. 未做什么

1. **未取任何真机截图**（本机跑不了 RN 渲染，`expo` 原生模块在 jest 里也不可用）；
   本简报的验收只能做到「每帧指到真存在的界面与真代码行」，图本身仍欠。
2. **未做设计稿**：文字主张定稿后交设计排版，本文件不含视觉规范。
3. **未复核 6.7 英寸以外尺寸的裁切**：主张按 ≤40 字符写，实际画面是否在 iPhone SE 上被截断，需真机取图后逐帧看。
4. **未走法务**：第 6 帧「Export it, delete it」是对能力的具体承诺，
   导出/删除的真实边界（含延迟、含云端备份留存）要和 KG-02 隐私对照一起过一遍。
