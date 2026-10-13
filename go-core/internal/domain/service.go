package domain

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/idgen"
)

// ErrVersionConflict 是 02 §32 的乐观并发冲突：0 行被更新。
// 语义＝reload / reconcile / retry，不是加全局用户锁；调用方（MQ consumer）重试。
var ErrVersionConflict = errors.New("domain: personal model version conflict")

// 行结构：只带 Gate 判定之后要落库的列，字段名对齐 N2 DDL。
type FactRow struct {
	ID              string
	UserID          string
	PersonalModelID string
	SubjectKey      string
	Predicate       string
	ObjectValue     string
	Origin          string
	Status          string
	Confidence      float64
	HasConfidence   bool
	ObservedAt      time.Time
	ModelVersion    int64
	GenerationRunID string
	RejectReason    string
}

type PatternRow struct {
	ID              string
	UserID          string
	PersonalModelID string
	PatternType     string
	SubjectKey      string
	Scope           string
	Description     string
	Status          string
	Confidence      float64
	HasConfidence   bool
	EvidenceCount   int64
	WindowFrom      time.Time
	HasWindowFrom   bool
	WindowTo        time.Time
	HasWindowTo     bool
	DetectorVersion string
	Fingerprint     string
	ScoreDetail     string
	ModelVersion    int64
	GenerationRunID string
	RejectReason    string
}

type InsightRow struct {
	ID              string
	UserID          string
	PersonalModelID string
	Statement       string
	ClaimType       string
	Status          string
	Confidence      float64
	HasConfidence   bool
	WindowFrom      time.Time
	HasWindowFrom   bool
	WindowTo        time.Time
	HasWindowTo     bool
	GenerationModel string
	GenerationVer   string
	GenerationRunID string
	RejectReason    string
	GateDetail      string
	ModelVersion    int64
}

type VersionRow struct {
	ID              string
	UserID          string
	PersonalModelID string
	Version         int64
	Reason          string
	SourceEvent     string
}

type EventRow struct {
	ID              string
	UserID          string
	PersonalModelID string
	EventType       string
	AggregateType   string
	AggregateID     string
	ModelVersion    int64
	Payload         string
	GenerationRunID string
}

