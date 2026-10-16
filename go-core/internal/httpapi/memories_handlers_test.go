// 批 6 两条 handler 的分支单测（假件面）。
//
// 逐字契约属 ② 的回放闸，这里补的是夹具**给不出**的几类情形，与批 5 同一分工：
//   - store 报错 ⇒ 信封 50003（Python 侧不会主动坏库，夹具只能录到成功路径）；
//   - `limit` 校验失败时**一次库都不许读**（红线打在假件的调用计数上）；
//   - `labels` 的四条解析规则（列是自由 TEXT，坏 JSON／顶层数组／非字符串元素这些
//     格子要逐条凑齐，靠播种十二行不划算，而且其中几行根本写不进库）；
//   - conviction 的 `0.0`／`1.0` 与 NaN（夹具只有真库里出现过的那些值）。
package httpapi

import (
	"context"
	"errors"
	"math"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

type memoryFakeStore struct {
	listRows   []domain.MemoryRow
	listErr    error
	detailRow  domain.MemoryRow
	detailFnd  bool
	detailErr  error
	seenLimit  int64
	seenCat    string
	seenCatSet bool
	seenID     string
	seenUser   string
	calls      int
}

func (f *memoryFakeStore) MemoryList(_ context.Context, userID string,
	limit int64, category string) ([]domain.MemoryRow, error) {
	f.calls++
	f.seenUser = userID
	f.seenLimit = limit
	f.seenCat = category
	f.seenCatSet = true
	if f.listRows != nil {
		return f.listRows, f.listErr
	}
	return nil, f.listErr
}

func (f *memoryFakeStore) MemoryByID(_ context.Context, memoryID,
	userID string) (domain.MemoryRow, bool, error) {
	f.calls++
	f.seenID = memoryID
	f.seenUser = userID
	return f.detailRow, f.detailFnd, f.detailErr
}

func newBatch6Server(t *testing.T, store *memoryFakeStore) *Server {
	t.Helper()
	// users/moods 用 forbidden 假件：这两条端点**一条 users 都不读**
	// （memory_service.list_memories 只有一条 repo 调用），读到了就是打红。
	return newN8Server(t, n8ForbiddenStore{t: t}, n8ForbiddenStore{t: t},
		&n8NoRelayStore{t: t}, nil, nil, nil, nil, &Batch6{Memories: store})
}

func doBatch6(t *testing.T, srv *Server, path string) (int, string) {
	t.Helper()
	r := httptest.NewRequest("GET", path, nil)
	r.Header.Set("X-Trace-Id", "trace-test")
	r.Header.Set("X-User-Id", "n8-user")
	rec := httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, r)
	return rec.Code, rec.Body.String()
}

func mustBatch6OK(t *testing.T, srv *Server, path string) string {
	t.Helper()
	code, body := doBatch6(t, srv, path)
	if code != 200 {
		t.Fatalf("%s status = %d，body = %s", path, code, body)
	}
	return body
}

// memoryRowJSON 是 MemoryResponse 的一个行体：13 键的书写序就是 schemas.py:49-63
// 的声明序，`similarity`/`regressed` 恒为 null/false 且排在 labels **之前**。
func TestMemoriesListRowBytes(t *testing.T) {
	rows := []domain.MemoryRow{{
		ID: "m-0", UserID: "n8-user", Content: "我讨厌临时会议", Category: "values",
		Status: "current", Source: "self", Conviction: 0.0,
		CreatedAt: time.Date(2026, 10, 12, 3, 0, 0, 0, time.UTC),
		Labels:    `{"labels": ["commitment"]}`,
	}}
	srv := newBatch6Server(t, &memoryFakeStore{listRows: rows})
	got := mustBatch6OK(t, srv, "/api/v1/memories")
	want := `{"code":200,"message":"success","data":[{"id":"m-0","user_id":"n8-user",` +
		`"content":"我讨厌临时会议","category":"values","status":"current","source":"self",` +
		`"conviction":0.0,"created_at":"2026-10-12T03:00:00Z",` +
		`"superseded_at":null,"transition_reason":null,` +
		`"similarity":null,"regressed":false,"labels":["commitment"]}],` +
		`"trace_id":"trace-test","user_id":"n8-user"}`
	if got != want {
		t.Fatalf("行字节 =\n %s\n期望\n %s", got, want)
	}
}

