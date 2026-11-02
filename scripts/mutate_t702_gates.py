#!/usr/bin/env python3
"""T7-02（分析事件逐事件确认与幂等重试）的变异自证（2026-10-25）。

被验对象与各自的靶：
- M1 前端把 retryable 当成确认接收（删队列）→ 「HTTP 成功 ≠ 已接收」失守；
- M2 死信原因被写成固定 'permanent' → 运维看不出是 consent_off 还是 invalid_event_id；
- M3 退避被改成「立刻可重试」→ 网络抖动时无限热重发；
- M4 重试耗尽不再进死信 → 一条坏事件永远卡在队列里；
- M5 Python 不查同批重复 event_id → 同一批两条都外发；
- M6 中继失败仍把台账置 accepted → 重试被判 duplicate，事件永久丢失；
- M7 无 event_id 也走新形状 → N8 冻结的旧响应被破坏；
- M8 Python 放宽 event_id 长度 → 129 字符也会被接受；
- M9 Go 不认 ack 模式（始终旧形状）→ 前端拿不到逐事件结果；
- M10 Go 把 pending 台账当成 accepted → in_flight 被误判 duplicate；
- M11 Go 长度边界差一（128 也被拒）→ 合法幂等键被判 invalid；
- M12 Go 结果键序被打乱 → 逐字契约破坏；
- M13 Go accepted 时删掉台账 → 下次重试重新外发，事件翻倍。

G1/G2 是「不该打红」的证人：只加注释时三侧闸必须仍然绿。

纪律：串行执行、copy2 备份＋filecmp 逐字节写回、不用 git checkout
（[[feedback-mutation-restore-no-git-checkout]]、[[feedback-mutation-gates-no-concurrency]]）；
每条红闸先跑基线，基线不绿记「未跑」，不把环境的红算成闸的红
（[[feedback-mutation-anchor-must-reach-target]]）。

用法：
    python3 scripts/mutate_t702_gates.py --check
    python3 scripts/mutate_t702_gates.py
    python3 scripts/mutate_t702_gates.py --only M1,M9
"""
from __future__ import annotations

import argparse
import filecmp
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FRONT = REPO / "frontend"
GO_CORE = REPO / "go-core"
VENV_PY = REPO / ".venv" / "bin" / "python"

ANALYTICS_TS = FRONT / "src" / "services" / "analytics.ts"
ANALYTICS_GO = GO_CORE / "internal" / "httpapi" / "analytics_batch.go"
ANALYTICS_PY = REPO / "backend" / "services" / "analytics_service.py"

FE_ACK = "__tests__/analyticsAck.test.ts"
FE_ACK_CONTRACT = "__tests__/analyticsAckContract.test.ts"
GO_ACK_PKG = "./internal/httpapi/"
GO_ACK_RUN = "TestAnalyticsAck"
PY_SERVICE = "backend/tests/test_analytics_service.py"
PY_IN_BATCH = (
    f"{PY_SERVICE}::test_ack_mode_in_batch_duplicate_relays_once_even_after_failure"
)
PY_RETRY = f"{PY_SERVICE}::test_ack_mode_retry_after_relay_failure_is_not_a_duplicate"
PY_LEGACY = f"{PY_SERVICE}::test_relay_batch_drops_all_without_consent"
PY_BOUNDARY = f"{PY_SERVICE}::test_shared_ack_contract_cases[id_length_boundary]"

GOROOT = "/Users/fengyu/sdk/go1.27.1"
GO_BIN = f"{GOROOT}/bin/go"
GO_ENV = {**os.environ, "GOROOT": GOROOT, "PATH": f"{GOROOT}/bin:{os.environ.get('PATH', '')}",
          "GO_BIN": os.environ.get("GO_BIN", GO_BIN)}

