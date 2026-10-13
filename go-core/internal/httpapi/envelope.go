package httpapi

import (
	"bytes"
	"encoding/json"
	"net/http"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
)

// Envelope is the frozen response body: exactly five keys in this order
// (contracts/endpoints.md §一, backend/core/response.py::success).
// Field order is load-bearing for the byte-for-byte parity test against FastAPI.
type Envelope struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Data    any    `json:"data"`
	TraceID string `json:"trace_id"`
	UserID  string `json:"user_id"`
}

func Success(data any, message, traceID, userID string) Envelope {
	if message == "" {
		message = "success"
	}
	return Envelope{Code: apierr.CodeOK, Message: message, Data: data, TraceID: traceID, UserID: userID}
}

// Marshal renders the envelope the way Starlette's JSONResponse does: compact
// separators, raw UTF-8 (ensure_ascii=False), no HTML escaping, no trailing newline.
func (e Envelope) Marshal() ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(e); err != nil {
		return nil, err
	}
	return bytes.TrimRight(buf.Bytes(), "\n"), nil
}

// WriteSuccess sends a 200 envelope.
func WriteSuccess(w http.ResponseWriter, data any, traceID, userID string) {
	writeEnvelope(w, http.StatusOK, Success(data, "", traceID, userID))
}

// WriteError sends the failure envelope for a business error.
func WriteError(w http.ResponseWriter, e *apierr.Error, traceID, userID string) {
	writeEnvelope(w, e.Status, Envelope{
		Code: e.Code, Message: e.Message, Data: e.Data, TraceID: traceID, UserID: userID,
	})
}

func writeEnvelope(w http.ResponseWriter, status int, e Envelope) {
	body, err := e.Marshal()
	if err != nil {
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}
