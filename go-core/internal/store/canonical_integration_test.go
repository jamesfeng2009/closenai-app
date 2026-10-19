// N2 canonical schema integration test: it runs the sqlc-generated statements in
// internal/store/canonicaldb against the real PostgreSQL schema produced by
// `alembic upgrade head`. This is the proof that the V3 02 §37 "Minimum V1
// Queries" are answerable on the tables as built, that the 02 §32 optimistic
// concurrency statement is 0-row-on-conflict rather than blocking, and that every
// read is scoped by user_id (01 §22 invariant 7's ownership half).
//
// Skips unless TEST_DATABASE_URL is set, e.g.
//
//	TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/closenai \
//	    go test ./internal/store/ -run Canonical
package store

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"math"
	"math/big"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/store/canonicaldb"
)

// canonicalTables are every N2 table carrying user_id, in child-before-parent
// order. There are no FKs (D-5 keeps deletion on the per-table rowcount purge),
// so this ordering only makes the cleanup readable.
var canonicalTables = []string{
	"fact_evidence", "pattern_evidence", "insight_evidence", "insight_patterns",
	"proactive_insights", "proactive_patterns", "outcome_links",
	// outbox_messages 带偏离列 user_id（七-6 N7-1 偏离①），所以按用户清——
	// 这正是它进 D-5 purge 清单的同一口径。
	"outbox_messages",
	"personal_model_versions", "personal_model_events",
	"personal_facts", "personal_goals", "personal_commitments",
	"evidence", "source_records", "patterns", "insights", "proactives",
	"outcomes", "user_feedback", "personal_models",
	// inbox_messages 不在此列：它的幂等键是 (message_id, consumer)，**没有 user_id**
	// （18 §7 的字段面就是按消费者去重的）。按 `WHERE user_id = $1` 删会直接撞
	// 「列不存在」，所以 N7 的用例各自按 consumer 清理（见 delivery_integration_test.go）。
}

func newID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(fmt.Sprintf("store: no entropy for uuid4: %v", err))
	}
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	h := hex.EncodeToString(b[:])
	return fmt.Sprintf("%s-%s-%s-%s-%s", h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])
}

func txt(s string) pgtype.Text { return pgtype.Text{String: s, Valid: true} }

func nullText() pgtype.Text { return pgtype.Text{} }

func ts(t time.Time) pgtype.Timestamptz {
	return pgtype.Timestamptz{Time: t, Valid: true}
}

func num(t *testing.T, v float64) pgtype.Numeric {
	t.Helper()
	// DECIMAL(5,4) => value * 10^4 with exponent -4. Scaling by hand keeps the
	// literal exact; big.Rat would reduce 9000/10000 to 9/10 and the exponent
	// would then be off by three digits.
	i := new(big.Int).SetInt64(int64(math.Round(v * 10000)))
	return pgtype.Numeric{Int: i, Exp: -4, Valid: true}
}

func i8(v int64) pgtype.Int8 { return pgtype.Int8{Int64: v, Valid: true} }

func cleanupCanonical(t *testing.T, pool *pgxpool.Pool, userID string) {
	t.Helper()
	ctx := context.Background()
	for _, table := range canonicalTables {
		if _, err := pool.Exec(ctx, `DELETE FROM `+table+` WHERE user_id = $1`, userID); err != nil {
			t.Errorf("cleanup %s: %v", table, err)
		}
	}
}

// chain is the planted object graph one user's personal model is made of.
type chain struct {
	userID, modelID string
	factID          string
	sourceID        string
	evidenceID      string
	patternID       string
	insightID       string
	proactiveID     string
	outcomeID       string
	goalID          string
	commitmentID    string
	now             time.Time
}

