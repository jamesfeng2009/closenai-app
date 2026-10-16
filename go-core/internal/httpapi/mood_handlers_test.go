// 批 5 三条 handler 的分支单测（假件面）。
//
// 逐字契约属 ② 的回放闸，这里补的是夹具**给不出**的三种情形：
//   - store 报错⇒信封 50003（夹具只能录到成功路径，Python 侧不会主动坏库）；
//   - 校验失败时**一次库都不许读**（假件把调用计数记下，红线在计数上）；
//   - 空态的 `[]` 与非空态的键序（夹具靠真库凑行数，这里一行就能钉住字节）。
package httpapi

import (
	"context"
	"errors"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

type moodFakeStore struct {
	historyRows []domain.MoodCheckinRow
	trendRows   []domain.Checkin
	timezone    string
	tzFound     bool
	tzSince     time.Time // 记下最后一次窗口下界，冻钟是否生效看这里
	historyErr  error
	trendErr    error
	limitSeen   int64
	calls       int
}

func (f *moodFakeStore) MoodHistory(_ context.Context, _ string,
	limit int64) ([]domain.MoodCheckinRow, error) {
	f.calls++
	f.limitSeen = limit
	return f.historyRows, f.historyErr
}

func (f *moodFakeStore) MoodTrendRows(_ context.Context, _ string,
	since time.Time) ([]domain.Checkin, error) {
	f.calls++
	f.tzSince = since
	return f.trendRows, f.trendErr
}

func (f *moodFakeStore) MoodUserTimezone(context.Context,
	string) (string, bool, error) {
	f.calls++
	return f.timezone, f.tzFound, nil
}

type reflectionFakeStore struct {
	rows  []domain.ReflectionRow
	err   error
	calls int
	seen  []string
	limit int64
}

func (f *reflectionFakeStore) Reflections(_ context.Context, _, recordType string,
	limit int64) ([]domain.ReflectionRow, error) {
	f.calls++
	f.seen = append(f.seen, recordType)
	f.limit = limit
	return f.rows, f.err
}

func newBatch5Server(t *testing.T, moods *moodFakeStore, refl *reflectionFakeStore,
	language string, now time.Time) *Server {
	t.Helper()
	srv := newN8Server(t, &fakeProfileReader{language: language}, n8ForbiddenStore{t: t},
		&n8NoRelayStore{t: t}, nil, nil, nil, &Batch5{
			Moods: moods, Reflections: refl, Users: &fakeProfileReader{language: language},
		}, nil)
	if !now.IsZero() {
		srv.now = func() time.Time { return now }
	}
	return srv
}

// fakeProfileReader 只回答批 5 需要的两个字段，其余读一律不该发生。
type fakeProfileReader struct {
	language string
	calls    int
	err      error
}

func (f *fakeProfileReader) UserProfile(context.Context,
	string) (domain.UserProfile, error) {
	f.calls++
	if f.err != nil {
		return domain.UserProfile{}, f.err
	}
	return domain.UserProfile{Language: f.language, Found: true}, nil
}

func doN8(t *testing.T, srv *Server, path string, headers map[string]string) string {
	t.Helper()
	r := httptest.NewRequest("GET", path, nil)
	// trace_id 钉成确定值：不带头时中间件会现生成 uuid，逐字比就成了比随机数。
	r.Header.Set("X-Trace-Id", "trace-test")
	for k, v := range headers {
		r.Header.Set(k, v)
	}
	rec := httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, r)
	if rec.Code != 200 {
		t.Fatalf("%s status = %d，body = %s", path, rec.Code, rec.Body.String())
	}
	return rec.Body.String()
}

func TestMoodHistoryEmptyIsJSONArray(t *testing.T) {
	srv := newBatch5Server(t, &moodFakeStore{}, &reflectionFakeStore{}, "en", time.Time{})
	want := `{"code":200,"message":"success","data":{"checkins":[]},` +
		`"trace_id":"trace-test","user_id":"n8-user"}`
	got := doN8(t, srv, "/api/v1/mood/history", map[string]string{"X-User-Id": "n8-user"})
	if got != want {
		t.Fatalf("空态字节 =\n %s\n期望\n %s", got, want)
	}
}