// Store 是 Gate 与 Service 需要的持久化面。实现只有一个：
// internal/store 里那个包在事务上的 sqlc 仓储（07:183-186「repository 持久化领域决策，
// 不做决策」——所以这里只有取数与写数，没有任何判断）。
type Store interface {
	Reader

	// EnsurePersonalModel 定位该用户的模型行，没有就建，返回 id 与当前 version。
	EnsurePersonalModel(ctx context.Context, userID string) (string, int64, error)
	// BumpVersion 走 02 §32 的条件更新；冲突返回 ErrVersionConflict。
	BumpVersion(ctx context.Context, modelID string, expected int64) (int64, error)
	InsertVersion(ctx context.Context, row VersionRow) error
	// AppendEvent 只写权威事件行。**业务代码不要直接调它**：投递判定在 Service.emit
	// （按 outboxRouting 决定要不要在同一事务补一行 outbox，N7-3），
	// 绕过去就等于让一次权威变更永远不进投递面。
	AppendEvent(ctx context.Context, row EventRow) error

	InsertFact(ctx context.Context, row FactRow) error
	InsertPattern(ctx context.Context, row PatternRow) error
	InsertInsight(ctx context.Context, row InsightRow) error

	LinkEvidence(ctx context.Context, kind Kind, aggregateID, userID string, evidenceIDs []string) error
	LinkPatterns(ctx context.Context, insightID, userID string, patternIDs []string) error

	// ReactivatePattern 刷新既有指纹行：link 新证据 + last_supported_at + 重算 evidence_count。
	// 返回刷新后的证据条数。
	ReactivatePattern(ctx context.Context, userID, patternID string,
		evidenceIDs []string) (int64, error)

	// ── N4：证据算分入口（03 §4-§5）─────────────────────────────────────────
	// UpsertSourceRecord 登记或复用 source 指针（D-2 薄版：只指不复制）。
	UpsertSourceRecord(ctx context.Context, row SourceRecordRow) (string, error)
	// InsertEvidence 写入**已算分**的证据行；strength 由 Service 算出，不是提案值。
	InsertEvidence(ctx context.Context, row EvidenceWrite) error

	// ── N4：重算入口（03 §17 / tasks N4「证据变弱 → 撤回或过期」）─────────────
	GetInsightState(ctx context.Context, userID, insightID string) (InsightState, error)
	LoadInsightEvidence(ctx context.Context, userID, insightID string) ([]EvidenceRow, error)
	SetInsightStatus(ctx context.Context, row InsightStatusRow) error

	// ── N5：主动触达的资格评估与投递面（04 §5–§12）───────────────────────────
	// InsertProactive 写评估结论行（通过与否都写，G-2 的留痕口径）。
	InsertProactive(ctx context.Context, row ProactiveRow) error
	// GetProactiveState 取重评所需的全部列；查不到返回 ErrProactiveNotFound。
	GetProactiveState(ctx context.Context, userID, proactiveID string) (ProactiveState, error)
	// ListRefStates 按 id 回查引用行的当前状态与锚点时刻（04 §12 取消条件）。
	ListRefStates(ctx context.Context, userID string, ids []string) ([]RefState, error)
	// GetProactiveUserPrefs 读 users 的偏好列；行不存在返回 Found=false（不报错，
	// 由偏好关判 fail-closed——读不到同意状态是判定结论，不是存储故障）。
	GetProactiveUserPrefs(ctx context.Context, userID string) (UserPrefs, error)
	// ListProactiveHistory 取该用户 since 之后的 proactive 行（频次/疲劳/查重共用）。
	ListProactiveHistory(ctx context.Context, userID string,
		since time.Time) ([]ProactiveSendRow, error)
	// LastUserActivityAt 取最后一次用户发言（沉默阶梯基准，同 _days_silent 口径）。
	LastUserActivityAt(ctx context.Context, userID string) (time.Time, bool, error)
	// RecentCorrections 返回窗口内被用户否定过的行 id（04:231）。
	RecentCorrections(ctx context.Context, userID string, withinDays int) ([]string, error)
	// SetProactiveStatus 改写重评结论；行已进终态返回 ErrProactiveTerminal。
	SetProactiveStatus(ctx context.Context, row ProactiveStatusRow) error
	// MarkProactiveSent 置 sent（非终态守卫）；返回库里的 sent_at。
	MarkProactiveSent(ctx context.Context, userID, proactiveID, detail string) (time.Time, error)
	// ExpireProactive 按有效期置 expired；行已进终态返回 ErrProactiveTerminal。
	ExpireProactive(ctx context.Context, userID, proactiveID string) error
	// LinkProactiveRefs 把引用到的 insight/pattern 写进两张回链表。
	LinkProactiveRefs(ctx context.Context, proactiveID, userID string, refs []RefState) error

	// ── N6：Outcome 闭环 + 版本化 + 源失效传播（04 §15–§18、01 §19–§22）──────
	// InsertOutcome 写 outcomes 行；status 由领域层按 origin 决定，不是提案值。
	InsertOutcome(ctx context.Context, row OutcomeRow) error
	// LinkOutcomeTargets 写 outcome_links（ON CONFLICT DO NOTHING，重跑不产重复行）。
	LinkOutcomeTargets(ctx context.Context, outcomeID, userID string, targets []TargetRef) error
	// MarkOutcomeStatus 改写 outcome 状态；守卫只放行 recorded/candidate 起算
	// （0 行 ⇒ ErrOutcomeNotFound，被纠正过的历史不得二次改写）。
	MarkOutcomeStatus(ctx context.Context, userID, outcomeID string, status OutcomeStatus) error
	// InsertFeedback 写 user_feedback 行（不变量 6：表态必然留痕）。
	InsertFeedback(ctx context.Context, row FeedbackRow) error
	// LatestFeedbackForTarget 取该目标最近一条表态的 id 与类型；无历史时 Found=false。
	LatestFeedbackForTarget(ctx context.Context, userID string,
		target RefType, targetID string) (LatestFeedback, error)
	// GetPatternState 取生命周期改写需要的列；查不到返回 ErrPatternNotFound。
	GetPatternState(ctx context.Context, userID, patternID string) (PatternState, error)
	// UpdatePatternLifecycle 是 patterns 状态列的唯一写入口（07 §3：转移由领域层决定）。
	UpdatePatternLifecycle(ctx context.Context, row PatternLifecycleRow) error
	// UpdateFactStatus 改写 fact 状态列（02:104-110 五值）。
	UpdateFactStatus(ctx context.Context, row FactStatusRow) error
	// CompleteGoal / CompleteCommitment 把目标落到应用真正使用的那张表
	// （02:824「不要维护重复的 goal/commitment 状态」）。
	CompleteGoal(ctx context.Context, userID, goalID string) error
	CompleteCommitment(ctx context.Context, userID, commitmentID string) error
	// ListInsightsForPattern 返回挂在该 pattern 上的 insight id 与当前状态（传播链第二跳）。
	ListInsightsForPattern(ctx context.Context, userID, patternID string) ([]PatternInsightRef, error)
	// ListQueuedProactives 取仍在队列里的触达行（完成目标后要按 packet 引用取消）。
	ListQueuedProactives(ctx context.Context, userID string) ([]QueuedProactive, error)
	// ListDerivedSupportForEvidence 返回一条证据支撑的派生行及其总支撑/活支撑计数
	// （不变量 9 的判定面；「活」＝所在 source_records 未删）。
	ListDerivedSupportForEvidence(ctx context.Context, userID,
		evidenceID string) ([]DerivedSupport, error)

	// ── N7：投递面（18 §2/§8：投递行与幂等行都要和权威态变更同事务）───────────
	// EnqueueOutbox 写一行待投递消息。message_id 撞 UNIQUE 就是 bug（18 §10：
	// 稳定 ID 由逻辑操作边界给出，重试不换新 id），所以仓储**不做**静默忽略。
	EnqueueOutbox(ctx context.Context, row OutboxRow) error
	// InsertInbox 认领幂等位；返回 false＝该消费者已处理过这条 message_id（重复投递）。
	InsertInbox(ctx context.Context, row InboxRow) (bool, error)
	// GetInboxStatus 读幂等行终态，用来区分「已消费」与「已记录永久拒绝」（14 §7）。
	GetInboxStatus(ctx context.Context, messageID, consumer string) (InboxStatus, error)
	MarkInboxProcessed(ctx context.Context, messageID, consumer string) error
	// DeletePublishedOutboxBefore / DeleteProcessedInboxBefore 只清已完成的投递
	// （18 §13 禁止激进删除：待办与死信不在此列）。
	DeletePublishedOutboxBefore(ctx context.Context, before time.Time) (int64, error)
	DeleteProcessedInboxBefore(ctx context.Context, before time.Time) (int64, error)
}