func plantChain(t *testing.T, q *canonicaldb.Queries, userID string) chain {
	t.Helper()
	ctx := context.Background()
	now := time.Now().UTC().Truncate(time.Microsecond)
	c := chain{userID: userID, now: now}

	c.modelID = newID()
	if err := q.InsertPersonalModel(ctx, canonicaldb.InsertPersonalModelParams{
		ID: c.modelID, UserID: userID,
	}); err != nil {
		t.Fatalf("InsertPersonalModel: %v", err)
	}

	// D-2 thin source registry: a pointer to an existing row, no copied content.
	gotID, err := q.InsertSourceRecord(ctx, canonicaldb.InsertSourceRecordParams{
		ID: newID(), UserID: userID, SourceType: "journal_entry",
		SourceTable: "memories", SourceID: newID(), OccurredAt: ts(now),
		RevisionOf: nullText(),
	})
	if err != nil {
		t.Fatalf("InsertSourceRecord: %v", err)
	}
	c.sourceID = gotID

	c.evidenceID = newID()
	if err := q.InsertEvidence(ctx, canonicaldb.InsertEvidenceParams{
		ID: c.evidenceID, UserID: userID, SourceRecordID: c.sourceID,
		EvidenceType: "direct", Role: "supporting", Strength: num(t, 0.9),
		Observation: txt("user wrote: quiet environment works"), Excerpt: txt("quiet"),
		MetadataJson: "{}", GenerationRunID: txt(newID()),
	}); err != nil {
		t.Fatalf("InsertEvidence: %v", err)
	}

	c.factID = newID()
	if err := q.InsertFact(ctx, canonicaldb.InsertFactParams{
		ID: c.factID, UserID: userID, PersonalModelID: c.modelID,
		SubjectKey: "user", Predicate: "prefers_work_environment",
		ObjectValue: `{"value":"quiet"}`, Origin: "explicit_user",
		Status: "candidate", Confidence: num(t, 0.88),
		FirstObservedAt: ts(now), LastObservedAt: ts(now),
		ModelVersion: i8(1), GenerationRunID: nullText(), RejectReason: nullText(),
	}); err != nil {
		t.Fatalf("InsertFact: %v", err)
	}
	if err := q.LinkFactEvidence(ctx, canonicaldb.LinkFactEvidenceParams{
		FactID: c.factID, EvidenceID: c.evidenceID, UserID: userID,
	}); err != nil {
		t.Fatalf("LinkFactEvidence: %v", err)
	}
	// Domain Gate (N3) promotes candidate -> active; the row must be readable
	// through §37-1 only after that transition.
	if _, err := q.UpdateFactStatus(ctx, canonicaldb.UpdateFactStatusParams{
		ID: c.factID, UserID: userID, Status: "active",
		ConfirmedAt: ts(now), InvalidatedAt: pgtype.Timestamptz{},
		RejectReason: nullText(), ModelVersion: i8(2),
	}); err != nil {
		t.Fatalf("UpdateFactStatus: %v", err)
	}

	c.patternID = newID()
	if err := q.InsertPattern(ctx, canonicaldb.InsertPatternParams{
		ID: c.patternID, UserID: userID, PersonalModelID: c.modelID,
		PatternType: "work_environment_preference", SubjectKey: txt("user"),
		Scope: txt("work"), Description: "focus holds longer in quiet settings",
		Status: "candidate", Confidence: num(t, 0.7), EvidenceCount: 1,
		ObservedFrom: ts(now.AddDate(0, 0, -21)), ObservedTo: ts(now),
		DetectorVersion: txt("v1"), Fingerprint: txt("fp-" + userID),
		ScoreDetail: txt(`{"evidence_strength":0.9}`), ModelVersion: i8(2),
		GenerationRunID: txt(newID()), RejectReason: nullText(),
	}); err != nil {
		t.Fatalf("InsertPattern: %v", err)
	}
	if err := q.LinkPatternEvidence(ctx, canonicaldb.LinkPatternEvidenceParams{
		PatternID: c.patternID, EvidenceID: c.evidenceID, UserID: userID,
	}); err != nil {
		t.Fatalf("LinkPatternEvidence: %v", err)
	}
	if _, err := q.UpdatePatternLifecycle(ctx, canonicaldb.UpdatePatternLifecycleParams{
		ID: c.patternID, UserID: userID, Status: "supported", Confidence: num(t, 0.72),
		EvidenceCount: 1, ScoreDetail: txt(`{"evidence_strength":0.9}`),
		LastSupportedAt: ts(now), ConfirmedAt: pgtype.Timestamptz{},
		RejectedAt: pgtype.Timestamptz{}, ExpiredAt: pgtype.Timestamptz{},
		RejectReason: nullText(), ModelVersion: i8(3),
	}); err != nil {
		t.Fatalf("UpdatePatternLifecycle: %v", err)
	}

	c.insightID = newID()
	if err := q.InsertInsight(ctx, canonicaldb.InsertInsightParams{
		ID: c.insightID, UserID: userID, PersonalModelID: c.modelID,
		Statement: "In the past three weeks you focus longer in quiet settings",
		ClaimType: "trend", Status: "draft", Confidence: num(t, 0.78),
		TimeFrom: ts(now.AddDate(0, 0, -21)), TimeTo: ts(now),
		GenerationModel: txt("test-model"), GenerationVersion: txt("v1"),
		GenerationRunID: txt(newID()), RejectReason: nullText(),
		GateDetail: txt(`{"gates":["schema","evidence"]}`), ModelVersion: i8(3),
	}); err != nil {
		t.Fatalf("InsertInsight: %v", err)
	}
	if err := q.LinkInsightEvidence(ctx, canonicaldb.LinkInsightEvidenceParams{
		InsightID: c.insightID, EvidenceID: c.evidenceID, UserID: userID, Role: "supporting",
	}); err != nil {
		t.Fatalf("LinkInsightEvidence: %v", err)
	}
	if err := q.LinkInsightPattern(ctx, canonicaldb.LinkInsightPatternParams{
		InsightID: c.insightID, PatternID: c.patternID, UserID: userID,
	}); err != nil {
		t.Fatalf("LinkInsightPattern: %v", err)
	}
	if _, err := q.UpdateInsightStatus(ctx, canonicaldb.UpdateInsightStatusParams{
		ID: c.insightID, UserID: userID, Status: "published",
		PublishedAt: ts(now), RejectReason: nullText(),
		GateDetail: txt(`{"gates":["schema","evidence","temporal"]}`), ModelVersion: i8(4),
	}); err != nil {
		t.Fatalf("UpdateInsightStatus: %v", err)
	}

	c.goalID = newID()
	if err := q.InsertGoal(ctx, canonicaldb.InsertGoalParams{
		ID: c.goalID, UserID: userID, PersonalModelID: c.modelID,
		Title: "protect two deep-work blocks a week", Description: nullText(),
		Status: "active", StartedAt: ts(now), TargetAt: ts(now.AddDate(0, 0, 30)),
	}); err != nil {
		t.Fatalf("InsertGoal: %v", err)
	}

	c.commitmentID = newID()
	if err := q.InsertCommitment(ctx, canonicaldb.InsertCommitmentParams{
		ID: c.commitmentID, UserID: userID, PersonalModelID: c.modelID,
		Title: "no meetings on Wednesday mornings", Description: nullText(),
		Status: "active", DueAt: ts(now.AddDate(0, 0, 7)),
		GoalID: nullText(), // T4-01 之前这一列不存在；NULL＝「没挂在任何目标下」，不是空串
	}); err != nil {
		t.Fatalf("InsertCommitment: %v", err)
	}

	c.proactiveID = newID()
	if err := q.InsertProactive(ctx, canonicaldb.InsertProactiveParams{
		ID: c.proactiveID, UserID: userID, PersonalModelID: c.modelID,
		Status: "eligible", TriggerType: "pattern_followup",
		Message:     txt("Wednesday morning is still yours"),
		ScheduledAt: ts(now), ExpiresAt: ts(now.AddDate(0, 0, 1)),
		EligibilityScore:  num(t, 0.81),
		EligibilityDetail: txt(`{"hard_gate":"pass"}`),
		GenerationModel:   txt("test-model"), GenerationVersion: txt("v1"),
		GenerationRunID: txt(newID()), ModelVersion: i8(4),
		JourneyEventID: txt(newID()),
	}); err != nil {
		t.Fatalf("InsertProactive: %v", err)
	}
	if err := q.LinkProactiveInsight(ctx, canonicaldb.LinkProactiveInsightParams{
		ProactiveID: c.proactiveID, InsightID: c.insightID, UserID: userID,
	}); err != nil {
		t.Fatalf("LinkProactiveInsight: %v", err)
	}
	if err := q.LinkProactivePattern(ctx, canonicaldb.LinkProactivePatternParams{
		ProactiveID: c.proactiveID, PatternID: c.patternID, UserID: userID,
	}); err != nil {
		t.Fatalf("LinkProactivePattern: %v", err)
	}

	c.outcomeID = newID()
	if err := q.InsertOutcome(ctx, canonicaldb.InsertOutcomeParams{
		ID: c.outcomeID, UserID: userID, OutcomeType: "goal_progress",
		Status: "recorded", Result: "better",
		Description: txt("user reported two deep-work blocks"),
		OccurredAt:  ts(now), Origin: "user_statement", ModelVersion: i8(5),
		GenerationRunID: nullText(),
	}); err != nil {
		t.Fatalf("InsertOutcome: %v", err)
	}
	if err := q.LinkOutcome(ctx, canonicaldb.LinkOutcomeParams{
		OutcomeID: c.outcomeID, TargetType: "goal", TargetID: c.goalID, UserID: userID,
	}); err != nil {
		t.Fatalf("LinkOutcome: %v", err)
	}

	if err := q.InsertUserFeedback(ctx, canonicaldb.InsertUserFeedbackParams{
		ID: newID(), UserID: userID, FeedbackType: "confirm", TargetType: "pattern",
		TargetID: c.patternID, CorrectionText: nullText(), MetadataJson: "{}",
	}); err != nil {
		t.Fatalf("InsertUserFeedback: %v", err)
	}

	if err := q.InsertModelEvent(ctx, canonicaldb.InsertModelEventParams{
		ID: newID(), UserID: userID, PersonalModelID: c.modelID,
		EventType: "pattern_supported", AggregateType: "pattern",
		AggregateID: txt(c.patternID), ModelVersion: 3,
		Payload: `{"status":"supported"}`, GenerationRunID: txt(newID()),
	}); err != nil {
		t.Fatalf("InsertModelEvent: %v", err)
	}
	return c
}

