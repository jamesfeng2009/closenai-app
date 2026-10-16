"""share 导出缓存键闸门的变异自证（2026-10-14 年报卡，2026-10-16 加周期报告 S1）。

两族不变量共用四条口径：**键含语种 / 后缀取规范化码 / 键的另一维不能被顶掉 / 命中缓存必须早退**。
被拍的两侧是 `share_service` 的 `_year_in_review_key`（年报卡）与 `_period_key`（weekly/monthly/
quarterly/yearly 报告）——2026-10-14 收口年报时发现的「周期报告同型缺陷」当时按「不顺手修」登记为
tasks.md:49，本次就是那条的结案闸。

手法只用「备份 → 写坏 → 跑那一条测试 → 备份写回 → filecmp 逐字节核验」，
不用 git checkout（2026-10-13 约定：仓里有并行未提交工作，checkout 会连带冲掉）。
串行跑，不并发（并发会把变异态当原件写回）。

用法（两侧测试都要真 PG，先给 TEST_DATABASE_URL）：
    TEST_DATABASE_URL=postgresql://postgres:…@127.0.0.1:55432/closenai \\
    .venv/bin/python scripts/mutate_share_lang_key_gate.py
"""
from __future__ import annotations

import filecmp
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path("/Users/fengyu/Downloads/myproject/workspace/closenai-app")
SVC = ROOT / "backend/services/share_service.py"
YEAR_TESTS = ROOT / "backend/tests/test_year_in_review.py"
REPORT_TESTS = ROOT / "backend/tests/test_share_service.py"
KEY_LINE = '        return f"year_{year}_{copy.locale_for(language)}"'
# 缓存读路径（只在 export_year_in_review 里出现一次，靠 period 常量定位）
CACHE_HIT = (
    "        cached = await share_repository.get_cached(\n"
    "            db, user_id, _YEAR_IN_REVIEW_PERIOD, period_key\n"
    "        )\n"
    "        if cached is not None:\n"
    "            return json.loads(cached.payload)\n"
)


def backup(*paths: Path) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="share-gate-backup-"))
    for path in paths:
        bak = tmp / path.name
        shutil.copy2(path, bak)
        BACKUPS[path] = bak


BACKUPS: dict[Path, Path] = {}


def restore_files() -> None:
    for path, bak in BACKUPS.items():
        shutil.copy2(bak, path)
        assert filecmp.cmp(bak, path, shallow=False), f"恢复失败：{path}"


def patch(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"变异锚点命中 {text.count(old)} 次，应为 1：{old[:70]!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def run(suite: Path, test_name: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "pytest", "-q", "--no-header",
         "-p", "no:cacheprovider",
         str(suite) + f"::{test_name}"],
        cwd=ROOT, capture_output=True, text=True, timeout=900)
    return proc.returncode, proc.stdout + proc.stderr


# 周期报告（export_report）那一侧的锚点：2026-10-16 S1 收口 tasks.md:49 时加进来，
# 与年报卡同一族不变量、同一套四条口径（含语种 / 规范化 / 键的另一维 / 命中即早退）。
REPORT_KEY_LINE = '        return f"{base}_{copy.locale_for(language)}"'
REPORT_CACHE_HIT = (
    "        cached = await share_repository.get_cached(db, user_id, period, period_key)\n"
    "        if cached is not None:\n"
    "            return json.loads(cached.payload)\n"
)
NO_EARLY_RETURN = "        if cached is not None:\n            return json.loads(cached.payload)\n"


# (测试文件, 测试名, 破坏动作, 这条不变量在说什么)
MUTATIONS = [
    (YEAR_TESTS, "test_cache_key_is_language_scoped",
     lambda: patch(SVC, KEY_LINE, '        return f"year_{year}"'),
     "年报卡缓存键必须含语种：否则换语言的用户拿回首次生成那个语种的卡"),

    (YEAR_TESTS, "test_cache_hits_within_same_language_including_region_variant",
     lambda: patch(SVC, KEY_LINE, '        return f"year_{year}_{language}"'),
     "后缀取规范化语言码：zh 与 zh-CN 必须命中同一条，否则每种地区变体重算一遍"),

    (YEAR_TESTS, "test_cache_row_uses_year_scoped_period_key",
     lambda: patch(SVC, KEY_LINE, '        return f"{year}_{copy.locale_for(language)}"'),
     "year_ 前缀必须在：去掉就会与 yearly 报告的裸年份键互相吃缓存"),

    (YEAR_TESTS, "test_export_is_cached_once_per_year",
     lambda: patch(SVC, CACHE_HIT, CACHE_HIT.replace(NO_EARLY_RETURN, "")),
     "命中缓存必须直接返回：去掉这条早退，每个请求都重算并重新落库，缓存形同不存在"),

    (REPORT_TESTS, "test_export_report_cache_key_is_language_scoped",
     lambda: patch(SVC, REPORT_KEY_LINE, "        return base"),
     "周期报告缓存键必须含语种：载荷的 period_label/mood_trend/正文全按 language 生成"),

    (REPORT_TESTS, "test_export_report_cache_hits_within_same_language_including_region_variant",
     lambda: patch(SVC, REPORT_KEY_LINE, '        return f"{base}_{language}"'),
     "周期报告后缀也要取规范化码：zh 与 zh-CN 必须命中同一条"),

    (REPORT_TESTS, "test_export_report_period_key_keeps_calendar_scoping",
     lambda: patch(SVC, REPORT_KEY_LINE, "        return copy.locale_for(language)"),
     "语种后缀不能顶掉日历维度：键一旦只剩语种，同一 (user, period) 永久返回第一次那一份"),

    (REPORT_TESTS, "test_export_report_is_cached_once_per_period",
     lambda: patch(SVC, REPORT_CACHE_HIT, REPORT_CACHE_HIT.replace(NO_EARLY_RETURN, "")),
     "周期报告命中缓存必须早退：不提前返回时 save() 撞唯一约束返回旧行，但函数返回刚算的那份"),
]


def main() -> int:
    backup(SVC, YEAR_TESTS, REPORT_TESTS)
    problems: list[str] = []
    try:
        for suite, test_name, mutate, claim in MUTATIONS:
            mutate()
            code, out = run(suite, test_name)
            reason = re.search(r"(?m)^E +(.{1,120})", out)
            if code == 0:
                problems.append(f"{test_name}：破坏后闸门仍然绿，等于没拍住")
                verdict = "GREEN(坏)"
            elif re.search(r"\b\d+ failed\b", out):
                verdict = "RED "
            else:
                verdict = "ERROR"
                problems.append(f"{test_name}：没跑出断言失败，先看输出\n{out[-600:]}")
            print(f"{verdict} {test_name[:60]:62} {claim}")
            if reason:
                print(f"      └─ {reason.group(1).strip()[:120]}")
            restore_files()
    finally:
        restore_files()
    if problems:
        print("\n".join(problems))
        return 1
    print(f"\n全部 {len(MUTATIONS)} 条变异都被对应闸门打红，恢复后文件逐字节一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