// LatestFeedback 是「同一目标最后一次表态」的投影（07 §6 ErrDuplicateFeedback 的输入）。
type LatestFeedback struct {
	ID           string
	FeedbackType string
	Found        bool
}

// PatternInsightRef 是 pattern→insight 回链一行。
type PatternInsightRef struct {
	ID     string
	Status string
}

// Transactor 把一次 Admit 的全部读写钉在同一事务里：判定与写入之间不许换快照，
// 否则「证据存在性」这一关可能读到并发删除的行。
type Transactor interface {
	InTx(ctx context.Context, fn func(context.Context, Store) error) error
	// RecordInboxFailure 写在**主事务之外**的一次独立写入（14 §7 的第三类 ACK 要求
	// 「永久拒绝已按策略记录」，而 18 §8 的单事务会把这条留痕一起回滚掉）。
	RecordInboxFailure(ctx context.Context, row InboxFailureRow) error
}

// Result 是一次接纳/拒绝的结果。
type Result struct {
	Action        Action
	ID            string
	Status        string
	Reason        RejectReason
	Detail        string
	ModelVersion  int64
	EvidenceCount int64
}

// Service 是 canonical 个人模型状态的唯一写入口（不变量 7）。
// HTTP handler 与 MQ consumer 都只能经它；这条由 arch_test.go 拍成可执行约束。
type Service struct {
	db   Transactor
	gate Gate
	// newID 生成 8-4-4-4-12 小写 uuid4，与 Python 侧 uuid4().str 同形（两栈共读同一行）。
	newID func() string
	// now 只服务于测试的确定性时间。
	now func() time.Time
}

