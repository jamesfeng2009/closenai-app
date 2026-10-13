// N4 算分与聚合口径的单测（V3 03 §5-§7）。
//
// 这一批断言的重点不是「分数好不好看」，而是三条可核实的性质：
//  1. 输入档位只能取枚举值，未知档位一律报错（01 §25 不允许静默兜底）；
//  2. 默认口径（geomean）与 V3 字面口径（product）在同一批样本上的差异必须显式存在，
//     否则「标定」无从谈起；
//  3. 独立计数与冲突判定按 §6/§7 的规则去重，不认调用方给的数字。
package domain

import (
	"math"
	"strings"
	"testing"
	"time"
)

func TestScoreEvidence_TableDriven(t *testing.T) {
	p := DefaultEvidencePolicy()
	now := time.Date(2026, 10, 13, 12, 0, 0, 0, time.UTC)
	fresh := now.Add(-2 * time.Hour)

	cases := []struct {
		name  string
		s     EvidenceSignals
		want  float64
		class EvidenceClass
	}{
		{"用户明说+明确+具体+单次",
			EvidenceSignals{SourceKind: SourceUserStatement, Explicitness: Explicit,
				Specificity: Specific, Independent: 1, ObservedAt: fresh},
			// geomean(1.00,1.00,1.00,0.60,1.00) = 0.60^(1/5) ≈ 0.9029
			0.9029, ClassA},
		{"AI 从明确文本抽取+具体+单次：V3 字面连乘落 C，geomean 落 A",
			EvidenceSignals{SourceKind: SourceAIExtraction, Explicitness: Explicit,
				Specificity: Specific, Independent: 1, ObservedAt: fresh},
			// product = 0.85*1*1*0.6*1 = 0.51；geomean = 0.51^(1/5) ≈ 0.8740
			0.8740, ClassA},
		{"AI 语义推断+弱暗示：派生出来的东西不该和明说同分",
			EvidenceSignals{SourceKind: SourceAIInference, Explicitness: WeaklyImplied,
				Specificity: Contextual, Independent: 1, ObservedAt: fresh},
			// product = 0.55*0.45*0.70*0.6*1 = 0.103905；geomean ≈ 0.6359
			0.6359, ClassC},
		{"框架推断单次：再取几何平均也仍是 D（03:126 不得独立晋升）",
			EvidenceSignals{SourceKind: SourceFrameworkInference, Explicitness: Speculative,
				Specificity: Generic, Independent: 1, ObservedAt: fresh},
			// product = 0.20*0.10*0.40*0.6*1 = 0.0048；geomean ≈ 0.3438
			0.3438, ClassD},
		{"独立重复抬分：同一条 4 次独立观测高于 1 次",
			EvidenceSignals{SourceKind: SourceAIExtraction, Explicitness: Explicit,
				Specificity: Specific, Independent: 4, ObservedAt: fresh},
			// repetition = 0.60+3*0.15 = 1.05 → 封顶 1.00；geomean(0.85,1,1,1,1) ≈ 0.9680
			0.9680, ClassA},
		{"91-180 天的旧证据按 §5 衰减档打折",
			EvidenceSignals{SourceKind: SourceUserBehavior, Explicitness: Explicit,
				Specificity: Specific, Independent: 2, ObservedAt: now.Add(-120 * 24 * time.Hour)},
			// 0.90,1,1,0.75,0.65 → product 0.438750 → geomean ≈ 0.8481
			0.8481, ClassB},
		{"用户明说的稳定性事实不衰减（03:147 NoDecay）",
			EvidenceSignals{SourceKind: SourceUserStatement, Explicitness: Explicit,
				Specificity: Specific, Independent: 1, NoDecay: true,
				ObservedAt: now.Add(-400 * 24 * time.Hour)},
			0.9029, ClassA},
		{"没给观测时刻按当下（不衰减）",
			EvidenceSignals{SourceKind: SourceUserStatement, Explicitness: Explicit,
				Specificity: Specific, Independent: 1},
			0.9029, ClassA},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := ScoreEvidence(tc.s, p, now)
			if err != nil {
				t.Fatalf("ScoreEvidence: %v", err)
			}
			if math.Abs(got.Strength-tc.want) > 0.0001 {
				t.Errorf("strength=%.4f，期望 %.4f（%s）", got.Strength, tc.want, got.Detail)
			}
			if got.Class != tc.class {
				t.Errorf("class=%s，期望 %s（strength=%.4f）", got.Class, tc.class, got.Strength)
			}
			if len(got.Components) != 5 {
				t.Errorf("components=%v，五因子必须逐个留痕（02 §37「Why do we know it」）", got.Components)
			}
		})
	}
}

