// T6-02：切流矩阵——path × method 的**可切性**判定与生成物渲染。
//
// 为什么这件事要有一份代码而不是两份文档：
// Ingress 的 path 匹配**不看 HTTP 方法**（决策点 D-10）。`/api/v1/reflections` 在契约里
// GET 与 POST 共用同一个 path，`/api/v1/memories` 同 path 还挂 POST、`/api/v1/memories/{id}`
// 同 path 还挂 DELETE ⇒ 一刀 Exact 下去，写侧方法也被搬进这台只注册了 GET 的 mux，
// 客户端拿到 405——**与灰度百分比无关**，0% 也救不回来（转交发生在 handler 内部，
// 路径没匹配上根本进不到 handler）。这个坑历史上是靠 server.go 与 50-ingress.yaml 的
// 注释记住的；注释不会在有人往 Ingress 加一行时跳出来反对，所以它现在是一条判据。
//
// 三张表各由一侧真源读出，两两不同源（本仓铁律：断言两侧不许同源）：
//   - Go 侧注册面 ← 调用方把 `httpapi.RoutePlan()` 传进来（本包不许 import httpapi，
//     那会成环：httpapi 已经 import 了 cutover）；
//   - 反代归属 ← `deploy/k8s/50-ingress.yaml` 与 `deploy/compose/Caddyfile` 的原文；
//   - 「这个 path 上到底有几种方法」← `contracts/endpoints.md`（由 FastAPI 生成的冻结登记表）。
//
// `contracts/cutover-matrix.md` 是**生成物**：由 tools/cutovermatrix 调 BuildRows＋Render 写出，
// 闸测（path_method_matrix_test.go）拿同一对函数重算一遍比字节。手改 md ⇒ 过期即红。
package cutover

import (
	"fmt"
	"regexp"
	"sort"
	"strings"
)

// GoRoute 是一条 Go 侧注册声明。Method 为空串＝不限方法（兜底 404 那条），
// 它不参与 path×method 对账，见 Matches 的用法。
type GoRoute struct {
	Method string
	Path   string
	Domain string
}

// ProxyRule 是反代的一条 path 归属规则。
type ProxyRule struct {
	Path     string
	PathType string // Exact | Prefix
	Backend  string // go-api / py-api
	Source   string // 哪个文件读来的（报错要能指回那一行所在的清单）
}

// ContractOp 是 contracts/endpoints.md 里登记的一个 (方法, 路径)。
type ContractOp struct {
	Method string
	Path   string
}

// DomainRow 是矩阵的一行＝一个切流域键。
type DomainRow struct {
	Domain       string
	EnvKey       string
	DefaultPct   int
	GoOps        []ContractOp // Go 该域注册的方法×路径（声明面，与批次无关）
	DomainOps    []ContractOp // 契约里属于该域的全部操作
	Complete     []string     // 方法齐全（契约方法 ⊆ Go 方法）的 path
	Incomplete   []string     // 缺方法的 path（上刀前必须补齐的那些）
	BoundRules   []ProxyRule  // 反代里判给 go-api 且命中该域的规则
	BoundMissing []ContractOp // 已被判给 go-api、但 Go 没注册的契约操作（＝现网会 405）
	Cuttable     bool
	Reason       string
}

var (
	prefixSegRe = regexp.MustCompile(`^/api/v1/([a-z0-9-]+)`)
	placeholder = regexp.MustCompile(`\{[^/}]+\}`)
	rulePathRE  = regexp.MustCompile(`^\s*-\s*path:\s*(\S+)\s*$`)
	ruleTypeRE  = regexp.MustCompile(`^\s*pathType:\s*(\S+)\s*$`)
	ruleNameRE  = regexp.MustCompile(`^\s*name:\s*(\S+)\s*$`)
	opRowRE     = regexp.MustCompile(
		`^\|\s*\d+\s*\|\s*(GET|POST|PATCH|PUT|DELETE)\s*\|\s*` + "`" + `([^` + "`" + `]+)` + "`" + `\s*\|`)
	caddyHandleRE = regexp.MustCompile(`^\s*handle(?:\s+(?:&?[\w/*-]+|"(?:[^"]+)"))?\s*\{?\s*$`)
	caddyProxyRE  = regexp.MustCompile(`^\s*reverse_proxy\s+(\S+)`)
)

