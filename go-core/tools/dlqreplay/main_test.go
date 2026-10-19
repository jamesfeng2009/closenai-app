// 命令行面的闸（T2-05 §怎么做.4）。
//
// 核心判据住在 internal/messaging/dlq（那里用 fake broker 判「peek 必放回」「purge 必确认」），
// 本文件只判**工具这一层**会坏的那三种形态：
//  1. 参数翻译错：`-replay` 被读成 list 档 ⇒ 一次「我以为在重放」的巡检；
//  2. 三档不互斥 ⇒ `-purge -replay` 同时给时先清后放，谁都不知道自己执行了哪一档；
//  3. 非 list 档没给 `-queue` ⇒ 一次对「整个拓扑」动手的 purge/replay。
//
// 另外钉一条形状判据：`run` 的产物必须是**能解析的 JSON**，且 stdout 上没有第二行
// （文件头第 1 条：这个工具的产物要能被进程闸解析）。
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	amqp091 "github.com/rabbitmq/amqp091-go"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/amqp"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/dlq"
)

const q = "dlq.ai.observation.extract"

// fake 与 internal/messaging/dlq 的测试 fake 各自独立：跨包借 fake 会让两个文件耦合，
// 而这两层要判的东西本来就不是一回事（一层判 flag，一层判 broker 语义）。
type fakeBroker struct {
	calls   []string
	depth   int
	msgs    []dlq.Message
	purged  int
	pubErr  error
	letters []string
}

func (b *fakeBroker) Inspect(context.Context, string) (int, int, error) {
	b.calls = append(b.calls, "inspect")
	return b.depth, 0, nil
}

func (b *fakeBroker) Get(context.Context, string) (dlq.Message, bool, error) {
	b.calls = append(b.calls, "get")
	if len(b.msgs) == 0 {
		return dlq.Message{}, false, nil
	}
	m := b.msgs[0]
	b.msgs = b.msgs[1:]
	b.depth = len(b.msgs)
	m.Settle = func(ack, requeue bool) error {
		if ack {
			b.calls = append(b.calls, "ack")
			return nil
		}
		b.calls = append(b.calls, "requeue")
		if requeue {
			b.msgs = append([]dlq.Message{m}, b.msgs...)
			b.depth = len(b.msgs)
		}
		return nil
	}
	return m, true, nil
}

func (b *fakeBroker) Publish(_ context.Context, exchange, key string, m dlq.Outbound) error {
	b.calls = append(b.calls, "publish")
	b.letters = append(b.letters, exchange+"/"+key+" "+string(m.Body))
	return b.pubErr
}

func (b *fakeBroker) Purge(context.Context, string) (int, error) {
	b.calls = append(b.calls, "purge")
	n := b.depth
	b.depth, b.msgs = 0, nil
	b.purged = n
	return n, nil
}

func oneMessage(id, body string) dlq.Message {
	return dlq.Message{
		Body: []byte(body), ContentType: "application/json", MessageID: id,
		Timestamp: time.Now().UTC().Add(-time.Minute),
		Headers: map[string]any{dlq.HeaderXDeath: []amqp091.Table{{
			dlq.HeaderXQueue:      "ai.observation.extract",
			dlq.HeaderXCount:      int64(3),
			dlq.HeaderXReason:     "rejected",
			dlq.HeaderXTime:       time.Now().UTC(),
			dlq.HeaderXRoutingKey: []any{"ai.observation.requested"},
		}}},
		DeliveryRoutingKey: q, DeliveryExchange: amqp.ExchangeDLQ,
	}
}

// ── 参数面 ───────────────────────────────────────────────

func TestDefaultModeIsList(t *testing.T) {
	o, err := parse(nil)
	if err != nil {
		t.Fatalf("空参数就该跑默认档（只读巡检），err=%v", err)
	}
	if o.mode != modeList || len(o.queues) != 0 || o.limit != 100 {
		t.Fatalf("默认档形状不对：%+v", o)
	}
}

func TestThreeModesAreMutuallyExclusive(t *testing.T) {
	for _, argv := range [][]string{
		{"-purge", "-replay", "-queue", q, "-confirm", q},
		{"-list", "-purge", "-queue", q, "-confirm", q},
		{"-list", "-replay", "-queue", q},
	} {
		if _, err := parse(argv); err == nil {
			t.Fatalf("%v 同时点了两档却通过了（表现是没人知道自己执行了哪一档）", argv)
		}
	}
	for _, argv := range [][]string{
		{"-list", "-queue", q},
		{"-replay", "-queue", q},
		{"-purge", "-queue", q, "-confirm", q},
	} {
		if _, err := parse(argv); err != nil {
			t.Fatalf("%v 应当合法：%v", argv, err)
		}
	}
}

