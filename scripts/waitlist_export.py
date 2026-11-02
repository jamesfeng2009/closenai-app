#!/usr/bin/env python3
"""
导出 ClosenAI 内测名单（从 Cloudflare KV 读取）。

用法：
    python3 scripts/waitlist_export.py                # 打印为表格
    python3 scripts/waitlist_export.py --csv out.csv  # 导出 CSV

前置：
    本机已执行过 `wrangler login`（凭据在 ~/.wrangler/config/default.toml）。
    OAuth token 约 1 小时过期；若提示 401，先跑一次 `wrangler whoami` 刷新。

可选环境变量：
    CF_API_TOKEN         用 API Token 代替 OAuth（需 Workers KV Read 权限）
    CF_ACCOUNT_ID        账号 ID
    WAITLIST_NAMESPACE   KV namespace id
"""

import argparse
import csv
import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "d1a2e66eb944d87c62b59436d02d00bc")
NAMESPACE = os.environ.get("WAITLIST_NAMESPACE", "e7713c3d171c48e68ebcdd4865eb5f30")
API = "https://api.cloudflare.com/client/v4"
WRANGLER_CONFIG = Path.home() / ".wrangler" / "config" / "default.toml"


def get_token() -> str:
    token = os.environ.get("CF_API_TOKEN")
    if token:
        return token.strip()
    if not WRANGLER_CONFIG.exists():
        sys.exit("未找到 wrangler 凭据，请先执行：wrangler login")
    try:
        import tomllib
    except ImportError:
        sys.exit("需要 Python 3.11+（tomllib），或改用 CF_API_TOKEN 环境变量")
    data = tomllib.loads(WRANGLER_CONFIG.read_text())
    token = data.get("oauth_token")
    if not token:
        sys.exit("凭据里没有 oauth_token，请重新执行：wrangler login")
    return token


def call(path: str, token: str) -> dict:
    req = urllib.request.Request(f"{API}{path}", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200]
        if e.code in (401, 403):
            sys.exit(f"HTTP {e.code}：凭据失效或无权限。先执行 `wrangler whoami` 刷新后重试。\n{body}")
        sys.exit(f"HTTP {e.code}: {body}")


def list_keys(token: str) -> list:
    keys, cursor = [], None
    while True:
        path = f"/accounts/{ACCOUNT_ID}/storage/kv/namespaces/{NAMESPACE}/keys?prefix=sub:&limit=1000"
        if cursor:
            path += f"&cursor={cursor}"
        data = call(path, token)
        if not data.get("success"):
            sys.exit(f"列举失败：{data.get('errors')}")
        keys.extend(k["name"] for k in data.get("result") or [])
        cursor = (data.get("result_info") or {}).get("cursor")
        if not cursor:
            return keys


def fetch_one(name: str, token: str) -> dict:
    path = f"/accounts/{ACCOUNT_ID}/storage/kv/namespaces/{NAMESPACE}/values/{name}"
    req = urllib.request.Request(f"{API}{path}", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="导出到指定 CSV 文件")
    ap.add_argument("--include-test", action="store_true", help="保留 example.com 测试数据")
    args = ap.parse_args()

    token = get_token()
    keys = list_keys(token)
    if not keys:
        print("名单是空的。")
        return

    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = [r for r in pool.map(lambda k: fetch_one(k, token), keys) if r]

    if not args.include_test:
        rows = [r for r in rows if not r.get("email", "").endswith("@example.com")]

    rows.sort(key=lambda r: r.get("at", ""))

    if args.csv:
        out = Path(args.csv)
        with out.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["email", "subscribed_at", "country", "city", "referer"])
            for r in rows:
                w.writerow([r.get("email", ""), r.get("at", ""), r.get("country", ""), r.get("city", ""), r.get("ref", "")])
        print(f"已导出 {len(rows)} 条到 {out.resolve()}")
        return

    print(f"共 {len(rows)} 条：\n")
    print(f"{'邮箱':38} {'时间(UTC)':20} {'地区'}")
    print("-" * 78)
    for r in rows:
        region = "/".join(x for x in (r.get("country"), r.get("city")) if x) or "-"
        print(f"{r.get('email',''):38} {r.get('at','')[:19]:20} {region}")


if __name__ == "__main__":
    main()