// NormalizePath 把 pattern 里的 `{param}` 归一成 `{}`：
// 契约写 `{memory_id}`、Go 也写 `{memory_id}` 是本项目的巧合，不是约束。
// 归一之后两侧才能真正比集合（改名一个路径参数不该打红切流闸）。
func NormalizePath(p string) string { return placeholder.ReplaceAllString(p, "{}") }

// DomainOfPath 取 `/api/v1/<域>/…` 的第一段；不是这个形状（/health、/）返回空串。
func DomainOfPath(p string) string {
	m := prefixSegRe.FindStringSubmatch(p)
	if m == nil {
		return ""
	}
	return m[1]
}

// RuleMatches 复刻 Ingress 的 path 归属语义（不含方法——这正是本闸的主题）。
// Exact 走归一后的逐字相等，Prefix 走**段边界**前缀（`/api/v1/mood` 不该命中 `/api/v1/moodx`）。
func RuleMatches(r ProxyRule, path string) bool {
	target := NormalizePath(path)
	rule := NormalizePath(r.Path)
	if r.PathType == "Exact" {
		return target == rule
	}
	if rule == "/" {
		return true
	}
	if strings.HasPrefix(rule, "/api/v1/") && !strings.HasPrefix(target, "/api/v1/") {
		return false
	}
	return target == rule || strings.HasPrefix(target, rule+"/")
}

// Match 判定一条契约操作归不归这条规则。
//
// `method` 参数**故意不参与判断**——这一行就是决策点 D-10 的全部形状：
// Ingress（与 Caddy/Nginx 同理）按 path 归属，方法只是操作的名字。
// 签名里带上它，是为了让调用点和闸测都能把「POST 也一样被搬走」这件事写在代码里，
// 而不是写在注释里等人读。
func (r ProxyRule) Match(method, path string) bool {
	_ = method
	return RuleMatches(r, path)
}

// ParseIngressRules 读 50-ingress.yaml 的 paths 段（只看 rules/http/paths 里那三元组）。
// 不引第三方 YAML 库是有意的：这个清单的**形状**由 backend/tests/test_k8s_manifests.py
// 用真正的 YAML 解析器验过，这里只取 path/pathType/backend name 三个键。
func ParseIngressRules(src string, source string) []ProxyRule {
	var out []ProxyRule
	lines := strings.Split(src, "\n")
	for i, ln := range lines {
		m := rulePathRE.FindStringSubmatch(ln)
		if m == nil {
			continue
		}
		r := ProxyRule{Path: m[1], PathType: "Prefix", Backend: "", Source: source}
		for j := i + 1; j < len(lines) && j <= i+8; j++ {
			// 看见下一条 `- path:` 立刻收口：这一条规则的三元组到此为止。
			// 少了这个 break，八行窗口会**读进下一条规则**——mood 那两条相邻的 Exact
			// 会让「把 Exact 改成 Prefix」这类清单改动被解析回 Exact，
			// 于是 VPrefix／D-5 那条判据在真坏料上绿（2026-10-19 由
			// scripts/mutate_t602_path_method_matrix_gates.py 的 M3 抓到并修）。
			if rulePathRE.MatchString(lines[j]) {
				break
			}
			if t := ruleTypeRE.FindStringSubmatch(lines[j]); t != nil {
				r.PathType = t[1]
			}
			if n := ruleNameRE.FindStringSubmatch(lines[j]); n != nil && r.Backend == "" {
				r.Backend = n[1]
			}
		}
		out = append(out, r)
	}
	return out
}

// ParseCaddyRules 读 Caddyfile：每个 `handle <matcher> { … }` 块里第一条 reverse_proxy 的
// 上游名就是这一段的归属；块没有 matcher（裸 handle {）时记为 "/"。
func ParseCaddyRules(src string, source string) []ProxyRule {
	var out []ProxyRule
	var pending *ProxyRule
	for _, ln := range strings.Split(src, "\n") {
		trimmed := strings.TrimSpace(ln)
		if strings.HasPrefix(trimmed, "#") {
			continue
		}
		if m := caddyHandleRE.FindStringSubmatch(ln); m != nil {
			match := strings.TrimSpace(strings.TrimPrefix(trimmed, "handle"))
			match = strings.TrimSuffix(match, "{")
			match = strings.TrimSpace(strings.TrimPrefix(match, "&"))
			match = strings.Trim(match, `"`)
			if match == "" {
				match = "/"
			}
			pending = &ProxyRule{Path: match, PathType: "Prefix", Source: source}
			continue
		}
		if m := caddyProxyRE.FindStringSubmatch(ln); m != nil && pending != nil {
			pending.Backend = strings.SplitN(m[1], ":", 2)[0]
			out = append(out, *pending)
			pending = nil
			continue
		}
		if trimmed == "}" {
			pending = nil
		}
	}
	return out
}

