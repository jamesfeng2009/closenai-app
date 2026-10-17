#!/usr/bin/env python3
"""批 4（D-G15／D-G16／D-G17／D-G18／D-G20）的变异自证（2026-10-17）。

本批正题是**口径失真**，所以这些靶比前几批更刁：一条说「全部每小时」的注释不会自己崩，
它只是骗下一个人。判据形状（工作单 §六.1）也只有两种可判的落点——
**失真字面量在源里归零** ＋ **替进去的实测口径带日期**。于是本文件要证的不是「代码能动」，
而是「那句谎一旦被写回来，闸当场就红」。

四族，每族红的是不同的东西：

* **W 族（D-G15／G16／G20 口径闸）**——把三句谎分别写回原处，看 `test_doc_wording_batch4.py`
  的八条各自认领一条。这一族最容易被写成假闸：needle 是我自己造的串，扫错根也能得「0 命中」。
  所以除 W1…W8 外，还配了 GR1／GR2／GR3／GR4 四条**绿闸**——其中 GR2／GR3 专证 `impl_only`
  那层排除（`backend/tests/**` 与 `*_test.go`）真的在生效：如果哪天有人把它去掉，
  这条绿会立刻变红，而 W3 会因为闸文件自己的存在而**永久红**（那种红只会教人删闸）。
* **S 族（D-G17 SafetyStatus 第四态）**——风险面是「一份枚举写在三处」：契约源、生成物、
  Python 常量、Go 常量。所以 S1（改 .proto 号位）**故意不重导生成物**，让
  `test_generated_python_enum_is_in_sync_with_source` 红成「号位漂移」——这正是
  「只改 proto 不重导 ⇒ `rpc/server.py` 当场 AttributeError」那格的判据版。
  S5／S6 是**同一破坏的两语证人**（Go 加第五个常量，Go 的值集闸与 Python 的扫源闸各红一次）。
  S15／S16 同理：把「不是三态之一」写回 gate.go，Go 的措辞归零闸与 Python 的
  `SURFACE_FILES` 扫描闸必须都认得它。
* **S17** 是本族唯一一条**多编辑变异**：`test_four_state_wording_really_landed`（正向证人）
  的阈值是「四态」在点名面里 ≥6 次，实测 7 次 ⇒ 改一处仍绿，必须成对改才红。
  这条证的正是「反向归零闸可能扫了个空集」这件事有人守。
* **T 族（D-G18 诚实计数）**——三处说谎点各打一次：`relay_batch` 的 `relayed += 1`
  回到「发了就算成功」（T1/T2/T3 三条 pytest 同刻红，因为 Python 的计数面在 service
  与 HTTP 两面各有证人）、`_relay_to_posthog` 的三个失败分支各退成 True（T4/T5/T6）、
  成功尾巴退成 False（T7，正向对照：它红说明前三条不是「永远回 False」混过去的）。
  T8 是**夹具形状闸**：把 `relayed:100` 改成 99，互补式当场不成立 ⇒ 证这条闸真的在按
  请求原文数事件，而不是读响应里的两个数互相印证（那样改哪个都绿）。
  T9…T13 是 Go 的对面五格。

⚠️ 纪律（见 [[feedback-mutation-gates-no-concurrency]]、
[[feedback-mutation-restore-no-git-checkout]]、[[feedback-definition-of-done-tests]]）：
**串行跑**、`copy2` 备份写回 ＋ `filecmp(shallow=False)` 逐字节复核、**不用 `git checkout` 恢复**；
备份名用**整条 repo 相对路径**展平（本批同时打 `backend/main.py` 与 `backend/routers/cron.py`、
`domain/gate.go` 与 `domain/candidate.go`，只留文件名会在 tmp 里互相覆盖，
而「恢复」一旦把 A 写回 B，filecmp 比的是被覆盖后的备份，红绿都不可信）。

**每条变异先跑基线**（改之前那一格必须是绿的）：基线就红说明环境或前置出了问题，
这种「红」记成未跑，不混进「已打红」——否则缺 env 的那几条会被算成我的功劳。
同一道理，收尾复核跑的是**整包** Go 用例，`./internal/httpapi/` 里有五条 N8 回放测要读库：
`TEST_DATABASE_URL` 指向未迁移的空库时，它们报的是 `relation ... does not exist`，
那是环境而不是残渣，却会被打印成「红（残留！）」——所以本文件开场先探一次表在不在，
缺就中止、一个文件都不碰（实测撞过一次：口令 env-only 从 `docker inspect` 现场取，
库名却指错了 `postgres` 而不是本机已迁移的那个）。

用法（Go 工具链走 GOROOT，本机 PATH 上的 go 是混合安装；T1/T2/T3 与收尾复核要**已迁移**的真库）：
    ./.venv/bin/python scripts/mutate_batch4_gates.py --check
    TEST_DATABASE_URL=postgresql://…（指向已 alembic upgrade head 的库）CLOSEN_REQUIRE_PG=1 \\
    GOROOT=/Users/fengyu/sdk/go1.27.1 \\
    ./.venv/bin/python scripts/mutate_batch4_gates.py
    ./.venv/bin/python scripts/mutate_batch4_gates.py --only W1,S5,T9
"""
from __future__ import annotations

import argparse
import ast
import filecmp
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GO_CORE = REPO / "go-core"

GOROOT = "/Users/fengyu/sdk/go1.27.1"
GO_BIN = f"{GOROOT}/bin/go"
GO_ENV = {
    **os.environ,
    "GOROOT": GOROOT,
    "PATH": f"{GOROOT}/bin:{os.environ.get('PATH', '')}",
    "GO_BIN": os.environ.get("GO_BIN", GO_BIN),
}
VENV_PY = REPO / ".venv" / "bin" / "python"

