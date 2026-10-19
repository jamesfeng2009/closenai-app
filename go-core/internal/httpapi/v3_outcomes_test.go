// T4-03 的入口形状闸（工单 §验收：非法 outcome_type 400、跨用户 404）。
//
// 三份判据不重叠：这里只钉**路由与映射**（这一面存不存在、身份从哪进、错误折成哪个码、
// 响应有哪十键）；origin/targets/词表/同日幂等在 `domain/action_outcome_test.go`（假桩，
// 断的是提案的钉子）；列与事务在 `store/canonical_outcome_integration_test.go`（真 PG）。
// 所以本文件用桩，一条 SQL 也不发——它的价值恰好在于「桩回什么，客户端就看到什么」，
// 与数据库里到底有没有那行无关。
package httpapi_test

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/httpapi"
)

// v3OutcomePayloadOf 是**测里重述**的响应形状，不是生产类型：
// 拿生产结构体反解自己的输出会恒真，只有把键名逐字写第二遍才测得出漂移。
type v3OutcomePayloadOf struct {
	OutcomeID       string `json:"outcome_id"`
	ActionID        string `json:"action_id"`
	OutcomeType     string `json:"outcome_type"`
	Result          string `json:"result"`
	Status          string `json:"status"`
	Changed         bool   `json:"changed"`
	ModelVersion    int64  `json:"model_version"`
	CompletedTarget string `json:"completed_target"`
	OperationID     string `json:"operation_id"`
	Detail          string `json:"detail"`
}

func outcomeServer(t *testing.T, writes httpapi.OutcomeWriter,
	token string) *httpapi.Server {
	t.Helper()
	return newV3ServerFull(t, nil, &stubFeedbackWriter{}, nil, nil, writes, token)
}

// ── 1. 路由存在，且随内部面五格里的任一格一起消失 ─────────────────────────────

func TestV3OutcomeRouteExistsAndIdentityComesOnlyFromHeader(t *testing.T) {
	const path = "/internal/v3/actions/a7/outcomes"
	on := outcomeServer(t, &stubOutcomeWriter{}, v3Token).Handler()
	status, env := do(t, on, "POST", path, `{"outcome_type":"commitment_completed",
		"result":"better"}`, goalHeaders())
	if status != 200 || env.Code != 200 {
		t.Fatalf("结果面 = %d/%d，期望 200 ⇒ 这一面缺了入口", status, env.Code)
	}
	// 密钥为空 ⇒ 整面不注册（与 v3.go 头注释那条取舍同判据）。
	off := outcomeServer(t, &stubOutcomeWriter{}, "").Handler()
	if s, e := do(t, off, "POST", path, `{"outcome_type":"commitment_completed"}`,
		goalHeaders()); s != 404 || e.Code != apierr.CodeNotFound {
		t.Errorf("密钥为空时 = %d/%d，期望 404 信封（这一面应与「从来没有它」同形）", s, e.Code)
	}
	// 身份只从 header 进：body 里多一个 user_id ⇒ 400 而不是「静默忽略」。
	writes := &stubOutcomeWriter{}
	h := outcomeServer(t, writes, v3Token).Handler()
	if s, e := do(t, h, "POST", path+"?user_id=victim",
		`{"outcome_type":"commitment_completed","user_id":"victim"}`, goalHeaders()); s != 400 ||
		e.Code != apierr.CodeInvalidParams {
		t.Errorf("body 塞 user_id = %d/%d，期望 400/40001 ⇒ 静默忽略等于让客户端以为身份生效了",
			s, e.Code)
	}
	if writes.calls != 0 {
		t.Error("被拒的请求还是走到了领域层")
	}
	if s, _ := do(t, h, "POST", path+"?user_id=victim",
		`{"outcome_type":"commitment_completed"}`, goalHeaders()); s != 200 ||
		writes.got.UserID != "owner" {
		t.Errorf("带着 query 的 user_id 时 status=%d、领域拿到 user=%q，期望 200/owner",
			s, writes.got.UserID)
	}
	// 缺身份 ⇒ 401、错密钥 ⇒ 403（与读面同一格，这一面也必须各自挡住）。
	if s, e := do(t, h, "POST", path, `{}`, v3Headers(v3Token, "")); s != 401 ||
		e.Code != apierr.CodeUnauthorized {
		t.Errorf("缺身份 = %d/%d，期望 401/40101", s, e.Code)
	}
	if s, e := do(t, h, "POST", path, `{}`, v3Headers("wrong-token", "owner")); s != 403 ||
		e.Code != apierr.CodeForbidden {
		t.Errorf("错密钥 = %d/%d，期望 403/40301", s, e.Code)
	}
}

// ── 2. 请求体四格 + operation_id 的搬运 ──────────────────────────────────────

