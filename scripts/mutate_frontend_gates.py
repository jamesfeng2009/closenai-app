#!/usr/bin/env python3
"""前端三类闸的变异自证（2026-10-14，jest 基线修复批的 DoD ②）。

被修的三类根因
1. `referral.test.ts` 里三条 ENOENT：站点 28a9fc3 迁 Astro 后真源搬到 `site/public/`，
   测试路径陈旧（产品件没缺）。
2. `i18nLocales.test.ts` 的 17 条英文兜底：317b390 往 15 个 locale 写回
   `explore.reflectionsTitle`/`reflectionsEmpty` 时非英语填的是英文原文。
3. `feedbackStates.test.ts` 的 `setSavingId(null)`：固定 16 行窗被 317b390 插进的
   13 行本地落库挤出窗外（假红）。

闸要能被一次真实破坏打红才算数，所以这里逐条写坏→只跑受害的测试文件→要求输出里
出现那条失败用例的名字→备份写回。**恢复用 copy2 + filecmp，不用 git checkout**
（见 [[feedback-mutation-restore-no-git-checkout]]）。

F16–F27（2026-10-14 追加）覆盖 #64 年报入口、#65 游客态埋点与 B1 登录补调这三批新闸：
埋点 source、language 透传、15 语言键、isLoggedIn 上送门、免费卡决策、40402 空态码、
暂存清理顺序各打红一次；G2 证明函数体增长不会把切片守卫变成假红。

F28–F30（2026-10-14 再追加）固化 2026-10-13 i18n 审校里只剩口头声明的 3 条反向验证
（当时的脚本在 /tmp/i18n/，目录已消失，仓内不可重跑）——见下方该段的注释。

用法：
    python3 scripts/mutate_frontend_gates.py --check   # 只验锚点，各命中一次
    python3 scripts/mutate_frontend_gates.py           # 46 条红 + 4 条绿
    python3 scripts/mutate_frontend_gates.py --only F1,F9
"""
from __future__ import annotations

import argparse
import filecmp
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FRONT = REPO / "frontend"
SITE = REPO / "site" / "public"
LOCALES = FRONT / "src" / "i18n" / "locales"
REFLECT = FRONT / "src" / "screens" / "ReflectScreen.tsx"

I18N = "__tests__/i18nLocales.test.ts"
REFERRAL = "__tests__/referral.test.ts"
FEEDBACK = "__tests__/feedbackStates.test.ts"
YR = "__tests__/yearInReviewEntry.test.ts"
GUEST = "__tests__/analyticsGuestQueue.test.ts"
FUNNEL = "__tests__/funnel.test.ts"
SCREEN = FRONT / "src" / "screens" / "InsightsScreen.tsx"
API = FRONT / "src" / "services" / "api.ts"
FUNNEL_SRC = FRONT / "src" / "services" / "funnel.ts"
APP_TSX = FRONT / "App.tsx"
AUTH = FRONT / "src" / "context" / "AuthContext.tsx"
FR = "__tests__/firstRead.test.ts"
COMBO = "__tests__/comboCard.test.ts"
GROWTH_TEST = "__tests__/growthRecordsWiring.test.ts"
GROWTH_SRC = FRONT / "src" / "screens" / "GrowthRecordsScreen.tsx"
EXPLORE_SRC = FRONT / "src" / "screens" / "ExploreScreen.tsx"
PDF_TEST = "__tests__/pdfExportWiring.test.ts"
PDF_CFG = FRONT / "src" / "config" / "pdfFiles.ts"
PDF_SVC = FRONT / "src" / "services" / "pdfExport.ts"
BASE64 = FRONT / "src" / "utils" / "base64.ts"

