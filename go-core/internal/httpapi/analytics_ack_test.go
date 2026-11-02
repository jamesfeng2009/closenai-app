// T7-02：带 event_id 的批次走逐事件确认形状。
//
// 期望值来自 contracts/analytics_ack_cases.json——与 backend/tests/test_analytics_service.py、
// frontend/__tests__/analyticsAckContract.test.ts 读的是同一份文件。这里只证 Go 的
// HTTP 层：认领顺序、同批重复、prior 状态、失效 id、计数守恒与键序。
//
// 无 event_id 的旧形状不在这份文件里重测：它由 go-core/testdata/n8_contract.json
// 的字节级回放钉住，改这里不该碰那条路径。
package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/auth"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/config"
)

type ackCaseEvent struct {
	Event      string                     `json:"event"`
	Properties map[string]json.RawMessage `json:"properties"`
	OccurredAt string                     `json:"occurred_at"`
	EventID    json.RawMessage            `json:"event_id"`
}

type ackCase struct {
	Name      string            `json:"name"`
	UserFound bool              `json:"user_found"`
	Consent   bool              `json:"consent"`
	Prior     map[string]string `json:"prior"`
	RelayFail []string          `json:"relay_fail"`
	Events    []ackCaseEvent    `json:"events"`
	Expected  struct {
		Counts map[string]int `json:"counts"`
		// 逐条结果保留原文：event_id 可能是 null，比对在解码后的 any 上做。
		Results []json.RawMessage `json:"results"`
	} `json:"expected"`
}

