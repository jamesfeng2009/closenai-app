"""批 2 · D-G5（分享深链闭环）的登记闸：本批**不接线**，只钉住"不硬编码 API 域"这条红线。

工作单默认「落地页 `site/public/r/index.html` 接 `GET /share/resolve/{code}` 拿归因与商店链」。
实测三条让它不能在代码里做完（取证见下，均为 2026-10-17 读源确认）：

① API 域不在静态页的可知面内：`r/index.html:118` 只有硬编码 `SITE_HOME='https://closenai.app/'`，
   而 App 侧的 API 基址是构建/运行期注入的（`frontend/src/services/api.ts:46` 读
   `EXPO_PUBLIC_API_BASE`，缺省回落 `DEV_API_BASE`）。Astro 之外的静态 public 文件读不到 env
   ⇒ 接 resolve 只能把 API 域**写死进 HTML**，那是把一条不可配置的地址发到生产。
② 后端 CORS 默认白名单只有两条 localhost（`backend/config.py:29`，`backend/main.py:137` 原样使用）
   ⇒ 即使写死，浏览器从 closenai.app 发起的 fetch 也必被 preflight 拦掉。
③ 即使接上也不可观测：`resolve_referral` 回的 `app_store_url` 就是
   `settings.share_landing_url = app_store_url or site_url`（`backend/config.py:144-151`、
   `backend/services/share_service.py:419`），而 `APP_STORE_URL` 的真值是 K4 的外部阻塞
   ⇒ 今天接完只能看到"回落到站点首页"，与现状**同形**，等于交付一条无法验证的线。
   （回落本身已有闸：`backend/tests/test_share_landing_fallback.py:61`。）

⇒ 交付物改成三条**反向**判据：静态页不许硬编码 API 域（这条本来没有任何闸盯，
   现有 `test_site_has_no_hardcoded_app_store_url` 钉的是链、不是域）、resolve 端点仍是零消费者
   且仍在契约册上、以及一条条件式不变量：**先加 CORS  Origin 再接页面请求**。
K4 落地后重开 D-G5，届时按 ② 的 CORS 事实再拍「构建期注入」还是「运行时 fetch」。
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
R_LANDING = (ROOT / "site" / "public" / "r" / "index.html").read_text(encoding="utf-8")
ENDPOINTS_MD = (ROOT / "contracts" / "endpoints.md").read_text(encoding="utf-8")
API_TS = (ROOT / "frontend" / "src" / "services" / "api.ts").read_text(encoding="utf-8")
FRONTEND_SRC = ROOT / "frontend" / "src"

_BLOCK_COMMENT = re.compile(r"/\*[\s\S]*?\*/|<!--[\s\S]*?-->")

# 静态页里一旦出现这些，就说明有人把「后端地址」当常量写进了 HTML
API_DOMAIN_MARKERS = (
    "localhost",
    ":8000",
    "/api/v1",
    "EXPO_PUBLIC_API_BASE",
    "API_BASE",
)
NETWORK_MARKERS = ("fetch(", "XMLHttpRequest", "axios")


def _code_lines(text: str):
    """只返回代码行的 (行号, 内容)：注释里允许讲这段改判的历史，值里不许出现 API 域。

    与 `test_legal_pages.py::_code_lines` 同一口径（整块注释按块剥，续行才不会被当成代码）。
    """
    out = []
    for no, line in enumerate(_BLOCK_COMMENT.sub("", text).splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("#"):
            continue
        out.append((no, line))
    return out


def _contract_row(path_fragment: str, method: str):
    for line in ENDPOINTS_MD.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 8 or not re.match(r"^\d+$", cells[1]):
            continue
        if path_fragment in line and cells[2] == method:
            return cells
    raise AssertionError(f"契约册里找不到 {method} {path_fragment}")


def test_referral_landing_page_hardcodes_no_api_domain():
    """落地页只认识站点自己：没有 API 域、也没有任何出网请求（D-G5 ①②的当前形状）。"""
    offenders = [
        f"{no}: {line.strip()}"
        for no, line in _code_lines(R_LANDING)
        if any(m in line for m in API_DOMAIN_MARKERS + NETWORK_MARKERS)
    ]
    assert not offenders, "静态落地页里出现了 API 域或出网请求：\n" + "\n".join(offenders)
    # 反向的另一半：它确实只用站点首页兜底（不是恰好没写到）
    assert "var SITE_HOME = 'https://closenai.app/';" in R_LANDING
    assert ".href = SITE_HOME;" in R_LANDING


def test_resolve_endpoint_is_zero_consumer_and_still_registered():
    """`GET /share/resolve/{code}` 保持「在册、无前端消费者」——改判的登记形状。"""
    cells = _contract_row("/api/v1/share/resolve/{code}", "GET")
    assert cells[6] == "—", f"该行的前端列不再是「—」：{cells[6]}（D-G5 已被接线？按头注释重开判据）"
    assert cells[5] == "anon", "这条端点一旦改成需登录，落地页的公开一跳就断了"
    assert "share/resolve" not in API_TS
    hits = [
        str(p.relative_to(ROOT))
        for p in FRONTEND_SRC.rglob("*")
        if p.is_file() and p.suffix in {".ts", ".tsx"} and "share/resolve" in p.read_text(encoding="utf-8")
    ]
    assert hits == [], f"前端又长出了 resolve 的消费者：{hits}"
    # 端点本体仍在（本批改判删的是接线，不是端点）
    router = (ROOT / "backend" / "routers" / "share.py").read_text(encoding="utf-8")
    assert '@router.get("/share/resolve/{code}"' in router


def test_wiring_the_landing_page_requires_a_cors_origin_first():
    """条件式不变量：落地页若开始发请求，站点域必须已在 CORS 白名单里。

    这条今天恒绿（前件为假）。它红的时候不是 bug，是提醒：有人接了 D-G5 却没有
    先按 ② 改 `backend/config.py:29` 的默认白名单——那种接法线上必被 preflight 拦。
    """
    wired = any(
        any(m in line for m in NETWORK_MARKERS + ("/api/v1",))
        for _, line in _code_lines(R_LANDING)
    )
    if not wired:
        return
    config_py = (ROOT / "backend" / "config.py").read_text(encoding="utf-8")
    cors_line = next(l for l in config_py.splitlines() if "cors_origins:" in l)
    assert "closenai.app" in cors_line, (
        "落地页已发请求但默认 CORS 白名单不含站点域（backend/config.py 的 cors_origins）"
        "⇒ 线上必被拦。按 D-G5 头注释重开判据，先拍 CORS 再接页面。"
    )
