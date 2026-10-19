// T5-04：proactives 审计面的形状与门禁闸（工单 §验收 两句话各自的可证形式）。
//
// 分工与另两条读面逐字同构：这里只钉**入口与映射**——路由存在、身份只从
// `X-User-Id` 进、查询参数各折成什么码、响应字段名与 null 语义。
// 「哪几态算超期」的真身在 SQL 谓词里，由 `store/proactive_audit_integration_test.go`
// 在真库上钉，加上 `domain/proactive_audit_test.go` 那条「词表与 SQL 源文本对账」；
// 这一层不许出现第二条同一件事的判据（出现了就等于本票要消灭的「两个人读出两个答案」）。
package httpapi_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/httpapi"
)

// stubProactiveAuditReader 记录**最后一次**调用的参数形状：
// 「三种过滤器各拨出什么参数」是一张参数映射表，它的真身由真库集成测钉住。
type stubProactiveAuditReader struct {
	rows     []domain.ProactiveAuditRecord
	err      error
	calls    int
	gotUser  string
	gotStat  string
	gotLimit int
	gotStale bool
}

func (s *stubProactiveAuditReader) ListProactiveAuditRecords(_ context.Context,
	userID, status string, limit int, stale bool) ([]domain.ProactiveAuditRecord, error) {
	s.calls++
	s.gotUser, s.gotStat, s.gotLimit, s.gotStale = userID, status, limit, stale
	return s.rows, s.err
}

const auditPath = "/internal/v3/proactives"

func auditServer(t *testing.T, read *stubProactiveAuditReader,
	token string) *httpapi.Server {
	t.Helper()
	v3 := fullV3(t)
	v3.Proactives = domain.NewProactiveAuditBrowser(read)
	return newV3ServerWith(t, v3, token)
}

// auditRow 是一行「排期了但没发出去」的触达：可空列全部留零值，
// 好让下面那条 null 判据真的有事可证（全填上值的夹具证不出「缺数据」那一格）。
func auditRow(id string) domain.ProactiveAuditRecord {
	return domain.ProactiveAuditRecord{
		ID: id, Status: "scheduled", TriggerType: "memory_recall",
		CreatedAt: time.Date(2026, 10, 19, 8, 0, 0, 0, time.UTC),
		UpdatedAt: time.Date(2026, 10, 19, 8, 0, 0, 0, time.UTC),
	}
}

type auditPayload struct {
	Items     []map[string]any `json:"items"`
	Truncated bool             `json:"truncated"`
	Limit     int              `json:"limit"`
	Status    string           `json:"status"`
	Stale     bool             `json:"stale"`
}

func decodeAudit(t *testing.T, env envelope) auditPayload {
	t.Helper()
	var out auditPayload
	decodeData(t, env, &out)
	return out
}

// ── 1. 路由在册，且这一面只有 GET（工单 §怎么做.1 那句「只读」）───────────────

func TestV3ProactiveAuditIsGetOnly(t *testing.T) {
	read := &stubProactiveAuditReader{rows: []domain.ProactiveAuditRecord{auditRow("p1")}}
	h := auditServer(t, read, v3Token).Handler()
	status, env := do(t, h, "GET", auditPath, "", goalHeaders())
	if status != 200 || env.Code != 200 {
		t.Fatalf("GET %s = %d/%d，期望 200 ⇒ 这一面缺了入口", auditPath, status, env.Code)
	}
	// 只读的**可证形式**是「写方法一个都到不了仓储」，不是注释里那句「这是只读面」。
	// 这里不走 do()：Go 的 mux 对「path 命中而方法不合」回的是它自己的 405 纯文本，
	// 那本来就不是信封，硬套信封只会把判据换成「我猜的形状」。
	for _, method := range []string{"POST", "PUT", "PATCH", "DELETE"} {
		before := read.calls
		s := rawStatus(t, h, method, auditPath)
		if read.calls != before {
			t.Errorf("%s 走到了仓储（calls %d→%d）⇒ 只读面长出了写路径",
				method, before, read.calls)
		}
		if s == 200 {
			t.Errorf("%s 回 200 ⇒ 审计面被当成写面注册了", method)
		}
	}
}

