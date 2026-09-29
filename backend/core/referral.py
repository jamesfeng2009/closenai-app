"""分享卡推荐码：格式、随机源与深链拼装（M4 / 架构方案 §二 M4 · 决策点 3）。

纯函数，不碰数据库——查重与持久化在 `user_repository`，这里只保证「一个码长什么样、
怎么变成一条链接」。拆开放是因为码的格式要同时被注册入口（校验 `?ref=`）、深链落地页
（解析路径段）和 PDF 渲染（印二维码 + 明文短链）三处复用。
"""
import re
import secrets
from typing import Optional

# 码会印在 PDF 的二维码下方、也可能被人手抄，因此排除易混字符：
# 数字去掉 0/1/8/9，字母去掉 I/L/O → 29 字符集，8 位 = 29^8 ≈ 2^38 空间。
REFERRAL_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ234567"
REFERRAL_CODE_LEN = 8
# 校验与生成共用同一字符集：手抄进来的 I/0/l 一律判非法，而不是放进 DB 撞 UNIQUE
REFERRAL_CODE_RE = re.compile(rf"^[{REFERRAL_ALPHABET}]{{{REFERRAL_CODE_LEN}}}$")


def new_referral_code() -> str:
    """随机源用 secrets：推荐码可被外部枚举撞库，random 的可预测种子不适合。"""
    return "".join(secrets.choice(REFERRAL_ALPHABET) for _ in range(REFERRAL_CODE_LEN))


def normalize_referral_code(raw: Optional[str]) -> Optional[str]:
    """归一化并校验推荐码；非法（空、长度不符、含被排除的易混字符）返回 None。

    注册链接里的 `?ref=` 是外部输入，不能直接落库：非法值静默丢弃而不是报错打断
    注册——归因标签丢了下次还能补，注册被打断会真的丢用户。大小写不敏感（转大写）。
    """
    code = (raw or "").strip().upper()
    return code if REFERRAL_CODE_RE.match(code) else None


def referral_link(site_url: str, code: str) -> str:
    """深链 `https://closenai.app/r/{code}`（决策点 3：域名已购，CF Pages 承载落地页）。

    站点与 API 域分离：落地页是纯静态资源，不能把 API 域名暴露成公开可枚举入口。
    """
    return f"{site_url.rstrip('/')}/r/{code}"
