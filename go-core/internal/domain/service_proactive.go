package domain

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"
)

// N5 的领域写入口：proactive 候选的接纳评估（生成时刻）与发送时刻二次校验。
//
// 这两条是 canonical `proactives` 表的**唯一**写路径（不变量 7）。表与 7 条 sqlc
// 语句在 N2 就有了，但此前没有任何非测试代码引用它们——本批补的正是这个缺口。
// 触发面（谁在什么时候调）归 N8 端点切流与 O2 worker，与 N4 同口径不半做接线；
// 本批也不新建调度（七-4 P-1）。

// ErrProactiveNotFound 是该用户名下查不到这条 proactive。
var ErrProactiveNotFound = errors.New("domain: proactive not found")

// ErrProactiveTerminal 是行已进入终态，二次校验不得回头改。
var ErrProactiveTerminal = errors.New("domain: proactive already terminal")

// payloadCap 是写进 personal_model_events.payload 的留痕上限（TEXT 列没有 CHECK，
// 但超过 4KB 的判定过程对 O 侧复盘没有增量）。
const payloadCap = 4000

// ProactiveResult 是一次评估/校验的结果。
type ProactiveResult struct {
	ID         string
	Status     string
	Action     ProactiveAction
	Reason     RejectReason
	Detail     string
	Score      float64
	Layers     []Layer
	Cadence    Cadence
	SilentDays int
	SentAt     time.Time
	HasSentAt  bool
}

// ProactiveState 是重跑评估需要的行态（store 只取列，Packet 由本包解码）。
type ProactiveState struct {
	ID              string
	UserID          string
	PersonalModelID string
	Status          string
	TriggerType     string
	Message         string
	ScheduledAt     time.Time
	HasScheduledAt  bool
	ExpiresAt       time.Time
	HasExpiresAt    bool
	Eligibility     float64
	HasEligibility  bool
	Detail          string
	BlockReason     string
	RejectReason    string
	JourneyEventID  string
	GenerationRunID string
	ModelVersion    int64
	Packet          EligibilityPacket
}

// EligibilityPacket 是 eligibility_detail 里那份**可复现**的判定输入。
//
// P-10 要求发送时刻重读库态，所以这里存的不是结论而是引用：证据 id、权威行 id、
// 锚点行 id、提案置信。重评时 id 是引子，状态一律回库里取。
type EligibilityPacket struct {
	Checks      []string `json:"checks"`
	Layers      []string `json:"layers"`
	MinLayers   int      `json:"min_layers"`
	Independent int      `json:"counted_independent"`
	SentToday   int      `json:"sent_today"`
	Cadence     string   `json:"cadence"`
	SilentDays  int      `json:"silent_days"`
	Trigger     string   `json:"trigger"`
	Source      string   `json:"trigger_source"`
	Confidence  float64  `json:"confidence"`
	Sensitive   bool     `json:"sensitive"`
	EvidenceIDs []string `json:"evidence_ids,omitempty"`
	RefIDs      []string `json:"ref_ids,omitempty"`
	AnchorRefID string   `json:"anchor_ref_id,omitempty"`
	AnchorAt    string   `json:"anchor_at,omitempty"`
	Timezone    string   `json:"timezone"`
	Fail        string   `json:"fail_detail,omitempty"`
	Reason      string   `json:"reason,omitempty"`
}

