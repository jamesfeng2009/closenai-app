// Command crosscheck prints the artifacts the Go core produces — HS256 tokens,
// an argon2id password hash, a sha256 refresh-token hash — as JSON, so the
// Python side can verify them. It covers the Go -> Python direction of the
// cross-language auth baseline; go-core/internal/auth/interop_test.go covers
// Python -> Go.
//
// With -mode=auth it additionally registers through the real pgx store and logs
// in against a row the caller names, which is how the Python side proves it can
// read a row Go wrote (and that Go can read one Python wrote) during the
// grey-rolling window.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"runtime"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/auth"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/dsn"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store"
)

type output struct {
	GoVersion         string `json:"go_version"`
	Mode              string `json:"mode"`
	UserID            string `json:"user_id"`
	AccessToken       string `json:"access_token"`
	RefreshToken      string `json:"refresh_token"`
	RefreshTokenID    string `json:"refresh_token_id"`
	RefreshTokenHash  string `json:"refresh_token_hash"`
	Password          string `json:"password"`
	PasswordHash      string `json:"password_hash"`
	AccessTTLSeconds  int64  `json:"access_ttl_seconds"`
	RefreshTTLSeconds int64  `json:"refresh_ttl_seconds"`

	// Filled in by -mode=auth only.
	GoRegisteredUserID   string `json:"go_registered_user_id"`
	GoRegisteredEmail    string `json:"go_registered_email"`
	GoRegisteredUsername string `json:"go_registered_username"`
	GoRegisteredToken    string `json:"go_registered_access_token"`
	LoginUserID          string `json:"login_user_id"`
	LoginAccessToken     string `json:"login_access_token"`
}

func main() {
	mode := flag.String("mode", "issue", "issue | auth")
	userID := flag.String("user-id", "crosscheck-user", "sub claim")
	password := flag.String("password", "passw0rd", "plaintext password to hash")
	identifier := flag.String("login-identifier", "", "identifier to log in with (mode=auth)")
	databaseURL := flag.String("database-url", os.Getenv("DATABASE_URL"),
		"postgresql DSN for -mode=auth; SQLAlchemy schemes (postgresql+psycopg://…) are accepted")
	accessTTL := flag.Duration("access-ttl", 30*time.Minute, "access token TTL")
	refreshTTL := flag.Duration("refresh-ttl", 30*24*time.Hour, "refresh token TTL")
	flag.Parse()

	secret := os.Getenv("JWT_SECRET")
	if secret == "" {
		fmt.Fprintln(os.Stderr, "crosscheck: JWT_SECRET is required")
		os.Exit(2)
	}

	out := output{
		GoVersion: runtime.Version(), Mode: *mode, UserID: *userID,
		AccessTTLSeconds: int64(accessTTL.Seconds()), RefreshTTLSeconds: int64(refreshTTL.Seconds()),
	}

	signer := auth.NewSigner(secret, *accessTTL, *refreshTTL)
	accessToken, err := signer.SignAccess(*userID)
	if err != nil {
		fail(err)
	}
	refreshToken, refreshID, err := signer.SignRefresh(*userID)
	if err != nil {
		fail(err)
	}
	passwordHash, err := auth.HashPassword(*password)
	if err != nil {
		fail(err)
	}
	// A hash Python would want to rewrite means the parameters drifted.
	if err := auth.CheckArgon2cffiParameters(passwordHash); err != nil {
		fail(err)
	}
	out.AccessToken = accessToken
	out.RefreshToken = refreshToken
	out.RefreshTokenID = refreshID
	out.RefreshTokenHash = auth.HashRefreshToken(refreshToken)
	out.Password = *password
	out.PasswordHash = passwordHash

	if *mode == "auth" {
		out.runAuth(*databaseURL, *identifier, *password, secret, *accessTTL, *refreshTTL)
	}

	if err := json.NewEncoder(os.Stdout).Encode(out); err != nil {
		fail(err)
	}
}

// runAuth writes a row through the Go store, then logs in through the read path
// Python shares.
func (out *output) runAuth(databaseURL, identifier, password, secret string,
	accessTTL, refreshTTL time.Duration,
) {
	if databaseURL == "" {
		fail(fmt.Errorf("-mode=auth needs -database-url or DATABASE_URL"))
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, dsn.Postgres(databaseURL))
	if err != nil {
		fail(err)
	}
	defer pool.Close()
	if err := pool.Ping(ctx); err != nil {
		fail(err)
	}

	svc := auth.NewService(store.NewPG(pool), auth.NewSigner(secret, accessTTL, refreshTTL), refreshTTL)
	stamp := time.Now().UnixNano()
	email := fmt.Sprintf("gon1x-%d@example.test", stamp)
	username := fmt.Sprintf("gon1x_%d", stamp%1_000_000_000)
	registered, err := svc.Register(ctx, auth.RegisterInput{
		Email: email, Username: username, Password: password, Language: "zh-CN",
	})
	if err != nil {
		fail(fmt.Errorf("go register: %w", err))
	}
	out.GoRegisteredUserID = registered.User.ID
	out.GoRegisteredEmail = email
	out.GoRegisteredUsername = username
	out.GoRegisteredToken = registered.AccessToken

	if identifier == "" {
		// Without a Python-created row, logging into Go's own row still
		// exercises the read path.
		identifier = username
	}
	loggedIn, err := svc.Login(ctx, identifier, password)
	if err != nil {
		fail(fmt.Errorf("go login %q: %w", identifier, err))
	}
	out.LoginUserID = loggedIn.User.ID
	out.LoginAccessToken = loggedIn.AccessToken
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "crosscheck:", err)
	os.Exit(1)
}
