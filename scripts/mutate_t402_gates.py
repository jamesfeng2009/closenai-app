#!/usr/bin/env python3
"""T4-02（Go：goals/actions CRUD 与状态机 ＋ BFF 那一跳六条转发）的变异自证（2026-10-19）。

靶文件九个（Go 七＋Python 二），红闸 39 条、绿闸 5 条。

工单 §验收四句，每句都有**它自己的**靶与红闸（[[feedback-mutation-anchor-must-reach-target]]）：

1. 「状态机非法转移 409」⇒ SM1…SM4 打的是转移表与 dispatch 本身（goal.go / service_goal.go），
   EM2 打的是 HTTP 侧那一格折叠（409 换成 400 就只有这一条会红）；
2. 「跨用户 404」⇒ OW1（domain 查不到当查到了）、EM1（把 404 折成 409）、
   DB4（SQL 摘掉 `user_id` 谓词）、DB9（仓储把 0 行当成功）——四侧各挡一种坏法；
3. 「重复 PATCH 幂等」⇒ ID1/ID2 摘掉同态那一格（行动面与目标面**各自**有闸）；
4. 「两张表各有真行」⇒ DB1…DB8 打在生成物与读面上：NULL/空串两个方向、
   cancel 不许刷 completed_at、`LIMIT` 与 `IS NULL` 只有真库能答。

另有 HT1…HT7 钉入口形状（多余键、身份只从头部、path 取 goal_id、截断标志、假装有值、
未知动词）与 LS1/LS2 钉分页与过滤器的两处收敛，LS1 那一格红的是 `Truncated` 的地基。

**P1…P11 钉的是 BFF 那一跳**（本票把 T3-03 结案行登记的欠账一并兑了：目标/行动六条
`/api/v1` 转发端点）。靶在 `backend/routers/v3.py`／`backend/services/v3_proxy.py`，
红点逐条落在 `test_v3_goals_proxy.py` 与 `test_bff_is_thin.py` 的对应判据上：
path×方法对账（P1/P2）、path 段转义两面各一条（P3/P4）、过滤器转发（P5）、
body 不拆包（P6，搬空 Go 那条 `DisallowUnknownFields` 的靶）、错误不被吞（P7）、
注册面数量与动词集合（P8/P9）、身份入口（P10）、业务分支关键字（P11）。
**第一轮 P7 是等价变异**：只把 `http_status=resp.status_code` 摘掉照样全绿，
因为 `AppException` 自己会按 code 推出状态——红点没落在判据上，改成「不报错照单回 data」。

Q1 打在**不参与执行**的那一侧（`db/queries/canonical.sql`）：改它一条 Go 测试都不会红，
只有 `sqlc diff` 会红——这是「源与产物两个真源」的现行代价，已登记归 T6-03。

一条**刻意不纳入**的等价变异（留档以免下轮重猜）：把 `internalEnabled()` 里
`s.v3.Goals != nil` 那一格摘掉——测试装配始终注入 goal 桩，摘掉它没有任何一条测会红；
这一格的真实坏法是「装配漏了 goals」，而它的判据在 `cmd/go-api` 的编译期（`*domain.GoalBrowser`
是必填字段），不在这一面的运行期。**本票在这一格确实翻过一次车**：
`tools/v3surface` 的夹具没跟着补 Goals/GoalWrites，于是跨进程那 15 条整片 404——
「装配漏了」的坏法不是假想，它在第一次全量回归就被抓到了；补上夹具后同批加了
`fixtureGoal`／`fixtureGoalWrites` 的跨进程四条用例（见 `test_v3_proxy_e2e.py` 第 4 节）。
要再钉一层（go-api 装配测）已记入 T6-04 的待办。

绿闸 GR1…GR5 证的是「闸不钉排版与注释」：Go 三处、Python 两处各插 15 行注释都不该让任何判据变红。

纪律：串行执行（[[feedback-mutation-gates-no-concurrency]]）、copy2 备份＋`filecmp(shallow=False)`
逐字节写回、不用 git checkout（[[feedback-mutation-restore-no-git-checkout]]）；每条红闸先跑基线，
基线本来就不绿或前置缺失一律记「未跑」（[[feedback-gate-needs-positive-witness]]）；
Go 侧 `--- SKIP` 与 `[build failed]`、pytest 侧 `ERROR <节点>` 与 `no tests ran` 都**不算打红**——
那些红的是环境，不是判据。

用法（store 那一侧需要真库，缺 env 的全部记「未跑」不算绿）：
    set -a && . /tmp/t206_env.sh && set +a
    GOROOT=/Users/fengyu/sdk/go1.27.1 ./.venv/bin/python scripts/mutate_t402_gates.py --check
    GOROOT=/Users/fengyu/sdk/go1.27.1 ./.venv/bin/python scripts/mutate_t402_gates.py
    ./.venv/bin/python scripts/mutate_t402_gates.py --only SM1,EM1,DB4
"""
from __future__ import annotations

import argparse
import ast
import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GO_CORE = REPO / "go-core"
VENV_PY = REPO / ".venv" / "bin" / "python"
GOROOT = os.environ.get("GOROOT", "/Users/fengyu/sdk/go1.27.1")
GO_BIN = os.environ.get("GO_BIN", f"{GOROOT}/bin/go")
GO_ENV = {
    **os.environ,
    "GOROOT": GOROOT,
    "PATH": f"{GOROOT}/bin:{os.environ.get('PATH', '')}",
    "GO_BIN": GO_BIN,
    "GOTOOLCHAIN": "local",
}
PG_ENV = "TEST_DATABASE_URL"

