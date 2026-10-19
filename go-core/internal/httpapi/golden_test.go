package httpapi_test

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/auth"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/config"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/httpapi"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store"
)

type golden struct {
	Name        string  `json:"name"`
	Method      string  `json:"method"`
	Path        string  `json:"path"`
	RequestRaw  *string `json:"request_raw"`
	Status      int     `json:"status"`
	ResponseTex string  `json:"response_text"`
}

type goldenFile struct {
	TraceID string   `json:"trace_id"`
	Cases   []golden `json:"cases"`
}

// newTestServer builds the slice with the in-memory store, mirroring config defaults.
func newTestServer(t *testing.T, environment string) (*httpapi.Server, *auth.Signer, *store.FakeStore) {
	t.Helper()
	fake := store.NewFakeStore()
	cfg := config.Config{AppName: "ClosenAI", Environment: environment, HTTPAddr: ":0"}
	signer := auth.NewSigner("golden-test-secret-golden-test-secret", 30*time.Minute, 30*24*time.Hour)
	service := auth.NewService(fake, signer, 30*24*time.Hour)
	return httpapi.NewServer(cfg, signer, service, nil, nil, nil, nil, nil, nil, nil, nil), signer, fake
}

func loadGoldens(t *testing.T) goldenFile {
	t.Helper()
	path := filepath.Join("..", "..", "testdata", "http_golden.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s (run scripts/gen_go_http_goldens.py): %v", path, err)
	}
	var file goldenFile
	if err := json.Unmarshal(raw, &file); err != nil {
		t.Fatalf("decode goldens: %v", err)
	}
	if len(file.Cases) == 0 {
		t.Fatal("no golden cases")
	}
	return file
}

// TestHTTPGoldens replays every request FastAPI answered in
// go-core/testdata/http_golden.json and requires the byte-identical body.
func TestHTTPGoldens(t *testing.T) {
	file := loadGoldens(t)
	server, _, _ := newTestServer(t, "development")
	handler := server.Handler()

	for _, c := range file.Cases {
		t.Run(c.Name, func(t *testing.T) {
			var body io.Reader
			if c.RequestRaw != nil {
				body = bytes.NewBufferString(*c.RequestRaw)
			}
			req := httptest.NewRequest(c.Method, c.Path, body)
			req.Header.Set("X-Trace-Id", file.TraceID)
			if c.RequestRaw != nil {
				req.Header.Set("Content-Type", "application/json")
			}
			rec := httptest.NewRecorder()
			handler.ServeHTTP(rec, req)

			if rec.Code != c.Status {
				t.Errorf("status = %d, want %d", rec.Code, c.Status)
			}
			if rec.Body.String() != c.ResponseTex {
				t.Errorf("body mismatch\n go: %s\n py: %s", rec.Body.String(), c.ResponseTex)
			}
			if got := rec.Header().Get("X-Trace-Id"); got != file.TraceID {
				t.Errorf("X-Trace-Id = %q, want %q", got, file.TraceID)
			}
			if got := rec.Header().Get("Content-Type"); got != "application/json" {
				t.Errorf("Content-Type = %q", got)
			}
		})
	}
}
