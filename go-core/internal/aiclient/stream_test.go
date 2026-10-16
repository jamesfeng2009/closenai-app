package aiclient

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	closenaiv1 "github.com/jamesfeng2009/closenai-app/go-core/gen/closenai/v1"
)

// 这一批是 StreamChat 的行为面：pump 的四条保证（channel 必关、末块必带收尾、
// 首字前断流补模板、首字后断流只报错）＋ 一条「流恒不重试」。
//
// 字节面在这里也一起测：每条流的输出会过 SSELines 换成现网事件，
// 而**期望字节取自 Python**（testdata/sse_event_golden.json 由 scripts/gen_go_ai_side.py
// 调 routers/chat.py 的 _sse_event() 录）。所以这几条测是跨语言的，不是 Go 自比自。

// goldenLine 取夹具里某类事件的现网字节。找不到就 Fatalf——
// 因为「夹具里没这条」和「Go 产不出这条」是两种完全不同的红。
func goldenLine(t *testing.T, event string, wantFragment string) string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "testdata", "sse_event_golden.json"))
	if err != nil {
		t.Fatalf("读不到 SSE 夹具：%v（先跑 scripts/gen_go_ai_side.py）", err)
	}
	var golden struct {
		Cases []struct {
			Event string `json:"event"`
			Line  string `json:"line"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &golden); err != nil {
		t.Fatalf("SSE 夹具不是合法 JSON：%v", err)
	}
	for _, c := range golden.Cases {
		if c.Event == event && strings.Contains(c.Line, wantFragment) {
			return c.Line
		}
	}
	t.Fatalf("夹具里没有 %s 且含 %q 的那条字节（要加就改录制脚本，别在这手打）", event, wantFragment)
	return ""
}

func collect(t *testing.T, ch <-chan ChatChunk) []ChatChunk {
	t.Helper()
	var out []ChatChunk
	for c := range ch {
		out = append(out, c)
		if len(out) > 200 {
			t.Fatal("流没有结束的迹象（pump 的第 1 条保证破了：channel 必关）")
		}
	}
	return out
}

// TestStreamChatHappyPathToSSEBytes 走一条完整的好流：memory 块 → 三个 token 块 → DONE，
// 再把每块换成 SSE 行，逐字节比 Python 的产物。
func TestStreamChatHappyPathToSSEBytes(t *testing.T) {
	s := &stub{chatFn: func(in *closenaiv1.StreamChatRequest, srv closenaiv1.ClosenAI_StreamChatServer) error {
		if in.SchemaVersion != schemaVersion || in.UserId != "u1" || in.Content == "" || in.Language == "" {
			return status.Errorf(codes.InvalidArgument, "请求面不对：%+v", in)
		}
		// 两条 memory 与夹具里那条**同值**：期望字节只能整条拿来比，
		// 否则这条测就变成了「Go 与自己比」。
		if err := srv.Send(&closenaiv1.ChatChunk{Memories: []*closenaiv1.StreamMemory{
			{Id: "m1", Content: "我最近在改作息", Category: "habit"},
			{Id: "m2", Content: "<b>&x</b>", Category: "emotion"},
		}}); err != nil {
			return err
		}
		for _, tok := range []string{"今天", " ", "怎么样"} {
			if err := srv.Send(&closenaiv1.ChatChunk{Content: tok}); err != nil {
				return err
			}
		}
		return srv.Send(&closenaiv1.ChatChunk{Finish: closenaiv1.ChatFinish_CHAT_FINISH_DONE})
	}}
	target := startStub(t, s)
	c := newTestClient(target, noSleep)
	defer c.Close()

	ch, err := c.StreamChat(context.Background(), ChatRequest{
		UserID: "u1", Content: "我今天很难过", MessageType: "text", Language: "zh",
	})
	if err != nil {
		t.Fatalf("流没开起来：%v", err)
	}
	got := collect(t, ch)

	if len(got) != 5 {
		t.Fatalf("应有 5 块（memory + 3 token + done），got=%d：%v", len(got), got)
	}
	if len(got[0].Memories) != 2 || got[0].Memories[1].Content != "<b>&x</b>" || got[0].Content != "" {
		t.Errorf("第一块不是 memory 块：%v", got[0])
	}
	if got[4].Finish != FinishDone {
		t.Errorf("末块收尾 got=%q want=done（pump 的第 2 条保证）", got[4].Finish)
	}
	for i, want := range []string{"今天", " ", "怎么样"} {
		if got[i+1].Content != want {
			t.Errorf("第 %d 块内容 got=%q want=%q（顺序即契约）", i+1, got[i+1].Content, want)
		}
	}
	if _, _, chat := s.calls(); chat != 1 {
		t.Errorf("好流走了 %d 次 RPC，want=1", chat)
	}

	// 字节面：memory 块与 done 块都必须逐字节等于 Python 的产物。
	if line := SSELine(got[0]); line != goldenLine(t, "memory_context", `"memories"`) {
		t.Errorf("memory_context 字节不同步：\n got=%q", line)
	}
	if line := SSELine(got[4]); line != goldenLine(t, "done", `"done"`) {
		t.Errorf("done 字节不同步：\n got=%q", line)
	}
	// 整条流拼起来就是 HTTP handler 要写出去的 body。
	var body string
	for _, cc := range got {
		for _, l := range SSELines(cc) {
			body += l
		}
	}
	wantBody := goldenLine(t, "memory_context", `"memories"`) +
		"data: " + `{"type": "token", "content": "\u4eca\u5929"}` + "\n\n" +
		"data: " + `{"type": "token", "content": " "}` + "\n\n" +
		"data: " + `{"type": "token", "content": "\u600e\u4e48\u6837"}` + "\n\n" +
		goldenLine(t, "done", `"done"`)
	if body != wantBody {
		t.Errorf("整条流的 SSE body 不对：\n got=%q\nwant=%q", body, wantBody)
	}
}

// TestStreamDegradeBeforeFirstToken 打 pump 的第 3 条保证：
// 流在**没吐出任何内容**时断掉 ⇒ 补一块降级模板 + DEGRADED。
//
// 这条路是 R2 唯一「Python 已经不可达、只能靠 Go 出文案」的路，
// 所以模板取自内嵌副本（scripts/gen_go_ai_side.py 从 backend/data/copy 录）。
func TestStreamDegradeBeforeFirstToken(t *testing.T) {
	s := &stub{chatFn: func(*closenaiv1.StreamChatRequest, closenaiv1.ClosenAI_StreamChatServer) error {
		return status.Error(codes.Internal, "provider 炸了")
	}}
	target := startStub(t, s)
	c := newTestClient(target, noSleep)
	defer c.Close()

	ch, err := c.StreamChat(context.Background(), ChatRequest{UserID: "u1", Content: "我今天", Language: "zh"})
	if err != nil {
		t.Fatalf("StreamChat 不该报错（对面断了也要给用户一句话）：%v", err)
	}
	got := collect(t, ch)
	if len(got) != 2 || got[0].Finish != FinishChunk || got[1].Finish != FinishDegraded {
		t.Fatalf("应有模板 + DEGRADED 两块，got=%v", got)
	}
	if !containsStr(fallbackCorpus["zh"], got[0].Content) {
		t.Errorf("降级块不在 zh 的副本里：%q", got[0].Content)
	}
	// 轮转必须与 Python 同形（码点数 3 ⇒ zh 的第 4 条），这里直接对 FallbackReply 而不是重算下标。
	if want, _ := FallbackReply("我今天", "zh"); got[0].Content != want {
		t.Errorf("轮转分叉：got=%q FallbackReply=%q", got[0].Content, want)
	}
	if got[0].Failure != FailureUnavailable || got[1].Failure != FailureUnavailable {
		t.Errorf("Failure 码没带出来（留痕与指标靠它）：%v", got)
	}
	if _, _, chat := s.calls(); chat != 1 {
		t.Errorf("流被重投了：对面收到 %d 次，want=1（半途的流不能重放）", chat)
	}
	// 用户侧看到的仍是老事件名：token + done。
	lines := SSELines(got[0])
	if len(lines) != 1 || !strings.HasPrefix(lines[0], `data: {"type": "token"`) {
		t.Errorf("模板块应落成 token 事件：%q", lines)
	}
	if lines := SSELines(got[1]); len(lines) != 1 || lines[0] != goldenLine(t, "done", `"done"`) {
		t.Errorf("DEGRADED 应落成 done 事件（不给已发布客户端造新事件名）：%q", lines)
	}
}

// TestStreamErrorAfterTokens 打第 4 条保证，也是它与第 3 条的分界：
// 已经吐过内容之后断流**只报错，不追加模板句**。
//
// 为什么：用户已经看到半句话，接一句「今天过得怎么样？」会产出一条谁都没审过的文案；
// 而 chat.py:137-140 对这种情况本来就是 error 事件，字节面也照它比。
func TestStreamErrorAfterTokens(t *testing.T) {
	s := &stub{chatFn: func(_ *closenaiv1.StreamChatRequest, srv closenaiv1.ClosenAI_StreamChatServer) error {
		if err := srv.Send(&closenaiv1.ChatChunk{Content: "半句话"}); err != nil {
			return err
		}
		return status.Error(codes.Unavailable, "对面在流中途倒了")
	}}
	target := startStub(t, s)
	c := newTestClient(target, noSleep)
	defer c.Close()

	got := collect(t, mustStream(t, c, ChatRequest{UserID: "u1", Content: "x", Language: "zh"}))
	if len(got) != 2 {
		t.Fatalf("got=%d 块：%v", len(got), got)
	}
	if got[0].Content != "半句话" {
		t.Errorf("第一块丢了：%v", got[0])
	}
	if got[1].Finish != FinishError || got[1].ErrorCode != internalErrorCode || !got[1].HasErrorCode {
		t.Errorf("断流后应为带码的 error 块：%v", got[1])
	}
	if containsStr(fallbackCorpus["zh"], got[1].ErrorMessage) {
		t.Error("半句话后面接了模板句（会产出一条没审过的文案）")
	}
	if got[1].Failure != FailureTransport {
		t.Errorf("Failure got=%s want=transport", got[1].Failure)
	}
	// 字节面：这条 error 事件必须逐字节等于现网 chat.py:137-140 那条 500。
	if line := SSELine(got[1]); line != goldenLine(t, "error", "500") {
		t.Errorf("error 事件字节不同步：\n got=%q\nwant=%q", line, goldenLine(t, "error", "500"))
	}
}

func mustStream(t *testing.T, c *GRPC, req ChatRequest) <-chan ChatChunk {
	t.Helper()
	ch, err := c.StreamChat(context.Background(), req)
	if err != nil {
		t.Fatalf("StreamChat：%v", err)
	}
	return ch
}

// TestStreamQuotaAndBlockedPassthrough 把两条「对面已经决定怎么收尾」的路各走一次：
// 配额 40301 的码必须原样带出（不能变成 Go 造的 500），BLOCKED 的安全话术必须留住。
func TestStreamQuotaAndBlockedPassthrough(t *testing.T) {
	code := int32(40301)
	t.Run("配额", func(t *testing.T) {
		s := &stub{chatFn: func(_ *closenaiv1.StreamChatRequest, srv closenaiv1.ClosenAI_StreamChatServer) error {
			return srv.Send(&closenaiv1.ChatChunk{
				Finish: closenaiv1.ChatFinish_CHAT_FINISH_ERROR, ErrorCode: &code,
				ErrorMessage: "今日对话次数已用完",
			})
		}}
		c := newTestClient(startStub(t, s), noSleep)
		defer c.Close()
		got := collect(t, mustStream(t, c, ChatRequest{Content: "x", Language: "zh"}))
		if len(got) != 1 || got[0].Finish != FinishError || got[0].ErrorCode != 40301 ||
			got[0].ErrorMessage != "今日对话次数已用完" || got[0].Failure != FailureNone {
			t.Fatalf("配额块没原样透传：%v", got)
		}
		// 服务端给的块 Failure 必须留空：那是「本包造的块」的标记，混了就没法分清是谁的错误。
		if line := SSELine(got[0]); line != goldenLine(t, "error", "40301") {
			t.Errorf("配额错误事件字节不同步：\n got=%q\nwant=%q", line, goldenLine(t, "error", "40301"))
		}
	})

	t.Run("安全截断", func(t *testing.T) {
		s := &stub{chatFn: func(_ *closenaiv1.StreamChatRequest, srv closenaiv1.ClosenAI_StreamChatServer) error {
			return srv.Send(&closenaiv1.ChatChunk{
				Finish: closenaiv1.ChatFinish_CHAT_FINISH_BLOCKED, Content: "我很在意你的安全",
			})
		}}
		c := newTestClient(startStub(t, s), noSleep)
		defer c.Close()
		got := collect(t, mustStream(t, c, ChatRequest{Content: "x", Language: "zh"}))
		if len(got) != 1 || got[0].Finish != FinishBlocked || got[0].Content == "" {
			t.Fatalf("BLOCKED 的话术丢了：%v", got)
		}
		lines := SSELines(got[0])
		if len(lines) != 2 || lines[1] != goldenLine(t, "done", `"done"`) {
			t.Errorf("BLOCKED 要拆成 token+done 两条（一块对两条事件）：%q", lines)
		}
	})
}

// TestStreamMalformedStopsWithoutRetry 是「畸形输出走 reject 而非重试」在流上的形态：
// 四条违规各造一次，每次都必须**当场停**、不重投、且给用户一个诚实的收尾。
func TestStreamMalformedStopsWithoutRetry(t *testing.T) {
	code := int32(500)
	cases := []struct {
		name   string
		chunks []*closenaiv1.ChatChunk
	}{
		{"content 与 memories 同现（无法定序）", []*closenaiv1.ChatChunk{
			{Content: "半句", Memories: []*closenaiv1.StreamMemory{{Id: "m1"}}},
		}},
		{"DONE 带 content（两个真相源）", []*closenaiv1.ChatChunk{
			{Finish: closenaiv1.ChatFinish_CHAT_FINISH_DONE, Content: "还有话"},
		}},
		{"ERROR 不带码（客户端只能猜）", []*closenaiv1.ChatChunk{
			{Finish: closenaiv1.ChatFinish_CHAT_FINISH_ERROR, ErrorCode: nil},
		}},
		{"未知 ChatFinish（版本错配）", []*closenaiv1.ChatChunk{
			{Finish: closenaiv1.ChatFinish(999)},
		}},
		// 没有「空块」这一条：gRPC 的 Recv() 永远不会返回 (nil, nil)，
		// decodeChatChunk 里的 nil 判断是防 Go 侧误用的护栏，不是线路面。
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := &stub{chatFn: func(_ *closenaiv1.StreamChatRequest, srv closenaiv1.ClosenAI_StreamChatServer) error {
				for _, ck := range tc.chunks {
					if err := srv.Send(ck); err != nil {
						return err
					}
				}
				return nil // 对面正常结束，问题只在块本身
			}}
			target := startStub(t, s)
			c := newTestClient(target, noSleep)
			defer c.Close()

			got := collect(t, mustStream(t, c, ChatRequest{Content: "我今天", Language: "zh"}))
			if _, _, chat := s.calls(); chat != 1 {
				t.Errorf("畸形流被重投：%d 次", chat)
			}
			last := got[len(got)-1]
			if last.Finish == FinishChunk {
				t.Errorf("畸形之后末块没收尾：%v", got)
			}
			// 首字之前 ⇒ 模板 + DEGRADED；首字之后 ⇒ error。两者都不许继续正常收尾。
			switch last.Finish {
			case FinishDegraded:
				if len(got) != 2 || !containsStr(fallbackCorpus["zh"], got[0].Content) {
					t.Errorf("首字前畸形应给模板+DEGRADED，got=%v", got)
				}
			case FinishError:
				if last.ErrorCode != int(code) || !last.HasErrorCode {
					t.Errorf("首字后畸形应给带码 error，got=%v", last)
				}
			default:
				t.Errorf("畸形之后收尾成了 %q（等于把违规当正常）", last.Finish)
			}
			for _, cc := range got {
				if cc.Failure == FailureMalformed {
					return
				}
			}
			t.Errorf("没有任何块标出 malformed（留痕面丢了）：%v", got)
		})
	}
}

// TestStreamDeadlineCutsStalledStream 测流上的预算：对面**开着流但不写字节**时，
// StreamDeadline 到了就得把流收掉，而不是让用户的页面挂着。
func TestStreamDeadlineCutsStalledStream(t *testing.T) {
	s := &stub{chatFn: func(_ *closenaiv1.StreamChatRequest, srv closenaiv1.ClosenAI_StreamChatServer) error {
		if err := srv.Send(&closenaiv1.ChatChunk{Content: "第一句"}); err != nil {
			return err
		}
		select {
		case <-srv.Context().Done():
			return srv.Context().Err()
		case <-time.After(2 * time.Second):
			return nil
		}
	}}
	target := startStub(t, s)
	c := newTestClient(target, func(o *Options) {
		o.BreakerThreshold = -1
		o.StreamDeadline = 80 * time.Millisecond
	})
	defer c.Close()

	start := time.Now()
	got := collect(t, mustStream(t, c, ChatRequest{Content: "x", Language: "zh"}))
	elapsed := time.Since(start)
	if elapsed > 1*time.Second {
		t.Errorf("等了 %v：流上的 deadline 没起作用（对面会睡 2s）", elapsed)
	}
	if got[0].Content != "第一句" {
		t.Errorf("已经收到的那块丢了：%v", got)
	}
	if last := got[len(got)-1]; last.Finish != FinishError {
		t.Errorf("超时收尾 got=%q want=error", last.Finish)
	}
	t.Logf("实测：流在 %v 被 %v 的预算切掉，末块 %s/%s",
		elapsed.Round(time.Millisecond), c.opts.StreamDeadline, got[len(got)-1].Failure, got[len(got)-1].Finish)
}

// TestStreamBufferIsBounded 说清这条测**能**测什么、不能测什么：
//
//	能测：本地 channel 的界（pump 领先慢消费者的上限）与顺序不被破坏。
//	不能测：HTTP/2 流控。它的窗口是 64KB，几个 token 块根本填不满，
//	       所以「Send 返回了」在这里不代表对面已经消费；拿它当背压证据是假的。
//
// 对面真 server 的端到端背压要等 R3（Python 侧 gRPC server）才量得到。
func TestStreamBufferIsBounded(t *testing.T) {
	const n = 40
	s := &stub{chatFn: func(_ *closenaiv1.StreamChatRequest, srv closenaiv1.ClosenAI_StreamChatServer) error {
		for i := 0; i < n; i++ {
			if err := srv.Send(&closenaiv1.ChatChunk{Content: "字"}); err != nil {
				return err
			}
		}
		return srv.Send(&closenaiv1.ChatChunk{Finish: closenaiv1.ChatFinish_CHAT_FINISH_DONE})
	}}
	target := startStub(t, s)

	// 默认缓冲 = defaultStreamBuffer（16），显式设 1 时也必须照住。
	for buf, want := range map[int]int{0: defaultStreamBuffer, 1: 1} {
		c := newTestClient(target, func(o *Options) {
			o.BreakerThreshold = -1
			o.StreamBuffer = buf
		})
		ch, err := c.StreamChat(context.Background(), ChatRequest{Content: "x", Language: "zh"})
		if err != nil {
			t.Fatalf("StreamChat：%v", err)
		}
		if got := cap(ch); got != want {
			t.Errorf("StreamBuffer=%d 时 channel 容量 got=%d want=%d（背压就在这条 channel 上）", buf, got, want)
		}
		// 慢消费者：逐个读，读完必须正好 n+1 块且顺序不变、末块收尾。
		var got []ChatChunk
		for cc := range ch {
			got = append(got, cc)
		}
		if len(got) != n+1 || got[n].Finish != FinishDone {
			t.Errorf("缓冲=%d 时块数 got=%d want=%d（末块=%v）", buf, len(got), n+1, lastFinish(got))
		}
		c.Close()
	}
}

func lastFinish(got []ChatChunk) Finish {
	if len(got) == 0 {
		return FinishChunk
	}
	return got[len(got)-1].Finish
}

// TestBackpressureMeasured 给 §R2 验收项里「首字延迟与背压需一条实测数字」的**背压**那一半。
//
// 这条测故意不写成「断言面」，因为想测的那件事恰恰不是本包能保证的：
// pump 领先慢消费者的上限是**本地 channel 的容量**（这里设 1），这一半是 Go 的语言保证；
// 而「压力有没有顶回服务端」取决于 HTTP/2 的流控窗口，小块根本填不满它。
// 所以这里量的是这两者的**分界**：消费者只取走 1 块并停住 100ms 时，对面已经写完了几块。
//
// 量的结果会写进 tasks.md §R2（数字本身不是结论）：如果对面的完成数远超 1＋缓冲，
// 就说明「背压」在今天这条路上只到 Go 进程边界为止，真要顶回去得靠**大载荷或窗口调小**，
// 而那属于 R3 起真 server 之后的事。
func TestBackpressureMeasured(t *testing.T) {
	const chunks = 200
	body := strings.Repeat("字", 100) // 每块 100 个汉字＝300 字节 UTF-8 ⇒ 整条约 60KB
	var sent atomic.Int64
	s := &stub{chatFn: func(_ *closenaiv1.StreamChatRequest, srv closenaiv1.ClosenAI_StreamChatServer) error {
		for i := 0; i < chunks; i++ {
			if err := srv.Send(&closenaiv1.ChatChunk{Content: body}); err != nil {
				return err
			}
			sent.Add(1)
		}
		return srv.Send(&closenaiv1.ChatChunk{Finish: closenaiv1.ChatFinish_CHAT_FINISH_DONE})
	}}
	target := startStub(t, s)
	c := newTestClient(target, func(o *Options) {
		o.BreakerThreshold = -1
		o.StreamBuffer = 1
	})
	defer c.Close()

	start := time.Now()
	ch := mustStream(t, c, ChatRequest{Content: "x", Language: "zh"})
	if cap(ch) != 1 {
		t.Fatalf("缓冲没照住：cap=%d", cap(ch))
	}
	<-ch // 只取一块，然后**停住**：pump 应该阻塞在 out<- 上
	time.Sleep(100 * time.Millisecond)
	byConsumer := len(ch) + 1 // 消费者已取走的 + 此刻还挂在 channel 里的
	stubDone := int(sent.Load())
	t.Logf("背压实测：本地缓冲=%d，消费者侧最多可见=%d 块；停住 100ms 时对面已写完 %d/%d 块（用时 %v）",
		cap(ch), byConsumer, stubDone, chunks, time.Since(start).Round(time.Millisecond))

	// 这一半才是本包真正保证住了的东西：**客户端**不会替对面囤积无限多的块。
	if byConsumer > c.opts.StreamBuffer+1 {
		t.Errorf("消费者只取了 1 块，客户端侧却可见 %d 块（缓冲 %d＋在途 1 是上限）",
			byConsumer, c.opts.StreamBuffer)
	}
	// 慢消费者不能把流挂死：放干之后必须收到全部块与收尾。
	got := []ChatChunk{{Content: body}}
	got = append(got, collect(t, ch)...)
	if len(got) != chunks+1 || got[chunks].Finish != FinishDone {
		t.Errorf("放干后 got=%d 块 want=%d（末块=%v）", len(got), chunks+1, lastFinish(got))
	}
	t.Logf("对面完成度：%d/%d ⇒ %s", stubDone, chunks,
		func() string {
			if stubDone > c.opts.StreamBuffer+1 {
				return "压力没顶回服务端（HTTP/2 窗口吃得下），背压只到 Go 进程边界"
			}
			return "压力顶回了服务端"
		}())
}

// TestFirstTokenLatencyMeasured 是「首字延迟」的**测量**，不是断言：
// 它记录 Go 客户端 + 环回 TCP + Go stub 这条路的 p50/p95，用来卡一个明显的回归
// （比如有人把 dial 挪到每次调用、或在首块之前加了一次同步 RPC）。
//
// ⚠️ 口径必须说破：这里的数字**不含** Python 侧的 provider 延迟（3–10s 量级，
// PRD 的「端到端 < 3s」由它主导）。所以这一条只能证明「Go 这侧没加不该有的等待」，
// 真正的端到端数字要等 R3 起真 server 再量一次（tasks.md §R3）。
func TestFirstTokenLatencyMeasured(t *testing.T) {
	s := &stub{chatFn: func(_ *closenaiv1.StreamChatRequest, srv closenaiv1.ClosenAI_StreamChatServer) error {
		if err := srv.Send(&closenaiv1.ChatChunk{Content: "第一句"}); err != nil {
			return err
		}
		return srv.Send(&closenaiv1.ChatChunk{Finish: closenaiv1.ChatFinish_CHAT_FINISH_DONE})
	}}
	target := startStub(t, s)
	c := newTestClient(target, func(o *Options) { o.BreakerThreshold = -1 })
	defer c.Close()

	const runs = 25
	samples := make([]time.Duration, 0, runs)
	for i := 0; i < runs; i++ {
		start := time.Now()
		ch, err := c.StreamChat(context.Background(), ChatRequest{UserID: "u1", Content: "x", Language: "zh"})
		if err != nil {
			t.Fatalf("第 %d 次没开起来：%v", i, err)
		}
		first := <-ch
		samples = append(samples, time.Since(start))
		if first.Content != "第一句" {
			t.Fatalf("第 %d 次首块不对：%v", i, first)
		}
		for range ch { // 收干，别把 goroutine 留到下一轮
		}
	}
	sort.Slice(samples, func(a, b int) bool { return samples[a] < samples[b] })
	p50 := samples[runs/2]
	p95 := samples[runs*95/100]
	t.Logf("首字延迟（Go 客户端＋环回 TCP＋Go stub，不含 Python）：runs=%d p50=%v p95=%v min=%v max=%v",
		runs, p50.Round(time.Microsecond), p95.Round(time.Microsecond),
		samples[0].Round(time.Microsecond), samples[runs-1].Round(time.Microsecond))
	if p95 > 250*time.Millisecond {
		t.Errorf("p95=%v：本地环回的首字延迟不该到这个量级（查 dial 是不是每次重连、或首块前多了同步 RPC）", p95)
	}
	// 连接是 lazy 的但只该建一次：25 轮流之后对面仍然只收到 25 次 StreamChat。
	if _, _, chat := s.calls(); chat != runs {
		t.Errorf("对面收到 %d 次流，want=%d", chat, runs)
	}
}
