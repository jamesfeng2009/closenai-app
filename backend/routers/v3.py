"""洞察面与目标/行动面的 BFF 代理（T3-03／T4-02，ADR-001/002）。

端点（对外）→ 内部面：
    GET   /api/v1/insights                    → GET   /internal/v3/insights
    GET   /api/v1/insights/{insight_id}       → GET   /internal/v3/insights/{id}
    POST  /api/v1/insights/{insight_id}/feedback → POST /internal/v3/insights/{id}/feedback
    GET   /api/v1/goals                       → GET   /internal/v3/goals
    POST  /api/v1/goals                       → POST  /internal/v3/goals
    PATCH /api/v1/goals/{goal_id}             → PATCH /internal/v3/goals/{id}
    POST  /api/v1/goals/{goal_id}/actions     → POST  /internal/v3/goals/{id}/actions
    GET   /api/v1/actions                     → GET   /internal/v3/actions
    PATCH /api/v1/actions/{action_id}         → PATCH /internal/v3/actions/{id}

这一层刻意薄到只剩三件事：认证（`get_current_user`）、拼目标路径、把 `data` 装回信封。
查询参数一律声明成 `str | None` 并原样转发——包括 `limit=abc` 这种明显不合法的值：
校验只许发生在 Go（唯一校验面），否则同一个错误在 BFF 是 422、在 Go 是 40001，
客户端拿到两种形状而前端只认一种。注册前缀沿用仓内约定（`main.py` 统一带
`prefix="/api/v1"`），与工单 §怎么做.1 的 `APIRouter(prefix=...)` 写法不同，见结案行登记。

请求体一律不收 Pydantic 模型（九条端点同一条口径，见 `_json_body`）：目标面的
`action` 动词、`title` 长度、`target_at` 格式在 Go 各有判据与错误码，这里一旦建模，
「BFF 先 422」就会把 Go 那 400/409 的分界抹平，而这条分界是工单 §验收 的全部内容。
"""
from fastapi import APIRouter, Depends, Request

from backend.core.auth import get_current_user
from backend.core.db_route import CommitBeforeResponseRoute
from backend.core.errors import AppException, ErrorCode
from backend.core.response import success
from backend.services import v3_proxy

# `CommitBeforeResponseRoute`（闸 `test_db_commit_boundary.py` 的闸门 1 逐 router 扫，无例外名单）：
# 这一面**不开事务**——九个 handler 没有任何取数依赖，`request.state.db_session` 永远不存在，
# 那个提交分支对这条链是惰性的。挂它而不给闸开豁免的理由：豁免名单一旦存在，
# 「新加一个会写库的 router 却忘了提交边界」这条回归就有一个专门给它留着的洞。
router = APIRouter(route_class=CommitBeforeResponseRoute)


async def _json_body(request: Request) -> dict:
    """读原始 JSON 体，不合形就 400——**不**在这里解释字段。

    九条端点共用这一个构造点：如果四条 POST/PATCH 各写一遍 `await request.json()`，
    就有人会给其中一条加上「先取 user_id」的便利，而 ADR-002 那一格（身份只从
    header 进来）恰好是靠「body 里的 user_id 一路带到 Go 才被 DisallowUnknownFields 挡掉」
    证出来的。BFF 提前拆包等于替 Go 把这条判据吞掉。
    """
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        raise AppException(ErrorCode.INVALID_PARAMS,
                           "请求体不是合法 JSON / body is not valid JSON") from exc
    return body


@router.get("/insights")
async def list_insights(status: str = None, limit: str = None, cursor: str = None,
                        user_id: str = Depends(get_current_user)) -> dict:
    data = await v3_proxy.get_json(v3_proxy.PATH_INSIGHT_LIST, user_id,
                                   params=v3_proxy.list_params(
                                       {"status": status, "limit": limit, "cursor": cursor}))
    return success(data)


@router.get("/insights/{insight_id}")
async def get_insight(insight_id: str,
                      user_id: str = Depends(get_current_user)) -> dict:
    data = await v3_proxy.get_json(v3_proxy.insight_detail_path(insight_id), user_id)
    return success(data)


@router.post("/insights/{insight_id}/feedback")
async def post_insight_feedback(insight_id: str, request: Request,
                                user_id: str = Depends(get_current_user)) -> dict:
    body = await _json_body(request)
    data = await v3_proxy.post_json(v3_proxy.feedback_path(insight_id), user_id, body=body)
    return success(data)


# ── T4-02：目标面 ────────────────────────────────────────────────────────────

@router.get("/goals")
async def list_goals(status: str = None, limit: str = None,
                     user_id: str = Depends(get_current_user)) -> dict:
    # 没有 cursor：Go 那侧的目标列表用 truncated 而不是游标（人手立的东西，见
    # domain.GoalListView 的头注释）。这里若转发 cursor，客户端会以为能翻页。
    data = await v3_proxy.get_json(v3_proxy.PATH_GOAL_LIST, user_id,
                                   params=v3_proxy.list_params({"status": status,
                                                                "limit": limit}))
    return success(data)


@router.post("/goals")
async def create_goal(request: Request,
                      user_id: str = Depends(get_current_user)) -> dict:
    body = await _json_body(request)
    data = await v3_proxy.post_json(v3_proxy.PATH_GOAL_LIST, user_id, body=body)
    return success(data)


@router.patch("/goals/{goal_id}")
async def transition_goal(goal_id: str, request: Request,
                          user_id: str = Depends(get_current_user)) -> dict:
    body = await _json_body(request)
    data = await v3_proxy.patch_json(v3_proxy.goal_detail_path(goal_id), user_id, body=body)
    return success(data)


@router.post("/goals/{goal_id}/actions")
async def create_action(goal_id: str, request: Request,
                        user_id: str = Depends(get_current_user)) -> dict:
    body = await _json_body(request)
    data = await v3_proxy.post_json(v3_proxy.goal_actions_path(goal_id), user_id, body=body)
    return success(data)


# ── T4-02：行动面 ────────────────────────────────────────────────────────────

@router.get("/actions")
async def list_actions(status: str = None, limit: str = None,
                       user_id: str = Depends(get_current_user)) -> dict:
    data = await v3_proxy.get_json(v3_proxy.PATH_ACTION_LIST, user_id,
                                   params=v3_proxy.list_params({"status": status,
                                                                "limit": limit}))
    return success(data)


@router.patch("/actions/{action_id}")
async def transition_action(action_id: str, request: Request,
                            user_id: str = Depends(get_current_user)) -> dict:
    body = await _json_body(request)
    data = await v3_proxy.patch_json(v3_proxy.action_detail_path(action_id), user_id,
                                     body=body)
    return success(data)
