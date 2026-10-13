"""N7 的变异自证：outbox 入队/认领/退避/死信 + inbox 幂等消费 + 保留期清理，
每一条口径都要能被一次真实的破坏打红。

手法与 mutate_n3/n4/n5/n6_gates.py 相同：备份 → 写坏 → 只跑那一条（组）测试 →
备份写回 → filecmp 核验，**不用 git checkout**（仓里有并行未提交工作，checkout 会连带冲掉）。
库口令只从环境取（CLOSEN_PG_PASSWORD，否则现取容器 env），脚本与输出都不落明文。

两处刻意不做的事，写明白免得被当成漏了：
1. `FOR UPDATE SKIP LOCKED` 的互斥性没有做成变异项——把它改成 `FOR UPDATE` 之后，
   第二个事务不是「认领到重叠的行」而是**原地等锁**，用例只会挂到超时，那种红
   证明不了闸门、还会在库里留下阻塞会话。SKIP LOCKED 本身由
   TestN7ConcurrentClaimsAreDisjoint（两个同时开着的事务）在真库上钉住。
2. 条件取反一律走 disabled()：条件里引用到的变量一个都不能从式子里消失，否则
   `declared and not used` 会让测试因为编不过而「红」，那条结果不作数。
   #15 的 `_ = currentVersion` 是同一理由。

SQL/生成物那几条（#5 #6 #8 #12 #13 #14）必须经 sqlc 重生成才进得了编译路径，
所以每条带 marker/absent 双侧断言：生成物没带上变异就判定这条闸门未被破坏、结果无效。

用法：
    .venv/bin/python scripts/mutate_n7_gates.py           # 全部
    .venv/bin/python scripts/mutate_n7_gates.py 5 6       # 只跑点名几条
"""
from __future__ import annotations

import filecmp
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/fengyu/Downloads/myproject/workspace/closenai-app")
GO = ROOT / "go-core"
DEL = GO / "internal/domain/delivery.go"
POL = GO / "internal/domain/delivery_policy.go"
EVIDENCE = GO / "internal/domain/service_evidence.go"
WK = GO / "internal/messaging/outbox/outbox.go"
REPO = GO / "internal/store/canonical_repo.go"
SQL = GO / "db/queries/canonical.sql"
SQLC_OUT = GO / "internal/store/canonicaldb/canonical.sql.go"
SQLC = "/opt/homebrew/bin/sqlc"

DOMAIN_TESTS = GO / "internal/domain"
STORE_TESTS = GO / "internal/store"


def pg_dsn() -> str:
    pw = os.environ.get("CLOSEN_PG_PASSWORD")
    if not pw:
        env = subprocess.run(
            ["docker", "inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}",
             "closenai-pg-dev"],
            capture_output=True, text=True, check=True).stdout
        pw = next(line[len("POSTGRES_PASSWORD="):] for line in env.splitlines()
                  if line.startswith("POSTGRES_PASSWORD="))
    return f"postgresql://postgres:{pw}@127.0.0.1:55432/closenai"


GOROOT = "/Users/fengyu/sdk/go1.27.1"
ENV = {
    **os.environ,
    "GOROOT": GOROOT,
    "PATH": f"{GOROOT}/bin:" + os.environ["PATH"],
    "TEST_DATABASE_URL": pg_dsn(),
}
BACKUPS: dict[Path, Path] = {}


def backup(*paths: Path):
    tmp = Path(tempfile.mkdtemp(prefix="n7-gate-backup-"))

    def run() -> None:
        for path in paths:
            bak = tmp / path.name
            shutil.copy2(path, bak)
            BACKUPS[path] = bak

    return run


def restore_files() -> None:
    for path, bak in BACKUPS.items():
        shutil.copy2(bak, path)
        assert filecmp.cmp(bak, path, shallow=False), f"恢复失败：{path}"
    BACKUPS.clear()


def patch(path: Path, old: str, new: str):
    def run() -> None:
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, f"变异锚点命中 {text.count(old)} 次，应为 1：{old[:70]!r}"
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    return run


def disabled(anchor: str) -> str:
    return re.sub(r" \{$", " && false {", anchor, count=1)


def sqlc_regen(marker: str = "", absent: str = ""):
    """重跑 sqlc 并核验生成物真的带上变异（sqlc 有 mtime 缓存，copy2 恢复会拨回旧值）。"""

    def run() -> None:
        for f in (SQL, SQLC_OUT):
            os.utime(f, None)
        subprocess.run([SQLC, "generate"], cwd=GO, capture_output=True, text=True,
                       env=ENV, check=True)
        generated = SQLC_OUT.read_text(encoding="utf-8")
        if marker:
            assert marker in generated, \
                f"sqlc 没把变异写进生成物，这条闸门未被破坏，结果无效：{marker!r}"
        if absent:
            assert absent not in generated, \
                f"生成物里仍然留着被删掉的片段，变异没落到编译路径上：{absent!r}"

    return run