// TestCanonicalMinimumV1Queries answers every question V3 02 §37 requires of the
// schema, against rows planted through the same statements the Go domain will use.
func TestCanonicalMinimumV1Queries(t *testing.T) {
	pool := poolForTest(t)
	q := canonicaldb.New(pool)
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })

	c := plantChain(t, q, userID)
	ctx := context.Background()

	t.Run("what_do_we_know", func(t *testing.T) {
		facts, err := q.ListActiveFacts(ctx, canonicaldb.ListActiveFactsParams{
			UserID: userID, PersonalModelID: c.modelID,
		})
		if err != nil {
			t.Fatalf("ListActiveFacts: %v", err)
		}
		if len(facts) != 1 || facts[0].ID != c.factID {
			t.Fatalf("active facts = %+v, want exactly the promoted fact %s", facts, c.factID)
		}
		if facts[0].ObjectValue != `{"value":"quiet"}` {
			t.Errorf("object_value = %q", facts[0].ObjectValue)
		}
	})

	t.Run("why_do_we_believe_it", func(t *testing.T) {
		rows, err := q.ListEvidenceForFact(ctx, canonicaldb.ListEvidenceForFactParams{
			UserID: userID, FactID: c.factID,
		})
		if err != nil {
			t.Fatalf("ListEvidenceForFact: %v", err)
		}
		if len(rows) != 1 {
			t.Fatalf("evidence rows = %d, want 1", len(rows))
		}
		// 02 §37's chain is Fact -> Evidence -> SourceRecord, one query.
		if rows[0].SourceRecordID != c.sourceID || rows[0].SourceTable != "memories" {
			t.Errorf("provenance not reachable: %+v", rows[0])
		}
	})

	t.Run("what_patterns_exist", func(t *testing.T) {
		patterns, err := q.ListLivePatterns(ctx, canonicaldb.ListLivePatternsParams{
			UserID: userID, PersonalModelID: c.modelID,
		})
		if err != nil {
			t.Fatalf("ListLivePatterns: %v", err)
		}
		if len(patterns) != 1 || patterns[0].Status != "supported" {
			t.Fatalf("patterns = %+v, want one supported", patterns)
		}
	})

	t.Run("why_does_the_pattern_exist", func(t *testing.T) {
		rows, err := q.ListEvidenceForPattern(ctx, canonicaldb.ListEvidenceForPatternParams{
			UserID: userID, PatternID: c.patternID,
		})
		if err != nil || len(rows) != 1 {
			t.Fatalf("ListEvidenceForPattern = %d rows, err %v", len(rows), err)
		}
	})

	t.Run("what_did_the_ai_tell_the_user", func(t *testing.T) {
		insights, err := q.ListPublishedInsights(ctx, canonicaldb.ListPublishedInsightsParams{
			UserID: userID, PersonalModelID: c.modelID,
		})
		if err != nil || len(insights) != 1 {
			t.Fatalf("ListPublishedInsights = %d rows, err %v", len(insights), err)
		}
		if insights[0].ClaimType != "trend" {
			t.Errorf("claim_type = %q, want trend (03 §14 gate 4)", insights[0].ClaimType)
		}
	})

	t.Run("why_is_the_insight_supported", func(t *testing.T) {
		ev, err := q.ListEvidenceForInsight(ctx, canonicaldb.ListEvidenceForInsightParams{
			UserID: userID, InsightID: c.insightID,
		})
		if err != nil || len(ev) != 1 {
			t.Fatalf("ListEvidenceForInsight = %d rows, err %v", len(ev), err)
		}
		ps, err := q.ListPatternsForInsight(ctx, canonicaldb.ListPatternsForInsightParams{
			UserID: userID, InsightID: c.insightID,
		})
		if err != nil || len(ps) != 1 || ps[0].ID != c.patternID {
			t.Fatalf("ListPatternsForInsight = %d rows, err %v", len(ps), err)
		}
	})

	t.Run("what_did_the_user_correct", func(t *testing.T) {
		rows, err := q.ListUserFeedback(ctx, canonicaldb.ListUserFeedbackParams{
			UserID: userID, Column2: []string{"corrected", "rejected", "answered"}, Limit: 10,
		})
		if err != nil {
			t.Fatalf("ListUserFeedback: %v", err)
		}
		if len(rows) != 0 {
			t.Fatalf("feedback = %+v, want none for a non-correcting type", rows)
		}
		confirming, err := q.ListUserFeedback(ctx, canonicaldb.ListUserFeedbackParams{
			UserID: userID, Column2: []string{"confirm"}, Limit: 10,
		})
		if err != nil || len(confirming) != 1 {
			t.Fatalf("ListUserFeedback(confirm) = %d rows, err %v", len(confirming), err)
		}
	})

	t.Run("what_changed", func(t *testing.T) {
		events, err := q.ListModelEvents(ctx, canonicaldb.ListModelEventsParams{
			UserID: userID, PersonalModelID: c.modelID, Limit: 10,
		})
		if err != nil || len(events) != 1 {
			t.Fatalf("ListModelEvents = %d rows, err %v", len(events), err)
		}
		if !events[0].AggregateID.Valid || events[0].AggregateID.String != c.patternID {
			t.Errorf("event aggregate_id = %+v, want %s", events[0], c.patternID)
		}
	})

	t.Run("what_happened_after_the_intervention", func(t *testing.T) {
		outs, err := q.ListOutcomesForUser(ctx, canonicaldb.ListOutcomesForUserParams{
			UserID: userID, Limit: 10,
		})
		if err != nil || len(outs) != 1 {
			t.Fatalf("ListOutcomesForUser = %d rows, err %v", len(outs), err)
		}
		if outs[0].OutcomeType != "goal_progress" || outs[0].Origin != "user_statement" {
			t.Errorf("outcome = %+v", outs[0])
		}
		links, err := q.ListOutcomeLinks(ctx, canonicaldb.ListOutcomeLinksParams{
			UserID: userID, OutcomeID: c.outcomeID,
		})
		if err != nil || len(links) != 1 || links[0].TargetID != c.goalID {
			t.Fatalf("ListOutcomeLinks = %d rows, err %v", len(links), err)
		}
		filtered, err := q.ListOutcomesForUser(ctx, canonicaldb.ListOutcomesForUserParams{
			UserID: userID, Column2: "behavior_change", Limit: 10,
		})
		if err != nil || len(filtered) != 0 {
			t.Fatalf("outcome_type filter returned %d rows, want 0 (err %v)", len(filtered), err)
		}
	})

	t.Run("when_was_it_last_confirmed", func(t *testing.T) {
		fact, err := q.GetFact(ctx, canonicaldb.GetFactParams{ID: c.factID, UserID: userID})
		if err != nil {
			t.Fatalf("GetFact: %v", err)
		}
		if fact.Status != "active" || !fact.Confidence.Valid {
			t.Fatalf("gate promotion not visible: %+v", fact)
		}
	})
}

