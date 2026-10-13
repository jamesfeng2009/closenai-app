package domain

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"
	"unicode/utf8"
)

// N4 的两条新入口：证据提案的确定性算分（03 §4-§7），与已发布 insight 的重算
// 撤回/过期（tasks N4「同一 claim 证据变弱 → 旧 insight retract/expire」）。
//
// 两条都挂在同一个不变量上：**只有本包能改 canonical 态**（01 §12 不变量 7），
// 所以算分与重算都跑在事务里，写数一律经 Store。

// ErrInsightNotFound 是重算入口查无此条：不静默返回空结果。
var ErrInsightNotFound = errors.New("domain: insight not found")

// EvidenceCandidate 是 Python 提案的一条证据。字段面＝V3 03 §4:82-90 的
// EvidenceCandidate（source_record_id / observation / evidence_type / role /
// strength / rationale），但 **strength 不在这里**：03:97-108 规定终局分数由
// domain code 算，提案只能给算分输入。
type EvidenceCandidate struct {
	UserID string
	// SourceKind 落 source_records.source_type，是 03 §5 八类可靠度的键。
	SourceKind SourceKind
	// SourceTable/SourceID 指向既有业务行（D-2：source_records 只做指针，不复制内容）。
	SourceTable string
	SourceID    string
	OccurredAt  time.Time

	EvidenceType string // 02 §3 六值：direct|repeated|temporal|behavioral|user_confirmed|user_corrected
	Role         string // supporting|contradicting；空＝supporting（N2 DDL 默认值）
	Observation  string
	Excerpt      string
	// Metadata 是调用方的自由文本 JSON，原样存进 evidence.metadata_json。
	Metadata json.RawMessage

	Explicitness Explicitness
	Specificity  Specificity

	GenerationRunID string

	// ProposedIndependentIDs 是提案方认为「同一观测的独立重复」的既有证据 id。
	// **一个都不采信**：逐条核验归属、时刻先后、不同源、不同日，只数过闸的条数
	// （03 §5 的 Repetition 因子 + §6:161-163 的同源/同日不得计独立重复）。
	ProposedIndependentIDs []string
}

// EvidenceResult 是算分结论，Detail 带五因子的来源，N9 读路径的「为何相信」用它。
type EvidenceResult struct {
	ID             string
	SourceRecordID string
	Strength       float64
	Class          EvidenceClass
	Independent    int
	Detail         string
}

// SourceRecordRow 是 source 指针的写入行（D-2 薄版）。
type SourceRecordRow struct {
	ID            string
	UserID        string
	SourceType    string
	SourceTable   string
	SourceID      string
	OccurredAt    time.Time
	HasOccurredAt bool
}

// EvidenceWrite 是 evidence 行的写入面。字段名对齐 N2 DDL。
type EvidenceWrite struct {
	ID              string
	UserID          string
	SourceRecordID  string
	EvidenceType    string
	Role            string
	Strength        float64
	HasStrength     bool
	Observation     string
	Excerpt         string
	Metadata        string
	GenerationRunID string
}

// InsightState 是重算入口需要的 insight 既有态投影。
type InsightState struct {
	ID              string
	UserID          string
	PersonalModelID string
	Statement       string
	ClaimType       string
	Status          string
	TimeFrom        time.Time
	HasTimeFrom     bool
	TimeTo          time.Time
	HasTimeTo       bool
	ModelVersion    int64
}

// InsightStatusRow 是状态迁移的写入行；UpdateInsightStatus 的入参形状。
type InsightStatusRow struct {
	UserID       string
	ID           string
	Status       string
	RejectReason string
	GateDetail   string
	ModelVersion int64
}

