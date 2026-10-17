// 批 3 D-G13 的闸：`JWT_ALGORITHM` 在 Go 侧是**校验位，不是旋钮**。
//
// 实测形状（2026-10-17 读源）：`config.go` 读这个 env 并只允许字面量 "HS256"，
// 而签发/校验在 `internal/auth/token.go` 里**硬编码** HS256，从不读这个字段。
// 于是把它改成 RS256 的后果是「Go 拒绝启动」，不是「换了算法」。
//
// 为什么这条不变量非要有人盯：Python 侧 `backend/core/auth.py:71/84/90/194`
// **确实**读 `settings.jwt_algorithm`（那边是真旋钮）。两侧对同一个 env 的解读不同，
// 就留出一条只有单边会动的路：运维改这一个键 ⇒ py-api 换算法签 token、go-api 要么
// 拒启要么验不掉 ⇒ 只有走了 Go 的那批端点 401。本文件的第三向判据（`token_alg_gate_test.go`）
// 证的就是「最坏情况是 fail-closed 而不是接受错算法」。
//
// 三向：① 只有 HS256 能起进程；② 这个字段除 config 之外没有任何读取点（＝真的不是旋钮）；
// ③ 反证——别给签发处插一根没人用的分支（工作单 D-G13 的默认改判）。
package config

import (
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestJWTAlgorithmAcceptsOnlyTheBaseline 钉「校验位」这一半：逐字相等才放行。
//
// 大小写与空格不宽容是刻意的：`envString` 不做 Trim（只有 Setenv 传进来的原值），
// 而 Python 侧 PyJWT 按注册名查算法，"hs256" 与 "HS256 " 在它那里同样是失败——
// 两侧都拒才是同形，一侧静默归一一侧报错就是「同一个键两套语义」。
func TestJWTAlgorithmAcceptsOnlyTheBaseline(t *testing.T) {
	cases := []struct {
		name    string
		raw     string
		setEnv  bool
		wantErr bool
	}{
		{"显式基线", "HS256", true, false},
		{"没设该键走默认", "", false, false},
		{"设成空串等同没设", "", true, false},
		{"非对称算法", "RS256", true, true},
		{"同族更大摘要", "HS384", true, true},
		{"同族最大摘要", "HS512", true, true},
		{"椭圆曲线", "ES256", true, true},
		{"none 攻击面", "none", true, true},
		{"大小写漂移", "hs256", true, true},
		{"尾随空格", "HS256 ", true, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("JWT_SECRET", "unit-test-secret-unit-test-unit-test")
			if tc.setEnv {
				t.Setenv("JWT_ALGORITHM", tc.raw)
			} else {
				// Load() 读的是进程环境：键必须真的不存在，空串走的是 envString 的回落。
				os.Unsetenv("JWT_ALGORITHM")
				t.Cleanup(func() { os.Unsetenv("JWT_ALGORITHM") })
			}
			cfg, err := Load()
			if tc.wantErr {
				if err == nil {
					t.Fatalf("JWT_ALGORITHM=%q 竟然启动了进程：cfg.JWTAlgorithm=%q"+
						"（这个键只能取 HS256；能启动就说明它变成了旋钮）", tc.raw, cfg.JWTAlgorithm)
				}
				if !strings.Contains(err.Error(), "JWT_ALGORITHM") {
					t.Fatalf("报错文案没有点名这个键，运维读不出来：%v", err)
				}
				return
			}
			if err != nil {
				t.Fatalf("JWT_ALGORITHM=%q（set=%v）应当能启动：%v", tc.raw, tc.setEnv, err)
			}
			if cfg.JWTAlgorithm != "HS256" {
				t.Fatalf("默认值不是基线：cfg.JWTAlgorithm=%q", cfg.JWTAlgorithm)
			}
		})
	}
}

// TestJWTAlgorithmHasNoReaderOutsideConfig 钉「不是旋钮」这一半：
// 除 `internal/config` 之外，整个模块里不许出现 `JWTAlgorithm` 这个标识符。
//
// 为什么用源码扫而不是调用图：本包没有一张依赖图可查，而这条判据要防的恰恰是
// 「下一个人给 token.go 加一行 if s.method == cfg.JWTAlgorithm」——那种改法看起来
// 让键生效了，实际是把跨语言基线拆成单边可改的旋钮（签发处改一次就要重录
// `backend/tests/test_go_auth_interop.py` 的基线夹具）。闸红时的正确修法写在
// D-G13：两侧同批改签发与校验＋重录基线，而不是给这根分支补一个调用方。
//
// 扫描面刻意**含** `*_test.go`：测试里读这个字段同样是「键被当成旋钮用」的信号。
func TestJWTAlgorithmHasNoReaderOutsideConfig(t *testing.T) {
	root := filepath.Join("..", "..")
	var readers []string
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			switch d.Name() {
			case "gen", "testdata", ".git", "node_modules":
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") {
			return nil
		}
		if strings.HasPrefix(filepath.ToSlash(path), filepath.ToSlash(root+"/internal/config")) {
			return nil
		}
		src, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		if strings.Contains(string(src), "JWTAlgorithm") {
			readers = append(readers, path)
		}
		return nil
	})
	if err != nil {
		t.Fatalf("扫 %s 失败：%v", root, err)
	}
	if len(readers) > 0 {
		t.Fatalf("这些文件读了 cfg.JWTAlgorithm：%v ⇒ 这个键已经从校验位变成旋钮。"+
			"D-G13 的口径：换算法要 Go/Python 两侧同批改签发与校验并重录跨语言基线，"+
			"不是在这里加一条分支", readers)
	}
}

// TestSigningSiteHardcodesTheBaseline 是上一条的反证：签发与校验必须真的写死。
// 「config 里没有读取点」＋「token.go 里也不是硬编码」＝算法从哪来的都说不清，
// 所以这两格要同时绿才算登记成立。
func TestSigningSiteHardcodesTheBaseline(t *testing.T) {
	src, err := os.ReadFile(filepath.Join("..", "auth", "token.go"))
	if err != nil {
		t.Fatalf("读 internal/auth/token.go 失败：%v（读不到就不该算过）", err)
	}
	text := string(src)
	for _, want := range []string{
		"jwt.NewWithClaims(jwt.SigningMethodHS256",
		"t.Method != jwt.SigningMethodHS256",
	} {
		if !strings.Contains(text, want) {
			t.Fatalf("token.go 里找不到 %q ⇒ 签发或校验已经不看这个字面量了，"+
				"D-G13 登记的「硬编码 HS256」不再成立", want)
		}
	}
}