# (id, 描述, [编辑], runner, target, go -run 节点, 失败标记)
CASES: list[tuple] = [
    (
        "M1",
        "前端把 retryable 当成确认接收：HTTP 200 被当成所有事件已落地",
        [(ANALYTICS_TS,
          "        default:\n"
          "          counts.retryable += scheduleRetry(p, ack.reason ?? 'retryable', nowMs);",
          "        default:\n"
          "          counts.accepted += 1;\n"
          "          acceptedIds.push(p.id);")],
        "jest", FE_ACK, "", "accepted/duplicate 删除，retryable 保留退避",
    ),
    (
        "M2",
        "死信原因写死 'permanent'：consent_off / invalid_event_id 不可区分",
        [(ANALYTICS_TS,
          "          analyticsEventsRepo.markDeadLetter(p.id, ack.reason ?? 'permanent', nowIso);",
          "          analyticsEventsRepo.markDeadLetter(p.id, 'permanent', nowIso);")],
        "jest", FE_ACK, "", "accepted/duplicate 删除，retryable 保留退避",
    ),
    (
        "M3",
        "退避改成立刻可重试：网络抖动时无限热重发",
        [(ANALYTICS_TS,
          "    new Date(nowMs + retryDelayMs(attempts)).toISOString()",
          "    new Date(nowMs).toISOString()")],
        "jest", FE_ACK, "", "可重试失败按指数退避",
    ),
    (
        "M4",
        "重试耗尽不再进死信：坏事件永远卡在队列",
        [(ANALYTICS_TS,
          "  if (attempts >= MAX_FLUSH_ATTEMPTS) {",
          "  if (attempts >= Number.MAX_SAFE_INTEGER) {")],
        "jest", FE_ACK, "", "重试次数到顶进死信",
    ),
    (
        "M5",
        "Python 不查同批重复 event_id：同一批两条都外发",
        [(ANALYTICS_PY,
          "            if event_id in seen_in_batch:\n"
          "                # 同一批里出现两次：第二条不是「重试」，是重复提交，直接判 duplicate。\n"
          "                results.append(_ack_item(ev, \"duplicate\"))\n"
          "                continue",
          "            if False:\n"
          "                continue")],
        "pytest", PY_IN_BATCH, "",
        "test_ack_mode_in_batch_duplicate_relays_once_even_after_failure",
    ),
    (
        "M6",
        "中继失败仍把台账置 accepted：下一次重试被判 duplicate，事件永久丢失",
        [(ANALYTICS_PY,
          "                await analytics_receipt_repository.release(db, user_id, event_id)",
          "                await analytics_receipt_repository.mark_accepted(db, user_id, event_id)")],
        "pytest", PY_RETRY, "", "test_ack_mode_retry_after_relay_failure_is_not_a_duplicate",
    ),
    (
        "M7",
        "无 event_id 也走新形状：旧客户端响应被改",
        [(ANALYTICS_PY,
          "        ack_mode = any(getattr(ev, \"event_id\", None) is not None for ev in events)",
          "        ack_mode = True")],
        "pytest", PY_LEGACY, "", "test_relay_batch_drops_all_without_consent",
    ),
    (
        "M8",
        "Python 去掉 event_id 长度上限：129 字符也被接受",
        [(ANALYTICS_PY,
          "    if isinstance(value, str) and 0 < len(value) <= MAX_EVENT_ID_LENGTH:",
          "    if isinstance(value, str) and 0 < len(value):")],
        "pytest", PY_BOUNDARY, "", "test_shared_ack_contract_cases",
    ),
    (
        "M9",
        "Go 不认 ack 模式（始终旧形状）：前端拿不到逐事件结果",
        [(ANALYTICS_GO, "\tif analyticsAckMode(events) {", "\tif false {")],
        "go", GO_ACK_PKG, GO_ACK_RUN, "",
    ),
    (
        "M10",
        "Go 把 pending 台账当成 accepted：in_flight 被误判 duplicate",
        [(ANALYTICS_GO, "\t\t\tif prior == \"accepted\" {", "\t\t\tif prior != \"\" {")],
        "go", GO_ACK_PKG, GO_ACK_RUN, "",
    ),
    (
        "M11",
        "Go 长度边界差一：128 字符的合法 event_id 也被拒",
        [(ANALYTICS_GO,
          "\tif utf8.RuneCountInString(raw.str) > maxAnalyticsEventIDLength {",
          "\tif utf8.RuneCountInString(raw.str) >= maxAnalyticsEventIDLength {")],
        "go", GO_ACK_PKG, GO_ACK_RUN, "",
    ),
    (
        "M12",
        "Go 逐事件结果键序被打乱：event_id 不再是第一键",
        [(ANALYTICS_GO,
          "type analyticsAckResult struct {\n"
          "\tEventID analyticsJSONValue `json:\"event_id\"`\n"
          "\tStatus  string             `json:\"status\"`",
          "type analyticsAckResult struct {\n"
          "\tStatus  string             `json:\"status\"`\n"
          "\tEventID analyticsJSONValue `json:\"event_id\"`")],
        "go", GO_ACK_PKG, GO_ACK_RUN, "",
    ),
    (
        "M13",
        "Go accepted 时删掉台账：下次重试重新外发，事件翻倍",
        [(ANALYTICS_GO,
          "\t\t\tif err := s.batch1.Receipts.MarkAccepted(r.Context(), userID, eventID); err != nil {",
          "\t\t\tif err := s.batch1.Receipts.Release(r.Context(), userID, eventID); err != nil {")],
        "go", GO_ACK_PKG, GO_ACK_RUN, "",
    ),
]

