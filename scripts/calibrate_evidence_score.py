"""N4 的标定回测（G-5）：把 03 §5 的全部档位组合摊成 strength 分布，
量化「geomean（默认）vs product（V3 字面连乘）」的偏离，供 env 调参时对照。

数值只有一份来源：go-core/tools/calibrate 用 domain.DefaultPolicy()（即 env 覆盖后的
实际生效值）算分，本脚本不复制第二套权重表。换 env 重跑就是换一版权重回测：

    CLOSEN_EVIDENCE_CLASS_B_FLOOR=0.6 .venv/bin/python scripts/calibrate_evidence_score.py

没有真实用户数据可标定（本机无 prod 库），所以这里给的是**合成分布**：
它只能回答「这组默认值会把多少比例的正常证据压到 C/D」，不能回答「真实用户有多少证据到 C/D」。
后者要等 prod 回测，登记在 tasks.md 的 N4 欠账里。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path("/Users/fengyu/Downloads/myproject/workspace/closenai-app")
GO = ROOT / "go-core"
GOROOT = os.environ.get("GOROOT", "/Users/fengyu/sdk/go1.27.1")
ENV = {**os.environ, "GOROOT": GOROOT, "PATH": f"{GOROOT}/bin:{os.environ['PATH']}"}

# 03 §5 表里「最正常的一档」：AI 从明确文本抽出一条具体观察、单次、30 天内。
REFERENCE = {"source_kind": "ai_extraction", "explicitness": "explicit",
             "specificity": "specific", "independent": 1, "age_days": 15}
CLASSES = ["A", "B", "C", "D"]
CLASS_NOTE = {"A": "可直接支撑用户可见 claim", "B": "可支撑派生 claim",
              "C": "只能内部探索，不得直接展示", "D": "不得面向用户"}


def samples() -> tuple[list[dict], dict[str, dict[str, float]]]:
    """明细行 + 每个 mode 实际生效的分档下限（不在此复制第二套数值）。"""
    proc = subprocess.run(["go", "run", "./tools/calibrate", "-mode=both"],
                          cwd=GO, env=ENV, capture_output=True, text=True, check=True)
    out, floors = [], {}
    for line in proc.stdout.splitlines():
        row = json.loads(line)
        if "source_kind" in row:
            out.append(row)
        elif "class_floors" in row:
            floors[row["mode"]] = row["class_floors"]
    return out, floors


def histogram(rows: list[dict]) -> None:
    total = len(rows)
    counts = Counter(r["class"] for r in rows)
    print(f"  合计 {total} 个组合")
    for cls in CLASSES:
        n = counts[cls]
        bar = "#" * max(1, round(60 * n / total)) if n else ""
        print(f"  {cls} {n:>5} ({100 * n / total:5.1f}%) {bar}  {CLASS_NOTE[cls]}")
    weak = counts["C"] + counts["D"]
    print(f"  C+D 占比 {100 * weak / total:.1f}%——「正常证据被压成不可展示」的比例")


def by_mode(rows: list[dict], mode: str) -> list[dict]:
    return [r for r in rows if r["mode"] == mode]


def reference(rows: list[dict], mode: str) -> dict | None:
    for r in rows:
        if r["mode"] != mode:
            continue
        if all(r[k] == v for k, v in REFERENCE.items()):
            return r
    return None


def by_source_kind(rows: list[dict], b_floor: float) -> None:
    kinds: dict[str, list[float]] = {}
    for r in rows:
        kinds.setdefault(r["source_kind"], []).append(r["strength"])
    print(f"  来源            均值   A+B 占比（B 下限 {b_floor}）")
    for kind, values in sorted(kinds.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
        ab = sum(1 for v in values if v >= b_floor) / len(values)
        print(f"  {kind:<22}{sum(values) / len(values):.3f}   {100 * ab:5.1f}%")


def main() -> int:
    dump_to = None
    if len(sys.argv) > 2 and sys.argv[1] == "--out":
        dump_to = Path(sys.argv[2])

    rows, floors = samples()
    for mode in ("geomean", "product"):
        subset = by_mode(rows, mode)
        print(f"\n== {mode} ==")
        histogram(subset)
        ref = reference(subset, mode)
        if ref:
            print(f"  基准档（03 §5 最正常的一档）strength={ref['strength']:.4f} → {ref['class']}")
        by_source_kind(subset, floors[mode]["B"])

    g, p = reference(by_mode(rows, "geomean"), "geomean"), \
        reference(by_mode(rows, "product"), "product")
    if g and p:
        print(f"\n偏离量化：基准档 geomean {g['strength']:.4f}({g['class']}) vs "
              f"product {p['strength']:.4f}({p['class']})——"
              "V3 字面连乘把这一档压成不可展示，这就是默认改几何平均的理由。")

    if dump_to:
        dump_to.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        print(f"\n明细已写 {dump_to}（{len(rows)} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
