# Python 依赖锁（T7-12）

本仓库的后端依赖**不再按 `>=` 现场解析**。运行时与 CI 都装带哈希的精确锁，冲突时安装直接失败，不会静默换版本。

## 文件角色

| 文件 | 角色 | 谁在用 |
|---|---|---|
| `backend/requirements.txt` | **输入清单**：人写直接依赖与范围 | 只被锁生成脚本读取，不被任何安装入口直接安装 |
| `backend/requirements-dev.in` | 开发输入：`-r requirements.txt` + 测试/构建工具 | 只被锁生成脚本读取 |
| `backend/requirements.lock` | 运行锁：74 个包，全部 `==` + `--hash=sha256:` | 镜像（`deploy/Dockerfile.py-api`） |
| `backend/requirements-dev.lock` | 开发锁：77 个包，运行锁的超集，共享包版本逐条相同 | CI 与本地测试 |

开发锁比运行锁多三个包：

- `pypdf` —— 仅测试用（PDF 用例解析产物），**不进镜像**。
- `grpcio-tools` —— 构建期工具，带 `protoc` 与 python 插件，装进镜像白加约 22M。
- `setuptools` —— `grpcio-tools` 的传递依赖，同样只在开发面。

两份锁的共享包版本必须一致（由 `backend/tests/test_python_lock.py::test_dev_lock_supersets_runtime_lock` 判）。

## 安装

```bash
# 运行时（镜像里用的就是这一条）
bash scripts/install_backend_deps.sh --runtime

# 开发/CI（默认）
bash scripts/install_backend_deps.sh --dev
```

脚本先校验解释器是 CPython **3.12+**，再执行
`pip install --require-hashes -r <lock>`。额外参数原样透传（例如 `--dry-run`、`--no-deps`）。

## 刷新（依赖变更后）

```bash
PYTHON=.venv/bin/python bash scripts/lock_backend_deps.sh
```

要求本机有 `uv`。脚本对 `requirements.txt` 与 `requirements-dev.in` 分别
`uv pip compile --generate-hashes --no-annotate --no-header`，然后写回统一的中文表头。
刷新后必须重跑后端全量测试——锁换了等于依赖换了。

## 干净环境校验

```bash
python3 scripts/verify_backend_lock.py                    # 默认 3.12 与 3.13
python3 scripts/verify_backend_lock.py --pythons python3.12
```

脚本为每个解释器新建临时 venv、只装开发锁（`--require-hashes`）、跑 `pip check`、
再 `pip freeze` 与锁逐包比对，并断言两个环境彼此一致。任何一步不一致即非零退出。
`--keep` 可保留临时目录用于排障。

## 解释器与系统前置

- **CPython 3.12**：CI 与本地验证用的版本（3.12.10 / 3.12.11 实测装出同一套版本）。
- **CPython 3.13**：镜像基座 `python:3.13-slim` 用的版本；同一把开发锁在 3.13 上装出同样的版本集合。
- **不需要系统 libpq 头文件**：`requirements.txt` 用 `psycopg[binary]`，wheel 自带 libpq。
- **pgvector**：Python 侧只是客户端包，扩展装在 PostgreSQL 服务端，不属本锁的安装前置。
- **`pg_dump` / `sqlc`**：只在 CI 的契约与迁移检查里用，属 CI 镜像前置，不入本锁。

## 安全更新流程

1. 改 `backend/requirements.txt`（或 `requirements-dev.in`）里的范围。
2. 跑 `scripts/lock_backend_deps.sh` 重新生成两份锁。
3. 跑 `./.venv/bin/python -m pytest backend/tests -q`（含 `test_python_lock.py`）。
4. 跑 `scripts/verify_backend_lock.py` 取证干净环境一致。
5. 升级单独成笔，不与功能改动混在一起。

## 判据与未覆盖

- 静态闸：`backend/tests/test_python_lock.py`（锁完整/精确/带哈希、直接依赖被覆盖且版本满足、
  开发锁是超集、镜像与 CI 都走锁并带 `--require-hashes`）。
- 实跑取证：`scripts/verify_backend_lock.py`。
- **未覆盖**：锁文件不校验索引端文件是否被替换之外的可信度问题（哈希已覆盖内容篡改）；
  镜像构建与真实部署未在本机验证，属部署面任务。
