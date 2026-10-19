#!/usr/bin/env python3
"""T3-01／T3-02（内部面洞察读面＋六态反馈）的变异自证（2026-10-19）。

这一票交付的是「入口与门禁」，它的死法有两种，两侧都得上靶
（[[feedback-mutation-anchor-must-reach-target]]）：

* **门禁被掏空**（httpapi 面 M1…M18）：这一面的鉴权是服务密钥而不是用户 JWT，
  「不信 body 里的 user_id」「没配密钥就整面不注册」「correct 必须有正文」每一格都是一句「不许」。
  掏掉哪一格，红的就必须是**那一格自己的用例**。
* **判据分叉**（domain／store／生成物面 M19…M35）：可见状态集合、游标精度、
  limit+1 的翻页判据、「无证据不上线」的 SQL 内连、反馈的状态矩阵——这些一旦在
  两个面各抄一份，编译器与单测都不报错，只有对账闸与真库集成测会红。

靶面分五侧（runner 一律 `go test -run <点名用例>`，缺工具链记「未跑」）：
`internal/httpapi`（入口面）、`internal/domain`（判据本体）、`internal/store`（真库闸 G-05／G-06，
需 `TEST_DATABASE_URL`）、`cmd/go-api`（装配与调用点台账）、`internal/store/canonicaldb`（生成物＝真正执行的那侧 SQL）。

纪律：串行执行、copy2 备份＋`filecmp(shallow=False)` 逐字节写回、不用 git checkout
（[[feedback-mutation-restore-no-git-checkout]]、[[feedback-mutation-gates-no-concurrency]]）。
每条变异前先跑基线（按 (target,marker) 缓存）；基线本来就不绿的记「未跑」，
不把环境的红记成闸的红；`[build failed]` 一律记「打红 ✗（变异体自身不合法）」。

G-05／G-06 的靶打在**生成物**（`canonicaldb/canonical.sql.go`）而不是 `db/queries/canonical.sql`：
后者不参与执行，改它一条测试都不会红——那正是 T6-03 登记的「生成物滞后」缺口的另一面。

用法：
    ./.venv/bin/python scripts/mutate_t30x_gates.py --check
    GOROOT=/Users/fengyu/sdk/go1.27.1 TEST_DATABASE_URL=… ./.venv/bin/python scripts/mutate_t30x_gates.py
    ./.venv/bin/python scripts/mutate_t30x_gates.py --only M4,M17
"""
from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GO_CORE = REPO / "go-core"
GOROOT = os.environ.get("GOROOT", "/Users/fengyu/sdk/go1.27.1")
GO_BIN = os.environ.get("GO_BIN", f"{GOROOT}/bin/go")
GO_ENV = {
    **os.environ,
    "GOROOT": GOROOT,
    "PATH": f"{GOROOT}/bin:{os.environ.get('PATH', '')}",
    "GO_BIN": GO_BIN,
    "GOTOOLCHAIN": "local",
}

V3GO = GO_CORE / "internal" / "httpapi" / "v3.go"
V3IN = GO_CORE / "internal" / "httpapi" / "v3_insights.go"
V3FB = GO_CORE / "internal" / "httpapi" / "v3_feedback.go"
ROUTES = GO_CORE / "internal" / "httpapi" / "routes.go"
BR = GO_CORE / "internal" / "domain" / "insight_browse.go"
ER = GO_CORE / "internal" / "domain" / "evidence_read.go"
SO = GO_CORE / "internal" / "domain" / "service_outcome.go"
GEN = GO_CORE / "internal" / "store" / "canonicaldb" / "canonical.sql.go"
MAIN = GO_CORE / "cmd" / "go-api" / "main.go"

PKG_HTTPAPI = "./internal/httpapi/"
PKG_DOMAIN = "./internal/domain/"
PKG_STORE = "./internal/store/"
PKG_MAIN = "./cmd/go-api/"

