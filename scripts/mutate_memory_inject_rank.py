"""A5 · 记忆注入位次闸门的变异自证（2026-10-17）。

拍的是 `tasks.md` A5 那三条决策点（原文见该条 ✅ 块）：
① 默认口径 = spec memory-layer.md §2.2 的纯距离序，加权只动**位次**不动**成员集合**；
② 重排在回归识别循环之后 ⇒ revert 的调用序列不随模式变；
③ 权重与半衰期是**占位数字**（文档零出处，标定归 #59），所以闸只拍单调性与不变量。

手法沿用既有约定：备份 → 写坏 → 跑对应靶 → 备份写回 → `filecmp(shallow=False)` 逐字节核验；
不用 `git checkout`（2026-10-13 约定）；**串行**跑（并发会把变异态当原件写回）。

用法：
    ./.venv/bin/python scripts/mutate_memory_inject_rank.py --check        # 只验锚点
    ./.venv/bin/python scripts/mutate_memory_inject_rank.py                # 11 红 + 3 绿
    ./.venv/bin/python scripts/mutate_memory_inject_rank.py --only M6,G3
"""
from __future__ import annotations

import filecmp
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/fengyu/Downloads/myproject/workspace/closenai-app")
SVC = ROOT / "backend/services/memory_service.py"
CFG = ROOT / "backend/config.py"
REPO = ROOT / "backend/repositories/memory_repository.py"
TESTS = ROOT / "backend/tests/test_memory_inject_rank.py"
TARGET = str(TESTS.relative_to(ROOT))

# ── 锚点（逐字，count 必须 == 1，--check 验） ──────────────────
ANCHOR_DEFAULT = '    memory_inject_rank: str = "distance"  # distance | weighted'
ANCHOR_DISTANCE_EARLY_RETURN = (
    "    if mode == INJECT_RANK_DISTANCE:\n        return responses\n"
)
ANCHOR_CALL_SITE = "        return order_for_injection(responses)\n"
ANCHOR_CONVICTION_TERM = (
    "        + settings.memory_inject_conviction_weight * (resp.conviction or 0.0)\n"
)
ANCHOR_RECENCY = "    recency = math.exp(-math.log(2.0) * age_days / half_life)"
ANCHOR_HALF_LIFE = "    half_life = max(1, settings.memory_inject_decay_half_life_days)"
ANCHOR_UNKNOWN_MODE = "    if mode != INJECT_RANK_WEIGHTED:"
ANCHOR_SORT = "    return sorted(responses, key=lambda r: -_inject_rank_score(r, moment))"
ANCHOR_SEARCH_RETURN = "        return [_to_response_from_row(r) for r in rows]"
ANCHOR_SQL_ORDER = "            ORDER BY embedding <=> CAST(:q AS halfvec)"
ANCHOR_REVERT_LOOP = "        for row in rows:\n            resp = _to_response_from_row(row)"
ANCHOR_W_C_DEFAULT = "    memory_inject_conviction_weight: float = 0.1"

BACKUPS: dict[Path, Path] = {}


def backup(*paths: Path) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="a5-gate-backup-"))
    for path in paths:
        bak = tmp / f"{path.parent.name}__{path.name}"
        shutil.copy2(path, bak)
        BACKUPS[path] = bak


def restore_files() -> None:
    for path, bak in BACKUPS.items():
        shutil.copy2(bak, path)
        assert filecmp.cmp(bak, path, shallow=False), f"恢复失败：{path}"


def patch(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    n = text.count(old)
    assert n == 1, f"变异锚点命中 {n} 次，应为 1：{old[:70]!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def insert_before(path: Path, anchor: str, block: str) -> None:
    patch(path, anchor, block + anchor)


def run(target: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "pytest", "-q", "--no-header",
         "-p", "no:cacheprovider", target],
        cwd=ROOT, capture_output=True, text=True, timeout=900)
    return proc.returncode, proc.stdout + proc.stderr


