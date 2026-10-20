// T6-04 的隔离闸（工单 §怎么做.4 点名的这个文件）。
//
// 判据本体在 env_isolation.go，这里做四件事：
//  1. 逐格跑**跨语言用例表** `testdata/env_isolation_cases.json`（规格，不是本侧的输出）；
//  2. 钉 Load() 真的把 PROD_DB_FINGERPRINTS／CLOSEN_CUTOVER_APPROVED 读了进来，
//     并钉词表外的 ENVIRONMENT 起不来——用例表绕过了 Load，这两条只能在这里钉；
//  3. 钉报错**不回显凭据**（串里有口令，日志里不能有）；
//  4. 钉 IsProtected() 那张表：staging 必须是受保护档（D-T604-3）；
//  5. 钉「这条串读不出来」那一族形状（libpq 的 keyword=value、大写 scheme、`host=a,b`、
//     坏端口、口令里的 `#`）与 `ENVIRONMENT` 的读法（没设≠设成空）。
//     这一组是 2026-10-20 code review 抓到的 fail-open：只认小写 URL、读不出就放行，
//     等于「闸没看懂那条串」换成了「进程连上它黑名单里那个库」。
//
// §验收 第一句「把 staging 指到生产串 ⇒ 启动失败并有明确报错」的靶是这里的
// TestAcceptanceStagingPointedAtProductionRefusesToBoot——它跑的是 Load()＋
// RequireDBIsolation()，即进程真实的启动路径，不是判据函数的单测。
// 同一条验收句在 keyword=value 形状上的第二条腿是 TestKeywordValueDSNIsChecked。
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

// pyConfigFile 是 Python 侧那份配置源。这里**跨语言读源文本**是有意的：
// pydantic 把字段名大写当作 env 键名，所以「Python 读不读得到 PROD_DB_FINGERPRINTS」
// 这件事的真源是 backend/config.py 里那一行字段名（D-T604-4）。
const pyConfigFile = "../../../backend/config.py"

