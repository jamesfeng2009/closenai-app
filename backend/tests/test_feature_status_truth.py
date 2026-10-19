"""T0-05 功能状态台账真实性闸（Baseline §15 六态 / §16 十行矩阵）。

被验对象：`docs/feature-status.json`（唯一事实源）与它生成的 `docs/feature-status.md`。
判据全部住在 `scripts/feature_status.py::verify()`，本文件只做两件事：

① 正面：现账必须零违例，且 md 与 JSON 同源（生成物过期＝PR 没重跑脚本）；
② 反面：**在内存里**把台账改虚高（`deepcopy`，JSON 一个字节都不动），断言每条阶梯
   都按设计打红。反面用例是本批的重点——台账这种「人写状态、机器背书」的东西，
   最容易死在「判据看起来有、其实什么都判不动」。

反面用例刻意全部用仓库里**真实存在**的坏料，而不是随手编一个字符串：
- `clearPushToken`：api.ts 里定义着、全前端零调用方（`orphanEndpoints.test.ts` 早就把
  它登记成「有意不接」），拿它冒充调用点就是「有 API 没接线」这一类谎的原型；
- `/api/v1/cron/follow-up`：契约台账里有、`deploy/schedule/crontab` 里没排程，
  拿它冒充 PRODUCTION_ENABLED 的调度证据就是「写了调度其实没人跑」的原型。

变异自证：`scripts/mutate_t005_feature_status_gates.py`（改真文件、跑本文件、要求打红，
再用 copy2 备份字节级写回）。
"""

import copy
from datetime import date

import pytest

from scripts.feature_status import (
    CLAUSE_RE,
    LEDGER,
    MARKDOWN,
    ROOT,
    api_wrappers,
    cutover_defaults,
    has_tests,
    render,
    scheduled_crons,
    verify,
)

BASELINE = ROOT / "CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md"
MATRIX_ROWS_16 = [
    "反思记录",
    "历史记录",
    "记忆管理",
    "模式/洞察",
    "主动消息",
    "第一周成长剧本",
    "Life Archive PDF",
    "订阅状态",
    "数据导出/删除",
    "隐私/条款",
]
# 真实坏料（见模块 docstring）：一个零调用封装、一个未排程的契约 cron 路径
DEAD_WRAPPER = "clearPushToken"
UNSCHEDULED_CRON = "/api/v1/cron/follow-up"
DATED = "2026-10-18"


@pytest.fixture(scope="module")
def ledger() -> dict:
    import json

    return json.loads(LEDGER.read_text(encoding="utf-8"))


def feat(ledger: dict, fid: str) -> dict:
    for f in ledger["features"]:
        if f["id"] == fid:
            return f
    raise AssertionError(f"台账里没有 feature：{fid}")


def mutated_errors(ledger: dict, fid: str, **changes) -> list[str]:
    """deepcopy 后只改这一条 feature 的点名字段，返回该条触发的违例。"""
    dup = copy.deepcopy(ledger)
    target = feat(dup, fid)
    target.update(changes)
    return [e for e in verify(dup) if e.startswith(f"{fid}:")]


# ── ① 正面：现账与证据一致 ──────────────────────────────────────────────


def test_current_ledger_has_no_evidence_violation(ledger):
    errors = verify(ledger)
    assert errors == [], "功能状态台账有判据违例：\n  - " + "\n  - ".join(errors)


def test_markdown_is_rendered_from_json(ledger):
    """md 必须逐字等于脚本现算的生成物——手改 md 或改完 JSON 不重跑，都在这里红。"""
    assert MARKDOWN.exists(), "docs/feature-status.md 不存在（跑 scripts/feature_status.py 生成）"
    assert MARKDOWN.read_text(encoding="utf-8") == render(ledger, verify(ledger)), (
        "docs/feature-status.md 与 JSON 证据不同源：重跑 .venv/bin/python scripts/feature_status.py 并把 md 一起提"
    )


