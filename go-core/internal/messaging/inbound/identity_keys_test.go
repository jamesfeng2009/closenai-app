// T7-03：体内身份键的跨语言用例（Go 这一半）。
//
// 期望值来自 `contracts/mq_identity_cases.json`——与
// `backend/tests/test_mq_identity_contract.py`、`backend/tests/test_ai_mq_consumer.py`
// 读的是同一份文件。这里证三件事：
//  1. 归一规则与 Python 同形（小写 + 剥 `_`/`-`）；
//  2. 候选面与事件面对每种变体都给出同一个码 `foreign_user_id`；
//  3. 拒绝发生在任何领域写入之前——用带调用计数的 fake 逐个数，不是靠读代码。
//
// 事件面的 `userId` 是契约上的合法拼写（DLQ 对账痕迹），所以它只允许那一个形态；
// 头体冲突仍是另一件事（`source_user_id_mismatch`）。
package inbound

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

type identitySurface struct {
	RoutingKey  string            `json:"routing_key"`
	MessageType string            `json:"message_type"`
	Headers     map[string]string `json:"headers"`
	BaseBody    map[string]any    `json:"base_body"`
}

type identityCases struct {
	Variants     []string        `json:"variants"`
	NonIdentity  []string        `json:"non_identity"`
	RejectReason string          `json:"reject_reason"`
	HeaderUser   string          `json:"header_user"`
	BodyUser     string          `json:"body_user"`
	Command      identitySurface `json:"command"`
	Candidate    identitySurface `json:"candidate"`
	SourceDelete struct {
		identitySurface
		AllowedIdentityKey string `json:"allowed_identity_key"`
		ConflictUser       string `json:"conflict_user"`
	} `json:"source_deleted"`
}

func loadIdentityCases(t *testing.T) identityCases {
	t.Helper()
	path := filepath.Join(repoRootFromCwd(t), "contracts", "mq_identity_cases.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("读不到共享用例 %s：%v", path, err)
	}
	var cases identityCases
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatalf("共享用例不是合法 JSON：%v", err)
	}
	if len(cases.Variants) == 0 || len(cases.NonIdentity) == 0 {
		t.Fatal("共享用例被清空了：变体或非身份键为空，扫空集永远绿")
	}
	if cases.RejectReason == "" {
		t.Fatal("共享用例没写 reject_reason：断言只能拿 Go 常量自比，码值漂移测不出来")
	}
	if ReasonForeignUserID != cases.RejectReason {
		t.Fatalf("Go 的码=%q，共享契约=%q ⇒ 两侧留痕面分叉", ReasonForeignUserID, cases.RejectReason)
	}
	return cases
}

func cloneBody(t *testing.T, base map[string]any) map[string]any {
	t.Helper()
	out := make(map[string]any, len(base)+1)
	for k, v := range base {
		out[k] = v
	}
	return out
}

func bodyBytes(t *testing.T, body map[string]any) []byte {
	t.Helper()
	raw, err := json.Marshal(body)
	if err != nil {
		t.Fatalf("夹具体序列化失败：%v", err)
	}
	return raw
}

func candidateDeliveryFor(fixture identityCases, raw []byte, messageID string) Delivery {
	return Delivery{
		RoutingKey:  fixture.Candidate.RoutingKey,
		MessageID:   messageID,
		MessageType: fixture.Candidate.MessageType,
		ContentType: ContentTypeJSON,
		Headers: map[string]any{
			HeaderSchemaVersion: SupportedSchemaVersion,
			HeaderUserID:        fixture.HeaderUser,
		},
		Body: raw,
	}
}

func eventDeliveryWith(fixture identityCases, raw []byte, messageID string) Delivery {
	return Delivery{
		RoutingKey:  fixture.SourceDelete.RoutingKey,
		MessageID:   messageID,
		MessageType: fixture.SourceDelete.MessageType,
		ContentType: ContentTypeJSON,
		Headers: map[string]any{
			HeaderSchemaVersion: SupportedSchemaVersion,
			HeaderUserID:        fixture.HeaderUser,
		},
		Body: raw,
	}
}

func TestIdentityNormaliserMatchesSharedCases(t *testing.T) {
	cases := loadIdentityCases(t)
	for _, key := range cases.Variants {
		if !isIdentityKey(key) {
			t.Errorf("%q 是登记的体内身份键，归一后却没认出来（规则漂了？）", key)
		}
	}
	for _, key := range cases.NonIdentity {
		if isIdentityKey(key) {
			t.Errorf("%q 不是身份键，却被归一成了 %q ⇒ 误伤面在扩大", key, normaliseIdentityKey(key))
		}
	}
	// 三张必填拼写各点名一次：共享表被删成只剩一条时，上面两条仍可能绿。
	for _, required := range []string{"user_id", "userId", "user-id"} {
		found := false
		for _, key := range cases.Variants {
			if key == required {
				found = true
				break
			}
		}
		if !found {
			t.Errorf("共享变体表缺了 %q：T7-03 的最小覆盖是三张拼写都在", required)
		}
	}
}

