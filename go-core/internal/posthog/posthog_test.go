// Package posthog 的返回值语义闸（D-G18，2026-10-17）。
//
// handler 的 relayed/dropped 计数现在**只看 Capture 的返回值**，所以「nil 是什么意思」
// 必须在这一层就说清楚，否则只是把谎话从一层搬到另一层：
//   - 没配 key（空 key 与 nil client 两种）⇒ ErrNotConfigured，一条都没出门，且一次网络都不许碰；
//   - 端点回 2xx ⇒ nil，这才是「送出去了」；
//   - 端点回非 2xx ⇒ error，落 dropped。
//
// 三条缺一条都会让上面的计数失去意义：只测「未配置回错」而不测「2xx 才回 nil」，
// 那么「永远回错」的实现同样绿。
package posthog

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

func TestCaptureWithoutKeyIsAnError(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		hits.Add(1)
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(srv.Close)

	for name, client := range map[string]*Client{
		"空 key":      New("", srv.URL),
		"nil client": nil,
	} {
		if err := client.Capture(context.Background(), Event{Event: "a"}); !errors.Is(err,
			ErrNotConfigured) {
			t.Errorf("%s：Capture 回 %v，want ErrNotConfigured", name, err)
		}
	}
	if got := hits.Load(); got != 0 {
		t.Errorf("未配置却打了 %d 次 capture 端点——no-op 的口径是不能有外发", got)
	}
}

func TestCaptureNilOnlyWhenDelivered(t *testing.T) {
	var status atomic.Int32
	var lastBody atomic.Value
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		lastBody.Store(string(body))
		w.WriteHeader(int(status.Load()))
	}))
	t.Cleanup(srv.Close)

	client := New("phk_test", srv.URL+"/")
	if !client.Configured() {
		t.Fatal("New 之后 Configured() 是 false：key 没落到客户端里")
	}
	for _, c := range []struct {
		name      string
		code      int
		wantError bool
	}{
		{name: "200 收下", code: http.StatusOK},
		{name: "202 收下", code: http.StatusAccepted},
		{name: "502 没收下", code: http.StatusBadGateway, wantError: true},
	} {
		status.Store(int32(c.code))
		err := client.Capture(context.Background(), Event{Event: "checkin", DistinctID: "u1"})
		if (err != nil) != c.wantError {
			t.Errorf("%s：err=%v，wantError=%v", c.name, err, c.wantError)
		}
	}
	// api_key 由客户端注入（与 posthog-python 的 project_api_key 同构）；
	// 尾斜杠不能拼出 //capture/，所以顺带给 host 加了斜杠。
	body, _ := lastBody.Load().(string)
	if !strings.Contains(body, `"api_key":"phk_test"`) {
		t.Errorf("最后一次请求体里没有 api_key：%s", body)
	}
}