// AdmitProactive 在**一个事务**里评估一条主动触达提案并落库。
//
// 留痕口径沿用 G-2：不通过的候选**也写行**（blocked/cancelled + 码 + detail），
// 否则 04 §22 的 Precision/Annoyance 没有分母，O 侧也无法回溯哪一类被挡了。
// 版本口径：02:1069「increment model version **if model state changes**」——
// 一条触达不是「模型相信什么」，是「模型打算做什么」，所以这里**不 ++**，只写事件。
func (s *Service) AdmitProactive(ctx context.Context, c ProactiveCandidate) (ProactiveResult, error) {
	result := ProactiveResult{}
	err := s.db.InTx(ctx, func(ctx context.Context, st Store) error {
		modelID, version, err := st.EnsurePersonalModel(ctx, c.UserID)
		if err != nil {
			return err
		}
		if c.PersonalModelID != "" && c.PersonalModelID != modelID {
			return fmt.Errorf("domain: candidate.personal_model_id=%s 与该用户实际模型 %s 不符",
				c.PersonalModelID, modelID)
		}
		c.PersonalModelID = modelID

		// 重试幂等：同 id 已落库就原样返回，不重复评估也不插第二行。
		if c.ID != "" {
			existing, err := st.GetProactiveState(ctx, c.UserID, c.ID)
			if err == nil {
				reason := RejectReason(existing.RejectReason)
				if reason == "" {
					reason = RejectReason(existing.BlockReason)
				}
				result = ProactiveResult{ID: existing.ID, Status: existing.Status,
					Reason: reason, Score: existing.Eligibility,
					Detail: "幂等命中：已存在的 proactive 行"}
				return nil
			}
			if !errors.Is(err, ErrProactiveNotFound) {
				return err
			}
		}

		if c.ID == "" {
			c.ID = s.newID()
		}
		at := sendMoment(c, s.now())
		gate := ProactiveGate{Policy: s.gate.Policy}
		in, err := s.loadProactiveInputs(ctx, st, c.UserID, at, gate)
		if err != nil {
			return err
		}
		if err := s.loadRefsAndEvidence(ctx, st, &c, in); err != nil {
			return err
		}
		resolveAnchor(&c, in)
		outcome := gate.Check(ctx, &c, *in)

		packet := newPacket(s.gate.Policy, c, *in, outcome)
		encoded, err := json.Marshal(packet)
		if err != nil {
			return fmt.Errorf("domain: encode eligibility packet: %w", err)
		}
		row := ProactiveRow{
			ID: c.ID, UserID: c.UserID, PersonalModelID: c.PersonalModelID,
			Status: outcome.Status, TriggerType: string(c.TriggerType),
			Message:     c.Message,
			ScheduledAt: c.ScheduledAt, HasScheduledAt: c.HasScheduledAt,
			ExpiresAt: at.Add(time.Duration(s.gate.Policy.Proactive.DefaultValidityHours) *
				time.Hour),
			HasExpiresAt:      true,
			EligibilityScore:  outcome.Score,
			EligibilityDetail: string(encoded),
			GenerationModel:   c.GenerationModel,
			GenerationVersion: c.GenerationVer,
			GenerationRunID:   c.GenerationRunID,
			ModelVersion:      version,
			JourneyEventID:    c.JourneyEventID,
			HasJourneyEventID: c.JourneyEventID != "",
		}
		if c.HasExpiresAt {
			row.ExpiresAt, row.HasExpiresAt = c.ExpiresAt, true
		}
		if outcome.Reason != "" {
			if outcome.IsReject {
				row.RejectReason = outcome.Reason.String()
			} else {
				row.BlockReason = outcome.Reason.String()
			}
		}
		if err := st.InsertProactive(ctx, row); err != nil {
			return err
		}
		if err := st.LinkProactiveRefs(ctx, row.ID, c.UserID, in.Refs); err != nil {
			return err
		}
		if err := s.emit(ctx, st, s.proactiveEvent(c.UserID, c.PersonalModelID, row.ID,
			proactiveEventType(outcome), string(encoded), version)); err != nil {
			return err
		}
		result = ProactiveResult{ID: row.ID, Status: outcome.Status, Action: outcome.Action,
			Reason: outcome.Reason, Detail: outcome.Detail, Score: outcome.Score,
			Layers: outcome.HitLayers, Cadence: outcome.Cadence, SilentDays: outcome.SilentDays}
		return nil
	})
	if err != nil {
		return ProactiveResult{}, err
	}
	return result, nil
}

