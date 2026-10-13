// Package domain 是 canonical 个人模型状态的唯一写入口（V3 01 §12 不变量 7）。
//
// 分层口径（D-1/P5）：Python 只产 candidate，Go 判终局，PostgreSQL 记权威态。
// 本包不认识 HTTP，也不认识 RabbitMQ：handler 与 consumer 都只能调 Service 的方法，
// 不许直接 import internal/store/canonicaldb（这条由 arch_test.go 拍成可执行约束）。
package domain

import "time"

// Kind 是 candidate 的三种落库目标。V3 01 §11.1 只允许 AI 产出这三类提案
// （外加 evidence，那条是 N4 的算分入口）。
type Kind string

const (
	KindFact    Kind = "fact"
	KindPattern Kind = "pattern"
	KindInsight Kind = "insight"
)

// SafetyStatus 是 Go 侧安全判定的三态。D-8 把危机判定与最终措辞归 Go，
// 并要求跨进程后 fail-closed：unavailable 不等于放行。
type SafetyStatus string

const (
	SafetyOK          SafetyStatus = "ok"
	SafetyCrisis      SafetyStatus = "crisis"
	SafetyUnavailable SafetyStatus = "unavailable"
)

// Safety 是 Gate 第 6 关（措辞/安全）的输入。危机类别沿用 backend 既有口径
// （self_harm / relationship_violence），medical 是 V3 03 §14 Safety 段点名的
// medical-emergency 信号。
type Safety struct {
	Status     SafetyStatus
	Categories []string
}

// Candidate 是 Python 回投的提案。字段按三种 Kind 取用，未用的留空：
// fact 用 SubjectKey/Predicate/ObjectValue，pattern 用 PatternType/Scope/Fingerprint，
// insight 用 Statement/ClaimType。
type Candidate struct {
	UserID          string
	PersonalModelID string // 留空则由 Service 定位/创建该用户的模型行
	Kind            Kind
	Origin          string // fact：direct|repeated|temporal|behavioral|user_confirmed|user_corrected
	SubjectKey      string
	Predicate       string
	ObjectValue     string
	PatternType     string
	Scope           string
	Description     string
	Fingerprint     string
	Statement       string
	ClaimType       string
	Confidence      float64
	DetectorVersion string
	EvidenceIDs     []string
	PatternIDs      []string
	WindowFrom      time.Time
	WindowTo        time.Time
	HasWindow       bool
	GenerationRunID string
	GenerationModel string
	GenerationVer   string
	Safety          *Safety // nil 视为判定缺失 → fail-closed
}

// IsSensitive 拍「敏感域」：危机类别或医疗信号命中即算。
// 依据 V3 03 §14「Sensitive domain: confidence ≥ 0.85 + strong evidence + special policy」
// ——N3 只落其中可确定性判定的置信部分，strong evidence 的其余含义归 N4。
func (c *Candidate) IsSensitive() bool {
	if c.Safety == nil {
		return false
	}
	for _, category := range c.Safety.Categories {
		switch category {
		case "self_harm", "relationship_violence", "medical":
			return true
		}
	}
	return false
}

// Evidence 是 Gate 需要的证据投影。N4 起多带 role/来源/时刻三列：
// §6 的「同源同日不得计独立重复」与 §7 的 supporting/contradicting 分流都要靠它们，
// 光有 id 和 strength 判不出来。
type Evidence struct {
	ID     string
	UserID string
	Role   string // supporting|contradicting（N2 DDL 默认 supporting）
	// SourceID 是 source_record_id：独立性的第一个去重键。
	SourceID string
	// ObservedAt 取 evidence.created_at（观测入账时刻），独立性去重的第二个键。
	ObservedAt time.Time
	// Strength 允许为空：N2 的 DDL 里 evidence.strength 可空。N4 起由
	// Service.AdmitEvidence 算分写入；空＝尚未算分，不参与分级判定。
	Strength    float64
	HasStrength bool
}
