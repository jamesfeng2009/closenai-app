#!/usr/bin/env python3
"""T2-06（`source.deleted`：删除一行记忆之后把账改过来）的变异自证（2026-10-19）。

靶点口径照 R5：每条变异只喂它该打红的那一张闸，且要求**报错原文里出现名片段**，
否则「红」可能是别的用例连带崩的假红。绿闸反过来证明这些闸不是假红制造机。

与 R5 的形状差异有两条，都写进来了：

1. 受害闸跨**三种** runner——Go 单测（domain／inbound／cmd）、Go 真库集成测（store，
   要 `TEST_DATABASE_URL`）、对面 Python（读源码的跨语言闸＋真×真×真的跨进程闸）。
2. 这一批的四件套里「失败恢复」那一件是真的有代码（传播中途失败 ⇒ 把翻转收回），
   所以 M4 打的是补偿那一支，而不是像 R5 那样只有正向路径可打。

编号与归属（每条都指一件具体的现网后果）：
  · M1  事件面与候选面的幂等名并成一个串（`go.core.source` ⇒ `go.core.candidate`）：
        候选面那句「这条 id 我吃过了」替事件面宣告吃过了 ⇒ **那条删除永远不传播**，
        而两侧日志各自都说自己没错。
  · M2  事件面只 `New` 不 `Run`：包 30 条闸全绿，进程里根本没有那条消费者。
  · M3  摘掉传播调用：登记行翻了 `deleted`，而摊给用户的那条结论还站在零证据上
        （不变量 9 的字面违反）。
  · M4  摘掉失败补偿：一条传播失败 ⇒ 库里停在 `deleted`，重投这一条会被单向闸跳过，
        **缺口永远补不回来**。
  · M5  摘掉「头体归属一致」判据：一条自报 `userId=victim` 的删除被按受害者入账。
  · S1  真库闸：`GetSourceRecordByPointer` 的 SQL 少带 `user_id` ⇒ 跨用户翻转。
  · P1  生产者开关默认开：灰度 0 的现网一 import 就拨号，broker 没起时启动即失败。
  · P2  钩子排到提交**之前**：Go 读到的还是翻转前的自己，传播变成空转。
  · P3  体里的键漂成 snake_case：对侧 `DisallowUnknownFields` ⇒ 每次正常删除都进 DLQ。
  · P4  幂等 id 掺随机：同一行删两次变成两条不同删除 ⇒ 「重复传播」。
  · P5  提交之后的失败抛回请求路径：用户拿到 500，重试删除拿到 404。
  · P6  入口没接线（`DELETE /memories/{id}` 不登记钩子）：这条链的第一跳没了，
        而 `source_events.py` 的单测照样全绿。
  · X1  `-mode source` 那一档缺席：只有真进程看得见「对面那个消费者根本没被装配过」。
  · X2  观测面恒报 `invalidates: 0`：与 R5 的 M15 同一格坏法，两侧账都说自己没错。

⚠️ S1／X1／X2 要真前置（真 PG／真 broker／真 go 工具链），缺任一条时它们**不会**算红：
   store 包在没有 `TEST_DATABASE_URL` 时 skip（退出码 0），e2e 文件缺 env 时整个 skip。
   脚本会把这种情况报成「未打红」，结案行里要按「未跑」登记，不许写成绿。
   另外 `TEST_DATABASE_URL` 必须用**能登录的角色**（本机 `closenai-pg-dev` 只有
   `postgres` 可登录；用 `closenai` 时 Go 侧报 28P01，pytest 把那一格判成 fixture
   error 而不是红）。

口径照抄既有纪律（见 [[feedback-mutation-gates-no-concurrency]]、
[[feedback-mutation-restore-no-git-checkout]]、[[feedback-definition-of-done-tests]]）：
**串行跑**、copy2 备份写回 ＋ `filecmp` 逐字节复核、**不用 `git checkout`**。

用法（env 由调用方给，凭据不进这个文件）：
    ./.venv/bin/python scripts/mutate_t206_gates.py --check
    GOROOT=... PATH=... GO_BIN=... TEST_DATABASE_URL=... DATABASE_URL=... CLOSEN_AMQP_URL=... \\
      ./.venv/bin/python scripts/mutate_t206_gates.py
    ./.venv/bin/python scripts/mutate_t206_gates.py --only M1,P4
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

SOURCE_DELETED_GO = GO_CORE / "internal" / "messaging" / "inbound" / "source_deleted.go"
INVALIDATION_GO = GO_CORE / "internal" / "domain" / "source_invalidation.go"
MAIN_GO = GO_CORE / "cmd" / "go-api" / "main.go"
TOOL_GO = GO_CORE / "tools" / "r5interop" / "main.go"
SQLGO_GO = GO_CORE / "internal" / "store" / "canonicaldb" / "canonical.sql.go"

REPO_PY = REPO / "backend" / "ai" / "mq" / "contract.py"
CONFIG_PY = REPO / "backend" / "ai" / "mq" / "config.py"
DB_ROUTE_PY = REPO / "backend" / "core" / "db_route.py"
SOURCE_EVENTS_PY = REPO / "backend" / "services" / "source_events.py"
ROUTER_PY = REPO / "backend" / "routers" / "memories.py"

INBOUND_PKG = "./internal/messaging/inbound/"
DOMAIN_PKG = "./internal/domain/"
CMD_PKG = "./cmd/go-api/"
STORE_PKG = "./internal/store/"

PRODUCER_PY = "backend/tests/test_source_deleted_producer.py"
CONTRACT_PY = "backend/tests/test_ai_mq_contract.py"
E2E_PY = "backend/tests/test_v3_evidence_e2e.py"
E2E_NODE = f"{E2E_PY}::test_source_deleted_propagates_cross_process"

GOROOT = "/Users/fengyu/sdk/go1.27.1"
GO_BIN = f"{GOROOT}/bin/go"
GO_ENV = {**os.environ, "GOROOT": GOROOT, "PATH": f"{GOROOT}/bin:{os.environ.get('PATH', '')}",
          "GO_BIN": os.environ.get("GO_BIN", GO_BIN)}
VENV_PY = REPO / ".venv" / "bin" / "python"

# ── 名片段（受害闸的报错原文片段；改断言文案要同时改这里，否则绿的是脚本、红的是真相）──
MARK_CONSUMER_MERGE = "事件面与候选面的 consumer 串相同"
MARK_NO_RUN = "事件面没接上进程"
MARK_PROPAGATE = "传播计数"
MARK_RESTORE = "状态没收回"
MARK_HEADER_BODY = "实得一条放行"
MARK_OWNER_SCOPE = "跨用户那次翻转了别人的账"
MARK_DEFAULT_ON = "assert sw.enabled is False"
MARK_ORDER = "事件投在提交之前"
MARK_BODY_KEYS = "Go 的体字段面"
MARK_IDEMPOTENT = "source_deleted_message_id"
MARK_FAILURE_ESCAPES = "broker 断了"
MARK_ENTRY_WIRE = "assert len(enabled.wire) == 1"
MARK_MODE_ABSENT = "r5interop -mode source rc=1"
MARK_INVALIDATES_ZERO = "assert 0 == 1"

# 每条：(id, 说明, (文件, 原文, 坏文), runner, target, node, 名片段)
MUTATIONS = [
    (
        "M1",
        "把事件面的幂等名并成候选面那一个串（`SourceDeletedConsumer = Consumer`）："
        "Go 的 inbox 唯一键是 `(message_id, consumer)`，并串之后候选面那句「这条 id 我"
        "吃过了」会替事件面宣告「我也吃过了」⇒ **那条删除永远不传播**，而库里一切正常。"
        "本批唯一能证「两条面各有各的幂等位」的写法是跨进程那一条（X1/X2），"
        "但这一格由**包内**那条契约闸先钉住——它比的是值，不是名字",
        (SOURCE_DELETED_GO, '\tSourceDeletedConsumer = "go.core.source"',
         "\tSourceDeletedConsumer = Consumer"),
        "go", INBOUND_PKG, "^TestEventQueueNamesMatchTheGeneratedTopology$", MARK_CONSUMER_MERGE,
    ),
    (
        "M2",
        "事件面只 `New` 不 `Run`（把那句 `go func(){ sourceDone <- sourceEvents.Run(...) }`"
        "换成 `_ = sourceCtx`）：inbound 包与 domain 包的用例**一条都不会红**，"
        "因为它们判的是构造出来的 Runner，不是进程里的循环。用户后果："
        "删了记忆，依据回链还指着那一行，而 `defer closeInbound(sourceEvents…)` "
        "在停机时等一条从没跑过的 goroutine 的 done 通道",
        (MAIN_GO, "\tgo func() { sourceDone <- sourceEvents.Run(sourceCtx) }()",
         "\t_ = sourceCtx // 变异：只 New 不 Run"),
        "go", CMD_PKG, "^TestMainSpawnsTheSourceDeletedWorker$", MARK_NO_RUN,
    ),
    (
        "M3",
        "摘掉传播调用（把 `PropagateEvidenceInvalidation` 藏进恒假分支）："
        "登记行照样翻成 `deleted`，但**摊给用户的那条结论一行都没改**——"
        "02 §37「Why do we know it」回链指着的 evidence 只剩一条已经不存在的来源，"
        "而 `out.Detail` 里那句「失效 0／只留痕 0」看起来像「本来就没有派生行」。"
        "⚠️ 打的是 domain 单测那一格（fake store，不需要库）；真库那一格由 S1 负责",
        (INVALIDATION_GO,
         "\t\tres, err := s.PropagateEvidenceInvalidation(ctx, p.UserID, evidenceID)\n",
         "\t\tvar res PropagationResult\n\t\tvar err error\n\t\tif false {\n"
         "\t\t\tres, err = s.PropagateEvidenceInvalidation(ctx, p.UserID, evidenceID)\n\t\t}\n"),
        "go", DOMAIN_PKG, "^TestInvalidateSource_FlipsOnceAndPropagates$", MARK_PROPAGATE,
    ),
    (
        "M4",
        "摘掉失败补偿（`restoreSourceRecordStatus` 不调，直接给 `rows=0, err=nil`）："
        "一条传播失败 ⇒ `out.RolledBack=false` 而库里停在 `deleted`，重投这一条又被"
        "`MarkSourceRecordDeletedOnce` 的单向闸跳过 ⇒ **缺口永远补不回来**，"
        "只能靠人读日志去手工补那一行。这一条打的是四件套里的「失败恢复」",
        (INVALIDATION_GO,
         "\trows, restoreErr := s.restoreSourceRecordStatus(ctx, p.UserID, out.SourceRecordID, statusBefore)",
         "\trows, restoreErr := int64(0), error(nil) // 变异：翻转不收回"),
        "go", DOMAIN_PKG, "^TestInvalidateSource_PropagationFailureRestoresTheFlip$", MARK_RESTORE,
    ),
    (
        "M5",
        "摘掉「体里自报的 userId 必须与 `x-user-id` 相等」那道门（条件加恒假）："
        "归属权威只剩 header，体里那个人被无视——最坏的形状是**替体里自报的那个人销账**"
        "或让一条拼错的生产者静默改掉别人的账。候选面同一条判据（`REASON_FOREIGN_USER_ID`）"
        "在 R5 已由 P4 打过，这里是事件面自己那一格",
        (SOURCE_DELETED_GO,
         '\tif got := strings.TrimSpace(body.UserID); got != "" && got != userID {',
         '\tif got := strings.TrimSpace(body.UserID); got != "" && got != userID && false {'),
        "go", INBOUND_PKG, "^TestDecodeEventRejectsContractViolations$", MARK_HEADER_BODY,
    ),
    (
        "S1",
        "真库闸：把 `GetSourceRecordByPointer` 的 SQL 里的 `user_id = $1` 摘掉。"
        "domain 层的 fake store 看不见这一格（假件按调用方给什么就查什么），"
        "只有真库会「按别人的指针查到受害者的登记行」。现网后果＝**跨用户删除**："
        "B 投一条指向 A 的记忆，A 的登记行被翻成 deleted、A 的 fact 被打成 invalidated，"
        "而 B 那侧 `Marked=true`，日志里连一句可疑都没有。"
        "⚠️ 需要 `TEST_DATABASE_URL`（缺 ⇒ store 包 skip ⇒ 本条按「未跑」登记，不算绿）。"
        "⚠️ 第一版直接把 `user_id = $1` 整段删掉，实测**不算这条判据的红**：PG 在解析"
        "预备语句时报 `could not determine data type of parameter $1`（42P18），"
        "两条真库闸一起死在「这条查询跑不动」，而不是死在「越权」那种形状。"
        "改成 `OR true`（谓词在、值仍被 typed，归属失效）之后，"
        "只有归属那一条红，且报的是「跨用户那次翻转了别人的账」——那才是本批要证的坏法",
        (SQLGO_GO,
         "const getSourceRecordByPointer = `-- name: GetSourceRecordByPointer :one\n"
         "SELECT id, user_id, source_type, source_table, source_id, occurred_at, status,\n"
         "       revision_of, created_at, updated_at\n"
         "FROM source_records\n"
         "WHERE user_id = $1 AND source_table = $2 AND source_id = $3",
         "const getSourceRecordByPointer = `-- name: GetSourceRecordByPointer :one\n"
         "SELECT id, user_id, source_type, source_table, source_id, occurred_at, status,\n"
         "       revision_of, created_at, updated_at\n"
         "FROM source_records\n"
         "WHERE (user_id = $1 OR true) AND source_table = $2 AND source_id = $3"),
        "go", STORE_PKG, "^TestInvalidateSourceIsScopedToTheOwner$", MARK_OWNER_SCOPE,
    ),
    # ── P1–P6：Python 那一侧，各自对应工作单点名的一条判据 ──
    (
        "P1",
        "把生产者开关的**默认档**翻成开（没写 `CLOSEN_SOURCE_EVENTS` 也开，并顺手给一个"
        "假地址让配置检查拦不住）：灰度 0 的现网里 broker 可能压根没起（拓扑由 Go 独占"
        "声明，D-R5-3），`backend/main.py` 一 import 就拨号 ⇒ 一次正常发布变成启动失败。"
        "⚠️ 只改 `enabled` 会让这条测先撞 `SwitchError`（红在配置自证而不是红在断言），"
        "所以靶写成「默认开＋默认地址」两行，让红的确实是 `sw.enabled is False` 那一格",
        (CONFIG_PY,
         '    enabled = _flag(lookup(ENV_SOURCE_EVENTS))\n    url = (lookup(ENV_URL) or "").strip()',
         '    enabled = True  # 变异：默认开\n    url = (lookup(ENV_URL) or "amqp://mutant").strip()'),
        "pytest", f"{PRODUCER_PY}::test_switch_is_off_by_default", "", MARK_DEFAULT_ON,
    ),
    (
        "P2",
        "把钩子排到提交**之前**（`run_post_commit_hooks` 挪到 `session.commit()` 上面）："
        "`live_support` 按 `source_records.status <> \'deleted\'` 算，Go 读到的还是翻转前的"
        "自己 ⇒ 传播变成空转，用户删了记忆而依据还挂在洞察上。这一格是本批的核心判据"
        "（探针是 `session.in_transaction()`，不是「记一个顺序数组」）",
        (DB_ROUTE_PY,
         "            if session is not None and session.in_transaction():\n"
         "                await session.commit()\n"
         "            await run_post_commit_hooks(request)\n",
         "            await run_post_commit_hooks(request)\n"
         "            if session is not None and session.in_transaction():\n"
         "                await session.commit()\n"),
        "pytest", f"{PRODUCER_PY}::test_delete_endpoint_publishes_only_after_the_commit",
        "", MARK_ORDER,
    ),
    (
        "P3",
        "把体里的第四个键漂成 snake_case（`sourceId` → `source_id`）：对侧"
        "`DisallowUnknownFields`（`source_deleted.go:98`）⇒ 「少一键」与「不是 JSON」"
        "是同一条永久拒绝，重投一万次也不会变成对，而每条正常删除都躺在 DLQ 里，"
        "账面停在翻转前。**默认字节只有对面读源码那条跨语言闸看得见**",
        (REPO_PY,
         '    {"schemaVersion", "userId", "sourceTable", "sourceId"}\n)',
         '    {"schemaVersion", "userId", "sourceTable", "source_id"}\n)'),
        "pytest", f"{CONTRACT_PY}::test_source_deleted_body_keys_match_the_go_struct",
        "", MARK_BODY_KEYS,
    ),
    (
        "P4",
        "幂等 id 掺进一个每次不同的盐：同一行删两次变成两条不同删除 ⇒ 对面那条幂等位"
        "形同虚设，重复传播。与 R5 的 P2 同一条坏法（那里的教训是**别掺 `id(locals())`**，"
        "CPython 会复用地址 ⇒ 空变异），这里用 uuid4",
        (REPO_PY, '    return f"srcdel-{digest[:40]}"',
         '    return f"srcdel-{digest[:40]}-{__import__(\'uuid\').uuid4().hex[:8]}"'),
        "pytest", f"{PRODUCER_PY}::test_message_id_is_deterministic_from_the_pointer",
        "", MARK_IDEMPOTENT,
    ),
    (
        "P5",
        "把提交之后的失败抛回请求路径（`except Exception` 那一支先 `raise`）："
        "数据已经提交，500 只会让用户**重试删除并拿到 404**，而那次 404 根本不登记钩子，"
        "于是这条删除彻底消失。本批的纪律是「失败不改响应，只留一条能 grep 的日志」",
        (SOURCE_EVENTS_PY,
         "    except Exception as exc:  # noqa: BLE001 - 见模块头第 3 条：提交之后的失败不许改响应\n"
         "        log.warning(",
         "    except Exception as exc:  # 变异：把提交之后的失败抛回请求路径\n"
         "        raise\n"
         "        log.warning("),
        "pytest", f"{PRODUCER_PY}::test_unexpected_error_does_not_escape", "",
        MARK_FAILURE_ESCAPES,
    ),
    (
        "P6",
        "入口没接线：`DELETE /memories/{id}` 不再登记钩子（保留 import 以免撞未使用告警）。"
        "`source_events.py` 的 30 多条单测**一条都不红**——它们判的是「登记了会发生什么」，"
        "不是「有没有登记」。用户后果与 M2 同形：删了记忆，账没改，而两侧都说自己没错",
        (ROUTER_PY,
         "    schedule_source_deleted(request, user_id=user_id, source_id=memory_id)\n",
         "    _ = schedule_source_deleted  # 变异：入口没接线\n"),
        "pytest", f"{PRODUCER_PY}::test_delete_endpoint_publishes_exactly_the_contracted_shape",
        "", MARK_ENTRY_WIRE,
    ),
    # ── X1／X2：只有真进程（r5interop ⇄ 真 broker ⇄ 真库）才看得见的两格 ──
    (
        "X1",
        "把互操作工具的 `-mode source` 那一档改名：工具编得过、Go 与 Python 的单测全绿，"
        "只有跨进程那条闸会报「对面这一档不存在」。后果是 T2-06 的交付面"
        "（**真 `inbound.NewSource` 认领这条队列**）在本仓失去唯一的证人——"
        "队列名、consumer 名、进门判据全在对面那个进程里，本工具是它唯一的调用方",
        (TOOL_GO,
         '\tcase "source":\n\t\treturn source(rep, sourceEventID, expectSettled, deadline)\n',
         '\tcase "source_moved":\n\t\treturn source(rep, sourceEventID, expectSettled, deadline)\n'),
        "pytest", E2E_NODE, "", MARK_MODE_ABSENT,
    ),
    (
        "X2",
        "跨进程独有：把每条结算行的 `invalidates` 恒报 0（差值退化成 0−0）。"
        "「进门一次、第二次被 inbox 挡在领域入口之前」这条判据在真链上失去观测面："
        "真双写权威表也只会显示 `invalidates: 0`。与 R5 的 M15 是同一格坏法，"
        "而且这里必须是**差值**——本工具这一档一次进程调用结算**两条**投递，"
        "写成累计会同时打红 `{0,1}` 那一格，红得看不出是哪条坏了。"
        "⚠️ 名片段用 `assert 0 == 1` 而不是 R5 那种 `'admits': 0`：本批这条断言的第二个"
        "参数是**两条** settled 行的列表，pytest 会把长 repr 截断成 `.....`，"
        "`'invalidates': 0` 根本不出现在输出里（实测学到）",
        (TOOL_GO,
         "\tbeforeInvalidate := r.invalidateCount()\n"
         "\tres, err := r.inner.ConsumeIdempotent(ctx, msg, fn)\n"
         "\tafterAdmit, afterFollow := r.admitCount(), r.followUpCount()\n"
         "\tafterInvalidate := r.invalidateCount()",
         "\tres, err := r.inner.ConsumeIdempotent(ctx, msg, fn)\n"
         "\tafterAdmit, afterFollow := r.admitCount(), r.followUpCount()\n"
         "\tbeforeInvalidate, afterInvalidate := 0, 0"),
        "pytest", E2E_NODE, "", MARK_INVALIDATES_ZERO,
    ),
]

# 绿闸：这些改动**不该**红。判据只认行为与形状——注释密度、日志措辞、
# 没有闸读过的文案都不算违规。红过一次就被当成噪音关掉的那类闸，本来就不该存在。
GREEN = [
    (
        "G1",
        "给 `source_invalidation.go` 的 `SourceRecordState` 说明加 12 行中文注释："
        "domain 包那批闸判的是翻转与传播的**行为**（计数、状态、Detail 里的关键短语），"
        "不是文本命中——否则「注释里写了恢复」也会红",
        (INVALIDATION_GO,
         "// SourceRecordState 是传播面需要的登记行三件（`Found=false` ＝没有这条登记行）。",
         "".join("// 绿闸占位注释，这一行不参与任何判定。\n" for _ in range(12))
         + "// SourceRecordState 是传播面需要的登记行三件（`Found=false` ＝没有这条登记行）。"),
        "go", DOMAIN_PKG, "",
    ),
    (
        "G2",
        "给 `source_deleted.go` 的 consumer 常量注释加 8 行：对面那条跨语言闸读的是"
        "「名字 = 串」的字面量，本包那条闸读的是值与幂等面——注释密度不在任何一张闸的判据里",
        (SOURCE_DELETED_GO,
         "\t// SourceDeletedConsumer 是这条链在 `inbox_messages.consumer` 里的唯一取值。",
         "".join("\t// 绿闸占位注释，这一行不参与任何判定。\n" for _ in range(8))
         + "\t// SourceDeletedConsumer 是这条链在 `inbox_messages.consumer` 里的唯一取值。"),
        "go", INBOUND_PKG, "",
    ),
    (
        "G3",
        "给 `source_events.py` 的 `_ensure_publisher` 加 10 行说明：生产者面判的是"
        "「懒开、复用、失败后不缓存坏连接」，写法与措辞不算违规",
        (SOURCE_EVENTS_PY, "    async def _ensure_publisher(self) -> Any:",
         "".join("    # 绿闸占位注释，这一行不参与任何判定。\n" for _ in range(10))
         + "    async def _ensure_publisher(self) -> Any:"),
        "pytest", PRODUCER_PY, "",
    ),
    (
        "G4",
        "把提交后钩子失败那条日志的键名从 `event=post_commit_hook_failed … error=%s` "
        "改成 `event=post_commit_hook_dropped … cause=%s`：没有任何闸读过这一行的措辞"
        "（本仓的日志面只有 broker 那一串有登记表），钉死它只会让一次合法改名变成红灯。"
        "⚠️ 若这一条红了，红的是「闸在数文案而不是行为」，要按缺陷登记而不是关掉绿闸",
        (DB_ROUTE_PY,
         '                "event=post_commit_hook_failed hook=%s error=%s",',
         '                "event=post_commit_hook_dropped hook=%s cause=%s",'),
        "pytest", PRODUCER_PY, "",
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
        cmd = ["go", "test", target, "-count=1", "-timeout", "120s"]
        if node:
            cmd += ["-run", node]
        cwd, timeout = GO_CORE, 1800
    else:
        assert node == "", "pytest 的节点 id 就写在 target 里，node 留空"
        cmd = [str(VENV_PY), "-m", "pytest", target, "-q", "--no-header", "-x",
               "-p", "no:randomly"]
        cwd, timeout = REPO, 1800
    proc = subprocess.run(cmd, cwd=cwd, env=GO_ENV, capture_output=True, text=True,
                          timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def is_red(code: int, out: str) -> bool:
    """Go 侧的「红」＝**受害用例真的跑起来并断言失败**（`--- FAIL:`）。

    编译坏掉（`[build failed]`）、「没匹配到用例」（`no tests to run`）与
    **skip**（`--- SKIP:`，真库前置没给）都**不算打红**：那些情况下红的是工具链或环境，
    不是闸。skip 尤其要挡——它的退出码是 0，会被读成「保持绿」，而 S1 那条恰恰依赖真库。
    """
    if code == 0:
        return False
    if "[build failed]" in out or "no tests to run" in out or "no test files" in out:
        return False
    if "--- SKIP:" in out and "--- FAIL:" not in out:
        return False
    return "--- FAIL:" in out


def is_red_pytest(code: int, out: str, target: str) -> bool:
    """pytest 侧的「红」＝**那一条节点真的跑起来并失败**（`FAILED <节点 id>`）。

    三种不是闸的红一律不认：收集期或 fixture 报错（`ERROR <节点 id>`，import 坏了、
    前置没给）、「节点没匹配到」（`no tests ran`）、skip（缺 broker／PG／go 工具链时
    e2e 整个文件 skip ⇒ 这条链一条都没跑，红的是环境）。

    ⚠️ 2026-10-19 实测学到：R5 那版用「取输出最后一段里有没有 `error`」来挡环境错，
    本批的受害节点叫 `test_unexpected_error_does_not_escape`——**名字里就带 error**，
    于是真的红被读成环境错（P5 第一次跑就被误判成「未打红」）。这里换成按节点 id 精确
    分家：`FAILED <target>` 才算红，`ERROR <target>` 才算环境。同一格隐患在
    `mutate_r5_gates.py` 里仍然存在（它现有靶的名字都不带 error，所以没爆），已登记不顺手修。
    """
    if code == 0:
        return False
    if "no tests ran" in out or "1 skipped" in out:
        return False
    if f"ERROR {target}" in out:
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

    env_missing = [k for k in ("TEST_DATABASE_URL",) if not os.environ.get(k, "").strip()]
    if env_missing:
        print(f"⚠️ 缺 {env_missing} ⇒ S1 会 skip，本脚本会把它报成「未打红」；"
              "按 DoD 那要登记成「未跑」而不是绿")
    if not (os.environ.get("CLOSEN_AMQP_URL", "").strip()
            and os.environ.get("DATABASE_URL", "").strip()):
        print("⚠️ 缺 CLOSEN_AMQP_URL／DATABASE_URL ⇒ X1／X2 会整文件 skip，"
              "同样只能登记成「未跑」")

    paths = sorted({case[2][0] for case in [*MUTATIONS, *GREEN]})
    with tempfile.TemporaryDirectory(prefix="mutate-t206-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            # 备份名带序号＋父目录：本批同时打 inbound/ 与 store/ 下的同名习惯，
            # 只用文件名会在 tmp 里互相覆盖，于是「恢复」把 A 写回 B，
            # 而 filecmp 比的是那份被覆盖后的备份——留残渣还不报错就是这一种错法。
            bak = Path(tmp) / f"{idx:02d}-{path.parent.name}-{path.name}"
            shutil.copy2(path, bak)
            originals[path] = bak

        def restore() -> None:
            for path, bak in originals.items():
                shutil.copy2(bak, path)
                assert filecmp.cmp(bak, path, shallow=False), f"{path} 恢复后字节不一致"

        red_ok = red_fail = green_ok = green_fail = 0
        failures: list[str] = []
        try:
            for cid, desc, mutation, runner, target, node, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
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
                    print(f"[{cid}] 打红 ✗（{runner} exit={code}，"
                          f"名片段 {marker!r} 命中={marker in out}）{desc}")
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

        # 收尾自证：被动过的文件回到绿。跑过全部 M／P／X 之后文件是写回恢复的，
        # 但残渣只有「真的再跑一遍」才验得出来，不能只信 filecmp。
        # 这一批有四种 runner 面，所以复核把四张受害包／文件都再跑一次：
        # domain 与 inbound（编排与契约串）、cmd（进程接线）、store（真库谓词）、
        # 对面两份 Python 闸（跨语言字面量＋生产者行为）。e2e 只在前置齐时跑：
        # 它是 X1／X2 的受害闸，也是「无残渣」在真进程那一面的唯一证据。
        for pkg in (DOMAIN_PKG, INBOUND_PKG, CMD_PKG):
            code, out = run("go", pkg, "")
            print(f"复核 {pkg}：{'绿' if code == 0 else '红（残留！）'}")
            if code != 0:
                print("  " + "\n  ".join(out.splitlines()[-12:]))
                return 1
        for target in (PRODUCER_PY, CONTRACT_PY):
            code, out = run("pytest", target, "")
            print(f"复核 {target}：{'绿' if code == 0 else '红（残留！）'}")
            if code != 0:
                print("  " + "\n  ".join(out.splitlines()[-12:]))
                return 1
        if not env_missing and os.environ.get("CLOSEN_AMQP_URL", "").strip() \
                and os.environ.get("DATABASE_URL", "").strip():
            code, out = run("pytest", E2E_PY, "")
            print(f"复核 {E2E_PY}：{'绿' if code == 0 else '红（残留！）'}")
            if code != 0:
                print("  " + "\n  ".join(out.splitlines()[-12:]))
                return 1
        else:
            print(f"复核 {E2E_PY}：**未跑**（缺 broker／DATABASE_URL 前置）")
        if not env_missing:
            code, out = run("go", STORE_PKG, "")
            print(f"复核 {STORE_PKG}：{'绿' if code == 0 else '红（残留！）'}")
            if code != 0:
                print("  " + "\n  ".join(out.splitlines()[-12:]))
                return 1
        else:
            print(f"复核 {STORE_PKG}：**未跑**（缺 TEST_DATABASE_URL）")

        print(f"\n计数：红闸 {red_ok} 打红 / {red_fail} 未打红；"
              f"绿闸 {green_ok} 保持绿 / {green_fail} 被误伤；异常 0")
        if failures:
            print("未通过：" + ",".join(failures))
        return 1 if (red_fail or green_fail) else 0


if __name__ == "__main__":
    sys.exit(main())