// RecheckProactive 是 04:104「At send time, eligibility is evaluated again」的实现：
// 到点重跑十一关（库态现读、safety 现判），全绿才置 sent。
//
// fail-closed 三处：safety 输入缺失、用户偏好行缺失、生成时刻的资格留痕读不出来。
// 终态行（sent/responded/expired/cancelled）一律不动——重复投递比漏发一次更贵
// （08 §7 MarkProactiveDelivered 的条件更新是同一立场的库侧兜底）。
func (s *Service) RecheckProactive(ctx context.Context, userID, proactiveID string,
	safety *Safety) (ProactiveResult, error) {
	result := ProactiveResult{}
	err := s.db.InTx(ctx, func(ctx context.Context, st Store) error {
		state, err := st.GetProactiveState(ctx, userID, proactiveID)
		if err != nil {
			return err
		}
		if isTerminalProactive(state.Status) {
			return fmt.Errorf("%w: status=%s", ErrProactiveTerminal, state.Status)
		}
		now := s.now()
		if state.HasExpiresAt && !now.Before(state.ExpiresAt) {
			if err := st.ExpireProactive(ctx, userID, proactiveID); err != nil {
				return err
			}
			if err := s.emit(ctx, st, s.proactiveEvent(userID, state.PersonalModelID,
				proactiveID, "proactive_expired",
				fmt.Sprintf("超过有效期 %s，沉默不得事后补发（04 §19）",
					state.ExpiresAt.UTC().Format(time.RFC3339)), state.ModelVersion)); err != nil {
				return err
			}
			result = ProactiveResult{ID: proactiveID, Status: string(ProactiveStatusExpired),
				Action: ActionProactiveCancel}
			return nil
		}
		if err := decodePacket(&state); err != nil {
			// 读不出生成时刻的引用清单就无从重跑判定，而 04 §5 的第一条硬门是
			// 「每条 proactive 必须有显式触发」——判不了就是不发。
			outcome := ProactiveOutcome{Action: ActionProactiveBlock,
				Status: string(ProactiveStatusBlocked), Reason: ReasonEvidenceNotFound,
				Detail: fmt.Sprintf("eligibility_detail 解析失败（%v），无法重跑十一关，"+
					"按 fail-closed 拦下", err)}
			fallback, encErr := json.Marshal(EligibilityPacket{
				Trigger: state.TriggerType, Reason: outcome.Reason.String(),
				Fail: outcome.Detail, Cadence: string(CadenceNormal)})
			if encErr != nil {
				return encErr
			}
			res, err := s.writeVerdict(ctx, st, state, outcome, string(fallback))
			if err != nil {
				return err
			}
			result = res
			return nil
		}

		c := candidateFromState(state, safety)
		at := sendMoment(c, now)
		gate := ProactiveGate{Policy: s.gate.Policy}
		in, err := s.loadProactiveInputs(ctx, st, userID, at, gate)
		if err != nil {
			return err
		}
		if err := s.loadRefsAndEvidence(ctx, st, &c, in); err != nil {
			return err
		}
		resolveAnchor(&c, in)
		outcome := gate.Check(ctx, &c, *in)
		packet := newPacket(s.gate.Policy, c, *in, outcome)
		encoded, err := json.Marshal(packet)
		if err != nil {
			return fmt.Errorf("domain: encode eligibility packet: %w", err)
		}
		if outcome.Action != ActionProactiveEligible {
			res, err := s.writeVerdict(ctx, st, state, outcome, string(encoded))
			if err != nil {
				return err
			}
			result = res
			return nil
		}
		sentAt, err := st.MarkProactiveSent(ctx, userID, proactiveID, string(encoded))
		if err != nil {
			return err
		}
		if err := s.emit(ctx, st, s.proactiveEvent(userID, state.PersonalModelID,
			proactiveID, "proactive_sent", string(encoded), state.ModelVersion)); err != nil {
			return err
		}
		result = ProactiveResult{ID: proactiveID, Status: string(ProactiveStatusSent),
			Action: ActionProactiveEligible, Score: outcome.Score, Layers: outcome.HitLayers,
			Cadence: outcome.Cadence, SilentDays: outcome.SilentDays,
			SentAt: sentAt, HasSentAt: true}
		return nil
	})
	if err != nil {
		return ProactiveResult{}, err
	}
	return result, nil
}

