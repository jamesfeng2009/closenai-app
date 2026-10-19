"""付费交付可观测（T1-04，Baseline §4.3「线上监控能发现付款成功但交付失败」）。

三条通道的分工是这个模块唯一要想清楚的事，因为它们会各自"看起来在工作"而实际互补：

- **结构化日志**（永远写）：一行 `DELIVERY-METRIC event=… user=… relay=… k=v`。
  这是不依赖任何外部平台的那一格——PostHog 未装、未配 key、用户没给分析同意，
  事件照样留在日志里，Loki 抓 stdout 就能数。
- **PostHog**（仅 `analytics_consent` 为真时中继）：给运营看板看漏斗。走
  `analytics_service.relay_server_event`，与前端埋点**共用同一张白名单**，
  所以服务端事件不会成为"绕过匿名化"的第二条出网路。
- **数据库**（`audit_paid_delivery` 数的那一格）：唯一能回答"现在还有几个人付了钱
  没拿到交付"的源。事件流会丢（未配 key／没同意／进程崩在中继之前），库里的
  权益行与导出行不会——所以告警的判据落在库里，不落在事件计数里。

红线条款（沿用 `analytics_service` 模块头的口径）：
- 属性只允许 `ALLOWED_PROPERTY_KEYS` 白名单内的机器码键；日志面同样先过匿名化，
  这样"把异常消息一路传进来"这种写法在两个通道都拿不到落地机会。
- `reason` 一律是枚举机器码（`timeout`／`rejected_40304`／`pdf_render_failed`…），
  不写自由文本、不写用户内容。
- 中继失败绝不影响主流程：本模块不抛异常、不改控制流，只加一行日志。
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import distinct, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.time_utils import utc_now
from backend.models.journey import UserEntitlement
from backend.models.share import LIFE_ARCHIVE_PERIOD, ShareExport
from backend.models.user import User
from backend.services import analytics_service

logger = logging.getLogger("closenai.delivery")

# 日志行前缀：Loki 侧按这个串把交付指标从普通日志里切出来，改了要同步告警规则。
METRIC_MARKER = "DELIVERY-METRIC"
ALERT_MARKER = "DELIVERY-ALERT"


class DeliveryEvent:
    """付费链路上要能数出来的七个点（工作单 §怎么做.1 逐字对应）。"""

    IAP_VERIFY_STARTED = "iap_verify_started"
    IAP_VERIFY_SUCCEEDED = "iap_verify_succeeded"
    IAP_VERIFY_FAILED = "iap_verify_failed"
    ARCHIVE_GENERATE_STARTED = "archive_generate_started"
    ARCHIVE_GENERATE_SUCCEEDED = "archive_generate_succeeded"
    ARCHIVE_GENERATE_FAILED = "archive_generate_failed"
    ARCHIVE_DOWNLOAD_REQUESTED = "archive_download_requested"


DELIVERY_EVENTS = (
    DeliveryEvent.IAP_VERIFY_STARTED,
    DeliveryEvent.IAP_VERIFY_SUCCEEDED,
    DeliveryEvent.IAP_VERIFY_FAILED,
    DeliveryEvent.ARCHIVE_GENERATE_STARTED,
    DeliveryEvent.ARCHIVE_GENERATE_SUCCEEDED,
    DeliveryEvent.ARCHIVE_GENERATE_FAILED,
    DeliveryEvent.ARCHIVE_DOWNLOAD_REQUESTED,
)

# 中继这一格到底成没成，四种机器码各有各的含义——把它们混成 True/False 就丢了
# 「用户没同意」与「平台没配」这两种完全不同的运营结论。
RELAY_RELAYED = "relayed"
RELAY_DROPPED = "dropped"
RELAY_NO_CONSENT = "no_consent"
# 「这一格手里没有用户行」——两种成因共用一个码：账号确实不存在（收据打到已注销的
# distinct_id 上），或事件发生在读库之前（`iap_verify_started` 就是这个形状）。
# 为什么不拆成两个码：拆了就要在发射点多读一次库才能分清，而那一次读本身可能失败，
# 届时又要第三个码。日志行里的 event 名已经分得清是哪一步，运营侧看 event+relay 足够。
RELAY_NO_USER = "no_user"


def emit(
    event: str,
    *,
    user: Optional[User] = None,
    user_id: Optional[str] = None,
    **properties: Any,
) -> str:
    """记一条交付事件：日志永远写，PostHog 只在有同意时中继。返回中继机器码。

    `user` 与 `user_id` 分开是必要的：校验失败的收据可能属于一个已经不存在的用户，
    那时手里只有 id、没有行——`relay=no_user` 就是"这条只进了日志"的诚实说法，
    而不是假装它也被分析平台收到了。
    """
    props = analytics_service.anonymize_properties(properties)
    if user is not None:
        if user.analytics_consent:
            relay = (
                RELAY_RELAYED
                if analytics_service.relay_server_event(user.id, event, props)
                else RELAY_DROPPED
            )
        else:
            relay = RELAY_NO_CONSENT
        uid = user.id
    else:
        relay = RELAY_NO_USER
        uid = user_id or "-"
    rendered = " ".join(f"{key}={props[key]}" for key in sorted(props)) or "-"
    logger.info(
        "%s event=%s user=%s relay=%s %s", METRIC_MARKER, event, uid, relay, rendered
    )
    return relay


def alert(message: str, **fields: Any) -> None:
    """ERROR 级告警行：供 Loki/Alertmanager 按 `DELIVERY-ALERT` 前缀抓。

    与 `emit` 分开是因为受众不同：metric 行给看板算比率，alert 行要能叫醒人。
    两者共用同一组机器码键，所以同一件事在两条通道里不会各说一套。
    """
    rendered = " ".join(f"{key}={value}" for key, value in sorted(fields.items())) or "-"
    logger.error("%s %s %s", ALERT_MARKER, message, rendered)


def _delivered_exists() -> Any:
    """相关子查询：该买断用户在**付费之后**有没有成功生成过档案。

    `created_at >= purchase_date` 而不是"任意一行"：老账号在付费前就导过档案
    （订阅期内的每月免费额度）是常事，把那种历史行算成交付证人，等于把
    「买断后一次都没出货」的人统计成已交付——那正是本指标要抓的那一格。
    """
    return (
        select(ShareExport.id)
        .where(
            ShareExport.user_id == UserEntitlement.user_id,
            ShareExport.period == LIFE_ARCHIVE_PERIOD,
            ShareExport.created_at >= UserEntitlement.purchase_date,
        )
        .exists()
    )


async def audit_paid_delivery(db: AsyncSession) -> dict[str, Any]:
    """每日交付审计：数「付了钱、过了宽限期、至今没有成功交付」的买断用户。

    判据（工作单 §怎么做.3）：人群是 `user_entitlements` 里未撤销的买断用户；
    宽限期 `delivery_audit_grace_hours`（默认 24h）内的新付费不计——刚买还没点导出
    不算交付失败；超过宽限期而 `share_exports` 里没有付费之后的成功行即计入。

    为什么阈值默认是 0（超过 0 人即告警）而不是"若干人"：独立运营期一个付费用户
    拿不到东西就是必须知道的事故，把阈值抬到"能容忍几个失败"只会让这条指标
    常年绿、常年不说真话。

    口径边界（登记在这里，别把它读成"下载成功率"）：分母仍是**生成成功**落库的档案行。
    `archive_download_requested` 只有日志面事件、库里没有下载结果列，所以"PDF 排版
    在生成成功之后炸掉"这一段靠 `emit(... reason=pdf_render_failed)` 的日志告警覆盖，
    不在这条审计的计数里。要把它算进交付率需要给 `share_exports` 加一列＋迁移，
    不在本批工作单的四步里。
    """
    now = utc_now()
    grace_cutoff = now - timedelta(hours=settings.delivery_audit_grace_hours)
    active = UserEntitlement.revoked_at.is_(None)

    paid_users = await db.scalar(
        select(func.count(distinct(UserEntitlement.user_id))).where(active)
    )
    undelivered_users = await db.scalar(
        select(func.count(distinct(UserEntitlement.user_id)))
        .where(active, UserEntitlement.purchase_date <= grace_cutoff, not_(_delivered_exists()))
    )
    # 退款侧分子：撤销过权益的用户数。工作单 §为什么 里"退款率无法算"的那一半，
    # 这一格库里本来就有（revoked_at），只是没人数过。
    revoked_users = await db.scalar(
        select(func.count(distinct(UserEntitlement.user_id))).where(
            UserEntitlement.revoked_at.isnot(None)
        )
    )

    paid = int(paid_users or 0)
    undelivered = int(undelivered_users or 0)
    revoked = int(revoked_users or 0)
    threshold = settings.delivery_audit_alert_threshold
    is_alert = undelivered > threshold

    result: dict[str, Any] = {
        "paid_users": paid,
        "undelivered_users": undelivered,
        "revoked_users": revoked,
        "grace_hours": settings.delivery_audit_grace_hours,
        "threshold": threshold,
        "alert": is_alert,
    }
    logger.info(
        "%s event=delivery_audit paid_users=%s undelivered_users=%s revoked_users=%s "
        "grace_hours=%s threshold=%s alert=%s",
        METRIC_MARKER,
        paid,
        undelivered,
        revoked,
        settings.delivery_audit_grace_hours,
        threshold,
        is_alert,
    )
    if is_alert:
        alert(
            "event=delivery_audit_alert",
            undelivered_users=undelivered,
            paid_users=paid,
            threshold=threshold,
        )
    return result