# ---------------------------------------------------------------- 红闸清单
# (id, 破坏说明, 文件, 原文, 坏文, 受害测试, 失败用例名片段)
MUTATIONS: list[tuple[str, str, Path, str, str, str, str]] = [
    (
        "F1",
        "es 的 reflectionsTitle 改回英文原文（正是 317b390 的写法）",
        LOCALES / "es.ts",
        '    "reflectionsTitle": "Mis reflexiones",',
        '    "reflectionsTitle": "My Reflections",',
        I18N,
        "没有任何一个值逐字等于英文",
    ),
    (
        "F2",
        "ja 改成拉丁文但**不与英文逐字相等**：只可能由文字种类闸抓到",
        LOCALES / "ja.ts",
        '    "reflectionsTitle": "私の振り返り",',
        '    "reflectionsTitle": "My Furikaeri",',
        I18N,
        "每个值都含该语言文字",
    ),
    (
        "F3",
        "de 塞进连续 3 个英文虚词：整句英文闸",
        LOCALES / "de.ts",
        '    "reflectionsTitle": "Meine Reflexionen",',
        '    "reflectionsTitle": "das the of your",',
        I18N,
        "没有整句英文的值",
    ),
    (
        "F4",
        "fr 的 hourOption 丢掉 {{hour}} 占位符：运行时直出裸花括号",
        LOCALES / "fr.ts",
        '    "hourOption": "{{hour}}:00",',
        '    "hourOption": ":00",',
        I18N,
        "名称与个数逐键相等",
    ),
    (
        "F5",
        "es 的 home.social 译掉：同形词豁免条目当场变死条目",
        LOCALES / "es.ts",
        '    "social": "Social",',
        '    "social": "Redes sociales",',
        I18N,
        "同形词豁免表不许留死条目",
    ),
    (
        "F6",
        "es 的 auth.version 换行并进一行：换行个数闸",
        LOCALES / "es.ts",
        '"version": "ClosenAI v1.0.0\\nBuild 2026.08.03"',
        '"version": "ClosenAI v1.0.0 Build 2026.08.03"',
        I18N,
        "换行转义个数逐键相等",
    ),
    (
        "F7",
        "ja（CLDR 单形态语言）多出 chatCount_one：死键档",
        LOCALES / "ja.ts",
        '    "chatCount_other": "{{count}}件の会話",',
        '    "chatCount_one": "{{count}}件の会話",\n    "chatCount_other": "{{count}}件の会話",',
        I18N,
        "变体键恰好等于该语言的类别集",
    ),
    (
        "F8",
        "ms 的键名打错一个字母：静默回退成英文",
        LOCALES / "ms.ts",
        '    "reflectionsTitle": "Refleksi Saya",',
        '    "reflectionsTitel": "Refleksi Saya",',
        I18N,
        "键集合与 en 完全一致",
    ),
    (
        "F9",
        "_redirects 的 /r/* 从 200 重写改成 301 跳转",
        SITE / "_redirects",
        "/r/*     /r/index.html  200",
        "/r/*     /r/index.html  301",
        REFERRAL,
        "落地页只重写 /r/*",
    ),
    (
        "F10",
        "AASA 接管路径从 /r/* 放宽成 *（整站变唤起 App）",
        SITE / ".well-known" / "apple-app-site-association",
        '"/": "/r/*",',
        '"/": "*",',
        REFERRAL,
        "落地页只重写 /r/*",
    ),
    (
        "F11",
        "落地页码表少一个字符（与前后端不再逐字相等 → 静默丢归因）",
        SITE / "r" / "index.html",
        "[ABCDEFGHJKMNPQRSTUVWXYZ234567]{8}",
        "[ABCDEFGHJKNPQRSTUVWXYZ234567]{8}",
        REFERRAL,
        "落地页的码字符集与前后端逐字相等",
    ),
    (
        "F12",
        "onSaveDraft 的 finally 里删掉 setSavingId(null)（按钮永久卡「保存中」）",
        REFLECT,
        "    } finally {\n      setSavingId(null);\n    }",
        "    } finally {\n    }",
        FEEDBACK,
        "onSaveDraft 记录 savingId",
    ),
    (
        "F13",
        "删掉连点守卫 if (savingId) return;",
        REFLECT,
        "    if (savingId) return; // 单条进行中：忽略连点，避免同一草稿重复入库\n",
        "",
        FEEDBACK,
        "onSaveDraft 记录 savingId",
    ),
    (
        "F14",
        "删掉按钮的 disabled={savingId === msg.id}",
        REFLECT,
        "                  disabled={savingId === msg.id}\n",
        "",
        FEEDBACK,
        "按钮在保存中 disabled",
    ),
    (
        "F15",
        "keyboardVerticalOffset 从被验证过的 90 改成 100",
        REFLECT,
        "keyboardVerticalOffset={90}",
        "keyboardVerticalOffset={100}",
        FEEDBACK,
        "既有键盘避让参数不动",
    ),
]

