// Package inbound 是 Go 侧的**入站**消费面（R5-②，V3 17 §3 的 Go 十步、18 §8 的 inbox 事务形状）。
//
// 它站在回投面的接收端：Python 产一条 candidate 投 `closenai.ai.candidates`，
// 本包从 `go.candidate.accept` 消费，解码后走 `domain.ConsumeIdempotent` ＋ 三个领域入口
// （`Admit`／`AdmitEvidence`／`AdmitProactive`），**只有事务提交之后才 ack**（17 §91-112）。
//
// 与 `dispatch` 的分工是刻意的、不重叠：dispatch 只管**出箱**（DB 里的 outbox 行 → broker），
// 本包只管**入箱**（broker → inbox 行 → 领域写面）。两者共用 `amqp` 的拓扑与连接面，
// 各自拨各自的连接（D-R5-1：同步面与吞吐面的重启节奏不同，同一个道理也适用于入箱≠出箱）。
//
// ⚠️ 本包不 import `internal/messaging/amqp`（端口定在这边、实现在那边），于是队列名与
// header 名在这里有一份**同名常量**。这不是第二套事实源：
// `internal/messaging/amqp/inbound_contract_test.go` 把这几格逐字钉回
// `amqp.CandidateRoutes`／`amqp.Header*`，漂了就红——与 R4 给命令面做的同一件事同形。
package inbound

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// 开关面（D-R5-9）。**只有两个键**：本批不新增键面，Python 那六个键已经占掉了额度，
// 清单面整条归 R7。其余参数是包内默认值＋结构体字段覆盖，理由见下面的默认值块。
const (
	// EnvWorker 默认关：不给值 ⇒ 进程零副作用（不拨号、不声明、不消费、不碰 DB）。
	EnvWorker = "CLOSEN_CANDIDATE_WORKER"
	// EnvURL 与出箱 worker、Python worker 同名同一个值（跨语言只有一个 broker 地址键）。
	EnvURL = "CLOSEN_AMQP_URL"
)

// 契约常量（与 amqp 包同值，由 inbound_contract_test.go 钉住）。
const (
	// Consumer 是 `inbox_messages.consumer` 的**唯一**取值（D-R5-7）。
	//
	// 为什么必须在 Go 侧定义而不是由消息带来：`ConsumeIdempotent` 要求 Consumer 非空
	// （delivery.go:269-272 直接 error），而 18 §7 的唯一键是 `(message_id, consumer)`——
	// 这个串一旦随消息可变，同一个 message_id 就能被同一个队列消费两次。
	// 命名跟队列名同源（`go.candidate.accept` ⇒ `go.core.candidate`），便于对账。
	Consumer = "go.core.candidate"
	// Queue 是本包消费的 purpose queue 名（14:55 逐字）。
	Queue = "go.candidate.accept"

	ContentTypeJSON = "application/json"

	HeaderSchemaVersion = "x-schema-version"
	HeaderUserID        = "x-user-id"
	HeaderCausationID   = "x-causation-id"
	HeaderAttemptCount  = "x-attempt-count"
	// HeaderTraceID 批 T-B 消费侧（与 amqp/publisher.go 同名同值，两侧不许漂移）：
	// 缺头不拒——「没有 trace」是合法形态（后台 cron 起源的命令）。
	HeaderTraceID = "x-trace-id"
)

