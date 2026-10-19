// T5-04 CLI 的测：一件**不需要 Go 在跑**的事——命令行面与一次 HTTP 请求的形状。
//
// 为什么这一族测必须存在（而不是「工具嘛，人跑一次就行」）：
//
//  1. 身份走哪儿是这一族唯一的越权路径。`--user` 一旦哪天被拼进 query，
//     服务端的「只认三个查询参数」那条 400 就会把它暴露成故障，
//     而在此之前它会先暴露成「审计端能看到别人的触达」。这条只能钉在客户端：
//     断言 URL 里**没有**那个 id，且头里有。
//  2. 密钥不许出现在 argv。argv 是 `ps`／shell history／CI 日志都会记下来的地方，
//     所以这一格判据是「构造请求时从 env 取」，测的是「传空 env 就一次请求都不发」。
//  3. stdout 那一行必须**只有产物**：提示、错误、日志都不许混进去，
//     否则进程闸拿到的那行不是 JSON，报出来的是「解析失败」而不是「值不对」。
//
// 两侧不许同源：期望值在这里是**字面量**（200、"truncated":true、头名），
// 不是拿 httpapi 的结构体反推——否则服务端改了线上格式，客户端跟着一起改，测照样绿。
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

// fakeTransport 是唯一的「网络」替身：记下收到的请求，回一段预先备好的响应。
type fakeTransport struct {
	got     *http.Request
	calls   int
	status  int
	body    string
	media   string
	sendErr error
}

func (f *fakeTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	f.calls++
	f.got = r
	if f.sendErr != nil {
		return nil, f.sendErr
	}
	media := f.media
	if media == "" {
		media = "application/json"
	}
	return &http.Response{
		StatusCode:    f.status,
		Header:        http.Header{"Content-Type": []string{media}},
		Body:          io.NopCloser(strings.NewReader(f.body)),
		ContentLength: int64(len(f.body)),
		Request:       r,
	}, nil
}

// twoRows 是一页真会回来的形状：两行、没截断、服务端把 limit 收敛到了 50。
const twoRows = `{"code":200,"message":"success","data":{"items":[` +
	`{"id":"p1","status":"scheduled","trigger_type":"memory_recall","scheduled_at":"2026-10-18T09:00:00Z",` +
	`"sent_at":null,"responded_at":null,"expires_at":null,"block_reason":null,"reject_reason":null,` +
	`"journey_event_id":"j1","created_at":"2026-10-18T08:00:00Z","updated_at":"2026-10-18T08:00:00Z"},` +
	`{"id":"p2","status":"blocked","trigger_type":"memory_recall","scheduled_at":null,` +
	`"sent_at":null,"responded_at":null,"expires_at":null,"block_reason":"quiet_hours",` +
	`"reject_reason":null,"journey_event_id":null,"created_at":"2026-10-17T08:00:00Z",` +
	`"updated_at":"2026-10-17T08:00:00Z"}],"truncated":true,"limit":50,"status":"", "stale":false},` +
	`"trace_id":"tr-1","user_id":"u-42"}`

func env(pairs ...string) func(string) string {
	m := map[string]string{}
	for i := 0; i+1 < len(pairs); i += 2 {
		m[pairs[i]] = pairs[i+1]
	}
	return func(k string) string { return m[k] }
}

func TestAuditClientSendsIdentityOnlyInHeaders(t *testing.T) {
	tr := &fakeTransport{status: 200, body: twoRows}
	opts := options{userID: "u-42", stale: true, limit: 50, status: "blocked",
		timeout: 5 * time.Second}
	_, err := run(context.Background(), &http.Client{Transport: tr},
		"http://go.internal:8000", "svc-secret", opts)
	if err != nil {
		t.Fatalf("run：%v", err)
	}
	req := tr.got
	if req.Method != http.MethodGet {
		t.Errorf("method = %s，期望 GET ⇒ 这一面是只读的，工具不许有写路径", req.Method)
	}
	if req.URL.Path != auditPath {
		t.Errorf("path = %q，期望 %q", req.URL.Path, auditPath)
	}
	if got := req.Header.Get("X-Service-Token"); got != "svc-secret" {
		t.Errorf("X-Service-Token = %q ⇒ 密钥没从头进，服务端那一格 403 就是必然", got)
	}
	if got := req.Header.Get("X-User-Id"); got != "u-42" {
		t.Errorf("X-User-Id = %q，期望 u-42 ⇒ ADR-002 唯一的身份入口空了，这一面会退成 401", got)
	}
	// 身份绝不进 URL：这条是本文件头第 1 条判据的全部落点。
	if strings.Contains(req.URL.RawQuery, "u-42") || strings.Contains(req.URL.String(), "u-42") {
		t.Errorf("URL 里出现了 user id：%s ⇒ 服务端会 400，而更早的坏处是审计日志里到处是身份",
			req.URL.String())
	}
	// 其余三格都要在（缺一格就是「查的不是我以为的那一档」）。
	for _, want := range []string{"status=blocked", "limit=50", "stale=true"} {
		if !strings.Contains(req.URL.RawQuery, want) {
			t.Errorf("query = %q，缺 %s", req.URL.RawQuery, want)
		}
	}
}

