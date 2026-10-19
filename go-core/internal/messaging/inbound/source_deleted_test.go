// 事件面（T2-06）的解码闸。
//
// 判据的形状与候选面同一条：一张「一件非法 ⇒ 一个码」的表，加两条正面证人
// （合法字节映射成什么、进门以后调的是哪扇门）。为什么这条链值得单独一个文件：
// 它进的是**另一个消费者**（`go.core.source`），少一格判据的现网表现不是报错，
// 而是「删除事件被吃了但账没改」或「一条毒消息在 30s/2m/10m 上撞三次库」。
package inbound

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// eventDeliveryFor 造一条其余面都合法的删除事件交付。
//
// 体用 snake_case 会被 `DisallowUnknownFields` 抓住，所以这里写的就是线形本身
// （lowerCamelCase，与候选面 protojson 同一套风格，见 sourceDeletedBody 的注释）。
func eventDeliveryFor(body string) Delivery {
	return Delivery{
		RoutingKey:  SourceDeletedRoutingKey,
		MessageType: SourceDeletedMessageType,
		MessageID:   "sr-del-1",
		ContentType: ContentTypeJSON,
		Headers: map[string]any{
			HeaderSchemaVersion: SupportedSchemaVersion,
			HeaderUserID:        "user-1",
		},
		Body: []byte(body),
	}
}

const goodEventBody = `{"schemaVersion":"1","userId":"user-1",` +
	`"sourceTable":"memories","sourceId":"mem-9"}`

func TestDecodeEventAcceptsTheContractShape(t *testing.T) {
	call, bad := DecodeEvent(eventDeliveryFor(goodEventBody), DefaultMaxBodyBytes)
	if bad != nil {
		t.Fatalf("合法字节被拒：%+v", bad)
	}
	dm := newFakeDomain()
	dm.wantConsumer = SourceDeletedConsumer
	if err := call(context.Background(), dm); err != nil {
		t.Fatalf("InvalidateSource: %v", err)
	}
	if len(dm.sources) != 1 {
		t.Fatalf("领域入口被调了 %d 次，应为 1", len(dm.sources))
	}
	want := domain.SourcePointer{UserID: "user-1", SourceTable: "memories", SourceID: "mem-9"}
	if dm.sources[0] != want {
		t.Errorf("指针=%+v，期望 %+v ⇒ 归属或来源行传丢了", dm.sources[0], want)
	}
}

// TestDecodeEventBodyIsTheOnlyPointerSource 钉住「体里那三格都进了指针」这一件事。
//
// 为什么不只判 UserID：`SourceID` 拼错或漏传时，库里那一行不会被命中，
// `InvalidateSource` 返回「没有这条登记行」，Handle 把它当**成功**ack 掉——
// 于是「传播没发生」与「本来就没有可传播的东西」在账上长得一模一样。
func TestDecodeEventBodyIsTheOnlyPointerSource(t *testing.T) {
	for _, tc := range []struct{ name, body, wantTable, wantID string }{
		{"逐字", goodEventBody, "memories", "mem-9"},
		{"两侧空白被去掉", `{"userId":" user-1 ","sourceTable":" memories ","sourceId":" mem-9 "}`,
			"memories", "mem-9"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			dm := newFakeDomain()
			if _, bad := DecodeEvent(eventDeliveryFor(tc.body), DefaultMaxBodyBytes); bad != nil {
				t.Fatalf("被拒：%+v", bad)
			}
			call, _ := DecodeEvent(eventDeliveryFor(tc.body), DefaultMaxBodyBytes)
			if err := call(context.Background(), dm); err != nil {
				t.Fatalf("call: %v", err)
			}
			p := dm.sources[0]
			if p.SourceTable != tc.wantTable || p.SourceID != tc.wantID {
				t.Errorf("指针=(%s,%s)，期望 (%s,%s)", p.SourceTable, p.SourceID, tc.wantTable, tc.wantID)
			}
		})
	}
}