// 进程参数默认值。都是**代码里的默认**，不是 env 键（见上面 EnvWorker 那条取舍）：
//   - Prefetch 1：与 Python 侧 `backend/ai/mq/config.py` 同一取向（宁可慢，不要乱序与并发放大）。
//     它同时压住另一件事：`Runner.Handle` 在 inbox 事务里调领域入口，而领域入口自己会再
//     `InTx`（`store/canonical_repo.go:53` 每次都从池里 `Begin`，不复用 ctx 上已有的事务）
//     ⇒ 一条在途消息最多占两条连接。prefetch 调大就是按倍数吃池。这件事登记为 R5b 欠账。
//   - MaxBodyBytes 65536：与 Python 侧 `DEFAULT_MAX_BODY_BYTES` 同值；那条跨语言闸在
//     `backend/tests/test_ai_mq_contract.py`，它读本文件的这个常量。
//   - DrainTimeout 30s：与 dispatch 的默认同量级；真值待 R7 在 pod 上按
//     `terminationGracePeriodSeconds` 标定（那条欠账 R4 已登记）。
const (
	DefaultPrefetch     = 1
	DefaultMaxBodyBytes = 65536
	DefaultDialTimeout  = 10 * time.Second
	DefaultDrainTimeout = 30 * time.Second

	// reconnectWait 是会话建不起来时的回退间隔。它不是 env 键，也不进指标面：
	// 一个连不上 broker 的 worker 需要的是一行可诊断日志＋一个不动物性的等待，
	// 而不是把失败重试调成可 tuning 的面。
	reconnectWait = time.Second
)

// Switch 是「这个进程要不要跑入站 worker」与它的四个参数。
type Switch struct {
	Enabled      bool
	URL          string
	Prefetch     int
	MaxBodyBytes int
	DialTimeout  time.Duration
	DrainTimeout time.Duration
}

// LoadSwitch 只读两个 env 键，其余走包内默认。
func LoadSwitch(getenv func(string) string) Switch {
	return Switch{
		Enabled:      isOn(getenv(EnvWorker)),
		URL:          strings.TrimSpace(getenv(EnvURL)),
		Prefetch:     DefaultPrefetch,
		MaxBodyBytes: DefaultMaxBodyBytes,
		DialTimeout:  DefaultDialTimeout,
		DrainTimeout: DefaultDrainTimeout,
	}
}

// Validate 拦住「开了关但没给地址」这类只会让 worker 空转的形态（与 dispatch 同一句话）。
func (s Switch) Validate() error {
	if s.Prefetch <= 0 {
		return fmt.Errorf("inbound: Prefetch 必须为正（0 在 AMQP 里＝不限），收到 %d", s.Prefetch)
	}
	if s.MaxBodyBytes <= 0 {
		return fmt.Errorf("inbound: MaxBodyBytes 必须为正，收到 %d", s.MaxBodyBytes)
	}
	if s.DialTimeout <= 0 || s.DrainTimeout <= 0 {
		return errors.New("inbound: DialTimeout/DrainTimeout 必须全为正")
	}
	if s.Enabled && s.URL == "" {
		return fmt.Errorf("inbound: %s 开了但 %s 为空 ⇒ worker 只会空转，拒绝启动", EnvWorker, EnvURL)
	}
	return nil
}

// isOn 的布尔档与 `dispatch.isOn`／Python 侧 `_TRUE` 同一套词表：三处认「开」的字面量
// 必须一致，否则同一个 ConfigMap 值在两个进程里读出相反结论。
func isOn(v string) bool {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "1", "true", "on", "yes":
		return true
	default:
		return false
	}
}

// Delivery 是一条入站交付（端口结构，不是 amqp091 的对象）。
//
// `Settle` 是唯一能确认/重投它的出口。字段全是值、没有接口，所以单测造 fake
// 只需要填一个记录动作的闭包——这是 dispatch 那边 Session 端口的同一套做法。
type Delivery struct {
	RoutingKey    string
	MessageID     string
	MessageType   string
	CorrelationID string
	ContentType   string
	Headers       map[string]any
	Body          []byte

	// Settle(true, _)＝ack；Settle(false, true)＝requeue 回原队列（会热转）；
	// Settle(false, false)＝reject，在本拓扑里进 30s 层（topology.go:188-193 逐字）。
	Settle func(ack, requeue bool) error
}

func (d Delivery) Ack() error { return d.settle(true, false) }

// Nack 把消息交回 broker。本包一律 requeue=false：瞬态失败交给阶梯去背压
// （14 §8「Prefer explicit retry queues over unlimited immediate requeue」）。
func (d Delivery) Nack(requeue bool) error { return d.settle(false, requeue) }

