#!/usr/bin/env python3
"""批1 G3（隐私政策/条款两端断链）的变异自证（2026-10-17）。

站点没有 JS 测试运行器，闸按 test_k8s_manifests.py 的先例落在
`backend/tests/test_legal_pages.py`，所以本脚本的靶子就是「把那两类会重新把链接
断开的回归手工做一遍」：

- A1 页脚隐私按钮退回 `href="#"`＝立项时的原始故障形态（看起来能点，点了没反应）；
- A2 App 侧把隐私路径改成站点上没有的页面＝404 从「站点缺页」换成「App 指错页」；
- A3 无扩展名重写从 200 改成 302 到自身＝提审点开政策时的那一跳没了；
- A4 页面把 canonical 路径写成另一篇的路径＝分享/搜索里两条法务页互相顶掉；
- A5 把占位商店链填回站点常量默认值＝D-G3-3 明令禁止的那条（发一条 404 给用户）；
- A6 分享落地页 CTA 指回商店直链＝分享文案里带着一条不存在的商店页；
- A7 中文隐私政策把某一家处理方改成不点名的说法＝代码里它在收数据，页面上查无此家。
     这条刻意**不删行**：删行会先被中英条数闸拦住（现场验过），那样「漏一家处理方」
     这个判据到底有没有效力就永远拿不到证据；换成同长度的不点名说法，红的才是这一条。
- A8 把「不是端到端加密」润色成「已端到端加密」＝本文件头注释里写的那类最贵回归；
- A9 下载区不再看 storeReady＝未上架也渲染按钮，按的是一个空 href；
- A10 删掉 en 的 policyLink 文案＝摘要卡片的「阅读完整隐私政策」变成空白。

应保持绿（证明这条闸不是假红制造机）：
- B1 中文那条跨境说明换一句中文：判据管「采集事实有没有」，不管措辞；
- B2 legal.ts 头注释插 15 行：闸钉的是正文的值，不是源码行数；
- B3 在 Legal.astro 的注释里写一句「不许再留 href="#"」：注释豁免按块生效，
      说明这段历史的权利要保留（与 test_share_landing_fallback.py 同一口径）。

恢复一律 **copy2 + filecmp 逐字节校验**，不用 git checkout
（见 [[feedback-mutation-restore-no-git-checkout]]）；**严禁并发跑**
（见 [[feedback-mutation-gates-no-concurrency]]）。

用法：
    ./.venv/bin/python scripts/mutate_g3_legal_gates.py --check
    ./.venv/bin/python scripts/mutate_g3_legal_gates.py
    ./.venv/bin/python scripts/mutate_g3_legal_gates.py --only A1,B1
"""
from __future__ import annotations

import argparse
import filecmp
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SITE = REPO / "site"
SRC = SITE / "src"
PUBLIC = SITE / "public"

LANDING = SRC / "components" / "Landing.astro"
LEGAL_ASTRO = SRC / "components" / "Legal.astro"
LEGAL_TS = SRC / "legal.ts"
SITE_LINKS = SRC / "config" / "links.ts"
APP_LINKS = REPO / "frontend" / "src" / "config" / "links.ts"
PRIVACY_PAGE = SRC / "pages" / "privacy.astro"
R_LANDING = PUBLIC / "r" / "index.html"
REDIRECTS = PUBLIC / "_redirects"

GATE = "backend/tests/test_legal_pages.py"

ZH_CROSSBORDER = (
    "          '我们的服务商分布在不同国家，所以你的数据可能在你居住国之外被处理"
    "——分析平台在欧盟，而数据库与模型服务可能位于别处。具体清单取决于部署配置，"
    "我们会让这一页与生产环境实际使用的清单保持一致。',\n"
)
ZH_HEADER_LINE = " *   危机转介摘录          backend/models/journey.py:129-160（kind/matched_terms/hotline/\n"
LEGAL_ASTRO_COMMENT = " * 页脚与下载区都不许再留 `href=\"#\"`（闸：backend/tests/test_legal_pages.py）。\n"