def test_ledger_covers_baseline_matrix_ten_rows(ledger):
    """§16 十行不是台账自己抄一份就完事：行名要逐个在 Baseline 原文里出现。"""
    assert ledger["matrix_rows"] == MATRIX_ROWS_16, "台账矩阵行序/行名与 Baseline §16 对不上"
    baseline = BASELINE.read_text(encoding="utf-8")
    section = baseline[baseline.index("## 16. 功能验收矩阵"):]
    section = section[: section.index("## 17.")]
    missing = [row for row in MATRIX_ROWS_16 if row not in section]
    assert not missing, f"§16 原文里找不到这些行名（矩阵被改过，台账没跟上）：{missing}"
    covered = {f["matrix_row"] for f in ledger["features"]}
    assert covered == set(MATRIX_ROWS_16), f"矩阵行未被 feature 覆盖：{set(MATRIX_ROWS_16) - covered}"


def test_no_feature_claims_production_enabled_while_cutover_is_zero(ledger):
    """切流键默认 0＝灰度零流量，任何人不许在这一栏写 PRODUCTION_ENABLED。

    正面自证：这条闸不是空转——`go-core/internal/config/config.go` 里确实有默认 0 的
    `CLOSEN_CUTOVER_*` 键，且现账确实有功能带这些键（下面断言拿到非空集）。
    """
    zero_keys = {k for k, v in cutover_defaults().items() if v == 0}
    assert zero_keys, "config.go 里没有默认 0 的切流键：这条闸已经扫空集，判据该重看"
    declared = {f.get("cutover_key") for f in ledger["features"]} - {None}
    blocked = declared & zero_keys
    assert blocked, "台账没有任何功能带灰度键：这条闸现在管不到东西"
    for f in ledger["features"]:
        if f.get("cutover_key") in zero_keys:
            assert f["state"] != "PRODUCTION_ENABLED", (
                f"{f['id']} 的切流键 {f['cutover_key']} 默认 0，状态却写成 PRODUCTION_ENABLED"
            )


# ── ② 反面：三条故意写错的 state，各红一次 ────────────────────────────


def test_wired_with_uncalled_wrapper_is_red(ledger):
    """WIRED 但点名一个零调用方封装 ⇒ 红（§15 末句：有 API 没接线只能算 IMPLEMENTED）。"""
    assert DEAD_WRAPPER in api_wrappers(), f"{DEAD_WRAPPER} 已不在 api.ts：坏料要换"
    errors = mutated_errors(
        ledger, "reflect.record",
        state="WIRED", frontend_calls=[DEAD_WRAPPER],
    )
    assert any("零调用方" in e for e in errors), f"零调用封装冒充调用点却全绿：{errors}"


def test_wired_without_any_call_site_is_red(ledger):
    errors = mutated_errors(ledger, "reflect.record", state="WIRED", frontend_calls=[])
    assert any("前端调用点" in e for e in errors), f"WIRED 且 frontend_calls 为空却全绿：{errors}"


def test_wired_with_renamed_wrapper_is_red(ledger):
    """调用点写成 api.ts 里已经不存在的封装名（改名/删除）⇒ 存在性判据先咬。"""
    errors = mutated_errors(
        ledger, "memory.manage", state="WIRED",
        frontend_calls=["listMemories", "listMemoriesV2"],
    )
    assert any("listMemoriesV2" in e and "已不存在" in e for e in errors), (
        f"不存在的封装名冒充调用点却全绿：{errors}"
    )


def test_production_enabled_with_unscheduled_cron_is_red(ledger):
    """PRODUCTION_ENABLED 但 cron 不在 `deploy/schedule/crontab` ⇒ 红（工作单点名的第 2 条）。"""
    assert UNSCHEDULED_CRON not in scheduled_crons(), f"{UNSCHEDULED_CRON} 已排程：坏料要换"
    errors = mutated_errors(
        ledger, "proactive.message",
        state="PRODUCTION_ENABLED", production_evidence=f"{DATED} 宿主 crontab 已安装（VPS 实测）",
        cron=[UNSCHEDULED_CRON],
    )
    assert any(UNSCHEDULED_CRON in e and "crontab" in e for e in errors), (
        f"未排程的 cron 冒充生产调度却全绿：{errors}"
    )