func TestAuditReportEchoesAppliedFiltersAndCounts(t *testing.T) {
	tr := &fakeTransport{status: 200, body: twoRows}
	var stdout, stderr bytes.Buffer
	client := &http.Client{Transport: tr}
	err := execute([]string{"--user", "u-42", "--limit", "500"},
		env("GO_API_URL", "http://go.internal:8000/", "GO_SERVICE_TOKEN", "svc-secret"),
		client, &stdout, &stderr)
	if err != nil {
		t.Fatalf("execute：%v（stderr=%s）", err, stderr.String())
	}
	line := strings.TrimSuffix(stdout.String(), "\n")
	if strings.Contains(line, "\n") || stdout.String() != line+"\n" {
		t.Fatalf("stdout 不是**一行**：%q ⇒ 进程闸会先撞上「解析失败」而不是「值不对」",
			stdout.String())
	}
	var rep report
	if err := json.Unmarshal([]byte(line), &rep); err != nil {
		t.Fatalf("stdout 那行不是 JSON：%v", err)
	}
	if rep.Total != 2 || len(rep.Items) != 2 {
		t.Errorf("total = %d（items %d），期望 2 ⇒ 少一行就是审计少报，而这是这一面唯一的产出判据",
			rep.Total, len(rep.Items))
	}
	if !rep.Truncated {
		t.Error("truncated = false，期望 true ⇒ 夹具里服务端明明说「还有」，产物却报成完整列表")
	}
	if rep.UserID != "u-42" || rep.Applied.Limit != 50 {
		t.Errorf("回显 = %+v，期望 user u-42／limit 50 ⇒ 请求里传了 500，"+
			"被服务端收敛到 50 这件事必须出现在产物里，否则读的人会以为那是全部", rep)
	}
	// 正文外流这一格在 CLI 侧只有一句可判：工具不打印它读不到的东西，也不该发明字段。
	if stderr.Len() != 0 {
		t.Errorf("正常一轮却往 stderr 写了东西：%q", stderr.String())
	}
}

func TestAuditRefusesToRunWithoutEnvAndMakesNoRequest(t *testing.T) {
	for _, c := range []struct {
		why  string
		vars []string
		want string
	}{
		{"没有 URL", []string{"GO_SERVICE_TOKEN", "s"}, "GO_API_URL"},
		{"没有密钥", []string{"GO_API_URL", "http://go.internal:8000"}, "GO_SERVICE_TOKEN"},
	} {
		tr := &fakeTransport{status: 200, body: twoRows}
		var stdout, stderr bytes.Buffer
		err := execute([]string{"--user", "u-42"}, env(c.vars...),
			&http.Client{Transport: tr}, &stdout, &stderr)
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s：err = %v，期望点名 %s ⇒ 缺配置时报的是「跑不动」而不是「没跑」",
				c.why, err, c.want)
		}
		if tr.calls != 0 {
			t.Errorf("%s：缺配置还发了 %d 次请求", c.why, tr.calls)
		}
		if stdout.Len() != 0 {
			t.Errorf("%s：失败的一轮 stdout 不空：%q ⇒ 产物里出现了「什么都没查到」那种假绿",
				c.why, stdout.String())
		}
	}
}

