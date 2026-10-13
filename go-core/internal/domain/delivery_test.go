// N7 投递面单测：路由表、emit 的字段形状、Inbox 幂等分道与保留期清理。
//
// 钉七条口径（裁决七-6）：
//  1. **只有路由表里的事件**入 outbox，且判据是「权威态变更 + 有跨进程消费面」，
//     不是「版本号涨了」（N7-3）；
//  2. message_id 取事件行 PK，重试不换新 id（18 §10 / N7-4）；
//  3. correlation 优先级 ctx → generation_run_id → 事件行 id，causation 取入站消息
//     （N7-6）；
//  4. payload 空串只在**投递行**上归一成 "{}"，事件行不动（不改 N3–N6 既有断言）；
//  5. 18 §8 的幂等分道：首次真跑、重复跳过、瞬时回滚可重投、永久拒绝先留痕再 ACK；
//  6. 永久拒绝的留痕写不进时**不能** ACK（否则消息无痕消失）；
//  7. 保留期只清已完成的投递，且按 policy 的天数算截止时间（N7-11）。
//
// 真库行为（SKIP LOCKED 认领、租约到期重取、UNIQUE 撞车）在 internal/store 的集成测里验。
package domain

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

// 列宽事实源：db/schema/0003_outbox_messages.sql（由 alembic d9b1f3a5c7e9 导出）
// 与 inbox_messages（N2 DDL）。
const (
	outboxVarchar128  = 128
	outboxVarchar36   = 36
	inboxErrorCodeMax = 64
)

// admitAndOutbox 跑一次 Admit 并返回该用户这次的投递行（0 行是合法结果，用例自己判）。
func admitAndOutbox(t *testing.T, s *Service, c Candidate) []OutboxRow {
	t.Helper()
	if _, err := s.Admit(context.Background(), c); err != nil {
		t.Fatalf("Admit: %v", err)
	}
	store := s.db.(*fakeStore)
	return store.outbox
}

func TestAdmitEnqueuesOnlyRoutedEvents(t *testing.T) {
	cases := []struct {
		name       string
		build      func() Candidate
		mutate     func(*Candidate)
		wantEvents []string // 权威事件类型
		wantKeys   []string // 对应的 routing key；长度必须与 wantEvents 一致才说明「每条都入了表」
	}{
		{"fact 显式路径", okFact, nil, []string{"fact_admitted"}, []string{"fact.created"}},
		{"pattern 接纳", okPattern, nil,
			[]string{"pattern_admitted"}, []string{"pattern.created"}},
		{"insight 接纳", okInsight, nil,
			[]string{"insight_admitted"}, []string{"insight.created"}},
		{"拒绝候选不入表", okFact, func(c *Candidate) { c.EvidenceIDs = nil },
			[]string{"candidate_rejected"}, nil},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newFakeStore()
			s, _ := newTestService(f)
			c := tc.build()
			if tc.mutate != nil {
				tc.mutate(&c)
			}
			rows := admitAndOutbox(t, s, c)
			if len(f.events) != len(tc.wantEvents) {
				t.Fatalf("事件行数=%d，期望 %d：%+v", len(f.events), len(tc.wantEvents), f.events)
			}
			for i, want := range tc.wantEvents {
				if f.events[i].EventType != want {
					t.Errorf("第 %d 条事件类型=%q，期望 %q", i, f.events[i].EventType, want)
				}
			}
			if len(rows) != len(tc.wantKeys) {
				t.Fatalf("投递行数=%d，期望 %d：%+v", len(rows), len(tc.wantKeys), rows)
			}
			for i, key := range tc.wantKeys {
				if rows[i].RoutingKey != key {
					t.Errorf("routing key=%q，期望 %q", rows[i].RoutingKey, key)
				}
				// message_id 与事件行 PK 同源（N7-4）：这是「重试不换新 id」的实现点。
				if rows[i].MessageID != f.events[i].ID {
					t.Errorf("message_id=%q 不等于事件行 id %q", rows[i].MessageID, f.events[i].ID)
				}
				if rows[i].MessageType != tc.wantEvents[i] {
					t.Errorf("message_type=%q，应等于事件类型 %q",
						rows[i].MessageType, tc.wantEvents[i])
				}
				if rows[i].UserID != owner {
					t.Errorf("user_id=%q，purge 按这一列搬走（D-5）", rows[i].UserID)
				}
				if rows[i].Payload == "" {
					t.Error("投递 payload 为空串：下游按 JSON 解码拿不到合法文档")
				}
			}
		})
	}
}

