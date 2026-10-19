#!/usr/bin/env python3
"""T1-02（账户生命周期对账闸）的变异自证（2026-10-18）。

这批交付的是「注销这句话的每一半都有人核对」，四张靶子各有判据：

注销覆盖（`backend/tests/test_account_deletion.py::test_purge_covers_every_user_scoped_table`）——
- R1 新建一张带 user_id 的表而不登记删除清单 → 工作单 T1-02 验收的第一句原话，闸必须当场红；
- R2 从 PURGE_TARGETS 抹掉一张既有表（T1-01 的 data_export_jobs）→ 注销后残留用户数据；
- R3 豁免理由写成两个字 → 豁免成了漏登记的逃生门，第一段判据白立；
- R4 同一张表既进删除清单又进豁免表 → 两条判据互相抵消，等于谁也不判。

豁免的行为面（`...::test_purge_user_does_not_delete_its_own_erasure_retry_row`）——
- R5 写完重试行后再删一次这张表（豁免失效的形状）→ purge_user 把自己刚写的重试线索抹掉，
   外部平台的残留重新变成永久无人认领。
失败留痕（`test_posthog_erasure_retry.py::test_purge_failure_leaves_retry_row`）——
- R6 撤掉 `if not posthog.get("erased")` → 工作单验收的第二句「PostHog 抛错 ⇒ 删除仍成功
   且留下可重试标记」只剩前半句，擦除失败只有一条 INFO 日志。

重试生命周期（`backend/tests/test_posthog_erasure_retry.py`）——
- T1 撤掉 CAS 谓词 → 两个 worker 各自 rowcount=1，同一个人被擦两遍；
- T2 认领时不推 `next_attempt_at` → 谓词对自己也不变假，同一轮里第二次认领照样成功；
- T3 `due_rows` 不看到点时刻 → 退避失效，每小时把 PostHog 撞一遍；
- T4 撤掉超龄告警分支 → 7 天到点仍只重试、不通知人，且永不退出（台账变墓碑表）；
- T5 告警后不删行 → 留下的就是 J4 否掉的那种「谁注销了」旁证；
- T6 登记时不 upsert → 同一用户叠成多行（唯一约束当场拒绝）；
- T7 重复登记把 `first_failed_at` 推到未来 → 一直失败的用户永远走不到告警那一格；
- T8 擦成功不删行 → 台账只进不出；
- T9 又失败时 attempts 不累加 → 告警日志里的 attempts 永远是 1，人没法判这是第几次。

排程可达（同文件最后两条）——
- T10 端点里漏掉 `await service.retry_pending_erasures(db)` → 只测 service 永远发现不了，
   每小时真跑的排程会安静地什么都不做；
- T11 宿主 crontab 撤掉这条排程 → 台账有人写、没人读，等价于没交付。

台账对账（`backend/tests/test_data_lineage_ledger.py::test_doc_matches_code_both_ways`）——
- D1 文档把 `posthog_erasure_retries` 的导出列写成 ✅（代码是 ❌）；
- D4 文档把它的注销删除列写成 ✅（代码是「豁免」）；
- D2 从 §二 删掉一行（既有表）→ 反向判据：代码有、台账没有；
- D3 撤掉 `EXPORT_EXCLUDE_REASONS["user_consents"]` → 本轮真实发生过的那条漂移：文档说
   不进导出、代码却因为派生自 PURGE_TARGETS 而把同意台账装进用户的 zip。

迁移链形状（`backend/tests/test_canonical_schema.py::test_migration_graph_is_single_head_with_n7_on_top`，
纯读脚本、不需要库）——
- M1 把 T1-02 件的 down_revision 挂回 T5-03 → 链上出现两个 head，alembic upgrade 当场报错。

实库面（`backend/tests/test_posthog_erasure_retry_pg.py`，一次性空库 `t102_e2e`）——
- P1 CAS 谓词摘掉：跨 session 的第二次认领照样 True（sqlite 是单写者，测不到这句）；
- P2 认领不推时刻：跨 session 的第二次认领照样 True（判的是同一条谓词的另一半）；
- P3 豁免在**迁移出来的库**上不成立：真库的 DDL 形状与 sqlite 的 `create_all` 本就不同。

P2 第一次挂的是 `test_real_pg_two_workers_erase_the_same_person_once`，实测**打不红**（exit=0），
根因记在这里以免下一轮再猜：那条用例的 stub 是「擦成功」，第一个 worker 成功后把台账行
DELETE 掉了——行没了本身就是第二道闸，所以「认领不推时刻」这只坏猫从批次结果面看不出来。
那条判的是批次结果（对外只调一次），CAS 的两半（谓词、推时刻）都由确定性的跨 session
认领用例承担，这也是它自己的 docstring 早写明的分工。

GR1/GR2/GR3 是「不该打红」的证人：闸判行为与台账内容，不判文件行数与注释位置。

没纳入本闸的一件：`test_canonical_schema.py::test_alembic_roundtrip_on_empty_database` 是
迁移里唯一约束与普通索引声明的证人。它要求目标库**每次都是空库**，而一条变异要跑两遍
（基线＋变异体），第二遍必然因为上一遍留下的 `alembic_version` 而红——那种红属于环境，
不属于判据（[[feedback-mutation-anchor-must-reach-target]]），所以它单独跑、单独取证，
不进这个串行 harness。要取证就重建库：
    docker exec closenai-pg-dev psql -U postgres -c 'DROP DATABASE IF EXISTS t102_rt WITH (FORCE)'
    docker exec closenai-pg-dev psql -U postgres -c 'CREATE DATABASE t102_rt'

纪律：串行执行、copy2 备份＋filecmp 逐字节写回、不用 git checkout
（[[feedback-mutation-restore-no-git-checkout]]、[[feedback-mutation-gates-no-concurrency]]）；
每条红闸先跑基线，基线不绿记「未跑」，不把环境的红算成闸的红
（[[feedback-mutation-anchor-must-reach-target]]）。

用法（`TEST_DATABASE_URL` 缺时 P 系列记「未跑」，既不算红也不算绿）：
    docker exec closenai-pg-dev psql -U postgres -c 'CREATE DATABASE t102_e2e'
    DATABASE_URL='postgresql+psycopg://postgres:***@127.0.0.1:55432/t102_e2e' \\
        .venv/bin/python -m alembic -c backend/alembic.ini upgrade head
    TEST_DATABASE_URL='postgresql://postgres:***@127.0.0.1:55432/t102_e2e' \\
        CLOSEN_REQUIRE_PG=1 ./.venv/bin/python scripts/mutate_t102_lifecycle_gates.py
    docker exec closenai-pg-dev psql -U postgres -c 'DROP DATABASE t102_e2e'

    ./.venv/bin/python scripts/mutate_t102_lifecycle_gates.py --check
    ./.venv/bin/python scripts/mutate_t102_lifecycle_gates.py --only R1,T10,P1
"""
from __future__ import annotations