func TestV3OutcomeCarriesPathIdOccurredAtAndNote(t *testing.T) {
	writes := &stubOutcomeWriter{rec: domain.ActionOutcomeReceipt{
		OutcomeID: "o1", ActionID: "a7", OutcomeType: "commitment_completed",
		Result: "better", Status: "recorded", Changed: true, ModelVersion: 12,
		OperationID: "op-9", CompletedTarget: "a7", Detail: "记下了"}}
	h := outcomeServer(t, writes, v3Token).Handler()
	status, env := do(t, h, "POST", "/internal/v3/actions/a7/outcomes",
		`{"outcome_type":"commitment_completed","result":"better",
		  "note":"这周真的去了三次","occurred_at":"2026-10-11T02:03:04Z",
		  "operation_id":"op-9"}`, goalHeaders())
	if status != 200 {
		t.Fatalf("status = %d，data = %s", status, string(env.Data))
	}
	if writes.got.ActionID != "a7" {
		t.Errorf("action_id = %q，期望取自 path ⇒ 取 body 的话 path 那一格就是装饰",
			writes.got.ActionID)
	}
	if writes.got.OccurredAt.IsZero() || !writes.got.OccurredAt.Equal(
		time.Date(2026, 10, 11, 2, 3, 4, 0, time.UTC)) {
		t.Errorf("occurred_at = %v，期望原样解析进来", writes.got.OccurredAt)
	}
	if writes.got.Note != "这周真的去了三次" || writes.got.OperationID != "op-9" {
		t.Errorf("提案 = %+v ⇒ 备注或 operation_id 没走到领域", writes.got)
	}
	// origin **不在**请求体的字段表里：客户端自报 origin 就能决定「这条算不算
	// 模型相信的事」（O-5），所以这一格必须由 domain 钉死，HTTP 层连收都不收。
	if s, _ := do(t, h, "POST", "/internal/v3/actions/a7/outcomes",
		`{"outcome_type":"commitment_completed","origin":"llm_inference"}`,
		goalHeaders()); s != 400 {
		t.Errorf("body 带 origin = %d，期望 400（未知键一律拒）", s)
	}
	var data v3OutcomePayloadOf
	decodeData(t, env, &data)
	if data.OutcomeID != "o1" || data.ActionID != "a7" || data.Status != "recorded" ||
		data.ModelVersion != 12 || !data.Changed || data.CompletedTarget != "a7" ||
		data.OperationID != "op-9" || data.Detail != "记下了" {
		t.Errorf("data = %+v", data)
	}
	// 信封五键逐字（与 v3_test.go 同一判据）。
	raw, _ := json.Marshal(env)
	for _, k := range []string{"code", "message", "data", "trace_id", "user_id"} {
		if !strings.Contains(string(raw), `"`+k+`"`) {
			t.Errorf("信封缺键 %s：%s", k, string(raw))
		}
	}
}

func TestV3OutcomeRejectsBadBodiesBeforeTheDomain(t *testing.T) {
	cases := []struct {
		name string
		body string
	}{
		{"occurred_at 不是 RFC3339", `{"outcome_type":"commitment_completed",
			"occurred_at":"昨天下午"}`},
		{"多传一个未知键", `{"outcome_type":"commitment_completed","channel":"sms"}`},
		{"空 body", ``},
		{"不是对象", `["commitment_completed"]`},
	}
	for _, c := range cases {
		writes := &stubOutcomeWriter{}
		h := outcomeServer(t, writes, v3Token).Handler()
		status, env := do(t, h, "POST", "/internal/v3/actions/a7/outcomes", c.body,
			goalHeaders())
		if status != 400 || env.Code != apierr.CodeInvalidParams {
			t.Errorf("%s → %d/%d，期望 400/40001", c.name, status, env.Code)
		}
		if writes.calls != 0 {
			t.Errorf("%s：坏请求还是走到了领域 %d 次", c.name, writes.calls)
		}
	}
	// 备注上限：501 rune 拒、500 rune 收，且**按 rune 数**不按字节。
	long := "一" + strings.Repeat("结", 500)
	h501 := outcomeServer(t, &stubOutcomeWriter{}, v3Token).Handler()
	if s, _ := do(t, h501, "POST", "/internal/v3/actions/a7/outcomes",
		`{"outcome_type":"commitment_completed","note":"`+long+`"}`,
		goalHeaders()); s != 400 {
		t.Errorf("501 个汉字备注 = %d，期望 400（一屏输入框装不下，放开等于让一次粘贴写进权威表）", s)
	}
	writes := &stubOutcomeWriter{}
	h500 := outcomeServer(t, writes, v3Token).Handler()
	if s, _ := do(t, h500, "POST", "/internal/v3/actions/a7/outcomes",
		`{"outcome_type":"commitment_completed","note":"`+strings.Repeat("结", 500)+`"}`,
		goalHeaders()); s != 200 || writes.calls != 1 {
		t.Errorf("500 个汉字备注 = %d、领域调用 %d 次，期望 200/1 ⇒ 上限被写成了字节数",
			s, writes.calls)
	}
}

// ── 3. 错误映射（§验收 前两格）────────────────────────────────────────────────

