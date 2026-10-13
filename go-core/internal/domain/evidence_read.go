package domain

import (
	"context"
	"errors"
	"time"
)

// N9：洞察依据展开的读面（V3 05 §18 的
// `GET /v1/personal-model/insights/:id/evidence`，裁决七-7）。
//
// 这一条是**只读**面，所以它不挂在 Service（不变量 7 的 canonical 唯一写入口）上，
// 而是一个独立的解释器：写侧的乐观锁、版本账本、outbox 投递判定在这里一概没有。
// 与写侧唯一的共享物是 `Policy` 与状态词表——判据只能有一份，否则同一个洞察
// 在写入时算「可见」、在读路径算「不可见」，用户就会看到一条谁也解释不了的空白。

// ErrInsightNotVisible 是「这条洞察存在、但它的依据不对外展开」。
// 与 ErrInsightNotFound 分开的理由见 InsightVisibleStatuses。
var ErrInsightNotVisible = errors.New("domain: insight evidence is not publicly visible")

// SourceTableMemories 是 D-2 薄指针里「用户自己写的素材」那一路（0002 之后由
// `source_records.source_table` 承载）。出处：`backend/models/canonical.py:112`
// 对该列取值面的登记（memories / chat_messages / mood_checkins…）。
const SourceTableMemories = "memories"

// InsightVisibleStatuses 是允许展开依据的洞察状态集合，取值与既有 §37 SQL 的字面
// 同一份（`canonical.sql:56` 的 `('published','acknowledged','confirmed')`），
// 不是第二个词表——两者由 TestN9VisibleStatusesMatchSQLLiteral 钉在一起。
//
// 其余六态（draft/validated/corrected/dismissed/retracted/expired，02:144-152）
// 一律按「不存在」处理：05:387「Never expose internal AI candidate APIs to the
// client」，`draft/validated` 是 AI 候选内部态，`retracted/dismissed/expired`
// 是已经对用户撤回的历史，把它们的依据铺出去等于替系统收回撤声明。
var InsightVisibleStatuses = []string{"published", "acknowledged", "confirmed"}

// InsightReadable 是解释器需要的那几列洞察（store 只搬这些）。
type InsightReadable struct {
	ID            string
	UserID        string
	Statement     string
	ClaimType     string
	Status        string
	Confidence    float64
	HasConfidence bool
	TimeFrom      time.Time
	HasTimeFrom   bool
	TimeTo        time.Time
	HasTimeTo     bool
}

// EvidenceSource 是 D-2 的薄指针本身：不复制内容，只说明这条依据来自哪张既有行。
type EvidenceSource struct {
	RecordID    string
	SourceType  string
	SourceTable string
	SourceID    string
}

// MemoryRef 是回链到用户素材的那一跳，三键逐字沿用前端既有口径
// （`contracts/frontend-compat.md:91` 的 `memory_refs[].{memory_id,excerpt,created_at}`
// 与 `:102` 的现构点）。
//
// 之所以是 additive 的一层而不是新形状：前端 `resolveMemoryRefs()`
// （`frontend/src/services/evidence.ts`）拿 id 去 `GET /memories` 解析，
// **解析不到就丢弃**——所以这里只允许给真实存在的 memory 主键，
// 而 `source_records.source_id` 正是 Python 写入时的那颗主键（D-2 指针语义）。
type MemoryRef struct {
	MemoryID     string
	Excerpt      string
	CreatedAt    time.Time
	HasCreatedAt bool
}

// EvidenceItem 是一条对外可见的依据。`excerpt` 一个字都不截断、不改写（N9-11）：
// 截断等于改用户原话，与既有文案 `journey.evidenceNote`「引用只来自你写过的原话」
// 是同一条口径。
type EvidenceItem struct {
	ID            string
	EvidenceType  string
	Role          string
	Strength      float64
	HasStrength   bool
	Excerpt       string
	HasExcerpt    bool
	ObservedAt    time.Time
	HasObservedAt bool
	Source        EvidenceSource
	MemoryRef     *MemoryRef
}

// SupportCounts 是 SQL 搬回来的两个数（`CountInsightEvidenceSupport`）。
type SupportCounts struct {
	Total int64
	Live  int64
}

// PatternReadable 是第二跳的模式行（既有 `ListPatternsForInsight` 的列）。
type PatternReadable struct {
	ID            string
	PatternType   string
	Scope         string
	Description   string
	Status        string
	Confidence    float64
	HasConfidence bool
	EvidenceCount int64
}

// PatternEvidenceLink 是二跳查询的一行：一条依据挂在哪个 pattern 上。
type PatternEvidenceLink struct {
	PatternID string
	Item      EvidenceItem
}