func TestCanonicalOptimisticConcurrency(t *testing.T) {
	pool := poolForTest(t)
	q := canonicaldb.New(pool)
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })

	ctx := context.Background()
	modelID := newID()
	if err := q.InsertPersonalModel(ctx, canonicaldb.InsertPersonalModelParams{
		ID: modelID, UserID: userID,
	}); err != nil {
		t.Fatalf("InsertPersonalModel: %v", err)
	}

	got, err := q.BumpPersonalModelVersion(ctx, canonicaldb.BumpPersonalModelVersionParams{
		ID: modelID, CurrentVersion: 0,
	})
	if err != nil {
		t.Fatalf("first bump: %v", err)
	}
	if got != 1 {
		t.Fatalf("current_version = %d, want 1", got)
	}

	// 02 §32: a stale writer must get "0 rows", not an error and not a blocking lock.
	_, err = q.BumpPersonalModelVersion(ctx, canonicaldb.BumpPersonalModelVersionParams{
		ID: modelID, CurrentVersion: 0,
	})
	if !errors.Is(err, pgx.ErrNoRows) {
		t.Fatalf("stale bump err = %v, want pgx.ErrNoRows (version conflict)", err)
	}

	model, err := q.GetPersonalModel(ctx, userID)
	if err != nil {
		t.Fatalf("GetPersonalModel: %v", err)
	}
	if model.CurrentVersion != 1 {
		t.Fatalf("current_version = %d, want 1 (conflicting write must not apply)", model.CurrentVersion)
	}

	if err := q.InsertPersonalModelVersion(ctx, canonicaldb.InsertPersonalModelVersionParams{
		ID: newID(), UserID: userID, PersonalModelID: modelID, Version: 1,
		Reason: "pattern_supported", SourceEventID: nullText(),
	}); err != nil {
		t.Fatalf("InsertPersonalModelVersion: %v", err)
	}
	if err := q.InsertPersonalModelVersion(ctx, canonicaldb.InsertPersonalModelVersionParams{
		ID: newID(), UserID: userID, PersonalModelID: modelID, Version: 1,
		Reason: "rebuild", SourceEventID: nullText(),
	}); err == nil {
		t.Fatal("duplicate (personal_model_id, version) accepted; 02 §6 requires UNIQUE")
	}
}