// conviction 是这批第一个**非整型浮点**字段：Go 的 encoding/json 把 0.0 写成 0、
// 把 1.0 写成 1，Python 侧 repr(float) 保留 `.0`，所以这三格必须钉在响应字节上。
func TestMemoriesConvictionKeepsFloatShape(t *testing.T) {
	rows := []domain.MemoryRow{
		{ID: "a", Conviction: 0.0, CreatedAt: time.Unix(0, 0).UTC()},
		{ID: "b", Conviction: 1.0, CreatedAt: time.Unix(0, 0).UTC()},
		{ID: "c", Conviction: 0.5, CreatedAt: time.Unix(0, 0).UTC()},
	}
	srv := newBatch6Server(t, &memoryFakeStore{listRows: rows})
	got := mustBatch6OK(t, srv, "/api/v1/memories")
	for _, want := range []string{`"conviction":0.0`, `"conviction":1.0`, `"conviction":0.5`} {
		if !strings.Contains(got, want) {
			t.Fatalf("缺 %s：%s", want, got)
		}
	}
	if strings.Contains(got, `"conviction":0,`) || strings.Contains(got, `"conviction":1,`) {
		t.Fatalf("浮点被写成整数了：%s", got)
	}
}

// NaN/Inf 在 CPython 侧是 JSONResponse(allow_nan=False) 的编码失败，
// 口径同批 4：映射成 50003，不降级成 404、也不吐 `NaN` 这种非法 JSON。
func TestMemoriesNonFiniteConvictionIs50003(t *testing.T) {
	for _, v := range []float64{math.NaN(), math.Inf(1), math.Inf(-1)} {
		srv := newBatch6Server(t, &memoryFakeStore{
			listRows: []domain.MemoryRow{{ID: "x", Conviction: v}},
		})
		code, body := doBatch6(t, srv, "/api/v1/memories")
		if code != 500 || !strings.Contains(body, `"code":50003`) {
			t.Fatalf("conviction=%v ⇒ status %d body %s；期望 500/50003", v, code, body)
		}
	}
}

func TestMemoriesListEmptyIsJSONArray(t *testing.T) {
	srv := newBatch6Server(t, &memoryFakeStore{})
	got := mustBatch6OK(t, srv, "/api/v1/memories")
	want := `{"code":200,"message":"success","data":[],` +
		`"trace_id":"trace-test","user_id":"n8-user"}`
	if got != want {
		t.Fatalf("空态字节 =\n %s\n期望\n %s", got, want)
	}
}

// transition_reason 是 Text：列上 NULL ⇒ null，空串 ⇒ ""（pydantic 的 Optional[str]
// 不把 "" 当 None）。这一对不能靠 SQL 折掉，所以两侧各钉一格。
func TestMemoryDetailNullVersusEmptyReason(t *testing.T) {
	srv := newBatch6Server(t, &memoryFakeStore{
		detailRow: domain.MemoryRow{ID: "m-1", UserID: "n8-user", Content: "c",
			Category: "goal", Status: "superseded", Source: "others", Conviction: 0.5,
			CreatedAt:       time.Date(2026, 10, 1, 8, 0, 0, 0, time.UTC),
			HasSupersededAt: true,
			SupersededAt:    time.Date(2026, 10, 5, 8, 0, 0, 0, time.UTC)},
		detailFnd: true,
	})
	got := mustBatch6OK(t, srv, "/api/v1/memories/m-1")
	if !strings.Contains(got, `"superseded_at":"2026-10-05T08:00:00Z"`) ||
		!strings.Contains(got, `"transition_reason":null`) {
		t.Fatalf("可空列形状不符：%s", got)
	}

	emptyReason := newBatch6Server(t, &memoryFakeStore{
		detailRow: domain.MemoryRow{ID: "m-2", UserID: "n8-user", Content: "c",
			Category: "goal", Status: "current", Source: "self", Conviction: 0.5,
			CreatedAt:           time.Date(2026, 10, 1, 8, 0, 0, 0, time.UTC),
			HasTransitionReason: true, TransitionReason: ""},
		detailFnd: true,
	})
	got = mustBatch6OK(t, emptyReason, "/api/v1/memories/m-2")
	if !strings.Contains(got, `"transition_reason":""`) {
		t.Fatalf(`空串被折成 null 了：%s`, got)
	}
}

