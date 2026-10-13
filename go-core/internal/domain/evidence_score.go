package domain

import (
	"fmt"
	"math"
	"time"
)

// N4：Evidence 的确定性算分（V3 03 §4-§7）。
//
// 分工口径（不变量 7 + 03:108「LLM confidence is only one input; it is never the
// final Evidence score」）：Python 只能提案「这条观察是什么、来自哪种源、有多明确」，
// 分数一律由这里算，且算分输入必须是**可核实的**——独立重复次数不采信调用方给的数字，
// 由 Go 逐条核验既有 evidence 行的归属与日期后推出来（§6 的同源/同日不得计独立重复）。

// SourceKind 是 03 §5 baseline source reliability 表的八类来源（03:112-121）。
type SourceKind string

const (
	SourceUserStatement      SourceKind = "user_statement"
	SourceUserCorrection     SourceKind = "user_correction"
	SourceUserSelection      SourceKind = "user_selection"
	SourceUserBehavior       SourceKind = "user_behavior"
	SourceUserEmotion        SourceKind = "user_emotion"
	SourceAIExtraction       SourceKind = "ai_extraction"
	SourceAIInference        SourceKind = "ai_semantic_inference"
	SourceFrameworkInference SourceKind = "framework_inference"
)

// Explicitness 取 03 §5 的四档（03:128-133）。
type Explicitness string

const (
	Explicit        Explicitness = "explicit"
	StronglyImplied Explicitness = "strongly_implied"
	WeaklyImplied   Explicitness = "weakly_implied"
	Speculative     Explicitness = "speculative"
)

// Specificity 三档：V3 §5 点了名（×Specificity）却**没给数值表**，
// 数值属 G-5 口径的待标定默认值，env 可覆盖；档位语义按 03 §3 extraction 的
// key/value 形态定：specific＝落到具体行为/事件，contextual＝带情境的一般描述，
// generic＝套在任何人都成立的框架话。
type Specificity string

const (
	Specific   Specificity = "specific"
	Contextual Specificity = "contextual"
	Generic    Specificity = "generic"
)

// EvidenceClass 是 03 §6 的 A/B/C/D（03:152-156）。
type EvidenceClass string

const (
	ClassA EvidenceClass = "A"
	ClassB EvidenceClass = "B"
	ClassC EvidenceClass = "C"
	ClassD EvidenceClass = "D"
)

// EvidenceSignals 是一次算分的输入。全部字段都可核实或来自提案的枚举：
// 没有一项是「LLM 自信度」。
type EvidenceSignals struct {
	SourceKind   SourceKind
	Explicitness Explicitness
	Specificity  Specificity
	// Independent 是**已核实**的独立观测条数（含本条），由 Service 数出来，不是提案给的。
	Independent int
	// ObservedAt 是观测发生时刻，用于时间衰减。零值＝按当下算（不衰减）。
	ObservedAt time.Time
	// NoDecay 对应 03:147「Stable explicit Facts may disable time decay」。
	// 这里不采信调用方的布尔：只有 source_kind ∈ {user_statement, user_correction}
	// 且 explicitness=explicit 时才生效（用户明说/用户纠正的稳定性事实不随时间贬值）。
	NoDecay bool
}

// Scored 是算分结论。Components 落 evidence.metadata_json 之外的可解释面
// （02 §37「Why do we know it」要的不仅是分数，还要分数怎么来的）。
type Scored struct {
	Strength   float64
	Class      EvidenceClass
	Components map[string]float64
	Detail     string
}

// EvidencePolicy 是算分的数值面。默认值逐条标出处，env 覆盖（G-5）。
type EvidencePolicy struct {
	ScoreMode string // geomean | product

	ReliabilityStatement  float64
	ReliabilityCorrection float64
	ReliabilitySelection  float64
	ReliabilityBehavior   float64
	ReliabilityEmotion    float64
	ReliabilityExtraction float64
	ReliabilityInference  float64
	ReliabilityFramework  float64

	ExplicitnessExplicit      float64
	ExplicitnessStronglyImpl  float64
	ExplicitnessWeaklyImplied float64
	ExplicitnessSpeculative   float64

	SpecificitySpecific   float64
	SpecificityContextual float64
	SpecificityGeneric    float64

	RepetitionBase float64
	RepetitionStep float64

	Decay30  float64
	Decay90  float64
	Decay180 float64
	Decay365 float64
	DecayOld float64

	ClassAFloor float64
	ClassBFloor float64
	ClassCFloor float64

	// Fact 晋升（03 §7:175-181）：≥2 条独立观测 + combined evidence ≥0.75 才有 active 资格。
	FactMinIndependent int
	FactCombinedMin    float64

	// InsightMinIndependentForDerived：非 observation 的派生 claim 至少需要几条**已算分**
	// 的独立证据。V3 没给计数（§6 只说派生 claim 要 A/B 级支撑），这里按 §7 给 fact 的
	// 同形门拍成 2，env 可覆盖。
	InsightMinIndependentForDerived int

	// RepetitionWindowDays：两条独立观测落在同一自然日不算独立（§6:161-163）。
	RepetitionWindowDays int

	// InsightStaleAfterDays：观测窗口结束后隔多久判 expired（03 §11:309-310 的退化通道）。
	// V3 没给数（§17 只给了 periodic rebuild 的节奏），按 daily/weekly 重建口径外推成
	// 90 天，env 可覆盖后回测标定。
	InsightStaleAfterDays int
}