func TestReactivateAndOutcomePathsEnqueue(t *testing.T) {
	t.Run("pattern 复活入表且路由到 pattern.updated", func(t *testing.T) {
		f := newFakeStore()
		f.patternID = "pat-live"
		s, _ := newTestService(f)
		rows := admitAndOutbox(t, s, okPattern())
		if len(rows) != 1 || rows[0].RoutingKey != "pattern.updated" {
			t.Fatalf("reactivate 应产出 1 行 pattern.updated：%+v", rows)
		}
		if rows[0].MessageType != "pattern_reactivated" {
			t.Errorf("message_type=%q", rows[0].MessageType)
		}
	})
	t.Run("outcome recorded 入表", func(t *testing.T) {
		f, s := outcomeStore(RefState{RefType: RefGoal, ID: "goal-1", Status: "active"})
		if _, err := s.RecordOutcome(context.Background(), OutcomeProposal{
			UserID: owner, Type: OutcomeGoalCompleted, Result: OutcomeResultBetter,
			Origin: OriginUserStatement, Description: "用户说这周跑完了三次",
			Targets: []TargetRef{{Type: RefGoal, ID: "goal-1"}}}); err != nil {
			t.Fatalf("RecordOutcome: %v", err)
		}
		want := map[string]string{"outcome_recorded": "outcome.recorded"}
		for _, event := range f.events {
			key, routed := want[event.EventType]
			if !routed {
				// target_completed 是「完成目标」的留痕，V3 里没有下游队列（N7-3 不进表）。
				if event.EventType == "target_completed" {
					continue
				}
				t.Fatalf("用例没预期的事件类型 %q", event.EventType)
			}
			found := false
			for _, row := range f.outbox {
				if row.MessageID == event.ID && row.RoutingKey == key {
					found = true
				}
			}
			if !found {
				t.Errorf("事件 %s(%s) 没有对应投递行：%+v", event.EventType, event.ID, f.outbox)
			}
		}
		if len(f.outbox) != 1 {
			t.Errorf("投递行数=%d，本用例只应有 outcome.recorded 一条：%+v", len(f.outbox), f.outbox)
		}
	})
	t.Run("用户表态入表且路由到 feedback.recorded", func(t *testing.T) {
		f, s := outcomeStore(RefState{RefType: RefInsight, ID: "ins-1",
			Status: "published", AnchorAt: windowEnd, HasTime: true})
		if _, err := s.RecordUserFeedback(context.Background(), FeedbackProposal{
			UserID: owner, Type: FeedbackReject, TargetType: RefInsight,
			TargetID: "ins-1", ResponseState: "rejected"}); err != nil {
			t.Fatalf("RecordUserFeedback: %v", err)
		}
		if len(f.outbox) != 1 || f.outbox[0].RoutingKey != "feedback.recorded" {
			t.Fatalf("表态应有且只有一条 feedback.recorded：%+v", f.outbox)
		}
	})
}

