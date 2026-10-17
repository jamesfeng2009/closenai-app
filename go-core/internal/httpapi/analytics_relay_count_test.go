// D-G18（2026-10-17）：/analytics/batch 的计数只认真正送出去的那几条。
//
// 逐字契约（n8_replay_test.go 的 seeded 组）钉的是**中继成功**那一支——录制侧装了成功
// 假件，所以夹具永远给不出「SDK 收下失败」的字节。这里补的正是那三种夹具给不出的情形：
//   - 外发失败 ⇒ 落 dropped，而不是照原样算进 relayed；
//   - 没有中继通道（Relay 为 nil，对应 Python 那侧 posthog 没装或没配 key）⇒ 整批 dropped；
//   - 失败再多也不许把业务请求变成 500（红线：不因分析平台抖动让前端拿到错）。
//
// 期望值不拿被测代码自证：relayed/dropped 的判据写死成字面量，
// 而「两格互补」这条不变量按**请求里的事件条数**现数。
package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/posthog"
)

// consentedProfileReader 只回答 analytics 那一条依赖（users.analytics_consent）。
type consentedProfileReader struct {
	consent bool
	found   bool
}

func (c *consentedProfileReader) UserProfile(context.Context,
	string) (domain.UserProfile, error) {
	return domain.UserProfile{Found: c.found, AnalyticsConsent: c.consent, Language: "en"}, nil
}

// scriptedRelay 按**事件名**决定这条送不送得出去，所以并发派发下结果仍是确定的。
type scriptedRelay struct {
	mu   sync.Mutex
	seen []posthog.Event
	fail map[string]bool
}

func (r *scriptedRelay) Capture(_ context.Context, ev posthog.Event) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.seen = append(r.seen, ev)
	if r.fail[ev.Event] {
		return fmt.Errorf("posthog: capture returned HTTP %d", 502)
	}
	return nil
}

func (r *scriptedRelay) count() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.seen)
}

// batchData 取出响应里的两格计数。
func batchData(t *testing.T, body string) (relayed, dropped int) {
	t.Helper()
	var envelope struct {
		Code int `json:"code"`
		Data struct {
			Relayed int `json:"relayed"`
			Dropped int `json:"dropped"`
		} `json:"data"`
	}
	if err := json.Unmarshal([]byte(body), &envelope); err != nil {
		t.Fatalf("响应体不是信封：%v（%s）", err, body)
	}
	if envelope.Code != 200 {
		t.Fatalf("业务码 = %d，want 200（%s）", envelope.Code, body)
	}
	return envelope.Data.Relayed, envelope.Data.Dropped
}