# ------------------------------------------------- 年报入口 / 游客埋点两批新闸（2026-10-14，#64 #65）
# 对应 tasks.md:147（后端有能力、前端零入口）与 tasks.md:140（游客态 flush 口径未取证）。
MUTATIONS += [
    (
        "F16",
        "删掉 InsightsScreen 的 share_tapped 埋点（分享分子当场归零，转化率断链）",
        SCREEN,
        "      trackFunnelShare('share_tapped', 'year_review');\n",
        "",
        YR,
        "source 用的是枚举里的 year_review",
    ),
    (
        "F17",
        "把 year_review 从 FUNNEL_SOURCES 摘掉（调用点变成枚举外野字符串）",
        FUNNEL_SRC,
        "  'year_review',\n",
        "",
        FUNNEL,
        "全仓漏斗调用里没有枚举外的 step/source",
    ),
    (
        "F18",
        "getYearInReview 不再透传 language（后端按 en 生成，中文用户拿到英文年报）",
        API,
        "`/share/year-in-review?language=${langParam()}${suffix}`",
        "`/share/year-in-review${suffix}`",
        YR,
        "getYearInReview 打的是后端那条 GET",
    ),
    (
        "F19",
        "ko 的 shareYearReview 填英文原文（正是 317b390 那类写法）",
        LOCALES / "ko.ts",
        '    "shareYearReview": "올해 회고 공유하기",',
        '    "shareYearReview": "Share My Year in Review",',
        I18N,
        "没有任何一个值逐字等于英文",
    ),
    (
        "F20",
        "ko 漏掉 yearReviewEmpty 键（空态文案静默回退英文）",
        LOCALES / "ko.ts",
        '    "yearReviewEmpty": "그해에는 기록이 없어서 돌아볼 게 없어요. 몇 개 남긴 뒤에 다시 와보세요.",\n',
        "",
        I18N,
        "键集合与 en 完全一致",
    ),
    (
        "F21",
        "冷启动首帧的 flush 去掉 isLoggedIn() 门（游客打出必 401 的请求）",
        APP_TSX,
        "        // 首帧后上送本地分析事件队列（仅已登录；后端校验 consent + 匿名化转发 PostHog）\n"
        "        if (isLoggedIn()) flushAnalytics().catch(() => {});\n",
        "        // 首帧后上送本地分析事件队列（仅已登录；后端校验 consent + 匿名化转发 PostHog）\n"
        "        flushAnalytics().catch(() => {});\n",
        GUEST,
        "App.tsx 的两个上送点都在 isLoggedIn() 门内",
    ),
    (
        "F22",
        "回前台的 flush 去掉 isLoggedIn() 门（另一个上送点同口径）",
        APP_TSX,
        "        // 回到前台时上送累积的分析事件（离线期间的事件延迟批量上送，口径按 occurred_at）\n"
        "        if (isLoggedIn()) flushAnalytics().catch(() => {});\n",
        "        // 回到前台时上送累积的分析事件（离线期间的事件延迟批量上送，口径按 occurred_at）\n"
        "        flushAnalytics().catch(() => {});\n",
        GUEST,
        "App.tsx 的两个上送点都在 isLoggedIn() 门内",
    ),
    (
        "F23",
        "给年报路径加上 Pro 门控（推翻自定决策 ①：它是引流免费卡）",
        SCREEN,
        "    setYearReviewLoading(true);\n",
        "    setYearReviewLoading(true);\n      if (!isPro) { showProAlert(); return; }\n",
        YR,
        "不做 Pro 门控",
    ),
    (
        "F24",
        "空态判定码从 40402 改成 404（后端不返 404，零记录年份会弹通用报错）",
        SCREEN,
        "const CODE_RESOURCE_NOT_FOUND = 40402;",
        "const CODE_RESOURCE_NOT_FOUND = 404;",
        YR,
        "整年零记录按空态提示（40402）",
    ),
    (
        "F25",
        "把 clearPendingFirstRead 提到 POST 之前（POST 一失败暂存就没了，初读卡永久丢）",
        AUTH,
        "      const card = await postFirstRead(pending.answers, pending.persona_type);\n"
        "      await saveFirstReadCard(card);\n"
        "      await clearPendingFirstRead();\n",
        "      await clearPendingFirstRead();\n"
        "      const card = await postFirstRead(pending.answers, pending.persona_type);\n"
        "      await saveFirstReadCard(card);\n",
        FR,
        "清暂存排在 POST 之后",
    ),
    (
        "F26",
        "去掉「无暂存直接返回」早退（老用户每次登录都被打一次空 POST）",
        AUTH,
        "      if (!pending) return;\n",
        "",
        FR,
        "无暂存直接返回",
    ),
    (
        "F27",
        "登录里的补调去掉 void 前缀（裸 Promise 无人接管 rejection，补调失败=未捕获告警噪音）",
        AUTH,
        "    void flushPendingOnboarding();\n",
        "    flushPendingOnboarding();\n",
        FR,
        "登录与冷启动都挂补调",
    ),
]