func TestScoreEvidence_ProductModeKeepsLiteralSemantics(t *testing.T) {
	p := DefaultEvidencePolicy()
	p.ScoreMode = scoreModeProduct
	now := time.Date(2026, 10, 13, 12, 0, 0, 0, time.UTC)
	got, err := ScoreEvidence(EvidenceSignals{
		SourceKind: SourceAIExtraction, Explicitness: Explicit, Specificity: Specific,
		Independent: 1, ObservedAt: now.Add(-time.Hour),
	}, p, now)
	if err != nil {
		t.Fatalf("ScoreEvidence: %v", err)
	}
	// 03 §5 的字面连乘结果：这就是 tasks N4:487 说的「小数域压到 C 级」，
	// 保留 product 模式是为了标定脚本能量化这条偏离，不是留着当默认。
	if math.Abs(got.Strength-0.5100) > 0.0001 {
		t.Errorf("product 模式 strength=%.4f，期望 0.5100", got.Strength)
	}
	if got.Class != ClassC {
		t.Errorf("product 模式 class=%s，期望 C（偏离必须看得见）", got.Class)
	}
	if !strings.Contains(got.Detail, "mode=product") {
		t.Errorf("detail=%q，必须标出口径", got.Detail)
	}
}

func TestScoreEvidence_RejectsUnknownSignals(t *testing.T) {
	p := DefaultEvidencePolicy()
	now := time.Date(2026, 10, 13, 12, 0, 0, 0, time.UTC)
	cases := []struct {
		name string
		s    EvidenceSignals
	}{
		{"未知来源", EvidenceSignals{SourceKind: SourceKind("telepathy"),
			Explicitness: Explicit, Specificity: Specific, Independent: 1}},
		{"未知明确度", EvidenceSignals{SourceKind: SourceUserStatement,
			Explicitness: Explicitness("half_said"), Specificity: Specific, Independent: 1}},
		{"未知具体度", EvidenceSignals{SourceKind: SourceUserStatement,
			Explicitness: Explicit, Specificity: Specificity("vibes"), Independent: 1}},
		{"未来时刻", EvidenceSignals{SourceKind: SourceUserStatement,
			Explicitness: Explicit, Specificity: Specific, Independent: 1,
			ObservedAt: now.Add(time.Hour)}},
		{"负独立数", EvidenceSignals{SourceKind: SourceUserStatement,
			Explicitness: Explicit, Specificity: Specific, Independent: -1}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := ScoreEvidence(tc.s, p, now); err == nil {
				t.Error("期望报错，静默按 0 或 1 兜底等于把畸形产出放进来")
			}
		})
	}
}

func TestEvidencePolicy_ClassOf(t *testing.T) {
	p := DefaultEvidencePolicy()
	for _, tc := range []struct {
		strength float64
		want     EvidenceClass
	}{
		{1.00, ClassA}, {0.85, ClassA}, {0.8499, ClassB}, {0.65, ClassB},
		{0.6499, ClassC}, {0.45, ClassC}, {0.4499, ClassD}, {0, ClassD},
	} {
		if got := p.ClassOf(tc.strength); got != tc.want {
			t.Errorf("ClassOf(%.4f)=%s，期望 %s（03 §6:152-156 的边界含端点）",
				tc.strength, got, tc.want)
		}
	}
}

