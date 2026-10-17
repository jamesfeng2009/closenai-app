#!/usr/bin/env python3
"""批 3（D-G12 死开关 ＋ D-G13 JWT 算法 ＋ D-G14 零调用方接门）的变异自证（2026-10-17）。

判据口径沿用 R4/R5：每条变异只喂它该打红的那一张闸，并且要求**报错原文里出现名片段**，
否则「红」可能只是别的用例连带崩（假红）。GREEN 族反过来证这些闸不是假红制造机：
加一行注释不该弄红任何判据。

三族靶点的形状各不相同，值得先读这段再跑：

* **A 族（D-G12）**——这一族的两个方向都是「静默」的。少一格不会编译报错、不会让进程起不来，
  它只在下一刀把 path 放进 Ingress 那天咬人（`cutover.go` 的 `!ok || pct >= 100` 把
  「没登记」与「100%」读成同一个形状）。所以 A1/A2 两条必须都在：**只证「少一格会红」
  而不证「多一格也会红」，闸就成了单向登记簿**，而「假旋钮」正是多出来的那一格。
* **B 族（D-G13）**——这一族的红分两种：一种是「键真的能起进程」（B1/B4，运维改一个 env
  就让 Go 换了算法），一种是「签发与校验不再看基线」（B2/B3/B3b）。B5 证的是
  「除 config 外没有读取点」这半：它只在 Go 源码文本上判，因此**故意不要求那份源能编译**
  ——它跑的 runner 是 pytest。`test_python_rejects_a_token_signed_with_a_non_baseline_algorithm`
  这一格**没有生产锚点**（它判的是 PyJWT 自己拒不拒跨算法），本文件不为它编一条假变异。
* **C/D 族（D-G14）**——这一族最容易写出假绿，所以每条都点名它防的是哪一种「看起来接了」：
  C1 摘装配、C2 摘循环、C4 发明假旋钮、D2 摘领域调用点（这条才是台账闸的证人：
  只摘 `return r.requestFollowUp(...)` 时 `.RequestAITask` 的文本引用**仍在**，
  台账闸照绿——那正是 D1 存在的理由：它是**第二证人**（pytest 侧）而不是重复证人）。
  D5/D6 是一对：同一处破坏分别由 Go 侧与 Python 侧的闸认领，两侧都咬才算钉住。
  **D1c/D2b 是这一批唯一跑在真链上的两条**（R5-③ 那族：真库＋真 broker＋真 `*domain.Service`）：
  上面那些闸判的全是源码形状或 fake 计数，只有这两条能回答「outbox 表里到底有没有长出
  那条 `ai.insight.requested` 行」。它们要 env——缺 `TEST_DATABASE_URL`/`CLOSEN_AMQP_URL`
  时按「未跑」记账，**不把 skip 读成绿**（R5 的口径）。

⚠️ 纪律（见 [[feedback-mutation-gates-no-concurrency]]、
[[feedback-mutation-restore-no-git-checkout]]、[[feedback-definition-of-done-tests]]）：
**串行跑**、`copy2` 备份写回 ＋ `filecmp` 逐字节复核、**不用 `git checkout` 恢复**；
备份名带序号＋父目录（本批同时打 `main.go` 与 `cmd/go-api` 的测试、`inbound` 与 `domain`
下的两个同名文件族，只用文件名会在 tmp 里互相覆盖）。

用法（Go 工具链走 GOROOT，本机 PATH 上的 go 是混合安装）：
    ./.venv/bin/python scripts/mutate_batch3_gates.py --check
    ./.venv/bin/python scripts/mutate_batch3_gates.py
    ./.venv/bin/python scripts/mutate_batch3_gates.py --only A1,B5,D2
    # 想连 D1c/D2b 那两条真链证人一起跑，要把本机实库与 broker 的 env 一起导上
    # （口令只走 env，从不写进本文件）：
    #   TEST_DATABASE_URL=postgresql://… CLOSEN_REQUIRE_PG=1 \\
    #   CLOSEN_AMQP_URL=amqp://… GOROOT=/Users/fengyu/sdk/go1.27.1 \\
    #   ./.venv/bin/python scripts/mutate_batch3_gates.py
"""
from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GO_CORE = REPO / "go-core"

MAIN_GO = GO_CORE / "cmd" / "go-api" / "main.go"
CONFIG_GO = GO_CORE / "internal" / "config" / "config.go"
TOKEN_GO = GO_CORE / "internal" / "auth" / "token.go"
COMMANDS_GO = GO_CORE / "internal" / "domain" / "commands.go"
TASK_CHAIN_GO = GO_CORE / "internal" / "domain" / "task_chain.go"
INBOUND_GO = GO_CORE / "internal" / "messaging" / "inbound" / "inbound.go"
DISPATCH_GO = GO_CORE / "internal" / "messaging" / "dispatch" / "dispatch.go"
PUBLISHER_GO = GO_CORE / "internal" / "messaging" / "amqp" / "publisher.go"
CONFIG_MAP_YAML = REPO / "deploy" / "k8s" / "10-config.yaml"

