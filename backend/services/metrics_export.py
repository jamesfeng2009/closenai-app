"""T7-06：核心循环指标字典、漏斗导出与投递质量汇总。

判决口径（写在这里省得每次重议）：

1. **权威源是业务表，不是分析事件**。注册、反思、有证据洞察、行动、结果、第二次循环、
   付费交付这七格，全部从各自的业务表数出来（见 `CORE_LOOP_STEPS.table`）。
   分析事件（PostHog / receipts）只回答「事件有没有送到」，不参与产品漏斗——
   用户撤回分析同意时，产品事实仍然成立，漏斗不该跟着塌一块。
2. **不造数**。没有用户时每个格子都返回 0，并带 `data_status="no_users"`；
   没有批批次日志时 `dropped` 返回 `None`（未知），不拿 0 冒充。
3. **测试用户不进正式指标**。约定后缀 `@closenai.test`（`TEST_USER_SUFFIXES`），
   装载层按它排除并在 `test_users_excluded` 里报出排除条数。
4. **只数不读**。这里只取时间戳与计数；正文、记忆、对话内容一概不读（隐私边界见字典）。

本模块的纯函数（`build_funnel`／`summarize_delivery`／`reconcile_sampling`／
`render_dashboard_html`）不碰数据库，测试直接喂合成批次；`load_*` 才需要真库。
"""
from __future__ import annotations

import html
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence

# ── 字典 ─────────────────────────────────────────────────────────────────────

# 测试用户约定的邮箱后缀：装载层按它把种子/联调账号挡在正式指标之外。
TEST_USER_SUFFIXES = ("@closenai.test",)

# 结果词表（frontend/src/types/index.ts:358 ActionResultChoice）。
FAILURE_RESULTS = frozenset({"worse"})


@dataclass(frozen=True)
class StepDef:
    key: str
    label: str
    owner: str
    ui_trigger: str
    api_symbol: str
    endpoint: str
    table: str
    privacy: str


CORE_LOOP_STEPS: tuple[StepDef, ...] = (
    StepDef(
        key="registered",
        label="注册（分群锚点）",
        owner="BE",
        ui_trigger="frontend/src/screens/AuthScreen.tsx::register",
        api_symbol="register",
        endpoint="POST /api/v1/auth/register",
        table="users.created_at",
        privacy="只取注册时间、语言与状态；邮箱仅用于排除测试账号，不进入导出",
    ),
    StepDef(
        key="first_reflection",
        label="首次反思",
        owner="FE+BE",
        ui_trigger="frontend/src/screens/ReflectScreen.tsx::saveReflection",
        api_symbol="saveReflection",
        endpoint="POST /api/v1/reflections",
        table="growth_records.record_type='reflection'",
        privacy="只数条数与最早时间，不读 content / related_memory_ids",
    ),
    StepDef(
        key="first_insight_with_evidence",
        label="首条有证据洞察",
        owner="GO+FE",
        ui_trigger="frontend/src/screens/InsightsScreen.tsx::evidence_count",
        api_symbol="listInsights",
        endpoint="GET /api/v1/insights",
        table="insights + insight_evidence（status='published'）",
        privacy="只认「有证据边且已发布」，不读 statement",
    ),
    StepDef(
        key="first_action",
        label="首次行动",
        owner="FE+GO",
        ui_trigger="frontend/src/screens/InsightsScreen.tsx::submitAction",
        api_symbol="createAction",
        endpoint="POST /api/v1/goals/{goal_id}/actions",
        table="personal_commitments.created_at",
        privacy="只数条数与时间，不读 title / description",
    ),
    StepDef(
        key="first_outcome",
        label="到期结果",
        owner="FE+GO",
        ui_trigger="frontend/src/screens/GrowthRecordsScreen.tsx::postActionOutcome",
        api_symbol="postActionOutcome",
        endpoint="POST /api/v1/actions/{action_id}/outcomes",
        table="outcomes（origin='user_statement'）",
        privacy="只计数与 result 词表分布，不读 description / note",
    ),
    StepDef(
        key="second_cycle",
        label="第二次核心循环",
        owner="PLAT",
        ui_trigger="派生（无单独 UI 事件）",
        api_symbol="—",
        endpoint="—（本模块派生）",
        table="growth_records + insights + personal_commitments + outcomes",
        privacy="派生指标，只含时间戳比较",
    ),
    StepDef(
        key="paid_delivery",
        label="付费交付",
        owner="BE",
        ui_trigger="frontend/src/services/iap.ts::purchaseUpdatedListener",
        api_symbol="verifyReceipt",
        endpoint="POST /api/v1/iap/verify",
        table="user_entitlements + share_exports",
        privacy="只数交付是否成立，不读导出 payload",
    ),
)

