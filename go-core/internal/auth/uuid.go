package auth

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
)

// newUUID returns a random (v4) UUID in the 8-4-4-4-12 lowercase form that
// Python's uuid4().str produces, because these ids become refresh_tokens.id /
// users.id values both stacks read back.
func newUUID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(fmt.Sprintf("auth: no entropy for uuid4: %v", err))
	}
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	h := hex.EncodeToString(b[:])
	return fmt.Sprintf("%s-%s-%s-%s-%s", h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])
}
