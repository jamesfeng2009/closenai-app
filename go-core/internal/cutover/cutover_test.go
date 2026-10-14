// 灰度闸的测：N8 的「Ingress 灰度 1%→100%」落在 go-api 侧，所以这一小包是切流
// 风险控制机制本身，不是可有可无的工具测。三件事必须可证：
//  1. 粘滞：同一 user_id 在灰度期反复请求必须落同一侧，否则 1% 的样本聚不起来；
//  2. 单调：百分比从小到大，被选中的集合只增不减（否则「调到 10%」会把用户甩回 Python）；
//  3. 逐字转交：未选中的请求必须原样交给 py-api，Go 侧一行业务逻辑都不跑——
//     这条决定了「0% 是真的 0%」。
package cutover

import (
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func mustGate(t *testing.T, upstream string, percents map[string]int) *Gate {
	t.Helper()
	g, err := New(upstream, percents)
	if err != nil {
		t.Fatalf("New(%q, %v): %v", upstream, percents, err)
	}
	return g
}

// TestNilGateAnswersEveryDomain 是「没接开关 ≠ 无人应答」那条口径：
// 本地开发、单测、还没配灰度的部署都走这条。
func TestNilGateAnswersEveryDomain(t *testing.T) {
	var g *Gate
	if !g.Selected("analytics", "any-user") {
		t.Fatal("nil gate 必须按 100% 由 Go 回答")
	}
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/analytics/batch", nil)
	if g.Delegate(rec, req, "analytics", "any-user") {
		t.Fatal("nil gate 不该转交")
	}
}

func TestNewRejectsPercentOutOfRange(t *testing.T) {
	for _, pct := range []int{-1, 101, 1000} {
		_, err := New("http://py.example", map[string]int{"analytics": pct})
		if err == nil {
			t.Fatalf("percent %d 应当拒绝启动，却构造成功", pct)
		}
		var re *RangeError
		if !errors.As(err, &re) {
			t.Fatalf("percent %d 的错误类型 = %T，want *RangeError", pct, err)
		}
		if re.Domain != "analytics" || re.Percent != pct {
			t.Fatalf("错误里没有可定位的域与数值：%+v", re)
		}
	}
}

// TestNewRequiresUpstreamBelowFullCut 是那句「灰度百分比未到就无处可转 ⇒ 拒绝启动」：
// 一个以为会转交其实无处可转的开关，比没有开关更糟。
func TestNewRequiresUpstreamBelowFullCut(t *testing.T) {
	if _, err := New("", map[string]int{"analytics": 50}); err == nil {
		t.Fatal("50% 且 CLOSEN_PY_API_URL 为空必须报错")
	}
	if _, err := New("not a url", map[string]int{"analytics": 50}); err == nil {
		t.Fatal("非法上游地址必须报错")
	}
	if _, err := New("http://py.example", map[string]int{"analytics": 0}); err != nil {
		t.Fatalf("0%% 配了上游应当可用（0%% 也要转交）：%v", err)
	}
	// 100% 时没有任何请求需要转交，缺上游不该拦启动。
	if _, err := New("", map[string]int{"analytics": 100, "discovery": 100}); err != nil {
		t.Fatalf("全量切流无需上游：%v", err)
	}
	if _, err := New("", map[string]int{}); err != nil {
		t.Fatalf("空百分比表（还没切任何域）无需上游：%v", err)
	}
}

// TestSelectedIsSticky 用同一个 user_id 连打 200 次：结果必须一模一样。
func TestSelectedIsSticky(t *testing.T) {
	g := mustGate(t, "http://py.example", map[string]int{"analytics": 37})
	const userID = "n8b1-freq3"
	first := g.Selected("analytics", userID)
	for i := 0; i < 200; i++ {
		if g.Selected("analytics", userID) != first {
			t.Fatalf("第 %d 次落点变了——粘滞失效，灰度样本无法聚合", i)
		}
	}
}

// TestSelectedIsMonotoneInPercent 保证「1%→10%→50%→100%」是把用户**接过来**，
// 而不是在两侧来回甩。
func TestSelectedIsMonotoneInPercent(t *testing.T) {
	users := make([]string, 0, 2000)
	for i := 0; i < 2000; i++ {
		users = append(users, "u-"+itoa(i))
	}
	previous := map[string]bool{}
	for _, pct := range []int{0, 1, 10, 37, 50, 99, 100} {
		g := mustGate(t, "http://py.example", map[string]int{"analytics": pct})
		var selected int
		for _, u := range users {
			got := g.Selected("analytics", u)
			if !got && previous[u] {
				t.Fatalf("percent=%d 把 %s 甩回了 Python（更低百分档已选中过）——单调性失效", pct, u)
			}
			if got {
				selected++
				previous[u] = true
			}
		}
		switch {
		case pct == 0 && selected != 0:
			t.Fatalf("0%% 却有 %d 个用户由 Go 回答", selected)
		case pct == 100 && selected != len(users):
			t.Fatalf("100%% 只有 %d/%d 由 Go 回答", selected, len(users))
		case pct == 1 && (selected < 1 || selected > 120):
			t.Fatalf("1%% 选中 %d/2000，落点分布不像 1%%", selected)
		case pct == 50 && (selected < 800 || selected > 1200):
			t.Fatalf("50%% 选中 %d/2000，分布明显偏斜", selected)
		}
	}
}

// TestUnknownDomainIsFullyGoServed 钉住「不在表里的域」的口径：
// 由 Go 全量回答（这是本地开发与单测的前提），而不是无人应答。
func TestUnknownDomainIsFullyGoServed(t *testing.T) {
	g := mustGate(t, "http://py.example", map[string]int{"analytics": 0})
	if !g.Selected("discovery", "someone") {
		t.Fatal("未配置的域必须默认由 Go 回答")
	}
}

// TestDelegateForwardsVerbatim 是「0% 就是真 0%」的证明：
// 上游收到的必须是原始方法、路径、查询串、请求头与请求体，回给客户端的必须是上游字节。
func TestDelegateForwardsVerbatim(t *testing.T) {
	var got struct {
		method, path, rawQuery, body, host, trace, user, contentType string
	}
	py := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got.method, got.path, got.rawQuery = r.Method, r.URL.Path, r.URL.RawQuery
		got.host = r.Host
		got.trace = r.Header.Get("X-Trace-Id")
		got.user = r.Header.Get("X-User-Id")
		got.contentType = r.Header.Get("Content-Type")
		b, _ := io.ReadAll(r.Body)
		got.body = string(b)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusTeapot)
		_, _ = io.WriteString(w, `{"code":200,"message":"from py-api"}`)
	}))
	defer py.Close()

	g := mustGate(t, py.URL, map[string]int{"analytics": 0})
	req := httptest.NewRequest(http.MethodPost,
		"/api/v1/analytics/batch?verbose=1", strings.NewReader(`{"events":[]}`))
	req.Header.Set("X-Trace-Id", "trace-abc")
	req.Header.Set("X-User-Id", "n8b1-consent-on")
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()

	if !g.Delegate(rec, req, "analytics", "n8b1-consent-on") {
		t.Fatal("0% 却没有转交")
	}
	if rec.Code != http.StatusTeapot {
		t.Errorf("status = %d，want 上游的 %d", rec.Code, http.StatusTeapot)
	}
	if rec.Body.String() != `{"code":200,"message":"from py-api"}` {
		t.Errorf("响应体不是上游字节：%s", rec.Body.String())
	}
	if rec.Header().Get("Content-Type") != "application/json" {
		t.Errorf("上游 Content-Type 没带回来：%q", rec.Header().Get("Content-Type"))
	}
	if got.method != http.MethodPost || got.path != "/api/v1/analytics/batch" ||
		got.rawQuery != "verbose=1" {
		t.Errorf("上游收到 %s %s?%s", got.method, got.path, got.rawQuery)
	}
	if got.body != `{"events":[]}` {
		t.Errorf("请求体没原样送出：%q", got.body)
	}
	if got.trace != "trace-abc" || got.user != "n8b1-consent-on" ||
		got.contentType != "application/json" {
		t.Errorf("请求头丢失：trace=%q user=%q ct=%q", got.trace, got.user, got.contentType)
	}
	// 原始 Host 必须保留（Rewrite 里 pr.Out.Host = pr.In.Host 那一行）：
	// httptest.NewRequest 的 Host 固定是 example.com，上游收到的必须就是它。
	if got.host != "example.com" {
		t.Errorf("上游 Host = %q，want 原始 example.com", got.host)
	}
}

// TestDelegateFalseWhenSelected 反方向：被选中的请求**不该**多一跳代理。
func TestDelegateFalseWhenSelected(t *testing.T) {
	py := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Error("已切给 Go 的请求被转交给了 py-api")
	}))
	defer py.Close()
	g := mustGate(t, py.URL, map[string]int{"analytics": 100})
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/analytics/batch", nil)
	if g.Delegate(rec, req, "analytics", "anyone") {
		t.Fatal("Selected 为真时 Delegate 必须返回 false")
	}
	if rec.Body.Len() != 0 {
		t.Errorf("未转交却写了响应体：%q", rec.Body.String())
	}
}

func itoa(i int) string {
	if i == 0 {
		return "0"
	}
	var digits []byte
	for i > 0 {
		digits = append([]byte{byte('0' + i%10)}, digits...)
		i /= 10
	}
	return string(digits)
}