// writeVerdict 把一次重评的失败结论改写进行（非终态守卫由 SQL 侧条件更新兜住）。
//
// eligibility_detail 永远是 packet JSON（N9 读路径按一个形状拆，不必分支）；
// packet 里的 fail/reason 就是这一关的结论，Checks 只装通过的关。
func (s *Service) writeVerdict(ctx context.Context, st Store, state ProactiveState,
	outcome ProactiveOutcome, detail string) (ProactiveResult, error) {
	row := ProactiveStatusRow{UserID: state.UserID, ID: state.ID,
		Status: outcome.Status, Detail: detail}
	if outcome.Reason != "" {
		if outcome.IsReject {
			row.RejectReason = outcome.Reason.String()
		} else {
			row.BlockReason = outcome.Reason.String()
		}
	}
	if err := st.SetProactiveStatus(ctx, row); err != nil {
		return ProactiveResult{}, err
	}
	if err := s.emit(ctx, st, s.proactiveEvent(state.UserID, state.PersonalModelID,
		state.ID, proactiveEventType(outcome), outcome.Detail, state.ModelVersion)); err != nil {
		return ProactiveResult{}, err
	}
	return ProactiveResult{ID: state.ID, Status: outcome.Status, Action: outcome.Action,
		Reason: outcome.Reason, Detail: outcome.Detail, Cadence: outcome.Cadence,
		SilentDays: outcome.SilentDays}, nil
}

// loadProactiveInputs 在调用方给的事务里把资格门需要的库态取齐。
// 全部读同一个快照：判定与写入之间换快照，频次关可能少算一条刚发出去的触达。
func (s *Service) loadProactiveInputs(ctx context.Context, st Store, userID string,
	at time.Time, gate ProactiveGate) (*ProactiveInputs, error) {
	p := gate.Policy.Proactive
	prefs, err := st.GetProactiveUserPrefs(ctx, userID)
	if err != nil {
		return nil, err
	}
	zone := prefs.Timezone
	if zone == "" {
		zone = "UTC"
	}
	history, err := st.ListProactiveHistory(ctx, userID, at.AddDate(0, 0, -p.HistoryDays))
	if err != nil {
		return nil, err
	}
	lastActivity, found, err := st.LastUserActivityAt(ctx, userID)
	if err != nil {
		return nil, err
	}
	if !found {
		lastActivity = time.Time{}
	}
	corrected, err := st.RecentCorrections(ctx, userID, p.StaleCheckDays)
	if err != nil {
		return nil, err
	}
	return &ProactiveInputs{
		Prefs: prefs, History: history, Corrected: corrected,
		SilentDays: SilenceDays(at, lastActivity, prefs.CreatedAt, zone),
		Now:        at, Timezone: zone,
	}, nil
}

// loadRefsAndEvidence 取回候选引用的权威行与证据。evidence/layers/freshness 三关的
// 输入都在这里现读，提案自报的状态一律不采信（不变量 7 的读侧形式）。
func (s *Service) loadRefsAndEvidence(ctx context.Context, st Store,
	c *ProactiveCandidate, in *ProactiveInputs) error {
	ids := append([]string{}, c.RefIDs...)
	if c.AnchorRefID != "" {
		ids = appendUniqueID(ids, c.AnchorRefID)
	}
	refs, err := st.ListRefStates(ctx, c.UserID, ids)
	if err != nil {
		return err
	}
	evidence, err := st.LoadEvidence(ctx, c.EvidenceIDs)
	if err != nil {
		return err
	}
	in.Refs, in.Evidence = refs, evidence
	return nil
}

// resolveAnchor 把「关联当下」那一层的锚点钉回权威行：提案只能指出锚点在哪一行，
// 时刻由那一行说了算（自报时刻等于让非权威侧决定「今天是什么日子」）。
func resolveAnchor(c *ProactiveCandidate, in *ProactiveInputs) {
	in.AnchorRefID, in.AnchorAt = "", time.Time{}
	in.HasAnchor = false
	if c.AnchorRefID != "" {
		for _, ref := range in.Refs {
			if ref.ID == c.AnchorRefID && ref.HasTime {
				in.AnchorRefID, in.AnchorAt = ref.ID, ref.AnchorAt
				in.HasAnchor = true
				return
			}
		}
		return
	}
	// 没有指定锚点行时，milestone 允许以注册时刻为锚
	// （PRD 冷启动③「我们认识第 1 天」的口径，见七-4 P-3）。
	if c.TriggerType == TriggerMilestone && !in.Prefs.CreatedAt.IsZero() {
		in.AnchorRefID, in.AnchorAt = "users.created_at", in.Prefs.CreatedAt
		in.HasAnchor = true
	}
}

