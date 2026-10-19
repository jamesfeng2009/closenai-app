// T4-02 的目标/行动面形状闸（工单 §怎么做.3 与 §验收）。
//
// 与 v3_test.go 同一条分工：这里只钉**入口与映射**——六条路由存在、身份只从
// `X-User-Id` 进、动词与错误各折成工单点名的那个码。
// 转移合法性、title 上下限、幂等与留痕在 `domain/service_goal_test.go`，
// 列的写法与归属在 `store/canonical_goal_integration_test.go`（真库）。
package httpapi_test

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

func goalHeaders() map[string]string { return v3Headers(v3Token, "owner") }

func decodeData(t *testing.T, env envelope, dst any) {
	t.Helper()
	if err := json.Unmarshal(env.Data, dst); err != nil {
		t.Fatalf("data = %s 合不成形状：%v", string(env.Data), err)
	}
}

// ── 1. 六条路由真的在，且整面随密钥消失 ──────────────────────────────────────

func TestV3GoalRoutesExistWithTokenAndVanishWithout(t *testing.T) {
	paths := []struct{ method, path, body string }{
		{"POST", "/internal/v3/goals", `{"title":"t"}`},
		{"GET", "/internal/v3/goals", ""},
		{"PATCH", "/internal/v3/goals/g1", `{"action":"complete"}`},
		{"POST", "/internal/v3/goals/g1/actions", `{"title":"a"}`},
		{"GET", "/internal/v3/actions", ""},
		{"PATCH", "/internal/v3/actions/a1", `{"action":"skip"}`},
	}
	on := newV3ServerGoals(t, nil, &stubFeedbackWriter{}, &stubGoalReader{},
		&stubGoalWriter{}, v3Token).Handler()
	for _, p := range paths {
		status, env := do(t, on, p.method, p.path, p.body, goalHeaders())
		if status == 404 && env.Code == apierr.CodeNotFound {
			t.Errorf("%s %s 回 404 信封 ⇒ 这一面缺了入口", p.method, p.path)
		}
	}
	// 反向证人：密钥为空时**六条一起不注册**（与「从来没有这个域」同形，v3.go 头注释）。
	off := newV3ServerGoals(t, nil, &stubFeedbackWriter{}, &stubGoalReader{},
		&stubGoalWriter{}, "").Handler()
	for _, p := range paths {
		status, env := do(t, off, p.method, p.path, p.body, goalHeaders())
		if status != 404 || env.Code != apierr.CodeNotFound {
			t.Errorf("密钥为空时 %s %s = %d/%d，期望 404 信封", p.method, p.path, status, env.Code)
		}
	}
}

// ── 2. 身份只从头部进来（ADR-002 在这一面上的可证落点）──────────────────────

func TestV3GoalsIgnoreIdentityFromQueryAndBody(t *testing.T) {
	writes := &stubGoalWriter{goalRec: domain.GoalReceipt{ID: "g1", Status: "active",
		Changed: true, EventID: "e1", ModelVersion: 3}}
	h := newV3ServerGoals(t, nil, &stubFeedbackWriter{}, &stubGoalReader{}, writes,
		v3Token).Handler()
	status, env := do(t, h, "POST", "/internal/v3/goals?user_id=victim",
		`{"title":"t","user_id":"victim"}`, goalHeaders())
	if status != 400 || env.Code != apierr.CodeInvalidParams {
		t.Fatalf("body 里塞 user_id = %d/%d，期望 400/40001 ⇒ 静默忽略等于让客户端以为身份生效了",
			status, env.Code)
	}
	if writes.calls != 0 {
		t.Error("被拒的请求还是走到了领域层")
	}
	// 只有头部那一格算身份：
	status, _ = do(t, h, "POST", "/internal/v3/goals?user_id=victim", `{"title":"t"}`,
		goalHeaders())
	if status != 200 || writes.gotGoal.UserID != "owner" {
		t.Errorf("status=%d 且带着 query 的 user_id 仍走到领域 = %q，期望 owner",
			status, writes.gotGoal.UserID)
	}
	// 缺 X-User-Id ⇒ 401；密钥错 ⇒ 403（两条各自挡一种坏法，v3.go 的三格）。
	if status, env = do(t, h, "GET", "/internal/v3/goals", "", v3Headers(v3Token, "")); status != 401 ||
		env.Code != apierr.CodeUnauthorized {
		t.Errorf("缺身份 = %d/%d，期望 401/40101", status, env.Code)
	}
	if status, env = do(t, h, "GET", "/internal/v3/goals", "",
		v3Headers("wrong-token", "owner")); status != 403 ||
		env.Code != apierr.CodeForbidden {
		t.Errorf("错密钥 = %d/%d，期望 403/40301", status, env.Code)
	}
}