func NewService(db Transactor, policy Policy) *Service {
	return &Service{db: db, gate: Gate{Policy: policy}, newID: idgen.New, now: time.Now}
}

// Admit 在**一个事务**里跑完七关并落库。
//
// 落库口径（G-2 + 七-2 勘误）：
//   - 拒绝也留行（可审计 + N4 的拒绝签名抑制要样本），status：
//     fact/pattern 用 'rejected'（02 §12/§13 有该态）；
//     **insight 用 'draft'**——02:144-154 的 insight_status 九态里没有 rejected，
//     拒因写 reject_reason，不新增状态。
//   - 接纳：fact 若 origin ∈ {direct, user_confirmed} 直接 active（03:169-173 的显式路径），
//     否则 candidate；pattern 落 candidate；insight 落 draft。
//   - 版本：accept/reactivate 才 ++ current_version 并写 personal_model_versions +
//     personal_model_events；reject 只写 event（权威模型内容没变）。
func (s *Service) Admit(ctx context.Context, c Candidate) (Result, error) {
	if c.Kind == "" {
		return Result{}, errors.New("domain: candidate.kind is required")
	}
	result := Result{}
	err := s.db.InTx(ctx, func(ctx context.Context, st Store) error {
		// 模型行以库为准：调用方给的 id 只做校验，不作为写入依据。
		modelID, version, err := st.EnsurePersonalModel(ctx, c.UserID)
		if err != nil {
			return err
		}
		if c.PersonalModelID != "" && c.PersonalModelID != modelID {
			return fmt.Errorf("domain: candidate.personal_model_id=%s 与该用户实际模型 %s 不符",
				c.PersonalModelID, modelID)
		}
		c.PersonalModelID = modelID

		outcome, err := s.gate.Check(ctx, &c, st)
		if err != nil {
			return err
		}
		result.Action = outcome.Action
		result.Reason = outcome.Reason
		result.Detail = outcome.Detail

		switch outcome.Action {
		case ActionReject:
			return s.writeRejected(ctx, st, &c, &result, version, outcome)
		case ActionReactivate:
			return s.reactivate(ctx, st, &c, &result, version, outcome)
		case ActionInsert:
			return s.writeAccepted(ctx, st, &c, &result, version, outcome)
		default:
			return fmt.Errorf("domain: 未知 Gate 结论 %q", outcome.Action)
		}
	})
	if err != nil {
		return Result{}, err
	}
	return result, nil
}

