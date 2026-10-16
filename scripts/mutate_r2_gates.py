#!/usr/bin/env python3
"""R2（Go 侧 gRPC client：deadline / 重试 / 熔断 / 降级 / SSE 映射）的变异自证（2026-10-16）。

tasks.md §R2 的验收逐字最后一句是本批的 DoD 定义：
  「**变异自证必须打红「超时被当放行」这条路径**」
⇒ M1 就是那一条，且它是全批唯一带 ⚠️ 的靶：它坏了不会有报错，只会让该拦的内容发出去。

其余靶点全部选在「回到 R2 立项时那个坏状态」或「今天真踩过的坑」上，不打稻草人：

- M2 Embed 的 fail-soft 变硬失败（ownership.md §1 第一行「不得变硬失败」）。
- M3 降级轮转按**字节**数（Python 的 len() 数码点）⇒ 同一段中文两侧落到不同模板。
- M4/M5/M6/M7 是 SSE 字节面四个「Go 的 encoding/json 与 CPython 默认值不一样」的点：
  分隔符、非 ASCII 转义、星平面代理对、`< > &` **不**转义。这四条都是夹具逐字节能看见的。
- M8 熔断把 permanent（畸形）也计进连续失败 ⇒ 一次字段误解把整条 AI 面关小黑屋。
- M9 首字之前的断流不再补模板（V3 `13:110-117` §8「must degrade gracefully」）。
- M10 DONE 带 content 被放过（chat.py:132 的 done 事件不带载荷 ⇒ 两个真相源）。
- M11/M12 是**今天真发生过的缺陷**：`withDefaults` 把零值 0 同时当成「没设」和「设成不重试」，
  于是 openai SDK 已经给现网的 2 次重投被悄悄砍成 0 次。这两条就是那两个哨兵的闸。

三条绿闸证明这些闸不是假红制造机：加注释、加与副本无关的键、给值对象加字段
都不该红——判据只看该看的东西。

恢复一律 **copy2 + filecmp 逐字节校验**，不用 `git checkout`
（见 [[feedback-mutation-restore-no-git-checkout]]）；**严禁并发跑**
（见 [[feedback-mutation-gates-no-concurrency]]）。
跑完自己复核两次：`gen_go_ai_side.py --check` 与整个 aiclient 包跑绿，确保没留残渣。

用法：
    ./.venv/bin/python scripts/mutate_r2_gates.py --check   # 只验锚点，不改文件
    ./.venv/bin/python scripts/mutate_r2_gates.py           # 全跑
    ./.venv/bin/python scripts/mutate_r2_gates.py --only M1,M3
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
GO_CORE = REPO / "go-core"
PKG = "./internal/aiclient/"
AICLIENT = GO_CORE / "internal" / "aiclient"
GRPC_GO = AICLIENT / "grpc.go"
SSE_GO = AICLIENT / "sse.go"
DEGRADE_GO = AICLIENT / "degrade.go"
API_GO = AICLIENT / "aiclient.go"
PY_GATE = "backend/tests/test_r2_ai_side_copies.py"
RECORDER = REPO / "scripts" / "gen_go_ai_side.py"
ZH_COPY = REPO / "backend" / "data" / "copy" / "zh.json"

GOROOT = "/Users/fengyu/sdk/go1.27.1"
# PATH 必须是**继承来的** PATH 后面追加 $GOROOT/bin，不是替换掉：这台机器的 Go 是混合安装
# （见 [[go-toolchain-mixed-install]]），把 PATH 换成脚本目录会让 go 找不到 git/ld，
# 于是「红」变成构建崩溃的红——那是假红，`is_red` 会把它判成未打红，白跑一轮。
GO_ENV = {**os.environ, "GOROOT": GOROOT, "PATH": f"{GOROOT}/bin:{os.environ.get('PATH', '')}"}
VENV_PY = str(REPO / ".venv/bin/python")


# ── 名片段（闸的报错原文片段；改文案要同时改这里，否则绿的是脚本、红的是真相）──
MARK_D8 = "超时被当放行"
MARK_FAILSOFT = "fail-soft 破了"
MARK_ROTATION = "轮转按字节数走了"
MARK_BYTES = "字节不同步"
MARK_BODY = "整条流的 SSE body 不对"
MARK_BREAKER = "把熔断打开了"
MARK_DEGRADE = "应有模板 + DEGRADED 两块"
MARK_MALFORMED = "畸形之后收尾成了"
MARK_RETRIES = "Retries got=0 want=2"
MARK_THRESHOLD = "threshold got=0 want=5"

# 每条：(id, 说明, 变异("text", 文件, 原文, 坏文), 闸, 名片段)
#   闸 ＝ ("go", "-run 名字") | ("pytest", 节点) | ("recorder", "--check")
MUTATIONS = [
    (
        "M1",
        "⚠️「超时被当放行」（D-8）：把 fail-closed 的失败面写成 SafetyOK ⇒ 必须红。"
        "这一条坏掉没有任何用户可见报错，所以它是本批唯一不可协商的靶",
        ("text", GRPC_GO,
         "Safety:   domain.Safety{Status: domain.SafetyUnavailable},",
         "Safety:   domain.Safety{Status: domain.SafetyOK},"),
        ("go", "TestSafetyTimeoutIsNeverPass"), MARK_D8,
    ),
    (
        "M2",
        "Embed 的 fail-soft 变硬失败（ownership.md §1：一次网络抖动不该让「记一条想法」失败）",
        ("text", GRPC_GO,
         "return EmbedResult{Degraded: true, Failure: e.Cause, Attempts: attempts}, nil",
         "return EmbedResult{}, e"),
        ("go", "TestUnaryFailureClasses"), MARK_FAILSOFT,
    ),
    (
        "M3",
        "降级轮转用 len(string)（字节）而不是 utf8.RuneCountInString（码点）："
        "Python 的 len() 数码点，同一段中文在两侧落到不同模板，且两种结果都通顺。"
        "坏文里那句 `+utf8.RuneCountInString(\"\")` 是**恒等于 0 的占位**，不是变异的一部分："
        "去掉整个调用会让 unicode/utf8 变成未使用导入，红就变成编译崩溃而不是断言失败",
        ("text", DEGRADE_GO,
         "templates[utf8.RuneCountInString(userInput)%len(templates)]",
         "templates[(len(userInput)+utf8.RuneCountInString(\"\"))%len(templates)]"),
        ("go", "TestFallbackRotationMatchesPythonCodePointCount"), MARK_ROTATION,
    ),
    (
        "M4",
        "SSE 的分隔符从 Python 的 `\", \"` 改成 Go 风格的 `\",\"`：现网字节变了，"
        "而这层字节是逐行 split 的依据",
        ("text", SSE_GO,
         "\tif value != nil {\n\t\tb = append(b, ',', ' ')",
         "\tif value != nil {\n\t\tb = append(b, ',')"),
        ("go", "TestSSEGoldenReplay"), MARK_BYTES,
    ),
    (
        "M5",
        "关掉 ensure_ascii（非 ASCII 裸 UTF-8 输出）：现网是 `\\u4f60\\u597d`，"
        "这正是这条字节闸立项时要拦的那一种「看起来一样」",
        ("text", SSE_GO,
         "case r <= 0xFFFF:\n\t\t\t\tb = appendHex4(b, uint16(r))",
         "case r <= 0xFFFF:\n\t\t\t\tb = append(b, []byte(string(r))...)"),
        ("go", "TestSSEGoldenReplay"), MARK_BYTES,
    ),
    (
        "M6",
        "星平面不拆代理对（v := r 而不是 r - 0x10000）：emoji 那一条夹具的观测点",
        ("text", SSE_GO, "v := r - 0x10000", "v := r - 0x2000"),
        ("go", "TestSSEGoldenReplay"), MARK_BYTES,
    ),
    (
        "M7",
        "把 `< > &` 也转义（Go 的 encoding/json 默认就这么干）：Python **不**转义它们，"
        "`<script>&</script>` 那条夹具就是反证",
        ("text", SSE_GO,
         "case r < 0x7f:\n\t\t\t\tb = append(b, byte(r))",
         "case r < 0x7f:\n\t\t\t\tif r == '<' || r == '>' || r == '&' {\n"
         "\t\t\t\t\tb = appendHex4(b, uint16(r))\n\t\t\t\t\t} else {\n"
         "\t\t\t\t\t\tb = append(b, byte(r))\n\t\t\t\t\t}"),
        ("go", "TestSSEGoldenReplay"), MARK_BYTES,
    ),
    (
        "M8",
        "熔断把 permanent（畸形）也计进连续失败：对面好好的，是我们的校验没过，"
        "一次字段误解就把整条 AI 面关进小黑屋是更严重的故障",
        ("text", GRPC_GO,
         "c.breaker.recordSuccess()\n\t\t}\n\t\t// 这里是 Embed 与 ClassifySafety 的分叉点",
         "c.breaker.recordTransient()\n\t\t}\n\t\t// 这里是 Embed 与 ClassifySafety 的分叉点"),
        ("go", "TestPermanentAndCancelDoNotOpenBreaker"), MARK_BREAKER,
    ),
    (
        "M9",
        "首字之前的断流不再补降级模板（delivered 一上来就 true）：V3 13 §8 的"
        "「user-facing product must degrade gracefully」直接破，用户看到的是一个错误",
        ("text", GRPC_GO, "delivered := false", "delivered := true"),
        ("go", "TestStreamDegradeBeforeFirstToken"), MARK_DEGRADE,
    ),
    (
        "M10",
        "DONE 带 content 被放过：done 事件今天不带载荷（chat.py:132 `_sse_event(\"done\", {})`），"
        "再带 content 就是两个真相源",
        ("text", GRPC_GO,
         "\t\tif out.Content != \"\" {\n\t\t\treturn ChatChunk{}, \"DONE 块带了 content\"\n\t\t}",
         "\t\t// 变异：放行 DONE 带 content"),
        ("go", "TestStreamMalformedStopsWithoutRetry"), MARK_MALFORMED,
    ),
    (
        "M11",
        "🔴 今天真发生过的缺陷：withDefaults 把零值当成「不重试」，"
        "于是 openai SDK 给现网的 2 次重投（DEFAULT_MAX_RETRIES=2）被悄悄砍成 0",
        ("text", GRPC_GO, "o.Retries = defaultRetries", "o.Retries = 0"),
        ("go", "TestOptionSentinels"), MARK_RETRIES,
    ),
    (
        "M12",
        "🔴 同一坑的第二半：熔断阈值的零值哨兵被当成「关掉」，默认 5 次从未生效过"
        "（对面重启期间每条请求都烧满 deadline）",
        ("text", GRPC_GO,
         "\tif o.BreakerThreshold == 0 {\n\t\to.BreakerThreshold = defaultBreakerCount\n\t}\n",
         ""),
        ("go", "TestOptionSentinels"), MARK_THRESHOLD,
    ),
    (
        "M13",
        "整条流的字节面被人改了一角（memory 块的键序 id/content/category 改成 id/category/content）："
        "单条 SSELine 的测可能没抓全，这一条量的是**拼接后的 body**",
        ("text", SSE_GO,
         'b = appendPyKeyValue(b, "content", m.Content)\n\t\tb = append(b, \',\', \' \')\n\t\tb = appendPyKeyValue(b, "category", m.Category)',
         'b = appendPyKeyValue(b, "category", m.Category)\n\t\tb = append(b, \',\', \' \')\n\t\tb = appendPyKeyValue(b, "content", m.Content)'),
        ("go", "TestStreamChatHappyPathToSSEBytes"), MARK_BODY,
    ),
    (
        "M14",
        "副本漂移：手改 Go 侧降级副本的一个字（成因②，与「改源忘重录」是两种成因）",
        ("text", AICLIENT / "templates" / "copy_msg_fallback.json",
         '"今天过得怎么样？有什么想和我聊聊的吗？"',
         '"今天过得怎么样？有什么想和我聊聊的吗～"'),
        ("pytest", f"{PY_GATE}::test_copies_do_not_drift"), "漂移",
    ),
    (
        "M15",
        "已发布客户端的事件面被人动了一角（前端联合类型去掉 done）："
        "Go 侧不许单方面造新事件名，但**客户端**那侧缩了也必须红",
        ("text", REPO / "frontend" / "src" / "types" / "index.ts",
         "type: 'token' | 'memory_context' | 'done' | 'error';",
         "type: 'token' | 'memory_context' | 'error';"),
        ("pytest", f"{PY_GATE}::test_shipped_client_still_only_knows_four_events"), "联合类型",
    ),
]

GREEN = [
    (
        "G1",
        "给 sse.go 加 15 行中文注释（不含任何被判的字节）：字节闸判的是输出，不是注释密度",
        ("text", SSE_GO, "func SSELine(chunk ChatChunk) string {",
         "".join(f"// 注释占位 {i}\n" for i in range(15)) + "func SSELine(chunk ChatChunk) string {"),
        ("go", "TestSSEGoldenReplay"),
    ),
    (
        "G2",
        "给 backend/data/copy/zh.json 加一个**与 msg.fallback 无关**的键："
        "副本只登记那一条数组，无关键不该引起漂移",
        ("text", ZH_COPY, '"msg": {\n    "_comment": "',
         '"msg": {\n    "r2_probe": "与副本无关的键",\n    "_comment": "'),
        ("recorder", "--check"),
    ),
    (
        "G3",
        "给 ChatRequest 加一个字段（additive）：方法面闸只数 Client 的方法名，"
        "值对象加字段不该被它误伤",
        ("text", API_GO, "type ChatRequest struct {\n\tUserID      string",
         "type ChatRequest struct {\n\tProbe       string\n\tUserID      string"),
        ("go", "TestClientSurfaceIsExactlyThreeMethods"),
    ),
]


def _apply(mutation: tuple) -> None:
    _kind, path, old, new = mutation
    src = path.read_text(encoding="utf-8")
    n = src.count(old)
    assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:90]!r}"
    path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _verify_anchor(mutation: tuple) -> str | None:
    _kind, path, old, _new = mutation
    if not path.exists():
        return "文件不存在"
    n = path.read_text(encoding="utf-8").count(old)
    return None if n == 1 else f"命中 {n} 次"


def run(gate: tuple) -> tuple[int, str]:
    kind, target = gate
    if kind == "go":
        proc = subprocess.run(
            ["go", "test", PKG, "-run", target, "-count=1"],
            cwd=GO_CORE, env=GO_ENV, capture_output=True, text=True, timeout=900,
        )
    elif kind == "recorder":
        proc = subprocess.run([VENV_PY, str(RECORDER), target],
                              cwd=REPO, capture_output=True, text=True, timeout=900)
    else:
        proc = subprocess.run([VENV_PY, "-m", "pytest", target, "-q", "--no-header"],
                              cwd=REPO, capture_output=True, text=True, timeout=900)
    return proc.returncode, proc.stdout + proc.stderr


def is_red(kind: str, code: int, out: str) -> bool:
    """「红」必须是**断言失败**的红，不是编译崩溃或收集失败的红。"""
    if code == 0:
        return False
    if "build failed" in out or "[setup failed]" in out:
        return False
    if kind == "pytest":
        return bool(out.count("failed") or out.count("error"))
    if kind == "go":
        return bool(out.count("FAIL"))
    return True  # recorder 用退出码表态（1＝漂移）


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

    paths = sorted({case[2][1] for case in [*MUTATIONS, *GREEN]})
    with tempfile.TemporaryDirectory(prefix="mutate-r2-") as tmp:
        originals = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            # 备份名带序号而不是只用 path.name：两个不同目录下的同名文件（本仓有 grpc.go 这种常见名）
            # 会在 tmp 里互相覆盖，于是「恢复」把 A 的内容写回 B，而 filecmp 因为比的是同一个
            # 被覆盖后的备份而照样通过——这就是留残渣还不报错的那一种错法。
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
            for cid, desc, mutation, gate, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(mutation)
                try:
                    code, out = run(gate)
                finally:
                    restore()
                if is_red(gate[0], code, out) and marker in out:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（exit={code}，名片段 {marker!r} 命中={marker in out}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-14:]))

            for cid, desc, mutation, gate in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(mutation)
                try:
                    code, out = run(gate)
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

        # 收尾自证：① 副本与夹具回到绿；② 整个包回到绿（变异残渣一个都不许留）。
        code, out = run(("recorder", "--check"))
        print(f"复核 gen_go_ai_side --check：{'绿' if code == 0 else '红（残留！）'}")
        proc = subprocess.run(["go", "test", PKG, "-count=1"], cwd=GO_CORE, env=GO_ENV,
                              capture_output=True, text=True, timeout=900)
        print(f"复核 aiclient 全包：{'绿' if proc.returncode == 0 else '红（残留！）'}")
        if proc.returncode != 0:
            print("  " + "\n  ".join(proc.stdout.splitlines()[-12:]))
            return 1

        print(f"\n计数：红闸 {red_ok} 打红 / {red_fail} 未打红；"
              f"绿闸 {green_ok} 保持绿 / {green_fail} 被误伤；异常 0")
        if failures:
            print("未通过：" + ",".join(failures))
        return 1 if (red_fail or green_fail) else 0


if __name__ == "__main__":
    sys.exit(main())