GOAL = GO_CORE / "internal" / "domain" / "goal.go"
SVC = GO_CORE / "internal" / "domain" / "service_goal.go"
V3GO = GO_CORE / "internal" / "httpapi" / "v3.go"
V3GOALS = GO_CORE / "internal" / "httpapi" / "v3_goals.go"
ROUTES = GO_CORE / "internal" / "httpapi" / "routes.go"
QUERIES = GO_CORE / "db" / "queries" / "canonical.sql"
GEN = GO_CORE / "internal" / "store" / "canonicaldb" / "canonical.sql.go"
REPO_GO = GO_CORE / "internal" / "store" / "canonical_repo.go"
GOAL_READ = GO_CORE / "internal" / "store" / "goal_read.go"
ROUTER_PY = REPO / "backend" / "routers" / "v3.py"
PROXY_PY = REPO / "backend" / "services" / "v3_proxy.py"

SCHEMA = "backend/tests/test_canonical_schema.py"
SQLC = f"{SCHEMA}::test_sqlc_generated_code_matches_queries"

PKG_DOMAIN = "./internal/domain/"
PKG_API = "./internal/httpapi/"
PKG_STORE = "./internal/store/"
T = "\t"

# 点名的判据（每条红闸必须把**它自己**那一条打红）。
GO_TABLES = "^TestTransitionTablesAdmitOnlyActiveOrigin$"
GO_GOAL_SKIP = "^TestTransitionGoalRejectsSkipButAdmitsCompleteAndCancel$"
GO_DISPATCH = "^TestTransitionActionAdmitsThreeVerbsFromActive$"
GO_TITLE = "^TestCreateGoalTitleBounds$"
GO_IDEM_ACT = "^TestTransitionActionIsIdempotentOnSameState$"
GO_IDEM_GOAL = "^TestTransitionGoalIsIdempotentOnSameState$"
GO_OWNER_PRE = "^TestCreateActionChecksOwnershipBeforeInsert$"
GO_LIMIT = "^TestGoalBrowserClampsLimitAndMarksTruncation$"
GO_STATUS = "^TestGoalBrowserPassesUnknownStatusThrough$"

H_VANISH = "^TestV3GoalRoutesExistWithTokenAndVanishWithout$"
H_IDENTITY = "^TestV3GoalsIgnoreIdentityFromQueryAndBody$"
H_PATH = "^TestV3ActionCreateTakesGoalIDFromPath$"
H_MAPPING = "^TestV3GoalErrorMapping$"
H_VERB = "^TestV3TransitionRejectsUnknownVerbAndExtraKeys$"
H_GOAL_LIST = "^TestV3GoalsListForwardsFiltersAndRendersNullTimes$"
H_ACTION_LIST = "^TestV3ActionsListMarksTruncationAndKeepsGoalIDNull$"

S_ROUNDTRIP = "^TestGoalWriteReadRoundTripKeepsNullAndTargetAt$"
S_LINK = "^TestActionGoalLinkAndUnlinkedAreBothReadable$"
S_CANCEL_GOAL = "^TestCancelGoalNeverWritesCompletedAt$"
S_CANCEL_ACT = "^TestCancelCommitmentKeepsCompletedAndSkippedNull$"
S_OWNER = "^TestCancelRejectsForeignUserAndLeavesRowUntouched$"
S_LIST = "^TestGoalAndActionListsHonourStatusFilterAndLimit$"

# BFF 那一侧（本票把 T3-03 结案行欠的那六条一并接了）。
PROXY = "backend/tests/test_v3_goals_proxy.py"
THIN = "backend/tests/test_bff_is_thin.py"
P_SIX = f"{PROXY}::test_six_routes_dial_the_six_internal_paths"
P_GOAL_ID = f"{PROXY}::test_goal_id_comes_from_path_not_body"
P_FILTER = f"{PROXY}::test_list_forwards_status_and_limit_only"
P_ESCAPE_G = f"{PROXY}::test_goal_path_segment_is_escaped"
P_ESCAPE_A = f"{PROXY}::test_action_path_segment_is_escaped"
P_VERBATIM = f"{PROXY}::test_body_is_forwarded_verbatim_including_identity_key"
P_ERRORS = f"{PROXY}::test_go_errors_pass_through_on_goal_face"
P_COUNT = f"{THIN}::test_forwarded_surface_has_nine_handlers_and_no_extra_verbs"
P_IDENTITY = f"{THIN}::test_identity_enters_only_from_the_auth_dependency"

