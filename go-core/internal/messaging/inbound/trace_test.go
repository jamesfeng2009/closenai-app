// 批 T-B 闸 G-B2（消费面）：candidate 的 x-trace-id 必须在 Handle 入口恢复进 ctx，
// 且缺头时保持清白（不残留、不造假）。fakeDomain 记的 consumeCtx 就是领域事务
// 真正拿到的 ctx——断言它，等于断言「Admit 里 emit 的 follow-up 行能续链」。
package inbound

import (
	"context"
	"testing"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

func TestHandleRestoresTraceIntoDomainCtx(t *testing.T) {
	dm := newFakeDomain()
	r := testRunner(t, dm, onSwitch(), nil)

	d := delivery(nil, nil)
	rec := &recorder{}
	d.Settle = rec.settle
	d.Headers[HeaderTraceID] = "T-9"
	if _, err := r.Handle(context.Background(), d); err != nil {
		t.Fatalf("Handle: %v", err)
	}
	if len(dm.consumeCtx) != 1 {
		t.Fatalf("领域事务跑了 %d 次，期望 1", len(dm.consumeCtx))
	}
	if got := domain.TraceIDFrom(dm.consumeCtx[0]); got != "T-9" {
		t.Errorf("领域 ctx 的 trace=%q，期望 T-9（M-B1 摘掉 Handle 那行注入时红 Here）", got)
	}
}

func TestHandleWithoutTraceHeaderStaysClean(t *testing.T) {
	dm := newFakeDomain()
	r := testRunner(t, dm, onSwitch(), nil)

	d := delivery(nil, nil) // 基础夹具没有 x-trace-id
	rec := &recorder{}
	d.Settle = rec.settle
	if _, err := r.Handle(context.Background(), d); err != nil {
		t.Fatalf("Handle: %v", err)
	}
	if got := domain.TraceIDFrom(dm.consumeCtx[0]); got != "" {
		t.Errorf("缺头时 ctx 里冒出 trace=%q——串链就是假证人", got)
	}
}

func TestHeaderStringAcceptsBytesForm(t *testing.T) {
	// Python aio_pika 把短字符串头编码成 bytes 是线上真实形态（与 HeaderUserID 同路），
	// 消费侧若只认 string，跨语言那一跳会静默断链。
	d := delivery(nil, nil)
	d.Headers[HeaderTraceID] = []byte("T-bytes")
	if got := d.HeaderString(HeaderTraceID); got != "T-bytes" {
		t.Errorf("bytes 形态读成 %q，期望 T-bytes", got)
	}
}
