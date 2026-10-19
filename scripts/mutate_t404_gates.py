#!/usr/bin/env python3
"""T4-04（前端：洞察 → 行动 → 结果闭环）的变异自证（2026-10-20）。

靶文件按类八处（api.ts／types／两个屏／locales 的 index＋15 本文案／契约表／BFF 装饰器），
红闸 46 条、绿闸 5 条。闸是 `frontend/__tests__/actionsWiring.test.ts`（65 条判据）。

**每条红闸都必须把「它自己那一条判据」打红**（[[feedback-mutation-anchor-must-reach-target]]）：
marker 记的是 `describe › it` 的全名，jest 输出里出现的必须是那一行的 `●`，
而不是「整套红了就算」——红了别的用例只算附带，红点没落到点名那一格就是失败。

按八组判据分：

- **①（A1…A5）出口与台账行**：路径字面量写坏（A1）、契约的「前端调用」列退回 `—`（A2）、
  表里多出一行同 path 的记录（A3，钉的是「过滤器不是扫空集」那条长度证人）、
  服务端装饰器改名（A4，屏上发的每一条都有人接）、列表查询长出 `cursor`（A5，
  Go 那面只有 `truncated` 没有游标——T4-02 定的口径）。
- **②（B1…B5）请求体逐字＝Go**：少一格、多一格、键名 camel 化、发状态词而不是动词，
  以及最坏的一条——身份从 body/headers 进来（B5，ADR-002）。
  B5 打在 `listActions` 上是**故意挑的**：那一条封装没有 body 键集判据，
  所以它的红点只可能落在「六条都不带身份」那一格，不会和②的其他判据混。
- **③（C1…C4）响应类型逐字＝Go**：`goalId`/`modelVersion` 这类改名在 TS 侧编译全过、
  只有屏上空白会告诉你；C4 把可空时间写成 `string`（「没设截止」会渲染成一个空日期）。
  C4 挑 `target_at` 而不是 `goal_id` 是因为键集闸只看键名不看类型——
  换 `goal_id` 会连 C2 一起红，换 `target_at` 只红「可空」那一格。
- **④（D1…D6）词表指回 Go**：动词少一格、屏上发 `skipped`（状态词）、四选一里冒出
  `mixed`、`outcome_type` 漂成 `goal_progress`、上限一格变两处、前端自己按 status 筛。
- **⑤（E1…E7）分区、三态、截断**：分区状态词写坏、行动面三态与记录面共用 flag、
  **整屏早退回来**（E3——这是本批实施过程中真犯过的错，见台账 T4-04 结案行）、
  两个 `truncated` 各消费一次（E4/E5 分钉两侧）、en 那句文案不再说「更早的」（E6）、
  空态的三段条件少一段（E7：加载失败时也会讲「你一条行动都没有」）。
- **⑥（F1…F6）行内语义**：先开弹层再重取（F1）、幂等两句话合成一句（F2）、
  防抖少一枚（F3）、点档位即提交（F4）、写失败还关弹层（F5）、
  `operation_id` 变成固定串（F6：重试不再是「新的一次尝试」）。
- **⑦（G1…G8）洞察入口与两步写**：预填用 `slice`（切坏 emoji）、空标题也发请求、
  目标建成却不留住 id（重试建第二颗）、本地键 `NEW_GOAL` 进请求体、
  截止日取 UTC 零点、失败弹服务端双语串、候选拉不到就整块坏掉、CTA 丢无障碍名。
- **⑧（H1…H5）15 本 locale**：单本少一键（H1）、**15 本同时少同一格**（H2）、
  占位符长到别的格（H3）、屏上用了没登记的键（H4）、语言表少一门（H5，
  长度证人倒下的那条路）。
  H2 是 `i18nLocales.test.ts` 那道全表键集闸放不过的形状——那一闸比的是「两侧一致」，
  15 份一起少同一格它照样绿；这里靠 en 自己的 29 格计数 +「屏上用的键必须在册」兜住。
  实测：H2 变异态下 `i18nLocales`（107 条）确实仍全绿，见结案行的取证记录。

**刻意不纳入的两条等价变异**（留档以免下轮重猜）：
① 把 `setCreatedGoalId(goalId)` 整行删掉——`indexOf` 在缺席时回 -1，
   「goalId 早于 null」那条次序判据反而**恒真**，红点落不到该落的判据上。
   同一条缺陷由 G3（ternary 不再复用 createdGoalId）打红，所以不补反向判据；
   要补的是闸本身（记在 T4-04 结案行的欠账里）。
② `onPress={submitResult}` 从 1 枚改成 2 枚——那条判据钉的是「只长在最后一枚按钮上」，
   两枚都提交与一枚都不提交是两个形状，前者由 F4 的 not.toMatch 侧打红，
   改枚数只是把同一句谎换个说法。

纪律：串行执行（[[feedback-mutation-gates-no-concurrency]]）、copy2 备份 +
`filecmp(shallow=False)` 逐字节写回、不用 git checkout
（[[feedback-mutation-restore-no-git-checkout]]）；每条红闸先跑基线，基线本来就不绿、
`npx` 不在 PATH 一律记「未跑」不算绿；jest 的 `Test suite failed to run`
（编译期坏掉）不算打红——那红的是工具链不是判据。

用法：
    ./.venv/bin/python scripts/mutate_t404_gates.py --check
    ./.venv/bin/python scripts/mutate_t404_gates.py
    ./.venv/bin/python scripts/mutate_t404_gates.py --only A1,F4,H2
"""
from __future__ import annotations