func TestV3OutcomeErrorMapping(t *testing.T) {
	cases := []struct {
		name       string
		err        error
		wantStatus int
		wantCode   int
	}{
		// §验收 逐字「非法 outcome_type 400」：词表与「这条面不允许挂 goal 级结果」
		// 都由 domain 折成同一个哨兵，HTTP 层不重演第二张表。
		{"提案不合形", domain.ErrInvalidOutcomeProposal, 400, apierr.CodeInvalidParams},
		{"跨用户或不存在", domain.ErrInvalidOwnership, 404, apierr.CodeResourceNotFound},
		{"领域内部故障", errors.New("canonical db down"), 500, apierr.CodeInternalError},
	}
	for _, c := range cases {
		h := outcomeServer(t, &stubOutcomeWriter{err: c.err}, v3Token).Handler()
		status, env := do(t, h, "POST", "/internal/v3/actions/a7/outcomes",
			`{"outcome_type":"commitment_completed"}`, goalHeaders())
		if status != c.wantStatus || env.Code != c.wantCode {
			t.Errorf("%s = %d/%d，期望 %d/%d", c.name, status, env.Code,
				c.wantStatus, c.wantCode)
		}
	}
	// 哨兵**匹配不上**时不许折成 400：裸字符串错误走 asAPIError ⇒ 5xx。
	// 这条挡的是「领域改了错误类型、HTTP 层的 Is() 悄悄全落空，于是所有故障都变成
	// 让用户去改 outcome_type 的 400」。
	h := outcomeServer(t, &stubOutcomeWriter{
		err: errors.New("wrap: " + domain.ErrInvalidOutcomeProposal.Error())}, v3Token).Handler()
	if s, e := do(t, h, "POST", "/internal/v3/actions/a7/outcomes",
		`{"outcome_type":"commitment_completed"}`, goalHeaders()); s < 500 ||
		e.Code == apierr.CodeInvalidParams {
		t.Errorf("未识别的领域错误 = %d/%d，期望 5xx ⇒ 客户端会按 400 去改参数", s, e.Code)
	}
	// 包了一层的哨兵**必须**还是 400：errors.Is 走链条，domain 里 fmt.Errorf("%w: …")
	// 的那八处包装全靠这一格才不是白写。
	h2 := outcomeServer(t, &stubOutcomeWriter{
		err: fmt.Errorf("记录结果失败: %w", domain.ErrInvalidOutcomeProposal)},
		v3Token).Handler()
	if s, e := do(t, h2, "POST", "/internal/v3/actions/a7/outcomes",
		`{"outcome_type":"astrology_reading"}`, goalHeaders()); s != 400 ||
		e.Code != apierr.CodeInvalidParams {
		t.Errorf("包装过的合形错误 = %d/%d，期望 400/40001", s, e.Code)
	}
	// 跨用户与「不存在」**必须**同一个码：分成 403 与 404 等于替旁人确认这颗 id 真的存在。
	h3 := outcomeServer(t, &stubOutcomeWriter{err: domain.ErrInvalidOwnership}, v3Token).Handler()
	_, env := do(t, h3, "POST", "/internal/v3/actions/does-not-exist/outcomes",
		`{"outcome_type":"commitment_completed"}`, goalHeaders())
	if env.Code != apierr.CodeResourceNotFound {
		t.Errorf("不存在的行动 = %d，期望 40402（与越权同码）", env.Code)
	}
}

// ── 4. 同日幂等的可见形状（Changed=false 必须带 Detail）───────────────────────

func TestV3OutcomeSameDayDuplicateKeepsFirstResult(t *testing.T) {
	// 领域侧的同日判定有它自己的闸（domain/action_outcome_test.go）；
	// 这里钉的是**客户端能不能分辨**「记下了」与「今天已经记过、回的是首次结果」。
	first := domain.ActionOutcomeReceipt{OutcomeID: "o-first", ActionID: "a7",
		OutcomeType: "commitment_completed", Result: "better", Status: "recorded",
		Changed: false, ModelVersion: 8, OperationID: "op-retry",
		Detail: "今天已经记过一条 commitment_completed 结果（outcome=o-first），这次不重复记账"}
	h := outcomeServer(t, &stubOutcomeWriter{rec: first}, v3Token).Handler()
	status, env := do(t, h, "POST", "/internal/v3/actions/a7/outcomes",
		`{"outcome_type":"commitment_completed","result":"worse","operation_id":"op-retry"}`,
		goalHeaders())
	if status != 200 {
		t.Fatalf("重复提交 = %d，期望 200 + changed=false（回 409 会让客户端以为按钮坏了）", status)
	}
	var data v3OutcomePayloadOf
	decodeData(t, env, &data)
	if data.Changed {
		t.Error("changed = true，期望 false")
	}
	if data.OutcomeID != "o-first" || data.Result != "better" || data.ModelVersion != 8 {
		t.Errorf("data = %+v，期望回的是**首次**结果而不是这次提交的 worse", data)
	}
	if data.Detail == "" {
		t.Error("changed=false 却没有 detail ⇒ 客户端只能猜为什么没写")
	}
	if data.OperationID != "op-retry" {
		t.Errorf("operation_id 回显 = %q，客户端没法把重试对上自己那次点击", data.OperationID)
	}
	// status 那一格必须活着：recorded 与 candidate 在屏上是两句话（O-5）。
	if data.Status != "recorded" {
		t.Errorf("status = %q", data.Status)
	}
}
