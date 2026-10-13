// Package auth implements the two primitives Python and Go must agree on
// byte-for-byte: HS256 JWTs and argon2id password hashes.
package auth

import (
	"errors"
	"fmt"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

const (
	ClaimType   = "type"
	TypeAccess  = "access"
	TypeRefresh = "refresh"
)

var (
	ErrTokenExpired = errors.New("token expired")
	ErrTokenInvalid = errors.New("invalid token")
	ErrNoSecret     = errors.New("JWT_SECRET not configured")
)

type Token struct {
	Access  string
	Refresh string
}

type Claims struct {
	Sub  string
	Type string
	IAT  int64
	Exp  int64
	JTI  string
}

type Signer struct {
	secret     []byte
	accessTTL  time.Duration
	refreshTTL time.Duration
	now        func() time.Time
	newID      func() string
}

func NewSigner(secret string, accessTTL, refreshTTL time.Duration) *Signer {
	return &Signer{
		secret:     []byte(secret),
		accessTTL:  accessTTL,
		refreshTTL: refreshTTL,
		now:        time.Now,
		newID:      newUUID,
	}
}

// SignAccess mirrors backend/core/auth.py::create_access_token:
// claims are exactly {sub, type, iat, exp}.
func (s *Signer) SignAccess(userID string) (string, error) {
	return s.sign(jwt.MapClaims{
		"sub":  userID,
		"type": TypeAccess,
		"iat":  s.now().Unix(),
		"exp":  s.now().Add(s.accessTTL).Unix(),
	})
}

// SignRefresh mirrors create_refresh_token: {sub, jti, type, iat, exp}.
// The jti is the refresh_tokens.id row that gets persisted alongside it.
func (s *Signer) SignRefresh(userID string) (string, string, error) {
	jti := s.newID()
	token, err := s.sign(jwt.MapClaims{
		"sub":  userID,
		"jti":  jti,
		"type": TypeRefresh,
		"iat":  s.now().Unix(),
		"exp":  s.now().Add(s.refreshTTL).Unix(),
	})
	if err != nil {
		return "", "", err
	}
	return token, jti, nil
}

func (s *Signer) sign(claims jwt.MapClaims) (string, error) {
	if len(s.secret) == 0 {
		return "", ErrNoSecret
	}
	return jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString(s.secret)
}

// Verify decodes and validates a token the same way jwt.decode(..., algorithms=["HS256"])
// does on the Python side: signature, then exp only.
func (s *Signer) Verify(token string) (Claims, error) {
	if len(s.secret) == 0 {
		return Claims{}, ErrNoSecret
	}
	claims := jwt.MapClaims{}
	_, err := jwt.NewParser(jwt.WithoutClaimsValidation()).ParseWithClaims(
		token, claims, func(t *jwt.Token) (any, error) {
			if t.Method != jwt.SigningMethodHS256 {
				return nil, fmt.Errorf("%w: unexpected alg %v", ErrTokenInvalid, t.Method)
			}
			return s.secret, nil
		},
	)
	if err != nil {
		return Claims{}, classify(err, claims, s.now())
	}
	if exp, ok := claims["exp"].(float64); ok && int64(exp) <= s.now().Unix() {
		return Claims{}, ErrTokenExpired
	}
	return toClaims(claims)
}

func classify(err error, claims jwt.MapClaims, now time.Time) error {
	if errors.Is(err, jwt.ErrSignatureInvalid) {
		return ErrTokenInvalid
	}
	if exp, ok := claims["exp"].(float64); ok && int64(exp) <= now.Unix() {
		return ErrTokenExpired
	}
	return ErrTokenInvalid
}

func toClaims(claims jwt.MapClaims) (Claims, error) {
	out := Claims{}
	for _, key := range []struct {
		name string
		dst  *string
	}{
		{"sub", &out.Sub},
		{"type", &out.Type},
		{"jti", &out.JTI},
	} {
		if v, ok := claims[key.name]; ok && v != nil {
			s, ok := v.(string)
			if !ok {
				return Claims{}, fmt.Errorf("%w: claim %s is not a string", ErrTokenInvalid, key.name)
			}
			*key.dst = s
		}
	}
	for _, key := range []struct {
		name string
		dst  *int64
	}{
		{"iat", &out.IAT},
		{"exp", &out.Exp},
	} {
		v, ok := claims[key.name]
		if !ok || v == nil {
			continue
		}
		n, ok := v.(float64)
		if !ok {
			return Claims{}, fmt.Errorf("%w: claim %s is not numeric", ErrTokenInvalid, key.name)
		}
		*key.dst = int64(n)
	}
	// PyJWT rejects a non-numeric exp with a generic PyJWTError before any
	// signature-time claim check, so treat it as invalid rather than unlimited.
	if exp, ok := claims["exp"]; ok && exp != nil {
		if _, ok := exp.(float64); !ok {
			return Claims{}, ErrTokenInvalid
		}
	}
	return out, nil
}