# ── 靶文件（全部点名，不 rglob）──────────────────────────────────────────
ISSUE_MD = REPO / "问题.md"
MAIN_PY = REPO / "backend" / "main.py"
CRON_PY = REPO / "backend" / "routers" / "cron.py"
VERCEL = REPO / "vercel.json"
SHARE_PY = REPO / "backend" / "services" / "share_service.py"
MEMORY_PY = REPO / "backend" / "services" / "memory_service.py"
SAFETY_PY = REPO / "backend" / "ai" / "safety.py"
RPC_SERVER_PY = REPO / "backend" / "ai" / "rpc" / "server.py"
ANALYTICS_PY = REPO / "backend" / "services" / "analytics_service.py"
PROTO = REPO / "contracts" / "proto" / "closenai_common.proto"
FIXTURE = GO_CORE / "testdata" / "n8_contract.json"
CANDIDATE_GO = GO_CORE / "internal" / "domain" / "candidate.go"
GATE_GO = GO_CORE / "internal" / "domain" / "gate.go"
PGATE_GO = GO_CORE / "internal" / "domain" / "proactive_gate.go"
PROACTIVE_GO = GO_CORE / "internal" / "domain" / "proactive.go"
GATE_TEST_GO = GO_CORE / "internal" / "domain" / "gate_test.go"
AICLIENT_GO = GO_CORE / "internal" / "aiclient" / "grpc.go"
BATCH_GO = GO_CORE / "internal" / "httpapi" / "analytics_batch.go"
POSTHOG_GO = GO_CORE / "internal" / "posthog" / "posthog.go"

WORDING_PY = "backend/tests/test_doc_wording_batch4.py"
UNSAFE_PY = "backend/tests/test_safety_unsafe_both_sides.py"
ANALYTICS_T_PY = "backend/tests/test_analytics_service.py"
ANALYTICS_API_T_PY = "backend/tests/test_analytics_api.py"
N8_T_PY = "backend/tests/test_n8_batch1_contract.py"

DOMAIN_PKG = "./internal/domain/"
AICLIENT_PKG = "./internal/aiclient/"
HTTPAPI_PKG = "./internal/httpapi/"
POSTHOG_PKG = "./internal/posthog/"

# D-G17 那条闸点名的 14 个面（与 `test_safety_unsafe_both_sides.py` 的 SURFACE_FILES 同源）。
# S17 要按这份清单做全文替换，所以两处必须一起改——清单漂移时 S17 会打不红，那是好事。
UNSAFE_SURFACE_RELS = [
    "backend/ai/safety.py",
    "backend/ai/rpc/server.py",
    "backend/ai/mq/contract.py",
    "backend/tests/test_r3_safety_tri.py",
    "backend/tests/test_r3_rpc_server.py",
    "backend/tests/test_r3_grpc_server_interop.py",
    "scripts/r3_interop_server.py",
    "contracts/proto/closenai_common.proto",
    "contracts/proto/closenai_ai_rpc.proto",
    "go-core/internal/domain/candidate.go",
    "go-core/internal/domain/gate.go",
    "go-core/internal/domain/proactive_gate.go",
    "go-core/internal/aiclient/grpc.go",
    "go-core/internal/messaging/inbound/candidate_decode.go",
]

# 需要真库的三条（relay_batch 的 db 夹具）：缺 env 时按「未跑」记账，**不把 skip 读成绿**。
DB_ONLY = {"T1", "T2", "T3"}
DB_ENV_OK = "TEST_DATABASE_URL" in os.environ

# 拼出来写的失真串：本文件一旦整句抄进去，自己的扫描面（口径闸读的就是这些源文件）
# 会被自己命中——所以 W 族的「新文本」用拼接，而不是把原句当常量摆在这里。
LIE_PRD_ALL = "PRD 未实现功能已全部" + "实现"
LIE_FIVE_MIN = "每" + " 5 分钟"


