package auth_test

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/auth"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store"
)

const testSecret = "unit-test-secret-unit-test-secret"

func newService() (*auth.Service, *store.FakeStore) {
	fake := store.NewFakeStore()
	signer := auth.NewSigner(testSecret, 30*time.Minute, 30*24*time.Hour)
	return auth.NewService(fake, signer, 30*24*time.Hour), fake
}

func registerInput() auth.RegisterInput {
	return auth.RegisterInput{
		Email:    "  Ada@Example.COM ",
		Username: "  ada_lovelace ",
		Password: "passw0rd",
	}
}

func TestRegisterStoresNormalizedUserAndRefreshRow(t *testing.T) {
	svc, fake := newService()
	result, err := svc.Register(context.Background(), registerInput())
	if err != nil {
		t.Fatalf("Register: %v", err)
	}
	if result.User.Email == nil || *result.User.Email != "ada@example.com" {
		t.Errorf("email = %v, want lower-cased", derefStr(result.User.Email))
	}
	if result.User.Username == nil || *result.User.Username != "ada_lovelace" {
		t.Errorf("username = %q", derefStr(result.User.Username))
	}
	if result.User.Tier != "free" || result.User.EmailVerified {
		t.Errorf("user = %+v, want tier=free email_verified=false", result.User)
	}
	rows := fake.RefreshTokens()
	if len(rows) != 1 {
		t.Fatalf("refresh rows = %d, want 1", len(rows))
	}
	if len(rows[0].TokenHash) != 64 {
		t.Errorf("token hash length = %d, want 64 (sha256 hex)", len(rows[0].TokenHash))
	}
	if rows[0].TokenHash != auth.HashRefreshToken(result.RefreshToken) {
		t.Error("stored hash does not match the returned refresh token")
	}
	if rows[0].ExpiresAt.Before(time.Now().Add(29 * 24 * time.Hour)) {
		t.Errorf("expires_at = %v, want ~30 days out", rows[0].ExpiresAt)
	}
}

func TestRegisterBusinessErrors(t *testing.T) {
	svc, _ := newService()
	ctx := context.Background()
	if _, err := svc.Register(ctx, registerInput()); err != nil {
		t.Fatal(err)
	}

	cases := []struct {
		name string
		in   auth.RegisterInput
		code int
		msg  string
	}{
		{
			name: "duplicate email",
			in:   auth.RegisterInput{Email: "  ADA@example.com ", Username: "someone_new", Password: "passw0rd"},
			code: apierr.CodeEmailAlreadyUsed,
			msg:  "该邮箱已注册 / Email already used",
		},
		{
			name: "duplicate username",
			in:   auth.RegisterInput{Email: "brand-new@example.com", Username: "ada_lovelace", Password: "passw0rd"},
			code: apierr.CodeUsernameAlreadyUsed,
			msg:  "用户名已被占用 / Username already used",
		},
		{
			name: "weak password",
			in:   auth.RegisterInput{Email: "fresh@example.com", Username: "fresh_user", Password: "password"},
			code: apierr.CodePasswordTooWeak,
			msg:  "密码至少 8 位，且需包含字母和数字 / Password too weak",
		},
	}
	for _, c := range cases {
		_, err := svc.Register(ctx, c.in)
		assertAPIError(t, c.name, err, c.code, c.msg)
	}
}

func TestLoginAcceptsEmailOrUsername(t *testing.T) {
	svc, _ := newService()
	ctx := context.Background()
	created, err := svc.Register(ctx, registerInput())
	if err != nil {
		t.Fatal(err)
	}
	for _, identifier := range []string{"ada@example.com", "  ADA@example.COM  ", "ada_lovelace"} {
		result, err := svc.Login(ctx, identifier, "passw0rd")
		if err != nil {
			t.Fatalf("login with %q: %v", identifier, err)
		}
		if result.User.ID != created.User.ID {
			t.Errorf("login with %q returned user %q", identifier, result.User.ID)
		}
	}

	invalid := "账号或密码错误 / Invalid credentials"
	_, err = svc.Login(ctx, "ada@example.com", "wrong-passw0rd")
	assertAPIError(t, "wrong password", err, apierr.CodeInvalidCredentials, invalid)
	_, err = svc.Login(ctx, "nobody@example.com", "passw0rd")
	assertAPIError(t, "unknown account", err, apierr.CodeInvalidCredentials, invalid)
}

func TestMeReturnsAuthUserResponse(t *testing.T) {
	svc, _ := newService()
	ctx := context.Background()
	created, err := svc.Register(ctx, registerInput())
	if err != nil {
		t.Fatal(err)
	}
	view, err := svc.Me(ctx, created.User.ID)
	if err != nil {
		t.Fatalf("Me: %v", err)
	}
	body, err := json.Marshal(view)
	if err != nil {
		t.Fatal(err)
	}
	want := `{"id":"` + view.ID + `","email":"ada@example.com","username":"ada_lovelace","name":null,"tier":"free","email_verified":false}`
	if string(body) != want {
		t.Errorf("user json =\n %s\nwant\n %s", body, want)
	}

	_, err = svc.Me(ctx, "does-not-exist")
	assertAPIError(t, "unknown user", err, apierr.CodeUserNotFound, "用户不存在 / User not found")
}

func assertAPIError(t *testing.T, name string, err error, code int, message string) {
	t.Helper()
	var e *apierr.Error
	if !asAPIError(err, &e) {
		t.Fatalf("%s: error = %v (%T), want *apierr.Error", name, err, err)
	}
	if e.Code != code || e.Message != message {
		t.Errorf("%s: code=%d status=%d msg=%q, want code=%d msg=%q", name, e.Code, e.Status, e.Message, code, message)
	}
	if e.Status != apierr.HTTPStatus(code) {
		t.Errorf("%s: http status = %d, want %d", name, e.Status, apierr.HTTPStatus(code))
	}
}

func asAPIError(err error, target **apierr.Error) bool {
	e, ok := err.(*apierr.Error)
	if ok {
		*target = e
	}
	return ok
}

func derefStr(s *string) string {
	if s == nil {
		return "<nil>"
	}
	return *s
}