import argparse
import ast
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
VENV_PY = REPO / ".venv" / "bin" / "python"

API = FRONT / "src" / "services" / "api.ts"
TYPES = FRONT / "src" / "types" / "index.ts"
GROWTH = FRONT / "src" / "screens" / "GrowthRecordsScreen.tsx"
INSIGHTS = FRONT / "src" / "screens" / "InsightsScreen.tsx"
LOC_INDEX = LOCALES / "index.ts"
ENDPOINTS_MD = REPO / "contracts" / "endpoints.md"
ROUTER_PY = REPO / "backend" / "routers" / "v3.py"

SUITE = "__tests__/actionsWiring.test.ts"
G1_ = "① 六条出口逐字回台账行"
G2_ = "② 请求体键名逐字等于 Go 的 v3*Request"
G3_ = "③ 响应类型逐字等于 Go 的 v3*Wire／v3*Payload"
G4_ = "④ 词表全部指回 Go 的 domain，屏上不重述合法性"
G5_ = "⑤ 三个分区、各一套三态、截断不藏"
G6_ = "⑥ 完成／跳过／记结果：幂等两句话与防抖"
G7_ = "⑦ 洞察 → 行动入口：预填、两步写与不重复建目标"
G8_ = "⑧ actions.* 在 15 个 locale 里键集齐"

LINE = "\n"
LANGS = ["en", "zh", "es", "ko", "ja", "ru", "id", "ms", "th", "de", "fr", "it", "pt", "vi", "km"]


def truncated_line(lang: str) -> str:
    """取某一本 locale 里 actions 段的 `truncated` 整行（含结尾换行）。

    各本该键的行号不同（en 684、km 675…），所以锚点按文件现取而不是写死；
    「恰好一行」由 --check 的命中数断言兜住。
    """
    text = (LOCALES / f"{lang}.ts").read_text(encoding="utf-8")
    hits = re.findall(r'^ {4}"truncated": .*\n', text, re.M)
    assert len(hits) == 1, f"{lang} 的 truncated 行数 {len(hits)}（要 1）"
    return hits[0]


TRUNC = {lng: truncated_line(lng) for lng in LANGS}