func (s *Service) writeRejected(ctx context.Context, st Store, c *Candidate,
	result *Result, version int64, outcome Outcome) error {
	// 用户历史抑制（Gate 第 9 关）带来一条例外：**不插行**。库里已有同指纹的 pattern，
	// 再插一条会直接撞 N2 的偏唯一索引 ix_patterns_user_fingerprint_unique（23505），
	// 而这一关要的恰恰是「别再生成」——所以只补审计事件，指向既有行。
	// G-2 的「拒绝也留行」在这里不失效：留痕是必须的，只是留成事件而不是重复行。
	if outcome.DuplicateOfID != "" {
		result.ID, result.Status = outcome.DuplicateOfID, "suppressed"
		result.ModelVersion = version
		return s.emit(ctx, st, s.eventRow(c, s.newID(), "candidate_suppressed",
			outcome.DuplicateOfID, "suppressed", version, outcome))
	}

	id := s.newID()
	// 拒绝路径不看晋升：status 一律走各 kind 的拒绝态（statusFor 的 accepted=false 分支）。
	status, _ := s.statusFor(c, false, false)
	result.ID, result.Status = id, status

	switch c.Kind {
	case KindFact:
		if err := st.InsertFact(ctx, s.factRow(c, id, status, version, outcome.Reason.String())); err != nil {
			return err
		}
	case KindPattern:
		if err := st.InsertPattern(ctx, s.patternRow(c, id, status, version, outcome.Reason.String())); err != nil {
			return err
		}
	case KindInsight:
		if err := st.InsertInsight(ctx, s.insightRow(c, id, status, version,
			outcome.Reason.String(), outcome.GateDetail())); err != nil {
			return err
		}
		result.EvidenceCount = int64(len(c.EvidenceIDs))
	default:
		return fmt.Errorf("domain: 无法拒绝未知 kind %q", c.Kind)
	}
	result.ModelVersion = version
	// 拒绝不动模型版本，但必须留可审计事件（不变量 6/8 的方向）。
	return s.emit(ctx, st, s.eventRow(c, s.newID(), "candidate_rejected",
		id, status, version, outcome))
}

func (s *Service) writeAccepted(ctx context.Context, st Store, c *Candidate,
	result *Result, version int64, outcome Outcome) error {
	id := s.newID()
	// fact 的 active 资格由 03 §7 的两条路径决定，其中推断路径要看**已算分**的证据，
	// 所以必须在这里查库，不能只看提案自称的 origin。
	promoted, err := s.factPromoted(ctx, st, c)
	if err != nil {
		return err
	}
	status, bump := s.statusFor(c, true, promoted)
	result.ID, result.Status = id, status

	if bump {
		newVersion, err := st.BumpVersion(ctx, c.PersonalModelID, version)
		if err != nil {
			return err
		}
		version = newVersion
	}
	eventID := s.newID()

	// 证据回链先于回链 pattern：02 §22 的回链是「为什么看到这条」的唯一答案，
	// 顺序固定下来，N9 读路径与集成测才能按同一条链路对账。
	if err := st.LinkEvidence(ctx, c.Kind, id, c.UserID, c.EvidenceIDs); err != nil {
		return err
	}

	switch c.Kind {
	case KindFact:
		if err := st.InsertFact(ctx, s.factRow(c, id, status, version, "")); err != nil {
			return err
		}
	case KindPattern:
		row := s.patternRow(c, id, status, version, "")
		row.EvidenceCount = int64(len(c.EvidenceIDs))
		if err := st.InsertPattern(ctx, row); err != nil {
			return err
		}
		result.EvidenceCount = row.EvidenceCount
	case KindInsight:
		row := s.insightRow(c, id, status, version, "", outcome.GateDetail())
		if err := st.InsertInsight(ctx, row); err != nil {
			return err
		}
		if err := st.LinkPatterns(ctx, id, c.UserID, c.PatternIDs); err != nil {
			return err
		}
		result.EvidenceCount = int64(len(c.EvidenceIDs))
	default:
		return fmt.Errorf("domain: 无法接纳未知 kind %q", c.Kind)
	}
	if bump {
		if err := st.InsertVersion(ctx, VersionRow{
			ID: s.newID(), UserID: c.UserID, PersonalModelID: c.PersonalModelID,
			Version: version, Reason: s.eventType(c.Kind, true), SourceEvent: eventID,
		}); err != nil {
			return err
		}
	}
	result.ModelVersion = version
	return s.emit(ctx, st, s.eventRow(c, eventID, s.eventType(c.Kind, bump),
		id, status, version, outcome))
}

