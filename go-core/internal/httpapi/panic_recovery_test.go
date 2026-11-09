// P2 #23：panic 恢复只能在「响应还没开始」时补 500 信封；
// http.ErrAbortHandler 必须原样上抛，不能吞成一个正常 500。
package httpapi

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
)

type headerCountingWriter struct {
	http.ResponseWriter
	codes []int
}

func (h *headerCountingWriter) WriteHeader(code int) {
	h.codes = append(h.codes, code)
	h.ResponseWriter.WriteHeader(code)
}

func runPanic(t *testing.T, h http.Handler) (*httptest.ResponseRecorder, *headerCountingWriter) {
	t.Helper()
	orig := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&bytes.Buffer{}, nil)))
	defer slog.SetDefault(orig)

	rec := httptest.NewRecorder()
	counter := &headerCountingWriter{ResponseWriter: rec}
	NewMiddleware(nil, false).Wrap(h).ServeHTTP(counter, httptest.NewRequest(http.MethodGet, "/v1/x", nil))
	return rec, counter
}

func TestPanicAfterWriteDoesNotWriteSecondEnvelope(t *testing.T) {
	rec, counter := runPanic(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"partial":true}`))
		panic("boom after body")
	}))

	if len(counter.codes) != 1 || counter.codes[0] != http.StatusOK {
		t.Fatalf("响应状态行只许写一次且保持已提交的 200，得到 %v", counter.codes)
	}
	if got := rec.Body.String(); got != `{"partial":true}` {
		t.Fatalf("panic 后不得再往同一响应追加信封，实际收到 %q", got)
	}
}

func TestPanicBeforeWriteStillWrites500Envelope(t *testing.T) {
	rec, counter := runPanic(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		panic("boom before body")
	}))

	if len(counter.codes) != 1 || counter.codes[0] != http.StatusInternalServerError {
		t.Fatalf("未写头时 panic 必须补一个 500 状态行，得到 %v", counter.codes)
	}
	var body map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("500 必须是标准信封：%v（body=%q）", err, rec.Body.String())
	}
	if code, _ := body["code"].(float64); code != 500 {
		t.Errorf("信封码应为 500，得到 %v", body["code"])
	}
}

func TestErrAbortHandlerIsRepanickedNotEnveloped(t *testing.T) {
	rec := httptest.NewRecorder()
	counter := &headerCountingWriter{ResponseWriter: rec}
	h := NewMiddleware(nil, false).Wrap(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		panic(http.ErrAbortHandler)
	}))

	defer func() {
		got := recover()
		if got != http.ErrAbortHandler {
			t.Fatalf("ErrAbortHandler 必须原样上抛给 net/http，得到 %v", got)
		}
		if len(counter.codes) != 0 || rec.Body.Len() != 0 {
			t.Errorf("断连信号不许写响应：codes=%v body=%q", counter.codes, rec.Body.String())
		}
	}()
	h.ServeHTTP(counter, httptest.NewRequest(http.MethodGet, "/v1/x", nil))
}