def test_production_enabled_without_dated_evidence_is_red(ledger):
    """cron 确实排了程，但没带日期的上线出处 ⇒ 仍然红（调度声明在库里，装没装是另一件事）。"""
    f = feat(ledger, "proactive.message")
    scheduled = [c for c in f["cron"] if c in scheduled_crons()]
    assert scheduled, "现账的 cron 一条都没排程：这条反面用例的前置坏了"
    errors = mutated_errors(ledger, "proactive.message", state="PRODUCTION_ENABLED", production_evidence="")
    assert any("production_evidence" in e for e in errors), f"缺上线出处却全绿：{errors}"


def test_production_enabled_behind_zero_cutover_is_red(ledger):
    """灰度键默认 0 的功能写 PRODUCTION_ENABLED ⇒ 红，且红的必须是切流那一句。"""
    f = feat(ledger, "journey.week1")
    assert f.get("cutover_key"), "journey.week1 没有切流键：坏料要换"
    errors = mutated_errors(
        ledger, "journey.week1", state="PRODUCTION_ENABLED",
        production_evidence=f"{DATED} 排程与契约均已核对",
    )
    assert any(f["cutover_key"] in e and "灰度" in e for e in errors), (
        f"切流默认 0 却能宣称 PRODUCTION_ENABLED：{errors}"
    )


def test_e2e_verified_with_missing_or_empty_test_file_is_red(ledger):
    """E2E_VERIFIED 指向不存在的测试文件、或指向一个没有测试声明的真文件 ⇒ 各红一次。"""
    missing = mutated_errors(ledger, "life_archive.pdf", e2e_tests=["backend/tests/test_does_not_exist.py"])
    assert any("指向不存在" in e for e in missing), f"不存在的测试文件冒充端到端证据却全绿：{missing}"

    hollow = mutated_errors(ledger, "life_archive.pdf", e2e_tests=["backend/services/cron_service.py"])
    assert any("没有测试声明" in e for e in hollow), (
        f"一个没有 test_ 的真文件冒充端到端证据却全绿：{hollow}"
    )


def test_e2e_verified_without_count_or_date_is_red(ledger):
    """「E2E 过了」这句话必须带非零实测计数与不晚于今天的日期，否则没有出处。"""
    no_count = mutated_errors(ledger, "journey.week1", verified_by="本地跑过，都过了")
    assert any("verified_by" in e for e in no_count), f"没有计数的 E2E 结论却全绿：{no_count}"

    zero_count = mutated_errors(ledger, "journey.week1", verified_by="0 passed")
    assert any("verified_by" in e for e in zero_count), f"「0 passed」也能当证据：计数判据空转：{zero_count}"

    future = mutated_errors(ledger, "journey.week1", verified_on="2027-01-01")
    assert any("verified_on" in e for e in future), f"未来的验证日期却全绿：{future}"

    bogus = mutated_errors(ledger, "journey.week1", verified_on="上周")
    assert any("verified_on" in e for e in bogus), f"非日期文本却通过日期判据：{bogus}"


def test_state_outside_six_states_is_red(ledger):
    errors = mutated_errors(ledger, "export.data", state="DONE")
    assert any("六态" in e for e in errors), f"六态之外的状态被放过：{errors}"


def test_value_measured_without_metric_evidence_is_red(ledger):
    errors = mutated_errors(
        ledger, "growth.history", state="VALUE_MEASURED",
        value_metric="", metric_evidence="",
    )
    assert any("value_metric" in e for e in errors), f"没有指标也能宣称 VALUE_MEASURED：{errors}"
    only_number = mutated_errors(
        ledger, "growth.history", state="VALUE_MEASURED",
        value_metric="历史查看率", metric_evidence="查看率 41%",
    )
    assert any("metric_evidence" in e for e in only_number), (
        f"指标证据没有日期也能过：{only_number}"
    )


