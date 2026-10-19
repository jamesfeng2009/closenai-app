#!/usr/bin/env python3
"""CLOSENAI_V3_TASK_BOARD.md 的进度汇总与一致性校验。

用法：
    python scripts/task_board.py            # 打印进度看板
    python scripts/task_board.py --check    # 校验总表与详情一致（CI/提交前）

为什么要有这个脚本：
台账的价值取决于「总表说的」和「详情写的」是同一件事。手抄两遍必然漂移，
而漂移之后进度就不再可信。这里只做三件事：解析、对账、汇总，不引入任何依赖。

口径（与台账 §0.1 一致）：
    TODO / DOING / BLOCKED / REVIEW / DONE / CANCELLED
    DONE 必须带一行 `> 结案 ...`（日期/PR/测试计数），否则视为未结案。
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "CLOSENAI_V3_TASK_BOARD.md"

STATUSES = ("TODO", "DOING", "BLOCKED", "REVIEW", "DONE", "CANCELLED")
WAVES = ("W0", "W1", "W2", "W3", "W4", "W5", "W6")

ID_RE = re.compile(r"^T\d-\d{2}$")
SUMMARY_RE = re.compile(
    r"^\|\s*(T\d-\d{2})\s*\|([^|]*)\|([^|]*)\|([^|]*)\|\s*(W\d)\s*\|\s*([A-Z]+)\s*\|([^|]*)\|"
)
DETAIL_HEAD_RE = re.compile(r"^###\s+(T\d-\d{2})\b")
DETAIL_STATUS_RE = re.compile(
    r"^\|\s*\**(" + "|".join(STATUSES) + r")\**\s*\|([^|]*)\|([^|]*)\|([^|]*)\|"
)
DONE_RECEIPT_RE = re.compile(r"^>\s*结案\s+\d{4}-\d{2}-\d{2}")


class Task:
    __slots__ = ("tid", "title", "owner", "deps", "wave", "status", "evidence",
                 "detail_owner", "detail_status", "has_how", "has_accept", "has_receipt")

    def __init__(self, tid: str) -> None:
        self.tid = tid
        self.title = self.owner = self.deps = self.wave = ""
        self.status = self.evidence = ""
        self.detail_owner = self.detail_status = ""
        self.has_how = self.has_accept = self.has_receipt = False


def parse(text: str) -> dict[str, Task]:
    tasks: dict[str, Task] = {}
    section = ""
    current: Task | None = None

    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            current = None
            continue

        if section.startswith("1."):
            m = SUMMARY_RE.match(line)
            if m:
                tid = m.group(1)
                t = tasks.setdefault(tid, Task(tid))
                t.title = m.group(2).strip()
                t.owner = m.group(3).strip()
                t.deps = m.group(4).strip()
                t.wave = m.group(5)
                t.status = m.group(6).strip()
                t.evidence = m.group(7).strip()
            continue

        if section.startswith("2."):
            m = DETAIL_HEAD_RE.match(line)
            if m:
                tid = m.group(1)
                current = tasks.setdefault(tid, Task(tid))
                continue
            if current is None:
                continue
            if line.startswith("**怎么做") or line.startswith("**T0-01a"):
                current.has_how = True
            if line.startswith("**验收"):
                current.has_accept = True
            if DONE_RECEIPT_RE.match(line):
                current.has_receipt = True
            sm = DETAIL_STATUS_RE.match(line)
            if sm and not current.detail_owner:
                current.detail_owner = sm.group(2).strip()
                # 详情里的状态是第二真相：这里只在 --check 时比对，不覆盖总表
                current.detail_status = sm.group(1)
    return tasks


def normalize_owner(value: str) -> str:
    return re.sub(r"\s*\+\s*", "+", value.replace(" ", "")).upper()


def bar(done: int, total: int, width: int = 14) -> str:
    filled = 0 if total == 0 else round(width * done / total)
    return "█" * filled + "·" * (width - filled)


def print_board(tasks: dict[str, Task]) -> None:
    ids = sorted(tasks)
    counts = Counter(t.status for t in tasks.values())
    total = len(ids)
    done = counts.get("DONE", 0)
    pct = 0 if total == 0 else round(100 * done / total)

    print(f"任务总数 {total}｜DONE {done}（{pct}%）｜DOING {counts.get('DOING', 0)}"
          f"｜REVIEW {counts.get('REVIEW', 0)}｜BLOCKED {counts.get('BLOCKED', 0)}"
          f"｜TODO {counts.get('TODO', 0)}｜CANCELLED {counts.get('CANCELLED', 0)}")
    print(f"总进度  [{bar(done, total)}] {done}/{total}")
    print()

    print("按波次")
    for wave in WAVES:
        group = [tasks[i] for i in ids if tasks[i].wave == wave]
        if not group:
            continue
        wdone = sum(1 for t in group if t.status == "DONE")
        wdoing = sum(1 for t in group if t.status == "DOING")
        wblock = sum(1 for t in group if t.status == "BLOCKED")
        print(f"  {wave}  [{bar(wdone, len(group))}] {wdone}/{len(group)}"
              f"  DOING {wdoing}  BLOCKED {wblock}")

    print()
    print("按状态")
    for status in STATUSES:
        group = [tasks[i] for i in ids if tasks[i].status == status]
        if group:
            print(f"  {status:10s} {len(group):2d}  {','.join(t.tid for t in group)}")


def check(tasks: dict[str, Task]) -> list[str]:
    problems: list[str] = []
    for tid in sorted(tasks):
        t = tasks[tid]
        if not t.title:
            problems.append(f"{tid}: 详情存在但总表没有这一行")
        if t.status not in STATUSES:
            problems.append(f"{tid}: 非法状态 {t.status!r}")
        detail_status = t.detail_status
        if not detail_status:
            problems.append(f"{tid}: 详情里找不到状态行")
        elif detail_status != t.status:
            problems.append(f"{tid}: 总表状态 {t.status} ≠ 详情状态 {detail_status}")
        if t.owner and t.detail_owner and normalize_owner(t.owner) != normalize_owner(t.detail_owner):
            problems.append(f"{tid}: 总表负责 {t.owner!r} ≠ 详情负责 {t.detail_owner!r}")
        if not t.has_how:
            problems.append(f"{tid}: 详情缺少「怎么做」")
        if not t.has_accept:
            problems.append(f"{tid}: 详情缺少「验收」")
        if t.status == "DONE" and not t.has_receipt:
            problems.append(f"{tid}: DONE 但没有 `> 结案 YYYY-MM-DD` 记录")

    # 总表里有、详情里没有的，会因为 detail_status 缺失被上面的循环抓到；
    # 详情里有、总表里没有的，title 为空。
    known = {t.tid for t in tasks.values() if ID_RE.match(t.tid)}
    if len(known) != len(tasks):
        problems.append(f"任务 ID 形状异常：{sorted(set(tasks) - known)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="任务台账进度与一致性")
    parser.add_argument("--check", action="store_true", help="只做一致性校验（CI 用）")
    parser.add_argument("--owner", help="只看某个角色：BE / GO / FE / PLAT / ARCH")
    parser.add_argument("--status", help="只看某个状态：TODO / DOING / ...")
    parser.add_argument("--file", default=str(BOARD), help="台账路径")
    args = parser.parse_args(argv)

    path = Path(args.file)
    if not path.exists():
        print(f"台账不存在：{path}", file=sys.stderr)
        return 2

    tasks = parse(path.read_text(encoding="utf-8"))

    if args.owner:
        want = args.owner.upper()
        tasks = {
            k: v for k, v in tasks.items()
            if want in normalize_owner(v.owner).split("+")
        }
    if args.status:
        want = args.status.upper()
        tasks = {k: v for k, v in tasks.items() if v.status == want}
    if (args.owner or args.status) and not tasks:
        print("没有匹配的任务", file=sys.stderr)
        return 1

    if args.check:
        problems = check(tasks)
        if problems:
            print("台账不一致：", file=sys.stderr)
            for item in problems:
                print(f"  - {item}", file=sys.stderr)
            return 1
        print(f"台账一致：{len(tasks)} 个任务，总表与详情同源")
        return 0

    print_board(tasks)
    problems = check(tasks)
    if problems:
        print()
        print(f"⚠️ 台账有 {len(problems)} 处不一致（运行 --check 查看）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