# ---------------------------------------------------------------- 三条证明缺口固化（2026-10-14，C4 色环批）
# 2026-10-13 那轮 i18n 审校做了 7 条反向验证，脚本落在 /tmp/i18n/（现已消失），仓内可重跑的只有
# 4 条：豁免键改回非英文 / 豁免表塞死条目（同一道闸，= F5）、非豁免键写成逐字英文（= F1）、
# ja 拉丁借词未豁免（= F2）。剩下 3 条「文字集闸不再共用豁免表」「comboCard 术语表回退成英文 token」
# 「locale 丢 {{count}}」当时只被口头声明过，无法重跑 ⇒ 本批把它们变成可重跑变异（F28/F29/F30）。
# 前两条破坏的是**守卫自身**（证明那条断言真的在消费豁免表/术语表，不是空转），
# 第三条破坏的是数据（证明占位符闸真的会抓到少一个 {{count}}）。
TESTS = FRONT / "__tests__"
MUTATIONS += [
    (
        "F28",
        "文字集闸摘掉同形词豁免（不再与「不许与英文同字」那道闸共用 HOMOGRAPHS）："
        "km 的三行 Enneagram 借词当场被判成没落成高棉文",
        TESTS / "i18nLocales.test.ts",
        "    const allowed = new Set(HOMOGRAPHS[lng] ?? []);\n"
        "    const missing = Object.keys(en).filter(\n"
        "      (k) => k in tables[lng] && !isSharedByDesign(k) && !allowed.has(k) && !re.test(tables[lng][k])",
        "    const allowed = new Set<string>(); // 变异：不共用豁免表\n"
        "    const missing = Object.keys(en).filter(\n"
        "      (k) => k in tables[lng] && !isSharedByDesign(k) && !allowed.has(k) && !re.test(tables[lng][k])",
        I18N,
        "每个值都含该语言文字",
    ),
    (
        "F29",
        "comboCard 的九型断言回退成旧英文 token 口径（/Enneagram/）："
        "真翻译后的泰语 นพลักษณ์ 会被它判成漏写——正是改口径的理由",
        TESTS / "comboCard.test.ts",
        "      expect(v).toMatch(ENNEAGRAM_TERM[lng]);\n",
        "      expect(v).toMatch(/Enneagram/); // 变异：回退成英文 token 代理断言\n",
        COMBO,
        "非 zh 语言点名两个框架",
    ),
    (
        "F30",
        "zh 的 share.streak_other 丢掉 {{count}}（运行时直出「连续 天」，数字没了）",
        LOCALES / "zh.ts",
        '    "streak_other": "连续 {{count}} 天",',
        '    "streak_other": "连续很多天",',
        I18N,
        "名称与个数逐键相等",
    ),
]

