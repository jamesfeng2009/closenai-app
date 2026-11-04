#!/usr/bin/env python3
"""迁移往返梯（test_alembic_roundtrip_on_empty_database）的变异自证（2026-11-04，批次 5）。

判据不是「测试跑过」，而是「这条梯的每一格都能被打红一次，且红在它该红的证人上」。

为什么这条 harness 单独存在：往返梯是 PG 专属的（缺 `N2_MIGRATE_DATABASE_URL` 时整条 skip），
2026-11-04 那次 CI 红就是这么躲过本机基线的——链头换成 b7c9d1e3f5a2，梯里钉着的十句字面
revision 一句没跟着动，而**没有任何一条变异能证明「期望值是现读的」这件事**。批次 5 把期望
改成从 `backend/core/alembic_chain.py` 现读，红靶覆盖：
- M1 迁移件没建那张表 ⇒ 必须红在「T7-02 件没建 …」这条正向证人上；
- M2 主键少一列 ⇒ 必须红在幂等键证人上（否则「重复事件仍能被落两行」无人拦）；
- M3 downgrade 空转 ⇒ 必须红在「表没退干净」这条回退证人上；
- M4 **往链顶加一件新迁移** ⇒ head 断言照样对（这就是「现读」的全部价值），红必须落在证人句上，
  落在 head 句上就算这条自证不成立——所以 M4 额外带 forbid：head 那句话不许出现在输出里；
- M5 退 T7-02 时顺手带走别人的表 ⇒ 必须红在「迁移边界没切开」上。

绿靶证明这不是「见字就红」：G1 只加注释不许红；G2 把主键两列的**顺序**调换仍要绿——证人比的是
集合，不是列序字面串，否则日后重排列序会红在无关改动上。

口径照抄既有纪律（[[feedback-mutation-gates-no-concurrency]]、
[[feedback-mutation-restore-no-git-checkout]]、[[feedback-definition-of-done-tests]]）：
**串行跑**、copy2 备份写回 ＋ `filecmp` 逐字节复核、
**不用 `git checkout`**；M4 造出来的临时迁移件用「跑前断言不存在 ＋ finally 删除」处理，
不靠 git 清理。缺 env/缺库一律记「未跑」并以非零退出——未跑 ≠ 绿。

连接串只从 env 读，口令不进本文件也不进输出（[[feedback-no-credentials-in-repo]]）。

用法：
    export N2_MIGRATE_DATABASE_URL='postgresql://postgres:<口令>@127.0.0.1:55432/n2_roundtrip'
    ./.venv/bin/python scripts/mutate_roundtrip_leg_gates.py --check
    ./.venv/bin/python scripts/mutate_roundtrip_leg_gates.py
    ./.venv/bin/python scripts/mutate_roundtrip_leg_gates.py --only M4
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
VENV_PY = REPO / ".venv" / "bin" / "python"
RT_ENV = "N2_MIGRATE_DATABASE_URL"
TARGET = ("backend/tests/test_canonical_schema.py"
          "::test_alembic_roundtrip_on_empty_database")
MIGRATION = (REPO / "backend" / "alembic" / "versions"
             / "b7c9d1e3f5a2_add_analytics_event_receipts.py")
VERSIONS_DIR = REPO / "backend" / "alembic" / "versions"
DRIFT_FILE = VERSIONS_DIR / "zz0000mutate_roundtrip_drift_probe.py"

# head 句（现读之后不该再是红的来源）与证人句的区分，靠 --tb=line 精确定位那一行。
HEAD_SENTENCES = ("应为链头", "应回到链上上一件")

TABLE_LINE = "    op.create_table(\n        'analytics_event_receipts',\n"
EVENT_ID_LINE = "        sa.Column('event_id', sa.String(length=128), primary_key=True),\n"
USER_ID_LINE = "        sa.Column('user_id', sa.String(length=36), primary_key=True),\n"
DROP_LINE = "    op.drop_table('analytics_event_receipts')\n"

# 链顶临时件：down_revision 现取（不钉字面量，否则本 harness 自己变成第二个过期真源）。
DRIFT_TEMPLATE = '''"""变异自证 M4 的一次性件：往链顶加一件迁移，验往返梯的期望值是不是真的现读。

Revision ID: zz0000mutate_roundtrip_drift
Revises: %s
Create Date: 2026-11-04