func TestAuditFailsLoudlyOnNonOKResponse(t *testing.T) {
	cases := []struct {
		why    string
		status int
		body   string
		media  string
		want   string
	}{
		{"密钥不合 ⇒ 403", 403,
			`{"code":403,"message":"service token mismatch","data":null,"trace_id":"t9","user_id":"u-42"}`,
			"", "403"},
		// 这一面「没注册」与「Go 从来没做过这个域」同形，都回 404 信封；
		// 把码抄出来才能让人去查装配而不是去查网络。
		{"整面没注册 ⇒ 404", 404,
			`{"code":404,"message":"not found","data":null,"trace_id":"t8","user_id":"u-42"}`,
			"", "404"},
		{"打错地址（HTML）", 404, "<html><body>no route</body></html>", "text/html", "不是信封"},
		{"连不上", 0, "", "", "查询"},
		// HTTP 状态与信封码是两个数：网关把上游的失败原样搬过来时状态是 200、code 是 500。
		// 只看状态就会把「一次失败的查询」念成「查到了零条」，而这两个词在审计面上相反。
		{"200 但信封说失败 ⇒ 码那一格兜住网关", 200,
			`{"code":500,"message":"canonical unavailable","data":null,"trace_id":"t7","user_id":"u-42"}`,
			"", "code 500"},
	}
	for _, c := range cases {
		tr := &fakeTransport{status: c.status, body: c.body, media: c.media,
			sendErr: func() error {
				if c.why == "连不上" {
					return errors.New("dial tcp: connect refused")
				}
				return nil
			}()}
		var stdout, stderr bytes.Buffer
		err := execute([]string{"--user", "u-42"},
			env("GO_API_URL", "http://go.internal:8000", "GO_SERVICE_TOKEN", "s"),
			&http.Client{Transport: tr}, &stdout, &stderr)
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s：err = %v，期望含 %q ⇒ 失败恢复那一格丢了处置线索", c.why, err, c.want)
		}
		if stdout.Len() != 0 {
			t.Errorf("%s：失败还写了产物：%q", c.why, stdout.String())
		}
	}
}

func TestAuditRejectsBadFlagsBeforeAnyRequest(t *testing.T) {
	cases := []struct {
		why  string
		argv []string
		want string
	}{
		{"没有 --user", []string{}, "--user"},
		{"--user 是空白", []string{"--user", "   "}, "--user"},
		{"负 limit", []string{"--user", "u-42", "--limit", "-3"}, "--limit"},
		{"多余位置参数", []string{"--user", "u-42", "extra"}, "多余"},
		{"不认识的 flag", []string{"--user", "u-42", "--all"}, "flag"},
	}
	for _, c := range cases {
		tr := &fakeTransport{status: 200, body: twoRows}
		var stdout, stderr bytes.Buffer
		err := execute(c.argv,
			env("GO_API_URL", "http://go.internal:8000", "GO_SERVICE_TOKEN", "s"),
			&http.Client{Transport: tr}, &stdout, &stderr)
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s：err = %v，期望含 %q", c.why, err, c.want)
		}
		if tr.calls != 0 {
			t.Errorf("%s：参数不合法却发了请求（%d 次）⇒ 会带上一次密钥与一个不明身份",
				c.why, tr.calls)
		}
	}
}

// ── 词表提示：这一格是「安静地回零行」唯一的解毒剂，而它只许走 stderr ──────────

func TestAuditHintsOnUnknownStatusWithoutChangingTheVerdict(t *testing.T) {
	empty := `{"code":200,"message":"success","data":{"items":[],"truncated":false,` +
		`"limit":50,"status":"pending","stale":false},"trace_id":"t7","user_id":"u-42"}`
	cases := []struct {
		why      string
		status   string
		wantHint bool
	}{
		{"pending 不在八态里（工单 §怎么做.1 用的就是这个词）", "pending", true},
		{"sent 是合法态 ⇒ 不许有多余提示", "sent", false},
	}
	for _, c := range cases {
		tr := &fakeTransport{status: 200, body: empty}
		var stdout, stderr bytes.Buffer
		err := execute([]string{"--user", "u-42", "--status", c.status},
			env("GO_API_URL", "http://go.internal:8000", "GO_SERVICE_TOKEN", "s"),
			&http.Client{Transport: tr}, &stdout, &stderr)
		if err != nil {
			t.Fatalf("%s：execute：%v", c.why, err)
		}
		hasHint := strings.Contains(stderr.String(), "不在八态词表里")
		if hasHint != c.wantHint {
			t.Errorf("%s：stderr 提示 = %v，期望 %v；stderr=%q", c.why, hasHint, c.wantHint,
				stderr.String())
		}
		// 提示不许改判：请求照发、产物照出（零行是真·零行时才该是零行）。
		if tr.calls != 1 || stdout.Len() == 0 {
			t.Errorf("%s：calls=%d／stdout %d 字节 ⇒ 提示变成了拦截", c.why, tr.calls, stdout.Len())
		}
		if strings.Contains(stdout.String(), "词表") {
			t.Errorf("%s：提示混进了 stdout ⇒ 那一行必须是纯产物", c.why)
		}
	}
}
