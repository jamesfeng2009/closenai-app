#!/usr/bin/env python3
"""批 2（D-G4…D-G10）九条新闸的变异自证（2026-10-17）。

批 2 的交付物形状不统一：真有接线的（D-G4/D-G6/D-G7/D-G8/D-G9①/D-G10）、
整块删除的（D-G9② discovery 封装、syncService）、**改判为有意零消费者**的
（D-G9③ push-token）、只登记不接线的（D-G5）。所以本脚本的靶也分四类：

- 打断真源 → 接线闸必须红（少传一个字段、硬编码一个档位、把跳转挪到落库前）；
- 复活死代码 → 删除闸必须红（把 getDailyDiscovery / resolveConflict 接回去）；
- 偷改登记形状 → 反向闸必须红（把契约行的「封装未接线」「—」抹掉、在 logout 里插 clearPushToken）；
- 跨过红线 → 展示副本闸必须红（让 isPro 去读 staleStatus）。

5 条 GREEN 证明这些闸钉的是语义不是行数：往函数体里塞 15 行注释、
在注释里讲历史（提到 trend.values / localhost:8000）都**不该**打红。

恢复一律 **copy2 + filecmp 逐字节校验**，不用 git checkout
（见 [[feedback-mutation-restore-no-git-checkout]]）；**严禁并发跑**
（见 [[feedback-mutation-gates-no-concurrency]]）。

用法：
    ./.venv/bin/python scripts/mutate_batch2_gates.py --check
    ./.venv/bin/python scripts/mutate_batch2_gates.py
    ./.venv/bin/python scripts/mutate_batch2_gates.py --only D-G4-M1,D-G10-M7
"""
from __future__ import annotations

import argparse
import filecmp
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FRONT = REPO / "frontend"
LOCALES = FRONT / "src" / "i18n" / "locales"
SRC = FRONT / "src"

SCHEMAS = REPO / "backend" / "models" / "schemas.py"
USER_SERVICE = REPO / "backend" / "services" / "user_service.py"
MOOD_SERVICE = REPO / "backend" / "services" / "mood_service.py"
R_LANDING = REPO / "site" / "public" / "r" / "index.html"
ENDPOINTS_MD = REPO / "contracts" / "endpoints.md"

API_TS = SRC / "services" / "api.ts"
TYPES_TS = SRC / "types" / "index.ts"
AUTH_TS = SRC / "context" / "AuthContext.tsx"
PUSH_TS = SRC / "services" / "push.ts"
APP_TSX = FRONT / "App.tsx"
MEMORIES_TSX = SRC / "screens" / "MemoriesScreen.tsx"
PROFILE_TSX = SRC / "screens" / "ProfileScreen.tsx"
REFLECT_TSX = SRC / "screens" / "ReflectScreen.tsx"
INSIGHTS_TSX = SRC / "screens" / "InsightsScreen.tsx"
PERSONALITY_TSX = SRC / "screens" / "PersonalityScreen.tsx"
PAYWALL_TSX = SRC / "screens" / "PaywallScreen.tsx"
HOME_TSX = SRC / "screens" / "HomeScreen.tsx"
MIGRATIONS_TS = SRC / "services" / "db" / "migrations.ts"
CACHE_TEST = FRONT / "__tests__" / "cacheRepositories.test.ts"

MEM_G = "__tests__/memoriesWiring.test.ts"
PUSHER_G = "__tests__/pushEnabledWiring.test.ts"
TAP_G = "__tests__/pushTapNavigate.test.ts"
NOTE_G = "__tests__/moodNoteWiring.test.ts"
ORPHAN_G = "__tests__/orphanEndpoints.test.ts"
IDLE_G = "__tests__/idleRepositoriesWiring.test.ts"
PY_PUSH = "backend/tests/test_push_enabled_setting.py"
PY_NOTE = "backend/tests/test_mood_checkin_note.py"
PY_SHARE = "backend/tests/test_share_deeplink_prereq.py"