# ── 变异表：(id, 说明, 编辑列表, runner, target, node, 名片段) ────────────
MUTATIONS: list[tuple] = [
    # ══ W 族：口径闸（D-G15 ／ D-G16 ／ D-G20）═════════════════════════════
    (
        "W1",
        "把「" + LIE_PRD_ALL + "」这句写回 问题.md 的修复进度横幅（就是 2026-08-04 那句、"
        "§三 的遗留说明当场推翻它的那句）：这一格红说明「文档自称全做了」这件事是可判的，"
        "而不是靠人记得去对遗留说明",
        [(ISSUE_MD,
          "PRD 未实现功能于 2026-08-04 补入主线（含 5 个 alembic 迁移）——⚠️ 但「全部」这层意思"
          "到 2026-10-17 复核时**不成立**，四项仍是零实现，逐条出处见 §三 的「2026-10-17 复核」块",
          LIE_PRD_ALL + "（2026-08-04，含 5 个 alembic 迁移）")],
        "pytest", f"{WORDING_PY}::test_prd_all_done_claim_is_gone_and_replaced_by_dated_review",
        "", "失真原句又回到",
    ),
    (
        "W2",
        "§三 复核块的第 3 项改成不带主语：把 `anniversary` 与那两个文案键一起换成「该关键词」。"
        "日期还在、块还在、四项还是四条，但没人能再核对「哪一项」——这条防的是**复核块退化成"
        "态度声明」。⚠️ 只改第一行不够：块里还剩 `memory.anniversary_one` 两处，"
        "子串判据照样绿（实测打红失败一次后改成整块替换，这条本身就是「判据要挑到最难的形状」）",
        [(ISSUE_MD,
          "> 3. **周年推送**：`anniversary`（忽略大小写）在 `backend/services/push/`、\n"
          ">    `backend/services/cron_service.py`、`backend/services/message_generator.py` "
          "**各 0 命中**；\n"
          ">    全后端唯一出现处是 `backend/services/share_service.py` 的记忆卡文案键\n"
          ">    （`memory.anniversary_one` / `memory.anniversary_n`）——那是**分享卡标题**，不是推送链路。",
          "> 3. **周年推送**：该关键词（忽略大小写）在 `backend/services/push/`、\n"
          ">    `backend/services/cron_service.py`、`backend/services/message_generator.py` "
          "**各 0 命中**；\n"
          ">    全后端唯一出现处是 `backend/services/share_service.py` 的分享卡文案键\n"
          ">    （见该文件）——那是**分享卡标题**，不是推送链路。")],
        "pytest", f"{WORDING_PY}::test_review_block_names_the_four_zero_items", "",
        "复核块没点名 anniversary",
    ),
    (
        "W3",
        "在 `memory_service.py` 里落下一个真的 `ttl_days` 赋值（TTL 归档的实现痕迹）："
        "文档登记的是「0 命中」，代码有了就必须在这里红，而不是等下一个人按文档去查一个不存在的字段。"
        "这条证的正是「文档不许跑在代码前面」",
        [(MEMORY_PY, '"""\nimport json\nimport logging\nimport math\n',
          '"""\nimport json\nimport logging\nimport math\n\n'
          "ttl_days = 90  # 变异证人：D-G15 第 2 项的实现痕迹\n")],
        "pytest", f"{WORDING_PY}::test_the_four_reviewed_zero_claims_still_match_the_code", "",
        "现在有实现痕迹",
    ),
    (
        "W4",
        "把 D-G16 删掉的那行 DayNo 注释加回 `ProactiveCandidate` 的字段块：结构体仍然没有这个字段、"
        "`decodeProactive` 仍然不赋值，所以**代码一行没坏**，只有注释重新开始骗人。"
        "这一族最阴的地方就在这儿——它不影响任何测试，只影响下一个人往哪找",
        [(PROACTIVE_GO,
          "\t// AnchorAt 是「关联当下」那一层的时间锚点，必须来自权威行（goal 的 target_at、",
          "\t// DayNo 供 milestone/剧本类引用现网 journey 的 day_no（04 §3 的 Goal due 类比）。\n"
          "\t// AnchorAt 是「关联当下」那一层的时间锚点，必须来自权威行（goal 的 target_at、")],
        "pytest", f"{WORDING_PY}::test_proactive_candidate_no_longer_advertises_a_dayno_field", "",
        "又出现了 DayNo",
    ),
    (
        "W5",
        "把 /health 的 docstring 改回「由 Vercel Cron " + LIE_FIVE_MIN + " GET 保热」："
        "vercel.json 一条 /health 都没有（W6 逐条数过），而这句谎会直接烤进 openapi.json、"
        "再烤进对接文档。红的是那句措辞，不是端点行为",
        [(MAIN_PY, "健康检查端点（供外部探活/手动调用）。",
          "健康检查端点（供 Vercel Cron " + LIE_FIVE_MIN + " GET 保热）。")],
        "pytest", f"{WORDING_PY}::test_health_endpoint_stops_claiming_a_minutely_cron", "",
        "又写了分钟级保热",
    ),
    (
        "W6",
        "反向：真的往 vercel.json 加一条 /health 排程。这时那句措辞不再是谎，"
        "闸必须在这里红并要求**连 docstring 与本闸一起改写**——口径闸不许把「现在对了」"
        "悄悄记成「一直对」",
        [(VERCEL, '  "crons": [\n    {\n      "path": "/api/v1/cron/daily-message",',
          '  "crons": [\n    {\n      "path": "/api/v1/health",\n      "schedule": "*/5 * * * *"\n'
          '    },\n    {\n      "path": "/api/v1/cron/daily-message",')],
        "pytest", f"{WORDING_PY}::test_vercel_schedule_list_has_no_health_and_no_minutely_entry",
        "", "现在真的排程了 /health",
    ),
    (
        "W7",
        "从 cron.py 的头注释端点清单里删掉 `curriculum` 那一行——就是 D-G20 实测抓出的那格漏项"
        "（16 条清单漏 1 条）。12 条／7 条那两个数还是对的、日期还在、句子还像话，"
        "只有清单少一条 ⇒ 这正是「看起来已核对」的形状",
        [(CRON_PY,
          "    GET  /api/v1/cron/curriculum      — Growth Curriculum 周规划预生成（PRD §5.17）\n", "")],
        "pytest",
        f"{WORDING_PY}::test_cron_module_header_states_the_measured_cadence_and_lists_every_route",
        "", "cron.py 头注释的端点清单漏了",
    ),
    (
        "W8",
        "给 main.py 加一个真的 `settings.health_check_interval` 读取点：D-G20 拍的是"
        "「只登记不顺手删」，所以这一格既不许悄悄变成旋钮、也不许悄悄被消费。"
        "判据走 `ast`（剥掉注释后数读取点），因此加注释骗不过它，加一行真代码必红",
        [(MAIN_PY, "app = FastAPI(",
          "_HEALTH_WARM_MS = settings.health_check_interval * 60 * 1000  # 变异证人\n\n"
          "app = FastAPI(")],
        "pytest",
        f"{WORDING_PY}::test_health_check_interval_has_no_consumer_and_stays_registered", "",
        "被这些地方读走了",
    ),

    # ══ S 族：D-G17 SafetyStatus 第四态（一处枚举写在三处）══════════════════
    (
        "S1",
        "把契约源里 `SAFETY_STATUS_UNSAFE` 的号位从 4 改成 5（additive 枚举不许动旧号位，"
        "V3 15 §8）：这条钉的是「号位以 .proto 为唯一真相源」",
        [(PROTO, "SAFETY_STATUS_UNSAFE = 4;", "SAFETY_STATUS_UNSAFE = 5;")],
        "pytest", f"{UNSAFE_PY}::test_proto_declares_the_four_states_plus_unspecified", "",
        "SafetyStatus 的值集变了",
    ),
    (
        "S2",
        "**同一破坏的不重导证人**：改完 .proto 不跑 codegen。这一格是本批最实在的一种失败——"
        "`rpc/server.py` 引用的 `common_pb2.SAFETY_STATUS_UNSAFE` 还是 4，"
        "线上发出的是 4、契约说的是 5，两侧对账全绿而字节已经分叉",
        [(PROTO, "SAFETY_STATUS_UNSAFE = 4;", "SAFETY_STATUS_UNSAFE = 5;")],
        "pytest", f"{UNSAFE_PY}::test_generated_python_enum_is_in_sync_with_source", "",
        "号位漂移",
    ),
    (
        "S3",
        "把 Python 常量改成 `not_safe`：值集形状闸先红（proto 名字去掉前缀是小写 unsafe）。"
        "这一格改什么行为都不会变——只是第四态在跨进程时再也表达不出来",
        [(SAFETY_PY, 'SAFETY_UNSAFE = "unsafe"', 'SAFETY_UNSAFE = "not_safe"')],
        "pytest", f"{UNSAFE_PY}::test_python_constants_cover_proto_names_verbatim", "", "not_safe",
    ),
    (
        "S4",
        "把 Go 的常量改成 `not_safe`（对面证人）：Go 侧入站解码比的就是这个串，"
        "Python 回投 \"unsafe\" 时读不到 ⇒ 走 default 变成「判定缺失」，"
        "拦住是对的、身份说成 unavailable 是错的，指标面按错身份计数",
        [(CANDIDATE_GO, 'SafetyUnsafe      SafetyStatus = "unsafe"',
          'SafetyUnsafe      SafetyStatus = "not_safe"')],
        "pytest", f"{UNSAFE_PY}::test_go_constants_match_python_constants", "", "not_safe",
    ),
    (
        "S5",
        "把契约里 `SAFETY_STATUS_UNSAFE = 4;` 整行删掉（＝第四态压根没进契约）："
        "Go 的值集闸按 `len(proto) != len(goSide)+1` 当场发现两侧不是同一份值集。"
        "注意这条**只红在 Go**——Python 的 `_proto_safety_enum()` 也读源，但它的期望 dict "
        "是写死的，红在 S1 那一格；两侧读的是同一份源、用的是不同的尺",
        [(PROTO, "  SAFETY_STATUS_UNSAFE = 4;\n", "")],
        "go", DOMAIN_PKG, "^TestSafetyStatusValueSetMatchesProto$", "两侧不是同一份值集",
    ),
    (
        "S5b",
        "反方向（Go 有、契约没有）：把 Go 的 unsafe 常量值改成 `not_safe`。"
        "Go 的值集闸认得这种格子——「这个态永远收不到，是假格子」；"
        "S4 是同一次破坏的 Python 侧证人，两语各红一次才算钉住「一份枚举两处写」",
        [(CANDIDATE_GO, 'SafetyUnsafe      SafetyStatus = "unsafe"',
          'SafetyUnsafe      SafetyStatus = "not_safe"')],
        "go", DOMAIN_PKG, "^TestSafetyStatusValueSetMatchesProto$", "是假格子",
    ),
    (
        "S6",
        "S5 那一刀的 **Python 侧证人**（`test_go_constants_match_python_constants` 直接读 candidate.go）："
        "Go 加了第五个常量，Python 的四个串对不上五格 ⇒ 不装 Go 工具链的 CI 也守得住这条口径",
        [(CANDIDATE_GO, '\tSafetyUnavailable SafetyStatus = "unavailable"',
          '\tSafetyUnavailable SafetyStatus = "unavailable"\n'
          '\tSafetyPending     SafetyStatus = "pending"')],
        "pytest", f"{UNSAFE_PY}::test_go_constants_match_python_constants", "", "pending",
    ),
    (
        "S7",
        "D-G17 的**正题**：把 rpc 映射表里 unsafe 那格退回 crisis（等于第四态没加）。"
        "线上两者同形 ⇒ Go 拦下内容时说成「用户处在危险中」，会牵出 04 §21 的危机让位流程",
        [(RPC_SERVER_PY, '"unsafe": common_pb2.SAFETY_STATUS_UNSAFE,',
          '"unsafe": common_pb2.SAFETY_STATUS_CRISIS,')],
        "pytest", f"{UNSAFE_PY}::test_rpc_unsafe_and_crisis_are_distinct_on_the_wire", "",
        "unsafe 与 crisis 在线上同形",
    ),
    (
        "S8",
        "映射表里**整格删掉** unsafe：不像 S7 那样与 crisis 同值，而是落到 `.get()` 的 None 分支"
        "⇒ 回 UNSPECIFIED 之外的 UNAVAILABLE，把「拦下内容」说成「判定缺失」。"
        "S7 管「挤进别的态」，这一条管「漏了一格」——两种漏法都常见",
        [(RPC_SERVER_PY, '            "unsafe": common_pb2.SAFETY_STATUS_UNSAFE,\n', "")],
        "pytest", f"{UNSAFE_PY}::test_rpc_emits_each_declared_state[unsafe]", "", "assert 3 == 4",
    ),
    (
        "S9",
        "把 banned 那一面从 unsafe 挪回 crisis：三面里最典型的一格。"
        "`_tri_calls()` 的**形状**判据先红（八元组第二位变 CRISIS），"
        "而不是只测「这一次调用返回什么」——同时改掉行为测的断言就能骗过行为面，骗不过形状面",
        [(SAFETY_PY, 'return _tri(SAFETY_UNSAFE, "banned", [banned], advice_blocked=False)',
          'return _tri(SAFETY_CRISIS, "banned", [banned], advice_blocked=False)')],
        "pytest", f"{UNSAFE_PY}::test_violation_faces_return_unsafe_not_crisis", "",
        "('CRISIS', 'banned')",
    ),
    (
        "S10",
        "同一刀的第二证人：`crisis` 在四态面里被用了不止一次 ⇒ 「crisis 只剩危机那一格」这句"
        "登记当场过期。两条一起红才说明违规面挤回来这件事有两把尺",
        [(SAFETY_PY, 'return _tri(SAFETY_UNSAFE, "banned", [banned], advice_blocked=False)',
          'return _tri(SAFETY_CRISIS, "banned", [banned], advice_blocked=False)')],
        "pytest", f"{UNSAFE_PY}::test_crisis_name_appears_once_in_tri_body", "",
        "crisis 在四态面里被用了不止一次",
    ),
    (
        "S11",
        "把 Go 第 6 关 unsafe 那一支的措辞改成危机措辞（码还是 safety_blocked，只是把话说成危机）："
        "这正是批 4 要修的缺陷本身，也是本批判据的**反证**——拦对不等于说对",
        [(GATE_GO, '"违规内容 %v 拦下（非危机，不进危机链路，D-G17）", c.Safety.Categories)',
          '"危机信号 %v 拦下（非危机，不进危机链路，D-G17）", c.Safety.Categories)')],
        "go", DOMAIN_PKG, "^TestGateSafetyBlocksUnsafeWithoutCrisisWording$",
        "unsafe 被说成了危机信号",
    ),
    (
        "S12",
        "反向：动 crisis 那一支的措辞。本批**不该碰它**（正证：真危机一个字没动），"
        "所以这一刀红的是「我把不该动的动了」",
        [(GATE_GO, '"危机信号 %v 退出常规管道（03 §14 Safety）", c.Safety.Categories)',
          '"违规内容 %v 退出常规管道（03 §14 Safety）", c.Safety.Categories)')],
        "go", DOMAIN_PKG, "^TestGateCrisisStillUsesCrisisWording$", "危机措辞被改掉了",
    ),
    (
        "S13",
        "加第四态时最容易顺手做的事：把 default 分支改成放行（`!= SafetyOK` 退成恒假）。"
        "未知态于是变成「安全」——D-8 的 fail-closed 在这里断掉，而三态与四态的单测都还绿",
        [(GATE_GO,
          '\tif c.Safety.Status != SafetyOK {\n'
          '\t\treturn ReasonSafetyUnavailable, fmt.Sprintf("未知安全态 %q", c.Safety.Status)\n\t}',
          "\tif false {\n"
          '\t\treturn ReasonSafetyUnavailable, fmt.Sprintf("未知安全态 %q", c.Safety.Status)\n\t}')],
        "go", DOMAIN_PKG, "^TestGateUnknownSafetyStatusStillFailClosed$",
        "期望 reject(safety_unavailable)",
    ),
    (
        "S14",
        "主动触达第 5 关：把 unsafe 那一支写成危机那套措辞（含「让位给安全流程」）。"
        "后果是「给用户发一句你还好吗」这种触达被内容违规触发——让位是危机才有的待遇",
        [(PGATE_GO, '"命中违规内容 %v：主动触达不发（拦下，非危机，不进安全流程，D-G17）",',
          '"命中危机信号 %v：主动触达管线让位给安全流程（04 §21）",')],
        "go", DOMAIN_PKG, "^TestProactiveGateUnsafeBlocksWithoutSafetyHandoff$",
        "unsafe 不该走危机的安全流程让位",
    ),
    (
        "S15",
        "把「不是三态之一」这句原话写回 gate.go 的注释：SafetyStatus 已经四态了，"
        "这句话开始骗人。批 4 的第五种形状就是**钉住那句文字**——假话比缺话更难发现，"
        "因为它会让所有代码面一直绿着",
        [(GATE_GO, "// 第 6 关安全：危机硬覆盖 + D-8 的 fail-closed。",
          "// 变异证人：SafetyStatus 不是三态之一。\n"
          "// 第 6 关安全：危机硬覆盖 + D-8 的 fail-closed。")],
        "go", DOMAIN_PKG, "^TestStaleThreeStateWordingIsGone$", "里还留着",
    ),
    (
        "S16",
        "同一刀的 **Python 侧证人**：`test_stale_three_state_wording_is_gone` 扫的是 14 个点名文件"
        "（含 gate.go）。Go 的措辞闸只看三个源文件，Python 这条看的是整个跨语言面——"
        "漏掉任何一个文件都会在这里红",
        [(GATE_GO, "// 第 6 关安全：危机硬覆盖 + D-8 的 fail-closed。",
          "// 变异证人：SafetyStatus 不是三态之一。\n"
          "// 第 6 关安全：危机硬覆盖 + D-8 的 fail-closed。")],
        "pytest", f"{UNSAFE_PY}::test_stale_three_state_wording_is_gone", "", "不是三态之一",
    ),
    (
        "S17",
        "正向证人的靶：把点名面里**全部**「四态」措辞撤掉（改成「状态」）。这条一开始我只打算改 "
        "safety.py 两处，实测打红失败才知道：`SURFACE_FILES` 面上「四态」共 **19 处**（含四份 "
        "R3 测试面与两份 proto），阈值是 ≥6，改两处掉到 17 仍然绿。"
        "所以本条用的是「全文替换」这一格变异（`edit` 的第 4 位＝\"all\"），"
        "它证的正是这条闸存在的理由——归零闸（S16）可能扫了个空集，必须有反向计数守着",
        [(REPO / rel, "四态", "状态", "all") for rel in UNSAFE_SURFACE_RELS],
        "pytest", f"{UNSAFE_PY}::test_four_state_wording_really_landed", "", "在点名面里只出现",
    ),
    (
        "S18",
        "只改服务端不改客户端：删掉 Go aiclient 解码里的 UNSAFE case。"
        "Python 明明回了 unsafe，Go 读到的是「契约外的值」⇒ 降级成判定缺失，"
        "主动触达与洞察管道都会按 unavailable 计数。这条是「两侧同批」的客户端那一半",
        [(AICLIENT_GO,
          "\tcase closenaiv1.SafetyStatus_SAFETY_STATUS_UNSAFE:\n"
          "\t\treturn domain.SafetyUnsafe, true\n", "")],
        "go", AICLIENT_PKG, "^TestSafetyStatusDecodeCoversFourStates$",
        "TestSafetyStatusDecodeCoversFourStates/UNSAFE",
    ),

    # ══ T 族：D-G18 诚实计数（Python 三面 ＋ 夹具 ＋ Go 五面）═══════════════
    (
        "T1",
        "回到改造前的形状：先 `relayed += 1` 再外发（发送结果不参与计数）。"
        "`seen` 三条还是照样试过，所以只有计数说谎 ⇒ 部分成功那格报成 3/0",
        [(ANALYTICS_PY,
          "            props = _anonymize(ev.properties)\n"
          "            if _relay_to_posthog(user_id, ev.event, props, ev.occurred_at):\n"
          "                relayed += 1\n",
          "            props = _anonymize(ev.properties)\n"
          "            _relay_to_posthog(user_id, ev.event, props, ev.occurred_at)\n"
          "            relayed += 1\n")],
        "pytest", f"{ANALYTICS_T_PY}::test_relay_batch_counts_only_the_events_that_left", "",
        "'relayed': 3",
    ),
    (
        "T2",
        "同一刀的「全失败」证人：一条都没送出去时报成 relayed=2。"
        "这一格最贵——它等于对分析平台宣称「这批落了」，而那批一条都没出门",
        [(ANALYTICS_PY,
          "            props = _anonymize(ev.properties)\n"
          "            if _relay_to_posthog(user_id, ev.event, props, ev.occurred_at):\n"
          "                relayed += 1\n",
          "            props = _anonymize(ev.properties)\n"
          "            _relay_to_posthog(user_id, ev.event, props, ev.occurred_at)\n"
          "            relayed += 1\n")],
        "pytest", f"{ANALYTICS_T_PY}::test_relay_batch_never_invents_a_relay", "", "'relayed': 2",
    ),
    (
        "T3",
        "同一刀的 **HTTP 面**证人（`test_endpoint_reports_failure_as_dropped`）："
        "计数谎话必须走到 200 响应体里才算用户可见，service 层红而端点面绿是不够的",
        [(ANALYTICS_PY,
          "            props = _anonymize(ev.properties)\n"
          "            if _relay_to_posthog(user_id, ev.event, props, ev.occurred_at):\n"
          "                relayed += 1\n",
          "            props = _anonymize(ev.properties)\n"
          "            _relay_to_posthog(user_id, ev.event, props, ev.occurred_at)\n"
          "            relayed += 1\n")],
        "pytest", f"{ANALYTICS_API_T_PY}::test_endpoint_reports_failure_as_dropped", "",
        "'relayed': 2",
    ),
    (
        "T4",
        "SDK 没装那一支退成 `return True`：本机实测就没装 posthog，所以这一格是**现网最常见的形状**——"
        "静默 no-op 却被计成成功。它也是 T1/T2/T3 的前置：没有返回值，计数就没有依据",
        [(ANALYTICS_PY, 'logger.debug("posthog 未安装，事件中继未发生")\n        return False',
          'logger.debug("posthog 未安装，事件中继未发生")\n        return True')],
        "pytest", f"{ANALYTICS_T_PY}::test_relay_returns_false_when_posthog_missing", "",
        "assert True is False",
    ),
    (
        "T5",
        "没配 api_key 那一支退成 True：配了 SDK 不配 key 与没装 SDK 的用户后果一模一样"
        "（一条都没出门），但代码路径完全不同，所以各要一格",
        [(ANALYTICS_PY, "if not settings.posthog_api_key:\n        return False",
          "if not settings.posthog_api_key:\n        return True")],
        "pytest", f"{ANALYTICS_T_PY}::test_relay_returns_false_without_api_key", "",
        "assert True is False",
    ),
    (
        "T6",
        "capture 抛异常那一支退成 True：网络失败/端点 5xx 时不阻塞主流程是对的，"
        "但「不阻塞」不等于「成功了」。这一格红的时候丢的是分析数据，不是消息",
        [(ANALYTICS_PY, 'logger.warning("posthog 中继失败: %s", exc)\n        return False',
          'logger.warning("posthog 中继失败: %s", exc)\n        return True')],
        "pytest", f"{ANALYTICS_T_PY}::test_relay_returns_false_when_capture_raises", "",
        "assert True is False",
    ),
    (
        "T7",
        "反向对照：把成功尾巴退成 `return False`。如果这一条不红，上面三条「必须 False」的判据"
        "就只是「永远回 False」在自证——正向证人不是可选项",
        [(ANALYTICS_PY, "\n    return True\n", "\n    return False\n")],
        "pytest", f"{ANALYTICS_T_PY}::test_relay_returns_true_when_capture_succeeds", "",
        "assert False is True",
    ),
    (
        "T8",
        "夹具形状：把已同意那批的 `relayed:100` 改成 99（请求原文还是 100 条）。"
        "这条证的正是「期望值不来自被测代码」：互补式是按 `request_raw` 现数的，"
        "所以把响应里的两个数同时改小骗不过它，只动一个才红",
        [(FIXTURE, '\\"relayed\\":100', '\\"relayed\\":99')],
        "pytest", f"{N8_T_PY}::test_fixture_analytics_counts_add_up_on_the_success_face", "",
        "两格不互补",
    ),
    (
        "T9",
        "Go 侧同一刀：`relayEvents` 先 `relayed.Add(1)` 再看 err（失败还是照样打日志，"
        "只是不再 return）。三条子用例里「全部失败」那格会报 3/0 而不是 0/3，"
        "而 `relay.count()` 还是 3 ⇒ 外发次数对、计数谎，正是最难发现的那种",
        [(BATCH_GO,
          "\t\t\tif err := s.batch1.Relay.Capture(ctx, payload); err != nil {\n"
          '\t\t\t\t_, _ = fmt.Fprintf(os.Stderr, "analytics: posthog relay failed: %v\\n", err)\n'
          "\t\t\t\treturn\n\t\t\t}\n\t\t\trelayed.Add(1)",
          "\t\t\trelayed.Add(1)\n"
          "\t\t\tif err := s.batch1.Relay.Capture(ctx, payload); err != nil {\n"
          '\t\t\t\t_, _ = fmt.Fprintf(os.Stderr, "analytics: posthog relay failed: %v\\n", err)\n'
          "\t\t\t}")],
        "go", HTTPAPI_PKG, "^TestAnalyticsBatchCountsFollowTheRelayResult$",
        "want {relayed:0, dropped:3}",
    ),
    (
        "T10",
        "没有中继通道（Relay==nil，等价于「没装 posthog／没配 key」）时 `return len(events)`："
        "改造前 Go 回的就是 `relayed:N`。响应仍是 200、前端照样 markSynced + cleanupSynced，"
        "于是那批事件从设备上被清掉而分析平台一条没有，事后无任何痕迹",
        [(BATCH_GO, "if s.batch1.Relay == nil || len(events) == 0 {\n\t\treturn 0\n\t}",
          "if s.batch1.Relay == nil || len(events) == 0 {\n\t\treturn len(events)\n\t}")],
        "go", HTTPAPI_PKG, "^TestAnalyticsBatchWithoutRelayChannelCountsNothing$",
        "没有中继通道时该如实回报没投递",
    ),
    (
        "T11",
        "把 consent 判据里的 `!profile.AnalyticsConsent` 摘掉（改成只判用户存不存在）："
        "未同意用户的批次被真的外发一次，同时计数还说 relayed=3。"
        "这条守的是改造时**没动**的那一支——把「按发送结果计数」写成「先外发再看 consent」"
        "是一步之遥的事",
        [(BATCH_GO, "if !profile.Found || !profile.AnalyticsConsent {", "if !profile.Found {")],
        "go", HTTPAPI_PKG, "^TestAnalyticsBatchUnconsentedSkipsRelay$", "未同意的批次外发了",
    ),
    (
        "T12",
        "`Capture` 在没配 key 时 `return nil`（而不是 ErrNotConfigured）：一次网络都不碰、"
        "却说「送出去了」。这一格红的是**返回值语义**——handler 的计数现在只看它，"
        "所以谎只要在这一层发生就一路传到 200 响应体",
        [(POSTHOG_GO, "\tif !c.Configured() {\n\t\treturn ErrNotConfigured\n\t}",
          "\tif !c.Configured() {\n\t\treturn nil\n\t}")],
        "go", POSTHOG_PKG, "^TestCaptureWithoutKeyIsAnError$", "want ErrNotConfigured",
    ),
    (
        "T13",
        "反向：把状态码判据改成恒假 ⇒ 5xx 也回 nil。若只测「未配置回错」而不测"
        "「2xx 才回 nil」，那「永远回错」与「永远回对」都能绿。这一条是 T12 的镜像证人",
        [(POSTHOG_GO, "if res.StatusCode < 200 || res.StatusCode >= 300 {", "if false {")],
        "go", POSTHOG_PKG, "^TestCaptureNilOnlyWhenDelivered$", "502 没收下",
    ),
]

