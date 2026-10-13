package auth

import (
	"errors"
	"fmt"
	"strings"

	"github.com/alexedwards/argon2id"
)

// argon2idParams are argon2-cffi's PasswordHasher() defaults, measured from
// backend/core/auth.py::_pwd_hasher on 2026-10-13: $argon2id$v=19$m=65536,t=3,p=4
// with a 16-byte salt and a 32-byte key, standard base64 without padding.
var argon2idParams = &argon2id.Params{
	Memory:      64 * 1024,
	Iterations:  3,
	Parallelism: 4,
	SaltLength:  16,
	KeyLength:   32,
}

var ErrMalformedHash = errors.New("malformed argon2id hash")

func HashPassword(password string) (string, error) {
	return argon2id.CreateHash(password, argon2idParams)
}

func VerifyPassword(password, hash string) bool {
	ok, err := argon2id.ComparePasswordAndHash(password, hash)
	if err != nil {
		return false
	}
	return ok
}

// CheckArgon2cffiParameters is the guard the cross-language test runs: a hash
// written here must parse as an argon2id PHC string carrying exactly the
// parameters argon2-cffi is configured with, otherwise Python would rehash it
// (needs_rehash) on first login and the two stacks would drift apart.
func CheckArgon2cffiParameters(hash string) error {
	params, _, key, err := argon2id.DecodeHash(hash)
	if err != nil {
		return fmt.Errorf("%w: %v", ErrMalformedHash, err)
	}
	want := fmt.Sprintf("$argon2id$v=19$m=%d,t=%d,p=%d$", params.Memory, params.Iterations, params.Parallelism)
	if !strings.HasPrefix(hash, want) {
		return fmt.Errorf("%w: parameter segment %q is not what argon2-cffi emits", ErrMalformedHash, want)
	}
	if *params != *argon2idParams {
		return fmt.Errorf("%w: params %+v, want %+v", ErrMalformedHash, *params, *argon2idParams)
	}
	if len(key) != int(argon2idParams.KeyLength) {
		return fmt.Errorf("%w: key length %d, want %d", ErrMalformedHash, len(key), argon2idParams.KeyLength)
	}
	return nil
}
