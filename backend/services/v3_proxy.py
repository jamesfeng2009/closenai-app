"""T3-03：BFF → Go 内部面（`/internal/v3/*`）的转发层（ADR-001/002，闸 G-03）。

这一层唯一的职责是把已认证的 `/api/v1/*` 请求搬到内部面，再把 Go 的信封原样还回去。
它不解析业务字段、不自己拼状态、不做权益判断：可见性判据只有一份（Go 的
`domain.InsightBrowser`），错误映射只有一份（Go 的 `internal/httpapi`），
权益只有一份（`backend/services/quota_service.py`）。

为什么"薄"值得单独立一条架构闸（`tests/test_bff_is_thin.py`）而不是靠自觉：
这一层离业务最近，每次"顺手在这里加一个判断"都会造出第二个事实源，而第二个事实源
坏起来的样子是同一颗洞察在两个屏上给出不同的状态——没有报错，只有用户看见的不一致。

超时是包内常量而不是 env 键（同 D-G12「键存在但没人读＝假旋钮」）：这一面的响应体积
由 Go 侧决定，改这个数的人应该同时改两条端点的形状闸，而不是在 ConfigMap 里试。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from backend.config import settings
from backend.core.errors import AppException, ErrorCode
from backend.core.logging import get_trace_id

# 一次拨号的总预算（秒）。连接、读、写三段都用它：内部面在集群内，5 秒还没回
# 就等于这一面挂了，让它挂得明确比让它慢慢吊住用户的前端更有用。
GO_TIMEOUT_SECONDS = 5.0

# 三个头名逐字取 ADR-002 与 Go 侧 httpapi/v3.go 的常量；改任何一个都要两侧同批。
SERVICE_TOKEN_HEADER = "X-Service-Token"
USER_ID_HEADER = "X-User-Id"
TRACE_HEADER = "X-Trace-Id"

# 失败面唯一的对外文案。带中英两段与 Go 信封里其余 message 同形，前端不翻译它也能显示。
UNAVAILABLE_MESSAGE = "服务暂时不可用 / Service temporarily unavailable"

# 内部面允许被 BFF 拨出去的路径前缀（id 由下面几个函数转义后拼进去）。
# 这是一份**白名单**而不是枚举业务：它挡的是"客户端给一个任意 path 让 BFF 替它去拨内部面"
# 这一种 SSRF 形状。
PATH_INSIGHT_LIST = "/internal/v3/insights"
PATH_INSIGHT_DETAIL = "/internal/v3/insights/"
PATH_INSIGHT_FEEDBACK = "/internal/v3/insights/{}/feedback"
# T4-02：目标与行动面（六条）。目标的新建与列表共用一个常量，行动挂在目标下的那条
# 用 `{}` 占位——**goal_id 只从 path 来**，与 Go 侧 `/goals/{id}/actions` 同一格判据。
PATH_GOAL_LIST = "/internal/v3/goals"
PATH_GOAL_DETAIL = "/internal/v3/goals/"
PATH_GOAL_ACTIONS = "/internal/v3/goals/{}/actions"
PATH_ACTION_LIST = "/internal/v3/actions"
PATH_ACTION_DETAIL = "/internal/v3/actions/"
# T4-03：行动结果面。`{}` 占位的是 **action_id**（只从 path 来，与 Go 侧
# `/actions/{id}/outcomes` 同一格判据）；这一条刻意不复用 PATH_ACTION_DETAIL，
# 否则「拼错了后缀」在 BFF 侧看不出来，而 Go 那边只会回一个形状陌生的 404。
PATH_ACTION_OUTCOME = "/internal/v3/actions/{}/outcomes"

# 列表面转发的 query 键。其余一律不带 ⇒ 客户端不能经这一层往内部面塞身份或筛选条件
# （Go 只认 X-User-Id，但"塞了也没用"和"根本进不去"是两种可审计的强度）。
LIST_QUERY_KEYS = ("status", "limit", "cursor")


def insight_detail_path(insight_id: str) -> str:
    """把路径段转义后再拼（`safe=""`：斜杠、问号、井号一律吃掉）。

    不转义的话，`insight_id = "x?user_id=victim"` 会在 URL 解析阶段长出第二个 query，
    而这一层的存在意义之一正是"客户端不能替自己声明身份"。
    """
    return PATH_INSIGHT_DETAIL + quote(insight_id, safe="")


def feedback_path(insight_id: str) -> str:
    return PATH_INSIGHT_FEEDBACK.format(quote(insight_id, safe=""))


def goal_detail_path(goal_id: str) -> str:
    return PATH_GOAL_DETAIL + quote(goal_id, safe="")


def goal_actions_path(goal_id: str) -> str:
    return PATH_GOAL_ACTIONS.format(quote(goal_id, safe=""))


def action_detail_path(action_id: str) -> str:
    return PATH_ACTION_DETAIL + quote(action_id, safe="")


def action_outcome_path(action_id: str) -> str:
    return PATH_ACTION_OUTCOME.format(quote(action_id, safe=""))


def _client_factory(timeout: float) -> httpx.AsyncClient:
    """唯一的 httpx 构造点；单测在这里换 transport，生产代码一行不改。"""
    return httpx.AsyncClient(timeout=timeout)


def build_headers(user_id: str) -> dict[str, str]:
    """出站三个头的**唯一**构造点（三条端点共用，所以"少发一个头"只可能坏一次）。

    密钥与 trace 都现取（配置里的服务密钥、当前请求上下文里的 trace），
    这一层不接受调用方传入的头——那等于给外面一个改写凭据的口子。
    """
    token = settings.go_service_token.strip()
    if not token:
        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, UNAVAILABLE_MESSAGE)
    if not user_id:
        raise AppException(ErrorCode.MISSING_TOKEN, "缺少身份凭证 / Missing token")
    headers = {SERVICE_TOKEN_HEADER: token, USER_ID_HEADER: user_id}
    trace = get_trace_id().strip()
    if trace:
        # 无 trace 时**不带**这个头，而不是带一个空串：Go 对空值会另生成一条 trace，
        # 带空串等于让两侧各有一份「这条请求没有 trace」的解释。
        headers[TRACE_HEADER] = trace
    return headers


def dial_target(path: str, user_id: str) -> tuple[str, dict[str, str]]:
    """拼出目标 URL 与头；配置不齐时在**拨号之前**失败。

    为什么先查配置再连：`go_api_url` 为空时若照样发起请求，用户看到的是 5 秒空等，
    日志里是一条 httpx 的 `UnsupportedProtocol`——那长得像 Go 挂了，而真相是没人配地址。
    """
    base = (settings.go_api_url or "").strip().rstrip("/")
    if not base:
        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, UNAVAILABLE_MESSAGE)
    return f"{base}{path}", build_headers(user_id)


def _unwrap(resp: httpx.Response) -> Any:
    """把 Go 的信封折成「成功回 data / 失败回 AppException」。

    成功与否只看信封里的 `code`，不看 HTTP 状态：Go 的两半是同一个判据折出来的
    （`envelope.go` 写 code、`writeJSONError` 写 http 状态），这里只该有一处读它。
    HTTP 状态原样带进 `http_status`，所以 409/404/400 到客户端仍是 409/404/400。
    """
    try:
        envelope = resp.json()
    except ValueError as exc:
        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, UNAVAILABLE_MESSAGE) from exc
    if not isinstance(envelope, dict) or "code" not in envelope:
        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, UNAVAILABLE_MESSAGE)
    code = envelope["code"]
    message = envelope.get("message") or UNAVAILABLE_MESSAGE
    data = envelope.get("data")
    if code == 200:
        return data
    raise AppException(int(code), str(message), data=data, http_status=resp.status_code)


async def forward(method: str, path: str, user_id: str, *,
                  params: dict[str, str] | None = None,
                  payload: Any = None) -> Any:
    """发一次内部面请求并返回信封里的 `data`。

    `httpx.HTTPError`（连接失败、超时、协议错）与不合形的响应一律折成 503：
    这一层没有"重试另一个上游"或"降级到本地数据"的余地，如实说不可用是唯一诚实的回答。
    """
    url, headers = dial_target(path, user_id)
    try:
        async with _client_factory(GO_TIMEOUT_SECONDS) as client:
            resp = await client.request(method, url, headers=headers,
                                        params=params, json=payload)
    except httpx.HTTPError as exc:
        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, UNAVAILABLE_MESSAGE) from exc
    return _unwrap(resp)


async def get_json(path: str, user_id: str, *,
                   params: dict[str, str] | None = None) -> Any:
    return await forward("GET", path, user_id, params=params)


async def post_json(path: str, user_id: str, *, body: Any) -> Any:
    return await forward("POST", path, user_id, payload=body)


async def patch_json(path: str, user_id: str, *, body: Any) -> Any:
    """目标/行动的转移端点用 PATCH（工单 T4-02 §怎么做.3 的动词，逐字带到 Go）。

    单独一条而不是把 method 交给调用方拼字符串：这一面能出现的动词就 GET/POST/PATCH
    三种，收成三个入口才有「有人新加了一种动词」的唯一改动点。
    """
    return await forward("PATCH", path, user_id, payload=body)


def list_params(query: dict[str, Any]) -> dict[str, str]:
    """只把白名单里**有值**的键拼进 query（None 一律丢掉）。"""
    return {k: str(query[k]) for k in LIST_QUERY_KEYS
            if query.get(k) is not None}