import argparse
import ast
import filecmp
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENV_PY = REPO / ".venv" / "bin" / "python"

MODEL = REPO / "backend" / "models" / "erasure.py"
SVC = REPO / "backend" / "services" / "user_service.py"
REPO_PY = REPO / "backend" / "repositories" / "erasure_repository.py"
CRON = REPO / "backend" / "routers" / "cron.py"
CRONTAB = REPO / "deploy" / "schedule" / "crontab"
LEDGER = REPO / "docs" / "data-lineage.md"
EXPORT_SVC = REPO / "backend" / "services" / "export_service.py"
MIG = REPO / "backend" / "alembic" / "versions" / "e5a7b9d1f3a5_add_posthog_erasure_retries.py"

DEL_GATE = "backend/tests/test_account_deletion.py"
RETRY_GATE = "backend/tests/test_posthog_erasure_retry.py"
LINEAGE_GATE = "backend/tests/test_data_lineage_ledger.py"
SCHEMA_GATE = "backend/tests/test_canonical_schema.py"
PG_GATE = "backend/tests/test_posthog_erasure_retry_pg.py"
PG_ENV = "TEST_DATABASE_URL"

COVERAGE = f"{DEL_GATE}::test_purge_covers_every_user_scoped_table"
EXEMPT_BEHAVIOR = f"{DEL_GATE}::test_purge_user_does_not_delete_its_own_erasure_retry_row"
BOTH_WAYS = f"{LINEAGE_GATE}::test_doc_matches_code_both_ways"
GRAPH = f"{SCHEMA_GATE}::test_migration_graph_is_single_head_with_n7_on_top"