func TestDecodeEventRejectsContractViolations(t *testing.T) {
	for _, tc := range []struct {
		name   string
		mut    func(*Delivery)
		reason string
	}{
		{"候选面的键不许从事件面进门", func(d *Delivery) {
			d.RoutingKey = "candidate.pattern"
			d.MessageType = "candidate_pattern"
		}, ReasonUnknownRoutingKey},
		{"命令面的键不归属这里", func(d *Delivery) {
			d.RoutingKey = "ai.pattern.requested"
			d.MessageType = "ai_pattern_requested"
		}, ReasonUnknownRoutingKey},
		{"十条领域事件的键没有解码分支", func(d *Delivery) {
			d.RoutingKey = "evidence.withdrawn"
			d.MessageType = "evidence_withdrawn"
		}, ReasonUnknownRoutingKey},
		{"content_type 不是 JSON", func(d *Delivery) {
			d.ContentType = "application/protobuf"
		}, ReasonContentType},
		{"body 超上限", func(d *Delivery) {
			d.Body = []byte(strings.Repeat("x", DefaultMaxBodyBytes+1))
		}, ReasonBodyTooLarge},
		{"message_type 与键不同源", func(d *Delivery) { d.MessageType = "source_delete" }, ReasonMessageTypeMismatch},
		{"schema 版本不认识", func(d *Delivery) {
			d.Headers[HeaderSchemaVersion] = "2"
		}, ReasonSchemaVersion},
		{"体内版本与 header 不一致（两侧一起漂的那格）", func(d *Delivery) {
			d.Body = []byte(`{"schemaVersion":"2","userId":"user-1","sourceTable":"memories","sourceId":"m"}`)
		}, ReasonSchemaVersion},
		{"header 缺 user", func(d *Delivery) { delete(d.Headers, HeaderUserID) }, ReasonMissingUserID},
		{"header 的 user 是空白", func(d *Delivery) { d.Headers[HeaderUserID] = "  " }, ReasonMissingUserID},
		{"体不是 JSON", func(d *Delivery) { d.Body = []byte("plain text") }, ReasonNotJSON},
		{"体里多了一个没人认的键", func(d *Delivery) {
			d.Body = []byte(`{"userId":"user-1","sourceTable":"memories","sourceId":"m","extra":1}`)
		}, ReasonNotJSON},
		{"snake_case 的体（对面拼错风格）", func(d *Delivery) {
			d.Body = []byte(`{"user_id":"user-1","source_table":"memories","source_id":"m"}`)
		}, ReasonNotJSON},
		{"字段类型不对", func(d *Delivery) {
			d.Body = []byte(`{"userId":"user-1","sourceTable":"memories","sourceId":42}`)
		}, ReasonPayloadInvalid},
		{"缺 sourceId", func(d *Delivery) {
			d.Body = []byte(`{"userId":"user-1","sourceTable":"memories"}`)
		}, ReasonPayloadInvalid},
		{"缺 sourceTable", func(d *Delivery) {
			d.Body = []byte(`{"userId":"user-1","sourceId":"mem-9"}`)
		}, ReasonPayloadInvalid},
		{"指针全空", func(d *Delivery) { d.Body = []byte(`{}`) }, ReasonPayloadInvalid},
		// 这一格是那条越权删除的**契约级**靶：头和体各说一个用户。
		// 库里的谓词带 user_id（第二道），但这里必须先把「生产者自己就不一致」拦下——
		// 放过去就等于让「按头查库」替体里那个人销账。
		{"体的 userId 与头不一致", func(d *Delivery) {
			d.Body = []byte(`{"userId":"victim","sourceTable":"memories","sourceId":"mem-9"}`)
		}, ReasonSourceUserIDMismatch},
	} {
		t.Run(tc.name, func(t *testing.T) {
			d := eventDeliveryFor(goodEventBody)
			tc.mut(&d)
			call, bad := DecodeEvent(d, DefaultMaxBodyBytes)
			if bad == nil {
				t.Fatalf("期望 %q，实得一条放行（call=%v）", tc.reason, call != nil)
			}
			if bad.Code != tc.reason {
				t.Errorf("reason=%q，期望 %q；detail=%s", bad.Code, tc.reason, bad.Detail)
			}
			if bad.Detail == "" {
				t.Error("永久拒绝没带 detail ⇒ DLQ 里的值班答不出为什么")
			}
		})
	}
}

