// Package store integration tests run the hand-written SQL against the real
// PostgreSQL schema produced by `alembic upgrade head`. They are the only proof
// that the statements in users.go match the live DDL (column list, NOT NULL
// defaults, and the soft-delete partial unique indexes that carry the 409 codes).
//
// They skip unless TEST_DATABASE_URL is set, e.g.
//
//	TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/closenai \
//	    go test ./internal/store/
package store

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/auth"
)

func poolForTest(t *testing.T) *pgxpool.Pool {
	t.Helper()
	raw := os.Getenv("TEST_DATABASE_URL")
	if raw == "" {
		t.Skip("TEST_DATABASE_URL not set")
	}
	// Same translation cmd/go-api applies, so one DATABASE_URL serves both stacks.
	url := strings.Replace(raw, "postgresql+asyncpg://", "postgresql://", 1)
	pool, err := pgxpool.New(context.Background(), url)
	if err != nil {
		t.Fatalf("pool: %v", err)
	}
	t.Cleanup(pool.Close)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := pool.Ping(ctx); err != nil {
		t.Fatalf("ping %s: %v", strings.SplitN(url, "@", 2)[1], err)
	}
	return pool
}

func cleanupUser(t *testing.T, pool *pgxpool.Pool, userID string) {
	t.Helper()
	ctx := context.Background()
	// refresh_tokens has an FK to users, so the child row goes first.
	if _, err := pool.Exec(ctx, `DELETE FROM refresh_tokens WHERE user_id = $1`, userID); err != nil {
		t.Errorf("cleanup refresh_tokens: %v", err)
	}
	if _, err := pool.Exec(ctx, `DELETE FROM users WHERE id = $1`, userID); err != nil {
		t.Errorf("cleanup users: %v", err)
	}
}

