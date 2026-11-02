#!/usr/bin/env bash
# T7-12：刷新后端锁文件。**这是升级依赖的唯一入口**，不要在锁文件里手改版本。
#
#   bash scripts/lock_backend_deps.sh
#   PYTHON=/opt/homebrew/bin/python3.12 bash scripts/lock_backend_deps.sh
#
# 语义与安全更新流程：
#   · 本脚本按 requirements.txt / requirements-dev.in 的**允许范围**解析最新版本，
#     所以刷新通常会带来版本变化——这正是「安全更新」想做的事；
#   · 刷新后必须跑后端全量测试（pytest）与契约重导（scripts/export_contract.py），
#     结果写进当次改动记录；不要只看安装成功就收工；
#   · 想保留现有版本只补新依赖时，先在 requirements 里把新包写成精确版本再刷新。
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-.venv/bin/python}"
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
# 判据：backend/tests/test_python_lock.py；说明：docs/python-dependency-lock.md
EOF
}

gen() {
  local input="$1" output="$2" tmp
  tmp="$(mktemp)"
  uv pip compile "$input" --python "$PY" --generate-hashes --no-annotate --no-header -o "$tmp"
  { header; cat "$tmp"; } > "$output"
  rm -f "$tmp"
  echo "→ $output（$(grep -cE '^[A-Za-z0-9][A-Za-z0-9._-]*(\[[^]]*\])?==' "$output") 个包）"
}

gen backend/requirements.txt backend/requirements.lock
gen backend/requirements-dev.in backend/requirements-dev.lock
