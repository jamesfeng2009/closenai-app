// N3/N4 Domain Gate 九关的单测：每种拒绝都必须落在**指定的那一关**并给出 G-1 词表里的
// 机器可读码。校验序本身是契约（G-3 的七关是 N4 九关的前缀，序按 V3 03 §14:361-371 原序）
// ——多关同时不过时落哪一关决定 golden 能否复现，所以这里除了断言 reason，
// 还断言 Outcome.Checks（= 被拦之前已通过的关）。
package domain

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/idgen"
)

// allChecks 是九关的定序，逐字对齐 Gate.Check：schema → evidence → temporal →
// claim_type → confidence → safety → language → duplicate → user_history（03 §14:361-371）。
var allChecks = []string{"schema", "evidence", "temporal", "claim_type",
	"confidence", "safety", "language", "duplicate", "user_history"}

type stubReader struct {
	evidence       map[string]Evidence
	patternHit     string
	factHit        string
	loadErr        error
	patternErr     error
	findFactErr    error
	rejectionHit   string
	rejectionErr   error
	loadedIDs      []string
	lookedFingerp  string
	cooldownUsed   int
	cooldownLooked string
}

func (r *stubReader) LoadEvidence(ctx context.Context, ids []string) ([]Evidence, error) {
	if r.loadErr != nil {
		return nil, r.loadErr
	}
	r.loadedIDs = append([]string(nil), ids...)
	out := make([]Evidence, 0, len(ids))
	for _, id := range ids {
		if row, ok := r.evidence[id]; ok {
			out = append(out, row)
		}
	}
	return out, nil
}

func (r *stubReader) FindPatternByFingerprint(ctx context.Context, userID, fingerprint string) (string, bool, error) {
	if r.patternErr != nil {
		return "", false, r.patternErr
	}
	r.lookedFingerp = fingerprint
	if r.patternHit == "" {
		return "", false, nil
	}
	return r.patternHit, true, nil
}

func (r *stubReader) FindLiveFactKey(ctx context.Context, userID, subjectKey, predicate, objectValue string) (string, bool, error) {
	if r.findFactErr != nil {
		return "", false, r.findFactErr
	}
	if r.factHit == "" {
		return "", false, nil
	}
	return r.factHit, true, nil
}

func (r *stubReader) RecentUserRejection(ctx context.Context, userID,
	fingerprint string, withinDays int) (string, bool, error) {
	if r.rejectionErr != nil {
		return "", false, r.rejectionErr
	}
	r.cooldownLooked = fingerprint
	r.cooldownUsed = withinDays
	if r.rejectionHit == "" {
		return "", false, nil
	}
	return r.rejectionHit, true, nil
}

// newReader 的证据行是**已算分、互相独立**的支持性证据：N4 起派生 claim 需要
// ≥2 条已算分且不同源不同日的证据（03 §6:161-163、§7:175-181），
// 光有 id 的桩在 trend/recurrence 这类句子上会被证据关拦下。
func newReader() *stubReader {
	return &stubReader{evidence: map[string]Evidence{
		"e1": {ID: "e1", UserID: owner, Strength: 0.90, HasStrength: true,
			Role: "supporting", SourceID: "s1", ObservedAt: windowStart},
		"e2": {ID: "e2", UserID: owner, Strength: 0.88, HasStrength: true,
			Role: "supporting", SourceID: "s2", ObservedAt: windowEnd},
		"e3": {ID: "e3", UserID: "other-user", Strength: 0.90, HasStrength: true,
			Role: "supporting", SourceID: "s3", ObservedAt: windowStart},
	}}
}

const owner = "11111111-1111-4111-8111-111111111111"

var windowStart = time.Date(2026, 9, 20, 8, 0, 0, 0, time.UTC)
var windowEnd = time.Date(2026, 10, 12, 20, 0, 0, 0, time.UTC)

func okFact() Candidate {
	return Candidate{
		UserID: owner, Kind: KindFact, Origin: "repeated",
		SubjectKey: "sleep", Predicate: "schedule", ObjectValue: "晚睡",
		Confidence: 0.8, EvidenceIDs: []string{"e1"},
		WindowTo: windowEnd, HasWindow: true,
		GenerationRunID: "run-1",
		Safety:          &Safety{Status: SafetyOK},
	}
}