T = "\t"

# 生成物里那条列表查询的六个字面行（M32／M33 的公共靶面）。
SQLBLOCK = (
    f"{T}JOIN insight_evidence ie ON ie.insight_id = i.id AND ie.user_id = i.user_id\n"
    f"{T}JOIN evidence e ON e.id = ie.evidence_id\n"
    f"{T}JOIN source_records s ON s.id = e.source_record_id\n"
    "WHERE i.user_id = $1 AND i.status = ANY($2::varchar[])\n"
    "  AND i.published_at IS NOT NULL\n"
    "  AND s.status <> 'deleted'\n"
)

MUTATIONS: list[tuple] = [
    # ── 入口面：鉴权与注册判据 ──────────────────────────────────────────
    (
        "M1", "服务密钥不合从 403 退化成 401 ⇒ BFF 收到「请重新认证」，而它没有这个动作",
        [(V3GO, f'{T}return "", r, apierr.New(apierr.CodeForbidden, "服务间凭据无效 / Invalid service token")\n',
          f'{T}return "", r, apierr.New(apierr.CodeUnauthorized, "服务间凭据无效 / Invalid service token")\n')],
        PKG_HTTPAPI, "^TestV3RejectsMissingOrWrongServiceToken$",
    ),
    (
        "M2", "摘掉「anonymous 占位身份也算缺身份」⇒ 查一颗不存在的主键，长得像「这个用户没有洞察」",
        [(V3GO, f'{T}if userID == "" || userID == anonymous {{\n',
          f'{T}if userID == "" {{\n')],
        PKG_HTTPAPI, "^TestV3RejectsMissingOrWrongServiceToken$",
    ),
    (
        "M3", "鉴权失败支把 nil request 带回去（2026-10-19 第一版真实犯过的错）⇒ 403 被中间件兜成 500",
        [(V3GO, f'{T}return "", r, apierr.New(apierr.CodeUnauthorized, "缺少用户身份 / Missing user id")\n',
          f'{T}return "", nil, apierr.New(apierr.CodeUnauthorized, "缺少用户身份 / Missing user id")\n')],
        PKG_HTTPAPI, "^TestV3RejectsMissingOrWrongServiceToken$",
    ),
    (
        "M4", "密钥未配置也从「整面不注册」退化成「注册」⇒ BFF 拿到形状陌生的 403 而不是同形 404",
        [(V3GO, f'{T}{T}s.cfg.GoServiceToken != ""\n', f'{T}{T}s.cfg.AppName != ""\n')],
        PKG_HTTPAPI, "^TestV3SurfaceIsNotRegisteredWithoutServiceToken$",
    ),
    (
        "M5", "给内部路由填上切流域键 ⇒ G-02／ADR-002 的反面：内部面被当成可切给客户端的路由",
        [(ROUTES, f'{{method: "GET", path: "/internal/v3/insights",\n',
          f'{{method: "GET", path: "/internal/v3/insights", domain: cutoverMemories,\n')],
        PKG_HTTPAPI, "^TestInternalRoutesAreAbsentFromClientContract$",
    ),
    (
        "M6", "摘掉反馈那条路由声明 ⇒ 这一面缺了写入口，而 httpapi 其余用例看不见",
        [(ROUTES, f'{T}{{method: "POST", path: "/internal/v3/insights/{{id}}/feedback",\n'
                  f'{T}{T}gated:   func(s *Server) bool {{ return s.internalEnabled() }},\n'
                  f'{T}{T}handler: func(s *Server) http.HandlerFunc {{ return s.handleV3InsightFeedback }}}},\n',
          "")],
        PKG_HTTPAPI, "^TestInternalRoutesAreAbsentFromClientContract$",
    ),
    # ── 入口面：读面形状 ───────────────────────────────────────────────
    (
        "M7", "坏游标不再显式发 400 ⇒ 退化成一条形状陌生的 5xx，翻页方分不清「游标坏了」与「Go 挂了」",
        [(V3IN, f'{T}{T}if errors.Is(err, domain.ErrInsightCursorInvalid) {{\n'
               f'{T}{T}{T}// 显式 400，不是「当成第一页」：静默回第一页会让翻页方永远拿不到后面的行，\n'
               f'{T}{T}{T}// 而它看到的状态码是 200（insight_browse.go:57 同一条理由）。\n'
               f'{T}{T}{T}writeJSONError(w, r, apierr.New(apierr.CodeInvalidParams,\n'
               f'{T}{T}{T}{T}"翻页游标无法识别 / Unrecognized page cursor"))\n'
               f'{T}{T}{T}return\n'
               f'{T}{T}}}\n',
          f'{T}{T}// 变异 M7：坏游标不显式发言\n')],
        PKG_HTTPAPI, "^TestV3ListBadCursorIsExplicitFourHundred$",
    ),
    (
        "M8", "limit=abc 当成默认值 ⇒ 调用方的拼写错误在生产里表现为「分页变小了」，最难归因的一类空",
        [(V3IN, f'{T}if err != nil || n <= 0 {{\n{T}{T}return 0, apierr.New(apierr.CodeInvalidParams, "limit 必须是正整数 / limit must be a positive integer")\n{T}}}\n',
          f'{T}if err != nil || n <= 0 {{\n{T}{T}return 0, nil\n{T}}}\n')],
        PKG_HTTPAPI, "^TestV3ListRejectsNonPositiveLimit$",
    ),
    (
        "M9", "列表把 evidence_count 抹成 0 ⇒ 前端只能逐颗洞察再问一次依据（工单点名要避开的 N+1）",
        [(V3IN, f'{T}{T}{T}PublishedAt: item.PublishedAt, EvidenceCount: item.EvidenceCount,\n',
          f'{T}{T}{T}PublishedAt: item.PublishedAt, EvidenceCount: 0,\n')],
        PKG_HTTPAPI, "^TestV3ListShapeAndPaginationBoundary$",
    ),
    (
        "M10", "详情的「不存在」不再折成 40402 ⇒ 领域错误顺着 asAPIError 掉成 500，跨用户探测有了回声",
        [(V3IN, f'{T}{T}if errors.Is(err, domain.ErrInsightNotFound) ||\n'
               f'{T}{T}{T}errors.Is(err, domain.ErrInsightNotVisible) {{\n',
          f'{T}{T}if errors.Is(err, domain.ErrInsightNotVisible) {{\n')],
        PKG_HTTPAPI, "^TestV3DetailForeignOrInvisibleIsSingleFourOhFour$",
    ),
    # ── 入口面：反馈的三道校验 ─────────────────────────────────────────
    (
        "M11", "摘掉 DisallowUnknownFields ⇒ body 里的 user_id 被静默吞掉，客户端以为身份生效了",
        [(V3FB, f'{T}dec.DisallowUnknownFields()\n', f'{T}// 变异 M11：未知键不再挡\n')],
        PKG_HTTPAPI, "^TestV3FeedbackIgnoresUserIDInBody$",
    ),
    (
        "M12", "correct 不再要求正文 ⇒ 「纠错」退化成一句表态，权威表里留下零字的 correction_text",
        [(V3FB, f'{T}if feedbackType == domain.FeedbackCorrect && correction == "" {{\n'
               f'{T}{T}writeJSONError(w, r, apierr.New(apierr.CodeInvalidParams,\n'
               f'{T}{T}{T}"纠错必须填写说明文字 / correction_text is required for correct"))\n'
               f'{T}{T}return\n'
               f'{T}}}\n',
          "")],
        PKG_HTTPAPI, "^TestV3FeedbackRejectsUnknownTypeAndEmptyCorrection$",
    ),
    (
        "M13", "纠错正文上限从 500 放开到 5000 ⇒ 与前端同值约束分叉，一次粘贴就能写进权威表",
        [(V3FB, "const maxCorrectionText = 500\n", "const maxCorrectionText = 5000\n")],
        PKG_HTTPAPI, "^TestV3FeedbackRejectsUnknownTypeAndEmptyCorrection$",
    ),
    (
        "M14", "重复表态从 409 退化成 400 ⇒ 前端的「刚刚提交过了」文案分支从此走不到",
        [(V3FB, f'{T}case errors.Is(err, domain.ErrDuplicateFeedback):\n{T}{T}return apierr.New(apierr.CodeConflict,\n',
          f'{T}case errors.Is(err, domain.ErrDuplicateFeedback):\n{T}{T}return apierr.New(apierr.CodeInvalidParams,\n')],
        PKG_HTTPAPI, "^TestV3FeedbackErrorMapping$",
    ),
    (
        "M15", "别人的行回 403 而不是与「不存在」同形 ⇒ 等于替旁人确认这颗 id 真实存在",
        [(V3FB, f'{T}case errors.Is(err, domain.ErrInvalidOwnership):\n{T}{T}return apierr.New(apierr.CodeResourceNotFound, "资源不存在 / Resource not found")\n',
          f'{T}case errors.Is(err, domain.ErrInvalidOwnership):\n{T}{T}return apierr.New(apierr.CodeForbidden, "资源不属于你 / Not yours")\n')],
        PKG_HTTPAPI, "^TestV3FeedbackErrorMapping$",
    ),
    (
        "M16", "operation_id 没带进 metadata ⇒ 幂等键丢了，判重只剩「最近一条同型」",
        [(V3FB, f'{T}{T}metadata["operation_id"] = op\n', f'{T}{T}_ = op // 变异 M16：幂等键不落 metadata\n')],
        PKG_HTTPAPI, "^TestV3FeedbackCarriesOperationIDIntoMetadata$",
    ),
    (
        "M17", "回执丢掉领域层的说明串 ⇒ partially_confirm 永远 Changed=false，客户端只能猜为什么没改写",
        [(V3FB, f'{T}{T}Changed: receipt.Changed, Detail: receipt.Detail,\n',
          f'{T}{T}Changed: receipt.Changed, Detail: "",\n')],
        PKG_HTTPAPI, "^TestV3FeedbackSixTypesAllReachDomainAndReturnReceipt$",
    ),
    (
        "M18", "端点假装写了领域层（直接回一个回执）⇒ 200 一片绿，权威表里一行没有",
        [(V3FB, f'{T}receipt, err := s.v3.Outcomes.RecordUserFeedback(r.Context(), domain.FeedbackProposal{{\n'
               f'{T}{T}UserID:         userID,\n'
               f'{T}{T}Type:           feedbackType,\n'
               f'{T}{T}TargetType:     domain.RefInsight,\n'
               f'{T}{T}TargetID:       r.PathValue("id"),\n'
               f'{T}{T}CorrectionText: correction,\n'
               f'{T}{T}Metadata:       metadata,\n'
               f'{T}}})\n'
               f'{T}if err != nil {{\n'
               f'{T}{T}writeJSONError(w, r, v3FeedbackError(err))\n'
               f'{T}{T}return\n'
               f'{T}}}\n',
          f'{T}receipt := domain.FeedbackReceipt{{TargetStatus: "confirmed", Changed: true}}\n'
          f'{T}_, _, _, _ = userID, feedbackType, correction, metadata\n')],
        PKG_MAIN, "^TestDomainServiceCallerLedger$",
    ),
    # ── 领域面：游标与翻页 ─────────────────────────────────────────────
    (
        "M19", "游标时刻编码从微秒降到秒 ⇒ 同一秒内两行被读成同一格，下一页重复或跳过",
        [(BR, 'raw := fmt.Sprintf("%d|%s", k.PublishedAt.UnixMicro(), k.ID)\n',
          'raw := fmt.Sprintf("%d|%s", k.PublishedAt.Unix(), k.ID)\n')],
        PKG_DOMAIN, "^TestInsightCursorRoundTripKeepsMicrosecondAndID$",
    ),
    (
        "M20", "「有时刻没 id」不再算畸形 ⇒ 游标悄悄换成另一格，翻页边界错位且两种都长得像成功",
        [(BR, f'{T}if !found || id == "" {{\n', f'{T}if !found {{\n')],
        PKG_DOMAIN, "^TestDecodeInsightKeyRejectsMalformedShapes$",
    ),
    (
        "M21", "领域层把坏游标当成第一页 ⇒ 调用方永远拿不到后面的行，而它看到 200",
        [(BR, f'{T}{T}decoded, err := DecodeInsightKey(cursor)\n'
             f'{T}{T}if err != nil {{\n'
             f'{T}{T}{T}return InsightListView{{}}, err\n'
             f'{T}{T}}}\n',
          f'{T}{T}decoded, _ := DecodeInsightKey(cursor) // 变异 M21：坏游标当第一页\n')],
        PKG_DOMAIN, "^TestInsightBrowserPropagatesCursorError$",
    ),
    (
        "M22", "不再多取一条 ⇒ 「页外还有行」没被证实就发游标与真尽头各错一格",
        [(BR, f'{T}rows, err := b.browse.ListInsightsForBrowsing(ctx, userID, statuses, after, hasAfter, limit+1)\n',
          f'{T}rows, err := b.browse.ListInsightsForBrowsing(ctx, userID, statuses, after, hasAfter, limit)\n')],
        PKG_DOMAIN, "^TestInsightBrowserClampsLimit$",
    ),
    (
        "M23", "摘掉 limit 上限钳制 ⇒ 一次 limit=10000 把单机 4c8G 的第一页撑爆，响应体积由调用方定",
        [(BR, f'{T}if limit > maxInsightListLimit {{\n{T}{T}limit = maxInsightListLimit\n{T}}}\n', "")],
        PKG_DOMAIN, "^TestInsightBrowserClampsLimit$",
    ),
    (
        "M24", "恰好一页也发游标 ⇒ 调用方在真尽头多打一次空请求，而那次与「游标坏了」在日志里同形",
        [(BR, f'{T}if len(rows) > limit {{\n', f'{T}if len(rows) >= limit {{\n')],
        PKG_HTTPAPI, "^TestV3ListLastPageHasNoCursor$",
    ),
    (
        "M25", "游标指向多取回来的那条而不是本页最后一行 ⇒ 每翻一页跳行一行",
        [(BR, f'{T}{T}last := rows[limit-1]\n', f'{T}{T}last := rows[limit]\n')],
        PKG_DOMAIN, "^TestInsightBrowserEmitsCursorOnlyForExtraRow$",
    ),
    # ── 领域面：可见集合 ───────────────────────────────────────────────
    (
        "M26", "状态过滤器交集失败时回落成全集 ⇒ 「过滤一个不该看的态」得到「看到全部」",
        [(BR, f'{T}{T}{T}return []string{{status}}\n{T}{T}}}\n{T}}}\n{T}return []string{{status}}\n',
          f'{T}{T}{T}return []string{{status}}\n{T}{T}}}\n{T}}}\n{T}return InsightReturnableStatuses\n')],
        PKG_DOMAIN, "^TestInsightBrowserStatusFilterCannotEscapeReturnableSet$",
    ),
    (
        "M27", "把 retracted 铺进可回看集合 ⇒ 系统替用户撤回的历史被当成用户的洞察摊开",
        [(BR, f'{T}"corrected", "expired", "dismissed")\n',
          f'{T}"corrected", "expired", "dismissed", "retracted")\n')],
        PKG_HTTPAPI, "^TestV3DetailOpensTheUserFeedbackStatuses$",
    ),
    (
        "M28", "从可回看集合里摘掉 corrected ⇒ 用户纠正过一次之后，那条洞察从列表里消失（工单 §怎么做.5）",
        [(BR, f'{T}"corrected", "expired", "dismissed")\n',
          f'{T}"expired", "dismissed")\n')],
        PKG_STORE, "^TestG06CorrectFeedbackWritesAuthoritativeRows$",
    ),
    (
        "M29", "空状态串判成可见 ⇒ 脏行（status 为空）出现在对外列表里",
        [(BR, f'{T}if status == "" {{\n{T}{T}return false\n{T}}}\n',
          f'{T}if status == "" {{\n{T}{T}return true\n{T}}}\n')],
        PKG_DOMAIN, "^TestReturnableStatusSetShape$",
    ),
    (
        "M30", "空 user_id 不再被拒 ⇒ 查一颗所有账号里都不存在的主键，回一片正常的空列表",
        [(BR, f'{T}if userID == "" {{\n', f'{T}if userID == "\\x00never" {{\n')],
        PKG_DOMAIN, "^TestInsightBrowserNeedsUserAndReader$",
    ),
    (
        "M31", "详情面用回 N9 的三态而不是并集 ⇒ 两条面各有一套「谁能看到」，纠错过的洞察详情 404",
        [(ER, f'{T}return e.explainWith(ctx, userID, insightID, isReturnableStatus)\n',
          f'{T}return e.explainWith(ctx, userID, insightID, isVisibleStatus)\n')],
        PKG_HTTPAPI, "^TestV3DetailOpensTheUserFeedbackStatuses$",
    ),
    # ── 生成物 SQL：闸 G-05 真正执行的那侧 ─────────────────────────────
    (
        "M32", "摘掉 `s.status <> 'deleted'` ⇒ 依据被撤回之后洞察仍挂在列表里冒充「有依据」（T2-06 链的下游）",
        [(GEN, SQLBLOCK, SQLBLOCK.replace("  AND s.status <> 'deleted'\n", ""))],
        PKG_STORE, "^TestG05InsightsWithoutLiveEvidenceAreNotListed$",
    ),
    (
        "M33", "三表内连改左连 ⇒ 闸 G-05「无证据不上线」被摘掉，从来没有依据的洞察也上线了",
        [(GEN, SQLBLOCK,
          SQLBLOCK.replace(f"{T}JOIN insight_evidence ie ON", f"{T}LEFT JOIN insight_evidence ie ON")
                  .replace(f"{T}JOIN evidence e ON", f"{T}LEFT JOIN evidence e ON")
                  .replace(f"{T}JOIN source_records s ON", f"{T}LEFT JOIN source_records s ON")
                  .replace("  AND s.status <> 'deleted'\n",
                           "  AND (s.status IS NULL OR s.status <> 'deleted')\n"))],
        PKG_STORE, "^TestG05InsightsWithoutLiveEvidenceAreNotListed$",
    ),
    # ── 领域写面：闸 G-06 与判重 ───────────────────────────────────────
    (
        "M34", "判重条件取反 ⇒ 连点两次同一个按钮写进两行权威记录，而「换一种表态」反倒被拒",
        [(SO, f'{T}{T}if latest.Found && latest.FeedbackType == string(f.Type) {{\n',
          f'{T}{T}if latest.Found && latest.FeedbackType != string(f.Type) {{\n')],
        PKG_DOMAIN, "^TestRecordUserFeedback_DuplicateRejected$",
    ),
    (
        "M35", "反馈不再改写 insights.status ⇒ 工单 §验收「反馈后 insights.status 按矩阵变化」落空，纠错只活在提示语里",
        [(SO, f'{T}{T}{T}if err := st.SetInsightStatus(ctx, InsightStatusRow{{UserID: f.UserID, ID: f.TargetID,\n'
             f'{T}{T}{T}{T}Status: status, ModelVersion: newVersion,\n',
          f'{T}{T}{T}if err := st.SetInsightStatus(ctx, InsightStatusRow{{UserID: f.UserID, ID: f.TargetID,\n'
             f'{T}{T}{T}{T}Status: current.Status, ModelVersion: newVersion,\n')],
        PKG_STORE, "^TestG06CorrectFeedbackWritesAuthoritativeRows$",
    ),
    # ── 装配面：唯一写入口 ─────────────────────────────────────────────
    (
        "M36", "内部面拿到第二颗 domain.NewService ⇒ 乐观锁与版本账本在两条链上各算一份（ADR 七-10 T3-02-5）",
        [(MAIN, f'{T}v3 := &httpapi.V3{{Insights: insightBrowser, Outcomes: candidateDomain}}\n',
          f'{T}v3 := &httpapi.V3{{Insights: insightBrowser, Outcomes: domain.NewService(canonical, domain.DefaultPolicy())}}\n')],
        PKG_MAIN, "^TestMainWiresTheInternalSurfaceToTheSameDomainService$",
    ),
    (
        "M37", "内部面只传读半（Outcomes 那一格没接进 HTTP 服务器）⇒ 反馈写入口一律 404，而包内用例全绿",
        [(MAIN, f'{T}{T}batch1, batch2, batch3, batch4, batch5, batch6, v3).Handler(),\n',
          f'{T}{T}batch1, batch2, batch3, batch4, batch5, batch6, &httpapi.V3{{Insights: v3.Insights}}).Handler(),\n')],
        PKG_MAIN, "^TestMainWiresTheInternalSurfaceToTheSameDomainService$",
    ),
]

