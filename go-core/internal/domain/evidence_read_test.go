package domain

import (
	"context"
	"errors"
	"fmt"
	"os"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"
	"time"
)

// N9 读路径的单测面（裁决七-7）。fake reader 只搬数据，一次调用记一次，
// 「二跳只往返一次」这类判据靠计数断言，不靠注释。
type fakeEvidenceReader struct {
	insight    InsightReadable
	insightErr error
	items      []EvidenceItem
	counts     SupportCounts
	patterns   []PatternReadable
	links      []PatternEvidenceLink

	calls map[string]int
}

func newFakeEvidenceReader() *fakeEvidenceReader {
	return &fakeEvidenceReader{
		insight: InsightReadable{ID: "i1", UserID: "u1", Statement: "你常在深夜记录",
			ClaimType: "behaviour", Status: "published", Confidence: 0.82, HasConfidence: true},
		counts: SupportCounts{Total: 2, Live: 2},
		calls:  map[string]int{},
	}
}

func (f *fakeEvidenceReader) GetInsightForReading(context.Context, string, string) (InsightReadable, error) {
	f.calls["insight"]++
	return f.insight, f.insightErr
}

func (f *fakeEvidenceReader) ListLiveEvidenceForInsight(context.Context, string, string) ([]EvidenceItem, error) {
	f.calls["evidence"]++
	return f.items, nil
}

func (f *fakeEvidenceReader) CountInsightEvidenceSupport(context.Context, string, string) (SupportCounts, error) {
	f.calls["counts"]++
	return f.counts, nil
}

func (f *fakeEvidenceReader) ListPatternsForInsightReading(context.Context, string, string) ([]PatternReadable, error) {
	f.calls["patterns"]++
	return f.patterns, nil
}

func (f *fakeEvidenceReader) ListLivePatternEvidenceForInsight(context.Context, string, string) ([]PatternEvidenceLink, error) {
	f.calls["pattern_evidence"]++
	return f.links, nil
}

func memoryItem(id, memoryID string, strength float64) EvidenceItem {
	return EvidenceItem{
		ID: id, EvidenceType: "quote", Role: "supporting",
		Strength: strength, HasStrength: true,
		Excerpt: "今晚又三点才睡", HasExcerpt: true,
		ObservedAt: time.Date(2026, 10, 1, 3, 0, 0, 0, time.UTC), HasObservedAt: true,
		Source: EvidenceSource{RecordID: "s-" + id, SourceType: "memory",
			SourceTable: SourceTableMemories, SourceID: memoryID},
	}
}

func readPolicy(evidenceMax, patternMax int) Policy {
	return Policy{Read: ReadPolicy{EvidenceMaxItems: evidenceMax, PatternMaxItems: patternMax}}
}

// TestN9ExplainerRequiresAuthenticatedUser 钉 05 §20 的第一句：
// Never trust user_id supplied by the client——空 user 一律不查库。
func TestN9ExplainerRequiresAuthenticatedUser(t *testing.T) {
	reader := newFakeEvidenceReader()
	explainer := NewInsightExplainer(reader, readPolicy(20, 6))
	if _, err := explainer.Explain(context.Background(), "", "i1"); err == nil {
		t.Fatal("空 user_id 竟然读通了")
	}
	if len(reader.calls) != 0 {
		t.Fatalf("空 user_id 仍下发了查询：%v", reader.calls)
	}
}

// TestN9OwnershipPropagatesNotFound：越权与不存在必须是同一条错误，
// 否则 handler 会在两条分支上给出不同文案，替旁人确认 id 为真。
func TestN9OwnershipPropagatesNotFound(t *testing.T) {
	reader := newFakeEvidenceReader()
	reader.insightErr = fmt.Errorf("store: get insight for reading: %w: i-other", ErrInsightNotFound)
	explainer := NewInsightExplainer(reader, readPolicy(20, 6))
	_, err := explainer.Explain(context.Background(), "u1", "i-other")
	if !errors.Is(err, ErrInsightNotFound) {
		t.Fatalf("err = %v，期望 %v", err, ErrInsightNotFound)
	}
	if reader.calls["evidence"] != 0 {
		t.Fatalf("归属已判失败还去取依据：%v", reader.calls)
	}
}