func TestReplayAndPurgeRequireOneQueue(t *testing.T) {
	// 留空＝「对全部队列动手」，这在只读档是便利，在写入档是事故。
	for _, argv := range [][]string{{"-replay"}, {"-purge", "-confirm", q}, {"-replay", "-limit", "5"}} {
		o, err := parse(argv)
		if err == nil {
			t.Fatalf("%v 没给 -queue 却通过了：%+v", argv, o)
		}
		if !strings.Contains(err.Error(), "-queue") {
			t.Fatalf("错误没落在 -queue 上：%v", err)
		}
	}
	// 空白串也不行：`-queue " "` 会被 TrimSpace 成空，然后按「全部队列」走下去。
	if _, err := parse([]string{"-replay", "-queue", " "}); err == nil {
		t.Fatal("-queue 给空白串应当拒")
	}
}

func TestTimeoutMustBePositive(t *testing.T) {
	for _, argv := range [][]string{{"-timeout", "0s"}, {"-timeout", "-5s"}} {
		if _, err := parse(argv); err == nil {
			t.Fatalf("%v 应当拒（timeout<=0 等于整轮没有上限）", argv)
		}
	}
	o, err := parse([]string{"-timeout", "90s"})
	if err != nil || o.timeout != 90*time.Second {
		t.Fatalf("-timeout 没被读进来：%+v err=%v", o, err)
	}
}

// ── 产物面 ───────────────────────────────────────────────

func TestListModeEmitsOneParsableJSONLine(t *testing.T) {
	b := &fakeBroker{depth: 1, msgs: []dlq.Message{oneMessage("m1", `{"a":1}`)}}
	o, err := parse([]string{"-queue", q})
	if err != nil {
		t.Fatalf("parse：%v", err)
	}
	out, err := run(context.Background(), o, b)
	if err != nil {
		t.Fatalf("run：%v", err)
	}
	var got map[string]any
	if err := json.Unmarshal(mustJSON(t, out), &got); err != nil {
		t.Fatalf("stdout 的产物解析不了（进程闸会把它判成解析失败而不是值不对）：%v", err)
	}
	if got["mode"] != "list" {
		t.Fatalf("mode=%v", got["mode"])
	}
	// 正向证人：这一档真的读过 broker，且把 peek 的那条放回原处。
	if strings.Join(b.calls, ",") != "inspect,get,requeue" {
		t.Fatalf("list 档的调用序列是 %v，应当是 inspect→get→requeue（第三步没有就是丢消息）", b.calls)
	}
	if int(got["dlq_total"].(float64)) != 1 {
		t.Fatalf("dlq_total=%v，应当数到那条终态消息", got["dlq_total"])
	}
	qs, ok := got["queues"].([]any)
	if !ok || len(qs) != 1 {
		t.Fatalf("queues 的形状不对：%v", got["queues"])
	}
	row := qs[0].(map[string]any)
	if row["queue"] != q || row["kind"] != "dlq" || row["purpose_queue"] != "ai.observation.extract" {
		t.Fatalf("行内容不对：%v", row)
	}
	if row["oldest_age_known"] != true || row["oldest_message_age_seconds"].(float64) < 50 {
		t.Fatalf("年龄没读出来：%v", row)
	}
}

func TestPurgeModeRefusesWithoutConfirmBeforeDial(t *testing.T) {
	// 判据本体：确认串不对 ⇒ run 一次都不会被叫到（工具在 main 里用 noopBroker 预检，
	// 这里同时判 run 面自己也不放行，两处口径同一个判据函数）。
	b := &fakeBroker{depth: 2}
	o, err := parse([]string{"-purge", "-queue", q})
	if err != nil {
		t.Fatalf("parse：%v", err)
	}
	if !o.purgeConfirmPrecheck() {
		t.Fatal("-purge 单队列时必须走预检")
	}
	if _, err := run(context.Background(), o, b); !errors.Is(err, dlq.ErrConfirmRequired) {
		t.Fatalf("run 面上的 purge 缺确认串应当拒，err=%v", err)
	}
	if len(b.calls) != 0 {
		t.Fatalf("缺确认串时 broker 被叫到了：%v", b.calls)
	}
	// 逐字相等的确认串才放行。
	o2, err := parse([]string{"-purge", "-queue", q, "-confirm", q})
	if err != nil {
		t.Fatalf("parse：%v", err)
	}
	out, err := run(context.Background(), o2, b)
	if err != nil {
		t.Fatalf("合法确认被拒：%v", err)
	}
	if strings.Join(b.calls, ",") != "purge" || b.purged != 2 {
		t.Fatalf("purge 没落到 broker：%v (%d)", b.calls, b.purged)
	}
	if !strings.Contains(string(mustJSON(t, out)), `"discarded":2`) {
		t.Fatalf("产物没写清丢了几条：%s", mustJSON(t, out))
	}
}