# ── 锚点（逐字取自靶文件，命中数必须恰好 1）────────────────────────────────
LIST_GOALS_CALL = "return request<GoalListPage>('/goals' + listTail(query));"
LIST_ACTIONS_CALL = "return request<ActionListPage>('/actions' + listTail(query));"
LIST_QUERY_IFACE = "export interface CommitmentListQuery {\n  status?: string;\n  limit?: number;\n}"
GOAL_BODY = "body: JSON.stringify({ title, description, target_at: targetAt }),"
ACTION_BODY = "body: JSON.stringify({ title, description, due_at: dueAt }),"
TRANSITION_BODY = "{ method: 'PATCH', body: JSON.stringify({ action }) },"
OCCURRED_LINE = "        occurred_at: occurredAt,"
OUTCOME_TYPE_LINE = "        outcome_type: 'commitment_completed',"
ACTION_PATH_LINE = (
    "  return request<ActionOutcomeReceipt>(\n"
    "    `/actions/${encodeURIComponent(actionId)}/outcomes`,"
)
GOAL_RECEIPT_HEAD = (
    "export interface GoalReceipt {\n"
    "  goal_id: string;\n"
    "  status: string;\n"
    "  changed: boolean;\n"
    "  event_id: string;\n"
    "  model_version: number;"
)
ACTION_GOAL_ID = (
    "  /** null ⇒ 这一颗没挂在目标下；空串是「挂在一颗标题为空的目标下」，两件事。 */\n"
    "  goal_id: string | null;"
)
TARGET_AT_NULL = "  target_at: string | null;"
COMPLETED_TARGET = "  completed_target: string;"
VERBS_TS = "export type ActionTransition = 'complete' | 'skip' | 'cancel';"
SKIP_CALL = "await transitionAction(item.id, 'skip');"
RESULT_CHOICES = "const RESULT_CHOICES: ActionResultChoice[] = ['better', 'same', 'worse', 'unknown'];"
TITLE_MAX = "const ACTION_TITLE_MAX = 200;"
LIST_ACTIONS_SCREEN = "      const page = await listActions();"
BUCKET_ACTIVE = "  { key: 'sectionActive', status: 'active' },"
ACTIONS_FAILED_STATE = "  const [actionsFailed, setActionsFailed] = useState(false);"
ACTION_BUCKETS_HEAD = "  const actionBuckets = useMemo(() => ACTION_BUCKETS.map((b) => ({"
SET_ACTIONS_TRUNC = "      setActionsTruncated(page.truncated);"
SET_GOALS_TRUNC = "      setActionGoalsTruncated(page.truncated);"
EMPTY_STATE = (
    "        {!actionsLoading && !actionsFailed && (actions ?? []).length === 0 && (\n"
    "          <Text style={styles.emptyText}>{t('actions.empty')}</Text>"
)
MARK_DONE_ORDER = (
    "      await loadActions();\n"
    "      setResultChoice(null);\n"
    "      setResultNote('');\n"
    "      setResultFor(item);"
)
CHANGED_ALERT = "      Alert.alert(receipt.changed ? t('actions.resultSaved') : t('actions.resultSameDay'));"
MARK_SKIP_HEAD = "  const markSkip = async (item: ActionItem) => {\n    if (busyActionId) return;\n"
CHOICE_ON_PRESS = "                  onPress={() => setResultChoice(choice)}"
RESULT_TAIL = (
    "      Alert.alert(t('common.error'), t('actions.failed'));\n"
    "    } finally {\n"
    "      setResultBusy(false);"
)
TRACE_CALL = "        resultFor.id, resultChoice, resultNote.trim(), null, newTraceId(),"
PREFILL = "    const prefill = truncateRunes(item.statement, ACTION_TITLE_MAX);"
SUBMIT_GUARD = "    if (!actionFor || !title || actionBusy) return;"
GOALID_REUSE = "      let goalId = actionGoalId === NEW_GOAL ? createdGoalId : actionGoalId;"
CREATE_ACTION_CALL = "      await createAction(goalId, title, '', dueAt);"
DUE_PARSE = "      const parsed = new Date(`${due}T23:59:00`);"
INSIGHTS_CATCH = (
    "    } catch {\n"
    "      // 不弹 e.message（服务端给运维的双语串），也不假装成功：这一句要说清「什么都没加」。\n"
    "      Alert.alert(t('common.error'), t('actions.failed'));"
)
RELOAD_CATCH = (
    "      setActionGoalsFailed(true);\n"
    "      setActionGoalsTruncated(false);\n"
    "      setActionGoalId(NEW_GOAL);"
)
CTA_A11Y = "                  accessibilityLabel={t('actions.cta')}\n"
EN_CTA = '    "cta": "Try a small action",'
SKIP_SAVED_KEY = "t('actions.skipSaved')"
TH_RESOURCE = "  th: { translation: th },"
ROW_CREATE_ACTION = (
    "| 45 | POST | `/api/v1/goals/{goal_id}/actions` | JSON 信封 | auth | "
    "frontend/src/services/api.ts:617 | 200+422 | - | goal_id(path) | "
    "`backend/routers/v3.py:110` `create_action` |"
)
ROW_LIST_GOALS = (
    "| 42 | GET | `/api/v1/goals` | JSON 信封 | auth | frontend/src/services/api.ts:581 | "
    "200+422 | - | status(query), limit(query) | `backend/routers/v3.py:83` `list_goals` |"
)
BFF_GET_ACTIONS = '@router.get("/actions")'

DECOR = "  // "
PY_COMMENTS = "".join(f"{DECOR}T4-04 绿闸占位 {i}{LINE}" for i in range(15))

