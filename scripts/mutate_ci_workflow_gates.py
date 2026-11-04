#!/usr/bin/env python3
"""CI 工具链版本闸（backend/tests/test_ci_workflow_versions.py）的变异自证（2026-11-04）。

判据不是「测试跑过」，而是「每条纪律都能被打红一次，且红在它该红的闸上」。
红靶覆盖（不写条数——条数句和清单漂过一次，见 docs/growth/kg22-kg29-template-closure-2026-11-03.md §3）：
三份 node 钉版各退回 engines 下限之下（ci.yml 两个作业 ＋ nightly 一处）、
装了 npm 依赖却把原生自检删掉、建库步骤的 PG env 摘掉、证人退回「只看退出码」、
按改动抽样的作业退回浅克隆（摘掉 fetch-depth 与把深度写成 1 各一条，证明判据比的是语义）、
Resolve diff base 退回被 echo 盖掉退出码的写法、再把它的正向证人单独删掉。
绿靶证明这不是「见字就红」：把 node 抬到 24（高于下限）必须仍绿——判据跟的是锁算出的下限，
不是一句写死的 22；node-version 去掉引号必须仍绿——它不红在 YAML 排版上；
证人那两句里的临时文件改名必须仍绿——它认的是「grep 到 (head)」这个动作，不是文件名；
fetch-depth 换成同义的 -1 必须仍绿；给步骤换显示名（id 不动）必须仍绿——判据按 YAML 结构认步骤。

M5 与 G5 那两条红/绿顺带自证了判据的作用域：M5 证明判据读的是**这一步的块**而不是整个文件
（ci.yml 的 backend 作业本来就挂着一条 postgresql+psycopg 串，按全文件找它照样绿），
G5 证明判据读的是 `id: base` 这一步而不是文件里出现的那句名字。

再往下一批管的是「作业有没有把被测代码需要的外部配置给齐」：backend 作业那条一次性 JWT 口令。
M11 把提供摘掉（＝2026-11-04 那个状态，7 条用例红在 `InvalidKeyError`），M12 把它改成写死的串
（红在「不是现生成的」），M13 在提供照旧的前提下往作业 env 里另抄一句字面量（红在全文那一格——
「别处有没有抄进口令」不能被「这一步给了没有」吸收掉）。G6/G7 是它的绿闸：换成同义的随机源、
只改这一步的显示名，都不该红。

最后一批管的是**变异作业自己的取证前置**（V 族）。run 41789159667 实测：作业抽到
`mutate_ci_workflow_gates.py` 自己时红在 `FileNotFoundError: …/.venv/bin/python`——
盘上绝大多数 `scripts/mutate_*.py`（条数由判据现读 `grep -l` 算出，不在文案里钉）把解释器钉成 `REPO/.venv/bin/python`，
而跑机的依赖装在 setup-python 那个解释器里，跑机上没有 `.venv` 这个目录。
V1 把造入口那一行删掉（＝这次修复前的状态），V2 给这一步挂 `if:`（步骤还在但被条件跳过——
判据读的是执行序列，不是「文件里有没有这段字」），V3 单独删掉 `.venv/bin/pytest` 那条证人
（`mutate_deviation_gates.py` 直接 exec 的是控制台脚本，只验 `-m pytest` 会漏这一格），
V4/V5 把 nightly 那侧的一次性口令与往返梯库摘掉（只补 PR 面会漏整条夜间流水线），
V6/V7 再把同样两格从 **ci.yml 的抽样作业**上单独摘掉（backend 作业那份留着）——
同一个串现在在一份文件里出现两次，不钉到作业上就说不清红读的是哪一格，所以 M11/M12/G6/G7
的锚点也带上紧跟其后的那一步 name。
G8/G9 是它的绿闸：只换这一步的显示名、只把符号链接的幂等守卫收成同义写法，都不该红。

口径照抄既有纪律（[[feedback-mutation-gates-no-concurrency]]、
[[feedback-mutation-restore-no-git-checkout]]、[[feedback-definition-of-done-tests]]）：
**串行跑**、copy2 备份写回 ＋ `filecmp` 逐字节复核、**不用 `git checkout`**。

用法：
    ./.venv/bin/python scripts/mutate_ci_workflow_gates.py --check
    ./.venv/bin/python scripts/mutate_ci_workflow_gates.py
    ./.venv/bin/python scripts/mutate_ci_workflow_gates.py --only M1,M4
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
CI = REPO / ".github" / "workflows" / "ci.yml"
NIGHTLY = REPO / ".github" / "workflows" / "mutation-nightly.yml"

TARGET = "backend/tests/test_ci_workflow_versions.py"
VENV_PY = REPO / ".venv" / "bin" / "python"

# 锚点出现次数不是 1 的用例，在这里显式登记期望次数。
ANCHOR_COUNT = {"M4": 2}

NODE_LINE = 'node-version: "22"'
WITNESS = (
    "          alembic -c backend/alembic.ini current 2>&1 | tee /tmp/alembic_current.txt\n"
    '          grep -q "(head)" /tmp/alembic_current.txt'
)
WITNESS_RENAMED = (
    "          alembic -c backend/alembic.ini current 2>&1 | tee /tmp/mut_head.txt\n"
    '          grep -q "(head)" /tmp/mut_head.txt'
)
SELFCHECK = (
    '          node -e "const pkg=require(\'better-sqlite3/package.json\');'
    "const D=require('better-sqlite3');const db=new D(':memory:');"
    "db.exec('create table t(x)');db.prepare('insert into t values (?)').run(7);"
    "if(db.prepare('select x from t').get().x!==7)throw new Error('roundtrip 不符');"
    "console.log('better-sqlite3 '+pkg.version+' 可用，node '+process.version)\""
)
DEPTH_LINE = "          fetch-depth: 0"
DIFF_BASE_RUN = (
    '        run: |\n'
    '          if [ "${{ github.event_name }}" = "pull_request" ]; then\n'
    '            sha="${{ github.event.pull_request.base.sha }}"\n'
    "          else\n"
    '            sha="$(git rev-parse --verify \'HEAD~1^{commit}\')"\n'
    "          fi\n"
)
# 2026-11-04 那次红真实的写法：echo 把 rev-parse 的 128 盖掉，字面量 HEAD~1 进了输出。
DIFF_BASE_RUN_OLD = (
    '        run: |\n'
    '          if [ "${{ github.event_name }}" = "pull_request" ]; then\n'
    '            echo "sha=${{ github.event.pull_request.base.sha }}" >> "$GITHUB_OUTPUT"\n'
    "          else\n"
    '            echo "sha=$(git rev-parse HEAD~1)" >> "$GITHUB_OUTPUT"\n'
    "          fi\n"
)
BASE_WITNESS = (
    '          git cat-file -e "${sha}^{commit}"\n'
    '          echo "diff base = ${sha}，其后 $(git rev-list --count "${sha}..HEAD") 个提交可比对"\n'
)
BASE_WITNESS_GONE = '          echo "diff base = ${sha}"\n'
# backend 作业那条一次性口令：写 $GITHUB_ENV 的那一行、它的同义写法、以及两种「不该过」的写法。
# 锚点必须带上**紧跟其后的那一步的 name**：同一段 heredoc 现在在 ci.yml 里出现两次
# （backend 作业与 mutation-sample 作业各一条），只钉 `fh.write(...)` 那一行会命中 2 次，
# 而 `replace` 是全量替换——那等于一条变异同时摘掉两个作业，判据到底红在哪一侧就说不清了。
JWT_WRITE_LINE = '              fh.write(f"JWT_SECRET={secrets.token_urlsafe(48)}\\n")\n'
JWT_WRITE_HEX = '              fh.write(f"JWT_SECRET={secrets.token_hex(32)}\\n")\n'
JWT_WRITE_DEAD = '              fh.write("JWT_SECRET=ci_hardcoded_secret_value_0123456789ab\\n")\n'
JWT_PRINT_TAIL = (
    '          print("已生成本次运行的一次性 JWT 口令（不落盘、不入库、不跨运行）")\n'
    "          EOF\n"
)
BACKEND_STEP_TAIL = JWT_PRINT_TAIL + "      - name: pytest (backend)\n"
SAMPLE_STEP_TAIL = JWT_PRINT_TAIL + "      - name: mutation_sample（按改动抽样，串行）\n"
BACKEND_JWT_STEP_HEAD = (
    "      - name: 生成本次运行的一次性 JWT 口令\n"
    "        # 为什么这一步存在（2026-11-04 实测红 7 条）：本作业此前不给 JWT_SECRET，\n"
)
BACKEND_JWT_STEP_RENAMED = (
    "      - name: 给本次运行生成一次性口令（改名不改判据）\n"
    "        # 为什么这一步存在（2026-11-04 实测红 7 条）：本作业此前不给 JWT_SECRET，\n"
)
BACKEND_JOB_ENV = ("    env:\n"
                   "      TEST_DATABASE_URL: "
                   "postgresql://postgres:postgres@127.0.0.1:5432/closenai_ci\n")
BACKEND_JOB_ENV_LITERAL = BACKEND_JOB_ENV.replace(
    "    env:\n",
    "    env:\n      JWT_SECRET: ci_hardcoded_secret_value_0123456789ab\n")

# 变异作业取证前置那一族（.venv 入口／它的两条证人／nightly 的口令与往返梯库）。
VENV_DIR_LINE = '          bin_dir = pathlib.Path(".venv/bin")\n'
VENV_STEP_HEAD = "      - name: 造 .venv 入口（变异脚本硬编码 REPO/.venv/bin/python）\n"
VENV_CONSOLE_WITNESS_LINE = "          .venv/bin/pytest --version\n"
VENV_GUARD_LINES = (
    "              if not link.is_symlink() and not link.exists():\n"
    "                  os.symlink(exe, link)\n"
)
N2_ENV_LINE = ("      N2_MIGRATE_DATABASE_URL: "
               "postgresql://postgres:postgres@127.0.0.1:5432/n2_roundtrip\n")
# ci.yml 里同一条 env 出现两次（backend 作业与 mutation-sample 作业），锚点得带上后面那格把它钉到
# 抽样作业上——摘错作业的那一条，红是同样红，但判据到底读的是哪个作业就说不清了。
N2_ENV_MUT = N2_ENV_LINE + "    steps:\n"

# 每条：(id, 说明, (文件, 原文, 坏文), target, 名片段)
MUTATIONS = [
    (
        "M1",
        "ci.yml 的 frontend 作业把 node 钉回 20（better-sqlite3 声明的下限是 22）："
        "npm 对 engines 不符只 warn，装出 ABI 不匹配的原生二进制，"
        "红最终以 13 个 suite 的 jest worker SIGSEGV 出现——本闸要把它按在提交上",
        (CI,
         f"          # 下限不猜：由 backend/tests/test_ci_workflow_versions.py 现读锁里逐包 engines 算出来。\n"
         f"          {NODE_LINE}",
         '          # 下限不猜：由 backend/tests/test_ci_workflow_versions.py 现读锁里逐包 engines 算出来。\n'
         '          node-version: "20"'),
        TARGET, "才是依赖要求的下限",
    ),
    (
        "M2",
        "ci.yml 的 mutation-sample 作业把 node 钉回 20：这个作业要跑 jest 侧的闸，"
        "同样会 require 原生包；只补 frontend 作业会漏掉这一条腿",
        (CI,
         '          # 而变异脚本里那些要跑 jest 的闸正好会 require 它（红在段错误上，看不出根因）。\n'
         f"          {NODE_LINE}",
         '          # 而变异脚本里那些要跑 jest 的闸正好会 require 它（红在段错误上，看不出根因）。\n'
         '          node-version: "20"'),
        TARGET, "才是依赖要求的下限",
    ),
    (
        "M3",
        "nightly 作业把 node 钉回 20：全量抽样走的这条流水线跟 ci.yml 用同一套前端依赖，"
        "版本闸必须覆盖两份 workflow 而不是只盯 PR 面",
        (NIGHTLY,
         '          # 下限由 backend/tests/test_ci_workflow_versions.py 现读锁算，不写死。\n'
         f"          {NODE_LINE}",
         '          # 下限由 backend/tests/test_ci_workflow_versions.py 现读锁算，不写死。\n'
         '          node-version: "20"'),
        TARGET, "才是依赖要求的下限",
    ),
    (
        "M4",
        "把 ci.yml 两处自检的 `node -e …require('better-sqlite3')…` 各换成一句 echo："
        "钉版够了不代表装出来的东西能用，删掉自检就回到「npm ci 全绿、jest 里 13 个 suite 全死」",
        (CI, SELFCHECK, '          echo "（原生自检这一格被删掉了）"'),
        TARGET, "装了 npm 依赖却没有原生模块自检",
    ),
    (
        "M5",
        "把 ci.yml 建库步骤的 PG env 摘掉：alembic 于是按 backend/config.py:37 的默认串走 sqlite，"
        "而这条迁移链在 sqlite 上红在 `CREATE EXTENSION IF NOT EXISTS vector`（本机实测）——"
        "红是红了，根因却长得像「迁移本身有问题」，而 CI 要的是它真的打到 closenai_mut。"
        "这一条红同时证明判据读的是**这一步的块**而不是整个文件："
        "ci.yml 的 backend 作业本来就有一条 postgresql+psycopg 串，按全文件找它照样绿",
        (CI,
         '        # 所以这里的 env 不是防假绿，而是让迁移真的落到 closenai_mut 这个 PG；证人再补一层落到了的证据。\n'
         '        env:\n'
         '          DATABASE_URL: postgresql+psycopg://postgres:postgres@127.0.0.1:5432/closenai_mut\n'
         '        run: |',
         '        # 所以这里的 env 不是防假绿，而是让迁移真的落到 closenai_mut 这个 PG；证人再补一层落到了的证据。\n'
         '        run: |'),
        TARGET, "的迁移步骤没挂 PG 连接串",
    ),
    (
        "M6",
        "把 nightly 证人那两句退回「只跑一次 alembic current」：本机实测对着一个从未 stamp 过的库，"
        "`alembic current` 不打印任何版本行但仍退 0——只看退出码就把「这一步有没有真把 schema 落进库」"
        "这件事放过去了，这正是补了 env 之后仍然缺的那一层证据",
        (NIGHTLY, WITNESS, "          alembic -c backend/alembic.ini current"),
        TARGET, "这个证人",
    ),
    (
        "M7",
        "把 mutation-sample 作业的 fetch-depth 那一格摘掉（回到 actions/checkout 默认的浅克隆）："
        "2026-11-04 run 41772007098 就是这么红的——深度 1 时仓库里只有 HEAD，`HEAD~1` 与 PR 的 "
        "base sha 都不在克隆里，按改动抽样永远拿到空变更集",
        (CI, "        with:\n" + DEPTH_LINE + "\n", "        with:\n"),
        TARGET, "把仓库浅克隆了",
    ),
    (
        "M8",
        "把 fetch-depth 从 0 改成 1：证明判据比的是**深度**，不是「有没有 with 这一格」——"
        "写成 fetch-depth: 1 的「看起来配置过了」必须照样红",
        (CI, DEPTH_LINE, "          fetch-depth: 1"),
        TARGET, "把仓库浅克隆了",
    ),
    (
        "M9",
        "把 Resolve diff base 退回 2026-11-04 那个写法（`echo \"sha=$(git rev-parse HEAD~1)\"`）："
        "本机在 depth-1 克隆里复现过，这一步 rc=0 并把字面量 HEAD~1 写进输出，红跑到下一步的 "
        "`git diff --name-only HEAD~1` 上，看起来像抽样脚本坏了。--verify＋赋值形式才是这一步的红",
        (CI, DIFF_BASE_RUN, DIFF_BASE_RUN_OLD),
        TARGET, "解析基准",
    ),
    (
        "M10",
        "保留 --verify 但删掉 `git cat-file -e` 证人：base 为空或不在库里时，"
        "mutation_sample.py 走「没有可比对的变更：抽样集为空」那条分支**退 0**——"
        "整个作业绿着什么也没抽，这是空集上的假绿，证人补的正是这一层",
        (CI, BASE_WITNESS, BASE_WITNESS_GONE),
        TARGET, "base 真是一个提交",
    ),
    (
        "M11",
        "把 backend 作业那条写 `$GITHUB_ENV` 的行摘掉（＝提供没了）：这就是 2026-11-04 那个状态——"
        "作业不给 JWT_SECRET，`backend/config.py:48` 的默认空串让 7 条「注册→签发 JWT」的用例"
        "全红在 `InvalidKeyError: HMAC key must not be empty.`，而本机因为 backend/.env 里有口令永远看不见",
        (CI, JWT_WRITE_LINE + BACKEND_STEP_TAIL, BACKEND_STEP_TAIL),
        TARGET, "却没给 JWT_SECRET",
    ),
    (
        "M12",
        "把 backend 作业的生成改成写死的串（`echo` 一个固定口令进 env）：提供这一格照样过，红必须落在"
        "「不是现生成的」上——补 env 的正当路径是每次跑现生成或走 `${{ secrets.* }}`，"
        "把口令抄进 workflow 就是凭据入库",
        (CI, JWT_WRITE_LINE + BACKEND_STEP_TAIL, JWT_WRITE_DEAD + BACKEND_STEP_TAIL),
        TARGET, "不是现生成的",
    ),
    (
        "M13",
        "生成步骤原样保留，只在作业 env 里另抄一句字面量 JWT_SECRET：这一格打的是全文那半——"
        "「别处有没有写死的口令」不跟着「这一步提供了没有」走，否则同一条红会被提供那一格吸收掉",
        (CI, BACKEND_JOB_ENV, BACKEND_JOB_ENV_LITERAL),
        TARGET, "写死的 JWT 口令",
    ),
    (
        "V1",
        "把 mutation-sample 作业造 `.venv` 那一步的入口行删掉（2026-11-04 那个状态就是「没这一步」）："
        "盘上成批脚本硬编码 `REPO/.venv/bin/python`，抽到任何一条都红在 FileNotFoundError，"
        "而那不是被测代码的锅——本闸把「作业没给解释器」按在作业上",
        (CI, VENV_DIR_LINE, ""),
        TARGET, "却没给 `.venv/bin/python` 这个入口",
    ),
    (
        "V2",
        "给造 `.venv` 那一步挂上 `if:`：步骤还在、文本还匹配，但被条件跳过的事件里"
        "抽样那一刻仍然没有解释器。判据读的是**执行序列**，不是「文件里有没有这段字」",
        (CI, VENV_STEP_HEAD,
         VENV_STEP_HEAD + '        if: github.event_name == "push"\n'),
        TARGET, "把造 `.venv` 挂在条件上",
    ),
    (
        "V3",
        "删掉 `.venv/bin/pytest --version` 这条证人（`-m pytest` 那条留着）："
        "`mutate_deviation_gates.py` 直接 exec 的是控制台脚本，只验 `-m pytest` 会漏掉那一格，"
        "这一步就退化成「造了个也许不能用的目录」",
        (CI, VENV_CONSOLE_WITNESS_LINE, ""),
        TARGET, "造完 .venv 没有 exec",
    ),
    (
        "V4",
        "把 nightly 的一次性 JWT 口令写行摘掉（ci.yml 那条留着）：全量面抽到的整文件闸先红在 "
        "`InvalidKeyError`，`mutation_sample.py` 判「正向证人先红了」，那一格变异自证没有证据力。"
        "只补 PR 面会漏这一整条夜间流水线",
        (NIGHTLY, JWT_WRITE_LINE, '              fh.write("")\n'),
        TARGET, "跑变异脚本却没给 JWT_SECRET",
    ),
    (
        "V5",
        "把 nightly 的 `N2_MIGRATE_DATABASE_URL` 摘掉（库照建）：往返梯整条 skip，"
        "变异脚本把 skip 记「未跑」＝非零退出＝作业红，看起来像迁移面坏了而不是作业缺前置",
        (NIGHTLY, N2_ENV_LINE, ""),
        TARGET, "没给 N2_MIGRATE_DATABASE_URL",
    ),
    (
        "V6",
        "把 **ci.yml 的 mutation-sample 作业**那条一次性 JWT 写行摘掉（backend 作业那条留着）："
        "两份 workflow 里三处生成口令，只打 nightly 那一格证明不了判据读到了 PR 面的抽样作业——"
        "抽样作业抽到的整文件闸同样要签 JWT，缺它时 `mutation_sample.py` 判「正向证人先红了」，"
        "那一格变异自证没有证据力",
        (CI, JWT_WRITE_LINE + SAMPLE_STEP_TAIL, SAMPLE_STEP_TAIL),
        TARGET, "跑变异脚本却没给 JWT_SECRET",
    ),
    (
        "V7",
        "把 **ci.yml 的 mutation-sample 作业**的 `N2_MIGRATE_DATABASE_URL` 摘掉（backend 作业那条留着）："
        "同一个串在一份文件里出现两次，摘错那一格照样红，但红就不是这条判据读抽样作业的证据。"
        "缺它时往返梯整条 skip → 变异脚本记「未跑」＝非零退出＝作业红",
        (CI, N2_ENV_MUT, "    steps:\n"),
        TARGET, "没给 N2_MIGRATE_DATABASE_URL",
    ),
]

GREEN = [
    (
        "G1",
        "把 ci.yml frontend 作业的 node 抬到 24（高于下限）：闸必须仍绿——"
        "它比的是锁里算出的 engines 下限，不是写死的 22；否则依赖抬版本时闸会反过来拦升级",
        (CI,
         f"          # 下限不猜：由 backend/tests/test_ci_workflow_versions.py 现读锁里逐包 engines 算出来。\n"
         f"          {NODE_LINE}",
         '          # 下限不猜：由 backend/tests/test_ci_workflow_versions.py 现读锁里逐包 engines 算出来。\n'
         '          node-version: "24"'),
        TARGET,
    ),
    (
        "G2",
        "nightly 的 node-version 去掉引号（写成 22 而不是带引号的版本串）：判据读的是版本号，"
        "不该因为 YAML 写法换了就红——否则这条闸会红在无关的排版改动上",
        (NIGHTLY, f"          {NODE_LINE}", "          node-version: 22"),
        TARGET,
    ),
    (
        "G3",
        "把证人落盘的临时文件改名（alembic_current.txt → mut_head.txt，tee 与 grep 同步改）："
        "判据认的是「grep 到 (head)」这个动作，不是某个具体文件名——"
        "否则日后给两个作业各用一个临时文件就会红在无关的重命名上",
        (CI, WITNESS, WITNESS_RENAMED),
        TARGET,
    ),
    (
        "G4",
        "fetch-depth 从 0 换成 -1（actions/checkout 认的另一种「取全部历史」写法）：判据读的是"
        "深度这个语义，不该因为排版换了同义词就红——否则它会在无关的等价改动上拦人",
        (CI, DEPTH_LINE, "          fetch-depth: -1"),
        TARGET,
    ),
    (
        "G5",
        "把 Resolve diff base 这一步的**显示名**改掉（`id: base` 与 run 内容一字不动）："
        "判据按 YAML 结构认步骤，绑的是 id 而不是 name ⇒ 改名不改判据。"
        "这条绿同时自证判据不是「在文件里找一个字符串」",
        (CI,
         "      - name: Resolve diff base\n        id: base",
         "      - name: 解析 diff 基准（改名不改判据）\n        id: base"),
        TARGET,
    ),
    (
        "G6",
        "把一次性口令的随机源从 `secrets.token_urlsafe` 换成同义的 `secrets.token_hex`：判据认的是"
        "「每次跑现生成」这个语义，不是某一个函数名——否则整理写法就会红在无关改动上",
        (CI, JWT_WRITE_LINE + BACKEND_STEP_TAIL, JWT_WRITE_HEX + BACKEND_STEP_TAIL),
        TARGET,
    ),
    (
        "G7",
        "给 backend 那条口令生成步骤**只换显示名**（run 内容一字不动）：判据按步骤的 run 找提供与随机源，"
        "不认 name——这条绿同时自证 M11 的红不是「在文件里找一个名字」造成的",
        (CI, BACKEND_JWT_STEP_HEAD, BACKEND_JWT_STEP_RENAMED),
        TARGET,
    ),
    (
        "G8",
        "给造 `.venv` 那一步**只换显示名**（run 内容一字不动）：判据按 `bin_dir = pathlib.Path(...)` "
        "这一行认步骤，不认 name——否则日后整理流水线文案就会红在无关改动上",
        (CI, VENV_STEP_HEAD, "      - name: 暴露跑机解释器给变异脚本（改名不改判据）\n"),
        TARGET,
    ),
    (
        "G9",
        "把符号链接的幂等守卫从 `if not link.is_symlink() and not link.exists():` 收成 "
        "`if not link.exists():`：两种写法在「链接已存在就不重复建」上等价，判据认的是这一步"
        "真造了入口并当场 exec 证人，不是某个具体守卫——否则改一行实现细节就红",
        (CI, VENV_GUARD_LINES,
         "              if not link.exists():\n                  os.symlink(exe, link)\n"),
        TARGET,
    ),
]


def _expected(case_id: str) -> int:
    return ANCHOR_COUNT.get(case_id, 1)


def _apply(mutation: tuple, expect: int) -> None:
    path, old, new = mutation
    src = path.read_text(encoding="utf-8")
    n = src.count(old)
    assert n == expect, f"{path.name}：锚点命中 {n} 次（要求 {expect} 次）\n{old[:90]!r}"
    path.write_text(src.replace(old, new), encoding="utf-8")


def _verify_anchor(mutation: tuple, expect: int) -> str | None:
    path, old, _new = mutation
    if not path.exists():
        return "文件不存在"
    n = path.read_text(encoding="utf-8").count(old)
    return None if n == expect else f"命中 {n} 次（要求 {expect} 次）"


def run(target: str) -> tuple[int, str]:
    cmd = [str(VENV_PY), "-m", "pytest", target, "-q", "--no-header", "-x"]
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=600)
    return proc.returncode, proc.stdout + proc.stderr


def is_red_pytest(code: int, out: str, target: str) -> bool:
    if code == 0:
        return False
    if "no tests ran" in out or "1 skipped" in out:
        return False
    if "error" in out.split("= ")[-1]:
        return False
    return f"FAILED {target}" in out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数，不改任何文件")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()
    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    if args.check:
        bad = 0
        for case in [*MUTATIONS, *GREEN]:
            problem = _verify_anchor(case[2], _expected(case[0]))
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        print(f"锚点校验：{'全部命中' if bad == 0 else f'{bad} 条有问题'}"
              f"（共 {len(MUTATIONS) + len(GREEN)} 条）")
        return 1 if bad else 0

    code, out = run(TARGET)
    if code != 0:
        print("基线不是绿的，先修基线再谈变异：")
        print("  " + "\n  ".join(out.splitlines()[-15:]))
        return 2
    print(f"基线：{TARGET} 绿")

    paths = sorted({case[2][0] for case in [*MUTATIONS, *GREEN]})
    with tempfile.TemporaryDirectory(prefix="mutate-ci-node-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            bak = Path(tmp) / f"{idx:02d}-{path.parent.name}-{path.name}"
            shutil.copy2(path, bak)
            originals[path] = bak

        def restore() -> None:
            for path, bak in originals.items():
                shutil.copy2(bak, path)
                assert filecmp.cmp(bak, path, shallow=False), f"{path} 恢复后字节不一致"

        red_ok = red_fail = green_ok = green_fail = 0
        failures: list[str] = []
        try:
            for cid, desc, mutation, target, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(mutation, _expected(cid))
                try:
                    code, out = run(target)
                finally:
                    restore()
                hit = is_red_pytest(code, out, target)
                if hit and marker in out:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（exit={code}，"
                          f"名片段 {marker!r} 命中={marker in out}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))

            for cid, desc, mutation, target in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(mutation, _expected(cid))
                try:
                    code, out = run(target)
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))
        except Exception as exc:  # noqa: BLE001
            restore()
            print(f"异常中止，已恢复：{exc!r}")
            return 2

        code, out = run(TARGET)
        print(f"复核 {TARGET}：{'绿' if code == 0 else '红（残留！）'}")
        if code != 0:
            print("  " + "\n  ".join(out.splitlines()[-12:]))
            return 1

        print(f"\n汇总：打红 {red_ok}/{red_ok + red_fail}，绿闸 {green_ok}/{green_ok + green_fail}"
              f"｜未打红 {red_fail}｜被误伤 {green_fail}")
        if failures:
            print("未通过：" + ",".join(failures))
        return 1 if (red_fail or green_fail) else 0


if __name__ == "__main__":
    sys.exit(main())