func (d Delivery) settle(ack, requeue bool) error {
	if d.Settle == nil {
		return errors.New("inbound: 这条交付没有 Settle，无法确认")
	}
	return d.Settle(ack, requeue)
}

// HeaderString 取一个 header 的字符串值。
//
// AMQP 的 header 值类型是自由的（long_string／byte_array 都可能出现，取决于对面语言），
// 所以这里只认「本来就是字符串」和「字节数组」两种，其余一律当缺失——
// 不做 `fmt.Sprint` 那种兜底：把 `[]byte{...}` 印成一个奇怪字符串，
// 表现是「归属校验通过了，但校验的是一个不存在的人」。
//
// ⚠️ 版本号那一格不要用这个方法，用 `HeaderVersion`（两者的判据不同，理由写在那里）。
func (d Delivery) HeaderString(name string) string {
	switch v := d.Headers[name].(type) {
	case string:
		return strings.TrimSpace(v)
	case []byte:
		return strings.TrimSpace(string(v))
	default:
		return ""
	}
}

// HeaderVersion 取一个 header 的「整数版本」形态，归一成十进制串。
//
// 为什么版本与归属必须用两个读法：`x-user-id` 上线成数字是**契约级非法**（归属只有
// Envelope 一个来源，数字不是一个人），而 `x-schema-version` 上线成数字是**线上真实形态**：
// 2026-10-16 真 broker × 真进程实测出来的——Go 的发布侧放的是 int
// （`amqp/publisher.go:130` 直接写 `msg.SchemaVersion`），对面 `contract.py:446` 放的是 str，
// 而对面读侧（`_as_str`＋`isdigit`）两种都吃。这边只吃 str 的后果不是报错而是**静默永久拒绝**：
// 「Go 发、Go 收」那一跳（重放路径把 outbox 行原样再投一次，走的正是 int 那一支）
// 会永远按 unsupported_schema_version 记账，而 inbox 会替它把这条账留成 failed。
//
// 写侧的类型漂移属于 R4 发布面，按「发现的缺陷不顺手修」登记归 R5b（2026-10-16）；
// 读侧与对面**对等**是这一批的活——`candidate_decode.go:161-164` 那条「两侧不许一侧严一侧松」
// 说的就是这件事。浮点与布尔不吃：那两个形态在两侧的产生路径里都不存在，
// 吃了等于把「谁拼错了」读成「另一种合法写法」。
func (d Delivery) HeaderVersion(name string) string {
	switch v := d.Headers[name].(type) {
	case string:
		return strings.TrimSpace(v)
	case []byte:
		return strings.TrimSpace(string(v))
	case int:
		return strconv.Itoa(v)
	case int32:
		return strconv.Itoa(int(v))
	case int64:
		return strconv.FormatInt(v, 10)
	case uint32:
		return strconv.FormatUint(uint64(v), 10)
	case uint64:
		return strconv.FormatUint(v, 10)
	default:
		return ""
	}
}

// Session 是一段 broker 会话（生产实现＝`*amqp.Session`，见 amqp/consumer.go）。
//
// 只有这四件事：声明拓扑、开始消费、判活、关闭。于是进程接线的几条闸
// （关→开、拨号失败、会话死后重拨、停机不卡在在途上）都不需要真 broker；
// 真 broker 那一段留给 R5-③ 的互操作闸。
type Session interface {
	Declare() error
	Consume(ctx context.Context, queue string, prefetch int) (<-chan Delivery, error)
	Alive() bool
	Close() error
}

// DialFunc 建立会话。拨号本身带超时，所以签名里必须有 ctx。
type DialFunc func(ctx context.Context, url string, dialTimeout time.Duration) (Session, error)