// TestUnknownEventKeyBeatsEveryOtherGate 钉住**判序**：键不在表里时，
// 即使其余面全非法（超长、类型错、缺头），报的也必须是 unknown_routing_key。
// 这条与候选面那条同判据，但那是另一张表、另一个消费者，必须各自有证人。
func TestUnknownEventKeyBeatsEveryOtherGate(t *testing.T) {
	d := eventDeliveryFor(strings.Repeat("x", DefaultMaxBodyBytes+1))
	d.RoutingKey = "source.removed"
	d.MessageType = "source_removed"
	d.ContentType = "text/plain"
	delete(d.Headers, HeaderUserID)
	if _, bad := DecodeEvent(d, DefaultMaxBodyBytes); bad == nil || bad.Code != ReasonUnknownRoutingKey {
		t.Fatalf("期望 unknown_routing_key 优先，实得 %+v", bad)
	}
}

// TestEventFaceNeverUsesTheCandidateEntryTable 是两张表分家的闸。
//
// 并表＝「加一个键看起来不需要加认领者」，而这条链上少一个认领者的表现是静默丢弃。
func TestEventFaceNeverUsesTheCandidateEntryTable(t *testing.T) {
	if len(EventEntries) != 1 {
		t.Fatalf("EventEntries 有 %d 条，本批只准 source.deleted 一条：%v", len(EventEntries), EventEntries)
	}
	for key := range EventEntries {
		if _, dup := CandidateEntries[key]; dup {
			t.Errorf("键 %q 同时在两张表里 ⇒ 两个消费者都会认领它，第二条永远撞幂等位", key)
		}
		if _, ok := EventEntries[key]; !ok {
			t.Errorf("表里查不到 %q（本条自己不该出现）", key)
		}
	}
	for key, entry := range CandidateEntries {
		if entry == EntryInvalidateSource {
			t.Errorf("候选面的键 %q 指向了事件面那扇门", key)
		}
	}
}

// TestEventCallTurnsUnsupportedTableIntoPermanent 判的是进门**之后**那一跳的分类：
// 「这张表没开」重投一万次也不会变成开，必须留痕进 DLQ 而不是上阶梯。
func TestEventCallTurnsUnsupportedTableIntoPermanent(t *testing.T) {
	dm := newFakeDomain()
	dm.sourceErr = domain.ErrUnsupportedSourceTable
	call, bad := DecodeEvent(eventDeliveryFor(goodEventBody), DefaultMaxBodyBytes)
	if bad != nil {
		t.Fatalf("契约面就把合法消息拒了：%+v", bad)
	}
	err := call(context.Background(), dm)
	var rej domain.PermanentRejection
	if !errors.As(err, &rej) {
		t.Fatalf("ErrUnsupportedSourceTable 没转成永久拒绝，实得 %v ⇒ 它会被当瞬态在阶梯上撞三次库", err)
	}
	if rej.Code != ReasonSourceTypeNotEnabled {
		t.Errorf("code=%q，期望 %q（T2-03 那张钉码表的同值串）", rej.Code, ReasonSourceTypeNotEnabled)
	}
}