func okPattern() Candidate {
	return Candidate{
		UserID: owner, Kind: KindPattern, PatternType: "temporal_rhythm",
		SubjectKey: "sleep", Scope: "context:work", Description: "工作日的记录集中在 23 点后",
		Fingerprint: "fp-sleep-night", DetectorVersion: "d-1",
		Confidence: 0.6, EvidenceIDs: []string{"e1", "e2"},
		WindowFrom: windowStart, WindowTo: windowEnd, HasWindow: true,
		GenerationRunID: "run-1",
		Safety:          &Safety{Status: SafetyOK},
	}
}

func okInsight() Candidate {
	return Candidate{
		UserID: owner, Kind: KindInsight, ClaimType: "trend",
		Statement:  "过去三周里，工作日的记录时间点后移了。",
		Confidence: 0.82, EvidenceIDs: []string{"e1", "e2"},
		PatternIDs: []string{"p1"},
		WindowFrom: windowStart, WindowTo: windowEnd, HasWindow: true,
		GenerationModel: "gpt-x", GenerationVer: "v1", GenerationRunID: "run-1",
		Safety: &Safety{Status: SafetyOK},
	}
}

func gateFor() Gate { return Gate{Policy: DefaultPolicy()} }

func run(t *testing.T, c Candidate, r *stubReader) Outcome {
	t.Helper()
	out, err := gateFor().Check(context.Background(), &c, r)
	if err != nil {
		t.Fatalf("Gate.Check: %v", err)
	}
	return out
}

func TestGate_AcceptsHappyPath(t *testing.T) {
	for _, tc := range []struct {
		name string
		c    Candidate
	}{
		{"fact", okFact()},
		{"pattern", okPattern()},
		{"insight", okInsight()},
	} {
		t.Run(tc.name, func(t *testing.T) {
			out := run(t, tc.c, newReader())
			if out.Action != ActionInsert {
				t.Fatalf("Action=%q reason=%q detail=%q，期望 insert", out.Action, out.Reason, out.Detail)
			}
			if strings.Join(out.Checks, ",") != strings.Join(allChecks, ",") {
				t.Errorf("Checks=%v，期望七关全绿 %v", out.Checks, allChecks)
			}
		})
	}
}

func TestGate_Schema(t *testing.T) {
	cases := []struct {
		name   string
		mutate func(*Candidate)
	}{
		{"kind 缺失", func(c *Candidate) { c.Kind = "" }},
		{"kind 未知", func(c *Candidate) { c.Kind = Kind("dream") }},
		{"user_id 缺失", func(c *Candidate) { c.UserID = "" }},
		{"generation_run_id 缺失", func(c *Candidate) { c.GenerationRunID = "" }},
		{"置信为负", func(c *Candidate) { c.Confidence = -0.01 }},
		{"置信大于 1", func(c *Candidate) { c.Confidence = 1.01 }},
		{"证据引用重复", func(c *Candidate) { c.EvidenceIDs = []string{"e1", "e1"} }},
		{"证据引用为空串", func(c *Candidate) { c.EvidenceIDs = []string{""} }},
		{"证据数量超上限", func(c *Candidate) {
			ids := make([]string, DefaultPolicy().MaxEvidencePerCandidate+1)
			for i := range ids {
				ids[i] = idgen.New()
			}
			c.EvidenceIDs = ids
		}},
		{"时间窗倒置", func(c *Candidate) { c.WindowFrom, c.WindowTo = windowEnd, windowStart }},
		{"subject_key 超列宽", func(c *Candidate) { c.SubjectKey = strings.Repeat("k", 129) }},
		{"predicate 超列宽", func(c *Candidate) { c.Predicate = strings.Repeat("p", 129) }},
		{"origin 不在六值集合", func(c *Candidate) { c.Origin = "guessed" }},
		{"fact 缺 predicate", func(c *Candidate) { c.Predicate = "" }},
		{"fact 缺 object_value", func(c *Candidate) { c.ObjectValue = "" }},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c := okFact()
			tc.mutate(&c)
			assertReject(t, run(t, c, newReader()), ReasonSchemaInvalid)
		})
	}

	t.Run("pattern 缺指纹", func(t *testing.T) {
		c := okPattern()
		c.Fingerprint = ""
		assertReject(t, run(t, c, newReader()), ReasonSchemaInvalid)
	})
	t.Run("pattern 缺描述", func(t *testing.T) {
		c := okPattern()
		c.Description = "   "
		assertReject(t, run(t, c, newReader()), ReasonSchemaInvalid)
	})
	t.Run("insight 缺陈述", func(t *testing.T) {
		c := okInsight()
		c.Statement = ""
		assertReject(t, run(t, c, newReader()), ReasonSchemaInvalid)
	})
	t.Run("scope 超列宽", func(t *testing.T) {
		c := okPattern()
		c.Scope = strings.Repeat("s", 65)
		assertReject(t, run(t, c, newReader()), ReasonSchemaInvalid)
	})
}

