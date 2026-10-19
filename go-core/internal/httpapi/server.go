// Package httpapi is the public HTTP surface of Go Core. The wire protocol stays
// HTTP/JSON + SSE (V3 06 §: protobuf is only the internal MQ contract), and every
// response uses the five-key envelope frozen in contracts/endpoints.md §一.
package httpapi

import (
	"errors"
	"io"
	"net/http"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/auth"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/config"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

const maxBodyBytes = 1 << 20

type Server struct {
	cfg        config.Config
	middleware *Middleware
	auth       *auth.Service
	// evidence 是 N9 的权威读路径；nil 时端点按信封 404 回，
	// 让「没接读面」与「读面关闭」对客户端同形（不新增错误码）。
	evidence *domain.InsightExplainer
	// batch1 是 N8 批 1（analytics + discovery）的依赖面；nil 时这两条路由
	// **不注册**，于是它们与「Go 侧从来没有这个域」同形（落到 handleNotFound），
	// 灰度期把百分比压回 0 也不必改代码。
	batch1 *Batch1
	// batch2 是 N8 批 2（curriculum 域）的依赖面；nil 时那两条路由不注册，与批 1 同策。
	batch2 *Batch2
	// batch3 是 N8 批 3（journey 域）的依赖面；nil 时那两条路由不注册。
	// 这个域与批 1/2 的差别是 GET 会写库（见 batch3.go 的 ensurePrefilled），
	// 所以「不注册」在灰度期意味着**连补写都不发生**——未放量的用户仍由 py-api 补，
	// 两侧行为一致的前提是 0% 时请求真的整条转交（internal/cutover）。
	batch3 *Batch3
	// batch5 是 N8 批 5（mood + reflections 三条纯读）的依赖面；因为这个域全是读，
	// 「nil 时不注册」没有批 3/批 4 那种「连补写都不发生」的连带后果。
	batch5 *Batch5
	// batch4 是 N8 批 4（onboarding 域）的依赖面；nil 时那两条路由不注册，口径同批 3。
	// 这个域是已切流域里唯一**一次请求写三处**的（users.referral_code、
	// onboarding_profiles、journey_events 六行），且三处的写条件不同源，
	// 见 batch4.go 头注释——所以未放量时的逐字一致完全依赖 0% 真转交。
	batch4 *Batch4
	// batch6 是 N8 批 6（memories 两条纯读）的依赖面；nil 时两条路由不注册，口径同批 5。
	// 这批的特殊处是**两条 path 都不能上 Ingress 刀**（同 path 还挂 POST/DELETE，
	// 见 batch6.go 头注释），所以「已实现」与「从入口可达」在这批是两件事——
	// 与批 5 那条 `GET /api/v1/reflections` 同一档。
	batch6 *Batch6
	// v3 是服务间内部面（`/internal/v3/*`，ADR-002）的依赖面；nil 或密钥为空时
	// 这一组路由**不注册**，与六个批次同一口径（gated），口径的取舍见 v3.go 头注释。
	// 它不参与切流（domain 空串）：客户端永远不该走到这里，所以「切给谁」这一说不成立。
	v3 *V3
	// now 是**时钟注入点**，默认 time.Now。契约回放必须把它冻在录制那一刻：
	// curriculum 的 week_start/每张卡的 date 都是「今天所在本地周」的函数，
	// 夹具跨天重放会得到另一周（同 discovery 的 7/14 天窗口，但周视图是整周平移，
	// 连 1.5 天的余量都救不了）。业务代码一行都不该感知测试——所以是字段而不是参数。
	// registered 是**实际注册**的路由形状（method/path/domain），由 registerRoutes 逐条填。
	// 它存在的唯一理由是让「声明表 ⇄ 运行时」这条对账不用抄第二遍（routes.go 头注释）。
	registered []Route

	now func() time.Time

	mux *http.ServeMux
}

func NewServer(cfg config.Config, signer *auth.Signer, service *auth.Service,
	explainer *domain.InsightExplainer, batch1 *Batch1, batch2 *Batch2,
	batch3 *Batch3, batch4 *Batch4, batch5 *Batch5, batch6 *Batch6, v3 *V3) *Server {
	s := &Server{
		cfg:        cfg,
		middleware: NewMiddleware(signer, cfg.IsProduction()),
		auth:       service,
		evidence:   explainer,
		batch1:     batch1,
		batch2:     batch2,
		batch3:     batch3,
		batch4:     batch4,
		batch5:     batch5,
		batch6:     batch6,
		v3:         v3,
		now:        time.Now,
		mux:        http.NewServeMux(),
	}
	s.routes()
	return s
}

func (s *Server) Handler() http.Handler { return s.middleware.Wrap(s.mux) }

// routes 的注册面已抽成 routeTable（routes.go）：声明表是切流矩阵的唯一真源，
// 这里只负责「把表消费一遍」——加一条路由改表，不改这个函数。
func (s *Server) routes() { s.registerRoutes() }

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	rc := fromContext(r.Context())
	WriteSuccess(w, healthPayload{
		Status:  "ok",
		AppName: s.cfg.AppName,
		Version: "1.0.0",
	}, rc.traceID, rc.userID)
}

type healthPayload struct {
	Status  string `json:"status"`
	AppName string `json:"app"`
	Version string `json:"version"`
}

func (s *Server) handleHealthz(w http.ResponseWriter, r *http.Request) {
	rc := fromContext(r.Context())
	WriteSuccess(w, map[string]string{"status": "ok"}, rc.traceID, rc.userID)
}

func (s *Server) handleNotFound(w http.ResponseWriter, r *http.Request) {
	rc := fromContext(r.Context())
	// FastAPI answers unknown paths with its own 404 body; Go Core keeps the
	// envelope and the 404 status, and the exact text is registered as a gap.
	WriteError(w, apierr.New(apierr.CodeNotFound, "Not Found"), rc.traceID, rc.userID)
}

func readBody(r *http.Request) ([]byte, error) {
	body, err := io.ReadAll(io.LimitReader(r.Body, maxBodyBytes))
	if err != nil {
		return nil, err
	}
	return body, nil
}

func writeJSONError(w http.ResponseWriter, r *http.Request, e *apierr.Error) {
	rc := fromContext(r.Context())
	WriteError(w, e, rc.traceID, rc.userID)
}

func asAPIError(err error) *apierr.Error {
	var e *apierr.Error
	if errors.As(err, &e) {
		return e
	}
	var v *ValidationError
	if errors.As(err, &v) {
		return v.AsError()
	}
	return apierr.New(apierr.CodeInternalError, "服务器内部错误 / Internal server error")
}