CRON_LINE = (
    '0 * * * * . /etc/closenai/cron.env && curl -fsS -H "Authorization: Bearer $CRON_SECRET" '
    "http://127.0.0.1:8000/api/v1/cron/purge-deleted-users >/dev/null"
)

MUTATIONS: list[tuple] = [
    # ── 1. 注销覆盖闸（工作单 T1-02 验收第一句）──
    (
        "R1",
        "新建一张带 user_id 的表却不登记删除清单：验收原话「加表不加 purge ⇒ 闸红」",
        [(MODEL,
          '        Index("ix_posthog_erasure_retries_next_attempt", "next_attempt_at"),\n'
          "    )\n",
          '        Index("ix_posthog_erasure_retries_next_attempt", "next_attempt_at"),\n'
          "    )\n"
          "\n\n"
          'class T102ProbeUnpurged(Base):\n'
          '    """变异证人：一张漏登记的表。"""\n'
          "\n"
          '    __tablename__ = "t102_probe_unpurged"\n'
          "    id: Mapped[str] = mapped_column(String(36), primary_key=True)\n"
          "    user_id: Mapped[str] = mapped_column(String(36))\n")],
        "pytest", COVERAGE, "test_purge_covers_every_user_scoped_table",
    ),
    (
        "R2",
        "从 PURGE_TARGETS 抹掉一张既有表：注销后这张表一行都不删，「注销即删干净」变成话术",
        [(SVC, '    (DataExportJob, "data_export_jobs"),\n', "")],
        "pytest", COVERAGE, "test_purge_covers_every_user_scoped_table",
    ),
    (
        "R3",
        "豁免理由只剩两个字：漏登记的表只要随手写个空理由就能过闸，豁免成了逃生门",
        [(SVC,
          '    "posthog_erasure_retries": (\n'
          '        "注销后的外部平台重试台账：由 retry_pending_erasures 自我销毁（成功即删、超龄告警后删），"\n'
          '        "不进 purge 循环否则 purge_user 刚写的重试行会被自己抹掉"\n'
          "    ),\n",
          '    "posthog_erasure_retries": "豁免",\n')],
        "pytest", COVERAGE, "test_purge_covers_every_user_scoped_table",
    ),
    (
        "R4",
        "同一张表既进删除清单又进豁免表：两条判据互相抵消，等于谁也没在核",
        [(SVC,
          '    (RefreshToken, "refresh_tokens"),\n',
          '    (PostHogErasureRetry, "posthog_erasure_retries"),\n'
          '    (RefreshToken, "refresh_tokens"),\n')],
        "pytest", COVERAGE, "test_purge_covers_every_user_scoped_table",
    ),
    # ── 2. 豁免的行为面 + 失败留痕 ──
    (
        "R5",
        "写完重试行后又把这张表删一遍（豁免失效的形状）：purge_user 抹掉自己刚写的重试线索",
        [(SVC,
          '            await self.record_erasure_retry(db, user_id=user_id, '
          'reason=str(posthog.get("reason", "?")))\n',
          '            await self.record_erasure_retry(db, user_id=user_id, '
          'reason=str(posthog.get("reason", "?")))\n'
          "            await db.execute(\n"
          "                delete(PostHogErasureRetry).where(PostHogErasureRetry.user_id == user_id)\n"
          "            )\n")],
        "pytest", EXEMPT_BEHAVIOR, "test_purge_user_does_not_delete_its_own_erasure_retry_row",
    ),
    (
        "R6",
        "撤掉「擦除失败就登记」那句判据：PostHog 抛错只留一条 INFO，残留永久无人认领",
        [(SVC, '        if not posthog.get("erased"):', "        if False:")],
        "pytest", f"{RETRY_GATE}::test_purge_failure_leaves_retry_row",
        "test_purge_failure_leaves_retry_row",
    ),
    # ── 3. 重试生命周期 ──
    (
        "T1",
        "CAS 认领摘掉 `next_attempt_at <= now` 谓词：谁来自认都算抢到，同一个人被擦两遍",
        [(REPO_PY,
          "            .where(\n"
          "                PostHogErasureRetry.id == row_id,\n"
          "                PostHogErasureRetry.next_attempt_at <= now,\n"
          "            )\n",
          "            .where(PostHogErasureRetry.id == row_id)\n")],
        "pytest", f"{RETRY_GATE}::test_retry_is_claimed_once_per_pass",
        "test_retry_is_claimed_once_per_pass",
    ),
    (
        "T2",
        "认领时不推 `next_attempt_at`：谓词对自己也不变假，同一轮里第二次认领照样成功",
        [(REPO_PY,
          "            .values(last_attempt_at=now, next_attempt_at=next_attempt_at)",
          "            .values(last_attempt_at=now)")],
        "pytest", f"{RETRY_GATE}::test_retry_is_claimed_once_per_pass",
        "test_retry_is_claimed_once_per_pass",
    ),
    (
        "T3",
        "扫描不看「到点了没有」：退避失效，每小时把没到点的行也送去撞一次 PostHog",
        [(REPO_PY,
          "            .where(PostHogErasureRetry.next_attempt_at <= now)",
          "            .where(PostHogErasureRetry.id.is_not(None))")],
        "pytest", f"{RETRY_GATE}::test_retry_skips_rows_not_due_yet",
        "test_retry_skips_rows_not_due_yet",
    ),
    (
        "T4",
        "撤掉超龄告警分支：第 8 天还在重试而不通知人，台账永不退出＝写成墓碑表",
        [(SVC,
          "                    if first_failed is not None and first_failed <= deadline:",
          "                    if False:")],
        "pytest", f"{RETRY_GATE}::test_retry_alerts_then_drops_row_past_max_days",
        "test_retry_alerts_then_drops_row_past_max_days",
    ),
    (
        "T5",
        "告警后仍不删行：留下的正是 J4 否掉的那种「谁注销了」旁证",
        [(SVC,
          "                        await erasure_repository.delete(db, row.id)\n"
          '                        out["alerted"] += 1\n',
          '                        out["alerted"] += 1\n')],
        "pytest", f"{RETRY_GATE}::test_retry_alerts_then_drops_row_past_max_days",
        "test_retry_alerts_then_drops_row_past_max_days",
    ),
    (
        "T6",
        "登记时不 upsert：同一用户叠成第二行，唯一约束当场拒绝",
        [(REPO_PY, "        row = await self.get(db, user_id)", "        row = None")],
        "pytest", f"{RETRY_GATE}::test_repeat_failure_counts_not_stacks",
        "test_repeat_failure_counts_not_stacks",
    ),
    (
        "T7",
        "重复登记把 first_failed_at 推到未来：一直失败的用户永远走不到 7 天告警那一格",
        [(REPO_PY,
          "            row.attempts = (row.attempts or 0) + 1\n",
          "            row.attempts = (row.attempts or 0) + 1\n"
          "            row.first_failed_at = now\n")],
        "pytest", f"{RETRY_GATE}::test_repeat_failure_counts_not_stacks",
        "test_repeat_failure_counts_not_stacks",
    ),
    (
        "T8",
        "擦成功不删行：台账只进不出，「自我销毁」这句承诺没了",
        [(SVC,
          "                        await erasure_repository.delete(db, row.id)\n"
          '                        out["erased"] += 1\n',
          '                        out["erased"] += 1\n')],
        "pytest", f"{RETRY_GATE}::test_retry_drops_row_when_erase_succeeds",
        "test_retry_drops_row_when_erase_succeeds",
    ),
    (
        "T9",
        "又失败时 attempts 不累加：告警日志里永远是 1 次，人没法判这是第几次重试",
        [(SVC, "                            attempts=row.attempts + 1,",
          "                            attempts=row.attempts,")],
        "pytest", f"{RETRY_GATE}::test_retry_reschedules_when_erase_fails_again",
        "test_retry_reschedules_when_erase_fails_again",
    ),
    # ── 4. 排程可达 ──
    (
        "T10",
        "端点里漏掉那一行重试调用：只测 service 的闸全绿，而每小时真跑的排程安静地什么都不做",
        [(CRON,
          "    erasure = await service.retry_pending_erasures(db)",
          '    erasure = {"checked": 0, "erased": 0, "pending": 0, "alerted": 0, "skipped": 0}')],
        "pytest", f"{RETRY_GATE}::test_cron_endpoint_drives_erasure_retry",
        "test_cron_endpoint_drives_erasure_retry",
    ),
    (
        "T11",
        "宿主 crontab 撤掉这条排程：台账有人写、没人读，等价于没交付",
        [(CRONTAB, CRON_LINE, "# " + CRON_LINE)],
        "pytest", f"{RETRY_GATE}::test_purged_users_cron_line_is_scheduled",
        "test_purged_users_cron_line_is_scheduled",
    ),
    # ── 5. 台账双向对账 ──
    (
        "D1",
        "台账把新表的导出列写成 ✅：文档与 export_targets() 分家，用户包里多出没人核对过的东西",
        [(LEDGER, "| py-api | 豁免 | ❌ |", "| py-api | 豁免 | ✅ |")],
        "pytest", BOTH_WAYS, "test_doc_matches_code_both_ways",
    ),
    (
        "D4",
        "台账把新表的注销删除列写成 ✅（代码口径是「豁免」）：豁免这件事在台账上被念反",
        [(LEDGER, "| py-api | 豁免 | ❌ |", "| py-api | ✅ | ❌ |")],
        "pytest", BOTH_WAYS, "test_doc_matches_code_both_ways",
    ),
    (
        "D2",
        "从 §二 删掉一行既有表：反向判据（代码有、台账没有）必须红，否则台账可以随便掉行",
        [(LEDGER,
          "| `chat_messages` | py-api | ✅ | ✅ | 对话本体：用户创作与经历的第一手内容 |\n",
          "")],
        "pytest", BOTH_WAYS, "test_doc_matches_code_both_ways",
    ),
    (
        "D3",
        "撤掉 user_consents 的表级排除：本轮真实发生过的漂移——文档说不进导出、代码却装进了 zip",
        [(EXPORT_SVC,
          '    "user_consents": (\n'
          '        "同意台账属**审计**（谁在哪个版本表过态），不是用户创作的内容；"\n'
          '        "把它打进导出包等于替平台做一份「你同意过什么」的二次分发（T5-01 裁决，"\n'
          '        "见 docs/data-lineage.md）。注销仍删净。"\n'
          "    ),\n",
          "")],
        "pytest", BOTH_WAYS, "test_doc_matches_code_both_ways",
    ),
    # ── 6. 迁移链形状 ──
    (
        "M1",
        "T1-02 件的 down_revision 挂回 T5-03：链上出现两个 head，alembic upgrade 当场报错",
        [(MIG,
          "down_revision: Union[str, Sequence[str], None] = 'd4f6a8b0c2e4'",
          "down_revision: Union[str, Sequence[str], None] = 'c3e5a7b9d1f2'")],
        "pytest", GRAPH, "test_migration_graph_is_single_head_with_n7_on_top",
    ),
    # ── 7. 实库面（缺 TEST_DATABASE_URL 记「未跑」）──
    (
        "P1",
        "CAS 谓词摘掉（真库面）：A 提交后 B 仍能认领同一行，跨 session 的「只有一次」不成立",
        [(REPO_PY,
          "            .where(\n"
          "                PostHogErasureRetry.id == row_id,\n"
          "                PostHogErasureRetry.next_attempt_at <= now,\n"
          "            )\n",
          "            .where(PostHogErasureRetry.id == row_id)\n")],
        "pytest-pg", f"{PG_GATE}::test_real_pg_claim_is_cross_session_single_use",
        "test_real_pg_claim_is_cross_session_single_use",
    ),
    (
        "P2",
        "认领不推时刻（真库面·跨 session）：A 提交后 B 的认领谓词仍然为真，同一行被认两次",
        [(REPO_PY,
          "            .values(last_attempt_at=now, next_attempt_at=next_attempt_at)",
          "            .values(last_attempt_at=now)")],
        "pytest-pg", f"{PG_GATE}::test_real_pg_claim_is_cross_session_single_use",
        "test_real_pg_claim_is_cross_session_single_use",
    ),
    (
        "P3",
        "豁免在迁移出来的库上不成立（真库面）：真库 DDL 与 sqlite 的 create_all 形状本就不同",
        [(SVC,
          '            await self.record_erasure_retry(db, user_id=user_id, '
          'reason=str(posthog.get("reason", "?")))\n',
          '            await self.record_erasure_retry(db, user_id=user_id, '
          'reason=str(posthog.get("reason", "?")))\n'
          "            await db.execute(\n"
          "                delete(PostHogErasureRetry).where(PostHogErasureRetry.user_id == user_id)\n"
          "            )\n")],
        "pytest-pg", f"{PG_GATE}::test_real_pg_purge_keeps_the_retry_row_it_just_wrote",
        "test_real_pg_purge_keeps_the_retry_row_it_just_wrote",
    ),
]

