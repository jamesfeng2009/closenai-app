#!/usr/bin/env python3
"""T7-13（订阅接口原子限流）的变异自证（2026-10-26）。

判据不是「测试跑过」，而是「每条纪律都能被打红一次，且红在它该红的闸上」。
7 条红靶覆盖：上限的临界值、窗口翻转、D1 故障方向、无 IP 的失败方向、
蜜罐短路、重复邮箱短路、限流键的隐私面。2 条绿靶证明这些闸不是见字就红。

口径照抄既有纪律（[[feedback-mutation-gates-no-concurrency]]、
[[feedback-mutation-restore-no-git-checkout]]、[[feedback-definition-of-done-tests]]）：
**串行跑**、copy2 备份写回 ＋ `filecmp` 逐字节复核、**不用 `git checkout`**。

用法：
    python3 scripts/mutate_t713_gates.py --check
    python3 scripts/mutate_t713_gates.py
    python3 scripts/mutate_t713_gates.py --only M1,M3
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
SUBSCRIBE = REPO / "web" / "functions" / "api" / "subscribe.js"
WEB_TEST = REPO / "tests" / "web" / "subscribe.test.mjs"

TEST_ARG = "tests/web/subscribe.test.mjs"

# 每条：(id, 说明, (文件, 原文, 坏文), 名片段)
MUTATIONS = [
    (
        "M1",
        "上限判据从 > 改成 >=：每分钟只放过两次，配置写 3 实际 2——"
        "限流比承诺更严，正常用户先被打到",
        (SUBSCRIBE,
         "  if (count > RATE_LIMIT_MAX) {",
         "  if (count >= RATE_LIMIT_MAX) {"),
        "并发十个请求只有三个通过",
    ),
    (
        "M2",
        "窗口翻转退化成永远自增：新窗口仍接着上一窗口的数，"
        "被限的人要等到全局冷却才恢复",
        (SUBSCRIBE,
         "    WHEN subscribe_rate_limits.window_start = excluded.window_start\n"
         "      THEN subscribe_rate_limits.count + 1\n"
         "    ELSE 1\n",
         "    WHEN subscribe_rate_limits.window_start = excluded.window_start\n"
         "      THEN subscribe_rate_limits.count + 1\n"
         "    ELSE subscribe_rate_limits.count + 1\n"),
        "窗口过期后同一 IP 恢复通过",
    ),
    (
        "M3",
        "D1 报错时当成第一次放行（fail-open）：限流库挂了等于没有限流，"
        "而且这条故障不会出现在任何拒绝计数里",
        (SUBSCRIBE,
         "  } catch {\n"
         "    return json({ ok: false, error: 'rate_limit_unavailable' }, 503);\n"
         "  }",
         "  } catch {\n"
         "    row = { count: 1 };\n"
         "  }"),
        "D1 故障时失败关闭",
    ),
    (
        "M4",
        "拿不到 cf-connecting-ip 时不再拒绝：所有无头请求共用同一个空串桶，"
        "要么一起被限、要么（桶还没满时）完全不受限",
        (SUBSCRIBE,
         "  if (!ip) {\n"
         "    return json({ ok: false, error: 'missing_client_ip' }, 400);\n"
         "  }",
         "  if (!ip) {\n"
         "    // mutated: 放行\n"
         "  }"),
        "缺少 cf-connecting-ip",
    ),
    (
        "M5",
        "蜜罐不再短路：机器人填了隐藏字段也被计一次限流并写入名单，"
        "蜜罐从「静默丢弃」变成「照收」",
        (SUBSCRIBE,
         "  if (body.website) {\n"
         "    return json({ ok: true, duplicate: false });\n"
         "  }",
         "  if (body.website) {\n"
         "    // mutated: 不返回，继续走完\n"
         "  }"),
        "蜜罐命中时静默成功",
    ),
    (
        "M6",
        "重复邮箱不再短路：同一邮箱第二次也走完整写入路径，"
        "duplicate 语义消失（KV 同键虽然覆盖，但响应与计数都变）",
        (SUBSCRIBE,
         "  if (await env.WAITLIST.get(key)) {\n"
         "    return json({ ok: true, duplicate: true });\n"
         "  }",
         "  if (false && (await env.WAITLIST.get(key))) {\n"
         "    return json({ ok: true, duplicate: true });\n"
         "  }"),
        "重复邮箱仍返回 duplicate",
    ),
    (
        "M7",
        "限流键改存原始 IP：限流表从此留下可回溯的自然人网络标识，"
        "保留期条款说的「只存截断哈希」不再成立",
        (SUBSCRIBE,
         "  const bucket = 'ip:' + (await sha256(ip)).slice(0, 32);",
         "  const bucket = 'ip:' + ip;"),
        "限流表只落截断哈希",
    ),
]

GREEN = [
    (
        "G1",
        "在限流常量上方加两行注释（不改代码）：闸判的是行为，不读注释",
        (SUBSCRIBE,
         "const RATE_LIMIT_MAX = 3;",
         "// 注释不改变判据。\nconst RATE_LIMIT_MAX = 3;"),
    ),
    (
        "G2",
        "在测试文件的辅助函数上方加两行注释（不改判据）：绿靶证明 runner 本身不假红",
        (WEB_TEST,
         "const statuses = (xs) => xs.map((x) => x.res.status).sort((a, b) => a - b);",
         "// 注释不改变判据。\n"
         "const statuses = (xs) => xs.map((x) => x.res.status).sort((a, b) => a - b);"),
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


def run() -> tuple[int, str]:
    proc = subprocess.run(
        ["node", "--test", "--test-reporter=tap", TEST_ARG],
        cwd=REPO, env=os.environ, capture_output=True, text=True, timeout=300,
    )
    return proc.returncode, proc.stdout + proc.stderr


def is_red(code: int, out: str, marker: str) -> bool:
    if code == 0:
        return False
    if "no test files" in out or "Could not find" in out:
        return False
    return "not ok" in out and marker in out


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

    if shutil.which("node") is None:
        print("未跑：宿主没有 node，不能把这条记成绿")
        return 2

    code, out = run()
    if code != 0:
        print("基线就是红的：先修基线，变异自证没有意义")
        print("\n".join(out.splitlines()[-20:]))
        return 1
    print("基线：绿（13 条）")

    paths = sorted({case[2][0] for case in [*MUTATIONS, *GREEN]})
    with tempfile.TemporaryDirectory(prefix="mutate-t713-") as tmp:
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
            for cid, desc, mutation, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(mutation)
                try:
                    code, out = run()
                finally:
                    restore()
                if is_red(code, out, marker):
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（exit={code}，名片段 {marker!r} 命中={marker in out}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-14:]))

            for cid, desc, mutation in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(mutation)
                try:
                    code, out = run()
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-14:]))
        except Exception as exc:  # noqa: BLE001
            restore()
            print(f"异常中止，已恢复：{exc!r}")
            return 2

        code, out = run()
        print(f"复核 {TEST_ARG}：{'绿' if code == 0 else '红（残留！）'}")
        if code != 0:
            print("\n".join(out.splitlines()[-14:]))
            return 1

        print(f"\n计数：红闸 {red_ok} 打红 / {red_fail} 未打红；"
              f"绿闸 {green_ok} 保持绿 / {green_fail} 被误伤；异常 0")
        if failures:
            print("未通过：" + ",".join(failures))
        return 1 if (red_fail or green_fail) else 0


if __name__ == "__main__":
    sys.exit(main())