// 详情**不带 status 过滤**（memory_repository.get 的 WHERE 只有 id/user_id/deleted）：
// handler 不许自己再筛一遍 current，否则 superseded 行在详情也读不到，
// 而列表读不到它是今天的行为差异（夹具 detail-superseded 钉的是真库那一格，
// 这里钉的是 handler 不越权替 SQL 做判断）。
func TestMemoryDetailDoesNotFilterStatus(t *testing.T) {
	srv := newBatch6Server(t, &memoryFakeStore{
		detailRow: domain.MemoryRow{ID: "m-s", UserID: "n8-user", Content: "旧信念",
			Category: "values", Status: "superseded", Source: "self",
			Conviction: 0.2, CreatedAt: time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)},
		detailFnd: true,
	})
	got := mustBatch6OK(t, srv, "/api/v1/memories/m-s")
	if !strings.Contains(got, `"status":"superseded"`) {
		t.Fatalf("详情把非 current 行拦下了：%s", got)
	}
}

// 不存在与「这是别人的 id」在 service 层都退化成同一个 None（WHERE 就带 user_id），
// 所以两侧必须是**同一份字节**——回 403 或给两条文案等于让端点变成 id 枚举 oracle。
func TestMemoryDetailAbsentAndForeignAreSameBytes(t *testing.T) {
	store := &memoryFakeStore{} // detailFnd=false ⇒ 两支都走这里
	srv := newBatch6Server(t, store)
	want := `{"code":40403,"message":"记忆不存在 / Memory not found","data":null,` +
		`"trace_id":"trace-test","user_id":"n8-user"}`
	for _, id := range []string{"does-not-exist", "belongs-to-someone-else"} {
		code, body := doBatch6(t, srv, "/api/v1/memories/"+id)
		if code != 404 || body != want {
			t.Errorf("%s ⇒ status %d body %s；期望 404 %s", id, code, body, want)
		}
		if store.seenUser != "n8-user" {
			t.Errorf("%s：user_id 没进查询（seenUser=%q）", id, store.seenUser)
		}
	}
}

// path 段是自由字符串：`GET /memories/search`（POST 才有的那条）在两侧都命中详情，
// 拿 "search" 当主键去查 ⇒ 40403，而不是 405 或页面路由的 404。
func TestMemoryDetailSearchPathCollidesOnID(t *testing.T) {
	store := &memoryFakeStore{}
	srv := newBatch6Server(t, store)
	code, body := doBatch6(t, srv, "/api/v1/memories/search")
	if code != 404 || !strings.Contains(body, `"code":40403`) {
		t.Fatalf("GET /memories/search ⇒ %d %s；期望 404/40403", code, body)
	}
	if store.seenID != "search" {
		t.Fatalf("没把 search 当主键交给 store（seenID=%q）", store.seenID)
	}
}

// limit 的三档界与 `category` 的假值判据：这几格必须在**读库之前**决定，
// 因为 Python 侧也是参数校验先行（一次都不许碰 store）。
func TestMemoriesLimitFailsBeforeStore(t *testing.T) {
	for _, path := range []string{
		"/api/v1/memories?limit=abc", "/api/v1/memories?limit=0",
		"/api/v1/memories?limit=101", "/api/v1/memories?limit=",
	} {
		store := &memoryFakeStore{}
		srv := newBatch6Server(t, store)
		code, body := doBatch6(t, srv, path)
		if code != 400 || !strings.Contains(body, `"code":40001`) {
			t.Fatalf("%s ⇒ %d %s；期望 400/40001", path, code, body)
		}
		if store.calls != 0 {
			t.Fatalf("%s 读了 %d 次 store；校验失败不该碰库（Python 侧同）", path, store.calls)
		}
	}
}