// rawStatus 只关心状态码：写方法那一格要证的是「没走到仓储」，不是响应体形状。
func rawStatus(t *testing.T, h http.Handler, method, path string) int {
	t.Helper()
	req := httptest.NewRequest(method, path, strings.NewReader(`{"status":"sent"}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Service-Token", v3Token)
	req.Header.Set("X-User-Id", "owner")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec.Code
}

// ── 2. 身份只从头部进来（§验收「跨用户查询必须显式带 user_id 且鉴权为服务级」）──

func TestV3ProactiveAuditIdentityComesOnlyFromHeader(t *testing.T) {
	read := &stubProactiveAuditReader{}
	h := auditServer(t, read, v3Token).Handler()

	// 缺 X-User-Id ⇒ 401。审计面最怕的不是查不到，而是**替一个不存在的人排除故障**
	// （domain 的 ErrProactiveAuditNeedsUser 同一条理由）。
	if s, env := do(t, h, "GET", auditPath, "", v3Headers(v3Token, "")); s != 401 ||
		env.Code != apierr.CodeUnauthorized {
		t.Errorf("缺身份 = %d/%d，期望 401/40101", s, env.Code)
	}
	if s, env := do(t, h, "GET", auditPath, "", v3Headers("wrong-token", "owner")); s != 403 ||
		env.Code != apierr.CodeForbidden {
		t.Errorf("错密钥 = %d/%d，期望 403/40301（服务级鉴权，与另两条读面同码）", s, env.Code)
	}
	// 工单 §怎么做.1 字面写的是 `?user_id=`，本票改判成头（见台账结案行）：
	// ADR-002 只留一个身份入口。而这一格必须**显式拒绝**而不是静默忽略——
	// 静默忽略会让值班以为自己查的是 victim，屏幕上那几行其实是 owner 的。
	before := read.calls
	s, env := do(t, h, "GET", auditPath+"?user_id=victim", "", goalHeaders())
	if s != 400 || env.Code != apierr.CodeInvalidParams {
		t.Errorf("查询里塞 user_id = %d/%d，期望 400/40001 ⇒ 它要么被误当成身份，要么被静默吞掉",
			s, env.Code)
	}
	if read.calls != before {
		t.Error("被拒的 user_id 查询还是走到了仓储 ⇒ 挡在领域层而不是入口")
	}
	// 头部那一格才是身份。
	if _, _ = do(t, h, "GET", auditPath, "", goalHeaders()); read.gotUser != "owner" {
		t.Errorf("传给领域的 user_id = %q，期望 owner", read.gotUser)
	}
}

// ── 3. 参数形状：三个都认，其余一律 400；收敛后的 limit 要回显 ────────────────

func TestV3ProactiveAuditQueryParameterShape(t *testing.T) {
	read := &stubProactiveAuditReader{}
	h := auditServer(t, read, v3Token).Handler()

	if s, env := do(t, h, "GET", auditPath+"?stal=1", "", goalHeaders()); s != 400 ||
		env.Code != apierr.CodeInvalidParams {
		t.Errorf("拼错的参数名 stal = %d/%d，期望 400/40001 ⇒ 静默忽略等于把过滤器悄悄丢掉",
			s, env.Code)
	}
	// stale 只认真伪。`stale=yes` 读成 false 是最坏的一种少报：运维以为自己看的是超期队列。
	for _, raw := range []string{"yes", "1x", "超期"} {
		if s, _ := do(t, h, "GET", auditPath+"?stale="+raw, "", goalHeaders()); s != 400 {
			t.Errorf("stale=%q = %d，期望 400", raw, s)
		}
	}
	if s, _ := do(t, h, "GET", auditPath+"?limit=0", "", goalHeaders()); s != 400 {
		t.Errorf("limit=0 = %d，期望 400（与洞察/目标两条读面同形，不猜默认值）", s)
	}

	// 三个过滤器各归各领域判据，HTTP 层不提前替它做一遍。
	status, _ := do(t, h, "GET", auditPath+"?status=sent&stale=true&limit=7", "",
		goalHeaders())
	if status != 200 {
		t.Fatalf("三个合法参数一起给 = %d，期望 200", status)
	}
	if read.gotStat != "sent" || !read.gotStale || read.gotLimit != 8 {
		t.Errorf("透传 = status %q stale %v limit %d，期望 sent/true/8（limit+1 是领域的取数口径）",
			read.gotStat, read.gotStale, read.gotLimit)
	}
	// 状态词表**不校验**（narrowListArgs 同判据）：库里没有的那一态必然零行。
	// 不校验的前提是回显，否则「拼错一个状态」与「这个人真的一条触达都没有」是同一张脸。
	if _, env := do(t, h, "GET", auditPath+"?status=pending", "", goalHeaders()); env.Code != 200 {
		t.Fatalf("status=pending = %d，期望 200（过滤器不校验词表，见 domain 的注释）", env.Code)
	} else if got := decodeAudit(t, env); got.Status != "pending" {
		t.Errorf("回显 status = %q，期望 pending ⇒ 回显没了，零行就成了谎话", got.Status)
	}

	// 回显的是**生效值**而不是请求值：传 5000 的人要被告知自己被截到 200。
	_, env := do(t, h, "GET", auditPath+"?limit=5000", "", goalHeaders())
	got := decodeAudit(t, env)
	if got.Limit != 200 || read.gotLimit != 201 {
		t.Errorf("limit=5000 之后 回显 %d／取数 %d，期望 200/201 ⇒ 上限没生效，或回显了请求值而不是生效值",
			got.Limit, read.gotLimit)
	}
	_, env = do(t, h, "GET", auditPath, "", goalHeaders())
	if got = decodeAudit(t, env); got.Limit != 50 || read.gotLimit != 51 {
		t.Errorf("不带 limit 时 回显 %d／取数 %d，期望 50/51（默认值也不能由 HTTP 层猜）",
			got.Limit, read.gotLimit)
	}
}

// ── 4. 字段名逐字＋NULL 语义（缺数据不许显示成一条 1970 年的记录）─────────────

func TestV3ProactiveAuditFieldNamesAndNulls(t *testing.T) {
	read := &stubProactiveAuditReader{rows: []domain.ProactiveAuditRecord{
		func() domain.ProactiveAuditRecord {
			row := auditRow("p1")
			row.ScheduledAt = time.Date(2026, 10, 19, 9, 0, 0, 0, time.UTC)
			row.BlockReason = "quiet_hours"
			row.JourneyEventID = "je-1"
			return row
		}(),
	}}
	h := auditServer(t, read, v3Token).Handler()
	_, env := do(t, h, "GET", auditPath, "", goalHeaders())
	got := decodeAudit(t, env)
	if len(got.Items) != 1 {
		t.Fatalf("items = %d 条，期望 1", len(got.Items))
	}
	row := got.Items[0]
	want := []string{"id", "status", "trigger_type", "scheduled_at", "sent_at",
		"responded_at", "expires_at", "block_reason", "reject_reason",
		"journey_event_id", "created_at", "updated_at"}
	if len(row) != len(want) {
		t.Errorf("字段数 = %d，期望 %d（%v）⇒ 多一格是这一面长出了没人登记的数据外流",
			len(row), len(want), want)
	}
	for _, k := range want {
		if _, ok := row[k]; !ok {
			t.Errorf("缺字段 %q ⇒ 前端/脚本按名取值会静默拿到 undefined（字段名两侧必须逐字）", k)
		}
	}
	// 没有正文那一格：审计面搬的是「发没发、为什么没发」，不是用户内容。
	for _, banned := range []string{"message", "eligibility_detail", "personal_model_id"} {
		if _, ok := row[banned]; ok {
			t.Errorf("审计面里出现了 %q ⇒ 每一次排障都多端走一份用户内容", banned)
		}
	}
	// 库里 NULL 的三格必须回 null。折成零值时间＝在审计面上凭空造一条 1970 年的发送记录。
	for _, k := range []string{"sent_at", "responded_at", "expires_at"} {
		if row[k] != nil {
			t.Errorf("%v = %v，期望 null ⇒ 缺数据被显示成一句断言", k, row[k])
		}
	}
	for _, k := range []string{"reject_reason"} {
		if row[k] != nil {
			t.Errorf("%v = %v，期望 null（工单点名的 skip_reason 在库里不存在，两格都是可空）",
				k, row[k])
		}
	}
	if row["scheduled_at"] == nil ||
		!strings.HasPrefix(row["scheduled_at"].(string), "2026-10-19T09:00:00") {
		t.Errorf("scheduled_at = %v，期望 2026-10-19T09:00:00Z ⇒ 有值那一格也得是原值",
			row["scheduled_at"])
	}
	// 顶层四格：裁剪要能自证（truncated），过滤器要能回显（status/stale）。
	var top map[string]json.RawMessage
	decodeData(t, env, &top)
	for _, k := range []string{"items", "truncated", "limit", "status", "stale"} {
		if _, ok := top[k]; !ok {
			t.Errorf("顶层缺 %q ⇒ 调用方无法判断自己拿到的是全部还是一页", k)
		}
	}
}

// ── 5. 裁剪不藏（N9-11 同一条纪律在审计面上的落点）───────────────────────────

func TestV3ProactiveAuditReportsTruncation(t *testing.T) {
	read := &stubProactiveAuditReader{rows: []domain.ProactiveAuditRecord{
		auditRow("p1"), auditRow("p2"), auditRow("p3"),
	}}
	h := auditServer(t, read, v3Token).Handler()
	_, env := do(t, h, "GET", auditPath+"?limit=2", "", goalHeaders())
	got := decodeAudit(t, env)
	if len(got.Items) != 2 || !got.Truncated {
		t.Errorf("limit=2 给了 3 行：items %d／truncated %v，期望 2/true ⇒ 少报而不说少报",
			len(got.Items), got.Truncated)
	}
	// 正向证人：没截断时 truncated 必须是 false（一个恒真的闸等于没有闸）。
	if _, env = do(t, h, "GET", auditPath+"?limit=9", "", goalHeaders()); decodeAudit(t, env).Truncated {
		t.Error("取回 3 行却报 truncated ⇒ 「后面还有」是假话，运维会白翻页")
	}
}

// ── 6. 领域错误不在这里编形状（一条桩错误 ⇒ 500 族，不是 200 空列表）──────────

func TestV3ProactiveAuditSurfacesReaderErrors(t *testing.T) {
	read := &stubProactiveAuditReader{err: context.DeadlineExceeded}
	h := auditServer(t, read, v3Token).Handler()
	s, env := do(t, h, "GET", auditPath, "", goalHeaders())
	if s == 200 || env.Code == 200 {
		t.Errorf("仓储报错却回 200（%d/%d）⇒ 库挂了与「这个人没有触达」在响应里同形", s, env.Code)
	}
}
