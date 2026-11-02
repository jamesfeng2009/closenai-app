"""T7-06：指标字典、核心循环漏斗与投递质量的判据。

四组闸：
1. **字典同源**：`docs/metrics-dictionary.md` 的表格与 `CORE_LOOP_STEPS` 逐格一致；
   每个机器码的 UI 触发点文件真的存在、真的含有那个符号，API 函数真的在 `api.ts` 里。
2. **漏斗不撒谎**：空批次是 0 且标 `no_users`；顺序不成立不算完成；第二次循环
   必须是"重新开始"，不能靠补记一条结果凑出来。
3. **投递质量可对账**：accepted/duplicate/dropped/queued 的恒等式、未知不装 0、
   设备与服务端逐事件对账。
4. **导出不带个人数据**：JSON 与 HTML 里没有 user_id、正文；HTML 插值全部转义。

真库装载（`load_journeys` / `load_delivery`）要 `DATABASE_URL`，本机没有 PG，
所以不在这里假装跑过——见 docs/metrics-dictionary.md 的「未覆盖」。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.services.metrics_export import (
    CORE_LOOP_STEPS,
    DELIVERY_COUNTERS,
    STEP_KEYS,
    TEST_USER_SUFFIXES,
    DeliveryFact,
    Journey,
    build_funnel,
    build_payload,
    payload_json,
    reconcile_sampling,
    render_dashboard_html,
    summarize_delivery,
)

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "metrics-dictionary.md"
API_TS = REPO / "frontend" / "src" / "services" / "api.ts"

BASE = datetime(2026, 11, 2, 9, tzinfo=timezone.utc)


def at(days: float = 0, hours: float = 0) -> datetime:
    return BASE + timedelta(days=days, hours=hours)


def journey(**kwargs) -> Journey:
    defaults = dict(user_id="u1", registered_at=at())
    defaults.update(kwargs)
    return Journey(**defaults)


def window(days: int = 14) -> dict:
    return {"since": at(), "until": at(days)}


def funnel_of(*journeys: Journey, **kwargs) -> dict:
    return build_funnel(journeys, **window(), **kwargs)


# ── 1. 字典同源 ─────────────────────────────────────────────────────────────


def _doc_rows() -> list[dict[str, str]]:
    text = DOC.read_text(encoding="utf-8")
    start = text.index("| 机器码 |")
    rows: list[dict[str, str]] = []
    headers: list[str] = []
    for line in text[start:].splitlines():
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not headers:
            headers = cells
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append(dict(zip(headers, cells)))
    return rows


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", value.replace("`", ""))


def test_dictionary_doc_matches_code() -> None:
    rows = _doc_rows()
    assert len(rows) == len(CORE_LOOP_STEPS) == 7
    for row, step in zip(rows, CORE_LOOP_STEPS):
        assert row["机器码"] == f"`{step.key}`", f"机器码漂了：{row['机器码']} vs {step.key}"
        assert row["中文"] == step.label
        assert row["owner"] == step.owner
        assert row["UI 触发点"].strip("`").endswith(step.ui_trigger.split("/")[-1]), (
            f"{step.key} 的 UI 触发点与字典不一致"
        )
        assert row["API 函数"] == f"`{step.api_symbol}`"
        assert _norm(row["服务端入口"]) == _norm(step.endpoint)
        assert _norm(row["权威表"]) == _norm(step.table)
        assert row["隐私边界"], f"{step.key} 没有隐私边界"


def test_machine_codes_are_unique_and_snake_case() -> None:
    assert len(set(STEP_KEYS)) == len(STEP_KEYS) == 7
    for key in STEP_KEYS:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", key), key
    assert STEP_KEYS[0] == "registered"
    assert STEP_KEYS[-2:] == ("second_cycle", "paid_delivery")
    assert set(DELIVERY_COUNTERS) == {
        "queued", "accepted", "relayed", "dropped", "duplicate",
        "latency_ms_p50", "latency_ms_p95",
    }


def test_every_ui_trigger_and_api_symbol_exists() -> None:
    api_source = API_TS.read_text(encoding="utf-8")
    for step in CORE_LOOP_STEPS:
        if "::" not in step.ui_trigger:
            assert step.key == "second_cycle", step.key
            continue
        path_text, symbol = step.ui_trigger.split("::")
        path = REPO / path_text
        assert path.is_file(), f"{step.key} 的 UI 触发点文件不存在：{path_text}"
        assert symbol in path.read_text(encoding="utf-8"), (
            f"{step.key} 声称的触发符号 {symbol} 不在 {path_text} 里"
        )
        if step.api_symbol == "—":
            assert step.key == "second_cycle"
            continue
        assert f"function {step.api_symbol}" in api_source, (
            f"{step.key} 的 API 函数 {step.api_symbol} 不在 api.ts 里"
        )


def test_test_user_suffix_is_registered() -> None:
    assert TEST_USER_SUFFIXES == ("@closenai.test",)
    assert "@closenai.test" in DOC.read_text(encoding="utf-8")


# ── 2. 漏斗 ─────────────────────────────────────────────────────────────────


def test_empty_batch_is_zero_and_says_so() -> None:
    out = funnel_of()
    assert out["data_status"] == "no_users"
    assert out["total_users"] == 0
    assert all(v == 0 for v in out["totals"].values())
    assert all(v is None for v in out["conversion"].values())
    assert out["by_cohort"] == {}
    assert "demo" not in json.dumps(out).lower()


def test_funnel_counts_ordered_milestones() -> None:
    full = journey(
        user_id="full",
        reflections=(at(0, 1),),
        insights=(at(0, 2),),
        actions=(at(0, 3),),
        outcomes=(at(1),),
    )
    partial = journey(user_id="partial", reflections=(at(0, 1),), insights=(at(0, 2),))
    only_reg = journey(user_id="reg")
    out = funnel_of(full, partial, only_reg)
    assert out["data_status"] == "ok"
    assert out["total_users"] == 3
    assert out["totals"] == {
        "registered": 3,
        "first_reflection": 2,
        "first_insight_with_evidence": 2,
        "first_action": 1,
        "first_outcome": 1,
        "second_cycle": 0,
        "paid_delivery": 0,
    }
    assert out["conversion"]["first_outcome"] == pytest.approx(0.3333, abs=1e-4)


def test_out_of_order_milestones_do_not_complete_a_loop() -> None:
    # 行动早于洞察：四段顺序不成立，结果不算「完成核心循环」。
    broken = journey(
        user_id="broken",
        reflections=(at(0, 1),),
        actions=(at(0, 2),),
        insights=(at(0, 3),),
        outcomes=(at(1),),
    )
    out = funnel_of(broken)
    assert out["totals"]["first_outcome"] == 0
    assert out["totals"]["second_cycle"] == 0


def test_second_cycle_requires_a_restart_not_a_second_outcome() -> None:
    first = journey(
        user_id="one-loop",
        reflections=(at(0),),
        insights=(at(0, 1),),
        actions=(at(0, 2),),
        outcomes=(at(1), at(2)),
    )
    restarted = journey(
        user_id="two-loops",
        reflections=(at(0), at(2, 1)),
        insights=(at(0, 1), at(2, 2)),
        actions=(at(0, 2), at(2, 3)),
        outcomes=(at(1), at(3)),
    )
    out = funnel_of(first, restarted)
    assert out["totals"]["first_outcome"] == 2
    assert out["totals"]["second_cycle"] == 1
    assert out["by_cohort"]  # 有分群行


def test_cohort_window_language_and_platform_splits() -> None:
    inside_zh = journey(user_id="zh", language="zh", platform="apns")
    inside_en = journey(user_id="en", language="en", platform="fcm")
    outside = journey(
        user_id="old", registered_at=at(-30), language="zh", platform="apns"
    )
    out = funnel_of(inside_zh, inside_en, outside)
    assert out["total_users"] == 2
    assert set(out["by_language"]) == {"en", "zh"}
    assert set(out["by_platform"]) == {"apns", "fcm"}
    assert out["by_language"]["zh"]["registered"] == 1
    assert list(out["by_cohort"]) == ["2026-W45"]


def test_test_users_are_excluded_and_counted() -> None:
    real = journey(user_id="real")
    seed = journey(user_id="seed")
    out = funnel_of(real, seed, test_user_ids={"seed"})
    assert out["total_users"] == 1
    assert out["test_users_excluded"] == 1


def test_quality_counters_ride_along() -> None:
    quality = journey(user_id="q", rejected_insights=2, corrections=3, failure_outcomes=1)
    out = funnel_of(quality)
    assert out["quality"] == {
        "rejected_insights": 2,
        "corrections": 3,
        "failure_outcomes": 1,
    }


# ── 3. 投递质量 ─────────────────────────────────────────────────────────────


def test_delivery_mixed_batch_identity() -> None:
    facts = [
        DeliveryFact("e", "accepted", 100),
        DeliveryFact("e", "accepted", 300),
        DeliveryFact("e", "accepted", 200),
        DeliveryFact("e", "duplicate"),
        DeliveryFact("e", "retryable"),
        DeliveryFact("e", "permanent"),
        DeliveryFact("e", "pending"),
    ]
    out = summarize_delivery(facts)
    assert out["accepted"] == 3
    assert out["relayed"] == 3
    assert out["duplicate"] == 1
    assert out["queued"] == 1
    assert out["ledger_dropped"] == 2
    assert out["dropped"] is None, "台账不落 dropped，不能拿 0 冒充"
    assert out["identity_holds"] is None
    assert out["latency_ms_p50"] == 200
    assert out["latency_ms_p95"] == 300

    with_batch = summarize_delivery(facts, relay_dropped=2)
    assert with_batch["dropped"] == 2
    assert with_batch["identity_holds"] is True

    assert summarize_delivery(facts, relay_dropped=5)["identity_holds"] is False


def test_delivery_rejects_unknown_status() -> None:
    with pytest.raises(ValueError):
        summarize_delivery([DeliveryFact("e", "delivered")])


def test_delivery_empty_is_zero_not_error() -> None:
    out = summarize_delivery([])
    assert out["accepted"] == 0 and out["queued"] == 0
    assert out["latency_ms_p50"] is None
    # 只有批次里的 dropped、没有台账行时，恒等式不可判——不能算「对账通过」。
    partial = summarize_delivery([], relay_dropped=2)
    assert partial["identity_holds"] is None


def test_sampling_reconciliation_flags_drift() -> None:
    clean = reconcile_sampling({"a": 3, "b": 1}, {"a": 3, "b": 1})
    assert clean["clean"] is True
    assert clean["device_total"] == clean["server_total"] == 4

    dirty = reconcile_sampling({"a": 3, "b": 1}, {"a": 3, "b": 2})
    assert dirty["clean"] is False
    row = next(r for r in dirty["rows"] if r["event"] == "b")
    assert row["drift"] == 1

    tolerant = reconcile_sampling({"a": 3}, {"a": 4}, tolerance=1)
    assert tolerant["clean"] is True

    missing = reconcile_sampling({"a": 2}, {})
    assert missing["clean"] is False and missing["rows"][0]["drift"] == -2


# ── 4. 导出与看板 ───────────────────────────────────────────────────────────


def test_payload_has_no_personal_data() -> None:
    secret = "我上周决定辞职，这是一段不该出现在指标里的正文"
    solo = journey(
        user_id="u-secret-id",
        reflections=(at(0),),
        insights=(at(0, 1),),
        actions=(at(0, 2),),
        outcomes=(at(1),),
    )
    payload = build_payload(funnel_of(solo))
    text = payload_json(payload)
    assert "u-secret-id" not in text
    assert secret not in text
    assert "反射" not in text
    parsed = json.loads(text)
    assert parsed["funnel"]["totals"]["first_outcome"] == 1


def test_dashboard_zero_state_is_explicit_and_escaped() -> None:
    payload = build_payload(funnel_of())
    html = render_dashboard_html(payload)
    assert "没有真实用户" in html
    assert "不填演示数据" in html
    assert "<table>" in html

    tampered = build_payload(funnel_of())
    tampered["funnel"]["step_labels"]["first_reflection"] = "<script>alert(1)</script>"
    html = render_dashboard_html(tampered)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_dashboard_shows_delivery_unknown_as_unknown() -> None:
    payload = build_payload(
        funnel_of(), summarize_delivery([DeliveryFact("e", "accepted")])
    )
    html = render_dashboard_html(payload)
    assert "未知" in html
    assert "queued" in html and "relayed" in html