func TestEvidencePolicy_Combined(t *testing.T) {
	p := DefaultEvidencePolicy()
	day1 := time.Date(2026, 10, 1, 9, 0, 0, 0, time.UTC)
	day2 := time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC)

	t.Run("同源多条只算一次", func(t *testing.T) {
		rows := []EvidenceRow{
			{ID: "a", UserID: owner, Strength: 0.9, HasStrength: true, Role: "supporting", SourceID: "s1", ObservedAt: day1},
			{ID: "b", UserID: owner, Strength: 0.8, HasStrength: true, Role: "supporting", SourceID: "s1", ObservedAt: day2},
		}
		independent, combined, conflict := p.Combined(rows)
		if independent != 1 || conflict {
			t.Errorf("independent=%d conflict=%t，同一 source_record 不得计独立重复（03 §6:161-163）",
				independent, conflict)
		}
		if combined != 0.9 {
			t.Errorf("combined=%.4f，去重后只剩第一条", combined)
		}
	})
	t.Run("同日不同源也只算一次", func(t *testing.T) {
		rows := []EvidenceRow{
			{ID: "a", UserID: owner, Strength: 0.9, HasStrength: true, Role: "supporting", SourceID: "s1", ObservedAt: day1},
			{ID: "b", UserID: owner, Strength: 0.7, HasStrength: true, Role: "supporting", SourceID: "s2",
				ObservedAt: day1.Add(3 * time.Hour)},
		}
		independent, _, _ := p.Combined(rows)
		if independent != 1 {
			t.Errorf("independent=%d，同一自然日的多条只算一次", independent)
		}
	})
	t.Run("contradicting 不参与平均，也不得被平均掉", func(t *testing.T) {
		rows := []EvidenceRow{
			{ID: "a", UserID: owner, Strength: 0.9, HasStrength: true, Role: "supporting", SourceID: "s1", ObservedAt: day1},
			{ID: "b", UserID: owner, Strength: 0.8, HasStrength: true, Role: "supporting", SourceID: "s2", ObservedAt: day2},
			{ID: "c", UserID: owner, Strength: 0.95, HasStrength: true, Role: "contradicting", SourceID: "s3", ObservedAt: day2},
		}
		independent, combined, conflict := p.Combined(rows)
		if independent != 2 {
			t.Errorf("independent=%d，反对证据不得计入支撑数", independent)
		}
		if math.Abs(combined-0.85) > 0.0001 {
			t.Errorf("combined=%.4f，期望只取 supporting 均值 0.85（03 §7:186-187）", combined)
		}
		if !conflict {
			t.Error("反对证据 0.95 ≥ 最强支撑 0.90，必须判冲突未解")
		}
	})
	t.Run("反对证据更弱时不算冲突", func(t *testing.T) {
		rows := []EvidenceRow{
			{ID: "a", UserID: owner, Strength: 0.9, HasStrength: true, Role: "supporting", SourceID: "s1", ObservedAt: day1},
			{ID: "c", UserID: owner, Strength: 0.30, HasStrength: true, Role: "contradicting", SourceID: "s3", ObservedAt: day2},
		}
		if _, _, conflict := p.Combined(rows); conflict {
			t.Error("弱得多的反对证据不该判成冲突未解")
		}
	})
	t.Run("未算分的行不参与", func(t *testing.T) {
		rows := []EvidenceRow{{ID: "a", UserID: owner, Role: "supporting", SourceID: "s1", ObservedAt: day1}}
		independent, combined, _ := p.Combined(rows)
		if independent != 0 || combined != 0 {
			t.Errorf("independent=%d combined=%.4f，strength 为 NULL 的行不能当输入",
				independent, combined)
		}
	})
}