// TestPythonSideReadsTheSameFingerprintKey 钉 D-T604-4 的 Python 那一半：
// 这个键两侧都要有读数点，且都不许带 CLOSEN_ 前缀（前缀在本仓＝只有 Go 认得）。
//
// 为什么这一格要在 Go 侧再钉一次，而不是只由 pytest 的那条同名证人钉：
// 2026-10-20 的批 2 把 `require_db_isolation` 接到了 backend/database.py 的建引擎之前，
// 于是「字段改名」这一坏法在 **import 阶段**就抛 AttributeError，pytest 侧整份文件收集失败
// （ERROR 不是打红——红的可能是环境）。Go 不 import Python，这条证人只判文本，
// 变异能确实到达它自己那句断言。
func TestPythonSideReadsTheSameFingerprintKey(t *testing.T) {
	raw, err := os.ReadFile(pyConfigFile)
	if err != nil {
		t.Fatalf("读 %s 失败：%v（读不到就不许算过）", pyConfigFile, err)
	}
	src := string(raw)
	// 字段声明逐字钉在行首缩进上：只判「出现过这个名字」会被 `closen_prod_db_fingerprints`
	// 那种加了前缀的改名糊过去（子串还在，pydantic 读的 env 键已经换了）。
	if !strings.Contains(src, "\n    prod_db_fingerprints: str") {
		t.Fatalf("%s 里没有 prod_db_fingerprints 这个**字段**：pydantic 按字段名大写读 env，"+
			"没有这个字段＝py-api 读不到黑名单，那道闸在 Python 侧一条都不判（D-T604-4）", pyConfigFile)
	}
	for _, prefixed := range []string{"CLOSEN_PROD_DB_FINGERPRINTS", "closen_prod_db_fingerprints"} {
		if strings.Contains(src, prefixed) {
			t.Fatalf("Python 侧的指纹键出现了 %q：CLOSEN_ 前缀在本仓的意思是「只有 Go 认得」，"+
				"而 py-api 同样是 canonical 的写方，只挡 Go 等于把事故的门开了一半（D-T604-4）", prefixed)
		}
	}
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
	// 这几格点名的是「判据读不出这条串」那一族形状（keyword=value、大写 scheme、多 host、
	// 坏端口、口令里的 #）。删掉任何一格，判据就可以退回「只认小写 URL 且读不出就放行」
	// 而这张表的其余格子照绿——所以规格的一侧也要有自己的点名证人。
	for _, want := range []string{
		"staging_keyword形状命中生产host", "staging_keyword串指自己的库必须放行",
		"staging_大写scheme也要命中", "staging_多host逗号任一格命中",
		"staging_坏端口读不出拒启", "staging_口令带hash读不出拒启",
		"staging_keyword串无非空键拒启", "production_连自己的库必须放行",
		"development_黑名单为空放行", "development_sqlite串放行",
	} {
		if !names[want] {
			t.Fatalf("用例表缺 %s：那一格是 Go 侧判据的方向性证人", want)
		}
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
//
// 2026-10-20 起这一格用 `Unsetenv` 而不是 `Setenv(…, "")`：这两件事不是同一种形状。
// 没设 ⇒ 回落 development；**设了空值** ⇒ 那是「半填的 .env／值为空的 ConfigMap 键」，
// 必须按词表外拒启（见下面那条测与 D-T604-2）。
func TestDevelopmentStillBootsWithoutFingerprints(t *testing.T) {
	t.Setenv("JWT_SECRET", "unit-test-secret-unit-test-unit-test")
	t.Setenv("DATABASE_URL", "postgresql://closenai:closenai-dev-only@postgres:5432/closenai")
	t.Setenv("PROD_DB_FINGERPRINTS", "")
	os.Unsetenv("ENVIRONMENT")

	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load（未设 ENVIRONMENT 应回落 development）：%v", err)
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

// TestExplicitEmptyEnvironmentRefusesToBoot 钉 ENVIRONMENT 的读法是「没设才回落」，
// 不是「空串也算没设」（2026-10-20 code review 抓到）。
//
// 旧读法下 `ENVIRONMENT=` 回落成 development，于是那台进程既不是受保护档
// （httpapi/server.go 按 IsProtected() 放行 X-User-Id 设备身份透传），又在黑名单为空时
// 连库面隔离都不判；而 Python 侧同一份 env 读到 "" 走词表外拒启。
// 两侧对同一条 env 给两个结论时，**fail-open 那一侧就是实际生效的那一侧**。
func TestExplicitEmptyEnvironmentRefusesToBoot(t *testing.T) {
	t.Setenv("ENVIRONMENT", "")
	t.Setenv("JWT_SECRET", "unit-test-secret-unit-test-unit-test")
	t.Setenv("DATABASE_URL", "postgresql://closenai:closenai-dev-only@postgres:5432/closenai")
	t.Setenv("PROD_DB_FINGERPRINTS", "")

	cfg, err := Load()
	if err == nil {
		t.Fatal("ENVIRONMENT 设成空串却回落成 development 起来了：那台机器会按开发档跑")
	}
	if !strings.Contains(err.Error(), "ENVIRONMENT") {
		t.Errorf("报错要指名是哪个键：%v", err)
	}
	if cfg.Environment != "" {
		t.Errorf("出错时仍要把读到的原值带回来（运维要看自己设了什么），得到 %q", cfg.Environment)
	}
	// 同一条 env 递给判据本体也必须拒（装配绕过 Load 时还有这一层）。
	if err := CheckDBIsolation("", "sqlite+aiosqlite:///./closenai.db", nil); err == nil {
		t.Error("CheckDBIsolation 放行了空环境名")
	}
}

// TestKeywordValueDSNIsChecked 是 §验收 第一句在 keyword=value 形状上的另一条腿：
// pgx 的 ParseConfig 文档逐字写着「connString may either be in URL format or
// keyword = value format」，所以这种串**照样建得了连**。只认 URL 的判据对它一个字都
// 读不出来然后放行＝staging 档过闸连上生产库，而报错看起来一切正常。
func TestKeywordValueDSNIsChecked(t *testing.T) {
	t.Setenv("ENVIRONMENT", "staging")
	t.Setenv("JWT_SECRET", "unit-test-secret-unit-test-unit-test")
	t.Setenv("DATABASE_URL",
		"host=db.closenai-prod.supabase.co dbname=postgres user=closenai password="+leakMarker)
	t.Setenv("PROD_DB_FINGERPRINTS", "db.closenai-prod.supabase.co")

	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	err = cfg.RequireDBIsolation()
	if err == nil {
		t.Fatal("staging 用 keyword=value 形状连到生产库却启动了（判据只认 URL 就是这格）")
	}
	var iso *IsolationError
	if !asIsolationError(err, &iso) {
		t.Fatalf("错误类型不是 *IsolationError：%T（%v）", err, err)
	}
	if iso.Target.Host != "db.closenai-prod.supabase.co" || iso.Target.DBName != "postgres" {
		t.Errorf("keyword 形状拆错：%+v", iso.Target)
	}
	if strings.Contains(err.Error(), leakMarker) {
		t.Errorf("报错回显了口令：%v", err)
	}
	// 同一形状的**正向证人**：指自己库的 keyword 串必须放行，
	// 否则「凡 keyword 串都拒」那种实现能把这张用例表全骗过去。
	fingerprints := []string{"db.closenai-prod.supabase.co", "closenai_prod"}
	own := CheckDBIsolation("staging",
		"host=postgres.staging.internal dbname=closenai_staging user=closenai password=x",
		fingerprints)
	if own != nil {
		t.Errorf("staging 连自己的库（keyword 形状）被拒了：%v", own)
	}
}

// TestUnreadablePGTargetRefusesToBoot 钉「拆不出」与「不是库串」这两种空**必须分开**：
// sqlite 那种本机文件库仍然放行（否则本机 dev 第一天起不来），
// 但一条认得出是 PG、却读不出可比字段的串必须拒启——读不出来的方向是双写。
func TestUnreadablePGTargetRefusesToBoot(t *testing.T) {
	t.Setenv("ENVIRONMENT", "staging")
	t.Setenv("JWT_SECRET", "unit-test-secret-unit-test-unit-test")
	t.Setenv("PROD_DB_FINGERPRINTS", "prod.example.com")

	for label, raw := range map[string]string{
		"坏端口":          "postgresql://closenai:" + leakMarker + "@db.prod.example.com:543x/postgres",
		"口令带井号":        "postgresql://closenai:FAKE#pw_not_real@db.prod.example.com:5432/postgres",
		"keyword无host": "password=" + leakMarker + " port=5432",
	} {
		t.Setenv("DATABASE_URL", raw)
		cfg, err := Load()
		if err != nil {
			t.Fatalf("%s：Load 本身不该被词表拦：%v", label, err)
		}
		err = cfg.RequireDBIsolation()
		if err == nil {
			t.Fatalf("%s：闸没看懂这条串却放行了（它连的就是黑名单那个库）", label)
		}
		var unreadable *UnreadableTargetError
		if !asUnreadable(err, &unreadable) {
			t.Fatalf("%s：错误类型不是 *UnreadableTargetError：%T（%v）", label, err, err)
		}
		if strings.Contains(err.Error(), leakMarker) {
			t.Fatalf("%s：报错回显了连接串（凭据不入日志）：%v", label, err)
		}
	}

	// 反方向：sqlite 那条空仍然放行——它没有 host 可比，不该由这道闸报错。
	t.Setenv("ENVIRONMENT", "development")
	t.Setenv("DATABASE_URL", "sqlite+aiosqlite:///./closenai.db")
	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if err := cfg.RequireDBIsolation(); err != nil {
		t.Errorf("sqlite 本机库被库面闸拦了（那会让本机 dev 起不来）：%v", err)
	}
}

// TestMultiHostTargetIsSplit 钉 `host=a,b` 那种 libpq/pgx 都支持的 HA 写法：
// 整串逐字比会让 `db.staging.internal,db.prod.example.com` 静默漏过。
func TestMultiHostTargetIsSplit(t *testing.T) {
	target := DBTarget{Host: "db.staging.internal,db.prod.example.com", DBName: "app", User: "u"}
	clean := DBTarget{Host: "db.staging.internal,db.other.example.net", DBName: "app", User: "u"}
	// 指纹刻意写**逐字 host**而不是域根：写 `prod.example.com` 时「整串按后缀」那条规则
	// 会把 `db.staging.internal,db.prod.example.com` 也判中，摘掉逗号拆分照样绿——
	// 2026-10-20 变异自证里 I19／P12 第一次就是这么等价掉的。
	const fp = "db.prod.example.com"
	if !MatchFingerprint(fp, target) {
		t.Fatal("多 host 形状里那一格生产库没被判命中：漏过的方向是双写")
	}
	if MatchFingerprint(fp, clean) {
		t.Error("多 host 全不命中却拒了：误伤的方向是运维绕闸")
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
	// 词表外的值：既有大小写不对的 Production，也有带空白的 staging 与没在表里的 test。
	// 空串不在这里——「设成空」与「没设」是两件事，见 TestExplicitEmptyEnvironmentRefusesToBoot
	// （本包用 envSetString 读 ENVIRONMENT，就是为了把这两者分开）。
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

func asUnreadable(err error, out **UnreadableTargetError) bool {
	e, ok := err.(*UnreadableTargetError)
	*out = e
	return ok
}