STEP_KEYS: tuple[str, ...] = tuple(s.key for s in CORE_LOOP_STEPS)

# 投递质量六格：queued / accepted / relayed / dropped / duplicate / latency。
# accepted 与 relayed 是同一事实的两个名字（外发成功），保留两个名字是为了与
# 既有响应体 {relayed, dropped} 和逐事件 ack {accepted, duplicate, …} 都对得上。
DELIVERY_COUNTERS: tuple[str, ...] = (
    "queued",
    "accepted",
    "relayed",
    "dropped",
    "duplicate",
    "latency_ms_p50",
    "latency_ms_p95",
)


# ── 漏斗 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Journey:
    """一名用户的核心循环事实（只有时间戳与计数）。"""

    user_id: str
    registered_at: datetime
    language: str = "unknown"
    platform: str = "unknown"
    reflections: tuple[datetime, ...] = ()
    insights: tuple[datetime, ...] = ()
    actions: tuple[datetime, ...] = ()
    outcomes: tuple[datetime, ...] = ()
    paid_deliveries: tuple[datetime, ...] = ()
    rejected_insights: int = 0
    corrections: int = 0
    failure_outcomes: int = 0


@dataclass
class _Milestones:
    registered: datetime
    first_reflection: datetime | None = None
    first_insight_with_evidence: datetime | None = None
    first_action: datetime | None = None
    first_outcome: datetime | None = None
    second_cycle: datetime | None = None
    paid_delivery: datetime | None = None

    def as_counts(self) -> dict[str, int]:
        return {key: 1 if getattr(self, key) is not None else 0 for key in STEP_KEYS}


def _first(values: Sequence[datetime]) -> datetime | None:
    return min(values) if values else None


def _first_loop_outcome(journey: Journey) -> datetime | None:
    """最早一次「反思→洞察→行动→结果」四段齐全且顺序成立的结果时间。"""
    reflection = _first(journey.reflections)
    insight = _first(journey.insights)
    action = _first(journey.actions)
    if reflection is None or insight is None or action is None:
        return None
    if not (reflection <= insight <= action):
        return None
    for outcome in sorted(journey.outcomes):
        if action < outcome:
            return outcome
    return None


def _second_cycle(journey: Journey, first_outcome: datetime | None) -> datetime | None:
    """第一次结果之后，反思/洞察/行动三件**都重新出现过**的最早结果时间。

    只看「又多了一条结果」是不够的：补记一条旧行动的结果也会让结果数变成 2，
    但循环并没有重新开始。所以这里要求三段在 (first_outcome, outcome) 内各出现一次。
    """
    if first_outcome is None:
        return None
    for outcome in sorted(journey.outcomes):
        if outcome <= first_outcome:
            continue
        window = (first_outcome, outcome)
        if any(window[0] < t <= window[1] for t in journey.reflections) and any(
            window[0] < t <= window[1] for t in journey.insights
        ) and any(window[0] < t <= window[1] for t in journey.actions):
            return outcome
    return None


def milestones(journey: Journey) -> _Milestones:
    first_outcome = _first_loop_outcome(journey)
    return _Milestones(
        registered=journey.registered_at,
        first_reflection=_first(journey.reflections),
        first_insight_with_evidence=_first(journey.insights),
        first_action=_first(journey.actions),
        first_outcome=first_outcome,
        second_cycle=_second_cycle(journey, first_outcome),
        paid_delivery=_first(journey.paid_deliveries),
    )


def _iso_week(moment: datetime) -> str:
    year, week, _ = moment.isocalendar()
    return f"{year}-W{week:02d}"


def _date(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).date().isoformat()


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _tally(rows: Iterable[dict[str, int]]) -> dict[str, int]:
    out = {key: 0 for key in STEP_KEYS}
    for row in rows:
        for key in STEP_KEYS:
            out[key] += row[key]
    return out