// TestEventCallPassesOtherErrorsThrough 是反面：别的错误（真瞬态、真库里的问题）
// 不许被顺手改判成永久拒绝——那会把一次本可自愈的重试变成一条死信。
func TestEventCallPassesOtherErrorsThrough(t *testing.T) {
	dm := newFakeDomain()
	dm.sourceErr = errors.New("boom: 连接池耗尽")
	call, _ := DecodeEvent(eventDeliveryFor(goodEventBody), DefaultMaxBodyBytes)
	err := call(context.Background(), dm)
	if err == nil {
		t.Fatal("领域错误被吞了")
	}
	var rej domain.PermanentRejection
	if errors.As(err, &rej) {
		t.Errorf("普通错误被误判成永久拒绝：%+v ⇒ 一条重投就能好的消息进了 DLQ", rej)
	}
}

// TestEventBodyRoundTripIsTheDocumentedShape 把线形钉成字面量。
//
// 为什么不用 struct 比 struct：`sourceDeletedBody` 与测试夹具同源于同一份 Go 定义，
// 改了 tag 两侧一起改，那条断言就恒真（用户踩过的那条「断言两侧不许同源」）。
// 这里写死 JSON 串，改 tag 必须先让这条红。
func TestEventBodyRoundTripIsTheDocumentedShape(t *testing.T) {
	raw, err := json.Marshal(sourceDeletedBody{
		SchemaVersion: "1", UserID: "u", SourceTable: "memories", SourceID: "r",
	})
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	want := `{"schemaVersion":"1","userId":"u","sourceTable":"memories","sourceId":"r"}`
	if string(raw) != want {
		t.Errorf("线形=%s\n期望   %s", raw, want)
	}
}

// ── Runner 面：第二条链认领的是自己的队列与幂等名 ──────────────────────────

// sourceTestRunner 起一条事件面 Runner（Handle-only 用例，不拨号）。
func sourceTestRunner(t *testing.T, dm Domain, sw Switch) *Runner {
	t.Helper()
	r, err := NewSource(Deps{
		Dial: func(context.Context, string, time.Duration) (Session, error) {
			return nil, errors.New("fake: 这条用例不该拨号")
		},
		Domain: dm, Switch: sw, Log: func(string, ...any) {},
	})
	if err != nil {
		t.Fatalf("NewSource: %v", err)
	}
	return r
}

func eventDelivery(rec *recorder, mutate func(*Delivery)) Delivery {
	d := eventDeliveryFor(goodEventBody)
	d.MessageID = "src-0001"
	if rec != nil {
		d.Settle = rec.settle
	}
	if mutate != nil {
		mutate(&d)
	}
	return d
}

// TestSourceRunnerClaimsItsOwnConsumerName 是幂等键那一半的证人。
//
// 用户后果说清楚：如果 `NewSource` 里的 `consumer` 传成了候选面那个串，
// 「同一条 message_id 已经被候选面吃过」会让删除事件直接判成 duplicate＋ack，
// 于是库里那一行**永远不翻**，而两侧指标面看起来一切正常（received=1 acked=1）。
// 只有把实际送进 `ConsumeIdempotent` 的那个串记下来比对，这一格才可判。
func TestSourceRunnerClaimsItsOwnConsumerName(t *testing.T) {
	dm := newFakeDomain()
	dm.wantConsumer = SourceDeletedConsumer
	r := sourceTestRunner(t, dm, onSwitch())
	rec := &recorder{}
	res, err := r.Handle(context.Background(), eventDelivery(rec, nil))
	if err != nil || res.Status != domain.ConsumeProcessed {
		t.Fatalf("合法删除事件应 processed：res=%+v err=%v", res, err)
	}
	if len(dm.consumers) != 1 || dm.consumers[0] != SourceDeletedConsumer {
		t.Fatalf("送进幂等认领的 consumer=%v，期望 [go.core.source]", dm.consumers)
	}
	if dm.consumers[0] == Consumer {
		t.Error("事件面复用了候选面的 consumer 串 ⇒ 两条面共用一个幂等 namespace")
	}
	if len(dm.sources) != 1 {
		t.Errorf("InvalidateSource 被调了 %d 次，应为 1", len(dm.sources))
	}
	if got := rec.seen(); len(got) != 1 || !got[0].ack {
		t.Errorf("结算=%+v，应为一次 ack", got)
	}
}