# ── 锚点（逐字取自被测文件，命中数必须恰好 1）────────────────────────────────
ACTION_GUARD = (
    f"func NextActionStatus(current string, verb LifecycleVerb) (string, bool) {{\n"
    f"{T}if current != StatusActive {{\n{T}{T}return \"\", false\n{T}}}\n"
)
GOAL_SKIP_TAIL = (
    f"{T}case VerbCancel:\n{T}{T}return StatusCancelled, true\n{T}}}\n"
    f"{T}// skip：personal_goals 没有 skipped_at（见文件头）。\n"
    f"{T}return \"\", false\n"
)
GOAL_SKIP_GUARD = (
    f"{T}if !validVerbForGoal(verb) {{\n"
    f"{T}{T}return GoalReceipt{{}}, fmt.Errorf(\"%w: goal 不接受 %s（personal_goals 没有 skipped_at 列）\",\n"
    f"{T}{T}{T}ErrGoalIllegalTransition, verb)\n"
    f"{T}}}\n"
)
TITLE_BOUND = f"{T}if utf8.RuneCountInString(trimmed) > maxGoalTitle {{\n"
DISPATCH_SKIP = f"{T}{T}case VerbSkip:\n{T}{T}{T}err = st.SkipCommitment(ctx, userID, actionID)\n"
IDEM_ACT = (
    f"{T}{T}to, legal := NextActionStatus(state.Status, verb)\n"
    f"{T}{T}if !legal {{\n{T}{T}{T}if state.Status == verbStatus(verb) {{\n"
)
IDEM_GOAL = (
    f"{T}{T}to, legal := NextGoalStatus(state.Status, verb)\n"
    f"{T}{T}if !legal {{\n{T}{T}{T}if state.Status == verbStatus(verb) {{\n"
)
REFSTATE_MISS = f"{T}return RefState{{}}, fmt.Errorf(\"%w: %s\", ErrInvalidOwnership, key)\n"
LIMIT_PLUS_ONE = f"{T}rows, err := b.read.ListGoalRecords(ctx, userID, status, limit+1)\n"
NARROW_PASS = f"{T}return strings.TrimSpace(status), clampListLimit(limit)\n"
NO_UNKNOWN_KEYS = (
    f"{T}dec := json.NewDecoder(bytes.NewReader(raw))\n"
    f"{T}dec.DisallowUnknownFields()\n"
)
ROUTE_GOAL_CREATE = (
    f"{{method: \"POST\", path: \"/internal/v3/goals\",\n"
    f"{T}{T}gated:   func(s *Server) bool {{ return s.internalEnabled() }},\n"
    f"{T}{T}handler: func(s *Server) http.HandlerFunc {{ return s.handleV3GoalCreate }}}},\n"
)
ENABLED_WITH_TOKEN = (
    f"{T}{T}s.v3.Goals != nil && s.v3.GoalWrites != nil && s.cfg.GoServiceToken != \"\"\n"
)
ACTION_FROM_PATH = (
    f"{T}p := domain.ActionProposal{{UserID: userID, GoalID: r.PathValue(\"id\"),\n"
    f"{T}{T}Title: body.Title, Description: body.Description}}\n"
)
TRUNCATED_WIRE = f"{T}WriteSuccess(w, v3ActionListPayload{{Items: items, Truncated: view.Truncated}},\n"
TARGET_AT_WIRE = f"{T}{T}{T}TargetAt:    optionalTime(g.TargetAt, !g.TargetAt.IsZero()),\n"
VERB_CHECK = (
    f"{T}verb := domain.LifecycleVerb(strings.TrimSpace(raw))\n"
    f"{T}if !verb.Valid() {{\n"
)
MAP_OWNER_404 = f"{T}{T}return apierr.New(apierr.CodeResourceNotFound, \"资源不存在 / Resource not found\")\n"
MAP_ILLEGAL_409 = (
    f"{T}{T}return apierr.New(apierr.CodeConflict,\n"
    f"{T}{T}{T}\"当前的状态不允许这个动作 / Transition not allowed from current state\")\n"
)
GEN_INSERT_ARGS = (
    f"{T}{T}arg.Title,\n{T}{T}arg.Description,\n{T}{T}arg.Status,\n"
    f"{T}{T}arg.StartedAt,\n{T}{T}arg.TargetAt,\n"
)
GEN_CANCEL_GOAL = (
    "const cancelGoal = `-- name: CancelGoal :one\n"
    "UPDATE personal_goals\n"
    "SET status = 'cancelled', updated_at = now()\n"
)
GEN_CANCEL_ACT = (
    "const cancelCommitment = `-- name: CancelCommitment :one\n"
    "UPDATE personal_commitments\n"
    "SET status = 'cancelled', updated_at = now()\n"
)
GEN_LIST_GOALS = (
    "FROM personal_goals\n"
    "WHERE user_id = $1 AND ($2::varchar = '' OR status = $2)\n"
    "ORDER BY created_at DESC\n"
)
GEN_LIST_GOALS_FILTERED = (
    "FROM personal_goals\n"
    "WHERE user_id = $1 AND status = $2\n"
    "ORDER BY created_at DESC\n"
)
GEN_GOAL_READ_DESC = (
    f"{T}{T}{T}ID: row.ID, Title: row.Title, Description: row.Description.String,\n"
    f"{T}{T}{T}Status: row.Status, StartedAt: timeOf(row.StartedAt),\n"
)
GEN_GOAL_READ_GOALID = f"{T}{T}{T}GoalID:    row.GoalID.String,\n"
READ_LIMIT_GUARD = (
    f"{T}if limit <= 0 {{\n"
    f"{T}{T}// 「取 0 条」与「取全部」在 SQL 里是两个形状（LIMIT 0 ⇄ 没有 LIMIT），\n"
    f"{T}{T}// 所以这里拒绝，而不是替调用方猜一个（insight_browse_read.go 同判据）。\n"
    f"{T}{T}return nil, fmt.Errorf(\"store: goal list needs a positive limit, got %d\", limit)\n"
    f"{T}}}\n"
)
REPO_CANCEL_GOAL_OWNER = (
    f"{T}_, err := t.q.CancelGoal(ctx, canonicaldb.CancelGoalParams{{ID: goalID, UserID: userID}})\n"
    f"{T}if errors.Is(err, pgx.ErrNoRows) {{\n"
    f"{T}{T}return fmt.Errorf(\"%w: goal %s\", domain.ErrInvalidOwnership, goalID)\n"
    f"{T}}}\n"
)
SQL_CANCEL_GOAL = (
    "UPDATE personal_goals\n"
    "SET status = 'cancelled', updated_at = now()\n"
)

