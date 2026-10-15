#!/usr/bin/env python3
"""U3-b（App Store 假 ID 收口）的变异自证（2026-10-16）。

这条缺陷的正题不是「链接长得不对」，而是「**没上架时发出去一条 404 / 一条空链**」，
而且它断在用户已经分享出去之后，事后修不回来。所以本脚本的靶点全部选在「回到
那个坏状态」的四条路径上：

- B1 把占位 ID 填回默认值 → 源码扫闸 + 默认值闸双红（这正是本批立项时的状态）；
- B2 把回落优先级写反（站点优先）→ 上架后注入真链也会被站点吃掉，归因照样断；
- B3–B6 让某个消费方绕过 share_landing_url 直读原字段 → 未配置时那一条发出空串；
- G1–G4 是 Go 侧同一格：默认值、回落优先级、两处消费点各打一遍——两侧同形才是契约。
- G5 撤掉夹具守卫里那条「AppStoreURL 合法为空」的更正 → 正确形态反而被 t.Fatalf 打死，
  证明这次改守卫不是「为了让测过而放宽」。

**没有**用「把夹具里的 share_url 改回商店链」这类靶：夹具是生成物，改它只证明
测在比字节，不证明产品是对的。

恢复一律 **copy2 + filecmp 逐字节校验**，不用 git checkout
（见 [[feedback-mutation-restore-no-git-checkout]]）；**严禁并发跑**
（见 [[feedback-mutation-gates-no-concurrency]]）。

用法：
    ./.venv/bin/python scripts/mutate_u3b_gates.py --check
    ./.venv/bin/python scripts/mutate_u3b_gates.py
    ./.venv/bin/python scripts/mutate_u3b_gates.py --only B1,G3
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
GO = REPO / "go-core"

CONFIG_PY = REPO / "backend" / "config.py"
SHARE_PY = REPO / "backend" / "services" / "share_service.py"
FIRST_READ_PY = REPO / "backend" / "services" / "first_read_service.py"
CONFIG_GO = GO / "internal" / "config" / "config.go"
SUBMIT_GO = GO / "internal" / "httpapi" / "onboarding_submit.go"
CONTRACT_GO = GO / "internal" / "httpapi" / "n8_contract_test.go"

PY_GATE = "backend/tests/test_share_landing_fallback.py"
GO_CONFIG_PKG = "./internal/config/"
GO_API_PKG = "./internal/httpapi/"

PLACEHOLDER = "https://apps.apple.com/app/closenai/id6740000000"

# (id, 破坏说明, 文件, 原文, 坏文, 测试类型, 目标, 失败输出名片段)
MUTATIONS = [
    (
        "B1",
        f"把占位商店链填回 Python 默认值（＝回到立项时那条 404）",
        CONFIG_PY, '    app_store_url: str = ""',
        f'    app_store_url: str = "{PLACEHOLDER}"',
        "pytest", PY_GATE, "app_store_url 默认",
    ),
    (
        "B2",
        "回落优先级写反（站点压过商店）：上架后注入真链也会被站点吃掉",
        CONFIG_PY, "        return self.app_store_url or self.site_url",
        "        return self.site_url or self.app_store_url",
        "pytest", PY_GATE, "test_configured_store_url_wins_over_site",
    ),
    (
        "B3",
        "first_read 绕过落地口径直读原字段：未配置时落进列里的是空串",
        FIRST_READ_PY, '            "share_url": settings.share_landing_url,',
        '            "share_url": settings.app_store_url,',
        "pytest", PY_GATE, "test_first_read_payload_landing_follows_the_same_rule",
    ),
    (
        "B4",
        "/share/link 的落地字段直读原字段（本人分享入口实际取的就是这一格）",
        SHARE_PY,
        '            # 上架前后同一份响应里它分别指向站点与商店，客户端不必知道有没有上架。\n'
        '            "app_store_url": settings.share_landing_url,',
        '            # 上架前后同一份响应里它分别指向站点与商店，客户端不必知道有没有上架。\n'
        '            "app_store_url": settings.app_store_url,',
        "pytest", PY_GATE, "test_share_link_for_owner_carries_landing",
    ),
    (
        "B5",
        "公开端点 /share/resolve 的落地字段直读原字段（落地页 CTA 用的就是它）",
        SHARE_PY,
        '            "valid": valid,\n            "app_store_url": settings.share_landing_url,',
        '            "valid": valid,\n            "app_store_url": settings.app_store_url,',
        "pytest", PY_GATE, "test_resolve_referral_landing_field_is_the_landing",
    ),
    (
        "B6",
        "Life Archive 的 share_url 直读原字段（这条不在任何响应断言的必经路上，"
        "只有源码扫闸抓得到）",
        SHARE_PY, '            "share_url": settings.share_landing_url,',
        '            "share_url": settings.app_store_url,',
        "pytest", PY_GATE, "有消费方绕过了 share_landing_url",
    ),
    (
        "G1",
        "把占位商店链填回 Go 默认值：两侧又各自发出一条 404",
        CONFIG_GO, '\t\tAppStoreURL:            envString("APP_STORE_URL", ""),',
        f'\t\tAppStoreURL:            envString("APP_STORE_URL", "{PLACEHOLDER}"),',
        "go", GO_CONFIG_PKG, "APP_STORE_URL 默认",
    ),
    (
        "G2",
        "Go 的回落优先级写反（站点优先）：与 Python 反向，同一个账号两侧拿到不同深链",
        CONFIG_GO,
        '\tif c.AppStoreURL != "" {\n\t\treturn c.AppStoreURL\n\t}\n\treturn c.SiteURL',
        '\tif c.SiteURL != "" {\n\t\treturn c.SiteURL\n\t}\n\treturn c.AppStoreURL',
        "go", GO_CONFIG_PKG, "配了 APP_STORE_URL 却仍回落到站点",
    ),
    (
        "G3",
        "POST first-read 的响应侧 share_url 直读原字段：未配置时响应里是空链",
        SUBMIT_GO,
        '\tshareURL := s.cfg.ShareLandingURL()\n\tif resolved.User.Found {',
        '\tshareURL := s.cfg.AppStoreURL\n\tif resolved.User.Found {',
        "go", GO_API_PKG, "share_url 没回落站点根",
    ),
    (
        "G4",
        "first-read 的**列文本**侧 share_url 直读原字段（restore 按列复现，这半壁最容易漏）",
        SUBMIT_GO,
        '\tshareURL := s.cfg.ShareLandingURL()\n\tif in.User.Found {',
        '\tshareURL := s.cfg.AppStoreURL\n\tif in.User.Found {',
        "go", GO_API_PKG, "列文本的 share_url 没跟着回落",
    ),
    (
        "G5",
        "撤掉夹具守卫的更正，把「AppStoreURL 非空」又当成必填：合法的空串会被 t.Fatalf 打死",
        CONTRACT_GO,
        '\t\t"share_url_inputs.site_url": file.ShareURLInputs.SiteURL == "",',
        '\t\t"share_url_inputs.site_url": file.ShareURLInputs.SiteURL == "" ||\n'
        '\t\t\tfile.ShareURLInputs.AppStoreURL == "",',
        "go", GO_API_PKG, "本节测会在空跑",
    ),
]

# 破坏「不该打红」的东西＝证明这些闸不是假红制造机。
GREEN = [
    (
        "GB1",
        "往 build_first_read 的 share_url 前插 15 行注释：闸判的是发出的值，"
        "不是源码行数",
        FIRST_READ_PY, '            "share_url": settings.share_landing_url,',
        "".join(f"            # 增长占位 {i}\n" for i in range(15))
        + '            "share_url": settings.share_landing_url,',
        "pytest", PY_GATE,
    ),
    (
        "GG1",
        "往 Go 的 ShareLandingURL 上方插 15 行注释：两侧同形闸切的是函数体，"
        "注释变多不该把它变红",
        CONFIG_GO, "func (c Config) ShareLandingURL() string {",
        "".join(f"// 增长占位 {i}\n" for i in range(15))
        + "func (c Config) ShareLandingURL() string {",
        "go", GO_CONFIG_PKG,
    ),
]

GOROOT = "/Users/fengyu/sdk/go1.27.1"
GO_ENV = {**os.environ, "GOROOT": GOROOT, "PATH": f"{GOROOT}/bin:" + os.environ["PATH"]}


def run(kind: str, target: str) -> tuple[int, str]:
    if kind == "go":
        proc = subprocess.run(
            ["go", "test", target, "-count=1"], cwd=GO, env=GO_ENV,
            capture_output=True, text=True, timeout=900,
        )
    else:
        proc = subprocess.run(
            [str(REPO / ".venv/bin/python"), "-m", "pytest", target, "-q", "--no-header"],
            cwd=REPO, capture_output=True, text=True, timeout=900,
        )
    return proc.returncode, proc.stdout + proc.stderr


def patch(path: Path, old: str, new: str) -> None:
    src = path.read_text(encoding="utf-8")
    count = src.count(old)
    assert count == 1, f"{path.name}：锚点命中 {count} 次（要求恰好 1 次）\n{old[:80]!r}"
    path.write_text(src.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()

    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    if args.check:
        bad = 0
        for case in [*MUTATIONS, *GREEN]:
            path, old = case[2], case[3]
            n = path.read_text(encoding="utf-8").count(old)
            if n != 1:
                print(f"坏锚点 {case[0]}：{path.name} 命中 {n} 次")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}"
              f"（共 {len(MUTATIONS) + len(GREEN)} 条）")
        return 1 if bad else 0

    tmp = Path(tempfile.mkdtemp(prefix="u3b-gate-backup-"))
    originals: dict[Path, Path] = {}
    for case in [*MUTATIONS, *GREEN]:
        p = case[2]
        if p not in originals:
            bak = tmp / p.name
            shutil.copy2(p, bak)
            originals[p] = bak

    def restore() -> None:
        for p, bak in originals.items():
            shutil.copy2(bak, p)
            assert filecmp.cmp(bak, p, shallow=False), f"{p} 恢复后字节不一致"

    red_ok = red_fail = green_ok = green_fail = 0
    failures: list[str] = []
    try:
        for cid, desc, path, old, new, kind, test, marker in MUTATIONS:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                patch(path, old, new)
                code, out = run(kind, test)
                # pytest 的「红」要带计数（防「收集失败」冒充「断言失败」）；
                # go test 的红只有 `FAIL\tpkg` 这一种落款，没有计数，用同一种判据会误报。
                counted = (re.search(r"\d+ (failed|error)", out) if kind == "pytest"
                           else re.search(r"^FAIL\t", out, re.M))
                hit = marker in out and counted
                if code != 0 and hit:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（exit={code}，marker={bool(hit)}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-10:]))
            finally:
                restore()
        for cid, desc, path, old, new, kind, test in GREEN:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                patch(path, old, new)
                code, out = run(kind, test)
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 应保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 应保持绿 ✗（exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-12:]))
            finally:
                restore()
    finally:
        restore()
        shutil.rmtree(tmp, ignore_errors=True)

    total = red_ok + red_fail + green_ok + green_fail
    print(
        f"\n变异自证合计：{total} 条｜红闸 {red_ok}/{red_ok + red_fail}"
        f"｜绿闸 {green_ok}/{green_ok + green_fail}"
    )
    if failures:
        print(f"未如预期：{', '.join(failures)}")
    print("源文件已全部按备份写回并逐字节校验一致。")
    return 0 if red_fail == 0 and green_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
