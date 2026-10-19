#!/usr/bin/env python3
"""T4-03（Go：`POST /internal/v3/actions/{id}/outcomes` ＋ BFF 那一跳）的变异自证（2026-10-19）。

靶文件八个（Go 六＋Python 二），红闸 40 条、绿闸 5 条。

工单 §验收四句，每句都有**它自己的**靶与红闸（[[feedback-mutation-anchor-must-reach-target]]）：

1. 「四种 result 各一条」⇒ D6 打在缺省归一化那一格（六值全收＋空串成 unknown），
   D7 打在词表校验（`!p.Type.Valid()` 摘掉⇒未知 outcome_type 照样记账）；
   HT4/HT5 打在 HTTP 折叠（400 那两格换成 500/409 就只有那一条会红）；
2. 「非法 outcome_type 400」⇒ D7（domain 不判词表）、D8（`OutcomeTargetRequirement`
   把目标级结果的链接要求改成承诺⇒「挂在行动上的目标结果」写得出来了）、
   HT3（请求体结构体长出 `user_id` 一格⇒身份从 body 进来）、
   HT1（备注上限差一格⇒一次粘贴写进权威表）；
3. 「跨用户 404」⇒ DB3（SQL 摘掉 `l.user_id` 谓词）、DB6（`ErrNoRows` 认错哨兵）、
   OW（domain 不再 TrimSpace 就把空目标送进提案）——三种坏法各自的红点不同；
4. 「`outcomes` + `outcome_links` 两行同事务」⇒ DB7（回链不带归属 ⇒ 真库当场回滚）
   与 D2（targets 钉成 goal ⇒ 回链那一格从根上换掉），只有真库能答第二行有没有落地。

另有 §怎么做.5 的同日幂等：D3（幂等整格摘掉）、D4（不看日期⇒跨日也吞）、
D5（不折 UTC⇒按写入方时区各算一天）、DB5（读面说「没查到」）、
DB1/DB2/DB4（最近一条的排序键、`outcome_type` 谓词、`target_id` 谓词）——
**同一格判据在两侧各钉一次**：domain 用桩钉「判定」，真库钉「读面 SQL 给得出最近一条」。

**P1…P11 钉的是 BFF 那一跳**（靶 `backend/routers/v3.py`／`backend/services/v3_proxy.py`）：
内部 path 逐字（P1）、path 段转义（P2）、body 不拆包（P3/P5）、方法不退化（P4）、
handler 计数与动词集合（P6/P7）、身份入口（P8）、对外路径改名的**两侧**各一条（P9 打实时生成的
客户端契约、P10 打已提交的 `contracts/openapi.json` 快照）、代理不读业务配置（P11）。

**第一轮 37/39 抓到两条「红点没落在点名判据上」，两条都是判据自己的问题（已修，写进结案行）**：
① DB2 是**等价变异**——不是变异写错，是那条真库测把「同一条行动上的另一种 outcome_type」
种到了**别的 target** 上，于是 `target_id` 谓词替 `outcome_type` 谓词挡了一切，摘掉后者照样全绿；
证人必须站在被证的那一格里，所以把那一行改种到 `c0(recent)` 这条**同一**行动、时刻更晚，
DB2 才第一次把 `TestT403LatestOutcomeForTargetPicksMostRecentSameType` 打红。
② P9 原先钉 `test_no_unregistered_or_stale_endpoint`，而那条闸的两边（`endpoints.md` 与
`contracts/openapi.json`）**都是已提交产物**：改 router 的路径字符串两边一起重导就照样绿，
它是「表与快照」的对账，不是「代码与快照」的对账。于是拆成 P9（实时 `app.openapi()` 的契约面）
＋P10（已提交快照的漂移面）两条，各打各的闸。

Q1 打在**不参与执行**的那一侧（`db/queries/canonical.sql`）：改它一条 Go 测试都不会红，
只有 `sqlc diff` 会红——这是 T4-02 就登记过的「源与产物两个真源」的现行代价，仍归 T6-03。

一条**刻意不纳入**的等价变异（留档以免下轮重猜）：摘掉 `internalEnabled()` 里
`s.v3.OutcomeWrites != nil` 之外的那四格、以及把 `v3OutcomePayload` 的字段名改成
与 domain 同名——前者已由 HT8 逐格钉住，后者两侧同源（生产结构体反解自己的输出恒真），
所以响应形状在测里**重述**了一遍（`v3OutcomePayloadOf`）。

纪律：串行执行（[[feedback-mutation-gates-no-concurrency]]）、copy2 备份＋`filecmp(shallow=False)`
逐字节写回、不用 git checkout（[[feedback-mutation-restore-no-git-checkout]]）；每条红闸先跑基线，
基线本来就不绿或前置缺失一律记「未跑」（[[feedback-gate-needs-positive-witness]]）；
Go 侧 `--- SKIP` 与 `[build failed]`、pytest 侧 `ERROR <节点>` 与 `no tests ran` 都**不算打红**——
那些红的是环境，不是判据。

用法（store 那一侧需要真库，缺 env 的全部记「未跑」不算绿）：
    set -a && . /tmp/t206_env.sh && set +a
    GOROOT=/Users/fengyu/sdk/go1.27.1 ./.venv/bin/python scripts/mutate_t403_gates.py --check
    GOROOT=/Users/fengyu/sdk/go1.27.1 ./.venv/bin/python scripts/mutate_t403_gates.py
    ./.venv/bin/python scripts/mutate_t403_gates.py --only D5,HT8,DB1,P2
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

AO = GO_CORE / "internal" / "domain" / "action_outcome.go"
OUTCOME = GO_CORE / "internal" / "domain" / "outcome.go"
V3OUT = GO_CORE / "internal" / "httpapi" / "v3_outcomes.go"
ROUTES = GO_CORE / "internal" / "httpapi" / "routes.go"
V3GO = GO_CORE / "internal" / "httpapi" / "v3.go"
QUERIES = GO_CORE / "db" / "queries" / "canonical.sql"
GEN = GO_CORE / "internal" / "store" / "canonicaldb" / "canonical.sql.go"
REPO_GO = GO_CORE / "internal" / "store" / "canonical_repo.go"
ROUTER_PY = REPO / "backend" / "routers" / "v3.py"
PROXY_PY = REPO / "backend" / "services" / "v3_proxy.py"

SCHEMA = "backend/tests/test_canonical_schema.py"
SQLC = f"{SCHEMA}::test_sqlc_generated_code_matches_queries"

PKG_DOMAIN = "./internal/domain/"
PKG_API = "./internal/httpapi/"
PKG_STORE = "./internal/store/"
T = "\t"

# 点名的判据（每条红闸必须把**它自己**那一条打红）。
D_ORIGIN = "^TestRecordActionOutcomePinsOriginTargetAndVersion$"
D_BLANK = "^TestRecordActionOutcomeRejectsBlankIdentityAndTarget$"
D_RESULTS = "^TestRecordActionOutcomeAcceptsEveryResultAndDefaultsToUnknown$"
D_VOCAB = "^TestRecordActionOutcomeRejectsUnknownVocabulary$"
D_KINDS = "^TestRecordActionOutcomeRefusesTargetsOfAnotherKind$"
D_DONE = "^TestRecordActionOutcomeCompletesTheCommitmentItDescribes$"
D_SAMEDAY = "^TestRecordActionOutcomeSameDaySubmitReturnsFirstResult$"
D_NEXTDAY = "^TestRecordActionOutcomeNextDayWritesASecondRow$"
D_CARRIED = "^TestRecordActionOutcomeKeepsOccurredAtAndOperationID$"
D_UTCDAY = "^TestSameUTCDateComparesDaysNotInstants$"

H_ROUTE = "^TestV3OutcomeRouteExistsAndIdentityComesOnlyFromHeader$"
H_CARRY = "^TestV3OutcomeCarriesPathIdOccurredAtAndNote$"
H_BODY = "^TestV3OutcomeRejectsBadBodiesBeforeTheDomain$"
H_MAP = "^TestV3OutcomeErrorMapping$"
H_DUP = "^TestV3OutcomeSameDayDuplicateKeepsFirstResult$"
H_SLOT = "^TestV3SurfaceVanishesWhenAnySingleDependencyIsMissing$"

S_BOTH = "^TestT403ActionOutcomeWritesBothRowsAndCompletesAction$"
S_DAY = "^TestT403SameDayRepeatWritesNoSecondRowButNextDayDoes$"
S_RECENT = "^TestT403LatestOutcomeForTargetPicksMostRecentSameType$"
S_TIE = "^TestT403LatestOutcomeForTargetTieBreaksOnID$"
S_SCOPE = "^TestT403LatestOutcomeForTargetIsUserScoped$"

PROXY = "backend/tests/test_v3_outcomes_proxy.py"
THIN = "backend/tests/test_bff_is_thin.py"
FREEZE = "backend/tests/test_contract_freeze.py"
P_DIAL = f"{PROXY}::test_outcome_route_dials_the_internal_path_verbatim"
P_ESCAPE = f"{PROXY}::test_action_path_segment_is_escaped_on_outcome_face"
P_VERBATIM = f"{PROXY}::test_body_is_forwarded_verbatim_including_identity_and_origin"
P_COUNT = f"{THIN}::test_forwarded_surface_has_ten_handlers_and_no_extra_verbs"
P_IDENTITY = f"{THIN}::test_identity_enters_only_from_the_auth_dependency"
P_KEYS = f"{THIN}::test_proxy_reads_only_its_own_two_config_keys"
P_CONTRACT = f"{THIN}::test_client_contract_exposes_the_forwarded_paths_and_no_internal"
P_BASELINE = f"{FREEZE}::test_openapi_baseline_is_current"

# ── 锚点（逐字取自被测文件，命中数必须恰好 1）────────────────────────────────
PIN_ORIGIN = f"{T}{T}Origin:      OriginUserStatement,\n"
PIN_TARGETS = f"{T}{T}Targets:     []TargetRef" + "{{Type: RefCommitment, ID: actionID}},\n"
SAME_DAY_IF = f"{T}if latest.Found && sameUTCDate(latest.CreatedAt, s.now()) {{\n"
UTC_SPLIT = f"{T}x, y := a.UTC(), b.UTC()\n"
RESULT_DEFAULT = f"{T}{T}result = OutcomeResultUnknown\n"
ACTION_TRIM = f"{T}actionID := strings.TrimSpace(p.ActionID)\n"
RECEIPT_WRITE = f"{T}{T}{T}Changed: true, ModelVersion: inner.ModelVersion, OperationID: p.OperationID,\n"
OCCURRED_WIRE = f"{T}{T}OccurredAt:  p.OccurredAt.UTC(),\n"
NOTE_WIRE = f"{T}{T}Description: p.Note,\n"
TYPE_VALID = f"{T}if !p.Type.Valid() {{\n"
TARGET_REQ = (
    f"{T}case OutcomeGoalProgress, OutcomeGoalCompleted:\n"
    f"{T}{T}return RefGoal, true\n"
)
COMPLETES = f"{T}return t == OutcomeGoalCompleted || t == OutcomeCommitmentCompleted\n"

NOTE_BOUND = "if utf8.RuneCountInString(note) > maxOutcomeNote {"
PATH_ID_FROM_ROUTE = f'{T}{T}ActionID:    r.PathValue("id"),\n'
REQ_STRUCT_HEAD = (
    "type v3OutcomeRequest struct {\n"
    f'{T}OutcomeType string     `json:"outcome_type"`\n'
)
MAP_PROPOSAL_400 = (
    f'{T}{T}return apierr.New(apierr.CodeInvalidParams,\n'
    f'{T}{T}{T}"结果类型或取值不合形 / Invalid outcome_type or result")\n'
)
MAP_OWNER_404 = (
    f'{T}{T}return apierr.New(apierr.CodeResourceNotFound, "资源不存在 / Resource not found")\n'
)
CHANGED_WIRE = f"{T}{T}Status: receipt.Status, Changed: receipt.Changed,\n"
ROUTE_OUTCOME = (
    f'{{method: "POST", path: "/internal/v3/actions/{{id}}/outcomes",\n'
    f"{T}{T}gated:   func(s *Server) bool {{ return s.internalEnabled() }},\n"
    f"{T}{T}handler: func(s *Server) http.HandlerFunc {{ return s.handleV3ActionOutcome }}}},\n"
)
ENABLED_WITH_OUTCOME_WRITES = (
    f"{T}{T}s.v3.Goals != nil && s.v3.GoalWrites != nil && s.v3.OutcomeWrites != nil &&\n"
)

GEN_ORDER = "ORDER BY o.created_at DESC, o.id DESC"
GEN_WHERE = ("WHERE l.user_id = $1 AND l.target_type = $2 AND l.target_id = $3 "
             "AND o.outcome_type = $4")
REPO_NO_ROWS = f"{T}if errors.Is(err, pgx.ErrNoRows) {{\n{T}{T}// 查不到同型历史"
REPO_FOUND = f"{T}{T}CreatedAt: row.CreatedAt.Time, Found: true}}, nil\n"
REPO_LINK_USER = f"{T}{T}{T}TargetID: target.ID, UserID: userID}}); err != nil {{\n"
SQL_ORDER = "ORDER BY o.created_at DESC, o.id DESC"

# ── BFF 那一侧的锚点（靶 ROUTER_PY／PROXY_PY，闸 = pytest）──
PATH_OUTCOME_CONST = 'PATH_ACTION_OUTCOME = "/internal/v3/actions/{}/outcomes"\n'
OUTCOME_PATH_BUILD = (
    "def action_outcome_path(action_id: str) -> str:\n"
    '    return PATH_ACTION_OUTCOME.format(quote(action_id, safe=""))\n'
)
OUTCOME_CALL = (
    "    body = await _json_body(request)\n"
    "    data = await v3_proxy.post_json(v3_proxy.action_outcome_path(action_id), user_id,\n"
    "                                    body=body)\n"
)
OUTCOME_DECORATOR = '@router.post("/actions/{action_id}/outcomes")\n'
OUTCOME_HANDLER_BLOCK = (
    '@router.post("/actions/{action_id}/outcomes")\n'
    "async def record_action_outcome(action_id: str, request: Request,\n"
    "                                user_id: str = Depends(get_current_user)) -> dict:\n"
    "    # 与九条同口径：**不收 Pydantic 模型**。outcome_type 的九类、result 的六值、\n"
    "    # 同日幂等与「这种 type 不许挂在行动上」全在 Go 的 domain，这里一旦建模，\n"
    "    # 工单 §验收 要的「非法 outcome_type 400」就会在 BFF 先变成 422。\n"
    "    body = await _json_body(request)\n"
    "    data = await v3_proxy.post_json(v3_proxy.action_outcome_path(action_id), user_id,\n"
    "                                    body=body)\n"
    "    return success(data)\n"
)
OUTCOME_HEAD = (
    "async def record_action_outcome(action_id: str, request: Request,\n"
    "                                user_id: str = Depends(get_current_user)) -> dict:\n"
)
BUILD_HEADERS_HEAD = "    token = settings.go_service_token.strip()\n"

MUTATIONS: list[tuple] = [
    # ── 1. 领域：提案的三颗钉子（靶 AO，闸 = domain 单测）──
    (
        "D1", "origin 从 `OriginUserStatement` 换成 `OriginLLMInference` ⇒ 这条用户自述落 "
              "candidate、不再 ++模型版本：O-5 那格「模型相信什么」被一次点击决定，"
              "而客户端看不见差别（status 那一格才被工单点名要回）",
        [(AO, PIN_ORIGIN, PIN_ORIGIN.replace("OriginUserStatement", "OriginLLMInference"))],
        "go", PKG_DOMAIN, D_ORIGIN,
    ),
    (
        "D2", "targets 钉成 `RefGoal` ⇒ 承诺的回链变成了目标回链：§验收 那句「outcomes + "
              "outcome_links 两行」写出来了，但挂的是**另一个对象**，04 §22 的目标完成度量凭空多分子",
        [(AO, PIN_TARGETS,
          PIN_TARGETS.replace("Type: RefCommitment", "Type: RefGoal"))],
        "go", PKG_DOMAIN, D_ORIGIN,
    ),
    (
        "D3", "同日幂等整格摘掉（`latest.Found && false`）⇒ 连点两下写出两行、顶两次版本，"
              "§怎么做.5 那句「返回首次结果」成了空话",
        [(AO, SAME_DAY_IF, SAME_DAY_IF.replace("sameUTCDate(latest.CreatedAt, s.now())", "false"))],
        "go", PKG_DOMAIN, D_SAMEDAY,
    ),
    (
        "D4", "只看「查得到同型历史」而不看日期 ⇒ 同日幂等被写成「这条行动一辈子只能有一次结果」，"
              "跨日的第二次观测被吞掉，按天记账的度量少一行",
        [(AO, SAME_DAY_IF, f"{T}if latest.Found {{\n")],
        "go", PKG_DOMAIN, D_NEXTDAY,
    ),
    (
        "D5", "同日判定不再折 UTC ⇒ 判据读到写入方时区的日历日：同一次连点在 UTC 与 UTC+8 "
              "各算一天（legacy_batch5.go:71 那条勘误的同一根因）",
        [(AO, UTC_SPLIT, f"{T}x, y := a, b\n")],
        "go", PKG_DOMAIN, D_UTCDAY,
    ),
    (
        "D6", "缺省 result 不再归一化成 unknown ⇒ 空 result 从「说不清」变成 400，"
              "而工单 §验收 要的四值里那一格是合法输入（02:182-189）",
        [(AO, RESULT_DEFAULT, f'{T}{T}result = ""\n')],
        "go", PKG_DOMAIN, D_RESULTS,
    ),
    (
        "D7", "`checkOutcomeProposal` 不再判 `outcome_type` 是否在九类词表里 ⇒ "
              "§验收 点名的「非法 outcome_type 400」失守，`astrology_reading` 一路写进权威表",
        [(OUTCOME, TYPE_VALID, f"{T}if false {{\n")],
        "go", PKG_DOMAIN, D_VOCAB,
    ),
    (
        "D8", "把目标级结果的链接要求改成「挂承诺」（`OutcomeTargetRequirement` 的 goal 那一格）"
              "⇒ §怎么做.3 点名的匹配判据失守：goal_progress 现在能挂在一条行动上，"
              "而这正是「挂在行动上却描述目标」的那一行",
        [(OUTCOME, TARGET_REQ, TARGET_REQ.replace("return RefGoal, true",
                                                  "return RefCommitment, true"))],
        "go", PKG_DOMAIN, D_KINDS,
    ),
    (
        "D9", "`OutcomeCompletesTarget` 不再认 commitment 级 ⇒ 结果记了、完成没落地："
              "02:824 要求完成时刻写进真正使用的那张表，而这里只留下 outcomes 里的一行影子",
        [(OUTCOME, COMPLETES, f"{T}return t == OutcomeGoalCompleted\n")],
        "go", PKG_DOMAIN, D_DONE,
    ),
    (
        "D10", "action_id 不再 TrimSpace ⇒ 空白 id 以「这条行动不存在」（404）的形式回来，"
               "而不是「请求不合形」（400）；越权与坏输入在客户端成了同一句话",
        [(AO, ACTION_TRIM, f"{T}actionID := p.ActionID\n")],
        "go", PKG_DOMAIN, D_BLANK,
    ),
    (
        "D11", "回执不回 operation_id ⇒ 客户端没法把一次重试对上自己那次点击，"
               "只能靠再点一次看 `changed` 猜（工单 §怎么做.5 点名的那一格）",
        [(AO, RECEIPT_WRITE, RECEIPT_WRITE.replace("OperationID: p.OperationID",
                                                   'OperationID: ""'))],
        "go", PKG_DOMAIN, D_CARRIED,
    ),
    (
        "D12", "occurred_at 不再带进提案（钉成零值）⇒ 按天记账读的是写入时刻而不是用户说的那一天，"
               "跨时区补记会算错日",
        [(AO, OCCURRED_WIRE, f"{T}{T}OccurredAt:  time.Time{{}},\n")],
        "go", PKG_DOMAIN, D_CARRIED,
    ),
    (
        "D13", "备注没落到 description ⇒「写了却没存下来」，而这一格在屏上是用户唯一自己写的东西",
        [(AO, NOTE_WIRE, f'{T}{T}Description: "",\n')],
        "go", PKG_DOMAIN, D_ORIGIN,
    ),
    # ── 2. 入口形状与注册面（靶 V3OUT／ROUTES／V3GO，闸 = httpapi 形状测）──
    (
        "HT1", "备注上限差一格（`> maxOutcomeNote + 1`）⇒ 501 字的粘贴进了权威表，"
               "而 500 与 501 在用户是同一个输入框",
        [(V3OUT, NOTE_BOUND, NOTE_BOUND.replace("> maxOutcomeNote {", "> maxOutcomeNote + 1 {"))],
        "go", PKG_API, H_BODY,
    ),
    (
        "HT2", "handler 读的 path 参数名与注册表不同批（`{id}` → `{actionId}`）⇒ "
               "action_id 成了空串：path 那一格是装饰，结果全记到「不存在」上",
        [(V3OUT, PATH_ID_FROM_ROUTE, PATH_ID_FROM_ROUTE.replace('PathValue("id")',
                                                                 'PathValue("actionId")'))],
        "go", PKG_API, H_CARRY,
    ),
    (
        "HT3", "请求体结构体长出 `user_id` 一格 ⇒ ADR-002「身份只从 X-User-Id 进来」在这一面上"
               "失去可证形式：客户端自报身份不再被 400 拒掉",
        [(V3OUT, REQ_STRUCT_HEAD, REQ_STRUCT_HEAD +
          f'{T}UserID      string     `json:"user_id"`\n')],
        "go", PKG_API, H_ROUTE,
    ),
    (
        "HT4", "「提案不合形」从 400 折成 500 ⇒ §验收 点名的「非法 outcome_type 400」变了码，"
               "前端只会说「服务挂了」而用户其实写错了参数",
        [(V3OUT, MAP_PROPOSAL_400, MAP_PROPOSAL_400.replace("CodeInvalidParams",
                                                             "CodeInternalError"))],
        "go", PKG_API, H_MAP,
    ),
    (
        "HT5", "跨用户／不存在从 404 折成 409 ⇒ 调用方据此知道「这一颗存在但状态不对」，"
               "等于替旁人确认 id 为真（反枚举那一格失守）",
        [(V3OUT, MAP_OWNER_404, MAP_OWNER_404.replace("CodeResourceNotFound", "CodeConflict"))],
        "go", PKG_API, H_MAP,
    ),
    (
        "HT6", "回执的 `changed` 恒 true ⇒「今天已经记过了」在客户端渲染成「新记了一条」，"
               "同日幂等这一格在屏上被抹掉（而库里只有一行）",
        [(V3OUT, CHANGED_WIRE, CHANGED_WIRE.replace("receipt.Changed", "true"))],
        "go", PKG_API, H_DUP,
    ),
    (
        "HT7", "注册表里漏掉这一条 ⇒ 十条变九条，而契约登记表**不知道**这一面应该有几条："
               "这一格红的是「这一面从来没有它」（404 兜底），不是「它坏了」",
        [(ROUTES, ROUTE_OUTCOME, "")],
        "go", PKG_API, H_ROUTE,
    ),
    (
        "HT8", "`internalEnabled()` 不再看 `OutcomeWrites` 这一格 ⇒ 装配漏了结果写入时"
               "这一面照样注册，用户在第一次写入时收到 500 而不是「对外根本不存在」"
               "（T4-02 结案行登记的那笔欠账，本轮逐格钉住）",
        [(V3GO, ENABLED_WITH_OUTCOME_WRITES, ENABLED_WITH_OUTCOME_WRITES.replace(
            " && s.v3.OutcomeWrites != nil", ""))],
        "go", PKG_API, H_SLOT,
    ),
    # ── 3. 真库面：读面 SQL 的排序键与谓词（靶 GEN／REPO_GO，闸 = 真 PG 集成测）──
    (
        "DB1", "`ORDER BY created_at DESC, id DESC` 的次级键反号 ⇒ 同一时刻落两行时 LIMIT 1 "
               "给的是**早**那条：重放与批量补记里同日幂等时灵时不灵",
        [(GEN, GEN_ORDER, "ORDER BY o.created_at DESC, o.id ASC")],
        "go", PKG_STORE, S_TIE,
    ),
    (
        "DB2", "`outcome_type` 谓词写成恒真 ⇒ 同一条行动上的**另一种**结果被当成今天的重复提交，"
               "该写的一行被吞掉（behavior_change 挡掉了 commitment_completed）",
        [(GEN, GEN_WHERE, GEN_WHERE.replace(
            "AND o.outcome_type = $4",
            "AND ($4::text IS NOT NULL OR o.outcome_type = $4)"))],
        "go", PKG_STORE, S_RECENT,
    ),
    (
        "DB3", "摘掉 `l.user_id` 谓词 ⇒ 别人的那条结果进我的判定，同日幂等**吞掉**我这一行："
               "用户看到「我记了，但屏幕上什么也没变」",
        [(GEN, GEN_WHERE, GEN_WHERE.replace("WHERE l.user_id = $1 AND", "WHERE"))],
        "go", PKG_STORE, S_SCOPE,
    ),
    (
        "DB4", "`target_id` 谓词写成恒真 ⇒「这一条行动」的最近一条变成了「这个人的最近一条」，"
               "两条行动的结果互相顶掉",
        [(GEN, GEN_WHERE, GEN_WHERE.replace(
            "AND l.target_id = $3",
            "AND ($3::text IS NOT NULL OR l.target_id = $3)"))],
        "go", PKG_STORE, S_RECENT,
    ),
    (
        "DB5", "读面把「查不到」说成 `Found=false` 之后领域再也不会幂等 ⇒ 第一次表态这一格"
               "判定输入丢了（与 DB6 两种坏法：这里是谎报没有，那里是把没有当故障）",
        [(REPO_GO, REPO_FOUND, REPO_FOUND.replace("Found: true", "Found: false"))],
        "go", PKG_STORE, S_DAY,
    ),
    (
        "DB6", "`ErrNoRows` 认错哨兵（换成 `ErrTxClosed`）⇒「第一次表态」被当成存储故障："
               "每个新用户的第一次记结果都 500",
        [(REPO_GO, REPO_NO_ROWS, REPO_NO_ROWS.replace("pgx.ErrNoRows", "pgx.ErrTxClosed"))],
        "go", PKG_STORE, S_DAY,
    ),
    (
        "DB7", "回链不带走归属 ⇒ 列上 NOT NULL 当场报错、整笔事务回滚："
               "§验收 那句「两行同事务」的**原子性**只有真库能证（桩里 append 两次永远一起成功）",
        [(REPO_GO, REPO_LINK_USER, REPO_LINK_USER.replace("UserID: userID", 'UserID: ""'))],
        "go", PKG_STORE, S_BOTH,
    ),
    # ── 4. 两个真源的代价（靶 QUERIES，闸 = sqlc 对账；登记归 T6-03）──
    (
        "Q1", "只改不参与执行的 `db/queries/canonical.sql`（次级键反号）⇒ Go 一条测试都不红，"
              "必须靠 `sqlc diff` 这条对账闸兜住",
        [(QUERIES, SQL_ORDER, "ORDER BY o.created_at DESC, o.id ASC")],
        "pytest-sqlc", SQLC, "",
    ),
    # ── 5. BFF 那一跳（靶 ROUTER_PY／PROXY_PY，闸 = pytest）──
    (
        "P1", "内部 path 常量少了一格（`/outcomes` → `/outcome`）⇒ Go 那侧没有这条路由，"
              "用户点「结果如何」收到 404，而 BFF 把它原样透传成 40402",
        [(PROXY_PY, PATH_OUTCOME_CONST,
          'PATH_ACTION_OUTCOME = "/internal/v3/actions/{}/outcome"\n')],
        "pytest", P_DIAL, P_DIAL,
    ),
    (
        "P2", "结果面的 path 段不再转义 ⇒ `x?user_id=victim` 在 URL 解析阶段长出第二个 query，"
              "客户端第一次能替自己声明身份（洞察面／目标面那条判据在结果面少钉了一遍）",
        [(PROXY_PY, OUTCOME_PATH_BUILD,
          "def action_outcome_path(action_id: str) -> str:\n"
          "    return PATH_ACTION_OUTCOME.format(action_id)\n")],
        "pytest", P_ESCAPE, P_ESCAPE,
    ),
    (
        "P3", "BFF 提前把 body 里的 `user_id`／`origin` 删掉 ⇒ 看着像好事，实际是把 Go 那条"
              "`DisallowUnknownFields` 的靶搬空了：`origin` 决定「算不算模型相信的事」（O-5），"
              "这一格必须由 domain 钉死而不是由转发层「顺手补干净」",
        [(ROUTER_PY, OUTCOME_CALL,
          "    body = await _json_body(request)\n"
          "    if isinstance(body, dict):\n"
          '        body.pop("user_id", None)\n'
          '        body.pop("origin", None)\n'
          "    data = await v3_proxy.post_json(v3_proxy.action_outcome_path(action_id), user_id,\n"
          "                                    body=body)\n")],
        "pytest", P_VERBATIM, P_VERBATIM,
    ),
    (
        "P4", "结果面从 POST 退化成 GET ⇒ 内部面这一格只注册了 POST（405/404），"
              "而且 body 根本进不去：一次记结果什么都没记",
        [(ROUTER_PY, OUTCOME_CALL,
          "    body = await _json_body(request)\n"
          "    data = await v3_proxy.get_json(v3_proxy.action_outcome_path(action_id), user_id,\n"
          "                                    params=None)\n"
          "    _ = body\n")],
        "pytest", P_DIAL, P_DIAL,
    ),
    (
        "P5", "转发时丢掉请求体（`body=None`）⇒ Go 收到空提案：400 是「不合形」而不是「没权限」，"
              "用户写的那句话从来没离开过 BFF",
        [(ROUTER_PY, OUTCOME_CALL, OUTCOME_CALL.replace("body=body)", "body=None)"))],
        "pytest", P_VERBATIM, P_VERBATIM,
    ),
    (
        "P6", "整条 handler 被删 ⇒ 十条变九条，而契约登记表与十条 handler 的计数闸是这条唯一的证人",
        [(ROUTER_PY, OUTCOME_HANDLER_BLOCK, "")],
        "pytest", P_COUNT, P_COUNT,
    ),
    (
        "P7", "给结果面换一种内部面根本没有的动词（PUT）⇒ 客户端契约里多一条**打不通**的端点："
              "前端能调、Go 回 404，那是最难归因的死线",
        [(ROUTER_PY, OUTCOME_DECORATOR, '@router.put("/actions/{action_id}/outcomes")\n')],
        "pytest", P_COUNT, P_COUNT,
    ),
    (
        "P8", "结果面的 handler 没有身份入口（`Depends` 换成默认空串）⇒ 那一条端点匿名可达，"
              "而 BFF 会把空身份发进内部面",
        [(ROUTER_PY, OUTCOME_HEAD,
          "async def record_action_outcome(action_id: str, request: Request,\n"
          '                                user_id: str = "") -> dict:\n')],
        "pytest", P_IDENTITY, P_IDENTITY,
    ),
    (
        "P9", "对外路径改名（`/outcomes` → `/outcome`）而客户端契约不知道 ⇒ 前端按老路径调，"
              "BFF 上根本没有这一条（404），工单 §怎么做.1 点名的路径逐字消费失效",
        [(ROUTER_PY, OUTCOME_DECORATOR, '@router.post("/actions/{action_id}/outcome")\n')],
        "pytest", P_CONTRACT, P_CONTRACT,
    ),
    (
        "P10", "同一条改名打**另一侧**的闸：`contracts/openapi.json` 是快照，路由改了而快照没重导 ⇒ "
               "N/O 口径与契约基线分家（这一格红的是 `test_openapi_baseline_is_current`，"
               "**不是**登记表与快照的逐条对账——那两个都是**已提交**的产物，同源 ⇒ 一起重导就照样绿）",
        [(ROUTER_PY, OUTCOME_DECORATOR, '@router.post("/actions/{action_id}/outcome")\n')],
        "pytest", P_BASELINE, P_BASELINE,
    ),
    (
        "P11", "代理开始读业务配置（额度键）⇒ 转发层有了「不问 Go 自己决定」的能力，"
               "而那条能力一旦写下来就不会再被删掉（闸 G-03 的两个键白名单）",
        [(PROXY_PY, BUILD_HEADERS_HEAD,
          BUILD_HEADERS_HEAD + "    _ = settings.outcome_daily_quota\n")],
        "pytest", P_KEYS, P_KEYS,
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
        "GR1", "action_outcome.go 的 sameUTCDate 上方插 15 行注释 ⇒ origin/targets 的钉子照旧绿"
               "（它钉的是语义，不钉排版）",
        [(AO, "func sameUTCDate(a time.Time, b time.Time) bool {\n",
          GO_COMMENTS + "func sameUTCDate(a time.Time, b time.Time) bool {\n")],
        "go", PKG_DOMAIN, D_ORIGIN,
    ),
    (
        "GR2", "v3_outcomes.go 的 v3OutcomeError 上方插 15 行注释 ⇒ 错误码映射闸照旧绿",
        [(V3OUT, "func v3OutcomeError(err error) *apierr.Error {\n",
          GO_COMMENTS + "func v3OutcomeError(err error) *apierr.Error {\n")],
        "go", PKG_API, H_MAP,
    ),
    (
        "GR3", "生成物里 latestOutcomeForTarget 上方插 15 行注释 ⇒ 真库读面的排序闸照旧绿",
        [(GEN, "const latestOutcomeForTarget = `-- name: LatestOutcomeForTarget :one\n",
          SQL_COMMENTS + "const latestOutcomeForTarget = `-- name: LatestOutcomeForTarget :one\n")],
        "go", PKG_STORE, S_RECENT,
    ),
    (
        "GR4", "routers/v3.py 的结果面 handler 上方插 15 行注释 ⇒ 十条 handler 的计数闸照旧绿",
        [(ROUTER_PY, OUTCOME_DECORATOR, PY_COMMENTS + OUTCOME_DECORATOR)],
        "pytest", P_COUNT, P_COUNT,
    ),
    (
        "GR5", "v3_proxy.py 的 action_outcome_path 上方插 15 行注释 ⇒ 内部 path 逐字对账闸照旧绿",
        [(PROXY_PY, "def action_outcome_path(action_id: str) -> str:\n",
          PY_COMMENTS + "def action_outcome_path(action_id: str) -> str:\n")],
        "pytest", P_DIAL, P_DIAL,
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
    with tempfile.TemporaryDirectory(prefix="mutate-t403-") as tmp:
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