func (s *Service) reactivate(ctx context.Context, st Store, c *Candidate,
	result *Result, version int64, outcome Outcome) error {
	count, err := st.ReactivatePattern(ctx, c.UserID, outcome.DuplicateOfID, c.EvidenceIDs)
	if err != nil {
		return err
	}
	newVersion, err := st.BumpVersion(ctx, c.PersonalModelID, version)
	if err != nil {
		return err
	}
	eventID := s.newID()
	if err := st.InsertVersion(ctx, VersionRow{
		ID: s.newID(), UserID: c.UserID, PersonalModelID: c.PersonalModelID,
		Version: newVersion, Reason: "pattern_reactivated", SourceEvent: eventID,
	}); err != nil {
		return err
	}
	// status 这里落的是「本次 Gate 对既有行的动作」，不是 patterns.status：
	// 被刷新的行仍活，N4 的生命周期算分负责给它定 confirmed/expired。
	result.ID, result.Status, result.ModelVersion, result.EvidenceCount =
		outcome.DuplicateOfID, "reactivated", newVersion, count
	return s.emit(ctx, st, s.eventRow(c, eventID, "pattern_reactivated",
		outcome.DuplicateOfID, "reactivated", newVersion, outcome))
}

// statusFor 返回「该落什么状态」与「要不要动模型版本」。
//
// 显式路径的事实（用户明说／用户确认）直接进 active：V3 03:169-173。
func (s *Service) statusFor(c *Candidate, accepted bool, promoted bool) (string, bool) {
	if !accepted {
		// 02:144-154 的 insight_status 没有 rejected，被拒的 insight 停在 draft + reject_reason。
		if c.Kind == KindInsight {
			return "draft", false
		}
		return "rejected", false
	}
	switch c.Kind {
	case KindFact:
		return s.factStatus(c.Origin, promoted), true
	case KindPattern:
		return "candidate", true
	case KindInsight:
		return "draft", true
	default:
		return "", false
	}
}

// factStatus 是 03 §7 的两条晋升路径（03:165-184）：
//   - 显式路径：用户明说／用户确认 → 直接 active（03:169-173）。
//   - 推断路径：origin=repeated/temporal/behavioral 这类**推断出来的**事实，
//     必须 ≥2 条独立观测 + combined evidence ≥0.75 才 active，否则停在 candidate。
//     promoted 由 factPromoted 查库算出来，不是提案说的。
func (s *Service) factStatus(origin string, promoted bool) string {
	switch origin {
	case "direct", "user_confirmed":
		return "active"
	default:
		if promoted {
			return "active"
		}
		return "candidate"
	}
}

// factPromoted 按 03 §7 判定推断事实是否够格进 active。
// 只对非显式 origin 的 fact 跑一次证据查询：显式路径不需要证据算术。
func (s *Service) factPromoted(ctx context.Context, st Store, c *Candidate) (bool, error) {
	if c.Kind != KindFact {
		return false, nil
	}
	switch c.Origin {
	case "direct", "user_confirmed":
		return false, nil
	}
	rows, err := st.LoadEvidence(ctx, c.EvidenceIDs)
	if err != nil {
		return false, fmt.Errorf("domain: load evidence for promotion: %w", err)
	}
	independent, combined, conflict := s.gate.Policy.Evidence.Combined(derivedRows(rows))
	if conflict {
		// §7:186-187：冲突必须表示出来，不能平均掉。这里不拒绝候选（拒绝是 Gate 的事），
		// 只是不给晋升资格——停在 candidate 等用户确认或证据变化。
		return false, nil
	}
	return independent >= s.gate.Policy.Evidence.FactMinIndependent &&
		combined >= s.gate.Policy.Evidence.FactCombinedMin, nil
}

