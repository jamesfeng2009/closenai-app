// Package idgen produces the 8-4-4-4-12 lowercase uuid4 form Python's
// uuid4().str writes into the same columns, because both stacks read each
// other's rows during the strangler migration (N1 互认基线).
package idgen

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
)

func New() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(fmt.Sprintf("idgen: no entropy for uuid4: %v", err))
	}
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	h := hex.EncodeToString(b[:])
	return fmt.Sprintf("%s-%s-%s-%s-%s", h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])
}