PY_AUTH = REPO / "backend" / "core" / "auth.py"
MQ_CONTRACT = REPO / "backend" / "ai" / "mq" / "contract.py"
MQ_REGISTRY = REPO / "backend" / "ai" / "mq" / "registry.py"

CMD_PKG = "./cmd/go-api/"
CONFIG_PKG = "./internal/config/"
AUTH_PKG = "./internal/auth/"
DOMAIN_PKG = "./internal/domain/"
INBOUND_PKG = "./internal/messaging/inbound/"
DISPATCH_PKG = "./internal/messaging/dispatch/"

D13_PY = "backend/tests/test_jwt_algorithm_baseline.py"
D14_PY = "backend/tests/test_go_task_command_body_accepted.py"
# 真链证人（R5-③ 那族）：需要真库＋真 broker＋Go 工具链，缺 env 时 pytest 会 skip，
# 而 skip 不是绿——所以这两条在环境不全时按**未跑**记账，不混进「未打红」。
R5_PY = "backend/tests/test_r5_interop_broker.py"
BROKER_ONLY = {"D1c", "D2b"}
BROKER_ENV_OK = all(k in os.environ for k in ("TEST_DATABASE_URL", "CLOSEN_AMQP_URL"))

GOROOT = "/Users/fengyu/sdk/go1.27.1"
GO_BIN = f"{GOROOT}/bin/go"
GO_ENV = {**os.environ, "GOROOT": GOROOT, "PATH": f"{GOROOT}/bin:{os.environ.get('PATH', '')}",
          "GO_BIN": os.environ.get("GO_BIN", GO_BIN)}
VENV_PY = REPO / ".venv" / "bin" / "python"

# ── 名片段（受害闸的报错原文；改断言文案要同时改这里，否则绿的是脚本、红的是真相）──
MARK_GATE_MISSING = "没进 main.go 的 gate map"
MARK_GATE_INVENTED = "在 config 没有对应的"
MARK_ALG_BOOT = "竟然启动了进程"
MARK_ALG_SET = "Go 侧认得的算法集合不再是单值"
MARK_VERIFY_ACCEPT = "Verify 接受了 alg=HS384"
MARK_SIGN_ALG = "签发出去的是 alg"
MARK_SIGN_LITERAL = "签发或校验已经不看这个字面量了"
MARK_NO_READER = "这些 Go 文件读了 cfg.JWTAlgorithm"
MARK_PY_READERS = "登记值是 4"
MARK_CONFIGMAP = "部署清单没把这个键钉在基线上"
MARK_CLEANUP_UNWIRED = "清理又回到零调用方"
MARK_CLEANUP_NOTRUN = "循环起来了但清理一次都没跑"
MARK_LOOP_NO_CALL = "没在循环里调 maybeCleanup"
MARK_FAKE_KNOB = "发明了 env 键"
MARK_LEDGER = "找不到引用点"
MARK_REALCHAIN = "各只许引起一次领域调用"
MARK_CHAIN_DEAD_PY = "入队调用点没了"
MARK_CHAIN_BREAK = "链在这一跳断了"
MARK_CYCLE = "链上有环"
MARK_EDGES = "链表有 3 条边，登记的是 2 条"
MARK_IDENTITY_GO = "命令体里出现了用户身份键"
MARK_IDENTITY_PY = "不再是本批约定的三键"
MARK_TRUNCATE = "摘要截断长度或前缀变了"
MARK_ROUTING_RULE = "RoutingKey() 写法变了"
MARK_WIRE_SHAPE = "publisher 的 type 属性来源变了"
MARK_DID_NOT_RAISE = "DID NOT RAISE"

