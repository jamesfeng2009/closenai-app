// 跨包契约闸（R5-②）。
//
// `inbound` 不 import 本包（端口定在那边、实现在这里），于是队列名、header 名、
// content_type 在那里各有一份**同名常量**。这不是第二套事实源——除非没人对着比。
// R4 给命令面做的是同一件事（topology_test.go / contract_drift_test.go），这里同形。
//
// 顺带钉两件只有本包才知道的事：
//   - `*Session` 逐字满足 `inbound.Session`（少一个方法或改了签名，装配处才报太晚）；
//   - Go 侧 `nack(requeue=false)` 之所以**免费**得到 30s 层，靠的是 purpose queue 上
//     那对 x-dead-letter-* 参数（topology.go:190-199）。阶梯一挪，入站面的背压假设就没了。
package amqp

import (
	"strings"
	"testing"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/inbound"
)

// 编译期闸：真会话必须能直接当入站端口用，装配处不需要适配器。
var _ inbound.Session = (*Session)(nil)

func TestInboundQueueAndExchangeAreTheDeclaredOnes(t *testing.T) {
	if len(CandidateRoutes) == 0 {
		t.Fatal("CandidateRoutes 空了 ⇒ 下面的比对会在「两边都空」上假绿")
	}
	for _, r := range CandidateRoutes {
		if r.Queue != inbound.Queue {
			t.Errorf("拓扑把 %s 绑到队列 %q，而消费者从 %q 取 ⇒ 消息会进一个没人看的队列",
				r.RoutingKey, r.Queue, inbound.Queue)
		}
		if r.Exchange != ExchangeAICandidate {
			t.Errorf("%s 的 exchange=%q，不是候选面 %q", r.RoutingKey, r.Exchange, ExchangeAICandidate)
		}
	}
}

// TestEveryInboundEntryIsRoutable 用**活表**（不是生成的 markdown）再对一遍：
// `inbound.CandidateEntries` 里每个键都得能被某条已声明的绑定投进那个队列。
// 表加一格而拓扑没加，表现是「代码看着支持，线上永远收不到」。
func TestEveryInboundEntryIsRoutable(t *testing.T) {
	for key, entry := range inbound.CandidateEntries {
		if !routableByCandidateBindings(key) {
			t.Errorf("映射表里的 %s（→%s）没有对应绑定 ⇒ 这一格只能靠通配碰运气，或直接成死键", key, entry)
		}
	}
	// 反向：逐字声明的每条非通配绑定，映射表里必须有分支。
	for _, r := range CandidateRoutes {
		if strings.HasSuffix(r.RoutingKey, "*") {
			continue
		}
		if _, ok := inbound.CandidateEntries[r.RoutingKey]; !ok {
			t.Errorf("已声明的绑定 %s 在映射表里没有分支 ⇒ 消息进得来却永远被 unknown_routing_key 拒掉",
				r.RoutingKey)
		}
	}
}

// TestInboundWireConstantsMatchPublishers 钉那六个串：
// 发布侧（本包）与消费侧（inbound）读的是同一个 header 名与同一个 content_type，
// 任何一侧改名都是「发得出、解不开」，而它不会报错，只会表现为「全部 missing_user_id」。
func TestInboundWireConstantsMatchPublishers(t *testing.T) {
	for _, pair := range []struct {
		ours, theirs, what string
	}{
		{HeaderSchemaVersion, inbound.HeaderSchemaVersion, "schema 版本头"},
		{HeaderUserID, inbound.HeaderUserID, "归属头"},
		{HeaderCausationID, inbound.HeaderCausationID, "因果头"},
		{HeaderAttemptCount, inbound.HeaderAttemptCount, "尝试计数头"},
		{ContentTypeJSON, inbound.ContentTypeJSON, "content_type"},
	} {
		if pair.ours != pair.theirs {
			t.Errorf("%s 两侧不同名：发布写 %q，消费读 %q", pair.what, pair.ours, pair.theirs)
		}
		if !strings.HasPrefix(pair.ours, "x-") && pair.what != "content_type" {
			t.Errorf("%s=%q 不是 x- 前缀，与 14 §5 的属性面不同形", pair.what, pair.ours)
		}
	}
}

// TestNackLandsOnTheFirstRetryTier 把「Go 侧只做到 30s 层」这句写在 inbound.go 里的话
// 变成可执行事实：purpose queue 的 DLX 键必须就是第 0 层队列名。
// 阶梯换了（比如把 30s 挪走），瞬态失败的退避时长会静默变成另一种，
// 而消费侧代码一个字都不用改。
func TestNackLandsOnTheFirstRetryTier(t *testing.T) {
	first := TierRoutingKey(inbound.Queue, 0)
	if !strings.HasPrefix(first, "retry.30s.") {
		t.Errorf("requeue=false 的第一站是 %q ⇒ 入站面注释里的「30s 层」不再成立", first)
	}
	if first != "retry.30s.go.candidate.accept" {
		t.Errorf("阶梯队列名 %q 与队列名脱钩了（14 §8 的命名面）", first)
	}
	if got := FinalQueueName(inbound.Queue); got != "dlq.go.candidate.accept" {
		t.Errorf("终态死信队列 %q ⇒ R7 的 replay 工具按队列名寻址，改名要连着工具一起改", got)
	}
	// ⚠️ 这一条同时把欠账写在脸上：入站面今天**没有** 2m／10m 的升级分支（登记 R5b）。
	if len(RetryTiers) < 2 {
		t.Errorf("RetryTiers 只有 %d 层，与拓扑文档里那条阶梯不齐", len(RetryTiers))
	}
}

func TestInboundConsumerNameFollowsTheQueueName(t *testing.T) {
	// consumer 是 inbox 唯一键的另一半（18 §7）。它跟队列名同源是**刻意的对账把手**：
	// `go.candidate.accept` ⇄ `go.core.candidate`。两个串各自被改都等于换一个记账 namespace，
	// 于是这里同时钉两侧。
	if inbound.Queue != "go.candidate.accept" || inbound.Consumer != "go.core.candidate" {
		t.Errorf("队列=%q consumer=%q ⇒ 与 14:55 与 D-R5-7 的那两个串不齐", inbound.Queue, inbound.Consumer)
	}
	if !strings.HasPrefix(inbound.Queue, "go.") || !strings.HasPrefix(inbound.Consumer, "go.") {
		t.Error("Go 侧收件面用 go. 前缀命名，两个串都要守")
	}
}

// routableByCandidateBindings 按 AMQP topic 的最小语义判：通配 `candidate.*` 覆盖
// `candidate.<单个词>`（本表里只有这一条通配，所以不需要实现 `#` 与多级段）。
func routableByCandidateBindings(key string) bool {
	for _, r := range CandidateRoutes {
		if !strings.HasSuffix(r.RoutingKey, "*") {
			if r.RoutingKey == key {
				return true
			}
			continue
		}
		prefix := strings.TrimSuffix(r.RoutingKey, "*")
		if rest := strings.TrimPrefix(key, prefix); rest != "" && !strings.ContainsAny(rest, ".*#") {
			return true
		}
	}
	return false
}
