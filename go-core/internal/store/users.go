// Package store is the PostgreSQL side of the auth vertical slice. The SQL here
// is the read/write path backend/repositories/{user,auth}_repository.py uses today,
// translated 1:1 so both stacks see the same rows; DDL stays with alembic (D-7).
package store

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/auth"
)

const uniqueViolation = "23505"

const selectUserColumns = `
	SELECT id, email, username, name, tier, email_verified_at, password_hash
	FROM users`

const insertUser = `
	INSERT INTO users (
		id, email, username, password_hash, language, timezone,
		streak_count, relationship_level, chat_count, daily_quota_used, tier,
		referred_by_code
	) VALUES ($1, $2, $3, $4, $5, $6, 0, 'L1', 0, 0, 'free', $7)`

const insertRefreshToken = `
	INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at)
	VALUES ($1, $2, $3, $4)`

type PG struct {
	pool *pgxpool.Pool
}

func NewPG(pool *pgxpool.Pool) *PG {
	return &PG{pool: pool}
}

func (p *PG) GetUserByEmail(ctx context.Context, email string) (*auth.User, error) {
	return p.queryUser(ctx, selectUserColumns+" WHERE email = $1 AND status = 'active'", email)
}

func (p *PG) GetUserByUsername(ctx context.Context, username string) (*auth.User, error) {
	return p.queryUser(ctx, selectUserColumns+" WHERE username = $1 AND status = 'active'", username)
}

func (p *PG) GetUserByID(ctx context.Context, id string) (*auth.User, error) {
	return p.queryUser(ctx, selectUserColumns+" WHERE id = $1 AND status = 'active'", id)
}

func (p *PG) queryUser(ctx context.Context, sql string, args ...any) (*auth.User, error) {
	row := p.pool.QueryRow(ctx, sql, args...)
	var u auth.User
	err := row.Scan(&u.ID, &u.Email, &u.Username, &u.Name, &u.Tier, &u.EmailVerifiedAt, &u.PasswordHash)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &u, nil
}

func (p *PG) CreateUser(ctx context.Context, in auth.NewUser) (*auth.User, error) {
	tag, err := p.pool.Exec(ctx, insertUser,
		in.ID, in.Email, in.Username, in.PasswordHash, in.Language, in.Timezone, in.ReferredByCode)
	if err != nil {
		return nil, classifyUnique(err)
	}
	if tag.RowsAffected() != 1 {
		return nil, fmt.Errorf("insert into users affected %d rows", tag.RowsAffected())
	}
	return p.GetUserByID(ctx, in.ID)
}

func (p *PG) CreateRefreshToken(ctx context.Context, id, userID, tokenHash string, expiresAt time.Time) error {
	_, err := p.pool.Exec(ctx, insertRefreshToken, id, userID, tokenHash, expiresAt)
	return err
}

// classifyUnique maps the partial unique indexes ix_users_email_unique /
// ix_users_username_unique onto the same 409 business codes the pre-check returns.
func classifyUnique(err error) error {
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) || pgErr.Code != uniqueViolation {
		return err
	}
	switch pgErr.ConstraintName {
	case "ix_users_email_unique":
		return auth.ErrDuplicateEmail
	case "ix_users_username_unique":
		return auth.ErrDuplicateUsername
	}
	return err
}
