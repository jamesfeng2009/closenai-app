#!/usr/bin/env python3
"""Cloudflare Pages 直传部署（绕过 wrangler 的代理问题）"""
import base64
import hashlib
import json
import mimetypes
import os
import subprocess
import sys
import tomllib

ACC = "d1a2e66eb944d87c62b59436d02d00bc"
PROJ = "closenai-web"
ROOT = "/Users/fengyu/Downloads/myproject/workspace/closenai-app/web"
CFG = os.path.expanduser("~/.wrangler/config/default.toml")
API = "https://api.cloudflare.com/client/v4"

CT_OVERRIDE = {
    ".html": "text/html; charset=utf-8",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    "apple-app-site-association": "application/json",
    "_headers": "text/plain; charset=utf-8",
    "_redirects": "text/plain; charset=utf-8",
}


def curl_json(method, url, token, payload=None, timeout=180):
    cmd = [
        "curl", "-s", "-m", str(timeout), "-X", method, url,
        "-H", f"Authorization: Bearer {token}",
        "-H", "Content-Type: application/json",
    ]
    data_file = None
    if payload is not None:
        data_file = "/tmp/cf_payload.json"
        with open(data_file, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        cmd += ["--data-binary", f"@{data_file}"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return {"success": False, "raw": out.stdout[:600], "stderr": out.stderr[:300]}


def collect(base):
    items = []
    for dirpath, _, names in os.walk(base):
        for n in names:
            p = os.path.join(dirpath, n)
            rel = "/" + os.path.relpath(p, base).replace(os.sep, "/")
            items.append((rel, p))
    return sorted(items)


def content_type(path):
    name = os.path.basename(path)
    if name in CT_OVERRIDE:
        return CT_OVERRIDE[name]
    ext = os.path.splitext(path)[1].lower()
    if ext in CT_OVERRIDE:
        return CT_OVERRIDE[ext]
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def main():
    token = tomllib.load(open(CFG, "rb"))["oauth_token"]
    files = collect(ROOT)
    print(f"[1/4] 收集文件 {len(files)} 个")
    for rel, p in files:
        print(f"      {rel}  ({os.path.getsize(p)} B)")

    payload = []
    manifest = {}
    for rel, p in files:
        raw = open(p, "rb").read()
        manifest[rel] = hashlib.sha256(raw).hexdigest()
        payload.append({
            "key": rel.lstrip("/"),
            "value": base64.b64encode(raw).decode(),
            "metadata": {"content_type": content_type(p)},
            "base64": True,
        })

    print("[2/4] 申请上传令牌")
    r = curl_json("POST", f"{API}/accounts/{ACC}/pages/projects/{PROJ}/uploads", token, {})
    if not r.get("success"):
        print("FAIL:", json.dumps(r, ensure_ascii=False)[:600])
        return 1
    jwt = r["result"]["jwt"]
    print("      ok")

    print("[3/4] 上传资源")
    r = curl_json("POST", f"{API}/accounts/{ACC}/pages/assets/upload", jwt, payload, timeout=300)
    if not r.get("success"):
        print("FAIL:", json.dumps(r, ensure_ascii=False)[:600])
        return 1
    print("      ok")

    print("[4/4] 创建部署")
    body = {"manifest": manifest, "branch": "main"}
    r = curl_json("POST", f"{API}/accounts/{ACC}/pages/projects/{PROJ}/deployments", token, body)
    if not r.get("success"):
        print("FAIL:", json.dumps(r, ensure_ascii=False)[:800])
        return 1
    res = r["result"]
    print("      deployment id:", res.get("id"))
    print("      url:", res.get("url"))
    print("      aliases:", res.get("aliases"))
    with open("/tmp/cf_deploy_ok.json", "w") as f:
        json.dump(res, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
