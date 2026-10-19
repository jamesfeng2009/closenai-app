// 装配证人：`internalEnabled()` 是六格 AND，夹具少接一格的**表现不是那一格 404，
// 而是整个 /internal/v3 面 404**。这件事已经真翻过两次车：
//   - T4-02 把 goals 算进注册判据之后，本工具的夹具没跟着补 ⇒ 跨进程 10 条整片红；
//   - T5-04 补审计面（第六格）时同一条链又断了一次，红的还是那 20 条跨进程测。
//
// 那两次都是「Python 侧整片红 → 回头查 Go」的形状，而归因指错了地方（BFF 没做错什么）。
// 所以这一格必须有**自己的**证人：不看 BFF、不起进程、不发真 SQL，只问一句
// 「夹具造的这颗 Server 里，声明表上每一条 /internal/v3/ 路由都注册上了吗」。
// [[feedback-gate-needs-positive-witness]]：反向归零闸（「契约里没有内部面」）永远兜不住这个。
package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/httpapi"
)

const witnessToken = "svc-e2e-token"

// internalRoutes 取声明表上全部内部面路由，并把 `{id}` 那一格填成真实形状的路径参数。
func internalRoutes(t *testing.T) []struct{ method, path string } {
	t.Helper()
	var out []struct{ method, path string }
	for _, r := range httpapi.RoutePlan() {
		if !strings.HasPrefix(r.Path, "/internal/v3/") {
			continue
		}
		if r.Method == "" {
			t.Errorf("%s 不限方法 ⇒ 内部面里不该有兜底条目", r.Path)
		}
		out = append(out, struct{ method, path string }{
			method: r.Method,
			path:   fillPathParams(r.Path),
		})
	}
	// 正向证人的一半：清单不许是空的。扫到零条时下面两条测会「全绿」而什么都没证。
	if len(out) != 11 {
		t.Fatalf("声明表里的 /internal/v3/ 路由 = %d 条，期望 11 ⇒ 这一格换了口径，证人跟着改", len(out))
	}
	return out
}

func fillPathParams(path string) string {
	for strings.Contains(path, "{") {
		open := strings.Index(path, "{")
		end := strings.Index(path, "}")
		path = path[:open] + "x-e2e" + path[end+1:]
	}
	return path
}

func get(h http.Handler, method, path, token string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, "http://go.internal"+path, nil)
	req.Header.Set("X-Service-Token", token)
	req.Header.Set("X-User-Id", fixtureUser)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func TestFixtureSurfaceRegistersEveryDeclaredInternalRoute(t *testing.T) {
	h := buildHandler(witnessToken)
	for _, r := range internalRoutes(t) {
		rec := get(h, r.method, r.path, witnessToken)
		// 判据不是「状态码 404」而是**那条兜底 handler 的字面文案**：
		// 域内的「查无此行」也是 404（`/internal/v3/insights/{id}` 换个 id 就是），
		// 按码判会让证人因为夹具数据对不上而红，而那与「少接一格」是两件事。
		// 兜底那条的文案写在 server.go 的 handleNotFound 里，这里按字面认它。
		if rec.Code == http.StatusNotFound &&
			strings.Contains(rec.Body.String(), `"message":"Not Found"`) {
			t.Errorf("%s %s ⇒ 走到兜底 404：夹具少接一格＝整个内部面不注册，跨进程那 20 条会一起红，"+
				"而归因第一次总会指向 BFF", r.method, r.path)
		}
	}
}

// TestFixtureSurfaceVanishesWithoutToken：密钥这一格在夹具里也要是真的那种「不注册」，
// 否则跨进程取证里「403 与 404 谁能出现」这一条就取决于夹具而不是生产代码。
func TestFixtureSurfaceVanishesWithoutToken(t *testing.T) {
	h := buildHandler("")
	for _, r := range internalRoutes(t) {
		if rec := get(h, r.method, r.path, ""); rec.Code != http.StatusNotFound {
			t.Errorf("密钥为空时 %s %s = %d，期望 404 ⇒ 夹具长出了生产侧没有的行为",
				r.method, r.path, rec.Code)
		}
	}
}

// TestFixtureAuditSurfaceServesTwoRows 是第六格（T5-04）自己那一格的证人：
// 上一测只判「不是 404」（`Proactives` 置 nil 时六格 AND 会让整面 404，那里已经拦得住），
// 这里要的是**两行真的从夹具端出来**——字段名、可空列与回链动线都得在这条面上露过一次脸，
// 否则「装配上了」与「装配上了一颗空仓储」在两测里同一张脸。
func TestFixtureAuditSurfaceServesTwoRows(t *testing.T) {
	rec := get(buildHandler(witnessToken), http.MethodGet, "/internal/v3/proactives", witnessToken)
	if rec.Code != http.StatusOK {
		t.Fatalf("审计面 = %d，期望 200：%s", rec.Code, rec.Body.String())
	}
	body := rec.Body.String()
	for _, want := range []string{"pa-e2e-sent", "pa-e2e-blocked", "quiet_hours", "je-e2e"} {
		if !strings.Contains(body, want) {
			t.Errorf("审计面产物里没有 %q ⇒ 这一格装配上了但没内容：审计口径不可测：%s", want, body)
		}
	}
}