// InsightEvidenceReader 是解释器需要的持久化面，实现只有一个：`internal/store`
// 里那个池面只读仓储（07:183-186「repository 持久化领域决策，不做决策」——
// 这里连判定都没有，只有取数）。
type InsightEvidenceReader interface {
	GetInsightForReading(ctx context.Context, userID, insightID string) (InsightReadable, error)
	ListLiveEvidenceForInsight(ctx context.Context, userID, insightID string) ([]EvidenceItem, error)
	CountInsightEvidenceSupport(ctx context.Context, userID, insightID string) (SupportCounts, error)
	ListPatternsForInsightReading(ctx context.Context, userID, insightID string) ([]PatternReadable, error)
	ListLivePatternEvidenceForInsight(ctx context.Context, userID, insightID string) ([]PatternEvidenceLink, error)
}

// EvidenceSummary 是 05 §19 的 `evidence_summary`。
//
// `Strength` 是**数值**而不是 V3 示例里的 `"strong"` 字符串（N9-12）：05 全文没给
// strong/moderate/weak 的词表与阈值，本项目实测 `"strong"` 字面 0 处，
// 造映射就是 §五-5/6 与 D-4 拒绝的「V3 自造默认值、无出处」那一类。
// 分母只含带强度的活依据——NULL 强度算进分母会把均值压低成一个假的「弱」。
type EvidenceSummary struct {
	Count          int
	WithdrawnCount int
	Strength       float64
	HasStrength    bool
}

// PatternEvidenceGroup 是二跳的一组：模式本身 + 它的活依据。
type PatternEvidenceGroup struct {
	Pattern   PatternReadable
	Evidence  []EvidenceItem
	Truncated bool
}

// InsightEvidenceView 是解释器的产物；httpapi 只负责把它编成 05 §19 的字段名。
type InsightEvidenceView struct {
	Insight   InsightReadable
	Summary   EvidenceSummary
	Evidence  []EvidenceItem
	Truncated bool
	Patterns  []PatternEvidenceGroup
}

// ReadPolicy 是 N9 读路径的裁剪数值（裁决七-7 N9-11）。
//
// 数值出处：V3 一处都没给读路径上限，两个都是本项目自定的本地默认值，
// 与 D-4/§五-5/6 同一口径（V3 自造数值不照抄，自己的数值要能被 env 标定）。
// 0 或负数＝不裁（返回全部），env 覆盖用 `CLOSEN_EVIDENCE_MAX_ITEMS` /
// `CLOSEN_PATTERN_MAX_ITEMS`。
type ReadPolicy struct {
	EvidenceMaxItems int
	PatternMaxItems  int
}

func DefaultReadPolicy() ReadPolicy {
	return ReadPolicy{
		EvidenceMaxItems: envInt("CLOSEN_EVIDENCE_MAX_ITEMS", 20),
		PatternMaxItems:  envInt("CLOSEN_PATTERN_MAX_ITEMS", 6),
	}
}

// InsightExplainer 把「这条洞察凭什么」拼成可见视图。
type InsightExplainer struct {
	reader InsightEvidenceReader
	policy Policy
}

func NewInsightExplainer(reader InsightEvidenceReader, policy Policy) *InsightExplainer {
	return &InsightExplainer{reader: reader, policy: policy}
}

// Explain 的顺序是有意义的：先归属、再可见性、最后取数。
//
// 归属先判是 05 §20（Never trust client user_id）的落点：`GetInsightForReading`
// 的 SQL 就是 `WHERE id = $1 AND user_id = $2`，别人的洞察在这里必然 ErrInsightNotFound，
// 于是**不需要**为了「防越权」再写第二道检查；可见性判在其后，是为了让越权尝试与
// 不存在都回同一个 404（回 403 等于告诉攻击者这颗 id 真实存在，N9-4）。
func (e *InsightExplainer) Explain(ctx context.Context, userID, insightID string) (InsightEvidenceView, error) {
	if userID == "" {
		return InsightEvidenceView{}, errors.New("domain: insight evidence read needs an authenticated user")
	}
	insight, err := e.reader.GetInsightForReading(ctx, userID, insightID)
	if err != nil {
		return InsightEvidenceView{}, err
	}
	if !isVisibleStatus(insight.Status) {
		return InsightEvidenceView{}, ErrInsightNotVisible
	}

	items, err := e.reader.ListLiveEvidenceForInsight(ctx, userID, insightID)
	if err != nil {
		return InsightEvidenceView{}, err
	}
	counts, err := e.reader.CountInsightEvidenceSupport(ctx, userID, insightID)
	if err != nil {
		return InsightEvidenceView{}, err
	}
	patterns, err := e.reader.ListPatternsForInsightReading(ctx, userID, insightID)
	if err != nil {
		return InsightEvidenceView{}, err
	}
	links, err := e.reader.ListLivePatternEvidenceForInsight(ctx, userID, insightID)
	if err != nil {
		return InsightEvidenceView{}, err
	}

	view := InsightEvidenceView{Insight: insight}
	for i := range items {
		attachMemoryRef(&items[i])
	}
	for i := range links {
		attachMemoryRef(&links[i].Item)
	}
	view.Summary = summarize(items, counts)
	view.Evidence, view.Truncated = capItems(items, e.policy.Read.EvidenceMaxItems)
	view.Patterns = groupPatternEvidence(patterns, links,
		e.policy.Read.PatternMaxItems, e.policy.Read.EvidenceMaxItems)
	return view, nil
}