def test_missing_next_gap_is_red(ledger):
    """没到顶的态必须写清「还差什么」——这是台账存在的意义，也是它最容易被写空的一栏。"""
    errors = mutated_errors(ledger, "account.delete", next_gap="")
    assert any("next_gap" in e for e in errors), f"缺口留空却全绿：{errors}"


def test_static_delivery_needs_real_assets(ledger):
    """§16 行 10 是「静态页面/链接 → 发布资产」：这条链没有端点，资产就是它的接线证据。"""
    assert feat(ledger, "privacy.terms").get("delivery") == "static", (
        "privacy.terms 不再按静态发布建模：这条判据的靶没了，该重看台账形状"
    )
    missing = mutated_errors(ledger, "privacy.terms", assets=["site/src/pages/privacy.astro", "site/src/pages/ko/privacy.astro"])
    assert any("site/src/pages/ko/privacy.astro" in e for e in missing), (
        f"点名不存在的发布资产却全绿：{missing}"
    )
    empty = mutated_errors(ledger, "privacy.terms", assets=[])
    assert any("发布资产" in e for e in empty), f"static 功能没有任何资产却全绿：{empty}"


def test_feature_ids_unique_and_row_names_known(ledger):
    ids = [f["id"] for f in ledger["features"]]
    assert len(ids) == len(set(ids)), f"feature id 重复：{ids}"
    assert feat(ledger, "consent.ledger")["state"] == "IMPLEMENTED", (
        "consent.ledger 的前端管理屏仍未立项，状态不该高于 IMPLEMENTED"
    )


# ── ③ T6-01：内部能力登记面（无用户入口，状态封顶 IMPLEMENTED）────────────


def cap(ledger: dict, cid: str) -> dict:
    for c in ledger.get("internal_capabilities", []):
        if c["id"] == cid:
            return c
    raise AssertionError(f"台账里没有内部能力：{cid}")


def mutated_cap_errors(ledger: dict, cid: str, **changes) -> list[str]:
    dup = copy.deepcopy(ledger)
    cap(dup, cid).update(changes)
    return [e for e in verify(dup) if e.startswith(f"{cid}:")]


def test_aiclient_is_registered_as_internal_capability(ledger):
    """T6-01 选 A：`internal/aiclient` 登记为「内部能力，未启用」。

    这条是**正向证人**——登记面是一条「不再出现」型的归零判据，扫空集也算绿，
    所以先要求这一条真的存在、真的标 IMPLEMENTED、真的点名了那个非生产调用方。
    「生产零调用方」这句话的另一半住在 `go-core/internal/aiclient/production_caller_ledger_test.go`
    （go/parser 现读 AST）：两侧不同源，任一侧漂了都会红。
    """
    entry = cap(ledger, "ai.grpc.client")
    assert entry["state"] == "IMPLEMENTED", "ai.grpc.client 状态被抬高：内部能力没有用户入口"
    assert entry["probe_tools"], "probe_tools 为空 ⇒ 台账不再回答「今天谁在引用这个包」"
    assert entry["impl"], "impl 为空 ⇒ 登记的接口实现指不回任何真实代码"


def test_internal_capability_cannot_claim_wired(ledger):
    """把内部能力标成 WIRED ⇒ 红。WIRED 的判据是「前端有调用点」，按定义它没有。"""
    errors = mutated_cap_errors(ledger, "ai.grpc.client", state="WIRED")
    assert any("封顶 IMPLEMENTED" in e for e in errors), f"内部能力能冒充 WIRED 却全绿：{errors}"


def test_internal_capability_expired_review_date_is_red(ledger):
    """`review_by` 过期 ⇒ 红。这条是「90 天内没调用方就删」这句话的牙齿：
    没有它，登记会变成一条永久绿的死账（tasks.md 里那种勾了没生效的形状）。
    """
    errors = mutated_cap_errors(ledger, "ai.grpc.client", review_by="2026-01-01")
    assert any("已过期" in e for e in errors), f"过期登记照样绿：{errors}"
    bogus = mutated_cap_errors(ledger, "ai.grpc.client", review_by="下个季度")
    assert any("YYYY-MM-DD" in e for e in bogus), f"非日期文本通过处置日期判据：{bogus}"


