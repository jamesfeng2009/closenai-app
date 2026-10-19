// T4-02：`/internal/v3` 的目标与行动面（六条端点）。
//
// 与 T3-01/02 同一层职责：**只做形状与错误映射**。
// 转移合法性、词表、title 长度、归属核验、事件留痕全在 `internal/domain`
// （goal.go 的转移表 + service_goal.go 的四条入口）；本文件不许出现第二张
// 「动词 → 状态」的表，出现了就有两个事实源，而 domain 那一侧有它自己的单测。
//
// 六条端点都不进 `contracts/endpoints.md` 的冻结登记表：那张表由 FastAPI 生成，
// 客户端契约里没有这几条 path（ADR-002），登记面是 ADR 七-10 与 v3.go 的头注释。
package httpapi

import (
	"bytes"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// ── 请求形状 ────────────────────────────────────────────────────────────────

// v3GoalCreateRequest 的三个字段是工单 §怎么做.1 那一格。
//
// `target_at` 用 `*time.Time`：JSON 解码器自己认 RFC3339，非法字符串会走
// 「请求体不合形」⇒ 400，而不是我在这儿再写一遍 `time.Parse` 的错误映射
// （写两遍就有两种「格式不对」的说法）。
// 同样不许出现 `user_id`——`DisallowUnknownFields` 是 ADR-002 那一格的可证形式，
// 见 v3.go 的 requireInternalService。
type v3GoalCreateRequest struct {
	Title       string     `json:"title"`
	Description string     `json:"description"`
	TargetAt    *time.Time `json:"target_at"`
}

// v3ActionCreateRequest 没有 goal_id：这一条挂在 `/goals/{id}/actions` 上，
// 目标由 path 给。body 里再给一次会造出「path 说 A、body 说 B」这种无法两全的请求，
// 而多传键在这里直接 400。
type v3ActionCreateRequest struct {
	Title       string     `json:"title"`
	Description string     `json:"description"`
	DueAt       *time.Time `json:"due_at"`
}

// v3TransitionRequest 只有动词一个字段。
//
// 为什么收 `action=complete` 而不是 `status=completed`：动词决定「跳到哪一态」
// **和**「刷哪一列」（completed 刷 completed_at、cancel 只留 updated_at），
// 让调用方直接给状态词就能拼出 `status=skipped` 打到 goal 上——那一列根本不存在。
type v3TransitionRequest struct {
	Action string `json:"action"`
}

// ── 响应形状 ────────────────────────────────────────────────────────────────

type v3GoalWritePayload struct {
	GoalID       string `json:"goal_id"`
	Status       string `json:"status"`
	Changed      bool   `json:"changed"`
	EventID      string `json:"event_id"`
	ModelVersion int64  `json:"model_version"`
	// Detail 在幂等那一格必须有：Changed=false 时客户端不知道「为什么没改」就只能猜，
	// 而「已经是 completed」与「刚刚改成 completed」在前端是两种提示。
	Detail string `json:"detail"`
}

type v3ActionWritePayload struct {
	ActionID     string  `json:"action_id"`
	GoalID       *string `json:"goal_id"`
	Status       string  `json:"status"`
	Changed      bool    `json:"changed"`
	EventID      string  `json:"event_id"`
	ModelVersion int64   `json:"model_version"`
	Detail       string  `json:"detail"`
}

type v3GoalListPayload struct {
	Items []v3GoalItemWire `json:"items"`
	// Truncated 是「还有更多但没给」的唯一表达。这一面**没有游标**：目标与行动是人手
	// 立的东西，量级在几十条，而 insights 那条会随跑批无限增长才需要 keyset（T3-01）。
	// 给这一面加游标等于造一个没有第二个调用方的机制。
	Truncated bool `json:"truncated"`
}

type v3GoalItemWire struct {
	ID          string     `json:"id"`
	Title       string     `json:"title"`
	Description string     `json:"description"`
	Status      string     `json:"status"`
	StartedAt   *time.Time `json:"started_at"`
	TargetAt    *time.Time `json:"target_at"`
	CompletedAt *time.Time `json:"completed_at"`
	CreatedAt   *time.Time `json:"created_at"`
	UpdatedAt   *time.Time `json:"updated_at"`
}

type v3ActionListPayload struct {
	Items     []v3ActionItemWire `json:"items"`
	Truncated bool               `json:"truncated"`
}

type v3ActionItemWire struct {
	ID          string     `json:"id"`
	Title       string     `json:"title"`
	Description string     `json:"description"`
	Status      string     `json:"status"`
	DueAt       *time.Time `json:"due_at"`
	CompletedAt *time.Time `json:"completed_at"`
	SkippedAt   *time.Time `json:"skipped_at"`
	// GoalID 可空 ⇒ null，而不是 ""。"" 会让前端渲染一个空的分组标题，
	// 而「没挂在目标下」与「挂在一颗标题为空的目标下」是两件事。
	GoalID    *string    `json:"goal_id"`
	CreatedAt *time.Time `json:"created_at"`
	UpdatedAt *time.Time `json:"updated_at"`
}

// ── 目标面 ──────────────────────────────────────────────────────────────────

func (s *Server) handleV3GoalCreate(w http.ResponseWriter, r *http.Request) {
	userID, r, apiErr := s.requireInternalService(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	var body v3GoalCreateRequest
	if apiErr := decodeV3Body(r, &body); apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	p := domain.GoalProposal{UserID: userID, Title: body.Title,
		Description: body.Description}
	if body.TargetAt != nil {
		p.TargetAt, p.HasTargetAt = body.TargetAt.UTC(), true
	}
	receipt, err := s.v3.GoalWrites.CreateGoal(r.Context(), p)
	if err != nil {
		writeJSONError(w, r, v3GoalError(err))
		return
	}
	rc := fromContext(r.Context())
	WriteSuccess(w, v3GoalWritePayload{GoalID: receipt.ID, Status: receipt.Status,
		Changed: receipt.Changed, EventID: receipt.EventID,
		ModelVersion: receipt.ModelVersion, Detail: receipt.Detail},
		rc.traceID, rc.userID)
}

func (s *Server) handleV3GoalsList(w http.ResponseWriter, r *http.Request) {
	userID, r, apiErr := s.requireInternalService(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	limit, apiErr := parseV3Limit(r.URL.Query().Get("limit"))
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	view, err := s.v3.Goals.ListGoals(r.Context(), userID,
		r.URL.Query().Get("status"), limit)
	if err != nil {
		writeJSONError(w, r, asAPIError(err))
		return
	}
	items := make([]v3GoalItemWire, 0, len(view.Items))
	for _, g := range view.Items {
		items = append(items, v3GoalItemWire{
			ID: g.ID, Title: g.Title, Description: g.Description, Status: g.Status,
			StartedAt:   optionalTime(g.StartedAt, !g.StartedAt.IsZero()),
			TargetAt:    optionalTime(g.TargetAt, !g.TargetAt.IsZero()),
			CompletedAt: optionalTime(g.CompletedAt, !g.CompletedAt.IsZero()),
			CreatedAt:   optionalTime(g.CreatedAt, !g.CreatedAt.IsZero()),
			UpdatedAt:   optionalTime(g.UpdatedAt, !g.UpdatedAt.IsZero()),
		})
	}
	rc := fromContext(r.Context())
	WriteSuccess(w, v3GoalListPayload{Items: items, Truncated: view.Truncated},
		rc.traceID, rc.userID)
}

func (s *Server) handleV3GoalTransition(w http.ResponseWriter, r *http.Request) {
	userID, r, apiErr := s.requireInternalService(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	var body v3TransitionRequest
	if apiErr := decodeV3Body(r, &body); apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	verb, apiErr := parseV3Verb(body.Action)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	receipt, err := s.v3.GoalWrites.TransitionGoal(r.Context(), userID,
		r.PathValue("id"), verb)
	if err != nil {
		writeJSONError(w, r, v3GoalError(err))
		return
	}
	rc := fromContext(r.Context())
	WriteSuccess(w, v3GoalWritePayload{GoalID: receipt.ID, Status: receipt.Status,
		Changed: receipt.Changed, EventID: receipt.EventID,
		ModelVersion: receipt.ModelVersion, Detail: receipt.Detail},
		rc.traceID, rc.userID)
}

// ── 行动面 ──────────────────────────────────────────────────────────────────

func (s *Server) handleV3ActionCreate(w http.ResponseWriter, r *http.Request) {
	userID, r, apiErr := s.requireInternalService(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	var body v3ActionCreateRequest
	if apiErr := decodeV3Body(r, &body); apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	p := domain.ActionProposal{UserID: userID, GoalID: r.PathValue("id"),
		Title: body.Title, Description: body.Description}
	if body.DueAt != nil {
		p.DueAt, p.HasDueAt = body.DueAt.UTC(), true
	}
	receipt, err := s.v3.GoalWrites.CreateAction(r.Context(), p)
	if err != nil {
		writeJSONError(w, r, v3GoalError(err))
		return
	}
	rc := fromContext(r.Context())
	WriteSuccess(w, actionWritePayloadOf(receipt), rc.traceID, rc.userID)
}

func (s *Server) handleV3ActionsList(w http.ResponseWriter, r *http.Request) {
	userID, r, apiErr := s.requireInternalService(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	limit, apiErr := parseV3Limit(r.URL.Query().Get("limit"))
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	view, err := s.v3.Goals.ListActions(r.Context(), userID,
		r.URL.Query().Get("status"), limit)
	if err != nil {
		writeJSONError(w, r, asAPIError(err))
		return
	}
	items := make([]v3ActionItemWire, 0, len(view.Items))
	for _, a := range view.Items {
		items = append(items, v3ActionItemWire{
			ID: a.ID, Title: a.Title, Description: a.Description, Status: a.Status,
			DueAt:       optionalTime(a.DueAt, !a.DueAt.IsZero()),
			CompletedAt: optionalTime(a.CompletedAt, !a.CompletedAt.IsZero()),
			SkippedAt:   optionalTime(a.SkippedAt, !a.SkippedAt.IsZero()),
			GoalID:      optionalString(a.GoalID),
			CreatedAt:   optionalTime(a.CreatedAt, !a.CreatedAt.IsZero()),
			UpdatedAt:   optionalTime(a.UpdatedAt, !a.UpdatedAt.IsZero()),
		})
	}
	rc := fromContext(r.Context())
	WriteSuccess(w, v3ActionListPayload{Items: items, Truncated: view.Truncated},
		rc.traceID, rc.userID)
}

func (s *Server) handleV3ActionTransition(w http.ResponseWriter, r *http.Request) {
	userID, r, apiErr := s.requireInternalService(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	var body v3TransitionRequest
	if apiErr := decodeV3Body(r, &body); apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	verb, apiErr := parseV3Verb(body.Action)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	receipt, err := s.v3.GoalWrites.TransitionAction(r.Context(), userID,
		r.PathValue("id"), verb)
	if err != nil {
		writeJSONError(w, r, v3GoalError(err))
		return
	}
	rc := fromContext(r.Context())
	WriteSuccess(w, actionWritePayloadOf(receipt), rc.traceID, rc.userID)
}

// ── 共用的三格小工具 ────────────────────────────────────────────────────────

// decodeV3Body 读体并拒绝多余键。语义与 v3_feedback.go 里那段逐字相同；
// 六条端点共用一个构造点，这样「有人给某条端点改成宽容解码」只可能在测里被发现一次。
func decodeV3Body(r *http.Request, dst any) *apierr.Error {
	raw, err := readBody(r)
	if err != nil {
		return JSONInvalid().AsError()
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(dst); err != nil {
		return apierr.New(apierr.CodeInvalidParams,
			"请求体不合形 / Malformed or unknown request field")
	}
	return nil
}

// parseV3Verb 只判「这是不是三枚动词之一」；「这一面接不接受这枚动词」是领域判据
// （goal 上的 skip 由 domain 报 ErrGoalIllegalTransition ⇒ 409，而不是这里 400）。
func parseV3Verb(raw string) (domain.LifecycleVerb, *apierr.Error) {
	verb := domain.LifecycleVerb(strings.TrimSpace(raw))
	if !verb.Valid() {
		return "", apierr.New(apierr.CodeInvalidParams,
			"未知的动作，只接受 complete / skip / cancel / Unknown action")
	}
	return verb, nil
}

func actionWritePayloadOf(rec domain.ActionReceipt) v3ActionWritePayload {
	return v3ActionWritePayload{ActionID: rec.ID, GoalID: optionalString(rec.GoalID),
		Status: rec.Status, Changed: rec.Changed, EventID: rec.EventID,
		ModelVersion: rec.ModelVersion, Detail: rec.Detail}
}

// v3GoalError 把领域错误折成信封码。
//
// 归属与「不存在」回**同一个** 40402（v3_feedback.go 的同一判据）：分成 403 与 404
// 等于告诉旁人这颗 id 存在与否。
// 转移非法与「目标已不在 active」都回 409：请求合形，是这一颗的当前状态不允许。
func v3GoalError(err error) *apierr.Error {
	switch {
	case errors.Is(err, domain.ErrInvalidGoalProposal),
		errors.Is(err, domain.ErrInvalidActionProposal):
		// 固定的两句文案而不是把领域错误原文透出去：那句话里带 Go 的英文句子，
		// 而这一面的用户文案由前端 i18n 负责（T3-04/05）。
		return apierr.New(apierr.CodeInvalidParams,
			"标题不合形，需要 1 到 200 字 / Invalid title")
	case errors.Is(err, domain.ErrGoalIllegalTransition),
		errors.Is(err, domain.ErrActionIllegalTransition),
		errors.Is(err, domain.ErrGoalNotActive):
		return apierr.New(apierr.CodeConflict,
			"当前的状态不允许这个动作 / Transition not allowed from current state")
	case errors.Is(err, domain.ErrInvalidOwnership):
		return apierr.New(apierr.CodeResourceNotFound, "资源不存在 / Resource not found")
	}
	return asAPIError(err)
}
