"""录制 Go 侧的两份「Python 生成物副本」，并给 `--check` 漂移闸（R2 · 2026-10-16）。

产出两份，都进 R2 的闸：

1. `go-core/internal/aiclient/templates/copy_msg_fallback.json`
   ＝ `backend/data/copy/{locale}.json` 里 `msg.fallback` 那一条数组的 15 语言副本。
   为什么 Go 要带一份：降级模板是 R2 的 D-8 落点里唯一「Python 已经不可达」的那条路径
   （`safety_service`/`message_generator` 都能就地兜底，只有「整个 AI 进程联系不上」时
   没人能兜），而 V3 `13:110-117` §8 逐条要求「user-facing product must degrade gracefully」。
   副本形态沿用本仓既有先例＝`internal/domain/templates/copy_mood.json`（内嵌 + 逐字节闸），
   不新造一套取文案的机制。

2. `go-core/testdata/sse_event_golden.json`
   ＝ `backend/routers/chat.py:39-42` 的 `_sse_event()` 对四类事件的**输出字节**夹具。
   期望值只从真函数取，不手打：手打过一遍 `ensure_ascii` 就会把中文写成裸 UTF-8，
   而现网字节是 `\u4f60\u597d`——那正是这条闸要拦的东西。

跑法：
    ./.venv/bin/python scripts/gen_go_ai_side.py            # 写
    ./.venv/bin/python scripts/gen_go_ai_side.py --check     # 只比不写（闸用这条）
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
COPY_DIR = ROOT / "backend" / "data" / "copy"
GO_TPL = ROOT / "go-core" / "internal" / "aiclient" / "templates" / "copy_msg_fallback.json"
GO_GOLDEN = ROOT / "go-core" / "testdata" / "sse_event_golden.json"

# 与 core/copy.py:26-28 同序：前缀匹配按这个顺序走，顺序本身是契约的一部分。
LOCALES = (
    "zh", "en", "es", "ko", "ja", "ru", "id", "ms", "th", "de", "fr", "it", "pt", "vi", "km",
)

# 15 语言逐个取 msg.fallback。sys.path 注入后直接 import 真函数，
# 这样「键改名」「从数组改成字符串」这类结构性变化会立刻在这里显形，而不是让副本悄悄留旧值。
sys.path.insert(0, str(ROOT))
from backend.core import copy as copy_lib  # noqa: E402
from backend.routers.chat import _sse_event  # noqa: E402


def corpus() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for locale in LOCALES:
        # 逐语言**只走 t_list 的第一级**：这里要的是「该语言那份原文」，
        # 而 t_list 的 en 回退会把缺译的语言伪装成 en，副本就再也看不出谁欠译（L 节那条欠账）。
        value = copy_lib._lookup(copy_lib._load(locale), ("msg", "fallback"))
        assert isinstance(value, list) and value, f"{locale} 的 msg.fallback 不是非空数组"
        assert all(isinstance(x, str) and x for x in value), f"{locale} 的 msg.fallback 含空项"
        out[locale] = list(value)
    # 条数必须一致：`_fallback_reply` 用 `len(user_input) % len(templates)` 轮转，
    # 某语言多一条／少一条就会让同一段中文在两侧落到不同模板上。
    counts = {len(v) for v in out.values()}
    assert len(counts) == 1, f"15 语言的 fallback 条数不一致：{counts}"
    return out


def sse_cases() -> list[dict[str, object]]:
    # 覆盖四类事件 + 三类「容易在另一种语言里写法不同」的载荷：
    #   中文（Python ensure_ascii=True 会转 \uXXXX）、引号/反斜杠、< > &（Go 默认会转义、Python 不会）。
    payloads: list[tuple[str, dict[str, object]]] = [
        ("token", {"content": "你好"}),
        ("token", {"content": "He said \"hi\" and left"}),
        ("token", {"content": "a\\b"}),
        ("token", {"content": "<script>&</script>"}),
        ("token", {"content": ""}),
        ("token", {"content": "line1\nline2\ttab"}),
        ("token", {"content": "emoji 😀 ok"}),
        # 三个「只有 \uXXXX 一种写法」的码点，逐个卡在 Go 与 CPython 的分歧边上：
        #   0x7f DEL 落在 ASCII 收尾（Python 转、Go 的 encoding/json 不转）、
        #   0x00 NUL（Go 裸字节会把 JSON 字符串截断给某些解析器看）、
        #   0x2028/0x2029 行分隔符（JS 里是换行，Python 转义、Go 默认原样输出）。
        ("token", {"content": "del \x7f nul \x00 nel \x85 end"}),
        ("token", {"content": "ls \u2028 ps \u2029 end"}),
        ("memory_context", {"memories": [
            {"id": "m1", "content": "我最近在改作息", "category": "habit"},
            {"id": "m2", "content": "<b>&x</b>", "category": "emotion"},
        ]}),
        # 刻意**没有** `{"memories": []}` 这一条：它不是「还没录」而是现网与传输层都产不出——
        # chat.py:103 的 `if memories:` 保证空列表不发事件，而 proto 的 repeated 字段没有
        # presence（空列表解出来就是长度 0，与「这块不是 memory 事件」不可区分）。
        # 留着它只会逼 Go 侧造一个契约里没有的判别位。判据见 sse.go 的 SSELine。
        ("done", {}),
        ("error", {"code": 40301, "message": "今日对话次数已用完"}),
        ("error", {"code": 500, "message": "生成失败，请稍后再试 / Generation failed, please try again later"}),
    ]
    return [{"event": event, "payload": payload, "line": _sse_event(event, payload)}
            for event, payload in payloads]


def render(data: object) -> str:
    # 两份生成物都用同一种可 diff 的形态：2 空格缩进、键保序、结尾一个换行。
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    check = "--check" in sys.argv[1:]
    bad = 0
    targets = [
        (GO_TPL, {"note": "gen_go_ai_side.py 生成，源＝backend/data/copy/*.json 的 msg.fallback",
                   "locales": list(LOCALES), "fallback": corpus()}),
        (GO_GOLDEN, {"note": "gen_go_ai_side.py 生成，期望字节＝backend/routers/chat.py::_sse_event",
                     "cases": sse_cases()}),
    ]
    for path, data in targets:
        text = render(data)
        if check:
            if not path.exists():
                print(f"缺失：{path}")
                bad += 1
                continue
            if path.read_text(encoding="utf-8") != text:
                print(f"漂移：{path}（改源后未重录，或手改了副本）")
                bad += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"已写：{path.relative_to(ROOT)}")
    if check:
        print(f"漂移检查：{'两份副本都与源一致' if bad == 0 else f'{bad} 份漂移'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
