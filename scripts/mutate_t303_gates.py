#!/usr/bin/env python3
"""T3-03（BFF 纯转发层）的变异自证（2026-10-19）。

这一票交付的是**一层薄代理**，它的死法只有三种，每种都得有自己的靶：

* **门禁被掏空**（M01…M04、M05…M08、M19…M26）：缺配置还在拨号、少发一个头、
  把 Go 的错误折成 200、传输失败假装成功——每一格都是一句「不许」，掏掉哪一格，
  红的就必须是那格自己的用例。
* **判据分叉**（M09…M18、M27…M33）：query 白名单、path 转义、方法、身份入口，
  以及 G-03 点名的「不许在这里做业务判断」。这些坏起来不报错，只在两个面上给出不一致的答案。
* **跨进程才看得见**（E1…E3）：静态闸只能读字符串，把 Go 的字段改名、把身份写死、
  把 query 整段丢掉，源码里每个关键字都还在——只有真起一台 Go 才红得出来。
  这三条靶打在 `test_v3_proxy_e2e.py` 上，缺 go 工具链时记「未跑」，不算绿
  （[[feedback-mutation-anchor-must-reach-target]]）。

GR1…GR3 是「不该打红」的证人：G-03 判的是关键字与身份入口，不判源码行数和注释位置
（[[feedback-gate-needs-positive-witness]]：只有反向归零的闸会因为文件被删空而永远绿）。

纪律：串行执行、copy2 备份＋`filecmp(shallow=False)` 逐字节写回、不用 git checkout
（[[feedback-mutation-restore-no-git-checkout]]、[[feedback-mutation-gates-no-concurrency]]）；
每条变异前先跑基线，基线本来就不绿记「未跑」，不把环境的红记成闸的红。

用法：
    ./.venv/bin/python scripts/mutate_t303_gates.py --check
    GOROOT=/Users/fengyu/sdk/go1.27.1 ./.venv/bin/python scripts/mutate_t303_gates.py
    ./.venv/bin/python scripts/mutate_t303_gates.py --only M01,M34,E1
"""
from __future__ import annotations

import argparse
import ast
import filecmp
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENV_PY = REPO / ".venv" / "bin" / "python"

PROXY = REPO / "backend" / "services" / "v3_proxy.py"
ROUTER = REPO / "backend" / "routers" / "v3.py"
MAIN = REPO / "backend" / "main.py"
CONFIG = REPO / "backend" / "config.py"

UNIT = "backend/tests/test_v3_proxy.py"
THIN = "backend/tests/test_bff_is_thin.py"
E2E = "backend/tests/test_v3_proxy_e2e.py"

