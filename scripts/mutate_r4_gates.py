#!/usr/bin/env python3
"""R4（异步发射器：拓扑／confirm／重试／命令面／进程接线）的变异自证（2026-10-16）。

靶点来自工作单 §三 的逐字要求（`R4管道工作单-2026-10-16.md:150-153`）：
  「M1 无 confirm 也置 published（把 confirm 等待摘掉）／M2 命令行走进十条事件路由（污染判据）／
    M3 retry 阶梯退化成无限 requeue／M4 摘掉 kill switch 让 worker 无条件起」
⇒ M1–M4 就是那四条，一条不打折。M5–M6 是本批 R4-④（进程接线）自己交付的两条：
M5 摘掉「在途那一批用不受停机信号影响的 ctx」（17 §11 步 2），M6 把停机收尾从 `defer`
改成当场执行（表现是 worker 刚起就被停掉，而 HTTP 退出时没人等它）。这两条都是
**dispatch 包自己看不见**的形态——M6 尤其：它坏掉时十三条 dispatch 闸全绿。
M7 摘掉双投预算警告：不改变投递，只是让「一批的最坏确认时间 > 租约」这件事静默上线。

口径照抄既有纪律（见 [[feedback-mutation-gates-no-concurrency]]、
[[feedback-mutation-restore-no-git-checkout]]、[[feedback-definition-of-done-tests]]）：
**串行跑**、copy2 备份写回 ＋ `filecmp` 逐字节复核、**不用 `git checkout`**、
「受害测试没跑起来」不算红——Go 侧的对应形态是 `[build failed]`（编译坏掉时
`--- FAIL:` 根本不会出现），所以 `is_red` 三件齐判：非零退出 ＋ 有 `--- FAIL:` ＋ 名片段命中。

闸的选择口径同 R3：每条变异只喂它该打红的那一张闸，且要求报错原文里出现名片段，
否则「红」可能是别的用例连带崩的假红。G1–G4 反过来证明这些闸不是假红制造机：
加注释、放宽默认预算、改日志措辞都不该红。

用法：
    ./.venv/bin/python scripts/mutate_r4_gates.py --check        # 只验锚点，不改文件
    ./.venv/bin/python scripts/mutate_r4_gates.py                # 全跑（7 红 + 4 绿）
    ./.venv/bin/python scripts/mutate_r4_gates.py --only M1,M4
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

PUBLISHER_GO = GO_CORE / "internal" / "messaging" / "amqp" / "publisher.go"
TOPOLOGY_GO = GO_CORE / "internal" / "messaging" / "amqp" / "topology.go"
OUTBOX_GO = GO_CORE / "internal" / "messaging" / "outbox" / "outbox.go"
DISPATCH_GO = GO_CORE / "internal" / "messaging" / "dispatch" / "dispatch.go"
MAIN_GO = GO_CORE / "cmd" / "go-api" / "main.go"

AMQP_PKG = "./internal/messaging/amqp/"
OUTBOX_PKG = "./internal/messaging/outbox/"
DISPATCH_PKG = "./internal/messaging/dispatch/"
CMD_PKG = "./cmd/go-api/"

# 本机 PATH 上的 go 是混合安装（见 [[go-toolchain-mixed-install]]），一编译就报版本不符，
# 所以点名 GOROOT 里的工具链，不依赖 shell 环境。
GOROOT = "/Users/fengyu/sdk/go1.27.1"
GO_ENV = {**os.environ, "GOROOT": GOROOT, "PATH": f"{GOROOT}/bin:{os.environ.get('PATH', '')}"}

# ── 名片段（受害闸的报错原文片段；改断言文案要同时改这里，否则绿的是脚本、红的是真相）──
MARK_NACK = "nack 之后回了 nil"
MARK_EVENT_SIDE = "被绑进了事件面"
MARK_LADDER = "退避写回"
MARK_KILL = "开关关着却拨了"
MARK_DRAIN = "ctx 已取消"
MARK_SPAWN = "worker 没接上进程"
MARK_BUDGET = "双投"

# 每条：(id, 说明, (文件, 原文, 坏文), go 包, -run 正则, 名片段)
MUTATIONS = [
    (
        "M1",
        "§三 第①条「无 confirm 也置 published」：把 publisher.go 里那句「broker 没 ack 就回 "
        "ErrNegativeAck」摘掉 ⇒ outbox 会把一条 broker 根本没收下的消息置成 published。"
        "这一条坏掉**没有任何用户可见报错**，只是永久丢事件（14 §11 逐字禁的就是这个）",
        (PUBLISHER_GO,
         '\tacked, err := p.conf.PublishConfirmed(cctx, exchange, msg.RoutingKey, pub)\n'
         '\tif err != nil {\n'
         '\t\treturn fmt.Errorf("amqp: publish %s: %w", msg.MessageID, err)\n'
         '\t}\n'
         '\tif !acked {\n'
         '\t\treturn fmt.Errorf("amqp: %s → %w", msg.MessageID, ErrNegativeAck)\n'
         '\t}\n',
         '\t_, err = p.conf.PublishConfirmed(cctx, exchange, msg.RoutingKey, pub)\n'
         '\tif err != nil {\n'
         '\t\treturn fmt.Errorf("amqp: publish %s: %w", msg.MessageID, err)\n'
         '\t}\n'),
        AMQP_PKG, "^TestNegativeAckIsAnErrorNotASuccess$", MARK_NACK,
    ),
    (
        "M2",
        "§三 第②条「命令行走进十条事件路由」：把 observation 那条命令的 exchange 从 "
        "`closenai.ai.tasks` 改成 `closenai.events` ⇒ 命令进事件账本，而进门判据"
        "（`domain/delivery.go:31-36`「权威模型内容变了」）说的正是命令不满足它（D-R4-3）",
        (TOPOLOGY_GO,
         '{RoutingKey: domain.AITaskObservation.RoutingKey(), Exchange: ExchangeAITasks, '
         'Queue: "ai.observation.extract",',
         '{RoutingKey: domain.AITaskObservation.RoutingKey(), Exchange: ExchangeEvents, '
         'Queue: "ai.observation.extract",'),
        AMQP_PKG, "^TestCommandRouteKeysComeFromDomain$", MARK_EVENT_SIDE,
    ),
    (
        "M3",
        "§三 第③条「阶梯退化成无限 requeue」：把 `MarkOutboxRetry` 的 retryAfter 从策略阶梯"
        "改成恒 0 ⇒ 行刚写回就重新可认领，14 §8 的 30s/2m/10m 三档一起失效，"
        "一条毒消息变成对库的对撞循环（17 §9 要的正是它不许阻塞队列）",
        (OUTBOX_GO,
         "\t\tw.policy.RetryDelay(claim.AttemptCount), reason)",
         "\t\t0, reason)"),
        OUTBOX_PKG, "^TestRunOnceRetriesWithLadder$", MARK_LADDER,
    ),
    (
        "M4",
        "§三 第④条「摘掉 kill switch 让 worker 无条件起」：把 `if !sw.Enabled` 摘成恒假 ⇒ "
        "灰度 0 的部署也会去拨 broker、声明拓扑、认领 outbox，"
        "「接线不许改变现网任何响应」这条验收当场失效",
        (DISPATCH_GO,
         "\tif !sw.Enabled {\n\t\tr.logf(\"outbox worker 未启动：%s 没给 1（kill switch 默认关）\", EnvWorker)\n",
         "\tif false {\n\t\tr.logf(\"outbox worker 未启动：%s 没给 1（kill switch 默认关）\", EnvWorker)\n"),
        DISPATCH_PKG, "^TestKillSwitchOffTouchesNothing$", MARK_KILL,
    ),
    (
        "M5",
        "R4-④ 停机语义（17 §11 步 2）：把在途那一批的收尾 ctx 从 `WithoutCancel(ctx)` 改回 "
        "`ctx` ⇒ SIGTERM 一到，最后一次发布当场被取消，行留在 publishing 状态等租约到期，"
        "而看起来像在优雅收尾。这条与 M4 是两种相反的坏法：一个不该动、一个该做完",
        (DISPATCH_GO,
         "\t\tdrain, cancel := context.WithTimeout(context.WithoutCancel(ctx), sw.DrainTimeout)",
         "\t\tdrain, cancel := context.WithTimeout(ctx, sw.DrainTimeout)"),
        DISPATCH_PKG, "^TestShutdownFinishesInFlightBatch$", MARK_DRAIN,
    ),
    (
        "M6",
        "R4-④ 调用点：把 `defer closeWorker(...)` 的 defer 摘掉 ⇒ 这行在启动瞬间就把 worker "
        "取消掉、进程退出时再没人等它收尾。**dispatch 包那十三条闸看不见调用点**，"
        "所以这一条只能由 cmd 包的源码闸兜住（这就是它存在的理由）",
        (MAIN_GO, "defer closeWorker(worker, stopWorker, workerDone,",
         "closeWorker(worker, stopWorker, workerDone,"),
        CMD_PKG, "^TestMainSpawnsTheWorker$", MARK_SPAWN,
    ),
    (
        "M7",
        "双投预算警告：把「一批最坏确认时间 > 租约 ⇒ 启动时说话」摘成恒假 ⇒ 参数写错时"
        "没人提醒，慢 broker 上同一行会被第二个 worker 双投。不拦停、只说话，"
        "所以这条坏掉的红没有任何副作用——正是最容易静默上线的一类",
        (DISPATCH_GO,
         "\tif worst := time.Duration(r.deps.Policy.BatchSize) * sw.ConfirmTimeout; "
         "worst > r.deps.Policy.OutboxLease() {",
         "\tif worst := time.Duration(r.deps.Policy.BatchSize) * sw.ConfirmTimeout; "
         "worst > r.deps.Policy.OutboxLease() && false {"),
        DISPATCH_PKG, "^TestPublishBudgetWarningAtStartup$", MARK_BUDGET,
    ),
]

# 绿闸：这些改动**不该**红。判据只认行为与形状，注释密度、合法的默认值余量、
# 日志措辞都不算违规——红过一次就会被当成噪音关掉的那类闸，本来就不该存在。
GREEN = [
    (
        "G1",
        "给 dispatch.go 加 12 行中文注释：包级闸判的是 Run 的行为，不是文本命中——"
        "否则「注释里写了 kill switch」也会红",
        (DISPATCH_GO,
         "// 开关面（全 env-only，默认关；凭据不进代码也不进日志，见 amqp.redactURL）。",
         "".join(f"// 进程面绿闸占位注释 {i}\n" for i in range(12))
         + "// 开关面（全 env-only，默认关；凭据不进代码也不进日志，见 amqp.redactURL）。"),
        DISPATCH_PKG, "",
    ),
    (
        "G2",
        "把默认收尾预算从 30s 放宽到 45s：停机闸判的是「收尾预算有上限」，"
        "而用例自己给 DrainTimeout=5s——钉死默认值的那种闸会因为一次调参而红",
        (DISPATCH_GO, "defaultDrainTimeout   = 30 * time.Second",
         "defaultDrainTimeout   = 45 * time.Second"),
        DISPATCH_PKG, "",
    ),
    (
        "G3",
        "给 publisher.go 的 ContentTypeJSON 说明加两行注释（不改代码）：nack 闸判的是"
        "确认语义，判不到注释；而 D-R4-1 那段口径本来就要留在这里",
        (PUBLISHER_GO,
         "// ContentTypeJSON 是 payload 的 MIME（D-R4-1）。",
         "// ContentTypeJSON 的补充说明：本协议标签由 payload 的真实格式决定，不由 14 §5 的期望决定。\n"
         "// O1 换 protobuf 时连着 schema_version 一起递增。\n"
         "// ContentTypeJSON 是 payload 的 MIME（D-R4-1）。"),
        AMQP_PKG, "",
    ),
    (
        "G4",
        "把会话失败日志里的「重投」改成「重新投递」：措辞闸是最脆的闸，"
        "这一包判的是「失败要说一句」，不是那三个字",
        (DISPATCH_GO,
         '⇒ 这一行会在租约到期后重投", claimID, stage, err)',
         '⇒ 这一行会在租约到期后重新投递", claimID, stage, err)'),
        DISPATCH_PKG, "",
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


def run(pkg: str, node: str) -> tuple[int, str]:
    cmd = ["go", "test", pkg, "-count=1", "-timeout", "120s"]
    if node:
        cmd += ["-run", node]
    proc = subprocess.run(cmd, cwd=GO_CORE, env=GO_ENV,
                          capture_output=True, text=True, timeout=1800)
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
    with tempfile.TemporaryDirectory(prefix="mutate-r4-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            # 备份名带序号＋父目录：本批同时打 amqp/publisher.go 与 amqp/topology.go 这类
            # 同目录多文件，只用文件名会在 tmp 里互相覆盖，于是「恢复」把 A 写回 B，
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
            for cid, desc, mutation, pkg, node, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(mutation)
                try:
                    code, out = run(pkg, node)
                finally:
                    restore()
                if is_red(code, out) and marker in out:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（exit={code}，名片段 {marker!r} 命中={marker in out}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-16:]))

            for cid, desc, mutation, pkg, node in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(mutation)
                try:
                    code, out = run(pkg, node)
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-16:]))
        except Exception as exc:  # noqa: BLE001
            restore()
            print(f"异常中止，已恢复：{exc!r}")
            return 2

        # 收尾自证：四个被动过的包回到绿。跑过 M1–M7 之后文件是写回恢复的，
        # 但残渣只有「真的再跑一遍」才验得出来，不能只信 filecmp。
        for pkg in (AMQP_PKG, OUTBOX_PKG, DISPATCH_PKG, CMD_PKG):
            code, out = run(pkg, "")
            print(f"复核 {pkg}：{'绿' if code == 0 else '红（残留！）'}")
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