def check_anchors() -> int:
    """只验锚点唯一性 + 基线绿，不改任何文件。"""
    texts = {p: p.read_text(encoding="utf-8") for p in (SVC, CFG, REPO)}
    pairs = [
        (CFG, ANCHOR_DEFAULT), (SVC, ANCHOR_DISTANCE_EARLY_RETURN), (SVC, ANCHOR_CALL_SITE),
        (SVC, ANCHOR_CONVICTION_TERM), (SVC, ANCHOR_RECENCY), (SVC, ANCHOR_HALF_LIFE),
        (SVC, ANCHOR_UNKNOWN_MODE), (SVC, ANCHOR_SORT), (SVC, ANCHOR_SEARCH_RETURN),
        (REPO, ANCHOR_SQL_ORDER), (SVC, ANCHOR_REVERT_LOOP), (CFG, ANCHOR_W_C_DEFAULT),
    ]
    bad = [f"{text.count(a)} 次：{a[:60]!r}" for path, a in pairs
           if (text := texts[path]) and text.count(a) != 1]
    for line in bad:
        print(f"锚点不唯一 ✗  {line}")
    if bad:
        return 1
    code, out = run(TARGET)
    print(f"锚点 12/12 唯一 ✓ 基线 {'绿 ✓' if code == 0 else '红 ✗'}")
    if code != 0:
        print(out[-800:])
        return 1
    return 0


# (id, 靶, 动作, 这条不变量在说什么)  expect_red=True 时靶必须红
MUTATIONS = [
    ("M1", f"{TARGET}::test_default_rank_is_distance_and_is_the_shipped_default",
     lambda: patch(CFG, ANCHOR_DEFAULT, '    memory_inject_rank: str = "weighted"'),
     "默认口径必须是 distance：改成默认加权＝在未标定的情况下改掉 spec §2.2 钉死的位次口径"),

    ("M2", f"{TARGET}::test_get_memory_context_keeps_distance_order_by_default",
     lambda: patch(SVC, ANCHOR_DISTANCE_EARLY_RETURN,
                   "    if mode == INJECT_RANK_DISTANCE:\n"
                   "        moment = now or utc_now()\n"
                   "        return sorted(responses, key=lambda r: -_inject_rank_score(r, moment))\n"),
     "distance 模式必须原序返回：让它也按加权分排，没开加权的用户就会被一条未标定的数字重排注入顺序"),

    ("M3", f"{TARGET}::test_weighted_rank_keeps_revert_call_sequence",
     lambda: patch(SVC, ANCHOR_CALL_SITE, "        return responses\n"),
     "注入位次必须真的被调用：摘掉这条调用，weighted 形同不存在，缺口原样留着"),

    ("M4", f"{TARGET}::test_weighted_rank_prefers_higher_conviction_at_equal_similarity",
     lambda: patch(SVC, ANCHOR_CONVICTION_TERM, ""),
     "conviction 必须进位次分：删掉这一项就是 A5 缺口本体的字面复发"),

    ("M5", f"{TARGET}::test_recency_term_halves_after_one_half_life_and_ignores_naive_times",
     lambda: patch(SVC, ANCHOR_RECENCY, "    recency = 1.0"),
     "衰减必须是年龄的函数：写成常数等于「没有时间衰减」——A5 登记的另一半"),

    ("M6", f"{TARGET}::test_zero_half_life_falls_back_instead_of_crashing",
     lambda: patch(SVC, ANCHOR_HALF_LIFE, "    half_life = settings.memory_inject_decay_half_life_days"),
     "半衰期必须取下限：env 配 0 时整条对话注入链路会 ZeroDivisionError 打死"),

    ("M7", f"{TARGET}::test_unknown_mode_falls_back_to_distance_and_says_so",
     lambda: patch(SVC, ANCHOR_UNKNOWN_MODE, '    if mode == "never-match":'),
     "未知 env 必须退回默认口径并留 warning：静默按加权处理，运维以为没开其实开了"),

    ("M8", f"{TARGET}::test_weighted_ties_keep_input_order_not_id_order",
     lambda: patch(SVC, ANCHOR_SORT,
                   "    return sorted(responses, key=lambda r: (-_inject_rank_score(r, moment), r.id))"),
     "同分必须保持输入序（即距离序）：换成按 id 打破平局会把「§2.2 的距离序」偷换成「主键序」"),

    ("M9", f"{TARGET}::test_public_search_path_is_not_reordered",
     lambda: patch(SVC, ANCHOR_SEARCH_RETURN,
                   "        return order_for_injection([_to_response_from_row(r) for r in rows])"),
     "公共搜索端点不接重排：那条顺序是响应字节（#24 Go 复刻时要逐字同形），未标定前不许动"),

    ("M10", f"{TARGET}::test_semantic_search_sql_order_by_is_still_pure_distance",
     lambda: patch(REPO, ANCHOR_SQL_ORDER,
                   "            ORDER BY (1 - (embedding <=> CAST(:q AS halfvec))) * conviction_score DESC"),
     "权重不许进 SQL：改了 ORDER BY 就同时改了「谁进 top-5」，而 top-5 是回归识别的取数窗口"),

    ("M11", f"{TARGET}::test_weighted_rank_keeps_revert_call_sequence",
     lambda: patch(SVC, ANCHOR_REVERT_LOOP,
                   "        rows = list(reversed(rows))\n" + ANCHOR_REVERT_LOOP),
     "revert 的迭代顺序必须是距离序：这条同时证明「重排在循环之后」不是嘴上说的"),

    # ── 绿闸：这些改动都不该把任何闸打死 ──
    ("G1", TARGET,
     lambda: insert_before(SVC, ANCHOR_SORT, "".join(
         f"    # 增长占位 {i}（证明闸钉的是语句形态，不是行号窗口）\n" for i in range(15))),
     "给 order_for_injection 插 15 行注释：函数体变长不该让任何一条位次闸变红"),

    ("G2", TARGET,
     lambda: insert_before(SVC, ANCHOR_REVERT_LOOP, "".join(
         f"        # 循环前占位 {i}\n" for i in range(10))),
     "给回归循环前插 10 行注释：源码切片闸找的是锚点语句，行窗漂移不该打死它"),

    ("G3", TARGET,
     lambda: patch(CFG, ANCHOR_W_C_DEFAULT,
                   "    memory_inject_conviction_weight: float = 0.25"),
     "conviction 权重从 0.1 调到 0.25（合法的占位调参）：闸拍的是单调性与不变量，不是具体数值"),
]


