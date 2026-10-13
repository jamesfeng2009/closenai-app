// Package httpapi is the public HTTP surface of Go Core. The wire protocol stays
// HTTP/JSON + SSE (V3 06 §: protobuf is only the internal MQ contract), and every
// response uses the five-key envelope frozen in contracts/endpoints.md §一.
package httpapi

import (
	"errors"
	"io"
	"net/http"

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
	mux      *http.ServeMux
}

func NewServer(cfg config.Config, signer *auth.Signer, service *auth.Service,
	explainer *domain.InsightExplainer) *Server {
	s := &Server{
		cfg:        cfg,
		middleware: NewMiddleware(signer, cfg.IsProduction()),
		auth:       service,
		evidence:   explainer,
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