// TestRoutingTableIsClosed 把 N7-3 的两面都钉住：表里的九项逐字对得上，
// 表外的「已知但不投递」清单也必须保持不投递。
// 新增事件类型时这个用例不会自动知道，所以另有 TestEveryEmittedEventTypeIsClassified
// 从代码面扫一遍，逼每次加码都做判定。
func TestRoutingTableIsClosed(t *testing.T) {
	want := map[string]string{
		"fact_admitted":          "fact.created",
		"pattern_admitted":       "pattern.created",
		"pattern_reactivated":    "pattern.updated",
		"pattern_lifecycle":      "pattern.updated",
		"insight_admitted":       "insight.created",
		"insight_retracted":      "insight.retracted",
		"insight_expired":        "insight.expired",
		"outcome_recorded":       "outcome.recorded",
		"user_feedback_recorded": "feedback.recorded",
		"evidence_withdrawn":     "evidence.withdrawn",
	}
	if len(outboxRouting) != len(want) {
		t.Errorf("路由表有 %d 项，登记表有 %d 项：新增/删除路由必须同步改这里",
			len(outboxRouting), len(want))
	}
	for event, key := range want {
		got, ok := RoutingKeyFor(event)
		if !ok || got != key {
			t.Errorf("RoutingKeyFor(%q)=%q,%v，期望 %q,true", event, got, ok, key)
		}
	}
	// 列宽闸：VARCHAR(128)（0003 快照实测），超长会被数据库拒绝而不是被领域层判错。
	for event, key := range want {
		if len(event) > outboxVarchar128 || len(key) > outboxVarchar128 {
			t.Errorf("%s→%s 超出 VARCHAR(%d)", event, key, outboxVarchar128)
		}
		if key == "" || strings.ContainsAny(key, " ") {
			t.Errorf("routing key %q 不是合法 topic 串", key)
		}
	}
	// 明确不投递的：权威态没变（candidate_*/unknown_admitted）、或 V3 无下游消费面。
	notRouted := []string{"candidate_admitted", "candidate_rejected", "candidate_suppressed",
		"unknown_admitted", "outcome_candidate", "target_completed",
		"insight_revalidation_requested", "proactive_eligible", "proactive_sent",
		"proactive_cancelled", "proactive_blocked", "proactive_expired"}
	for _, event := range notRouted {
		if key, ok := RoutingKeyFor(event); ok {
			t.Errorf("%q 被判为不入表，却仍在路由表里（→%s）", event, key)
		}
	}
}

// TestConsumeIdempotentWritesOutboxWithCausation 覆盖 18 §8 的完整形状：
// 消费事务里的副作用写入同样要进投递面，且因果链接上入站消息。
func TestConsumeIdempotentWritesOutboxWithCausation(t *testing.T) {
	f := newFakeStore()
	s, _ := newTestService(f)
	msg := InboundMessage{MessageID: "in-1", MessageType: "ai.insight.draft",
		Consumer: "go.candidate.accept", CorrelationID: "corr-9"}
	res, err := s.ConsumeIdempotent(context.Background(), msg,
		func(ctx context.Context, st Store) error {
			return s.emit(ctx, st, EventRow{ID: "ev-1", UserID: owner,
				EventType: "insight_retracted", AggregateType: "insights",
				AggregateID: "ins-1", Payload: `{"status":"retracted"}`})
		})
	if err != nil {
		t.Fatalf("ConsumeIdempotent: %v", err)
	}
	if res.Status != ConsumeProcessed || !res.Ack {
		t.Errorf("结果=%+v，首次投递应 processed+ACK", res)
	}
	if len(f.outbox) != 1 {
		t.Fatalf("消费事务内的权威变更应产出 1 行投递：%+v", f.outbox)
	}
	row := f.outbox[0]
	if row.CausationID != "in-1" {
		t.Errorf("causation=%q，应指向入站 message_id（N7-6）", row.CausationID)
	}
	if row.CorrelationID != "corr-9" {
		t.Errorf("correlation=%q，应采用入站 envelope", row.CorrelationID)
	}
	if len(f.processed) != 1 || f.processed[0] != inboxKey("in-1", "go.candidate.accept") {
		t.Errorf("幂等行未落 processed：%v", f.processed)
	}
}