def run_go(target: Path, test_name: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["go", "test", "./" + str(target.relative_to(GO)).strip("/"), "-count=1",
         "-run", test_name, "-v"],
        cwd=GO, capture_output=True, text=True, env=ENV, timeout=900)
    return proc.returncode, proc.stdout + proc.stderr


def m(num: int, claim: str, target: Path, test: str, *steps):
    return (num, claim, target, test, steps)


# ── 变异锚点（逐字取自当前源码，命中数由 patch 断言）─────────────────────────
GUARD_NOT_ROUTED = "\tif !ok {\n"
ROUTING_ENTRY = '\t"pattern_reactivated":    "pattern.updated",\n'
MESSAGE_ID = "\t\tID: s.newID(), MessageID: row.ID, MessageType: row.EventType,"
CAUSATION_LINE = "\t\tCausationID: causationFor(ctx), Payload: payload,"
PAYLOAD_NORMALIZE = '\tif payload == "" {\n'
CALLBACK_GUARD = "\t\tif err := fn(ctx, st); err != nil {\n"
CLASSIFY_GUARD = "\tif !errors.As(cause, &rej) {\n"
LADDER_INDEX = "\tindex := attempt - 1\n"
BUMP_EXPECT = "\tnewVersion, err := st.BumpVersion(ctx, state.PersonalModelID, currentVersion)\n"
CLAIM_STATUS = "    WHERE status IN ('pending', 'failed', 'publishing')\n"
CLAIM_DUE = "        AND available_at <= now()\n"
PUBLISH_GUARD = ("    last_error = NULL, updated_at = now()\n"
                 "WHERE id = $1 AND status = 'publishing'\n")
PUBLISH_GUARD_LOST = ("    last_error = NULL, updated_at = now()\n"
                      "WHERE id = $1\n")
DEADLETTER_GUARD = ("SET status = 'dead_lettered', last_error = $2, updated_at = now()\n"
                    "WHERE id = $1 AND status = 'publishing'\n")
DEADLETTER_LOST = ("SET status = 'dead_lettered', last_error = $2, updated_at = now()\n"
                   "WHERE id = $1\n")
INBOX_DO_NOTHING = "ON CONFLICT (message_id, consumer) DO NOTHING\n"
INBOX_DUP_AS_FIRST = "ON CONFLICT (message_id, consumer) DO UPDATE SET updated_at = now()\n"
FAILURE_ACCUMULATE = "    attempt_count = inbox_messages.attempt_count + 1, updated_at = now()\n"
FAILURE_FLAT = "    attempt_count = inbox_messages.attempt_count, updated_at = now()\n"
RETENTION_STATUS = "WHERE status = 'published' AND published_at < $1;\n"
RETENTION_ANY = "WHERE published_at < $1;\n"
RETENTION_INVERT = "WHERE status = 'published' AND published_at >= $1;\n"
DLQ_CEILING = "\tif claim.AttemptCount >= w.policy.MaxAttempts {\n"
REPO_LOST_ACK = ('\t_, err := c.q.MarkOutboxPublished(ctx, id)\n'
                 "\tif errors.Is(err, pgx.ErrNoRows) {\n\t\treturn false, nil\n\t}\n")
REPO_LOST_AS_OK = ('\t_, err := c.q.MarkOutboxPublished(ctx, id)\n'
                   "\tif errors.Is(err, pgx.ErrNoRows) {\n\t\treturn true, nil\n\t}\n")