// postBatch 用 X-User-Id 走 dev 口径（与夹具回放同一套鉴权降级，见 middleware.go 的 RequireBearer）。
func postBatch(t *testing.T, handler http.Handler, userID, body string) (int, string) {
	t.Helper()
	req := httptest.NewRequest("POST", "/api/v1/analytics/batch", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-User-Id", userID)
	req.Header.Set("X-Trace-Id", "00000000-0000-0000-0000-000000000001")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	return rec.Code, rec.Body.String()
}

// eventsBody 造 `{"events":[…]}`，事件名按次序 a、b、c…，每条都是合法载荷。
func eventsBody(n int) string {
	items := make([]string, 0, n)
	for i := 0; i < n; i++ {
		items = append(items, fmt.Sprintf(
			`{"event":"%s","occurred_at":"2026-10-01T00:00:00+00:00"}`, string(rune('a'+i))))
	}
	return `{"events":[` + strings.Join(items, ",") + `]}`
}

func newAnalyticsServer(t *testing.T, users UserProfileReader, relay Relay) http.Handler {
	t.Helper()
	// Moods 用 nil 就够：这条端点不读 mood_checkins，读到了也是新加依赖（下面单独钉）。
	return newN8Server(t, users, n8ForbiddenStore{t: t}, relay, nil, nil, nil, nil, nil).Handler()
}

func TestAnalyticsBatchCountsFollowTheRelayResult(t *testing.T) {
	cases := []struct {
		name                     string
		events                   int
		fail                     []string
		wantRelayed, wantDropped int
		wantCalls                int
	}{
		{name: "全部成功", events: 3, wantRelayed: 3, wantDropped: 0, wantCalls: 3},
		{name: "全部失败", events: 3, fail: []string{"a", "b", "c"},
			wantRelayed: 0, wantDropped: 3, wantCalls: 3},
		{name: "部分成功", events: 4, fail: []string{"b"},
			wantRelayed: 3, wantDropped: 1, wantCalls: 4},
		{name: "空批次", events: 0, wantRelayed: 0, wantDropped: 0, wantCalls: 0},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			relay := &scriptedRelay{fail: map[string]bool{}}
			for _, name := range c.fail {
				relay.fail[name] = true
			}
			handler := newAnalyticsServer(t,
				&consentedProfileReader{found: true, consent: true}, relay)
			status, body := postBatch(t, handler, "u1", eventsBody(c.events))
			if status != 200 {
				t.Fatalf("HTTP %d，want 200（%s）——外发失败绝不能变成业务失败", status, body)
			}
			relayed, dropped := batchData(t, body)
			if relayed != c.wantRelayed || dropped != c.wantDropped {
				t.Errorf("计数 = {relayed:%d, dropped:%d}，want {relayed:%d, dropped:%d}；体：%s",
					relayed, dropped, c.wantRelayed, c.wantDropped, body)
			}
			if relayed+dropped != c.events {
				t.Errorf("两格不互补：%d+%d != 请求里的 %d 条", relayed, dropped, c.events)
			}
			if got := relay.count(); got != c.wantCalls {
				t.Errorf("真的发起了 %d 次外发，want %d（计数改了不该连带少发/多发）", got, c.wantCalls)
			}
		})
	}
}

// TestAnalyticsBatchWithoutRelayChannelCountsNothing 是 Go 侧的「posthog 没装/没配 key」：
// Relay 为 nil 时**一条都不算送出去**，但响应仍是 200＋计数。
// 改造前这里回的是 `relayed:N`——那正是 D-G18 要收掉的那句谎话。
func TestAnalyticsBatchWithoutRelayChannelCountsNothing(t *testing.T) {
	handler := newAnalyticsServer(t, &consentedProfileReader{found: true, consent: true}, nil)
	status, body := postBatch(t, handler, "u1", eventsBody(2))
	if status != 200 {
		t.Fatalf("HTTP %d，want 200（%s）", status, body)
	}
	if !strings.Contains(body, `"relayed":0,"dropped":2`) {
		t.Errorf("没有中继通道时该如实回报没投递，实得 %s", body)
	}
}

// TestAnalyticsBatchUnconsentedSkipsRelay 保住改造时**没动**的那一支：
// 未同意／用户不存在都不该有一次外发，计数整批落 dropped。
// 这条存在的理由是防止「按发送结果计数」被写成「先外发再看 consent」。
func TestAnalyticsBatchUnconsentedSkipsRelay(t *testing.T) {
	for name, profile := range map[string]*consentedProfileReader{
		"未同意":   {found: true, consent: false},
		"用户不存在": {found: false, consent: true},
	} {
		t.Run(name, func(t *testing.T) {
			relay := &scriptedRelay{fail: map[string]bool{}}
			handler := newAnalyticsServer(t, profile, relay)
			status, body := postBatch(t, handler, "u1", eventsBody(3))
			if status != 200 {
				t.Fatalf("HTTP %d（%s）", status, body)
			}
			relayed, dropped := batchData(t, body)
			if relayed != 0 || dropped != 3 {
				t.Errorf("计数 = {relayed:%d, dropped:%d}，want {0, 3}", relayed, dropped)
			}
			if got := relay.count(); got != 0 {
				t.Errorf("未同意的批次外发了 %d 条，want 0", got)
			}
		})
	}
}

// TestPosthogUnconfiguredIsNotSuccess 在 internal/posthog 包里（同一个判据不该跨包代测），
// 这里只钉 handler 那一层：计数跟着 Relay.Capture 的返回值走。
