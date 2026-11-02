#!/usr/bin/env bash
# T7-12：从锁文件安装后端依赖（唯一受控入口）。
#
#   bash scripts/install_backend_deps.sh --runtime   # 运行镜像
#   bash scripts/install_backend_deps.sh --dev       # CI / 本机测试（含 pypdf、grpcio-tools）
#   bash scripts/install_backend_deps.sh --dev --dry-run
#
# `--require-hashes` 是这条入口的重点：锁文件里每个包都带哈希，pip 不再做任何
# 「按 >= 现场解析」的动作；锁与索引不一致时直接失败，不会静默换版本。
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
mode="runtime"
if [ "${1:-}" = "--dev" ]; then
  mode="dev"
  shift
elif [ "${1:-}" = "--runtime" ]; then
  shift
fi

if [ "$mode" = "dev" ]; then
  lock="backend/requirements-dev.lock"
else
  lock="backend/requirements.lock"
fi

"$PY" - <<'PY'
import sys

if sys.version_info < (3, 12):
    raise SystemExit(f"需要 CPython >= 3.12，当前 {sys.version.split()[0]}")
print(f"→ Python {sys.version.split()[0]}；从锁文件安装（--require-hashes）")
PY

exec "$PY" -m pip install --require-hashes -r "$lock" "$@"