// ── 3. 写面的形状与动词映射 ─────────────────────────────────────────────────

func TestV3GoalCreateCarriesTargetAtAndTrimsTitle(t *testing.T) {
	writes := &stubGoalWriter{goalRec: domain.GoalReceipt{ID: "g9", Status: "active",
		Changed: true, EventID: "ev9", ModelVersion: 11}}
	h := newV3ServerGoals(t, nil, &stubFeedbackWriter{}, &stubGoalReader{}, writes,
		v3Token).Handler()
	status, env := do(t, h, "POST", "/internal/v3/goals",
		`{"title":"  每天写五十行  ","description":"","target_at":"2026-12-31T00:00:00Z"}`,
		goalHeaders())
	if status != 200 || env.Code != 200 {
		t.Fatalf("创建 = %d/%d", status, env.Code)
	}
	if writes.gotGoal.UserID != "owner" || writes.gotGoal.Title != "  每天写五十行  " {
		t.Errorf("proposal = %+v ⇒ 去空白是领域的判据，HTTP 层不提前替它做一遍", writes.gotGoal)
	}
	if !writes.gotGoal.HasTargetAt || writes.gotGoal.TargetAt.IsZero() {
		t.Errorf("target_at 没进来 = %+v", writes.gotGoal)
	}
	var data v3GoalWritePayloadOf
	decodeData(t, env, &data)
	if data.GoalID != "g9" || data.ModelVersion != 11 || data.EventID != "ev9" {
		t.Errorf("data = %+v", data)
	}
	// 信封五键逐字（与 v3_test.go 同一判据）：
	raw, _ := json.Marshal(env)
	for _, k := range []string{"code", "message", "data", "trace_id", "user_id"} {
		if !strings.Contains(string(raw), `"`+k+`"`) {
			t.Errorf("信封缺键 %s：%s", k, string(raw))
		}
	}
}

// v3GoalWritePayloadOf 是**测里重述**的响应形状，不是生产类型：
// 两侧同源（用生产结构体反解）会恒真，只有把键名逐字写第二遍才测得出漂移。
type v3GoalWritePayloadOf struct {
	GoalID       string `json:"goal_id"`
	Status       string `json:"status"`
	Changed      bool   `json:"changed"`
	EventID      string `json:"event_id"`
	ModelVersion int64  `json:"model_version"`
	Detail       string `json:"detail"`
}

type v3ActionWritePayloadOf struct {
	ActionID     string  `json:"action_id"`
	GoalID       *string `json:"goal_id"`
	Status       string  `json:"status"`
	Changed      bool    `json:"changed"`
	EventID      string  `json:"event_id"`
	ModelVersion int64   `json:"model_version"`
	Detail       string  `json:"detail"`
}

type v3GoalListPayloadOf struct {
	Items []struct {
		ID          string  `json:"id"`
		Title       string  `json:"title"`
		Description string  `json:"description"`
		Status      string  `json:"status"`
		TargetAt    *string `json:"target_at"`
	} `json:"items"`
	Truncated bool `json:"truncated"`
}

type v3ActionListPayloadOf struct {
	Items []struct {
		ID        string  `json:"id"`
		Status    string  `json:"status"`
		SkippedAt *string `json:"skipped_at"`
		GoalID    *string `json:"goal_id"`
	} `json:"items"`
	Truncated bool `json:"truncated"`
}