func TestGate_Evidence(t *testing.T) {
	t.Run("不带证据", func(t *testing.T) {
		c := okInsight()
		c.EvidenceIDs = nil
		assertReject(t, run(t, c, newReader()), ReasonEvidenceMissing)
	})
	t.Run("证据不存在", func(t *testing.T) {
		c := okInsight()
		c.EvidenceIDs = []string{"e1", "nope"}
		out := run(t, c, newReader())
		assertReject(t, out, ReasonEvidenceNotFound)
		if !strings.Contains(out.Detail, "nope") {
			t.Errorf("detail 未点出缺失 id：%q", out.Detail)
		}
	})
	t.Run("证据属于别人", func(t *testing.T) {
		c := okInsight()
		c.EvidenceIDs = []string{"e1", "e3"}
		out := run(t, c, newReader())
		assertReject(t, out, ReasonEvidenceNotOwned)
		if strings.Contains(out.Detail, "不存在") {
			t.Errorf("跨用户证据被误判成不存在：%q", out.Detail)
		}
	})
	t.Run("D 级证据只能拦 insight", func(t *testing.T) {
		weak := Evidence{ID: "e1", UserID: owner, Strength: 0.20, HasStrength: true}
		r := newReader()
		r.evidence["e1"] = weak
		assertReject(t, run(t, okInsight(), r), ReasonEvidenceBelowFloor)

		r2 := newReader()
		r2.evidence["e1"] = weak
		if out := run(t, okPattern(), r2); out.Action != ActionInsert {
			t.Errorf("pattern 被 D 级下限拦了（%s）：03:158-159 只禁「用户可见 claim」，pattern 不直接可见", out.Reason)
		}
	})
	t.Run("未算分的证据不触发 D 级下限", func(t *testing.T) {
		// observation 是对所引证据本身的复述，一条即可（03 §6 的 A/B 门槛只管派生 claim）。
		c := okInsight()
		c.ClaimType = "observation"
		c.EvidenceIDs = []string{"e1"}
		r := newReader()
		r.evidence["e1"] = Evidence{ID: "e1", UserID: owner} // HasStrength=false
		if out := run(t, c, r); out.Action != ActionInsert {
			t.Errorf("Action=%q reason=%q，未算分证据不该被判成 D 级", out.Action, out.Reason)
		}
	})
	t.Run("派生 claim 需 ≥2 条已算分独立证据", func(t *testing.T) {
		c := okInsight()
		c.ClaimType = "trend"
		c.EvidenceIDs = []string{"e1", "e2"}
		r := newReader()
		r.evidence["e1"] = Evidence{ID: "e1", UserID: owner} // 没算分 ⇒ 不参与计数
		out := run(t, c, r)
		assertReject(t, out, ReasonEvidenceBelowFloor)
		if !strings.Contains(out.Detail, "需 ≥2 条独立证据") {
			t.Errorf("detail 未点出独立性缺口：%q", out.Detail)
		}
	})
	t.Run("同源同日只算一次独立观测", func(t *testing.T) {
		c := okInsight()
		c.EvidenceIDs = []string{"e1", "e2"}
		r := newReader()
		// 两条都算分、都支持，但同 source_record ⇒ 去重后只剩 1 条（03 §6:161-163）。
		r.evidence["e2"] = Evidence{ID: "e2", UserID: owner, Strength: 0.9, HasStrength: true,
			Role: "supporting", SourceID: "s1", ObservedAt: windowStart}
		assertReject(t, run(t, c, r), ReasonEvidenceBelowFloor)
	})
	t.Run("contradicting 不弱于最强支撑时派生 claim 被拦", func(t *testing.T) {
		c := okInsight()
		c.EvidenceIDs = []string{"e1", "e2"}
		r := newReader()
		r.evidence["e2"] = Evidence{ID: "e2", UserID: owner, Strength: 0.95, HasStrength: true,
			Role: "contradicting", SourceID: "s2", ObservedAt: windowEnd}
		out := run(t, c, r)
		assertReject(t, out, ReasonEvidenceConflict)
		if !strings.Contains(out.Detail, "03 §7") {
			t.Errorf("detail 未标冲突出处：%q", out.Detail)
		}
	})
	t.Run("读库错误原样上抛", func(t *testing.T) {
		r := newReader()
		r.loadErr = errors.New("conn reset")
		if _, err := gateFor().Check(context.Background(), ptr(okInsight()), r); err == nil {
			t.Fatal("期望 DB 错误上抛，而不是转成 reject")
		}
	})
}