// Domain 是本包需要的领域面：一个幂等消费入口＋三个候选入口（D-R5-8 的三条绑定）
// ＋一条命令下发口（D-G14-②：候选入库后请对面算下一段）。
//
// `*domain.Service` 逐字满足它；单测用带调用计数的 fake，于是
// 「重复消息第二次领域入口不被调用」这条验收（tasks.md R5 逐字要求）才是可判的，
// 而不是靠人读代码确认 inbox 那一跳在前面。
type Domain interface {
	ConsumeIdempotent(ctx context.Context, msg domain.InboundMessage,
		fn func(context.Context, domain.Store) error) (domain.ConsumeResult, error)
	Admit(ctx context.Context, c domain.Candidate) (domain.Result, error)
	AdmitEvidence(ctx context.Context, c domain.EvidenceCandidate) (domain.EvidenceResult, error)
	AdmitProactive(ctx context.Context, c domain.ProactiveCandidate) (domain.ProactiveResult, error)
	// RequestAITask 必须拿 **inbox 这一笔事务的 Store**：命令与「这条消息我消费过了」
	// 同一条 COMMIT 才是 outbox 模式（18 §2）。自开事务会变成「候选收下了、命令丢了」
	// 那种补不回来的分叉——与 `commands.go:81-89` 拒不自开事务同一条理由。
	RequestAITask(ctx context.Context, st domain.Store, req domain.AITaskRequest) error
}

// 编译期闸：装配处（cmd/go-api/main.go）给的就是 `*domain.Service`，它一改方法签名这里先红，
// 而不是等 `go build ./...` 在 main 包里报一句「missing method Admit」。
var _ Domain = (*domain.Service)(nil)

// Stats 是指标面。分类直接对着 `domain.ConsumeStatus` 来，不自己发明第二套：
// Processed／Duplicate／Rejected（永久拒绝已留痕）／Transient（已 NACK，等重投）。
//
// ⚠️ 积压年龄（`oldest_pending`）仍然不在这里，理由与 R4 的欠账①同一条：
// 要看队列深度得连上 broker 之后才有意义，届时再定要不要那条最小 SQL。
type Stats struct {
	Received   int
	Processed  int
	Duplicates int
	Rejected   int
	Transient  int
	Acks       int
	Nacks      int
	SettleErrs int
	Dials      int
	Redials    int
	// FollowUpsEnqueued／FollowUpSkipped 是 D-G14-② 那条链的计数器：
	// 一条候选入库之后请没请下一段，必须是**可数的**——否则「链有没有在跑」
	// 又变回一句口头话，而本批改判的起因正是「在册没人调」这件事没人看得见。
	FollowUpsEnqueued int
	FollowUpSkipped   int
	FollowUpErrors    int
	// RejectsByReason 按 `domain.PermanentRejection.Code` 计数——毒消息排查第一眼要看的就是这一格。
	RejectsByReason map[string]int
}

// Deps 是装配面。Log 是缝隙：本包不 import log（一个消费编排不该绑死日志实现）。
type Deps struct {
	Dial   DialFunc
	Domain Domain
	Switch Switch
	Log    func(format string, args ...any)
}

// Runner 消费 `go.candidate.accept` 直到 ctx 取消。可并发读 Stats；写只在 Run/Handle 里。
type Runner struct {
	deps    Deps
	mu      sync.Mutex
	stat    Stats
	session Session
}

func New(deps Deps) (*Runner, error) {
	if deps.Dial == nil {
		return nil, errors.New("inbound: Dial 为 nil")
	}
	if deps.Domain == nil {
		return nil, errors.New("inbound: Domain 为 nil")
	}
	if err := deps.Switch.Validate(); err != nil {
		return nil, err
	}
	if deps.Log == nil {
		deps.Log = func(string, ...any) {}
	}
	r := &Runner{deps: deps}
	// 计数面里的 map 必须在这里建好：`Handle` 直接往里写，nil map 会让**第一条毒消息**
	// 把这个 worker 打 panic（而毒消息恰恰是它唯一会走的那条分支，正常消息测不出这个）。
	r.stat.RejectsByReason = map[string]int{}
	return r, nil
}

// Stats 取累计计数快照（map 是复制的：读侧不能看见后续写入）。
func (r *Runner) Stats() Stats {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := r.stat
	out.RejectsByReason = make(map[string]int, len(r.stat.RejectsByReason))
	for k, v := range r.stat.RejectsByReason {
		out.RejectsByReason[k] = v
	}
	return out
}

