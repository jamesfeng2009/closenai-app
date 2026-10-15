// query int 校验的取值表（批 5 D-3）。
//
// 每一格的**期望值都抄自夹具**（scripts/gen_n8_contract_goldens.py 录的真 FastAPI 字节），
// 不是按 pydantic 文档推的——推的部分已在录制时被打回过一次：
// 重复键「取最后一个」、`+14` 合法、全角 `１４` 非法，三条都不是能凭印象写对的东西。
//
// 与回放闸的分工：② 的 TestN8*ContractBytes 比的是整条响应，这里比的是
// 「message/data 两个渲染面」在**没被 HTTP 用例命中的取值**上是否还成立
// （`99999999999999999999` 这类 i64 溢出、`90` 恰好合规、`days=1` 的下界）。
package httpapi

import (
	"encoding/json"
	"net/http/httptest"
	"testing"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

func queryWith(t *testing.T, raw string) (int64, *ValidationError) {
	t.Helper()
	r := httptest.NewRequest("GET", "/api/v1/mood/trend"+raw, nil)
	return queryInt(r, "days", domain.MoodTrendDaysSpec)
}

func TestQueryIntAccepts(t *testing.T) {
	cases := []struct {
		query string
		want  int64
	}{
		{"", domain.MoodTrendDefaultDays}, // 参数缺席⇒Query 的 default
		{"?days=14", 14},
		{"?days=%2B14", 14}, // 夹具 trend-days-plus-14：+ 号合法
		{"?days=1", 1},      // 下界恰好合规（ge 是闭区间）
		{"?days=90", 90},    // 上界恰好合规
		{"?days=007", 7},    // 前导零
		{"?days=1&days=14", 14},
		{"?days=&days=3", 3},                      // 夹具 trend-days-empty-then-valid
		{"?limit=9", domain.MoodTrendDefaultDays}, // 无关的键≠days，缺席仍取默认值
	}
	for _, c := range cases {
		got, err := queryWith(t, c.query)
		if err != nil || got != c.want {
			t.Fatalf("%q = %d, %v；期望 %d, nil", c.query, got, err, c.want)
		}
	}
}

func TestQueryIntRejectsMinusZero(t *testing.T) {
	// int("-0") == 0 ⇒ 落 greater_than_equal，且 input 回显**原始串** "-0"。
	_, err := queryWith(t, "?days=-0")
	if err == nil || err.Entries[0].Type != "greater_than_equal" {
		t.Fatalf("-0 = %v；期望 greater_than_equal", err)
	}
	if got, ok := err.Entries[0].Input.(string); !ok || got != "-0" {
		t.Fatalf("input = %#v；期望原始串 \"-0\"", err.Entries[0].Input)
	}
}

func TestQueryIntErrorShape(t *testing.T) {
	// want 的四列都是夹具里出现过的字节。
	cases := []struct {
		query    string
		wantType string
		wantMsg  string
		wantIn   string
		wantBody string // data 序列化的整格字节
	}{
		{
			query: "?days=abc", wantType: "int_parsing",
			wantMsg: "Input should be a valid integer, unable to parse string as an integer",
			wantIn:  "abc",
			wantBody: `{"type":"int_parsing","loc":["query","days"],` +
				`"msg":"Input should be a valid integer, unable to parse string as an integer",` +
				`"input":"abc"}`,
		},
		{
			query: "?days=1.5", wantType: "int_parsing", wantIn: "1.5",
			wantMsg: "Input should be a valid integer, unable to parse string as an integer",
		},
		{query: "?days=", wantType: "int_parsing", wantIn: "",
			wantMsg: "Input should be a valid integer, unable to parse string as an integer"},
		{query: "?days=%20", wantType: "int_parsing", wantIn: " ",
			wantMsg: "Input should be a valid integer, unable to parse string as an integer"},
		// 全角数字：Python 的 int() 会吃，pydantic-core 的 Rust 解析与 Go 的 ParseInt 都不吃。
		{query: "?days=１４", wantType: "int_parsing", wantIn: "１４",
			wantMsg: "Input should be a valid integer, unable to parse string as an integer"},
		{query: "?days=±", wantType: "int_parsing", wantIn: "±",
			wantMsg: "Input should be a valid integer, unable to parse string as an integer"},
		{
			query: "?days=0", wantType: "greater_than_equal", wantIn: "0",
			wantMsg: "Input should be greater than or equal to 1",
			wantBody: `{"type":"greater_than_equal","loc":["query","days"],` +
				`"msg":"Input should be greater than or equal to 1","input":"0","ctx":{"ge":1}}`,
		},
		{
			query: "?days=91", wantType: "less_than_equal", wantIn: "91",
			wantMsg: "Input should be less than or equal to 90",
			wantBody: `{"type":"less_than_equal","loc":["query","days"],` +
				`"msg":"Input should be less than or equal to 90","input":"91","ctx":{"le":90}}`,
		},
		// i64 之外：Python 的 int 无界，所以它只可能落 le；Go 必须在溢出时给同一档。
		{query: "?days=99999999999999999999", wantType: "less_than_equal",
			wantIn:  "99999999999999999999",
			wantMsg: "Input should be less than or equal to 90"},
		{query: "?days=-99999999999999999999", wantType: "greater_than_equal",
			wantIn:  "-99999999999999999999",
			wantMsg: "Input should be greater than or equal to 1"},
	}
	for _, c := range cases {
		_, err := queryWith(t, c.query)
		if err == nil {
			t.Fatalf("%q 竟然通过了校验", c.query)
		}
		e := err.Entries[0]
		if len(err.Entries) != 1 {
			t.Fatalf("%q 给了 %d 条错误，期望 1 条", c.query, len(err.Entries))
		}
		if e.Type != c.wantType {
			t.Fatalf("%q type = %s，期望 %s", c.query, e.Type, c.wantType)
		}
		if e.Msg != c.wantMsg {
			t.Fatalf("%q msg = %q，期望 %q", c.query, e.Msg, c.wantMsg)
		}
		if got, ok := e.Input.(string); !ok || got != c.wantIn {
			t.Fatalf("%q input = %#v，期望 %q", c.query, e.Input, c.wantIn)
		}
		if want := []any{"query", "days"}; len(e.Loc) != 2 || e.Loc[0] != want[0] || e.Loc[1] != want[1] {
			t.Fatalf("%q loc = %v，期望 [query, days]", c.query, e.Loc)
		}
		if c.wantBody != "" {
			raw, marshalErr := json.Marshal(e)
			if marshalErr != nil {
				t.Fatalf("%q marshal: %v", c.query, marshalErr)
			}
			if string(raw) != c.wantBody {
				t.Fatalf("%q 条目字节 =\n %s\n期望\n %s", c.query, raw, c.wantBody)
			}
		}
	}
}

func TestQueryIntMessageIsTheFastAPIRender(t *testing.T) {
	// 中间件把首条错误拼成 message：`参数错误: {loc} {msg} / Invalid parameters`。
	// loc 不含 "body" 段，所以 query 面拼出的是 `query.days`。
	_, err := queryWith(t, "?days=abc")
	want := "参数错误: query.days Input should be a valid integer, " +
		"unable to parse string as an integer / Invalid parameters"
	if got := err.Message(); got != want {
		t.Fatalf("message =\n %q\n期望\n %q", got, want)
	}
	asErr := err.AsError()
	if asErr.Code != 40001 || asErr.Status != 400 {
		t.Fatalf("code/status = %d/%d，期望 40001/400", asErr.Code, asErr.Status)
	}
}

func TestQueryIntUsesLastValueForAnyKey(t *testing.T) {
	// Starlette 给 MultiDict、pydantic 取末位；Go 的 Query().Get() 取首位，
	// 所以这里必须自己取末位——把 Get() 用回去就会让
	// `?days=14&days=abc` 两侧一绿一红（Python 400、Go 200）。
	r := httptest.NewRequest("GET", "/api/v1/mood/trend?days=14&days=abc", nil)
	if _, err := queryInt(r, "days", domain.MoodTrendDaysSpec); err == nil {
		t.Fatal("重复键取了第一个值（14）并通过；必须按末位 abc 报 400")
	}
	r = httptest.NewRequest("GET", "/api/v1/mood/trend?days=abc&days=14", nil)
	if got, err := queryInt(r, "days", domain.MoodTrendDaysSpec); err != nil || got != 14 {
		t.Fatalf("= %d, %v；期望 14, nil", got, err)
	}
}

func TestQueryIntReflectionsSpecBounds(t *testing.T) {
	// reflections 用的是另一对界（1..100），夹具里 101 报 less_than_equal。
	r := httptest.NewRequest("GET", "/api/v1/reflections?limit=101", nil)
	_, err := queryInt(r, "limit", domain.ReflectionsLimitSpec)
	if err == nil || err.Entries[0].Type != "less_than_equal" {
		t.Fatalf("limit=101 = %v；期望 less_than_equal", err)
	}
	if err.Entries[0].Msg != "Input should be less than or equal to 100" {
		t.Fatalf("msg = %q；期望按 spec 的 100 而不是 days 的 90", err.Entries[0].Msg)
	}
	if want := []any{"query", "limit"}; err.Entries[0].Loc[1] != want[1] {
		t.Fatalf("loc = %v；期望 [query, limit]", err.Entries[0].Loc)
	}
	if got, ok := err.Entries[0].Ctx["le"].(int64); !ok || got != 100 {
		t.Fatalf("ctx.le = %#v；期望 int64(100)", err.Entries[0].Ctx["le"])
	}
	// 缺席⇒20
	r = httptest.NewRequest("GET", "/api/v1/reflections", nil)
	if got, err := queryInt(r, "limit", domain.ReflectionsLimitSpec); err != nil ||
		got != domain.ReflectionsDefaultLimit {
		t.Fatalf("= %d, %v；期望 20, nil", got, err)
	}
}
