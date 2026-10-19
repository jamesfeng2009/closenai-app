#!/usr/bin/env python3
"""T2-05（DLQ 重放工具与异步指标）的变异自证（2026-10-19）。

这一批交付的东西有两类，各有一种「看着绿其实没判」的死法，所以两侧都得上靶
（[[feedback-mutation-anchor-must-reach-target]]）：

* **判据本体被掏空**（dlq 包 M1…M11）：重放工具的每一条护栏都是一句「不许」——
  不许把阶梯队列当终态面重放、不许带假确认串清队列、不许把 peek 出来的消息留在手里、
  不许猜原始键、不许把「捞空了」报成「到上限了」。掏掉哪一格，红的就必须是**那一格自己的用例**。
* **口径漂了没人知道**（headers／obs／main／工具，M11…M19）：x-death 的字段名是 broker 给的、
  指标名是告警要引用的、kill switch 是现网默认的，这三类东西一旦被改写成「另一种说法」，
  编译器与单元测试都不报错——只有字面闸会红。

靶面分四侧（runner 一律 `go test -run <点名用例>`，缺 GOROOT 记「未跑」）：

`internal/messaging/dlq`（fake broker 面，28 条）——
- M1 `x-death` 内部字段名改回 `x-reason` ⇒ 那条名字是 2026-10-19 真 broker 集成测跑实的，
  写成 `x-reason` 之后摘要里的「死因」永远是空；
- M2 `-purge` 的确认串从「逐字等于队列名」退化成「非空」⇒ `--confirm yes` 就能清掉一个队列；
- M3 重放不再拒阶梯／purpose 队列 ⇒ 一次 `-replay retry.30s.…` 会把还在阶梯里的消息提前放行；
- M4 重放不再清 `x-death` ⇒ 对面按 x-death 计数换层的消费侧会把它当「已经试过 3 次」立刻打死；
- M5 摘掉「队列上有消费者就不 peek」的护栏 ⇒ 巡检会从在途交付里抢一条，账会被自己动过；
- M6 摘掉「peek 出来必放回」⇒ 一次 `--list` 就丢一条消息，而且是最该留案底的那条；
- M7 「队列先空」报成「到上限」⇒ 值班据此再敲一轮，而那条队列已经没了；
- M8 重放不看 broker 记的原始键 ⇒ 候选面三个键里回错一个，对面收下的是**类型写错的洞察**；
- M9 `dlq_total` 把阶梯队列算进来 ⇒ 正常瞬态抖动也触发告警，第一次误报之后没人再看它；
- M10 年龄优先序反转（先 x-death 后 Timestamp，且没 Timestamp 时拿零值算）⇒ 「读不到」变成「天文数字」；
- M11 形态表少一种（`[]any`）⇒ 真 broker 给的那一种形状读成「没有死信记录」，摘要全空。

`internal/obs`（日志面，8 条）——
- M12 `read == nil` 从「拒绝启动」变成「静默空转」⇒ 运维把它读成「异步面正在被巡检」；
- M13 汇总行的 `dlq_total` 算成全量（同 M9 的日志侧对口）；
- M14 年龄 0 秒被判成「不知道」⇒ 刚投进去的积压永远不进告警；
- M15 三个指标名之一改名 ⇒ Loki 里那条查询扫空集，而告警规则是**文档**，不会跟着红；
- M16 明细行的 `msg` 改名 ⇒ 「哪一格堵了」这一格从此查不到。

`cmd/go-api`（接线面，7 条）——
- M17 摘掉 spawn 那一行（只留 ctx）⇒ dlq 28 条＋obs 8 条全绿，指标面其实没起（假接线）；
- M18 给指标面加第二个 env 键 ⇒ D-G12 的「假旋钮」红线，banned-key 那一格咬住。

`tools/dlqreplay`（命令行面，10 条）——
- M19 摘掉「拨号之前先验确认串」⇒ 清错队列名时工具先握手再报错（本批补的正向证人）；
- M20 `run` 面绕过 `dlq.Purge` 直接清 ⇒ 确认串判据在两处之一失效，口径分叉。

不该打红的证人（GR1／GR2／GR3）：往 `Purge`、`summarize`、`queueMetricsTarget` 上方各插 15 行注释
⇒ 三侧照旧全绿。这几条证明闸判的是**语义与字面**，不钉行号也不钉排版——否则一次纯格式化
就会把这套闸门全打成红，那之后的处置一定是「关掉闸」而不是「修代码」。

纪律：串行执行、copy2 备份＋`filecmp(shallow=False)` 逐字节写回、不用 git checkout
（[[feedback-mutation-restore-no-git-checkout]]、[[feedback-mutation-gates-no-concurrency]]）。
每条变异前先跑基线（按 (target,marker) 缓存）；基线本来就不绿的记「未跑」，
不把环境的红记成闸的红；`[build failed]` 一律记「打红 ✗（变异体自身不合法）」，
因为那种红证明的是 Go 编译器，不是判据。

用法：
    ./.venv/bin/python scripts/mutate_t205_gates.py --check
    GOROOT=/Users/fengyu/sdk/go1.27.1 ./.venv/bin/python scripts/mutate_t205_gates.py
    ./.venv/bin/python scripts/mutate_t205_gates.py --only M4,M17
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

DLQ = GO_CORE / "internal" / "messaging" / "dlq" / "dlq.go"
HDR = GO_CORE / "internal" / "messaging" / "dlq" / "headers.go"
OBS = GO_CORE / "internal" / "obs" / "queue_metrics.go"
MAIN = GO_CORE / "cmd" / "go-api" / "main.go"
TOOL = GO_CORE / "tools" / "dlqreplay" / "main.go"

PKG_DLQ = "./internal/messaging/dlq/"
PKG_OBS = "./internal/obs/"
PKG_MAIN = "./cmd/go-api/"
PKG_TOOL = "./tools/dlqreplay/"

T = "\t"

MUTATIONS: list[tuple] = [
    # ── dlq 包：判据本体 ────────────────────────────────────────────────
    (
        "M1", "x-death 内部字段名改回 x-reason ⇒ 摘要里的死因永远是空（真 broker 跑实的那格）",
        [(DLQ, f'{T}HeaderXReason     = "reason"\n', f'{T}HeaderXReason     = "x-reason"\n')],
        PKG_DLQ, "^TestXDeathFieldNamesAreVerbatim$",
    ),
    (
        "M2", "purge 的确认串从逐字等于队列名退化成非空 ⇒ --confirm yes 就能清一个队列",
        [(DLQ, f'{T}if strings.TrimSpace(confirm) != queue {{\n',
          f'{T}if strings.TrimSpace(confirm) == "" {{\n')],
        PKG_DLQ, "^TestPurgeRequiresConfirmEqualToQueueName$",
    ),
    (
        "M3", "重放不再拒阶梯／purpose 队列 ⇒ 还在阶梯里的消息被提前放行",
        [(DLQ, f'{T}if QueueKind(queue) != "dlq" {{\n', f'{T}if QueueKind(queue) == "not-a-real-kind" {{\n')],
        PKG_DLQ, "^TestReplayRefusesTierAndPurposeQueues$",
    ),
    (
        "M4", "重放不再清 x-death ⇒ 对面按死信计数换层的消费侧把它当「已经试过 N 次」立刻打死",
        [(DLQ, f'{T}{T}if k == HeaderXDeath || k == amqp.HeaderAttemptCount {{\n',
          f'{T}{T}if k == amqp.HeaderAttemptCount {{\n')],
        PKG_DLQ, "^TestReplayKeepsBytesAndIdentityButClearsCounters$",
    ),
    (
        "M5", "摘掉「队列上有消费者就不 peek」的护栏 ⇒ 巡检从在途交付里抢一条",
        [(DLQ, f'{T}if consumers > 0 {{\n', f'{T}if consumers < 0 {{\n')],
        PKG_DLQ, "^TestListNeverPeeksAQueueWithConsumers$",
    ),
    (
        "M6", "摘掉「peek 出来必放回」⇒ 一次 --list 就丢一条消息",
        [(DLQ, f'{T}defer func() {{ _ = msg.Requeue() }}()\n', f'{T}// 变异 M6：peek 出来的这条不写回原处\n')],
        PKG_DLQ, "^TestListPeeksAndAlwaysPutsTheMessageBack$",
    ),
    (
        "M7", "「队列先空」报成「到上限」⇒ 值班据此再敲一轮，而那队列本来就没货",
        [(DLQ, f'{T}{T}{T}res.Reason = "queue_empty"\n{T}{T}{T}return res, nil\n',
          f'{T}{T}{T}res.Reason = "limit_reached"\n{T}{T}{T}return res, nil\n')],
        PKG_DLQ, "^TestReplayCountsLimitAndEmptySeparately$",
    ),
    (
        "M8", "重放不看 broker 记的原始键 ⇒ 候选面三个键里回错一个（类型写错的洞察）",
        [(DLQ, f'{T}for _, d := range Deaths(m.Headers) {{\n',
          f'{T}for _, d := range []DeathEntry(nil) {{\n')],
        PKG_DLQ, "^TestReplayRoutingKeyUsesTheBrokersOwnDeathRecord$",
    ),
    (
        "M9", "dlq_total 把阶梯队列算进来 ⇒ 正常瞬态抖动也响，第一次误报之后没人再看",
        [(DLQ, f'{T}{T}if s.Kind == "dlq" {{\n', f'{T}{T}if s.Kind != "purpose" {{\n')],
        PKG_DLQ, "^TestDLQTotalCountsOnlyFinalFaces$",
    ),
    (
        "M10", "年龄优先序反转（没 Timestamp 时拿零值算）⇒「读不到」变成天文数字",
        [(DLQ, f'{T}if !m.Timestamp.IsZero() {{\n', f'{T}if m.Timestamp.IsZero() {{\n')],
        PKG_DLQ, "^TestOldestAgePrefersTimestampThenXDeathTime$",
    ),
    # ── headers：broker 形状 ────────────────────────────────────────────
    (
        "M11", "形态表少一种（[]any）⇒ 真 broker 给的那一种读成「没有死信记录」",
        [(HDR, f'{T}case []any:\n{T}{T}var out []map[string]any\n',
          f'{T}case []int32:\n{T}{T}var out []map[string]any\n')],
        PKG_DLQ, "^TestDeathsTakesEveryBrokerShape$",
    ),
    # ── obs：日志面 ─────────────────────────────────────────────────────
    (
        "M12", "read==nil 从拒绝启动变成静默空转 ⇒ 运维读成「异步面正在被巡检」",
        [(OBS, f'{T}{T}return errors.New("queue metrics: 没给 QueueReader ⇒ 这一轮一次都不会读数，拒绝启动")\n',
          f'{T}{T}_ = errors.New("queue metrics: 空转")\n{T}{T}return nil\n')],
        PKG_OBS, "^TestNilReaderRefusesToStart$",
    ),
    (
        "M13", "汇总行的 dlq_total 算成全量 ⇒ 与 DLQTotal 那条口径分叉（日志侧对口）",
        [(OBS, f'{T}{T}if r.Kind == "dlq" {{\n{T}{T}{T}dlqTotal += r.Depth\n{T}{T}}}\n',
          f'{T}{T}dlqTotal += r.Depth\n')],
        PKG_OBS, "^TestBacklogAddsOneDetailLinePerStuckQueue$",
    ),
    (
        "M14", "年龄 0 秒被判成不知道 ⇒ 刚投进去的积压永远不进告警窗口",
        [(OBS, f'{T}{T}if r.Depth > 0 && r.OldestAgeKnown && (!oldestKnown || r.OldestAgeSecs > oldest) {{\n',
          f'{T}{T}if r.Depth > 0 && r.OldestAgeKnown && r.OldestAgeSecs > oldest {{\n')],
        PKG_OBS, "^TestAgeZeroIsKnownNotUnknown$",
    ),
    (
        "M15", "指标名 queue_depth 改名 ⇒ Loki 那条查询扫空集，而告警规则是文档、不会跟着红",
        [(OBS, f'{T}{T}"queue_depth", total,\n', f'{T}{T}"queue_backlog", total,\n')],
        PKG_OBS, "^TestHealthyTickEmitsExactlyOneLineWithThreeMetricNames$",
    ),
    (
        "M16", "明细行的 msg 改名 ⇒「哪一格堵了」这一格从此查不到",
        [(OBS, f'{T}{T}slog.Info("queue_metric", detail...)\n',
          f'{T}{T}slog.Info("queue_metric_line", detail...)\n')],
        PKG_OBS, "^TestBacklogAddsOneDetailLinePerStuckQueue$",
    ),
    # ── cmd/go-api：接线面 ──────────────────────────────────────────────
    (
        "M17", "摘掉 spawn 那一行（只留 ctx）⇒ dlq 与 obs 全绿而指标面其实没起",
        [(MAIN, f'{T}{T}go func() {{ metricsDone <- obs.RunQueueMetrics(metricsCtx, newQueueReader(url)) }}()\n',
          f'{T}{T}_, _ = url, metricsCtx // 变异 M17：循环没起\n')],
        PKG_MAIN, "^TestMainSpawnsTheQueueMetrics$",
    ),
    (
        "M18", "给指标面加第二个 env 键 ⇒ D-G12「假旋钮」红线（banned-key 那一格）",
        [(MAIN, f'{T}if url, ok := queueMetricsTarget(sws, inboundSw); ok {{\n',
          f'{T}if url, ok := queueMetricsTarget(sws, inboundSw); ok && os.Getenv("CLOSEN_QUEUE_METRICS") != "0" {{\n')],
        PKG_MAIN, "^TestMainSpawnsTheQueueMetrics$",
    ),
    # ── tools/dlqreplay：命令行面 ───────────────────────────────────────
    (
        "M19", "摘掉「拨号之前先验确认串」⇒ 清错队列名时工具先握手再报错",
        [(TOOL, f'{T}if opts.purgeConfirmPrecheck() {{\n',
          f'{T}if opts.mode == modePurge && len(opts.queues) == 0 {{\n')],
        PKG_TOOL, "^TestPurgePrecheckRefusesBeforeDial$",
    ),
    (
        "M20", "run 面绕过 dlq.Purge 直接清 ⇒ 确认串判据在两处之一失效，口径分叉",
        [(TOOL, f'{T}{T}n, err := dlq.Purge(ctx, b, o.queues[0], o.confirm)\n',
          f'{T}{T}n, err := b.Purge(ctx, o.queues[0])\n')],
        PKG_TOOL, "^TestPurgeModeRefusesWithoutConfirmBeforeDial$",
    ),
]

GR_GO_15 = "".join(
    f"{T}// 绿闸注脚 {i:02d}：这套闸判的是语义与字面，不钉行号也不钉排版。\n" for i in range(1, 16)
)
GR_GO2_15 = "".join(
    f"// 绿闸注脚 {i:02d}：格式化不是缺陷，本批的闸不许因为一次 gofmt 就把整套闸门打成红。\n" for i in range(1, 16)
)

GREEN: list[tuple] = [
    (
        "GR1", "dlq.go 的 Purge 上方插 15 行注释 ⇒ purge 确认闸照旧绿（它不钉行号）",
        [(DLQ, f'{T}if strings.TrimSpace(confirm) != queue {{\n',
          GR_GO_15 + f'{T}if strings.TrimSpace(confirm) != queue {{\n')],
        PKG_DLQ, "^TestPurgeRequiresConfirmEqualToQueueName$",
    ),
    (
        "GR2", "queue_metrics.go 的 summarize 上方插 15 行注释 ⇒ 形状闸照旧绿",
        [(OBS, 'func summarize(rows []QueueStat) (total, dlqTotal int, oldest float64, oldestQueue string, oldestKnown bool) {\n',
          GR_GO2_15 + 'func summarize(rows []QueueStat) (total, dlqTotal int, oldest float64, oldestQueue string, oldestKnown bool) {\n')],
        PKG_OBS, "^TestHealthyTickEmitsExactlyOneLineWithThreeMetricNames$",
    ),
    (
        "GR3", "main.go 的接线段上方插 15 行注释 ⇒ 调用点文本闸照旧绿（它点的是符号不是位置）",
        [(MAIN, f'{T}if url, ok := queueMetricsTarget(sws, inboundSw); ok {{\n',
          GR_GO_15 + f'{T}if url, ok := queueMetricsTarget(sws, inboundSw); ok {{\n')],
        PKG_MAIN, "^TestMainSpawnsTheQueueMetrics$",
    ),
]


def _apply(edits: list) -> None:
    """把锚点替换成变异体。锚点命中数必须恰好 1——命中两次说明靶写错了。"""
    for path, old, new in edits:
        assert path.exists(), f"{path}：靶文件不存在"
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:120]!r}"
        path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _verify_anchor(edits: list) -> str | None:
    for path, old, _new in edits:
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if n != 1:
            return f"锚点命中 {n} 次（要 1）：{old[:60]!r}"
    return None


def go_available() -> bool:
    return Path(GO_BIN).is_file()


def run(target: str, marker: str) -> tuple[int, str]:
    if not go_available():
        return -2, f"{GO_BIN} 不存在：Go 侧用例没跑，不算绿"
    cmd = [GO_BIN, "test", target, "-count=1", "-timeout", "300s"]
    if marker:
        cmd += ["-run", marker]
    proc = subprocess.run(cmd, cwd=GO_CORE, env=GO_ENV, capture_output=True,
                          text=True, timeout=1800)
    return proc.returncode, proc.stdout + proc.stderr


def _report_name(marker: str) -> str:
    name = marker[1:] if marker.startswith("^") else marker
    return name[:-1] if name.endswith("$") else name


def is_red(code: int, out: str, marker: str) -> bool:
    """「红」＝**那条判据自己**失败。编译失败／没匹配到用例都不算——
    那种红证明的是工具链，不是闸门。"""
    if code == 0:
        return False
    if "[build failed]" in out or "no tests to run" in out or "no test files" in out:
        return False
    return f"--- FAIL: {_report_name(marker)}" in out


BASELINE: dict[tuple[str, str], tuple[int, str]] = {}


def baseline(target: str, marker: str) -> tuple[int, str]:
    key = (target, marker)
    if key not in BASELINE:
        BASELINE[key] = run(target, marker)
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
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}（共 {len(cases)} 条）")
        return 1 if bad else 0

    if not go_available():
        print(f"提示：没找到 {GO_BIN}，全部靶记「未跑」，不算绿")

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-t205-") as tmp:
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
            for cid, desc, edits, target, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(target, marker)
                if base_code == -2:
                    unrun.append(cid)
                    print(f"[{cid}] 未跑（{base_out}）：缺工具链不是绿，也不把它的红记在闸头上")
                    continue
                if base_code != 0:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 未跑（基线本来就不绿，exit={base_code}）："
                          "不能把环境的红记成闸的红\n  " + "\n  ".join(base_out.splitlines()[-8:]))
                    continue
                try:
                    _apply(edits)
                    code, out = run(target, marker)
                finally:
                    restore()
                if code != 0 and "[build failed]" in out:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（变异体编译不过，红的是工具链不是判据）：\n  "
                          + "\n  ".join(out.splitlines()[-10:]))
                    continue
                if is_red(code, out, marker):
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗  {desc}\n  " + "\n  ".join(out.splitlines()[-12:]))

            for cid, desc, edits, target, marker in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(target, marker)
                if base_code != 0:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（基线就不绿，exit={base_code}）：{desc}")
                    continue
                try:
                    _apply(edits)
                    code, out = run(target, marker)
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 被误伤 ✗  {desc}\n  " + "\n  ".join(out.splitlines()[-12:]))
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
            print("ⓘ 有靶未跑：缺工具链一律不算绿")
        return 1 if (red_fail or green_fail or unrun) else 0


if __name__ == "__main__":
    sys.exit(main())