def main() -> int:
    argv = sys.argv[1:]
    if "--check" in argv:
        return check_anchors()
    only = None
    if "--only" in argv:
        only = {t.strip() for t in argv[argv.index("--only") + 1].split(",")}

    backup(SVC, CFG, REPO, TESTS)
    targets = [m for m in MUTATIONS if not only or m[0] in only]
    problems: list[str] = []
    red_ok = green_ok = 0
    try:
        for mid, target, mutate, claim in targets:
            is_green = mid.startswith("G")
            mutate()
            code, out = run(target)
            if is_green:
                if code == 0:
                    green_ok += 1
                    print(f"[{mid}] 应保持绿 ✓  {claim}")
                else:
                    problems.append(f"{mid} 应保持绿却红了：{claim}\n{out[-900:]}")
                    print(f"[{mid}] 应保持绿 ✗ 红在：{claim}")
            else:
                if code == 0:
                    problems.append(f"{mid} 破坏后闸门仍绿＝没拍住：{claim}")
                    print(f"[{mid}] 打红 ✗（仍绿）  {claim}")
                elif re.search(r"\b\d+ failed\b", out):
                    red_ok += 1
                    print(f"[{mid}] 打红 ✓  {claim}")
                else:
                    # 「没跑出断言失败」不能算红：收集错/夹具崩/超时都长这样
                    problems.append(f"{mid} 没出现 failed 计数，受害测试可能压根没跑起来：{claim}\n{out[-900:]}")
                    print(f"[{mid}] 打红 ?（无 failed 计数）  {claim}")
            restore_files()
    finally:
        restore_files()
    total = len(targets)
    expect_red = sum(1 for m in targets if not m[0].startswith("G"))
    print(f"\n变异自证合计：{total} 条｜红闸 {red_ok}/{expect_red}｜绿闸 {green_ok}/{total - expect_red}")
    if problems:
        print("\n".join(problems))
        return 1
    print("源文件已全部按备份写回并逐字节校验一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