# ── 逐字锚点（`--check` 会验每一条命中恰好一次）────────────────────────────────
TWO_GUARDS = (
    "    token = settings.go_service_token.strip()\n"
    "    if not token:\n"
    "        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, UNAVAILABLE_MESSAGE)"
)
BASE_GUARD = (
    '    base = (settings.go_api_url or "").strip().rstrip("/")\n'
    "    if not base:\n"
    "        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, UNAVAILABLE_MESSAGE)"
)
IDENTITY_GUARD = (
    "    if not user_id:\n"
    '        raise AppException(ErrorCode.MISSING_TOKEN, "缺少身份凭证 / Missing token")'
)
HEADER_DICT = "    headers = {SERVICE_TOKEN_HEADER: token, USER_ID_HEADER: user_id}"
TRACE_GUARD = "    trace = get_trace_id().strip()\n    if trace:"
LIST_KEYS = 'LIST_QUERY_KEYS = ("status", "limit", "cursor")'
LIST_PARAMS_BODY = (
    "    return {k: str(query[k]) for k in LIST_QUERY_KEYS\n"
    "            if query.get(k) is not None}"
)
DETAIL_PATH_FN = '    return PATH_INSIGHT_DETAIL + quote(insight_id, safe="")'
FEEDBACK_PATH_FN = '    return PATH_INSIGHT_FEEDBACK.format(quote(insight_id, safe=""))'
REQUEST_CALL = (
    "            resp = await client.request(method, url, headers=headers,\n"
    "                                        params=params, json=payload)"
)
CODE_200 = "    if code == 200:\n        return data"
CODE_200_WITH_RAISE = (
    "    if code == 200:\n"
    "        return data\n"
    "    raise AppException(int(code), str(message), data=data, http_status=resp.status_code)"
)
ERROR_RAISE = (
    "    raise AppException(int(code), str(message), data=data, http_status=resp.status_code)"
)
JSON_GUARD = (
    "    except ValueError as exc:\n"
    "        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, UNAVAILABLE_MESSAGE) from exc"
)
ENVELOPE_GUARD = '    if not isinstance(envelope, dict) or "code" not in envelope:'
MESSAGE_FALLBACK = '    message = envelope.get("message") or UNAVAILABLE_MESSAGE'
HTTP_ERROR_GUARD = (
    "    except httpx.HTTPError as exc:\n"
    "        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, UNAVAILABLE_MESSAGE) from exc"
)
GET_JSON_LINE = '    return await forward("GET", path, user_id, params=params)'
POST_JSON_LINE = '    return await forward("POST", path, user_id, payload=body)'
TIMEOUT_CALL = "        async with _client_factory(GO_TIMEOUT_SECONDS) as client:"
PROXY_IMPORTS = "import httpx\n\nfrom backend.config import settings"
UNAVAILABLE_CONST = 'UNAVAILABLE_MESSAGE = "服务暂时不可用 / Service temporarily unavailable"'
CONFIG_URL_DEFAULT = '    go_api_url: str = ""'
CONFIG_TOKEN_DEFAULT = '    go_service_token: str = ""'
ROUTER_LIST_TAIL = (
    '                                       {"status": status, "limit": limit, "cursor": cursor}))\n'
    "    return success(data)"
)
ROUTER_LIST_DEF = (
    "async def list_insights(status: str = None, limit: str = None, cursor: str = None,\n"
    "                        user_id: str = Depends(get_current_user)) -> dict:"
)
ROUTER_DETAIL_DEF = (
    "async def get_insight(insight_id: str,\n"
    "                      user_id: str = Depends(get_current_user)) -> dict:"
)
ROUTER_POST_CALL = (
    "    data = await v3_proxy.post_json(v3_proxy.feedback_path(insight_id), user_id, body=body)"
)
ROUTER_LIST_SIG = "async def list_insights(status: str = None, limit: str = None, cursor: str = None,"
ROUTER_PROXY_IMPORT = "from backend.services import v3_proxy"
ROUTER_ROUTER_LINE = "router = APIRouter(route_class=CommitBeforeResponseRoute)"
MAIN_REGISTER = 'app.include_router(v3.router, prefix="/api/v1", tags=["v3"])'