func TestV3ActionCreateTakesGoalIDFromPath(t *testing.T) {
	writes := &stubGoalWriter{actionRec: domain.ActionReceipt{ID: "a1", Status: "active",
		GoalID: "g7", Changed: true, EventID: "ev", ModelVersion: 2}}
	h := newV3ServerGoals(t, nil, &stubFeedbackWriter{}, &stubGoalReader{}, writes,
		v3Token).Handler()
	status, env := do(t, h, "POST", "/internal/v3/goals/g7/actions",
		`{"title":"a","due_at":"2026-10-25T09:00:00Z"}`, goalHeaders())
	if status != 200 {
		t.Fatalf("创建行动 = %d %s", status, string(env.Data))
	}
	if writes.gotAction.GoalID != "g7" {
		t.Errorf("goal_id = %q，期望取自 path ⇒ 取 body 的话 path 那一格就是装饰",
			writes.gotAction.GoalID)
	}
	if !writes.gotAction.HasDueAt || writes.gotAction.DueAt.IsZero() {
		t.Errorf("due_at 没进来 = %+v", writes.gotAction)
	}
	// body 里再给一次 goal_id ⇒ 400（两格不一致时没有正确答案）。
	if status, _ = do(t, h, "POST", "/internal/v3/goals/g7/actions",
		`{"title":"a","goal_id":"g8"}`, goalHeaders()); status != 400 {
		t.Errorf("body 带 goal_id = %d，期望 400", status)
	}
	var data v3ActionWritePayloadOf
	decodeData(t, env, &data)
	if data.ActionID != "a1" || data.GoalID == nil || *data.GoalID != "g7" {
		t.Errorf("data = %+v", data)
	}
	// 反向证人：没有目标时 goal_id 是 **null** 而不是 ""。
	// 前端拿到 "" 会去渲染一个空的分组标题，而「没挂在目标下」与
	// 「挂在一颗标题为空的目标下」是两件事（goal_read.go 的同一口径）。
	writes2 := &stubGoalWriter{actionRec: domain.ActionReceipt{ID: "a2", Status: "active"}}
	h2 := newV3ServerGoals(t, nil, &stubFeedbackWriter{}, &stubGoalReader{}, writes2,
		v3Token).Handler()
	_, env = do(t, h2, "POST", "/internal/v3/goals/g7/actions", `{"title":"a"}`, goalHeaders())
	if !strings.Contains(string(env.Data), `"goal_id":null`) {
		t.Errorf("data = %s，期望 goal_id 为 null", string(env.Data))
	}
}

// ── 4. 错误映射：409 / 404 各一格，且「不存在」与「别人的行」同码 ─────────────

func TestV3GoalErrorMapping(t *testing.T) {
	cases := []struct {
		name       string
		err        error
		wantStatus int
		wantCode   int
	}{
		{"转移非法", domain.ErrGoalIllegalTransition, 409, apierr.CodeConflict},
		{"行动转移非法", domain.ErrActionIllegalTransition, 409, apierr.CodeConflict},
		{"目标不在 active", domain.ErrGoalNotActive, 409, apierr.CodeConflict},
		{"跨用户或不存在", domain.ErrInvalidOwnership, 404, apierr.CodeResourceNotFound},
		{"入参不合形", domain.ErrInvalidGoalProposal, 400, apierr.CodeInvalidParams},
		{"行动入参", domain.ErrInvalidActionProposal, 400, apierr.CodeInvalidParams},
	}
	for _, c := range cases {
		writes := &stubGoalWriter{err: c.err}
		h := newV3ServerGoals(t, nil, &stubFeedbackWriter{}, &stubGoalReader{}, writes,
			v3Token).Handler()
		status, env := do(t, h, "PATCH", "/internal/v3/goals/g1",
			`{"action":"complete"}`, goalHeaders())
		if status != c.wantStatus || env.Code != c.wantCode {
			t.Errorf("%s：PATCH goal = %d/%d，期望 %d/%d",
				c.name, status, env.Code, c.wantStatus, c.wantCode)
		}
		status, env = do(t, h, "PATCH", "/internal/v3/actions/a1",
			`{"action":"skip"}`, goalHeaders())
		if status != c.wantStatus || env.Code != c.wantCode {
			t.Errorf("%s：PATCH action = %d/%d，期望 %d/%d",
				c.name, status, env.Code, c.wantStatus, c.wantCode)
		}
	}
	// 跨用户与不存在**必须**同一个码：分成 403 与 404 等于替旁人确认这颗 id 是真的。
	notFound := &stubGoalWriter{err: domain.ErrInvalidOwnership}
	h := newV3ServerGoals(t, nil, &stubFeedbackWriter{}, &stubGoalReader{}, notFound,
		v3Token).Handler()
	_, env := do(t, h, "POST", "/internal/v3/goals/gx/actions", `{"title":"a"}`, goalHeaders())
	if env.Code != apierr.CodeResourceNotFound {
		t.Errorf("挂在别人的目标下 = %d，期望 40402", env.Code)
	}
}