// TestSourceRunnerConsumesItsOwnQueue 判队列名：认错了队列＝收不到，或者更糟——
// 收到候选面的消息再按事件面的表拒成 unknown_routing_key。
func TestSourceRunnerConsumesItsOwnQueue(t *testing.T) {
	dm := newFakeDomain()
	dm.wantConsumer = SourceDeletedConsumer
	r := sourceTestRunner(t, dm, onSwitch())
	if r.queue != SourceDeletedQueue {
		t.Errorf("queue=%q，期望 %q", r.queue, SourceDeletedQueue)
	}
	if r.chained {
		t.Error("事件面 chained=true ⇒ 每条删除都会白记一次 FollowUpSkipped")
	}
	// 候选面的交付喂给事件面：必须按事件面的表**永久拒绝**＋ack，而不是「认了队列名就当候选」。
	// err 非 nil 是真实现的形状（`MarkInboxFailed` 之后仍要 ack），所以这里判的是 res，不是 err。
	rec := &recorder{}
	res, err := r.Handle(context.Background(), delivery(rec, nil))
	if res.Status != domain.ConsumeRejected || !res.Ack {
		t.Errorf("候选面的键在事件面应永久拒绝＋ack，实收 res=%+v err=%v", res, err)
	}
	if len(dm.sources) != 0 {
		t.Errorf("候选面的字节走进了事件面的领域入口：%+v", dm.sources)
	}
}

// TestSourceRunnerDoesNotRequestFollowUp 钉住 `chained=false` 的**指标面**后果。
func TestSourceRunnerDoesNotRequestFollowUp(t *testing.T) {
	dm := newFakeDomain()
	dm.wantConsumer = SourceDeletedConsumer
	r := sourceTestRunner(t, dm, onSwitch())
	if _, err := r.Handle(context.Background(), eventDelivery(&recorder{}, nil)); err != nil {
		t.Fatalf("Handle: %v", err)
	}
	st := r.Stats()
	if st.FollowUpsEnqueued+st.FollowUpSkipped+st.FollowUpErrors != 0 {
		t.Errorf("删除事件动了候选链的计数：enqueued=%d skipped=%d errors=%d ⇒ "+
			"「候选链为什么没往前推」这个指标读起来就不再只说候选",
			st.FollowUpsEnqueued, st.FollowUpSkipped, st.FollowUpErrors)
	}
	if len(dm.tasks) != 0 {
		t.Errorf("删除事件请了下一段命令：%+v", dm.tasks)
	}
}