// Run 常驻直到 ctx 取消。
//
// kill switch 关时**直接返回 nil**：不拨号、不声明、不消费、不碰 DB。
// 这条是「灰度 0 时现网行为一字不变」的实现层证据，闸在 inbound_test.go。
func (r *Runner) Run(ctx context.Context) error {
	sw := r.deps.Switch
	if !sw.Enabled {
		r.logf("candidate worker 未启动：%s 没给 1（kill switch 默认关）", EnvWorker)
		return nil
	}
	defer r.closeSession()

	for {
		if ctx.Err() != nil {
			return nil
		}
		deliveries, err := r.currentSession(ctx)
		if err != nil {
			r.logf("inbound: broker 会话没建起来：%v ⇒ %v 后重试", err, reconnectWait)
			if !sleep(ctx, reconnectWait) {
				return nil
			}
			continue
		}
		if keepGoing := r.drainDeliveries(ctx, deliveries); !keepGoing {
			return nil
		}
	}
}

// drainDeliveries 单线程消费一条会话的交付。
//
// 不起 goroutine 池是刻意的：prefetch 已经限定了在途条数，并发消费只会把乱序引进来，
// 而领域侧的「同一用户同一模型行」判定是按顺序设计的（N3 的乐观并发靠版本递增，不靠加锁）。
// 回 false ⇒ ctx 结束；回 true ⇒ 交付通道关了（连接死），调用方该重拨。
func (r *Runner) drainDeliveries(ctx context.Context, deliveries <-chan Delivery) bool {
	for {
		select {
		case <-ctx.Done():
			return false
		case d, ok := <-deliveries:
			if !ok {
				// 计数点放在这儿而不是 sessionFor 的「有 session 但已死」分支：
				// 通道关了才是「会话死了」这件事的**发生时刻**，而那一支随后会把
				// session 置 nil，重拨时只剩「没有会话」这个状态，分不清首拨与重拨。
				r.count(func(s *Stats) { s.Redials++ })
				r.logf("inbound: 交付通道关闭 ⇒ 重拨重消费（累计收到 %d 条）", r.Stats().Received)
				r.closeSession()
				return true
			}
			r.count(func(s *Stats) { s.Received++ })
			// 在途这一条用**不受停机信号影响**的 ctx 做完，但要设上限：
			// pod 到点是 SIGKILL，一个卡在退出路径上的进程会被读成「优雅停机成功」。
			drain, cancel := context.WithTimeout(context.WithoutCancel(ctx), r.deps.Switch.DrainTimeout)
			res, err := r.Handle(drain, d)
			cancel()
			r.logOutcome(d, res, err)
		}
	}
}

// currentSession 返回一条可消费的交付通道；会话死了就关掉旧的、重拨、重声明、重消费。
func (r *Runner) currentSession(ctx context.Context) (<-chan Delivery, error) {
	sess, err := r.sessionFor(ctx)
	if err != nil {
		return nil, err
	}
	return sess.Consume(ctx, Queue, r.deps.Switch.Prefetch)
}

func (r *Runner) sessionFor(ctx context.Context) (Session, error) {
	r.mu.Lock()
	alive := r.session != nil && r.session.Alive()
	sess := r.session
	r.mu.Unlock()
	if alive {
		return sess, nil
	}
	if sess != nil {
		// 走到这儿＝上一条会话已判定不可用。关掉再开一条，
		// 别让一个死连接占着「有 session 所以不重拨」的位置。
		r.closeSession()
		r.count(func(s *Stats) { s.Redials++ })
	}
	dialCtx, cancel := context.WithTimeout(ctx, r.deps.Switch.DialTimeout)
	defer cancel()
	fresh, err := r.deps.Dial(dialCtx, r.deps.Switch.URL, r.deps.Switch.DialTimeout)
	if err != nil {
		return nil, err
	}
	r.count(func(s *Stats) { s.Dials++ })
	// Go 独占主动声明（D-R5-3）：对面 Python 只做 passive 断言，
	// 所以「队列不存在」只能由这一行变成静默建表——那正是 D-R5-3 要避免的形态。
	if err := fresh.Declare(); err != nil {
		_ = fresh.Close()
		return nil, fmt.Errorf("inbound: declare topology: %w", err)
	}
	r.mu.Lock()
	r.session = fresh
	r.mu.Unlock()
	return fresh, nil
}