# ── BFF 那一侧的锚点（靶 ROUTER_PY／PROXY_PY，闸 = pytest）──
CALL_ACTIONS = (
    "    data = await v3_proxy.post_json(v3_proxy.goal_actions_path(goal_id), user_id,"
    " body=body)\n"
)
PATCH_FORWARD = '    return await forward("PATCH", path, user_id, payload=body)\n'
GOAL_DETAIL_BUILD = (
    "def goal_detail_path(goal_id: str) -> str:\n"
    '    return PATH_GOAL_DETAIL + quote(goal_id, safe="")\n'
)
ACTION_DETAIL_BUILD = (
    "def action_detail_path(action_id: str) -> str:\n"
    '    return PATH_ACTION_DETAIL + quote(action_id, safe="")\n'
)
GOAL_LIST_CALL = (
    "    data = await v3_proxy.get_json(v3_proxy.PATH_GOAL_LIST, user_id,\n"
    '                                   params=v3_proxy.list_params({"status": status,\n'
    '                                                                "limit": limit}))\n'
)
RETURN_BODY = "    return body\n"
UNWRAP_RAISE = (
    "    if code == 200:\n"
    "        return data\n"
    "    raise AppException(int(code), str(message), data=data,"
    " http_status=resp.status_code)\n"
)
ACTION_HANDLER_BLOCK = (
    '@router.patch("/actions/{action_id}")\n'
    "async def transition_action(action_id: str, request: Request,\n"
    "                            user_id: str = Depends(get_current_user)) -> dict:\n"
    "    body = await _json_body(request)\n"
    "    data = await v3_proxy.patch_json(v3_proxy.action_detail_path(action_id), user_id,\n"
    "                                     body=body)\n"
    "    return success(data)\n"
)
ACTION_LIST_DECORATOR = '@router.get("/actions")\n'
ACTION_LIST_HEAD = (
    "async def list_actions(status: str = None, limit: str = None,\n"
    "                       user_id: str = Depends(get_current_user)) -> dict:\n"
)
ACTION_LIST_BODY = (
    "    data = await v3_proxy.get_json(v3_proxy.PATH_ACTION_LIST, user_id,\n"
)