func TestConsumeIdempotentDuplicateSkipsSideEffects(t *testing.T) {
	f := newFakeStore()
	s, _ := newTestService(f)
	msg := InboundMessage{MessageID: "in-dup", MessageType: "pattern.updated",
		Consumer: "go.candidate.accept"}
	runs := 0
	fn := func(ctx context.Context, st Store) error {
		runs++
		return st.AppendEvent(ctx, EventRow{ID: "ev-dup", UserID: owner,
			EventType: "pattern_admitted", Payload: "{}"})
	}
	if _, err := s.ConsumeIdempotent(context.Background(), msg, fn); err != nil {
		t.Fatalf("首次消费: %v", err)
	}
	res, err := s.ConsumeIdempotent(context.Background(), msg, fn)
	if err != nil {
		t.Fatalf("重复消费: %v", err)
	}
	if res.Status != ConsumeDuplicate || !res.Ack {
		t.Errorf("结果=%+v，重复投递应 duplicate+ACK（14 §7 第 2 项）", res)
	}
	if runs != 1 {
		t.Errorf("业务回调跑了 %d 次，重复投递必须跳过副作用", runs)
	}
	if len(f.events) != 1 {
		t.Errorf("事件行数=%d，副作用被重复执行", len(f.events))
	}
	// 不同消费者消费同一条 message_id 不算重复（18 §7 的唯一键是一对）。
	other := msg
	other.Consumer = "python.ai.runtime"
	if _, err := s.ConsumeIdempotent(context.Background(), other, fn); err != nil {
		t.Fatalf("另一消费者: %v", err)
	}
	if runs != 2 {
		t.Errorf("另一消费者被当成重复跳过了（runs=%d）", runs)
	}
}

func TestConsumeIdempotentTransientRollsBackInboxRow(t *testing.T) {
	f := newFakeStore()
	s, _ := newTestService(f)
	msg := InboundMessage{MessageID: "in-tmp", MessageType: "pattern.created",
		Consumer: "go.candidate.accept"}
	res, err := s.ConsumeIdempotent(context.Background(), msg,
		func(ctx context.Context, st Store) error {
			return errors.New("db 抖动")
		})
	if err == nil {
		t.Fatal("瞬时失败必须把原因带回去，调用方才知道是哪一笔抖动")
	}
	if res.Status != ConsumeTransient || res.Ack {
		t.Errorf("结果=%+v，瞬时失败应 transient+NACK（14 §7）", res)
	}
	if !f.rolledBack {
		t.Error("事务没回滚")
	}
	// 18 §9 case 1：inbox 行随事务消失，所以重投能再次执行副作用。
	if _, ok := f.inbox[inboxKey(msg.MessageID, msg.Consumer)]; ok {
		t.Error("幂等行活过了回滚：真库里这条消息会被永久当成重复")
	}
	if len(f.failures) != 0 {
		t.Errorf("瞬时失败不该记永久拒绝留痕：%+v", f.failures)
	}
	// 重投一次应当真的重新执行副作用。
	ran := false
	retry, err := s.ConsumeIdempotent(context.Background(), msg,
		func(ctx context.Context, st Store) error {
			ran = true
			return st.AppendEvent(ctx, EventRow{ID: "ev-retry", UserID: owner,
				EventType: "pattern_admitted", Payload: "{}"})
		})
	if err != nil {
		t.Fatalf("重投: %v", err)
	}
	if !ran || retry.Status != ConsumeProcessed || !retry.Ack {
		t.Errorf("重投结果=%+v ran=%v，应重新执行并 processed+ACK", retry, ran)
	}
}

