// P2 #21：读面 50003 必须把根因落进日志，否则线上 DB 故障只剩一个通用码。
package httpapi

import (
	"bytes"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestWriteDBErrorLogsRootCause(t *testing.T) {
	orig := slog.Default()
	defer slog.SetDefault(orig)
	var buf bytes.Buffer
	slog.SetDefault(slog.New(slog.NewJSONHandler(&buf, nil)))

	req := httptest.NewRequest(http.MethodGet, "/v1/memories", nil)
	rec := httptest.NewRecorder()
	writeDBError(rec, req, errors.New("pq: connection pool exhausted"))

	var line map[string]any
	if err := json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &line); err != nil {
		t.Fatalf("故障日志必须是可 grep 的 JSON 行：%v（原文 %q）", err, buf.String())
	}
	if line["msg"] != "go-api: db error" {
		t.Errorf("日志 msg 不对：%v", line["msg"])
	}
	if line["method"] != http.MethodGet || line["path"] != "/v1/memories" {
		t.Errorf("日志缺请求维度：%v", line)
	}
	if got := line["error"]; got != "pq: connection pool exhausted" {
		t.Errorf("根因没进日志，GET 排障仍然抓瞎：得到 %v", got)
	}

	var body map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("响应仍是标准错误信封：%v", err)
	}
	code, _ := body["code"].(float64)
	if code != 50003 {
		t.Errorf("对外仍是 50003，不因加日志改契约：%v（body=%v）", code, body)
	}
	if bytes.Contains(rec.Body.Bytes(), []byte("connection pool exhausted")) {
		t.Error("根因只进日志，不许回给客户端")
	}
}
