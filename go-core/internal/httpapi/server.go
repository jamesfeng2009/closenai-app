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
	// now 是**时钟注入点**，默认 time.Now。契约回放必须把它冻在录制那一刻：
	// curriculum 的 week_start/每张卡的 date 都是「今天所在本地周」的函数，
	// 夹具跨天重放会得到另一周（同 discovery 的 7/14 天窗口，但周视图是整周平移，
	// 连 1.5 天的余量都救不了）。业务代码一行都不该感知测试——所以是字段而不是参数。
	now func() time.Time

	mux *http.ServeMux
}

func NewServer(cfg config.Config, signer *auth.Signer, service *auth.Service,
	explainer *domain.InsightExplainer, batch1 *Batch1, batch2 *Batch2,
	batch3 *Batch3) *Server {
	s := &Server{
		cfg:        cfg,
		middleware: NewMiddleware(signer, cfg.IsProduction()),
		auth:       service,
		evidence:   explainer,
		batch1:     batch1,
		batch2:     batch2,
		batch3:     batch3,
		now:        time.Now,
		mux:        http.NewServeMux(),
	}
	s.routes()
	return s
}

func (s *Server) Handler() http.Handler { return s.middleware.Wrap(s.mux) }

func (s *Server) routes() {
	// /health is the frozen contract shape (contracts/endpoints.md row for GET /health);
	// /healthz is the added process-readiness probe and is not part of that register.
	s.mux.HandleFunc("GET /health", s.handleHealth)
	s.mux.HandleFunc("GET /healthz", s.handleHealthz)
	s.mux.HandleFunc("POST /api/v1/auth/register", s.handleRegister)
	s.mux.HandleFunc("POST /api/v1/auth/login", s.handleLogin)
	s.mux.HandleFunc("GET /api/v1/auth/me", s.handleMe)
	// N9 的权威依据展开端点（V3 05 §18 逐字形状 + 本项目 /api/v1 前缀）。
	// 与 /healthz 同理：**不在** contracts/endpoints.md §五 的冻结登记表里，
	// 登记表由 FastAPI 生成，加一行 Go 专有端点会打红 test_contract_freeze.py。
	s.mux.HandleFunc("GET /api/v1/personal-model/insights/{id}/evidence", s.handleInsightEvidence)
	// N8 批 1 的两个切流域。注册在 batch1 非 nil 时才有，见上面字段注释。
	if s.batch1 != nil {
		s.mux.HandleFunc("POST /api/v1/analytics/batch", s.handleAnalyticsBatch)
		s.mux.HandleFunc("GET /api/v1/discovery/daily", s.handleDiscoveryDaily)
	}
	// N8 批 2 的 curriculum 域（两条端点都是 journey_events 的读写，零 LLM）。
	if s.batch2 != nil {
		s.mux.HandleFunc("GET /api/v1/curriculum/week", s.handleCurriculumWeek)
		s.mux.HandleFunc("POST /api/v1/curriculum/{event_id}/read", s.handleCurriculumRead)
	}
	// N8 批 3 的 journey 域（GET 会补写剧本、POST 只改一行状态，两条都零 LLM）。
	if s.batch3 != nil {
		s.mux.HandleFunc("GET /api/v1/journey/events", s.handleJourneyEvents)
		s.mux.HandleFunc("POST /api/v1/journey/events/acknowledge",
			s.handleJourneyAcknowledge)
	}
	s.mux.HandleFunc("GET /api/v1/health", s.handleNotFound)
	s.mux.HandleFunc("/", s.handleNotFound)
}

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