const (
	scoreModeGeomean = "geomean"
	scoreModeProduct = "product"
)

func (p EvidencePolicy) reliability(kind SourceKind) (float64, bool) {
	switch kind {
	case SourceUserStatement:
		return p.ReliabilityStatement, true
	case SourceUserCorrection:
		return p.ReliabilityCorrection, true
	case SourceUserSelection:
		return p.ReliabilitySelection, true
	case SourceUserBehavior:
		return p.ReliabilityBehavior, true
	case SourceUserEmotion:
		return p.ReliabilityEmotion, true
	case SourceAIExtraction:
		return p.ReliabilityExtraction, true
	case SourceAIInference:
		return p.ReliabilityInference, true
	case SourceFrameworkInference:
		return p.ReliabilityFramework, true
	default:
		return 0, false
	}
}

func (p EvidencePolicy) explicitnessFactor(e Explicitness) (float64, bool) {
	switch e {
	case Explicit:
		return p.ExplicitnessExplicit, true
	case StronglyImplied:
		return p.ExplicitnessStronglyImpl, true
	case WeaklyImplied:
		return p.ExplicitnessWeaklyImplied, true
	case Speculative:
		return p.ExplicitnessSpeculative, true
	default:
		return 0, false
	}
}

func (p EvidencePolicy) specificityFactor(s Specificity) (float64, bool) {
	switch s {
	case Specific:
		return p.SpecificitySpecific, true
	case Contextual:
		return p.SpecificityContextual, true
	case Generic:
		return p.SpecificityGeneric, true
	default:
		return 0, false
	}
}

// repetition：§5 的 Repetition 因子。V3 没给曲线，这里拍成「第一条打底 + 每多一条
// 独立观测加一档、封顶 1.0」，两个参数都在 EvidencePolicy 里（env 可覆盖）。
func (p EvidencePolicy) repetition(independent int) float64 {
	if independent < 1 {
		independent = 1
	}
	value := p.RepetitionBase + p.RepetitionStep*float64(independent-1)
	return math.Min(value, 1.0)
}

// temporalRelevance：§5 的衰减档（03:140-144），按天分档，边界含端点。
func (p EvidencePolicy) temporalRelevance(s EvidenceSignals, now time.Time) float64 {
	if s.NoDecay &&
		(s.SourceKind == SourceUserStatement || s.SourceKind == SourceUserCorrection) &&
		s.Explicitness == Explicit {
		return 1.0
	}
	if s.ObservedAt.IsZero() {
		return p.Decay30
	}
	age := now.Sub(s.ObservedAt).Hours() / 24
	switch {
	case age <= 30:
		return p.Decay30
	case age <= 90:
		return p.Decay90
	case age <= 180:
		return p.Decay180
	case age <= 365:
		return p.Decay365
	default:
		return p.DecayOld
	}
}

// ErrUnknownSignal 是枚举拼写的落点：Python 提案里出现未知档位一律拒算，
// 不静默按 0 或 1 处理（01 §25 LLM Failure Containment）。
var ErrUnknownSignal = fmt.Errorf("domain: 未知算分档位")

