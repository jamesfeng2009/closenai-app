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
- posthog 未安装 / 未配置（API key 为空）时服务内 no-op，不影响主流程；
  但 no-op 不等于送出去了 —— 这批一律计入 dropped，见 relay_batch
- 注销（purge_user）必须连带抹除 PostHog 侧数据：见 erase_person_data
"""
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.time_utils import utc_now
from backend.repositories import user_repository

logger = logging.getLogger("closenai.analytics")

# 注销抹除走 Personal API 的超时：purge 链路里同步等待，但绝不因外部平台抖动而卡住
POSTHOG_ERASE_TIMEOUT_SECONDS = 10

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
        # 失败原因的**机器码**（`iap_verify_failed`／`pdf_render_failed` 这类，T1-04）。
        # 注册进来的代价是"多一个可以出网的键"，所以口径必须是枚举值而非自由文本：
        # 自由文本会把异常消息（可能含用户内容）带进分析平台，那就踩了上面那条红线。
        "reason",
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


def anonymize_properties(properties: dict | None) -> dict:
    """匿名化：仅保留白名单内的行为属性，其余（含内容字段）一律丢弃。

    值为 None 的属性同样剔除，避免空字段污染分析数据。

    公开成函数名（T1-04）：付费交付事件要写日志，而日志面和 PostHog 面必须共用
    **同一张**白名单。留着 `_anonymize` 私有，第二条通道就只能自己抄一份键集——
    抄出来的那份一旦漂移，红线就退化成"两处各自守着一个不同的名单"。
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
) -> bool:
    """把单条匿名事件中继到 PostHog；**返回这条到底送出去了没有**。

    独立成函数便于测试注入桩（测试环境不安装 posthog，直接替换本函数断言调用）。

    为什么必须是返回值而不是 no-op（D-G18，2026-10-17）：返回值是 `relay_batch` 计数的唯一
    依据，而那个计数在整条链路上是**只有后端能说真话的一格**——未装/未配 key 时静默 return
    又照原样 `relayed += 1`，等于对下游宣称「这批已经落到分析平台了」，而实际一条都没出门。
    口径与 `erase_person_data:122-124` 一致：没做的事要如实回报，不伪装成成功。
    """
    try:
        import posthog
    except Exception:  # noqa: BLE001  posthog 未安装 → 没送出去
        logger.debug("posthog 未安装，事件中继未发生")
        return False
    if not settings.posthog_api_key:
        return False
    posthog.project_api_key = settings.posthog_api_key
    posthog.host = settings.posthog_host
    try:
        posthog.capture(
            distinct_id=distinct_id,
            event=event,
            properties=properties,
            timestamp=occurred_at,
        )
    except Exception as exc:  # noqa: BLE001  中继失败不阻塞主流程，但不算送出去
        logger.warning("posthog 中继失败: %s", exc)
        return False
    return True


def relay_server_event(distinct_id: str, event: str, properties: dict) -> bool:
    """服务端业务事件的中继入口（T1-04 付费交付可观测用）。

    与 `relay_batch` 的区别只在"谁发起"：前端那批带着自己的 occurred_at，服务端这条
    就用当前时刻；两者共用同一条 `_relay_to_posthog` 与**同一张**白名单。
    为什么不新开一条直连 SDK 的路：那样就等于让服务端事件绕开匿名化，
    而"只有行为元数据能出网"这条红线不该因为发起方是服务端就少一道闸。

    consent 判定不在这里：调用方手里已经有 user 行（`analytics_consent` 是 users 列），
    由调用方决定是否把 user 传进来——没有同意就不该走到这个函数。
    """
    return _relay_to_posthog(
        distinct_id, event, anonymize_properties(properties), utc_now()
    )


async def erase_person_data(distinct_id: str) -> dict:
    """注销第二步的收尾：将本人在 PostHog 侧的人物记录 + 事件 + 录屏一并删除。

    为什么不能复用 capture 通道：posthog-python 客户端库根本不提供删除接口
    （实测 wheel 7.60.1：模块级与 CapturedClient 均无 delete_person / delete_data），
    删除只能走 Personal API —— POST {host}/api/person/bulk_delete/
    body {"distinct_ids":[...], "delete_events":true, "delete_recordings":true}，
    鉴权 Authorization: Bearer <personal_api_key>
    （证据：posthog/api/person.py 的 PersonBulkDeleteRequestSerializer 支持
     distinct_ids 直传、无需先查 person uuid；posthog/auth.py
     PersonalAPIKeyAuthentication.keyword == "Bearer"）。

    红线：
    - 未配置 personal key → no-op 并告警（本地库已物理删除，外部平台残留要看得见）
    - 任何异常/非 2xx 只记日志，绝不抛出——不能因分析平台抖动让注销失败
    """
    if not settings.posthog_personal_api_key:
        logger.warning("PostHog Personal API key 未配置，跳过人物数据删除 user=%s", distinct_id)
        return {"erased": False, "reason": "not_configured"}

    url = f"{settings.posthog_host.rstrip('/')}/api/person/bulk_delete/"
    try:
        async with httpx.AsyncClient(timeout=POSTHOG_ERASE_TIMEOUT_SECONDS) as client:
            res = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.posthog_personal_api_key}"},
                json={
                    "distinct_ids": [distinct_id],
                    "delete_events": True,
                    "delete_recordings": True,
                },
            )
    except Exception as exc:  # noqa: BLE001 - 外部平台故障不阻断注销
        logger.warning("PostHog 数据删除请求失败 user=%s: %s", distinct_id, exc)
        return {"erased": False, "reason": "request_failed"}

    if res.status_code in (200, 202):
        logger.info("PostHog 已排队删除人物数据 user=%s", distinct_id)
        return {"erased": True, "status": res.status_code}
    # 不回显响应体：可能包含其他账号标识
    logger.warning("PostHog 数据删除返回非成功状态 user=%s status=%s", distinct_id, res.status_code)
    return {"erased": False, "reason": f"http_{res.status_code}"}


class AnalyticsService:
    """分析中继服务。"""

    async def relay_batch(
        self, db: AsyncSession, user_id: str, events: list
    ) -> dict:
        """中继一批前端离线事件，返回 {"relayed": n, "dropped": m}。

        校验用户 analytics_consent（未同意 / 用户不存在 → 整批丢弃）；
        匿名化后按 occurred_at 真实时间上报 PostHog。

        **计数只认真正送出去的那几条**（D-G18）：`relayed + dropped == len(events)`，
        未同意与「中继没发生」都落 `dropped`。为什么这一格不许含糊：它是整条链路上
        只有后端能说真话的地方——前端 `analytics.ts:103-106` 拿到 200 就 markSynced +
        cleanupSynced，压根没读 dropped（已登记的缺陷，本批不改前端），所以这里的数字
        一旦说谎，两侧就再没有任何地方留着「这批其实没落到分析平台」这件事。
        """
        user = await user_repository.get_user(db, user_id)
        if not user or not user.analytics_consent:
            return {"relayed": 0, "dropped": len(events)}
        relayed = 0
        for ev in events:
            props = anonymize_properties(ev.properties)
            if _relay_to_posthog(user_id, ev.event, props, ev.occurred_at):
                relayed += 1
        return {"relayed": relayed, "dropped": len(events) - relayed}


analytics_service = AnalyticsService()