MUTATIONS: list[tuple] = [
    # ── ① 出口逐字回台账行 ──
    (
        "A1", "listGoals 的路径字面量写成 `/goal` ⇒ 契约扫描器认不到调用点、台账那一行的行号"
              "不再指着这条 path：屏上「目标候选」整块退成新建，而契约表仍说自己已接线",
        [(API, LIST_GOALS_CALL, LIST_GOALS_CALL.replace("'/goals'", "'/goal'"))],
        G1_, "listGoals：封装在册、台账有对应行、出处列那一行真指着这条路径",
    ),
    (
        "A2", "台账那一行的「前端调用」列退回 `—` ⇒ 契约说这条端点没人用，"
              "而屏上确实在发：接线状态在表里丢了，下一轮按表决定下线它就会把在用的一面切掉",
        [(ENDPOINTS_MD, ROW_CREATE_ACTION, ROW_CREATE_ACTION.replace(
            "frontend/src/services/api.ts:617", "—"))],
        G1_, "createAction：封装在册、台账有对应行、出处列那一行真指着这条路径",
    ),
    (
        "A3", "台账里多出一行同 path 的记录（编号 999）⇒ 「恰好七条」那条长度证人倒下："
              "表与实现开始各说一套，而 it.each 用 find() 取第一条，单条判据看不出重复",
        [(ENDPOINTS_MD, ROW_LIST_GOALS,
          ROW_LIST_GOALS + LINE + ROW_LIST_GOALS.replace("| 42 |", "| 999 |"))],
        G1_, "台账过滤器不是扫空集：/goals 与 /actions 面恰好七条（含屏上不接的那条目标 PATCH）",
    ),
    (
        "A4", "BFF 的 `GET /actions` 装饰器改名 ⇒ 屏上发的那一条在服务端没人接："
              "契约表与封装都还说自己已接线，只有「每条出口都有人接」这一格会红",
        [(ROUTER_PY, BFF_GET_ACTIONS, '@router.get("/action-list")')],
        G1_, "BFF 那六条转发装饰器在册（屏上发的每一条都有人在服务端接）",
    ),
    (
        "A5", "列表查询长出 `cursor` ⇒ Go 那面根本没有游标（只有 truncated）："
              "前端以为自己分页了，实际上每一页拿回的是同一批最近 50 条",
        [(API, LIST_QUERY_IFACE,
          LIST_QUERY_IFACE.replace("  limit?: number;", "  limit?: number;\n  cursor?: string;"))],
        G1_, "列表查询只有 status/limit 两格：这一面没有游标（Go 用 truncated 表达还有更多）",
    ),
    # ── ② 请求体键名 ──
    (
        "B1", "createGoal 的 body 少 `description` ⇒ Go 用 `*string` 之外按必填收的一格变成空："
              "目标建出来了但描述永远空白，且 400 的说法是中英双语串",
        [(API, GOAL_BODY, "body: JSON.stringify({ title, target_at: targetAt }),")],
        G2_, "createGoal 的三格逐字＝v3GoalCreateRequest",
    ),
    (
        "B2", "createAction 的 body 长出 `goal_id` ⇒ path 与 body 各给一个目标："
              "两处不同值时服务端 400，而屏上只会说「什么都没加」",
        [(API, ACTION_BODY,
          "body: JSON.stringify({ title, description, due_at: dueAt, goal_id: goalId }),")],
        G2_, "createAction 的三格逐字＝v3ActionCreateRequest（body 里没有 goal_id：目标由 path 给）",
    ),
    (
        "B3", "生命周期那一跳发的是 `status` 而不是 `action` ⇒ Go 的 v3TransitionRequest "
              "没这个键：状态转移变成空请求，屏上按「已保存」渲染",
        [(API, TRANSITION_BODY, "{ method: 'PATCH', body: JSON.stringify({ status: action }) },")],
        G2_, "transitionAction 只带动词一个键＝v3TransitionRequest",
    ),
    (
        "B4", "结果的 `occurred_at` 在 body 里 camel 化 ⇒ Go 收不到那一格（缺省成服务端此刻），"
              "跨时区补记会算错「哪一天」，而同日幂等正是按天判的",
        [(API, OCCURRED_LINE, "        occurredAt,")],
        G2_, "postActionOutcome 的五格逐字＝v3OutcomeRequest",
    ),
    (
        "B5", "listActions 自己拼 `X-User-Id` 头 ⇒ ADR-002「身份只从令牌解出」在封装层失守："
              "前端可以替别人声明身份，而这一格本来由服务端注入",
        [(API, LIST_ACTIONS_CALL,
          "return request<ActionListPage>('/actions' + listTail(query), "
          "{ headers: { 'X-User-Id': 'someone-else' } });")],
        G2_, "六条封装一个都不带身份：身份由服务端从令牌解出，前端无从替用户声明「这是谁」",
    ),
    # ── ③ 响应类型 ──
    (
        "C1", "GoalReceipt 的 `model_version` 改成 camel ⇒ TS 编译全过、只有屏上那格空白："
              "回执里的模型版本读不到了",
        [(TYPES, GOAL_RECEIPT_HEAD, GOAL_RECEIPT_HEAD.replace("model_version", "modelVersion"))],
        G3_, "GoalReceipt ↔ go-core/internal/httpapi/v3_goals.go（Go 的 v3GoalWritePayload）：键集与顺序都逐字对齐",
    ),
    (
        "C2", "ActionItem 的 `goal_id` 改成 `goalId` ⇒ 行动与目标的关联在屏上消失："
              "键名不匹配到 Go 的 wire，行还是那些行，只是每颗都成「没挂目标」",
        [(TYPES, ACTION_GOAL_ID,
          ACTION_GOAL_ID.replace("goal_id: string | null;", "goalId?: string | null;"))],
        G3_, "ActionItem ↔ go-core/internal/httpapi/v3_goals.go（Go 的 v3ActionItemWire）：键集与顺序都逐字对齐",
    ),
    (
        "C3", "结果回执少 `completed_target` ⇒ 屏上没法说「这一条把哪颗目标推完了」："
              "Go 回的那一格被 TS 类型漏掉，读的人以为服务端没给",
        [(TYPES, COMPLETED_TARGET, "")],
        G3_, "ActionOutcomeReceipt ↔ go-core/internal/httpapi/v3_outcomes.go（Go 的 v3OutcomePayload）：键集与顺序都逐字对齐",
    ),
    (
        "C4", "可空时间写成 `string`（Go 是 `*time.Time`）⇒ 「没设截止」在屏上渲染成一个空日期，"
              "而不是不显示；键名没变，所以只有可空判据能抓到它",
        [(TYPES, TARGET_AT_NULL, "  target_at: string;")],
        G3_, "可空时间在 TS 侧是 string | null 而不是 string（Go 是 *time.Time，线上就是 null）",
    ),
    # ── ④ 词表 ──
    (
        "D1", "TS 的动词联合少 `cancel` ⇒ 前端自己数一套词表：Go 的 lifecycleVerbs 有三格，"
              "类型只剩两格，将来接取消入口时先在类型上撞墙",
        [(TYPES, VERBS_TS, "export type ActionTransition = 'complete' | 'skip';")],
        G4_, "三个生命周期动词逐字＝Go 的 lifecycleVerbs",
    ),
    (
        "D2", "屏上把动词发成状态词 `skipped` ⇒ Go 的 v3TransitionRequest 只认动词："
              "点「跳过」回 400，用户看到的是「没存上」",
        [(GROWTH, SKIP_CALL, "await transitionAction(item.id, 'skipped');")],
        G4_, "屏上只发 complete 与 skip：cancel 在这一面没有入口，也不自己造第四枚按钮",
    ),
    (
        "D3", "四选一里混进 `mixed` ⇒ 工单 §怎么做.3 那句「四句答案」变成五句，"
              "而这一屏的问题设计里没给「有好有坏」留位置",
        [(GROWTH, RESULT_CHOICES,
          "const RESULT_CHOICES: ActionResultChoice[] = ['better', 'same', 'worse', 'mixed', 'unknown'];")],
        G4_, "四选一逐字取 Go 六值里的四格，且 mixed/not_relevant 确实不在屏上",
    ),
    (
        "D4", "outcome_type 漂成 `goal_progress` ⇒ Go 的 OutcomeTargetRequirement 当场 400："
              "「挂在行动上的目标结果」是这一面唯一写不出的一类，而屏上仍显示「保存失败」",
        [(API, OUTCOME_TYPE_LINE, "        outcome_type: 'goal_progress',")],
        G4_, "outcome_type 只有一个字面量且住在 api.ts：屏上无从漂进九类词表",
    ),
    (
        "D5", "屏上的标题上限改成 500 ⇒ Go 的 maxGoalTitle 仍是 200："
              "第 201 字起被服务端拒，用户写完才知道超限（一格变两处规则）",
        [(INSIGHTS, TITLE_MAX, "const ACTION_TITLE_MAX = 500;")],
        G4_, "标题上限一格不两处：屏上的 maxLength 与 Go 的 maxGoalTitle 同值",
    ),
    (
        "D6", "前端自己按 `status=active` 取行动列表 ⇒ 分区变成三次服务端调用的拼装，"
              "而 `truncated` 的口径（最近 50 条）也跟着变：completed 那格里看不到历史",
        [(GROWTH, LIST_ACTIONS_SCREEN, "      const page = await listActions({ status: 'active' });")],
        G4_, "屏上不发带 status 的行动列表，也不自己判日期合法性（服务端词表是唯一判据）",
    ),
    # ── ⑤ 分区、三态、截断 ──
    (
        "E1", "分区状态词写成 `pending` ⇒ Go 的 ActionStatuses 没这一态：进行中那一格永远空，"
              "屏上的行全挤到「已跳过」下面看不见了",
        [(GROWTH, BUCKET_ACTIVE, "  { key: 'sectionActive', status: 'pending' },")],
        G5_, "分区状态取自 Go 的 ActionStatuses，且 cancelled 那一格是登记过的缺口而不是漏写",
    ),
    (
        "E2", "行动面的 failed 直接吃记录面的 loading ⇒ 两段同源把另一个源的可用时刻一起吃掉："
              "记录还在加载时行动区报「加载失败」",
        [(GROWTH, ACTIONS_FAILED_STATE, "  const [actionsFailed, setActionsFailed] = useState(loading);")],
        G5_, "行动面三态各一份，且不与记录面共用一个 flag",
    ),
    (
        "E3", "整屏 `if (loading) return` 回来 ⇒ 行动区不等记录面加载完就不能用："
              "这一屏有两个数据源，早退把另一个源的可用时刻也吃掉（T4-04 实施中真犯过的错）",
        [(GROWTH, ACTION_BUCKETS_HEAD,
          "  if (loading) {\n    return null;\n  }\n" + ACTION_BUCKETS_HEAD)],
        G5_, "记录区不再整屏早退：行动区不等记录加载完就能用（记录区自己有 spinner）",
    ),
    (
        "E4", "行动列表的 `truncated` 被吞（钉成 false）⇒ 第 51 条行动凭空消失，"
              "用户以为「我没立过那么多」——这是最难归因的假象",
        [(GROWTH, SET_ACTIONS_TRUNC, "      setActionsTruncated(false);")],
        G5_, "两个列表的 truncated 都被消费：一句「更早的没显示」而不是把截断讲成没有",
    ),
    (
        "E5", "目标候选的 `truncated` 被吞 ⇒ 第 51 颗目标在弹层里选不出来，"
              "而那一格的文案说的是「挑一颗」而不是「这里只列了最近的」",
        [(INSIGHTS, SET_GOALS_TRUNC, "      setActionGoalsTruncated(false);")],
        G5_, "两个列表的 truncated 都被消费：一句「更早的没显示」而不是把截断讲成没有",
    ),
    (
        "E6", "en 的截断文案改成「全部列在这里了」⇒ 与 Go 的 `ORDER BY created_at DESC` + LIMIT "
              "直接矛盾：一句反向的谎，比不说更坏",
        [(LOCALES / "en.ts", TRUNC["en"],
          '    "truncated": "Every action you have recorded is listed here.",\n')],
        G5_, "截断那句文案与 Go 的 ORDER BY created_at DESC 同口径：说的是「更早的没显示」",
    ),
    (
        "E7", "空态条件少「不失败」那一段 ⇒ 拉取失败时屏上同时说「加载失败」和"
              "「你一条行动都没有」：两种说法在同一格",
        [(GROWTH, EMPTY_STATE,
          EMPTY_STATE.replace("!actionsLoading && !actionsFailed &&", "!actionsLoading &&"))],
        G5_, "空态只在「不加载、不失败、确实零行」时说出口",
    ),
    # ── ⑥ 行内语义 ──
    (
        "F1", "先开结果弹层再重取分区 ⇒ 屏上在 active 那一格里问「结果如何」，"
              "而这一颗刚刚被服务端落成 completed：顺序反了就是把状态问错",
        [(GROWTH, MARK_DONE_ORDER,
          "      setResultFor(item);\n      await loadActions();\n"
          "      setResultChoice(null);\n      setResultNote('');")],
        G6_, "完成之后先重取分区再开结果弹层（顺序反了就把行动留在 active 上问结果）",
    ),
    (
        "F2", "幂等两句话合成一句 ⇒ `changed=false`（今天已经记过）在屏上念成「新记了一条」："
              "库里只有一行，用户以为记了两次",
        [(GROWTH, CHANGED_ALERT, "      Alert.alert(t('actions.resultSaved'));")],
        G6_, "changed=false 是「今天已经记过了」而不是失败：两句话都在，且按回执选",
    ),
    (
        "F3", "「跳过」那枚按钮不再看忙位 ⇒ 一次抖动里完成与跳过各发一次："
              "服务端回 409/后写覆盖先写，而屏上先长出两种说法",
        [(GROWTH, MARK_SKIP_HEAD, "  const markSkip = async (item: ActionItem) => {\n")],
        G6_, "忙位防抖：两枚行内按钮共用一个 busy，结果提交另有自己一个",
    ),
    (
        "F4", "点档位即提交 ⇒ 备注那一格永远来不及写：这一面唯一的幂等判据是（同一天·同 type），"
              "手滑多点一次就吃掉今天那一格",
        [(GROWTH, CHOICE_ON_PRESS,
          "                  onPress={() => { setResultChoice(choice); void submitResult(); }}")],
        G6_, "结果必须显式提交：四枚档位只选中，submitResult 只长在最后一枚按钮上",
    ),
    (
        "F5", "写结果失败也把弹层关掉 ⇒ 行动已经落成 completed、结果没记上，"
              "而关掉就等于让用户以为记过了",
        [(GROWTH, RESULT_TAIL,
          "      Alert.alert(t('common.error'), t('actions.failed'));\n"
          "      setResultFor(null);\n"
          "    } finally {\n      setResultBusy(false);")],
        G6_, "写结果失败不关弹层：关掉就是让用户以为记过了",
    ),
    (
        "F6", "`operation_id` 从每次新串改成固定串 ⇒ 两次不同的尝试在回显里成了同一笔："
              "重试再也对不上自己那次点击",
        [(GROWTH, TRACE_CALL, "        resultFor.id, resultChoice, resultNote.trim(), null, 'op-fixed',")],
        G6_, "operation_id 每次尝试都是新的一条（同日幂等按 UTC 日判，它只负责把重试对上自己那次点击）",
    ),
    # ── ⑦ 洞察入口与两步写 ──
    (
        "G1", "预填改用 `slice` 而不是按 rune 截 ⇒ 标题里的 emoji 被切成半个字符，"
              "并把「截到 200」这条与 Go 同值的判据换成一个会数错单位的写法",
        [(INSIGHTS, PREFILL, "    const prefill = item.statement.slice(0, ACTION_TITLE_MAX);")],
        G7_, "预填按 rune 截断到 Go 的上限，不用 slice（slice 会把 emoji 切成半个字符）",
    ),
    (
        "G2", "空标题也发请求 ⇒ 服务端 400，而屏上刚刚说的是「什么都没加」："
              "用户以为自己立了一条空标题的行动",
        [(INSIGHTS, SUBMIT_GUARD, "    if (!actionFor || actionBusy) return;")],
        G7_, "空标题不发请求，忙时不重发",
    ),
    (
        "G3", "两步写之间不复用已建成的目标 id ⇒ 用户重试时又建一颗同名目标："
              "canonical 里凭空多一行，而 04 的目标度量按行数算",
        [(INSIGHTS, GOALID_REUSE, "      let goalId = actionGoalId === NEW_GOAL ? null : actionGoalId;")],
        G7_, "两步写之间留住已建成的目标 id：重试只补行动那一步，不再建第二颗目标",
    ),
    (
        "G4", "本地键 `NEW_GOAL` 直接当 goal_id 发出去 ⇒ path 变成 `/goals/__new__/actions`："
              "服务端 404，而这一步谎称「目标已经选好」",
        [(INSIGHTS, CREATE_ACTION_CALL, "      await createAction(NEW_GOAL, title, '', dueAt);")],
        G7_, "NEW_GOAL 只是本地键，绝不进请求体",
    ),
    (
        "G5", "截止日改用本地零点（等价于 UTC 漂移）⇒「那天结束之前」变成「那天一开始就过期」："
              "屏上写的日期与库里存的是两个日子",
        [(INSIGHTS, DUE_PARSE, "      const parsed = new Date(`${due}T00:00:00`);")],
        G7_, "截止日取本地日的 23:59 再转 RFC3339：UTC 零点会切掉那天的最后一整天",
    ),
    (
        "G6", "两步写失败弹服务端串（`e.message`）⇒ 用户读到的是一句中英双语的运维话术，"
              "而不是「什么都没加」这个他能据以行动的事实",
        [(INSIGHTS, INSIGHTS_CATCH,
          "    } catch (e) {\n"
          "      // 不弹 e.message（服务端给运维的双语串），也不假装成功：这一句要说清「什么都没加」。\n"
          "      Alert.alert(t('common.error'), String((e as Error).message));")],
        G7_, "两步写失败说一句「什么都没加」，不弹服务端双语串",
    ),
    (
        "G7", "目标候选拉不到时不再退成「新建一颗」⇒ 那一格空列表把整个弹层堵死："
              "一次候选接口的抖动变成「行动立不出去」",
        [(INSIGHTS, RELOAD_CATCH,
          "      setActionGoalsFailed(true);\n      setActionGoalsTruncated(false);")],
        G7_, "目标候选拉不到时退成「新建一颗」而不是整块坏掉，且给重试",
    ),
    (
        "G8", "CTA 丢掉无障碍名 ⇒ 读屏用户听到的是「按钮」而不知它做什么："
              "这一枚是整条闭环的唯一入口",
        [(INSIGHTS, CTA_A11Y, "")],
        G7_, "每张洞察卡有一枚入口按钮，文案走 actions.cta（不是屏上自造英文）",
    ),
    # ── ⑧ 15 本 locale ──
    (
        "H1", "zh 的 actions 段少 `truncated` 一键 ⇒ 中文那一屏的截断说明回退成英文键名/整句英文："
              "只有那一门语言坏，其余 14 门照常",
        [(LOCALES / "zh.ts", TRUNC["zh"], "")],
        G8_, "zh：actions 段与 en 同键集（含 result 四键）",
    ),
    (
        "H2", "15 本 locale **同时**少 `truncated` 同一格 ⇒ `i18nLocales` 那道全表键集闸放得过它"
              "（比的是两侧一致），只有 en 自己的 29 格计数 +「屏上用的键必须在册」能兜住："
              "屏上引用一个不存在的键，15 门一起显英文",
        [(LOCALES / f"{lng}.ts", TRUNC[lng], "") for lng in LANGS],
        G8_, "en 那一格 29 个扁平键 + result 四键，且屏上用的每个键都在册",
    ),
    (
        "H3", "占位符 `{{date}}` 长到 `cta` 那一格 ⇒ 洞察卡上的按钮文案带出一个没传的变量："
              "屏上直接念出 `{{date}}`（只有 due 该带它）",
        [(LOCALES / "en.ts", EN_CTA, '    "cta": "Try a small action {{date}}",')],
        G8_, "占位符只长在 due 那一格，且每一门语言都带着它",
    ),
    (
        "H4", "屏上引用一个没登记的键（`skipSavedNow`）⇒ i18next 把键名原样吐到 Alert 上："
              "「键先行不上刀」那条登记表之外的手滑",
        [(GROWTH, SKIP_SAVED_KEY, "t('actions.skipSavedNow')")],
        G8_, "en 那一格 29 个扁平键 + result 四键，且屏上用的每个键都在册",
    ),
    (
        "H5", "语言表少注册一门（th）⇒ 泰语用户整屏英文，而键集闸数的是「表里有几门」："
              "少了那一门时它扫的是 14 门的空集",
        [(LOC_INDEX, TH_RESOURCE, "")],
        G8_, "语言表恰好 15 门（少了就是这一闸在扫空集）",
    ),
]