def _group(
    journeys: Sequence[Journey],
    key_of,
) -> dict[str, dict]:
    buckets: dict[str, list[Journey]] = {}
    for journey in journeys:
        buckets.setdefault(key_of(journey), []).append(journey)
    out: dict[str, dict] = {}
    for key in sorted(buckets):
        rows = [milestones(j).as_counts() for j in buckets[key]]
        totals = _tally(rows)
        out[key] = {
            "registered": totals["registered"],
            "counts": totals,
            "second_cycle_rate": _ratio(totals["second_cycle"], totals["registered"]),
        }
    return out


def build_funnel(
    journeys: Iterable[Journey],
    *,
    since: datetime,
    until: datetime,
    test_user_ids: Iterable[str] = (),
) -> dict:
    """按注册窗口取分群，导出总量、转化与切片。纯函数，不碰库。"""
    excluded = set(test_user_ids)
    cohort: list[Journey] = []
    excluded_hits = 0
    for journey in journeys:
        if journey.user_id in excluded:
            excluded_hits += 1
            continue
        if since <= journey.registered_at < until:
            cohort.append(journey)

    rows = [milestones(j).as_counts() for j in cohort]
    totals = _tally(rows)
    return {
        "cohort": {"since": _date(since), "until": _date(until)},
        "data_status": "no_users" if not cohort else "ok",
        "total_users": len(cohort),
        "totals": totals,
        "conversion": {
            key: _ratio(totals[key], totals["registered"]) for key in STEP_KEYS
        },
        "step_labels": {s.key: s.label for s in CORE_LOOP_STEPS},
        "by_cohort": _group(cohort, lambda j: _iso_week(j.registered_at)),
        "by_language": _group(cohort, lambda j: j.language or "unknown"),
        "by_platform": _group(cohort, lambda j: j.platform or "unknown"),
        "quality": {
            # 失败出口与纠正：它们不进漏斗，但必须和漏斗一起被看到。
            "rejected_insights": sum(j.rejected_insights for j in cohort),
            "corrections": sum(j.corrections for j in cohort),
            "failure_outcomes": sum(j.failure_outcomes for j in cohort),
        },
        "test_users_excluded": excluded_hits,
        "authoritative_source": sorted({s.table for s in CORE_LOOP_STEPS}),
    }


# ── 投递质量 ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DeliveryFact:
    """逐事件投递结果。status 取 accepted/duplicate/retryable/permanent/pending。"""

    event: str
    status: str
    latency_ms: int | None = None


_DROPPED_STATUSES = frozenset({"retryable", "permanent"})


