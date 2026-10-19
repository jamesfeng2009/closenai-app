#!/usr/bin/env python3
"""T1-04（付费交付可观测：订单→生成→下载）的变异自证（2026-10-18）。

这批交付的是「付款成功但交付失败」这件事在线上能不能被发现，四组靶子各有判据：

审计侧（`backend/tests/test_delivery_audit.py`，判据落在库里）——
- A1 宽限期不参与筛选 ⇒ 刚买 1 小时还没点导出的人被算成交付失败，指标第一天就撒谎；
- A2 把 `grace_hours` 硬编码进返回体 ⇒ 换 env 不改行为，生产调不动（工作单点名的旋钮）；
- A3 交付证人不再要求「付费之后」⇒ 订阅期的历史免费导出替买断用户「作证已交付」，
  要抓的那一格正好被漏掉；
- A4 交付证人不看 `period` ⇒ 周报行被当成档案已出货；
- A5 人群不看 `revoked_at` ⇒ 退款回收的人仍留在分母里，未交付率长期虚高；
- A6 退款分子筛反 ⇒ 撤销数永远等于持有数，「退款率算不出」这半句仍是口头承诺；
- A7 `>` 改成 `>=` ⇒ 阈值 0 时零未交付也告警，值班很快就不信这条指标；
- A8 撤掉告警分支 ⇒ 计数还在，但没人被叫醒（`DELIVERY-ALERT` 这格本来就是交付面欠的）；
- A9 metric 行少一个字段 ⇒ Loki 侧按 k=v 解析的看板当场缺一列，「可解析」这句不再成立；
- A10 端点回一串零 ⇒ service 层全绿而线上每小时跑的是那条 HTTP，指标永远「一切正常」；
- A11 宿主 crontab 注释掉这条排程 ⇒ 审计有人写、没人跑，等价于没交付；
- A12 把人群改读 `Subscription` ⇒ 这条打的是「口径护栏」闸：改判必须过一次讨论，不能顺手漂走。

事件通道（`backend/tests/test_delivery_observability.py`）——
- E1 日志/中继不再过白名单 ⇒ 反思文本、记忆内容顺着交付事件进了分析平台（红线）；
- E2 撤掉 consent 判定 ⇒ 没给分析同意的用户被中继；
- E3 中继失败仍写 `relayed` ⇒ 「平台没配」看起来像「已上报」（D-G18 同一口径）；
- E4 没有用户行也写 `relayed`；
- E5 日志行漏掉 `relay=` ⇒ 这条 metric 不再能区分「记了」与「送出去了」。

发射点接线（同上文件）——
- I1 撤掉 `except AppException` 里的失败上报 ⇒ 最该告警的那几种失败数成 0；
- I2 `reason` 取 `message` 而不是机器码 ⇒ Apple 自由文本进分析平台；
- I3 把 succeeded 发射点改名 ⇒ 登记表里写着、代码里没人发（七个点缺一整个）；
- S1 撤掉被门禁拦下的上报 ⇒ 40304 那类「付费用户为什么没拿到东西」全部隐身；
- S2 门禁分支再补一条上报 ⇒ 同一次失败算两遍，比率失真；
- S3 撤掉 `archive_download_requested` ⇒ 下载这一跳重新不可见；
- S4 撤掉排版炸掉的 ERROR 告警 ⇒「库里已交付、用户手上没文件」只剩一行 INFO。

白名单三侧（同上文件最后一条 ＋ 前端闸）——
- L1/L2/L3 分别在 `frontend/src/services/analytics.ts`、`analytics_service.py` 的 frozenset、
  `go-core/internal/domain/analytics.go` 的 map 里删掉 `reason`：任何一侧掉，属性就在
  匿名化时被剔空，指标永远显示 reason 为空——那种红不会自己出现，所以逐侧打一次。

客户端最后一跳（`frontend/__tests__/archiveShareTracking.test.ts`）——
后端只看得到「PDF 生成并送出去了」，`Sharing.isAvailableAsync()` 为假时抛的
`PdfUnavailableError` 全程不出设备，所以这一段只有客户端知道。
- P1 删掉成功上报 ⇒「付了钱、后端出货了、用户手里没东西」变成看不见的形状；
- P2 把成功上报挪到 `exportPdf` 之前 ⇒ 面板还没接住就宣布 ok；
- P3 `reason` 回 `e.message` ⇒ 自由文本上网（与 I2 同一红线的前端侧）；
- P4 键名漂成 `error_detail` ⇒ 不在白名单，后端匿名化时整条剔空；
- P5 静默 return 也上报 ⇒ 分母被点击噪声灌满；
- P6 失败上报排到 Alert 之后 ⇒ 阻塞式原生面板之后可能永远回不来。

实库面（`backend/tests/test_delivery_audit_pg.py`，一次性空库 `t104_e2e`）——
- PA1 交付证人不再要求「付费之后」（真库面）：sqlite 把 `DateTime(timezone=True)` 存成字符串、
  比较是字典序，恰好与时间序同向，所以那条写错在 sqlite 侧永远看不出；`timestamptz`
  比的才是生产真正执行的那个瞬间；
- PA2 人群不看 `revoked_at`（真库面）：`distinct` 计数与 `revoked` 分子跑在迁移出来的 DDL 上。

GR1–GR4 是「不该打红」的证人：闸判的是查询语义、日志字段与调用点，不判文件行数与
注释位置。

四处第一轮打不中靶、当场回补的取证漏洞（都记在这里，以免下一轮再猜）：
- **A2** 第一次挂在 `test_grace_and_threshold_come_from_settings`，实测**打不红**（exit=0）：
  那条用例只拿响应等于「行为变了没有」，从没核对响应里回显的 `grace_hours`／`threshold`，
  而另一条 `test_purchase_inside_grace_is_not_a_failure` 比的是 `== settings.xxx`——默认值就是
  24，硬编码 24 与它恒等。那是同一种假绿（[[feedback-orm-identity-map-gate]]）：断言两侧
  读的是同一个真源。现在用例改比**字面量**（48／1），A2 才打得住。
- **I2** 第一次挂的是 `test_verify_reason_never_carries_message_text`，也**打不红**：那条用例
  走的是异常分支（`unexpected_RuntimeError`），根本没经过被改坏的那一行 `result.get("reason")`。
  换到确实读这条返回体的 `test_verify_apple_status_is_machine_reason`，破坏才到达自己的靶
  （[[feedback-mutation-anchor-must-reach-target]]）。
- **P1／P5** 打红了整个文件却打不中用例名（`Tests: 0 total`）：切片函数 `usedPropertyKeys`
  里塞了一条重复的「调用点恰好两个」，而且它在 describe 体求值阶段就抛，jest 于是把整个
  suite 记成崩溃、失败用例的名字不再出现在输出里。已把数量判据收回 ① 那条用例、切片改惰性
  （在 `it` 里取源码），现在这两条各由自己的用例红。
- 同理，`is_red` 对 jest 只认 `● describe › 用例名` 或结果行 `✕ 用例名`：jest 会把通过的用例
  也列出来（✓ 行），只按「串在输出里出现过」判红会把别人的红念成自己的靶。

基线只跑一遍并按 (kind,target) 缓存，理由是每条变异后都由 `filecmp(shallow=False)`
逐字节确认写回，源状态可证与基线时一致——这不是「省时间所以不跑基线」，
而是漂移一旦发生就会被恢复断言当场中止（[[feedback-mutation-anchor-must-reach-target]]）。
缺库/缺 node 的情况一律记「未跑」，不算绿（[[feedback-definition-of-done-tests]]）。

纪律：串行执行、copy2 备份＋filecmp 逐字节写回、不用 git checkout
（[[feedback-mutation-restore-no-git-checkout]]、[[feedback-mutation-gates-no-concurrency]]）。

用法：
    ./.venv/bin/python scripts/mutate_t104_delivery_gates.py --check
    ./.venv/bin/python scripts/mutate_t104_delivery_gates.py --only A1,P3
    # 带实库面（PA1/PA2；缺 TEST_DATABASE_URL 时这两条记「未跑」，不算绿）：
    TEST_DATABASE_URL='postgresql://postgres:…@127.0.0.1:55432/t104_e2e' \\
        ./.venv/bin/python scripts/mutate_t104_delivery_gates.py   # 全跑 35 红 + 4 绿
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
FRONT = REPO / "frontend"
VENV_PY = REPO / ".venv" / "bin" / "python"

DELIVERY = REPO / "backend" / "services" / "delivery_service.py"
IAP = REPO / "backend" / "services" / "iap_service.py"
SHARE = REPO / "backend" / "services" / "share_service.py"
CRON = REPO / "backend" / "routers" / "cron.py"
CRONTAB = REPO / "deploy" / "schedule" / "crontab"
BACK_ANALYTICS = REPO / "backend" / "services" / "analytics_service.py"
GO_ANALYTICS = REPO / "go-core" / "internal" / "domain" / "analytics.go"
FRONT_ANALYTICS = REPO / "frontend" / "src" / "services" / "analytics.ts"
PAYWALL = REPO / "frontend" / "src" / "screens" / "PaywallScreen.tsx"

AUD = "backend/tests/test_delivery_audit.py"
OBS = "backend/tests/test_delivery_observability.py"
AUD_PG = "backend/tests/test_delivery_audit_pg.py"
JEST_GATE = "__tests__/archiveShareTracking.test.ts"
PG_ENV = "TEST_DATABASE_URL"

CRON_LINE = (
    '0 4 * * * . /etc/closenai/cron.env && curl -fsS -H "Authorization: Bearer $CRON_SECRET" '
    "http://127.0.0.1:8000/api/v1/cron/delivery-audit >/dev/null"
)
CRON_NOTE = (
    "# T1-04 付费交付审计：数「买断过了、过了宽限期、库里还没有成功导出行」的用户，"
    "超阈值打 DELIVERY-ALERT"
)

OK_TRACK = (
    "      track('archive_share_result', {\n"
    "        feature: 'life_archive', action: 'share', result: 'ok',\n"
    "      });\n"
)
FAIL_TRACK = (
    "      track('archive_share_result', {\n"
    "        feature: 'life_archive', action: 'share', result: 'failed',\n"
    '        reason: pdfFailureReason(e),\n'
    "      });\n"
)

MUTATIONS: list[tuple] = [
    # ── 1. 审计判据（库里那格）──
    (
        "A1",
        "宽限期不参与筛选：刚买 1 小时还没点导出的人被算成「交付失败」",
        [(DELIVERY,
          "    grace_cutoff = now - timedelta(hours=settings.delivery_audit_grace_hours)\n",
          "    grace_cutoff = now\n")],
        "pytest", f"{AUD}::test_purchase_inside_grace_is_not_a_failure",
        "test_purchase_inside_grace_is_not_a_failure",
    ),
    (
        "A2",
        "返回体里的 grace_hours 硬编码：换 env 不改行为，工作单点名的旋钮失效",
        [(DELIVERY,
          '        "grace_hours": settings.delivery_audit_grace_hours,\n',
          '        "grace_hours": 24,\n')],
        "pytest", f"{AUD}::test_grace_and_threshold_come_from_settings",
        "test_grace_and_threshold_come_from_settings",
    ),
    (
        "A3",
        "交付证人不看「付费之后」：订阅期的历史免费导出替买断用户作证已出货",
        [(DELIVERY,
          "            ShareExport.created_at >= UserEntitlement.purchase_date,\n",
          "")],
        "pytest", f"{AUD}::test_export_before_purchase_is_not_a_delivery_witness",
        "test_export_before_purchase_is_not_a_delivery_witness",
    ),
    (
        "A4",
        "交付证人不看 period：一行周报被当成 Life Archive 已出货",
        [(DELIVERY,
          "            ShareExport.period == LIFE_ARCHIVE_PERIOD,\n",
          "            ShareExport.id.is_not(None),\n")],
        "pytest", f"{AUD}::test_other_period_export_does_not_count",
        "test_other_period_export_does_not_count",
    ),
    (
        "A5",
        "人群不看 revoked_at：退款回收的人仍留在分母里，未交付数长期虚高",
        [(DELIVERY,
          "    active = UserEntitlement.revoked_at.is_(None)\n",
          "    active = UserEntitlement.user_id.is_not(None)\n")],
        "pytest", f"{AUD}::test_revoked_entitlement_leaves_the_paid_population",
        "test_revoked_entitlement_leaves_the_paid_population",
    ),
    (
        "A6",
        "退款分子筛反了（isnot→is）：撤销数永远等于持有数，「退款率」这半句仍算不出",
        [(DELIVERY,
          "            UserEntitlement.revoked_at.isnot(None)\n",
          "            UserEntitlement.revoked_at.is_(None)\n")],
        "pytest", f"{AUD}::test_metric_line_is_parseable_and_counts_users",
        "test_metric_line_is_parseable_and_counts_users",
    ),
    (
        "A7",
        "阈值比较 `>` 改成 `>=`：阈值 0 时零未交付也告警，值班很快就不信这条指标",
        [(DELIVERY,
          "    is_alert = undelivered > threshold\n",
          "    is_alert = undelivered >= threshold\n")],
        "pytest", f"{AUD}::test_export_after_purchase_clears_the_count",
        "test_export_after_purchase_clears_the_count",
    ),
    (
        "A8",
        "撤掉告警分支：计数还在但没人被叫醒，DELIVERY-ALERT 这格等于没交付",
        [(DELIVERY, "    if is_alert:\n        alert(\n", "    if False:\n        alert(\n")],
        "pytest", f"{AUD}::test_alert_line_is_emitted_only_over_threshold",
        "test_alert_line_is_emitted_only_over_threshold",
    ),
    (
        "A9",
        "metric 行少一个字段：Loki 按 k=v 解析的看板缺一列，「可解析」不再成立",
        [(DELIVERY,
          '        "%s event=delivery_audit paid_users=%s undelivered_users=%s revoked_users=%s "\n',
          '        "%s event=delivery_audit paid_users=%s undelivered_users=%s "\n')],
        "pytest", f"{AUD}::test_metric_line_is_parseable_and_counts_users",
        "test_metric_line_is_parseable_and_counts_users",
    ),
    (
        "A10",
        "端点回一串零（不真跑审计）：service 层全绿，而线上跑的是这条 HTTP",
        [(CRON,
          "    return success(await delivery_service.audit_paid_delivery(db))\n",
          "    return success({\n"
          '        "paid_users": 0, "undelivered_users": 0, "revoked_users": 0,\n'
          '        "grace_hours": 0, "threshold": 0, "alert": False,\n'
          "    })\n")],
        "pytest", f"{AUD}::test_cron_endpoint_is_fail_closed_on_secret",
        "test_cron_endpoint_is_fail_closed_on_secret",
    ),
    (
        "A11",
        "宿主 crontab 注释掉这条排程：审计有人写、没人跑，等价于没交付",
        [(CRONTAB, CRON_LINE, "# " + CRON_LINE)],
        "pytest", f"{AUD}::test_delivery_audit_is_scheduled_daily",
        "test_delivery_audit_is_scheduled_daily",
    ),
    (
        "A12",
        "审计人群改读 Subscription：口径护栏闸必须红，逼这次改判过一次讨论",
        [(DELIVERY,
          "async def audit_paid_delivery(db: AsyncSession) -> dict[str, Any]:\n",
          "async def audit_paid_delivery(db: AsyncSession) -> dict[str, Any]:  # 改判：人群读 Subscription\n")],
        "pytest", f"{AUD}::test_audit_query_targets_the_two_named_tables",
        "test_audit_query_targets_the_two_named_tables",
    ),
    # ── 2. 事件通道（日志＋中继）──
    (
        "E1",
        "日志/中继不再过白名单：反思文本、记忆内容顺着交付事件进了分析平台",
        [(DELIVERY,
          "    props = analytics_service.anonymize_properties(properties)\n",
          "    props = dict(properties)\n")],
        "pytest", f"{OBS}::test_content_key_never_enters_log_or_relay",
        "test_content_key_never_enters_log_or_relay",
    ),
    (
        "E2",
        "撤掉 consent 判定：没给分析同意的用户照样被中继（红线）",
        [(DELIVERY,
          "        if user.analytics_consent:\n",
          "        if True:  # 变异证人：摘掉同意判定\n")],
        "pytest", f"{OBS}::test_consent_off_is_no_consent_and_does_not_relay",
        "test_consent_off_is_no_consent_and_does_not_relay",
    ),
    (
        "E3",
        "中继失败仍写 relayed：平台没配看起来像已上报（D-G18 同一口径）",
        [(DELIVERY,
          "            relay = (\n"
          "                RELAY_RELAYED\n"
          "                if analytics_service.relay_server_event(user.id, event, props)\n"
          "                else RELAY_DROPPED\n"
          "            )\n",
          "            relay = RELAY_RELAYED\n")],
        "pytest", f"{OBS}::test_relay_distinguishes_relayed_from_dropped",
        "test_relay_distinguishes_relayed_from_dropped",
    ),
    (
        "E4",
        "手里没有用户行也写 relayed：这条 metric 开始谎报覆盖面",
        [(DELIVERY, "        relay = RELAY_NO_USER\n", "        relay = RELAY_RELAYED\n")],
        "pytest", f"{OBS}::test_missing_user_row_is_no_user_but_still_logged",
        "test_missing_user_row_is_no_user_but_still_logged",
    ),
    (
        "E5",
        "日志行漏掉 relay= 字段：分不清「记下来了」与「送出去了」",
        [(DELIVERY,
          '        "%s event=%s user=%s relay=%s %s", METRIC_MARKER, event, uid, relay, rendered\n',
          '        "%s event=%s user=%s %s", METRIC_MARKER, event, uid, rendered\n')],
        "pytest", f"{OBS}::test_metric_line_shape_is_parseable",
        "test_metric_line_shape_is_parseable",
    ),
    # ── 3. 发射点接线 ──
    (
        "I1",
        "撤掉 AppException 分支的失败上报：超时/跨账号收据这类最该告警的失败数成 0",
        [(IAP,
          "        except AppException as exc:\n"
          '            await self._emit_verify_failed(db, user_id, f"app_error_{exc.code}")\n'
          "            raise\n",
          "        except AppException:\n            raise\n")],
        "pytest", f"{OBS}::test_verify_exception_still_emits_failed",
        "test_verify_exception_still_emits_failed",
    ),
    (
        "I2",
        "reason 取 message 而不是机器码：Apple 的自由文本原文被推上分析平台",
        [(IAP,
          'result.get("reason") or "invalid"',
          'result.get("message") or "invalid"')],
        "pytest", f"{OBS}::test_verify_apple_status_is_machine_reason",
        "test_verify_apple_status_is_machine_reason",
    ),
    (
        "I3",
        "succeeded 发射点改名 started：登记表里写着、代码里没人发（七个点缺一个）",
        [(IAP,
          "        emit(\n            DeliveryEvent.IAP_VERIFY_SUCCEEDED,\n            user=user,\n",
          "        emit(\n            DeliveryEvent.IAP_VERIFY_STARTED,\n            user=user,\n")],
        "pytest", f"{OBS}::test_seven_named_events_all_have_call_sites",
        "test_seven_named_events_all_have_call_sites",
    ),
    (
        "S1",
        "撤掉被门禁拦下的上报：40304 这类「付费用户为什么没拿到东西」全部隐身",
        [(SHARE,
          "            emit(\n"
          "                DeliveryEvent.ARCHIVE_GENERATE_FAILED,\n"
          "                user=user,\n"
          '                source="life_archive",\n'
          "                feature=scope,\n"
          '                action="generate",\n'
          '                result="failed",\n'
          '                reason=f"rejected_{exc.code}",\n'
          "            )\n"
          "            raise\n",
          "            raise\n")],
        "pytest", f"{OBS}::test_generate_denied_emits_rejected_code",
        "test_generate_denied_emits_rejected_code",
    ),
    (
        "S2",
        "门禁分支再补一条上报：同一次失败在指标里算两遍",
        [(SHARE,
          "        except AppException:\n"
          "            # 权益/参数类失败已由 export_life_archive 数过（reason=rejected_*），\n",
          "        except AppException:\n"
          "            emit(\n"
          "                DeliveryEvent.ARCHIVE_GENERATE_FAILED,\n"
          "                user=user,\n"
          '                source="life_archive",\n'
          "                feature=scope,\n"
          '                action="download",\n'
          '                result="failed",\n'
          '                reason="double_counted",\n'
          "            )\n"
          "            # 权益/参数类失败已由 export_life_archive 数过（reason=rejected_*），\n")],
        "pytest", f"{OBS}::test_pdf_denied_by_gate_is_counted_once",
        "test_pdf_denied_by_gate_is_counted_once",
    ),
    (
        "S3",
        "撤掉下载那一跳的上报：请求进没进来重新无人知晓",
        [(SHARE,
          "        emit(\n"
          "            DeliveryEvent.ARCHIVE_DOWNLOAD_REQUESTED,\n"
          "            user=user,\n"
          '            source="life_archive",\n'
          "            feature=scope,\n"
          '            action="download",\n'
          '            language=language or "en",\n'
          "        )\n",
          "")],
        "pytest", f"{OBS}::test_pdf_download_requested_and_render_failure_alerts",
        "test_pdf_download_requested_and_render_failure_alerts",
    ),
    (
        "S4",
        "撤掉排版炸掉的 ERROR 告警：「库里已交付、用户手上没文件」只剩一行 INFO",
        [(SHARE,
          "            alert(\n"
          '                "event=archive_pdf_render_failed",\n'
          "                scope=scope,\n"
          "                reason=type(exc).__name__,\n"
          "            )\n",
          "")],
        "pytest", f"{OBS}::test_pdf_download_requested_and_render_failure_alerts",
        "test_pdf_download_requested_and_render_failure_alerts",
    ),
    # ── 4. 白名单三侧（少一侧 = 静默丢属性）──
    (
        "L1",
        "前端白名单删掉 reason：客户端入队成功、后端匿名化时整条剔空",
        [(FRONT_ANALYTICS, "  'reason',\n", "")],
        "pytest", f"{OBS}::test_reason_key_registered_on_all_three_sides",
        "test_reason_key_registered_on_all_three_sides",
    ),
    (
        "L2",
        "Python frozenset 删掉 reason：中继侧把失败原因整列丢掉，指标永远显示空",
        [(BACK_ANALYTICS, '        "reason",\n', "")],
        "pytest", f"{OBS}::test_reason_key_registered_on_all_three_sides",
        "test_reason_key_registered_on_all_three_sides",
    ),
    (
        "L3",
        "Go map 删掉 reason：canonical 面写入路径与 py-api 面各说一套",
        [(GO_ANALYTICS, '\t"reason":             true,\n', "")],
        "pytest", f"{OBS}::test_reason_key_registered_on_all_three_sides",
        "test_reason_key_registered_on_all_three_sides",
    ),
    # ── 5. 客户端最后一跳（jest 闸）──
    (
        "P1",
        "删掉成功上报：付了钱、后端出货、用户手里没东西这一形状重新不可见",
        [(PAYWALL, OK_TRACK, "")],
        "jest", JEST_GATE, "archive_share_result 恰好两个调用点",
    ),
    (
        "P2",
        "成功上报挪到 exportPdf 之前：分享面板还没接住就宣布 ok",
        [(PAYWALL,
          "      await exportPdf(bytes, lifeArchivePdfFile(scope));\n" + OK_TRACK,
          OK_TRACK + "      await exportPdf(bytes, lifeArchivePdfFile(scope));\n")],
        "jest", JEST_GATE, "成功上报排在 exportPdf 之后",
    ),
    (
        "P3",
        "reason 回 e.message：给人看的裸文案进了分析事件（与 I2 同一红线的前端侧）",
        [(PAYWALL,
          "  if (e instanceof ApiError) return `api_${e.code}`;\n",
          "  if (e instanceof ApiError) return e.message;\n")],
        "jest", JEST_GATE, "函数体里没有任何自由文本出口",
    ),
    (
        "P4",
        "键名漂成 error_detail：不在三端白名单里，后端匿名化时整条剔空",
        [(PAYWALL, "        reason: pdfFailureReason(e),\n", "        error_detail: pdfFailureReason(e),\n")],
        "jest", JEST_GATE, "用到的键恰好是 feature/action/reason/result 四个",
    ),
    (
        "P5",
        "静默 return 也上报一次：分母被「点了但没资格」的点击噪声灌满",
        [(PAYWALL,
          "    if (anyBusy || !isArchiveOwned(scope)) return;\n",
          "    if (anyBusy || !isArchiveOwned(scope)) {\n"
          "      track('archive_share_result', {\n"
          "        feature: 'life_archive', action: 'share', result: 'failed',\n"
          "        reason: 'not_owned',\n"
          "      });\n"
          "      return;\n"
          "    }\n")],
        "jest", JEST_GATE, "未owned / 忙时的静默 return 不算一次交付尝试",
    ),
    (
        "P6",
        "失败上报排到 Alert 之后：阻塞式原生面板一弹窗，这条事件可能就永远回不来",
        [(PAYWALL,
          FAIL_TRACK + "      Alert.alert(t('common.error'), pdfFailureText(e));\n",
          "      Alert.alert(t('common.error'), pdfFailureText(e));\n" + FAIL_TRACK)],
        "jest", JEST_GATE, "失败上报带 failed + 机器码，且先上报再弹窗",
    ),
    # ── 6. 实库面（缺 TEST_DATABASE_URL 记「未跑」，不算绿）──
    (
        "PA1",
        "交付证人不再要求「付费之后」（真库面）：timestamptz 比的是真实瞬间，"
        "订阅期那行历史导出照样被当成已交付",
        [(DELIVERY,
          "            ShareExport.created_at >= UserEntitlement.purchase_date,\n",
          "")],
        "pytest-pg", f"{AUD_PG}::test_real_pg_export_after_purchase_clears_but_before_does_not",
        "test_real_pg_export_after_purchase_clears_but_before_does_not",
    ),
    (
        "PA2",
        "人群不看 revoked_at（真库面）：迁移出来的 DDL 上退款行仍留在分母，"
        "distinct 计数与 revoked 分子同时失真",
        [(DELIVERY,
          "    active = UserEntitlement.revoked_at.is_(None)\n",
          "    active = UserEntitlement.user_id.is_not(None)\n")],
        "pytest-pg", f"{AUD_PG}::test_real_pg_distinct_counts_users_not_rows_and_revoked_leaves",
        "test_real_pg_distinct_counts_users_not_rows_and_revoked_leaves",
    ),
]

GREEN: list[tuple] = [
    (
        "GR1",
        "往 audit_paid_delivery 上面插 15 行注释：审计闸判的是查询语义，不该因为文件变长而红",
        [(DELIVERY,
          "async def audit_paid_delivery(db: AsyncSession) -> dict[str, Any]:",
          "".join("# 变异证人 %d\n" % i for i in range(15))
          + "async def audit_paid_delivery(db: AsyncSession) -> dict[str, Any]:")],
        "pytest", AUD, "",
    ),
    (
        "GR2",
        "往 emit 上面插 15 行注释：事件通道闸判的是行为，不钉行号",
        [(DELIVERY, "def emit(", "".join("# 变异证人 %d\n" % i for i in range(15)) + "def emit(")],
        "pytest", OBS, "",
    ),
    (
        "GR3",
        "往 pdfFailureReason 上面插 15 行注释：前端切片闸找的是函数名，不是行号",
        [(PAYWALL,
          "function pdfFailureReason(e: unknown): string {",
          "".join("// 变异证人 %d\n" % i for i in range(15))
          + "function pdfFailureReason(e: unknown): string {")],
        "jest", JEST_GATE, "",
    ),
    (
        "GR4",
        "往 crontab 的 T1-04 说明上再插 15 行注释：排程闸判的是那条非注释行，不被注释淹没",
        [(CRONTAB, CRON_NOTE, CRON_NOTE + "".join("\n# 变异证人 %d" % i for i in range(15)))],
        "pytest", AUD, "",
    ),
]


def _apply(edits: list) -> None:
    for path, old, new in edits:
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:90]!r}"
        path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _verify_anchor(edits: list) -> str | None:
    for path, old, _new in edits:
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if n != 1:
            return f"命中 {n} 次（要 1）"
    return None


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


def run_pytest(node: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(VENV_PY), "-m", "pytest", node, "-q", "--no-header"],
        cwd=REPO, capture_output=True, text=True, timeout=1800,
    )
    return proc.returncode, proc.stdout + proc.stderr


def run_jest(test_file: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["npx", "jest", "--silent", test_file],
        cwd=FRONT, capture_output=True, text=True, timeout=1800,
    )
    return proc.returncode, proc.stdout + proc.stderr


def run(kind: str, target: str) -> tuple[int, str]:
    if kind == "jest":
        return run_jest(target)
    if kind == "pytest-pg" and not os.environ.get(PG_ENV):
        # 缺库时那条用例是 skip，pytest 退出码仍是 0——不拦下来就会把「没跑」念成「基线绿」。
        return -2, f"{PG_ENV} 未设置：{target} 没跑，实库用例不算绿"
    return run_pytest(target)


# 基线缓存：每条变异后都由 filecmp 逐字节确认写回，源状态可证与基线时一致。
BASELINE: dict[tuple[str, str], tuple[int, str]] = {}


def baseline(kind: str, target: str) -> tuple[int, str]:
    key = (kind, target)
    if key not in BASELINE:
        BASELINE[key] = run(kind, target)
    return BASELINE[key]


def is_red(code: int, out: str, kind: str, target: str, marker: str) -> bool:
    if code == 0 or not marker:
        return False
    if not any(re.search(r"\d+ (failed|error)", ln) for ln in out.splitlines()):
        return False
    if kind.startswith("pytest"):
        return f"FAILED {target}" in out
    # jest 会把通过的用例也列出来（✓ 行），所以只认「这条用例自己」的那两处印痕：
    # 失败摘要里的 `● describe › title` 与结果行里的 `✕ title`。
    return f"› {marker}" in out or f"✕ {marker}" in out


RESIDUE = [
    AUD, OBS,
    "backend/tests/test_cron_schedule_coverage.py",
    "backend/tests/test_doc_wording_batch4.py",
    "backend/tests/test_contract_freeze.py",
    "backend/tests/test_n8_batch1_contract.py",
]


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

    if not VENV_PY.is_file():
        print(f"解释器不存在：{VENV_PY}（变异自证要求先备好 .venv）")
        return 2

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-t104-") as tmp:
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
                base_code, base_out = baseline(kind, target)
                if base_code == -2:
                    unrun.append(cid)
                    print(f"[{cid}] 未跑（{base_out}）：缺库不是绿，也不把它的红记在闸头上")
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
        for node in RESIDUE:
            code, out = run_pytest(node)
            if code != 0:
                residue += 1
                print(f"残留（红！）：{node}\n  " + "\n  ".join(out.splitlines()[-12:]))
        code, out = run_jest(JEST_GATE)
        if code != 0:
            residue += 1
            print(f"残留（红！）：{JEST_GATE}\n  " + "\n  ".join(out.splitlines()[-12:]))
        if os.environ.get(PG_ENV):
            pg_code, pg_out = run_pytest(AUD_PG)
            residue += 1 if pg_code else 0
            print("复核 实库审计闸：" + ("绿" if pg_code == 0
                                        else "残留（红！）\n  " + "\n  ".join(pg_out.splitlines()[-12:])))
        else:
            print(f"复核 实库审计闸：未跑（缺 {PG_ENV}）")
        print("复核 审计／事件／排程／契约／前端闸：" + ("绿" if residue == 0 else f"{residue} 条残留"))

        print(f"\nT1-04 变异自证：打红 {red_ok}/{len(MUTATIONS)}，保持绿 {green_ok}/{len(GREEN)}，"
              f"失败 {red_fail + green_fail}，未跑 {len(unrun)}，残留 {residue}")
        if unrun:
            print(f"未跑清单（缺 {PG_ENV}，不算绿）：{', '.join(unrun)}")
        if failures:
            print(f"失败清单：{failures}")
        return 1 if (red_fail or green_fail or residue or unrun) else 0


if __name__ == "__main__":
    sys.exit(main())