func TestConsumeIdempotentPendingDuplicateIsTransient(t *testing.T) {
	f := newFakeStore()
	s, _ := newTestService(f)
	msg := InboundMessage{MessageID: "in-pend", MessageType: "pattern.created",
		Consumer: "go.candidate.accept"}
	// 造一个「行已存在但还是 pending」的现场：另一笔消费事务尚未提交。
	f.inbox = map[string]InboxStatus{
		inboxKey(msg.MessageID, msg.Consumer): {Status: "pending", Found: true}}
	res, err := s.ConsumeIdempotent(context.Background(), msg,
		func(ctx context.Context, st Store) error {
			t.Error("重复投递不该执行副作用")
			return nil
		})
	if err != nil {
		t.Fatalf("ConsumeIdempotent: %v", err)
	}
	if res.Status != ConsumeTransient || res.Ack {
		t.Errorf("结果=%+v，pending 重复应按可重投处理（不能猜另一笔的命运）", res)
	}
}

func TestConsumeIdempotentPermanentRejectionRecordsAndAcks(t *testing.T) {
	f := newFakeStore()
	s, _ := newTestService(f)
	msg := InboundMessage{MessageID: "in-bad", MessageType: "ai.insight.draft",
		Consumer: "go.candidate.accept"}
	res, err := s.ConsumeIdempotent(context.Background(), msg,
		func(ctx context.Context, st Store) error {
			// 14 §7 Reject/DLQ 的第一类：非法 payload。
			return PermanentRejection{Code: "invalid_payload", Detail: "protobuf 解不开"}
		})
	if !errors.Is(err, ErrPermanentMessage) {
		t.Fatalf("错误=%v，应能被 errors.Is 判成永久拒绝", err)
	}
	if res.Status != ConsumeRejected || !res.Ack {
		t.Errorf("结果=%+v，永久拒绝应 ACK（14 §7 第 3 项）", res)
	}
	if len(f.failures) != 1 || f.failures[0].ErrorCode != "invalid_payload" {
		t.Fatalf("永久拒绝留痕缺失：%+v", f.failures)
	}
	if f.failures[0].MessageID != msg.MessageID || f.failures[0].Consumer != msg.Consumer {
		t.Errorf("留痕键不对：%+v", f.failures[0])
	}
	// 留痕之后重投：必须仍被判成「已按策略记录永久拒绝」而丢弃（17 §9 不阻塞队列）。
	again, err := s.ConsumeIdempotent(context.Background(), msg,
		func(ctx context.Context, st Store) error {
			t.Error("已判永久拒绝的消息不该再执行副作用")
			return nil
		})
	if err != nil {
		t.Fatalf("重投: %v", err)
	}
	if again.Status != ConsumeRejected || !again.Ack {
		t.Errorf("重投结果=%+v", again)
	}
}

func TestPermanentRejectionErrorCodeFitsColumn(t *testing.T) {
	f := newFakeStore()
	s, _ := newTestService(f)
	// error_code 是 VARCHAR(64)（N2 DDL 实测）；超长必须截断而不是让它撞库。
	long := strings.Repeat("x", 200)
	res, err := s.ConsumeIdempotent(context.Background(), InboundMessage{
		MessageID: "in-long", MessageType: "x", Consumer: "c"},
		func(ctx context.Context, st Store) error {
			return PermanentRejection{Code: long}
		})
	// 永久拒绝也带错误回去（供日志/指标），但 Ack 必须是 true——两者一起看才是结论。
	if err == nil || res.Status != ConsumeRejected || !res.Ack {
		t.Fatalf("结果=%+v err=%v，永久拒绝应留痕并 ACK 且带原因", res, err)
	}
	if got := len(f.failures[0].ErrorCode); got != inboxErrorCodeMax {
		t.Errorf("error_code 长度=%d，应截断到 VARCHAR(%d)", got, inboxErrorCodeMax)
	}
	// 空码回落到固定码，留痕不能是空字串。
	res2, err := s.ConsumeIdempotent(context.Background(), InboundMessage{
		MessageID: "in-empty", MessageType: "x", Consumer: "c"},
		func(ctx context.Context, st Store) error {
			return PermanentRejection{}
		})
	if err == nil || res2.Status != ConsumeRejected {
		t.Fatalf("空码用例=%+v err=%v", res2, err)
	}
	if f.failures[1].ErrorCode != "permanent_rejection" {
		t.Errorf("error_code=%q", f.failures[1].ErrorCode)
	}
}

