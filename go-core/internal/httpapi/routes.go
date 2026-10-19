// T6-02：路由的**声明表**。
//
// 为什么把 `s.mux.HandleFunc("GET /…", …)` 那十二行抽成一张表（server.go 里原来是散着的）：
// 切流矩阵要回答的是「这条 path 上 Go 答得了哪些方法」，而它必须有一个**可枚举的**真源。
// 三种做法里只有这张表站得住：
//   - 人抄一份清单进测试 ⇒ 两侧同源，抄错没人红（本仓铁律：断言两侧不许同源）；
//   - 读 `http.ServeMux` 的注册结果 ⇒ 它没有导出注册表，而且带 `METHOD /path` 的 pattern
//     在 nil 批次时**根本没注册**，从运行时读到的集合是「今天这台进程」的集合，
//     不是「这个域补齐了没有」的集合——矩阵要的是后者；
//   - 声明表 ⇒ 注册与对账都从同一份数据出发，`routes()` 逐条消费它，
//     少一条或多一条都会在 `TestRouteTableRegistersEverySpec` 红。
//
// `Domain` 是切流域键（与 cmd/go-api/main.go 里 `cutover.New` 那张 map 的 key 逐字同一批字符串）。
// 空串＝不参与切流：/health、/healthz、auth 三条、N9 的依据展开、兜底 404——
// 它们要么两侧行为由别的闸管（auth 面有 interop 闸），要么本来就没有「切给谁」这一说。
package httpapi

import "net/http"

// Route 是一条路由的对外形状（切流矩阵与 tools/cutovermatrix 读的就是它）。
type Route struct {
	// Method 为空串＝**不限方法**（ServeMux 的裸 `/` pattern）。
	// 它只有兜底 404 那一条，切流矩阵按「不限方法的条目不参与 path×method 对账」处理。
	Method string
	Path   string
	Domain string
}

// routeSpec 比 Route 多两件事：处理器，和「这条路由要等哪个批次接上才注册」。
//
// `gated` 为 nil＝无条件注册。非 nil 时它只决定**注册与否**，不决定声明是否存在：
// 生产装配（cmd/go-api/main.go）把六个批次全部接上，所以今天这张表就是对外 HTTP 面的全集；
// 单测里造 nil 是为了让「没接读面」与「读面关闭」对客户端同形，不是另一种切流口径。
type routeSpec struct {
	method  string
	path    string
	domain  string
	gated   func(*Server) bool
	handler func(*Server) http.HandlerFunc
}

