"""反思提示库路由（KG-26）。

端点：
    GET /api/v1/reflection-prompts — 反思提示库（只读内容，不耗配额、不查同意）

为什么不挂在 `/api/v1/reflections/prompts`（原方案 §6.1.2 写的那个形状）：
`reflections` 是 N8 的「键先行、path 不上刀」档（`PATHLESS_KEYS`），将来那一刀是把
**整段前缀** `/api/v1/reflections`（`pathType: Prefix`）指给 go-api。子路径会跟着一起搬，
而提示库的内容只在 Python 侧（`backend/content/reflection_prompts.json`），Go 没有它——
放量那天这条端点会静默变 404。`backend/tests/test_k8s_manifests.py` 的
`test_cutover_keys_without_ingress_path_stay_unrouted` 第 4 格就是在拦这件事。
所以它放在切流前缀之外，作为一条**永不进 Go** 的 Python 自有面。
"""
from fastapi import APIRouter, Depends, Query

from backend.core.db_route import CommitBeforeResponseRoute
from backend.core.language import get_request_language
from backend.core.response import success
from backend.services import prompt_library

# 全仓每个 router 都挂 route_class（test_db_commit_boundary.py 第 1 格）；
# 这条端点不建会话，所以那次 commit 是空转，不是第二事务。
router = APIRouter(route_class=CommitBeforeResponseRoute)


@router.get("/reflection-prompts")
async def list_prompts(
    cluster: str | None = Query(None, description="只取某一簇；未登记的簇名返回空 prompts"),
    language: str = Depends(get_request_language),
):
    """反思提示库（KG-26）。

    静态内容、无用户数据、不耗 LLM 配额，因此不查 AI 同意、不进配额门。
    语言只有 en/zh 两版：其余 13 个语言回退 en，并在响应里显式给
    `language_used` + `fallback=true`，由前端标注「未翻译」而不是让用户自己猜。
    """
    return success(prompt_library.list_prompts(cluster=cluster, language=language))
