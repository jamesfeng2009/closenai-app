package auth

import (
	"strings"
	"testing"
)

func TestGoHashMatchesArgon2cffiShape(t *testing.T) {
	hash, err := HashPassword("passw0rd")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(hash, "$argon2id$v=19$m=65536,t=3,p=4$") {
		t.Errorf("hash prefix = %q", hash)
	}
	if err := CheckArgon2cffiParameters(hash); err != nil {
		t.Errorf("CheckArgon2cffiParameters: %v", err)
	}
	if !VerifyPassword("passw0rd", hash) {
		t.Error("Go could not verify its own hash")
	}
	if VerifyPassword("passw0rdX", hash) {
		t.Error("Go verified a wrong password")
	}
}

func TestVerifyPasswordRejectsMalformedHash(t *testing.T) {
	for _, hash := range []string{"", "not-a-hash", "$argon2id$v=19$m=64,t=1,p=1$c2FsdA$a2V5"} {
		if VerifyPassword("passw0rd", hash) {
			t.Errorf("VerifyPassword accepted %q", hash)
		}
		if err := CheckArgon2cffiParameters(hash); err == nil {
			t.Errorf("CheckArgon2cffiParameters accepted %q", hash)
		}
	}
}

func TestPasswordStrengthMatchesPython(t *testing.T) {
	weak := []string{"", "short1", "abcdefgh", "12345678", "abc\n1234", "abcdef"}
	strong := []string{"passw0rd", "abcdefgh1", "long1password"}
	for _, p := range weak {
		if !passwordTooWeak(p) {
			t.Errorf("PASSWORD_RE would reject %q but Go accepted it", p)
		}
	}
	for _, p := range strong {
		if passwordTooWeak(p) {
			t.Errorf("PASSWORD_RE would accept %q but Go rejected it", p)
		}
	}
}

func TestNormalizeReferralCode(t *testing.T) {
	got := NormalizeReferralCode("  abcdef23 ")
	if got == nil || *got != "ABCDEF23" {
		t.Errorf("NormalizeReferralCode(\"  abcdef23 \") = %v, want ABCDEF23", deref(got))
	}
	// 0/1/8/9 and I/L/O are excluded from the alphabet (they blur when printed).
	for _, bad := range []string{"", "SHORT1", "has space", "O0I1L89X", "ABCDEFGH1", "ABCDEFGHJ"} {
		if got := NormalizeReferralCode(bad); got != nil {
			t.Errorf("NormalizeReferralCode(%q) = %v, want nil", bad, *got)
		}
	}
}

func deref(s *string) string {
	if s == nil {
		return "<nil>"
	}
	return *s
}
