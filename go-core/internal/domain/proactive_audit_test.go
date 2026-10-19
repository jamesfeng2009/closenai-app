// T5-04：proactives 审计读面的领域判据（工单 §验收 第一句的口径来源）。
//
// 与 T4-02 的 GoalBrowser 同一分工：这里钉**参数与裁剪**——身份必带、limit 的
// 默认与上限、取数为什么是 limit+1、截断怎么报。
// 「哪几态算超期」不在这里判（真身是 canonical.sql 的那一条谓词），
// 本包只对账一次：`ProactiveAuditStaleStatuses` 与 SQL 源文本里的状态集合必须同集
// （[[feedback-claims-need-provenance]] 的两侧不同源：拿 Go 的表比 Go 的表是恒真）。
package domain

import (
	"context"
	"errors"
	"os"
	"regexp"
	"strings"
	"testing"
	"time"
)

type fakeAuditReader struct {
	rows     []ProactiveAuditRecord
	err      error
	gotUser  string
	gotStat  string
	gotLimit int
	gotStale bool
	calls    int
}

func (f *fakeAuditReader) ListProactiveAuditRecords(_ context.Context, userID, status string,
	limit int, stale bool) ([]ProactiveAuditRecord, error) {
	f.calls++
	f.gotUser, f.gotStat, f.gotLimit, f.gotStale = userID, status, limit, stale
	return f.rows, f.err
}

func auditRows(n int) []ProactiveAuditRecord {
	out := make([]ProactiveAuditRecord, 0, n)
	for i := 0; i < n; i++ {
		out = append(out, ProactiveAuditRecord{
			ID: "p" + string(rune('a'+i)), Status: "scheduled",
			CreatedAt: time.Date(2026, 10, 19, 8, 0, 0, 0, time.UTC),
		})
	}
	return out
}

// ── 1. 身份：没有身份就不查，而不是查出一个空列表 ────────────────────────────

func TestProactiveAuditRefusesToQueryWithoutUser(t *testing.T) {
	read := &fakeAuditReader{rows: auditRows(3)}
	browser := NewProactiveAuditBrowser(read)
	for _, userID := range []string{"", "   "} {
		_, err := browser.List(context.Background(), userID, "", 0, false)
		if !errors.Is(err, ErrProactiveAuditNeedsUser) {
			t.Errorf("user=%q 得 err=%v，期望 ErrProactiveAuditNeedsUser ⇒ 空列表在审计面上的"+
				"意思是「这个人没有触达」，而实情是「没人告诉我要查谁」", userID, err)
		}
	}
	if read.calls != 0 {
		t.Errorf("无身份还是拨了仓储 %d 次 ⇒ 挡在 HTTP 层以外就没有第二道了", read.calls)
	}
	// 没有读面＝这一面没装配，必须是错误而不是「查不到」。
	if _, err := NewProactiveAuditBrowser(nil).List(context.Background(),
		"u1", "", 0, false); err == nil {
		t.Error("reader 为 nil 时不报错 ⇒ 装配漏一格会表现成「这个用户零条触达」")
	}
}

// ── 2. limit：默认/上限/取数口径三格，各自挡一种坏法 ─────────────────────────

func TestProactiveAuditLimitClampAndOverfetch(t *testing.T) {
	// want 那一侧写**字面量**而不是 `defaultProactiveAuditLimit`／`maxProactiveAuditLimit`：
	// 常量自己比自己是恒真，改小默认页（50→20）这条测照样绿，而「默认就是 50」是登记过的数。
	cases := []struct{ in, want int }{
		{0, 50},
		{-7, 50},
		{7, 7},
		{200, 200},
		{5000, 200},
	}
	for _, c := range cases {
		read := &fakeAuditReader{}
		view, err := NewProactiveAuditBrowser(read).List(context.Background(),
			"u1", "", c.in, false)
		if err != nil {
			t.Fatalf("limit=%d err=%v", c.in, err)
		}
		// 回显的是**生效值**（响应里那一格），请求值不许冒充它。
		if view.Limit != c.want || read.gotLimit != c.want+1 {
			t.Errorf("limit=%d ⇒ 回显 %d／取数 %d，期望 %d/%d（多取一行只为回答「还有没有」，"+
				"而回显请求值会让调用方以为自己拿到了 5000 条）",
				c.in, view.Limit, read.gotLimit, c.want, c.want+1)
		}
	}
}

// ── 3. 裁剪要报（少报而不说少报是审计面最坏的一种输出）───────────────────────

func TestProactiveAuditReportsTruncation(t *testing.T) {
	read := &fakeAuditReader{rows: auditRows(4)}
	view, err := NewProactiveAuditBrowser(read).List(context.Background(), "u1", "", 3, false)
	if err != nil {
		t.Fatal(err)
	}
	if len(view.Items) != 3 || !view.Truncated {
		t.Errorf("给了 4 行而 limit=3：items %d／truncated %v，期望 3/true",
			len(view.Items), view.Truncated)
	}
	// 正向证人：不截断的那一次必须报 false，否则这条判据恒真。
	read.rows = auditRows(2)
	view, _ = NewProactiveAuditBrowser(read).List(context.Background(), "u1", "", 3, false)
	if len(view.Items) != 2 || view.Truncated {
		t.Errorf("只有 2 行却 items %d／truncated %v，期望 2/false ⇒ 恒真的裁剪闸等于没有闸",
			len(view.Items), view.Truncated)
	}
	// 截断时不许就地改切片首元素：view.Items 必须是新的一片，
	// 否则调用方拿着同一个底层数组会读到被裁掉的那几行。
	read.rows = auditRows(4)
	view, _ = NewProactiveAuditBrowser(read).List(context.Background(), "u1", "", 1, false)
	view.Items[0].ID = "changed"
	if read.rows[0].ID == "changed" {
		t.Error("截断后的切片与仓储返回的那一片共享底层数组 ⇒ 改视图会改到源数据")
	}
}