# (id, 破坏说明, 文件, 原文, 坏文, 受害测试名片段)
MUTATIONS = [
    (
        "A1",
        "页脚隐私按钮退回 href=\"#\"：两条常驻法务入口又变成点了没反应的样子",
        LANDING,
        '      <a href={privacyPath(lang)}>{t.foot.privacy}</a>',
        '      <a href="#">{t.foot.privacy}</a>',
        "test_no_dead_hash_links",
    ),
    (
        "A2",
        "App 把隐私政策指到 /privacy-policy：站点没有那一页，404 换了个成因回来",
        APP_LINKS,
        "export const PRIVACY_POLICY_URL = 'https://closenai.app/privacy';",
        "export const PRIVACY_POLICY_URL = 'https://closenai.app/privacy-policy';",
        "test_app_constants_are_what_the_pages_claim",
    ),
    (
        "A3",
        "/privacy 的无扩展名重写改成 302 到自身：App 点开政策的那一跳没了",
        REDIRECTS,
        "/privacy      /privacy/index.html  200",
        "/privacy      /privacy  302",
        "test_extensionless_legal_paths_are_rewritten_to_the_pages",
    ),
    (
        "A4",
        "隐私页把 canonical 写成 /terms：两条法务页在搜索与分享里互相顶掉",
        PRIVACY_PAGE,
        '<Base lang="en" path="/privacy"',
        '<Base lang="en" path="/terms"',
        "test_app_legal_url_has_a_site_page",
    ),
    (
        "A5",
        "把占位商店链填回站点常量默认值（D-G3-3 明令禁止：分享出去就是一条 404）",
        SITE_LINKS,
        "export const APP_STORE_URL = readEnv('PUBLIC_APP_STORE_URL');",
        "export const APP_STORE_URL = readEnv('PUBLIC_APP_STORE_URL') || "
        "'https://apps.apple.com/app/closenai/id6740000000';",
        "test_app_store_placeholder_is_not_in_any_served_web_source",
    ),
    (
        "A6",
        "分享落地页 CTA 指回商店直链：未上架时这条链接点开的是一条不存在的商店页",
        R_LANDING,
        '<a class="cta" id="download" href="https://closenai.app/" rel="noopener">ClosenAI</a>',
        '<a class="cta" id="download" href="https://apps.apple.com/app/closenai" rel="noopener">App Store</a>',
        "test_share_landing_falls_back_to_the_site_not_a_fake_store_link",
    ),
    (
        "A7",
        "中文隐私政策把 Resend 写成「邮件服务」：代码里是这家在发验证码，页面上却查无此家",
        LEGAL_TS,
        "          'Resend——登录与重置密码的验证邮件。',",
        "          '邮件服务——只发登录与重置密码的验证码。',",
        "test_privacy_page_names_every_processor_and_switch",
    ),
    (
        "A8",
        "把「不是端到端加密」润色成「已端到端加密」：一句提审时会被核对的假承诺",
        LEGAL_TS,
        "受访问控制保护，但不是端到端加密。",
        "受访问控制保护，并且已做端到端加密。",
        "test_security_section_does_not_claim_end_to_end_encryption",
    ),
    (
        "A9",
        "下载区不再看 storeReady：未上架时照样渲染按钮，按下去是一条空 href",
        LANDING,
        "const storeReady = APP_STORE_URL !== '';",
        "const storeReady = true;",
        "test_download_area_uses_the_shared_constant_and_has_an_unreleased_state",
    ),
    (
        "A10",
        "删掉 en 的 policyLink 文案：摘要卡片那条「阅读完整隐私政策」变成空白",
        SRC / "i18n.ts",
        "      policyLink: 'Read the full privacy policy',\n",
        "",
        "test_privacy_section_links_to_the_full_policy",
    ),
]

GREEN = [
    (
        "B1",
        "中文那条跨境说明换一句中文：判据管「采集事实有没有」，不管措辞",
        LEGAL_TS, ZH_CROSSBORDER,
        "          '处理发生在你居住国以外的地方：分析平台在欧盟，数据库与模型服务按部署配置可能在别处。"
        "这一页会跟着生产环境实际用到的服务商清单一起更新。',\n",
    ),
    (
        "B2",
        "legal.ts 头注释插 15 行：闸钉的是正文里的值，不是源码行数",
        LEGAL_TS, ZH_HEADER_LINE,
        ZH_HEADER_LINE + "".join(f" *   占位说明 {i}\n" for i in range(15)),
    ),
    (
        "B3",
        "在 Legal.astro 的注释里再写一次「不许留 href=\"#\"」：说明这段历史要留得住",
        LEGAL_ASTRO, LEGAL_ASTRO_COMMENT,
        LEGAL_ASTRO_COMMENT + ' * 立项时页脚两条按钮就是 href="#"，这句是写给后来人的，不是给用户的链接。\n',
    ),
]


def pytest_gate() -> tuple[int, str]:
    proc = subprocess.run(
        ["./.venv/bin/python", "-m", "pytest", GATE, "-q"],
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
    cases = [*MUTATIONS, *GREEN]

    if args.check:
        bad = 0
        for case in cases:
            path, old = case[2], case[3]
            n = path.read_text(encoding="utf-8").count(old)
            if n != 1:
                print(f"坏锚点 {case[0]}：{path.name} 命中 {n} 次")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}（共 {len(cases)} 条）")
        return 1 if bad else 0

    tmp = Path(tempfile.mkdtemp(prefix="g3-legal-gate-backup-"))
    originals: dict[Path, Path] = {}
    for case in cases:
        p = case[2]
        if p not in originals:
            # 备份名必须整条相对路径唯一：本批同时改 site/src/config/links.ts 与
            # frontend/src/config/links.ts，只取 parent.name+name 会让两者撞成同一个
            # 备份文件，恢复时把站点那份写回 App 那份（现场踩过，见下面的注释）。
            bak = tmp / p.relative_to(REPO).as_posix().replace("/", "__")
            bak.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, bak)
            originals[p] = bak
    assert len({b.name for b in originals.values()}) == len(originals), "备份文件名撞了，恢复会串源"

    def restore() -> None:
        for p, bak in originals.items():
            shutil.copy2(bak, p)
            assert filecmp.cmp(bak, p, shallow=False), f"{p} 恢复后字节不一致"

    red_ok = red_fail = green_ok = green_fail = 0
    failures: list[str] = []
    try:
        for cid, desc, path, old, new, marker in MUTATIONS:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                patch(path, old, new)
                code, out = pytest_gate()
                hit = marker in out and re.search(r"\d+ (failed|error)", out)
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
        for cid, desc, path, old, new in GREEN:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                patch(path, old, new)
                code, out = pytest_gate()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 应保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 应保持绿 ✗（exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-14:]))
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
