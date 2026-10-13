package httpapi

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"net/http"
	"os"
	"runtime/debug"
	"strings"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/auth"
)

const (
	TraceHeader = "X-Trace-Id"
	anonymous   = "anonymous"
)

type ctxKey int

const (
	ctxTraceID ctxKey = iota
	ctxUserID
)

type requestContext struct {
	traceID string
	userID  string
}

func fromContext(ctx context.Context) requestContext {
	rc := requestContext{traceID: "", userID: anonymous}
	if v, ok := ctx.Value(ctxTraceID).(string); ok {
		rc.traceID = v
	}
	if v, ok := ctx.Value(ctxUserID).(string); ok {
		rc.userID = v
	}
	return rc
}

// Middleware wires trace propagation, user-id resolution and panic recovery around
// the mux, reproducing backend/core/middleware.py::TraceIdMiddleware plus the
// dev/prod split documented at the top of backend/core/auth.py.
type Middleware struct {
	signer      *auth.Signer
	enforceAuth bool
}

func NewMiddleware(signer *auth.Signer, enforceAuth bool) *Middleware {
	return &Middleware{signer: signer, enforceAuth: enforceAuth}
}

func (m *Middleware) Wrap(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if rec := recover(); rec != nil {
				// 500 envelope matches the unhandled_exception_handler in
				// backend/core/middleware.py; the stack goes to stderr only.
				fmt.Fprintf(os.Stderr, "panic: %v\n%s\n", rec, debug.Stack())
				rc := fromContext(r.Context())
				WriteError(w, apierr.New(apierr.CodeInternalError, "服务器内部错误 / Internal server error"), rc.traceID, rc.userID)
			}
		}()

		traceID := r.Header.Get(TraceHeader)
		if traceID == "" {
			traceID = newTraceID()
		}
		w.Header().Set(TraceHeader, traceID)
		ctx := context.WithValue(r.Context(), ctxTraceID, traceID)

		userID := m.resolveUserID(r)
		ctx = context.WithValue(ctx, ctxUserID, userID)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// resolveUserID reproduces resolve_user_id_from_request: bearer first, then
// X-User-Id in development only, then anonymous.
func (m *Middleware) resolveUserID(r *http.Request) string {
	if token := bearerToken(r); token != "" {
		if claims, err := m.signer.Verify(token); err == nil && claims.Sub != "" && claims.Type != auth.TypeRefresh {
			return claims.Sub
		}
	}
	if !m.enforceAuth {
		if x := r.Header.Get("X-User-Id"); x != "" {
			return x
		}
	}
	return anonymous
}

// RequireBearer mirrors get_current_user: production is fail-closed (40103 missing,
// 40102 invalid, 40101 expired), development degrades to the id the middleware
// already resolved, which is X-User-Id or "anonymous".
func (m *Middleware) RequireBearer(r *http.Request) (string, *apierr.Error) {
	token := bearerToken(r)
	if m.enforceAuth {
		if token == "" {
			return "", apierr.New(apierr.CodeMissingToken, "缺少身份凭证 / Missing token")
		}
		return m.strictDecode(token)
	}
	fallback := fromContext(r.Context()).userID
	if token == "" {
		return fallback, nil
	}
	claims, err := m.signer.Verify(token)
	if err != nil || claims.Type == auth.TypeRefresh || claims.Sub == "" {
		return fallback, nil
	}
	return claims.Sub, nil
}
func bearerToken(r *http.Request) string {
	authHeader := r.Header.Get("Authorization")
	if len(authHeader) > 7 && strings.EqualFold(authHeader[:7], "bearer ") {
		return strings.TrimSpace(authHeader[7:])
	}
	return ""
}

func (m *Middleware) strictDecode(token string) (string, *apierr.Error) {
	claims, err := m.signer.Verify(token)
	if errors.Is(err, auth.ErrTokenExpired) {
		return "", apierr.New(apierr.CodeTokenExpired, "登录已过期，请重新登录 / Session expired")
	}
	if err != nil || claims.Type == auth.TypeRefresh || claims.Sub == "" {
		return "", apierr.New(apierr.CodeTokenInvalid, "无效的身份凭证 / Invalid token")
	}
	return claims.Sub, nil
}

func newTraceID() string {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "00000000-0000-4000-8000-000000000000"
	}
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	h := hex.EncodeToString(b)
	return fmt.Sprintf("%s-%s-%s-%s-%s", h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])
}