// AdmitEvidence 是 evidence 表的唯一写入口：核验 → 算分 → 落库。
//
// 落库口径：
//   - source 指针按 (user_id, source_table, source_id) 复用既有行（08 §3 幂等），
//     重跑管道不产生第二条登记；
//   - strength 一定是 Go 算出来的（03:108「LLM confidence 是输入之一，绝不是终局分」）；
//   - metadata_json 同时留下调用方原文与算分过程，02 §37 的「Why do we know it」
//     要能指回五个因子各自取值。
func (s *Service) AdmitEvidence(ctx context.Context, c EvidenceCandidate) (EvidenceResult, error) {
	if err := s.checkEvidenceCandidate(c); err != nil {
		return EvidenceResult{}, err
	}
	result := EvidenceResult{}
	err := s.db.InTx(ctx, func(ctx context.Context, st Store) error {
		// 核验在前、写在后：越权引用必须**在任何写入之前**报错，否则会留下
		// 一条指向别人业务行的 source 登记（事务会回滚，但「先写后判」的次序
		// 本身违反不变量 9，也让单测里的半状态看不见）。
		cited, err := s.verifyIndependent(ctx, st, c)
		if err != nil {
			return err
		}
		// 登记 source 指针后才数独立观测：去重要按 source_record_id，
		// 而本条的 id 只有 upsert 之后才知道（同指针复用既有行，08 §3 幂等）。
		sourceID, err := st.UpsertSourceRecord(ctx, SourceRecordRow{
			ID: s.newID(), UserID: c.UserID, SourceType: string(c.SourceKind),
			SourceTable: c.SourceTable, SourceID: c.SourceID,
			OccurredAt: c.OccurredAt, HasOccurredAt: !c.OccurredAt.IsZero(),
		})
		if err != nil {
			return err
		}
		independent, dropped := s.countIndependent(c, cited, sourceID)
		scored, err := ScoreEvidence(EvidenceSignals{
			SourceKind: c.SourceKind, Explicitness: c.Explicitness,
			Specificity: c.Specificity, Independent: independent,
			ObservedAt: c.OccurredAt,
		}, s.gate.Policy.Evidence, s.now())
		if err != nil {
			return err
		}
		metadata, err := evidenceMetadata(c.Metadata, scored, independent, dropped)
		if err != nil {
			return err
		}
		id := s.newID()
		role := c.Role
		if role == "" {
			role = "supporting"
		}
		if err := st.InsertEvidence(ctx, EvidenceWrite{
			ID: id, UserID: c.UserID, SourceRecordID: sourceID,
			EvidenceType: c.EvidenceType, Role: role,
			Strength: scored.Strength, HasStrength: true,
			Observation: c.Observation, Excerpt: c.Excerpt, Metadata: metadata,
			GenerationRunID: c.GenerationRunID,
		}); err != nil {
			return err
		}
		result = EvidenceResult{ID: id, SourceRecordID: sourceID, Strength: scored.Strength,
			Class: scored.Class, Independent: independent,
			Detail: fmt.Sprintf("%s proposed_independent=%d counted_independent=%d",
				scored.Detail, len(c.ProposedIndependentIDs), independent)}
		return nil
	})
	if err != nil {
		return EvidenceResult{}, err
	}
	return result, nil
}