# 不该打红的东西：证明闸判的是行为与台账内容，不是源码行数或注释位置。
GREEN: list[tuple] = [
    (
        "GR1",
        "往 retry_pending_erasures 上面插 15 行注释：重试的五个出口判的是行为，不该因为文件变长而红",
        [(SVC,
          "    async def retry_pending_erasures(self, db: AsyncSession) -> dict:",
          "".join("    # 变异证人 %d\n" % i for i in range(15))
          + "    async def retry_pending_erasures(self, db: AsyncSession) -> dict:")],
        "pytest", RETRY_GATE, "",
    ),
    (
        "GR2",
        "往 §二 台账里插 15 行非表格文本：对账闸解析的是表格行，注释性段落不该让它扫空集",
        [(LEDGER,
          "## 二、逐表登记（全量：43 张，按上面三条闸逐格对账）\n",
          "## 二、逐表登记（全量：43 张，按上面三条闸逐格对账）\n"
          + "".join(f"> 变异证人 {i}\n" for i in range(15)) + "\n")],
        "pytest", LINEAGE_GATE, "",
    ),
    (
        "GR3",
        "往 erasure_repository.py 的 claim 上面插 15 行注释：认领闸判 rowcount 语义，不判行号",
        [(REPO_PY,
          "    async def claim(",
          "".join("    # 变异证人 %d\n" % i for i in range(15)) + "    async def claim(")],
        "pytest", RETRY_GATE, "",
    ),
]