// ParseContractOps 读 contracts/endpoints.md 的登记表行。
func ParseContractOps(src string) []ContractOp {
	var out []ContractOp
	for _, ln := range strings.Split(src, "\n") {
		m := opRowRE.FindStringSubmatch(ln)
		if m == nil {
			continue
		}
		out = append(out, ContractOp{Method: m[1], Path: m[2]})
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Path == out[j].Path {
			return out[i].Method < out[j].Method
		}
		return out[i].Path < out[j].Path
	})
	return out
}

// BuildRows 是判定的全部逻辑：给三张真源，产出每个域键一行＋可切性。
// 它不读文件、不 import httpapi，所以闸测与生成工具都能调（同一份实现，不存在两套口径）。
func BuildRows(keys map[string]int, rules []ProxyRule, ops []ContractOp, plan []GoRoute) []DomainRow {
	domains := make([]string, 0, len(keys))
	for d := range keys {
		domains = append(domains, d)
	}
	sort.Strings(domains)

	var out []DomainRow
	for _, d := range domains {
		row := DomainRow{Domain: d, EnvKey: EnvKeyOf(d), DefaultPct: keys[d]}
		// Go 侧该域注册了什么（声明面：含 nil 批次时没注册的那几条，见 RoutePlan 的注释）。
		seen := map[string]bool{}
		for _, r := range plan {
			if r.Domain == d && r.Method != "" {
				key := r.Method + " " + NormalizePath(r.Path)
				if !seen[key] {
					seen[key] = true
					row.GoOps = append(row.GoOps, ContractOp{Method: r.Method, Path: r.Path})
				}
			}
		}
		// 契约侧该域有哪些操作，并按 path 归堆算「方法齐全」的 path 集合。
		byPath := map[string][]ContractOp{}
		for _, op := range ops {
			if DomainOfPath(op.Path) == d {
				row.DomainOps = append(row.DomainOps, op)
				p := NormalizePath(op.Path)
				byPath[p] = append(byPath[p], op)
			}
		}
		paths := make([]string, 0, len(byPath))
		for p := range byPath {
			paths = append(paths, p)
		}
		sort.Strings(paths)
		for _, p := range paths {
			complete := true
			for _, op := range byPath[p] {
				if !seen[op.Method+" "+p] {
					complete = false
				}
			}
			if complete {
				row.Complete = append(row.Complete, p)
			} else {
				row.Incomplete = append(row.Incomplete, p)
			}
		}
		// 反代里判给 go-api、并且**属于这个域**的规则（按 path 的第一段归域，
		// 与 DomainOfPath 对契约操作的归域同一套函数——两条规则都归错域的话，
		// 下面 BoundMissing 的集合会跟着错，所以这里不许用字符串前缀另算一遍）。
		for _, r := range rules {
			if r.Backend == "go-api" && DomainOfPath(r.Path) == d {
				row.BoundRules = append(row.BoundRules, r)
			}
		}
		for _, op := range row.DomainOps {
			for _, r := range row.BoundRules {
				if RuleMatches(r, op.Path) && !seen[op.Method+" "+NormalizePath(op.Path)] {
					row.BoundMissing = append(row.BoundMissing, op)
				}
			}
		}
		row.Cuttable = len(row.BoundRules) > 0 && len(row.BoundMissing) == 0
		switch {
		case len(row.BoundMissing) > 0:
			row.Reason = fmt.Sprintf("已上刀的 path 上还有 %d 个方法 Go 没注册（%s）⇒ 这些请求 405/404，"+
				"与灰度百分比无关（D-10）", len(row.BoundMissing), joinOps(row.BoundMissing))
		case len(row.BoundRules) == 0 && len(row.Incomplete) > 0:
			row.Reason = fmt.Sprintf("未上刀：path 匹配不看方法，而这个域还有方法不全的 path（%s）⇒ "+
				"Go 补齐写方法之前不许加 path（D-10）", strings.Join(row.Incomplete, "、"))
		case len(row.BoundRules) == 0:
			row.Reason = "未上刀：域内方法齐全，但反代清单里还没有指 go-api 的这条 path"
		default:
			row.Reason = fmt.Sprintf("已上刀 %d 条规则，命中的契约操作全部由 Go 回答（%s）",
				len(row.BoundRules), joinRules(row.BoundRules))
		}
		out = append(out, row)
	}
	return out
}

