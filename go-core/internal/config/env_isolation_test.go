// T6-04 的隔离闸（工单 §怎么做.4 点名的这个文件）。
//
// 判据本体在 env_isolation.go，这里做四件事：
//  1. 逐格跑**跨语言用例表** `testdata/env_isolation_cases.json`（规格，不是本侧的输出）；
//  2. 钉 Load() 真的把 PROD_DB_FINGERPRINTS／CLOSEN_CUTOVER_APPROVED 读了进来，
//     并钉词表外的 ENVIRONMENT 起不来——用例表绕过了 Load，这两条只能在这里钉；
//  3. 钉报错**不回显凭据**（串里有口令，日志里不能有）；
//  4. 钉 IsProtected() 那张表：staging 必须是受保护档（D-T604-3）。
//
// §验收 第一句「把 staging 指到生产串 ⇒ 启动失败并有明确报错」的靶是这里的
// TestAcceptanceStagingPointedAtProductionRefusesToBoot——它跑的是 Load()＋
// RequireDBIsolation()，即进程真实的启动路径，不是判据函数的单测。
package config

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

const casesFile = "../../testdata/env_isolation_cases.json"

// leakMarker 是用例表里那条假口令。报错文本里出现它＝把凭据写进了日志。
const leakMarker = "FAKE_pw_not_real"

type isolationCase struct {
	Name         string   `json:"name"`
	Environment  string   `json:"environment"`
	DatabaseURL  string   `json:"database_url"`
	Fingerprints []string `json:"fingerprints"`
	Expect       string   `json:"expect"`
	Reason       string   `json:"reason"`
}