# GREEN：不该弄红任何闸的中性改动（证这些闸不是「只要动文件就红」的假红制造机）。
GREEN: list[tuple] = [
    (
        "GR1",
        "在**没被点名**的 share_service.py 的注释里写「" + LIE_FIVE_MIN + "」：口径闸判的是"
        "「这句话被当成事实写在 main.py／cron.py／契约里」，不是「全仓禁词」。"
        "八条判据必须全绿，否则闸已经从「纠谎」漂移成「不许有人提 5 分钟」",
        [(SHARE_PY,
          '            if years_ago == 1:\n'
          '                return copy.t("share", "memory.anniversary_one", language)',
          '            if years_ago == 1:\n'
          '                # 变异证人：' + LIE_FIVE_MIN + '做一次心跳，与调度面无关。\n'
          '                return copy.t("share", "memory.anniversary_one", language)', 1)],
        "pytest", WORDING_PY, "",
    ),
    (
        "GR2",
        "在 `backend/tests/**` 里写出 `coexist_with`：W3 那条「0 命中」的判据只看实现面。"
        "这一格必须绿——它是 `impl_only` 那层排除的证人：哪天有人把它去掉，"
        "闸文件自己的存在就会让 W3 永久红（那种红只会教人删闸）",
        [(REPO / ANALYTICS_T_PY, "\nimport pytest\n", "\nimport pytest\n\n"
          'COEXIST_PROBE = "coexist_with"  # 变异证人：测试面不该被实现痕迹判据算进去\n', 1)],
        "pytest", WORDING_PY, "",
    ),
    (
        "GR3",
        "在 `*_test.go` 里写出 `coexist_with`（排除面的 Go 那一半）：同一把尺必须两侧都放过测试面",
        [(GATE_TEST_GO, "func assertReject(",
          "// 变异证人：coexist_with 在测试面不算实现痕迹。\nfunc assertReject(", 1)],
        "pytest", WORDING_PY, "",
    ),
    (
        "GR4",
        "在 /health 的 docstring 里加一句带日期的话（不含那句谎）：证口径闸判的是措辞，"
        "不是「这个文件被改过就算红」",
        [(MAIN_PY, "健康检查端点（供外部探活/手动调用）。",
          "健康检查端点（供外部探活/手动调用）。变异证人：2026-10-17 加一句不涉调度面的话。")],
        "pytest", WORDING_PY, "",
    ),
    (
        "GR5",
        "在 safety.py 的两处靶之间加一行注释（不动「四态」措辞、不动值集）："
        "D-G17 那十四条判据读的是常量表、_tri 的形状与映射面，加注释不该红任何一条。"
        "同一条也顺带证 S17 的红来自「四态」计数而不是「文件被改过」",
        [(SAFETY_PY, "def _tri(", "# 变异证人：注释不该弄红任何判据。\ndef _tri(", 1)],
        "pytest", UNSAFE_PY, "",
    ),
]


