// R4-③ 命令面单测：`ai.*.requested` 怎么变成一条 outbox 行，以及它**不许**走进十条事件路由。
//
// 钉五条口径：
//  1. 命令的 message_type／routing key 由 `domain.AITaskKind` 一处生成，且与
//     `delivery.go:37-46` 那十条**两两不相交**（命令进 `closenai.ai.tasks`，事件进
//     `closenai.events`；并进一套就等于把「权威模型内容变了」那条进门判据反着用）；
//  2. 命令行走 `EnqueueOutbox`，复用 outbox 的租约/退避/死信与 confirm 后置 published；
//  3. 命令**不写** personal_model_events 行（它不动权威态）；
//  4. message_id 是调用方给的稳定 OperationID（18 §10），重试不换；
//  5. 入参非法（未知 kind／缺 user_id／缺 operation_id／payload 不是 JSON／超列宽）一律
//     在入队前拒，一条都不写。
//
// 真库行为（UNIQUE 撞车、SKIP LOCKED 认领）在 internal/store 的集成测里验，这里用 fake。
package domain

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func okTaskRequest() AITaskRequest {
	return AITaskRequest{
		Kind: AITaskObservation, UserID: "u-1", OperationID: "op-obs-1",
		Payload: `{"source_ids":["s1"]}`,
	}
}

// TestCommandSurfaceIsDisjointFromEventSurface 是 M2 的靶子：
// 有人把 `ai_*_requested` 加进 outboxRouting（或把命令键写成某条事件键）就红。
func TestCommandSurfaceIsDisjointFromEventSurface(t *testing.T) {
	eventKeys := map[string]bool{}
	for _, key := range outboxRouting {
		eventKeys[key] = true
	}
	eventTypes := map[string]bool{}
	for eventType := range outboxRouting {
		eventTypes[eventType] = true
	}
	if len(outboxRouting) != 10 {
		t.Fatalf("十条事件路由是既有契约（N7-3），现在查到 %d 条", len(outboxRouting))
	}
	for _, kind := range AITaskKinds {
		if eventKeys[kind.RoutingKey()] {
			t.Errorf("命令路由键 %s 撞进了十条事件路由 ⇒ 一条命令会被投进 closenai.events", kind.RoutingKey())
		}
		if eventTypes[kind.MessageType()] {
			t.Errorf("命令 message_type %s 撞进了事件类型表 ⇒ 命令被当成权威态变更", kind.MessageType())
		}
		// 反向复判：RoutingKeyFor 对命令的 message_type 必须说「不入表」。
		if key, ok := RoutingKeyFor(kind.MessageType()); ok {
			t.Errorf("RoutingKeyFor(%q)=%q, ok=true ⇒ 命令行会被 emit 投进事件面", kind.MessageType(), key)
		}
		if !IsCommandRoutingKey(kind.RoutingKey()) {
			t.Errorf("IsCommandRoutingKey(%q) 应判真", kind.RoutingKey())
		}
	}
	// 十条事件键一律不许被认成命令，否则消费端会把事件当命令处理。
	for eventType, key := range outboxRouting {
		if IsCommandRoutingKey(key) {
			t.Errorf("事件 %s 的键 %q 被认成命令路由", eventType, key)
		}
	}
}

func TestCommandKindNaming(t *testing.T) {
	cases := []struct {
		kind     AITaskKind
		wantKey  string
		wantType string
	}{
		{AITaskObservation, "ai.observation.requested", "ai_observation_requested"},
		{AITaskPattern, "ai.pattern.requested", "ai_pattern_requested"},
		{AITaskInsight, "ai.insight.requested", "ai_insight_requested"},
		{AITaskProactive, "ai.proactive.requested", "ai_proactive_requested"},
	}
	for _, tc := range cases {
		if got := tc.kind.RoutingKey(); got != tc.wantKey {
			t.Errorf("RoutingKey=%q，期望 %q", got, tc.wantKey)
		}
		if got := tc.kind.MessageType(); got != tc.wantType {
			t.Errorf("MessageType=%q，期望 %q", got, tc.wantType)
		}
	}
}

// TestRequestAITaskWritesOneOutboxRowAndNoEvent 钉口径 2/3/4：一行投递、零事件行、字段逐字。
func TestRequestAITaskWritesOneOutboxRowAndNoEvent(t *testing.T) {
	f := newFakeStore()
	s, _ := newTestService(f)
	if err := s.RequestAITask(context.Background(), f, okTaskRequest()); err != nil {
		t.Fatalf("RequestAITask: %v", err)
	}
	if len(f.events) != 0 {
		t.Errorf("命令写了 %d 条权威事件行 ⇒ 命令被当成「模型内容变了」（判据见 delivery.go:31-36）：%+v",
			len(f.events), f.events)
	}
	if len(f.outbox) != 1 {
		t.Fatalf("投递行数=%d，期望 1：%+v", len(f.outbox), f.outbox)
	}
	row := f.outbox[0]
	if row.MessageID != "op-obs-1" {
		t.Errorf("message_id=%q，期望调用方的 operation_id（18 §10：稳定边界，重试不换）", row.MessageID)
	}
	if row.MessageType != "ai_observation_requested" {
		t.Errorf("message_type=%q", row.MessageType)
	}
	if row.RoutingKey != "ai.observation.requested" {
		t.Errorf("routing_key=%q", row.RoutingKey)
	}
	if row.UserID != "u-1" {
		t.Errorf("user_id=%q", row.UserID)
	}
	if row.SchemaVersion != outboxSchemaVersion {
		t.Errorf("schema_version=%d，期望策略默认 %d", row.SchemaVersion, outboxSchemaVersion)
	}
	if row.ID == "" {
		t.Error("投递行主键为空")
	}
	if row.Payload != `{"source_ids":["s1"]}` {
		t.Errorf("payload=%q，期望原样搬过去", row.Payload)
	}
	// 链头：没有 ctx envelope 时，correlation 自指 operation_id，causation 留空。
	if row.CorrelationID != "op-obs-1" {
		t.Errorf("correlation_id=%q，链头应自指 operation_id", row.CorrelationID)
	}
	if row.CausationID != "" {
		t.Errorf("causation_id=%q，链头应为空", row.CausationID)
	}
}