MUTATIONS: list[tuple] = [
    # ── 1. 状态机：唯一那张表（靶 GOAL／SVC，闸 = domain 单测）──
    (
        "SM1", "行动侧的守卫从「只放行 active 起算」换成「只有 cancelled 起算不了」⇒ 已完成的行动"
              "能被改回任何一态，而 completed_at／skipped_at 还留在行上，行自相矛盾",
        [(GOAL, ACTION_GUARD, ACTION_GUARD.replace("current != StatusActive", "current == StatusCancelled"))],
        "go", PKG_DOMAIN, GO_TABLES,
    ),
    (
        "SM2", "给目标开放 skip（转移表 admit ＋ 目标侧那格守卫摘掉）⇒ 一次「合法」转移写不出列"
               "（personal_goals 没有 skipped_at），dispatch 没有 skip 分支于是悄悄走了 CancelGoal，"
               "工单 §怎么做.1 的四态里有一态根本不属于这一面",
        [(GOAL, GOAL_SKIP_TAIL,
          GOAL_SKIP_TAIL.replace(f"{T}}}\n", f"{T}case VerbSkip:\n{T}{T}return StatusSkipped, true\n{T}}}\n")),
         (SVC, GOAL_SKIP_GUARD, "")],
        "go", PKG_DOMAIN, GO_GOAL_SKIP,
    ),
    (
        "SM3", "标题上限写成 `>= maxGoalTitle` ⇒ 恰好 200 字的合法标题被拒，用户「写完却存不下」",
        [(GOAL, TITLE_BOUND, TITLE_BOUND.replace("> maxGoalTitle", ">= maxGoalTitle"))],
        "go", PKG_DOMAIN, GO_TITLE,
    ),
    (
        "SM4", "dispatch 里 skip 落到 CancelCommitment ⇒ 三枚动词互不串台这一格失守，"
               "「跳过」在库里成了「取消」，而 04 §22 的两种收尾就此并成一个",
        [(SVC, DISPATCH_SKIP, DISPATCH_SKIP.replace("SkipCommitment", "CancelCommitment"))],
        "go", PKG_DOMAIN, GO_DISPATCH,
    ),
    # ── 2. 幂等：重复 PATCH 不产生第二次变更（靶 SVC）──
    (
        "ID1", "摘掉行动侧「当前态就是动词目标态 ⇒ 幂等返回」那一格 ⇒ 连点两下的第二次回 409，"
               "网络重试被当成非法转移",
        [(SVC, IDEM_ACT, IDEM_ACT.replace("state.Status == verbStatus(verb)", "false"))],
        "go", PKG_DOMAIN, GO_IDEM_ACT,
    ),
    (
        "ID2", "摘掉目标侧同一格 ⇒ 工单 §验收的「重复 PATCH 幂等」只兑了行动面那一半",
        [(SVC, IDEM_GOAL, IDEM_GOAL.replace("state.Status == verbStatus(verb)", "false"))],
        "go", PKG_DOMAIN, GO_IDEM_GOAL,
    ),
    # ── 3. 跨用户（靶 SVC，闸 = domain 的归属判据）──
    (
        "OW1",
        "refState 查不到时返回空状态而不是 ErrInvalidOwnership ⇒「这一颗不存在」与「这一颗不是你的」"
        "都退化成转移非法（409），越权没有回声，而且还敢插行",
        [(SVC, REFSTATE_MISS, f"{T}return RefState{{}}, nil\n")],
        "go", PKG_DOMAIN, GO_OWNER_PRE,
    ),
    # ── 4. 列表面：limit+1 证人与过滤器原词（靶 SVC）──
    (
        "LS1", "读面不再多取一条 ⇒ `Truncated` 永远为 false，前端显示「这就是全部」而库里还有",
        [(SVC, LIMIT_PLUS_ONE, LIMIT_PLUS_ONE.replace("limit+1", "limit"))],
        "go", PKG_DOMAIN, GO_LIMIT,
    ),
    (
        "LS2", "不认识的 status 在领域层被换成空串（＝忽略过滤器）⇒ 一次拼错的请求把用户"
               "以为已经过滤掉的历史全拉出来",
        [(SVC, NARROW_PASS, f"{T}return \"\", clampListLimit(limit)\n")],
        "go", PKG_DOMAIN, GO_STATUS,
    ),
    # ── 5. 入口形状与身份（靶 V3GOALS／V3GO／ROUTES，闸 = httpapi 形状测）──
    (
        "HT1", "摘掉 DisallowUnknownFields ⇒ body 里的 user_id 被静默收下，"
               "ADR-002「身份只从 X-User-Id 进来」在这一面上失去可证形式",
        [(V3GOALS, NO_UNKNOWN_KEYS, f"{T}dec := json.NewDecoder(bytes.NewReader(raw))\n")],
        "go", PKG_API, H_IDENTITY,
    ),
    (
        "HT2", "注册表里漏掉 POST /internal/v3/goals ⇒ 六条少一条，而契约登记表**不知道**"
               "这一面应该有几条（这条红也是那条计数闸 9 的证人）",
        [(ROUTES, ROUTE_GOAL_CREATE, "")],
        "go", PKG_API, H_VANISH,
    ),
    (
        "HT3", "internalEnabled 不再看密钥 ⇒ 没配 GO_SERVICE_TOKEN 时这一面照样注册，"
               "「对外不存在」退化成「对外存在但会 403」",
        [(V3GO, ENABLED_WITH_TOKEN, f"{T}{T}s.v3.Goals != nil && s.v3.GoalWrites != nil\n")],
        "go", PKG_API, H_VANISH,
    ),
    (
        "HT4", "新建行动不再取 path 上的 goal_id ⇒ `/goals/{id}/actions` 那一格成了装饰，"
               "行动全都落在「没有目标」里",
        [(V3GOALS, ACTION_FROM_PATH,
          f"{T}p := domain.ActionProposal{{UserID: userID,\n"
          f"{T}{T}Title: body.Title, Description: body.Description}}\n")],
        "go", PKG_API, H_PATH,
    ),
    (
        "HT5", "行动列表的 truncated 恒 false ⇒ 截断这件事不再对调用方发言",
        [(V3GOALS, TRUNCATED_WIRE, TRUNCATED_WIRE.replace("view.Truncated", "false"))],
        "go", PKG_API, H_ACTION_LIST,
    ),
    (
        "HT6", "目标列表把没有的 target_at 也渲染成一个时间（直接取字段地址，绕开 optionalTime 的"
               "零值判断）⇒「假装有值」比空着更难归因，前端会显示一个 0001-01-01 的截止日",
        [(V3GOALS, TARGET_AT_WIRE, f"{T}{T}{T}TargetAt:    &g.TargetAt,\n")],
        "go", PKG_API, H_GOAL_LIST,
    ),
    (
        "HT7", "parseV3Verb 不再校验动词 ⇒ 未知动词一路走到领域层，400 那一格没人守，"
               "而这一面把坏请求当成合法入参记账",
        [(V3GOALS, VERB_CHECK, VERB_CHECK.replace("!verb.Valid()", "false"))],
        "go", PKG_API, H_VERB,
    ),
    (
        "EM1", "跨用户／不存在从 404 折成 409 ⇒ 工单 §验收点名的那一格变了码，"
               "而且调用方据此知道「这一颗存在但状态不对」，等于替旁人确认 id 为真",
        [(V3GOALS, MAP_OWNER_404, MAP_OWNER_404.replace("CodeResourceNotFound", "CodeConflict"))],
        "go", PKG_API, H_MAPPING,
    ),
    (
        "EM2", "非法转移从 409 折成 400 ⇒「请求本身合形、是这一颗的当前状态不允许」与"
               "「标题不合形」在客户端成了同一件事",
        [(V3GOALS, MAP_ILLEGAL_409, MAP_ILLEGAL_409.replace("CodeConflict", "CodeInvalidParams"))],
        "go", PKG_API, H_MAPPING,
    ),
    # ── 6. 真库面：NULL 与空串、cancel 的列、归属谓词（靶 GEN／REPO_GO／GOAL_READ）──
    (
        "DB1", "InsertGoal 把 description 绑成恒 NULL ⇒ 形状对、列数对，只有说明永远写不进去",
        [(GEN, GEN_INSERT_ARGS, GEN_INSERT_ARGS.replace("arg.Description,", "pgtype.Text{},"))],
        "go", PKG_STORE, S_ROUNDTRIP,
    ),
    (
        "DB2", "取消目标顺手写 completed_at ⇒ 04 §22 的完成度量凭空多一个分子，"
               "而 domain、读面与桩全都「正常」",
        [(GEN, GEN_CANCEL_GOAL, GEN_CANCEL_GOAL.replace(
            "SET status = 'cancelled', updated_at = now()",
            "SET status = 'cancelled', completed_at = now(), updated_at = now()"))],
        "go", PKG_STORE, S_CANCEL_GOAL,
    ),
    (
        "DB3", "取消行动顺手写 completed_at ⇒「取消」「跳过」「完成」三种收尾共用了一格时间",
        [(GEN, GEN_CANCEL_ACT, GEN_CANCEL_ACT.replace(
            "SET status = 'cancelled', updated_at = now()",
            "SET status = 'cancelled', completed_at = now(), updated_at = now()"))],
        "go", PKG_STORE, S_CANCEL_ACT,
    ),
    (
        "DB4", "ListGoals 摘掉 `user_id` 谓词 ⇒ 别人的目标进我的列表，而行的形状一模一样",
        [(GEN, GEN_LIST_GOALS, GEN_LIST_GOALS.replace(
            "WHERE user_id = $1 AND", "WHERE"))],
        "go", PKG_STORE, S_OWNER,
    ),
    (
        "DB5", "ListGoals 的空串过滤器退化成 `status = $2` ⇒「我的目标」默认回 0 行，"
               "刚完成的那颗直接从列表消失",
        [(GEN, GEN_LIST_GOALS, GEN_LIST_GOALS_FILTERED)],
        "go", PKG_STORE, S_LIST,
    ),
    (
        "DB6", "读面不再拒 limit<=0 ⇒「取 0 条」在 SQL 里是 LIMIT 0，静默回空集而不是报错",
        [(GOAL_READ, READ_LIMIT_GUARD, "")],
        "go", PKG_STORE, S_LIST,
    ),
    (
        "DB7", "读面把 description 折成恒空串 ⇒ 目标写了说明却永远读不回来",
        [(GOAL_READ, GEN_GOAL_READ_DESC, GEN_GOAL_READ_DESC.replace(
            "Description: row.Description.String", "Description: \"\""))],
        "go", PKG_STORE, S_ROUNDTRIP,
    ),
    (
        "DB8", "读面把 goal_id 折成恒空串 ⇒ 挂在目标下的行动在列表里全成了「没有目标」",
        [(GOAL_READ, GEN_GOAL_READ_GOALID, f"{T}{T}{T}GoalID:    \"\",\n")],
        "go", PKG_STORE, S_LINK,
    ),
    (
        "DB9", "仓储不再把 0 行折算成 ErrInvalidOwnership ⇒ 跨用户取消回「成功」，"
               "越权在这一面完全没有回声",
        [(REPO_GO, REPO_CANCEL_GOAL_OWNER,
          f"{T}_, err := t.q.CancelGoal(ctx, canonicaldb.CancelGoalParams{{ID: goalID, UserID: userID}})\n")],
        "go", PKG_STORE, S_OWNER,
    ),
    # ── 7. 两个真源的代价（靶 QUERIES，闸 = sqlc 对账；登记归 T6-03）──
    (
        "Q1", "只改不参与执行的 `db/queries/canonical.sql`（给 CancelGoal 加 completed_at）"
              "⇒ Go 一条测试都不红，必须靠 `sqlc diff` 这条对账闸兜住",
        [(QUERIES, SQL_CANCEL_GOAL, SQL_CANCEL_GOAL.replace(
            "SET status = 'cancelled', updated_at = now()",
            "SET status = 'cancelled', completed_at = now(), updated_at = now()"))],
        "pytest-sqlc", SQLC, "",
    ),
    # ── 8. BFF 那一跳（靶 ROUTER_PY／PROXY_PY，闸 = pytest；本票把 T3-03 欠的六条接上）──
    (
        "P1", "新建行动不再拨到 `/goals/{id}/actions` 而是拨到目标列表 ⇒ path 上的 goal_id 成了装饰，"
              "行动全都落在「没有目标」里，而 BFF 这边 200 照回",
        [(ROUTER_PY, CALL_ACTIONS,
          "    data = await v3_proxy.post_json(v3_proxy.PATH_GOAL_LIST, user_id, body=body)\n")],
        "pytest", P_SIX, P_SIX,
    ),
    (
        "P2", "patch_json 退化成 POST ⇒ 目标/行动的转移在内部面找不到路由（Go 只注册了 PATCH），"
              "用户点「完成」收到 404，而这一层把它原样透传成 40402",
        [(PROXY_PY, PATCH_FORWARD,
          '    return await forward("POST", path, user_id, payload=body)\n')],
        "pytest", P_SIX, P_SIX,
    ),
    (
        "P3", "goal 的 path 段不再转义 ⇒ `x?user_id=victim` 在 URL 解析阶段长出第二个 query，"
              "客户端第一次能替自己声明筛选条件（洞察面那条判据在目标面少钉了一遍）",
        [(PROXY_PY, GOAL_DETAIL_BUILD,
          "def goal_detail_path(goal_id: str) -> str:\n"
          "    return PATH_GOAL_DETAIL + goal_id\n")],
        "pytest", P_ESCAPE_G, P_ESCAPE_G,
    ),
    (
        "P4", "行动的 path 段不再转义 ⇒ 同一条判据的行动那一半；只测目标面就等于给"
              "`/actions/{id}` 留了一个没转义的洞",
        [(PROXY_PY, ACTION_DETAIL_BUILD,
          "def action_detail_path(action_id: str) -> str:\n"
          "    return PATH_ACTION_DETAIL + action_id\n")],
        "pytest", P_ESCAPE_A, P_ESCAPE_A,
    ),
    (
        "P5", "目标列表不再转发 status/limit ⇒ 过滤器静默失效，客户端以为自己筛过了而 Go 收到的是「无筛选」",
        [(ROUTER_PY, GOAL_LIST_CALL,
          "    data = await v3_proxy.get_json(v3_proxy.PATH_GOAL_LIST, user_id,\n"
          "                                   params=None)\n")],
        "pytest", P_FILTER, P_FILTER,
    ),
    (
        "P6", "BFF 提前把 body 里的 user_id 删掉 ⇒ 看着像好事，实际是把 Go 那条"
              "`DisallowUnknownFields` 的靶搬空了：ADR-002 的「身份只从 header 进来」从此没有证人",
        [(ROUTER_PY, RETURN_BODY,
          '    if isinstance(body, dict):\n        body.pop("user_id", None)\n'
          "    return body\n")],
        "pytest", P_VERBATIM, P_VERBATIM,
    ),
    (
        "P7", "`_unwrap` 不再按信封 code 报错而是照单回 data ⇒ Go 的 409/404/400 到客户端成了"
              "200＋data=null，工单 §验收那三格经过两跳之后一句都核不出来（前端把失败渲染成空列表）",
        [(PROXY_PY, UNWRAP_RAISE, "    return data\n")],
        "pytest", P_ERRORS, P_ERRORS,
    ),
    (
        "P8", "注册面少一条（整个 transition_action handler 被删）⇒ 六条变成五条，"
              "而契约登记表与九条 handler 的计数闸是这条唯一的证人",
        [(ROUTER_PY, ACTION_HANDLER_BLOCK, "")],
        "pytest", P_COUNT, P_COUNT,
    ),
    (
        "P9", "给行动列表加一种内部面根本没有的动词（DELETE）⇒ 客户端契约里多一条**打不通**的端点："
              "前端能调、Go 回 404，那是最难归因的死线",
        [(ROUTER_PY, ACTION_LIST_DECORATOR, '@router.delete("/actions")\n')],
        "pytest", P_COUNT, P_COUNT,
    ),
    (
        "P10", "某个 handler 没有身份入口（Depends 换成默认空串）⇒ 那一条端点匿名可达，"
               "而 BFF 会把空身份发进内部面",
        [(ROUTER_PY, ACTION_LIST_HEAD,
          "async def list_actions(status: str = None, limit: str = None,\n"
          '                       user_id: str = "") -> dict:\n')],
        "pytest", P_IDENTITY, P_IDENTITY,
    ),
    (
        "P11", "BFF 里长出一枚状态分支（`status == archived`）⇒ 第二事实源出生："
               "同一个筛选在 BFF 与 Go 有两种解释，而闸 G-03 只认逐字关键字",
        [(ROUTER_PY, ACTION_LIST_BODY,
          '    if status == "archived":\n        status = ""\n' + ACTION_LIST_BODY)],
        "pytest", THIN + "::test_router_source_has_no_business_judgement",
        THIN + "::test_router_source_has_no_business_judgement",
    ),
]