func TestN9VisibleStatusGate(t *testing.T) {
	for _, status := range []string{"published", "acknowledged", "confirmed"} {
		reader := newFakeEvidenceReader()
		reader.insight.Status = status
		view, err := NewInsightExplainer(reader, readPolicy(20, 6)).
			Explain(context.Background(), "u1", "i1")
		if err != nil {
			t.Errorf("状态 %s 应可展开依据，err = %v", status, err)
			continue
		}
		if view.Insight.Status != status {
			t.Errorf("状态没原样带出：%q", view.Insight.Status)
		}
	}
	// 其余六态 + 空串 + 一个库里不可能出现的脏值都必须挡住（02:144-152 的九值全集）。
	for _, status := range []string{"draft", "validated", "corrected", "dismissed",
		"retracted", "expired", "", "candidate"} {
		reader := newFakeEvidenceReader()
		reader.insight.Status = status
		_, err := NewInsightExplainer(reader, readPolicy(20, 6)).
			Explain(context.Background(), "u1", "i1")
		if !errors.Is(err, ErrInsightNotVisible) {
			t.Errorf("状态 %q 不该可展开依据，err = %v", status, err)
			continue
		}
		if reader.calls["evidence"] != 0 {
			t.Errorf("状态 %q 已被判不可见，还下发了依据查询", status)
		}
	}
}

// TestN9VisibleStatusesMatchSQLLiteral 把 N9-5 的「不新建第二个词表」变成可执行约束：
// Go 侧的可见集合必须与 §37 SQL 里的字面一模一样。这条用例是词表漂移的哨兵——
// 改了 canonical.sql:56 或改了 InsightVisibleStatuses 而忘了另一处，这里必须红。
func TestN9VisibleStatusesMatchSQLLiteral(t *testing.T) {
	path := strings.Join([]string{"..", "..", "db", "queries", "canonical.sql"}, string(os.PathSeparator))
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("读 %s 失败，词表对齐闸未生效: %v", path, err)
	}
	pattern := regexp.MustCompile(`(?s)-- name: ListPublishedInsights :many.*?AND status IN \(([^)]*)\)`)
	matches := pattern.FindStringSubmatch(string(raw))
	if matches == nil {
		t.Fatal("没在 canonical.sql 里定位到 ListPublishedInsights 的状态集合，词表对齐闸空转")
	}
	var sqlStatuses []string
	for _, quoted := range regexp.MustCompile(`'([^']+)'`).FindAllStringSubmatch(matches[1], -1) {
		sqlStatuses = append(sqlStatuses, quoted[1])
	}
	if len(sqlStatuses) == 0 {
		t.Fatal("SQL 状态集合解析成空，词表对齐闸空转")
	}
	want := append([]string(nil), sqlStatuses...)
	sort.Strings(want)
	got := append([]string(nil), InsightVisibleStatuses...)
	sort.Strings(got)
	if !reflect.DeepEqual(got, want) {
		t.Errorf("Go 侧可见集合 %v 与 SQL 的 %v 不一致（N9-5 要求同一份字面）", got, want)
	}
}