GR_GO_15 = "".join(
    f"{T}// 绿闸注脚 {i:02d}：这套闸判的是语义与字面，不钉行号也不钉排版。\n" for i in range(1, 16)
)
GR_GO2_15 = "".join(
    f"// 绿闸注脚 {i:02d}：格式化不是缺陷，本批的闸不许因为一次 gofmt 就把整套闸门打成红。\n" for i in range(1, 16)
)

GREEN: list[tuple] = [
    (
        "GR1", "requireInternalService 上方插 15 行注释 ⇒ 鉴权闸照旧绿（它不钉行号）",
        [(V3GO, 'func (s *Server) requireInternalService(r *http.Request) (string, *http.Request, *apierr.Error) {\n',
          GR_GO_15 + 'func (s *Server) requireInternalService(r *http.Request) (string, *http.Request, *apierr.Error) {\n')],
        PKG_HTTPAPI, "^TestV3RejectsMissingOrWrongServiceToken$",
    ),
    (
        "GR2", "InsightBrowser.List 上方插 15 行注释 ⇒ 翻页形状闸照旧绿",
        [(BR, 'func (b *InsightBrowser) List(ctx context.Context, userID, status string,\n',
          GR_GO2_15 + 'func (b *InsightBrowser) List(ctx context.Context, userID, status string,\n')],
        PKG_DOMAIN, "^TestInsightBrowserEmitsCursorOnlyForExtraRow$",
    ),
    (
        "GR3", "v3FeedbackRequest 上方插 15 行注释 ⇒ body 未知键的闸照旧绿（它点的是符号不是位置）",
        [(V3FB, "type v3FeedbackRequest struct {\n", GR_GO2_15 + "type v3FeedbackRequest struct {\n")],
        PKG_HTTPAPI, "^TestV3FeedbackIgnoresUserIDInBody$",
    ),
]