def _percentile(values: Sequence[int], pct: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(pct / 100 * len(ordered)) - 1
    return ordered[max(0, min(rank, len(ordered) - 1))]


def summarize_delivery(
    facts: Iterable[DeliveryFact],
    *,
    relay_dropped: int | None = None,
) -> dict:
    """六格投递质量。

    `relay_dropped` 是**批次响应**里的 dropped 计数（T7-02 的台账故意不落 dropped 行，
    见 backend/models/analytics.py 模块头）。没给就返回 None——未知不等于 0。
    """
    counters = {"queued": 0, "accepted": 0, "duplicate": 0, "retryable": 0, "permanent": 0}
    latencies: list[int] = []
    total = 0
    for fact in facts:
        total += 1
        if fact.status == "pending":
            counters["queued"] += 1
        elif fact.status in ("accepted", "duplicate", "retryable", "permanent"):
            counters[fact.status] += 1
        else:
            raise ValueError(f"未知投递状态：{fact.status!r}")
        if fact.latency_ms is not None and fact.status == "accepted":
            latencies.append(fact.latency_ms)

    ledger_dropped = counters["retryable"] + counters["permanent"]
    # dropped 只认批次响应：台账里没有这一格（T7-02 的设计），拿不到就是未知。
    dropped = relay_dropped
    out = {
        "queued": counters["queued"],
        "accepted": counters["accepted"],
        "relayed": counters["accepted"],
        "duplicate": counters["duplicate"],
        "dropped": dropped,
        "latency_ms_p50": _percentile(latencies, 50),
        "latency_ms_p95": _percentile(latencies, 95),
        "observed_total": total,
        "ledger_dropped": ledger_dropped,
        "relay_dropped_supplied": relay_dropped is not None,
        # 台账为空时恒等式无从谈起：只有 dropped 一格不可算「对账通过」。
        "identity_holds": (
            counters["accepted"] + counters["duplicate"] + dropped + counters["queued"]
            == total
            if dropped is not None and total > 0
            else None
        ),
    }
    return out


def reconcile_sampling(
    device_counts: dict[str, int],
    server_counts: dict[str, int],
    *,
    tolerance: int = 0,
) -> dict:
    """设备侧与服务器侧按事件名对账；任一行漂移超过 tolerance 即 `clean=False`。"""
    events = sorted(set(device_counts) | set(server_counts))
    rows = []
    clean = True
    for event in events:
        device = device_counts.get(event, 0)
        server = server_counts.get(event, 0)
        drift = server - device
        if abs(drift) > tolerance:
            clean = False
        rows.append({"event": event, "device": device, "server": server, "drift": drift})
    return {
        "clean": clean,
        "tolerance": tolerance,
        "rows": rows,
        "device_total": sum(device_counts.values()),
        "server_total": sum(server_counts.values()),
    }


# ── 看板渲染（无外部依赖，零数据时明说）─────────────────────────────────────


def render_dashboard_html(payload: dict) -> str:
    """把导出的 JSON 渲染成一页静态看板。所有插值一律转义。"""
    funnel = payload.get("funnel") or {}
    delivery = payload.get("delivery") or {}
    sampling = payload.get("sampling") or {}
    totals = funnel.get("totals") or {}
    labels = funnel.get("step_labels") or {}
    status = funnel.get("data_status", "unknown")

    rows = []
    for key in STEP_KEYS:
        count = totals.get(key, 0)
        rate = (funnel.get("conversion") or {}).get(key)
        rate_text = "—" if rate is None else f"{rate * 100:.1f}%"
        rows.append(
            "<tr><th scope=\"row\">{}</th><td>{}</td><td>{}</td></tr>".format(
                html.escape(str(labels.get(key, key))), int(count), rate_text
            )
        )
    funnel_table = (
        "<table><caption>核心循环（注册窗口 {} → {}，{} 人）</caption>"
        "<thead><tr><th>步骤</th><th>人数</th><th>占注册</th></tr></thead>"
        "<tbody>{}</tbody></table>".format(
            html.escape(str((funnel.get("cohort") or {}).get("since", "?"))),
            html.escape(str((funnel.get("cohort") or {}).get("until", "?"))),
            int(funnel.get("total_users", 0)),
            "".join(rows),
        )
    )
    if status == "no_users":
        funnel_table += (
            "<p class=\"zero\">零：注册窗口内没有真实用户。这里显示 0，不填演示数据。</p>"
        )

    delivery_rows = []
    for key in DELIVERY_COUNTERS:
        value = delivery.get(key)
        text = "未知" if value is None else str(value)
        delivery_rows.append(
            "<tr><th scope=\"row\">{}</th><td>{}</td></tr>".format(
                html.escape(key), html.escape(text)
            )
        )
    delivery_table = (
        "<table><caption>投递质量</caption><tbody>{}</tbody></table>".format(
            "".join(delivery_rows)
        )
        if delivery
        else "<p class=\"zero\">投递质量：未提供台账输入。</p>"
    )

    sampling_block = ""
    if sampling:
        cells = "".join(
            "<tr><th scope=\"row\">{}</th><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                html.escape(str(row.get("event", ""))),
                int(row.get("device", 0)),
                int(row.get("server", 0)),
                int(row.get("drift", 0)),
            )
            for row in sampling.get("rows", [])
        )
        sampling_block = (
            "<table><caption>设备 / 服务端抽样对账（{clean}）</caption>"
            "<thead><tr><th>事件</th><th>设备</th><th>服务端</th><th>差</th></tr></thead>"
            "<tbody>{cells}</tbody></table>".format(
                clean="一致" if sampling.get("clean") else "有漂移", cells=cells
            )
        )

    return (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>ClosenAI 核心循环看板</title></head><body>"
        "<h1>ClosenAI 核心循环看板</h1>"
        "<p>数据口径：只数业务表，不读内容；测试账号按 @closenai.test 排除。</p>"
        f"{funnel_table}{delivery_table}{sampling_block}"
        "</body></html>"
    )