// EnvKeyOf 把域键换成 config.go 里的放量变量名。
// 这张映射写在被 config.go 与 main.go **共同**对账的地方：键名漂了就红（见 Violations）。
func EnvKeyOf(domain string) string {
	return "CLOSEN_CUTOVER_" + strings.ToUpper(domain)
}

func joinOps(ops []ContractOp) string {
	parts := make([]string, 0, len(ops))
	for _, o := range ops {
		parts = append(parts, o.Method+" "+o.Path)
	}
	return strings.Join(parts, "、")
}

func joinRules(rs []ProxyRule) string {
	parts := make([]string, 0, len(rs))
	for _, r := range rs {
		parts = append(parts, r.PathType+" "+r.Path)
	}
	return strings.Join(parts, "、")
}

// ParseConfigKeys 从 config.go 读出「放量键 → 默认百分比」，域键取变量名尾段的小写。
// 矩阵的行集合由它决定：加一个键不写实现 ⇒ 假旋钮那条判据红；
// 实现里多一个域却没有键 ⇒ 由 path_method_matrix_test.go 的对称判据红。
func ParseConfigKeys(src string) map[string]int {
	re := regexp.MustCompile(`envInt\("CLOSEN_CUTOVER_([A-Z_]+)",\s*(-?\d+)\)`)
	out := map[string]int{}
	for _, m := range re.FindAllStringSubmatch(src, -1) {
		out[strings.ToLower(m[1])] = atoiOrZero(m[2])
	}
	return out
}

// ParseAssemblyKeys 从 cmd/go-api/main.go 的 `cutover.New(…, map[string]int{…})` 里读出
// 真正装配进灰度闸的域键。D-G12 的教训：memories 这个键在 config 里声明了三年，
// 却一直没进这张 map，于是「缺格 ⇒ 当成 满额自答」是**静默**的。
func ParseAssemblyKeys(src string) map[string]bool {
	re := regexp.MustCompile(`"([a-z]+)":\s*cfg\.Cutover(\w+)`)
	out := map[string]bool{}
	for _, m := range re.FindAllStringSubmatch(src, -1) {
		if strings.ToLower(m[2]) != m[1] {
			// 键名与字段名对不上（比如 "mood": cfg.CutoverReflections）＝装配表自己写错了，
			// 这个域按「没装配」记，让对称判据去红——不许在这里替它猜一个。
			continue
		}
		out[m[1]] = true
	}
	return out
}

func atoiOrZero(s string) int {
	neg := strings.HasPrefix(s, "-")
	digits := strings.TrimPrefix(s, "-")
	n := 0
	for _, ch := range digits {
		if ch < '0' || ch > '9' {
			return 0
		}
		n = n*10 + int(ch-'0')
	}
	if neg {
		return -n
	}
	return n
}

// 违例前缀码：每条判据的红都要能指回**它自己那一条**（[[feedback-mutation-anchor-must-reach-target]]）。
// 闸测按码过滤 Violations 的输出，所以一条变异只会打红它对应的那条测，
// 而不是「violations 非空」这种谁都能撞红的笼统断言。
const (
	VEmpty    = "空集"
	VMissing  = "缺方法"
	VPrefix   = "前缀刀"
	VPhantom  = "假旋钮"
	VKeyDrift = "键名脱节"
	VAssembly = "装配缺格"
	VPublic   = "公网暴露"
)