func TestMemoriesLimitAndCategoryPassedThrough(t *testing.T) {
	for _, c := range []struct {
		path  string
		limit int64
		cat   string
	}{
		{"/api/v1/memories", domain.MemoriesDefaultLimit, ""},
		{"/api/v1/memories?limit=7", 7, ""},
		{"/api/v1/memories?limit=100", 100, ""},
		{"/api/v1/memories?limit=1", 1, ""},
		// 空串＝假值：Python 的 `if category:` 不加那条 WHERE，Go 也不许加。
		{"/api/v1/memories?category=", domain.MemoriesDefaultLimit, ""},
		{"/api/v1/memories?category=goal", domain.MemoriesDefaultLimit, "goal"},
		// 重复键取**最后一个**（本日实测：FastAPI 的 scalar query 就是 r.getlist 末位）。
		{"/api/v1/memories?category=a&category=goal", domain.MemoriesDefaultLimit, "goal"},
		{"/api/v1/memories?limit=3&limit=9", 9, ""},
	} {
		store := &memoryFakeStore{}
		srv := newBatch6Server(t, store)
		mustBatch6OK(t, srv, c.path)
		if store.calls != 1 || !store.seenCatSet {
			t.Fatalf("%s：calls=%d seenCatSet=%v，期望 1/true", c.path, store.calls, store.seenCatSet)
		}
		if store.seenLimit != c.limit || store.seenCat != c.cat {
			t.Errorf("%s ⇒ store 看到 limit=%d category=%q；期望 limit=%d category=%q",
				c.path, store.seenLimit, store.seenCat, c.limit, c.cat)
		}
	}
}

func TestMemoriesStoreErrorIsEnvelope50003(t *testing.T) {
	boom := errors.New("库断了")
	cases := []struct {
		path  string
		store *memoryFakeStore
	}{
		{"/api/v1/memories", &memoryFakeStore{listErr: boom}},
		{"/api/v1/memories/m-9", &memoryFakeStore{detailErr: boom}},
	}
	for _, c := range cases {
		srv := newBatch6Server(t, c.store)
		code, body := doBatch6(t, srv, c.path)
		if code != 500 || !strings.Contains(body, `"code":50003`) {
			t.Errorf("%s ⇒ %d %s；期望 500/50003", c.path, code, body)
		}
	}
}

// _parse_labels(memory_service.py:102-112) 的四条规则逐条钉：
// 空/NULL⇒null、坏 JSON⇒null、顶层非 dict⇒null、空数组⇒null，
// 非字符串元素走 Python 的 str()（不是 Go 的字符串化）。
func TestMemoryLabelsParseRules(t *testing.T) {
	for _, c := range []struct{ raw, want string }{
		{"", "null"},
		{"   ", "null"}, // json.loads("   ") 抛 JSONDecodeError ⇒ null，不是空串那一支
		{"not json", "null"},
		{`[1, 2]`, "null"},
		{`"labels"`, "null"},
		{`3`, "null"},
		{`{"labels": []}`, "null"},
		{`{"labels": "commitment"}`, "null"},
		{`{"labels": null}`, "null"},
		{`{"other": 1}`, "null"},
		{`{"labels": ["commitment"]}`, `["commitment"]`},
		{`{"labels": [1, true, false, null]}`, `["1","True","False","None"]`},
		{`{"labels": [1.5, 2.0]}`, `["1.5","2.0"]`},
		{`{"labels": [["a", 1], {"k": "v"}]}`, `["['a', 1]","{'k': 'v'}"]`},
		{`{"labels": ["a", "a"]}`, `["a","a"]`},
	} {
		assertLabels(t, c.raw, c.want)
	}
}

func assertLabels(t *testing.T, raw, want string) {
	t.Helper()
	got, err := memoryLabelsValue(raw).json()
	if err != nil {
		t.Fatalf("labels=%q 编码失败：%v", raw, err)
	}
	if got != want {
		t.Errorf("labels=%q ⇒ %s；期望 %s", raw, got, want)
	}
}