func loadAckCases(t *testing.T) []ackCase {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "contracts", "analytics_ack_cases.json"))
	if err != nil {
		t.Fatalf("读不到共享用例：%v", err)
	}
	var doc struct {
		Cases []ackCase `json:"cases"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("共享用例不是合法 JSON：%v", err)
	}
	if len(doc.Cases) == 0 {
		t.Fatal("共享用例是空的——参数化测试会静默全绿")
	}
	return doc.Cases
}

// fakeReceipts 是 AnalyticsReceiptStore 的内存实现：与 PG 的 ON CONFLICT 同语义，
// 认领失败时回报已有状态。mutex 不可省——中继是并发派发的，落账在主协程，
// 但 -race 下仍要对同一张 map 串行。
type fakeReceipts struct {
	mu     sync.Mutex
	rows   map[string]string
	claims int
}

func newFakeReceipts(prior map[string]string) *fakeReceipts {
	rows := make(map[string]string, len(prior))
	for id, status := range prior {
		rows[id] = status
	}
	return &fakeReceipts{rows: rows}
}

func (f *fakeReceipts) Claim(_ context.Context, _, eventID string) (bool, string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if status, ok := f.rows[eventID]; ok {
		return false, status, nil
	}
	f.rows[eventID] = "pending"
	f.claims++
	return true, "", nil
}

func (f *fakeReceipts) MarkAccepted(_ context.Context, _, eventID string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.rows[eventID] = "accepted"
	return nil
}

func (f *fakeReceipts) Release(_ context.Context, _, eventID string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	delete(f.rows, eventID)
	return nil
}

func newAckServer(t *testing.T, users UserProfileReader, relay Relay,
	receipts AnalyticsReceiptStore) http.Handler {
	t.Helper()
	cfg := config.Config{AppName: "ClosenAI", Environment: "development", HTTPAddr: ":0"}
	signer := auth.NewSigner("n8-contract-golden-secret-n8-contract-golden",
		30*time.Minute, 30*24*time.Hour)
	return NewServer(cfg, signer, nil, nil,
		&Batch1{Users: users, Relay: relay, Receipts: receipts},
		nil, nil, nil, nil, nil, nil).Handler()
}

// ackBody 造请求体；event_id 为 null/缺省时都不写，与夹具的两种 None 同义。
func ackBody(t *testing.T, events []ackCaseEvent) string {
	t.Helper()
	items := make([]map[string]any, 0, len(events))
	for _, ev := range events {
		item := map[string]any{
			"event":       ev.Event,
			"properties":  ev.Properties,
			"occurred_at": ev.OccurredAt,
		}
		if len(ev.EventID) > 0 && string(ev.EventID) != "null" {
			item["event_id"] = ev.EventID
		}
		items = append(items, item)
	}
	encoded, err := json.Marshal(map[string]any{"events": items})
	if err != nil {
		t.Fatalf("造请求体失败：%v", err)
	}
	return string(encoded)
}

func TestAnalyticsAckSharedContract(t *testing.T) {
	for _, c := range loadAckCases(t) {
		t.Run(c.Name, func(t *testing.T) {
			fail := map[string]bool{}
			for _, name := range c.RelayFail {
				fail[name] = true
			}
			relay := &scriptedRelay{fail: fail}
			receipts := newFakeReceipts(c.Prior)
			handler := newAckServer(t,
				&consentedProfileReader{found: c.UserFound, consent: c.Consent},
				relay, receipts)

			status, body := postBatch(t, handler, "u1", ackBody(t, c.Events))
			if status != 200 {
				t.Fatalf("HTTP %d，want 200（%s）", status, body)
			}

			var envelope struct {
				Data map[string]any `json:"data"`
			}
			if err := json.Unmarshal([]byte(body), &envelope); err != nil {
				t.Fatalf("响应不是信封：%v（%s）", err, body)
			}
			data := envelope.Data
			for key, want := range c.Expected.Counts {
				got, ok := data[key].(float64)
				if !ok {
					t.Fatalf("响应缺 %q 计数：%s", key, body)
				}
				if int(got) != want {
					t.Errorf("%s = %v，want %d（%s）", key, got, want, body)
				}
			}

			wantResults := make([]any, 0, len(c.Expected.Results))
			for _, raw := range c.Expected.Results {
				var item any
				if err := json.Unmarshal(raw, &item); err != nil {
					t.Fatalf("用例里的结果不是合法 JSON：%v", err)
				}
				wantResults = append(wantResults, item)
			}
			gotResults, ok := data["results"].([]any)
			if !ok {
				t.Fatalf("results 不是数组：%s", body)
			}
			if !reflect.DeepEqual(gotResults, wantResults) {
				t.Errorf("逐条结果不符\n go: %#v\n用例: %#v", gotResults, wantResults)
			}

			// 键序是契约的一部分（Python dict 顺序），不能靠 map 比对漏掉。
			assertAckKeyOrder(t, body, len(gotResults))
			total := len(c.Events)
			accepted := int(data["accepted"].(float64))
			duplicate := int(data["duplicate"].(float64))
			dropped := int(data["dropped"].(float64))
			if accepted+duplicate+dropped != total {
				t.Errorf("不守恒：accepted(%d)+duplicate(%d)+dropped(%d) != %d",
					accepted, duplicate, dropped, total)
			}
			if c.Consent && c.UserFound {
				wantAttempts := accepted + countReason(c.Expected.Results, "relay_failed")
				if len(relay.seen) != wantAttempts {
					t.Errorf("外发 %d 次，want %d（accepted + relay_failed）",
						len(relay.seen), wantAttempts)
				}
			} else if len(relay.seen) != 0 {
				t.Errorf("未同意/用户不存在时仍外发了 %d 条", len(relay.seen))
			}
			for _, ev := range relay.seen {
				if len(ev.Properties) != 1 || string(ev.Properties["screen"]) != `"home"` {
					t.Errorf("白名单外属性出网了：%#v", ev.Properties)
				}
			}
			assertReceiptRows(t, receipts, c)
		})
	}
}

func countReason(results []json.RawMessage, reason string) int {
	n := 0
	for _, raw := range results {
		var item struct {
			Status string `json:"status"`
			Reason string `json:"reason"`
		}
		if json.Unmarshal(raw, &item) == nil && item.Status == "retryable" && item.Reason == reason {
			n++
		}
	}
	return n
}

func assertAckKeyOrder(t *testing.T, body string, resultCount int) {
	t.Helper()
	tokens := []string{
		`"data":{"relayed":`,
		`"dropped":`,
		`"accepted":`,
		`"duplicate":`,
		`"retryable":`,
		`"permanent":`,
		`"results":[`,
	}
	last := -1
	for _, token := range tokens {
		idx := strings.Index(body, token)
		if idx < 0 {
			t.Fatalf("响应缺 %s：%s", token, body)
		}
		if idx <= last {
			t.Fatalf("键序不符：%s 出现在错误位置（%s）", token, body)
		}
		last = idx
	}

	// 每条结果也必须是 event_id → status（→ reason）：解析按 key 取值，键序只有
	// 逐字断言才发现——这正是 M12 变异要打红的点。
	resultsStart := strings.Index(body, `"results":[`)
	resultsText := body[resultsStart:]
	if got := strings.Count(resultsText, `{"event_id":`); got != resultCount {
		t.Errorf("有 %d 条结果不是以 event_id 开头（共 %d 条）：%s",
			resultCount-got, resultCount, resultsText)
	}
	if strings.Contains(resultsText, `{"status":`) {
		t.Errorf("results 里出现了以 status 开头的对象（键序被打乱）：%s", resultsText)
	}
}

// assertReceiptRows 钉住台账终态：accepted 留行，relay_failed 删行，prior 原样，
// 永久拒绝（含失效 id）不落行。断言取的是用例里的期望，不拿被测代码自证。
func assertReceiptRows(t *testing.T, f *fakeReceipts, c ackCase) {
	t.Helper()
	want := map[string]string{}
	for id, status := range c.Prior {
		want[id] = status
	}
	for _, raw := range c.Expected.Results {
		var item struct {
			EventID *string `json:"event_id"`
			Status  string  `json:"status"`
		}
		if err := json.Unmarshal(raw, &item); err != nil {
			t.Fatalf("用例结果不可解：%v", err)
		}
		if item.EventID == nil || item.Status != "accepted" {
			continue
		}
		want[*item.EventID] = "accepted"
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if !reflect.DeepEqual(f.rows, want) {
		t.Errorf("台账终态不符\n got: %#v\nwant: %#v", f.rows, want)
	}
}

// TestAnalyticsAckEchoesRawEventID：event_id 是 Any，非字符串的非法值要**原样**
// 回显（123 不是 "123"），前端才认得出是哪一条被永久拒绝。Python 侧 `_ack_item`
// 同样直取原对象。
func TestAnalyticsAckEchoesRawEventID(t *testing.T) {
	handler := newAckServer(t, &consentedProfileReader{found: true, consent: true},
		&scriptedRelay{fail: map[string]bool{}}, newFakeReceipts(nil))
	status, body := postBatch(t, handler, "u1", `{"events":[
		{"event":"a","occurred_at":"2026-10-01T00:00:00+00:00","event_id":123}]}`)
	if status != 200 {
		t.Fatalf("HTTP %d，want 200（%s）", status, body)
	}
	want := `"results":[{"event_id":123,"status":"permanent","reason":"invalid_event_id"}]`
	if !strings.Contains(body, want) {
		t.Errorf("非法 event_id 未原样回显\n got: %s\nwant: %s", body, want)
	}
}

// TestAnalyticsNullEventIDStaysLegacy：只有 null 的 event_id 不触发新形状——
// Python 侧 `is not None` 与 Go 侧 `eventID != nil` 必须同判。这也是「旧客户端
// 字节不变」的边界：Receipts 为 nil 都不该被读到。
func TestAnalyticsNullEventIDStaysLegacy(t *testing.T) {
	handler := newAckServer(t, &consentedProfileReader{found: true, consent: true},
		&scriptedRelay{fail: map[string]bool{}}, nil)
	status, body := postBatch(t, handler, "u1", `{"events":[
		{"event":"a","occurred_at":"2026-10-01T00:00:00+00:00","event_id":null}]}`)
	if status != 200 {
		t.Fatalf("HTTP %d，want 200（%s）", status, body)
	}
	if !strings.Contains(body, `"data":{"relayed":1,"dropped":0}`) {
		t.Errorf("event_id=null 应走旧形状：%s", body)
	}
	if strings.Contains(body, `"accepted"`) {
		t.Errorf("event_id=null 不该出现逐事件计数：%s", body)
	}
}

// TestAnalyticsAckWithoutReceiptStoreFailsLoudly：ack 模式依赖台账，装配漏了就是服务端
// 配置错误——不许静默退回旧形状（那会让前端把「未确认」当成成功而删队列）。
func TestAnalyticsAckWithoutReceiptStoreFailsLoudly(t *testing.T) {
	handler := newAckServer(t, &consentedProfileReader{found: true, consent: true},
		&scriptedRelay{fail: map[string]bool{}}, nil)
	status, body := postBatch(t, handler, "u1", `{"events":[
		{"event":"a","occurred_at":"2026-10-01T00:00:00+00:00","event_id":"e-a"}]}`)
	if status != 500 {
		t.Fatalf("HTTP %d，want 500（%s）", status, body)
	}
	if !strings.Contains(body, `"code":500`) {
		t.Errorf("不是内部错误信封：%s", body)
	}
}
