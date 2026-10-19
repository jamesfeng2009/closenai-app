"""docs/data-lineage.md 的双向对账闸（T5-01 起，T1-02 于 2026-10-18 升级为全量双向）。

文档写「导出／删除／审计」的分工，代码写 `PURGE_TARGETS` 与 `export_targets()`——
两边各说各话时，用户拿到的是一份没人核对过的承诺。这不是假想：本文件旧版只核
「文档点名的表还在不在代码里」，于是 T1-01 新建的 `data_export_jobs` 没进台账也不许报错，
而文档从 T5-01 起就写着 `user_consents`／`safety_events`／`push_tokens`「不进导出」、
代码却因为它们派生自 `PURGE_TARGETS` 而把它们**装进了用户的下载包**。两个方向都漏过一次，
所以现在闸拍四件事：

1. 文档存在，且 `user_consents` 的裁决有落点（不写在文档里就等于下一个人把它加回导出清单）；
2. 文档 §二 点名的每张表都还在 `Base.metadata` 里（改名/删表后文档不许继续漂着）；
3. **反向**：代码里的删除清单、导出靶、排除清单、豁免表，每一张都必须出现在 §二，
   且「来源／注销删除／导出」三列与代码逐格相等——新增一张表而不登记 ⇒ 当场红；
4. 正向证人：至少有一行是 `PURGE_TARGETS` 里真实存在的表、`chat_messages` 必须在册——
   防止第 2、3 条在文档被清空后变成「扫空集永远绿」。
"""
import inspect
import re
from pathlib import Path

from backend.models import canonical
from backend.services.export_service import EXPORT_EXCLUDE_REASONS, export_targets
from backend.services.user_service import PURGE_EXEMPT_TABLES, PURGE_TARGETS

ROOT = Path(__file__).resolve().parents[2]
LINEAGE_DOC = ROOT / "docs" / "data-lineage.md"

TABLE_NAME = re.compile(r"`([a-z][a-z0-9_]+)`")
# go-core 的取值不靠人记：canonical 模块里出现的表就是 Go 唯一写入口那批（D-5）。
GO_TABLES = {
    v.__tablename__
    for v in vars(canonical).values()
    if inspect.isclass(v) and hasattr(v, "__tablename__")
}

PURGE_KEYS = {key for _model, key in PURGE_TARGETS}
EXPORT_KEYS = {key for _model, key in export_targets()}
# 台账应当覆盖的全集：删除清单 ∪ 豁免 ∪ 导出排除（inbox_messages 这类两边都不进的也在这里）
EXPECTED = PURGE_KEYS | set(PURGE_EXEMPT_TABLES) | set(EXPORT_EXCLUDE_REASONS)


def _doc_table_rows() -> dict[str, tuple[str, str, str]]:
    """§二 表格 → {表名: (来源, 注销删除, 导出)}。首列反引号里的名字，其余按列位取。"""
    section = LINEAGE_DOC.read_text().split("## 二、逐表登记")[1].split("## 三、")[0]
    rows: dict[str, tuple[str, str, str]] = {}
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 5 or not cells[0].startswith("`"):
            continue  # 表头／分隔行／列数不符的行（列数不符＝写法变了，闸要跟着改，见下面的计数证人）
        names = TABLE_NAME.findall(cells[0])
        assert len(names) == 1, f"§二 一行点名了 {len(names)} 张表：{cells[0]}（一行一表，否则策略列说不清）"
        name = names[0]
        assert name not in rows, f"§二 里 {name} 出现两次，两行策略可能互相矛盾"
        rows[name] = (cells[1], cells[2], cells[3])
    return rows


def _doc_rows() -> list[str]:
    """§二 逐表登记里的表名（供「文档点名的表还在不在」那条闸用）。"""
    return list(_doc_table_rows())


