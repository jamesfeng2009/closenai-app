package domain

import (
	"context"
	"encoding/base64"
	"errors"
	"strings"
	"testing"
	"time"
)

// T3-01 列表面的领域判据（裁决七-10）。
//
// 这里证明的是**只有领域层能答**的四件事：游标的编解码、limit 的默认与上限、
// 状态过滤器与授权集的交集、「页外还有行」如何兑现成 next_cursor。
// 「无证据不上线」不在这里——那条判据落在 SQL 的内连里（canonical.sql 的
// `ListInsightsForBrowsing`），由 `store/insight_browse_integration_test.go` 在真库上证。

type browseStub struct {
	rows      []InsightListItem
	err       error
	gotUser   string
	gotStatus []string
	gotAfter  InsightKey
	gotHasAft bool
	gotLimit  int
	called    int
}

func (s *browseStub) ListInsightsForBrowsing(_ context.Context, userID string,
	statuses []string, after InsightKey, hasAfter bool, limit int) ([]InsightListItem, error) {
	s.called++
	s.gotUser, s.gotStatus, s.gotAfter = userID, statuses, after
	s.gotHasAft, s.gotLimit = hasAfter, limit
	return s.rows, s.err
}

func row(id string, at time.Time) InsightListItem {
	return InsightListItem{ID: id, Statement: "s", ClaimType: "behaviour",
		Status: "published", PublishedAt: at, EvidenceCount: 1}
}

// ── 游标 ────────────────────────────────────────────────────────────────────

func TestInsightCursorRoundTripKeepsMicrosecondAndID(t *testing.T) {
	// 微秒精度是**翻页正确性**的一部分，不是洁癖：ORDER BY 的键是 (published_at, id)，
	// 落在 SQL 里是 timestamptz（微秒）。编码若只到秒，同一秒内的两行会被游标
	// 判成「同一格」，下一页要么重复上一页那行、要么跳过它——而且两种都长得像成功。
	cases := []InsightKey{
		{PublishedAt: time.Date(2026, 10, 19, 8, 0, 0, 123456000, time.UTC), ID: "i-1"},
		{PublishedAt: time.Date(2024, 2, 29, 0, 0, 0, 1000, time.UTC), ID: "i|with|bar"},
		{PublishedAt: time.UnixMicro(0).UTC(), ID: "epoch"},
	}
	for _, want := range cases {
		got, err := DecodeInsightKey(want.Encode())
		if err != nil {
			t.Fatalf("往返失败：%v（%+v）", err, want)
		}
		if !got.PublishedAt.Equal(want.PublishedAt) {
			t.Errorf("时刻漂移：%v → %v（微秒 %d → %d）",
				want.PublishedAt, got.PublishedAt, want.PublishedAt.UnixMicro(), got.PublishedAt.UnixMicro())
		}
		if got.ID != want.ID {
			t.Errorf("id = %q，期望 %q ⇒ 带分隔符的 id 被拆错了", got.ID, want.ID)
		}
	}
}

func TestDecodeInsightKeyRejectsMalformedShapes(t *testing.T) {
	// 每一种都要**显式失败**：游标静默退化＝翻页方永远拿不到后面的行，而它看到 200。
	for _, raw := range []string{
		"!!!not-base64!!!",
		base64Raw("no separator here"),
		base64Raw("1730000000000000|"), // 有时刻没 id
		base64Raw("notanumber|i-1"),    // 时刻合不成整数
		base64Raw("1730000000000000"),  // 缺分隔符
	} {
		k, err := DecodeInsightKey(raw)
		if !errors.Is(err, ErrInsightCursorInvalid) {
			t.Errorf("%q：err = %v，期望 ErrInsightCursorInvalid", raw, err)
		}
		if !k.PublishedAt.IsZero() || k.ID != "" {
			t.Errorf("%q：失败仍然返回了键 %+v", raw, k)
		}
	}
}

func TestInsightBrowserPropagatesCursorError(t *testing.T) {
	stub := &browseStub{}
	b := NewInsightBrowser(stub, nil)
	_, err := b.List(context.Background(), "u1", "", 0, "garbage-garbage")
	if !errors.Is(err, ErrInsightCursorInvalid) {
		t.Fatalf("err = %v", err)
	}
	if stub.called != 0 {
		t.Errorf("坏游标仍然打到仓储 %d 次 ⇒ 「当成第一页」的退化路径被实现了", stub.called)
	}
}

// ── limit ───────────────────────────────────────────────────────────────────

func TestInsightBrowserClampsLimit(t *testing.T) {
	cases := []struct {
		in   int
		want int // 下发给仓储的是 want+1（多取一条判页外还有行）
	}{
		{0, defaultInsightListLimit},
		{-7, defaultInsightListLimit},
		{5, 5},
		{maxInsightListLimit, maxInsightListLimit},
		{10_000, maxInsightListLimit}, // 上限来自这一面的响应体积，不来自调用方
	}
	for _, c := range cases {
		stub := &browseStub{}
		if _, err := NewInsightBrowser(stub, nil).List(context.Background(), "u1", "", c.in, ""); err != nil {
			t.Fatalf("limit=%d err=%v", c.in, err)
		}
		if stub.gotLimit != c.want+1 {
			t.Errorf("limit=%d 下发 %d，期望 %d", c.in, stub.gotLimit, c.want+1)
		}
	}
}

// ── 状态过滤器与授权集 ────────────────────────────────────────────────────────