def _apply(edits: list) -> None:
    for path, old, new in edits:
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:90]!r}"
        path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _verify_anchor(edits: list) -> str | None:
    for path, old, _new in edits:
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if n != 1:
            return f"命中 {n} 次（要 1）"
    return None


def _sanity(edits: list) -> str | None:
    """Python 靶被改成语法不通时，红的是解释器而不是判据——先当场拦下。"""
    for path, _old, _new in edits:
        if path.suffix != ".py":
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            return f"{path.name}：变异体不是合法 Python（{exc}）"
    return None


def run_pytest(node: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(VENV_PY), "-m", "pytest", node, "-q", "--no-header"],
        cwd=REPO, capture_output=True, text=True, timeout=1200,
    )
    return proc.returncode, proc.stdout + proc.stderr


def run(kind: str, target: str) -> tuple[int, str]:
    if kind == "pytest-pg" and not os.environ.get(PG_ENV):
        # 缺库时那条用例是 skip，pytest 退出码仍是 0——不拦下来就会把「没跑」念成「基线绿」。
        return -2, f"{PG_ENV} 未设置：{target} 没跑，实库用例不算绿"
    return run_pytest(target)


def is_red(code: int, out: str, marker: str, target: str) -> bool:
    if code == 0 or not marker:
        return False
    if not re.search(r"\d+ (failed|error)", out):
        return False
    return f"FAILED {target}" in out