// TestSourceRunnerPermanentRejectionLeavesTrace 是越权删除那格的端到端形状：
// 头和体不一致 ⇒ 永久拒绝（留痕进 inbox＋ack＋按 reason 计数），不是瞬态。
//
// 这里用**真** `domain.Service`＋`fakeTransactor`，而不是本包那个 fakeDomain：
// 「留痕落在 inbox 上」这一格只有真实现的那条分道（主事务回滚＋事务外补 failed）能证，
// 与候选面 TestDecodeRejectionReachesTheDomainClassifier 同一判据。
func TestSourceRunnerPermanentRejectionLeavesTrace(t *testing.T) {
	tx := newFakeTransactor()
	svc := domain.NewService(tx, domain.DefaultPolicy())
	r := sourceTestRunner(t, svc, onSwitch())
	rec := &recorder{}
	d := eventDelivery(rec, func(x *Delivery) {
		x.Body = []byte(`{"userId":"victim","sourceTable":"memories","sourceId":"mem-9"}`)
	})
	res, err := r.Handle(context.Background(), d)
	if res.Status != domain.ConsumeRejected || !res.Ack {
		t.Fatalf("res=%+v err=%v，期望 rejected＋ack（留痕进 inbox，重投同一条字节不会变合法）", res, err)
	}
	if got := rec.seen(); len(got) != 1 || !got[0].ack {
		t.Errorf("结算=%+v，期望一次 ack ⇒ 不 ack 就是让毒消息在阶梯上无限撞", got)
	}
	if codes := tx.failureCodes(); len(codes) != 1 || codes[0] != ReasonSourceUserIDMismatch {
		t.Errorf("留痕的 error_code=%v，期望 [source_user_id_mismatch] ⇒ "+
			"DLQ 里的值班答不出这条删除为什么被拒", codes)
	}
	if n := r.Stats().RejectsByReason[ReasonSourceUserIDMismatch]; n != 1 {
		t.Errorf("按 reason 计数=%d，期望 1（RejectsByReason=%v）⇒ 这类拒绝的量看不见",
			n, r.Stats().RejectsByReason)
	}

	// 同一条重投：不许再补一次留痕，否则 error_code 计数被重投次数放大（候选面同规矩）。
	res2, _ := r.Handle(context.Background(), eventDelivery(&recorder{}, func(x *Delivery) {
		x.Body = []byte(`{"userId":"victim","sourceTable":"memories","sourceId":"mem-9"}`)
	}))
	if res2.Status != domain.ConsumeRejected || !res2.Ack {
		t.Fatalf("重投的永久拒绝应仍 rejected＋ack，实收 %+v", res2)
	}
	if n := len(tx.failureCodes()); n != 1 {
		t.Errorf("同一条消息留痕了 %d 次 ⇒ 账被重投次数放大", n)
	}
}

// TestSourceRunnerTransientErrorNacksWithoutAck 判瞬态那一边：
// 库里临时故障（连接池耗尽）必须不 ack，交给阶梯重投；
// 把它误判成永久拒绝＝一次本可自愈的重试变成一条需要人工重放的死信。
func TestSourceRunnerTransientErrorNacksWithoutAck(t *testing.T) {
	dm := newFakeDomain()
	dm.wantConsumer = SourceDeletedConsumer
	dm.sourceErr = errors.New("boom: 连接池耗尽")
	r := sourceTestRunner(t, dm, onSwitch())
	rec := &recorder{}
	res, err := r.Handle(context.Background(), eventDelivery(rec, nil))
	if err == nil {
		t.Fatal("领域错误被吞了")
	}
	if res.Ack {
		t.Errorf("瞬态失败却 ack：%+v ⇒ 这条删除永远不会被重投，而账上看起来消费过了", res)
	}
	if got := rec.seen(); len(got) != 1 || got[0].ack {
		t.Errorf("结算=%+v，期望 nack（requeue=false，交给 30s 层）", got)
	}
	if st := r.Stats(); st.Transient != 1 {
		t.Errorf("Transient=%d，期望 1", st.Transient)
	}
}

// TestSourceKillSwitchOffTouchesNothing 与候选面那条同判据，但必须各有一条：
// 两条面共用一个 env 键，「关着就什么都不碰」这件事对每条链都要单独可证。
func TestSourceKillSwitchOffTouchesNothing(t *testing.T) {
	dm := newFakeDomain()
	dm.wantConsumer = SourceDeletedConsumer
	dials := 0
	r, err := NewSource(Deps{
		Dial: func(context.Context, string, time.Duration) (Session, error) {
			dials++
			return nil, errors.New("fake: 开关关着不该拨号")
		},
		Domain: dm,
		Switch: Switch{Prefetch: 1, MaxBodyBytes: 1024,
			DialTimeout: time.Second, DrainTimeout: time.Second},
		Log: func(string, ...any) {},
	})
	if err != nil {
		t.Fatalf("NewSource: %v", err)
	}
	if err := r.Run(context.Background()); err != nil {
		t.Fatalf("Run（关）带回错误：%v", err)
	}
	if dials != 0 {
		t.Errorf("开关关着却拨了 %d 次号 ⇒ 灰度 0 时事件面会连 broker", dials)
	}
	if len(dm.sources) != 0 {
		t.Errorf("开关关着却翻了登记行：%+v", dm.sources)
	}
}