// checkEvidenceCandidate 把畸形提案挡在写库之前（01 §25 LLM Failure Containment）。
// 列宽取 N2 DDL：超长会被数据库拒绝，先在这里判成可解释的错误。
func (s *Service) checkEvidenceCandidate(c EvidenceCandidate) error {
	p := s.gate.Policy
	switch {
	case c.UserID == "":
		return errors.New("domain: evidence.user_id is required")
	case c.SourceTable == "":
		return errors.New("domain: evidence.source_table is required")
	case c.SourceID == "":
		return errors.New("domain: evidence.source_id is required")
	case c.GenerationRunID == "":
		// 不变量 8：没有留痕号的 AI 产出不可追溯，不接受入表。
		return errors.New("domain: evidence.generation_run_id is required")
	}
	if _, ok := p.Evidence.reliability(c.SourceKind); !ok {
		return fmt.Errorf("%w: source_kind=%q", ErrUnknownSignal, c.SourceKind)
	}
	if _, ok := p.Evidence.explicitnessFactor(c.Explicitness); !ok {
		return fmt.Errorf("%w: explicitness=%q", ErrUnknownSignal, c.Explicitness)
	}
	if _, ok := p.Evidence.specificityFactor(c.Specificity); !ok {
		return fmt.Errorf("%w: specificity=%q", ErrUnknownSignal, c.Specificity)
	}
	if !contains(allowedOrigins, c.EvidenceType) {
		return fmt.Errorf("domain: evidence_type=%q 不在 02 §3 的六值集合", c.EvidenceType)
	}
	if c.Role != "" && c.Role != "supporting" && c.Role != "contradicting" {
		return fmt.Errorf("domain: role=%q 只允许 supporting|contradicting（03 §7:186-187）", c.Role)
	}
	if len(c.Metadata) > 0 {
		var probe json.RawMessage
		if err := json.Unmarshal(c.Metadata, &probe); err != nil {
			return fmt.Errorf("domain: evidence.metadata 不是合法 JSON: %w", err)
		}
	}
	for _, item := range []struct {
		name, value string
		max         int
	}{
		{"source_table", c.SourceTable, 64}, {"source_id", c.SourceID, 36},
		{"evidence_type", c.EvidenceType, 32}, {"role", c.Role, 24},
		{"generation_run_id", c.GenerationRunID, 36},
	} {
		if utf8.RuneCountInString(item.value) > item.max {
			return fmt.Errorf("domain: %s 长 %d，超列宽 %d",
				item.name, utf8.RuneCountInString(item.value), item.max)
		}
	}
	if !c.OccurredAt.IsZero() && c.OccurredAt.After(s.now()) {
		return fmt.Errorf("domain: evidence.occurred_at=%s 晚于当下", c.OccurredAt.Format(time.RFC3339))
	}
	return nil
}

// verifyIndependent 逐条核验「提案说这些是同一观测的独立重复」，只读、不写。
//
// 查不到或不属于本用户一律报错（不变量 9「拒绝跨用户 id 引用」＋幻觉引用不可入账）；
// 其余不合格（时刻、同源、同日）只是不计入，不拒整条提案——虚高的重复计数只会让
// 分数偏高，而悄悄拒掉一条合法观察会把证据丢掉，两种错误的代价不对称。
func (s *Service) verifyIndependent(ctx context.Context, st Store,
	c EvidenceCandidate) ([]Evidence, error) {
	if len(c.ProposedIndependentIDs) == 0 {
		return nil, nil
	}
	rows, err := st.LoadEvidence(ctx, c.ProposedIndependentIDs)
	if err != nil {
		return nil, fmt.Errorf("domain: load proposed independent evidence: %w", err)
	}
	found := make(map[string]Evidence, len(rows))
	for _, row := range rows {
		found[row.ID] = row
	}
	cited := make([]Evidence, 0, len(c.ProposedIndependentIDs))
	for _, id := range c.ProposedIndependentIDs {
		row, ok := found[id]
		if !ok || row.UserID != c.UserID {
			return nil, fmt.Errorf("domain: 提案引用的独立证据 %s 不存在或不属于该用户", id)
		}
		cited = append(cited, row)
	}
	return cited, nil
}

// countIndependent 数出「本条 + 已核实的更早独立观测」：时刻要更早，
// 同一 source_record 或同一窗口内的多条只算一次（03 §6:161-163）。
func (s *Service) countIndependent(c EvidenceCandidate,
	cited []Evidence, sourceID string) (int, int) {
	window := s.gate.Policy.Evidence.RepetitionWindowDays
	seenSource := map[string]bool{sourceID: true}
	seenDay := map[string]bool{}
	if !c.OccurredAt.IsZero() {
		seenDay[dayKey(c.OccurredAt, window)] = true
	}
	verified, dropped := 0, 0
	for _, row := range cited {
		if !c.OccurredAt.IsZero() && !row.ObservedAt.Before(c.OccurredAt) {
			dropped++
			continue
		}
		key := dayKey(row.ObservedAt, window)
		if seenSource[row.SourceID] || seenDay[key] {
			dropped++
			continue
		}
		seenSource[row.SourceID] = true
		seenDay[key] = true
		verified++
	}
	return verified + 1, dropped
}