func TestConsumeIdempotentUnrecordableRejectionStaysUnacked(t *testing.T) {
	f := newFakeStore()
	f.failureErr = errors.New("库不可用")
	s, _ := newTestService(f)
	res, err := s.ConsumeIdempotent(context.Background(), InboundMessage{
		MessageID: "in-nb", MessageType: "x", Consumer: "c"},
		func(ctx context.Context, st Store) error {
			return PermanentRejection{Code: "unsupported_schema"}
		})
	if err == nil {
		t.Fatal("留痕失败要带错误回去，调用方才知道这笔没记成")
	}
	// 关键一条：写不进去就**不能** ACK，否则消息无痕消失。
	if res.Status != ConsumeTransient || res.Ack {
		t.Errorf("结果=%+v，留痕失败必须退回可重投", res)
	}
}

func TestConsumeIdempotentValidatesMessage(t *testing.T) {
	f := newFakeStore()
	s, _ := newTestService(f)
	noFn := func(ctx context.Context, st Store) error { return nil }
	cases := []struct {
		name string
		msg  InboundMessage
	}{
		{"缺 message_id", InboundMessage{Consumer: "c"}},
		{"缺 consumer", InboundMessage{MessageID: "m"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := s.ConsumeIdempotent(context.Background(), tc.msg, noFn); err == nil {
				t.Error("18 §7 的唯一键两面缺一就必须报错，不能当普通消息处理")
			}
		})
	}
	if _, err := s.ConsumeIdempotent(context.Background(),
		InboundMessage{MessageID: "m", Consumer: "c"}, nil); err == nil {
		t.Error("没有业务回调的消费没有意义，必须报错")
	}
}

func TestEmitCorrelationPriority(t *testing.T) {
	f := newFakeStore()
	s, _ := newTestService(f)
	event := EventRow{ID: "ev-c", UserID: owner, GenerationRunID: "run-7"}
	// ctx 注入优先（N7-6）。
	ctx := WithEnvelope(context.Background(), Envelope{CorrelationID: "corr-1",
		CausationID: "in-0"})
	if got := s.correlationFor(ctx, event); got != "corr-1" {
		t.Errorf("correlation=%q，ctx 注入应优先于 generation_run_id", got)
	}
	if got := causationFor(ctx); got != "in-0" {
		t.Errorf("causation=%q", got)
	}
	// 其次 generation_run_id（一次 LLM 生成的整条链天然同相关）。
	if got := s.correlationFor(context.Background(), event); got != "run-7" {
		t.Errorf("correlation=%q，期望 run-7", got)
	}
	// 都没有时自指事件行 id：链头，不是错误。
	head := event
	head.GenerationRunID = ""
	if got := s.correlationFor(context.Background(), head); got != "ev-c" {
		t.Errorf("correlation=%q，期望事件行 id", got)
	}
	if got := causationFor(context.Background()); got != "" {
		t.Errorf("causation=%q，链头应是空串", got)
	}
	// 空串 envelope 不该盖掉 generation_run_id。
	blank := WithEnvelope(context.Background(), Envelope{})
	if got := s.correlationFor(blank, event); got != "run-7" {
		t.Errorf("correlation=%q，空 envelope 不该被当成注入", got)
	}
}