MUTATIONS: list[tuple] = [
    # ── 一、配置门禁：不齐就在拨号之前失败 ──────────────────────────────────
    (
        "M01",
        "掏掉服务密钥判据：没配 GO_SERVICE_TOKEN 也照样拨号，缺配置从「立刻 503」变成对面回一个 403",
        [(PROXY, TWO_GUARDS,
          "    token = settings.go_service_token.strip()\n    if False:\n"
          "        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, UNAVAILABLE_MESSAGE)")],
        "pytest", f"{UNIT}::test_missing_service_token_never_dials",
        "test_missing_service_token_never_dials",
    ),
    (
        "M02",
        "掏掉目标地址判据：go_api_url 为空仍去连，用户等满 5 秒才看到 503",
        [(PROXY, BASE_GUARD,
          '    base = (settings.go_api_url or "").strip().rstrip("/")\n    if False:\n'
          "        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, UNAVAILABLE_MESSAGE)")],
        "pytest", f"{UNIT}::test_missing_go_url_never_dials",
        "test_missing_go_url_never_dials",
    ),
    (
        "M03",
        "掏掉身份判据：空 user_id 也发出 X-User-Id，Go 回 401，用户看到「我没登录」而真相是没身份",
        [(PROXY, IDENTITY_GUARD,
          '    if False:\n'
          '        raise AppException(ErrorCode.MISSING_TOKEN, "缺少身份凭证 / Missing token")')],
        "pytest", f"{UNIT}::test_headers_are_built_once_with_two_guards_and_one_omission",
        "test_headers_are_built_once_with_two_guards_and_one_omission",
    ),
    (
        "M04",
        "把 go_api_url 的默认值写成 http://go-api:8080：配置缺失从此长成一次真的连接超时",
        [(CONFIG, CONFIG_URL_DEFAULT, '    go_api_url: str = "http://go-api:8080"')],
        "pytest", f"{THIN}::test_the_two_forwarding_keys_default_to_empty_in_config",
        "test_the_two_forwarding_keys_default_to_empty_in_config",
    ),
    # ── 二、三个头（ADR-002 的字面契约）──────────────────────────────────────
    (
        "M05",
        "服务密钥头名改掉（X-Service-Token → X-Service-Key）：Go 侧读不到＝整面 403，而两侧单测各自还绿",
        [(PROXY, 'SERVICE_TOKEN_HEADER = "X-Service-Token"',
          'SERVICE_TOKEN_HEADER = "X-Service-Key"')],
        "pytest", f"{UNIT}::test_headers_path_and_query_are_verbatim",
        "test_headers_path_and_query_are_verbatim",
    ),
    (
        "M06",
        "身份头名改掉（X-User-Id → X-User）：ADR-002 的字面契约被单方面改掉，对面当这是匿名请求",
        [(PROXY, 'USER_ID_HEADER = "X-User-Id"', 'USER_ID_HEADER = "X-User"')],
        "pytest", f"{UNIT}::test_body_identity_field_is_forwarded_unchanged",
        "test_body_identity_field_is_forwarded_unchanged",
    ),
    (
        "M07",
        "不再转发 trace 头：一次用户投诉在 BFF 与 Go 两侧各有一条无关联的日志，归因断在跳与跳之间",
        [(PROXY, "        headers[TRACE_HEADER] = trace", "        pass")],
        "pytest", f"{UNIT}::test_trace_id_is_forwarded_when_the_request_has_one",
        "test_trace_id_is_forwarded_when_the_request_has_one",
    ),
    (
        "M08",
        "没有 trace 也发一个空头：Go 对空值另生成一条 trace，两侧各有一份「这条请求没有 trace」的解释",
        [(PROXY, TRACE_GUARD, "    trace = get_trace_id().strip()\n    if True:")],
        "pytest", f"{UNIT}::test_headers_are_built_once_with_two_guards_and_one_omission",
        "test_headers_are_built_once_with_two_guards_and_one_omission",
    ),
    # ── 三、query 白名单（客户端不许往内部面塞条件）───────────────────────────
    (
        "M09",
        "整段原始 query 转出去＋白名单不再筛（「顺手少写一层」的真实形状）：客户端能经这一层往内部面塞身份与任意筛选条件",
        [(ROUTER, ROUTER_LIST_DEF,
          "async def list_insights(request: Request, status: str = None, limit: str = None,\n"
          "                        cursor: str = None,\n"
          "                        user_id: str = Depends(get_current_user)) -> dict:"),
         (ROUTER, ROUTER_LIST_TAIL,
          "                                       dict(request.query_params)))\n"
          "    return success(data)"),
         (PROXY, LIST_PARAMS_BODY, "    return {k: str(v) for k, v in query.items()}")],
        "pytest", f"{UNIT}::test_unknown_query_keys_are_not_forwarded",
        "test_unknown_query_keys_are_not_forwarded",
    ),
    (
        "M10",
        "list_params 干脆不筛白名单（整个 query 直接转出去）：转发面失去了唯一那道路径白名单",
        [(PROXY, LIST_PARAMS_BODY, "    return {k: str(v) for k, v in query.items()}")],
        "pytest", f"{UNIT}::test_list_params_drops_absent_keys_only",
        "test_list_params_drops_absent_keys_only",
    ),
    (
        "M11",
        "list_params 不再丢掉 None：缺省的 status 变成字面量 \"None\" 发给 Go，列表恒空且不报错",
        [(PROXY, LIST_PARAMS_BODY,
          "    return {k: str(query[k]) for k in LIST_QUERY_KEYS if True}")],
        "pytest", f"{UNIT}::test_list_params_drops_absent_keys_only",
        "test_list_params_drops_absent_keys_only",
    ),
    (
        "M12",
        "query 键名抄错（status → state）：Go 认不出这个筛选条件，用户的「只看已发布」静默失效",
        [(PROXY, LIST_KEYS, 'LIST_QUERY_KEYS = ("state", "limit", "cursor")')],
        "pytest", f"{UNIT}::test_headers_path_and_query_are_verbatim",
        "test_headers_path_and_query_are_verbatim",
    ),
    # ── 四、path：三条内部面路径与转义 ───────────────────────────────────────
    (
        "M13",
        "详情 path 不做转义（裸拼接）：`x?user_id=victim` 在 URL 解析阶段长出第二个 query",
        [(PROXY, DETAIL_PATH_FN, "    return PATH_INSIGHT_DETAIL + insight_id")],
        "pytest", f"{UNIT}::test_path_segment_is_escaped_not_concatenated",
        "test_path_segment_is_escaped_not_concatenated",
    ),
    (
        "M14",
        "反馈 path 不做转义：GET 那条守住了、POST 那条漏了，同一个洞从另一扇门进来",
        [(PROXY, FEEDBACK_PATH_FN, "    return PATH_INSIGHT_FEEDBACK.format(insight_id)")],
        "pytest", f"{UNIT}::test_feedback_path_segment_is_escaped_too",
        "test_feedback_path_segment_is_escaped_too",
    ),
    (
        "M15",
        "列表面目标 path 改掉（insights → insight）：转发到一条 Go 没有的 pattern，客户端拿到 404 信封",
        [(PROXY, 'PATH_INSIGHT_LIST = "/internal/v3/insights"',
          'PATH_INSIGHT_LIST = "/internal/v3/insight"')],
        "pytest", f"{UNIT}::test_headers_path_and_query_are_verbatim",
        "test_headers_path_and_query_are_verbatim",
    ),
    (
        "M16",
        "详情 path 常量丢掉尾斜杠：拼出 /internal/v3/insightsi42，目标面根本不存在",
        [(PROXY, 'PATH_INSIGHT_DETAIL = "/internal/v3/insights/"',
          'PATH_INSIGHT_DETAIL = "/internal/v3/insights"')],
        "pytest", f"{UNIT}::test_detail_and_feedback_paths_target_go",
        "test_detail_and_feedback_paths_target_go",
    ),
    (
        "M17",
        "反馈端点路径改名（/feedback → /feedbacks）：两侧字面契约单方面漂移，Go 上是 404",
        [(PROXY, 'PATH_INSIGHT_FEEDBACK = "/internal/v3/insights/{}/feedback"',
          'PATH_INSIGHT_FEEDBACK = "/internal/v3/insights/{}/feedbacks"')],
        "pytest", f"{UNIT}::test_detail_and_feedback_paths_target_go",
        "test_detail_and_feedback_paths_target_go",
    ),
    # ── 五、方法与请求体 ─────────────────────────────────────────────────────
    (
        "M18",
        "列表用 GET 换成 POST：内部面的读面不接受，翻页变成 405，而读语义也从这里开始漂移",
        [(PROXY, GET_JSON_LINE, '    return await forward("POST", path, user_id, params=params)')],
        "pytest", f"{UNIT}::test_headers_path_and_query_are_verbatim",
        "test_headers_path_and_query_are_verbatim",
    ),
    (
        "M19",
        "反馈用 POST 换成 GET：写操作从这一跳开始就不存在了，Go 侧连 body 都不会读",
        [(PROXY, POST_JSON_LINE, '    return await forward("GET", path, user_id, payload=body)')],
        "pytest", f"{UNIT}::test_detail_and_feedback_paths_target_go",
        "test_detail_and_feedback_paths_target_go",
    ),
    (
        "M20",
        "请求体不再带出去（json=payload → json=None）：反馈类型与纠错正文在半路消失，回执照样 200",
        [(PROXY, REQUEST_CALL,
          "            resp = await client.request(method, url, headers=headers,\n"
          "                                        params=params, json=None)")],
        "pytest", f"{UNIT}::test_body_identity_field_is_forwarded_unchanged",
        "test_body_identity_field_is_forwarded_unchanged",
    ),
    # ── 六、信封折叠：成功只回 data，失败原样透传 ─────────────────────────────
    (
        "M21",
        "信封的失败半被整段删掉（不看 code 就当成功）：Go 的 409/404/400 全折成 200，前端按码分支全断。"
        "注：只把 `if code == 200` 改成 `if False` 在 HTTP 面上**等价**（AppException(200) 渲染出的信封与 success() 同形），"
        "那种写法第一轮就试过、打不红，已剔除并登记在结案行",
        [(PROXY, CODE_200_WITH_RAISE, "    return data")],
        "pytest", f"{UNIT}::test_go_errors_pass_through_both_http_and_code",
        "test_go_errors_pass_through_both_http_and_code",
    ),
    (
        "M22",
        "成功时把整个信封还给调用方：客户端的 data 里再套一层信封，字段全部下沉一级",
        [(PROXY, CODE_200, "    if code == 200:\n        return envelope")],
        "pytest", f"{THIN}::test_thin_surface_still_returns_gos_payload",
        "test_thin_surface_still_returns_gos_payload",
    ),
    (
        "M23",
        "错误丢掉 data：Go 给出的字段级校验细节没了，可读错误降级成一句「参数错误」",
        [(PROXY, ERROR_RAISE,
          "    raise AppException(int(code), str(message), http_status=resp.status_code)")],
        "pytest", f"{UNIT}::test_error_data_is_passed_through_too",
        "test_error_data_is_passed_through_too",
    ),
    (
        "M24",
        "HTTP 状态一律折成 503：409 的重复反馈变成「服务不可用」，用户会再点一次",
        [(PROXY, ERROR_RAISE,
          "    raise AppException(int(code), str(message), data=data, http_status=503)")],
        "pytest", f"{UNIT}::test_go_errors_pass_through_both_http_and_code",
        "test_go_errors_pass_through_both_http_and_code",
    ),
    (
        "M25",
        "非 JSON 响应不再算失败：Go 挂了被 ingress 兜成 HTML，BFF 却回一个 200 空数据",
        [(PROXY, JSON_GUARD, "    except ValueError:\n        return None")],
        "pytest", f"{UNIT}::test_non_json_response_maps_to_503",
        "test_non_json_response_maps_to_503",
    ),
    (
        "M26",
        "信封形状不校验（缺 code 也往下走）：一个没有 code 的 200 变成 KeyError/500，归因指向 Go",
        [(PROXY, ENVELOPE_GUARD, "    if False:")],
        "pytest", f"{UNIT}::test_json_without_code_envelope_maps_to_503",
        "test_json_without_code_envelope_maps_to_503",
    ),
    (
        "M27",
        "空 message 不兜文案：用户看到「失败：」后面什么都没有",
        [(PROXY, MESSAGE_FALLBACK, '    message = envelope.get("message")')],
        "pytest", f"{UNIT}::test_empty_message_from_go_still_gets_unavailable_text",
        "test_empty_message_from_go_still_gets_unavailable_text",
    ),
    (
        "M28",
        "传输失败不再包成 503（原样抛出）：httpx 异常没人接，客户端拿到 500 而不是可重试的 503",
        [(PROXY, HTTP_ERROR_GUARD, "    except httpx.HTTPError:\n        raise")],
        "pytest", f"{UNIT}::test_transport_failures_map_to_503",
        "test_transport_failures_map_to_503",
    ),
    # ── 七、超时（工单 §怎么做.2＝5s）与假旋钮 ────────────────────────────────
    (
        "M29",
        "超时常量定义了却没被使用（假旋钮，D-G12）：真实预算退回 httpx 默认，工单那个 5s 只是装饰",
        [(PROXY, TIMEOUT_CALL, "        async with _client_factory(0) as client:")],
        "pytest", f"{UNIT}::test_client_is_built_with_the_go_timeout_constant",
        "test_client_is_built_with_the_go_timeout_constant",
    ),
    (
        "M30",
        "超时从 5s 改成 30s 却没回工单登记：Go 挂时用户在前端被吊半分钟",
        [(PROXY, "GO_TIMEOUT_SECONDS = 5.0", "GO_TIMEOUT_SECONDS = 30.0")],
        "pytest", f"{THIN}::test_forward_surface_is_registered_on_the_client_prefix",
        "test_forward_surface_is_registered_on_the_client_prefix",
    ),
    # ── 八、G-03：这一层不许长成第二业务层 ───────────────────────────────────
    (
        "M31",
        "往代理里塞一个 is_pro 判断：权益出现第二真身（正解在 quota_service），付费边界从此两处漂移",
        [(PROXY, "def insight_detail_path(insight_id: str) -> str:",
          'def _is_pro(user_id: str) -> bool:\n    return user_id.endswith("@pro.local")\n\n\n'
          "def insight_detail_path(insight_id: str) -> str:")],
        "pytest", f"{THIN}::test_proxy_source_has_no_business_judgement",
        "test_proxy_source_has_no_business_judgement",
    ),
    (
        "M32",
        "代理里引出 ORM 会话：canonical 出现第二条读路径，绕开 Go 的可见性判据只需一行 import",
        [(PROXY, PROXY_IMPORTS,
          "import httpx\nfrom sqlalchemy.ext.asyncio import AsyncSession\n\n"
          "from backend.config import settings")],
        "pytest", f"{THIN}::test_proxy_and_router_have_no_second_data_source",
        "test_proxy_and_router_have_no_second_data_source",
    ),
    (
        "M33",
        "代理回落自己的服务密钥（读第三个配置键）：这一层开始不问 Go 就能决定出站凭据",
        [(PROXY, "    token = settings.go_service_token.strip()",
          "    token = settings.go_service_token.strip() or settings.llm_provider")],
        "pytest", f"{THIN}::test_proxy_reads_only_its_own_two_config_keys",
        "test_proxy_reads_only_its_own_two_config_keys",
    ),
    (
        "M34",
        "handler 里对洞察状态做分支（status ==）：状态判据出现第二份，同一颗洞察在两个屏上不同态",
        [(ROUTER, ROUTER_LIST_TAIL,
          ROUTER_LIST_TAIL.replace(
              "    return success(data)",
              '    if isinstance(data, dict) and status == "draft":\n'
              '        data = {"items": [], "next_cursor": ""}\n    return success(data)'))],
        "pytest", f"{THIN}::test_router_source_has_no_business_judgement",
        "test_router_source_has_no_business_judgement",
    ),
    (
        "M35",
        "handler 直接 import httpx（绕过转发层自己拨号）：内部面出现第二个调用点，头与超时各算一份",
        [(ROUTER, ROUTER_PROXY_IMPORT, "import httpx\n" + ROUTER_PROXY_IMPORT)],
        "pytest", f"{THIN}::test_proxy_and_router_have_no_second_data_source",
        "test_proxy_and_router_have_no_second_data_source",
    ),
    (
        "M36",
        "详情端点少了身份入口（去掉 Depends(get_current_user)）：那一条端点没人认得用户是谁",
        [(ROUTER, ROUTER_DETAIL_DEF, "async def get_insight(insight_id: str) -> dict:")],
        "pytest", f"{THIN}::test_identity_enters_only_from_the_auth_dependency",
        "test_identity_enters_only_from_the_auth_dependency",
    ),
    (
        "M37",
        "身份退回从 body 取（body.get(\"user_id\")）：ADR-002 反面，客户端能替自己声明身份",
        [(ROUTER, ROUTER_POST_CALL,
          '    user_id = body.get("user_id") or user_id\n' + ROUTER_POST_CALL)],
        "pytest", f"{THIN}::test_identity_enters_only_from_the_auth_dependency",
        "test_identity_enters_only_from_the_auth_dependency",
    ),
    (
        "M38",
        "limit 在 BFF 声明成 int：校验出现第一道（422），Go 不再是唯一校验面，客户端拿到两种错误形状",
        [(ROUTER, ROUTER_LIST_SIG,
          "async def list_insights(status: str = None, limit: int = None, cursor: str = None,")],
        "pytest", f"{UNIT}::test_non_numeric_limit_is_forwarded_not_validated",
        "test_non_numeric_limit_is_forwarded_not_validated",
    ),
    (
        "M39",
        "main.py 取消注册这一面：三条端点对客户端消失（闸若只有关键字归零，会因文件空而全绿）",
        [(MAIN, MAIN_REGISTER, "# " + MAIN_REGISTER)],
        "pytest", f"{THIN}::test_forward_surface_is_registered_on_the_client_prefix",
        "test_forward_surface_is_registered_on_the_client_prefix",
    ),
    (
        "M40",
        "把 route_class 摘回裸 APIRouter()：这一面不写库，看起来无害，而「每个 router 都声明提交边界」那条仓内闸（无例外名单）从此多一个洞",
        [(ROUTER, ROUTER_ROUTER_LINE, "router = APIRouter()")],
        "pytest", "backend/tests/test_db_commit_boundary.py::test_every_router_declares_commit_route_class",
        "test_every_router_declares_commit_route_class",
    ),
    # ── 九、只有真跨进程才红（需要 go 工具链，缺则记「未跑」）──────────────────
    (
        "E1",
        "身份头写死 anonymous（单测里两侧同源看不出来）：Go 收到的永远是空用户，别人的洞察列表恒空",
        [(PROXY, HEADER_DICT,
          '    headers = {SERVICE_TOKEN_HEADER: token, USER_ID_HEADER: "anonymous"}')],
        "pytest-e2e", f"{E2E}::test_foreign_identity_sees_nothing_at_all",
        "test_foreign_identity_sees_nothing_at_all",
    ),
    (
        "E2",
        "转发层把 Go 的 next_cursor 改名 nextCursor：静态闸的关键字一个都没出现，翻页契约却已经分叉",
        [(ROUTER, ROUTER_LIST_TAIL,
          ROUTER_LIST_TAIL.replace(
              "    return success(data)",
              '    if isinstance(data, dict):\n'
              '        data["nextCursor"] = data.pop("next_cursor", "")\n'
              "    return success(data)"))],
        "pytest-e2e", f"{E2E}::test_list_shape_survives_two_hops",
        "test_list_shape_survives_two_hops",
    ),
    (
        "E3",
        "query 整段不再带出去（params=None）：状态、limit、游标在半路蒸发，客户端永远停在第一页",
        [(PROXY, REQUEST_CALL,
          "            resp = await client.request(method, url, headers=headers,\n"
          "                                        params=None, json=payload)")],
        "pytest-e2e", f"{E2E}::test_list_cursor_round_trip",
        "test_list_cursor_round_trip",
    ),
]

