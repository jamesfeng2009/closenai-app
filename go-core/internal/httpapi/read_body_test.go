// P2 #22：请求体超限必须回 413，不许被 LimitReader 截断后伪装成 JSON 不合形。
package httpapi

import (
	"bytes"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestReadBodyRejectsOversizeWith413(t *testing.T) {
	oversized := bytes.Repeat([]byte("a"), maxBodyBytes+1)
	req := httptest.NewRequest(http.MethodPost, "/v1/analytics/batch", bytes.NewReader(oversized))
	rec := httptest.NewRecorder()

	raw, err := readBody(req)
	if err == nil {
		t.Fatalf("超限请求体必须报错，实际读到 %d 字节", len(raw))
	}
	var maxErr *http.MaxBytesError
	if !errors.As(err, &maxErr) {
		t.Fatalf("错误类型必须是 *http.MaxBytesError（便于统一映射 413），得到 %T: %v", err, err)
	}

	writeJSONError(rec, req, asAPIError(err))
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("HTTP 状态必须是 413，得到 %d（body=%s）", rec.Code, rec.Body.String())
	}
	var body map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("413 也必须是标准信封：%v", err)
	}
	if code, _ := body["code"].(float64); code != 40001 {
		t.Errorf("沿用 40001 不新造码，得到 %v", body["code"])
	}
}

func TestReadBodyAllowsExactlyLimit(t *testing.T) {
	atLimit := bytes.Repeat([]byte("a"), maxBodyBytes)
	req := httptest.NewRequest(http.MethodPost, "/v1/x", bytes.NewReader(atLimit))
	raw, err := readBody(req)
	if err != nil {
		t.Fatalf("恰好等于上限应放行：%v", err)
	}
	if len(raw) != maxBodyBytes {
		t.Fatalf("读到的字节数不对：%d", len(raw))
	}
}
