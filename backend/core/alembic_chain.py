"""alembic 链形状的唯一推导处（T6-03：head 不许再被抄成第四份字面量）。

为什么要有这个模块：`test_data_export_pg.py`、`test_posthog_erasure_retry_pg.py`、
`test_delivery_audit_pg.py`、`test_canonical_schema.py` 四份实库伴生测各自钉过一句
`HEAD = "f6b8d0e2a4c6"`。那一句的本意是「库必须已经 upgrade 到最新，否则本文件的靶
根本不在这库里」，可它的**字面量**是个第二真源：每落一件迁移就要同步改四处，漏改一处
就把「库落后了」和「这段测试自己过期了」两件事混成一条红。2026-10-19 登记给本票。

现在按两侧不同源重新拍：
- 期望值＝本模块从 `backend/alembic/versions/` 离线推出来的 head（不连库）；
- 实测值＝`select version_num from alembic_version`（连库读）。
两侧对上才叫「库跟上了链」。

留一处的例外并说明理由：`test_canonical_schema.py` 的
`heads == [T401_COMMITMENT_REVISION]` 仍按字面钉着。它拍的**不是**「库升级到最新」，
而是「链的形状＝单头、且新件挂在该挂的位置」——那是需要人显式确认一次的判据，
把它也改成自推导就变成自己跟自己比（恒真）。所以这一处不是漏改的第五个真源，
而是全套闸里唯一那张「要人签字的表」，改它时红的是「链形状变了，请确认」。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "backend" / "alembic.ini"
VERSIONS_DIR = ROOT / "backend" / "alembic" / "versions"


@lru_cache(maxsize=1)
def _script():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))


def head_revision() -> str:
    """链的唯一 head；出现多头直接红（单头线性是本仓既有闸，见 test_canonical_schema）。"""
    heads = _script().get_heads()
    if len(heads) != 1:
        raise AssertionError(
            f"alembic 有 {len(heads)} 个 head（{sorted(heads)}）：链不是单头线性，"
            "先把并行头合并掉再谈 head 的单一真源")
    return heads[0]


def revision_ids() -> tuple[str, ...]:
    """全部 revision id，按依赖序从 base 到 head（离线，不连库）。"""
    script = _script()
    return tuple(rev.revision for rev in reversed(list(script.walk_revisions())))


def is_ancestor(revision: str, other: str) -> bool:
    """`revision` 是否排在 `other` 之前（含相等）。用来拍「本文件依赖的那件已生效」。"""
    ids = revision_ids()
    if revision not in ids or other not in ids:
        raise AssertionError(f"revision 不在链上：{revision} / {other}")
    return ids.index(revision) <= ids.index(other)


def versions_on_disk() -> tuple[str, ...]:
    """迁移文件名（不含 __pycache__）：闸门用它核对「名单里件真的在仓库里」。"""
    return tuple(sorted(p.name for p in VERSIONS_DIR.glob("*.py")))
