#!/usr/bin/env python3
"""T3-04（前端：V3 洞察的证据区＋状态徽章）的变异自证（2026-10-20）。

靶文件七个（api.ts／types/index.ts／InsightsScreen.tsx／en.ts／km.ts／th.ts／fr.ts），
红闸 24 条、绿闸 5 条。闸有两份：`__tests__/insightEvidenceWiring.test.ts`（本票的接线闸）
与 `__tests__/i18nLocales.test.ts`（15 语言的总闸）。

工单 §验收 四句，每句都有自己的靶与红闸（[[feedback-mutation-anchor-must-reach-target]]）：

1. 「路径逐字」⇒ F1（列表路径改名）、F2（GET 退化成 POST＝打不通的那条线）、
   F3（id 不编码：一颗带 `?` 的 id 会打到另一条端点上）、F4（body 字段改名）、
   F5（body 长出 `user_id`＝ADR-002 的身份入口在这一格失守）；
2. 「字段逐字消费（防解析不到即静默丢弃）」⇒ T1…T5 各改一个 TS 字段名：
   **这类改名字节层面完全合法**，TS 不报错、后端照样 200，坏的只是「那一栏永远空着」，
   所以判据两侧必须不同源——TS 侧读 `types/index.ts`，Go 侧读 `httpapi/*.go` 的 json tag；
3. 「空证据不渲染结论」⇒ U1（把 statement 铺回 `evidence_count === 0` 那一格）、
   U2（判据从 `=== 0` 挪走＝这一格再也不会被走到）、U3（confidence 判空摘掉＝缺数据显示成 0%）；
4. 「15 语言键集齐」⇒ I1/I2 打**词表对账**（en 少一档状态、多一档 restricted 的 claim），
   I3/I4/I5 打**跨语言总闸**（km 整句英文兜底、th 漏一个 sourceType 键、fr 少一个占位符）——
   三种坏法在真机上都是静默的：fallbackLng 让它们直接显英文，没有任何一处会报错。

另有四态分离与「裁剪不藏」：U4（重试按钮硬编码英文）、U5（5 条上限被放开＝一次详情请求把
整个依据集铺进列表）、U6（不再消费 `truncated`＝只说 5 条不说 5/23，替后端把裁剪藏起来）、
U7（游客守卫摘掉＝必 401 的请求照发）、U8（展开即重发＝点一次发一次）、
U9（摘掉周报卡的期间级依据＝违反 ADR-003「新面不替换旧卡」，这一条是**归零型**判据，
正向证人是同一段里的 `services/evidence.ts 仍在` 与「两张卡各在页上」）。

纪律：串行执行（[[feedback-mutation-gates-no-concurrency]]）、copy2 备份＋`filecmp(shallow=False)`
逐字节写回、不用 git checkout（[[feedback-mutation-restore-no-git-checkout]]）；每条红闸先跑基线，
基线不绿一律记「未跑」（[[feedback-gate-needs-positive-witness]]）；jest 侧
`Test suite failed to run`（解析/语法坏掉）**不算打红**——那些红的是工具链，不是判据；
`--check` 会逐条把 marker（含 `it.each` 的 `%s` 模板）回查回闸的源文件，防止「靶打歪了、
判据根本不存在」被念成打红。

用法：
    ./.venv/bin/python scripts/mutate_t304_gates.py --check
    ./.venv/bin/python scripts/mutate_t304_gates.py
    ./.venv/bin/python scripts/mutate_t304_gates.py --only U1,T2,I4
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
API = SRC / "services" / "api.ts"
TYPES = SRC / "types" / "index.ts"
SCREEN = SRC / "screens" / "InsightsScreen.tsx"
EN = SRC / "i18n" / "locales" / "en.ts"
KM = SRC / "i18n" / "locales" / "km.ts"
TH = SRC / "i18n" / "locales" / "th.ts"
FR = SRC / "i18n" / "locales" / "fr.ts"

GATE = "__tests__/insightEvidenceWiring.test.ts"
I18N_GATE = "__tests__/i18nLocales.test.ts"
JEST = FE / "node_modules" / ".bin" / "jest"

D1 = "① 三条新封装指向契约里那三条 path"
D2 = "② 六个形状与 Go 的 json tag 逐字同集同序"
D3 = "③ evidence_count === 0 那一格里没有结论"
D4 = "④ 徽章与标签的键集 = Go 的词表（两侧不同源）"
D5 = "⑤ 加载/空/失败/重试四态分离，且裁剪不藏"
D6 = "⑥ 新面不替换旧卡"

EN_CANONICAL_EMPTY = (
    '"canonicalEmpty": "There is nothing to tell you yet. The model only draws a '
    'conclusion once it has enough of your own records behind it.",'
)

# ── 锚点（逐字取自靶文件，命中数必须恰好 1）────────────────────────────────
LIST_CALL = "  return request<InsightListPage>('/insights' + tail);\n"
DETAIL_CALL = "  return request<InsightDetail>(`/insights/${encodeURIComponent(insightId)}`);\n"
FEEDBACK_BODY = (
    "      body: JSON.stringify({\n"
    "        type,\n"
    "        correction_text: correctionText ?? '',\n"
    "        operation_id: operationId ?? '',\n"
    "      }),\n"
)
INSUFFICIENT_CELL = "<Text style={styles.emptyText}>{t('insights.evidenceInsufficient')}</Text>"
CONF_GUARD = "                  {item.confidence != null && (\n"
DETAIL_RETRY = (
    "                          onPress={() => fetchDetail(item.id)}\n"
    "                          accessibilityRole=\"button\"\n"
    "                        >\n"
    "                          <Text style={styles.retryText}>{t('common.retry')}</Text>\n"
)
MAX_CONST = "const EVIDENCE_MAX = 5;\n"
TRUNC_GUARD = "                        {detailById[item.id].truncated && (\n"
GUEST_GUARD = (
    "    if (!isLoggedIn) {\n"
    "      // 游客态：这一面需要 JWT，请求必 401，所以直接落到空态而不是失败态\n"
)
TOGGLE_CALL = "    if (!detailById[item.id]) await fetchDetail(item.id);\n"
WEEKLY_EVIDENCE = "        w?.content ? await memoriesWithinDays(7, w.period).catch(() => []) : []\n"
EN_DISMISSED = '      "dismissed": "Dismissed",\n'
EN_CLAIM_HEAD = '      "observation": "Observation",\n'
KM_EMPTY = (
    '"canonicalEmpty": "មិនទាន់មានអ្វីនិយាយទេ។ '
    'គំរូនឹងសន្និដ្ឋានលុះត្រាតែមានកំណត់ត្រារបស់អ្នកគ្រប់គ្រាន់។",'
)
TH_FRAMEWORK = '      "framework_inference": "การสรุปจากกรอบทฤษฎี",\n'
FR_TOGGLE = '"evidenceToggle": "{{n}} preuves {{arrow}}",'

TS_COMMENTS = "".join(
    f"// 绿闸注脚 {i:02d}：这一格判的是字段名与语义，不钉行号也不钉排版。\n" for i in range(1, 16)
)

MUTATIONS: list[tuple] = [
    # ── 1. 路径逐字（靶 api.ts）──
    (
        "F1", "列表路径改名（`/insights` → `/insight`）⇒ BFF 上根本没有这一条，用户打开洞察屏"
              "看到的是永久失败态；而契约登记表一个字没改，台账仍说「已接线」",
        [(API, LIST_CALL, LIST_CALL.replace("'/insights'", "'/insight'"))],
        GATE, f"{D1} › listInsights：GET /api/v1/insights，三个查询参数名逐字",
    ),
    (
        "F2", "GET 封装长出 `method: 'POST'` ⇒ 这一面只有 GET，POST 打出来是 405/404，"
              "坏法与 F1 一样安静（返回体解不出来时前端只会显示空列表）",
        [(API, LIST_CALL, LIST_CALL.replace("+ tail);", "+ tail, { method: 'POST' });"))],
        GATE, f"{D1} › listInsights：GET /api/v1/insights，三个查询参数名逐字",
    ),
    (
        "F3", "详情的 id 不再 encodeURIComponent ⇒ id 里出现 `?`／`#` 时打到另一条端点上，"
              "表现为「只有某几条洞察点不开」——最难归因的那类 404",
        [(API, DETAIL_CALL,
          "  return request<InsightDetail>(`/insights/${insightId}`);\n")],
        GATE, f"{D1} › getInsight：id 走 path 且编码（不编码时一颗带 ? 的 id 会打到另一条端点）",
    ),
    (
        "F4", "纠错正文的字段名从 Go 的 tag（`correction_text`）改成 camelCase ⇒ Go 那边"
              "`DisallowUnknownFields` 当场 400：用户写完 500 字提交，得到「请求不合形」，"
              "而这句话在前端永远说不清",
        [(API, FEEDBACK_BODY, FEEDBACK_BODY.replace("correction_text:", "correctionText:"))],
        GATE, f"{D1} › postInsightFeedback：feedback 后缀逐字，正文字段名跟 Go 的 tag 同字",
    ),
    (
        "F5", "反馈请求体长出 `user_id` ⇒ ADR-002「身份只从 Authorization/X-User-Id 进来」在这一格"
              "失去可证形式：客户端开始习惯自报身份，而服务端根本不看它",
        [(API, FEEDBACK_BODY,
          FEEDBACK_BODY.replace("        operation_id: operationId ?? '',\n",
                                "        operation_id: operationId ?? '',\n        user_id: insightId,\n"))],
        GATE, f"{D1} › postInsightFeedback：feedback 后缀逐字，正文字段名跟 Go 的 tag 同字",
    ),
    # ── 2. 字段名与 Go 的 json tag 逐字对账（靶 types/index.ts）──
    (
        "T1", "`InsightEvidenceItem.excerpt` 改名成 `text` ⇒ 这是本票 §验收 点名的坏法："
              "后端照常 200，TS 照常编译，只有依据正文从此永远是空的（解析不到即静默丢弃）",
        [(TYPES, "  excerpt: string | null;\n  observed_at: string | null;\n",
          "  text: string | null;\n  observed_at: string | null;\n")],
        GATE, f"{D2} › evidenceWire ↔ InsightEvidenceItem",
    ),
    (
        "T2", "`InsightListItem.evidence_count` 改成 camelCase ⇒ 空证据那一格的判据永远读到 undefined，"
              "「证据不足就不铺结论」整条 UI 闸从根上失效（§怎么做.2 的 G-05 面）",
        [(TYPES, "  published_at: string;\n  evidence_count: number;\n}",
          "  published_at: string;\n  evidenceCount: number;\n}")],
        GATE, f"{D2} › v3InsightListItemWire ↔ InsightListItem",
    ),
    (
        "T3", "详情的 `patterns` 改名 ⇒ 模式组静默消失；这一格归 T4-04 呈现，但**在册**是本票的交付物",
        [(TYPES, "  patterns: InsightPatternGroup[];\n",
          "  pattern_groups: InsightPatternGroup[];\n")],
        GATE, f"{D2} › v3InsightDetailPayload ↔ InsightDetail",
    ),
    (
        "T4", "六态反馈类型里 `forget_from_model` 写成 `forget` ⇒ TS 侧「合法值」比 Go 少一档，"
              "用户点「别再这样猜我」得到 400，而前端类型检查全绿",
        [(TYPES, "  | 'forget_from_model';\n", "  | 'forget';\n")],
        GATE, f"{D2} › 反馈六态类型跟着 Go 的 FeedbackTypes（02:156-163）",
    ),
    (
        "T5", "`source_type` 改名成 `type` ⇒ 依据的「这句话是从哪儿来的」那一枚标签整排空掉，"
              "而这正是本票要给用户看的唯一来源线索",
        [(TYPES, "  record_id: string;\n  source_type: string;\n",
          "  record_id: string;\n  type: string;\n")],
        GATE, f"{D2} › sourceWire ↔ InsightEvidenceSource",
    ),
    # ── 3. 空证据不渲染结论（靶 InsightsScreen.tsx）──
    (
        "U1", "把 `item.statement` 铺回「证据不足」那一格 ⇒ §怎么做.2 的 G-05 UI 面失守："
              "没有活支撑的结论照样进了用户的眼睛，只是旁边多了一句「不足」",
        [(SCREEN, INSUFFICIENT_CELL,
          "<Text style={styles.bodyText}>{item.statement}</Text>\n              " + INSUFFICIENT_CELL)],
        GATE, f"{D3} › 那一格只有一句「证据不足」，不出现 statement／徽章／依据栏",
    ),
    (
        "U2", "判据从 `=== 0` 挪到 `=== 1` ⇒ 这一格永远不会被走到（0 条证据落进 else 分支），"
              "「空证据不显示结论」变成一段死代码，而屏上看不出任何差别",
        [(SCREEN, "{item.evidence_count === 0 ? (", "{item.evidence_count === 1 ? (")],
        GATE, f"{D3} › 判据逐字存在，且用的是列表带回来的 evidence_count",
    ),
    (
        "U3", "confidence 的判空摘掉 ⇒ 缺数据（Go 的 `*float64` 为 null）显示成「Confidence 0%」："
              "那是模型的一句断言，不是「模型没说」",
        [(SCREEN, CONF_GUARD, "                  {true && (\n")],
        GATE, f"{D3} › 结论与徽章各消费列表行的字段，不是从详情里另取一份",
    ),
    (
        "U4", "重试按钮硬编码英文 `Retry` ⇒ 15 语言里这一枚按钮只有英文；"
              "闸钉的正是「一律走 common.retry」这个白名单",
        [(SCREEN, DETAIL_RETRY, DETAIL_RETRY.replace(
            '<Text style={styles.retryText}>{t(\'common.retry\')}</Text>',
            "<Text style={styles.retryText}>Retry</Text>"))],
        GATE, f"{D5} › 失败态与空态是两句文案，重试一律走 common.retry",
    ),
    (
        "U5", "`EVIDENCE_MAX` 从 5 放开到 50 ⇒ 一次展开把整个依据集铺满屏幕，"
              "而「裁剪」这件事的后端口径（N9-11）就不再需要 `truncated` 了——两头一起漂",
        [(SCREEN, MAX_CONST, "const EVIDENCE_MAX = 50;\n")],
        GATE, f"{D5} › 依据最多 5 条是这一屏的常量，且 truncated 时给出裁剪前的总数",
    ),
    (
        "U6", "不再消费 `truncated` ⇒ 用户看到 5 条就以为自己只有 5 条依据："
              "后端如实说了「还有 18 条」而前端替它藏起来",
        [(SCREEN, TRUNC_GUARD, "                        {!!detailById[item.id] && (\n")],
        GATE, f"{D5} › 依据最多 5 条是这一屏的常量，且 truncated 时给出裁剪前的总数",
    ),
    (
        "U7", "游客守卫摘掉 ⇒ 这一面必 401 的请求照发，失败态替游客显示「加载失败 + 重试」，"
              "而真正缺的是登录；空态与失败态在游客身上成了同一句谎话",
        [(SCREEN, GUEST_GUARD, "    if (false) {\n")],
        GATE, f"{D5} › 游客态跳过请求（这一面要 JWT，硬发只会得到 401）",
    ),
    (
        "U8", "展开时不再判「已取过」⇒ 每一次点依据栏都发一次详情请求："
              "列表 20 条时来回点就是 20 次以上，而缓存那一格是 §怎么做.2 明写的",
        [(SCREEN, TOGGLE_CALL, "    await fetchDetail(item.id);\n")],
        GATE, f"{D5} › 详情只有 getInsight 一个调用点，且展开过就不再重复发请求",
    ),
    (
        "U9", "摘掉周报卡的期间级依据聚合 ⇒ 违反 ADR-003「新面不替换旧卡」："
              "canonical 依据上线的同时把 #34 那格期间级证据删掉，用户少看到的是旧口径的全部出处",
        [(SCREEN, WEEKLY_EVIDENCE, "        []\n")],
        GATE, f"{D6} › 周报卡与期间级聚合一行没动",
    ),
    # ── 4. 徽章/标签词表跟着 Go ＋ 15 语言总闸 ──
    (
        "I1", "en 的 `insights.status` 少了 `dismissed` ⇒ 一条被用户忽略过的洞察，屏上徽章位置"
              "渲染出裸键名 `insights.status.dismissed`（i18next 对缺失的插值键不回退成英文句子，"
              "直接吐键名），而这正是 Go 的返回集合里真有的一档",
        [(EN, EN_DISMISSED, "")],
        GATE, f"{D4} › 状态徽章键 == 这一面真能返回的状态集合（N9 三态 ∪ 用户表态三态）",
    ),
    (
        "I2", "en 的 `insights.claim` 多出 `cause` ⇒ 前端给一档**受限**声明备好了标签，"
              "等于预告「这条端点会返回 cause」，而 Go 的 gate 现在一律 reject 它（03 §14）",
        [(EN, EN_CLAIM_HEAD, EN_CLAIM_HEAD + '      "cause": "Cause",\n')],
        GATE, f"{D4} › claim_type 标签键 == domain.AllowedClaimTypes（03 §14）",
    ),
    (
        "I3", "km 的整句文案退回英文 ⇒ 本仓踩过两次的静默回退：fallbackLng 让它在真机上直接显英文，"
              "没有任何一处报错，「支持 15 种语言」变成一句假话（打的是 15 语言总闸）",
        [(KM, KM_EMPTY, EN_CANONICAL_EMPTY)],
        I18N_GATE,
        "13 个语言不留英文兜底（#39） › km：没有任何一个值逐字等于英文，除非落在设计同字白名单",
    ),
    (
        "I4", "th 漏掉 `sourceType.framework_inference` 一个键 ⇒ 漏键比漏翻译更难发现："
              "键不存在时 i18next 静默回退成英文整句，而键集合对账是唯一会叫的闸",
        [(TH, TH_FRAMEWORK, "")],
        I18N_GATE,
        "15 语言文案表与英文源表同构 › th：键集合与 en 完全一致（漏键会静默回退成英文；复数键按逻辑键归一去重）",
    ),
    (
        "I5", "fr 的 `evidenceToggle` 少一个 `{{arrow}}` 占位符 ⇒ 展开/收起的箭头从界面上消失，"
              "而这一格是「少一个就是运行时渲染出裸花括号／丢一个符号」的那条对账",
        [(FR, FR_TOGGLE, '"evidenceToggle": "{{n}} preuves",')],
        I18N_GATE,
        "15 语言文案表与英文源表同构 › fr：{{占位符}}名称与个数逐键相等（复数键按逻辑键聚合；少一个就是运行时渲染出裸花括号）",
    ),
]

GREEN: list[tuple] = [
    (
        "GR1", "api.ts 的 getInsight 上方插 15 行注释 ⇒ 编码判据照旧绿（它钉的是写法语义，不钉行号）",
        [(API, DETAIL_CALL, TS_COMMENTS + DETAIL_CALL)],
        GATE,
        f"{D1} › getInsight：id 走 path 且编码（不编码时一颗带 ? 的 id 会打到另一条端点）",
    ),
    (
        "GR2", "InsightsScreen 的 loadInsights 上方插注释 ⇒ 游客守卫那格照旧绿",
        [(SCREEN, "  const loadInsights = useCallback(async () => {\n",
          TS_COMMENTS + "  const loadInsights = useCallback(async () => {\n")],
        GATE, f"{D5} › 游客态跳过请求（这一面要 JWT，硬发只会得到 401）",
    ),
    (
        "GR3", "en.ts 的 canonicalTitle 上方插注释 ⇒ claim 词表对账照旧绿（不认排版只认键集）",
        [(EN, '    "canonicalTitle": "Insights from your own data",\n',
          TS_COMMENTS + '    "canonicalTitle": "Insights from your own data",\n')],
        GATE, f"{D4} › claim_type 标签键 == domain.AllowedClaimTypes（03 §14）",
    ),
    (
        "GR4", "types/index.ts 的 InsightEvidenceItem 文档行加长 ⇒ 字段对账照旧绿"
               "（`tsFields` 只看两格缩进的字段声明，注释行不算）",
        [(TYPES, "/** 一条依据：Go 的 `evidenceWire`（handlers_evidence.go:58-67）。 */\n",
          "/** 一条依据：Go 的 `evidenceWire`（handlers_evidence.go:58-67）。\n"
          " *  绿闸注脚：本行只加文档，不改字段名。 */\n")],
        GATE, f"{D2} › evidenceWire ↔ InsightEvidenceItem",
    ),
    (
        "GR5", "km.ts 的 canonicalEmpty 上方插注释 ⇒ 该语言的徽章键集照旧绿（15 语言逐门都有正向证人）",
        [(KM, '    "canonicalTitle"', TS_COMMENTS + '    "canonicalTitle"')],
        GATE, f"{D4} › km：五档徽章＋标签＋区文案全部有值",
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
    """marker 必须是闸里真的在跑的那一条：先去掉 it('…') 包裹，再逐字回查测试源文件。

    `it.each` 的标题在源里带 `%s`（jest 打印时已替换成语言码），所以两侧都认。
    """
    title = marker.split(" › ")[-1]
    src = (FE / target).read_text(encoding="utf-8")
    candidates = [title]
    if "：" in title:
        candidates.append("%s：" + title.split("：", 1)[1])
    if " ↔ " in title:
        candidates.append("%s ↔ %s")
    for cand in candidates:
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
    with tempfile.TemporaryDirectory(prefix="mutate-t304-") as tmp:
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