MUTATIONS = [
    m(1, "路由表就是「要不要跨进程」的唯一闸：未被路由的事件也必须入表，等于把"
         "candidate_rejected 这类「权威态没变」的噪声推给下游（N7-3 判据 1）",
      DOMAIN_TESTS, "TestAdmitEnqueuesOnlyRoutedEvents",
      backup(DEL),
      patch(DEL, GUARD_NOT_ROUTED, disabled(GUARD_NOT_ROUTED))),

    m(2, "路由表少一项就是静默不投递：pattern_reactivated 不再进 pattern.updated"
         "（14:66-70 逐字的那两条之一）",
      DOMAIN_TESTS, "TestRoutingTableIsClosed|TestReactivateAndOutcomePathsEnqueue",
      backup(DEL),
      patch(DEL, ROUTING_ENTRY, "")),

    m(3, "message_id 必须取事件行 PK：换成新生成的 id 就不再是「同一条权威事件的稳定"
         "标识」，重试与消费端去重各说各话（18 §10）",
      DOMAIN_TESTS, "TestAdmitEnqueuesOnlyRoutedEvents",
      backup(DEL),
      patch(DEL, MESSAGE_ID, "\t\tID: s.newID(), MessageID: s.newID(), MessageType: row.EventType,")),

    m(4, "causation_id 断链：入站消息不再是被引起那一行的直接原因，17 §7 的因果链查无实据",
      DOMAIN_TESTS, "TestConsumeIdempotentWritesOutboxWithCausation",
      backup(DEL),
      patch(DEL, CAUSATION_LINE, "\t\tPayload: payload,")),

    m(5, "认领不看 available_at＝退避与租约双双失效：毒消息被立刻重投、崩溃 worker 的"
         "行被并发双投（14 §8 的阶梯 + 18 §5）",
      STORE_TESTS, "TestN7StaleLeaseIsReclaimed",
      backup(SQL, SQLC_OUT),
      patch(SQL, CLAIM_DUE, ""),
      sqlc_regen(absent="AND available_at <= now()")),

    m(6, "确认写入的租约守卫（SQL 侧）：去掉 status='publishing' 后，pending 行也能被"
         "直接确认成 published，18 §6「broker 确认之前不得 published」形同虚设",
      STORE_TESTS, "TestN7PublishedRequiresLease",
      backup(SQL, SQLC_OUT),
      patch(SQL, PUBLISH_GUARD, PUBLISH_GUARD_LOST),
      sqlc_regen(marker=PUBLISH_GUARD_LOST.strip())),

    m(7, "仓储面把「守卫没放行」读成成功：0 行＝认领权已被别的 worker 接管，返回 true "
         "就等于把这条投递记成我发布成了（N7-3 的返回值口径）",
      STORE_TESTS, "TestN7PublishedRequiresLease",
      backup(REPO),
      patch(REPO, REPO_LOST_ACK, REPO_LOST_AS_OK)),

    m(8, "认领面的状态集合放宽到 published/dead_lettered：已发布与死信被反复投递，"
         "17 §9 的「毒消息不阻塞队列」反面的另一半天（重复轰炸下游）",
      STORE_TESTS, "TestN7WorkerRetriesAndDeadLetters|TestN7PublishedRequiresLease",
      backup(SQL, SQLC_OUT),
      patch(SQL, CLAIM_STATUS,
            "    WHERE status IN ('pending', 'failed', 'publishing', 'published', "
            "'dead_lettered')\n"),
      sqlc_regen(marker="'publishing', 'published', 'dead_lettered'")),

    m(9, "死信闸的判定从 >= 松成 >：到达上限的那一次仍然排退避，永远进不了 "
         "dead_lettered（17 §9 + 14 §8 的第三级之后）",
      STORE_TESTS, "TestN7WorkerRetriesAndDeadLetters",
      backup(WK),
      patch(WK, DLQ_CEILING, "\tif claim.AttemptCount > w.policy.MaxAttempts {\n")),

    m(10, "消费回调的失败被吞：业务副作用报错却继续 COMMIT 并把 inbox 置 processed，"
          "18 §8 的事务形状与 14 §7 的 NACK 一起作废",
      DOMAIN_TESTS, "TestConsumeIdempotentTransientRollsBackInboxRow",
      backup(DEL),
      patch(DEL, CALLBACK_GUARD, disabled(CALLBACK_GUARD))),

    m(11, "永久/瞬时分类闸失效：可恢复的抖动也被记成永久拒绝并 ACK 丢弃，事务回滚"
          "本该让它在下一轮重投（14 §7「transient 只用于可恢复失败」+ 18 §9 case 1）。"
          "受害用例取瞬时那一侧：`errors.As` 在条件里仍给 rej 赋值，所以永久拒绝那条"
          "路径对这个变异本来就是对的，拿它当受害面只会得到一条假绿",
      STORE_TESTS, "TestN7ConsumeIdempotentTransientLeavesNoInboxRow",
      backup(DEL),
      patch(DEL, CLASSIFY_GUARD, disabled(CLASSIFY_GUARD))),

    m(12, "幂等键 (message_id, consumer) 的重复不再返回 0 行：重复投递被当成首次消费，"
          "副作用第二次执行（18 §7/§8）",
      STORE_TESTS, "TestN7ConsumeIdempotentProcessedThenDuplicate",
      backup(SQL, SQLC_OUT),
      patch(SQL, INBOX_DO_NOTHING, INBOX_DUP_AS_FIRST),
      sqlc_regen(marker="DO UPDATE SET updated_at = now()")),

    m(13, "永久拒绝留痕的 attempt_count 不累加：同一消息被反复永久拒绝时，账上永远"
          "只有 1 次尝试，毒消息的重投上限无从对账（18 §7 的 attempt_count）",
      STORE_TESTS, "TestN7ConsumeIdempotentPermanentSurvivesRollback",
      backup(SQL, SQLC_OUT),
      patch(SQL, FAILURE_ACCUMULATE, FAILURE_FLAT),
      sqlc_regen(marker="attempt_count = inbox_messages.attempt_count, updated_at")),

    m(14, "清理不看终态：带旧 published_at 的 failed 行被一起删掉——18 §13 认的是状态，"
          "时间戳只是历史戳（被删的那一行就是这条口径的证据）",
      STORE_TESTS, "TestN7CleanupDeliveryRecordsKeepsPendingAndDeadLetters",
      backup(SQL, SQLC_OUT),
      patch(SQL, RETENTION_STATUS, RETENTION_ANY),
      sqlc_regen(absent="status = 'published' AND published_at")),

    m(15, "N7-14 的乐观锁期望值回退成 insight 行自己的快照版本：模型在洞察之后变过"
          "一次就永远假报版本冲突，撤回与过期这条路径整笔回滚（七-5.1 第 2 条）",
      STORE_TESTS, "TestN7ReevaluateTakesCurrentModelVersion",
      backup(EVIDENCE),
      patch(EVIDENCE, BUMP_EXPECT,
            "\t\t_ = currentVersion\n"
            "\tnewVersion, err := st.BumpVersion(ctx, state.PersonalModelID, state.ModelVersion)\n")),

    m(16, "投递 payload 的空串归一被跳过：下游按 JSON 解码拿到空文档（N7-5 的 payload "
          "契约，事件行的 Payload 口径不受影响）",
      DOMAIN_TESTS, "TestEmitKeepsEventPayloadAndNormalizesDeliveryPayloadOnly",
      backup(DEL),
      patch(DEL, PAYLOAD_NORMALIZE, disabled(PAYLOAD_NORMALIZE))),

    m(17, "退避阶梯错档：第一次失败等的是 2 分钟而不是 14 §8 的 retry.30s——"
          "阶梯的索引换算就是这张表的落地",
      DOMAIN_TESTS, "TestDeliveryPolicyRetryLadder",
      backup(POL),
      patch(POL, LADDER_INDEX, "\tindex := attempt\n")),

    m(18, "保留期比较方向反了：清掉的是窗口内的新行、留下的是该搬走的超期行，"
          "等于一边丢掉近期幂等历史、一边让 18 §13 的清理永远搬不走东西",
      STORE_TESTS, "TestN7CleanupDeliveryRecordsKeepsPendingAndDeadLetters",
      backup(SQL, SQLC_OUT),
      patch(SQL, RETENTION_STATUS, RETENTION_INVERT),
      sqlc_regen(marker="published_at >= $1")),
]