func TestN9SummaryAndMemoryRef(t *testing.T) {
	reader := newFakeEvidenceReader()
	reader.items = []EvidenceItem{
		memoryItem("e1", "m1", 0.90),
		{ID: "e2", EvidenceType: "statistic", Role: "supporting",
			Excerpt: "近 30 天有 12 次深夜记录", HasExcerpt: true,
			Source: EvidenceSource{RecordID: "s-e2", SourceType: "aggregate",
				SourceTable: "chat_messages", SourceID: "c1"}},
	}
	reader.counts = SupportCounts{Total: 5, Live: 2}
	view, err := NewInsightExplainer(reader, readPolicy(20, 6)).
		Explain(context.Background(), "u1", "i1")
	if err != nil {
		t.Fatalf("Explain: %v", err)
	}
	if view.Summary.Count != 2 || view.Summary.WithdrawnCount != 3 {
		t.Errorf("摘要 = %+v，期望 count 2 / withdrawn 3（5 总支撑 - 2 活）", view.Summary)
	}
	// 只有一条带强度：均值必须是它自己，而不是除以总数被 NULL 拉低（N9-12）。
	if !view.Summary.HasStrength || view.Summary.Strength != 0.90 {
		t.Errorf("强度均值 = %v/%v，期望 0.90 且只按有强度的一条算",
			view.Summary.Strength, view.Summary.HasStrength)
	}
	if view.Evidence[0].MemoryRef == nil {
		t.Fatal("memories 指针没构造出 memory_ref")
	}
	ref := view.Evidence[0].MemoryRef
	if ref.MemoryID != "m1" || ref.Excerpt != "今晚又三点才睡" ||
		!ref.HasCreatedAt || !ref.CreatedAt.Equal(view.Evidence[0].ObservedAt) {
		t.Errorf("memory_ref = %+v，期望三键逐字（memory_id/excerpt/created_at）", *ref)
	}
	if view.Evidence[1].MemoryRef != nil {
		t.Errorf("非 memories 来源不该有 memory_ref：%+v", *view.Evidence[1].MemoryRef)
	}
}

// TestN9MemoryRefNeedsRealID：空 source_id 不给 memory_ref——前端的
// resolveMemoryRefs() 是「解析不到即丢弃」，把空主键递过去等于让它静默丢。
func TestN9MemoryRefNeedsRealID(t *testing.T) {
	reader := newFakeEvidenceReader()
	item := memoryItem("e1", "", 0.5)
	reader.items = []EvidenceItem{item}
	view, err := NewInsightExplainer(reader, readPolicy(20, 6)).
		Explain(context.Background(), "u1", "i1")
	if err != nil {
		t.Fatalf("Explain: %v", err)
	}
	if view.Evidence[0].MemoryRef != nil {
		t.Fatalf("空 source_id 仍产出了 memory_ref：%+v", *view.Evidence[0].MemoryRef)
	}
}

func TestN9WithdrawalClampedToZero(t *testing.T) {
	reader := newFakeEvidenceReader()
	// 并发撤回会让 total < live：宁可不报撤回数，也不报一个负数。
	reader.counts = SupportCounts{Total: 1, Live: 2}
	view, err := NewInsightExplainer(reader, readPolicy(20, 6)).
		Explain(context.Background(), "u1", "i1")
	if err != nil {
		t.Fatalf("Explain: %v", err)
	}
	if view.Summary.WithdrawnCount != 0 {
		t.Errorf("withdrawn_count = %d，期望按 0 收", view.Summary.WithdrawnCount)
	}
}

// TestN9CapsTrimListsNotContent 钉 N9-11 的两半：裁剪只发生在列表长度上，
// excerpt 一字不动；且裁完仍如实给 truncated + 全量计数。
func TestN9CapsTrimListsNotContent(t *testing.T) {
	reader := newFakeEvidenceReader()
	for i := 0; i < 25; i++ {
		reader.items = append(reader.items, memoryItem(
			string(rune('a'+i)), "m"+string(rune('a'+i)), 0.5))
	}
	reader.counts = SupportCounts{Total: 25, Live: 25}
	view, err := NewInsightExplainer(reader, readPolicy(20, 6)).
		Explain(context.Background(), "u1", "i1")
	if err != nil {
		t.Fatalf("Explain: %v", err)
	}
	if len(view.Evidence) != 20 || !view.Truncated {
		t.Fatalf("一跳 = %d 条 truncated=%v，期望 20 条且标 truncated", len(view.Evidence), view.Truncated)
	}
	if view.Summary.Count != 25 {
		t.Errorf("count = %d，期望全量 25（裁剪不改摘要计数）", view.Summary.Count)
	}
	if view.Evidence[19].Excerpt != "今晚又三点才睡" {
		t.Errorf("excerpt 被动过：%q", view.Evidence[19].Excerpt)
	}

	// cap<=0 ＝ 不裁：返回全部且不标 truncated。
	reader2 := newFakeEvidenceReader()
	reader2.items = reader.items
	reader2.counts = reader.counts
	open, err := NewInsightExplainer(reader2, readPolicy(0, 0)).
		Explain(context.Background(), "u1", "i1")
	if err != nil {
		t.Fatalf("Explain(不裁): %v", err)
	}
	if len(open.Evidence) != 25 || open.Truncated {
		t.Errorf("不裁时 = %d 条 truncated=%v，期望 25 条不标", len(open.Evidence), open.Truncated)
	}
}