GREEN: list[tuple] = [
    (
        "GR1", "InsightsScreen 的 styles 段上方插 15 行注释 ⇒ 六十五条判据全绿："
               "闸读的是文本形状而不是行号/位置巧合（唯一按行号判的①在 api.ts 与契约表那一侧）",
        [(INSIGHTS, "  goalInput: {",
          PY_COMMENTS.replace("  // ", "  // ") + "  goalInput: {")],
    ),
    (
        "GR2", "zh 的截断文案换一句同义中文 ⇒ 全绿：⑤ 只让 **en** 那一格背「更早的」这个语义判据，"
               "其余 14 门的措辞由 i18n 审校闸管，不在这里重复",
        [(LOCALES / "zh.ts", TRUNC["zh"],
          '    "truncated": "这里只列最近几条，更早的行动没有显示出来。",\n')],
    ),
    (
        "GR3", "api.ts 文件末尾追加 15 行注释 ⇒ 全绿：契约表记的是那六条各自的行号，"
               "在后面追加不动它们（也说明这张表**会**因为在前面插代码而漂移——"
               "那是 §八 登记的已知代价，不是这条闸的漏）",
        [(API, "export async function getMe(): Promise<AuthUser> {\n  return request<AuthUser>('/auth/me');\n}",
          "export async function getMe(): Promise<AuthUser> {\n  return request<AuthUser>('/auth/me');\n}"
          + PY_COMMENTS)],
    ),
    (
        "GR4", "submitAction 的 catch 里再加一行**提到** message 的注释 ⇒ 全绿："
               "那条闸判的是「Alert 的参数里不许有服务端串」，不是全文不许出现这个词——"
               "否则登记为什么不打它的注释自己就会把闸打红",
        [(INSIGHTS, INSIGHTS_CATCH,
          "    } catch {\n"
          "      // 不弹 e.message（服务端给运维的双语串），也不假装成功：这一句要说清「什么都没加」。\n"
          "      // 反例登记：把服务端那一格直接抬到屏上，用户读到的是运维话术。\n"
          "      Alert.alert(t('common.error'), t('actions.failed'));")],
    ),
    (
        "GR5", "contracts/endpoints.md 表头上方插 15 行注释 ⇒ 全绿：① 的过滤器只看表行，"
               "不被文档性文字喂出假行（否则「恰好七条」那条证人就是数注释）",
        [(ENDPOINTS_MD, ROW_LIST_GOALS,
          "".join(f"<!-- T4-04 绿闸占位 {i} -->\n" for i in range(15)) + ROW_LIST_GOALS)],
    ),
]