def _apply(edits: list) -> None:
    """把锚点替换成变异体。锚点命中数必须恰好 1——命中两次说明靶写错了。"""
    for path, old, new in edits:
        assert path.exists(), f"{path}：靶文件不存在"
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:120]!r}"
        path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _verify_anchor(edits: list) -> str | None:
    for path, old, _new in edits:
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if n != 1:
            return f"锚点命中 {n} 次（要 1）：{old[:60]!r}"
    return None


def go_available() -> bool:
    return Path(GO_BIN).is_file()


def run(target: str, marker: str) -> tuple[int, str]:
    if not go_available():
        return -2, f"{GO_BIN} 不存在：Go 侧用例没跑，不算绿"
    cmd = [GO_BIN, "test", target, "-count=1", "-timeout", "300s"]
    if marker:
        cmd += ["-run", marker]
    proc = subprocess.run(cmd, cwd=GO_CORE, env=GO_ENV, capture_output=True,
                          text=True, timeout=1800)
    return proc.returncode, proc.stdout + proc.stderr


def _report_name(marker: str) -> str:
    name = marker[1:] if marker.startswith("^") else marker
    return name[:-1] if name.endswith("$") else name


def is_red(code: int, out: str, marker: str) -> bool:
    """「红」＝**那条判据自己**失败。编译失败／没匹配到用例都不算——
    那种红证明的是工具链，不是闸门。"""
    if code == 0:
        return False
    if "[build failed]" in out or "no tests to run" in out or "no test files" in out:
        return False
    return f"--- FAIL: {_report_name(marker)}" in out


