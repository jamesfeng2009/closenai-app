// 批 T-B 闸 G-B2（domain 面）：trace 载体与两个出箱写入口。
//
// 拦的事故形态：
// - emit/RequestAITask 漏读 ctx ⇒ HTTP 触发的命令在库里就没 trace，后两段永远断链；
// - WithTraceID 把空串也注入 ⇒ TraceIDFrom 分不清「没链」与「空链」。
package domain

import (
	"context"
	"testing"
)

func TestTraceCarrierRoundTrip(t *testing.T) {
	if got := TraceIDFrom(context.Background()); got != "" {
		t.Errorf("裸 ctx 应得空串，实得 %q", got)
	}
	ctx := WithTraceID(context.Background(), "T-1")
	if got := TraceIDFrom(ctx); got != "T-1" {
		t.Errorf("注入后应逐字取回 T-1，实得 %q", got)
	}
	// 空串＝不注入（与 Envelope 空串不盖 run_id 同一条取舍）。
	base := WithTraceID(context.Background(), "T-keep")
	if got := TraceIDFrom(WithTraceID(base, "")); got != "T-keep" {
		t.Errorf("空串注入不该清掉已有 trace，实得 %q", got)
	}
}

func TestRequestAITaskRowCarriesCtxTrace(t *testing.T) {
	f := newFakeStore()
	s, _ := newTestService(f)
	ctx := WithTraceID(context.Background(), "T-req")
	if err := s.RequestAITask(ctx, f, okTaskRequest()); err != nil {
		t.Fatalf("RequestAITask: %v", err)
	}
	if len(f.outbox) != 1 {
		t.Fatalf("投递行数=%d，期望 1", len(f.outbox))
	}
	if row := f.outbox[0]; row.TraceID != "T-req" {
		t.Errorf("OutboxRow.TraceID=%q，期望 ctx 的 T-req（G-B2 的主断言）", row.TraceID)
	}

	// 无 trace 的后台上下文：行照写，TraceID 留空串——这是合法形态，不是错误。
	f2 := newFakeStore()
	s2, _ := newTestService(f2)
	if err := s2.RequestAITask(context.Background(), f2, okTaskRequest()); err != nil {
		t.Fatalf("RequestAITask(无 trace): %v", err)
	}
	if row := f2.outbox[0]; row.TraceID != "" {
		t.Errorf("后台起源的行 TraceID=%q，应为空串", row.TraceID)
	}
}

func TestEmitRowCarriesCtxTrace(t *testing.T) {
	f := newFakeStore()
	s, _ := newTestService(f)
	err := s.db.InTx(context.Background(), func(ctx context.Context, st Store) error {
		return s.emit(WithTraceID(ctx, "T-emit"), st, EventRow{ID: "ev-t", UserID: owner,
			EventType: "insight_expired", AggregateType: "insights", AggregateID: "ins-1"})
	})
	if err != nil {
		t.Fatalf("emit: %v", err)
	}
	if len(f.outbox) != 1 {
		t.Fatalf("投递行数=%d，期望 1", len(f.outbox))
	}
	if row := f.outbox[0]; row.TraceID != "T-emit" {
		t.Errorf("emit 写的行 TraceID=%q，期望 T-emit（HTTP 触发的事件必须能在库里续链）", row.TraceID)
	}
}
