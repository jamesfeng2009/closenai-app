// Package cutover 是 N8 strangler 切流的灰度闸：同一个域在灰度期内两侧都能答，
// 由这里决定「这一条请求归谁答」。
//
// 为什么权重不写在 Ingress 里（tasks.md N8 那句「Ingress 灰度 1%→100%」）：
// 目标集群是 k3s 自带的 Traefik，普通 Ingress 对象只能整条 path 二选一，
// 加权分流要 TraefikService（IngressRoute CRD）——往那台共享生产集群装 CRD 属另一回事，
// 且本机没有它的 kubeconfig，写出来的清单无法自证。
// 于是 Ingress 只做「域级 path 归属」这一刀（deploy/k8s/50-ingress.yaml），
// 百分比落在 go-api 侧：未选中的请求**逐字转交 py-api**，
// 于是 0% 是真的 0%（Go 只多一跳代理，业务逻辑一行没跑），回滚 = 把数改回 0。
//
// 粘滞按 user_id 取模：同一用户在灰度期反复请求必须落在同一侧，
// 否则「A 侧写入、B 侧读出」的行为差异会被随机化，1% 的样本再也聚不起来。
package cutover

import (
	"hash/fnv"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strconv"
)

// Gate 持每个已切流域的百分比与 py-api 的上游地址。
type Gate struct {
	upstream *url.URL
	percents map[string]int
	proxy    *httputil.ReverseProxy
}

// New 构造灰度闸。upstream 为空且存在 <100 的域时返回错误：
// 一个「以为会转交其实无处可转」的开关比没有开关更糟（同 N9 回退开关的口径）。
func New(upstream string, percents map[string]int) (*Gate, error) {
	g := &Gate{percents: map[string]int{}}
	partial := false
	for domain, pct := range percents {
		if pct < 0 || pct > 100 {
			return nil, &RangeError{Domain: domain, Percent: pct}
		}
		g.percents[domain] = pct
		if pct < 100 {
			partial = true
		}
	}
	if !partial {
		return g, nil
	}
	parsed, err := url.Parse(upstream)
	if err != nil || parsed.Host == "" {
		return nil, &UpstreamError{Upstream: upstream}
	}
	g.upstream = parsed
	g.proxy = &httputil.ReverseProxy{
		Rewrite: func(pr *httputil.ProxyRequest) {
			// pr.Out 是 pr.In 的克隆，所以客户端的每一个头（含 X-Trace-Id、Authorization）
			// 都逐字带过去；这里只改 URL 与 Host。TestDelegateForwardsVerbatim 钉的就是这件事。
			pr.SetURL(g.upstream)
			// 上游要看到原始 Host 与客户端真实路径（SetURL 会把 X-Forwarded-* 填好）。
			pr.Out.Host = pr.In.Host
		},
	}
	return g, nil
}

type RangeError struct {
	Domain  string
	Percent int
}

func (e *RangeError) Error() string {
	return "cutover percent for " + e.Domain + " must be 0..100, got " +
		strconv.Itoa(e.Percent)
}

type UpstreamError struct{ Upstream string }

func (e *UpstreamError) Error() string {
	return "cutover below 100% requires CLOSEN_PY_API_URL (the py-api address to delegate to), got " +
		strconv.Quote(e.Upstream)
}

// Selected 报告这个域该不该由 Go 侧自己回答。
//
// nil 接收者＝没配灰度闸（本地开发、单测、还没切流的部署），一律按 100% 回答：
// 「没接开关」不能变成「整条路由无人应答」。
func (g *Gate) Selected(domain, userID string) bool {
	if g == nil {
		return true
	}
	pct, ok := g.percents[domain]
	if !ok || pct >= 100 {
		return true
	}
	if pct <= 0 {
		return false
	}
	return bucket(userID) < pct
}

// Delegate 把未选中的请求原样交给 py-api，返回是否已经处理完。
func (g *Gate) Delegate(w http.ResponseWriter, r *http.Request, domain, userID string) bool {
	if g == nil || g.Selected(domain, userID) {
		return false
	}
	g.proxy.ServeHTTP(w, r)
	return true
}

// bucket 是 user_id 的稳定 0..99 落点（FNV-1a，纯函数，不受进程重启影响）。
func bucket(userID string) int {
	h := fnv.New32a()
	_, _ = h.Write([]byte(userID))
	return int(h.Sum32() % 100)
}
