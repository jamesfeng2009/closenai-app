package auth

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// pythonFixtures is the baseline emitted by scripts/gen_go_auth_fixtures.py from
// the real backend/core/auth.py functions. Everything here asserts that Go reads
// what Python writes.
func pythonFixtures(t *testing.T) map[string]any {
	t.Helper()
	path := filepath.Join("..", "..", "testdata", "python_auth_baseline.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s (run scripts/gen_go_auth_fixtures.py): %v", path, err)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("decode fixtures: %v", err)
	}
	return out
}

func fixtureSigner(t *testing.T) *Signer {
	t.Helper()
	f := pythonFixtures(t)
	secret := f["secret"].(string)
	return NewSigner(secret,
		time.Duration(f["access_expire_minutes"].(float64))*time.Minute,
		time.Duration(f["refresh_expire_days"].(float64))*24*time.Hour)
}

func TestPythonAccessTokenVerifiesInGo(t *testing.T) {
	f := pythonFixtures(t)
	signer := fixtureSigner(t)
	access := f["tokens"].(map[string]any)["access"].(map[string]any)

	claims, err := signer.Verify(access["jwt"].(string))
	if err != nil {
		t.Fatalf("Verify(python access token) = %v", err)
	}
	if claims.Sub != access["sub"].(string) {
		t.Errorf("sub = %q, want %q", claims.Sub, access["sub"])
	}
	if claims.Type != TypeAccess {
		t.Errorf("type = %q, want %q", claims.Type, TypeAccess)
	}
	if claims.IAT != int64(access["iat"].(float64)) || claims.Exp != int64(access["exp"].(float64)) {
		t.Errorf("iat/exp = %d/%d, want %v/%v", claims.IAT, claims.Exp, access["iat"], access["exp"])
	}
}

func TestPythonRefreshTokenVerifiesInGo(t *testing.T) {
	f := pythonFixtures(t)
	signer := fixtureSigner(t)
	refresh := f["tokens"].(map[string]any)["refresh"].(map[string]any)

	claims, err := signer.Verify(refresh["jwt"].(string))
	if err != nil {
		t.Fatalf("Verify(python refresh token) = %v", err)
	}
	if claims.Type != TypeRefresh || claims.JTI != refresh["jti"].(string) {
		t.Errorf("claims = %+v, want type=refresh jti=%v", claims, refresh["jti"])
	}
}

func TestPythonFailureCasesMapToSameCodes(t *testing.T) {
	f := pythonFixtures(t)
	signer := fixtureSigner(t)
	tokens := f["tokens"].(map[string]any)

	if _, err := signer.Verify(tokens["expired_access"].(map[string]any)["jwt"].(string)); err != ErrTokenExpired {
		t.Errorf("expired token error = %v, want ErrTokenExpired (40101)", err)
	}
	if _, err := signer.Verify(tokens["wrong_secret_access"].(string)); err != ErrTokenInvalid {
		t.Errorf("wrong secret error = %v, want ErrTokenInvalid (40102)", err)
	}
	if _, err := signer.Verify(tokens["tampered_access"].(string)); err != ErrTokenInvalid {
		t.Errorf("tampered token error = %v, want ErrTokenInvalid (40102)", err)
	}
}

func TestPythonArgon2HashesVerifyInGo(t *testing.T) {
	f := pythonFixtures(t)
	blocks := f["argon2id"].(map[string]any)["hashes"].([]any)
	if len(blocks) == 0 {
		t.Fatal("no argon2 fixtures")
	}
	for _, raw := range blocks {
		b := raw.(map[string]any)
		hash := b["hash"].(string)
		if !VerifyPassword(b["password"].(string), hash) {
			t.Errorf("Go rejected Python hash for %q", b["password"])
		}
		if VerifyPassword(b["password"].(string)+"x", hash) {
			t.Errorf("Go accepted a wrong password for %q", b["password"])
		}
		if err := CheckArgon2cffiParameters(hash); err != nil {
			t.Errorf("Python hash %q not in the agreed shape: %v", hash, err)
		}
	}
}

