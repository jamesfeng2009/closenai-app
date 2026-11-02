#!/usr/bin/env python3
"""
为 closenai.app 绑定 Cloudflare Pages 自定义域。
需要具备 Zone.DNS:Edit 权限的 API Token（OAuth 登录的凭据不含此权限）。

用法：
  1) 把 token 写进项目根目录的 .cf-token（单行，不要引号）
  2) python3 scripts/cf_dns_setup.py
  3) 脚本结束后会提示你删除 .cf-token 并去 Dashboard 撤销该 token
"""
import json
import os
import subprocess
import sys
import time

DOMAIN = "closenai.app"
WWW = "www.closenai.app"
ACC = "d1a2e66eb944d87c62b59436d02d00bc"
PROJ = "closenai-web"
TARGET = "closenai-web.pages.dev"
API = "https://api.cloudflare.com/client/v4"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_FILE = os.path.join(ROOT, ".cf-token")


def read_token():
    tok = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if tok:
        return tok
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def cf(method, path, token, payload=None, timeout=60):
    url = path if path.startswith("http") else f"{API}{path}"
    cmd = [
        "curl", "-s", "-m", str(timeout), "-X", method, url,
        "-H", f"Authorization: Bearer {token}",
        "-H", "Content-Type: application/json",
    ]
    tmp = "/tmp/cf_call.json"
    if payload is not None:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        cmd += ["--data-binary", f"@{tmp}"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return {"success": False, "raw": (out.stdout or "")[:400], "stderr": (out.stderr or "")[:200]}


def errs(r):
    return "; ".join(f"{e.get('code')}: {e.get('message')}" for e in (r.get("errors") or [])) or str(r)[:200]


def main():
    token = read_token()
    if not token:
        print("✘ 未找到 API Token。请写入 .cf-token 或设置 CLOUDFLARE_API_TOKEN")
        return 1
    print(f"  已读取 token（长度 {len(token)}，不显示内容）")

    # 1. 定位 zone
    r = cf("GET", f"/zones?name={DOMAIN}", token)
    if not r.get("success") or not r.get("result"):
        print("✘ 查 zone 失败:", errs(r))
        return 1
    zone = r["result"][0]
    zid = zone["id"]
    print(f"[1/5] zone ok: {zone['name']} | status={zone['status']} | id={zid}")

    # 2. 列出已有记录，避免重复
    r = cf("GET", f"/zones/{zid}/dns_records?per_page=100", token)
    existing = r.get("result") or []
    print(f"[2/5] 现有 DNS 记录 {len(existing)} 条")
    for x in existing:
        print(f"      {x.get('type'):6} {x.get('name'):24} -> {str(x.get('content'))[:40]}")

    # 3. 创建 CNAME（apex + www）
    wanted = [(DOMAIN, "@"), (WWW, "www")]
    created = []
    for fqdn, _label in wanted:
        hit = [x for x in existing if x.get("name") == fqdn]
        if hit:
            rec = hit[0]
            print(f"[3/5] 跳过（已存在）: {fqdn} -> {rec.get('content')} proxied={rec.get('proxied')}")
            created.append(fqdn)
            continue
        r = cf("POST", f"/zones/{zid}/dns_records", token, {
            "type": "CNAME",
            "name": fqdn,
            "content": TARGET,
            "proxied": True,
            "ttl": 1,
            "comment": "Cloudflare Pages (closenai-web)",
        })
        if r.get("success"):
            res = r["result"]
            print(f"[3/5] 已创建: {res['type']} {res['name']} -> {res['content']} proxied={res.get('proxied')}")
            created.append(fqdn)
        else:
            print(f"[3/5] ✘ 创建失败 {fqdn}: {errs(r)}")

    if not created:
        print("✘ 没有成功创建任何记录，后续无法继续")
        return 1

    # 4. 触发 Pages 域名重新校验
    print("[4/5] 触发自定义域重新校验")
    for d in (DOMAIN, WWW):
        r = cf("PATCH", f"/accounts/{ACC}/pages/projects/{PROJ}/domains/{d}", token, {})
        if r.get("success"):
            print(f"      {d}: {r['result'].get('status')}")
        else:
            # 若 PATCH 不支持，删了重建
            cf("DELETE", f"/accounts/{ACC}/pages/projects/{PROJ}/domains/{d}", token)
            r2 = cf("POST", f"/accounts/{ACC}/pages/projects/{PROJ}/domains", token, {"name": d})
            ok = r2.get("success")
            print(f"      {d}: rebuild {'ok' if ok else errs(r2)}")

    # 5. 轮询直到 active
    print("[5/5] 等待域名激活（最多 180 秒）")
    for i in range(18):
        time.sleep(10)
        r = cf("GET", f"/accounts/{ACC}/pages/projects/{PROJ}/domains", token)
        rows = r.get("result") or []
        st = {x.get("name"): x.get("status") for x in rows}
        print(f"      {10*(i+1):3}s  {st}")
        if all(st.get(d) == "active" for d in (DOMAIN, WWW)):
            print("      ✔ 两个域名均 active")
            break

    print("\n完成。请执行清理：")
    print(f"  rm -f {TOKEN_FILE}")
    print("  并到 Cloudflare Dashboard → My Profile → API Tokens 撤销该 token")
    return 0


if __name__ == "__main__":
    sys.exit(main())