// ScoreEvidence 按 03 §5 的五因子算 strength，并按 §6 的分档给 A/B/C/D。
//
// ⚠️ 与 V3 原文的一处**必要偏离**（tasks N4:487「五项连乘在小数域会系统性压到 D 级」）：
// 五因子连乘的量纲塌缩是数学事实——03 §5 表里最「正常」的一档
// （AI 从明确文本抽取 0.85 × explicit 1.00 × specific 1.00 × 单次 0.60 × 30 天内 1.00）
// 连乘只有 0.51，落在 C 级，而 §6 说 C 只能探索、D 不得面向用户——
// 也就是说 V3 自己的默认值会把绝大多数正常证据判成 C/D。
// 这里把默认 ScoreMode 拍成 **几何平均**（连乘开 5 次方），单调性与连乘一致、
// 但保持 [0,1] 量纲；`CLOSEN_EVIDENCE_SCORE_MODE=product` 可随时切回 V3 字面口径回测。
func ScoreEvidence(s EvidenceSignals, p EvidencePolicy, now time.Time) (Scored, error) {
	reliability, ok := p.reliability(s.SourceKind)
	if !ok {
		return Scored{}, fmt.Errorf("%w: source_kind=%q", ErrUnknownSignal, s.SourceKind)
	}
	explicit, ok := p.explicitnessFactor(s.Explicitness)
	if !ok {
		return Scored{}, fmt.Errorf("%w: explicitness=%q", ErrUnknownSignal, s.Explicitness)
	}
	specific, ok := p.specificityFactor(s.Specificity)
	if !ok {
		return Scored{}, fmt.Errorf("%w: specificity=%q", ErrUnknownSignal, s.Specificity)
	}
	if s.Independent < 0 {
		return Scored{}, fmt.Errorf("domain: 独立观测数为负: %d", s.Independent)
	}
	if !s.ObservedAt.IsZero() && s.ObservedAt.After(now) {
		return Scored{}, fmt.Errorf("domain: 观测时刻 %s 晚于当下，不参与算分（时钟漂移或伪造）",
			s.ObservedAt.Format(time.RFC3339))
	}
	repetition := p.repetition(s.Independent)
	temporal := p.temporalRelevance(s, now)

	factors := []float64{reliability, explicit, specific, repetition, temporal}
	product := 1.0
	for _, f := range factors {
		product *= f
	}
	strength := product
	if p.ScoreMode != scoreModeProduct {
		strength = math.Pow(product, 1/float64(len(factors)))
	}
	strength = math.Round(strength*10000) / 10000

	class := ClassD
	switch {
	case strength >= p.ClassAFloor:
		class = ClassA
	case strength >= p.ClassBFloor:
		class = ClassB
	case strength >= p.ClassCFloor:
		class = ClassC
	}
	return Scored{
		Strength: strength,
		Class:    class,
		Components: map[string]float64{
			"source_reliability": reliability,
			"explicitness":       explicit,
			"specificity":        specific,
			"repetition":         repetition,
			"temporal_relevance": temporal,
		},
		Detail: fmt.Sprintf(
			"class=%s mode=%s independent=%d factors=%.4f×%.4f×%.4f×%.4f×%.4f=%.6f",
			class, modeOr(p.ScoreMode), s.Independent, reliability, explicit, specific,
			repetition, temporal, product),
	}, nil
}

func modeOr(mode string) string {
	if mode == "" {
		return scoreModeGeomean
	}
	return mode
}

// EvidenceRow 是聚合口径需要的证据投影（比 Gate 第 2 关的 Evidence 多几列）。
type EvidenceRow struct {
	ID          string
	UserID      string
	Strength    float64
	HasStrength bool
	Role        string
	SourceID    string
	ObservedAt  time.Time
}

// ClassOf 把已有行的 strength 归到 §6 的档；未算分（NULL）不参与。
func (p EvidencePolicy) ClassOf(strength float64) EvidenceClass {
	switch {
	case strength >= p.ClassAFloor:
		return ClassA
	case strength >= p.ClassBFloor:
		return ClassB
	case strength >= p.ClassCFloor:
		return ClassC
	default:
		return ClassD
	}
}

// Combined 是 03 §7:175-186 的「combined evidence」与冲突判定。
//
// 口径（V3 只给了词，没给式，此处拍定并登记）：
//   - 独立计数按 §6:161-163 去重：同一 source_record 只算一条，同一自然日只算一条。
//   - 合成分只取 supporting 的均值；**contradicting 不参与平均**（§7:186-187
//     「冲突证据必须表示为 supporting/contradicting，不得平均掉」）。
//   - 只要有一条 contradicting 的强度不低于 supporting 最大值，就算冲突未解：
//     不得晋升（Conflict=true）。这不是打分，是准入条件。
func (p EvidencePolicy) Combined(rows []EvidenceRow) (independent int, combined float64, conflict bool) {
	seenSource := map[string]bool{}
	seenDay := map[string]bool{}
	var supporting, contradicting []float64
	var maxSupport float64
	for _, row := range rows {
		if !row.HasStrength {
			// 未算分的行不参与：算分是这里的职责，不能拿别人的猜测当输入。
			continue
		}
		switch row.Role {
		case "supporting":
			// 「同源」与「同日」各自只算一次（03 §6:161-163 的 or 关系，不是组合键）：
			// 同一 source_record 的不同条目是同一件事的复述，同一天的不同来源是
			// 同一个情境，两者都撑不起「重复出现」。
			if seenSource[row.SourceID] || seenDay[dayKey(row.ObservedAt, p.RepetitionWindowDays)] {
				continue
			}
			seenSource[row.SourceID] = true
			seenDay[dayKey(row.ObservedAt, p.RepetitionWindowDays)] = true
			independent++
			supporting = append(supporting, row.Strength)
			if row.Strength > maxSupport {
				maxSupport = row.Strength
			}
		case "contradicting":
			contradicting = append(contradicting, row.Strength)
		}
	}
	if len(supporting) > 0 {
		sum := 0.0
		for _, v := range supporting {
			sum += v
		}
		combined = math.Round(sum/float64(len(supporting))*10000) / 10000
	}
	for _, v := range contradicting {
		if v >= maxSupport {
			conflict = true
			break
		}
	}
	return independent, combined, conflict
}

// dayKey 把观测时刻归到「第几个 RepetitionWindowDays 窗口」，
// 同一窗口内的多条只算一次独立观测。
func dayKey(t time.Time, windowDays int) string {
	if windowDays <= 0 {
		windowDays = 1
	}
	if t.IsZero() {
		return "-"
	}
	return fmt.Sprintf("%d", int(t.Unix()/(int64(windowDays)*24*3600)))
}