# (id, 破坏说明, 文件, 原文, 坏文, 测试类型, 受害闸, 失败用例名)
MUTATIONS = [
    # ── D-G4 记忆面新增 + 详情/演变接线 ──────────────────────────────
    (
        "D-G4-M1",
        "createMemory 的 body 少送 category：界面选的分类根本到不了后端，"
        "后端按 factual 兜底 ⇒ 用户标了「价值观」库里记成「事实」",
        API_TS,
        "    body: JSON.stringify({ content: input.content, category: input.category }),",
        "    body: JSON.stringify({ content: input.content }),",
        "jest", MEM_G, "createMemory：POST /memories，只送 content＋category",
    ),
    (
        "D-G4-M2",
        "提交后乐观插一条、不回读：判重命中时后端压根没新建，界面那条是本地编的",
        MEMORIES_TSX,
        "      setDraft('');\n      await load();",
        "      setDraft('');\n      setMemories((prev) => [{ content, category: draftCategory } as any, ...prev]);",
        "jest", MEM_G, "提交后回读列表，绝不乐观插一条",
    ),
    (
        "D-G4-M3",
        "后端 category 的 Literal 多出第 6 档 mood：前后端类别集合漂移，"
        "前端 picker 少一档而后端照收",
        SCHEMAS,
        '    category: Literal["values", "preference", "relationship", "goal", "factual"] = Field(',
        '    category: Literal["values", "preference", "relationship", "goal", "factual", "mood"] = Field(',
        "jest", MEM_G, "前端 MEMORY_CATEGORIES 与 MemoryCreate.category 的 Literal 同集同序",
    ),
    (
        "D-G4-M4",
        "km 删掉 memories.checking 一行：判重中间态在高棉语界面渲染成空串",
        LOCALES / "km.ts",
        '    "checking": "កំពុងត្រួតពិនិត្យភាពស្ទួន…",\n', "",
        "jest", MEM_G, "km：12 个键都有值",
    ),
    # ── D-G6 推送总开关 UI ─────────────────────────────────────────
    (
        "D-G6-M1",
        "get_profile 撤掉 push_enabled 传值＝回到本批之前的「写得进读不出」",
        USER_SERVICE,
        "            push_enabled=bool(user.push_enabled),\n", "",
        "pytest", PY_PUSH, "test_flag_survives_a_fresh_profile_read",
    ),
    (
        "D-G6-M2",
        "读侧退化成 Optional：响应能给 null，开关的位置只能由前端猜",
        SCHEMAS,
        "    push_enabled: bool = False",
        "    push_enabled: Optional[bool] = False",
        "pytest", PY_PUSH, "test_read_side_is_a_plain_bool_not_optional",
    ),
    (
        "D-G6-M3",
        "写侧从三态退化成 bool：PATCH 不带这个键时会被当成「用户关掉了开关」",
        SCHEMAS,
        "    push_enabled: Optional[bool] = None  # 推送总开关",
        "    push_enabled: bool = False  # 推送总开关",
        "pytest", PY_PUSH, "test_write_side_stays_optional_tri_state",
    ),
    (
        "D-G6-M4",
        "开关文案硬编码成「已开启」：读路径整条断开而界面永远显示开着",
        PROFILE_TSX,
        "            {profile?.push_enabled ? t('profile.pushEnabledOn') : t('profile.pushEnabledOff')}",
        "            {t('profile.pushEnabledOn')}",
        "jest", PUSHER_G, "标签读的是 profile.push_enabled，不是本地 state",
    ),
    (
        "D-G6-M5",
        "handler 只改本地 state 不发 PATCH：看起来存上了，重进就回原样",
        PROFILE_TSX,
        "      const updated = await updateUserSettings({ push_enabled: next });\n      setProfile(updated);",
        "      setProfile({ ...(profile as any), push_enabled: next } as UserProfile);",
        "jest", PUSHER_G, "点开关走 PATCH /user/settings，且用响应刷新（不许只改本地 state 装作存上了）",
    ),
    (
        "D-G6-M6",
        "types 的 UserProfile 撤掉 push_enabled：读路径在类型面上又消失了",
        TYPES_TS,
        "  push_enabled?: boolean | null;\n", "",
        "jest", PUSHER_G, "UserProfile（读）与 UserSettings（写）都带 push_enabled",
    ),
    (
        "D-G6-M7",
        "zh 的 off 档填回「已开启」：同一门语言里开与关同字，界面看不出开关位置",
        LOCALES / "zh.ts",
        '    "pushEnabledOff": "已关闭",',
        '    "pushEnabledOff": "已开启",',
        "jest", PUSHER_G, "开与关两档的文案不同一门语言内不重合（重合＝界面上看不出位置）",
    ),
    # ── D-G7 点开通知跳转消息中心 ──────────────────────────────────
    (
        "D-G7-M1",
        "把「前台收到」也接到 onOpen：没点通知也会跳页，正是本批立项时的那种坏法",
        PUSH_TS,
        "      Notifications.addNotificationReceivedListener((n) =>\n        persistProactivePush(extractPushData(n)),\n      ),",
        "      Notifications.addNotificationReceivedListener((n) => onOpen(n)),",
        "jest", TAP_G, "前台收到（不是用户点击）：落库，但绝不回调跳转",
    ),
    (
        "D-G7-M2",
        "跳转挪到落库之前：导航失败或抛错就把已经要落库的消息弄丢了",
        PUSH_TS,
        "    const data = extractPushData(notification);\n    persistProactivePush(data);",
        "    const data = extractPushData(notification);\n    if (onTap && isLoggedIn() && isMessageCenterPush(data)) onTap(data);\n    persistProactivePush(data);",
        "jest", TAP_G, "跳转排在落库之后：导航失败不许把消息弄丢",
    ),
    (
        "D-G7-M3",
        "撤掉回调外层的 try/catch：navRef 未 ready 时异常会外泄到原生监听器",
        PUSH_TS,
        "    try {\n      onTap(data);\n    } catch {\n      // 静默：navRef 未 ready / 路由名不存在都不该影响已经落库的消息\n    }",
        "    onTap(data);",
        "jest", TAP_G, "onTap 抛错被吞掉：监听器不外泄异常，落库仍在",
    ),
    (
        "D-G7-M4",
        "路由名写成 'Message'（少一个 s）：navigate 抛错被静默吞掉，点了通知永远不跳",
        APP_TSX,
        "        navRef.navigate('Messages');",
        "        navRef.navigate('Message');",
        "jest", TAP_G, "跳转目标是已注册的那条栈路由",
    ),
    # ── D-G8 签到附一句话 ──────────────────────────────────────────
    (
        "D-G8-M1",
        "撤掉备注沉淀：打了字却不进记忆，「它记得你说过的那句」整条功能消失",
        MOOD_SERVICE,
        "        if note and note.strip():",
        "        if False and note and note.strip():",
        "pytest", PY_NOTE, "test_note_is_sedimented_as_a_memory",
    ),
    (
        "D-G8-M2",
        "后端 note 的 max_length 从 1000 改成 2000：前端抄的常量与实际放行长度脱钩",
        SCHEMAS,
        '    note: Optional[str] = Field(None, max_length=1000, description="可选文字备注")',
        '    note: Optional[str] = Field(None, max_length=2000, description="可选文字备注")',
        "pytest", PY_NOTE, "test_length_limit_is_the_one_the_frontend_copies",
    ),
    (
        "D-G8-M3",
        "把 1000 抄回 JSX：第二真源出现，后端改上限时界面会静默截错",
        REFLECT_TSX,
        "            maxLength={MOOD_NOTE_MAX_LENGTH}",
        "            maxLength={1000}",
        "jest", NOTE_G, "界面上用的是常量，不是把 1000 抄在 JSX 里",
    ),
    (
        "D-G8-M4",
        "本地行写 note: null：服务端响应里本来就没有 note 字段，离线一读用户那句话就没了",
        REFLECT_TSX,
        "          note: note ?? null,",
        "          note: null,",
        "jest", NOTE_G, "本地行写用户刚打的那句（服务端响应里没有 note 字段，回读不到）",
    ),
    (
        "D-G8-M5",
        "撤掉 trim：只有空格的输入会被当成一句备注写进库和记忆",
        REFLECT_TSX,
        "    const note = moodNote.trim() || undefined;",
        "    const note = moodNote || undefined;",
        "jest", NOTE_G, "trim 后为空传 undefined，而不是空串",
    ),
    (
        "D-G8-M6",
        "HomeScreen 的一键签到也开始传 note：两个入口对同一条端点给出两种口径",
        HOME_TSX,
        "      const res = await moodCheckin(emoji);",
        "      const res = await moodCheckin(emoji, '占位备注');",
        "jest", NOTE_G, "HomeScreen 保持一键签到：不传 note、没有 moodNote 输入框",
    ),
    # ── D-G9 三条孤儿（接线 / 删封装 / 改判） ───────────────────────
    (
        "D-G9-M1",
        "撤掉趋势摘要的渲染：接线只接了一半，请求发了但用户永远看不到那句话",
        INSIGHTS_TSX,
        "            {!!trend?.summary && (\n              <Text style={styles.trendSummary}>{trend.summary}</Text>\n            )}",
        "",
        "jest", ORPHAN_G, "InsightsScreen 首屏并行取趋势（7 天窗、失败降级为 null）并渲染摘要一行",
    ),
    (
        "D-G9-M2",
        "折线数据源换成 trend.values：同一屏出现第二个「情绪值」真源，断线口径也会跟着变",
        INSIGHTS_TSX,
        "            <MoodLineChart days={recentDays(timeline.days, 7)} width={340} height={56} />",
        "            <MoodLineChart days={trend?.values.length ? 7 : 0} width={340} height={56} />",
        "jest", ORPHAN_G, "折线数据源仍是 /growth/timeline 的 days，没被 trend 替换",
    ),
    (
        "D-G9-M3",
        "封装的查询串参数从 days 改成 window：后端读不到 days，永远按默认窗回",
        API_TS,
        "  return request<MoodTrendData>(`/mood/trend?days=${days}`);",
        "  return request<MoodTrendData>(`/mood/trend?window=${days}`);",
        "jest", ORPHAN_G, "api.ts 的封装打的是带 days 的查询串",
    ),
    (
        "D-G9-M4",
        "MoodTrendResponse.summary 退化成 Optional：前端类型与后端脱钩，摘要一行变成猜的",
        SCHEMAS,
        "    summary: str       # 趋势总结",
        "    summary: Optional[str] = None  # 趋势总结",
        "jest", ORPHAN_G, "前端类型三键与后端 MoodTrendResponse 一致（summary 不可选）",
    ),
    (
        "D-G9-M5",
        "在 logout 里插回 clearPushToken：把「登出保留推送 token」这条产品口径取消掉",
        AUTH_TS,
        "    if (rt) {\n      try {\n        await logoutApi(rt);",
        "    await clearPushToken().catch(() => {});\n    if (rt) {\n      try {\n        await logoutApi(rt);",
        "jest", ORPHAN_G, "logout 块里没有 clearPushToken，且写明了这是有意不接",
    ),
    (
        "D-G9-M6",
        "把 getDailyDiscovery 封装接回 api.ts：本批删掉的死代码又长回来（后端仍是 deprecated）",
        API_TS,
        "export async function getMoodTrend(days: number = 14): Promise<MoodTrendData> {",
        "export async function getDailyDiscovery(): Promise<{ text: string }> {\n  return request<{ text: string }>('/discovery/daily');\n}\n\nexport async function getMoodTrend(days: number = 14): Promise<MoodTrendData> {",
        "jest", ORPHAN_G, "api.ts 里 getDailyDiscovery 与 DailyDiscovery 类型引用已消失",
    ),
    (
        "D-G9-M7",
        "types 里把 DailyDiscovery 接口加回来：删封装删一半，类型面还留着占位",
        TYPES_TS,
        "export interface MoodTrendData {",
        "export interface DailyDiscovery {\n  text: string;\n}\n\nexport interface MoodTrendData {",
        "jest", ORPHAN_G, "types 里不再导出 DailyDiscovery 接口",
    ),
    (
        "D-G9-M8",
        "契约行把 discovery 的前端列从「—」写成有封装：登记形状说谎，读者会以为已接线",
        ENDPOINTS_MD,
        "| 36 | GET | `/api/v1/discovery/daily` | JSON 信封 | auth | — |",
        "| 36 | GET | `/api/v1/discovery/daily` | JSON 信封 | auth | frontend/src/services/api.ts:1 |",
        "jest", ORPHAN_G, "契约：该行的前端列退回「—」，但这条端点本身仍登记在册",
    ),
    (
        "D-G9-M9",
        "契约行抹掉 push-token 那格的「封装未接线」：改判的登记形状被抹平，"
        "看起来像已接线而实际零消费者",
        ENDPOINTS_MD,
        "frontend/src/services/api.ts:584（封装未接线）",
        "frontend/src/services/api.ts:584",
        "jest", ORPHAN_G, "契约：该行仍带「封装未接线」——这正是本改判的登记形状",
    ),
    # ── D-G5 分享深链：只登记红线，不接线 ──────────────────────────
    (
        "D-G5-M1",
        "把 API 域写死进静态落地页：一条不可配置的地址被发到生产",
        R_LANDING,
        "  var SITE_HOME = 'https://closenai.app/';",
        "  var SITE_HOME = 'https://closenai.app/';\n  var API_BASE = 'http://localhost:8000/api/v1';",
        "pytest", PY_SHARE, "test_referral_landing_page_hardcodes_no_api_domain",
    ),
    (
        "D-G5-M2",
        "落地页开始发 fetch 但默认 CORS 白名单没有站点域：线上必被 preflight 拦",
        R_LANDING,
        "  var SITE_HOME = 'https://closenai.app/';",
        "  var SITE_HOME = 'https://closenai.app/';\n  fetch('/api/v1/share/resolve/ABC12345').catch(function () {});",
        "pytest", PY_SHARE, "test_wiring_the_landing_page_requires_a_cors_origin_first",
    ),
    (
        "D-G5-M3",
        "前端长出 resolve 的消费者：改判（在册、零消费者）被偷偷改成接线",
        API_TS,
        "export async function getMoodTrend(days: number = 14): Promise<MoodTrendData> {",
        "export async function resolveShareCode(code: string): Promise<{ ok: boolean }> {\n  return request<{ ok: boolean }>(`/share/resolve/${code}`);\n}\n\nexport async function getMoodTrend(days: number = 14): Promise<MoodTrendData> {",
        "pytest", PY_SHARE, "test_resolve_endpoint_is_zero_consumer_and_still_registered",
    ),
    # ── D-G10 四个空转本地仓储 ─────────────────────────────────────
    (
        "D-G10-M1",
        "撤掉首屏 combo 读缓存：离线/游客进「人格」页只有一张空卡，报告正文没有任何本地来源",
        PERSONALITY_TSX,
        "      comboReportRepo.getLatest().catch(() => null),\n", "",
        "jest", IDLE_G, "首屏读缓存在登录守卫之前（游客/离线也有正文可读）",
    ),
    (
        "D-G10-M2",
        "生成成功后不写缓存：仓储仍然空转，下次进屏又是空的",
        PERSONALITY_TSX,
        "      comboReportRepo.cache(comboStateToRow(report)).catch(() => {});\n", "",
        "jest", IDLE_G, "生成成功后才写缓存，且写失败不顶掉已渲染的报告",
    ),
    (
        "D-G10-M3",
        "撤掉订阅状态的写副本：读副本永远读不到东西，Tier 3 离线展示退回空白",
        PAYWALL_TSX,
        "      subscriptionStatusRepo.cache(statusToRow(s)).catch(() => {});\n", "",
        "jest", IDLE_G, "成功写副本、失败读副本（读的那条在 catch 里）",
    ),
    (
        "D-G10-M4",
        "让 isPro 去读展示副本：把「离线副本能解锁付费功能」这条红线踩穿",
        PAYWALL_TSX,
        "  const isPro = status?.is_active && status.tier !== 'free';",
        "  const isPro = (status?.is_active || !!staleStatus) && (status?.tier || staleStatus?.plan) !== 'free';",
        "jest", IDLE_G, "四条权益判断逐行只看服务端 status，没有 stale 旁路",
    ),
    (
        "D-G10-M5",
        "展示卡去掉「非实时」标注：用户会把上次看到的付费状态当成现在的",
        PAYWALL_TSX,
        "          <Text style={styles.staleNote}>{t('paywall.staleStatusNote')}</Text>\n", "",
        "jest", IDLE_G, "展示卡只在服务端没数据时出现，且自带「非实时」标注",
    ),
    (
        "D-G10-M6",
        "把 syncService 的 resolveConflict 接回 HomeScreen：整块删除的合并器又被引用，"
        "而模块文件已经不在了",
        HOME_TSX,
        "const HEAT_DAYS = 14;",
        "const HEAT_DAYS = 14;\nconst MERGE_LWW = resolveConflict;",
        "jest", IDLE_G, "全 src 无人引用这两个模块的名字",
    ),
    (
        "D-G10-M7",
        "迁移里把 daily_discovery 建表语句改名：删的是代码，表却被顺手删掉（数据动作不在本批）",
        MIGRATIONS_TS,
        "    CREATE TABLE IF NOT EXISTS daily_discovery (",
        "    CREATE TABLE IF NOT EXISTS daily_archive (",
        "jest", IDLE_G, "三张表仍在迁移里（删表是数据动作，不在本批）",
    ),
    (
        "D-G10-M8",
        "de 删掉 staleStatusNote 一行：德语界面那张展示卡没有标注文案",
        LOCALES / "de.ts",
        '    "staleStatusNote": "Zuletzt angezeigter Status (nicht aktuell)",\n', "",
        "jest", IDLE_G, "de 有 paywall.staleStatusNote 且非空",
    ),
]