# ---------------------------------------------------------------- U1 成长记录页接线闸（2026-10-15，P0-F07 §6）
# 这批的失效方式全静默：路径少一个字母只是「还没有记录」，tag 映射漂移只在非中文界面显中文，
# 造句替代 topics[].topic 让 15 语言白做，created_at 切串让跨时区差一天。F39 反过来证明
# 本批新加进 HOMOGRAPHS.fr 的那条豁免此刻真的在消费（改译了就该红），不是空转条目。
MUTATIONS += [
    (
        "F31",
        "把 /growth/records 写成 /growth/record（后端不返这个路径，列表永远显空态）",
        API,
        "return request<{ records: GrowthRecordItem[]; total: number }>('/growth/records');",
        "return request<{ records: GrowthRecordItem[]; total: number }>('/growth/record');",
        GROWTH_TEST,
        "api.ts 打的是 /growth/records 与 /growth/topics",
    ),
    (
        "F32",
        "删掉 App.tsx 里 GrowthRecords 的 Stack.Screen 注册（导航名对不上，入口点了没反应）",
        APP_TSX,
        "              <Stack.Screen\n"
        '                name="GrowthRecords"\n'
        "                component={GrowthRecordsScreen}\n"
        "                options={{ headerShown: true, headerStyle: { backgroundColor: Colors.bgPrimary }, headerTintColor: Colors.textPrimary, headerTitle: '' }}\n"
        "              />\n",
        "",
        GROWTH_TEST,
        "App.tsx 三处齐",
    ),
    (
        "F33",
        "前端 TAG_KEYS 删掉「沟通」一行（那一档筛选点不出来，且该标签的记录只能显中文规范名）",
        GROWTH_SRC,
        "  沟通: 'communication',\n",
        "",
        GROWTH_TEST,
        "两侧键集逐字相等",
    ),
    (
        "F34",
        "探索页入口导航名单复数写错（navigate 到没注册的名字）",
        EXPLORE_SRC,
        "navigation.navigate('GrowthRecords')",
        "navigation.navigate('GrowthRecord')",
        GROWTH_TEST,
        "探索页入口格在",
    ),
    (
        "F35",
        "话题行改成显 tp.tag（后端 15 语言的整句文案白生成，界面变成「情绪」两字）",
        GROWTH_SRC,
        "<Text style={styles.topicText}>{tp.topic}</Text>",
        "<Text style={styles.topicText}>{tp.tag}</Text>",
        GROWTH_TEST,
        "话题正文取 tp.topic",
    ),
    (
        "F36",
        "分桶改用 created_at.slice(0, 10)（那是 UTC 日，东八区用户差一天）",
        GROWTH_SRC,
        "r.created_at ? toLocalDate(r.created_at) : '—'",
        "r.created_at ? r.created_at.slice(0, 10) : '—'",
        GROWTH_TEST,
        "分桶走 toLocalDate",
    ),
    (
        "F37",
        "记录信封字段写成 items（读不到即整屏空态，正是最难查的那种假象）",
        GROWTH_SRC,
        "setRecords(recordsRes.records);",
        "setRecords((recordsRes as any).items);",
        GROWTH_TEST,
        "消费面逐字",
    ),
    (
        "F38",
        "zh 的 tag.mood 填英文原文 Mood（新键 × 15 语言那道闸的靶子）",
        LOCALES / "zh.ts",
        '      "mood": "情绪",',
        '      "mood": "Mood",',
        I18N,
        "没有任何一个值逐字等于英文",
    ),
    (
        "F39",
        "把 fr 的 tag.communication 译成 Échanges：本批为它加的 HOMOGRAPHS.fr 豁免条目"
        "当场变死条目（证明那条豁免此刻真在逐字消费，不是懒人兜底）",
        LOCALES / "fr.ts",
        '      "communication": "Communication",',
        '      "communication": "Échanges",',
        I18N,
        "同形词豁免表不许留死条目",
    ),
]