func (r *Runner) closeSession() {
	r.mu.Lock()
	sess := r.session
	r.session = nil
	r.mu.Unlock()
	if sess != nil {
		if err := sess.Close(); err != nil {
			r.logf("inbound: 关会话：%v", err)
		}
	}
}

// Handle 处理一条交付：抽幂等键 → ConsumeIdempotent（解码与领域调用都在这笔事务里）
// → 按 `ConsumeResult.Ack` 决定 broker 动作。
//
// ACK 的纪律（17 §91-112）：`ConsumeResult.Ack` 是**唯一**依据。它已经把
// 「processed／duplicate／已留痕的永久拒绝」三种算成 true，把「瞬时失败」与
// 「另一笔还没提交的 pending」算成 false（delivery.go:316-338），所以这里不许
// 只看 err 是否为 nil——只看 err 会把已留痕的永久拒绝当成要重投。
//
// 导出的理由：R5-③ 的互操作闸要在同一个进程里驱动「Python 投出的字节 → Go 的领域入口」，
// 而那一侧的连接是它自己起的 broker 会话；把这条路径做成 Run 的私有细节，
// 互操作闸就只能去复制一份消费循环了。
func (r *Runner) Handle(ctx context.Context, d Delivery) (domain.ConsumeResult, error) {
	// 批 T-B 消费侧恢复：候选携带的是触发那条命令的请求 trace，
	// 注回 ctx 后 Admit 事务里 emit 的 follow-up outbox 行继续同链（G-B2 断言这一跳）。
	ctx = domain.WithTraceID(ctx, d.HeaderString(HeaderTraceID))
	msg, preErr := envelope(d)
	if preErr != nil {
		// 连幂等键都拿不到 ⇒ inbox 那条唯一键写不进去，留痕面根本不存在（18 §7）。
		// 这一条只能 reject（requeue=false）＋把 reason 打进指标面，而不是让它无限原地转。
		r.count(func(s *Stats) {
			s.Rejected++
			s.RejectsByReason[preErr.Code]++
		})
		if err := d.Nack(false); err != nil {
			r.count(func(s *Stats) { s.SettleErrs++ })
			return domain.ConsumeResult{Status: domain.ConsumeRejected, Detail: preErr.Detail},
				errors.Join(*preErr, err)
		}
		r.count(func(s *Stats) { s.Nacks++ })
		return domain.ConsumeResult{Status: domain.ConsumeRejected, Ack: false, Detail: preErr.Detail}, *preErr
	}

	fn := func(inner context.Context, st domain.Store) error {
		// 解码放在事务**内**：契约级非法要变成 `domain.PermanentRejection`，
		// 才会走 delivery.go:341-362 那条「主事务回滚＋事务外补 failed 留痕」的分道。
		// 三个领域入口仍各自开事务（这件事的连带风险登记在
		// DefaultPrefetch 那段与 tasks.md 的 R5b）；`st` 只有下面那条命令用得上。
		call, decErr := Decode(d, r.deps.Switch.MaxBodyBytes)
		if decErr != nil {
			// 解引用**不是**风格问题：`classifyFailure` 用 `errors.As(cause, &PermanentRejection{})`
			// 认这条路（delivery.go:344-345），而指针 `*PermanentRejection` 对值类型不可赋值。
			// 传指针＝契约级非法被读成瞬态 ⇒ 毒消息在阶梯上无限撞、永远不进留痕面，
			// 正是本文件上面那条分道要防的反面。闸在 TestDecodeRejectionReachesTheDomainClassifier。
			return *decErr
		}
		if err := call(inner, r.deps.Domain); err != nil {
			return err
		}
		// D-G14-②：候选进门之后，由这里请对面算下一段。放在 `call` 之后而不是之前——
		// 领域入口报错（瞬态）时链不许往前推，否则一次重投就多请一次，
		// 而命令 id 只由来源候选派生，第二次投进来的 id 与第一条**相同**，
		// 会被 outbox 的 UNIQUE(message_id) 撞掉 ⇒ 表现是「链有时不生效」而不是「链重复」。
		return r.requestFollowUp(inner, st, d)
	}
	res, err := r.deps.Domain.ConsumeIdempotent(ctx, msg, fn)
	switch res.Status {
	case domain.ConsumeProcessed:
		r.count(func(s *Stats) { s.Processed++ })
	case domain.ConsumeDuplicate:
		r.count(func(s *Stats) { s.Duplicates++ })
	case domain.ConsumeRejected:
		code := rejectCode(err)
		r.count(func(s *Stats) {
			s.Rejected++
			s.RejectsByReason[code]++
		})
	case domain.ConsumeTransient:
		r.count(func(s *Stats) { s.Transient++ })
	}
	if res.Ack {
		if aerr := d.Ack(); aerr != nil {
			r.count(func(s *Stats) { s.SettleErrs++ })
			return res, errors.Join(err, aerr)
		}
		r.count(func(s *Stats) { s.Acks++ })
		return res, err
	}
	// 瞬态：交回 broker 走阶梯（requeue=false ⇒ 30s 层，见 topology.go:188-193）。
	if nerr := d.Nack(false); nerr != nil {
		r.count(func(s *Stats) { s.SettleErrs++ })
		return res, errors.Join(err, nerr)
	}
	r.count(func(s *Stats) { s.Nacks++ })
	return res, err
}