func TestGate_Confidence(t *testing.T) {
	p := DefaultPolicy()
	t.Run("普通 insight 低于 0.70", func(t *testing.T) {
		c := okInsight()
		c.Confidence = p.InsightConfidenceMin - 0.01
		assertReject(t, run(t, c, newReader()), ReasonConfidenceBelowThresh)
	})
	t.Run("敏感 insight 用更高门限", func(t *testing.T) {
		c := okInsight()
		c.Confidence = (p.InsightConfidenceMin + p.InsightSensitiveConfMin) / 2
		c.Safety = &Safety{Status: SafetyOK, Categories: []string{"self_harm"}}
		assertReject(t, run(t, c, newReader()), ReasonConfidenceBelowThresh)
	})
	t.Run("敏感 insight 达 0.85 放行", func(t *testing.T) {
		c := okInsight()
		c.Confidence = p.InsightSensitiveConfMin
		c.Safety = &Safety{Status: SafetyOK, Categories: []string{"self_harm"}}
		if out := run(t, c, newReader()); out.Action != ActionInsert {
			t.Errorf("Action=%q reason=%q，0.85 应过普通安全关", out.Action, out.Reason)
		}
	})
	t.Run("恰好等于下限算过", func(t *testing.T) {
		c := okInsight()
		c.Confidence = p.InsightConfidenceMin
		if out := run(t, c, newReader()); out.Action != ActionInsert {
			t.Errorf("Action=%q reason=%q", out.Action, out.Reason)
		}
	})
	t.Run("fact/pattern 默认无置信下限", func(t *testing.T) {
		f := okFact()
		f.Confidence = 0
		if out := run(t, f, newReader()); out.Action != ActionInsert {
			t.Errorf("fact: reason=%q，V3 未给 fact 置信下限，0 不得被拒", out.Reason)
		}
		ptn := okPattern()
		ptn.Confidence = 0
		if out := run(t, ptn, newReader()); out.Action != ActionInsert {
			t.Errorf("pattern: reason=%q，V3 未给 pattern 置信下限，0 不得被拒", out.Reason)
		}
	})
	t.Run("policy 覆盖生效", func(t *testing.T) {
		t.Setenv("CLOSEN_INSIGHT_MIN_CONFIDENCE", "0.95")
		c := okInsight()
		c.Confidence = 0.94
		assertReject(t, run(t, c, newReader()), ReasonConfidenceBelowThresh)
	})
}