# ── 装载（需要真库；本机无 PG 时按未跑记）──────────────────────────────────


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def load_journeys(db, *, since: datetime, until: datetime) -> tuple[list[Journey], int]:
    """从权威表装载注册窗口内的用户事实。返回 (journeys, 测试账号排除数)。"""
    from sqlalchemy import select

    from backend.models.canonical import (
        Insight,
        InsightEvidence,
        Outcome,
        PersonalCommitment,
    )
    from backend.models.growth import GrowthRecord
    from backend.models.push import PushToken
    from backend.models.share import ShareExport
    from backend.models.user import User

    users = (
        await db.execute(
            select(User.id, User.email, User.language, User.created_at).where(
                User.status == "active", User.created_at >= since, User.created_at < until
            )
        )
    ).all()
    test_ids: set[str] = set()
    kept: list[tuple[str, str, datetime]] = []
    for user_id, email, language, created_at in users:
        if email and email.lower().endswith(TEST_USER_SUFFIXES):
            test_ids.add(user_id)
            continue
        kept.append((user_id, language or "unknown", _as_utc(created_at)))
    if not kept:
        return [], len(test_ids)
    ids = [row[0] for row in kept]

    def stamps(rows) -> dict[str, list[datetime]]:
        out: dict[str, list[datetime]] = {}
        for user_id, value in rows:
            if value is not None:
                out.setdefault(user_id, []).append(_as_utc(value))
        return out

    reflections = stamps(
        (
            await db.execute(
                select(GrowthRecord.user_id, GrowthRecord.created_at).where(
                    GrowthRecord.user_id.in_(ids),
                    GrowthRecord.record_type == "reflection",
                )
            )
        ).all()
    )
    insights = stamps(
        (
            await db.execute(
                select(Insight.user_id, Insight.published_at)
                .join(InsightEvidence, InsightEvidence.insight_id == Insight.id)
                .where(
                    Insight.user_id.in_(ids),
                    Insight.status == "published",
                    Insight.published_at.is_not(None),
                )
                .distinct()
            )
        ).all()
    )
    actions = stamps(
        (
            await db.execute(
                select(PersonalCommitment.user_id, PersonalCommitment.created_at).where(
                    PersonalCommitment.user_id.in_(ids)
                )
            )
        ).all()
    )
    outcomes = stamps(
        (
            await db.execute(
                select(Outcome.user_id, Outcome.created_at).where(
                    Outcome.user_id.in_(ids), Outcome.origin == "user_statement"
                )
            )
        ).all()
    )
    paid = stamps(
        (
            await db.execute(
                select(ShareExport.user_id, ShareExport.created_at).where(
                    ShareExport.user_id.in_(ids)
                )
            )
        ).all()
    )
    platforms: dict[str, str] = {}
    for user_id, platform in (
        await db.execute(
            select(PushToken.user_id, PushToken.platform).where(PushToken.user_id.in_(ids))
        )
    ).all():
        platforms.setdefault(user_id, platform or "unknown")

    journeys = [
        Journey(
            user_id=user_id,
            registered_at=created_at,
            language=language,
            platform=platforms.get(user_id, "unknown"),
            reflections=tuple(reflections.get(user_id, ())),
            insights=tuple(insights.get(user_id, ())),
            actions=tuple(actions.get(user_id, ())),
            outcomes=tuple(outcomes.get(user_id, ())),
            paid_deliveries=tuple(paid.get(user_id, ())),
        )
        for user_id, language, created_at in kept
    ]
    return journeys, len(test_ids)


async def load_delivery(db, *, since: datetime) -> list[DeliveryFact]:
    """从回执台账装载投递事实。dropped 不在台账里（设计如此），由批次响应补。"""
    from sqlalchemy import select

    from backend.models.analytics import AnalyticsEventReceipt

    rows = (
        await db.execute(
            select(
                AnalyticsEventReceipt.status,
                AnalyticsEventReceipt.created_at,
                AnalyticsEventReceipt.updated_at,
            ).where(AnalyticsEventReceipt.created_at >= since)
        )
    ).all()
    facts: list[DeliveryFact] = []
    for status, created_at, updated_at in rows:
        latency = None
        if status == "accepted" and created_at and updated_at:
            latency = max(0, int((updated_at - created_at).total_seconds() * 1000))
        facts.append(DeliveryFact(event="analytics_batch", status=status, latency_ms=latency))
    return facts


def build_payload(funnel: dict, delivery: dict | None = None, sampling: dict | None = None) -> dict:
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "funnel": funnel}
    if delivery is not None:
        payload["delivery"] = delivery
    if sampling is not None:
        payload["sampling"] = sampling
    return payload


def payload_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
