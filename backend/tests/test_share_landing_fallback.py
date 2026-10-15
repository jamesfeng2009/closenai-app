"""U3-b · 分享落地的收口闸门。

这批的产品事实是「App 还没上架，手上没有真 App Store ID」。它带来的最坏结果不是
少一条链接，而是**发出去一条 404**：`id6740000000` 这个占位一旦被拼进用户分享的
文案，收到的人点开的是一条不存在的商店页——归因当场断掉，而且断在「已经分享了」
这一步，事后修不回来。

改后的口径：`app_store_url` 变 env-only、默认空串，消费方一律读
`settings.share_landing_url`（没配商店链⇒回落站点）。所以本文件的判据不是「商店链
长得对不对」，而是两件更容易做错的事：
1. **没配**商店链时两侧到底发出去什么——必须是一条能用、能归因的站点链接，不能是
   空串，也不能是占位；
2. 占位 ID 不许再回到任何生产源码里（含「顺手把默认值填回去」这种最可能的回归）。

第 3 组的两侧同形闸是本批真正的跨语言点：share_url 会**落进 first_read 的列**，
restore 之后按列复现、不再回查设置，两侧回落的优先级写反了就不是「显示不一样」，
而是同一个账号在切流前后拿到两条不同的深链。
"""
import pathlib

import pytest

from backend.config import settings
from backend.services.first_read_service import first_read_service
from backend.services.share_service import share_service

PLACEHOLDER_ID = "id6740000000"
ROOT = pathlib.Path(__file__).resolve().parents[2]


def _production_python_files():
    for path in (ROOT / "backend").rglob("*.py"):
        if "tests" in path.relative_to(ROOT).parts:
            continue
        yield path


def _production_go_files():
    for path in (ROOT / "go-core").rglob("*.go"):
        if path.name.endswith("_test.go"):
            continue
        yield path


# ── 1. 默认值本身：空串，不是占位 ──

def test_default_store_url_is_empty_not_a_placeholder():
    """默认必须是空串。填回占位＝把 404 重新发出去；给个站点值＝字段名开始说谎。"""
    assert settings.app_store_url == "", (
        f"app_store_url 默认 = {settings.app_store_url!r}，期望空串（上架前一律 env 注入）")


def test_site_url_default_is_unchanged():
    """回落目标必须是个真能打开的站点：回落到空串等于分享文案里出现一条坏链。"""
    assert settings.site_url == "https://closenai.app"


# ── 2. 回落口径：消费方拿到的是「点了会去哪」 ──

def test_landing_falls_back_to_site_when_store_unconfigured():
    assert settings.share_landing_url == settings.site_url


def test_configured_store_url_wins_over_site(monkeypatch):
    """上架后注入真链，消费方必须立刻改指商店——回落不能反过来压过配置。"""
    monkeypatch.setattr(settings, "app_store_url", "https://apps.apple.com/app/closenai/id123")
    assert settings.share_landing_url == "https://apps.apple.com/app/closenai/id123"


async def test_resolve_referral_landing_field_is_the_landing(db):
    """公开端点 /share/resolve 的 `app_store_url` 字段回的是落地链接（见 :419 注释）。

    这个键名会误导人：客户端拿它当「未安装时的去处」，所以上架前它必须是站点，
    而不是空串——空串在这里的表现是「点了没反应」，排查成本极高。
    """
    resolved = await share_service.resolve_referral(db, "ZZZZZZZZ")
    assert resolved["app_store_url"] == settings.site_url
    assert resolved["app_store_url"] != ""
    assert PLACEHOLDER_ID not in str(resolved)


async def test_share_link_for_owner_carries_landing(db, make_user):
    """本人取分享链接：深链带码，落地字段用同一个 share_landing_url 口径。"""
    await make_user("u-u3b", name="小见")
    data = await share_service.get_share_link(db, "u-u3b")
    assert data["link"].startswith(f"{settings.site_url}/r/")
    assert data["app_store_url"] == settings.site_url


def test_first_read_payload_landing_follows_the_same_rule(monkeypatch):
    """first_read 的 share_url 会落进列、进 N8 夹具的字节面，所以这条是契约闸。"""
    card = first_read_service.build_first_read([], None, "zh")
    assert card["card_payload"]["share_url"] == settings.site_url

    monkeypatch.setattr(settings, "app_store_url", "https://apps.apple.com/app/closenai/id123")
    card = first_read_service.build_first_read([], None, "zh")
    assert card["card_payload"]["share_url"] == "https://apps.apple.com/app/closenai/id123"


# ── 3. 回归面：占位 ID 与「绕过回落直接读原字段」都不许回来 ──

def test_placeholder_id_is_not_in_any_production_source():
    """生产源码里 `id6740000000` 只许出现在注释里（说明这段历史），不许出现在值里。"""
    offenders = []
    for path in [*_production_python_files(), *_production_go_files()]:
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PLACEHOLDER_ID not in line:
                continue
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("*"):
                continue
            offenders.append(f"{path.relative_to(ROOT)}:{no}: {stripped}")
    assert not offenders, "占位商店链回到了生产值里（这些行只会是注释以外的形态）:\n" + "\n".join(offenders)


def test_no_production_consumer_reads_the_raw_field():
    """绕过 `share_landing_url` 直接读 `settings.app_store_url` ＝未配置时发出空链。"""
    offenders = []
    for path in _production_python_files():
        if path.name == "config.py":
            continue
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "settings.app_store_url" in line:
                offenders.append(f"{path.relative_to(ROOT)}:{no}: {line.strip()}")
    assert not offenders, "有消费方绕过了 share_landing_url:\n" + "\n".join(offenders)


def test_go_reads_the_landing_only_through_the_helper():
    """Go 侧同一条规矩：`AppStoreURL` 字段只许在 config.go 里被读一次。"""
    offenders = []
    for path in _production_go_files():
        if path.name == "config.go":
            continue
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "AppStoreURL" in line:
                offenders.append(f"{path.relative_to(ROOT)}:{no}: {line.strip()}")
    assert not offenders, "Go 有消费方绕过了 ShareLandingURL():\n" + "\n".join(offenders)


def test_both_languages_fall_back_the_same_way():
    """两侧回落的**优先级**必须同形：商店优先、站点兜底。

    写在源码形状上是有原因的：这条判据打在字节契约面上要等下一批夹具重录才可见，
    而「谁优先」恰好是切流前后同一个账号拿到不同深链的那类分歧，越早打红越好。
    """
    py = (ROOT / "backend" / "config.py").read_text(encoding="utf-8")
    assert "return self.app_store_url or self.site_url" in py, \
        "Python 侧回落写法变了，跨语言闸要跟着改"

    go = (ROOT / "go-core" / "internal" / "config" / "config.go").read_text(encoding="utf-8")
    start = go.index("func (c Config) ShareLandingURL()")
    # 切到**函数**结尾（顶格的 `\n}`），不是第一个 if 块的收尾——按 `}` 切只拿到半截。
    block = go[start:go.index("\n}", start) + 2]
    assert "if c.AppStoreURL != \"\"" in block and "return c.AppStoreURL" in block, \
        f"Go 侧不是「商店优先」：\n{block}"
    assert "return c.SiteURL" in block, f"Go 侧没有站点兜底：\n{block}"


@pytest.mark.parametrize("field", ["app_store_url", "site_url"])
def test_env_variable_names_are_the_documented_ones(field):
    """BaseSettings 没有 env_prefix ⇒ 变量名就是大写字段名；改名＝部署侧静默失效。"""
    assert field in type(settings).model_fields