func TestEmitKeepsEventPayloadAndNormalizesDeliveryPayloadOnly(t *testing.T) {
	f := newFakeStore()
	s, _ := newTestService(f)
	err := s.db.InTx(context.Background(), func(ctx context.Context, st Store) error {
		// insight_expired 在 N4 的重算路径里就是不写 Payload（service_evidence.go）。
		return s.emit(ctx, st, EventRow{ID: "ev-p", UserID: owner,
			EventType: "insight_expired", AggregateType: "insights", AggregateID: "ins-1"})
	})
	if err != nil {
		t.Fatalf("emit: %v", err)
	}
	if len(f.events) != 1 || f.events[0].Payload != "" {
		t.Errorf("事件行 Payload 被改了：%+v", f.events)
	}
	if len(f.outbox) != 1 || f.outbox[0].Payload != "{}" {
		t.Errorf("投递 payload=%+v，空串应归一成合法 JSON", f.outbox)
	}
	if f.outbox[0].RoutingKey != "insight.expired" {
		t.Errorf("routing key=%q", f.outbox[0].RoutingKey)
	}
}

func TestEmitFieldWidths(t *testing.T) {
	f := newFakeStore()
	s, _ := newTestService(f)
	err := s.db.InTx(context.Background(), func(ctx context.Context, st Store) error {
		return s.emit(ctx, st, EventRow{ID: "ev-w", UserID: strings.Repeat("u", outboxVarchar36),
			EventType: "pattern_lifecycle", AggregateType: "patterns", AggregateID: "pat-1",
			Payload: `{"from":"supported"}`, GenerationRunID: strings.Repeat("r", 36)})
	})
	if err != nil {
		t.Fatalf("emit: %v", err)
	}
	row := f.outbox[0]
	for name, value := range map[string]string{"id": row.ID, "user_id": row.UserID,
		"message_id": row.MessageID, "message_type": row.MessageType,
		"routing_key": row.RoutingKey, "correlation_id": row.CorrelationID,
		"causation_id": row.CausationID} {
		if len(value) > outboxVarchar128 {
			t.Errorf("%s 长 %d，超出列宽", name, len(value))
		}
	}
	if row.SchemaVersion != DefaultPolicy().Delivery.SchemaVersion {
		t.Errorf("schema_version=%d，应取策略值", row.SchemaVersion)
	}
}

func TestEmitOutOfboxFailureRollsBack(t *testing.T) {
	f := newFakeStore()
	f.outboxErr = errors.New("outbox 写入失败")
	s, _ := newTestService(f)
	_, err := s.Admit(context.Background(), okFact())
	if err == nil {
		t.Fatal("投递行写失败必须让整笔回滚：权威态已改而下游永远收不到，比拒绝这条候选更糟")
	}
	if !f.rolledBack {
		t.Error("没回滚")
	}
	// 真库里权威行随之消失；fake 不回滚权威行（N3–N6 的用例依赖能看到失败点），
	// 所以「同一事务」这一条由 internal/store 的集成测证（见 #28 的用例）。
	if len(f.events) != 1 {
		t.Errorf("事件行数=%d，emit 应先写事件再写投递行", len(f.events))
	}
}

func TestCleanupDeliveryRecordsUsesRetentionPolicy(t *testing.T) {
	f := newFakeStore()
	f.outboxDeleted, f.inboxDeleted = 7, 3
	s, _ := newTestService(f)
	got, err := s.CleanupDeliveryRecords(context.Background())
	if err != nil {
		t.Fatalf("CleanupDeliveryRecords: %v", err)
	}
	policy := DefaultPolicy().Delivery
	wantOutbox := windowEnd.AddDate(0, 0, -policy.OutboxRetentionDays)
	wantInbox := windowEnd.AddDate(0, 0, -policy.InboxRetentionDays)
	if !got.OutboxBefore.Equal(wantOutbox) || !got.InboxBefore.Equal(wantInbox) {
		t.Errorf("截止时间 outbox=%v inbox=%v，期望 %v / %v",
			got.OutboxBefore, got.InboxBefore, wantOutbox, wantInbox)
	}
	if got.OutboxDeleted != 7 || got.InboxDeleted != 3 {
		t.Errorf("计数未原样搬回：%+v", got)
	}
	// inbox 保留期必须不短于 outbox：删早了旧重投就不再被识别为重复（18 §13）。
	if policy.InboxRetentionDays < policy.OutboxRetentionDays {
		t.Errorf("inbox %d 天 < outbox %d 天", policy.InboxRetentionDays,
			policy.OutboxRetentionDays)
	}
	if !f.cleaned {
		t.Error("清理没走事务")
	}
}