// Violations 是判据本体：任何一条非空都意味着「清单上写着的切流与代码能答的不是同一回事」。
func Violations(rows []DomainRow, rules []ProxyRule, plan []GoRoute, goConfigSrc string,
	assembly map[string]bool) []string {
	var v []string
	// 证人：三张真源都得非空。扫空集会让下面所有判据在「没有需要切的域」这件事上永远绿。
	if len(rows) == 0 {
		v = append(v, VEmpty+"：矩阵零行＝一个切流域键都没解析出来（config.go 的 CLOSEN_CUTOVER_* 漂了？）")
	}
	if len(rules) == 0 {
		v = append(v, VEmpty+"：反代清单解析出零条 path 规则＝本闸在扫空集")
	}
	if len(plan) == 0 {
		v = append(v, VEmpty+"：httpapi.RoutePlan() 返回零条路由＝声明表被掏空")
	}
	domainsWithGoRoutes := map[string]bool{}
	for _, r := range plan {
		if r.Domain != "" {
			domainsWithGoRoutes[r.Domain] = true
		}
	}
	for _, row := range rows {
		// 1) 已上刀但方法不全：现网 405，最硬的一条。
		for _, op := range row.BoundMissing {
			v = append(v, fmt.Sprintf("%s：%s %s 被反代判给 go-api，但 Go 没注册这个操作 ⇒ 当场 405/404（D-10）",
				VMissing, row.EnvKey, op.Method+" "+op.Path))
		}
		// 2) 域级 Prefix 只许用在「方法齐全」的域上（D-5：键是域级、刀是端点级的那种例外，
		//    必须用 Exact 一条条列，否则新端点会自动被搬进没注册它的 mux）。
		for _, r := range row.BoundRules {
			if r.PathType == "Prefix" && len(row.Incomplete) > 0 {
				v = append(v, fmt.Sprintf("%s：%s %s 用 Prefix 上刀，但这个域还有方法不全的 path（%s）⇒ "+
					"只许按端点列 Exact（D-5／D-10）", VPrefix, row.EnvKey, r.Path,
					strings.Join(row.Incomplete, "、")))
			}
		}
		// 3) 键与实现一一对应：config.go 里有键、Go 侧却没有这个域的任何路由＝假旋钮。
		if !domainsWithGoRoutes[row.Domain] {
			v = append(v, fmt.Sprintf("%s：%s config 里有这个放量键，但 httpapi 的声明表里没有任何 domain=%q 的路由"+
				"⇒ 拨了不响的假旋钮（D-G12 踩过）", VPhantom, row.EnvKey, row.Domain))
		}
		// 4) 键名必须真的住在 config.go 里：EnvKeyOf 造出来的名字对不上现网变量＝矩阵在自说自话。
		if !strings.Contains(goConfigSrc, `"`+row.EnvKey+`"`) {
			v = append(v, fmt.Sprintf("%s：%s go-core/internal/config/config.go 里找不到这个变量名"+
				"⇒ 矩阵的键列与放量面脱节", VKeyDrift, row.EnvKey))
		}
		// 5) 键必须真的装配进灰度闸（D-G12：config 有键、main.go 的 map 没格＝静默当成 满额自答，
		//    下一刀上 path 时键调回 0 也回滚不了）。
		if !assembly[row.Domain] {
			v = append(v, fmt.Sprintf("%s：%s cmd/go-api/main.go 的 cutover.New 里没有这个域的格子"+
				"⇒ 缺格语义是「当成 满额自答」，下一刀上 path 时键调回 0 也回滚不了", VAssembly, row.EnvKey))
		}
	}
	// 6) Caddyfile 是**对公网**那一层：go-api 不对公网暴露（方案 §12／T0-01 生产 compose），
	//    所以反代里任何指向 go-api 的公网 path 都是清单写错，与方法齐全与否无关。
	for _, r := range rules {
		if r.Backend == "go-api" && strings.Contains(r.Source, "Caddy") {
			v = append(v, fmt.Sprintf("%s：%s 把 %s 反代到 go-api ⇒ go-api 不对公网暴露（方案 §12／T0-01）",
				VPublic, r.Source, r.Path))
		}
	}
	for d := range assembly {
		found := false
		for _, row := range rows {
			if row.Domain == d {
				found = true
			}
		}
		if !found {
			v = append(v, fmt.Sprintf("%s：装配表里的域 %q 在 config.go 的 CLOSEN_CUTOVER_* 里没有对应键"+
				"⇒ main.go 读了一个不存在的放量面", VAssembly, d))
		}
	}
	return v
}