RESIDUE = [COVERAGE, EXEMPT_BEHAVIOR, BOTH_WAYS, GRAPH, RETRY_GATE, LINEAGE_GATE]


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
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}（共 {len(cases)} 条）")
        return 1 if bad else 0

    if not VENV_PY.is_file():
        print(f"解释器不存在：{VENV_PY}（变异自证要求先备好 .venv）")
        return 2

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-t102-") as tmp:
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
                base_code, base_out = run(kind, target)
                if base_code == -2:
                    unrun.append(cid)
                    print(f"[{cid}] 未跑（{base_out}）：缺库不是绿，也不把它的红记在闸头上")
                    continue
                if base_code != 0:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 未跑（基线本来就不绿，exit={base_code}）：不能把环境的红记成闸的红\n  "
                          + "\n  ".join(base_out.splitlines()[-8:]))
                    continue
                _apply(edits)
                try:
                    broken = _sanity(edits)
                    code, out = (-1, broken) if broken else run(kind, target)
                finally:
                    restore()
                if code == -1:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（变异体自身不合法）：{out}")
                    continue
                if is_red(code, out, marker, target):
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓（{kind}）  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（{kind} exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))

            for cid, desc, edits, kind, target, _marker in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(edits)
                try:
                    code, out = run(kind, target)
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓（{kind}）  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（{kind} exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))
        except Exception as exc:  # noqa: BLE001
            restore()
            print(f"异常中止，已恢复：{exc!r}")
            return 2

        residue = 0
        for node in RESIDUE:
            code, out = run_pytest(node)
            if code != 0:
                residue += 1
                print(f"残留（红！）：{node}\n  " + "\n  ".join(out.splitlines()[-12:]))
        print("复核 生命周期／台账／链形状闸：" + ("绿" if residue == 0 else f"{residue} 条残留"))
        if os.environ.get(PG_ENV):
            pg_code, pg_out = run_pytest(PG_GATE)
            residue = residue or (1 if pg_code else 0)
            print(("复核 实库闸：绿" if pg_code == 0
                   else "残留（红！）：\n  " + "\n  ".join(pg_out.splitlines()[-12:])))
        else:
            print(f"复核 实库闸：未跑（缺 {PG_ENV}）")
        print(f"\nT1-02 变异自证：打红 {red_ok}/{len(MUTATIONS)}，保持绿 {green_ok}/{len(GREEN)}，"
              f"失败 {red_fail + green_fail}，未跑 {len(unrun)}，残留 {residue}")
        if unrun:
            print(f"未跑清单（缺 {PG_ENV}，不算绿）：{', '.join(unrun)}")
        if failures:
            print(f"失败清单：{failures}")
        return 1 if (red_fail or green_fail or residue or unrun) else 0


if __name__ == "__main__":
    sys.exit(main())
