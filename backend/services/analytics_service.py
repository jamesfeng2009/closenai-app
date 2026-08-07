"""
分析服务（PostHog 中继）。

职责：接收前端本地 analytics_events 队列，经 consent 校验 + 匿名化后中继到 PostHog。
业务代码不直接依赖 PostHog SDK，统一经本服务封装。

处理顺序（对应 /analytics/batch 端点）：
1. 校验用户 analytics_consent（未同意 / 用户不存在 → 整批丢弃）
2. 匿名化：仅保留白名单内的行为属性，剔除任何内容字段
3. 用事件携带的 occurred_at（真实发生时间）上报 PostHog —— 保证离线延迟事件口径准确

红线：
- 仅行为元数据，禁止携带任何敏感内容（反思文本 / 记忆 / 对话原文 / 情绪具体值）
- posthog 未安装 / 未配置（API key 为空）时服务内 no-op，不影响主流程
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.repositories import user_repository

logger = logging.getLogger("closenai.analytics")

# 允许的匿名行为属性键（白名单）：仅保留行为元数据，剔除任何内容字段。
# 前端可能携带 screen/feature/action/tier 等行为上下文，以及任何被误塞的文本内容。
# 不在白名单内的键（如 reflection_text / memory_content / message / mood_emoji）一律丢弃。
ALLOWED_PROPERTY_KEYS = frozenset(
    {
        "screen",
        "screen_from",
        "screen_to",
        "section",
        "feature",
        "action",
        "source",
        "trigger",
        "result",
        "duration_ms",
        "count",
        "value",
        "tier",
        "language",
        "platform",
        "app_version",
        "locale",
        "is_new_user",
        "streak_count",
        "relationship_level",
    }
)


def _anonymize(properties: dict | None) -> dict:
    """匿名化：仅保留白名单内的行为属性，其余（含内容字段）一律丢弃。

    值为 None 的属性同样剔除，避免空字段污染分析数据。
    """
    if not properties:
        return {}
    out: dict = {}
    for key, val in properties.items():
        if key not in ALLOWED_PROPERTY_KEYS:
            continue
        if val is None:
            continue
        out[key] = val
    return out


def _relay_to_posthog(
    distinct_id: str,
    event: str,
    properties: dict,
    occurred_at,
) -> None:
    """把单条匿名事件中继到 PostHog（未安装 / 未配置时 no-op）。

    独立成函数便于测试注入桩（测试环境不安装 posthog，直接替换本函数断言调用）。
    """
    try:
        import posthog
    except Exception:  # noqa: BLE001  posthog 未安装 → no-op
        logger.debug("posthog 未安装，跳过中继")
        return
    if not settings.posthog_api_key:
        return
    posthog.project_api_key = settings.posthog_api_key
    posthog.host = settings.posthog_host
    try:
        posthog.capture(
            distinct_id=distinct_id,
            event=event,
            properties=properties,
            timestamp=occurred_at,
        )
    except Exception as exc:  # noqa: BLE001  中继失败不阻塞主流程
        logger.warning("posthog 中继失败: %s", exc)


class AnalyticsService:
    """分析中继服务。"""

    async def relay_batch(
        self, db: AsyncSession, user_id: str, events: list
    ) -> dict:
        """中继一批前端离线事件。

        校验用户 analytics_consent（未同意 / 用户不存在 → 整批丢弃）；
        匿名化后按 occurred_at 真实时间上报 PostHog。
        返回 {"relayed": n, "dropped": m}。
        """
        user = await user_repository.get_user(db, user_id)
        if not user or not user.analytics_consent:
            return {"relayed": 0, "dropped": len(events)}
        relayed = 0
        for ev in events:
            props = _anonymize(ev.properties)
            _relay_to_posthog(user_id, ev.event, props, ev.occurred_at)
            relayed += 1
        return {"relayed": relayed, "dropped": 0}


analytics_service = AnalyticsService()