// evidenceMetadata 把「调用方原文 + 算分过程」合成 metadata_json。
// 算分过程必须与证据同行：02 §37 问「Why do we know it」时，只给一个 0.72
// 不给回答，而重新算一遍需要当时的权重，权重是会变的（G-5 标定后会换 env）。
func evidenceMetadata(raw json.RawMessage, scored Scored, independent, dropped int) (string, error) {
	type envelope struct {
		Caller      json.RawMessage    `json:"caller,omitempty"`
		Strength    float64            `json:"strength"`
		Class       EvidenceClass      `json:"class"`
		Independent int                `json:"independent_observations"`
		Dropped     int                `json:"proposed_but_not_counted"`
		Components  map[string]float64 `json:"components"`
		Detail      string             `json:"detail"`
	}
	box := envelope{Strength: scored.Strength, Class: scored.Class,
		Independent: independent, Dropped: dropped,
		Components: scored.Components, Detail: scored.Detail}
	if len(raw) > 0 {
		// 合法性已在 checkEvidenceCandidate 里校验过。
		box.Caller = raw
	}
	out, err := json.Marshal(box)
	if err != nil {
		return "", fmt.Errorf("domain: encode evidence metadata: %w", err)
	}
	return string(out), nil
}

// ReevaluateInsight 重算一条用户可见 insight 的证据支撑，变弱就撤回或过期。
//
// 判定口径（03 §6:158-159、§7:186-187、§11:309-310）：
//   - 只有 validated|published|acknowledged 参与：**用户已表态的行系统不得撤回**
//     （confirmed/corrected/dismissed 属 §12 的用户动作结果，覆盖它等于否定用户）；
//   - 有 contradicting 证据不弱于最强支撑 ⇒ retracted（§7 禁止把冲突平均掉）；
//   - 支撑跌到 D 级 ⇒ retracted；派生 claim（非 observation）跌到 C 级 ⇒ retracted
//     （§6「A/B 才能支撑正常派生 claim」）；
//   - 以上都不成立、但观测窗口已过 InsightStaleAfterDays ⇒ expired（时间衰减，
//     不是证据不对）；
//   - 其余情况不动状态，也不动模型版本。
//
// 「+ 新 candidate 生成」不在这里做：Go 只发 insight_retracted / insight_expired
// 事件，重新提案是 Python 的职责（不变量 7 的方向：Go 判终局，不产提案）。
func (s *Service) ReevaluateInsight(ctx context.Context, userID, insightID string) (Result, error) {
	if userID == "" || insightID == "" {
		return Result{}, errors.New("domain: reevaluate 需要 user_id 与 insight_id")
	}
	result := Result{}
	err := s.db.InTx(ctx, func(ctx context.Context, st Store) error {
		inner, err := s.reevaluateWithin(ctx, st, userID, insightID)
		if err != nil {
			return err
		}
		result = inner
		return nil
	})
	if err != nil {
		return Result{}, err
	}
	return result, nil
}

