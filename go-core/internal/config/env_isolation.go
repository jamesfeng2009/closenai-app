// T6-04（2026-10-20）· 运行环境词表 + 「非生产档不许碰生产库」这道闸。
//
// 这一文件存在的理由是一句很朴素的话：**注释拦不住人，闸拦**。
// Baseline §22 风险 3 描述的事故形状是「两套编排的变量指向同一个库」：
// staging 的进程（Go 或 Python 任一）连上生产 DATABASE_URL，只要它的百分比不为 0，
// 就与另一侧同时写 canonical——写冲突不会出现，出现的是**两份都看着对的账**。
// 在这之前仓库里只有 README 的一句话和 `10-config.yaml` 那八个恰好全为 "0" 的键，
// 而「当前是 0」不是约束：键一拨、库一换，进程照起。
//
// 三条判据（决策表 D-T604-2 / 5 / 6，逐字在 CLOSENAI_V3_TASK_BOARD.md 的 T6-04 节）：
//  1. `ENVIRONMENT` 只认 development|staging|production|prod，别的值一律拒启；
//  2. `staging` 必须给出生产库指纹（空黑名单＝没配＝没保护），
//     `development` 没给也放行（本机 dev 编排第一天就得能起），给了同样参与比较；
//  3. 非生产档的 DATABASE_URL 命中任一指纹 ⇒ 拒启；生产档不参与比较。
//
// 报错只回显 host 与 dbname，**绝不回显整条连接串**：串里那段是口令。
// Python 侧的同一份判据在 backend/core/env_isolation.py（不同源实现），
// 两侧由 go-core/testdata/env_isolation_cases.json 这份用例表对账。
package config

import (
	"fmt"
	"net/url"
	"regexp"
	"strings"
)

// pgScheme 认「带驱动或不带驱动的 PG 串」。这与 internal/dsn 是同一条方言规则，
// 但这里要的是**拆出 host/dbname**，dsn 只做 scheme 改写，所以各留一份、
// 由 backend/tests/test_env_isolation_cross_language.py 钉住两侧都认这四种形状。
// 大小写不敏感：URL scheme 按 RFC 3986 本来就不区分大小写，而 pgx 两种都接——
// 只认小写时，漂一下大小写就能让 staging 带着生产 host 起来
// （2026-10-20 code review 抓到，与 dsnKeyword 那一格同一个方向）。
var pgScheme = regexp.MustCompile(`(?i)^postgres(?:ql)?(?:\+[^:/]*)?://`)

// dsnKeyword 认 libpq 的 keyword=value 形状。这不是假想形状：
// `go doc github.com/jackc/pgx/v5/pgconn.ParseConfig` 逐字写着
// "connString may either be in URL format or keyword = value format"，
// 所以这种串在 Go 侧**照样建得了连**，而只认 URL 的旧判据对它一个字都读不出来
// ⇒ staging 档过闸、连上生产库（本票 D-T604-5 要拦的正是这个方向）。
var dsnKeyword = regexp.MustCompile(`(?i)^[a-z_][a-z0-9_]*=`)

// dsnValue 取 keyword=value 里某个键的值；带单引号的值允许含空格。
// 两侧各写一份（Go 用 regexp、Python 用 re），由用例表对账，不在这里互相引用。
func dsnValue(raw, key string) string {
	re := regexp.MustCompile(`(?i)(?:^|\s)` + key + `\s*=\s*(?:'([^']*)'|([^\s]*))`)
	m := re.FindStringSubmatch(raw)
	if m == nil {
		return ""
	}
	if m[1] != "" {
		return m[1]
	}
	return m[2]
}

// looksLikePGTarget 判「这条串是不是一条该拿来比对的库串」。
// sqlite 那种本机文件库走 false（没有 host 可比，也不该由本闸报错——生产档缺 PG
// 会在真正的建连处失败，那儿的报错比这里更准）；两种 PG 形状走 true。
// **true 却拆不出可比字段时，本闸选择拒启**而不是放行：读不出来的方向是双写，
// 误伤的方向只是起不来（D-T604-5 那句「本闸一律选择后者」在同一格的应用）。
func looksLikePGTarget(raw string) bool {
	return pgScheme.MatchString(raw) || dsnKeyword.MatchString(strings.TrimSpace(raw))
}

// DBTarget 是连接串里可比对的那一半，**不含口令**（错误信息只许打印 Host/DBName）。
type DBTarget struct {
	Host   string // 小写、不含端口；取不到时为空串
	DBName string // 小写、路径第一段
	User   string // 小写的用户名，**只参与匹配、永不回显**（下面那条注释）
}

