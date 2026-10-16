#!/usr/bin/env python3
"""R5（入站面：Python 产出候选 → Go 消费并落权威表）的变异自证（2026-10-16）。

靶点口径与 R4 相同：每条变异只喂它该打红的那一张闸，且要求**报错原文里出现名片段**，
否则「红」可能是别的用例连带崩的假红。G1–G4 反过来证明这些闸不是假红制造机。

与 R4 的一处形状差异：R5 的受害闸**跨两种 runner**。R4 的闸全在 Go 侧，本批的归属面
有一半长在 Python 读 Go 源码的跨语言闸（`test_ai_mq_contract.py`）和真×真×真的跨进程闸
（`test_r5_interop_broker.py`）上——那两侧的红只能由 pytest 产生。所以每条用例多一个
`runner` 字段（"go" ⇒ target=包、node=-run 正则；"pytest" ⇒ target=节点 id、node 空），
`is_red` 也分两种判法（见各自 docstring）。

15 条 M 靶 ＋ 6 条 P 靶，分五族：
  · M1–M3／M5／M6  入站编排自身：解引用、reason 计数、快照复制、版本归一、kill switch 默认值；
  · M4／M12        契约面与那张封闭的「键→领域入口」表（D-R5-8 点名的反面）；
  · M8–M9          进程接线（defer 顺序与「零消费要说话」）；
  · M7／M10–M11／M13–M15  **只有跨语言／跨进程才看得见**的那几格：命名法、默认值、码表、
    header 副本、指标键名、每次投递的 admits 归属；
  · P1–P6          工作单 §R5-⑤ 点名的 M1／M2／M4／M5／M6／M7 那六条，落在 **Python 那一侧**
    （编号沿用工作单是为了让「七条候选」这条要求可核对；本文件的 M 编号是 Go 与跨语言那一半）。
    映射：P1←工作单 M1（未 confirm 就 ACK）、P2←M2（幂等 id 每次新生成）、P3←M4（非法走 requeue）、
    P4←M5（跨用户校验）、P5←M6（主动声明拓扑撞 406）、P6←M7（摘掉 spawn，源码闸才看得见）。
    工作单的 M3（evidence 落进 Admit）在本文件是 **M12**（那条落在 Go 的表上，Python 那一半
    由 `test_kind_mismatch_between_key_and_body_is_rejected` 已钉）。

M14／M15 坏掉时 Go 与 Python 两侧的单测**一条都不会红**（一个是 JSON 标签改名、
一个是把「这一条消息引起几次」写成「累计几次」），只有真进程互操作闸咬得住——这是本批
存在理由的自证。反过来 M13 那一格由对面读源码的闸咬，因为 Go 侧的副本与发布侧同值
并不代表与 Python 同值。

M14／M15 的取值来自本机环境（真 broker＋真 PG＋真对面进程），跑之前先清 broker：
R4-② 的 `TestRejectedMessageGoesToFirstRetryTierNotBackToPurposeQueue` 会在 30s 层留一条
不清理的残留（2026-10-16 登记，见 tasks.md），它会撞同一份库里的其它判据。
⚠️ `TEST_DATABASE_URL` 必须用**能登录的角色**：2026-10-16 实测本机 `closenai-pg-dev`
只有 `postgres` 可登录（容器没设 POSTGRES_USER，库名才是 closenai）；用 `closenai` 当用户时
Go 那一侧报 28P01，pytest 侧把这一格判成 fixture error 而**不算红**（见 `is_red_pytest`）。

口径照抄既有纪律（见 [[feedback-mutation-gates-no-concurrency]]、
[[feedback-mutation-restore-no-git-checkout]]、[[feedback-definition-of-done-tests]]）：
**串行跑**、copy2 备份写回 ＋ `filecmp` 逐字节复核、**不用 `git checkout`**。

用法（env 由调用方给，凭据不进这个文件）：
    ./.venv/bin/python scripts/mutate_r5_gates.py --check
    GOROOT=... PATH=... GO_BIN=... TEST_DATABASE_URL=... CLOSEN_AMQP_URL=... \\
      ./.venv/bin/python scripts/mutate_r5_gates.py
    ./.venv/bin/python scripts/mutate_r5_gates.py --only M1,P4
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

INBOUND_GO = GO_CORE / "internal" / "messaging" / "inbound" / "inbound.go"
DECODE_GO = GO_CORE / "internal" / "messaging" / "inbound" / "candidate_decode.go"
MAIN_GO = GO_CORE / "cmd" / "go-api" / "main.go"
TOOL_GO = GO_CORE / "tools" / "r5interop" / "main.go"

INBOUND_PKG = "./internal/messaging/inbound/"
CMD_PKG = "./cmd/go-api/"
CONTRACT_PY = "backend/tests/test_ai_mq_contract.py"
INTEROP_PY = "backend/tests/test_r5_interop_broker.py"
CONSUMER_PY = "backend/tests/test_ai_mq_consumer.py"

MQ = REPO / "backend" / "ai" / "mq"
PIPELINE_PY = MQ / "pipeline.py"
CONSUMER_MQ_PY = MQ / "consumer.py"
CONTRACT_MQ_PY = MQ / "contract.py"
PUBLISHER_PY = MQ / "publisher.py"
WORKER_PY = MQ / "worker.py"

# 本机 PATH 上的 go 是混合安装（见 [[go-toolchain-mixed-install]]），一编译就报版本不符，
# 所以点名 GOROOT 里的工具链；pytest 那侧还要 GO_BIN（互操作工具要用它编 Go 那一半）。
GOROOT = "/Users/fengyu/sdk/go1.27.1"
GO_BIN = f"{GOROOT}/bin/go"
GO_ENV = {**os.environ, "GOROOT": GOROOT, "PATH": f"{GOROOT}/bin:{os.environ.get('PATH', '')}",
          "GO_BIN": os.environ.get("GO_BIN", GO_BIN)}
VENV_PY = REPO / ".venv" / "bin" / "python"

# ── 名片段（受害闸的报错原文片段；改断言文案要同时改这里，否则绿的是脚本、红的是真相）──
MARK_DEREF = "契约级非法应走「留痕＋ACK」"
MARK_REASON_COUNT = "毒消息排查第一眼看的就是 reason 计数"
MARK_SNAPSHOT = "读侧能改写指标"
MARK_VERSION_GATE = "这条本该出局"
MARK_INT_VERSION = "该归成"
MARK_DEFAULT_ON = "默认值不关"
MARK_NAMING = "ReplaceAll"
MARK_SPAWN = "入站面没接上进程"
MARK_SILENT = "关着的时候退出路径没交代零消费"
MARK_BODY_BYTES = "64 == 65536"
MARK_VOCAB = "没有对应物也没有理由的码"
MARK_ENTRY = "合法载荷被拒"
MARK_HEADER_COPY = "x-schema_version"
MARK_METRIC_KEY = "rejectsByReason"
MARK_ADMITS = "'admits': 0"
MARK_NACK_ACK = "'ack' == 'requeue'"
MARK_IDEMPOTENT = "幂等失效"
MARK_REQUEUE = "action='requeue'"
MARK_FOREIGN = "action='ack'"
MARK_PASSIVE = "[False, True] == [True, True]"
MARK_SPAWN_PY = "for name in list("

# 每条：(id, 说明, (文件, 原文, 坏文), runner, target, node, 名片段)
MUTATIONS = [
    (
        "M1",
        "把 Handle 事务内那句解引用 `return *decErr` 退化成 `return decErr`：指针 "
        "`*PermanentRejection` 对值类型不可赋值 ⇒ `errors.As` 认不出来 ⇒ 契约级非法被读成"
        "**瞬态**。用户后果：一条永远不可能合法的毒候选在 30s/2m/10m 阶梯上无限撞库、"
        "永远不进 inbox 留痕面，运维查不到它被拒过",
        (INBOUND_GO, "\t\t\treturn *decErr\n", "\t\t\treturn decErr\n"),
        "go", INBOUND_PKG, "^TestDecodeRejectionReachesTheDomainClassifier$", MARK_DEREF,
    ),
    (
        "M2",
        "摘掉「连幂等键都拿不到」那一支的 reason 计数（只留 Rejected++）：这一支是"
        "唯一一条**写不进 inbox** 的拒绝路径，指标面是它仅剩的证人。用户后果：一条 "
        "message_id 都没有的产出消失了，排查时 rejects_by_reason 全空，看起来像没来过",
        (INBOUND_GO,
         "\t\tr.count(func(s *Stats) {\n\t\t\ts.Rejected++\n"
         "\t\t\ts.RejectsByReason[preErr.Code]++\n\t\t})\n",
         "\t\tr.count(func(s *Stats) { s.Rejected++ })\n"),
        "go", INBOUND_PKG, "^TestMissingMessageIDNeverReachesDomain$", MARK_REASON_COUNT,
    ),
    (
        "M3",
        "把 `Stats()` 里 map 的**复制**改成共享引用：调用方一改就改到 worker 的内部账。"
        "用户后果：一个只读观测点（指标抓取、互操作工具）能把拒绝计数写歪，"
        "于是「这条消息被拒过几次」这件事失去证人",
        (INBOUND_GO,
         "\tout.RejectsByReason = make(map[string]int, len(r.stat.RejectsByReason))\n"
         "\tfor k, v := range r.stat.RejectsByReason {\n\t\tout.RejectsByReason[k] = v\n\t}\n",
         "\tout.RejectsByReason = r.stat.RejectsByReason\n"),
        "go", INBOUND_PKG, "^TestStatsSnapshotCopiesTheReasonMap$", MARK_SNAPSHOT,
    ),
    (
        "M4",
        "摘掉 `x-schema-version` 那道门（条件加一个恒假）：**不认识的版本被放行**并按 v1 的 "
        "schema 解。用户后果：对面升了 proto 而 header 跟着升时，这条链照收并按旧字节 "
        "形态落进权威表（D-R5-8b 的有界黑盒判据当场失效），写进去就收不回。"
        "⚠️ 这一条第一次打**不算红**（包全绿），原因是门有两道：表里原来那几格只改 header、"
        "体内还是夹具的 \"1\"，于是 `checkBodyVersion`（candidate_decode.go:169）用同一个 "
        "reason 兜住了它们。已按实测补了「两侧一起升到 2」那一格（inbound_test.go:859 区），"
        "它才是 :134 这道门的独立证人；这道门的冗余本身也登记在 R5b",
        (DECODE_GO,
         "\tif sv := d.HeaderVersion(HeaderSchemaVersion); sv != SupportedSchemaVersion {",
         "\tif sv := d.HeaderVersion(HeaderSchemaVersion); sv != SupportedSchemaVersion && false {"),
        "go", INBOUND_PKG, "^TestDecodeRejectsContractViolations$", MARK_VERSION_GATE,
    ),
    (
        "M5",
        "删掉 `HeaderVersion` 的 int／int32 分支（只留 int64 等）：这正是 2026-10-16 "
        "真进程互操作实测到的那一格——我们自己的发布侧（`amqp/publisher.go:130`）写上线的"
        "就是 int。用户后果：「Go 发、Go 收」的重放路径**静默**变成 "
        "unsupported_schema_version，inbox 里多一行 failed，而没人报错",
        (INBOUND_GO,
         "\tcase int:\n\t\treturn strconv.Itoa(v)\n\tcase int32:\n"
         "\t\treturn strconv.Itoa(int(v))\n\tcase int64:\n",
         "\tcase int64:\n"),
        "go", INBOUND_PKG, "^TestVersionHeaderAcceptsIntWireForm$", MARK_INT_VERSION,
    ),
    (
        "M6",
        "把入站面 kill switch 的**默认值**翻成「开」（`Enabled: isOn(getenv(EnvWorker))` ⇒ 恒真）："
        "不给 `CLOSEN_CANDIDATE_WORKER` 的部署也会拨号、声明拓扑、消费并把候选写进权威表。"
        "用户后果：灰度 0 的现网当场变成「候选由 Go 写进 canonical 表」，而这条链从没被验收跑过。"
        "⚠️ 第一版打的是 `Run` 里那句 `if !sw.Enabled`（摘成恒假），实测**不算红**：那条测的 fake "
        "拨号器按「关着不该拨号」返回 error，摘掉开关后 Run 进入「拨号失败 ⇒ 等 reconnectWait ⇒ "
        "重试」的死循环，120s 包超时把整包打成 panic 而不是断言红。默认值那一格是同一条验收的"
        "可断言形态，所以打这里",
        (INBOUND_GO, "\t\tEnabled:      isOn(getenv(EnvWorker)),", "\t\tEnabled:      true,"),
        "go", INBOUND_PKG, "^TestSwitchVocabularyIsTheSharedOne$", MARK_DEFAULT_ON,
    ),
    (
        "M7",
        "`messageTypeFor` 不再把点换成下划线：`candidate_pattern` 变成 `candidate.pattern`。"
        "后果分两半——这边Decode 当场拒（message_type_mismatch），而对面按自己的规则写，"
        "于是**每一条**候选都被拒；闸是 Python 读 Go 源码那一条，它判的是那条规则的**实现**",
        (DECODE_GO,
         '\treturn strings.ReplaceAll(routingKey, ".", "_")',
         '\treturn routingKey + "_requested"'),
        "pytest", f"{CONTRACT_PY}::test_go_inbound_message_type_rule_is_the_python_one",
        "", MARK_NAMING,
    ),
    (
        "M8",
        "把 `defer closeInbound(...)` 的 defer 摘掉：这一行在启动瞬间就把 worker 取消掉、"
        "进程退出时再没人等它收尾。**inbound 包那 30 条闸看不见调用点**，"
        "所以这一条只能由 cmd 包的源码闸兜住（这就是它存在的理由，与 R4 的 M6 同形）",
        (MAIN_GO, "defer closeInbound(candidates,", "closeInbound(candidates,"),
        "go", CMD_PKG, "^TestMainSpawnsTheCandidateWorker$", MARK_SPAWN,
    ),
    (
        "M9",
        "把零消费时的退出日志换成一句不报 `received=0` 的：kill switch 关着的部署"
        "在停机路径上**不交代**「这条面一条都没收」。用户后果：运维看着「进程正常退出」"
        "以为入站在跑，而队列里已经积了几千条候选",
        (MAIN_GO,
         '\t\tlog.Printf("go-api: candidate worker 未启动或零消费就退出了：received=0 '
         'dials=%d（kill switch 关着时这就是预期形态）",\n\t\t\tst.Dials)\n',
         '\t\tlog.Printf("go-api: candidate worker 已退出")\n'),
        "go", CMD_PKG, "^TestCloseInboundExitsFastWhenSwitchOff$", MARK_SILENT,
    ),
    (
        "M10",
        "把 `DefaultMaxBodyBytes` 从 65536 改成 64：发的那侧按自己的上限放行、收的那侧按"
        "更小的上限拒 ⇒ 每条「合法但偏大」的候选都变成永久拒绝＋一行留痕，"
        "看起来像对面的 bug。**默认值也是契约**，而它只在对面的跨语言闸里有证人",
        (INBOUND_GO, "\tDefaultMaxBodyBytes = 65536", "\tDefaultMaxBodyBytes = 64"),
        "pytest", f"{CONTRACT_PY}::test_go_inbound_process_defaults_match_python",
        "", MARK_BODY_BYTES,
    ),
    (
        "M11",
        "把 `ReasonKindMismatch` 的串从 `candidate_kind_mismatch` 改成 `kind_mismatch`："
        "同名的串只出现一次时 Python 那侧的例外表查不到它 ⇒ 排查时一条 x-reject-reason "
        "指不回两边的代码，运维要维护两份「这个串是哪侧发的」记忆",
        (DECODE_GO,
         '\tReasonKindMismatch        = "candidate_kind_mismatch"',
         '\tReasonKindMismatch        = "kind_mismatch"'),
        "pytest", f"{CONTRACT_PY}::test_go_inbound_reason_codes_use_the_shared_vocabulary",
        "", MARK_VOCAB,
    ),
    (
        "M12",
        "把 D-R5-8 那张表的 evidence 那一格从 `AdmitEvidence` 改成 `Admit`（工作单点名的"
        "就是这一格）：一条「依据」被塞进「洞察」的领域语义写进 canonical 表。"
        "两侧各自的单测都只验自己那张表完整，**这里先红的是解码分支**",
        (DECODE_GO,
         '\t"candidate.evidence":  EntryAdmitEvidence,',
         '\t"candidate.evidence":  EntryAdmit,'),
        "go", INBOUND_PKG, "^TestDecodeMapsEvidenceCandidate$", MARK_ENTRY,
    ),
    (
        "M13",
        "把入站侧 header 常量副本 `x-schema-version` 写成 `x-schema_version`：Go 自己的那条"
        "副本闸（钉回发布侧）会一起改，只有对面读源码那一条能发现**三方**漂了。"
        "后果：每一条候选都因缺版本被拒，两侧日志各自都说自己没错",
        (INBOUND_GO,
         '\tHeaderSchemaVersion = "x-schema-version"',
         '\tHeaderSchemaVersion = "x-schema_version"'),
        "pytest", f"{CONTRACT_PY}::test_go_inbound_wire_constants_match_python",
        "", MARK_HEADER_COPY,
    ),
    (
        "M14",
        "跨进程独有：把互操作工具的指标 JSON 键 `rejects_by_reason` 改成驼峰。"
        "Go 编译得过、inbound 包 30 条闸全绿、对面 Python 的单测也全绿——"
        "因为这一格只在**真进程报出来的那份字节**里存在。后果：R5-④ 的排查看不到"
        "「按 reason 分类」这一格，毒消息排查回到猜",
        (TOOL_GO,
         '"redials": s.Redials, "rejects_by_reason": s.RejectsByReason,',
         '"redials": s.Redials, "rejectsByReason": s.RejectsByReason,'),
        "pytest", f"{INTEROP_PY}::test_metric_surfaces_share_vocabulary", "", MARK_METRIC_KEY,
    ),
    (
        "M15",
        "跨进程独有：把 `admits` 的取值时机挪到调用**之后**（`before := after`），于是每条"
        "结算行都报 0。聚合计数分不清是哪条消息触发的，tasks.md 逐字要的那条验收——"
        "「重复消息第二次领域入口不被调用」——在这条真链上失去判据：观测面失明后，"
        "真双写权威表也只会显示 `admits: 0`，而两侧账都说自己没错。"
        "⚠️ 两次改版都是实测学到的：① `Admits: after`（扔掉 before）＝未使用变量 ⇒ "
        "`go build` 失败 ⇒ 对面 `go_tool` fixture 直接 skip，而 skip 不是红；"
        "② `before := 0`（把差值退化成累计）在本机**打不红且不是闸的问题**——这个工具"
        "每一次进程调用只结算一条消息，单条会话里「累计」与「差值」同值，那是个**等价变异**。"
        "所以这一条改打「观测面恒报 0」那一格，证人换到整链那条（`admits == 1`）",
        (TOOL_GO,
         "\tbefore := r.admitCount()\n\tres, err := r.inner.ConsumeIdempotent(ctx, msg, fn)\n\tafter := r.admitCount()",
         "\tres, err := r.inner.ConsumeIdempotent(ctx, msg, fn)\n\tafter := r.admitCount()\n\tbefore := after"),
        "pytest", f"{INTEROP_PY}::test_chain_outbox_to_canonical", "", MARK_ADMITS,
    ),
    # ── P1–P6：工作单 §R5-⑤ 点名的 M1／M2／M4／M5／M6／M7 那六条，落在 Python 那一侧 ──
    (
        "P1",
        "工作单 §R5-⑤ 的 M1「candidate 未 confirm 就 ACK 原命令」：把发布失败那一支从 "
        "REQUEUE 改成 ACK ⇒ 候选没拿到 broker 确认，原件却被 ack 掉，**永久丢候选**"
        "（17:91-112 逐字禁的就是这个）。用户后果：一条洞察再也不会出现，而两侧账都写着已处理",
        (PIPELINE_PY,
         '    except Exception as exc:  # noqa: BLE001 — DeliveryError/PublishError/连接错同一处置\n'
         '        return Decision(\n'
         '            REQUEUE, REASON_PUBLISH_UNCONFIRMED, f"{type(exc).__name__}: {exc}"\n'
         '        )\n',
         '    except Exception as exc:  # noqa: BLE001 — 变异：没确认也 ACK\n'
         '        return Decision(\n'
         '            ACK, REASON_PUBLISH_UNCONFIRMED, f"{type(exc).__name__}: {exc}"\n'
         '        )\n'),
        "pytest", f"{CONSUMER_PY}::test_no_ack_when_broker_nacks_the_candidate", "", MARK_NACK_ACK,
    ),
    (
        "P2",
        "工作单的 M2「幂等 id 改成每次新生成」：把 D-R5-5 的派生掺进一个每次不同的盐。"
        "后果不是报错而是**幂等失效**：同一命令的两次投递产出两条互不相干的候选，"
        "Go 的 (message_id, consumer) 唯一键永远撞不上 ⇒ 重复生效，而 inbox 看起来一切正常。"
        "⚠️ 第一版掺的是 `id(locals())`，实测**不算红**：CPython 会把上一轮释放的字典地址"
        "原样复用 ⇒ 每次拿到同一个「盐」⇒ 派生仍然确定，那是个空变异。这里用 uuid4 才是"
        "「每次新生成」的那条坏法",
        (CONTRACT_MQ_PY, '    return f"cand-{digest[:40]}"',
         '    return f"cand-{digest[:40]}-{__import__(\'uuid\').uuid4().hex[:8]}"'),
        "pytest", f"{CONTRACT_PY}::test_candidate_message_id_is_deterministic_across_calls",
        "", MARK_IDEMPOTENT,
    ),
    (
        "P3",
        "工作单的 M4「非法 payload 走 requeue 而不是 reject」：契约级非法改投阶梯。"
        "后果＝毒消息在 30s/2m/10m 上无限撞库撞 LLM（14 §8 明令禁止的那个形态），"
        "而且永远不进终态 DLQ，排查时看不到它",
        (CONSUMER_MQ_PY,
         "            return Decision(pipeline.REJECT, exc.reason, exc.detail)",
         "            return Decision(pipeline.REQUEUE, exc.reason, exc.detail)"),
        "pytest", f"{CONSUMER_PY}::test_illegal_command_is_parked_in_final_dlq", "", MARK_REQUEUE,
    ),
    (
        "P4",
        "工作单的 M5「跨用户校验摘掉」：body 自报 userId 与 header 归属冲突时不再拒。"
        "后果最坏的一条：**按 A 用户跑、产出记到 B 头上**——归属只认 Envelope 这条纪律"
        "（15:49）被绕过去，而 canonical 表里看不出任何异常",
        (CONTRACT_MQ_PY, "    if foreign:\n        raise ContractError(\n"
                         "            REASON_FOREIGN_USER_ID,",
         "    if False:  # 变异：吃掉体内自报的 user\n        raise ContractError(\n"
         "            REASON_FOREIGN_USER_ID,"),
        "pytest", f"{CONSUMER_PY}::test_illegal_command_is_parked_in_final_dlq", "", MARK_FOREIGN,
    ),
    (
        "P5",
        "工作单的 M6「Python 消费者改成主动声明拓扑」：passive 断言换成带参数的主动声明。"
        "后果是 R4 实测撞过的 406（两份声明必撞一份表），更坏的是「对面 worker 没起」"
        "被静默建成第二套拓扑——那正是 D-R5-3 要避免的形态",
        (PUBLISHER_PY,
         "        return await channel.declare_exchange(name, aio_pika.ExchangeType.TOPIC, "
         "passive=True)",
         "        return await channel.declare_exchange(name, aio_pika.ExchangeType.TOPIC, "
         "passive=False)"),
        "pytest", f"{CONSUMER_PY}::test_topology_is_only_asserted_never_created", "",
        MARK_PASSIVE,
    ),
    (
        "P6",
        "工作单的 M7「把 spawn 摘掉」：四个命令队列只订阅前三个 ⇒ 漏掉那条队列的命令"
        "永远没人消费，而 worker 日志一切正常。**消费回调这一侧看不见调用点**，"
        "所以只能由 worker 的源码闸兜住（与 Go 侧 M8 是对称的两条）",
        (WORKER_PY, "                for name in topology.CONSUMED_QUEUES:",
         "                for name in list(topology.CONSUMED_QUEUES)[:3]:"),
        "pytest", f"{CONSUMER_PY}::test_worker_source_is_wired_to_the_four_queues", "", MARK_SPAWN_PY,
    ),
]

# 绿闸：这些改动**不该**红。判据只认行为与形状——注释密度、没有闸读过的间隔与超时、
# 日志措辞都不算违规。红过一次就被当成噪音关掉的那类闸，本来就不该存在。
GREEN = [
    (
        "G1",
        "给 inbound.go 的 HeaderString 说明加 12 行中文注释：包级闸判的是 Decode 的行为，"
        "不是文本命中——否则「注释里写了 x-schema-version」也会红",
        (INBOUND_GO, "// HeaderString 取一个 header 的字符串值。",
         "".join(f"// 绿闸占位注释 {i}：这一行不参与任何判定。\n" for i in range(12))
         + "// HeaderString 取一个 header 的字符串值。"),
        "go", INBOUND_PKG, "",
    ),
    (
        "G2",
        "把会话建不起来时的回退间隔从 1s 放宽到 2s：没有任何闸读过 reconnectWait——"
        "它既不是 env 键也不进指标面，钉死它的数值只会让一次合法调参变成红灯",
        (INBOUND_GO, "\treconnectWait = time.Second", "\treconnectWait = 2 * time.Second"),
        "go", INBOUND_PKG, "",
    ),
    (
        "G3",
        "把默认拨号超时从 10s 调到 12s：进程面闸判的是「拨号预算必须是正值」，"
        "而对面那条跨语言默认值闸只读 MaxBodyBytes 与 Prefetch 两格",
        (INBOUND_GO, "\tDefaultDialTimeout  = 10 * time.Second",
         "\tDefaultDialTimeout  = 12 * time.Second"),
        "go", INBOUND_PKG, "",
    ),
    (
        "G4",
        "给 candidate_decode.go 的版本常量加两行注释（不改代码）：词表闸读的是 "
        "`Reason* = "
        "\"...\"` 的字面量与那张表的键，判不到注释",
        (DECODE_GO, "// SupportedSchemaVersion 是本消费者认得的 schema 版本，**只有一条**。",
         "// 绿闸说明：下面两行是给人读的，任何闸都不读它们。\n"
         "// 版本号的产生路径只有 publisher 一处，切 protobuf 时与 ContentTypeJSON 一起递增。\n"
         "// SupportedSchemaVersion 是本消费者认得的 schema 版本，**只有一条**。"),
        "go", INBOUND_PKG, "",
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
        cmd = [str(VENV_PY), "-m", "pytest", target, "-q", "--no-header", "-x"]
        cwd, timeout = REPO, 1800
    proc = subprocess.run(cmd, cwd=cwd, env=GO_ENV, capture_output=True, text=True,
                          timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def is_red(code: int, out: str) -> bool:
    """Go 侧的「红」＝**受害用例真的跑起来并断言失败**。

    编译坏掉（`[build failed]`）与「没匹配到用例」（`no tests to run`）都**不算打红**：
    那些情况下红的是工具链，不是闸。三件齐判才认。
    """
    if code == 0:
        return False
    if "[build failed]" in out or "no tests to run" in out or "no test files" in out:
        return False
    return "--- FAIL:" in out


def is_red_pytest(code: int, out: str, target: str) -> bool:
    """pytest 侧的「红」＝**那一条节点真的跑起来并失败**（`FAILED <节点 id>`）。

    三种不是闸的红一律不认：收集期报错（`error` 而不是 `failed`，import 或 fixture 坏了）、
    「节点没匹配到」（`no tests ran`）、前置缺失导致的 skip（broker／PG env 没给 ⇒
    这条链一条都没跑，红的是环境）。skip 尤其要挡：它退出码为 0，会被读成「保持绿」。
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
    with tempfile.TemporaryDirectory(prefix="mutate-r5-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            # 备份名带序号＋父目录：本批同时打 inbound/ 下两个文件与两个 main.go，
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

        # 收尾自证：被动过的文件回到绿。跑过 M1–M15 之后文件是写回恢复的，
        # 但残渣只有「真的再跑一遍」才验得出来，不能只信 filecmp。
        # 这一批的残渣有一种是**只有对面那条闸**才验得出的（M5 那种「Go 侧全绿而
        # 对面的跨语言闸才认得默认值」），所以复核里除了两个 Go 包还带两份 Python
        # 闸：test_ai_mq_contract.py（纯读源码）与 test_ai_mq_consumer.py（P1–P6 的
        # 受害闸都在这里，用 fake 不需要 broker/PG）。
        # 不带 test_r5_interop_broker.py：那条要真 broker＋真 PG，属于「环境绿」而非
        # 「无残渣」的证据，它的实测计数在收尾 R5-⑥ 里单独报。
        for pkg in (INBOUND_PKG, CMD_PKG):
            code, out = run("go", pkg, "")
            print(f"复核 {pkg}：{'绿' if code == 0 else '红（残留！）'}")
            if code != 0:
                print("  " + "\n  ".join(out.splitlines()[-12:]))
                return 1
        for target in (CONTRACT_PY, CONSUMER_PY):
            code, out = run("pytest", target, "")
            print(f"复核 {target}：{'绿' if code == 0 else '红（残留！）'}")
            if code != 0:
                print("  " + "\n  ".join(out.splitlines()[-12:]))
                return 1

        print(f"\n计数：红闸 {red_ok} 打红 / {red_fail} 未打红；"
              f"绿闸 {green_ok} 保持绿 / {green_fail} 被误伤；异常 0")
        if failures:
            print("未通过：" + ",".join(failures))
        return 1 if (red_fail or green_fail) else 0


if __name__ == "__main__":
    sys.exit(main())
