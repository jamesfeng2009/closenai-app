#!/usr/bin/env python3
"""金句卡（KG-30）闸门的变异自证（2026-11-01，DoD ②）。

守的是这一批里**只会静默失效或静默说谎**的落点：

- 后端（backend/tests/test_quote_card.py）：请求体塞不进正文、AI 抽取的记忆走 40405
  而不是 40403、归属与类型同进 WHERE、草稿来源要改口径、超长由服务端裁、文案键命名。
- 前端接线（__tests__/quoteCardWiring.test.ts）：预览不发请求、导出是唯一授权点、
  入口两处参数齐全、空正文不给按钮、清单与实际卡面同源、15 语言的披露与 40405 文案。
- 跨仓镜像（__tests__/pdfExportWiring.test.ts、__tests__/funnel.test.ts）：40405 →
  自己的文案键、280 上限与 §七 登记行、埋点来源在册。

每条都要能被一次真实破坏打红才算数：写坏 → 只跑受害的那份测试 → 要求输出里出现那条
失败用例／判据的名字 → 备份写回。**恢复用 copy2 + filecmp，不用 git checkout**
（见 [[feedback-mutation-restore-no-git-checkout]]）。严禁并发跑（见
[[feedback-mutation-gates-no-concurrency]]）：本脚本一次只改一个文件、跑一份测试。

G1/G2 是反向绿闸：往被锚点切片守卫的窗口里插注释（正是 317b390 造成假红的那种增长），
守卫不该因此变红——证明这些结构断言不是脆的。

B8 打的是本批真实踩到过的那一脚：文案键写成 "share.quote.source_memory"（双命名空间），
卡上会印出裸键名、日期整段消失。

用法：
    python3 scripts/mutate_quote_card_gates.py --check   # 只验锚点，各命中一次
    python3 scripts/mutate_quote_card_gates.py           # 全部红 + 2 条绿
    python3 scripts/mutate_quote_card_gates.py --only B8,Q4
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FRONT = REPO / "frontend"
SRC = FRONT / "src"

TYPES = SRC / "types" / "index.ts"
SHARE_SCREEN = SRC / "screens" / "ShareCardScreen.tsx"
MEMORIES_SCREEN = SRC / "screens" / "MemoriesScreen.tsx"
EXPLORE_SCREEN = SRC / "screens" / "ExploreScreen.tsx"
API = SRC / "services" / "api.ts"
FUNNEL = SRC / "services" / "funnel.ts"
PDF_CFG = SRC / "config" / "pdfFiles.ts"
LOCALES = SRC / "i18n" / "locales"

BACKEND_SVC = REPO / "backend" / "services" / "share_service.py"
BACKEND_ROUTER = REPO / "backend" / "routers" / "share.py"
GROWTH_REPO = REPO / "backend" / "repositories" / "growth_repository.py"
SCHEMAS = REPO / "backend" / "models" / "schemas.py"

TS_QUOTE = "__tests__/quoteCardWiring.test.ts"
TS_PDF = "__tests__/pdfExportWiring.test.ts"
TS_FUNNEL = "__tests__/funnel.test.ts"
PY_QUOTE = "backend/tests/test_quote_card.py"

# 金句卡里三行 WHERE 的整块（B3／B4 各拆掉其中一行，两块锚点都只命中一次）
WHERE_BLOCK = (
    "                GrowthRecord.id == record_id,\n"
    "                GrowthRecord.user_id == user_id,\n"
    "                GrowthRecord.record_type == record_type,\n"
)

# ------------------------------------------------------------------ 红闸：后端判据
MUTATIONS: list[tuple[str, str, Path, str, str, str, str]] = [
    (
        "B1",
        "去掉「AI 抽取的记忆不出卡」这道门：机器整理的话被印成「你说过的话」，"
        "而且不再走 40405（前端就念不出真相）",
        BACKEND_SVC,
        "            if memory.generation_run_id:",
        "            if False:",
        PY_QUOTE,
        "test_ai_extracted_memory_is_rejected_with_its_own_code",
    ),
    (
        "B2",
        "软删除／被取代的记忆也出卡（越权之外的第二个漏口：已经收回的话又印出去了）",
        BACKEND_SVC,
        '            if memory is None or memory.status != "current":',
        "            if memory is None:",
        PY_QUOTE,
        "test_superseded_memory_is_40403",
    ),
    (
        "B3",
        "get_owned 的 WHERE 里去掉 record_type：daily_insight 的 id 也能当反思出卡"
        "（AI 产出的行和 reflection 住同一张表）",
        GROWTH_REPO,
        WHERE_BLOCK,
        "                GrowthRecord.id == record_id,\n"
        "                GrowthRecord.user_id == user_id,\n",
        PY_QUOTE,
        "test_daily_insight_id_is_not_a_reflection",
    ),
    (
        "B4",
        "get_owned 的 WHERE 里去掉 user_id：别人的反思 id 撞上就出一张带本人推荐码的卡",
        GROWTH_REPO,
        WHERE_BLOCK,
        "                GrowthRecord.id == record_id,\n"
        "                GrowthRecord.record_type == record_type,\n",
        PY_QUOTE,
        "test_other_users_reflection_and_an_unknown_id_answer_the_same",
    ),
    (
        "B5",
        "超长不再由服务端裁：三千字灌进卡里，前端预览却仍按 280 裁（两头两样）",
        BACKEND_SVC,
        '        shown = text if len(text) <= self.QUOTE_MAX_CHARS '
        'else text[: self.QUOTE_MAX_CHARS - 1].rstrip() + "…"',
        "        shown = text",
        PY_QUOTE,
        "test_over_long_line_is_truncated_by_the_server",
    ),
    (
        "B6",
        "草稿来源不再改口径（AI 草稿保存的那句也被印成「你自己写的原话」）",
        BACKEND_SVC,
        '            draft = _reflection_metadata_source(record) in ("draft", "mood_analysis")',
        "            draft = False",
        PY_QUOTE,
        "test_ai_draft_reflection_keeps_the_card_but_changes_the_wording",
    ),
    (
        "B7",
        "落盘文件名改掉一个词（§七 与前端常量逐字比对的口径失守）",
        BACKEND_ROUTER,
        '\'attachment; filename="closenai_quote_card.pdf"\'',
        '\'attachment; filename="closenai_quote.pdf"\'',
        PY_QUOTE,
        "test_request_body_cannot_put_words_on_the_card",
    ),
    (
        "B8",
        "文案键写成双命名空间 share.quote.source_memory（本批真踩到过的一脚："
        "卡上印裸键名、日期整段消失）",
        BACKEND_SVC,
        '            source_key = "quote.source_memory"',
        '            source_key = "share.quote.source_memory"',
        PY_QUOTE,
        "test_own_memory_makes_a_card_with_the_original_line",
    ),
    (
        "B9",
        "请求体偷偷加一个 source_text 字段：端点退化成任意文本→带本人短链的 PDF 生成器",
        SCHEMAS,
        '    source_id: str = Field(..., max_length=36, description="来源记录 id")',
        '    source_id: str = Field(..., max_length=36, description="来源记录 id")\n'
        '    source_text: str = Field("", description="变异：把正文交给调用方")',
        PY_QUOTE,
        "test_schema_has_no_room_for_the_text",
    ),
]

# ------------------------------------------------------------------ 红闸：前端接线
MUTATIONS += [
    (
        "Q1",
        "类型表去掉 'quote'：金句卡只在运行时存在，编译面不认识它",
        TYPES,
        "export type ShareCardVariant = 'classic' | 'minimal' | 'combo' | 'memory' | 'quote';",
        "export type ShareCardVariant = 'classic' | 'minimal' | 'combo' | 'memory';",
        TS_QUOTE,
        "ShareCardVariant 含 'quote'",
    ),
    (
        "Q2",
        "前端来源类型多加一个 'chat'：与后端 Literal 集合不再同（发出去必然 40001）",
        TYPES,
        "export type QuoteSourceType = 'memory' | 'reflection';",
        "export type QuoteSourceType = 'memory' | 'reflection' | 'chat';",
        TS_QUOTE,
        "QuoteSourceType 只有 memory / reflection",
    ),
    (
        "Q3",
        "金句分支不再在任何 await 之前 return：进卡页就取名片/考古数据＝预览即发请求",
        SHARE_SCREEN,
        "        if (variant === 'quote') return;\n",
        "",
        TS_QUOTE,
        "取数 effect 里金句分支在任何 await 之前 return",
    ),
    (
        "Q4",
        "导出时把本地那句一起 POST 上去（授权边界从服务端挪到客户端）",
        API,
        "    body: JSON.stringify({ source_type: sourceType, source_id: sourceId }),",
        "    body: JSON.stringify({ source_type: sourceType, source_id: sourceId, "
        "source_text: quoteText }),",
        TS_QUOTE,
        "api.ts 的 body 只有 source_type / source_id 两个键",
    ),
    (
        "Q5",
        "去掉 40405 → 自己的文案键：那句 AI 整理的话被念成「记忆已经不在了」",
        PDF_CFG,
        "  if (code === CODE_QUOTE_NOT_ORIGINAL) return 'pdf.failed.notOriginal';\n",
        "",
        TS_PDF,
        "四个码各映射到自己的文案键",
    ),
    (
        "Q6",
        "本地正文上限改成 120（与后端 280 不再同值：预览一句、卡上另一句）",
        PDF_CFG,
        "export const QUOTE_CARD_MAX_CHARS = 280;",
        "export const QUOTE_CARD_MAX_CHARS = 120;",
        TS_PDF,
        "金句卡正文上限与后端同值",
    ),
    (
        "Q7",
        "漏斗来源去掉 'quote'：金句卡的两次埋点落在白名单外，静默丢事件",
        FUNNEL,
        "  'quote',\n",
        "",
        TS_FUNNEL,
        "九段台阶 + 两个分享环路事件都在枚举里",
    ),
    (
        "Q8",
        "清单虚报第三项（多列那一项并不会出现在卡上＝对外虚报分享面）",
        SHARE_SCREEN,
        "<ShareManifest items={[t('share.quoteManifestQuote'), t('share.manifestLink')]} />",
        "<ShareManifest items={[t('share.quoteManifestQuote'), t('share.manifestLink'), "
        "t('share.quoteEmpty')]} />",
        TS_QUOTE,
        "清单两项＝那句原话 + 深链",
    ),
    (
        "Q9",
        "删掉导出前那句「按导出才分享、卡上带推荐链接」的披露",
        SHARE_SCREEN,
        "            <Text style={styles.quoteNote}>{t('share.quoteNote')}</Text>\n",
        "",
        TS_QUOTE,
        "导出前那一句",
    ),
    (
        "Q10",
        "记忆详情入口不带 quote_text：卡页拿不到正文，永远走空态",
        MEMORIES_SCREEN,
        "      quote_text: item.content,\n",
        "",
        TS_QUOTE,
        "记忆详情→金句卡",
    ),
    (
        "Q11",
        "空正文那条也给按钮（点下去只拿到一句「这句是空的」）",
        EXPLORE_SCREEN,
        "{!!r.summary?.trim() && (",
        "{true && (",
        TS_QUOTE,
        "空正文那条不给按钮",
    ),
    (
        "Q12",
        "删掉越南语的 share.quoteNote（15 语言齐备闸的负向对账）",
        LOCALES / "vi.ts",
        '    "quoteNote": "Chưa gì được chia sẻ cho tới khi bạn bấm xuất. '
        'Thẻ kèm theo liên kết giới thiệu của bạn, và thẻ đã lưu trên máy thì '
        'không thu hồi lại được.",\n',
        "",
        TS_QUOTE,
        "share.quoteNote：15 语言都有且非空",
    ),
    (
        "Q13",
        "高棉语的 40405 文案退回成「记忆已经不在了」那句（今日实测抓到过的缺陷）："
        "两个码念同一句话＝这条码白分",
        LOCALES / "km.ts",
        "ប្រយោកនេះ AI បានរៀបចំពីការសន្ទនារបស់អ្នក "
        "ដូច្នេះវាមិនត្រូវបានដាក់លើកាតដែលចែងថាអ្នកជាអ្នកសរសេរឡើយ។",
        "ការចងចាំនេះលែងមានទៀតហើយ ដូច្នេះមិនអាចនាំចេញកាតបានទេ។",
        TS_QUOTE,
        "40405 的文案 × 15",
    ),
]

# ------------------------------------------------------------------ 绿闸
GREEN: list[tuple[str, str, Path, str, str, str]] = [
    (
        "G1",
        "往金句卡 JSX 分支（卡面与清单之间）插 12 行注释：锚点切片守卫不该因此变红",
        SHARE_SCREEN,
        "            <ShareManifest items={[t('share.quoteManifestQuote')",
        "              {/* 增长占位 */}\n" * 12
        + "            <ShareManifest items={[t('share.quoteManifestQuote')",
        TS_QUOTE,
    ),
    (
        "G2",
        "往 export_quote_card_pdf 的 docstring 里插 8 行（同类增长）：取数与键集闸不该变红",
        BACKEND_SVC,
        "        不能靠这条端点探测谁有什么。\n",
        "        不能靠这条端点探测谁有什么。\n"
        + "".join(f"        增长占位 {i}\n" for i in range(8)),
        PY_QUOTE,
    ),
]


def run_test(target: str) -> tuple[int, str]:
    if target.endswith(".py"):
        proc = subprocess.run(
            [str(REPO / ".venv" / "bin" / "pytest"), target, "-q", "--no-header"],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
    else:
        proc = subprocess.run(
            ["npx", "jest", "--silent", target], cwd=FRONT, capture_output=True, text=True
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

    def pick(items):
        return [c for c in items if not wanted or c[0].upper() in wanted]

    if args.check:
        bad = 0
        for case in [*MUTATIONS, *GREEN]:
            path, old = case[2], case[3]
            n = path.read_text(encoding="utf-8").count(old)
            if n != 1:
                print(f"坏锚点 {case[0]}：{path.name} 命中 {n} 次")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}")
        return 1 if bad else 0

    tmp = Path(tempfile.mkdtemp(prefix="mut-quote-card-"))
    originals: dict[Path, Path] = {}
    for case in [*pick(MUTATIONS), *pick(GREEN)]:
        p = case[2]
        if p not in originals:
            bak = tmp / f"{len(originals)}-{p.name}"
            shutil.copy2(p, bak)
            originals[p] = bak

    def restore() -> None:
        for p, bak in originals.items():
            shutil.copy2(bak, p)
            assert filecmp.cmp(bak, p, shallow=False), f"{p} 恢复后字节不一致"

    red_ok = red_fail = green_ok = green_fail = 0
    try:
        for cid, desc, path, old, new, test, marker in pick(MUTATIONS):
            try:
                patch(path, old, new)
                code, out = run_test(test)
                hit = (marker in out) and (" failed" in out or "Failed" in out)
                if code != 0 and hit:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    print(f"[{cid}] 打红 ✗（exit={code}，marker={bool(hit)}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-8:]))
            finally:
                restore()
        for cid, desc, path, old, new, test in pick(GREEN):
            try:
                patch(path, old, new)
                code, out = run_test(test)
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 应保持绿 ✓  {desc}")
                else:
                    green_fail += 1
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
    print("源文件已全部按备份写回并逐字节校验一致。")
    return 0 if red_fail == 0 and green_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