// admittedKind 把「进门的那条路由键」读回「刚入库的候选是哪种」。
//
// 用 `CandidateKinds` 而不是再建一张表：那张表已经是「键 ⇒ 体内 kind」的唯一登记
// （`Decode` 用它判 routing key 与 kind 同义），第二张表迟早和它漂。
// evidence／proactive 不在 `CandidateKinds` 里（它们没有 kind 字段），
// 于是这里天然回 false——它们各自的触发面在领域那一侧，不在这条链上。
func admittedKind(routingKey string) (domain.Kind, bool) {
	protoKind, ok := CandidateKinds[routingKey]
	if !ok {
		return "", false
	}
	return mappedKind(protoKind)
}

// requestFollowUp 是一条候选进门之后唯一会往下推的一步（D-G14-②）。
//
// 三种「不推」分得很清楚，因为它们在排查时是三个问题：
//   - 这一种候选没有下一段（insight／evidence／proactive）⇒ `FollowUpSkipped`，正常；
//   - 链表有边但入队失败 ⇒ 把错误传出去 ⇒ inbox 事务回滚、不 ACK、走阶梯重投。
//     **不能**在这里吞掉：吞了就变成「候选收下了、下一段永远没请求」，
//     而这正是 R4/R5 一直拒绝的那种静默丢失。
//
// `st` 是 `ConsumeIdempotent` 给的那笔 inbox 事务，命令行就写在这里。
// ⚠️ 连带后果（登记，不粉饰）：三个领域入口各自再开事务（R5b ②），所以「候选行」与
// 「inbox 行＋命令行」是两笔 COMMIT。候选已提交而本事务回滚 ⇒ 重投时 inbox 不判重，
// 命令会随第二次消费重新入队，id 与第一次相同 ⇒ 撞 outbox 的 UNIQUE 而**再次失败**。
// 这一格真正收口要等 R5b 把领域入口改成复用调用方 Store；在那之前这条链的
// 判据面只能证到「命令与 inbox 同事务」。
func (r *Runner) requestFollowUp(ctx context.Context, st domain.Store, d Delivery) error {
	admitted, ok := admittedKind(d.RoutingKey)
	if !ok {
		r.count(func(s *Stats) { s.FollowUpSkipped++ })
		return nil
	}
	next, ok := domain.NextAITaskForKind(admitted)
	if !ok {
		r.count(func(s *Stats) { s.FollowUpSkipped++ })
		return nil
	}
	body, err := domain.TaskRequestBody(next, admitted, d.MessageID)
	if err != nil {
		r.count(func(s *Stats) { s.FollowUpErrors++ })
		return err
	}
	req := domain.AITaskRequest{
		Kind:        next,
		UserID:      strings.TrimSpace(d.HeaderString(HeaderUserID)),
		OperationID: domain.DeriveTaskOperationID(next, d.MessageID),
		Payload:     body,
	}
	if err := r.deps.Domain.RequestAITask(ctx, st, req); err != nil {
		r.count(func(s *Stats) { s.FollowUpErrors++ })
		return err
	}
	r.count(func(s *Stats) { s.FollowUpsEnqueued++ })
	r.logfC(ctx, "candidate: %s 入库 ⇒ 已入队下一段命令 %s（来源 message_id=%s）",
		admitted, next.RoutingKey(), d.MessageID)
	return nil
}