# 破坏「不该打红」的东西＝证明这些闸钉的是语义不是行数/注释。
GREEN = [
    (
        "D-G4-G1",
        "往 onComboReport 函数体插 15 行注释：接线闸钉的是逐字锚点，函数体变长不该算破坏",
        PERSONALITY_TSX,
        "      comboReportRepo.cache(comboStateToRow(report)).catch(() => {});\n",
        "      comboReportRepo.cache(comboStateToRow(report)).catch(() => {});\n"
        + "".join(f"      // 增长占位 {i}\n" for i in range(15)),
        "jest", IDLE_G,
    ),
    (
        "D-G8-G2",
        "往 quickMoodCheckin 插 15 行注释：行为闸判的是请求体与本地行，源码行数不是判据",
        REFLECT_TSX,
        "    const note = moodNote.trim() || undefined;\n",
        "    const note = moodNote.trim() || undefined;\n"
        + "".join(f"      // 增长占位 {i}\n" for i in range(15)),
        "jest", NOTE_G,
    ),
    (
        "D-G9-G3",
        "在 InsightsScreen 的块注释里提到 trend.values：解释「为什么不换数据源」的历史"
        "本来就该写在那儿，判据只看代码面",
        INSIGHTS_TSX,
        "                放在模糊门之内：免费/Pro 的分界不因这条而变。 */}",
        "                放在模糊门之内：免费/Pro 的分界不因这条而变；也绝不读 trend.values。 */}",
        "jest", ORPHAN_G,
    ),
    (
        "D-G5-G4",
        "在落地页注释里写 http://localhost:8000/api/v1：闸禁的是代码里的常量，"
        "不是解释历史的那句注释",
        R_LANDING,
        "  // 上架后的收口动作：把本文件的 href 与文案一并交给构建期注入，见 tasks.md K4。",
        "  // 上架后的收口动作：把本文件的 href 与文案一并交给构建期注入，见 tasks.md K4。\n"
        "  // 历史备注：DEV 直连 http://localhost:8000/api/v1 只存在于 App 侧构建变量。",
        "pytest", PY_SHARE,
    ),
    (
        "D-G10-G5",
        "给 staleStatus 那几行加 15 行注释：展示副本的红线判的是四个权益表达式本身",
        PAYWALL_TSX,
        "  const isPro = status?.is_active && status.tier !== 'free';",
        "".join(f"  // 增长占位 {i}\n" for i in range(15))
        + "  const isPro = status?.is_active && status.tier !== 'free';",
        "jest", IDLE_G,
    ),
]


