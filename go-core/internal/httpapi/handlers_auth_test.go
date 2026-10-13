package httpapi_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/auth"
)

type envelope struct {
	Code    int             `json:"code"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data"`
	TraceID string          `json:"trace_id"`
	UserID  string          `json:"user_id"`
}

func do(t *testing.T, handler http.Handler, method, path, body string, headers map[string]string) (int, envelope) {
	t.Helper()
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	var env envelope
	if err := json.Unmarshal(rec.Body.Bytes(), &env); err != nil {
		t.Fatalf("%s %s -> %q is not the envelope: %v", method, path, rec.Body.String(), err)
	}
	return rec.Code, env
}

const registerPayload = `{"email":"u@e.com","username":"unit_user","password":"passw0rd"}`

func TestEnvelopeKeyOrderAndDefaults(t *testing.T) {
	server, _, _ := newTestServer(t, "development")
	status, env := do(t, server.Handler(), "GET", "/health", "", nil)
	if status != 200 || env.Code != 200 || env.Message != "success" || env.UserID != "anonymous" {
		t.Fatalf("envelope = %+v", env)
	}
	body, err := json.Marshal(env)
	if err != nil {
		t.Fatal(err)
	}
	want := `{"code":200,"message":"success","data":{"status":"ok","app":"ClosenAI","version":"1.0.0"}`
	if !strings.HasPrefix(string(body), want) {
		t.Errorf("envelope key order drifted:\n %s\nwant prefix %s", body, want)
	}
}

func TestRegisterThenMeWithGoIssuedToken(t *testing.T) {
	server, _, _ := newTestServer(t, "development")
	handler := server.Handler()

	status, env := do(t, handler, "POST", "/api/v1/auth/register", registerPayload, nil)
	if status != 200 || env.Code != 200 {
		t.Fatalf("register = %d %+v", status, env)
	}
	var result auth.Result
	if err := json.Unmarshal(env.Data, &result); err != nil {
		t.Fatal(err)
	}
	if result.AccessToken == "" || result.RefreshToken == "" {
		t.Fatal("no tokens returned")
	}
	if got := field(t, env.Data, "user"); !strings.HasPrefix(got, `{"id":`) {
		t.Errorf("user object key order = %s", got)
	}
	if got := field(t, env.Data, "access_token"); !strings.HasPrefix(got, `"`) {
		t.Errorf("access_token = %s", got)
	}

	status, env = do(t, handler, "GET", "/api/v1/auth/me", "",
		map[string]string{"Authorization": "Bearer " + result.AccessToken})
	if status != 200 || env.Code != 200 {
		t.Fatalf("me = %d %+v", status, env)
	}
	if env.UserID != result.User.ID {
		t.Errorf("envelope user_id = %q, want %q", env.UserID, result.User.ID)
	}

	// A refresh token must not open a business endpoint: it degrades to anonymous.
	_, env = do(t, handler, "GET", "/api/v1/auth/me", "",
		map[string]string{"Authorization": "Bearer " + result.RefreshToken})
	if env.Code != apierr.CodeUserNotFound {
		t.Errorf("refresh token gave %d, want the 40401 fall-through", env.Code)
	}
}

func TestProductionAuthFailures(t *testing.T) {
	server, _, _ := newTestServer(t, "production")
	handler := server.Handler()

	_, env := do(t, handler, "GET", "/api/v1/auth/me", "", nil)
	if env.Code != apierr.CodeMissingToken {
		t.Errorf("missing token gave %d, want 40103", env.Code)
	}
	_, env = do(t, handler, "GET", "/api/v1/auth/me", "",
		map[string]string{"Authorization": "Bearer not.a.jwt"})
	if env.Code != apierr.CodeTokenInvalid {
		t.Errorf("garbage token gave %d, want 40102", env.Code)
	}

	// A negative access TTL produces a token whose exp is already in the past.
	expiredSigner := auth.NewSigner("golden-test-secret-golden-test-secret", -time.Hour, time.Hour)
	expired, err := expiredSigner.SignAccess("user-x")
	if err != nil {
		t.Fatal(err)
	}
	_, env = do(t, handler, "GET", "/api/v1/auth/me", "",
		map[string]string{"Authorization": "Bearer " + expired})
	if env.Code != apierr.CodeTokenExpired {
		t.Errorf("expired token gave %d, want 40101", env.Code)
	}

	// X-User-Id must not bypass authentication in production.
	_, env = do(t, handler, "GET", "/api/v1/auth/me", "",
		map[string]string{"X-User-Id": "someone-else"})
	if env.Code != apierr.CodeMissingToken {
		t.Errorf("X-User-Id bypass gave %d, want 40103", env.Code)
	}
}

// TestDevelopmentFallsBackToAnonymous pins today's non-enforced behaviour: no token
// means user_id "anonymous", which the store reports as 40401.
func TestDevelopmentFallsBackToAnonymous(t *testing.T) {
	server, _, _ := newTestServer(t, "development")
	_, env := do(t, server.Handler(), "GET", "/api/v1/auth/me", "", nil)
	if env.Code != apierr.CodeUserNotFound || env.UserID != "anonymous" {
		t.Errorf("dev me = code %d user_id %q, want 40401/anonymous", env.Code, env.UserID)
	}
}

func TestRegisterBusinessCodesOverHTTP(t *testing.T) {
	server, _, _ := newTestServer(t, "development")
	handler := server.Handler()

	if _, env := do(t, handler, "POST", "/api/v1/auth/register", registerPayload, nil); env.Code != 200 {
		t.Fatalf("register failed: %+v", env)
	}
	cases := []struct {
		body   string
		code   int
		status int
	}{
		{`{"email":"u@e.com","username":"other_user","password":"passw0rd"}`, apierr.CodeEmailAlreadyUsed, 409},
		{`{"email":"fresh@e.com","username":"unit_user","password":"passw0rd"}`, apierr.CodeUsernameAlreadyUsed, 409},
		{`{"email":"other@e.com","username":"other_user","password":"password"}`, apierr.CodePasswordTooWeak, 400},
	}
	for _, c := range cases {
		status, env := do(t, handler, "POST", "/api/v1/auth/register", c.body, nil)
		if env.Code != c.code || status != c.status {
			t.Errorf("register %s -> http %d code %d, want http %d code %d",
				c.body, status, env.Code, c.status, c.code)
		}
	}
	status, env := do(t, handler, "POST", "/api/v1/auth/login",
		`{"identifier":"u@e.com","password":"nope-nope1"}`, nil)
	if env.Code != apierr.CodeInvalidCredentials || status != 401 {
		t.Errorf("login -> http %d code %d, want 401/40104", status, env.Code)
	}
}

func TestUnknownPathKeepsEnvelope(t *testing.T) {
	server, _, _ := newTestServer(t, "development")
	status, env := do(t, server.Handler(), "GET", "/api/v1/nope", "", nil)
	if status != 404 || env.Code != apierr.CodeNotFound {
		t.Errorf("unknown path = %d %+v", status, env)
	}
}

func field(t *testing.T, raw json.RawMessage, name string) string {
	t.Helper()
	var m map[string]json.RawMessage
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatal(err)
	}
	return string(m[name])
}
