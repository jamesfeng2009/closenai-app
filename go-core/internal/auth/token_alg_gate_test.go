// 批 3 D-G13 的第二座闸（行为面）：非基线算法签出来的 token **必须验不过**。
//
// 为什么要有这一条而不是只写注释：Python 侧的 `JWT_ALGORITHM` 是真旋钮
// （`backend/core/auth.py` 四处都读 `settings.jwt_algorithm`），所以「有人在部署里把它改掉」
// 不是假设，而是一条只有单边会动的真实路径。那条路的下限必须是 **fail-closed**：
// go-api 拒绝这种 token，而不是拿同一个 HMAC 密钥去接受另一种算法
// （后者就是经典的 alg-confusion / 密钥复用漏洞）。
//
// 与 `internal/config/jwt_algorithm_gate_test.go` 的分工：那两条判「Go 侧这个键起不了作用」，
// 本条判「即使对面真的换了算法，这一侧也不会误接受」。
package auth

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

const gateSecret = "d-g13-gate-secret-d-g13-gate-secret"

// signWith 刻意绕开 Signer.sign：这里要的就是「对面用别的算法签出来的字节」。
func signWith(t *testing.T, method jwt.SigningMethod) string {
	t.Helper()
	token, err := jwt.NewWithClaims(method, jwt.MapClaims{
		"sub":  "11111111-2222-3333-4444-555555555555",
		"type": TypeAccess,
		"iat":  time.Now().Unix(),
		"exp":  time.Now().Add(time.Hour).Unix(),
	}).SignedString([]byte(gateSecret))
	if err != nil {
		t.Fatalf("用 %s 签 token 失败：%v", method.Alg(), err)
	}
	return token
}

func TestVerifyRejectsEveryNonBaselineAlgorithm(t *testing.T) {
	signer := NewSigner(gateSecret, time.Hour, 24*time.Hour)
	// HMAC 同族是**最危险**的两格：密钥相同、只是摘要更长，
	// 少了 `t.Method != jwt.SigningMethodHS256` 那道门就会静默接受（密钥复用＋算法降级面）。
	for _, alg := range []string{"HS384", "HS512"} {
		method := jwt.GetSigningMethod(alg)
		if method == nil {
			t.Fatalf("库里没有算法 %q，这条用例空转", alg)
		}
		t.Run(alg, func(t *testing.T) {
			token := signWith(t, method)
			if _, err := signer.Verify(token); err == nil {
				t.Fatalf("Verify 接受了 alg=%s 的 token ⇒ 同一个密钥跨算法可用（密钥复用）", alg)
			} else if !errors.Is(err, ErrTokenInvalid) {
				t.Fatalf("错误分类不对：期望 ErrTokenInvalid，实际 %v", err)
			}
		})
	}
}

func TestSignerItselfEmitsOnlyTheBaseline(t *testing.T) {
	// 反方向：本侧签出去的 header 必须是 HS256。这条不是重复上一条——它防的是
	// 「有人把 sign 换成了别的算法」，那种改法会让 Python 侧验不过自己签的 token，
	// 而 test_go_auth_interop.py 的夹具是录好的字节，不会当场发现。
	signer := NewSigner(gateSecret, time.Hour, 24*time.Hour)
	token, err := signer.SignAccess("11111111-2222-3333-4444-555555555555")
	if err != nil {
		t.Fatalf("SignAccess: %v", err)
	}
	header, err := base64.RawURLEncoding.DecodeString(strings.SplitN(token, ".", 2)[0])
	if err != nil {
		t.Fatalf("header 不是 base64url：%v", err)
	}
	var h map[string]any
	if err := json.Unmarshal(header, &h); err != nil {
		t.Fatalf("header 不是 JSON：%v", err)
	}
	if h["alg"] != "HS256" {
		t.Fatalf("签发出去的是 alg=%v ⇒ 跨语言基线已经不在 Go 这一侧了", h["alg"])
	}
	if _, err := signer.Verify(token); err != nil {
		t.Fatalf("自己签的自己验不过：%v", err)
	}
}