// TestSourceEnabledWithoutURLRefusesToStart：空转的 worker 比没有 worker 更坏。
func TestSourceEnabledWithoutURLRefusesToStart(t *testing.T) {
	_, err := NewSource(Deps{
		Dial:   func(context.Context, string, time.Duration) (Session, error) { return &fakeSession{}, nil },
		Domain: newFakeDomain(),
		Switch: Switch{Enabled: true, Prefetch: 1, MaxBodyBytes: 1024,
			DialTimeout: time.Second, DrainTimeout: time.Second},
	})
	if err == nil || !strings.Contains(err.Error(), EnvURL) {
		t.Fatalf("开了没给 %s 必须拒绝启动，实收：%v", EnvURL, err)
	}
}

// TestSameMessageIDOnBothFacesGetsTwoSlots 钉的是幂等键的**复合**形状。
//
// 现网真会发生这一格：outbox 与候选的 message_id 都取事件行／候选行的 PK，
// 而 `DeriveTaskOperationID` 那条规则产出的 id 完全可能同串（同一条命令的候选与
// 它触发的删除指向同一段记忆）。如果两条面共用一个 consumer 串，
// 「候选面已经吃过 cnd-1」会让**删除事件**被判成 duplicate＋ack，
// 库里那一行永远不翻，而两侧指标全绿（received=1、acked=1、rejected=0）。
// 只有把同一串 id 真的喂给两条面，这个错误才可判——单面用例永远碰不到。
func TestSameMessageIDOnBothFacesGetsTwoSlots(t *testing.T) {
	shared := &fakeDomain{consumed: map[string]string{}} // wantConsumer 留空＝两条面都认
	cand := testRunner(t, shared, onSwitch(), nil)
	src := sourceTestRunner(t, shared, onSwitch())

	const sharedID = "cnd-and-src-1"
	res1, err := cand.Handle(context.Background(), delivery(&recorder{}, func(d *Delivery) {
		d.MessageID = sharedID
	}))
	if err != nil || res1.Status != domain.ConsumeProcessed {
		t.Fatalf("候选面首投应 processed：res=%+v err=%v", res1, err)
	}
	res2, err2 := src.Handle(context.Background(), eventDelivery(&recorder{}, func(d *Delivery) {
		d.MessageID = sharedID
	}))
	if err2 != nil || res2.Status != domain.ConsumeProcessed {
		t.Fatalf("同一个 message_id 在事件面应另占一格，实收 res=%+v err=%v ⇒ 两条面共用了幂等 namespace",
			res2, err2)
	}
	if len(shared.sources) != 1 {
		t.Errorf("删除没走到领域入口（sources=%d）⇒ 它被候选面的记录吞了", len(shared.sources))
	}

	// 反向证人：同一条删除重投第二次**才**该是 duplicate（否则就是两条面各吃一次、幂等没了）。
	res3, err3 := src.Handle(context.Background(), eventDelivery(&recorder{}, func(d *Delivery) {
		d.MessageID = sharedID
	}))
	if err3 != nil || res3.Status != domain.ConsumeDuplicate || !res3.Ack {
		t.Errorf("同面重投应 duplicate＋ack，实收 res=%+v err=%v", res3, err3)
	}
	if len(shared.sources) != 1 {
		t.Errorf("同面重投又跑了一次领域入口（sources=%d）⇒ 幂等位没占住", len(shared.sources))
	}
	if got := shared.consumers; len(got) != 3 || got[0] != Consumer || got[1] != SourceDeletedConsumer {
		t.Errorf("consumer 序列=%v，期望前两位是候选面＋事件面", got)
	}
}