func TestCanonicalReadsAreUserScoped(t *testing.T) {
	pool := poolForTest(t)
	q := canonicaldb.New(pool)
	owner, attacker := newID(), newID()
	t.Cleanup(func() {
		cleanupCanonical(t, pool, owner)
		cleanupCanonical(t, pool, attacker)
	})

	c := plantChain(t, q, owner)
	ctx := context.Background()

	// Every single-row read carries user_id in its WHERE, so another user's
	// primary key is not a capability (01 §22 invariant 7 / 架构方案 §六-3).
	if _, err := q.GetFact(ctx, canonicaldb.GetFactParams{ID: c.factID, UserID: attacker}); !errors.Is(err, pgx.ErrNoRows) {
		t.Errorf("GetFact as another user: err = %v, want ErrNoRows", err)
	}
	if _, err := q.GetInsight(ctx, canonicaldb.GetInsightParams{ID: c.insightID, UserID: attacker}); !errors.Is(err, pgx.ErrNoRows) {
		t.Errorf("GetInsight as another user: err = %v", err)
	}
	if _, err := q.GetPatternForUpdate(ctx, canonicaldb.GetPatternForUpdateParams{ID: c.patternID, UserID: attacker}); !errors.Is(err, pgx.ErrNoRows) {
		t.Errorf("GetPatternForUpdate as another user: err = %v", err)
	}
	if _, err := q.GetSourceRecord(ctx, canonicaldb.GetSourceRecordParams{ID: c.sourceID, UserID: attacker}); !errors.Is(err, pgx.ErrNoRows) {
		t.Errorf("GetSourceRecord as another user: err = %v", err)
	}

	foreign, err := q.ListActiveFacts(ctx, canonicaldb.ListActiveFactsParams{
		UserID: attacker, PersonalModelID: c.modelID,
	})
	if err != nil {
		t.Fatalf("ListActiveFacts: %v", err)
	}
	if len(foreign) != 0 {
		t.Errorf("cross-user fact list = %d rows, want 0", len(foreign))
	}

	// The owner still reads its own rows: proves the scoping is not just an
	// accidentally-empty query.
	own, err := q.ListActiveFacts(ctx, canonicaldb.ListActiveFactsParams{
		UserID: owner, PersonalModelID: c.modelID,
	})
	if err != nil || len(own) != 1 {
		t.Errorf("owner read = %d rows, err %v", len(own), err)
	}
}

