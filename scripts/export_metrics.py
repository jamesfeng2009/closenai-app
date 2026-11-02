#!/usr/bin/env python3
"""T7-06：核心循环指标导出（JSON + 静态看板）。

两种输入：
  · 真库：`python scripts/export_metrics.py --since 2026-10-19 --until 2026-10-26`
    （需要 `DATABASE_URL`，本机无 PG 时这条按未跑记）
  · 离线：`--input journeys.json`，格式见 docs/metrics-dictionary.md；
    种子用户实验（T7-07）在没有生产库时用这条跑同一套判据。

没有真实用户时导出里是 0，并带 `data_status="no_users"`——不生成演示数据。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from backend.services.metrics_export import (  # noqa: E402
    DeliveryFact,
    Journey,
    build_funnel,
    build_payload,
    load_delivery,
    load_journeys,
    payload_json,
    reconcile_sampling,
    render_dashboard_html,
    summarize_delivery,
)


def _parse_day(value: str, *, end: bool) -> datetime:
    day = date.fromisoformat(value)
    if end:
        day = day + timedelta(days=1)
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_input(path: str) -> list[Journey]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = raw["journeys"] if isinstance(raw, dict) else raw
    journeys: list[Journey] = []
    for row in rows:
        journeys.append(
            Journey(
                user_id=str(row["user_id"]),
                registered_at=_parse_ts(row["registered_at"]),
                language=row.get("language", "unknown"),
                platform=row.get("platform", "unknown"),
                reflections=tuple(_parse_ts(x) for x in row.get("reflections", ())),
                insights=tuple(_parse_ts(x) for x in row.get("insights", ())),
                actions=tuple(_parse_ts(x) for x in row.get("actions", ())),
                outcomes=tuple(_parse_ts(x) for x in row.get("outcomes", ())),
                paid_deliveries=tuple(_parse_ts(x) for x in row.get("paid_deliveries", ())),
                rejected_insights=int(row.get("rejected_insights", 0)),
                corrections=int(row.get("corrections", 0)),
                failure_outcomes=int(row.get("failure_outcomes", 0)),
            )
        )
    return journeys


async def load_from_db(since: datetime, until: datetime):
    from backend.database import SessionLocal

    async with SessionLocal() as db:
        journeys, excluded = await load_journeys(db, since=since, until=until)
        facts = await load_delivery(db, since=since)
    return journeys, excluded, facts


def main() -> int:
    ap = argparse.ArgumentParser(description="导出 ClosenAI 核心循环指标")
    ap.add_argument("--since", required=True, help="注册窗口起（含），YYYY-MM-DD")
    ap.add_argument("--until", required=True, help="注册窗口止（不含），YYYY-MM-DD")
    ap.add_argument("--input", help="离线 journeys.json（与 --database 二选一）")
    ap.add_argument("--batch-stats", help="批次统计 JSON：{\"relay_dropped\": n, \"device\": {}, \"server\": {}}")
    ap.add_argument("--json", dest="json_out", help="JSON 输出路径（缺省 stdout）")
    ap.add_argument("--html", help="静态看板输出路径")
    ap.add_argument("--strict", action="store_true", help="台账恒等式不成立或抽样有漂移时非零退出")
    args = ap.parse_args()

    since = _parse_day(args.since, end=False)
    until = _parse_day(args.until, end=True)
    if until <= since:
        ap.error("--until 必须晚于 --since")

    if args.input:
        journeys = load_input(args.input)
        excluded = 0
        facts: list[DeliveryFact] = []
    else:
        journeys, excluded, facts = asyncio.run(load_from_db(since, until))

    funnel = build_funnel(journeys, since=since, until=until)
    funnel["test_users_excluded"] += excluded

    batch = {}
    if args.batch_stats:
        batch = json.loads(Path(args.batch_stats).read_text(encoding="utf-8"))

    delivery = summarize_delivery(facts, relay_dropped=batch.get("relay_dropped")) if facts or batch else None
    sampling = None
    if batch.get("device") and batch.get("server"):
        sampling = reconcile_sampling(
            batch["device"], batch["server"], tolerance=int(batch.get("tolerance", 0))
        )

    payload = build_payload(funnel, delivery, sampling)
    text = payload_json(payload)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
        print(f"JSON → {args.json_out}")
    else:
        print(text)
    if args.html:
        Path(args.html).write_text(render_dashboard_html(payload), encoding="utf-8")
        print(f"看板 → {args.html}")

    if args.strict:
        problems = []
        if delivery and delivery["identity_holds"] is False:
            problems.append("投递恒等式不成立（accepted+duplicate+dropped+queued != total）")
        if sampling and not sampling["clean"]:
            problems.append("设备/服务端抽样有漂移")
        if problems:
            print("✘ " + "；".join(problems), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
