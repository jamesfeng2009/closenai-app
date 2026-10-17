package httpapi

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"regexp"
	"runtime/debug"
	"strings"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/auth"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/obs"
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
		// 批 T-C（工作单 T-8）：入站值先消毒再采用——不合形的头视同缺失，
		// 重新生成。不拦 CR/LF 的话，一个恶意头名就是一行伪造日志。
		traceID := sanitizeTraceID(r.Header.Get(TraceHeader))
		if traceID == "" {
			traceID = newTraceID()
		}
		userID := m.resolveUserID(r)

		ctx := context.WithValue(r.Context(), ctxTraceID, traceID)
		ctx = context.WithValue(ctx, ctxUserID, userID)
		// 载体跨层可见：domain 让 T-B 的出箱行拿到 trace，obs 让每条日志行拿到两字段。
		ctx = domain.WithTraceID(ctx, traceID)
		ctx = obs.WithUser(ctx, userID)

		sw := &statusWriter{ResponseWriter: w, status: http.StatusOK}
		start := time.Now()
		defer func() {
			if rec := recover(); rec != nil {
				// 500 envelope matches the unhandled_exception_handler in
				// backend/core/middleware.py; the stack goes to stderr only.
				fmt.Fprintf(os.Stderr, "panic: %v\n%s\n", rec, debug.Stack())
				slog.ErrorContext(ctx, "go-api: panic recovered", slog.Any("recover", rec))
				WriteError(sw, apierr.New(apierr.CodeInternalError, "服务器内部错误 / Internal server error"), traceID, userID)
			}
			// 访问日志一行/请求，panic 路径也要有（defer 在前＝先进栈后出，恒打）。
			slog.InfoContext(ctx, "go-api: access",
				slog.String("method", r.Method),
				slog.String("path", r.URL.Path),
				slog.Int("status", sw.status),
				slog.Int64("duration_ms", time.Since(start).Milliseconds()),
			)
		}()
		sw.Header().Set(TraceHeader, traceID)
		next.ServeHTTP(sw, r.WithContext(ctx))
	})
}

// traceIDShape 与 backend/core/middleware.py 的消毒口径逐字同形：
// 可打印安全字符、1..128 长。换掉「照收任意字符串」的旧行为是 T-8 的判据本体。
var traceIDShape = regexp.MustCompile(`^[A-Za-z0-9._-]{1,128}$`)

func sanitizeTraceID(raw string) string {
	if traceIDShape.MatchString(raw) {
		return raw
	}
	return ""
}

// statusWriter 只为访问日志记最终状态码；其余透传。
type statusWriter struct {
	http.ResponseWriter
	status int
}

func (w *statusWriter) WriteHeader(code int) {
	w.status = code
	w.ResponseWriter.WriteHeader(code)
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