func TestMoodHistoryRowBytes(t *testing.T) {
	// 八键的**书写序**就是 MoodCheckinResponse 的声明序；末两键恒 null。
	rows := []domain.MoodCheckinRow{{
		ID: "m0", Emoji: "😰", Label: "焦虑",
		Interpretation: "含\"引号\"与\\反斜杠\n换行",
		CreatedAt:      time.Date(2026, 10, 12, 3, 0, 0, 0, time.UTC),
	}}
	srv := newBatch5Server(t, &moodFakeStore{historyRows: rows}, &reflectionFakeStore{},
		"en", time.Time{})
	got := doN8(t, srv, "/api/v1/mood/history", map[string]string{"X-User-Id": "n8-user"})
	want := `{"code":200,"message":"success","data":{"checkins":[{"id":"m0",` +
		`"mood_emoji":"😰","mood_label":"焦虑",` +
		`"ai_interpretation":"含\"引号\"与\\反斜杠\n换行",` +
		`"streak_updated":0,"created_at":"2026-10-12T03:00:00Z",` +
		`"deep_interpretation":null,"level_up":null}]},` +
		`"trace_id":"trace-test","user_id":"n8-user"}`
	if got != want {
		t.Fatalf("行字节 =\n %s\n期望\n %s", got, want)
	}
}

func TestMoodHistoryLimitIsThirty(t *testing.T) {
	fake := &moodFakeStore{}
	srv := newBatch5Server(t, fake, &reflectionFakeStore{}, "en", time.Time{})
	doN8(t, srv, "/api/v1/mood/history", map[string]string{"X-User-Id": "n8-user"})
	if fake.limitSeen != domain.MoodHistoryLimit || fake.calls != 1 {
		t.Fatalf("limit = %d、calls = %d；期望 30、1（history 不查 users）",
			fake.limitSeen, fake.calls)
	}
}

func TestMoodTrendUsesFrozenClockForWindow(t *testing.T) {
	anchor := time.Date(2026, 10, 15, 2, 44, 56, 0, time.UTC)
	fake := &moodFakeStore{
		trendRows: []domain.Checkin{
			{Emoji: "😌", CreatedAt: anchor.Add(-48 * time.Hour)},
			{Emoji: "😢", CreatedAt: anchor.Add(-47 * time.Hour)},
			{Emoji: "😊", CreatedAt: anchor.Add(-24 * time.Hour)},
		},
		timezone: "UTC", tzFound: true,
	}
	srv := newBatch5Server(t, fake, &reflectionFakeStore{}, "en", anchor)
	got := doN8(t, srv, "/api/v1/mood/trend", map[string]string{"X-User-Id": "n8-user"})
	// 窗口下界 = anchor − 14*86400s（timedelta 是精确秒数，不是日历加法）。
	if !fake.tzSince.Equal(anchor.Add(-14 * 24 * time.Hour)) {
		t.Fatalf("since = %s；期望 %s", fake.tzSince, anchor.Add(-14*24*time.Hour))
	}
	// 同一本地日两行取升序首行：10-13 的 😌⇒4 与 😢⇒1 只留 4，10-14 的 😊⇒5。
	want := `{"code":200,"message":"success","data":{"labels":["10-13","10-14"],` +
		`"values":[4,5],"summary":"Your overall mood has been great — keep the rhythm going."},` +
		`"trace_id":"trace-test","user_id":"n8-user"}`
	if got != want {
		t.Fatalf("trend 字节 =\n %s\n期望\n %s", got, want)
	}
}

func TestMoodTrendLanguageComesFromDBWhenNoHeader(t *testing.T) {
	anchor := time.Date(2026, 10, 15, 2, 44, 56, 0, time.UTC)
	fake := &moodFakeStore{
		trendRows: []domain.Checkin{{Emoji: "😌", CreatedAt: anchor}},
		timezone:  "UTC", tzFound: true,
	}
	srv := newBatch5Server(t, fake, &reflectionFakeStore{}, "zh", anchor)
	got := doN8(t, srv, "/api/v1/mood/trend", map[string]string{"X-User-Id": "n8-user"})
	if !strings.Contains(got, `"summary":"整体情绪状态不错，保持这个节奏。"`) {
		t.Fatalf("users.language=zh 没生效：%s", got)
	}
}