def _verify_anchor(edits: list) -> str | None:
    for path, old, _new in edits:
        if not path.exists():
            return "靶文件不存在"
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        if n != 1:
            return f"锚点命中 {n} 次（要 1）：{old[:80]!r}"
        if path.suffix == ".py":
            try:
                ast.parse(src.replace(old, _new, 1))
            except SyntaxError as exc:
                return f"变异体不是合法 Python：{exc}"
    return None


def _apply(edits: list) -> None:
    for path, old, new in edits:
        src = path.read_text(encoding="utf-8")
        assert src.count(old) == 1, f"{path.name}：锚点命中数变了\n{old[:120]!r}"
        path.write_text(src.replace(old, new, 1), encoding="utf-8")


BASELINE: tuple[int, str] | None = None


def run_jest() -> tuple[int, str]:
    if shutil.which("npx") is None:
        return -2, "npx 不在 PATH：前端闸门没跑，不算绿"
    proc = subprocess.run(
        ["npx", "jest", "--silent", SUITE],
        cwd=FRONT, capture_output=True, text=True, timeout=1200,
    )
    return proc.returncode, proc.stdout + proc.stderr


def baseline() -> tuple[int, str]:
    global BASELINE
    if BASELINE is None:
        BASELINE = run_jest()
    return BASELINE