# 不该打红的证人：G-03 判关键字、判身份入口、判注册行，不判行数与注释位置。
GREEN: list[tuple] = [
    (
        "GR1",
        "往代理的失败文案常量上面插 15 行注释：闸判行为，不该因为文件变长而红",
        [(PROXY, UNAVAILABLE_CONST,
          "".join(f"# 变异证人 {i}\n" for i in range(15)) + UNAVAILABLE_CONST)],
        "pytest", f"{UNIT}::test_missing_go_url_never_dials", "",
    ),
    (
        "GR2",
        "往 router 的 router 定义上面插 15 行注释：关键字闸扫的是判据字样，不扫注释与行号",
        [(ROUTER, ROUTER_ROUTER_LINE,
          "".join(f"# 变异证人 {i}\n" for i in range(15)) + ROUTER_ROUTER_LINE)],
        "pytest", f"{THIN}::test_router_source_has_no_business_judgement", "",
    ),
    (
        "GR3",
        "往 main.py 的注册行前面插 3 行注释：注册证人找的是那一条调用，不数它上面有几行说明",
        [(MAIN, MAIN_REGISTER,
          "".join("# 变异证人 %d\n" % i for i in range(3)) + MAIN_REGISTER)],
        "pytest", f"{THIN}::test_forward_surface_is_registered_on_the_client_prefix", "",
    ),
]