func loadIsolationCases(t *testing.T) []isolationCase {
	t.Helper()
	raw, err := os.ReadFile(casesFile)
	if err != nil {
		t.Fatalf("读用例表 %s 失败：%v（读不到就不许算过）", casesFile, err)
	}
	var doc struct {
		Cases []isolationCase `json:"cases"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("用例表 %s 不是合法 JSON：%v", casesFile, err)
	}
	if len(doc.Cases) < 12 {
		t.Fatalf("用例表只剩 %d 格：这张表是判据的规格，缩水＝两侧同时朝同一个方向瞎", len(doc.Cases))
	}
	var sawAllow, sawRefuse int
	names := map[string]bool{}
	for _, c := range doc.Cases {
		if names[c.Name] {
			t.Fatalf("用例名重复：%s", c.Name)
		}
		names[c.Name] = true
		switch c.Expect {
		case "allow":
			sawAllow++
		case "refuse":
			sawRefuse++
		default:
			t.Fatalf("%s 的 expect=%q，只认 allow|refuse", c.Name, c.Expect)
		}
	}
	// 反向证人与正向证人 must both exist：全是 refuse 的用例表，
	// 可以用「一律拒启」的实现骗过去（那条会把生产自己判死，上线第一天就被注释掉）。
	if sawAllow == 0 || sawRefuse == 0 {
		t.Fatalf("用例表 allow=%d refuse=%d：两个方向都得有格子", sawAllow, sawRefuse)
	}
	return doc.Cases
}

func TestSharedCaseTableBothLanguagesShape(t *testing.T) {
	for _, c := range loadIsolationCases(t) {
		t.Run(c.Name, func(t *testing.T) {
			err := CheckDBIsolation(c.Environment, c.DatabaseURL, c.Fingerprints)
			switch c.Expect {
			case "refuse":
				if err == nil {
					t.Fatalf("该拒启却放行：%s（%s）", c.Name, c.Reason)
				}
				msg := err.Error()
				if strings.Contains(msg, leakMarker) {
					t.Fatalf("报错回显了连接串里的口令：%s", msg)
				}
				if strings.Contains(msg, "postgres.abcd1234") {
					t.Fatalf("报错回显了用户名（只许打 host/dbname）：%s", msg)
				}
			case "allow":
				if err != nil {
					t.Fatalf("该放行却拒启：%v（%s）", err, c.Reason)
				}
			}
		})
	}
}

// §验收 第一句逐字：把 staging 指到生产串 ⇒ 启动失败，且报错要说清是哪条指纹命中。
// 走的是 Load()＋RequireDBIsolation() 这条**进程启动路径**：键名拼错、
// Load 里忘了读数、main 忘了调用，这三种坏法都只会在这里红。
func TestAcceptanceStagingPointedAtProductionRefusesToBoot(t *testing.T) {
	t.Setenv("ENVIRONMENT", "staging")
	t.Setenv("JWT_SECRET", "unit-test-secret-unit-test-unit-test")
	t.Setenv("DATABASE_URL",
		"postgresql://closenai:"+leakMarker+"@db.closenai-prod.supabase.co:5432/postgres")
	t.Setenv("PROD_DB_FINGERPRINTS", "db.closenai-prod.supabase.co, closenai_prod")

	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load（词表内的 staging 不该被词表拦）：%v", err)
	}
	err = cfg.RequireDBIsolation()
	if err == nil {
		t.Fatal("staging 连到生产库却启动了：§验收 第一句没落地")
	}
	msg := err.Error()
	for _, want := range []string{"ENVIRONMENT=staging", "db.closenai-prod.supabase.co",
		"PROD_DB_FINGERPRINTS", "host=", "dbname="} {
		if !strings.Contains(msg, want) {
			t.Fatalf("报错不够明确，缺 %q：%s", want, msg)
		}
	}
	if strings.Contains(msg, leakMarker) {
		t.Fatalf("报错里出现了口令（凭据不入库也不入日志）：%s", msg)
	}
	var iso *IsolationError
	if !asIsolationError(err, &iso) {
		t.Fatalf("错误类型不是 *IsolationError（运维要按类型判是哪道闸）：%T", err)
	}
	if iso.Target.User != "" && !strings.Contains(iso.Target.User, "closenai") {
		t.Errorf("Target.User 取错了：%q", iso.Target.User)
	}
}

// TestStagingWithoutFingerprintsRefusesToBoot 钉 D-T604-6 那半句：
// 「staging 没配黑名单」不是「没配所以不判」，而是「没配＝没保护」。
func TestStagingWithoutFingerprintsRefusesToBoot(t *testing.T) {
	t.Setenv("ENVIRONMENT", "staging")
	t.Setenv("JWT_SECRET", "unit-test-secret-unit-test-unit-test")
	t.Setenv("DATABASE_URL", "postgresql://closenai:x@postgres.staging.internal:5432/closenai_staging")
	t.Setenv("PROD_DB_FINGERPRINTS", "")

	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if len(cfg.ProdDBFingerprints) != 0 {
		t.Fatalf("空串应当拆成空列表，得到 %q", cfg.ProdDBFingerprints)
	}
	err = cfg.RequireDBIsolation()
	if err == nil {
		t.Fatal("staging 没配黑名单却启动了：这道闸会变成一根假旋钮")
	}
	var missing *MissingFingerprintsError
	if !asMissingFingerprints(err, &missing) {
		t.Fatalf("错误类型不是 *MissingFingerprintsError：%T（%v）", err, err)
	}

	// 只有分隔符没有内容：同样算空，不许被当成一条能匹配任何 host 的指纹。
	t.Setenv("PROD_DB_FINGERPRINTS", " , , ")
	cfg, err = Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if len(cfg.ProdDBFingerprints) != 0 {
		t.Fatalf("空白项必须丢掉，得到 %q", cfg.ProdDBFingerprints)
	}
	if err := cfg.RequireDBIsolation(); err == nil {
		t.Fatal("「, ,」被当成了配置：空白不是指纹")
	}
}

// TestDevelopmentStillBootsWithoutFingerprints 是本闸的**可用性**证人：
// 本机 dev 编排（docker-compose.dev.yml）不配黑名单，它必须照起。
// 这条红了不等于安全——它等于第一天就有人把整道闸注释掉。
func TestDevelopmentStillBootsWithoutFingerprints(t *testing.T) {
	t.Setenv("ENVIRONMENT", "")
	t.Setenv("JWT_SECRET", "unit-test-secret-unit-test-unit-test")
	t.Setenv("DATABASE_URL", "postgresql://closenai:closenai-dev-only@postgres:5432/closenai")
	t.Setenv("PROD_DB_FINGERPRINTS", "")

	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load（ENVIRONMENT 空＝回落 development）：%v", err)
	}
	if cfg.Environment != "development" {
		t.Fatalf("回落值 = %q", cfg.Environment)
	}
	if err := cfg.RequireDBIsolation(); err != nil {
		t.Fatalf("本机 dev 起不来了：%v", err)
	}
	if cfg.IsProtected() {
		t.Error("development 不是受保护档")
	}
}

// TestProductionIsNotBlockedByItsOwnFingerprint 钉反方向：
// 黑名单不许把生产自己判死（用例表里那一格走的是判据函数，这里走 Load）。
func TestProductionIsNotBlockedByItsOwnFingerprint(t *testing.T) {
	t.Setenv("ENVIRONMENT", "production")
	t.Setenv("JWT_SECRET", "unit-test-secret-unit-test-unit-test")
	t.Setenv("DATABASE_URL", "postgresql://closenai:"+leakMarker+"@db.prod.example.com:5432/postgres")
	t.Setenv("PROD_DB_FINGERPRINTS", "prod.example.com")

	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if err := cfg.RequireDBIsolation(); err != nil {
		t.Fatalf("生产档连自己的库被拒了：%v", err)
	}
	if !cfg.IsProtected() {
		t.Error("production 必须是受保护档")
	}
}

// TestEnvironmentVocabularyGate 钉 D-T604-2 的取值面：四个合法值都要能起，
// 词表外的（含大小写、空白、别名漏写）一律拒启。
func TestEnvironmentVocabularyGate(t *testing.T) {
	t.Setenv("JWT_SECRET", "unit-test-secret-unit-test-unit-test")
	for _, good := range EnvironmentValues {
		t.Setenv("ENVIRONMENT", good)
		if _, err := Load(); err != nil {
			t.Errorf("合法值 %q 被词表拦了：%v", good, err)
		}
	}
	// 词表外的值：既有大小写不对的 Production，也有裸着的空串（envString 回落 development
	// 之前先由这里判一次——显式设成 "" 与不设是两件事，见用例表最后一格）。
	for _, bad := range []string{"Production", "PROD", "staging ", "stag", "test", "prod2", "staging,production"} {
		t.Setenv("ENVIRONMENT", bad)
		if _, err := Load(); err == nil {
			t.Errorf("取值 %q 被接受了（词表 = %v）", bad, EnvironmentValues)
		}
	}
	t.Setenv("ENVIRONMENT", "stging")
	cfg, err := Load()
	if err == nil {
		t.Fatal("拼错的 staging 起得来")
	}
	if !strings.Contains(err.Error(), "ENVIRONMENT") ||
		!strings.Contains(err.Error(), "staging") {
		t.Errorf("报错要说清是哪个键、期望哪些值：%v", err)
	}
	if cfg.Environment != "stging" {
		t.Errorf("Load 出错时仍要把读到的原值带回来（运维要看自己设了什么），得到 %q", cfg.Environment)
	}
}

// TestProtectedTierTruthTable 钉 D-T604-3：staging 与 production 同档。
// 这一格漏了，staging 拿到的是 development 的设备身份透传口径（X-User-Id 当身份），
// 比本票要防的双写更糟——所以它单独有一条测，不靠上面的用例表。
func TestProtectedTierTruthTable(t *testing.T) {
	cases := map[string]bool{
		"development": false,
		"staging":     true,
		"production":  true,
		"prod":        true,
	}
	for env, want := range cases {
		got := Config{Environment: env}.IsProtected()
		if got != want {
			t.Errorf("IsProtected(%q) = %v，期望 %v", env, got, want)
		}
	}
	// 缺密钥时 staging 必须拒启（与 production 同一句判据）。
	if err := (Config{Environment: "staging"}).RequireAuthSecret(); err == nil {
		t.Error("staging 缺 JWT_SECRET 却允许启动")
	}
	if err := (Config{Environment: "development"}).RequireAuthSecret(); err != nil {
		t.Errorf("development 不该拒启（本机没有密钥是常态）：%v", err)
	}
	if (Config{Environment: "staging"}).IsProduction() {
		t.Error("IsProduction 不许把 staging 算成 production（切流审批位按它判，见 D-T604-7）")
	}
}

// TestNewKeysAreActuallyRead 钉两个新键的**读数点**在 Load 里。
// 用例表与判据单测都绕过了 Load，只有这条能抓到「函数写对了但没人读 env」。
func TestNewKeysAreActuallyRead(t *testing.T) {
	t.Setenv("ENVIRONMENT", "development")
	t.Setenv("JWT_SECRET", "unit-test-secret-unit-test-unit-test")
	t.Setenv("PROD_DB_FINGERPRINTS", " db.prod.example.com ,closenai_prod, ,")
	t.Setenv("CLOSEN_CUTOVER_APPROVED", "1")

	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if got := cfg.ProdDBFingerprints; len(got) != 2 || got[0] != "db.prod.example.com" ||
		got[1] != "closenai_prod" {
		t.Errorf("PROD_DB_FINGERPRINTS 拆错：%q（要逐项 trim、丢空项）", got)
	}
	if cfg.CutoverApproved != "1" {
		t.Errorf("CLOSEN_CUTOVER_APPROVED 没读进来：%q", cfg.CutoverApproved)
	}
	for _, bad := range []string{"true", "yes", "1 ", "2"} {
		t.Setenv("CLOSEN_CUTOVER_APPROVED", bad)
		if _, err := Load(); err == nil {
			t.Errorf("审批位取值 %q 被接受了：只认 1（已签）与 0/空（未签）", bad)
		}
	}
}

// 小工具：不引 errors 的 As 是为了让这份文件读起来只有两种坏法。
func asIsolationError(err error, out **IsolationError) bool {
	e, ok := err.(*IsolationError)
	*out = e
	return ok
}

func asMissingFingerprints(err error, out **MissingFingerprintsError) bool {
	e, ok := err.(*MissingFingerprintsError)
	*out = e
	return ok
}