func TestGate_ClaimType(t *testing.T) {
	for _, claim := range RestrictedClaimTypes {
		t.Run("受限 "+claim, func(t *testing.T) {
			c := okInsight()
			c.ClaimType = claim
			assertReject(t, run(t, c, newReader()), ReasonClaimTypeRestricted)
		})
	}
	for _, claim := range AllowedClaimTypes {
		t.Run("允许 "+claim, func(t *testing.T) {
			c := okInsight()
			c.ClaimType = claim
			if out := run(t, c, newReader()); out.Action != ActionInsert {
				t.Errorf("reason=%q，%s 属 03 §14 允许集合", out.Reason, claim)
			}
		})
	}
	t.Run("未知 claim", func(t *testing.T) {
		c := okInsight()
		c.ClaimType = "vibes"
		assertReject(t, run(t, c, newReader()), ReasonClaimTypeRestricted)
	})
	for _, patternType := range ForbiddenPatternTypes {
		t.Run("禁用 pattern_type "+patternType, func(t *testing.T) {
			c := okPattern()
			c.PatternType = patternType
			assertReject(t, run(t, c, newReader()), ReasonClaimTypeRestricted)
		})
	}
	t.Run("两条证据 + 因果动词", func(t *testing.T) {
		c := okInsight()
		c.Statement = "加班增多导致你的记录时间后移。"
		assertReject(t, run(t, c, newReader()), ReasonClaimTypeRestricted)
	})
	t.Run("两条证据 + 英文因果动词", func(t *testing.T) {
		c := okInsight()
		c.Statement = "Overtime caused later check-ins."
		assertReject(t, run(t, c, newReader()), ReasonClaimTypeRestricted)
	})
	t.Run("单条观察里的「是因为」不误杀", func(t *testing.T) {
		c := okInsight()
		c.ClaimType = "observation" // 单条证据只撑得起复述，派生 claim 的独立性门另测
		c.EvidenceIDs = []string{"e1"}
		c.Statement = "这条记录是因为你提到过项目截止。"
		if out := run(t, c, newReader()); out.Action != ActionInsert {
			t.Errorf("reason=%q：禁令原文是「禁用因果动词连接两条 Pattern」，单条观察不该被拦", out.Reason)
		}
	})
	t.Run("fact 的 object_value 也过因果闸", func(t *testing.T) {
		c := okFact()
		c.EvidenceIDs = []string{"e1", "e2"}
		c.ObjectValue = "加班导致晚睡"
		assertReject(t, run(t, c, newReader()), ReasonClaimTypeRestricted)
	})
}

func TestGate_Temporal(t *testing.T) {
	for _, term := range absoluteQuantifiers {
		t.Run("绝对量词 "+term, func(t *testing.T) {
			c := okInsight()
			c.Statement = "你 " + term + " 在深夜记录。"
			assertReject(t, run(t, c, newReader()), ReasonTemporalOverreach)
		})
	}
	t.Run("有界措辞放行", func(t *testing.T) {
		c := okInsight()
		c.Statement = "在过去三周的几条记录里，时间点后移了。"
		if out := run(t, c, newReader()); out.Action != ActionInsert {
			t.Errorf("reason=%q", out.Reason)
		}
	})
}

func TestGate_Safety(t *testing.T) {
	t.Run("判定缺失 fail-closed", func(t *testing.T) {
		c := okInsight()
		c.Safety = nil
		assertReject(t, run(t, c, newReader()), ReasonSafetyUnavailable)
	})
	t.Run("超时不可当放行", func(t *testing.T) {
		c := okInsight()
		c.Safety = &Safety{Status: SafetyUnavailable}
		assertReject(t, run(t, c, newReader()), ReasonSafetyUnavailable)
	})
	t.Run("危机硬覆盖", func(t *testing.T) {
		c := okInsight()
		// 0.90：危机类别会把 insight 归入敏感域（下限 0.85），置信关必须先过，
		// 否则按定序契约落在 confidence 而不是 safety。
		c.Confidence = 0.90
		c.Safety = &Safety{Status: SafetyCrisis, Categories: []string{"self_harm"}}
		out := run(t, c, newReader())
		assertReject(t, out, ReasonSafetyBlocked)
		if !strings.Contains(out.Detail, "self_harm") {
			t.Errorf("detail 未点出危机类别：%q", out.Detail)
		}
	})
	t.Run("未知安全态", func(t *testing.T) {
		c := okInsight()
		c.Safety = &Safety{Status: SafetyStatus("maybe")}
		assertReject(t, run(t, c, newReader()), ReasonSafetyUnavailable)
	})
}

func TestGate_Duplicate(t *testing.T) {
	t.Run("指纹命中改判 reactivate", func(t *testing.T) {
		r := newReader()
		r.patternHit = "existing-pattern"
		c := okPattern()
		out := run(t, c, r)
		if out.Action != ActionReactivate {
			t.Fatalf("Action=%q reason=%q，期望 reactivate", out.Action, out.Reason)
		}
		if out.DuplicateOfID != "existing-pattern" {
			t.Errorf("DuplicateOfID=%q", out.DuplicateOfID)
		}
		if r.lookedFingerp != c.Fingerprint {
			t.Errorf("查重用的指纹是 %q，期望 %q", r.lookedFingerp, c.Fingerprint)
		}
	})
	t.Run("同 key 活事实判重", func(t *testing.T) {
		r := newReader()
		r.factHit = "existing-fact"
		assertReject(t, run(t, okFact(), r), ReasonDuplicate)
	})
	t.Run("insight 无重复键故空转", func(t *testing.T) {
		// insights 表没有指纹列（N2 DDL），V3 也没给 insight 的重复键：
		// 不自造规则，这一关对 insight 只走 pattern/fact 两条支路。
		r := newReader()
		r.patternHit = "whatever"
		r.factHit = "whatever"
		if out := run(t, okInsight(), r); out.Action != ActionInsert {
			t.Errorf("Action=%q reason=%q", out.Action, out.Reason)
		}
	})
}