// TestCanonicalIdempotency pins the three dedup points N2 leaves in the schema:
// D-2's thin source registry, 08 §3's pattern fingerprint, and 18 §4's inbox key.
func TestCanonicalIdempotency(t *testing.T) {
	pool := poolForTest(t)
	q := canonicaldb.New(pool)
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })
	ctx := context.Background()

	if err := q.InsertPersonalModel(ctx, canonicaldb.InsertPersonalModelParams{
		ID: newID(), UserID: userID,
	}); err != nil {
		t.Fatalf("InsertPersonalModel: %v", err)
	}
	legacyRow := newID()
	params := canonicaldb.InsertSourceRecordParams{
		ID: newID(), UserID: userID, SourceType: "chat_message",
		SourceTable: "chat_messages", SourceID: legacyRow, RevisionOf: nullText(),
	}
	first, err := q.InsertSourceRecord(ctx, params)
	if err != nil {
		t.Fatalf("first registration: %v", err)
	}
	params.ID = newID()
	second, err := q.InsertSourceRecord(ctx, params)
	if err != nil {
		t.Fatalf("re-registration must reuse the row: %v", err)
	}
	if first != second {
		t.Fatalf("duplicate registry rows %s / %s", first, second)
	}

	// patterns fingerprint: UNIQUE (user_id, fingerprint) WHERE fingerprint IS NOT NULL
	model, err := q.GetPersonalModel(ctx, userID)
	if err != nil {
		t.Fatalf("GetPersonalModel: %v", err)
	}
	modelID := model.ID
	insert := func(id string) error {
		return q.InsertPattern(ctx, canonicaldb.InsertPatternParams{
			ID: id, UserID: userID, PersonalModelID: modelID,
			PatternType: "mood_work", SubjectKey: txt("user"), Scope: txt("work"),
			Description: "same detection re-run", Status: "candidate",
			Confidence: num(t, 0.5), EvidenceCount: 0,
			DetectorVersion: txt("v1"), Fingerprint: txt("fingerprint-a"),
			ScoreDetail: nullText(), ModelVersion: pgtype.Int8{},
			GenerationRunID: nullText(), RejectReason: nullText(),
			ObservedFrom: pgtype.Timestamptz{}, ObservedTo: pgtype.Timestamptz{},
		})
	}
	if err := insert(newID()); err != nil {
		t.Fatalf("first pattern: %v", err)
	}
	if err := insert(newID()); err == nil {
		t.Fatal("second pattern with the same fingerprint accepted (18 §11)")
	}
	found, err := q.FindPatternByFingerprint(ctx, canonicaldb.FindPatternByFingerprintParams{
		UserID: userID, Fingerprint: txt("fingerprint-a"),
	})
	if err != nil || found.Status != "candidate" {
		t.Fatalf("FindPatternByFingerprint: %+v err %v", found, err)
	}

	// inbox_messages UNIQUE (message_id, consumer): the second insert returns no row.
	msgID := "msg-" + userID
	inboxID, err := q.InsertInboxMessage(ctx, canonicaldb.InsertInboxMessageParams{
		ID: newID(), MessageID: msgID, MessageType: "insight.draft", Consumer: "python-ai",
	})
	if err != nil {
		t.Fatalf("first inbox insert: %v", err)
	}
	if inboxID == "" {
		t.Fatal("first inbox insert returned no id")
	}
	dup, err := q.InsertInboxMessage(ctx, canonicaldb.InsertInboxMessageParams{
		ID: newID(), MessageID: msgID, MessageType: "insight.draft", Consumer: "python-ai",
	})
	if !errors.Is(err, pgx.ErrNoRows) {
		t.Fatalf("duplicate inbox delivery err = %v, want ErrNoRows (18 §4)", err)
	}
	_ = dup
	other, err := q.InsertInboxMessage(ctx, canonicaldb.InsertInboxMessageParams{
		ID: newID(), MessageID: msgID, MessageType: "insight.draft", Consumer: "go-audit",
	})
	if err != nil || other == "" {
		t.Fatalf("same message, second consumer must be its own row: err %v", err)
	}

	t.Cleanup(func() {
		pool.Exec(ctx, `DELETE FROM inbox_messages WHERE message_id = $1`, msgID)
	})
}