// Render 把矩阵写成 contracts/cutover-matrix.md（生成物，禁止手改）。
func Render(rows []DomainRow, violations []string, ingressSource, caddySource, contractSource string) string {
	var b strings.Builder
	b.WriteString("# 切流 path × method 矩阵（T6-02）\n\n")
	b.WriteString("> 本文件由 `go run ./tools/cutovermatrix -write` 生成，**禁止手改**。\n")
	b.WriteString("> 判据住在 `go-core/internal/cutover/matrix.go::Violations()`，闸测在\n")
	b.WriteString("> `go-core/internal/cutover/path_method_matrix_test.go`；三张真源分别是\n")
	b.WriteString(fmt.Sprintf("> `%s`（反代归属）、`%s`（path 上有几种方法）、`internal/httpapi` 的路由声明表（Go 答得了哪些）。\n",
		ingressSource, contractSource))
	b.WriteString("> \n")
	b.WriteString("> ## 为什么这张表存在\n")
	b.WriteString(">\n")
	b.WriteString("> Ingress（与 Caddy/Nginx 同理）的 **path 匹配不看 HTTP 方法**（决策点 D-10）。\n")
	b.WriteString("> 于是「这个域的某条 path 我 Go 已经实现了 GET」≠「这条 path 可以切」：\n")
	b.WriteString("> 同一条 path 上还挂着 POST/DELETE，一刀下去它们**一起**搬到 go-api，\n")
	b.WriteString("> 而 Go 那台 mux 没注册它们 ⇒ 405——与灰度百分比无关，0% 也救不回来\n")
	b.WriteString("> （转交发生在 handler 内部，路径没匹配上根本进不到 handler）。\n")
	b.WriteString(">\n")
	b.WriteString("> **硬性结论（不许「顺手试一下」）**：`memories` 与 `reflections` 两个域，\n")
	b.WriteString("> 在 Go 补齐写方法（`POST /api/v1/reflections`、`POST /api/v1/memories`、\n")
	b.WriteString("> `DELETE /api/v1/memories/{memory_id}`，含同 path 的 search/version 那几条）之前，\n")
	b.WriteString("> **不得**在反代清单里加指向 go-api 的 path；`mood` 域只按端点列 `Exact`，\n")
	b.WriteString("> 不许写成 `Prefix /api/v1/mood`（D-5：键是域级、刀是端点级）。\n\n")
	b.WriteString("## 矩阵\n\n")
	b.WriteString("| 域键（放量变量） | 默认百分比 | 反代已上刀 | Go 已注册（方法 路径） | 方法齐全的 path | 缺方法的 path（上刀前必须补） | 是否可切 | 原因 |\n")
	b.WriteString("|---|---|---|---|---|---|---|---|\n")
	for _, r := range rows {
		bound := "—（未上刀）"
		if len(r.BoundRules) > 0 {
			bound = joinRules(r.BoundRules)
		}
		cut := "**不可切**"
		if r.Cuttable {
			cut = "**可切**"
		}
		b.WriteString(fmt.Sprintf("| `%s` | %d%% | %s | %s | %s | %s | %s | %s |\n",
			r.EnvKey, r.DefaultPct, bound, orDash(joinOps(r.GoOps)),
			orDash(strings.Join(r.Complete, "、")), orDash(strings.Join(r.Incomplete, "、")),
			cut, r.Reason))
	}
	b.WriteString("\n## 口径备忘\n\n")
	b.WriteString("- **域级键 × 端点级 path** 是合法的（D-5），但一条 `Prefix` 只许用在「方法齐全的域」上；\n")
	b.WriteString("- 「不可切」不是缺陷而是**裁决**：它拦的是「把写方法搬进没注册它的进程」这件事；\n")
	b.WriteString("- 百分比为 0 时 go-api 把请求**逐字转交** py-api（`internal/cutover`），\n")
	b.WriteString("  所以「已上刀」不等于「已放量」——但「上刀且方法不全」在 0% 也已经是坏的：\n")
	b.WriteString("  没匹配上的路径进不到 handler，转交无从谈起。\n")
	if caddySource != "" {
		b.WriteString(fmt.Sprintf("\n反代第二处出口（`%s`）解析到的归属：%s。\n",
			caddySource, "go-api 不对公网暴露，故该清单里不应出现任何指向 go-api 的 path"))
	}
	if len(violations) > 0 {
		b.WriteString("\n## 判据违例\n\n")
		for _, x := range violations {
			b.WriteString("- " + x + "\n")
		}
	}
	return b.String()
}

func orDash(s string) string {
	if s == "" {
		return "—"
	}
	return s
}