func TestMoodTrendDaysBoundsAndParseNeverReadStore(t *testing.T) {
	for _, path := range []string{
		"/api/v1/mood/trend?days=abc", "/api/v1/mood/trend?days=0",
		"/api/v1/mood/trend?days=91", "/api/v1/mood/trend?days=",
	} {
		fake := &moodFakeStore{}
		// X-Language 在场⇒get_request_language 短路，整条请求零库读（与录制侧同口径）。
		srv := newBatch5Server(t, fake, &reflectionFakeStore{}, "en", time.Now())
		r := httptest.NewRequest("GET", path, nil)
		r.Header.Set("X-User-Id", "n8-user")
		r.Header.Set("X-Language", "en")
		rec := httptest.NewRecorder()
		srv.Handler().ServeHTTP(rec, r)
		if rec.Code != 400 {
			t.Fatalf("%s status = %d，期望 400：%s", path, rec.Code, rec.Body.String())
		}
		if fake.calls != 0 {
			t.Fatalf("%s 读了 %d 次 store；校验失败不该碰库（Python 侧同）", path, fake.calls)
		}
	}
}

func TestReflectionsNullVersusArray(t *testing.T) {
	rows := []domain.ReflectionRow{
		{ID: "g0", Content: "无关联", MemoryIDs: "", CreatedAt: time.Date(2026, 10, 14, 9, 0, 0, 0, time.UTC)},
		{ID: "g1", Content: "两个空", MemoryIDs: ",", CreatedAt: time.Date(2026, 10, 13, 9, 0, 0, 0, time.UTC)},
	}
	refl := &reflectionFakeStore{rows: rows}
	srv := newBatch5Server(t, &moodFakeStore{}, refl, "en", time.Time{})
	got := doN8(t, srv, "/api/v1/reflections", map[string]string{"X-User-Id": "n8-user"})
	want := `{"code":200,"message":"success","data":[` +
		`{"id":"g0","content":"无关联","related_memory_ids":null,` +
		`"created_at":"2026-10-14T09:00:00Z"},` +
		`{"id":"g1","content":"两个空","related_memory_ids":["",""],` +
		`"created_at":"2026-10-13T09:00:00Z"}],` +
		`"trace_id":"trace-test","user_id":"n8-user"}`
	if got != want {
		t.Fatalf("reflections 字节 =\n %s\n期望\n %s", got, want)
	}
	// record_type 必须是精确的 "reflection"，limit 缺席⇒20。
	if len(refl.seen) != 1 || refl.seen[0] != domain.ReflectionRecordType ||
		refl.limit != domain.ReflectionsDefaultLimit {
		t.Fatalf("record_type=%v limit=%d；期望 [reflection] 20", refl.seen, refl.limit)
	}
}

func TestReflectionsEmptyIsJSONArray(t *testing.T) {
	srv := newBatch5Server(t, &moodFakeStore{}, &reflectionFakeStore{}, "en", time.Time{})
	got := doN8(t, srv, "/api/v1/reflections", map[string]string{"X-User-Id": "n8-user"})
	want := `{"code":200,"message":"success","data":[],` +
		`"trace_id":"trace-test","user_id":"n8-user"}`
	if got != want {
		t.Fatalf("空态字节 =\n %s\n期望\n %s", got, want)
	}
}

func TestBatch5StoreErrorIsEnvelope50003(t *testing.T) {
	boom := errors.New("库断了")
	cases := []struct {
		path  string
		moods *moodFakeStore
		refl  *reflectionFakeStore
	}{
		{"/api/v1/mood/history", &moodFakeStore{historyErr: boom}, nil},
		{"/api/v1/mood/trend", &moodFakeStore{trendErr: boom}, nil},
		{"/api/v1/reflections", &moodFakeStore{}, &reflectionFakeStore{err: boom}},
	}
	for _, c := range cases {
		srv := newBatch5Server(t, c.moods, c.refl, "en", time.Now())
		r := httptest.NewRequest("GET", c.path, nil)
		r.Header.Set("X-User-Id", "n8-user")
		rec := httptest.NewRecorder()
		srv.Handler().ServeHTTP(rec, r)
		if rec.Code != 500 {
			t.Fatalf("%s status = %d，期望 500", c.path, rec.Code)
		}
		if !strings.Contains(rec.Body.String(), `"code":50003`) {
			t.Fatalf("%s body = %s；期望 50003", c.path, rec.Body.String())
		}
	}
}
