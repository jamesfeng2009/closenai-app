#!/usr/bin/env python3
"""AGIBar token proxy self-test — zero dependencies, stdlib only.

Usage:
    python3 scripts/agibar/selftest.py                    # test every model advertised by /v1/models
    python3 scripts/agibar/selftest.py --model k3.1-pro
    python3 scripts/agibar/selftest.py --probe-temp       # probe which temperature values each model accepts
    python3 scripts/agibar/selftest.py --json

Exit code: 0 = every model answered with usable content, 1 = at least one not ok.

Status semantics:
    ok        HTTP 200 + non-empty content
    degraded  HTTP 200 but empty content (reasoning models burn reasoning_tokens inside
              max_tokens; finish_reason=length with empty content means the budget ran out
              before the answer started). Bump --max-tokens.
    fail      non-200 / network error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://token.agi.bar/v1"
DEFAULT_API_KEY = "sk-agi_bar-static"


def _request(
    method: str,
    path: str,
    *,
    base_url: str,
    api_key: str,
    timeout: float,
    payload: dict | None = None,
) -> tuple[int, object, float, str]:
    url = base_url.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method=method,
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            code = resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        code = e.code
    except urllib.error.URLError as e:
        return 0, {}, time.perf_counter() - t0, f"network error: {e.reason}"
    dt = time.perf_counter() - t0
    try:
        return code, json.loads(body), dt, ""
    except json.JSONDecodeError:
        return code, {}, dt, body[:300]


def _error_message(resp: object) -> str:
    """从 LiteLLM 错误体里取 message（截断），用于给出可读的失败原因。"""
    if isinstance(resp, dict):
        err = resp.get("error")
        if isinstance(err, dict):
            return str(err.get("message", ""))[:220]
        if err:
            return str(err)[:220]
    return ""


def _chat(
    model: str,
    *,
    base_url: str,
    api_key: str,
    timeout: float,
    max_tokens: int,
    temperature: float | None,
) -> dict:
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: pong"}],
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        payload["temperature"] = temperature

    code, resp, dt, raw = _request(
        "POST", "/chat/completions", base_url=base_url, api_key=api_key, timeout=timeout, payload=payload
    )
    check: dict = {"check": f"POST /v1/chat/completions [{model}]", "latency_ms": round(dt * 1000)}

    if code != 200:
        check.update(
            status="fail",
            code=code,
            detail=_error_message(resp) or raw,
        )
        return check

    choice = (resp.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = (message.get("content") or "").strip()
    reasoning = message.get("reasoning_content") or ""
    check.update(
        code=200,
        finish_reason=choice.get("finish_reason"),
        reply=content[:80],
        usage=resp.get("usage") or {},
        reasoning_used=bool(reasoning),
    )

    if content:
        check["status"] = "ok"
    else:
        check["status"] = "degraded"
        check["detail"] = (
            "empty content"
            + (" (budget consumed by reasoning tokens — raise --max-tokens)" if reasoning else "")
        )
    return check


def _probe_temperature(
    model: str,
    *,
    base_url: str,
    api_key: str,
    timeout: float,
    max_tokens: int,
) -> dict:
    """探测模型接受的 temperature 取值。部分推理模型只接受 1，其他值直接 400。"""
    accepted: list[float] = []
    rejected: dict[str, str] = {}
    for temp in (0, 0.7, 1):
        code, resp, _, raw = _request(
            "POST",
            "/chat/completions",
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            payload={
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": max_tokens,
                "temperature": temp,
            },
        )
        if code == 200:
            accepted.append(temp)
        else:
            rejected[str(temp)] = _error_message(resp) or raw or f"HTTP {code}"
    return {"model": model, "accepted": accepted, "rejected": rejected}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("AGIBAR_BASE_URL", DEFAULT_BASE_URL))
    ap.add_argument("--api-key", default=os.environ.get("AGIBAR_API_KEY", DEFAULT_API_KEY))
    ap.add_argument("--model", action="append", help="limit to specific model id(s)")
    ap.add_argument("--max-tokens", type=int, default=256, help="default 256: reasoning models need headroom")
    ap.add_argument("--temperature", type=float, default=None, help="omit to use the server default")
    ap.add_argument("--probe-temp", action="store_true", help="probe accepted temperature values per model")
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report: dict = {"base_url": args.base_url, "checks": [], "temperature": []}
    exit_ok = True

    code, models, dt, raw = _request(
        "GET", "/models", base_url=args.base_url, api_key=args.api_key, timeout=args.timeout
    )
    ids: list[str] = []
    if code == 200 and isinstance(models, dict):
        ids = [m.get("id", "") for m in models.get("data", []) if m.get("id")]
        report["checks"].append(
            {"check": "GET /v1/models", "status": "ok", "latency_ms": round(dt * 1000), "models": ids}
        )
    else:
        exit_ok = False
        report["checks"].append(
            {"check": "GET /v1/models", "status": "fail", "code": code, "detail": _error_message(models) or raw}
        )

    for mid in args.model or ids:
        check = _chat(
            mid,
            base_url=args.base_url,
            api_key=args.api_key,
            timeout=args.timeout,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
        )
        if check["status"] != "ok":
            exit_ok = False
        report["checks"].append(check)

        if args.probe_temp:
            report["temperature"].append(
                _probe_temperature(
                    mid,
                    base_url=args.base_url,
                    api_key=args.api_key,
                    timeout=args.timeout,
                    max_tokens=args.max_tokens,
                )
            )

    report["all_passed"] = exit_ok

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if exit_ok else 1

    print(f"target: {report['base_url']}  (max_tokens={args.max_tokens})")
    for c in report["checks"]:
        if c["status"] == "ok":
            extra = " -> " + ", ".join(c["models"]) if "models" in c else f" -> {c.get('reply', '')!r}"
            flags = ""
            if c.get("reasoning_used"):
                flags = "  [reasoning model]"
            print(f"  PASS      {c['latency_ms']:>5}ms  {c['check']}{extra}{flags}")
        elif c["status"] == "degraded":
            print(f"  DEGRADED  {c['latency_ms']:>5}ms  {c['check']}  {c.get('detail', '')}")
        else:
            print(f"  FAIL      {c['check']}  code={c.get('code')}  {c.get('detail', '')}")

    if report["temperature"]:
        print("temperature policy:")
        for t in report["temperature"]:
            acc = ", ".join(str(x) for x in t["accepted"]) or "none"
            print(f"  {t['model']:<24} accepts: {acc}")
            for temp, msg in t["rejected"].items():
                print(f"    {temp:>4} rejected: {msg[:100]}")

    print("result:", "ALL PASS" if exit_ok else "NOT OK")
    return 0 if exit_ok else 1


if __name__ == "__main__":
    sys.exit(main())
