#!/usr/bin/env python3
"""批 T-F（trace 全链路·聚合清单）的变异自证（2026-10-17）。

本批交付的是**还没上刀的文件**，所以闸只能是静态断言，红也只能由变异制造：
采集面少一条 job、regex 与消毒形状漂一格、镜像退回 latest、hostPath 丢了只读、
口径注释被改薄——每一种都是「装上去那天才发现」的代价，这里各打红一次。

纪律同前两批（串行、copy2 备份写回＋filecmp、不用 git checkout；基线不绿记「未跑」）。

用法：
    ./.venv/bin/python scripts/mutate_trace_tf_gates.py --check
    ./.venv/bin/python scripts/mutate_trace_tf_gates.py
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
VENV_PY = REPO / ".venv" / "bin" / "python"

PROMTAIL = REPO / "deploy" / "k8s" / "60-observability" / "62-promtail.yaml"
LOKI = REPO / "deploy" / "k8s" / "60-observability" / "61-loki.yaml"
CONFIG = REPO / "deploy" / "k8s" / "10-config.yaml"
GATE = "backend/tests/test_trace_observability_manifests.py"

PY_REGEX_LINE = "              expression: 'trace_id=(?P<trace_id>[A-Za-z0-9._-]{1,128})'"

# ── 变异表 ────────────────────────────────────────────────────────────────
MUTATIONS: list[tuple] = [
    (
        "F1",
        "go-api 的 json 阶段抽了 trace_id，但 labels 阶段漏提——查询时按标签过滤永远空",
        [(PROMTAIL,
          "          - labels:\n              trace_id:\n              user_id:\n              level:",
          "          - labels:\n              user_id:\n              level:")],
        "pytest", f"{GATE}::test_go_job_indexes_trace_via_json_pipeline", "", "json 抽了但没提标签",
    ),
    (
        "F2",
        "第一条 Python job 的 regex 形状漂成 {1,64}：消毒收到 128、索引只收 64，"
        "长 trace 的行「在 Loki 里不存在」——T-8 字面量的三方钉法就是防这一格",
        [(PROMTAIL, PY_REGEX_LINE,
          "              expression: 'trace_id=(?P<trace_id>[A-Za-z0-9._-]{1,64})'", 2)],
        "pytest", f"{GATE}::test_python_jobs_index_trace_with_the_shared_shape", "",
        "形状与 core/logging.TRACE_SHAPE 漂移",
    ),
    (
        "F2b",
        "漂的是**第二条** job（ai-rpc）：循环若只真判了第一条，这条会假装绿",
        [(PROMTAIL,
          "              __path__: /var/log/pods/closenai_ai-rpc-*/*/*.log\n"
          "        pipeline_stages:\n"
          "          - regex:\n" + PY_REGEX_LINE,
          "              __path__: /var/log/pods/closenai_ai-rpc-*/*/*.log\n"
          "        pipeline_stages:\n"
          "          - regex:\n"
          "              expression: 'trace_id=(?P<trace_id>[A-Za-z0-9._-]{1,64})'")],
        "pytest", f"{GATE}::test_python_jobs_index_trace_with_the_shared_shape", "",
        "ai-rpc: regex 形状",
    ),
    (
        "F3",
        "__path__ 通配改成不存在的前缀：采集面对着一个不存在的目录扫——不报错，只是查不到",
        [(PROMTAIL,
          "__path__: /var/log/pods/closenai_go-api-*/*/*.log",
          "__path__: /var/log/pods/closenai_goapi-*/*/*.log")],
        "pytest", f"{GATE}::test_three_jobs_cover_exactly_three_deployments", "", "通配没指到",
    ),
    (
        "F4",
        "loki 镜像退回 latest：单节点上「重启换了个小版本」是查无对证的事故形态",
        [(LOKI, "image: grafana/loki:3.3.2", "image: grafana/loki:latest")],
        "pytest", f"{GATE}::test_images_pinned_on_one_release_line", "", "钉死版本",
    ),
    (
        "F5",
        "pods-logs 挂载丢了 readOnly：采集器拿到节点日志目录的写权限——不成比例的授权面",
        [(PROMTAIL,
          "            - name: pods-logs\n              mountPath: /var/log/pods\n              readOnly: true",
          "            - name: pods-logs\n              mountPath: /var/log/pods")],
        "pytest", f"{GATE}::test_host_path_is_read_only_and_positions_writable", "", "必须只读",
    ),
    (
        "F6",
        "把 10-config 口径注释里的目录指针改薄成泛指：注释还在、裁决的对象没了——"
        "下一个人往 ConfigMap 塞日志开关时少了一道拦住他的字",
        [(CONFIG,
          "# deploy/k8s/60-observability/ 的 Loki＋promtail 清单只认这两种形状做索引——",
          "# Loki＋promtail 那套清单只认这两种形状做索引——")],
        "pytest", f"{GATE}::test_config_map_carries_the_cutover_note", "", "口径注释",
    ),
]

# GREEN：中性改动不许弄红任何闸（同前两批的「闸判内容不判指纹」证人）。
GREEN: list[tuple] = [
    (
        "GR1",
        "在 promtail 清单里加一行纯注释：静态闸不该因「文件被改过」变红",
        [(PROMTAIL, "    positions:",
          "    # 变异证人：这行注释不该弄红任何闸。\n    positions:", 1)],
        "pytest", GATE, "", "",
    ),
    (
        "GR2",
        "在 10-config 的注释里多提一次 trace_id：正向证人的两个词都还在，闸必须保持绿",
        [(CONFIG, "# 敏感项（DATABASE_URL",
          "# 变异证人：注释里提一句 trace_id 与 60-observability 不该弄红。\n"
          "# 敏感项（DATABASE_URL", 1)],
        "pytest", GATE, "", "",
    ),
]


def _apply(edits: list) -> None:
    for edit in edits:
        path, old, new = edit[0], edit[1], edit[2]
        want = edit[3] if len(edit) > 3 else 1
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        if want == "all":
            assert n >= 1, f"{path.name}：needle 0 命中"
            path.write_text(src.replace(old, new), encoding="utf-8")
        else:
            assert n == want, f"{path.name}：锚点命中 {n} 次，要求 {want} 次\n{old[:90]!r}"
            path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _sanity(edits: list) -> str | None:
    """清单被改坏成语法不可解析时，红的是工具链而不是判据——先当场拦下。"""
    import yaml
    for edit in edits:
        path = edit[0]
        text = path.read_text(encoding="utf-8")
        try:
            list(yaml.safe_load_all(text))
        except yaml.YAMLError as exc:
            return f"{path.name}：变异体不是合法 YAML（{exc}）"
    return None


def _verify_anchor(edits: list) -> str | None:
    for edit in edits:
        path, old = edit[0], edit[1]
        want = edit[3] if len(edit) > 3 else 1
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if want != "all" and n != want:
            return f"命中 {n} 次（要 {want}）"
    return None


def run(target: str) -> tuple[int, str]:
    proc = subprocess.run([str(VENV_PY), "-m", "pytest", target, "-q", "--no-header"],
                          cwd=REPO, capture_output=True, text=True, timeout=600)
    return proc.returncode, proc.stdout + proc.stderr


def is_red(code: int, out: str, target: str) -> bool:
    if code == 0:
        return False
    if f"FAILED {target}" not in out:
        return False
    if "error" in out.splitlines()[-1].lower():
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数，不改任何文件")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()
    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    cases = [*MUTATIONS, *GREEN]
    if args.check:
        bad = 0
        for case in cases:
            problem = _verify_anchor(case[2])
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        print(f"锚点校验：{'全部命中要求次数' if bad == 0 else f'{bad} 条有问题'}"
              f"（共 {len(cases)} 条）")
        return 1 if bad else 0

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-trace-tf-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            rel = path.relative_to(REPO).as_posix().replace("/", "+")
            bak = Path(tmp) / f"{idx:02d}-{rel}"
            assert bak not in originals.values(), f"备份名碰撞：{bak}"
            shutil.copy2(path, bak)
            originals[path] = bak

        def restore() -> None:
            for path, bak in originals.items():
                shutil.copy2(bak, path)
                assert filecmp.cmp(bak, path, shallow=False), f"{path} 恢复后字节不一致"

        red_ok = red_fail = green_ok = green_fail = 0
        failures: list[str] = []
        try:
            for cid, desc, edits, _runner, target, _node, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = run(target)
                if base_code != 0:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 未跑（基线本来就不绿，exit={base_code}）：不能把环境的红记成闸的红\n  "
                          + "\n  ".join(base_out.splitlines()[-8:]))
                    continue
                _apply(edits)
                try:
                    broken = _sanity(edits)
                    if broken is None:
                        code, out = run(target)
                    else:
                        code, out = -1, broken
                finally:
                    restore()
                if code == -1:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（变异体自身不合法）：{out}")
                    continue
                if is_red(code, out, target) and marker in out:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓（pytest）  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（pytest exit={code}，名片段 {marker!r} 命中={marker in out}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))

            for cid, desc, edits, _runner, target, _node, _marker in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(edits)
                try:
                    code, out = run(target)
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓（pytest）  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（pytest exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))
        except Exception as exc:  # noqa: BLE001
            restore()
            print(f"异常中止，已恢复：{exc!r}")
            return 2

        code, out = run(GATE)
        residue = 0 if code == 0 else 1
        print(("复核 清单闸：绿" if code == 0
               else "残留（红！）：\n  " + "\n  ".join(out.splitlines()[-15:])))
        print(f"\n批 T-F 变异自证：打红 {red_ok}/{len(MUTATIONS)}，保持绿 {green_ok}/{len(GREEN)}，"
              f"失败 {red_fail + green_fail}，残留 {residue}")
        if failures:
            print(f"失败清单：{failures}")
        return 1 if (red_fail or green_fail or residue) else 0


if __name__ == "__main__":
    sys.exit(main())