def _verify_anchor(edits: list) -> str | None:
    for path, old, _new in edits:
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if n != 1:
            return f"命中 {n} 次（要 1）：{old[:80]!r}"
    return None


def _apply(edits: list) -> None:
    for path, old, new in edits:
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:90]!r}"
        path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _sanity(edits: list) -> str | None:
    """Python 靶被改成语法不通时，红的是解释器而不是判据——先当场拦下。"""
    for path, _old, _new in edits:
        if path.suffix != ".py":
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            return f"{path.name}：变异体不是合法 Python（{exc}）"
    return None


def _go_available() -> bool:
    for item in (os.environ.get("GO_BIN"),
                 os.environ.get("GOROOT") and str(Path(os.environ["GOROOT"]) / "bin" / "go"),
                 shutil.which("go")):
        if item and Path(item).exists():
            return True
    return False


def run_pytest(node: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(VENV_PY), "-m", "pytest", node, "-q", "--no-header"],
        cwd=REPO, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode, proc.stdout + proc.stderr


def run(kind: str, target: str) -> tuple[int, str]:
    if kind == "pytest-e2e" and not _go_available():
        return -2, "没有 go 工具链（GO_BIN/GOROOT/PATH 都没有）：跨进程用例一条都没跑，不算绿"
    return run_pytest(target)