func TestGoSignedAccessClaimsMatchPython(t *testing.T) {
	signer := fixtureSigner(t)
	const userID = "11111111-2222-4333-8444-555555555555"

	access, err := signer.SignAccess(userID)
	if err != nil {
		t.Fatal(err)
	}
	claims := decodeClaims(t, access)
	assertKeySet(t, claims, []string{"exp", "iat", "sub", "type"})
	if claims["sub"] != userID || claims["type"] != TypeAccess {
		t.Errorf("claims = %v", claims)
	}
	iat := int64(claims["iat"].(float64))
	exp := int64(claims["exp"].(float64))
	if delta := exp - iat; delta != 1800 {
		t.Errorf("exp-iat = %d, want 1800 (30 minutes)", delta)
	}

	refresh, jti, err := signer.SignRefresh(userID)
	if err != nil {
		t.Fatal(err)
	}
	claims = decodeClaims(t, refresh)
	assertKeySet(t, claims, []string{"exp", "iat", "jti", "sub", "type"})
	if claims["jti"] != jti || claims["type"] != TypeRefresh {
		t.Errorf("claims = %v, jti = %q", claims, jti)
	}
	if got := len(jti); got != 36 {
		t.Errorf("jti length %d, want 36 (uuid4 string, refresh_tokens.id is varchar(36))", got)
	}
}

func TestRefreshTokenHashMatchesPythonShape(t *testing.T) {
	f := pythonFixtures(t)
	entry := f["refresh_token_hash"].(map[string]any)
	if got := HashRefreshToken(entry["token"].(string)); got != entry["sha256_hex"].(string) {
		t.Errorf("sha256 hex mismatch:\n go: %s\n py: %v", got, entry["sha256_hex"])
	}
}

func TestVerifyRejectsOtherAlgorithms(t *testing.T) {
	signer := fixtureSigner(t)
	header := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"none","typ":"JWT"}`))
	payload := base64.RawURLEncoding.EncodeToString([]byte(`{"sub":"x","type":"access"}`))
	if _, err := signer.Verify(header + "." + payload + "."); err != ErrTokenInvalid {
		t.Errorf("alg=none error = %v, want ErrTokenInvalid", err)
	}
}

func TestSignerWithoutSecretFailsClosed(t *testing.T) {
	signer := NewSigner("", time.Minute, time.Hour)
	if _, err := signer.SignAccess("u"); err != ErrNoSecret {
		t.Errorf("SignAccess with empty secret = %v, want ErrNoSecret", err)
	}
	if _, err := signer.Verify("a.b.c"); err != ErrNoSecret {
		t.Errorf("Verify with empty secret = %v, want ErrNoSecret", err)
	}
}

func decodeClaims(t *testing.T, token string) map[string]any {
	t.Helper()
	parts := splitToken(t, token)
	var claims map[string]any
	if err := json.Unmarshal(mustBase64URL(t, parts[1]), &claims); err != nil {
		t.Fatalf("decode claims: %v", err)
	}
	return claims
}

func splitToken(t *testing.T, token string) []string {
	t.Helper()
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		t.Fatalf("token %q is not three parts", token)
	}
	return parts
}

func mustBase64URL(t *testing.T, s string) []byte {
	t.Helper()
	b, err := base64.RawURLEncoding.DecodeString(s)
	if err != nil {
		t.Fatalf("base64url %q: %v", s, err)
	}
	return b
}

func assertKeySet(t *testing.T, got map[string]any, want []string) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("claim keys = %v, want exactly %v", keysOf(got), want)
	}
	for _, k := range want {
		if _, ok := got[k]; !ok {
			t.Errorf("missing claim %q (have %v)", k, keysOf(got))
		}
	}
}

func keysOf(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