BASELINE: dict[tuple[str, str], tuple[int, str]] = {}


def baseline(target: str, marker: str) -> tuple[int, str]:
    key = (target, marker)
    if key not in BASELINE:
        BASELINE[key] = run(target, marker)
    return BASELINE[key]


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

    if not go_available():
        print(f"提示：没找到 {GO_BIN}，全部靶记「未跑」，不算绿")

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-t30x-") as tmp:
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
            for cid, desc, edits, target, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(target, marker)
                if base_code == -2:
                    unrun.append(cid)
                    print(f"[{cid}] 未跑（{base_out}）：缺工具链不是绿，也不把它的红记在闸头上")
                    continue
                if base_code != 0:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 未跑（基线本来就不绿，exit={base_code}）："
                          "不能把环境的红记成闸的红\n  " + "\n  ".join(base_out.splitlines()[-8:]))
                    continue
                try:
                    _apply(edits)
                    code, out = run(target, marker)
                finally:
                    restore()
                if code != 0 and "[build failed]" in out:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（变异体编译不过，红的是工具链不是判据）：\n  "
                          + "\n  ".join(out.splitlines()[-10:]))
                    continue
                if is_red(code, out, marker):
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗  {desc}\n  " + "\n  ".join(out.splitlines()[-12:]))

            for cid, desc, edits, target, marker in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(target, marker)
                if base_code != 0:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（基线就不绿，exit={base_code}）：{desc}")
                    continue
                try:
                    _apply(edits)
                    code, out = run(target, marker)
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 被误伤 ✗  {desc}\n  " + "\n  ".join(out.splitlines()[-12:]))
        finally:
            restore()
            residue = [p for p in paths if not filecmp.cmp(originals[p], p, shallow=False)]
            print(f"\n残留复核：{'0 个靶文件与原件逐字节一致' if not residue else ''}")
            if residue:
                print("  以下靶文件恢复后仍不一致：" + "、".join(str(p) for p in residue))

        total_red = red_ok + red_fail
        total_green = green_ok + green_fail
        print(f"\n汇总：红闸 {red_ok}/{total_red}｜绿闸 {green_ok}/{total_green}｜"
              f"未跑 {len(unrun)}{'（' + ','.join(unrun) + '）' if unrun else ''}")
        if failures:
            print("失败条目：" + ",".join(failures))
        if unrun:
            print("ⓘ 有靶未跑：缺 env／缺工具链一律不算绿")
        return 1 if (red_fail or green_fail or unrun) else 0


if __name__ == "__main__":
    sys.exit(main())