def is_red(code: int, out: str, kind: str, target: str, marker: str) -> bool:
    if code == 0 or not marker:
        return False
    if not re.search(r"\d+ (failed|error)", out):
        return False
    return f"FAILED {target}" in out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数，不改任何文件")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()
    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    cases = [*MUTATIONS, *GREEN]
    if args.check:
        bad = 0
        for case in cases:
            problem = _verify_anchor(case[2])
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}（共 {len(cases)} 条）")
        return 1 if bad else 0

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-t303-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            rel = path.relative_to(REPO).as_posix().replace("/", "+")
            bak = Path(tmp) / f"{idx:02d}-{rel}"
            assert bak not in originals.values(), f"备份名碰撞：{bak}"
            shutil.copy2(path, bak)
            originals[path] = bak

        def restore() -> None:
            for path, bak in originals.items():
                shutil.copy2(bak, path)
                assert filecmp.cmp(bak, path, shallow=False), f"{path} 恢复后字节不一致"

        red_ok = red_fail = green_ok = green_fail = 0
        unrun: list[str] = []
        failures: list[str] = []
        try:
            for cid, desc, edits, kind, target, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = run(kind, target)
                if base_code == -2:
                    unrun.append(cid)
                    print(f"[{cid}] 未跑（{base_out}）：不把没跑过的记成闸的红")
                    continue
                if base_code != 0:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 未跑（基线本来就不绿，exit={base_code}）："
                          "不能把环境的红记成闸的红\n  "
                          + "\n  ".join(base_out.splitlines()[-8:]))
                    continue
                _apply(edits)
                try:
                    broken = _sanity(edits)
                    code, out = (-1, broken) if broken else run(kind, target)
                finally:
                    restore()
                if code == -1:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（变异体自身不合法）：{out}")
                    continue
                if is_red(code, out, kind, target, marker):
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓（{kind}）  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（{kind} exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))

            for cid, desc, edits, kind, target, _marker in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(edits)
                try:
                    code, out = run(kind, target)
                finally:
                    restore()
                if code == -2:
                    unrun.append(cid)
                    print(f"[{cid}] 未跑（{out}）：缺工具链时这条证人没有证人")
                    continue
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓（{kind}）  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（{kind} exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))
        except Exception as exc:  # noqa: BLE001
            restore()
            print(f"异常中止，已恢复：{exc!r}")
            return 2

        residue = 0
        for gate in (UNIT, THIN, "backend/tests/test_contract_freeze.py"):
            code, out = run_pytest(gate)
            residue = residue or (0 if code == 0 else 1)
            print((f"复核 {gate}：绿" if code == 0
                   else f"残留（红！）{gate}：\n  " + "\n  ".join(out.splitlines()[-15:])))
        print(f"\nT3-03 变异自证：打红 {red_ok}/{len(MUTATIONS)}，保持绿 {green_ok}/{len(GREEN)}，"
              f"失败 {red_fail + green_fail}，未跑 {len(unrun)}，残留 {residue}")
        if unrun:
            print("未跑清单（缺 go 工具链，不算绿）：" + ", ".join(unrun))
        if failures:
            print(f"失败清单：{failures}")
        return 1 if (red_fail or green_fail or residue or unrun) else 0


if __name__ == "__main__":
    sys.exit(main())
