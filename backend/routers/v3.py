"""洞察面的 BFF 代理（T3-03，ADR-001/002）。

端点（对外）→ 内部面：
    GET  /api/v1/insights                → GET  /internal/v3/insights
    GET  /api/v1/insights/{insight_id}   → GET  /internal/v3/insights/{id}
    POST /api/v1/insights/{insight_id}/feedback → POST /internal/v3/insights/{id}/feedback

这一层刻意薄到只剩三件事：认证（`get_current_user`）、拼目标路径、把 `data` 装回信封。
查询参数一律声明成 `str | None` 并原样转发——包括 `limit=abc` 这种明显不合法的值：
校验只许发生在 Go（唯一校验面），否则同一个错误在 BFF 是 422、在 Go 是 40001，
客户端拿到两种形状而前端只认一种。注册前缀沿用仓内约定（`main.py` 统一带
`prefix="/api/v1"`），与工单 §怎么做.1 的 `APIRouter(prefix=...)` 写法不同，见结案行登记。
"""
from fastapi import APIRouter, Depends, Request

from backend.core.auth import get_current_user
from backend.core.db_route import CommitBeforeResponseRoute
from backend.core.errors import AppException, ErrorCode
from backend.core.response import success
from backend.services import v3_proxy

# `CommitBeforeResponseRoute`（闸 `test_db_commit_boundary.py` 的闸门 1 逐 router 扫，无例外名单）：
# 这一面**不开事务**——三个 handler 没有任何取数依赖，`request.state.db_session` 永远不存在，
# 那个提交分支对这条链是惰性的。挂它而不给闸开豁免的理由：豁免名单一旦存在，
# 「新加一个会写库的 router 却忘了提交边界」这条回归就有一个专门给它留着的洞。
router = APIRouter(route_class=CommitBeforeResponseRoute)


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
    # body 原样转发，不声明 Pydantic 模型：一旦在这里定义字段，BFF 就成了第二个校验面，
    # Go 那条 `DisallowUnknownFields`（挡住 body 里塞 user_id 的判据）会被先一步绕开。
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        raise AppException(ErrorCode.INVALID_PARAMS,
                           "请求体不是合法 JSON / body is not valid JSON") from exc
    data = await v3_proxy.post_json(v3_proxy.feedback_path(insight_id), user_id, body=body)
    return success(data)