// routeTable 是 Go Core 对外 HTTP 面的**唯一**声明。顺序不重要（ServeMux 按具体度匹配），
// 但形状重要：`method + " " + path` 拼出来的就是注册用的 pattern。
var routeTable = []routeSpec{
	{method: "GET", path: "/health", handler: func(s *Server) http.HandlerFunc { return s.handleHealth }},
	{method: "GET", path: "/healthz", handler: func(s *Server) http.HandlerFunc { return s.handleHealthz }},
	{method: "POST", path: "/api/v1/auth/register", handler: func(s *Server) http.HandlerFunc { return s.handleRegister }},
	{method: "POST", path: "/api/v1/auth/login", handler: func(s *Server) http.HandlerFunc { return s.handleLogin }},
	{method: "GET", path: "/api/v1/auth/me", handler: func(s *Server) http.HandlerFunc { return s.handleMe }},
	// N9 的权威依据展开端点（不在 contracts/endpoints.md §五 的冻结登记表里：
	// 那张表由 FastAPI 生成，加一行 Go 专有端点会打红 test_contract_freeze.py）。
	{method: "GET", path: "/api/v1/personal-model/insights/{id}/evidence",
		handler: func(s *Server) http.HandlerFunc { return s.handleInsightEvidence }},
	// N8 批 1：analytics＋discovery 两个域（最低耦合的一对，两条各自一个域键）。
	{method: "POST", path: "/api/v1/analytics/batch", domain: "analytics",
		gated:   func(s *Server) bool { return s.batch1 != nil },
		handler: func(s *Server) http.HandlerFunc { return s.handleAnalyticsBatch }},
	{method: "GET", path: "/api/v1/discovery/daily", domain: "discovery",
		gated:   func(s *Server) bool { return s.batch1 != nil },
		handler: func(s *Server) http.HandlerFunc { return s.handleDiscoveryDaily }},
	// N8 批 2：curriculum 域（两条端点都是 journey_events 的读写，零 LLM）。
	{method: "GET", path: "/api/v1/curriculum/week", domain: "curriculum",
		gated:   func(s *Server) bool { return s.batch2 != nil },
		handler: func(s *Server) http.HandlerFunc { return s.handleCurriculumWeek }},
	{method: "POST", path: "/api/v1/curriculum/{event_id}/read", domain: "curriculum",
		gated:   func(s *Server) bool { return s.batch2 != nil },
		handler: func(s *Server) http.HandlerFunc { return s.handleCurriculumRead }},
	// N8 批 3：journey 域（GET 会补写剧本、POST 只改一行状态，两条都零 LLM）。
	{method: "GET", path: "/api/v1/journey/events", domain: "journey",
		gated:   func(s *Server) bool { return s.batch3 != nil },
		handler: func(s *Server) http.HandlerFunc { return s.handleJourneyEvents }},
	{method: "POST", path: "/api/v1/journey/events/acknowledge", domain: "journey",
		gated:   func(s *Server) bool { return s.batch3 != nil },
		handler: func(s *Server) http.HandlerFunc { return s.handleJourneyAcknowledge }},
	// N8 批 4：onboarding 域（同一个 path 挂两个方法；POST 一次写三处，GET 只读一行）。
	{method: "POST", path: "/api/v1/onboarding/first-read", domain: "onboarding",
		gated:   func(s *Server) bool { return s.batch4 != nil },
		handler: func(s *Server) http.HandlerFunc { return s.handleFirstReadSubmit }},
	{method: "GET", path: "/api/v1/onboarding/first-read", domain: "onboarding",
		gated:   func(s *Server) bool { return s.batch4 != nil },
		handler: func(s *Server) http.HandlerFunc { return s.handleFirstReadRestore }},
	// N8 批 5：mood 两条纯读＋reflections 一条纯读。
	// 同域的 POST /mood/checkin、POST /reflections、POST /reflections/generate **不在这里**：
	// 它们要 LLM、配额与 AI 同意，属后面几家——于是 reflections 这个域的方法集**天然不全**，
	// 而 Ingress 的 path 匹配不看方法（决策点 D-10），所以上面那条 GET 今天从入口不可达。
	// 这条「不可切」由 internal/cutover/path_method_matrix_test.go 判，不靠注释记得住。
	{method: "GET", path: "/api/v1/mood/history", domain: cutoverMood,
		gated:   func(s *Server) bool { return s.batch5 != nil },
		handler: func(s *Server) http.HandlerFunc { return s.handleMoodHistory }},
	{method: "GET", path: "/api/v1/mood/trend", domain: cutoverMood,
		gated:   func(s *Server) bool { return s.batch5 != nil },
		handler: func(s *Server) http.HandlerFunc { return s.handleMoodTrend }},
	{method: "GET", path: "/api/v1/reflections", domain: cutoverReflections,
		gated:   func(s *Server) bool { return s.batch5 != nil },
		handler: func(s *Server) http.HandlerFunc { return s.handleReflectionsList }},
	// N8 批 6：memories 两条纯读。`{memory_id}` 是单段通配，会吃掉
	// `GET /api/v1/memories/search`——这与 Python 侧按声明序匹配到 `/memories/{memory_id}`
	// 同形（夹具 list-path-collision 钉它），不是 Go 的 pattern 写错了。
	{method: "GET", path: "/api/v1/memories", domain: cutoverMemories,
		gated:   func(s *Server) bool { return s.batch6 != nil },
		handler: func(s *Server) http.HandlerFunc { return s.handleMemoriesList }},
	{method: "GET", path: "/api/v1/memories/{memory_id}", domain: cutoverMemories,
		gated:   func(s *Server) bool { return s.batch6 != nil },
		handler: func(s *Server) http.HandlerFunc { return s.handleMemoryDetail }},
	// T3-01／T3-02：服务间内部面（ADR-002）。**domain 一律留空**——它不参与切流，
	// 因为客户端契约里没有这几条 path（Ingress 也没有任何规则覆盖 /internal/v3，
	// 见 v3.go 头注释）。开关是「密钥 + 依赖」两格同时成立，不是灰度百分比：
	// 这一面对外不存在，拨进来的一方是我们的 BFF，它要么有密钥要么没有。
	{method: "GET", path: "/internal/v3/insights",
		gated:   func(s *Server) bool { return s.internalEnabled() },
		handler: func(s *Server) http.HandlerFunc { return s.handleV3Insights }},
	{method: "GET", path: "/internal/v3/insights/{id}",
		gated:   func(s *Server) bool { return s.internalEnabled() },
		handler: func(s *Server) http.HandlerFunc { return s.handleV3InsightDetail }},
	{method: "POST", path: "/internal/v3/insights/{id}/feedback",
		gated:   func(s *Server) bool { return s.internalEnabled() },
		handler: func(s *Server) http.HandlerFunc { return s.handleV3InsightFeedback }},
	// 兜底 404：不是切流面，但形状要留在表里（GET /api/v1/health 是 app 级端点的历史坑）。
	// `/` 这条**不写方法**＝不限方法，与改写前的 `s.mux.HandleFunc("/", …)` 逐字同形；
	// 给它补上 GET 会让 POST / 从「404 信封」变成「Go 默认的 405」，那是一次没人批的行为改动。
	{method: "GET", path: "/api/v1/health", handler: func(s *Server) http.HandlerFunc { return s.handleNotFound }},
	{path: "/", handler: func(s *Server) http.HandlerFunc { return s.handleNotFound }},
}

// RoutePlan 返回**声明面**的全集（含批次没接上时不注册的那几条）。
// 切流矩阵要的就是这个：某个域「补齐了没有」不能取决于「今天这个测试进程接了几个 db」。
func RoutePlan() []Route {
	out := make([]Route, 0, len(routeTable))
	for _, r := range routeTable {
		out = append(out, Route{Method: r.method, Path: r.path, Domain: r.domain})
	}
	return out
}

// registerRoutes 逐条消费 routeTable，并把实际注册的形状记进 s.registered。
// 那个集合是**运行时**口径（nil 批次时少几条），与 s.mux 的真相同源，
// 所以 TestRouteTableRegistersEverySpec 能拿它核声明表——不是把表再抄一遍。
func (s *Server) registerRoutes() {
	for _, spec := range routeTable {
		if spec.gated != nil && !spec.gated(s) {
			continue
		}
		pattern := spec.path
		if spec.method != "" {
			pattern = spec.method + " " + spec.path
		}
		s.mux.HandleFunc(pattern, spec.handler(s))
		s.registered = append(s.registered, Route{
			Method: spec.method, Path: spec.path, Domain: spec.domain,
		})
	}
}

// RegisteredRoutes 返回这台进程**真的**注册了的路由（顺序＝注册顺序）。
func (s *Server) RegisteredRoutes() []Route {
	out := make([]Route, len(s.registered))
	copy(out, s.registered)
	return out
}