func TestV3TransitionRejectsUnknownVerbAndExtraKeys(t *testing.T) {
	writes := &stubGoalWriter{goalRec: domain.GoalReceipt{ID: "g1", Status: "cancelled",
		Changed: true}}
	h := newV3ServerGoals(t, nil, &stubFeedbackWriter{}, &stubGoalReader{}, writes,
		v3Token).Handler()
	for _, body := range []string{`{}`, `{"action":"archive"}`, `{"action":"complete","status":"x"}`} {
		status, env := do(t, h, "PATCH", "/internal/v3/goals/g1", body, goalHeaders())
		if status != 400 || env.Code != apierr.CodeInvalidParams {
			t.Errorf("%s → %d/%d，期望 400/40001", body, status, env.Code)
		}
	}
	if writes.calls != 0 {
		t.Errorf("三次坏请求里有 %d 次走到了领域", writes.calls)
	}
	// skip 是**合法动词**，只是目标面不接受：那一格由 domain 判 ⇒ 409，不是这里的 400。
	status, _ := do(t, h, "PATCH", "/internal/v3/goals/g1", `{"action":"skip"}`, goalHeaders())
	if status != 200 {
		t.Errorf("stub 收到 skip 却回 %d ⇒ HTTP 层把它当非法入参挡了（判据越界）", status)
	}
	if writes.gotVerb != domain.VerbSkip {
		t.Errorf("送进领域的动词 = %q，期望 skip", writes.gotVerb)
	}
}

// ── 5. 两条列表面 ───────────────────────────────────────────────────────────

func TestV3GoalsListForwardsFiltersAndRendersNullTimes(t *testing.T) {
	read := &stubGoalReader{goals: []domain.GoalRecord{
		{ID: "g1", Title: "t1", Status: "active"},
		{ID: "g2", Title: "t2", Status: "completed"},
	}}
	h := newV3ServerGoals(t, nil, &stubFeedbackWriter{}, read, &stubGoalWriter{},
		v3Token).Handler()
	status, env := do(t, h, "GET", "/internal/v3/goals?status=completed&limit=2", "",
		goalHeaders())
	if status != 200 {
		t.Fatalf("列表 = %d %s", status, string(env.Data))
	}
	if read.gotStat != "completed" || read.gotUser != "owner" {
		t.Errorf("读面收到 (user=%q, status=%q)", read.gotUser, read.gotStat)
	}
	if read.gotLimit != 3 {
		t.Errorf("limit = %d，期望 2+1 的证人", read.gotLimit)
	}
	var data v3GoalListPayloadOf
	decodeData(t, env, &data)
	if len(data.Items) != 2 || data.Truncated {
		t.Errorf("data = %+v", data)
	}
	if data.Items[0].TargetAt != nil {
		t.Error("没有 target_at 的行却给出了一个时间 ⇒ 假装有值比空着更难归因")
	}
	// limit 非正整数一律 400（与 T3-01 同一判据：静默回默认值会把拼写错误藏成分页变小）。
	for _, bad := range []string{"limit=abc", "limit=0", "limit=-3"} {
		if s, _ := do(t, h, "GET", "/internal/v3/goals?"+bad, "", goalHeaders()); s != 400 {
			t.Errorf("%s → %d，期望 400", bad, s)
		}
	}
}

func TestV3ActionsListMarksTruncationAndKeepsGoalIDNull(t *testing.T) {
	rows := make([]domain.ActionRecord, 0, 3)
	for i := 0; i < 3; i++ {
		rows = append(rows, domain.ActionRecord{ID: "a" + string(rune('0'+i)),
			Status: "skipped"})
	}
	read := &stubGoalReader{actions: rows}
	h := newV3ServerGoals(t, nil, &stubFeedbackWriter{}, read, &stubGoalWriter{},
		v3Token).Handler()
	status, env := do(t, h, "GET", "/internal/v3/actions?limit=2", "", goalHeaders())
	if status != 200 {
		t.Fatalf("行动列表 = %d %s", status, string(env.Data))
	}
	var data v3ActionListPayloadOf
	decodeData(t, env, &data)
	if len(data.Items) != 2 || !data.Truncated {
		t.Errorf("data = %+v，期望截断成两条并标 truncated", data)
	}
	for _, it := range data.Items {
		if it.GoalID != nil {
			t.Errorf("%s 的 goal_id = %q，期望 null", it.ID, *it.GoalID)
		}
	}
	if read.gotLimit != 3 {
		t.Errorf("读面 limit = %d，期望多取一条来判断截断", read.gotLimit)
	}
	// 读面故障必须是 5xx 而不是空列表：静默的空集会让「行动都消失了」看起来像真的。
	failing := &stubGoalReader{err: errors.New("db down")}
	h2 := newV3ServerGoals(t, nil, &stubFeedbackWriter{}, failing, &stubGoalWriter{},
		v3Token).Handler()
	if s, e := do(t, h2, "GET", "/internal/v3/actions", "", goalHeaders()); s < 500 ||
		e.Code == apierr.CodeNotFound {
		t.Errorf("读库故障 = %d/%d，期望 5xx 信封", s, e.Code)
	}
}
