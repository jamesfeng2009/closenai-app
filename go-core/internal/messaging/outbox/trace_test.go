// 批 T-B 闸 G-B2（认领行 → broker 属性面）：EnvelopeOf 不许丢 TraceID。
package outbox

import (
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

func TestEnvelopeOfMapsTrace(t *testing.T) {
	claim := domain.OutboxClaim{
		ID: "row-1", MessageID: "m1", MessageType: "ai_pattern_requested",
		SchemaVersion: 1, RoutingKey: "ai.pattern.requested", UserID: "u-1",
		CorrelationID: "c-1", CausationID: "ca-1", TraceID: "T-row",
		Payload: `{}`, AttemptCount: 1, LeaseExpiresAt: time.Now().UTC(),
	}
	env := EnvelopeOf(claim)
	if env.TraceID != "T-row" {
		t.Errorf("EnvelopeOf 丢了 TraceID（得 %q）⇒ 库到 broker 这一跳断链", env.TraceID)
	}
}