# 每条：(id, 说明, (文件, 原文, 坏文), runner, target, node, 名片段)
MUTATIONS = [
    # ── A 族：D-G12 的两向 ────────────────────────────────────────────────────
    (
        "A1",
        "把 main.go 的 gate map 里「memories」那一格删掉（就是本批补上的那一格）：config 仍然读"
        "`CLOSEN_CUTOVER_MEMORIES`、进程照常启动、`go test ./internal/cutover/` 全绿，而按域问闸时"
        "拿到的是 `!ok` ⇒ 当成 100% 自答。用户后果：下一刀把 /memories 的 path 放进 Ingress 之后，"
        "灰度键拨回 0 也回滚不了——Go 会无视百分比全量自答",
        (MAIN_GO, '\t\t"memories": cfg.CutoverMemories,\n', ""),
        "go", CMD_PKG, "^TestEveryDeclaredCutoverDomainHasAGateMapEntry$", MARK_GATE_MISSING,
    ),
    (
        "A2",
        "反方向：在 gate map 里多加一格**没有对应键**的域（notes → cfg.CutoverMemories，"
        "复用别的字段好让它能编译）。这一格没人能拨，与 D-G12 否掉的那条「假旋钮」同形；"
        "只证少一格会红的闸看不见它",
        (MAIN_GO, '\t\t"memories": cfg.CutoverMemories,\n',
         '\t\t"memories": cfg.CutoverMemories,\n\t\t"notes": cfg.CutoverMemories,\n'),
        "go", CMD_PKG, "^TestGateMapHasNoInventedDomain$", MARK_GATE_INVENTED,
    ),

    # ── B 族：D-G13 的三格（校验位／签发处／部署钉值）─────────────────────────
    (
        "B1",
        "把 config.go 的单值比对放宽成「HS256 或 RS256 都放行」：这个键就从**校验位**变成"
        "**旋钮**——但只有 Go 这一侧的进程能起来，而 `internal/auth/token.go` 仍然硬编码 HS256，"
        "于是 RS256 的部署会「启动成功、验不掉对面签的 token」。用户后果：走 Go 的那批端点整片 401，"
        "而 ConfigMap 看起来是合法配置",
        (CONFIG_GO, '\tif cfg.JWTAlgorithm != "HS256" {',
         '\tif cfg.JWTAlgorithm != "HS256" && cfg.JWTAlgorithm != "RS256" {'),
        "go", CONFIG_PKG, "^TestJWTAlgorithmAcceptsOnlyTheBaseline$", MARK_ALG_BOOT,
    ),
    (
        "B4",
        "同一处破坏的**对面证人**：Python 那条不依赖 go 工具链的复刻闸（本机没装 Go 时 CI"
        "仍然有人守这条口径）。它读的是 Go 源文本，所以红的形状是「认得的算法集合不再是单值」",
        (CONFIG_GO, '\tif cfg.JWTAlgorithm != "HS256" {',
         '\tif cfg.JWTAlgorithm != "HS256" && cfg.JWTAlgorithm != "RS256" {'),
        "pytest", f"{D13_PY}::test_go_validator_refuses_every_other_value", "", MARK_ALG_SET,
    ),
    (
        "B2",
        "摘掉 `Verify` 里 `t.Method != jwt.SigningMethodHS256` 那道门（改成恒假）：同一个 HMAC 密钥"
        "签出的 HS384/HS512 token 被**静默接受**。这是经典的 alg-confusion／密钥复用面，"
        "而对面 Python 的 `JWT_ALGORITHM` 是真旋钮，所以「有人改成 HS384」不是假设",
        (TOKEN_GO, "\t\t\tif t.Method != jwt.SigningMethodHS256 {", "\t\t\tif false {"),
        "go", AUTH_PKG, "^TestVerifyRejectsEveryNonBaselineAlgorithm$", MARK_VERIFY_ACCEPT,
    ),
    (
        "B3",
        "把签发处的字面量从 HS256 换成 HS512：Go 自己签的自己验得过，`test_go_auth_interop.py` "
        "的夹具是**录好的字节**也不会当场发现，而 Python 按 algorithms=[HS256] 验 ⇒ 所有"
        "「Go 签、Python 验」的路径全断。这条证的是「基线在 Go 这一侧」这句话真有闸",
        (TOKEN_GO, "return jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString(s.secret)",
         "return jwt.NewWithClaims(jwt.SigningMethodHS512, claims).SignedString(s.secret)"),
        "go", AUTH_PKG, "^TestSignerItselfEmitsOnlyTheBaseline$", MARK_SIGN_ALG,
    ),
    (
        "B3b",
        "同一处破坏的**第二证人**（config 包里的硬编码闸）：它判的是源码字面量在不在，"
        "与 B3 的行为判据互补——B3 红说明签出去的 header 变了，这条红说明「登记说签发处写死」"
        "这句话源文件已经不成立",
        (TOKEN_GO, "return jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString(s.secret)",
         "return jwt.NewWithClaims(jwt.SigningMethodHS512, claims).SignedString(s.secret)"),
        "go", CONFIG_PKG, "^TestSigningSiteHardcodesTheBaseline$", MARK_SIGN_LITERAL,
    ),
    (
        "B5",
        "给 main.go 插一行真的读取点（`_ = cfg.JWTAlgorithm`）：这就是 D-G13 明确不许的那种改法——"
        "看起来让键生效了，实际把跨语言基线拆成单边可改的旋钮。两侧的单测都不碰这格，"
        "只有「除 config 外零读取点」这条扫源闸认得",
        (MAIN_GO, "\tbatch1 := &httpapi.Batch1{\n",
         '\t_ = cfg.JWTAlgorithm // 变异证人：把校验位冒充旋钮\n\n\tbatch1 := &httpapi.Batch1{\n'),
        "pytest", f"{D13_PY}::test_go_has_no_reader_of_the_field_outside_config", "", MARK_NO_READER,
    ),
    (
        "B6",
        "把 `backend/core/auth.py` 四处读取点之一改成硬编码 "
        "把 algorithms 列表写成字面量 HS256：登记面写的「Python 侧是真旋钮」当场失真。"
        "这条红**不代表现网坏了**（硬编码反而更一致），它红的是**台账里的数**——"
        "数一旦不等于实测，注释就开始骗人，这正是 D-G15/G16/G20 那一族口径失活的起点",
        (PY_AUTH, "return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])",
         'return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])'),
        "pytest", f"{D13_PY}::test_python_side_readers_are_still_there_and_counted", "", MARK_PY_READERS,
    ),
    (
        "B7",
        "把部署清单的 `JWT_ALGORITHM` 改成 HS384（值本身仍合法、进程仍起得来）：三处默认值里"
        "只有 ConfigMap 这一处是现网真正喂进去的值，它一旦不钉在基线上，Go 就拒启 ⇒ 整个 go-api "
        "起不来；而这条闸不看 Go、不看 Python，只看部署面",
        (CONFIG_MAP_YAML, '  JWT_ALGORITHM: "HS256"\n', '  JWT_ALGORITHM: "HS384"\n'),
        "pytest", f"{D13_PY}::test_three_defaults_are_the_same_literal", "", MARK_CONFIGMAP,
    ),

    # ── C 族：D-G14-① 保留期清理 ─────────────────────────────────────────────
    (
        "C1",
        "摘掉 main.go 里 `Cleanup: candidateDomain.CleanupDeliveryRecords,` 这一格：字段还在 "
        "`dispatch.Deps` 上、`New` 校验照样过、`Stats` 全零、投递一条不丢——"
        "`delivery_records` 那张表就永久不再清（N7-11 拍的「唯一清理入口」又回到零调用方）。"
        "**dispatch 包自己的用例看不见 main**，所以这一格只能由装配面源码闸认领",
        (MAIN_GO, "\t\tCleanup: candidateDomain.CleanupDeliveryRecords,\n", ""),
        "go", CMD_PKG, "^TestMainWiresTheRetentionCleanup$", MARK_CLEANUP_UNWIRED,
    ),
    (
        "C2",
        "在 Run 循环里把 `r.maybeCleanup(ctx)` 换成恒不成立的错误：钩子中着、注入点留着、"
        "`Deps.Cleanup` 的字段注释还在，但清理一次也不跑。这一族最省事的假接线形态"
        "就是「有字段、有校验、没人调用」",
        (DISPATCH_GO, "\t\tif cerr := r.maybeCleanup(ctx); cerr != nil {",
         "\t\tif cerr := error(nil); cerr != nil {"),
        "go", DISPATCH_PKG, "^TestCleanupRunsAtStartupAndIsCounted$", MARK_CLEANUP_NOTRUN,
    ),
    (
        "C3",
        "同一处破坏的**装配面证人**（cmd 包那条只读 dispatch.go 源码形状的闸）："
        "C2 那条红的是行为，这条红的是「循环里真的没有这一跳」，"
        "它在 `Cleanup` 被做成一次性启动清（而不是循环内判时点）时同样会红",
        (DISPATCH_GO, "\t\tif cerr := r.maybeCleanup(ctx); cerr != nil {",
         "\t\tif cerr := error(nil); cerr != nil {"),
        "go", CMD_PKG, "^TestDispatchLoopActuallyCallsCleanup$", MARK_LOOP_NO_CALL,
    ),
    (
        "C4",
        "给 dispatch 发明一个清理节拍的 env 键（新增常量 EnvCleanup 指向 CLOSEN_CLEANUP_EVERY）："
        "D-G12 刚登记完「键存在但没人读＝假旋钮」，这一格是把同一个错误用「新增旋钮」的形态"
        "再犯一次。包内默认节拍（6h）是这一批的决定，本条把它写成可判的断言",
        (DISPATCH_GO, "func (r *Runner) maybeCleanup(ctx context.Context) error {",
         'const EnvCleanup = "CLOSEN_CLEANUP_EVERY" // 变异证人：假旋钮\n\n'
         "func (r *Runner) maybeCleanup(ctx context.Context) error {"),
        "go", CMD_PKG, "^TestDispatchLoopActuallyCallsCleanup$", MARK_FAKE_KNOB,
    ),

    # ── D 族：D-G14-② 候选入库后请下一段 ────────────────────────────────────
    (
        "D1",
        "把 `return r.requestFollowUp(inner, st, d)` 摘成 `return nil`：候选仍然正常进门、"
        "仍然 ACK、仍然计数，只是**下一段永远不请求**。注意这一格 `RequestAITask` 的文本引用"
        "仍在（在 requestFollowUp 体内），所以台账闸照绿 ⇒ 这一条只能由行为闸与源码形状闸认领",
        (INBOUND_GO, "\t\treturn r.requestFollowUp(inner, st, d)", "\t\treturn nil"),
        "pytest", f"{D14_PY}::test_the_inbound_caller_is_still_wired_in_source", "", MARK_CHAIN_DEAD_PY,
    ),
    (
        "D1b",
        "同一处破坏的**行为证人**（inbound 包）：fake 域收到的命令数从 1 变 0，"
        "`FollowUpsEnqueued` 全零。这条与 D1 一内一外——D1 判的是「进程里还接没接」，"
        "这条判的是「接了之后语义对不对」",
        (INBOUND_GO, "\t\treturn r.requestFollowUp(inner, st, d)", "\t\treturn nil"),
        "go", INBOUND_PKG, "^TestPatternCandidateEnqueuesInsightTask$", MARK_CHAIN_BREAK,
    ),
    (
        "D2",
        "摘掉 `requestFollowUp` 内部那句 `r.deps.Domain.RequestAITask(ctx, st, req)` 的调用"
        "（换成恒不成立的错误，但把 `req` 留在条件里，否则变异体自己编译不过＝这一条没真跑过）："
        "函数还在、还被调用、计数还在涨，但领域入口一次也没被请。"
        "**这一条才是台账闸的证人**：生产代码里 `.RequestAITask` 的引用归零 ⇒ "
        "`wiredThisBatch` 那张表当场过期",
        (INBOUND_GO, "\tif err := r.deps.Domain.RequestAITask(ctx, st, req); err != nil {",
         "\tif err := error(nil); err != nil && req.OperationID != \"\" {"),
        "go", CMD_PKG, "^TestDomainServiceCallerLedger$", MARK_LEDGER,
    ),
    (
        "D1c",
        "D1 同一处破坏的**真链证人**（R5-③ 那族跨进程闸）：这里没有 fake 域，被调的是真 "
        "`*domain.Service`、真库、真 broker——「候选进门后请下一段」少了这一跳，"
        "outbox 表里就不会有那条 `ai.insight.requested` 行。**需要真库＋真 broker 的 env**，"
        "缺 env 时这两条按「未跑」记账，不混进「未打红」（skip 不是绿）",
        (INBOUND_GO, "\t\treturn r.requestFollowUp(inner, st, d)", "\t\treturn nil"),
        "pytest", f"{R5_PY}::test_chain_outbox_to_canonical", "", MARK_REALCHAIN,
    ),
    (
        "D2b",
        "D2 同一处破坏的**真链证人**：`requestFollowUp` 还被调用、计数还在涨，但真领域入口"
        "一次也没被请 ⇒ 真库里的 outbox 行消失。这一条与 D2 一内一外：D2 判台账表还新不新，"
        "这条判那条命令**在库里存不存在**",
        (INBOUND_GO, "\tif err := r.deps.Domain.RequestAITask(ctx, st, req); err != nil {",
         "\tif err := error(nil); err != nil && req.OperationID != \"\" {"),
        "pytest", f"{R5_PY}::test_chain_outbox_to_canonical", "", MARK_REALCHAIN,
    ),
    (
        "D3",
        "给链表加一条反向边（insight → observation）：链上出现环 ⇒ 一条洞察永久把自己重放下去，"
        "而命令 id 每次由**来源候选**派生、outbox 的 UNIQUE 拦不住不同来源的循环。"
        "用户后果是「一次记录 → 永久 LLM 调用」，那条正是本文件顶部登记的事故形状",
        (TASK_CHAIN_GO, "\tKindPattern: AITaskInsight,\n",
         "\tKindPattern: AITaskInsight,\n\tKindInsight: AITaskObservation,\n"),
        "go", DOMAIN_PKG, "^TestFollowUpChainIsAcyclic$", MARK_CYCLE,
    ),
    (
        "D3b",
        "同一处破坏的**边界证人**：表从两条边变三条。这条红的是「本批只接工作单 D-G14 点名的"
        "那一条链路」这个范围决定——有人顺手加边就是在没有调用方审计的情况下发明第二条触发面",
        (TASK_CHAIN_GO, "\tKindPattern: AITaskInsight,\n",
         "\tKindPattern: AITaskInsight,\n\tKindInsight: AITaskObservation,\n"),
        "go", DOMAIN_PKG, "^TestFollowUpTableCoversExactlyTheAgreedEdges$", MARK_EDGES,
    ),
    (
        "D4",
        "把命令体的 `sourceKind` 键改名成 `user_id`（值不变）：Go 侧的闸按 Python 的归一规则"
        "逐字复刻，先红；对面则按 `foreign_user_id` **永久拒绝**这条命令 ⇒ 链断在第一步，"
        "而 DLQ 里那条记录的 reason 指回的是「体里带了归属」。"
        "这条证的正是「连键名都不写」那条约束不是形式主义",
        (TASK_CHAIN_GO, '\t\t"sourceKind":      string(admitted),\n',
         '\t\t"user_id":         string(admitted),\n'),
        "go", DOMAIN_PKG, "^TestTaskRequestBodyCarriesNoUserIdentity$", MARK_IDENTITY_GO,
    ),
    (
        "D4b",
        "同一处破坏的**对面证人**：Python 那条判「Go 实际写了哪几个键」的闸读不到 `sourceKind` "
        "了（它的正则只认 `[A-Za-z]+` 的键名，而 `user_id` 带下划线）⇒ 形状一变就先在**这里**红，"
        "而不是等到命令进了 DLQ 才发现",
        (TASK_CHAIN_GO, '\t\t"sourceKind":      string(admitted),\n',
         '\t\t"user_id":         string(admitted),\n'),
        "pytest", f"{D14_PY}::test_body_carries_exactly_the_three_agreed_fields", "", MARK_IDENTITY_PY,
    ),
    (
        "D5",
        "把命令 id 的摘要截断从 40 位改成 36 位：id 仍然定长、仍然确定性，**所有单测的语义判据都还过**，"
        "但两侧互钉的那两条字面量不再相等 ⇒ 现网升级那一刻，同一批来源候选会派生出**不同的** id，"
        "重放不再撞 UNIQUE ⇒ 同一条候选被请两次。这是「期望值不许用被测函数自证」那条纪律的自证",
        (TASK_CHAIN_GO, 'return "task-" + hex.EncodeToString(sum[:])[:40]',
         'return "task-" + hex.EncodeToString(sum[:])[:36]'),
        "pytest", f"{D14_PY}::test_command_ids_match_the_literals_pinned_in_the_go_test", "",
        MARK_TRUNCATE,
    ),
    (
        "D6",
        "把键串的命名法加一段后缀（`ai.<kind>.requested` → `ai.<kind>.requested.v2`）："
        "键的唯一生成点是 `AITaskKind.RoutingKey()`，改它的一侧而不改拓扑表就是"
        "「投给一个没人绑定的键」——broker 照单 ack、消息消失、两侧单测全绿。"
        "Python 这条闸先判规则有没有改，再按规则拼，所以它红得比队列积压早。"
        "认领它的是 `test_every_command_kind_on_the_face_is_decodable`（四条命令面全喂一遍那条）："
        "它调 `go_routing_key()`，而那个助手先钉 commands.go 的字面量",
        (COMMANDS_GO, 'return "ai." + string(k) + ".requested"',
         'return "ai." + string(k) + ".requested.v2"'),
        "pytest",
        f"{D14_PY}::test_every_command_kind_on_the_face_is_decodable",
        "", MARK_ROUTING_RULE,
    ),
    (
        "D6b",
        "同一处破坏的**第二证人**（接上的那两条边）：参数化那条用例也走 `go_routing_key()`。"
        "两条一起红才说明「键的命名法」这件事同时兜住了命令面的四条键与 D-G14-② 真的会发的两条边",
        (COMMANDS_GO, 'return "ai." + string(k) + ".requested"',
         'return "ai." + string(k) + ".requested.v2"'),
        "pytest",
        f"{D14_PY}::test_go_followup_command_is_accepted_by_the_real_decoder[fact->Pattern]",
        "", MARK_ROUTING_RULE,
    ),
    (
        "D7",
        "把 publisher 的 `type` 属性来源从 `msg.MessageType` 换成 `msg.RoutingKey`：现网真的会发"
        "`ai.pattern.requested` 这种带点的 type，而对面判的是 `ai_pattern_requested` ⇒ "
        "`message_type_mismatch` 永久拒绝每一条命令。这条打的是**本文件的镜像形状**："
        "它红说明我这几十条判据正在测一个现网不会再发的形状",
        (PUBLISHER_GO, "\t\tType:          msg.MessageType,", "\t\tType:          msg.RoutingKey,"),
        "pytest", f"{D14_PY}::test_the_wire_shape_mirror_is_publishers_own_code", "", MARK_WIRE_SHAPE,
    ),
    (
        "D8",
        "让 `Registry.handler_for` 在没注册时返回一个空 handler 而不是抛 `handler_unregistered`："
        "命令被「处理」成零候选然后 ACK ⇒ 静默丢失。这一格是 `events.md` 那句"
        "「流量仍是零」的可判版本——它红的含义是**口径**变了（R6 接生产者那批必须连着改文档）",
        (MQ_REGISTRY, "        handler = self._handlers.get(task_kind)\n        if handler is None:",
         "        handler = self._handlers.get(task_kind)\n        if False:"),
        "pytest",
        f"{D14_PY}::test_the_producer_side_is_still_unregistered_so_traffic_is_zero",
        "", MARK_DID_NOT_RAISE,
    ),
    (
        "D9",
        "放宽 Python 的体内归属判据（把归一规则退化成精确匹配 `user_id`）：`userId`／`UserID` "
        "这些形态就不再被拒，而是被**静默忽略**——body 原样透传给 handler，"
        "于是出现「按 header 的 A 用户跑、体里写着 B」的跨用户通路。本条证第四格不是装饰",
        (MQ_CONTRACT, 'k.lower().replace("_", "") == "userid"', 'k == "user_id"'),
        "pytest",
        f"{D14_PY}::test_body_with_any_user_identity_form_is_permanently_rejected[userId]",
        "", MARK_DID_NOT_RAISE,
    ),
    (
        "D10",
        "摘掉 `taskType`↔routing key 的一致性判据：pattern 任务的体可以顺着 insight 的键投出去，"
        "而注册表按键取 handler ⇒ 语义串了台（能跑通、也进了权威表，但算的是另一种东西）。"
        "这条是工作单 D-G14 之外、R5 的 D-R5-8 纪律在本批的复证",
        (MQ_CONTRACT, "    if got_task_type != expected_task_type:", "    if False:"),
        "pytest", f"{D14_PY}::test_key_and_task_type_mismatch_is_permanently_rejected", "",
        MARK_DID_NOT_RAISE,
    ),
    (
        "D11",
        "摘掉 `x-user-id` 缺失即拒的判据：Python 不查库，这个头就是唯一的归属来源，"
        "空值只能靠猜——放行之后任务会跑在「不知道属于谁」的用户上。本条钉的是"
        "「归属只有 header 一个来源」这句话的另一半（Go 侧不写体、对面不接空值）",
        (MQ_CONTRACT, '    user_id = _as_str(table.get(HEADER_USER_ID)).strip()\n    if user_id == "":',
         '    user_id = _as_str(table.get(HEADER_USER_ID)).strip()\n    if False:'),
        "pytest", f"{D14_PY}::test_missing_user_header_is_permanently_rejected", "",
        MARK_DID_NOT_RAISE,
    ),
    (
        "D12",
        "摘掉 `type` 属性与键的同义判据（`if message_type and ...` 退化成恒假）："
        "两条路都写得对却只判一条时，另一条写错就是「按 A 的键投了 B 的 type」。"
        "今天这一格在 Go 侧由同一个 `Kind` 写出来所以不可能不一致，本条证的是"
        "**这条闸真的在判**（而不是我写了一条永真）",
        (MQ_CONTRACT,
         '    if message_type and message_type != f"ai_{ROUTING_KEY_TO_TASK_KIND[routing_key]}_requested":',
         "    if False:"),
        "pytest", f"{D14_PY}::test_message_type_property_disagreeing_with_key_is_rejected", "",
        MARK_DID_NOT_RAISE,
    ),
]