// TestCanonicalDeliveryLifecycle pins 08 §7 MarkProactiveDelivered: the same
// proactive must not become "sent" twice, and silence must expire it (04 §19).
func TestCanonicalDeliveryLifecycle(t *testing.T) {
	pool := poolForTest(t)
	q := canonicaldb.New(pool)
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })
	ctx := context.Background()

	c := plantChain(t, q, userID)

	if _, err := q.MarkProactiveDelivered(ctx, canonicaldb.MarkProactiveDeliveredParams{
		ID: c.proactiveID, UserID: userID,
	}); err != nil {
		t.Fatalf("first delivery: %v", err)
	}
	if _, err := q.MarkProactiveDelivered(ctx, canonicaldb.MarkProactiveDeliveredParams{
		ID: c.proactiveID, UserID: userID,
	}); !errors.Is(err, pgx.ErrNoRows) {
		t.Fatalf("second delivery err = %v, want ErrNoRows", err)
	}
	pc, err := q.GetProactive(ctx, canonicaldb.GetProactiveParams{ID: c.proactiveID, UserID: userID})
	if err != nil || pc.Status != "sent" || !pc.SentAt.Valid {
		t.Fatalf("proactive after delivery = %+v err %v", pc, err)
	}
	if _, err := q.ExpireProactive(ctx, canonicaldb.ExpireProactiveParams{
		ID: c.proactiveID, UserID: userID,
	}); !errors.Is(err, pgx.ErrNoRows) {
		t.Fatalf("expiring a sent proactive err = %v, want ErrNoRows", err)
	}
	if _, err := q.BlockProactive(ctx, canonicaldb.BlockProactiveParams{
		ID: c.proactiveID, UserID: userID, BlockReason: txt("safety_hit"),
	}); err != nil {
		t.Fatalf("BlockProactive: %v", err)
	}

	if _, err := q.CompleteGoal(ctx, canonicaldb.CompleteGoalParams{
		ID: c.goalID, UserID: userID,
	}); err != nil {
		t.Fatalf("CompleteGoal: %v", err)
	}
	goals, err := q.ListGoals(ctx, canonicaldb.ListGoalsParams{
		UserID: userID, PersonalModelID: c.modelID, Status: "completed",
	})
	if err != nil || len(goals) != 1 || !goals[0].CompletedAt.Valid {
		t.Fatalf("completed goals = %+v err %v", goals, err)
	}
}
