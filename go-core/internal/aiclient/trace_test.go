// 批 T-E 闸 G-E1：Go 客户端出站 metadata 必须逐字带上 ctx 里的 x-trace-id。
//
// 夹具复用 stub_test.go 的**真 TCP** 假服务端（不用 bufconn 的理由同那里：这一跳的
// 首字延迟测要靠同一条路径）。测面只有一件事——服务端侧 `metadata.FromIncomingContext`
// 读到的值 vs 客户端 `domain.WithTraceID` 注入的值逐字相等；没注入时键必须**缺席**。
package aiclient

import (
	"context"
	"testing"

	"google.golang.org/grpc/metadata"

	closenaiv1 "github.com/jamesfeng2009/closenai-app/go-core/gen/closenai/v1"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// incomingTrace 在服务端侧读入站 metadata。返回 nil 表示键缺席（区别于空串值）。
func incomingTrace(ctx context.Context) []string {
	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return nil
	}
	return md.Get(metadataTraceKey)
}

func TestInjectTraceCarrierSemantics(t *testing.T) {
	// 单元面直判 injectTrace（不经过 transport）：M-E5 摘掉空值守卫时，
	// 「空串到底能不能上线」不该取决于 grpc 版本对空值的容忍度。
	if _, ok := metadata.FromOutgoingContext(injectTrace(context.Background())); ok {
		t.Error("空值不注入：ctx 无 trace 时不许造 outgoing metadata")
	}
	md, ok := metadata.FromOutgoingContext(
		injectTrace(domain.WithTraceID(context.Background(), "T-UNIT")))
	if !ok || len(md[metadataTraceKey]) != 1 || md[metadataTraceKey][0] != "T-UNIT" {
		t.Errorf("injectTrace 必须逐字搬运，实际 ok=%v md=%v", ok, md)
	}
}

func TestMetadataKeyLiteral(t *testing.T) {
	// 与 AMQP（publisher.go:44）／HTTP（middleware.go:24）同一个逻辑头的字面量闸，
	// 沿 amqp/trace_test.go:27 的同一条「两侧不许漂移」纪律。
	if metadataTraceKey != "x-trace-id" {
		t.Errorf("metadata 键漂移：%q", metadataTraceKey)
	}
}

func TestUnaryCallsCarryTraceMetadata(t *testing.T) {
	var gotEmbed, gotSafety []string
	s := &stub{
		embedFn: func(ctx context.Context, _ *closenaiv1.EmbedRequest) (*closenaiv1.EmbedResponse, error) {
			gotEmbed = incomingTrace(ctx)
			return okEmbed([][]float32{{0.1, 0.2}})(ctx, nil)
		},
		safetyFn: func(ctx context.Context, _ *closenaiv1.TextInput) (*closenaiv1.SafetyResult, error) {
			gotSafety = incomingTrace(ctx)
			return safety(closenaiv1.SafetyStatus_SAFETY_STATUS_OK)(ctx, nil)
		},
	}
	c := newTestClient(startStub(t, s), noSleep)
	defer c.Close()

	ctx := domain.WithTraceID(context.Background(), "T-GE1-EMB")
	if _, err := c.Embed(ctx, EmbedRequest{Texts: []string{"我今天很难过"}}); err != nil {
		t.Fatalf("Embed：%v", err)
	}
	if len(gotEmbed) != 1 || gotEmbed[0] != "T-GE1-EMB" {
		t.Errorf("Embed 出站 metadata x-trace-id=%v，要 [T-GE1-EMB] 逐字", gotEmbed)
	}

	ctx2 := domain.WithTraceID(context.Background(), "T-GE1-SAFE")
	if _, err := c.ClassifySafety(ctx2, TextInput{Text: "我不想活了", Language: "zh"}); err != nil {
		t.Fatalf("ClassifySafety：%v", err)
	}
	if len(gotSafety) != 1 || gotSafety[0] != "T-GE1-SAFE" {
		t.Errorf("ClassifySafety 出站 metadata x-trace-id=%v，要 [T-GE1-SAFE] 逐字", gotSafety)
	}
}

func TestStreamChatCarriesTraceMetadata(t *testing.T) {
	// 流面单独一条：链式 stream 拦截器与 unary 是两个挂点，M-E2 之外
	// 任何「只挂 unary」的退化都要在这里先红，而不是等 R3b 流量上线才发现。
	var got []string
	s := &stub{chatFn: func(_ *closenaiv1.StreamChatRequest, srv closenaiv1.ClosenAI_StreamChatServer) error {
		got = incomingTrace(srv.Context())
		return srv.Send(&closenaiv1.ChatChunk{Finish: closenaiv1.ChatFinish_CHAT_FINISH_DONE})
	}}
	c := newTestClient(startStub(t, s), noSleep)
	defer c.Close()

	ch, err := c.StreamChat(domain.WithTraceID(context.Background(), "T-GE1-STREAM"),
		ChatRequest{Content: "x", Language: "zh"})
	if err != nil {
		t.Fatalf("StreamChat：%v", err)
	}
	collect(t, ch)
	if len(got) != 1 || got[0] != "T-GE1-STREAM" {
		t.Errorf("StreamChat 出站 metadata x-trace-id=%v，要 [T-GE1-STREAM] 逐字", got)
	}
}

func TestNoTraceMeansNoMetadata(t *testing.T) {
	// 空值不注入（trace.go 与 publisher.go:140 同一取舍）：这里的判据是**键缺席**，
	// 不是「值为空串」。若哪天改成注入空串，对面 Python 会在「没带」和「带了个空的」
	// 之间被迫猜，消毒面（server 的 _apply_trace）也会多一条无意义分支。
	var got []string
	keyPresent := false
	s := &stub{
		embedFn: func(ctx context.Context, _ *closenaiv1.EmbedRequest) (*closenaiv1.EmbedResponse, error) {
			md, _ := metadata.FromIncomingContext(ctx)
			got = md.Get(metadataTraceKey)
			_, keyPresent = md[metadataTraceKey]
			return okEmbed([][]float32{{0.1}})(ctx, nil)
		},
	}
	c := newTestClient(startStub(t, s), noSleep)
	defer c.Close()

	if _, err := c.Embed(context.Background(), EmbedRequest{Texts: []string{"a"}}); err != nil {
		t.Fatalf("Embed：%v", err)
	}
	if keyPresent || len(got) != 0 {
		t.Errorf("ctx 无 trace 时键必须缺席，实际 present=%v values=%v", keyPresent, got)
	}
}