// 顺序契约：多关同时不过时，落在最靠前的那一关（03 §14:361-371 原序）。
func TestGate_OrderIsContract(t *testing.T) {
	t.Run("绝对量词+低置信 → temporal（不在 confidence 之后）", func(t *testing.T) {
		c := okInsight()
		c.Confidence = 0.10
		c.Statement = "你总是在深夜记录。"
		out := run(t, c, newReader())
		assertReject(t, out, ReasonTemporalOverreach)
		if strings.Join(out.Checks, ",") != "schema,evidence" {
			t.Errorf("Checks=%v，temporal 之前只应过 schema+evidence", out.Checks)
		}
	})
	t.Run("低置信+因果 → claim_type 在 confidence 之前", func(t *testing.T) {
		c := okInsight()
		c.Confidence = 0.10
		c.Statement = "加班增多导致记录时间后移。"
		c.EvidenceIDs = []string{"e1", "e2"}
		out := run(t, c, newReader())
		assertReject(t, out, ReasonClaimTypeRestricted)
		if strings.Join(out.Checks, ",") != "schema,evidence,temporal" {
			t.Errorf("Checks=%v", out.Checks)
		}
	})
	t.Run("低置信+危机 → confidence 先于 safety", func(t *testing.T) {
		c := okInsight()
		c.Confidence = 0.10
		c.Safety = &Safety{Status: SafetyCrisis, Categories: []string{"self_harm"}}
		out := run(t, c, newReader())
		assertReject(t, out, ReasonConfidenceBelowThresh)
		if strings.Join(out.Checks, ",") != "schema,evidence,temporal,claim_type" {
			t.Errorf("Checks=%v", out.Checks)
		}
	})
	t.Run("安全危机优先于查重与用户历史 → safety", func(t *testing.T) {
		c := okPattern()
		c.Safety = &Safety{Status: SafetyCrisis, Categories: []string{"self_harm"}}
		r := newReader()
		r.patternHit = "existing-pattern"
		r.rejectionHit = "existing-pattern"
		out := run(t, c, r)
		assertReject(t, out, ReasonSafetyBlocked)
		if contains(out.Checks, "duplicate") {
			t.Errorf("危机样本不应走到查重关：%v", out.Checks)
		}
		if r.cooldownLooked != "" {
			t.Errorf("危机样本不应查用户抑制签名：%v", out.Checks)
		}
	})
	t.Run("诊断措辞优先于查重 → language", func(t *testing.T) {
		c := okPattern()
		c.Description = "这是依恋类型的表现"
		r := newReader()
		r.patternHit = "existing-pattern"
		out := run(t, c, r)
		assertReject(t, out, ReasonLanguageOverreach)
		if strings.Join(out.Checks, ",") != "schema,evidence,temporal,claim_type,confidence,safety" {
			t.Errorf("Checks=%v", out.Checks)
		}
	})
	t.Run("schema 优先于证据缺失", func(t *testing.T) {
		c := okFact()
		c.EvidenceIDs = nil
		c.Origin = "guessed"
		out := run(t, c, newReader())
		assertReject(t, out, ReasonSchemaInvalid)
	})
}

