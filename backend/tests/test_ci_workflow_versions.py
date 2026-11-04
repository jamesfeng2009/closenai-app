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

后面两条判据是从 2026-11-04 那次 CI 红里长出来的，管的不是版本而是**这一步能不能失败**：
建库步骤没挂 PG 连接串时迁移打到 sqlite 文件，以及按改动抽样的作业浅克隆后拿不到基线、
`git rev-parse` 的 128 又被 `echo` 吞掉。两条都配了正向证人，理由写在各自的 docstring 里。
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
# actions/checkout 里「取全部历史」的两种同义写法；不带引号 YAML 解析成 int、
# 带引号解析成 str，判据读的是深度这个语义而不是排版。
DEEP_FETCH = {0, -1, "0", "-1"}


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


def test_mutation_sampling_job_fetches_history_and_proves_its_diff_base() -> None:
    """按改动抽样的作业必须① 拿得到历史、② base 解析不出来时**这一步**就红。

    立的动机是现读 run 41772007098 的日志（2026-11-04）：mutation-sample 作业红在
    `git diff --name-only HEAD~1 失败：fatal: ambiguous argument 'HEAD~1'`，
    而红的那一步之前，「Resolve diff base」是**绿**的——它把字面量 `HEAD~1` 当成了 sha 写进输出。
    两个叠在一起的原因：
    - `actions/checkout@v4` 默认 `fetch-depth: 1`，仓库里只有 HEAD 一个提交，`HEAD~1` 无从解析
      （push 事件按改动抽样从来没真跑过，一直是这一步在兜底）；
    - `echo "sha=$(git rev-parse HEAD~1)"` 里 git 退 128 也被盖掉：rev-parse 对解析不出来的参数
      **原样打到 stdout**，而命令替换写在 echo 参数位置时 errexit 看的是 echo 的退出码。
      本机在 depth-1 克隆里复现过：该步 rc=0、输出 `sha=HEAD~1`；
      换成 `sha="$(git rev-parse --verify 'HEAD~1^{commit}')"` 则 rc=128。
    证人（`git cat-file -e`）补的是第三层：base 为空或不在库里时，抽样会走
    mutation_sample.py 的「没有可比对的变更：抽样集为空」分支**退 0**——那是空集上的假绿。
    按 YAML 结构读而不是按文本找：判据要落在**那个真跑 `--changed-from` 的作业**自己的
    checkout 与 `id: base` 步骤上，别的作业钉多浅都不该红它。
    """
    import yaml

    sampling: list[tuple[Path, dict]] = []
    for workflow in WORKFLOWS:
        doc = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for job_id, job in (doc.get("jobs") or {}).items():
            steps = job.get("steps") or []
            if any("--changed-from" in str(s.get("run") or "") for s in steps):
                sampling.append((workflow, {"job": job_id, "steps": steps}))
    assert sampling, (
        "两份 workflow 里再没有按改动抽样的作业 ⇒ 本闸恒真（空集上的绿），"
        "作业被删了也照样绿，先确认抽样面去哪了再删本闸"
    )

    for workflow, entry in sampling:
        job_id, steps = entry["job"], entry["steps"]
        checkouts = [s for s in steps if "actions/checkout" in str(s.get("uses") or "")]
        assert checkouts, f"{workflow.name} 的作业 {job_id} 按改动抽样却没有 checkout 步骤"
        for checkout in checkouts:
            depth = (checkout.get("with") or {}).get("fetch-depth")
            assert depth in DEEP_FETCH, (
                f"{workflow.name} 的作业 {job_id} 把仓库浅克隆了（fetch-depth={depth!r}）"
                f" ⇒ `HEAD~1` 与 PR 的 base sha 都不在克隆里，按改动抽样只能拿到空变更集"
            )
        base_steps = [s for s in steps if s.get("id") == "base"]
        assert len(base_steps) == 1, (
            f"{workflow.name} 的作业 {job_id} 里 `id: base` 的步骤有 {len(base_steps)} 条"
            " ⇒ 抽样脚本的 `--changed-from` 到底吃哪一个基线不确定"
        )
        run_text = str(base_steps[0].get("run") or "")
        assert "--verify" in run_text, (
            f"{workflow.name} 的 Resolve diff base 用裸 `git rev-parse` 解析基准 ⇒ "
            "解析失败时它把参数原样吐进 stdout 并把退出码交给 echo，红会跑到下一步去"
        )
        assert "git cat-file -e" in run_text, (
            f"{workflow.name} 的 Resolve diff base 没有「base 真是一个提交」这个证人 ⇒ "
            "base 为空时抽样走空集分支退 0，作业绿着什么都没抽"
        )
