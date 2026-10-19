#!/usr/bin/env python3
"""T3-05（前端：洞察的反馈条与纠错输入）的变异自证（2026-10-20）。

靶文件九个：`InsightsScreen.tsx`／`api.ts`／`types/index.ts`／三份语言文件（en／ja／km／th／zh）／
**Go 的三处真源**（`outcome.go`、`v3_feedback.go`、`apierr.go`）／`v3_proxy.py`。
红闸 30 条、绿闸 5 条。闸有两份：`__tests__/insightFeedbackWiring.test.ts`（本票的接线闸）
与 `__tests__/i18nLocales.test.ts`（15 语言的总闸）。

为什么 Go 与 Python 也当靶：本票六条判据里有一半是**两侧不同源**的对账
（[[feedback-orm-identity-map-gate]]）。只改前端那一侧，无法证明「另一侧真的被读了」——
所以 G1…G3 与 P1 各改后端一个字面量，要求红的必须是本票那条判据本身
（[[feedback-mutation-anchor-must-reach-target]]）。

工单 §验收 四句各自的靶与红闸：

1. 「六种反馈各一条 jest」⇒ S1（词表少一档）、S2（TS 联合改名）、S4（乐观表长出第六格）、
   M1（矩阵值改名）、G1（**Go 的矩阵**改名）、G2（**Go 的上限**改名）、G3＋P1（**409 的两侧**）；
2. 「失败回滚一条」⇒ R1（摘掉 catch 里的回滚＝用户看到一次没发生的表态）、
   R2（忙位不清＝卡在忙态）、O1（摘掉乐观写＝这条判据是回滚存在性的正向证人）；
3. 「空纠错不可提交一条」⇒ C1（判空守卫摘掉）、C2（按钮 disabled 摘掉＝按下去发一次空正文请求）、
   C3（maxLength 放开）、C4（`Alert` 里弹 `e.message`＝把内部口径当界面）；
4. 「i18n 键集闸绿」⇒ I1…I6：en／zh 各少一档（打的是键集对账），
   km 整句退回英文、th 漏一个扁平键、ja 少一个 `{{label}}`（打的是 15 语言总闸）。
   三种坏法在真机上都是静默的：fallbackLng 让缺键直接显英文，界面上没有任何一处会报错。

另有本票自己的两条结构性判据：B1/B2/B3（徽章取值优先级与「changed=false 不许显示成改写」）、
X1（六枚按钮落到「没有结论」那一格＝表态对象根本不存在）、
X2/X3（出口不唯一、body 长出 user_id＝ADR-002 的身份入口在这一格失守）、
T1/T2/T3（埋点：事件名、六态值走白名单里的 `action`、正文进队列）。

纪律：串行执行（[[feedback-mutation-gates-no-concurrency]]）、copy2 备份＋`filecmp(shallow=False)`
逐字节写回、不用 git checkout（[[feedback-mutation-restore-no-git-checkout]]）；每条红闸先跑基线，
基线不绿一律记「未跑」（[[feedback-gate-needs-positive-witness]]）；jest 侧
`Test suite failed to run` 不算打红——那些红的是工具链，不是判据；
`--check` 会逐条把 marker（含 `it.each` 的 `%s` 与 `%s → %s` 模板）回查回闸的源文件。

用法：
    ./.venv/bin/python scripts/mutate_t305_gates.py --check
    ./.venv/bin/python scripts/mutate_t305_gates.py
    ./.venv/bin/python scripts/mutate_t305_gates.py --only S1,M1,I4
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FE = REPO / "frontend"
SRC = FE / "src"
SCREEN = SRC / "screens" / "InsightsScreen.tsx"
API = SRC / "services" / "api.ts"
TYPES = SRC / "types" / "index.ts"
EN = SRC / "i18n" / "locales" / "en.ts"
ZH = SRC / "i18n" / "locales" / "zh.ts"
JA = SRC / "i18n" / "locales" / "ja.ts"
KM = SRC / "i18n" / "locales" / "km.ts"
TH = SRC / "i18n" / "locales" / "th.ts"
GO_OUTCOME = REPO / "go-core" / "internal" / "domain" / "outcome.go"
GO_FEEDBACK = REPO / "go-core" / "internal" / "httpapi" / "v3_feedback.go"
GO_APIERR = REPO / "go-core" / "internal" / "apierr" / "apierr.go"
PY_PROXY = REPO / "backend" / "services" / "v3_proxy.py"

GATE = "__tests__/insightFeedbackWiring.test.ts"
I18N_GATE = "__tests__/i18nLocales.test.ts"
JEST = FE / "node_modules" / ".bin" / "jest"

D1 = "① 反馈只有一个出口，请求体三键与 Go 的 tag 逐字同集"
D2 = "② 六个按钮＝Go 的六态，映射表＝Go 的 RefInsight 那一格"
D3 = "③ 徽章取值：回执 > 本地推测，changed=false 回到读面表"
D4 = "④ 「我要纠正」＝Modal 多行输入，非空才可提交"
D5 = "⑤ 乐观写的徽章在失败时必须收回去"
D6 = "⑥ 埋点：事件名逐字，六态值走白名单里的 action"
D7 = "⑦ i18n：六态标签表＝Go 的词表，状态机表＝Go 的矩阵值"

# ── 锚点（逐字取自靶文件，命中数必须恰好 1）────────────────────────────────
FEEDBACK_TYPES = "  'confirm', 'partially_confirm', 'reject', 'correct', 'outdated', 'forget_from_model',\n"
LOCAL_REJECT = "  reject: 'retracted',\n"
LOCAL_TAIL = "  forget_from_model: 'dismissed',\n};\n"
BADGE_PRIO = "mark.receipt?.target_status ?? LOCAL_TARGET_STATUS[mark.type]"
CHANGED_FALSE = (
    "    if (mark.receipt && !mark.receipt.changed) "
    "return `insights.status.${mark.receipt.target_status}`;\n"
)
BAR_COND = "                {feedbackById[item.id]?.receipt && amendId !== item.id ? (\n"
ROLLBACK = (
    "      setFeedbackById((p) => {\n"
    "        const next = { ...p };\n"
    "        if (prev) {\n"
    "          next[item.id] = prev;\n"
    "        } else {\n"
    "          delete next[item.id];\n"
    "        }\n"
    "        return next;\n"
    "      });\n"
)
OPT_WRITE = "    if (predicted) setFeedbackById((p) => ({ ...p, [item.id]: { type } }));\n"
BUSY_CLEAR = "      setFeedbackBusyId(null);\n"
CODE409 = "const CODE_DUPLICATE_FEEDBACK = 409;\n"
ALERT_FAIL_T = "        e instanceof ApiError && e.code === CODE_DUPLICATE_FEEDBACK\n"
MAX_CONST = "const CORRECTION_MAX_LENGTH = 500;\n"
INPUT_MAX = "              maxLength={CORRECTION_MAX_LENGTH}\n"
EMPTY_GUARD = "    if (!correctionFor || !text || correcting) return;\n"
DISABLED = "                disabled={!correctionText.trim() || correcting}\n"
CORRECT_BRANCH = "    if (type === 'correct') {\n"
TRACK = "      track('insight_feedback_submitted', { feature: 'insights', action: type });\n"
A11Y = "                        accessibilityLabel={t(`insights.fb.${type}`)}\n"
INSUFFICIENT = "              <Text style={styles.emptyText}>{t('insights.evidenceInsufficient')}</Text>\n"
CALLSITE = "      const receipt = await postInsightFeedback(item.id, type, correction);\n"
BODY = (
    "      body: JSON.stringify({\n"
    "        type,\n"
    "        correction_text: correctionText ?? '',\n"
    "        operation_id: operationId ?? '',\n"
    "      }),\n"
)
UNION_TAIL = "  | 'forget_from_model';\n"
G_MATRIX = '\t\tcase FeedbackReject:\n\t\t\treturn "retracted", true\n'
G_MAX = "const maxCorrectionText = 500\n"
G_CONFLICT = "\tCodeConflict            = 409\n"
P_RAISE = "    raise AppException(int(code), str(message), data=data, http_status=resp.status_code)\n"
EN_FORGET = '      "forget_from_model": "Remove from the model",\n'
EN_DONE = '    "fbDone": "You answered: {{label}}",\n'
ZH_RETRACTED = '      "retracted": "已收回",\n'
JA_DONE = '    "fbDone": "フィードバック済み：{{label}}",\n'
KM_SAVED = '    "fbSavedTitle": "បានកត់ត្រា",\n'
TH_AMEND = '    "fbAmend": "เปลี่ยนคำตอบ",\n'
KM_SAVED_EN = '    "fbSavedTitle": "Saved",\n'

TS_COMMENTS = "".join(
    f"// 绿闸注脚 {i:02d}：这一格判的是写法语义，不钉行号也不钉排版。\n" for i in range(1, 16)
)

MUTATIONS: list[tuple] = [
    # ── 1. 六种反馈各一条：词表、类型、矩阵（含 Go 那一侧）──
    (
        "S1", "六态里摘掉 `outdated` ⇒ 屏上只有五个按钮，而 Go 那边「已过时」是真的会回来的状态；"
              "用户少一个表态口，前端不报错、后端不报错",
        [(SCREEN, FEEDBACK_TYPES,
          "  'confirm', 'partially_confirm', 'reject', 'correct', 'forget_from_model',\n")],
        GATE, f"{D2} › 屏上六态与 Go 的词表同集（多一档少一档都红）",
    ),
    (
        "S2", "TS 联合里 `forget_from_model` 写成 `forget` ⇒ 类型检查全绿的合法改名，"
              "而 Go 的 `Valid()` 当场 400：用户点「别再这样猜我」永远失败",
        [(TYPES, UNION_TAIL, "  | 'forget';\n")],
        GATE, f"{D2} › 类型联合也跟着 Go（改名先红这里，而不是红在屏上少一个按钮）",
    ),
    (
        "S3", "乐观表里 `retracted` 写成 `withdrawn` ⇒ 这一格落库仍是 `retracted`（Go 说了算），"
              "而屏上徽章显示成另一档；**同一个词表在前端长出了第二个拼法**",
        [(SCREEN, LOCAL_REJECT, "  reject: 'withdrawn',\n")],
        GATE, f"{D2} › reject → retracted：乐观表的值逐字等于 Go 的矩阵",
    ),
    (
        "S4", "替 `partially_confirm` 猜一个状态（乐观表长出第六格）⇒ Go 明确它不改写状态"
              "（`outcome.go:611-615`），前端替它猜＝一次假改写进了用户的眼睛",
        [(SCREEN, LOCAL_TAIL,
          "  forget_from_model: 'dismissed',\n  partially_confirm: 'confirmed',\n};\n")],
        GATE, f"{D2} › Go 的矩阵五格都在屏上那张表里，且没有第六格",
    ),
    (
        "M1", "按钮数组里 `correct` 改名成 `fix` ⇒ 六枚按钮少一枚（Go 那一档回 400），"
              "而这一格打的是「每个词表成员各自成一条 jest」那条验收",
        [(SCREEN, FEEDBACK_TYPES,
          FEEDBACK_TYPES.replace("'correct'", "'fix'"))],
        GATE, f"{D2} › 按钮 correct 在屏上有字面量，并且在 i18n 里有同名词",
    ),
    (
        "G1", "**改 Go 那一侧**：把 `StatusForDerivedTarget` 的 RefInsight/reject 折成 `withdrawn` ⇒ "
              "本票那条对账必须红——它证明判据真的读了 Go 的源，而不是前端自己跟自己比",
        [(GO_OUTCOME, G_MATRIX, G_MATRIX.replace('"retracted"', '"withdrawn"'))],
        # 靶改的是**数据**，所以 it.each 那条用例的标题也跟着变（reject → withdrawn）：
        # 认这条新标题，才算「红的是这条判据本身」而不是红在别处。
        GATE, f"{D2} › reject → withdrawn：乐观表的值逐字等于 Go 的矩阵",
    ),
    (
        "G2", "**改 Go 那一侧**：`maxCorrectionText` 从 500 收成 480 ⇒ 前端若仍写 500，"
              "用户写完 481～500 字提交会被 400 挡在门外；打红的必须是本票那条上限对账",
        [(GO_FEEDBACK, G_MAX, "const maxCorrectionText = 480\n")],
        GATE, f"{D4} › 上限逐字等于 Go 的 maxCorrectionText，并且真的挂到 maxLength 上",
    ),
    (
        "G3", "**改 Go 那一侧**：`CodeConflict` 从 409 改成 428 ⇒ 前端的 409 分支立刻指错，"
              "「你已经答过了」这句话再也不会出现",
        [(GO_APIERR, G_CONFLICT, "\tCodeConflict            = 428\n")],
        GATE, f"{D5} › 409 的码值两侧不同源：Go 的 CodeConflict ＋ BFF 原样透传",
    ),
    (
        "P1", "**改 BFF 那一侧**：转发时把信封码重折成 502 ⇒ T4-03 刚立的「转发不改错误码」被本票"
              "的另一半接住：409 到不了客户端，回滚仍发生但用户只看到「出错了」",
        [(PY_PROXY, P_RAISE,
          "    raise AppException(502, str(message), data=data, http_status=resp.status_code)\n")],
        GATE, f"{D5} › 409 的码值两侧不同源：Go 的 CodeConflict ＋ BFF 原样透传",
    ),
    # ── 2. 徽章取值优先级（回执 > 本地推测）──
    (
        "B1", "忽略回执、永远用本地推测 ⇒ 服务端按矩阵折出来的 `target_status` 被前端覆盖掉，"
              "乐观值一旦与真值不同（归属判定、并发下的版本冲突），屏上就永久显示一个没发生过的状态",
        [(SCREEN, BADGE_PRIO, "LOCAL_TARGET_STATUS[mark.type] ?? mark.receipt!.target_status")],
        GATE, f"{D3} › 回执优先：receipt?.target_status 排在本地推测之前",
    ),
    (
        "B2", "`changed=false` 那一格也走状态机表 ⇒ 「部分认可」被显示成一次改写"
              "（用户只是说了句大致对，界面上却说这条洞察换了状态）",
        [(SCREEN, CHANGED_FALSE,
          "    if (mark.receipt && !mark.receipt.changed) "
          "return `insights.fbResult.${mark.receipt.target_status}`;\n")],
        GATE, f"{D3} › changed=false 那一格回 insights.status（不能显示成一次改写）",
    ),
    (
        "B3", "「已反馈」那一行的判据换成忙位 ⇒ 提交成功后六枚按钮永远不收起（工单 §怎么做.4"
              "「不重复弹按钮」），用户以为还没表态，于是连点三次拿三个 409",
        [(SCREEN, BAR_COND, "                {feedbackBusyId === item.id ? (\n")],
        GATE, f"{D3} › 提交成功过就收起按钮，改口才重新铺出来",
    ),
    (
        "X1", "给「证据不足」那一格也铺六枚按钮 ⇒ 表态对象根本不存在（那一格连结论都没铺给用户），"
              "而 Go 那边这条洞察连对外都不可见",
        [(SCREEN, INSUFFICIENT,
          "              <Text>{FEEDBACK_TYPES.map((x) => x).join(' ')}</Text>\n" + INSUFFICIENT)],
        GATE, f"{D3} › 六枚按钮只长在「有结论」那一格里（evidence_count===0 没有结论可表态）",
    ),
    # ── 3. 失败回滚（工单 §验收「失败回滚一条」）──
    (
        "R1", "摘掉 catch 里的回滚 ⇒ 请求根本没落库，而徽章停在用户以为已经改成的状态上："
              "这是本票最坏的一条谎（乐观更新没有回滚＝假改写永久留在界面）",
        [(SCREEN, ROLLBACK, "      void prev;\n")],
        GATE, f"{D5} › 回滚把整格收回 prev 或删掉（删不掉＝用户看到一次没发生的表态）",
    ),
    (
        "R2", "忙位不清 ⇒ 一次失败之后这一条洞察的六枚按钮永久置灰，用户既不能重试也不能改口",
        [(SCREEN, BUSY_CLEAR, "")],
        GATE, f"{D5} › 忙位进入与退出各一次（防连点，也防卡在忙态）",
    ),
    (
        "O1", "摘掉乐观写 ⇒ 这条红的是「先改本地徽章」那半句（工单 §怎么做.3）；"
              "它同时是 R1 的正向证人——没有乐观写就谈不上回滚",
        [(SCREEN, OPT_WRITE, "      void predicted;\n")],
        GATE, f"{D5} › 先乐观写、回执覆盖：setFeedbackById 两次（写／覆盖）加一次回滚",
    ),
    (
        "C4", "失败时把服务端那条中英双语串直接弹给用户 ⇒ 工单 §怎么做.3「文案走 i18n，"
              "不弹 error.message」失守：用户读到的是 `correction_text is required for correct`",
        [(SCREEN, ALERT_FAIL_T,
          "        (e instanceof ApiError ? e.message : t('insights.fbFailed'))\n")],
        GATE, f"{D4} › 不弹 e.message／error.message：服务端那条串是给运维看的",
    ),
    # ── 4. 空纠错不可提交（工单 §验收「空纠错不可提交一条」）──
    (
        "C1", "判空守卫摘掉 ⇒ 空正文照样发请求，Go 回 400，用户看到「提交失败」"
              "而不知道是自己在跟自己空气对话",
        [(SCREEN, EMPTY_GUARD, "    if (!correctionFor || correcting) return;\n")],
        GATE, f"{D4} › 空正文不发请求：trim 之后判空即 return（工单 §怎么做.2「非空才可提交」）",
    ),
    (
        "C2", "提交按钮的 disabled 摘掉 ⇒ 空正文时按钮仍然亮着：按下去只是静静地被 C1 吞掉，"
              "用户以为应用卡住了（判据与守卫必须是同一条）",
        [(SCREEN, DISABLED, "                disabled={correcting}\n")],
        GATE, f"{D4} › 提交按钮的 disabled 判据与上面同一条（灰掉而不是按下去没反应）",
    ),
    (
        "C3", "输入框的 maxLength 摘掉 ⇒ 前端能写 5000 字、Go 只收 500："
              "用户写完长文按提交，拿到的是一句不合形",
        [(SCREEN, INPUT_MAX, "              maxLength={5000}\n")],
        GATE, f"{D4} › 上限逐字等于 Go 的 maxCorrectionText，并且真的挂到 maxLength 上",
    ),
    (
        "N1", "「我要纠正」不再开弹层，直接提交空正文 ⇒ 这一档在 Go 那边必填正文（400），"
              "而纠错输入框从此永远不出现",
        [(SCREEN, CORRECT_BRANCH, "    if (false) {\n")],
        GATE, f"{D4} › 只有 correct 这一档开弹层，其余五档直接提交",
    ),
    # ── 5. 出口唯一与请求形状 ──
    (
        "X2", "屏上多发一处裸调用（绕过封装）⇒ 请求出口不再唯一：这一条不带 trace/语言头，"
              "而契约台账仍说「已接线」",
        [(SCREEN, CALLSITE,
          CALLSITE + "      await postInsightFeedback(item.id, 'confirm');\n")],
        GATE, f"{D1} › 屏上只调用 postInsightFeedback 一处，且从 api.ts 导入",
    ),
    (
        "X3", "body 长出 `user_id` ⇒ ADR-002「身份只从 Authorization/X-User-Id 进来」在这一格失去"
              "可证形式：Go 的 `DisallowUnknownFields` 会 400，但更坏的是客户端开始习惯自报身份",
        [(API, BODY,
          BODY.replace("        operation_id: operationId ?? '',\n",
                       "        operation_id: operationId ?? '',\n        user_id: insightId,\n"))],
        GATE, f"{D1} › 封装的 body 三键 == Go 的 v3FeedbackRequest 的 json tag（同序）",
    ),
    (
        "X4", "body 的 `correction_text` 改成 camelCase ⇒ Go 当场 400：用户写完 500 字提交，"
              "得到「请求不合形」，而这句话在前端永远说不清",
        [(API, BODY, BODY.replace("correction_text:", "correctionText:"))],
        GATE, f"{D1} › 封装的 body 三键 == Go 的 v3FeedbackRequest 的 json tag（同序）",
    ),
    # ── 6. 埋点 ──
    (
        "T1", "事件名改名 ⇒ 工单 §怎么做.5 点名的 `insight_feedback_submitted` 从分析面消失，"
              "付费/留存口径里这一格变成零，而界面一切正常",
        [(SCREEN, TRACK,
          "      track('insight_feedback', { feature: 'insights', action: type });\n")],
        GATE, f"{D6} › 事件名逐字等于工单 §怎么做.5 的 insight_feedback_submitted",
    ),
    (
        "T2", "六态值不埋了 ⇒ 只剩 `feature` 一条元数据：分析面看不出用户到底说了「准确」还是「不准确」，"
              "而这条判据钉的正是「白名单里的 action 承载六态」这笔改判",
        [(SCREEN, TRACK,
          "      track('insight_feedback_submitted', { feature: 'insights' });\n")],
        GATE, f"{D6} › 事件名逐字等于工单 §怎么做.5 的 insight_feedback_submitted",
    ),
    (
        "T3", "把纠错正文塞进埋点属性 ⇒ 内容多了第二条出网路径（白名单会当场把它丢掉，"
              "而这正是本仓的隐私红线：内容只走那一条 POST）",
        [(SCREEN, TRACK,
          "      track('insight_feedback_submitted', { feature: 'insights', action: type, correction });\n")],
        GATE, f"{D6} › 正文不进埋点队列（纠错内容只走那一条 POST）",
    ),
    # ── 7. i18n：键集对账（本票闸）＋ 15 语言总闸 ──
    (
        "I1", "en 的 `insights.fb` 少 `forget_from_model` ⇒ 屏上那一枚按钮渲染出裸键名"
              "（i18next 对缺失的插值键不回退成英文句子，直接吐键名）",
        [(EN, EN_FORGET, "")],
        GATE, f"{D7} › insights.fb 的键集 == Go 的 FeedbackTypes（多一档少一档都红）",
    ),
    (
        "I2", "zh 的 `insights.fbResult` 少 `retracted` ⇒ 用户点「不准确」之后徽章位置出现裸键名，"
              "而这正是 Go 的矩阵里真会折出来的那一档（读面六档表根本没有它）",
        [(ZH, ZH_RETRACTED, "")],
        GATE, f"{D7} › zh：六态＋五状态＋十句扁平文案全部有值，键集与 en 同",
    ),
    (
        "I3", "en 少 `fbDone` 的 `{{label}}` ⇒ 「已反馈」那一行不再说清用户表的是哪一态，"
              "只剩一句「已反馈」（改口时无法确认自己之前说了什么）",
        [(EN, EN_DONE, '    "fbDone": "You answered.",\n')],
        GATE, D7 + " › 扁平键的 {{label}} 只有一个，且 fbDone 真的带它（改口行嵌标签，不是再铺六个按钮）",
    ),
    (
        "I4", "km 的整句文案退回英文 ⇒ 本仓踩过两次的静默回退：fallbackLng 让它在真机上直接显英文，"
              "没有任何一处报错，「支持 15 种语言」变成一句假话（打的是 15 语言总闸）",
        [(KM, KM_SAVED, KM_SAVED_EN)],
        I18N_GATE,
        "13 个语言不留英文兜底（#39） › km：没有任何一个值逐字等于英文，除非落在设计同字白名单",
    ),
    (
        "I5", "th 漏掉扁平键 `fbAmend` ⇒ 漏键比漏翻译更难发现：键不存在时 i18next 静默回退成英文整句，"
              "而键集合对账是唯一会叫的闸",
        [(TH, TH_AMEND, "")],
        I18N_GATE,
        "15 语言文案表与英文源表同构 › th：键集合与 en 完全一致（漏键会静默回退成英文；复数键按逻辑键归一去重）",
    ),
    (
        "I6", "ja 的 `fbDone` 丢一个 `{{label}}` 占位符 ⇒ 界面上要么裸花括号要么丢内容，"
              "而这一格是「少一个就是运行时事故」那条逐语言对账",
        [(JA, JA_DONE, '    "fbDone": "フィードバック済み",\n')],
        I18N_GATE,
        "15 语言文案表与英文源表同构 › ja：{{占位符}}名称与个数逐键相等（复数键按逻辑键聚合；少一个就是运行时渲染出裸花括号）",
    ),
]

GREEN: list[tuple] = [
    (
        "GR1", "乐观表上方插 15 行注释 ⇒ 六态对账照旧绿（判据钉词表与映射，不钉行号）",
        [(SCREEN, "const LOCAL_TARGET_STATUS: Partial<Record<InsightFeedbackType, string>> = {\n",
          TS_COMMENTS + "const LOCAL_TARGET_STATUS: Partial<Record<InsightFeedbackType, string>> = {\n")],
        GATE, f"{D2} › 屏上六态与 Go 的词表同集（多一档少一档都红）",
    ),
    (
        "GR2", "submitFeedback 上方插注释 ⇒ 乐观写／回执覆盖／回滚三格照旧绿",
        [(SCREEN, "  const submitFeedback = async (\n",
          TS_COMMENTS + "  const submitFeedback = async (\n")],
        GATE, f"{D5} › 先乐观写、回执覆盖：setFeedbackById 两次（写／覆盖）加一次回滚",
    ),
    (
        "GR3", "按钮数组换成 Go 的声明序 ⇒ ② 照旧绿（这条判据比的是**集合**，"
               "按钮排布是界面事，工单 §怎么做.1 的排序不在闸里）",
        [(SCREEN, FEEDBACK_TYPES,
          "  'confirm', 'reject', 'partially_confirm', 'correct', 'outdated', 'forget_from_model',\n")],
        GATE, f"{D2} › 屏上六态与 Go 的词表同集（多一档少一档都红）",
    ),
    (
        "GR4", "en 的 fbDone 换措辞（仍带一个 {{label}}）⇒ 键集与占位符对账照旧绿",
        [(EN, EN_DONE, '    "fbDone": "Your answer: {{label}}",\n')],
        GATE, D7 + " › 扁平键的 {{label}} 只有一个，且 fbDone 真的带它（改口行嵌标签，不是再铺六个按钮）",
    ),
    (
        "GR5", "Modal 上方插注释 ⇒ 弹层三条判据（非空可提交／多行／返回键关闭）照旧绿",
        [(SCREEN, "      <Modal\n", TS_COMMENTS + "      <Modal\n")],
        GATE, f"{D4} › 弹层是 Modal 多行输入，关闭走 onRequestClose（安卓返回键）",
    ),
]


def _apply(edits: list) -> None:
    """锚点替换成变异体；命中数必须恰好 1——命中两次说明靶写错了。"""
    for path, old, new in edits:
        assert path.exists(), f"{path}：靶文件不存在"
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:160]!r}"
        path.write_text(src.replace(old, new, 1), encoding="utf-8")


def verify_anchor(edits: list) -> str | None:
    for path, old, _new in edits:
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if n != 1:
            return f"锚点命中 {n} 次（要 1）：{old[:80]!r}"
    return None


def verify_marker(target: str, marker: str) -> str | None:
    """marker 必须是闸里真的在跑的那一条：逐字回查测试源文件，`it.each` 的模板另算。"""
    title = marker.split(" › ")[-1]
    src = (FE / target).read_text(encoding="utf-8")
    cands = [title]
    if "：" in title:
        tail = title.split("：", 1)[1]
        cands.append("%s：" + tail)
        if "→" in title:
            cands.append("%s → %s：" + tail)
    if title.startswith("按钮 "):
        cands.append("按钮 %s 在屏上有字面量，并且在 i18n 里有同名词")
    for cand in cands:
        if f"'{cand}'" in src or f"`{cand}`" in src:
            return None
    return f"闸里没有这条判据：{title}"


def run_jest(target: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(JEST), target, "--ci"], cwd=FE, capture_output=True, text=True, timeout=1200,
    )
    return proc.returncode, proc.stdout + proc.stderr


BASELINE: dict[str, tuple[int, str]] = {}


def baseline(target: str) -> tuple[int, str]:
    if target not in BASELINE:
        BASELINE[target] = run_jest(target)
    return BASELINE[target]


def is_red(code: int, out: str, marker: str) -> bool:
    """「红」＝**那条判据自己**失败；套件坏在解析期、或红的是别的判据，都不算。"""
    if code == 0:
        return False
    if "Test suite failed to run" in out:
        return False
    return f"● {marker}" in out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数与判据在册，不改任何文件")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()
    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    cases = [*MUTATIONS, *GREEN]
    if args.check:
        bad = 0
        for cid, _d, edits, target, marker in cases:
            problem = verify_anchor(edits) or verify_marker(target, marker)
            if problem:
                print(f"坏锚点/坏判据 {cid}：{problem}")
                bad += 1
        print(f"锚点与判据校验：{'全部命中一次且判据在册' if bad == 0 else f'{bad} 条有问题'}"
              f"（共 {len(cases)} 条）")
        return 1 if bad else 0

    if not JEST.is_file():
        print(f"jest 不存在：{JEST}（先在 frontend 里 npm ci）")
        return 2

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-t305-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            rel = path.relative_to(REPO).as_posix().replace("/", "+")
            bak = Path(tmp) / f"{idx:02d}-{rel}"
            assert bak not in originals.values(), f"备份名碰撞：{bak}"
            shutil.copy2(path, bak)
            originals[path] = bak

        def restore() -> None:
            for path, bak in originals.items():
                shutil.copy2(bak, path)
                assert filecmp.cmp(bak, path, shallow=False), f"{path} 恢复后字节不一致"

        red_ok = red_fail = green_ok = green_fail = 0
        unrun: list[str] = []
        failures: list[str] = []
        try:
            for cid, desc, edits, target, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(target)
                if base_code != 0:
                    unrun.append(cid)
                    print(f"[{cid}] 未跑（基线就不绿，exit={base_code}）："
                          "不能把环境的红记成闸的红\n  "
                          + "\n  ".join(base_out.splitlines()[-8:]))
                    continue
                try:
                    _apply(edits)
                    code, out = run_jest(target)
                finally:
                    restore()
                if is_red(code, out, marker):
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗  {desc}\n  " + "\n  ".join(out.splitlines()[-16:]))

            for cid, desc, edits, target, marker in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(target)
                if base_code != 0:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（基线就不绿，exit={base_code}）：{desc}")
                    continue
                try:
                    _apply(edits)
                    code, out = run_jest(target)
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 被误伤 ✗  {desc}\n  " + "\n  ".join(out.splitlines()[-16:]))
        finally:
            restore()
            residue = [p for p in paths if not filecmp.cmp(originals[p], p, shallow=False)]
            print(f"\n残留复核：{'0 个靶文件与原件逐字节一致' if not residue else ''}")
            if residue:
                print("  以下靶文件恢复后仍不一致：" + "、".join(str(p) for p in residue))

        total_red = red_ok + red_fail
        total_green = green_ok + green_fail
        print(f"\n汇总：红闸 {red_ok}/{total_red}｜绿闸 {green_ok}/{total_green}｜"
              f"未跑 {len(unrun)}{'（' + ','.join(unrun) + '）' if unrun else ''}｜"
              f"基线 {GATE.split('/')[-1]} 与 {I18N_GATE.split('/')[-1]} 各跑一次")
        if failures:
            print("失败条目：" + ",".join(failures))
        if unrun:
            print("ⓘ 有靶未跑：基线不绿不算闸的红，也不念成绿")
        return 1 if (red_fail or green_fail or unrun) else 0


if __name__ == "__main__":
    sys.exit(main())
