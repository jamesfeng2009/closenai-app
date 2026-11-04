#!/usr/bin/env bash
# T7-12：刷新后端锁文件。**这是升级依赖的唯一入口**，不要在锁文件里手改版本。
#
#   bash scripts/lock_backend_deps.sh
#   bash scripts/lock_backend_deps.sh --minimal
#   PYTHON=/opt/homebrew/bin/python3.12 bash scripts/lock_backend_deps.sh
#
# 语义与安全更新流程：
#   · 默认（不带参数）按 requirements.txt / requirements-dev.in 的**允许范围**解析最新版本，
#     所以刷新通常会带来版本变化——这正是「安全更新」想做的事；
#   · 刷新后必须跑后端全量测试（pytest）与契约重导（scripts/export_contract.py），
#     结果写进当次改动记录；不要只看安装成功就收工；
#   · 想保留现有版本只补新依赖时，用 `--minimal`：把**现行锁**当 uv 的 constraint，
#     共享包的版本由锁本身钉住，只有输入里新增的那几条会进锁。
#     2026-11-04 实测为什么需要这个模式：只为补一条 `aiosqlite==0.22.1`（CI 三条红的根因）
#     跑默认刷新，两份锁里有 **52／55 个包换了版本**（fastapi 0.141.1→0.162.0、
#     alembic 1.18.5→1.20.1），还新增 4 个传递依赖并踢掉 tqdm——一笔 CI 修复背不动
#     一整轮依赖升级的回归面，所以「只补一条」必须是入口自己能表达的动作，
#     而不是让人退回去手改锁文件（那正好违反本文件第一句话）。
#   · 旧写法「先在 requirements 里把新包写成精确版本再刷新」只钉得住新那条，
#     其余仍会漂，所以它解决不了上面的问题。
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-.venv/bin/python}"
mode="full"
if [ "${1:-}" = "--minimal" ]; then
  mode="minimal"
  shift
fi
if [ ! -x "$PY" ]; then
  echo "没找到 $PY；用 PYTHON=/path/to/python 指定一个 >=3.12 的解释器" >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "需要 uv（https://docs.astral.sh/uv/）；brew install uv" >&2
  exit 1
fi

header() {
  cat <<'EOF'
# T7-12：Python 依赖锁（完整、带哈希）。不要手改本文件。
# 安装：bash scripts/install_backend_deps.sh --runtime（CI/本地测试用 --dev）
# 刷新：bash scripts/lock_backend_deps.sh（依赖变更后跑，再跑后端全量测试）
#       只想补新依赖、不让共享包版本漂：加 --minimal
# 判据：backend/tests/test_python_lock.py；说明：docs/python-dependency-lock.md
EOF
}

# 把现行锁剥成约束文件：只留 `name==version`，去掉 --hash 续行。
# 为什么要剥：uv 不接受「约束里带哈希」这种形状（哈希属于安装期，不属于求解期），
# 直接喂原锁会整条命令报错，而不是安静忽略。
constraints_from_lock() {
  local lock="$1" out="$2"
  "$PY" - "$lock" "$out" <<'PY'
import re
import sys

pattern = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^]]*\])?==([^\s;\\]+)")
kept = 0
with open(sys.argv[1], encoding="utf-8") as src, open(sys.argv[2], "w", encoding="utf-8") as dst:
    for line in src:
        m = pattern.match(line)
        if m:
            dst.write(f"{m.group(1)}=={m.group(2)}\n")
            kept += 1
print(f"→ 约束：{sys.argv[1]} 的 {kept} 个包版本")
PY
}

gen() {
  local input="$1" output="$2" tmp extra=()
  tmp="$(mktemp)"
  if [ "$mode" = "minimal" ]; then
    if [ ! -s "$output" ]; then
      echo "--minimal 要求 $output 已存在（它本身就是约束来源）" >&2
      exit 1
    fi
    cfile="$(mktemp)"
    constraints_from_lock "$output" "$cfile"
    extra=(-c "$cfile")
  fi
  uv pip compile "$input" "${extra[@]}" --python "$PY" --generate-hashes --no-annotate --no-header -o "$tmp"
  { header; cat "$tmp"; } > "$output"
  rm -f "$tmp" "${extra[1]:-}"
  echo "→ $output（$(grep -cE '^[A-Za-z0-9][A-Za-z0-9._-]*(\[[^]]*\])?==' "$output") 个包）"
}

gen backend/requirements.txt backend/requirements.lock
gen backend/requirements-dev.in backend/requirements-dev.lock