// TestRequestAITaskEnvelopeAndExplicitCorrelation 钉相关链的三级优先级（与事件行版同序）。
func TestRequestAITaskEnvelopeAndExplicitCorrelation(t *testing.T) {
	f := newFakeStore()
	s, _ := newTestService(f)
	req := okTaskRequest()
	ctx := WithEnvelope(context.Background(), Envelope{CorrelationID: "corr-1", CausationID: "in-0"})
	if err := s.RequestAITask(ctx, f, req); err != nil {
		t.Fatalf("ctx envelope：%v", err)
	}
	row := f.outbox[0]
	if row.CorrelationID != "corr-1" || row.CausationID != "in-0" {
		t.Errorf("envelope 没带上：correlation=%q causation=%q", row.CorrelationID, row.CausationID)
	}

	f2 := newFakeStore()
	s2, _ := newTestService(f2)
	explicit := okTaskRequest()
	explicit.CorrelationID = "run-9"
	if err := s2.RequestAITask(ctx, f2, explicit); err != nil {
		t.Fatalf("显式 correlation：%v", err)
	}
	got := f2.outbox[0]
	if got.CorrelationID != "run-9" {
		t.Errorf("显式 CorrelationID 应压过 ctx 注入，实测 %q", got.CorrelationID)
	}
	if got.CausationID != "in-0" {
		t.Errorf("causation 仍应取入站消息，实测 %q", got.CausationID)
	}
}

// TestRequestAITaskEmptyPayloadNormalised 与 emit 同口径：空串在投递行上归一成 "{}"。
func TestRequestAITaskEmptyPayloadNormalised(t *testing.T) {
	f := newFakeStore()
	s, _ := newTestService(f)
	req := okTaskRequest()
	req.Payload = ""
	if err := s.RequestAITask(context.Background(), f, req); err != nil {
		t.Fatalf("RequestAITask: %v", err)
	}
	if got := f.outbox[0].Payload; got != "{}" {
		t.Errorf("payload=%q，期望 \"{}\"（下游按 JSON 解码，空串不是合法 JSON）", got)
	}
}

func TestRequestAITaskRejectsBadInput(t *testing.T) {
	cases := []struct {
		name   string
		mutate func(*AITaskRequest)
		wantIs error
		wantIn string
	}{
		{"未知任务类型", func(r *AITaskRequest) { r.Kind = AITaskKind("summarize") },
			ErrUnknownAITaskKind, ""},
		{"空任务类型", func(r *AITaskRequest) { r.Kind = "" }, ErrUnknownAITaskKind, ""},
		{"缺 user_id", func(r *AITaskRequest) { r.UserID = "" }, nil, "user_id"},
		{"user_id 超列宽", func(r *AITaskRequest) { r.UserID = strings.Repeat("u", outboxVarchar36+1) },
			nil, "36"},
		{"缺 operation_id", func(r *AITaskRequest) { r.OperationID = "" }, nil, "message_id"},
		{"operation_id 超列宽",
			func(r *AITaskRequest) { r.OperationID = strings.Repeat("o", outboxVarchar128+1) }, nil, "128"},
		{"payload 不是 JSON", func(r *AITaskRequest) { r.Payload = `{"a":` }, nil, "JSON"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newFakeStore()
			s, _ := newTestService(f)
			req := okTaskRequest()
			tc.mutate(&req)
			err := s.RequestAITask(context.Background(), f, req)
			if err == nil {
				t.Fatalf("非法入参却入了队：%+v", f.outbox)
			}
			if tc.wantIs != nil && !errors.Is(err, tc.wantIs) {
				t.Errorf("错误 %v 不是 %v 的分类（调用方要按它分 4xx/5xx）", err, tc.wantIs)
			}
			if tc.wantIn != "" && !strings.Contains(err.Error(), tc.wantIn) {
				t.Errorf("错误 %q 里读不出 %q", err, tc.wantIn)
			}
			// 关键：拒在入队之前，一条都不写。
			if len(f.outbox) != 0 {
				t.Errorf("校验失败仍写了 %d 行投递：%+v", len(f.outbox), f.outbox)
			}
		})
	}
}

// TestRequestAITaskPropagatesStoreError：仓储写不进时错误照实传出，让调用方事务回滚。
// 吞掉它就等于「命令没入队、决策却 COMMIT 了」⇒ 用户永远等不到那条洞察。
func TestRequestAITaskPropagatesStoreError(t *testing.T) {
	f := newFakeStore()
	s, _ := newTestService(f)
	f.outboxErr = errors.New("fake: 唯一键冲突")
	err := s.RequestAITask(context.Background(), f, okTaskRequest())
	if err == nil || !strings.Contains(err.Error(), "唯一键冲突") {
		t.Fatalf("入队错误没传出来：%v", err)
	}
}

func TestIsCommandRoutingKeyRejectsUnknownAndWildcard(t *testing.T) {
	for _, key := range []string{"ai.observation.done", "ai..requested", "candidate.*", "fact.created", ""} {
		if IsCommandRoutingKey(key) {
			t.Errorf("IsCommandRoutingKey(%q) 判真了", key)
		}
	}
}