# F40–F46（2026-10-15 追加）＝ U2「PDF 导出接线」那批闸的反向验证。这批的风险全在
# 「前端自己造的第二真源」上——落盘文件名、错误码、鉴权形态，所以逐条真改一处打红。
# G4 证同一批的锚点不是固定行窗：往新handler里插注释不该让任何一条变红。
MUTATIONS += [
    (
        "F40",
        "回忆卡的落盘文件名漂一个字母（§七 登记的 Content-Disposition 就不是这个名字了）",
        PDF_CFG,
        "export const MEMORY_CARD_PDF_FILE = 'closenai_memory_card.pdf';",
        "export const MEMORY_CARD_PDF_FILE = 'closenai_memory_cards.pdf';",
        PDF_TEST,
        "回忆卡、年报两个固定名逐字相等",
    ),
    (
        "F41",
        "POST /share/memory-card 的 body 丢掉（后端 Pydantic 立刻 422，卡永远导不出）",
        API,
        "    body: JSON.stringify({ memory_id: memoryId }),\n",
        "",
        PDF_TEST,
        "回忆卡带 memory_id（POST body 不能丢）",
    ),
    (
        "F42",
        "周期报告的 binary 出口摘掉（拿 res.json() 解一份 PDF，成功态必炸）",
        API,
        "  return request<ArrayBuffer>(`/share/report/${period}/pdf?language=${langParam()}`, { binary: true });",
        "  return request<ArrayBuffer>(`/share/report/${period}/pdf?language=${langParam()}`);",
        PDF_TEST,
        "位子上（契约扫描器只认这个形态）",
    ),
    (
        "F43",
        "退回 Linking.openURL 去打开本地 PDF（这条路带不上 Bearer，也是 U2 的防退化项）",
        PDF_SVC,
        "  await Sharing.shareAsync(uri, { mimeType: 'application/pdf', UTI: 'com.adobe.pdf' });",
        "  Linking.openURL(uri);",
        PDF_TEST,
        "没有 Linking.openURL 调用",
    ),
    (
        "F44",
        "40402 写成 40401（USER_NOT_FOUND 那个码，前端已登录态会被当成注销清会话）",
        PDF_CFG,
        "export const CODE_RESOURCE_NOT_FOUND = 40402;",
        "export const CODE_RESOURCE_NOT_FOUND = 40401;",
        PDF_TEST,
        "两个常量与 Python 侧逐值相等",
    ),
    (
        "F45",
        "base64 尾部 padding 少一个字符（写进文件的 PDF 直接损坏，且只有非 3 倍数长度会坏）",
        BASE64,
        "      (i + 1 < len ? ALPHABET[(n >> 6) & 63] : '=') +",
        "      (i + 1 < len ? ALPHABET[(n >> 6) & 63] : '') +",
        PDF_TEST,
        "字节每个长度都与 Buffer 一致",
    ),
    (
        "F46",
        "km 的 pdf.exporting 删掉一行（15 语言缺一个，忙态在高棉语界面显空串）",
        LOCALES / "km.ts",
        '    "exporting": "កំពុងនាំចេញ…",\n',
        "",
        PDF_TEST,
        "pdf.exporting：15 语言都非空",
    ),
]