// attachMemoryRef 落 N9-8 的判据：`insight_evidence` 没有 memory_id 列，
// 回链只能经薄指针走 `source_records(source_table, source_id)`；只有指向 memories
// 的那一路才是「用户自己写过的素材」，才配一个叫 memory_refs 的形状。
//
// source_id 为空 ⇒ 不给 memory_ref：前端 `resolveMemoryRefs()` 的既有行为是
// 「解析不到的 id 丢弃」，我们不该把一颗空主键送去让它丢。
func attachMemoryRef(item *EvidenceItem) {
	if item.Source.SourceTable != SourceTableMemories || item.Source.SourceID == "" {
		return
	}
	item.MemoryRef = &MemoryRef{
		MemoryID:     item.Source.SourceID,
		Excerpt:      item.Excerpt,
		CreatedAt:    item.ObservedAt,
		HasCreatedAt: item.HasObservedAt,
	}
}

// summarize 算 05 §19 的摘要。withdrawn 用「总支撑 − 活支撑」而不是单独查一次，
// 因为两个数来自同一条 COUNT 查询的同一快照（N9-6：依据变少必须可解释）。
// 负数按 0 收：并发撤回会让 total < live，宁可不报也不报一个负的撤回数。
func summarize(items []EvidenceItem, counts SupportCounts) EvidenceSummary {
	summary := EvidenceSummary{Count: len(items)}
	if counts.Total > counts.Live {
		summary.WithdrawnCount = int(counts.Total - counts.Live)
	}
	var sum float64
	var scored int
	for _, item := range items {
		if item.HasStrength {
			sum += item.Strength
			scored++
		}
	}
	if scored > 0 {
		summary.Strength = sum / float64(scored)
		summary.HasStrength = true
	}
	return summary
}

// capItems 只裁列表，不裁内容（N9-11）：超出上限时如实标 truncated，
// 摘要里的 Count 仍是活依据总数，用户看到的「依据变少」不会变成「依据消失」。
// 空输入返回**空切片**而不是 nil：前端对 `pattern.evidence` 直接 `.map`。
func capItems(items []EvidenceItem, max int) ([]EvidenceItem, bool) {
	if max <= 0 || len(items) <= max {
		capped := make([]EvidenceItem, len(items))
		copy(capped, items)
		return capped, false
	}
	capped := make([]EvidenceItem, max)
	copy(capped, items[:max])
	return capped, true
}

// groupPatternEvidence 把二跳的扁平行按 pattern 归组。
//
// pattern 的顺序沿用 SQL 的 `ORDER BY p.confidence DESC NULLS LAST`（既有
// `ListPatternsForInsight` 的排序，:82），这里不重新排序、只补「没有依据的 pattern
// 也占一行」的空组——依据为空是事实，不是要藏起来的东西。
func groupPatternEvidence(patterns []PatternReadable, links []PatternEvidenceLink,
	maxPatterns, maxEvidence int) []PatternEvidenceGroup {
	byPattern := make(map[string][]EvidenceItem, len(patterns))
	for _, link := range links {
		byPattern[link.PatternID] = append(byPattern[link.PatternID], link.Item)
	}
	limit := len(patterns)
	if maxPatterns > 0 && limit > maxPatterns {
		limit = maxPatterns
	}
	groups := make([]PatternEvidenceGroup, 0, limit)
	for _, pattern := range patterns[:limit] {
		items, truncated := capItems(byPattern[pattern.ID], maxEvidence)
		groups = append(groups, PatternEvidenceGroup{
			Pattern: pattern, Evidence: items, Truncated: truncated,
		})
	}
	return groups
}

// isVisibleStatus 是可见性判据的唯一入口：状态取库里那一行的字面值，
// 集合取 InsightVisibleStatuses。空串一律不可见（脏数据不对外展开）。
func isVisibleStatus(status string) bool {
	if status == "" {
		return false
	}
	for _, visible := range InsightVisibleStatuses {
		if status == visible {
			return true
		}
	}
	return false
}