func TestN9PatternSecondHopIsOneQuery(t *testing.T) {
	reader := newFakeEvidenceReader()
	reader.counts = SupportCounts{Total: 3, Live: 3}
	reader.patterns = []PatternReadable{
		{ID: "p1", PatternType: "temporal", Description: "深夜记录", Status: "supported",
			Confidence: 0.7, HasConfidence: true, EvidenceCount: 2},
		{ID: "p2", PatternType: "behavioral", Description: "周末不记录", Status: "active",
			EvidenceCount: 0},
	}
	reader.links = []PatternEvidenceLink{
		{PatternID: "p1", Item: memoryItem("e10", "m10", 0.8)},
		{PatternID: "p1", Item: memoryItem("e11", "m11", 0.6)},
	}
	view, err := NewInsightExplainer(reader, readPolicy(20, 6)).
		Explain(context.Background(), "u1", "i1")
	if err != nil {
		t.Fatalf("Explain: %v", err)
	}
	// 二跳是**一条**查询，不是按 pattern 循环（N9-7：N 个 pattern ⇒ N+1 次往返）。
	if reader.calls["pattern_evidence"] != 1 {
		t.Errorf("二跳下发了 %d 次查询，期望 1 次", reader.calls["pattern_evidence"])
	}
	if len(view.Patterns) != 2 {
		t.Fatalf("pattern 组 = %d，期望 2（没依据的 p2 也要占一行）", len(view.Patterns))
	}
	if len(view.Patterns[0].Evidence) != 2 || len(view.Patterns[1].Evidence) != 0 {
		t.Errorf("归组错位：%d / %d", len(view.Patterns[0].Evidence), len(view.Patterns[1].Evidence))
	}
	if view.Patterns[1].Evidence == nil {
		t.Error("空依据组要给空数组而不是 nil，前端 .map 会炸")
	}
	if view.Patterns[0].Evidence[0].MemoryRef == nil {
		t.Error("二跳的依据同样要构造 memory_ref")
	}
}

func TestN9PatternCapTrimsGroups(t *testing.T) {
	reader := newFakeEvidenceReader()
	for i := 0; i < 8; i++ {
		id := string(rune('a' + i))
		reader.patterns = append(reader.patterns, PatternReadable{ID: "p" + id, Status: "active"})
		reader.links = append(reader.links,
			PatternEvidenceLink{PatternID: "p" + id, Item: memoryItem("e"+id, "m"+id, 0.5)},
			PatternEvidenceLink{PatternID: "p" + id, Item: memoryItem("f"+id, "n"+id, 0.5)})
	}
	view, err := NewInsightExplainer(reader, readPolicy(1, 1)).
		Explain(context.Background(), "u1", "i1")
	if err != nil {
		t.Fatalf("Explain: %v", err)
	}
	if len(view.Patterns) != 1 {
		t.Fatalf("pattern 组 = %d，期望按 PatternMaxItems 裁到 1", len(view.Patterns))
	}
	if len(view.Patterns[0].Evidence) != 1 || !view.Patterns[0].Truncated {
		t.Errorf("组内依据 = %d truncated=%v，期望 1 条且标 truncated",
			len(view.Patterns[0].Evidence), view.Patterns[0].Truncated)
	}
}

// TestN9DefaultReadCaps 钉默认值本身（改默认值要过这条，属契约面）。
func TestN9DefaultReadCaps(t *testing.T) {
	policy := DefaultReadPolicy()
	if policy.EvidenceMaxItems != 20 || policy.PatternMaxItems != 6 {
		t.Errorf("默认裁剪 = %+v，期望 20/6（裁决七-7 N9-11）", policy)
	}
	if got := len(InsightVisibleStatuses); got != 3 {
		t.Errorf("可见状态集合 %d 个，期望 3 个", got)
	}
	if SourceTableMemories != "memories" {
		t.Errorf("memories 指针常量被改成 %q", SourceTableMemories)
	}
}
