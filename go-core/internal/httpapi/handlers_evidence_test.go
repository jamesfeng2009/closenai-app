package httpapi_test

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/auth"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/config"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/httpapi"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store"
)

// N9 对外读端点的线上形状闸（裁决七-7）。这里的断言全部打在**响应字节**上，
// 不是打在 Go 结构体上：字段名与逐字兼容项一样，只有字节面才是客户端看到的。

type stubEvidenceReader struct {
	insight domain.InsightReadable
	err     error
	items   []domain.EvidenceItem
	pattern []domain.PatternReadable
	links   []domain.PatternEvidenceLink
	counts  domain.SupportCounts
}

func (s *stubEvidenceReader) GetInsightForReading(context.Context, string, string) (domain.InsightReadable, error) {
	return s.insight, s.err
}

func (s *stubEvidenceReader) ListLiveEvidenceForInsight(context.Context, string, string) ([]domain.EvidenceItem, error) {
	return s.items, nil
}

func (s *stubEvidenceReader) CountInsightEvidenceSupport(context.Context, string, string) (domain.SupportCounts, error) {
	return s.counts, nil
}

func (s *stubEvidenceReader) ListPatternsForInsightReading(context.Context, string, string) ([]domain.PatternReadable, error) {
	return s.pattern, nil
}

func (s *stubEvidenceReader) ListLivePatternEvidenceForInsight(context.Context, string, string) ([]domain.PatternEvidenceLink, error) {
	return s.links, nil
}

func newEvidenceServer(t *testing.T, environment, switchValue string,
	reader domain.InsightEvidenceReader) (*httpapi.Server, *auth.Signer) {
	t.Helper()
	cfg := config.Config{AppName: "ClosenAI", Environment: environment,
		HTTPAddr: ":0", InsightEvidenceRead: switchValue}
	signer := auth.NewSigner("evidence-test-secret-evidence-test-secret",
		30*time.Minute, 30*24*time.Hour)
	var explainer *domain.InsightExplainer
	if reader != nil {
		explainer = domain.NewInsightExplainer(reader,
			domain.Policy{Read: domain.ReadPolicy{EvidenceMaxItems: 20, PatternMaxItems: 6}})
	}
	return httpapi.NewServer(cfg, signer, auth.NewService(store.NewFakeStore(), signer,
		30*24*time.Hour), explainer, nil, nil, nil, nil, nil, nil), signer
}

const evidencePath = "/api/v1/personal-model/insights/i1/evidence"

func seedReader() *stubEvidenceReader {
	observed := time.Date(2026, 10, 1, 3, 0, 0, 0, time.UTC)
	strength := domain.EvidenceItem{
		ID: "e1", EvidenceType: "quote", Role: "supporting",
		Strength: 0.9, HasStrength: true,
		Excerpt: "今晚又三点才睡", HasExcerpt: true,
		ObservedAt: observed, HasObservedAt: true,
		Source: domain.EvidenceSource{RecordID: "s1", SourceType: "memory",
			SourceTable: "memories", SourceID: "m1"},
	}
	return &stubEvidenceReader{
		insight: domain.InsightReadable{ID: "i1", UserID: "u1",
			Statement: "你常在深夜记录", ClaimType: "behaviour", Status: "published",
			Confidence: 0.82, HasConfidence: true,
			TimeFrom: observed, HasTimeFrom: true},
		items:  []domain.EvidenceItem{strength},
		counts: domain.SupportCounts{Total: 3, Live: 1},
		pattern: []domain.PatternReadable{{ID: "p1", PatternType: "temporal",
			Description: "深夜记录", Status: "supported", EvidenceCount: 1}},
		links: []domain.PatternEvidenceLink{{PatternID: "p1", Item: strength}},
	}
}

func TestN9EvidenceEndpointShapeAndEnvelope(t *testing.T) {
	reader := seedReader()
	server, _ := newEvidenceServer(t, "development", "on", reader)
	req := httptest.NewRequest("GET", evidencePath, nil)
	req.Header.Set("X-User-Id", "u1")
	req.Header.Set("X-Trace-Id", "t-evidence")
	rec := httptest.NewRecorder()
	server.Handler().ServeHTTP(rec, req)

	if rec.Code != 200 {
		t.Fatalf("status = %d body=%s", rec.Code, rec.Body.String())
	}
	body := rec.Body.String()
	// 五键顺序是逐字兼容项（endpoints.md §一 / envelope.go 的注释）。
	if !strings.HasPrefix(body, `{"code":200,"message":"success","data":{`) {
		t.Fatalf("信封前缀不符：%s", body)
	}
	if !strings.HasSuffix(body, `,"trace_id":"t-evidence","user_id":"u1"}`) {
		t.Fatalf("信封尾部不符：%s", body)
	}
	// memory_refs 的三键逐字（frontend-compat.md:91）：改名＝依据回链静默消失。
	for _, key := range []string{`"memory_ref":{"memory_id":"m1"`, `"excerpt":"今晚又三点才睡"`,
		`"created_at":"2026-10-01T03:00:00Z"`} {
		if !strings.Contains(body, key) {
			t.Errorf("响应里少了逐字兼容项 %s\n%s", key, body)
		}
	}
	// 摘要：count 是活支撑（1），withdrawn 是 3-1=2，strength 是数值不是 "strong"。
	var payload struct {
		Data struct {
			EvidenceSummary struct {
				Count          int      `json:"count"`
				WithdrawnCount int      `json:"withdrawn_count"`
				Strength       *float64 `json:"strength"`
			} `json:"evidence_summary"`
			Evidence []struct {
				EvidenceID string `json:"evidence_id"`
				Source     struct {
					SourceTable string `json:"source_table"`
					SourceID    string `json:"source_id"`
				} `json:"source"`
			} `json:"evidence"`
			Patterns []struct {
				ID string `json:"id"`
			} `json:"patterns"`
		} `json:"data"`
	}
	if err := json.Unmarshal([]byte(body), &payload); err != nil {
		t.Fatalf("解码: %v", err)
	}
	summary := payload.Data.EvidenceSummary
	if summary.Count != 1 || summary.WithdrawnCount != 2 {
		t.Errorf("摘要 = %+v，期望 count 1 / withdrawn 2", summary)
	}
	if summary.Strength == nil || *summary.Strength != 0.9 {
		t.Errorf("strength = %v，期望数值 0.9", summary.Strength)
	}
	if len(payload.Data.Evidence) != 1 ||
		payload.Data.Evidence[0].Source.SourceTable != "memories" {
		t.Fatalf("依据面不符：%+v", payload.Data.Evidence)
	}
	if len(payload.Data.Patterns) != 1 || payload.Data.Patterns[0].ID != "p1" {
		t.Errorf("二跳归组不符：%+v", payload.Data.Patterns)
	}
	if strings.Contains(body, `"strong"`) {
		t.Error("响应里出现了 \"strong\" 字符串，N9-12 要求回数值")
	}
}