def test_internal_capability_needs_clause_and_deletion_action(ledger):
    """reason 必须指回条款、review_action 必须写明删什么——否则这条登记没法复查。"""
    no_clause = mutated_cap_errors(ledger, "ai.grpc.client", reason="没人用，先记一下")
    assert any("条款" in e for e in no_clause), f"没有出处的理由照样绿：{no_clause}"
    vague = mutated_cap_errors(ledger, "ai.grpc.client", review_action="到时候再看一眼")
    assert any("删除" in e for e in vague), f"处置动作不写删除照样绿：{vague}"


def test_internal_capability_impl_files_must_exist(ledger):
    errors = mutated_cap_errors(
        ledger, "ai.grpc.client",
        impl=["go-core/internal/aiclient/grpc.go", "go-core/internal/aiclient/deleted_gopher.go"],
    )
    assert any("deleted_gopher.go" in e for e in errors), f"点名不存在的实现文件却全绿：{errors}"
    hollow = mutated_cap_errors(ledger, "ai.grpc.client", tests=["backend/services/cron_service.py"])
    assert any("没有测试声明" in e for e in hollow), f"没有测试声明的真文件冒充测试面却全绿：{hollow}"


# ── ④ 现账字段闸：读**真实 JSON 里的那个值**，与上面的内存反面用例不同源 ──────
#
# ②③ 那批用例改的是 deepcopy，`docs/feature-status.json` 一个字节都没动——
# 所以它们证的是「判据判得动」，证不了「现账是对的」。变异脚本要改的是真文件，
# 红的就必须是下面这六条各自那一句（scripts/mutate_t601_aiclient_ledger_gates.py 的 M2…M7）。


def cap_entry(ledger: dict, cid: str = "ai.grpc.client") -> dict:
    return cap(ledger, cid)


def test_registered_capability_state_stays_capped(ledger):
    """现账里这条登记的态必须真的停在 IMPLEMENTED（不是判据判不动 WIRED，是台账没写 WIRED）。"""
    assert cap_entry(ledger)["state"] == "IMPLEMENTED", (
        "internal/aiclient 已登记为内部能力：没有用户入口就封顶 IMPLEMENTED（Baseline §15 末句／§10.4）。"
        "若真的接进了生产，请连同 M… 这批闸与 T6-01 一起改判，别只改台账。"
    )


def test_registered_capability_review_date_is_still_future(ledger):
    review = cap_entry(ledger)["review_by"]
    assert date.fromisoformat(review) > date.today(), (
        f"处置日期 {review} 已到期 ⇒ 按 review_action 删除，或另立裁决改日期；过期登记不许继续绿"
    )


def test_registered_capability_review_action_still_deletes(ledger):
    action = cap_entry(ledger)["review_action"]
    assert "删除" in action, f"到期动作里没有「删除」＝90 天条款成了空话：{action[:60]!r}"


def test_registered_capability_reason_still_cites_clauses(ledger):
    reason = cap_entry(ledger)["reason"]
    hits = CLAUSE_RE.findall(reason)
    assert len(hits) >= 2, (
        f"reason 里的条款锚点只剩 {len(hits)} 处（本条登记靠 §10.4 与 §21 两条撑起，"
        "锚点没了就没人能复查这句「实现 ≠ 上线」说的是哪条规矩）"
    )


def test_registered_capability_impl_points_at_real_files(ledger):
    missing = [rel for rel in cap_entry(ledger)["impl"] if not (ROOT / rel).exists()]
    assert not missing, f"impl 点名了不存在的实现文件：{missing}"


def test_registered_capability_tests_are_declared_files(ledger):
    hollow = [rel for rel in cap_entry(ledger)["tests"] if not has_tests(ROOT / rel)]
    assert not hollow, f"tests 里这些文件没有测试声明，不能当证据：{hollow}"