func TestGate_Language(t *testing.T) {
	for _, term := range LabelTerms {
		t.Run("标签词 "+term, func(t *testing.T) {
			c := okInsight()
			c.ClaimType = "observation"
			c.Statement = "记录显示" + term + "的迹象。"
			assertReject(t, run(t, c, newReader()), ReasonLanguageOverreach)
		})
	}
	t.Run("敏感域必须留限定语", func(t *testing.T) {
		c := okInsight()
		c.Confidence = 0.95
		c.Statement = "你在深夜时段的记录占了一半。"
		c.Safety = &Safety{Status: SafetyOK, Categories: []string{"self_harm"}}
		out := run(t, c, newReader())
		assertReject(t, out, ReasonLanguageOverreach)
		if !strings.Contains(out.Detail, "限定语") {
			t.Errorf("detail 未点出缺的是限定语：%q", out.Detail)
		}
	})
	t.Run("敏感域带限定语放行", func(t *testing.T) {
		c := okInsight()
		c.Confidence = 0.95
		c.Statement = "过去三周里，我注意到记录时间点后移了。"
		c.Safety = &Safety{Status: SafetyOK, Categories: []string{"self_harm"}}
		if out := run(t, c, newReader()); out.Action != ActionInsert {
			t.Errorf("Action=%q reason=%q", out.Action, out.Reason)
		}
	})
	t.Run("非敏感域不强制 hedging", func(t *testing.T) {
		c := okInsight()
		c.Statement = "过去三周里，工作日的记录时间点后移了。"
		if out := run(t, c, newReader()); out.Action != ActionInsert {
			t.Errorf("Action=%q reason=%q：V3 只给敏感域 special policy", out.Action, out.Reason)
		}
	})
}

func TestGate_UserHistory(t *testing.T) {
	t.Run("冷却窗内的用户否决压制同指纹", func(t *testing.T) {
		c := okPattern()
		r := newReader()
		r.rejectionHit = "rejected-pattern"
		out := run(t, c, r)
		assertReject(t, out, ReasonSuppressedByUserHistory)
		if out.DuplicateOfID != "rejected-pattern" {
			t.Errorf("DuplicateOfID=%q，Service 要靠它决定不插行", out.DuplicateOfID)
		}
		if r.cooldownLooked != c.Fingerprint {
			t.Errorf("查抑制签名用的指纹是 %q，期望 %q", r.cooldownLooked, c.Fingerprint)
		}
		if want := DefaultPolicy().RejectionCooldownDays; r.cooldownUsed != want {
			t.Errorf("冷却窗传了 %d 天，期望 policy 的 %d 天", r.cooldownUsed, want)
		}
	})
	t.Run("reactivate 也要过这一关", func(t *testing.T) {
		c := okPattern()
		r := newReader()
		r.patternHit = "existing-pattern"
		r.rejectionHit = "existing-pattern"
		out := run(t, c, r)
		if out.Action != ActionReject || out.Reason != ReasonSuppressedByUserHistory {
			t.Fatalf("Action=%q reason=%q：用户否掉的指纹不得靠新证据悄悄复活",
				out.Action, out.Reason)
		}
	})
	t.Run("fact/insight 不走这一关", func(t *testing.T) {
		// insight 没有重复键（insights 表无指纹列），fact 的键不是指纹：
		// 这一关只按 pattern 的 fingerprint 查，其余 kind 显式空转。
		r := newReader()
		r.rejectionHit = "whatever"
		if out := run(t, okFact(), r); out.Action != ActionInsert {
			t.Errorf("fact: reason=%q", out.Reason)
		}
		if out := run(t, okInsight(), r); out.Action != ActionInsert {
			t.Errorf("insight: reason=%q", out.Reason)
		}
		if r.cooldownLooked != "" {
			t.Errorf("非 pattern 不该查抑制签名，查了 %q", r.cooldownLooked)
		}
	})
	t.Run("读库错误原样上抛", func(t *testing.T) {
		r := newReader()
		r.rejectionErr = errors.New("conn reset")
		if _, err := gateFor().Check(context.Background(), ptr(okPattern()), r); err == nil {
			t.Fatal("期望 DB 错误上抛，而不是转成 reject")
		}
	})
}

func assertReject(t *testing.T, out Outcome, want RejectReason) {
	t.Helper()
	if out.Action != ActionReject {
		t.Fatalf("Action=%q detail=%q，期望 reject(%s)", out.Action, out.Detail, want)
	}
	if out.Reason != want {
		t.Errorf("Reason=%q，期望 %q（detail=%q）", out.Reason, want, out.Detail)
	}
	if out.Detail == "" {
		t.Errorf("reject 必须带可解释 detail")
	}
	if !knownReason(out.Reason) {
		t.Errorf("Reason=%q 不在 G-1 词表里", out.Reason)
	}
}

func knownReason(r RejectReason) bool {
	for _, item := range RejectReasons {
		if item == r {
			return true
		}
	}
	return false
}

func ptr(c Candidate) *Candidate { return &c }