GREEN: list[tuple] = [
    (
        "G1",
        "前端只加一行注释：共享契约用例必须仍然绿",
        [(ANALYTICS_TS,
          "// 并发上送锁（防多入口同时 flush 重复发送）。",
          "// 变异证人：注释不该弄红闸。\n// 并发上送锁（防多入口同时 flush 重复发送）。")],
        "jest", FE_ACK_CONTRACT, "", "",
    ),
    (
        "G2",
        "Go 只加一行注释：逐事件确认用例必须仍然绿",
        [(ANALYTICS_GO,
          "\t// eventID 是客户端幂等键（`event_id: Any = None`）。nil＝请求里没给/给了 null，",
          "\t// 变异证人：注释不该弄红闸。\n"
          "\t// eventID 是客户端幂等键（`event_id: Any = None`）。nil＝请求里没给/给了 null，")],
        "go", GO_ACK_PKG, GO_ACK_RUN, "",
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
            return f"{path} 不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if n != 1:
            return f"{path.name} 命中 {n} 次（要 1）"
    return None


def run(runner: str, target: str, node: str) -> tuple[int, str]:
    if runner == "jest":
        cmd = ["npx", "jest", "--silent", "--runInBand", target]
        cwd, timeout, env = FRONT, 900, None
    elif runner == "go":
        cmd = [GO_BIN, "test", target, "-count=1", "-timeout", "300s"]
        if node:
            cmd += ["-run", node]
        cwd, timeout, env = GO_CORE, 900, GO_ENV
    else:
        assert node == "", "pytest 的节点 id 就写在 target 里，node 留空"
        cmd = [str(VENV_PY), "-m", "pytest", target, "-q", "--no-header", "-x"]
        cwd, timeout, env = REPO, 900, None
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def is_red(runner: str, code: int, out: str, marker: str) -> bool:
    if code == 0:
        return False
    if runner == "jest":
        if not re.search(r"\d+ failed", out):
            return False
        return marker in out if marker else True
    if runner == "go":
        # 编译坏 / 没匹配到用例都不算打红：红的是工具链，不是闸。
        if "[build failed]" in out or "no tests to run" in out:
            return False
        return "--- FAIL:" in out
    if "no tests ran" in out:
        return False
    return "FAILED" in out and (marker in out if marker else True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数，不改任何文件")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()
    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    cases = [*CASES, *GREEN]
    if args.check:
        bad = 0
        for case in cases:
            problem = _verify_anchor(case[2])
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}（共 {len(cases)} 条）")
        return 1 if bad else 0

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-t702-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            bak = Path(tmp) / f"{idx:02d}-{path.name}"
            shutil.copy2(path, bak)
            originals[path] = bak

        def restore() -> None:
            for path, bak in originals.items():
                shutil.copy2(bak, path)
                assert filecmp.cmp(bak, path, shallow=False), f"{path} 恢复后字节不一致"

        red_ok = red_fail = green_ok = green_fail = 0
        failures: list[str] = []
        try:
            for cid, desc, edits, runner, target, node, marker in CASES:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = run(runner, target, node)
                if base_code != 0:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 未跑（基线本来就不绿，exit={base_code}）："
                          "不能把环境的红记成闸的红\n  "
                          + "\n  ".join(base_out.splitlines()[-8:]))
                    continue
                _apply(edits)
                try:
                    code, out = run(runner, target, node)
                finally:
                    restore()
                if is_red(runner, code, out, marker):
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓（{runner}）  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（{runner} exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))

            for cid, desc, edits, runner, target, node, _marker in GREEN:
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

        residue = 0
        for runner, target, node, label in (
            ("jest", FE_ACK_CONTRACT, "", "共享契约（前端）"),
            ("go", GO_ACK_PKG, GO_ACK_RUN, "逐事件确认（Go）"),
            ("pytest", PY_SERVICE, "", "逐事件确认（Python）"),
        ):
            code, out = run(runner, target, node)
            if code == 0:
                print(f"复核 {label}：绿")
            else:
                residue += 1
                print(f"残留（红！）{label}：\n  "
                      + "\n  ".join(out.splitlines()[-15:]))
        print(f"\nT7-02 变异自证：打红 {red_ok}/{len(CASES)}，保持绿 {green_ok}/{len(GREEN)}，"
              f"失败 {red_fail + green_fail}，未跑 0，残留 {residue}")
        if failures:
            print(f"失败清单：{failures}")
        return 1 if (red_fail or green_fail or residue) else 0


if __name__ == "__main__":
    sys.exit(main())
