#!/usr/bin/env python3
"""KG-04 的 ASO 文案包校验器：读 `docs/aso/en-US-2026-11.json`，把「靠手数」的那几条判据变成脚本。

**为什么要有脚本**：台账 §KG-04 的验收原文写的是「字符数用脚本或 ASC 预览复核，不靠手数」
「关键词字段只放搜索词，不重复标题/副标题已有词，不用竞品品牌词」。
这三条都是能机械判的，留在人眼里就会漂；而且商店文案一旦贴进 ASC 就成了对外承诺，
比 App 内文案更没有「回头再改」的余地。

**为什么判据不只看长度**：长度超了 ASC 会直接拦，真正没人拦的是另外三类——
① 关键词字段里重复标题已有词（白占字符、不增加索引覆盖）；
② 关键词字段出现竞品品牌词（KG-05 规则：只允许出现在对比页）；
③ 医疗/绝对化/最高级用词（Apple 指南 2.3 与本项目 KG-01 的口径）。
这三类能安静地通过 ASC 的字符计数。

用法：
    python scripts/check_aso_copy.py              # 校验仓内文案包
    python scripts/check_aso_copy.py --self-test   # 证明这条闸会红（超长标题必须被拒）

`--self-test` 存在是因为「反向归零闸要配正向证人」这条老规矩：
只跑一次「全绿」不能证明判据在场，所以要拿一条**必然不合格**的输入证明它会红。
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "docs" / "aso" / "en-US-2026-11.json"

#: App Store Connect 的字段上限。**这里的字面量就是判据的「另一侧」**：
#: JSON 里的 `limits` 只是给文案包作者看的注记，脚本拿它跟本表对，两边不一致就红。
ASC_LIMITS = {"title": 30, "subtitle": 30, "keywords": 100}

#: 禁语的**正则**口径。刻意写词边界＋词干，而不是裸子串：
#: `best` 不该打到 `better`，`treat` 该打到 `treatment` 但不该打到 `retreat`，
#: `#1` 的 `#` 要转义。`diagnos\w*` 同覆盖 diagnose/diagnosis/diagnostic。
BANNED_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("therapy", re.compile(r"\btherap\w*", re.I)),
    ("treat", re.compile(r"\btreat\w*", re.I)),
    ("diagnose", re.compile(r"\bdiagnos\w*", re.I)),
    ("remembers everything", re.compile(r"remembers\s+(everything|all\b|every\s+word)", re.I)),
    ("AI companion", re.compile(r"\bai\s+compani\w*", re.I)),
    ("AI friend", re.compile(r"\bai\s+friend\w*", re.I)),
    ("best", re.compile(r"\bbest\b", re.I)),
    ("#1", re.compile(r"#\s*1\b")),
]

PLACEHOLDERS = {"待补", "待定", "暂无", "tbd", "todo", "—", "-", ""}

#: 判停句里必须出现**可测量的东西**（时间窗或指标名）。
#: 只查「不是空串／不是待补」是不够的：「看情况再说，不行就换回去」既通顺又没人能判——
#: 这条是被变异 F7 打出来的（首轮 exit=0）。
METRIC_TOKENS = re.compile(r"两周|一周|7 日|8 周|4 周|四周|转化|安装|曝光|激活|留存|读数|点击")


def _norm_words(text: str) -> set[str]:
    """把一句文案拆成可比较的词（小写、去标点、去数字）。

    `ClosenAI: AI Journal & Growth` → {closenai, ai, journal, growth}；
    关键词项 `self improvement` 会拆成 {self, improvement}——ASO 的索引覆盖按词算，不按短语算，
    所以「标题已有 growth，关键词里就别再放 growth」。
    """
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if not w.isdigit()}


def _variant_texts(payload: dict, key: str) -> list[tuple[str, str]]:
    variants = payload["fields"].get(key, [])
    return [(v.get("id", "?"), v.get("text", "")) for v in variants]


def violations(payload: dict) -> list[str]:
    """返回所有不合格项；空列表＝文案包可以贴进 ASC（法务签字另说）。"""
    out: list[str] = []
    limits = payload.get("limits", {})
    fields = payload.get("fields", {})
    title = fields.get("title", "")

    # 1) 长度：把 ASC 的上限写成**本脚本里的字面量**，而不是回读 JSON 的 `limits`。
    #    断言两侧不许同源——如果上限从 JSON 取，改 JSON 就能把判据放宽到任意值。
    for asc_key, cap in ASC_LIMITS.items():
        declared = limits.get(asc_key)
        if declared != cap:
            out.append(f"limits.{asc_key} 与 ASC 真实上限 {cap} 不符（文案包写的是 {declared!r}）")

    def _check_len(label: str, text: str, cap: int) -> None:
        if len(text) > cap:
            out.append(f"{label} 超长度：{len(text)}/{cap} ⇒ {text!r}")

    _check_len("title", title, ASC_LIMITS["title"])

    subtitled = _variant_texts(payload, "subtitle_variants")
    if len(subtitled) < 2:
        out.append("副标题要有 A/B 两版（台账 §KG-04 验收点名），当前 " + str(len(subtitled)) + " 版")
    for vid, text in subtitled:
        _check_len(f"subtitle[{vid}]", text, ASC_LIMITS["subtitle"])

    keywords = _variant_texts(payload, "keyword_variants")
    if len(keywords) < 2:
        out.append("关键词字段要商标版＋保守版两版，当前 " + str(len(keywords)) + " 版")
    for kid, text in keywords:
        _check_len(f"keywords[{kid}]", text, ASC_LIMITS["keywords"])
        items = text.split(",")
        for it in items:
            if it != it.strip():
                out.append(f"keywords[{kid}] 的项 {it!r} 前后有空格（每个空格都吃字符数）")
            if it.strip() == "":
                out.append(f"keywords[{kid}] 有空项（多余的逗号）")
        words = [w.strip().lower() for w in items if w.strip()]
        dup = {w for w in words if words.count(w) > 1}
        if dup:
            out.append(f"keywords[{kid}] 内部重复用词：{sorted(dup)}")

    # 2) 关键词字段不重复标题/副标题已有词
    indexed = _norm_words(title)
    for _, text in subtitled:
        indexed |= _norm_words(text)
    for kid, text in keywords:
        overlap = sorted(_norm_words(text) & indexed)
        if overlap:
            out.append(f"keywords[{kid}] 重复了标题/副标题已收录的词：{overlap} ⇒ 白占字符，不增加覆盖")

    # 3) 竞品品牌词不进关键词字段（只允许出现在对比页，KG-05 规则）
    for brand in payload.get("competitor_brands", []):
        pat = re.compile(rf"\b{re.escape(brand)}\b", re.I)
        for kid, text in keywords:
            if pat.search(text):
                out.append(f"keywords[{kid}] 含竞品品牌词 {brand!r} ⇒ 按 KG-05 只能放对比页")

    # 4) 禁语（医疗边界／绝对化记忆／陪伴定位／无证据最高级）
    scan: list[tuple[str, str]] = [("title", title)]
    scan += [(f"subtitle[{v}]", t) for v, t in subtitled]
    scan += [(f"keywords[{v}]", t) for v, t in keywords]
    play = payload.get("play_store_short_description")
    if play:
        scan.append(("play_short_description", play.get("text", "")))
        if len(play.get("text", "")) > int(play.get("limit", 80)):
            out.append(f"play_short_description 超 {play.get('limit')} 字符")
    for label, text in scan:
        for term, pat in BANNED_PATTERNS:
            if pat.search(text):
                out.append(f"{label} 命中禁用词 {term!r} ⇒ {text!r}")

    # 5) 声明的禁用词清单必须与判据一一对应（防「清单写了但没判」）
    declared = {t.lower() for t in payload.get("banned_terms", [])}
    enforced = {term.lower() for term, _ in BANNED_PATTERNS}
    missing = sorted(declared - enforced)
    if missing:
        out.append(f"banned_terms 声明了但脚本没判：{missing}")
    extra = sorted(enforced - declared)
    if extra:
        out.append(f"脚本判了但文案包没声明：{extra}")

    # 6) A/B 假设与判停不许是占位符（否则「两周后读数」无人能判）
    def _judgable(label: str, fld: str, value: str) -> None:
        if value.lower() in PLACEHOLDERS or len(value) < 8:
            out.append(f"{label} 的 {fld} 不是可判定的内容：{value!r}")
        elif fld == "kill_metric" and not METRIC_TOKENS.search(value):
            out.append(f"{label} 的 kill_metric 不是可判定的内容（没有窗口或指标）：{value!r}")

    for variant in payload["fields"].get("subtitle_variants", []):
        vid = variant.get("id", "?")
        for fld in ("hypothesis", "kill_metric"):
            _judgable(f"subtitle[{vid}]", fld, str(variant.get(fld, "")).strip())
    for variant in payload["fields"].get("keyword_variants", []):
        kid = variant.get("id", "?")
        _judgable(f"keywords[{kid}]", "kill_metric", str(variant.get("kill_metric", "")).strip())

    return out


def mutated_for_self_test(payload: dict) -> dict:
    """造一条**必然不合格**的输入：标题写成 31 字符。

    选它是因为长度判据是 ASC 会硬拦的那条，最不该失效；
    而它又只碰长度，不掺杂别的判据，红得干净。
    """
    bad = copy.deepcopy(payload)
    bad["fields"]["title"] = "ClosenAI: AI Journal & Growthes"  # 31/30
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="KG-04 ASO 文案包字符与用词校验")
    ap.add_argument("--pack", type=Path, default=PACK, help="文案包 JSON 路径")
    ap.add_argument("--self-test", action="store_true", help="用超长标题证明这条闸会红")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    payload = json.loads(args.pack.read_text(encoding="utf-8"))

    if args.self_test:
        bad = mutated_for_self_test(payload)
        hits = violations(bad)
        title_hit = [h for h in hits if h.startswith("title 超长度")]
        ok = bool(title_hit)
        if args.json:
            print(json.dumps({"self_test": ok, "violations": hits}, ensure_ascii=False))
        else:
            print(f"[self-test] 31 字符标题被拒：{'✓ 会红' if ok else '✗ 闸失效'}")
            for h in hits:
                print(f"  - {h}")
        return 0 if ok else 1

    hits = violations(payload)
    if args.json:
        print(json.dumps({"ok": not hits, "violations": hits}, ensure_ascii=False))
    else:
        for label, text in [("title", payload["fields"]["title"])] + _variant_texts(
            payload, "subtitle_variants"
        ) + _variant_texts(payload, "keyword_variants"):
            print(f"{len(text):>3} 字符  {label}")
    if hits:
        print("不合格：", file=sys.stderr)
        for h in hits:
            print(f"  - {h}", file=sys.stderr)
        return 1
    print("文案包判据全绿（法务签字＝KG-02，不在本脚本判据内）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