def _apply(edits: list) -> None:
    for edit in edits:
        path, old, new = edit[0], edit[1], edit[2]
        want = edit[3] if len(edit) > 3 else 1
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        if want == "all":
            if n >= 1:
                path.write_text(src.replace(old, new), encoding="utf-8")
        else:
            assert n == want, f"{path.name}：锚点命中 {n} 次，要求 {want} 次\n{old[:90]!r}"
            path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _sanity(edits: list) -> str | None:
    """变异体自己必须先合法：坏掉的语法会让 Go 报 build failed、pytest 报收集错误，
    那种「红」红的是工具链而不是闸（而且会把我打坏的锚点字符串一路带到恢复）。
    所以每条变异在跑闸之前先按文件类型解析一遍。"""
    for edit in edits:
        path = edit[0]
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            try:
                ast.parse(text)
            except SyntaxError as exc:
                return f"{path.name}：变异体不是合法 Python（{exc.msg} @ line {exc.lineno}）"
        elif path.suffix == ".go":
            # 只判**语法**：`gofmt -e` 把错误写 stderr、格式化结果写 stdout；
            # 不加 -l（那会把「缩进没对齐」也算失败，而我加常量的变异体本来就不保证 gofmt 干净）。
            proc = subprocess.run(
                [f"{GOROOT}/bin/gofmt", "-e", str(path)],
                capture_output=True, text=True,
            )
            if proc.stderr.strip():
                return f"{path.name}：变异体 gofmt -e 报错\n{proc.stderr[:300]}"
        elif path.suffix == ".json":
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                return f"{path.name}：变异体不是合法 JSON（{exc.msg}）"
    return None


