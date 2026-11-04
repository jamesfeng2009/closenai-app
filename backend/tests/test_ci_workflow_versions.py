"""CI 工具链版本必须满足依赖自己声明的 engines（2026-11-04 立）。

立的动机是一条查了三天的红：frontend 作业 `Test Suites: 13 failed, 51 passed`、
`Tests: 1783 passed, 1783 total`——失败数是 0，那 13 个 suite **根本没跑起来**，
每个都报 `A jest worker process (pid=…) was terminated by another process: signal=SIGSEGV`。
真因不在 jest、不在内存、不是 flake：`better-sqlite3@13.0.3` 在 npm 锁里自带
`engines.node = ">=22"`，而跑机给的是 v20.20.2 —— npm 对 engines 不符只 warn 不拦，
于是装进来一个 ABI 对不上的原生二进制，只有「真的 require 它」的那些 suite 会死
（全是 `*Repository`／`migrations`／`cache` 一族，见 `__tests__/mocks/expo-sqlite.js` 顶上那句注释），
`npm ci` 自己反而是绿的。两次 run 死的是同一批 13 个，所以它是确定性的。

判据两侧不同源：一侧读 `frontend/package-lock.json` 里逐包的 engines，
一侧读两份 workflow 的 `node-version`。所以它不是一句写死的「必须 22」，
下次哪个原生包把下限抬到 24，这条闸会自己要求改跑机。

第二条判据补的是同一件事的另一半：版本够不够和**装出来的东西能不能用**判的不是
同一件事——`npm ci` 对 engines 不符只 warn，它绿着装出一个跑不了的二进制。
所以装了 npm 依赖的作业必须自己 require 一次那个原生包，把段错误提前成安装期红。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FRONTEND_LOCK = REPO / "frontend" / "package-lock.json"
WORKFLOWS = (
    REPO / ".github" / "workflows" / "ci.yml",
    REPO / ".github" / "workflows" / "mutation-nightly.yml",
)

NODE_FLOOR = re.compile(r">=\s*(\d+)")
PINNED = re.compile(r'node-version:\s*"?(\d+)"?')


def engine_floors() -> dict[int, list[str]]:
    """包名 → 它要求的 node 大版本下限（只认 `>=N` 这种硬下限）。"""
    data = json.loads(FRONTEND_LOCK.read_text(encoding="utf-8"))
    out: dict[int, list[str]] = {}
    for raw, meta in data.get("packages", {}).items():
        if not isinstance(meta, dict):
            continue
        spec = (meta.get("engines") or {}).get("node")
        if not isinstance(spec, str):
            continue
        floors = [int(m) for m in NODE_FLOOR.findall(spec)]
        if floors:
            out.setdefault(max(floors), []).append(raw.removeprefix("node_modules/"))
    return out


def test_ci_node_version_satisfies_every_declared_engine_floor() -> None:
    floors = engine_floors()
    assert floors, (
        f"{FRONTEND_LOCK.name} 里一个带 engines.node 的包都没有 ⇒ 本闸恒真，"
        "要么解析口径变了，要么锁换了形状，重写本闸而不是留着它空转"
    )
    need = max(floors)
    who = sorted(floors[need])
    pinned: dict[Path, list[int]] = {}
    for workflow in WORKFLOWS:
        majors = [int(m) for m in PINNED.findall(workflow.read_text(encoding="utf-8"))]
        assert majors, (
            f"{workflow.name} 里找不到任何 `node-version:` 钉版 ⇒ "
            "setup-node 会退到跑机默认镜像自带的那档，而默认档没人保证 >= 依赖要求"
        )
        pinned[workflow] = majors

    for workflow, majors in pinned.items():
        for got in majors:
            assert got >= need, (
                f"{workflow.name} 把 node 钉在 {got}，但 {need} 才是依赖要求的下限"
                f"（{len(who)} 个包卡它，如 {who[0]}）：engines 不符时 npm 只 warn，"
                "原生包会装成 ABI 不匹配的二进制并在运行时段错误"
            )


def test_native_selfcheck_step_exists_where_jest_runs() -> None:
    """装了前端依赖的作业必须**真的 require 一次**那个原生包。

    为什么还要这一条：`npm ci` 绿 ≠ 原生模块能用（上面那次红就是 npm ci 全绿、
    jest 里 13 个 suite 全死）。版本闸管的是「钉的版本够不够」，这一步管的是
    「装出来的东西能不能用」——两者判的不是同一件事，缺一个就还剩一种假绿。
    """
    needle = "require('better-sqlite3')"
    for workflow in WORKFLOWS:
        text = workflow.read_text(encoding="utf-8")
        assert "cd frontend && npm ci" in text, f"{workflow.name} 不再装前端依赖，本闸需重写"
        assert needle in text, (
            f"{workflow.name} 装了 npm 依赖却没有原生模块自检 ⇒ "
            "ABI 不匹配只会在 jest worker 里以 SIGSEGV 的形式出现，红得看不出根因"
        )


def test_mutation_migration_step_proves_it_reached_head_in_postgres() -> None:
    """建库那步必须**同时**挂 PG 的 DATABASE_URL 和一个读库的证人，且只看这一步自己的块。

    取证到日志级（2026-11-04 现读 run 41245498089／41357054861／41741006531 的 step 8）：
    三次 nightly 都红在「Create and migrate mutation database」，报的是
    `sqlalchemy/dialects/sqlite/aiosqlite.py:328 import_dbapi` 处的 ModuleNotFoundError——
    那一步没挂 `DATABASE_URL`，alembic 于是按 `backend/config.py:37` 的默认串走 sqlite。
    「装上 aiosqlite 后它会绿着把迁移打到 sqlite 文件、PG 留空」是我先前的推断，
    本机实测**不成立**（这条链在 sqlite 上红在 `CREATE EXTENSION IF NOT EXISTS vector`），
    所以 env 治的是「迁移根本没落到 PG」，而不是假绿。
    证人补另一半：本机实测对着一个从未 stamp 过的库，`alembic current` 不打印任何版本行
    **但仍退 0**，退出码因此不构成「schema 真在这个库里」的证据。
    读的是 marker 之后、下一个 `- name:` 之前那一段，不是整个文件——
    ci.yml 的 backend 作业本来也有一条 PG 串，按全文件找会让「迁移步骤没挂 env」溜过去。
    """
    for workflow in WORKFLOWS:
        text = workflow.read_text(encoding="utf-8")
        marker = "Create and migrate mutation database"
        assert marker in text, f"{workflow.name} 不再建变异库，本闸需重写"
        block = text.split(marker, 1)[1].split("\n      - name:", 1)[0]
        assert "DATABASE_URL: postgresql+psycopg://" in block, (
            f"{workflow.name} 的迁移步骤没挂 PG 连接串 ⇒ alembic 会按默认串打到 sqlite 文件"
        )
        assert 'grep -q "(head)"' in block, (
            f"{workflow.name} 的迁移步骤没有「库里 stamped 到 head」这个证人 ⇒ "
            "`alembic current` 打印空也照样退 0，空库放过去没人知道"
        )