本件由 scripts/mutate_roundtrip_leg_gates.py 写出、跑完即删，不属于仓库。
"""
from alembic import op
import sqlalchemy as sa

revision: str = 'zz0000mutate_roundtrip_drift'
down_revision = %r
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'zz_roundtrip_drift_probe',
        sa.Column('id', sa.String(length=36), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table('zz_roundtrip_drift_probe')
'''

# 每条：(id, 说明, edits, 名片段, forbid, create)
# edits = [(文件, 原文, 坏文), …]；create = 一次性文件的写法（None 表示不造文件）。
MUTATIONS = [
    (
        "M1",
        "T7-02 那件把表名写错（建出来的是另一张表）：链头照样落到库里、head 断言照样对，"
        "只有「这张表在不在」这条证人能看见——重复事件仍无处拦这件事不能只靠 head 数字",
        [(MIGRATION, TABLE_LINE,
          "    op.create_table(\n        'analytics_event_receipts_typo',\n")],
        "T7-02 件没建 analytics_event_receipts", HEAD_SENTENCES, None,
    ),
    (
        "M2",
        "把 event_id 的 primary_key 关掉（只剩 user_id 一个主键列）：表建了、head 对了、"
        "列也在，但幂等键塌成「一个用户一行」，同一个 event_id 能被落两行——"
        "这条证人拍的就是主键的**组成**，不是建表动作",
        [(MIGRATION, EVENT_ID_LINE,
          "        sa.Column('event_id', sa.String(length=128), primary_key=False),\n")],
        "T7-02 的接收台账主键不是", HEAD_SENTENCES, None,
    ),
    (
        "M3",
        "把 T7-02 的 downgrade 写成 pass（空转）：head 一路退得回去、版本号一格不差，"
        "可表留在库里——下一次正向迁移以为自己在空位上加东西，快照与库长期漂移",
        [(MIGRATION, DROP_LINE, "    pass  # 变异：downgrade 空转\n")],
        "T7-02 的表没退干净", HEAD_SENTENCES, None,
    ),
    (
        "M4",
        "往链顶加一件新迁移（改的是仓库文件之外的东西，所以靠临时件）：批 5 之前梯里钉的是字面 "
        "revision，这种情况会红在 head 句上——那是「测试自己过期了」，不是「迁移坏了」。"
        "现在期望值现读，head 断言必须继续对，红要落在证人句上（红在「表没退干净」那一格）。"
        "forbid 拍的就是这件事：输出里不许出现 head 那两句话",
        [],
        "T7-02 的表没退干净", HEAD_SENTENCES, DRIFT_TEMPLATE,
    ),
    (
        "M5",
        "退 T7-02 时顺手把别人的表一起 drop（迁移边界没切开）：这一格证人拍的是「回退只带走自己那张表」，"
        "它比 head 断言更接近生产事故——版本号对了但别人的数据没了",
        [(MIGRATION, DROP_LINE,
          "    op.drop_table('analytics_event_receipts')\n"
          "    op.drop_table('personal_commitments')\n")],
        "退 T7-02 带走了别人的表", HEAD_SENTENCES, None,
    ),
]

GREEN = [
    (
        "G1",
        "只在 upgrade() 里加一行注释，建表逻辑一字未动：梯不许红在无关改动上，"
        "否则这条闸会在每次整理迁移件注释时拦人",
        [(MIGRATION,
          "def upgrade() -> None:\n" + TABLE_LINE,
          "def upgrade() -> None:\n    # 变异自证 G1：只加注释，建表逻辑未动\n" + TABLE_LINE)],
    ),
    (
        "G2",
        "把主键两列的书写顺序调换（event_id 放在 user_id 前面）：证人比的是主键列的**集合**，"
        "列序换了照样是 (user_id, event_id) 这个幂等键——必须仍绿。"
        "这条绿同时自证 M2 的红不是「文本里找不到那句」造成的",
        [(MIGRATION, USER_ID_LINE + EVENT_ID_LINE, EVENT_ID_LINE + USER_ID_LINE)],
    ),
]


def _apply(edits: list) -> None:
    for path, old, new in edits:
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        assert n == 1, f"{path.name}：锚点命中 {n} 次（要求 1 次）\n{old[:90]!r}"
        if path.suffix == ".py":
            import ast
            ast.parse(src.replace(old, new))
        path.write_text(src.replace(old, new), encoding="utf-8")


def _verify_edits(edits: list) -> str | None:
    for path, old, _new in edits:
        if not path.exists():
            return f"靶文件不存在：{path.name}"
        n = path.read_text(encoding="utf-8").count(old)
        if n != 1:
            return f"{path.name} 锚点命中 {n} 次（要 1）：{old[:60]!r}"
    return None


def chain_head() -> str:
    """临时件的 down_revision：现读链头，不钉字面量。"""
    sys.path.insert(0, str(REPO))
    from backend.core.alembic_chain import head_revision

    return head_revision()


def admin_url() -> str:
    from sqlalchemy.engine import make_url

    url = make_url(os.environ[RT_ENV]).set(database="postgres")
    return url.render_as_string(hide_password=False)


def reset_roundtrip_db() -> tuple[bool, str]:
    """往返梯每次都要一座**空库**（库里有 alembic_version 时这一格直接 skip 前的断言红）。

    走 psycopg 直连管理库，不依赖 `docker exec`：本机（容器 55432 映射）和 CI（service 5432）
    同一套代码，缺哪一头都如实报「未跑」。
    """
    import psycopg

    name = make_rt_name()
    try:
        conn = psycopg.connect(admin_url(), autocommit=True)
    except Exception as exc:  # noqa: BLE001
        return False, f"连不上 {RT_ENV} 的管理库（未跑，不算绿）：{type(exc).__name__}"
    try:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}"')
    except Exception as exc:  # noqa: BLE001
        return False, f"重建空库 {name} 失败（未跑，不算绿）：{type(exc).__name__}"
    finally:
        conn.close()
    return True, name


def make_rt_name() -> str:
    from sqlalchemy.engine import make_url

    return make_url(os.environ[RT_ENV]).database or ""


def run_pytest(node: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(VENV_PY), "-m", "pytest", node, "-q", "--no-header",
         "--tb=line", "-p", "no:randomly"],
        cwd=REPO, capture_output=True, text=True, timeout=2400,
    )
    return proc.returncode, proc.stdout + proc.stderr


def run(target: str) -> tuple[int, str]:
    ok, info = reset_roundtrip_db()
    if not ok:
        return -2, info
    return run_pytest(target)


def is_red(code: int, out: str, target: str, marker: str, forbid: tuple) -> bool:
    """「红」＝**那条证人自己**失败；没跑、skip、收集期报错、红在 head 句上都不算。"""
    if code == -2:
        return False
    if code == 0 or "no tests ran" in out or "1 skipped" in out:
        return False
    if f"FAILED {target}" not in out:
        return False
    if marker not in out:
        return False
    hit_forbid = [sentence for sentence in forbid if sentence in out]
    if hit_forbid:
        print(f"    ↳ 红在了 head 句上（{hit_forbid}），不是证人句——这条自证不成立")
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
        for case in [*MUTATIONS, *GREEN]:
            problem = _verify_edits(case[2])
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        if any(case[5] for case in MUTATIONS) and DRIFT_FILE.exists():
            print(f"坏锚点：临时件还在：{DRIFT_FILE.name}")
            bad += 1
        print(f"锚点校验：{'全部命中一次且变异体合法' if bad == 0 else f'{bad} 条有问题'}"
              f"（共 {len(cases)} 条）")
        return 1 if bad else 0

    if not VENV_PY.is_file():
        print(f"解释器不存在：{VENV_PY}（变异自证要求先备好 .venv）")
        return 2
    if not os.environ.get(RT_ENV):
        print(f"{RT_ENV} 未设置：往返梯整条 skip，所有靶一律记「未跑」，不算绿")
        return 1

    code, out = run(TARGET)
    if code != 0:
        print(f"基线不是绿的（空库＝{out.strip()[-200:]}），先修基线再谈变异：")
        print("  " + "\n  ".join(out.splitlines()[-15:]))
        return 2
    print(f"基线：{TARGET} 绿（空库已重建）")

    paths = sorted({e[0] for case in cases for e in case[2]})
    head = chain_head()
    with tempfile.TemporaryDirectory(prefix="mutate-rt-leg-") as tmp:
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
            DRIFT_FILE.unlink(missing_ok=True)

        red_ok = red_fail = green_ok = green_fail = unrun = 0
        failures: list[str] = []
        try:
            for cid, desc, edits, marker, forbid, create in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(edits)
                if create:
                    assert not DRIFT_FILE.exists(), "临时件已存在，先清掉再跑"
                    DRIFT_FILE.write_text(create % (head, head), encoding="utf-8")
                try:
                    code, out = run(TARGET)
                finally:
                    restore()
                if code == -2:
                    unrun += 1
                    failures.append(cid)
                    print(f"[{cid}] 未跑 ✗  {out.strip()[-120:]}")
                    continue
                if is_red(code, out, TARGET, marker, forbid):
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（exit={code}，名片段 {marker!r} 命中={marker in out}）"
                          f"{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))

            for cid, desc, edits in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(edits)
                try:
                    code, out = run(TARGET)
                finally:
                    restore()
                if code == -2:
                    unrun += 1
                    failures.append(cid)
                    print(f"[{cid}] 未跑 ✗  {out.strip()[-120:]}")
                    continue
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

        print(f"\n计数：红闸 {red_ok} 打红 / {red_fail} 未打红；"
              f"绿闸 {green_ok} 保持绿 / {green_fail} 被误伤；未跑 {unrun}")
        if failures:
            print("未通过：" + ",".join(failures))
        return 1 if (red_fail or green_fail or unrun) else 0


if __name__ == "__main__":
    sys.exit(main())