func TestCandidateBodyIdentityVariantsAreRejectedBeforeAnyDomainWrite(t *testing.T) {
	fixture := loadIdentityCases(t)
	for idx, key := range fixture.Variants {
		t.Run(key, func(t *testing.T) {
			body := cloneBody(t, fixture.Candidate.BaseBody)
			body[key] = fixture.BodyUser
			d := candidateDeliveryFor(fixture, bodyBytes(t, body),
				"cnd-identity-"+string(rune('a'+idx)))

			if _, rej := Decode(d, DefaultMaxBodyBytes); rej == nil || rej.Code != fixture.RejectReason {
				t.Fatalf("候选体带 %q：期望 %q，得到 %+v", key, fixture.RejectReason, rej)
			}

			dm := newFakeDomain()
			r := testRunner(t, dm, onSwitch(), nil)
			rec := &recorder{}
			d.Settle = rec.settle
			res, err := r.Handle(context.Background(), d)
			if res.Status != domain.ConsumeRejected {
				t.Fatalf("状态=%q，期望 rejected；err=%v", res.Status, err)
			}
			var rej domain.PermanentRejection
			if !errors.As(err, &rej) || rej.Code != fixture.RejectReason {
				t.Fatalf("留痕码=%v，期望 %q", err, fixture.RejectReason)
			}
			_, admits, evidences, proactives := dm.counts()
			if admits+evidences+proactives != 0 || len(dm.sources) != 0 {
				t.Fatalf("拒绝之前已经发生领域写入：admits=%d evidences=%d proactive=%d sources=%d",
					admits, evidences, proactives, len(dm.sources))
			}
			if got := rec.seen(); len(got) != 1 || !got[0].ack || got[0].requeue {
				t.Fatalf("永久拒绝必须 ACK（一条只走一层），实得 %+v", got)
			}
		})
	}
}

func TestCandidateWithoutBodyIdentityKeyReachesDomain(t *testing.T) {
	fixture := loadIdentityCases(t)
	d := candidateDeliveryFor(fixture, bodyBytes(t, fixture.Candidate.BaseBody), "cnd-legal")
	d.Settle = (&recorder{}).settle
	dm := newFakeDomain()
	r := testRunner(t, dm, onSwitch(), nil)
	res, err := r.Handle(context.Background(), d)
	if err != nil || res.Status != domain.ConsumeProcessed {
		t.Fatalf("合法候选被拒：status=%q err=%v", res.Status, err)
	}
	if _, admits, _, _ := dm.counts(); admits != 1 {
		t.Fatalf("合法候选进入领域 %d 次，应为 1", admits)
	}
}

func TestSourceDeletedIdentityVariantsAreRejectedBeforeAnyDomainWrite(t *testing.T) {
	fixture := loadIdentityCases(t)
	allowed := fixture.SourceDelete.AllowedIdentityKey
	for idx, key := range fixture.Variants {
		if key == allowed {
			continue // 契约上唯一合法的拼写，由下面那条正例覆盖
		}
		t.Run(key, func(t *testing.T) {
			body := cloneBody(t, fixture.SourceDelete.BaseBody)
			delete(body, allowed)
			body[key] = fixture.BodyUser
			d := eventDeliveryWith(fixture, bodyBytes(t, body),
				"srcdel-identity-"+string(rune('a'+idx)))

			if _, rej := DecodeEvent(d, DefaultMaxBodyBytes); rej == nil || rej.Code != fixture.RejectReason {
				t.Fatalf("事件体带 %q：期望 %q，得到 %+v", key, fixture.RejectReason, rej)
			}

			dm := newFakeDomain()
			dm.wantConsumer = SourceDeletedConsumer
			r := sourceTestRunner(t, dm, onSwitch())
			rec := &recorder{}
			d.Settle = rec.settle
			res, _ := r.Handle(context.Background(), d)
			if res.Status != domain.ConsumeRejected {
				t.Fatalf("状态=%q，期望 rejected", res.Status)
			}
			if len(dm.sources) != 0 {
				t.Fatalf("拒绝之前已经调用 InvalidateSource：%+v", dm.sources)
			}
		})
	}
}

func TestSourceDeletedLegalShapeAndHeaderBodyConflict(t *testing.T) {
	fixture := loadIdentityCases(t)
	dm := newFakeDomain()
	dm.wantConsumer = SourceDeletedConsumer
	r := sourceTestRunner(t, dm, onSwitch())

	legal := eventDeliveryWith(fixture, bodyBytes(t, fixture.SourceDelete.BaseBody), "srcdel-legal")
	legal.Settle = (&recorder{}).settle
	res, err := r.Handle(context.Background(), legal)
	if err != nil || res.Status != domain.ConsumeProcessed {
		t.Fatalf("合法事件被拒：status=%q err=%v", res.Status, err)
	}
	if len(dm.sources) != 1 || dm.sources[0].UserID != fixture.HeaderUser {
		t.Fatalf("合法事件指向=%+v，期望归属只取 header %q", dm.sources, fixture.HeaderUser)
	}

	conflictBody := cloneBody(t, fixture.SourceDelete.BaseBody)
	conflictBody[fixture.SourceDelete.AllowedIdentityKey] = fixture.SourceDelete.ConflictUser
	conflict := eventDeliveryWith(fixture, bodyBytes(t, conflictBody), "srcdel-conflict")
	if _, rej := DecodeEvent(conflict, DefaultMaxBodyBytes); rej == nil || rej.Code != ReasonSourceUserIDMismatch {
		t.Fatalf("头体冲突：期望 %q，得到 %+v", ReasonSourceUserIDMismatch, rej)
	}
	dm2 := newFakeDomain()
	dm2.wantConsumer = SourceDeletedConsumer
	r2 := sourceTestRunner(t, dm2, onSwitch())
	if _, err := r2.Handle(context.Background(), conflict); err == nil {
		t.Fatal("头体冲突必须留痕")
	}
	if len(dm2.sources) != 0 {
		t.Fatalf("头体冲突也不许碰库：%+v", dm2.sources)
	}
}