func (s *Service) eventRow(c *Candidate, id, eventType, aggregateID, status string,
	version int64, outcome Outcome) EventRow {
	raw, _ := json.Marshal(struct {
		Status      string   `json:"status"`
		Reason      string   `json:"reason,omitempty"`
		Detail      string   `json:"detail,omitempty"`
		Checks      []string `json:"checks,omitempty"`
		Fingerprint string   `json:"fingerprint,omitempty"`
		Evidence    int      `json:"evidence_count"`
	}{status, outcome.Reason.String(), outcome.Detail, outcome.Checks,
		c.Fingerprint, len(c.EvidenceIDs)})
	return EventRow{
		ID: id, UserID: c.UserID, PersonalModelID: c.PersonalModelID,
		EventType: eventType, AggregateType: s.table(c.Kind), AggregateID: aggregateID,
		ModelVersion: version, Payload: string(raw), GenerationRunID: c.GenerationRunID,
	}
}

func (s *Service) eventType(kind Kind, canonicalChange bool) string {
	if !canonicalChange {
		return "candidate_admitted"
	}
	switch kind {
	case KindFact:
		return "fact_admitted"
	case KindPattern:
		return "pattern_admitted"
	case KindInsight:
		return "insight_admitted"
	default:
		return "unknown_admitted"
	}
}

func (s *Service) table(kind Kind) string {
	switch kind {
	case KindFact:
		return "personal_facts"
	case KindPattern:
		return "patterns"
	case KindInsight:
		return "insights"
	default:
		return ""
	}
}

func (s *Service) factRow(c *Candidate, id, status string, version int64, reason string) FactRow {
	row := FactRow{
		ID: id, UserID: c.UserID, PersonalModelID: c.PersonalModelID,
		SubjectKey: c.SubjectKey, Predicate: c.Predicate, ObjectValue: c.ObjectValue,
		Origin: c.Origin, Status: status, Confidence: c.Confidence, HasConfidence: true,
		ObservedAt: s.observedAt(c), ModelVersion: version,
		GenerationRunID: c.GenerationRunID, RejectReason: reason,
	}
	if status == "active" {
		row.ObservedAt = s.now()
	}
	return row
}

func (s *Service) patternRow(c *Candidate, id, status string, version int64, reason string) PatternRow {
	return PatternRow{
		ID: id, UserID: c.UserID, PersonalModelID: c.PersonalModelID,
		PatternType: c.PatternType, SubjectKey: c.SubjectKey, Scope: c.Scope,
		Description: c.Description, Status: status, Confidence: c.Confidence,
		HasConfidence: true, WindowFrom: c.WindowFrom, HasWindowFrom: !c.WindowFrom.IsZero(),
		WindowTo: c.WindowTo, HasWindowTo: !c.WindowTo.IsZero(),
		DetectorVersion: c.DetectorVersion, Fingerprint: c.Fingerprint,
		ModelVersion: version, GenerationRunID: c.GenerationRunID, RejectReason: reason,
	}
}

func (s *Service) insightRow(c *Candidate, id, status string, version int64,
	reason, detail string) InsightRow {
	return InsightRow{
		ID: id, UserID: c.UserID, PersonalModelID: c.PersonalModelID,
		Statement: c.Statement, ClaimType: c.ClaimType, Status: status,
		Confidence: c.Confidence, HasConfidence: true,
		WindowFrom: c.WindowFrom, HasWindowFrom: !c.WindowFrom.IsZero(),
		WindowTo: c.WindowTo, HasWindowTo: !c.WindowTo.IsZero(),
		GenerationModel: c.GenerationModel, GenerationVer: c.GenerationVer,
		GenerationRunID: c.GenerationRunID, RejectReason: reason, GateDetail: detail,
		ModelVersion: version,
	}
}

func (s *Service) observedAt(c *Candidate) time.Time {
	if !c.WindowTo.IsZero() {
		return c.WindowTo
	}
	return s.now()
}

// RejectReason.String 让落库口径只有一处转换。
func (r RejectReason) String() string { return string(r) }
