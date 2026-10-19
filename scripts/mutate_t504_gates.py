#!/usr/bin/env python3
"""T5-04（Go：`GET /internal/v3/proactives` ＋ CLI ＋ 真库谓词）的变异自证（2026-10-20）。

靶文件十一个（Go 九＋SQL 源一个＋Python 一个），红闸 54 条、绿闸 6 条。

工单 §验收 两句 + §怎么做 三格，每句都有**它自己的**靶与红闸
（[[feedback-mutation-anchor-must-reach-target]]）：

1. 「能看到 pending 超期任务」⇒ 这一句里 `pending` 那一格是**改判**（库里没有这一态），
   所以靶打在真身那三态上：G3（SQL 少一态）、G4（`sent_at IS NULL` 摘掉）、
   G5（过期比较反号）、G8（`NOT $3::boolean` 反置）、D11（Go 登记的三态与 SQL 源文本分叉）——
   五条各打掉一种「超期」的坏法，红点全在 `TestAuditStalePredicateSelectsExactlyTheStuckRows`
   或 `TestStaleVocabularyMatchesTheSQLPredicate` 本身。
2. 「与最近发送记录」⇒ S3（`sent_at` 读成 `scheduled_at`）、G1（归属谓词摘掉）、
   G6（排序反号）、G7（次级排序键）、S1（LIMIT 少一格）、S4（journey 接反）。
3. 「跨用户查询必须显式带 user_id 且鉴权为服务级」⇒ H1（白名单放进 `user_id`）、
   H2（白名单整格摘掉）、S/G1（SQL 少 user_id 谓词）、D1（领域不再拦空身份）、
   C1（CLI 把身份拼进 query）、C2（CLI 的密钥头空掉）。
4. 「不进 BFF、不暴露给客户端」⇒ B1（给 BFF 加一条 `/proactives` 转发 ⇒ 十条 handler 的
   计数闸红）、H13（内部面长出第二条同形路由 ⇒ 11 条计数闸红）、
   H10（这一条从注册表消失 ⇒ 逐格注册闸的正向证人红）。

**为什么 CLI 那一族也进靶面**：这一面的唯一消费方就是它（工单 §怎么做.3）。
身份走哪儿、密钥从哪儿来、产物那一行纯不纯，全在 CLI 侧决定，
服务端拦得住的前提是客户端不先把它拼错——C1…C11 钉的是这条。

**两个真源的现行代价（Q1／V1）**：`db/queries/canonical.sql` 不参与执行，
改它一条 Go 测试都不会红，只有 `sqlc diff`（Q1）与 domain 那条「词表与 SQL 源文本对账」（V1）
兜得住；同一条破坏在两处各钉一次，是因为它们是**两条不同的判据**
（产物漂移 vs 口径分叉），归 T6-03 的账本票继续挂着。

一条**刻意不纳入**的等价变异（留档以免下轮重猜）：摘掉
`store.ListProactiveAuditRecords` 里 `limit <= 0` 那一格守卫——`domain` 永远把 limit 收敛到
≥1 后才调它，两个调用方（HTTP、CLI）都过 domain，所以「0 条」这一格在真库里不可观测；
要钉它得让 domain 之外直接拨仓储，那与分层纪律相反。

纪律：串行执行（[[feedback-mutation-gates-no-concurrency]]）、copy2 备份＋`filecmp(shallow=False)`
逐字节写回、不用 git checkout（[[feedback-mutation-restore-no-git-checkout]]）；每条红闸先跑基线，
基线本来就不绿或前置缺失一律记「未跑」（[[feedback-gate-needs-positive-witness]]）；
Go 侧 `--- SKIP` 与 `[build failed]`、pytest 侧 `ERROR <节点>` 与 `no tests ran` 都**不算打红**——
那些红的是环境，不是判据。

**第一轮 44/52，四条「红点没落在点名判据上」＋四条编译期翻车，处置如下（留档）**：

- D8（默认页 50→20）恒绿：那条测的 `want` 侧写的就是 `defaultProactiveAuditLimit` 常量自己 ⇒
  两侧同源、恒真（[[feedback-orm-identity-map-gate]]）。判据改成字面量 50/200 后有了靶。
- H7（`optionalTime(row.SentAt, true)`）恒绿：helper 内部本来就判 `IsZero` ⇒ 等价变异；
  变异体改成 `&row.SentAt` 绕开它（与 T4-02 的 HT6 同一课）。
- C3（不判信封 code）恒绿：判据的夹具只有 403/404/HTML/连不上四档，全都是 HTTP 非 200，
  「HTTP 200 而信封 code 500」那一格没人测 ⇒ 给 `TestAuditFailsLoudlyOnNonOKResponse` 补了那条夹具。
- G4（摘 `sent_at IS NULL`）恒绿：状态白名单已经挡住了 sent 行，库里没有「状态没推进但已写出
  sent_at」的半途行 ⇒ 给真库夹具补了那一行，这一格从此才有可观测的坏法。
- D10／H2／W1／C3 第一版都是 `if false` 型撤法，Go 报「变量未使用」＝红的是编译器；
  分别改成 `|| true`／`&& false`／撤两格／`{ _ = apierr.CodeOK`。
- G2（单边 `status = $2`）红在**前面那条**顺序测的 panic 上（进程崩了，点名判据根本不跑）⇒
  store 那一族支持 `包|测名` 作用域，这一条单独拨一次。

用法（store 那一侧需要真库，缺 env 的全部记「未跑」不算绿）：
    set -a && . /tmp/t206_env.sh && set +a
    GOROOT=/Users/fengyu/sdk/go1.27.1 ./.venv/bin/python scripts/mutate_t504_gates.py --check
    GOROOT=/Users/fengyu/sdk/go1.27.1 ./.venv/bin/python scripts/mutate_t504_gates.py
    ./.venv/bin/python scripts/mutate_t504_gates.py --only D1,H13,B1
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

PA = GO_CORE / "internal" / "domain" / "proactive_audit.go"
ST = GO_CORE / "internal" / "store" / "proactive_audit_read.go"
HV = GO_CORE / "internal" / "httpapi" / "v3_proactives.go"
ROUTES = GO_CORE / "internal" / "httpapi" / "routes.go"
V3GO = GO_CORE / "internal" / "httpapi" / "v3.go"
MAIN = GO_CORE / "cmd" / "go-api" / "main.go"
SURFACE = GO_CORE / "tools" / "v3surface" / "main.go"
QUERIES = GO_CORE / "db" / "queries" / "canonical.sql"
GEN = GO_CORE / "internal" / "store" / "canonicaldb" / "canonical.sql.go"
CLI = GO_CORE / "tools" / "proactiveaudit" / "main.go"
ROUTER_PY = REPO / "backend" / "routers" / "v3.py"

SCHEMA = "backend/tests/test_canonical_schema.py"
SQLC = f"{SCHEMA}::test_sqlc_generated_code_matches_queries"

PKG_DOMAIN = "./internal/domain/"
PKG_API = "./internal/httpapi/"
PKG_STORE = "./internal/store/"
PKG_MAIN = "./cmd/go-api/"
PKG_SURFACE = "./tools/v3surface/"
PKG_CLI = "./tools/proactiveaudit/"
T = "\t"

# ── 点名的判据（每条红闸必须把**它自己**那一条打红）────────────────────────────
D_USER = "^TestProactiveAuditRefusesToQueryWithoutUser$"
D_CLAMP = "^TestProactiveAuditLimitClampAndOverfetch$"
D_TRUNC = "^TestProactiveAuditReportsTruncation$"
D_ECHO = "^TestProactiveAuditPassesFiltersThroughAndEchoesThem$"
D_VOCAB = "^TestIsProactiveStatusFollowsTheEightStateVocabulary$"
D_SQLVOCAB = "^TestStaleVocabularyMatchesTheSQLPredicate$"

H_GETONLY = "^TestV3ProactiveAuditIsGetOnly$"
H_IDENTITY = "^TestV3ProactiveAuditIdentityComesOnlyFromHeader$"
H_QUERY = "^TestV3ProactiveAuditQueryParameterShape$"
H_FIELDS = "^TestV3ProactiveAuditFieldNamesAndNulls$"
H_TRUNC = "^TestV3ProactiveAuditReportsTruncation$"
H_ERR = "^TestV3ProactiveAuditSurfacesReaderErrors$"
H_SLOT = "^TestV3SurfaceVanishesWhenAnySingleDependencyIsMissing$"
H_WITNESS = "^TestV3SurfaceRegistersAllElevenRoutesWhenComplete$"
H_COUNT = "^TestInternalRoutesAreAbsentFromClientContract$"

S_STALE = "^TestAuditStalePredicateSelectsExactlyTheStuckRows$"
S_SCOPE = "^TestAuditReadScopesToUserKeepsNullsAndOrders$"
S_STATUS = "^TestAuditStatusFilterKeepsBothDirections$"
S_LIMIT = "^TestAuditOverfetchRowBecomesTruncated$"

W_MAIN = "^TestMainWiresTheInternalSurfaceToTheSameDomainService$"
V_REGISTERED = "^TestFixtureSurfaceRegistersEveryDeclaredInternalRoute$"
V_AUDIT_ROWS = "^TestFixtureAuditSurfaceServesTwoRows$"

C_HEAD = "^TestAuditClientSendsIdentityOnlyInHeaders$"
C_REPORT = "^TestAuditReportEchoesAppliedFiltersAndCounts$"
C_ENV = "^TestAuditRefusesToRunWithoutEnvAndMakesNoRequest$"
C_FAIL = "^TestAuditFailsLoudlyOnNonOKResponse$"
C_FLAGS = "^TestAuditRejectsBadFlagsBeforeAnyRequest$"
C_HINT = "^TestAuditHintsOnUnknownStatusWithoutChangingTheVerdict$"

THIN = "backend/tests/test_bff_is_thin.py"
B_COUNT = f"{THIN}::test_forwarded_surface_has_ten_handlers_and_no_extra_verbs"

# ── 锚点（逐字取自被测文件，命中数必须恰好 1）────────────────────────────────
PA_USER = f"{T}if strings.TrimSpace(userID) == \"\" {{\n"
PA_OVERFETCH = f"{T}{T}status, limit+1, stale)\n"
PA_TRUNC_IF = f"{T}if len(rows) > limit {{\n"
PA_SLICE = f"{T}{T}view.Items = append([]ProactiveAuditRecord(nil), rows[:limit]...)\n"
PA_ECHO = f"{T}{T}AppliedStatus: status, AppliedStale: stale}}\n"
PA_DEFAULT = f"{T}defaultProactiveAuditLimit = 50\n"
PA_CLAMP_MAX = (
    f"{T}if limit > maxProactiveAuditLimit {{\n"
    f"{T}{T}return maxProactiveAuditLimit\n"
    f"{T}}}\n"
)
PA_ISSTATUS = f"{T}{T}if string(s) == status {{\n"
PA_STALELIST = 'var ProactiveAuditStaleStatuses = []string{"candidate", "eligible", "scheduled"}\n'

ST_PARAMS = f"{T}{T}UserID: userID, Status: status, Stale: stale, RowLimit: int32(limit)}})\n"
ST_TIMES = (
    f"{T}{T}{T}ScheduledAt: timeOf(row.ScheduledAt), SentAt: timeOf(row.SentAt),\n"
)
ST_REASONS = (
    f"{T}{T}{T}BlockReason: row.BlockReason.String, RejectReason: row.RejectReason.String,\n"
)
ST_JOURNEY = f"{T}{T}{T}JourneyEventID: row.JourneyEventID.String,\n"

HV_KEYS = 'var v3AuditQueryKeys = map[string]bool{"status": true, "limit": true, "stale": true}\n'
HV_ALLOWLIST = f"{T}{T}if !v3AuditQueryKeys[key] {{\n"
HV_STALE_400 = (
    f"{T}{T}return false, apierr.New(apierr.CodeInvalidParams,\n"
    f'{T}{T}{T}"stale 只能是 true 或 false / stale must be true or false")\n'
)
HV_LIMIT_ECHO = f"{T}{T}Items: items, Truncated: view.Truncated, Limit: view.Limit,\n"
HV_BLOCK_TAG = f'{T}BlockReason    *string    `json:"block_reason"`\n'
HV_STRUCT_HEAD = f"type v3ProactiveItemWire struct {{\n{T}ID             string     `json:\"id\"`\n"
HV_SENT_NULL = f"{T}{T}SentAt:         optionalTime(row.SentAt, !row.SentAt.IsZero()),\n"
HV_ERR_SWALLOW = (
    f"{T}if err != nil {{\n"
    f"{T}{T}// 审计面没有「别人的行」这一格可泄：user_id 由 domain 一路带到 SQL，"
)

RT_AUDIT_ROUTE = (
    f'{{method: "GET", path: "/internal/v3/proactives",\n'
    f"{T}{T}gated:   func(s *Server) bool {{ return s.internalEnabled() }},\n"
    f"{T}{T}handler: func(s *Server) http.HandlerFunc {{ return s.handleV3ProactiveAudit }}}},\n"
)
RT_AUDIT_METHOD = f'{{method: "GET", path: "/internal/v3/proactives",\n'
RT_EXTRA_ROUTE = (
    RT_AUDIT_ROUTE +
    f'{{method: "DELETE", path: "/internal/v3/proactives",\n'
    f"{T}{T}gated:   func(s *Server) bool {{ return s.internalEnabled() }},\n"
    f"{T}{T}handler: func(s *Server) http.HandlerFunc {{ return s.handleV3ProactiveAudit }}}},\n"
)
V3_ENABLED_CELL = f"{T}{T}s.v3.Proactives != nil && s.cfg.GoServiceToken != \"\"\n"

MAIN_WIRE = f"{T}{T}Proactives: proactiveAudit}}\n"
MAIN_BROWSER = f"{T}proactiveAudit := domain.NewProactiveAuditBrowser(canonical)\n"
SURFACE_WIRE = f"{T}{T}{T}Proactives:    domain.NewProactiveAuditBrowser(fixtureProactiveAudit{{}})}}"

# 生成物里那段**真正被执行**的 SQL（两侧不同源：这一份是产物，QU 那一格是口径）
# 注意：`WHERE user_id = $1` 与那条 status 谓词在**别的面**里各自还出现过，
# 所以这一族的锚点一律取「user_id 换行 + 两个空格 + status 谓词」那一整格（唯一）。
GEN_USER = "WHERE user_id = $1\n  AND ($2::varchar = '' OR status = $2)"
GEN_STALE_ON = "AND (NOT $3::boolean"
GEN_STALE_SENT = "OR (sent_at IS NULL\n           AND status IN ('candidate', 'eligible', 'scheduled')"
GEN_STALE_DUE = "(scheduled_at < now() OR expires_at < now())"
GEN_ORDER = "ORDER BY created_at DESC, id\nLIMIT $4"
GEN_TIEBREAK = "ORDER BY created_at DESC, id\n"

QU_STALE_STAT = "       AND status IN ('candidate', 'eligible', 'scheduled')"
QU_ORDER = "ORDER BY created_at DESC, id\nLIMIT sqlc.arg(row_limit);"

CLI_QUERY_BUILDER = f"{T}q := url.Values{{}}\n"
CLI_TOKEN_HEADER = f'{T}req.Header.Set("X-Service-Token", token)\n'
CLI_CODE_CHECK = f"{T}if resp.StatusCode != http.StatusOK || env.Code != apierr.CodeOK {{\n"
CLI_TRUNC_WIRE = f"{T}{T}Truncated: body.Truncated,\n"
CLI_HINT_STDERR = f'{T}{T}fmt.Fprintf(stderr, "proactiveaudit: 提示：--status %q 不在八态词表里（%s）"'
CLI_NO_URL = f'{T}{T}return errors.New("GO_API_URL 未设置 ⇒ 本工具一条都没查，别把它当绿")\n'
CLI_NO_TOKEN = (
    f'{T}{T}return errors.New("GO_SERVICE_TOKEN 未设置 ⇒ 这一面唯一的鉴权口是空的，" +\n'
    f'{T}{T}{T}"服务端会整面不注册（404）而不是回你一片空列表")\n'
)
CLI_USER_REQUIRED = f'{T}if strings.TrimSpace(*user) == "" {{\n'
CLI_LIMIT_NEG = f"{T}if *limit < 0 {{\n"
CLI_EXTRA_ARG = f"{T}if len(fs.Args()) > 0 {{\n"

ROUTER_OUTCOME_DECORATOR = '@router.post("/actions/{action_id}/outcomes")\n'
ROUTER_TAIL = (
    "    body = await _json_body(request)\n"
    "    data = await v3_proxy.post_json(v3_proxy.action_outcome_path(action_id), user_id,\n"
    "                                    body=body)\n"
    "    return success(data)\n"
)
BFF_ADDED_HANDLER = ROUTER_TAIL + (
    "\n\n# 绿闸之外的反面靶：这一面**不许**进 BFF（工单 §怎么做.2）。\n"
    '@router.get("/proactives")\n'
    "async def list_proactives(user_id: str = Depends(get_current_user)) -> dict:\n"
    "    return success(None)\n"
)

MUTATIONS: list[tuple] = [
    # ── 1. 领域：身份、取数口径、裁剪、回显、词表（靶 PA，闸 = domain 单测）──
    (
        "D1", "`List` 不再拦空身份 ⇒ 空列表在审计面上的意思是「这个人没有触达」，"
              "而实情是「没人告诉我要查谁」：值班替一个不存在的人排除故障",
        [(PA, PA_USER, f"{T}if false {{\n")],
        "go", PKG_DOMAIN, D_USER,
    ),
    (
        "D2", "取数不再 `limit+1` ⇒ 「页外还有行」这件事永远看不出来，"
              "审计会安静地少报（工单 §验收 要的正是「少没少报」）",
        [(PA, PA_OVERFETCH, f"{T}{T}status, limit, stale)\n")],
        "go", PKG_DOMAIN, D_CLAMP,
    ),
    (
        "D3", "`stale` 不透传给仓储（恒 false）⇒ 客户端拨了超期过滤器，库侧查的是全量：" +
              "「pending 超期任务」那一格从来没被实现过",
        [(PA, PA_OVERFETCH, f"{T}{T}status, limit+1, false)\n")],
        "go", PKG_DOMAIN, D_ECHO,
    ),
    (
        "D4", "截断判定差一格（`> limit+1`）⇒ 恰好多取那一行时不报 truncated，"
              "调用方以为自己拿到了全部",
        [(PA, PA_TRUNC_IF, f"{T}if len(rows) > limit+1 {{\n")],
        "go", PKG_DOMAIN, D_TRUNC,
    ),
    (
        "D5", "截断时不裁行（把仓储那一片整个交出去）⇒ `limit` 变成装饰："
              "响应里有 limit+1 条而回显说 3",
        [(PA, PA_SLICE, "")],
        "go", PKG_DOMAIN, D_TRUNC,
    ),
    (
        "D6", "截断时就地切片（与仓储那片共享底层数组）⇒ 调用方改视图会改到源数据，"
              "一次审计能把行改成别人的",
        [(PA, PA_SLICE, f"{T}{T}view.Items = rows[:limit]\n")],
        "go", PKG_DOMAIN, D_TRUNC,
    ),
    (
        "D7", "不回显 `status` ⇒ 「拼错一个状态」与「这个人真的一条触达都没有」在响应里同一张脸"
              "（这一面不校验词表，代价就是这一格回显）",
        [(PA, PA_ECHO, f"{T}{T}AppliedStatus: \"\", AppliedStale: stale}}\n")],
        "go", PKG_DOMAIN, D_ECHO,
    ),
    (
        "D8", "默认页大小 50→20 ⇒ 审计一次要看的是「这一周排了几封、卡在哪几封」，"
              "20 条刚好截断在最能说明问题的那批之前，而且回显与取数一起变小没人发现",
        [(PA, PA_DEFAULT, f"{T}defaultProactiveAuditLimit = 20\n")],
        "go", PKG_DOMAIN, D_CLAMP,
    ),
    (
        "D9", "上限不再收敛 ⇒ 一次误拨把整张表拉进内存（这一面的调用方是运维，"
              "手滑的代价是 4c8G 那台单机）",
        [(PA, PA_CLAMP_MAX, "")],
        "go", PKG_DOMAIN, D_CLAMP,
    ),
    (
        "D10", "`IsProactiveStatus` 恒真 ⇒ CLI 的词表提示永远不说「你过滤了一个不存在的状态」，"
               "零行被读成「这个人没有触达」（变异体写成 `== status || true` 而不是 `if true`，"
               "后者会让循环变量未使用 ⇒ 红的是编译器不是判据）",
        [(PA, PA_ISSTATUS, f"{T}{T}if string(s) == status || true {{\n")],
        "go", PKG_DOMAIN, D_VOCAB,
    ),
    (
        "D11", "Go 登记的超期三态少一态（去掉 `scheduled`）⇒ 与 SQL 源文本分叉："
               "两侧各说一套，而这一面的「超期」正是那一条谓词",
        [(PA, PA_STALELIST,
          'var ProactiveAuditStaleStatuses = []string{"candidate", "eligible"}\n')],
        "go", PKG_DOMAIN, D_SQLVOCAB,
    ),
    # ── 2. HTTP 入口：门禁、参数形状、字段名、null 语义（靶 HV，闸 = httpapi 形状测）──
    (
        "H1", "查询白名单放进 `user_id` ⇒ ADR-002「身份只从 X-User-Id 进来」在这一面上失去可证形式："
              "值班以为自己查的是 victim，屏幕上其实是 owner 的触达",
        [(HV, HV_KEYS,
          'var v3AuditQueryKeys = map[string]bool' +
          '{"status": true, "limit": true, "stale": true, "user_id": true}\n')],
        "go", PKG_API, H_IDENTITY,
    ),
    (
        "H2", "白名单整格失效（任何参数都放行）⇒ 拼错的 `stal=1` 被静默忽略："
              "运维拿着全量列表以为自己看的是超期队列。变异体写 `&& false` 而不是 `if false`，"
              "后者会让 `key` 未使用 ⇒ 红的是编译器不是判据",
        [(HV, HV_ALLOWLIST, f"{T}{T}if !v3AuditQueryKeys[key] && false {{\n")],
        "go", PKG_API, H_QUERY,
    ),
    (
        "H3", "`stale=yes` 悄悄读成 false（不再 400）⇒ 与 H2 同一件坏事的另一半："
              "过滤器失效而不报错，是审计面最坏的一种输出",
        [(HV, HV_STALE_400, f"{T}{T}return false, nil\n")],
        "go", PKG_API, H_QUERY,
    ),
    (
        "H4", "`limit` 回显请求值而不是生效值 ⇒ 传 5000 的人被截到 200 却不知道，"
              "他会以为「200 条就是这个人的全部触达」",
        [(HV, HV_LIMIT_ECHO,
          f"{T}{T}Items: items, Truncated: view.Truncated, Limit: limit,\n")],
        "go", PKG_API, H_QUERY,
    ),
    (
        "H5", "`block_reason` 改名 `skip_reason`（工单 §怎么做.1 的字面词）⇒ 台账登记的改判被推翻："
              "库里从来是两格（block／reject）而不是一格，而按名取值的脚本静默拿到 undefined",
        [(HV, HV_BLOCK_TAG, f'{T}BlockReason    *string    `json:"skip_reason"`\n')],
        "go", PKG_API, H_FIELDS,
    ),
    (
        "H6", "投影里长出 `message` 一格 ⇒ 每一次排障都多端走一份用户内容"
              "（工单 §怎么做.2「仅供运维/客服」不是「把正文也带给运维」）",
        [(HV, HV_STRUCT_HEAD,
          HV_STRUCT_HEAD + f'{T}Message        *string    `json:"message"`\n')],
        "go", PKG_API, H_FIELDS,
    ),
    (
        "H7", "NULL 的 `sent_at` 折成零值时间 ⇒ 审计面上凭空多出一条 1970 年的发送记录"
              "（T3-04 的 U3 打的就是「缺数据显示成断言」这种坏法）。"
              "**第二轮改的是变异体**：第一版写 `optionalTime(row.SentAt, true)` 恒绿——"
              "那个 helper 自己就判 `IsZero`，所以它是等价变异；换成直接取字段地址才绕开它",
        [(HV, HV_SENT_NULL, f"{T}{T}SentAt:         &row.SentAt,\n")],
        "go", PKG_API, H_FIELDS,
    ),
    (
        "H8", "`truncated` 恒 false ⇒ 「后面还有」这一格从响应里消失，运维白翻页，"
              "而少报的东西永远没人知道自己少看了",
        [(HV, HV_LIMIT_ECHO,
          f"{T}{T}Items: items, Truncated: false, Limit: view.Limit,\n")],
        "go", PKG_API, H_TRUNC,
    ),
    (
        "H9", "仓储报错被折成「200＋空列表」⇒ 库挂了与「这个人没有触达」在响应里同形，"
              "而这一面唯一的产出就是那两句话的区别",
        [(HV, HV_ERR_SWALLOW, f"{T}if false {{\n{T}{T}// 审计面没有「别人的行」这一格可泄：")],
        "go", PKG_API, H_ERR,
    ),
    # ── 3. 注册面与装配（靶 ROUTES／V3GO／MAIN，闸 = 逐格注册测＋main 装配测）──
    (
        "H10", "这一条从 routeTable 里消失 ⇒ 审计面缺了入口，而各面各自的用例看不见它："
               "「摘掉一格不可观察」正是 T4-02 登记过的那笔欠账",
        [(ROUTES, RT_AUDIT_ROUTE, "")],
        "go", PKG_API, H_WITNESS,
    ),
    (
        "H11", "这一条从 GET 写成 POST ⇒ 只读面长出了写形状，而 §怎么做.1 那句「只读」"
               "唯一的可证形式就是「GET 打得通、写方法一个都到不了仓储」",
        [(ROUTES, RT_AUDIT_METHOD, f'{{method: "POST", path: "/internal/v3/proactives",\n')],
        "go", PKG_API, H_GETONLY,
    ),
    (
        "H12", "`internalEnabled()` 不再看 `Proactives` 这一格 ⇒ 装配漏了审计面时这一面照样注册，"
               "运维在第一次查询时收到 500 而不是「对外根本不存在」",
        [(V3GO, V3_ENABLED_CELL, f'{T}{T}s.cfg.GoServiceToken != ""\n')],
        "go", PKG_API, H_SLOT,
    ),
    (
        "H13", "内部面长出第二条同形路由（DELETE）⇒ 声明表 11 条变 12 条，"
               "而计数闸是唯一知道「这一面应该有几条」的地方",
        [(ROUTES, RT_AUDIT_ROUTE, RT_EXTRA_ROUTE)],
        "go", PKG_API, H_COUNT,
    ),
    (
        "W1", "main.go 忘了装配 `Proactives` 那一格 ⇒ 整面 404，而 httpapi／domain 两侧的用例"
              "照样全绿（T4-02 那次翻车的同一形状，这一格换成审计面再钉一次）。"
              "两格一起改（撤装配语句＋撤那行构造）才是「忘了接」的真身；只撤一格的话 "
              "`proactiveAudit` 未使用 ⇒ 红的是编译器不是判据",
        [(MAIN, MAIN_WIRE, f"{T}{T}Proactives: nil}}\n"),
         (MAIN, MAIN_BROWSER, "")],
        "go", PKG_MAIN, W_MAIN,
    ),
    (
        "W2", "装配给了一个 nil 读面 ⇒ 这一格存在但没有任何仓储："
              "错误形状从「没注册（404）」变成运行期 500，而装配测是唯一看得见这一格的地方",
        [(MAIN, MAIN_BROWSER,
          f"{T}proactiveAudit := domain.NewProactiveAuditBrowser(nil)\n")],
        "go", PKG_MAIN, W_MAIN,
    ),
    (
        "W3", "**跨进程夹具**忘了装配 `Proactives` 那一格 ⇒ 六格 AND 少一格＝整个 /internal/v3 面"
              "不注册，红的是 Python 那 20 条跨进程测，而归因第一次一定指向 BFF。"
              "T4-02 为这件事翻过一次车、T5-04 补审计面时又断了一次，所以这一格换成夹具侧的靶",
        [(SURFACE, SURFACE_WIRE,
          f"{T}{T}{T}Proactives:    nil}}")],
        "go", PKG_SURFACE, V_REGISTERED,
    ),
    (
        "W4", "夹具装配上了一颗**空仓储**（NewProactiveAuditBrowser(nil)）⇒ 上一测照样绿"
              "（AND 只看非 nil 的那一格是 Browser 指针，不是 reader），"
              "但审计面一拨就 500：这一格钉的是「装配上了还得有内容」",
        [(SURFACE, SURFACE_WIRE,
          f"{T}{T}{T}Proactives:    domain.NewProactiveAuditBrowser(nil)}}")],
        "go", PKG_SURFACE, V_AUDIT_ROWS,
    ),
    # ── 4. 仓储搬行列（靶 ST，闸 = 真 PG 集成测）──
    (
        "S1", "`LIMIT` 少一格（`limit-1`）⇒ 领域多取的那一行本来只为回答「还有没有」，"
              "现在直接把数据少取一条，truncated 还照报 true",
        [(ST, ST_PARAMS,
          f"{T}{T}UserID: userID, Status: status, Stale: stale, RowLimit: int32(limit - 1)}})\n")],
        "go", PKG_STORE, S_LIMIT,
    ),
    (
        "S2", "`sent_at` 读成 `scheduled_at` ⇒ 「最近发送记录」那一半验收失真："
              "排期了没发的行被读成发过，超期谓词又恰好把它排除在「卡住」之外",
        [(ST, ST_TIMES,
          f"{T}{T}{T}ScheduledAt: timeOf(row.ScheduledAt), SentAt: timeOf(row.ScheduledAt),\n")],
        "go", PKG_STORE, S_SCOPE,
    ),
    (
        "S3", "两格原因列接反（block ⇄ reject）⇒ 工单那句 `skip_reason` 的改判在这一层被抹平："
              "「被挡（还没发）」与「被拒（不该再发）」在审计面上又成了一格",
        [(ST, ST_REASONS,
          f"{T}{T}{T}BlockReason: row.RejectReason.String, RejectReason: row.BlockReason.String,\n")],
        "go", PKG_STORE, S_STATUS,
    ),
    (
        "S4", "`journey_event_id` 读成 `id` ⇒ 回链动线那一格变成自我复读："
              "12 列按位置搬进结构体时接反的正是这种「两格都非空」的位置",
        [(ST, ST_JOURNEY, f"{T}{T}{T}JourneyEventID: row.ID,\n")],
        "go", PKG_STORE, S_SCOPE,
    ),
    # ── 5. 生成物里那段真正被执行的 SQL（靶 GEN，闸 = 真 PG 集成测）──
    (
        "G1", "`WHERE user_id = $1` 的归属谓词摘掉（换成恒真的 `$1 <> ''`）⇒ 别人的触达端给这个"
              "用户：这一面的调用方是运维/客服，那条谎会直接变成一次错误的排障",
        [(GEN, GEN_USER, "WHERE $1 <> ''\n  AND ($2::varchar = '' OR status = $2)")],
        "go", PKG_STORE, S_SCOPE,
    ),
    (
        "G2", "status 谓词写成单边（`status = $2`）⇒ 不带过滤器时查的是「状态等于空串」："
              "审计面上的「全部触达」永远是零行，而这长得像「这个人真的没有触达」。"
              "这一条单独跑（`包|测名`）：同包那条顺序测先撞上零行会 panic，"
              "进程一崩点名的判据根本不跑——红点必须在它自己身上",
        [(GEN, GEN_USER, "WHERE user_id = $1\n  AND status = $2")],
        "go", f"{PKG_STORE}|TestAuditStatusFilterKeepsBothDirections", S_STATUS,
    ),
    (
        "G3", "超期三态少一态（去掉 `candidate`）⇒ 卡在最初那一态的触达从审计里消失，"
              "而那正是 worker 从没跑起来时唯一的形状",
        [(GEN, GEN_STALE_SENT,
          "OR (sent_at IS NULL\n           AND status IN ('eligible', 'scheduled')")],
        "go", PKG_STORE, S_STALE,
    ),
    (
        "G4", "摘掉 `sent_at IS NULL` ⇒ 已发送的行也被算进「超期」：排障的人第一眼看的是"
              "「卡住的一排」，而其中一半早就发出去了",
        [(GEN, GEN_STALE_SENT,
          "OR (status IN ('candidate', 'eligible', 'scheduled')")],
        "go", PKG_STORE, S_STALE,
    ),
    (
        "G5", "过期比较反号（`> now()`）⇒「超期」变成了「还没轮到」："
              "这一面给出的两个相反结论在这里互换",
        [(GEN, GEN_STALE_DUE, "(scheduled_at > now() OR expires_at > now())")],
        "go", PKG_STORE, S_STALE,
    ),
    (
        "G6", "`ORDER BY created_at` 反号 ⇒ 首行不再是最近一次触达，"
              "「最近发送记录」与「最新一条」都读成最早那条",
        [(GEN, GEN_ORDER, "ORDER BY created_at ASC, id\nLIMIT $4")],
        "go", PKG_STORE, S_SCOPE,
    ),
    (
        "G7", "次级排序键 `, id` 摘掉 ⇒ 同刻落两行时（批量/补记）顺序由执行计划决定："
              "同一个过滤器两次读出两个顺序",
        [(GEN, GEN_TIEBREAK, "ORDER BY created_at DESC\n")],
        "go", PKG_STORE, S_SCOPE,
    ),
    (
        "G8", "`NOT $3::boolean` 反置 ⇒ stale 开关两边都对调：不带它查的是超期，"
              "带它查的是全量（§验收 那句「pending 超期任务」正好反过来）",
        [(GEN, GEN_STALE_ON, "AND ($3::boolean")],
        "go", PKG_STORE, S_STALE,
    ),
    # ── 6. 两个真源的现行代价（靶 QU：源文件不参与执行）──
    (
        "Q1", "只改 `db/queries/canonical.sql`（超期三态少一个）⇒ Go 一条测试都不红，"
              "必须靠 `sqlc diff` 这条对账闸兜住（登记归 T6-03）",
        [(QUERIES, QU_STALE_STAT, "       AND status IN ('candidate', 'eligible')")],
        "pytest-sqlc", SQLC, "",
    ),
    (
        "V1", "同一条破坏打**另一侧**的判据：domain 那条「Go 登记的三态 vs SQL 源文本」对账红 ⇒ "
              "它兜的不是产物漂移，而是口径分叉（两条判据不能合成一条）",
        [(QUERIES, QU_STALE_STAT, "       AND status IN ('candidate', 'eligible')")],
        "go", PKG_DOMAIN, D_SQLVOCAB,
    ),
    (
        "V2", "源的排序键反号（同样不参与执行）⇒ 产物没跟着改就是两份真源在漂，"
              "这一格红的还是 `sqlc diff`",
        [(QUERIES, QU_ORDER, "ORDER BY created_at ASC, id\nLIMIT sqlc.arg(row_limit);")],
        "pytest-sqlc", SQLC, "",
    ),
    # ── 7. CLI（工单 §怎么做.3 点名的那件包装，靶 CLI）──
    (
        "C1", "`--user` 的值拼进 query ⇒ 服务端那条 400 会先把它暴露成故障，"
              "而更早的坏处是审计日志与网关日志里到处是身份（ADR-002 在客户端的这一半）",
        [(CLI, CLI_QUERY_BUILDER,
          CLI_QUERY_BUILDER + f'{T}q.Set("user_id", o.userID)\n')],
        "go", PKG_CLI, C_HEAD,
    ),
    (
        "C2", "密钥头给成空串 ⇒ 这一面唯一的鉴权口空了：请求会回 401/403，"
              "而报出来的那句话与「这个人没有触达」一样会被误读",
        [(CLI, CLI_TOKEN_HEADER, f'{T}req.Header.Set("X-Service-Token", "")\n')],
        "go", PKG_CLI, C_HEAD,
    ),
    (
        "C3", "不再判信封 `code`（只看 HTTP 状态）⇒ 网关回 200 而信封说「失败」时被念成"
              "「查到了零条」；这一格的第一版判据只有 403/404 三档，兜不住这种形状，"
              "所以 `TestAuditFailsLoudlyOnNonOKResponse` 补了「HTTP 200／code 500」那一条",
        [(CLI, CLI_CODE_CHECK,
          f"{T}if resp.StatusCode != http.StatusOK {{\n{T}{T}_ = apierr.CodeOK\n")],
        "go", PKG_CLI, C_FAIL,
    ),
    (
        "C4", "`truncated` 不回显进产物 ⇒ 一行 JSON 说「这就是全部」而库里还有："
              "CLI 是这一面唯一的消费方，少报在这里最后落地",
        [(CLI, CLI_TRUNC_WIRE, f"{T}{T}Truncated: false,\n")],
        "go", PKG_CLI, C_REPORT,
    ),
    (
        "C5", "词表提示写进 stdout ⇒ 那一行不再是纯产物，进程闸拿到的是「解析失败」而不是「值不对」"
              "（文件头第 3 条判据的落点）",
        [(CLI, CLI_HINT_STDERR, CLI_HINT_STDERR.replace("Fprintf(stderr", "Fprintf(os.Stdout"))],
        "go", PKG_CLI, C_HINT,
    ),
    (
        "C6", "缺 `GO_API_URL` 时不再退出而是用一个假地址 ⇒ 「本工具一条都没查」被念成"
              "「查出来是空」，这是缺配置最坏的一种形状",
        [(CLI, CLI_NO_URL, f'{T}{T}base = "http://fallback.invalid"\n')],
        "go", PKG_CLI, C_ENV,
    ),
    (
        "C7", "缺 `GO_SERVICE_TOKEN` 时照样发请求 ⇒ 服务端整面不注册（404）被报成一句"
              "不明所以的失败，而真相是这台机器没配密钥",
        [(CLI, CLI_NO_TOKEN, f'{T}{T}token = "injected-fallback"\n')],
        "go", PKG_CLI, C_ENV,
    ),
    (
        "C8", "`--user` 空白放行 ⇒ 空身份进头：查不到东西被读成「这个人没有触达」，"
              "而命令行那一格明明是空的",
        [(CLI, CLI_USER_REQUIRED, f"{T}if false {{\n")],
        "go", PKG_CLI, C_FLAGS,
    ),
    (
        "C9", "负 `--limit` 放行 ⇒ 请求里悄悄少了这一格、由服务端补默认值："
              "「我以为我限了 5 条」不成立",
        [(CLI, CLI_LIMIT_NEG, f"{T}if false {{\n")],
        "go", PKG_CLI, C_FLAGS,
    ),
    (
        "C10", "多余位置参数被忽略 ⇒ `proactiveaudit --user u1 u2` 只查 u1 而操作人以为查了两个人："
               "审计面最不能有的就是「少查了还报完成」",
        [(CLI, CLI_EXTRA_ARG, f"{T}if false {{\n")],
        "go", PKG_CLI, C_FLAGS,
    ),
    # ── 8. 「不进 BFF」那一格的反证（靶 ROUTER_PY，闸 = 十条 handler 计数）──
    (
        "B1", "给 BFF 加一条 `/proactives` 转发 ⇒ 十条变十一条，而工单 §怎么做.2 要的是"
              "「仅供运维/客服，不暴露给客户端」：这一面一旦进契约就长出前端调用方，"
              "而它的鉴权是服务密钥、不是用户 JWT",
        [(ROUTER_PY, ROUTER_TAIL, BFF_ADDED_HANDLER)],
        "pytest", B_COUNT, B_COUNT,
    ),
]

GO_COMMENTS = "".join(
    f"{T}// 绿闸注脚 {i:02d}：这一格判的是语义，不钉行号也不钉排版。\n" for i in range(1, 16)
)
SQL_COMMENTS = "".join(
    "// 绿闸注脚 %02d：生成物与源的对账看的是字面，不看注释密度。\n" % i for i in range(1, 16)
)
PY_COMMENTS = "".join(
    f"# 绿闸注脚 {i:02d}：这一格判的是语义，不钉行号也不钉排版。\n" for i in range(1, 16)
)

GREEN: list[tuple] = [
    (
        "GR1", "proactive_audit.go 的 clampProactiveAuditLimit 上方插 15 行注释 ⇒ "
               "limit 默认/上限/取数口径三条判据照旧绿（钉语义不钉排版）",
        [(PA, "func clampProactiveAuditLimit(limit int) int {\n",
          GO_COMMENTS + "func clampProactiveAuditLimit(limit int) int {\n")],
        "go", PKG_DOMAIN, D_CLAMP,
    ),
    (
        "GR2", "v3_proactives.go 的 handleV3ProactiveAudit 上方插 15 行注释 ⇒ 字段名与 null 语义闸照旧绿",
        [(HV, "func (s *Server) handleV3ProactiveAudit(",
          GO_COMMENTS + "func (s *Server) handleV3ProactiveAudit(")],
        "go", PKG_API, H_FIELDS,
    ),
    (
        "GR3", "生成物里 listProactivesForAudit 上方插 15 行注释 ⇒ 真库谓词闸照旧绿"
               "（对账看的是 SQL 字面，不看注释密度）",
        [(GEN, "const listProactivesForAudit = `-- name: ListProactivesForAudit :many\n",
          SQL_COMMENTS + "const listProactivesForAudit = `-- name: ListProactivesForAudit :many\n")],
        "go", PKG_STORE, S_STALE,
    ),
    (
        "GR4", "tools/proactiveaudit 的 run 上方插 15 行注释 ⇒ 身份只进头的 CLI 闸照旧绿",
        [(CLI, "func run(ctx context.Context, client *http.Client, base, token string,"
              " o options) (*report, error) {\n",
          GO_COMMENTS + "func run(ctx context.Context, client *http.Client, base, token string,"
          " o options) (*report, error) {\n")],
        "go", PKG_CLI, C_HEAD,
    ),
    (
        "GR5", "routers/v3.py 的结果面 handler 上方插 15 行注释 ⇒ 十条 handler 的计数闸照旧绿",
        [(ROUTER_PY, ROUTER_OUTCOME_DECORATOR, PY_COMMENTS + ROUTER_OUTCOME_DECORATOR)],
        "pytest", B_COUNT, B_COUNT,
    ),
    (
        "GR6", "tools/v3surface 的 buildHandler 上方插 15 行注释 ⇒ 夹具装配证人（六格 AND）照旧绿",
        [(SURFACE, "func buildHandler(token string) http.Handler {\n",
          GO_COMMENTS + "func buildHandler(token string) http.Handler {\n")],
        "go", PKG_SURFACE, V_REGISTERED,
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
        # target 允许写成 `包|正则`：只跑点名的那一条判据。
        # 为什么需要这一格：真库那一族的夹具会互相排队——一条破坏先把**前面那条测**
        # 打成 panic（进程一崩，后面的测根本不跑），红点就落不到点名的判据上。
        # 与其改判据，不如把这一条单独拨一次。
        pkg, _, run_expr = target.partition("|")
        if not Path(GO_BIN).is_file():
            return -2, f"{GO_BIN} 不存在：Go 侧用例没跑，不算绿"
        if pkg == PKG_STORE and not os.environ.get(PG_ENV):
            return -2, f"{PG_ENV} 未设置：store 集成测会整包 skip，不算绿也不算红"
        args = [GO_BIN, "test", pkg, "-count=1", "-timeout", "300s"]
        if run_expr:
            args += ["-run", f"^{run_expr}$"]
        proc = subprocess.run(args, cwd=GO_CORE, env=GO_ENV,
                              capture_output=True, text=True, timeout=1800)
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
    with tempfile.TemporaryDirectory(prefix="mutate-t504-") as tmp:
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