GO_COMMENTS = "".join(
    f"{T}// 绿闸注脚 {i:02d}：这一格判的是语义，不钉行号也不钉排版。\n" for i in range(1, 16)
)
SQL_COMMENTS = "".join(
    f"// 绿闸注脚 {i:02d}：生成物与源的对账看的是字面，不看注释密度。\n" for i in range(1, 16)
)
PY_COMMENTS = "".join(
    f"# 绿闸注脚 {i:02d}：这一格判的是语义，不钉行号也不钉排版。\n" for i in range(1, 16)
)

GREEN: list[tuple] = [
    (
        "GR4", "routers/v3.py 的 create_goal 上方插 15 行注释 ⇒ 九条 handler 的计数闸照旧绿"
               "（它钉的是装饰器与动词集合，不钉排版）",
        [(ROUTER_PY, '@router.post("/goals")\nasync def create_goal(',
          PY_COMMENTS + '@router.post("/goals")\nasync def create_goal(')],
        "pytest", P_COUNT, P_COUNT,
    ),
    (
        "GR5", "v3_proxy.py 的 patch_json 上方插 15 行注释 ⇒ 六条 path×方法的对账闸照旧绿",
        [(PROXY_PY, "async def patch_json(path: str, user_id: str, *, body: Any) -> Any:",
          PY_COMMENTS + "async def patch_json(path: str, user_id: str, *, body: Any) -> Any:")],
        "pytest", P_SIX, P_SIX,
    ),
    (
        "GR1", "service_goal.go 的 TransitionAction 上方插 15 行注释 ⇒ 幂等闸照旧绿",
        [(SVC, "func (s *Service) TransitionAction(ctx context.Context, userID, actionID string,\n",
          GO_COMMENTS + "func (s *Service) TransitionAction(ctx context.Context, userID, actionID string,\n")],
        "go", PKG_DOMAIN, GO_IDEM_ACT,
    ),
    (
        "GR2", "生成物里 listGoals 上方插 15 行注释 ⇒ 真库过滤器与 LIMIT 的闸照旧绿",
        [(GEN, "const listGoals = `-- name: ListGoals :many\n",
          SQL_COMMENTS + "const listGoals = `-- name: ListGoals :many\n")],
        "go", PKG_STORE, S_LIST,
    ),
    (
        "GR3", "v3_goals.go 的 handleV3GoalTransition 上方插 15 行注释 ⇒ 错误码映射闸照旧绿",
        [(V3GOALS, "func (s *Server) handleV3GoalTransition(w http.ResponseWriter, r *http.Request) {\n",
          GO_COMMENTS + "func (s *Server) handleV3GoalTransition(w http.ResponseWriter, r *http.Request) {\n")],
        "go", PKG_API, H_MAPPING,
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


def _verify_anchor(edits: list) -> str | None:
    for path, old, _new in edits:
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if n != 1:
            return f"锚点命中 {n} 次（要 1）：{old[:80]!r}"
        if path.suffix == ".py":
            try:
                ast.parse(path.read_text(encoding="utf-8").replace(old, _new, 1))
            except SyntaxError as exc:
                return f"变异体不是合法 Python：{exc}"
    return None


def run_pytest(node: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(VENV_PY), "-m", "pytest", node, "-q", "--no-header", "-p", "no:randomly"],
        cwd=REPO, capture_output=True, text=True, timeout=2400,
    )
    return proc.returncode, proc.stdout + proc.stderr


def run(kind: str, target: str) -> tuple[int, str]:
    if kind == "go":
        if not Path(GO_BIN).is_file():
            return -2, f"{GO_BIN} 不存在：Go 侧用例没跑，不算绿"
        if target == PKG_STORE and not os.environ.get(PG_ENV):
            return -2, f"{PG_ENV} 未设置：store 集成测会整包 skip，不算绿也不算红"
        proc = subprocess.run([GO_BIN, "test", target, "-count=1", "-timeout", "300s"],
                              cwd=GO_CORE, env=GO_ENV, capture_output=True, text=True, timeout=1800)
        return proc.returncode, proc.stdout + proc.stderr
    if kind == "pytest-sqlc":
        if not VENV_PY.is_file():
            return -2, f"解释器不存在：{VENV_PY}"
        if shutil.which("sqlc") is None:
            # sqlc 不在 PATH 时那条闸自己 pytest.skip（退出码仍是 0），
            # 不拦下来就会把「没跑」念成「基线绿」。
            return -2, "sqlc 不在 PATH：源与生成物的对账闸没跑，不算绿"
        return run_pytest(target)
    if kind == "pytest":
        if not VENV_PY.is_file():
            return -2, f"解释器不存在：{VENV_PY}"
        return run_pytest(target)
    return -2, f"未知的闸类型 {kind}"


def report_name(marker: str) -> str:
    name = marker[1:] if marker.startswith("^") else marker
    return name[:-1] if name.endswith("$") else name


def is_red(code: int, out: str, kind: str, target: str, marker: str) -> bool:
    """「红」＝**那条判据自己**失败；编译坏掉、没匹配到用例、skip、收集期报错都不算。"""
    if code == 0:
        return False
    if kind == "go":
        if "[build failed]" in out or "no tests to run" in out or "no test files" in out:
            return False
        if "--- SKIP:" in out and "--- FAIL:" not in out:
            return False
        return f"--- FAIL: {report_name(marker)}" in out
    if "no tests ran" in out:
        return False
    if f"ERROR {target}" in out:
        return False
    return f"FAILED {target}" in out


BASELINE: dict[tuple[str, str, str], tuple[int, str]] = {}


def baseline(kind: str, target: str, marker: str) -> tuple[int, str]:
    key = (kind, target, marker)
    if key not in BASELINE:
        BASELINE[key] = run(kind, target)
    return BASELINE[key]


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
        print(f"锚点校验：{'全部命中一次且变异体合法' if bad == 0 else f'{bad} 条有问题'}（共 {len(cases)} 条）")
        return 1 if bad else 0

    if not VENV_PY.is_file():
        print(f"解释器不存在：{VENV_PY}（变异自证要求先备好 .venv）")
        return 2
    if not os.environ.get(PG_ENV):
        print(f"提示：{PG_ENV} 未设置，store 那一侧的靶全部记「未跑」，不算绿")

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-t402-") as tmp:
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
            for cid, desc, edits, kind, target, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(kind, target, marker)
                if base_code == -2:
                    unrun.append(cid)
                    print(f"[{cid}] 未跑（{base_out}）：缺前置不是绿，也不把它的红记在闸头上")
                    continue
                if base_code != 0:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 未跑（基线本来就不绿，exit={base_code}）："
                          "不能把环境的红记成闸的红\n  " + "\n  ".join(base_out.splitlines()[-8:]))
                    continue
                try:
                    _apply(edits)
                    code, out = run(kind, target)
                finally:
                    restore()
                if code != 0 and kind == "go" and "[build failed]" in out:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（变异体编译不过，红的是工具链不是判据）：\n  "
                          + "\n  ".join(out.splitlines()[-10:]))
                    continue
                if is_red(code, out, kind, target, marker):
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗  {desc}\n  " + "\n  ".join(out.splitlines()[-14:]))

            for cid, desc, edits, kind, target, marker in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(kind, target, marker)
                if base_code != 0:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（基线就不绿，exit={base_code}）：{desc}")
                    continue
                try:
                    _apply(edits)
                    code, out = run(kind, target)
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 被误伤 ✗  {desc}\n  " + "\n  ".join(out.splitlines()[-14:]))
        finally:
            restore()
            residue = [p for p in paths if not filecmp.cmp(originals[p], p, shallow=False)]
            print(f"\n残留复核：{'0 个靶文件与原件逐字节一致' if not residue else ''}")
            if residue:
                print("  以下靶文件恢复后仍不一致：" + "、".join(str(p) for p in residue))

        total_red = red_ok + red_fail
        total_green = green_ok + green_fail
        print(f"\n汇总：红闸 {red_ok}/{total_red}｜绿闸 {green_ok}/{total_green}｜"
              f"未跑 {len(unrun)}{'（' + ','.join(unrun) + '）' if unrun else ''}")
        if failures:
            print("失败条目：" + ",".join(failures))
        if unrun:
            print("ⓘ 有靶未跑：缺 env／缺工具链一律不算绿")
        return 1 if (red_fail or green_fail or unrun) else 0


if __name__ == "__main__":
    sys.exit(main())