def test_lineage_doc_exists_and_records_consent_ruling():
    assert LINEAGE_DOC.exists(), "docs/data-lineage.md 缺失：T5-01 的导出裁决没有落点"
    text = LINEAGE_DOC.read_text()
    assert "user_consents" in text, "user_consents 的导出/删除裁决未登记"
    assert "PURGE_TARGETS" in text, "登记口径未指向 PURGE_TARGETS"


def test_every_table_named_in_doc_still_exists():
    from backend.database import Base
    import backend.models  # noqa: F401  注册全部表

    known = set(Base.metadata.tables)
    rows = _doc_rows()
    assert rows, "§二 表格解析为空（表头写法变了？闸本身要跟着改，不许静默扫空集）"
    unknown = [t for t in rows if t not in known]
    assert not unknown, f"文档点名的表已不存在：{unknown}"


def test_doc_matches_code_both_ways():
    """双向：台账集合 == 代码集合，且三列逐格相等。

    这一条是 T1-02 的落点。旧闸只拍「文档 → 代码」，所以「代码新增一张表而文档没写」
    永远不会报错；本轮 T1-01 的 `data_export_jobs` 就是这么漏掉的，而同一时间导出侧的
    反向漂移（文档说排除、代码在导出）已经真实把审计表装进了用户的 zip。
    """
    rows = _doc_table_rows()
    documented = set(rows)
    assert documented == EXPECTED, (
        f"未登记={sorted(EXPECTED - documented)} 台账里代码没有={sorted(documented - EXPECTED)}"
    )
    for name, (source, delete_policy, export_policy) in sorted(rows.items()):
        want_source = "go-core" if name in GO_TABLES else "py-api"
        assert source == want_source, f"{name} 来源列={source}，代码里它是 {want_source} 写的"
        want_delete = "✅" if name in PURGE_KEYS else ("豁免" if name in PURGE_EXEMPT_TABLES else "❌")
        assert delete_policy == want_delete, (
            f"{name} 注销删除列={delete_policy}，代码口径是 {want_delete}"
        )
        want_export = "✅" if name in EXPORT_KEYS else "❌"
        assert export_policy == want_export, (
            f"{name} 导出列={export_policy}，代码口径是 {want_export}"
        )


def test_doc_has_no_unexplained_export_exclusion():
    """排除清单的每一条都要在台账里落到一行，且在文档里说得出理由（每格非空、长度够）。"""
    rows = _doc_table_rows()
    for name in EXPORT_EXCLUDE_REASONS:
        assert name in rows, f"{name} 被排除出导出却没进台账"
        assert rows[name][2] == "❌", f"{name} 在排除清单里，台账导出列却写成 ✅"
    for name, (source, delete_policy, export_policy) in rows.items():
        assert source and delete_policy and export_policy, f"{name} 的策略列有空格"
        # 第 5 列是理由：一行没有理由的策略等于「我抄了代码」，对账价值为零
        section = LINEAGE_DOC.read_text().split("## 二、逐表登记")[1].split("## 三、")[0]
        line = [ln for ln in section.splitlines() if f"`{name}`" in ln and ln.startswith("|")]
        assert line, f"{name} 的行找不到（上一道闸解析它却解析不到原文？）"
        reason = [c.strip() for c in line[0].strip("|").split("|")][4]
        assert len(reason) >= 6, f"{name} 的理由列为空或过短：{reason!r}"


def test_purged_tables_are_witnessed_in_doc():
    """正向证人：登记在 PURGE_TARGETS 的表至少有一张出现在文档 §二，且 chat_messages 必须在。"""
    rows = set(_doc_rows())
    assert "chat_messages" in rows
    assert rows & {key for _, key in PURGE_TARGETS}, "文档与 PURGE_TARGETS 零交集＝对账失效"
    # 计数证人：台账是全量而不是样例（43 张与代码集合同量级，掉到个位数就是解析器瞎了）
    assert len(rows) >= len(EXPECTED), f"§二 只解析出 {len(rows)} 行，代码侧有 {len(EXPECTED)} 张"