def main() -> int:
    wanted = {int(a) for a in sys.argv[1:]}
    failures = []
    attempted = 0
    try:
        for num, claim, target, test, steps in MUTATIONS:
            if wanted and num not in wanted:
                continue
            attempted += 1
            rc, output = 0, ""
            try:
                for step in steps:
                    step()
                rc, output = run_go(target, test)
            finally:
                restore_files()
            red = rc != 0
            print(f"[{'RED' if red else 'GREEN-FAIL'}] #{num:<3} {claim}")
            if not red:
                failures.append((num, claim, test, output))
                print(output[-1500:])
        print(f"\n{attempted - len(failures)}/{attempted} 条变异打红")
        for num, claim, test, _ in failures:
            print(f"  #{num} {test} 仍然通过：{claim}")
    finally:
        restore_files()

    # 复跑全量：证明每份备份都写回去了。变异脚本最怕的不是打红，是打红之后把源码留在坏状态。
    for pkg in ("./internal/domain/", "./internal/store/", "./internal/messaging/outbox/"):
        proc = subprocess.run(["go", "test", pkg, "-count=1"], cwd=GO,
                              capture_output=True, text=True, env=ENV, timeout=900)
        tail = (proc.stdout + proc.stderr).strip().splitlines()
        print(f"[{'GREEN' if proc.returncode == 0 else 'BROKEN'}] 复跑 {pkg}：{tail[-1] if tail else ''}")
        if proc.returncode != 0:
            print((proc.stdout + proc.stderr)[-2000:])
            return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