func TestDeliveryPolicyRetryLadder(t *testing.T) {
	policy := DefaultDeliveryPolicy()
	cases := []struct {
		attempt int
		want    time.Duration
	}{
		{1, 30 * time.Second}, // 14 §8 retry.30s
		{2, 2 * time.Minute},  // 14 §8 retry.2m
		{3, 10 * time.Minute}, // 14 §8 retry.10m
		{4, 10 * time.Minute}, // 阶梯走完沿用最后一档
		{99, 10 * time.Minute},
		{0, 30 * time.Second}, // 防御：不该出现 0 次
	}
	for _, tc := range cases {
		if got := policy.RetryDelay(tc.attempt); got != tc.want {
			t.Errorf("RetryDelay(%d)=%v，期望 %v", tc.attempt, got, tc.want)
		}
	}
	if len(policy.RetryLadderSeconds) != 3 {
		t.Errorf("阶梯=%v，14 §8 是三级", policy.RetryLadderSeconds)
	}
	if policy.MaxAttempts <= len(policy.RetryLadderSeconds) {
		t.Errorf("MaxAttempts=%d 必须大于阶梯级数，否则死信永远轮不到最后一档退避",
			policy.MaxAttempts)
	}
	if empty := (DeliveryPolicy{}); empty.RetryDelay(1) != 0 {
		t.Error("零值策略不该凭空造出退避时长")
	}
	if policy.OutboxLease() <= 0 {
		t.Errorf("租约=%v，崩溃 worker 的孤儿行回收依赖它（N7-2）", policy.OutboxLease())
	}
	// schema_version 有 CHECK (>=1)：0 会被数据库拒绝（0003 快照实测）。
	if policy.SchemaVersion < 1 {
		t.Errorf("schema_version=%d，CHECK (schema_version >= 1) 会拒", policy.SchemaVersion)
	}
	if policy.BatchSize <= 0 {
		t.Errorf("BatchSize=%d", policy.BatchSize)
	}
}

func TestDeliveryPolicyReadsEnv(t *testing.T) {
	t.Setenv("CLOSEN_OUTBOX_BATCH_SIZE", "7")
	t.Setenv("CLOSEN_OUTBOX_MAX_ATTEMPTS", "3")
	t.Setenv("CLOSEN_OUTBOX_RETRY_SECONDS", "5,15")
	t.Setenv("CLOSEN_OUTBOX_RETENTION_DAYS", "1")
	t.Setenv("CLOSEN_INBOX_RETENTION_DAYS", "2")
	policy := DefaultDeliveryPolicy()
	if policy.BatchSize != 7 || policy.MaxAttempts != 3 {
		t.Errorf("env 没生效：%+v", policy)
	}
	// 阶梯长度是配置的一部分：只留两级是合法收敛，不该回退整表（对比 envFloats）。
	if got := policy.RetryLadderSeconds; len(got) != 2 || got[0] != 5 || got[1] != 15 {
		t.Errorf("阶梯=%v", got)
	}
	if policy.RetryDelay(3) != 15*time.Second {
		t.Errorf("超出阶梯应沿用最后一档，得 %v", policy.RetryDelay(3))
	}
	if policy.OutboxRetentionDays != 1 || policy.InboxRetentionDays != 2 {
		t.Errorf("保留期=%d/%d", policy.OutboxRetentionDays, policy.InboxRetentionDays)
	}
	// 非法值整表回退，避免出现半新半旧的退避节奏。
	t.Setenv("CLOSEN_OUTBOX_RETRY_SECONDS", "5,abc")
	if got := DefaultDeliveryPolicy().RetryLadderSeconds; len(got) != 3 || got[0] != 30 {
		t.Errorf("非法阶梯段应整表回退，得 %v", got)
	}
}
