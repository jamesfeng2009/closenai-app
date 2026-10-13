package auth

import (
	"crypto/sha256"
	"encoding/hex"
)

// HashRefreshToken matches backend/core/auth.py::hash_refresh_token: sha256 hex
// digest of the token string (64 chars, the width of refresh_tokens.token_hash).
func HashRefreshToken(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])
}