// TestN9NotExistAndInvisibleAreSameBody 是 N9-4 的闸：越权/不存在/状态不可见
// 三种情形必须给出**逐字相同**的 404 体，否则文案差异就是一份 id 枚举 oracle。
func TestN9NotExistAndInvisibleAreSameBody(t *testing.T) {
	notFound := seedReader()
	notFound.err = domain.ErrInsightNotFound

	reader := seedReader()
	reader.insight.Status = "draft"

	serverNotFound, _ := newEvidenceServer(t, "development", "on", notFound)
	serverDraft, _ := newEvidenceServer(t, "development", "on", reader)

	bodies := []string{}
	for _, tc := range []struct {
		name   string
		server *httpapi.Server
	}{{"not_found", serverNotFound}, {"not_visible", serverDraft}} {
		req := httptest.NewRequest("GET", evidencePath, nil)
		req.Header.Set("X-User-Id", "u1")
		// 固定 trace_id：不固定的话两次响应的信封只有 trace_id 不同，
		// 「逐字相同」这条断言就在比一个没在比的东西。
		req.Header.Set("X-Trace-Id", "t-same")
		rec := httptest.NewRecorder()
		tc.server.Handler().ServeHTTP(rec, req)
		if rec.Code != 404 {
			t.Errorf("%s: status = %d，期望 404", tc.name, rec.Code)
		}
		bodies = append(bodies, rec.Body.String())
	}
	if bodies[0] != bodies[1] {
		t.Errorf("两种情形回的不是同一个体，id 存在性泄露：\n %s\n %s", bodies[0], bodies[1])
	}
	if !strings.Contains(bodies[0], `"code":40402`) {
		t.Errorf("期望业务码 40402，实际 %s", bodies[0])
	}
}

func TestN9EvidenceEndpointRequiresAuthInProduction(t *testing.T) {
	server, _ := newEvidenceServer(t, "production", "on", seedReader())
	req := httptest.NewRequest("GET", evidencePath, nil)
	rec := httptest.NewRecorder()
	server.Handler().ServeHTTP(rec, req)
	if rec.Code != 401 {
		t.Fatalf("status = %d body=%s，生产无 token 必须 401", rec.Code, rec.Body.String())
	}
	if !strings.Contains(rec.Body.String(), `"code":40103`) {
		t.Errorf("期望 40103，实际 %s", rec.Body.String())
	}
}

// TestN9KillSwitchOff 钉 D-3 的回退开关：off 时不是 503、不是 403，
// 而是与「这条路径从未存在」同形的信封 404（沿用 handleNotFound 的形状）。
func TestN9KillSwitchOff(t *testing.T) {
	for name, build := range map[string]func(*testing.T, string) *httpapi.Server{
		"switch_off": func(t *testing.T, value string) *httpapi.Server {
			server, _ := newEvidenceServer(t, "development", value, seedReader())
			return server
		},
		"no_reader": func(t *testing.T, value string) *httpapi.Server {
			server, _ := newEvidenceServer(t, "development", value, nil)
			return server
		},
	} {
		server := build(t, "off")
		req := httptest.NewRequest("GET", evidencePath, nil)
		req.Header.Set("X-User-Id", "u1")
		rec := httptest.NewRecorder()
		server.Handler().ServeHTTP(rec, req)
		if rec.Code != 404 {
			t.Errorf("%s: status = %d，期望 404", name, rec.Code)
		}
		if !strings.Contains(rec.Body.String(), `"code":404`) {
			t.Errorf("%s: 期望裸 404 码，实际 %s", name, rec.Body.String())
		}
	}
}

// TestN9RouteIsRegisteredOnce 防「同名路由注册两次」与「新端点漏进登记表」：
// 前者会让 ServeMux 启动即 panic，后者由 §五 的字节闸把关，这里只确认 Go 侧
// 这条路径确实挂在预期方法上（非 GET 一律 404/405，不该是 200）。
func TestN9RouteIsRegisteredOnce(t *testing.T) {
	server, _ := newEvidenceServer(t, "development", "on", seedReader())
	for _, method := range []string{"POST", "PUT", "DELETE"} {
		req := httptest.NewRequest(method, evidencePath, nil)
		req.Header.Set("X-User-Id", "u1")
		rec := httptest.NewRecorder()
		server.Handler().ServeHTTP(rec, req)
		if rec.Code == 200 {
			t.Errorf("%s 不该命中依据展开端点", method)
		}
	}
}
