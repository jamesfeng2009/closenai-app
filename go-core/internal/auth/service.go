package auth

import (
	"context"
	"errors"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
)

var (
	ErrDuplicateEmail    = errors.New("duplicate email")
	ErrDuplicateUsername = errors.New("duplicate username")
)

// Baselines copied from backend/services/auth_service.py.
const (
	defaultTimezone = "UTC"
	defaultTier     = "free"
	defaultLevel    = "L1"
	refreshHashLen  = 64
)

var (
	// passwordStrong is backend/services/auth_service.py::PASSWORD_RE minus the
	// lookarounds (RE2 has none); the letter/digit/length checks are done in code.
	passwordLetter = regexp.MustCompile(`[A-Za-z]`)
	passwordDigit  = regexp.MustCompile(`\d`)
	// referralAlphabet excludes the glyphs that blur on a printed card: 0O1lI89.
	referralCodeRE = regexp.MustCompile(`^[ABCDEFGHJKMNPQRSTUVWXYZ234567]{8}$`)
)

type User struct {
	ID              string
	Email           *string
	Username        *string
	Name            *string
	Tier            string
	EmailVerifiedAt *time.Time
	PasswordHash    *string
}

// NewUser is the row Register inserts; the defaults mirror
// backend/repositories/user_repository.py::create_user.
type NewUser struct {
	ID             string
	Email          *string
	Username       *string
	PasswordHash   string
	Language       string
	Timezone       string
	ReferredByCode *string
}

type Store interface {
	GetUserByEmail(ctx context.Context, email string) (*User, error)
	GetUserByUsername(ctx context.Context, username string) (*User, error)
	GetUserByID(ctx context.Context, id string) (*User, error)
	CreateUser(ctx context.Context, in NewUser) (*User, error)
	CreateRefreshToken(ctx context.Context, id, userID, tokenHash string, expiresAt time.Time) error
}

type UserView struct {
	ID            string  `json:"id"`
	Email         *string `json:"email"`
	Username      *string `json:"username"`
	Name          *string `json:"name"`
	Tier          string  `json:"tier"`
	EmailVerified bool    `json:"email_verified"`
}

type Result struct {
	AccessToken  string   `json:"access_token"`
	RefreshToken string   `json:"refresh_token"`
	User         UserView `json:"user"`
}

type Service struct {
	store      Store
	signer     *Signer
	refreshTTL time.Duration
	now        func() time.Time
}

func NewService(store Store, signer *Signer, refreshTTL time.Duration) *Service {
	return &Service{store: store, signer: signer, refreshTTL: refreshTTL, now: time.Now}
}

type RegisterInput struct {
	Email    string
	Username string
	Password string
	Language string
	Ref      string
}

func (s *Service) Register(ctx context.Context, in RegisterInput) (*Result, error) {
	if passwordTooWeak(in.Password) {
		return nil, apierr.New(apierr.CodePasswordTooWeak, "密码至少 8 位，且需包含字母和数字 / Password too weak")
	}
	email := strings.ToLower(strings.TrimSpace(in.Email))
	username := strings.TrimSpace(in.Username)

	if existing, err := s.store.GetUserByEmail(ctx, email); err != nil {
		return nil, databaseError(err)
	} else if existing != nil {
		return nil, apierr.New(apierr.CodeEmailAlreadyUsed, "该邮箱已注册 / Email already used")
	}
	if existing, err := s.store.GetUserByUsername(ctx, username); err != nil {
		return nil, databaseError(err)
	} else if existing != nil {
		return nil, apierr.New(apierr.CodeUsernameAlreadyUsed, "用户名已被占用 / Username already used")
	}

	language := in.Language
	if language == "" {
		language = "en"
	}
	ref := NormalizeReferralCode(in.Ref)
	user, err := s.store.CreateUser(ctx, NewUser{
		ID:             newUUID(),
		Email:          &email,
		Username:       &username,
		PasswordHash:   mustHash(in.Password),
		Language:       language,
		Timezone:       defaultTimezone,
		ReferredByCode: ref,
	})
	if err != nil {
		switch {
		case errors.Is(err, ErrDuplicateEmail):
			return nil, apierr.New(apierr.CodeEmailAlreadyUsed, "该邮箱已注册 / Email already used")
		case errors.Is(err, ErrDuplicateUsername):
			return nil, apierr.New(apierr.CodeUsernameAlreadyUsed, "用户名已被占用 / Username already used")
		default:
			return nil, databaseError(err)
		}
	}
	return s.IssueTokens(ctx, user)
}

func (s *Service) Login(ctx context.Context, identifier, password string) (*Result, error) {
	ident := strings.TrimSpace(identifier)
	user, err := s.store.GetUserByEmail(ctx, strings.ToLower(ident))
	if err != nil {
		return nil, databaseError(err)
	}
	if user == nil {
		user, err = s.store.GetUserByUsername(ctx, ident)
		if err != nil {
			return nil, databaseError(err)
		}
	}
	invalid := apierr.New(apierr.CodeInvalidCredentials, "账号或密码错误 / Invalid credentials")
	if user == nil || user.PasswordHash == nil || !VerifyPassword(password, *user.PasswordHash) {
		return nil, invalid
	}
	return s.IssueTokens(ctx, user)
}

func (s *Service) Me(ctx context.Context, userID string) (*UserView, error) {
	user, err := s.store.GetUserByID(ctx, userID)
	if err != nil {
		return nil, databaseError(err)
	}
	if user == nil {
		return nil, apierr.New(apierr.CodeUserNotFound, "用户不存在 / User not found")
	}
	view := ToUserView(user)
	return &view, nil
}

// IssueTokens writes the refresh row before returning the pair: an access token
// whose refresh never landed would be unusable for silent renewal.
func (s *Service) IssueTokens(ctx context.Context, user *User) (*Result, error) {
	access, err := s.signer.SignAccess(user.ID)
	if err != nil {
		return nil, err
	}
	refresh, jti, err := s.signer.SignRefresh(user.ID)
	if err != nil {
		return nil, err
	}
	expiresAt := s.now().Add(s.refreshTTL)
	if err := s.store.CreateRefreshToken(ctx, jti, user.ID, HashRefreshToken(refresh), expiresAt); err != nil {
		return nil, databaseError(err)
	}
	view := ToUserView(user)
	return &Result{AccessToken: access, RefreshToken: refresh, User: view}, nil
}

func ToUserView(user *User) UserView {
	return UserView{
		ID:            user.ID,
		Email:         user.Email,
		Username:      user.Username,
		Name:          user.Name,
		Tier:          user.Tier,
		EmailVerified: user.EmailVerifiedAt != nil,
	}
}

// NormalizeReferralCode mirrors backend/core/referral.py::normalize_referral_code:
// an unparseable ?ref= is dropped silently, never a validation error.
func NormalizeReferralCode(raw string) *string {
	code := strings.ToUpper(strings.TrimSpace(raw))
	if !referralCodeRE.MatchString(code) {
		return nil
	}
	return &code
}

func passwordTooWeak(password string) bool {
	if strings.ContainsRune(password, '\n') {
		return true
	}
	return utf8.RuneCountInString(password) < 8 ||
		!passwordLetter.MatchString(password) ||
		!passwordDigit.MatchString(password)
}

func mustHash(password string) string {
	hash, err := HashPassword(password)
	if err != nil {
		panic("auth: password hashing failed: " + err.Error())
	}
	return hash
}

func databaseError(err error) error {
	if err == nil {
		return nil
	}
	return apierr.New(apierr.CodeDatabaseError, "数据库错误 / Database error")
}