// ParseDBTarget 拆一条 PG 串，认两种形状：URL 形式与 libpq 的 keyword=value 形式。
// 第二个返回值为 false ⇒ 要么这不是库串可比对的形状（sqlite 那种本机文件库走这条路：
// 没有 host 可比，也不该由本闸报错——生产档缺 PG 会在真正的建连处失败，那儿的报错比这里更准），
// 要么它是 PG 串但一个可比字段都读不出来。**调用方对后者的结论是拒启**，
// 判据在 CheckDBIsolation 里用 looksLikePGTarget 把两种 false 分开。
func ParseDBTarget(raw string) (DBTarget, bool) {
	if !pgScheme.MatchString(raw) {
		// keyword=value 那条分支：host/dbname/user 三个键能读出来几个算几个。
		// 读不出任何一个 ⇒ 交给调用方按「库串但不可读」处理（见 looksLikePGTarget）。
		if !dsnKeyword.MatchString(strings.TrimSpace(raw)) {
			return DBTarget{}, false
		}
		t := DBTarget{
			Host:   strings.ToLower(dsnValue(raw, "host")),
			DBName: strings.ToLower(dsnValue(raw, "dbname")),
			User:   strings.ToLower(dsnValue(raw, "user")),
		}
		if t.Host == "" && t.DBName == "" && t.User == "" {
			return DBTarget{}, false
		}
		return t, true
	}
	u, err := url.Parse(raw)
	if err != nil {
		return DBTarget{}, false
	}
	host := strings.ToLower(u.Hostname())
	name := strings.TrimPrefix(u.Path, "/")
	if i := strings.Index(name, "/"); i >= 0 {
		name = name[:i]
	}
	// 为什么把用户名也拿去比：Supabase 的 session pooler 主机名里**没有** project ref
	// （`aws-0-<region>.pooler.supabase.com`），ref 只长在用户名（`postgres.<ref>`）里。
	// 只比 host/dbname 的话，按 ref 写的黑名单会对这条真实形状静默放行——
	// 静默放行是双写，不是误报。用户名只参与匹配，Error() 里一个字符都不打。
	var user string
	if u.User != nil {
		user = strings.ToLower(u.User.Username())
	}
	return DBTarget{Host: host, DBName: strings.ToLower(name), User: user}, true
}

// MatchFingerprint 判一条指纹是否命中这个库。
//
// 指纹带点（host 形状）⇒ host 逐字相等、host 以 ".指纹" 结尾、dbname 逐字相等；
// 指纹不带点（project ref／库名片段那种**裸令牌**）⇒ 出现在 host、dbname 或用户名里就算命中。
// 为什么不给带点的指纹也开子串：`supabase.co` 这种写法本来就 intended 是后缀，
// 开子串会让它命中 `supabase.co.evil.example`（反向的假阴性）而同时误伤一堆合法 host；
// 裸令牌开子串是因为 Supabase 的 project ref 就长在 host 中间
// （`db.<ref>.supabase.co`）或用户名里（`postgres.<ref>`），
// 要求逐字相等会让它**静默漏过**——漏过的方向是双写，误伤的方向只是拒启，本闸一律选择后者。
func MatchFingerprint(fingerprint string, t DBTarget) bool {
	fp := strings.ToLower(strings.TrimSpace(fingerprint))
	if fp == "" || (t.Host == "" && t.DBName == "" && t.User == "") {
		return false
	}
	if strings.Contains(fp, ".") {
		// host 按逗号拆开逐个比：libpq 与 pgx 都允许 `host=a,b` 那种多 host（HA）写法
		// （pgconn.ParseConfig 的文档里有这一条），整串逐字比会让 `host=staging.internal,db.prod.example.com`
		// 静默漏过——漏过的方向是双写，而拆开最多多拒一次，仍是本闸的选择。
		for _, host := range strings.Split(t.Host, ",") {
			host = strings.TrimSpace(host)
			if host == "" {
				continue
			}
			if host == fp || strings.HasSuffix(host, "."+fp) {
				return true
			}
		}
		return t.DBName == fp
	}
	return strings.Contains(t.Host, fp) || strings.Contains(t.DBName, fp) ||
		strings.Contains(t.User, fp)
}

// hit 报告命中的是哪一条指纹（错误信息要指名它，不能只说「不允许」）。
func hit(fingerprints []string, t DBTarget) (string, bool) {
	for _, fp := range fingerprints {
		if MatchFingerprint(fp, t) {
			return strings.TrimSpace(fp), true
		}
	}
	return "", false
}

// IsolationError 是「这一档不许连这个库」。
type IsolationError struct {
	Environment string
	Target      DBTarget
	Fingerprint string
}