// envelope 抽出幂等三件套。只有 message_id 缺失是在这里判的——它是唯一键的一半，
// 缺了就既无法认领也无法留痕；其余契约判据（键↔类型、content_type、字节上限、user_id、
// schema_version）全在 `Decode` 里，因为那些要能在 inbox 上留下痕迹。
func envelope(d Delivery) (domain.InboundMessage, *domain.PermanentRejection) {
	id := strings.TrimSpace(d.MessageID)
	if id == "" {
		return domain.InboundMessage{}, &domain.PermanentRejection{
			Code:   ReasonMissingMessageID,
			Detail: "消息没有 message_id：(message_id, consumer) 唯一键少一半，无法认领也无法留痕",
		}
	}
	return domain.InboundMessage{MessageID: id, MessageType: d.MessageType,
		Consumer: Consumer, CorrelationID: d.CorrelationID}, nil
}

// logOutcome 只在**真有事**时打日志：重复消息在灰度期是常态（D-R5-6 明说同一命令会跑两次
// LLM），逐条打会把有用信息埋掉；而空轮本来就不产日志。
func (r *Runner) logOutcome(d Delivery, res domain.ConsumeResult, err error) {
	if res.Status == domain.ConsumeDuplicate {
		return
	}
	if err != nil {
		r.logfD(d, "inbound: %s 消费失败 status=%s ack=%v message_id=%s：%v",
			d.RoutingKey, res.Status, res.Ack, d.MessageID, err)
		return
	}
	r.logfD(d, "inbound: %s ⇒ %s（message_id=%s，ack=%v）%s",
		d.RoutingKey, res.Status, d.MessageID, res.Ack, res.Detail)
}

func (r *Runner) count(f func(*Stats)) {
	r.mu.Lock()
	f(&r.stat)
	r.mu.Unlock()
}

func (r *Runner) logf(format string, args ...any) { r.deps.Log(format, args...) }

// logfC 消息级日志（批 T-C）：ctx 里带着 Handle 入口恢复的 trace，
// obs 的 handler 自动补 trace_id/user_id 字段；生命周期日志仍走 deps.Log 回调。
func (r *Runner) logfC(ctx context.Context, format string, args ...any) {
	slog.InfoContext(ctx, "go-api: "+fmt.Sprintf(format, args...))
}

// logfD 从 Delivery 的 trace 头起一个一次性 ctx 再打日志：
// 消费结果（ack/拒/失败）发生在 Handle 的 ctx 之外，但同一批判据要求它们可 grep。
func (r *Runner) logfD(d Delivery, format string, args ...any) {
	r.logfC(domain.WithTraceID(context.Background(), d.HeaderString(HeaderTraceID)), format, args...)
}

// rejectCode 从 ConsumeIdempotent 带回的错误里取原始码面（PermanentRejection.Code）。
func rejectCode(err error) string {
	var rej domain.PermanentRejection
	if errors.As(err, &rej) && rej.Code != "" {
		return rej.Code
	}
	if err != nil {
		return "permanent_rejection"
	}
	return ""
}

// sleep 回 false＝ctx 在这期间结束了（调用方据此退出循环）。
func sleep(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}