def jest(test_file: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["npx", "jest", "--silent", test_file],
        cwd=FRONT, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode, proc.stdout + proc.stderr


def pytest(node: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(REPO / ".venv/bin/python"), "-m", "pytest", node, "-q", "--no-header"],
        cwd=REPO, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode, proc.stdout + proc.stderr


def run(kind: str, target: str) -> tuple[int, str]:
    return jest(target) if kind == "jest" else pytest(target)


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
                print(f"坏锚点 {case[0]}：{path.name} 命中 {n} 次 :: {old[:60]!r}")
                bad += 1
        total = len(MUTATIONS) + len(GREEN)
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}（共 {total} 条）")
        return 1 if bad else 0

    tmp = Path(tempfile.mkdtemp(prefix="batch2-gate-backup-"))
    originals: dict[Path, Path] = {}
    for case in [*MUTATIONS, *GREEN]:
        p = case[2]
        if p not in originals:
            bak = tmp / str(p.relative_to(REPO)).replace("/", "__")
            assert not bak.exists(), f"备份名撞车：{bak}"
            shutil.copy2(p, bak)
            originals[p] = bak

    def restore() -> None:
        for p, bak in originals.items():
            shutil.copy2(bak, p)
            assert filecmp.cmp(bak, p, shallow=False), f"{p} 恢复后字节不一致"

    red_ok = red_fail = green_ok = green_fail = 0
    failures: list[str] = []
    try:
        for cid, desc, path, old, new, kind, test, marker in MUTATIONS:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                patch(path, old, new)
                code, out = run(kind, test)
                hit = marker in out and re.search(r"\d+ (failed|error)", out)
                if code != 0 and hit:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（exit={code}，marker={bool(hit)}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-10:]))
            finally:
                restore()
        for cid, desc, path, old, new, kind, test in GREEN:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                patch(path, old, new)
                code, out = run(kind, test)
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 应保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 应保持绿 ✗（exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-14:]))
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
    if failures:
        print(f"未如预期：{', '.join(failures)}")
    print("源文件已全部按备份写回并逐字节校验一致。")
    return 0 if red_fail == 0 and green_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