# GREEN：不该弄红任何闸的中性改动（证这些闸不是「只要动文件就红」的假红制造机）。
GREEN = [
    (
        "E1",
        "在 dispatch.go 的清理入口前加一行注释：所有判据读的是代码形状与行为，"
        "注释不该算调用点",
        (DISPATCH_GO, "// maybeCleanup 到点就跑一次保留期清理，没到点就什么都不做（D-G14-①）。",
         "// 变异证人：这一行注释不该弄红任何闸。\n"
         "// maybeCleanup 到点就跑一次保留期清理，没到点就什么都不做（D-G14-①）。"),
        "go", DISPATCH_PKG, "",
    ),
    (
        "E2",
        "在 contract.py 的 parse_command 上方加一行注释：本批那十六条判据读的是函数体与常量，"
        "加注释不该让任何一条红（同一形状也顺带证 `goCodeOnly` 那台机没有误伤 Python 侧）",
        (MQ_CONTRACT, "def parse_command(",
         "# 变异证人：注释不该弄红判据。\ndef parse_command("),
        "pytest", D14_PY, "",
    ),
]


def _apply(mutation: tuple) -> None:
    path, old, new = mutation
    src = path.read_text(encoding="utf-8")
    n = src.count(old)
    assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:90]!r}"
    path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _verify_anchor(mutation: tuple) -> str | None:
    path, old, _new = mutation
    if not path.exists():
        return "文件不存在"
    n = path.read_text(encoding="utf-8").count(old)
    return None if n == 1 else f"命中 {n} 次"