func TestReplayModeReportsTheQueueItTouched(t *testing.T) {
	b := &fakeBroker{depth: 1, msgs: []dlq.Message{oneMessage("m1", `{"a":1}`)}}
	o, err := parse([]string{"-replay", "-queue", q, "-limit", "5"})
	if err != nil {
		t.Fatalf("parse：%v", err)
	}
	out, err := run(context.Background(), o, b)
	if err != nil {
		t.Fatalf("run：%v", err)
	}
	if strings.Join(b.calls, ",") != "get,publish,ack,get" {
		t.Fatalf("replay 的步序是 %v（先投成功再 ack，最后探一次队列空不空）", b.calls)
	}
	if len(b.letters) != 1 || !strings.HasPrefix(b.letters[0],
		amqp.ExchangeAITasks+"/ai.observation.requested {\"a\":1}") {
		t.Fatalf("重放的目标与字节：%v", b.letters)
	}
	var got struct {
		Mode string `json:"mode"`
		RP   struct {
			Queue    string `json:"queue"`
			Replayed int    `json:"replayed"`
			Reason   string `json:"reason"`
		} `json:"replay"`
	}
	if err := json.Unmarshal(mustJSON(t, out), &got); err != nil {
		t.Fatalf("产物解析不了：%v", err)
	}
	if got.Mode != "replay" || got.RP.Queue != q || got.RP.Replayed != 1 ||
		got.RP.Reason != "queue_empty" {
		t.Fatalf("产物内容不对：%+v", got)
	}
}

func TestPublishFailureStillEmitsWhatWasSeen(t *testing.T) {
	// 半途而废那一轮：报告先出、错误后返（文件头第 1 条＋execute 的步序）。
	b := &fakeBroker{depth: 1, msgs: []dlq.Message{oneMessage("m1", `{"a":1}`)}, pubErr: errors.New("连不上")}
	o, err := parse([]string{"-replay", "-queue", q})
	if err != nil {
		t.Fatalf("parse：%v", err)
	}
	out, err := run(context.Background(), o, b)
	if err == nil {
		t.Fatal("投失败必须回错误")
	}
	if out == nil {
		t.Fatal("半途而废的那一轮也要把已看到的数字摊出来")
	}
	if strings.Join(b.calls, ",") != "get,publish,requeue" {
		t.Fatalf("失败后没把原条放回：%v", b.calls)
	}
}

func TestListAllModeCoversWholeTopology(t *testing.T) {
	b := &fakeBroker{}
	o, err := parse(nil) // 不带 -queue ⇒ 全部
	if err != nil {
		t.Fatalf("parse：%v", err)
	}
	out, err := run(context.Background(), o, b)
	if err != nil {
		t.Fatalf("run：%v", err)
	}
	var got struct {
		Queues []map[string]any `json:"queues"`
	}
	if err := json.Unmarshal(mustJSON(t, out), &got); err != nil {
		t.Fatalf("产物解析不了：%v", err)
	}
	if len(got.Queues) != len(amqp.QueueNames()) {
		t.Fatalf("留空 -queue 应当巡检全部 %d 个队列，实际 %d",
			len(amqp.QueueNames()), len(got.Queues))
	}
	// 反向证人＋正向证人：一次 inspect 一个队列，全程零 get（fake 里深度全是 0）。
	if b.depth != 0 {
		t.Fatalf("fake 深度不为 0 就别指望这条闸：%d", b.depth)
	}
	if got2 := strings.Count(strings.Join(b.calls, ","), "inspect"); got2 != len(got.Queues) {
		t.Fatalf("inspect 次数 %d 与行数 %d 不符", got2, len(got.Queues))
	}
}

// mustJSON 把 run 的产物编码一次并断言它是**单行**（stdout 的产物形状，文件头第 1 条）。
func mustJSON(t *testing.T, out any) []byte {
	t.Helper()
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	if err := enc.Encode(out); err != nil {
		t.Fatalf("产物编码失败：%v", err)
	}
	encoded := strings.TrimRight(buf.String(), "\n")
	if strings.Contains(encoded, "\n") {
		t.Fatalf("stdout 必须是**一行** JSON，实际 %d 行", strings.Count(encoded, "\n")+1)
	}
	return []byte(encoded)
}

// TestPurgePrecheckRefusesBeforeDial 判的是 `execute` 里那一格预检，而不是 `run` 里的判据。
//
// 为什么单独一条：上面那条走的是 `run(ctx, o, fake)`，它**绕过了拨号**，于是「确认串不对时
// 一次号都不拨」这件事在整套用例里没有任何证人——把 `execute` 里那三行删掉，其余九条全绿，
// 而线上出现的形态是：运维敲错队列名，工具先跟 broker 握一次手、再告诉他确认串不对。
// 更坏的是 `-purge` 在半途失败时留下「已拨号、已声明？」的疑问，而这条链上本该一次连接都没有。
//
// 取证手法：把地址指到一个**没人监听**的端口。拨号失败报的是连接错误，预检失败报的是
// `ErrConfirmRequired`——两个串不同源，所以这条断言咬的是「谁先跑」。
func TestPurgePrecheckRefusesBeforeDial(t *testing.T) {
	t.Setenv("CLOSEN_AMQP_URL", "amqp://127.0.0.1:1/")
	err := execute([]string{"-purge", "-queue", q})
	if err == nil {
		t.Fatal("缺 -confirm 却一路跑到了拨号")
	}
	if !errors.Is(err, dlq.ErrConfirmRequired) {
		t.Fatalf("预检没在拨号之前拦住，错误不是确认串那条：%v", err)
	}
	if strings.Contains(err.Error(), "connection refused") || strings.Contains(err.Error(), "dial") {
		t.Fatalf("确认串不对时一次号都不该拨，却拿到了连接错误：%v", err)
	}
}