func TestRegisterAndLoginAgainstRealSchema(t *testing.T) {
	pool := poolForTest(t)
	pg := NewPG(pool)
	signer := auth.NewSigner("integration-test-secret-integration-test", 30*time.Minute, 30*24*time.Hour)
	svc := auth.NewService(pg, signer, 30*24*time.Hour)
	stamp := time.Now().UnixNano()
	email := fmt.Sprintf("gon1-%d@example.test", stamp)
	username := fmt.Sprintf("gon1_%d", stamp%1_000_000_000)
	ctx := context.Background()

	result, err := svc.Register(ctx, auth.RegisterInput{
		Email: "  " + strings.ToUpper(email) + " ", Username: username,
		Password: "passw0rd", Language: "zh-CN",
	})
	if err != nil {
		t.Fatalf("register: %v", err)
	}
	userID := result.User.ID
	t.Cleanup(func() { cleanupUser(t, pool, userID) })

	if result.User.Tier != "free" {
		t.Errorf("tier = %q, alembic default is 'free'", result.User.Tier)
	}
	if deref(result.User.Email) != email {
		t.Errorf("email = %q, want normalised %q", deref(result.User.Email), email)
	}

	// The row Python will read during the grey-rolling window.
	stored, err := pg.GetUserByEmail(ctx, email)
	if err != nil {
		t.Fatalf("get by email: %v", err)
	}
	if stored == nil || stored.ID != userID {
		t.Fatalf("stored row not readable: %+v", stored)
	}
	var passwordHash string
	if err := pool.QueryRow(ctx, `SELECT password_hash FROM users WHERE id = $1`, userID).Scan(&passwordHash); err != nil {
		t.Fatalf("scan password_hash: %v", err)
	}
	if !strings.HasPrefix(passwordHash, "$argon2id$v=19$m=65536,t=3,p=4$") {
		t.Errorf("password_hash prefix drifts from argon2-cffi defaults: %q", passwordHash[:min(40, len(passwordHash))])
	}
	if !auth.VerifyPassword("passw0rd", passwordHash) {
		t.Error("stored hash does not verify")
	}

	// refresh_tokens: sha256 hex row, keyed by the jti inside the token.
	// Checked before the logins below, which each issue their own row.
	var tokenHash string
	var expiresAt time.Time
	var rowCount int
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM refresh_tokens WHERE user_id = $1`, userID).Scan(&rowCount); err != nil {
		t.Fatalf("count refresh rows: %v", err)
	}
	if rowCount != 1 {
		t.Errorf("refresh_tokens rows = %d, want 1", rowCount)
	}
	if err := pool.QueryRow(ctx,
		`SELECT token_hash, expires_at FROM refresh_tokens WHERE user_id = $1`, userID,
	).Scan(&tokenHash, &expiresAt); err != nil {
		t.Fatalf("scan refresh row: %v", err)
	}
	if tokenHash != auth.HashRefreshToken(result.RefreshToken) {
		t.Error("stored token_hash != sha256(refresh token)")
	}
	if ttl := time.Until(expiresAt); ttl < 29*24*time.Hour || ttl > 31*24*time.Hour {
		t.Errorf("refresh ttl = %s, want ~30d", ttl)
	}

	// Login accepts email and username, like auth_service.py.
	if _, err := svc.Login(ctx, username, "passw0rd"); err != nil {
		t.Errorf("login by username: %v", err)
	}
	if _, err := svc.Login(ctx, email, "passw0rd123"); err == nil {
		t.Error("login with wrong password succeeded")
	} else {
		var appErr *apierr.Error
		if !errors.As(err, &appErr) || appErr.Code != apierr.CodeInvalidCredentials {
			t.Errorf("login wrong password code = %v, want %d", err, apierr.CodeInvalidCredentials)
		}
	}

	if _, err := svc.Me(ctx, userID); err != nil {
		t.Errorf("me: %v", err)
	}
}

func TestUniqueIndexesMapToBusinessCodes(t *testing.T) {
	pool := poolForTest(t)
	pg := NewPG(pool)
	ctx := context.Background()
	stamp := time.Now().UnixNano()
	email := fmt.Sprintf("gon1u-%d@example.test", stamp)
	username := fmt.Sprintf("gon1u_%d", stamp%1_000_000_000)
	otherEmail := "gon1dup-" + fmt.Sprint(stamp) + "@example.test"
	otherUsername := fmt.Sprintf("gon1d_%d", stamp%1_000_000_000)

	var created []string
	t.Cleanup(func() {
		for _, id := range created {
			cleanupUser(t, pool, id)
		}
	})
	register := func(mail, name string) string {
		t.Helper()
		row, err := pg.CreateUser(ctx, auth.NewUser{
			ID: testUserID(), Email: &mail, Username: &name,
			PasswordHash: "x", Language: "zh-CN", Timezone: "UTC",
		})
		if err != nil {
			t.Fatalf("create user %s: %v", mail, err)
		}
		created = append(created, row.ID)
		return row.ID
	}
	firstID := register(email, username)

	// Bypassing the service pre-check on purpose: this is the DB constraint,
	// which is what a concurrent double-submit actually hits.
	if _, err := pg.CreateUser(ctx, auth.NewUser{
		ID: testUserID(), Email: &otherEmail, Username: &username,
		PasswordHash: "x", Language: "zh-CN", Timezone: "UTC",
	}); !errors.Is(err, auth.ErrDuplicateUsername) {
		t.Errorf("duplicate username err = %v, want ErrDuplicateUsername", err)
	}
	if _, err := pg.CreateUser(ctx, auth.NewUser{
		ID: testUserID(), Email: &email, Username: &otherUsername,
		PasswordHash: "x", Language: "zh-CN", Timezone: "UTC",
	}); !errors.Is(err, auth.ErrDuplicateEmail) {
		t.Errorf("duplicate email err = %v, want ErrDuplicateEmail", err)
	}

	// 注销口径 = user_service.purge 的 status='deleted' + deleted_at；索引是
	// WHERE deleted_at IS NULL 的偏索引，因此注销账号不得阻塞重新注册。
	if _, err := pool.Exec(ctx,
		`UPDATE users SET status = 'deleted', deleted_at = now() WHERE id = $1`, firstID,
	); err != nil {
		t.Fatalf("soft delete: %v", err)
	}
	deletedRow, err := pg.GetUserByEmail(ctx, email)
	if err != nil {
		t.Fatalf("get deleted: %v", err)
	}
	if deletedRow != nil {
		t.Error("status='active' filter must hide the deleted row (Python parity)")
	}
	register(email, username)
}

func testUserID() string {
	return fmt.Sprintf("gon1-%d-%d", time.Now().UnixNano(), os.Getpid())
}

func deref(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}