def run(runner: str, target: str, node: str) -> tuple[int, str]:
    if runner == "go":
        cmd = [GO_BIN, "test", target, "-count=1", "-timeout", "300s"]
        if node:
            cmd += ["-run", node]
        cwd, timeout = GO_CORE, 900
    else:
        assert node == "", "pytest 的节点 id 就写在 target 里，node 留空"
        cmd = [str(VENV_PY), "-m", "pytest", target, "-q", "--no-header", "-x"]
        cwd, timeout = REPO, 900
    proc = subprocess.run(cmd, cwd=cwd, env=GO_ENV, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def is_red(code: int, out: str) -> bool:
    """Go 侧的「红」＝**受害用例真的跑起来并断言失败**。

    编译坏掉（`[build failed]`）与「没匹配到用例」（`no tests to run`）都不算打红：
    那些情况下红的是工具链，不是闸。
    """
    if code == 0:
        return False
    if "[build failed]" in out or "no tests to run" in out or "no test files" in out:
        return False
    return "--- FAIL:" in out


def is_red_pytest(code: int, out: str, target: str) -> bool:
    """pytest 侧的「红」＝**那一条节点真的跑起来并失败**（`FAILED <节点 id>`）。

    收集期报错、节点没匹配到、前置缺失导致的 skip 都不算红：那些红的是环境，不是闸。
    """
    if code == 0:
        return False
    if "no tests ran" in out or "1 skipped" in out or "error" in out.split("= ")[-1]:
        return False
    return f"FAILED {target}" in out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数，不改任何文件")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()
    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    if args.check:
        bad = 0
        for case in [*MUTATIONS, *GREEN]:
            problem = _verify_anchor(case[2])
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}"
              f"（共 {len(MUTATIONS) + len(GREEN)} 条）")
        return 1 if bad else 0

    paths = sorted({case[2][0] for case in [*MUTATIONS, *GREEN]})
    with tempfile.TemporaryDirectory(prefix="mutate-batch3-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            bak = Path(tmp) / f"{idx:02d}-{path.parent.name}-{path.name}"
            # 先断言备份名不碰撞：两个 main.go／两个 contract.py 一旦同名，
            # 「恢复」就会把 A 写回 B，而 filecmp 比的是被覆盖后的那份备份。
            assert not any(bak == b for b in originals.values()), f"备份名碰撞：{bak}"
            shutil.copy2(path, bak)
            originals[path] = bak

        def restore() -> None:
            for path, bak in originals.items():
                shutil.copy2(bak, path)
                assert filecmp.cmp(bak, path, shallow=False), f"{path} 恢复后字节不一致"

        red_ok = red_fail = green_ok = green_fail = 0
        not_run: list[str] = []
        failures: list[str] = []
        try:
            for cid, desc, mutation, runner, target, node, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                if cid in BROKER_ONLY and not BROKER_ENV_OK:
                    not_run.append(cid)
                    print(f"[{cid}] 未跑（缺 `TEST_DATABASE_URL`/`CLOSEN_AMQP_URL`："
                          f"这一族的靶要真库＋真 broker，硬跑只会得到 skip，"
                          f"而 skip 不是绿也不该记成打红）  {desc}")
                    continue
                _apply(mutation)
                try:
                    code, out = run(runner, target, node)
                finally:
                    restore()
                hit = (is_red(code, out) if runner == "go"
                       else is_red_pytest(code, out, target))
                if hit and marker in out:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓（{runner}）  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（{runner} exit={code}，名片段 {marker!r} "
                          f"命中={marker in out}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))

            for cid, desc, mutation, runner, target, node in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(mutation)
                try:
                    code, out = run(runner, target, node)
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓（{runner}）  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（{runner} exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))
        except Exception as exc:  # noqa: BLE001
            restore()
            print(f"异常中止，已恢复：{exc!r}")
            return 2

        # 收尾自证：被动过的文件回到绿。恢复是备份写回的，但残渣只有再跑一遍才验得出来。
        # 本批的靶文件跨六个 Go 包与三个 Python 模块，所以复核按「被改过的那个包／那份测试」点验，
        # 不跑全仓（全量回归在批 3 的收尾步骤里单独报）。
        rechecks = [
            ("go", CMD_PKG, ""), ("go", CONFIG_PKG, ""), ("go", AUTH_PKG, ""),
            ("go", DOMAIN_PKG, ""), ("go", INBOUND_PKG, ""), ("go", DISPATCH_PKG, ""),
            ("pytest", D13_PY, ""), ("pytest", D14_PY, ""),
            ("pytest", "backend/tests/test_ai_mq_contract.py", ""),
        ]
        if BROKER_ENV_OK:
            # D1c/D2b 改过 `inbound.go`，而这一族的靶是那份真链报告里的计数——
            # 它比六个 Go 包都慢（要起 broker 消费者＋真库往返），只在 env 齐时复核。
            rechecks.append(("pytest", R5_PY, ""))
        for runner, target, node in rechecks:
            code, out = run(runner, target, node)
            print(f"复核 {target}：{'绿' if code == 0 else '红（残留！）'}")
            if code != 0:
                print("  " + "\n  ".join(out.splitlines()[-12:]))
                return 1

        print(f"\n计数：红闸 {red_ok} 打红 / {red_fail} 未打红"
              + (f" / {len(not_run)} 未跑（{','.join(not_run)}，缺真库＋真 broker 的 env）" if not_run else "")
              + f"；绿闸 {green_ok} 保持绿 / {green_fail} 被误伤；异常 0")
        if failures:
            print("未通过：" + ",".join(failures))
        return 1 if (red_fail or green_fail) else 0


if __name__ == "__main__":
    sys.exit(main())
