// T6-02 的声明表自证：表 ⇄ 实际注册 ⇄ mux 真答案，三者必须逐字同形。
//
// 为什么单独一条测：切流矩阵读的是 `RoutePlan()`（声明面），而现网跑的是 `s.mux`（运行时面）。
// 如果 registerRoutes 与 routeTable 之间漂了（少消费一条、pattern 拼错、gated 判反），
// 矩阵会「全绿但全错」——它拿那张表算方法齐全，而流量打的是另一套。
// 这条测把两侧钉在一起：`mux.Handler(method, path)` 返回的是 **mux 自己**记的 pattern，
// 不是我把表抄第二遍，所以断言两侧不同源。
package httpapi

import (
	"net/http"
	"net/http/httptest"
	"regexp"
	"testing"
	"time"
)

// paramValueRe 把 `{memory_id}` 这类单段通配换成一个具体值（探针请求用）。
var paramValueRe = regexp.MustCompile(`\{[^/}]+\}`)

// newBareServer 只装配 mux 与注册面：注册路径上不会碰 cfg／signer／db，
// 所以这里给零值——给真的依赖反而会把「注册」这件事与「跑业务」混在一条测里。
func newBareServer() *Server {
	return &Server{mux: http.NewServeMux(), now: func() time.Time { return time.Time{} }}
}

func TestRouteTableRegistersEverySpec(t *testing.T) {
	s := newBareServer()
	s.registerRoutes()

	registered := s.RegisteredRoutes()

	// ① 表里 gated==nil 的条数必须与实际注册条数相等：
	//    多出来＝注册了表里没有的东西（矩阵看不见它），少了＝某条被 gated 误判掉。
	var want int
	seen := map[string]string{}
	for _, spec := range routeTable {
		pattern := spec.path
		if spec.method != "" {
			pattern = spec.method + " " + spec.path
		}
		if dup, ok := seen[pattern]; ok {
			t.Errorf("声明表里 %q 出现了两次（%s ⇄ %s）⇒ ServeMux 会 panic，注册面从此不完整",
				pattern, dup, spec.path)
		}
		seen[pattern] = spec.path
		if spec.gated == nil {
			want++
		}
	}
	if len(registered) != want {
		t.Errorf("批次全 nil 时应注册 %d 条（gated==nil 的那些），实得 %d ⇒ registerRoutes 的门槛判错",
			want, len(registered))
	}

	// ② 每一条声明的 pattern 都必须**匹配得上自己**：把它注册进一台只挂探针的 ServeMux，
	//    再拿一个把 `{param}` 换成具体值的请求去打。这条抓的是形状错——
	//    `{event-id}` 这种非法参数名、`{x:}`、多写一层斜杠，都会在 ServeMux 里静默匹配不上，
	//    而矩阵按表算「这个方法注册了」，现网却一条都路由不到（切流闸从此失真）。
	for _, spec := range routeTable {
		pattern := spec.path
		if spec.method != "" {
			pattern = spec.method + " " + spec.path
		}
		probe := http.NewServeMux()
		matched := false
		probe.HandleFunc(pattern, func(http.ResponseWriter, *http.Request) { matched = true })
		method := spec.method
		if method == "" {
			method = http.MethodGet
		}
		target := paramValueRe.ReplaceAllString(spec.path, "x")
		rec := httptest.NewRecorder()
		probe.ServeHTTP(rec, httptest.NewRequest(method, target, nil))
		if !matched {
			t.Errorf("声明 %q 匹配不上自己的实例 %q ⇒ ServeMux 永远不会把流量给它，"+
				"而切流矩阵已经把它算成「Go 答得了」", pattern, target)
		}
	}

	// ③ 声明表全集（含批次没接上的那些）必须比实际注册的多——
	//    相等就意味着 gated 全被掏空了，矩阵读的是这份全集，注册用的是另一份。
	plan := RoutePlan()
	if len(plan) <= len(registered) {
		t.Errorf("RoutePlan()=%d 条 ⇄ 实际注册=%d 条 ⇒ 批次门槛全丢了（矩阵的声明面从此失真）",
			len(plan), len(registered))
	}

	// ④ 兜底那条必须**不限方法**：给它补上 GET 会让 POST / 从 404 信封变成 Go 的默认 405。
	rec := httptest.NewRecorder()
	s.mux.ServeHTTP(rec, httptest.NewRequest("POST", "/whatever-not-registered", nil))
	if rec.Code != http.StatusNotFound {
		t.Errorf("未注册路径拿到 %d 而不是 404 ⇒ 裸 \"/\" 那条丢了方法通配（行为改动，没人批过）",
			rec.Code)
	}

	// ⑤ 带切流域键的声明**一条都不许**出现在「批次全 nil」的注册面里。
	//    ① 数的是条数，而条数是从同一张表推出来的——把某条的 gated 掏成 nil 时，
	//    两侧会**一起**加一，① 咬不住（2026-10-19 由 M10 变异抓到）。
	//    这条判的是语义：域键＝这一域归切流管，机制没接线时 Go 必须不注册它，
	//    否则 Ingress 一刀指过来，Go 会带着 nil 依赖去答这一域（5xx 归 Go，且灰度键调回 0 也回滚不了）。
	gatedPattern := map[string]bool{}
	for _, spec := range routeTable {
		if spec.domain == "" {
			continue
		}
		pattern := spec.path
		if spec.method != "" {
			pattern = spec.method + " " + spec.path
		}
		gatedPattern[pattern] = true
	}
	for _, r := range registered {
		pattern := r.Path
		if r.Method != "" {
			pattern = r.Method + " " + r.Path
		}
		if gatedPattern[pattern] && r.Domain != "" {
			t.Errorf("%s 在批次全 nil 时仍然注册了 ⇒ 它的 gated 丢了（或本来就是 nil）："+
				"这一域的开关从此管不到注册面", pattern)
		}
	}
	if len(gatedPattern) == 0 {
		t.Error("声明表里一条带域键的路由都没有 ⇒ 面扫空集，上面那条判据在自证空气")
	}
}