func (e *IsolationError) Error() string {
	return fmt.Sprintf(
		"ENVIRONMENT=%s 不许连 host=%q dbname=%q：它命中了生产库指纹 %q（PROD_DB_FINGERPRINTS）。"+
			"两套进程写同一个 canonical 库＝两份都看着对的账（Baseline §22 风险 3）。"+
			"改法：把这一档的 DATABASE_URL 指到它自己的库，或按双签口径把 ENVIRONMENT 设成 production",
		e.Environment, e.Target.Host, e.Target.DBName, e.Fingerprint)
}

// MissingFingerprintsError 是「staging 没配黑名单」——它和命中生产库同一个方向：
// 一个没人验过的隔离不叫隔离（D-T604-6）。
type MissingFingerprintsError struct{ Environment string }

func (e *MissingFingerprintsError) Error() string {
	return fmt.Sprintf(
		"ENVIRONMENT=%s 必须配 PROD_DB_FINGERPRINTS（逗号分隔的生产库 host／dbname／project ref）："+
			"黑名单为空＝这道闸一条都不判，「以为隔离了其实没隔离」比没有这道闸更糟（D-T604-6）",
		e.Environment)
}

// UnreadableTargetError 是「这是一条 PG 库串，但本闸拆不出可比对的 host/dbname/用户名」。
// 它刻意**不打这条串本身**：串里那段是口令（红线：凭据不入库，也不入日志与报错）。
type UnreadableTargetError struct{ Environment string }

func (e *UnreadableTargetError) Error() string {
	return fmt.Sprintf(
		"ENVIRONMENT=%s 的 DATABASE_URL 是一条 PG 库串（postgres(ql):// 那种 URL 形状，"+
			"或 libpq 的 keyword=value 形状），但拆不出 host/dbname/用户名，"+
			"无法与 PROD_DB_FINGERPRINTS 比对 ⇒ 拒绝启动。"+
			"读不出来的方向是两套进程写同一个 canonical 库，多拒一次的方向只是起不来（D-T604-5）；"+
			"本条不回显连接串本身。改法：把它写成 `postgres://user@host:5432/dbname` 那种标准形状，"+
			"或按双签口径把 ENVIRONMENT 设成 production",
		e.Environment)
}

// UnknownEnvironmentError 是词表外的取值（D-T604-2）。
type UnknownEnvironmentError struct{ Environment string }

func (e *UnknownEnvironmentError) Error() string {
	return fmt.Sprintf(
		"unsupported ENVIRONMENT %q: only %s are accepted（词表外的值必须拒绝启动，"+
			"否则它会落进非生产档、连鉴权口径一起换掉，见 D-T604-2/3）",
		e.Environment, strings.Join(EnvironmentValues, "|"))
}

// CheckDBIsolation 是判据本体（不读 env、不碰 Config，好让两侧的闸喂同一份用例表）。
func CheckDBIsolation(environment, databaseURL string, fingerprints []string) error {
	if !IsValidEnvironment(environment) {
		return &UnknownEnvironmentError{Environment: environment}
	}
	if IsProductionEnv(environment) {
		// 生产档连自己的库是应有之义：黑名单对它不参与比较。
		// 这一格必须由用例表的正向证人钉住——反过来（生产档也被这条闸判）会让
		// 运维在上线第一天就把整道闸注释掉。
		return nil
	}
	if len(fingerprints) == 0 {
		if environment == "staging" {
			return &MissingFingerprintsError{Environment: environment}
		}
		return nil
	}
	target, ok := ParseDBTarget(databaseURL)
	if !ok {
		// 旧版这一格是 `return nil`：拆不出来就当没这事。方向错了——
		// 「拆不出来」有两种：sqlite 那种本机文件库（确实没有 host 可比），
		// 和「是 PG 库串但形状读不出」（`POSTGRESQL://`、坏端口、带 # 的口令）。
		// 后者放行的话，进程会连上它黑名单里那个库，只是这道闸没看懂那条串
		// （2026-10-20 code review 抓到）。
		if looksLikePGTarget(databaseURL) {
			return &UnreadableTargetError{Environment: environment}
		}
		return nil
	}
	if fp, matched := hit(fingerprints, target); matched {
		return &IsolationError{Environment: environment, Target: target, Fingerprint: fp}
	}
	return nil
}

// RequireDBIsolation 是装配面调的那句（cmd/go-api 在建连**之前**调它）。
func (c Config) RequireDBIsolation() error {
	return CheckDBIsolation(c.Environment, c.DatabaseURL, c.ProdDBFingerprints)
}
