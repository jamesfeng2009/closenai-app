// 批 T-B 闸 G-B2（发布面）：x-trace-id 的写与不写。
//
// 变异 M-B1＝摘掉 publisher.go 里的条件写；M-B2＝把「空值不写」改成「空值照写」。
// 两条各打红下面两个用例之一，红的就是判据本身。
package amqp

import (
	"context"
	"testing"
	"time"
)

func TestPublishCarriesTraceHeader(t *testing.T) {
	conf := &fakeConfirmer{acked: true}
	p := newTestPublisher(t, conf, time.Second)

	env := commandEnvelope()
	env.TraceID = "T-5"
	if err := p.Publish(context.Background(), env); err != nil {
		t.Fatalf("Publish: %v", err)
	}
	got := conf.calls[0].pub.Headers[HeaderTraceID]
	if got != "T-5" {
		t.Errorf("header %v=%q，期望逐字 T-5（与 Python contract.py 的 HEADER_TRACE_ID 同值同名）",
			HeaderTraceID, got)
	}
	if HeaderTraceID != "x-trace-id" {
		t.Errorf("header 名漂移：%q，两侧约定的字面量", HeaderTraceID)
	}
}

func TestPublishOmitsEmptyTrace(t *testing.T) {
	conf := &fakeConfirmer{acked: true}
	p := newTestPublisher(t, conf, time.Second)

	env := commandEnvelope() // TraceID 零值＝空串
	if err := p.Publish(context.Background(), env); err != nil {
		t.Fatalf("Publish: %v", err)
	}
	if _, present := conf.calls[0].pub.Headers[HeaderTraceID]; present {
		t.Error("空 trace 占了位：对面 grep 会把「没有链路」读成一条假链路（M-B2 靶）")
	}
}
