package store

import (
	"context"
	"sync"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/auth"
)

// FakeStore is the in-memory auth.Store used by the unit tests, so the HTTP
// vertical slice can be exercised without PostgreSQL. The production path is PG.
type FakeStore struct {
	mu            sync.Mutex
	users         map[string]*auth.User
	byEmail       map[string]string
	byUsername    map[string]string
	refreshTokens []FakeRefreshToken
}

type FakeRefreshToken struct {
	ID, UserID, TokenHash string
	ExpiresAt             time.Time
}

func NewFakeStore() *FakeStore {
	return &FakeStore{
		users:      map[string]*auth.User{},
		byEmail:    map[string]string{},
		byUsername: map[string]string{},
	}
}

func (f *FakeStore) GetUserByEmail(_ context.Context, email string) (*auth.User, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if id, ok := f.byEmail[email]; ok {
		return f.users[id], nil
	}
	return nil, nil
}

func (f *FakeStore) GetUserByUsername(_ context.Context, username string) (*auth.User, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if id, ok := f.byUsername[username]; ok {
		return f.users[id], nil
	}
	return nil, nil
}

func (f *FakeStore) GetUserByID(_ context.Context, id string) (*auth.User, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if u, ok := f.users[id]; ok {
		return u, nil
	}
	return nil, nil
}

func (f *FakeStore) CreateUser(_ context.Context, in auth.NewUser) (*auth.User, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if in.Email != nil {
		if _, dup := f.byEmail[*in.Email]; dup {
			return nil, auth.ErrDuplicateEmail
		}
	}
	if in.Username != nil {
		if _, dup := f.byUsername[*in.Username]; dup {
			return nil, auth.ErrDuplicateUsername
		}
	}
	hash := in.PasswordHash
	user := &auth.User{
		ID:           in.ID,
		Email:        in.Email,
		Username:     in.Username,
		Tier:         "free",
		PasswordHash: &hash,
	}
	f.users[in.ID] = user
	if in.Email != nil {
		f.byEmail[*in.Email] = in.ID
	}
	if in.Username != nil {
		f.byUsername[*in.Username] = in.ID
	}
	return user, nil
}

func (f *FakeStore) CreateRefreshToken(_ context.Context, id, userID, tokenHash string, expiresAt time.Time) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.refreshTokens = append(f.refreshTokens, FakeRefreshToken{id, userID, tokenHash, expiresAt})
	return nil
}

func (f *FakeStore) RefreshTokens() []FakeRefreshToken {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]FakeRefreshToken, len(f.refreshTokens))
	copy(out, f.refreshTokens)
	return out
}

func (f *FakeStore) AddUser(user auth.User) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.users[user.ID] = &user
	if user.Email != nil {
		f.byEmail[*user.Email] = user.ID
	}
	if user.Username != nil {
		f.byUsername[*user.Username] = user.ID
	}
}