def marker_hit(out: str, describe: str, title: str) -> bool:
    if "Test suite failed to run" in out:
        return False
    return f"● {describe} › {title}" in out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数，不改任何文件")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()
    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    cases = [*MUTATIONS, *GREEN]
    if args.check:
        bad = 0
        for case in cases:
            problem = _verify_anchor(case[2])
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        print(f"锚点校验：{'全部命中一次且变异体合法' if bad == 0 else f'{bad} 条有问题'}"
              f"（红 {len(MUTATIONS)}＋绿 {len(GREEN)}＝{len(cases)} 条）")
        return 1 if bad else 0

    base_code, base_out = baseline()
    if base_code == -2:
        print(f"全部未跑（{base_out}）：缺工具链不算绿")
        return 2
    if base_code != 0:
        print(f"基线本来就不绿（exit={base_code}）：不能把环境的红记成闸的红")
        print("  " + "\n  ".join(base_out.splitlines()[-12:]))
        return 2
    tests_line = next((l.strip() for l in base_out.splitlines() if l.startswith("Tests:")), "?")
    print(f"基线：{SUITE} 全绿（{tests_line}）")

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-t404-") as tmp:
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
        failures: list[str] = []
        try:
            for cid, desc, edits, describe, title in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                try:
                    _apply(edits)
                    code, out = run_jest()
                finally:
                    restore()
                if code != 0 and marker_hit(out, describe, title):
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（exit={code}，红点未落在「{describe} › {title[:28]}…」）：{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-12:]))

            for cid, desc, edits in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                try:
                    _apply(edits)
                    code, out = run_jest()
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 被误伤 ✗  {desc}")
                    print("  " + "\n  ".join(out.splitlines()[-12:]))
        finally:
            restore()
            residue = [p for p in paths if not filecmp.cmp(originals[p], p, shallow=False)]
            print(f"\n残留复核：{'0 个靶文件与原件逐字节一致' if not residue else ''}")
            if residue:
                print("  以下靶文件恢复后仍不一致：" + "、".join(str(p) for p in residue))

        total_red = red_ok + red_fail
        total_green = green_ok + green_fail
        print(f"\n汇总：打红 {red_ok}/{total_red}｜保持绿 {green_ok}/{total_green}｜未跑 0")
        if failures:
            print("失败条目：" + ",".join(failures))
        return 1 if (red_fail or green_fail) else 0


if __name__ == "__main__":
    sys.exit(main())