func TestInsightBrowserStatusFilterCannotEscapeReturnableSet(t *testing.T) {
	for _, c := range []struct {
		in   string
		want []string
	}{
		{"", InsightReturnableStatuses},
		{"published", []string{"published"}},
		{"corrected", []string{"corrected"}},
		// 内部态与系统撤回态：过滤器**不放宽**授权集，而是原样下发，
		// 让 SQL 自然返回零行。回落成全集＝「过滤一个不该看的态」得到「看到全部」。
		{"draft", []string{"draft"}},
		{"retracted", []string{"retracted"}},
	} {
		stub := &browseStub{}
		if _, err := NewInsightBrowser(stub, nil).List(context.Background(), "u1", c.in, 10, ""); err != nil {
			t.Fatal(err)
		}
		if strings.Join(stub.gotStatus, ",") != strings.Join(c.want, ",") {
			t.Errorf("status=%q 下发 %v，期望 %v", c.in, stub.gotStatus, c.want)
		}
	}
}

func TestReturnableStatusSetShape(t *testing.T) {
	// 并集的两半都要钉：N9 的三态**一个都不能少**（否则列表比详情还严，用户会在列表里
	// 看不到一条自己在详情里看得见的洞察）；用户表态造成的三态要齐（工单 T3-01 §怎么做.5
	// 「用户要能回看自己纠错过的」）；`retracted` 与两个 AI 候选态一律不给。
	want := map[string]bool{
		"published": true, "acknowledged": true, "confirmed": true,
		"corrected": true, "expired": true, "dismissed": true,
	}
	if len(InsightReturnableStatuses) != len(want) {
		t.Errorf("集合 = %v，期望 %d 格", InsightReturnableStatuses, len(want))
	}
	for _, s := range InsightReturnableStatuses {
		if !want[s] {
			t.Errorf("集合里多了 %s", s)
		}
		if !isReturnableStatus(s) {
			t.Errorf("%s 在集合里却判成不可见 ⇒ 两处判据分叉了", s)
		}
	}
	for _, s := range []string{"retracted", "draft", "validated", "", "PUBLISHED"} {
		if isReturnableStatus(s) {
			t.Errorf("%q 判成可见", s)
		}
	}
	// N9 的三态必须是这一集合的**子集**：两条面共用一份词表，不是各抄一份。
	for _, s := range InsightVisibleStatuses {
		if !isReturnableStatus(s) {
			t.Errorf("N9 可见态 %s 不在内部面集合里 ⇒ 同一颗洞察在两个面能给出不一致的可见性", s)
		}
	}
}

// ── next_cursor 与空输入 ────────────────────────────────────────────────────

func TestInsightBrowserEmitsCursorOnlyForExtraRow(t *testing.T) {
	at := time.Date(2026, 10, 19, 8, 0, 0, 0, time.UTC)
	for _, c := range []struct {
		name       string
		rows       int
		limit      int
		wantItems  int
		wantCursor bool
	}{
		{"恰好一页", 1, 1, 1, false},
		{"页外还有", 2, 1, 1, true},
		{"空列表", 0, 10, 0, false},
		{"三行取二", 3, 2, 2, true},
	} {
		rows := make([]InsightListItem, 0, c.rows)
		for i := 0; i < c.rows; i++ {
			rows = append(rows, row(string(rune('a'+i)), at.Add(-time.Duration(i)*time.Hour)))
		}
		view, err := NewInsightBrowser(&browseStub{rows: rows}, nil).
			List(context.Background(), "u1", "", c.limit, "")
		if err != nil {
			t.Fatalf("%s：%v", c.name, err)
		}
		if len(view.Items) != c.wantItems {
			t.Errorf("%s：items=%d，期望 %d ⇒ 多取那条漏进响应", c.name, len(view.Items), c.wantItems)
		}
		if (view.NextCursor != "") != c.wantCursor {
			t.Errorf("%s：next_cursor=%q，期望有=%v", c.name, view.NextCursor, c.wantCursor)
		}
		if c.wantCursor {
			key, err := DecodeInsightKey(view.NextCursor)
			if err != nil {
				t.Fatalf("%s：%v", c.name, err)
			}
			last := view.Items[len(view.Items)-1]
			if key.ID != last.ID || !key.PublishedAt.Equal(last.PublishedAt) {
				t.Errorf("%s：游标 %+v 不指向本页最后一行 %+v ⇒ 下一页跳行或重复", c.name, key, last)
			}
		}
	}
}

func TestInsightBrowserNeedsUserAndReader(t *testing.T) {
	if _, err := NewInsightBrowser(&browseStub{}, nil).
		List(context.Background(), "", "", 10, ""); err == nil {
		t.Error("空 user_id 没有被拒 ⇒ 列表会查一个不存在的主键，长得像「这个用户没有洞察」")
	}
	if _, err := (&InsightBrowser{}).List(context.Background(), "u1", "", 10, ""); err == nil {
		t.Error("nil 仓储没有被拒")
	}
	if _, err := (&InsightBrowser{}).Detail(context.Background(), "u1", "i1"); err == nil {
		t.Error("Detail 在 nil explainer 上没有被拒")
	}
}

func TestInsightBrowserPropagatesReaderError(t *testing.T) {
	boom := errors.New("store: down")
	_, err := NewInsightBrowser(&browseStub{err: boom}, nil).
		List(context.Background(), "u1", "", 10, "")
	if !errors.Is(err, boom) {
		t.Errorf("err = %v，期望原样上抛（不折成空列表）", err)
	}
}

// base64Raw 造一个「看起来像游标」的输入，供畸形用例逐格摆出内容。
func base64Raw(s string) string { return base64.RawURLEncoding.EncodeToString([]byte(s)) }