def _verify_anchor(edits: list) -> str | None:
    for edit in edits:
        path, old = edit[0], edit[1]
        want = edit[3] if len(edit) > 3 else 1
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if want == "all":
            # 全文替换按整条变异**合计**至少命中一次来自证（见 _verify_anchor）：
            # S17 遍历的是 D-G17 闸点名的 14 个面，里面本就有几个面没写「四态」这个词。
            pass
        elif n != want:
            return f"命中 {n} 次（要 {want}）"
    if any((e[3] if len(e) > 3 else 1) == "all" for e in edits):
        total = sum(e[0].read_text(encoding="utf-8").count(e[1])
                    for e in edits if (e[3] if len(e) > 3 else 1) == "all")
        if total == 0:
            return f"全文替换的 needle {edits[0][1]!r} 在 {len(edits)} 个面上 0 命中"
    return None


def run(runner: str, target: str, node: str) -> tuple[int, str]:
    if runner == "go":
        cmd = [GO_BIN, "test", target, "-count=1", "-timeout", "300s"]
        if node:
            cmd += ["-run", node]
        cwd, timeout = GO_CORE, 900
    else:
        assert node == "", "pytest 的节点 id 就写在 target 里，node 留空"
        cmd = [str(VENV_PY), "-m", "pytest", target, "-q", "--no-header"]
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

    收集期报错只会给 `ERROR`、前置缺失只会给 skip，都不算红：那些红的是环境，不是闸。
    （判据刻意不用 `out.split("= ")`：pytest 的 `==` 里含 `= `，会把摘要行切没。）
    """
    if code == 0:
        return False
    if f"FAILED {target}" not in out:
        return False
    if "error" in out.splitlines()[-1].lower():
        return False
    return True


def _preflight() -> str | None:
    """开场探一次「`TEST_DATABASE_URL` 指的库迁过没有」，没迁就中止，一个文件都不碰。

    口令只从 env 读，报错文案里不回显 URL——只说库不对，不说什么用户/口令。
    """
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        return None  # 没导 env：T1…T3 自己记「未跑」，Go 的读库用例自动跳过，不会有假「残留」
    try:
        import asyncio

        import asyncpg
    except ImportError as exc:  # pragma: no cover - 本机 .venv 缺包时才走到
        return f"无法自检库（{exc!r}）：请用 ./.venv/bin/python 跑本文件"
    # scheme 归一走仓里那把唯一的尺（backend/core/pg_scheme.py）：本文件曾被
    # test_pg_env_dialect 的「不许第二处硬编码 asyncpg scheme」闸打红——手写 .replace() 只认
    # 一种前缀，换个 scheme 传进来就假报「连不上」。归一下沉之后这里不再有第二种译法可写。
    sys.path.insert(0, str(REPO))
    from backend.core.pg_scheme import libpq_url

    try:
        dsn = libpq_url(url)
    except ValueError as exc:
        return f"TEST_DATABASE_URL 不是 PG 连接串（{exc}）"

    async def _probe() -> str | None:
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchval("select to_regclass('public.mood_checkins')::text")
        finally:
            await conn.close()

    try:
        got = asyncio.run(_probe())
    except Exception as exc:  # noqa: BLE001 - 连不上时也只报异常类型，不回显 dsn
        return f"连不上 TEST_DATABASE_URL 指向的库（{type(exc).__name__}）：先起本机 dev PG"
    if got is None:
        return (
            "TEST_DATABASE_URL 指向的库里没有 public.mood_checkins ⇒ 那是未迁移的空库。"
            "收尾复核整包跑 ./internal/httpapi/ 时，五条 N8 回放测会报 "
            "relation does not exist，那会被打成「残留」（其实是环境）。"
            "请先对该库 alembic upgrade head，或把 env 改指到本机已迁移的库"
        )
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数，不改任何文件")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()
    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    if args.check:
        bad = 0
        for case in [*MUTATIONS, *GREEN]:
            edits = case[2] if isinstance(case[2], list) else [case[2]]
            problem = _verify_anchor(edits)
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        print(f"锚点校验：{'全部命中要求次数' if bad == 0 else f'{bad} 条有问题'}"
              f"（共 {len(MUTATIONS) + len(GREEN)} 条）")
        return 1 if bad else 0

    # 前置自检放在**碰任何文件之前**：环境问题跑到一半才暴露，只会把「红」记错归属。
    problem = _preflight()
    if problem:
        print(f"前置不满足，未改动任何文件：{problem}")
        return 2

    # 收集所有被碰过的文件，先整体备份；备份名＝整条 repo 相对路径展平（避免同名覆盖）。
    paths = sorted({e[0] for case in [*MUTATIONS, *GREEN]
                    for e in (case[2] if isinstance(case[2], list) else [case[2]])})
    with tempfile.TemporaryDirectory(prefix="mutate-batch4-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            rel = path.relative_to(REPO).as_posix().replace("/", "+")
            bak = Path(tmp) / f"{idx:02d}-{rel}"
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
            for cid, desc, edits, runner, target, node, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                if cid in DB_ONLY and not DB_ENV_OK:
                    not_run.append(cid)
                    print(f"[{cid}] 未跑（缺 `TEST_DATABASE_URL`：这一格要真库跑 relay_batch，"
                          f"没有它只会得到 skip，而 skip 不是绿）  {desc}")
                    continue
                base_code, base_out = run(runner, target, node)
                if base_code != 0:
                    not_run.append(cid)
                    print(f"[{cid}] 未跑（基线本来就不绿，exit={base_code}）：环境或前置问题，"
                          f"不能把这种红记成我的闸打红了\n  " +
                          "\n  ".join(base_out.splitlines()[-8:]))
                    continue
                _apply(edits)
                try:
                    broken = _sanity(edits)
                    if broken is None:
                        code, out = run(runner, target, node)
                    else:
                        code, out = -1, broken
                finally:
                    restore()
                if code == -1:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（变异体自身不合法，闸无从判断）：{out}")
                    continue
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

            for cid, desc, edits, runner, target, node in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(edits)
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
        # 本批的靶跨四个 Go 包与五份 pytest 面，所以按「被改过的那个包／那份测试」点验，
        # 不跑全仓（全量回归在批 4 收口第③步单独报）。
        rechecks = [
            ("pytest", WORDING_PY, ""), ("pytest", UNSAFE_PY, ""),
            ("pytest", ANALYTICS_T_PY, ""), ("pytest", ANALYTICS_API_T_PY, ""),
            ("pytest", N8_T_PY, ""),
            ("go", DOMAIN_PKG, ""), ("go", AICLIENT_PKG, ""), ("go", POSTHOG_PKG, ""),
            ("go", HTTPAPI_PKG, ""),
        ]
        for runner, target, node in rechecks:
            code, out = run(runner, target, node)
            print(f"复核 {target}：{'绿' if code == 0 else '红（残留！）'}")
            if code != 0:
                print("  " + "\n  ".join(out.splitlines()[-12:]))
                return 1

        print(f"\n计数：红闸 {red_ok} 打红 / {red_fail} 未打红"
              + (f" / {len(not_run)} 未跑（{','.join(not_run)}）" if not_run else "")
              + f"；绿闸 {green_ok} 保持绿 / {green_fail} 被误伤；异常 0")
        if failures:
            print("未通过：" + ",".join(failures))
        return 1 if (red_fail or green_fail) else 0


if __name__ == "__main__":
    sys.exit(main())