// reevaluateWithin 是重算的事务体，抽出来的唯一理由：N6 的传播链要在**同一个事务**
// 里改完 pattern 再重算 insight（01 §22 不变量 9 的原子性——半途失败不能留下
// 「模式已削弱但洞察还挂着它」的可见状态），而本方法自带 InTx，直接调用会嵌套事务。
func (s *Service) reevaluateWithin(ctx context.Context, st Store,
	userID, insightID string) (Result, error) {
	result := Result{}
	state, err := st.GetInsightState(ctx, userID, insightID)
	if err != nil {
		return Result{}, err
	}
	result.ID, result.Status = state.ID, state.Status
	// 版本随结果返回：不动状态时调用方（O 侧轮询）也要能拿到当前版本。
	result.ModelVersion = state.ModelVersion
	if !reevaluableStatus(state.Status) {
		result.Detail = "该状态不由系统重算改写（用户已表态或已是终局）"
		return result, nil
	}
	rows, err := st.LoadInsightEvidence(ctx, userID, state.ID)
	if err != nil {
		return Result{}, err
	}
	independent, combined, conflict := s.gate.Policy.Evidence.Combined(rows)
	next, reason := s.reevaluateVerdict(state, independent, combined, conflict)
	if next == "" {
		result.Detail = fmt.Sprintf(
			"支撑未跌破门槛：independent=%d combined=%.4f", independent, combined)
		return result, nil
	}
	// N7-14 修（七-5.1 第 2 条登记、2026-10-13 落地）：乐观锁的期望值必须取**模型行的
	// 当前版本**，原来传的是 state.ModelVersion——那是 insight 行自己的快照版本，只要
	// 模型在该洞察之后又变过一次（或同一事务里 outcome 已 ++ 过），期望值必然落后 ⇒
	// ErrVersionConflict ⇒ 整笔回滚，重算永远做不成。真正的冲突是「从这次读之后模型
	// 又变了」，所以期望值在紧邻 BumpVersion 处取当前值。
	_, currentVersion, err := st.EnsurePersonalModel(ctx, userID)
	if err != nil {
		return Result{}, err
	}
	newVersion, err := st.BumpVersion(ctx, state.PersonalModelID, currentVersion)
	if err != nil {
		return Result{}, err
	}
	detail := fmt.Sprintf(`{"independent":%d,"combined":%.4f,"conflict":%t}`,
		independent, combined, conflict)
	if err := st.SetInsightStatus(ctx, InsightStatusRow{
		UserID: userID, ID: state.ID, Status: next,
		RejectReason: reason, GateDetail: detail, ModelVersion: newVersion,
	}); err != nil {
		return Result{}, err
	}
	eventType := "insight_retracted"
	if next == "expired" {
		eventType = "insight_expired"
	}
	if err := st.InsertVersion(ctx, VersionRow{
		ID: s.newID(), UserID: userID, PersonalModelID: state.PersonalModelID,
		Version: newVersion, Reason: eventType,
	}); err != nil {
		return Result{}, err
	}
	result.Status, result.ModelVersion = next, newVersion
	result.Detail = detail
	return result, s.emit(ctx, st, EventRow{
		ID: s.newID(), UserID: userID, PersonalModelID: state.PersonalModelID,
		EventType: eventType, AggregateType: "insights", AggregateID: state.ID,
		ModelVersion: newVersion,
		Payload: fmt.Sprintf(`{"status":%q,"reason":%q,"claim_type":%q,"combined":%.4f}`,
			next, reason, state.ClaimType, combined),
		GenerationRunID: "",
	})
}

func reevaluableStatus(status string) bool {
	switch status {
	case "validated", "published", "acknowledged":
		return true
	default:
		return false
	}
}

// reevaluateVerdict 返回下一个状态与机器可读原因码。空状态＝不动。
//
// expired 一律**不写** reject_reason：reject_reason 的语义是「Gate 拒了它」，
// 而过期是 §11 的生命周期退化，那条 insight 当初是合法发布的，
// 把生命周期事件塞进拒绝码会让 O 侧把过期算成误判。
func (s *Service) reevaluateVerdict(state InsightState, independent int,
	combined float64, conflict bool) (string, string) {
	ep := s.gate.Policy.Evidence
	if conflict {
		return "retracted", ReasonEvidenceConflict.String()
	}
	if combined < ep.ClassCFloor {
		return "retracted", ReasonEvidenceBelowFloor.String()
	}
	if state.ClaimType != "" && state.ClaimType != "observation" && combined < ep.ClassBFloor {
		return "retracted", ReasonEvidenceBelowFloor.String()
	}
	if state.HasTimeTo && !state.TimeTo.IsZero() &&
		s.now().Sub(state.TimeTo) > time.Duration(ep.InsightStaleAfterDays)*24*time.Hour {
		return "expired", ""
	}
	return "", ""
}