# ---------------------------------------------------------------- 绿闸清单
# 破坏「不该打红」的东西＝证明这条守卫不是假红制造机。
GREEN: list[tuple[str, str, Path, str, str, str]] = [
    (
        "G1",
        "往 onSaveDraft 的 try 里插 20 行（正是 317b390 造成假红的那种增长）："
        "函数体切片守卫不该因此变红",
        REFLECT,
        "    setSavingId(msg.id);\n",
        "    setSavingId(msg.id);\n" + "".join(f"    // 增长占位 {i}\n" for i in range(20)),
        FEEDBACK,
    ),
    (
        "G2",
        "往 onShareYearReview 函数体插 15 行注释（同类增长）："
        "年报的函数体切片守卫（Pro 门控 / 空态码 / share_text）不该因此变红",
        SCREEN,
        "    setYearReviewLoading(true);\n",
        "    setYearReviewLoading(true);\n"
        + "".join(f"      // 增长占位 {i}\n" for i in range(15)),
        YR,
    ),
    (
        "G3",
        "往 GrowthRecordsScreen 的 load 函数体插 15 行注释（同类增长）："
        "U1 接线闸钉的是逐字锚点而不是固定行窗，函数体变长不该把它变红",
        GROWTH_SRC,
        "      setLoading(true);\n",
        "      setLoading(true);\n"
        + "".join(f"      // 增长占位 {i}\n" for i in range(15)),
        GROWTH_TEST,
    ),
    (
        "G4",
        "往 InsightsScreen 的 onExportReportPdf 函数体插 15 行注释（同类增长）："
        "U2 的 PDF 闸钉的是逐字锚点与计数，函数体变长不该把它变红",
        SCREEN,
        "    setPdfBusy('report');\n",
        "    setPdfBusy('report');\n"
        + "".join(f"      // 增长占位 {i}\n" for i in range(15)),
        PDF_TEST,
    ),
]


def jest(test_file: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["npx", "jest", "--silent", test_file],
        cwd=FRONT,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def patch(path: Path, old: str, new: str) -> None:
    src = path.read_text(encoding="utf-8")
    count = src.count(old)
    assert count == 1, f"{path.name}：锚点命中 {count} 次（要求恰好 1 次）\n{old[:80]!r}"
    path.write_text(src.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()

    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    if args.check:
        bad = 0
        for case in [*MUTATIONS, *GREEN]:
            path, old = case[2], case[3]
            n = path.read_text(encoding="utf-8").count(old)
            if n != 1:
                print(f"坏锚点 {case[0]}：{path.name} 命中 {n} 次")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}")
        return 1 if bad else 0

    tmp = Path(tempfile.mkdtemp(prefix="mut-frontend-"))
    originals: dict[Path, Path] = {}
    for case in [*MUTATIONS, *GREEN]:
        p = case[2]
        if p not in originals:
            bak = tmp / p.name
            shutil.copy2(p, bak)
            originals[p] = bak

    def restore() -> None:
        for p, bak in originals.items():
            shutil.copy2(bak, p)
            assert filecmp.cmp(bak, p, shallow=False), f"{p} 恢复后字节不一致"

    red_ok = red_fail = green_ok = green_fail = 0
    try:
        for cid, desc, path, old, new, test, marker in MUTATIONS:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                patch(path, old, new)
                code, out = jest(test)
                hit = (marker in out) and re.search(r"\d+ failed", out)
                if code != 0 and hit:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    print(f"[{cid}] 打红 ✗（exit={code}，marker={bool(hit)}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-8:]))
            finally:
                restore()
        for cid, desc, path, old, new, test in GREEN:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                patch(path, old, new)
                code, out = jest(test)
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 应保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    print(f"[{cid}] 应保持绿 ✗（exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-12:]))
            finally:
                restore()
    finally:
        restore()
        shutil.rmtree(tmp, ignore_errors=True)

    total = red_ok + red_fail + green_ok + green_fail
    print(
        f"\n变异自证合计：{total} 条｜红闸 {red_ok}/{red_ok + red_fail}"
        f"｜绿闸 {green_ok}/{green_ok + green_fail}"
    )
    print("源文件已全部按备份写回并逐字节校验一致。")
    return 0 if red_fail == 0 and green_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