// ── 4. 过滤器：不校验词表，但必须回显 + 原样透传 ─────────────────────────────

func TestProactiveAuditPassesFiltersThroughAndEchoesThem(t *testing.T) {
	read := &fakeAuditReader{}
	view, err := NewProactiveAuditBrowser(read).List(context.Background(),
		"u1", "  sent  ", 0, true)
	if err != nil {
		t.Fatal(err)
	}
	if read.gotStat != "sent" || view.AppliedStatus != "sent" || !read.gotStale ||
		!view.AppliedStale {
		t.Errorf("透传/回显 = 仓储收到 %q/%v，视图 %q/%v，期望 sent/true 两边同",
			read.gotStat, read.gotStale, view.AppliedStatus, view.AppliedStale)
	}
	// 库里没有的那一态**不报错**（narrowListArgs 同判据：抢先判 400 会让同一个错误
	// 在两条面上有两种形状）。不校验的代价由回显付：零行时调用方看得见自己过滤了什么。
	view, err = NewProactiveAuditBrowser(&fakeAuditReader{}).List(context.Background(),
		"u1", "pending", 0, false)
	if err != nil || len(view.Items) != 0 || view.AppliedStatus != "pending" {
		t.Errorf("status=pending = err %v／items %d／回显 %q，期望无错、零行、看得见过滤器",
			err, len(view.Items), view.AppliedStatus)
	}
}

// ── 5. 仓储的错误不许被这一面吞成空列表 ──────────────────────────────────────

func TestProactiveAuditPropagatesReaderError(t *testing.T) {
	boom := errors.New("store: list proactives for audit: connection refused")
	view, err := NewProactiveAuditBrowser(&fakeAuditReader{err: boom}).
		List(context.Background(), "u1", "", 0, false)
	if !errors.Is(err, boom) {
		t.Errorf("err = %v，期望原样带出 %v ⇒ 库挂了必须与「没有触达」分得开", err, boom)
	}
	if len(view.Items) != 0 {
		t.Error("出错时还带了行 ⇒ 半截结果比错误更危险")
	}
}

// ── 6. 词表：八态是唯一的来源，`pending` 从来不是其中一员 ────────────────────

func TestIsProactiveStatusFollowsTheEightStateVocabulary(t *testing.T) {
	for _, s := range ProactiveStatuses {
		if !IsProactiveStatus(string(s)) {
			t.Errorf("%s 在 ProactiveStatuses 里却不是合法态 ⇒ 词表被抄成第二份了", s)
		}
	}
	if len(ProactiveStatuses) != 8 {
		t.Errorf("八态成了 %d 态 ⇒ 工单 §为什么 引的那句「proactives 有八态」从此失真",
			len(ProactiveStatuses))
	}
	// 工单 §怎么做.1/§验收 用的词是 `pending`，库里没有这一态（台账登记为改判）。
	for _, bad := range []string{"pending", "", "  ", "sent ", "DRAFT", "draft"} {
		if IsProactiveStatus(bad) {
			t.Errorf("%q 被判成合法态 ⇒ 拼错的过滤器会被这一面当成真状态接受", bad)
		}
	}
}

// ── 7. 超期谓词的状态集合与 SQL 源文本对账（两侧不同源）──────────────────────

var staleInSQL = regexp.MustCompile(`(?s)-- name: ListProactivesForAudit :many.*?status IN \(([^)]*)\)`)

func TestStaleVocabularyMatchesTheSQLPredicate(t *testing.T) {
	raw, err := os.ReadFile(filepathJoinAuditSQL())
	if err != nil {
		t.Fatalf("读 canonical.sql 失败：%v ⇒ 这条对账没有真的比过", err)
	}
	block := staleInSQL.FindSubmatch(raw)
	if block == nil {
		t.Fatal("在 canonical.sql 里找不到 ListProactivesForAudit 的 status IN (...) ⇒ " +
			"谓词被改写或整条被删，而这一面的「超期」从此无人对账")
	}
	var got []string
	for _, part := range strings.Split(string(block[1]), ",") {
		got = append(got, strings.Trim(strings.TrimSpace(part), "'"))
	}
	if strings.Join(got, ",") != strings.Join(ProactiveAuditStaleStatuses, ",") {
		t.Errorf("SQL 里的超期态 = %v，Go 登记的 = %v ⇒ 两侧分叉，审计会安静地少报或多报",
			got, ProactiveAuditStaleStatuses)
	}
}

// filepathJoinAuditSQL 从本包目录走到 canonical.sql（路径写在一处，便于上面那条报错）。
func filepathJoinAuditSQL() string {
	return "../../db/queries/canonical.sql"
}
