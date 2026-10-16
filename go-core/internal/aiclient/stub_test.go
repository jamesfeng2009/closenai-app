package aiclient

import (
	"context"
	"net"
	"sync"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	closenaiv1 "github.com/jamesfeng2009/closenai-app/go-core/gen/closenai/v1"
)

// stub 是一个可控的 ClosenAI 服务端，用来在**不依赖 Python** 的前提下把 R2 的失败面全跑一遍。
//
// ⚠️ 它测的是「Go 客户端对这四种失败各做了什么」，**不**测「Python 真的会给出这四种失败」——
// 后一条要等 R3 的 gRPC server（tasks.md §R3 验收项：pytest 走 grpc test harness）。
// 所以这里的夹具走真 TCP 而不是 bufconn：同一条路径要能被首字延迟测复用，
// 而 bufconn 少掉一层真实的环回 TCP 写读，那个数字就没有参考价值。
type stub struct {
	closenaiv1.UnimplementedClosenAIServer

	mu sync.Mutex

	embedFn  func(context.Context, *closenaiv1.EmbedRequest) (*closenaiv1.EmbedResponse, error)
	safetyFn func(context.Context, *closenaiv1.TextInput) (*closenaiv1.SafetyResult, error)
	chatFn   func(*closenaiv1.StreamChatRequest, closenaiv1.ClosenAI_StreamChatServer) error

	embedCalls  int
	safetyCalls int
	chatCalls   int
}

func (s *stub) Embed(ctx context.Context, in *closenaiv1.EmbedRequest) (*closenaiv1.EmbedResponse, error) {
	s.mu.Lock()
	s.embedCalls++
	fn := s.embedFn
	s.mu.Unlock()
	if fn == nil {
		return nil, status.Error(codes.Unimplemented, "stub 没配 Embed")
	}
	return fn(ctx, in)
}

func (s *stub) ClassifySafety(ctx context.Context, in *closenaiv1.TextInput) (*closenaiv1.SafetyResult, error) {
	s.mu.Lock()
	s.safetyCalls++
	fn := s.safetyFn
	s.mu.Unlock()
	if fn == nil {
		return nil, status.Error(codes.Unimplemented, "stub 没配 ClassifySafety")
	}
	return fn(ctx, in)
}

func (s *stub) StreamChat(in *closenaiv1.StreamChatRequest, out closenaiv1.ClosenAI_StreamChatServer) error {
	s.mu.Lock()
	s.chatCalls++
	fn := s.chatFn
	s.mu.Unlock()
	if fn == nil {
		return status.Error(codes.Unimplemented, "stub 没配 StreamChat")
	}
	return fn(in, out)
}

func (s *stub) calls() (int, int, int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.embedCalls, s.safetyCalls, s.chatCalls
}

// startStub 起一个真 TCP server，返回 target 与清理函数。
func startStub(t *testing.T, s *stub) string {
	t.Helper()
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("监听失败：%v", err)
	}
	srv := grpc.NewServer()
	closenaiv1.RegisterClosenAIServer(srv, s)
	go func() { _ = srv.Serve(lis) }()
	t.Cleanup(func() {
		srv.Stop()
		_ = lis.Close()
	})
	return lis.Addr().String()
}

func okEmbed(vecs [][]float32) func(context.Context, *closenaiv1.EmbedRequest) (*closenaiv1.EmbedResponse, error) {
	return func(_ context.Context, _ *closenaiv1.EmbedRequest) (*closenaiv1.EmbedResponse, error) {
		out := &closenaiv1.EmbedResponse{SchemaVersion: schemaVersion, Model: "text-embedding-v4"}
		for _, v := range vecs {
			out.Vectors = append(out.Vectors, &closenaiv1.EmbeddingVector{Values: v})
		}
		if len(vecs) > 0 {
			out.Dimensions = uint32(len(vecs[0]))
		}
		return out, nil
	}
}

func safety(statusVal closenaiv1.SafetyStatus) func(context.Context, *closenaiv1.TextInput) (*closenaiv1.SafetyResult, error) {
	return func(_ context.Context, _ *closenaiv1.TextInput) (*closenaiv1.SafetyResult, error) {
		return &closenaiv1.SafetyResult{
			SchemaVersion: schemaVersion,
			Safety:        &closenaiv1.Safety{Status: statusVal, Categories: []string{"self_harm"}},
			Reason:        "model",
		}, nil
	}
}

func newTestClient(target string, mutate func(*Options)) *GRPC {
	opts := Options{Target: target}
	if mutate != nil {
		mutate(&opts)
	}
	return New(opts)
}

// noSleep 把退避变成即时：这些测关心的是「试了几次、每次什么后果」，
// 不是 0.5s×2 的真实等待。真等待由延迟测单独负责。
//
// BreakerThreshold 用 **-1** 而不是 0：0 现在是「用默认（5）」的哨兵（见 Options 的注释），
// 想要关掉必须显式给负数——这条测顺手就是那个区分的第二个观测点。
func noSleep(o *Options) {
	o.Sleep = func(context.Context, time.Duration) error { return nil }
	o.BreakerThreshold = -1
}