// newPacket 把一次判定折成可复现的 JSON（Checks/Layers/引用/置信/锚点）。
func newPacket(policy Policy, c ProactiveCandidate, in ProactiveInputs,
	outcome ProactiveOutcome) EligibilityPacket {
	p := policy.Proactive
	packet := EligibilityPacket{
		Checks: outcome.Checks, Layers: layerStrings(outcome.HitLayers),
		MinLayers: p.MinLayersFor(c.IsSensitive()),
		SentToday: p.sentOnLocalDay(in.History, in.Now, in.Timezone),
		Cadence:   string(p.CadenceOf(in.SilentDays)), SilentDays: in.SilentDays,
		Trigger: string(c.TriggerType), Source: string(c.TriggerSource),
		Confidence: c.Confidence, Sensitive: c.IsSensitive(),
		EvidenceIDs: append([]string(nil), c.EvidenceIDs...),
		RefIDs:      append([]string(nil), c.RefIDs...),
		AnchorRefID: in.AnchorRefID, Timezone: in.Timezone,
		Fail: outcome.Detail, Reason: outcome.Reason.String(),
	}
	if in.HasAnchor {
		packet.AnchorAt = in.AnchorAt.UTC().Format(time.RFC3339)
	}
	if c.TriggerType == TriggerPatternFollowup {
		independent, _, _ := policy.Evidence.Combined(scoredRows(in.Evidence, c.EvidenceIDs))
		packet.Independent = independent
	}
	return packet
}

func (s *Service) proactiveEvent(userID, modelID, aggregateID, eventType,
	payload string, version int64) EventRow {
	return EventRow{
		ID: s.newID(), UserID: userID, PersonalModelID: modelID,
		EventType: eventType, AggregateType: "proactive", AggregateID: aggregateID,
		ModelVersion: version, Payload: truncatePayload(payload),
	}
}

func truncatePayload(payload string) string {
	if len(payload) <= payloadCap {
		return payload
	}
	return payload[:payloadCap]
}

func proactiveEventType(outcome ProactiveOutcome) string {
	switch outcome.Action {
	case ActionProactiveEligible:
		return "proactive_eligible"
	case ActionProactiveCancel:
		return "proactive_cancelled"
	default:
		return "proactive_blocked"
	}
}

func isTerminalProactive(status string) bool {
	switch status {
	case string(ProactiveStatusSent), string(ProactiveStatusResponded),
		string(ProactiveStatusExpired), string(ProactiveStatusCancelled):
		return true
	default:
		return false
	}
}

// candidateFromState 用行 + packet 重建一次重评所需的候选。置信与引用取自 packet
// （那是生成侧的事实），safety 由调用方现判传入——上次判定的结论一律不沿用。
func candidateFromState(state ProactiveState, safety *Safety) ProactiveCandidate {
	return ProactiveCandidate{
		UserID:          state.UserID,
		PersonalModelID: state.PersonalModelID,
		ID:              state.ID,
		TriggerType:     TriggerType(state.TriggerType),
		TriggerSource:   TriggerSource(state.Packet.Source),
		Message:         state.Message,
		Confidence:      state.Packet.Confidence,
		EvidenceIDs:     append([]string(nil), state.Packet.EvidenceIDs...),
		RefIDs:          append([]string(nil), state.Packet.RefIDs...),
		AnchorRefID:     state.Packet.AnchorRefID,
		ScheduledAt:     state.ScheduledAt,
		HasScheduledAt:  state.HasScheduledAt,
		ExpiresAt:       state.ExpiresAt,
		HasExpiresAt:    state.HasExpiresAt,
		JourneyEventID:  state.JourneyEventID,
		GenerationRunID: state.GenerationRunID,
		Safety:          safety,
	}
}

// sendMoment 给出「按哪个时刻判这次触达」：排期时刻优先，没排期就按现在。
func sendMoment(c ProactiveCandidate, now time.Time) time.Time {
	if c.HasScheduledAt {
		return c.ScheduledAt
	}
	return now
}

func decodePacket(state *ProactiveState) error {
	if state.Detail == "" {
		return errors.New("eligibility_detail 为空")
	}
	return json.Unmarshal([]byte(state.Detail), &state.Packet)
}

func layerStrings(layers []Layer) []string {
	out := make([]string, 0, len(layers))
	for _, layer := range layers {
		out = append(out, string(layer))
	}
	return out
}

func appendUniqueID(ids []string, want string) []string {
	for _, id := range ids {
		if id == want {
			return ids
		}
	}
	return append(ids, want)
}
