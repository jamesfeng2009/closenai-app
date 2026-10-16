// Command r5interop 是 R5-③ 跨语言互操作闸的 Go 那一半（沿 tools/aiclientprobe 的形态：
// pytest 当指挥，本命令只**如实报告观测值**，断言全在 backend/tests/test_r5_interop_broker.py）。
//
// 为什么要有这一层：R4-② 证的是「真 broker 收得下我们这套拓扑参数」，R5-② 证的是
// 「消费循环的次序与计数」——两侧都跑在 fake 或半程上。今天第一次要把
// **真 outbox 行 → 真 publisher → 真 Python 消费者 → 真候选 → 真 Go 入站 → 真 domain 入口 → 真 canonical 表**
// 整条串起来跑，而这条链上任何一跳的字节面写错（header 名、message_type 命名法、
// schema_version 的整数/字符串两形态、候选 id 的派生规则）都只在跨进程时才暴露。
//
// ⚠️ 本命令**不是生产入口**，也不进部署：生产里谁 spawn 谁已由 D-R5-1 裁成两个独立
// Deployment（实测 deploy/k8s 里三个 worker 键 0 命中，那条欠账归 R7）。
// 放在 tools/ 下是因为两条架构闸都排除这个目录（domain/arch_test.go 的 canonicaldb 闸
// 与 aiclient 的生成物闸），于是这一层既能装配真包，又不会被读成「生产码里多了一条入口」。
//
// 跑法（由 pytest 起停；手工跑法见 backend/tests/test_r5_interop_broker.py 头部）：
//
//	go run ./tools/r5interop -mode publish  -user u-1 -operation op-1
//	go run ./tools/r5interop -mode consume   -user u-1 -candidate-id cand-abc
//	go run ./tools/r5interop -mode replay    -operation op-1
//	go run ./tools/r5interop -mode cleanup   -user u-1 -operation op-1 -candidate-id cand-abc
//
// 输出恒为一行 JSON（日志一律走 stderr），退出码 0＝观测采集成功，1＝这一跳没走到预期形态。
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/dsn"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/idgen"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/amqp"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/dispatch"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/inbound"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/outbox"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store"
)

// 参数面只有五个：库与 broker 走 env（与 cmd/go-api 同两个键，凭据不进命令行也不进日志），
// 其余是这一条链的身份。`-candidate-id` **由对面 Python 的 candidate_message_id() 算好传进来**，
// 本命令不自己抄一遍派生规则——第二套实现就是第二套口径，而这条规则恰恰是 D-R5-5 的落点。
func main() {
	mode := flag.String("mode", "", "publish|consume|replay|candidate|cleanup（必填）")
	user := flag.String("user", "", "本次夹具的用户 id（≤36，逐字对齐 outbox_messages.user_id 列宽）")
	operation := flag.String("operation", "", "稳定逻辑操作 id＝命令的 message_id")
	candidateID := flag.String("candidate-id", "", "候选的 message_id，由 Python 侧 candidate_message_id() 派生")
	payload := flag.String("payload", "", "-mode candidate 要投的消息体（原样 JSON）")
	deadlineSeconds := flag.Int("deadline-seconds", 90, "每一步等结果的上限")
	flag.Parse()

	// stdout 只许有一行 JSON：pytest 要解析它，而 dispatch/inbound 的日志、amqp091 的
	// 任何内部输出都会把那一行冲掉。
	log.SetOutput(os.Stderr)
	log.SetPrefix("r5interop: ")

	rep := &report{Mode: *mode, User: *user, Operation: *operation,
		CandidateMessageID: *candidateID,
		// 这两格**永远都在**（见 report 里的注释）：本命令的产物是观测值，
		// 「观测到零次领域调用」必须写成 `{}`，不能写成「这一格不存在」——
		// 后一种形态下，pytest 那条 `admissions == {}` 会先撞 KeyError，
		// 于是失败信息指向工具而不是指向那一跳。
		Admissions: map[string]int{}, Settled: []settled{}}
	err := run(rep, *mode, *user, *operation, *candidateID, *payload,
		time.Duration(*deadlineSeconds)*time.Second)
	rep.OK = err == nil
	if err != nil {
		rep.Fail = err.Error()
	}
	encoded, marshalErr := json.Marshal(rep)
	if marshalErr != nil {
		log.Fatalf("报告编码失败：%v", marshalErr)
	}
	fmt.Println(string(encoded))
	if err != nil {
		os.Exit(1)
	}
}

// report 是观测值，不是断言。字段按模式复用，空值一律 omitempty，
// 好让 pytest 的失败信息里只出现这一轮真的观察到的东西。
type report struct {
	Mode               string         `json:"mode"`
	OK                 bool           `json:"ok"`
	Fail               string         `json:"fail,omitempty"`
	User               string         `json:"user,omitempty"`
	Operation          string         `json:"operation,omitempty"`
	CommandMessageID   string         `json:"command_message_id,omitempty"`
	CandidateMessageID string         `json:"candidate_message_id,omitempty"`
	EvidenceIDs        []string       `json:"evidence_ids,omitempty"`
	SourceRecordIDs    []string       `json:"source_record_ids,omitempty"`
	Payload            string         `json:"payload,omitempty"`
	OutboxRowID        string         `json:"outbox_row_id,omitempty"`
	OutboxStatus       string         `json:"outbox_status,omitempty"`
	OutboxAttempts     int            `json:"outbox_attempts,omitempty"`
	BodyBytes          int            `json:"body_bytes,omitempty"`
	PendingBefore      int            `json:"pending_before,omitempty"`
	DispatchStats      map[string]any `json:"dispatch_stats,omitempty"`
	InboundStats       map[string]any `json:"inbound_stats,omitempty"`
	// 这两个字段**不带 omitempty**：调用计数为零是这一轮最重要的观测之一
	// （`admissions == {}` 就是「第二次投递没生效」的判据），省掉那一格等于
	// 把「观察到零次」和「工具没报」压成同一个 JSON 形态。
	Admissions map[string]int `json:"admissions"`
	Settled    []settled      `json:"settled"`
}

// settled 是一条交付在这侧的最终观测：inbox 判成什么、那次有没有真的走进领域入口。
//
// `Admits` 是**这一条消息**引起的领域入口调用次数。tasks.md 的 R5 验收逐字要求
// 「重复消息第二次领域入口不被调用」，而聚合计数分不清是哪条消息触发的——
// 只有把它挂到 message_id 上，那条验收才在这条真链上可判。
type settled struct {
	MessageID string `json:"message_id"`
	Status    string `json:"status"`
	Ack       bool   `json:"ack"`
	Admits    int    `json:"admits"`
	Detail    string `json:"detail,omitempty"`
	Err       string `json:"err,omitempty"`
}

func run(rep *report, mode, user, operation, candidateID, payload string,
	deadline time.Duration) error {
	switch mode {
	case "publish":
		return publish(rep, user, operation, deadline)
	case "consume":
		return consume(rep, user, candidateID, deadline)
	case "replay":
		return replay(rep, operation, deadline)
	case "candidate":
		return publishCandidate(rep, user, candidateID, payload, deadline)
	case "cleanup":
		return cleanup(rep, user, operation, candidateID)
	default:
		return fmt.Errorf("未知 -mode %q（publish|consume|replay|candidate|cleanup）", mode)
	}
}

// ── 装配面 ────────────────────────────────────────────────

// pool 读 TEST_DATABASE_URL 并 Ping：与 internal/store 那批集成测同一个键、同一条
// dsn.Postgres 方言归一，所以「Go 能吃现网串」这件事在这里也顺带被测到。
func pool(ctx context.Context) (*pgxpool.Pool, error) {
	raw := strings.TrimSpace(os.Getenv("TEST_DATABASE_URL"))
	if raw == "" {
		return nil, fmt.Errorf("TEST_DATABASE_URL 未设置")
	}
	cfg, err := pgxpool.ParseConfig(dsn.Postgres(raw))
	if err != nil {
		return nil, fmt.Errorf("TEST_DATABASE_URL 解析失败：%w", err)
	}
	p, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, fmt.Errorf("建池：%w", err)
	}
	pingCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	if err := p.Ping(pingCtx); err != nil {
		p.Close()
		return nil, fmt.Errorf("ping 库失败：%w", err)
	}
	return p, nil
}

// brokerURL 与两侧 worker 共用同一个键（跨语言只有一个 broker 地址键，D-R5-9）。
func brokerURL() (string, error) {
	raw := strings.TrimSpace(os.Getenv("CLOSEN_AMQP_URL"))
	if raw == "" {
		return "", fmt.Errorf("CLOSEN_AMQP_URL 未设置")
	}
	return raw, nil
}

func stderrLog() func(string, ...any) {
	return func(format string, args ...any) { log.Printf(format, args...) }
}

// ── publish：真 outbox 行 → 真 dispatch worker → 真 broker ──

// publish 走完「种一个可用用户 → 决策事务内入队一条命令 → 起真 outbox worker 把它发出去」。
//
// 入队必须在调用方事务里（domain.RequestAITask 的那条取舍）：这里用 canonical.InTx 把
// 「决策」与「因它而发的命令」装进同一笔 COMMIT，所以这条链测的是生产的形状，
// 不是「先写库再补一条 outbox」那种永远撞不到的形状。
func publish(rep *report, user, operation string, deadline time.Duration) error {
	if user == "" || operation == "" {
		return fmt.Errorf("publish 需要 -user 与 -operation")
	}
	ctx := context.Background()
	db, err := pool(ctx)
	if err != nil {
		return err
	}
	defer db.Close()
	url, err := brokerURL()
	if err != nil {
		return err
	}
	canonical := store.NewCanonical(db)
	svc := domain.NewService(canonical, domain.DefaultPolicy())

	if err := seedUser(ctx, db, user); err != nil {
		return err
	}
	evidenceIDs, sourceIDs, err := seedEvidence(ctx, canonical, user)
	if err != nil {
		return err
	}
	rep.EvidenceIDs = evidenceIDs
	rep.SourceRecordIDs = sourceIDs

	// 命令体的形状按 contracts/proto/closenai_ai.proto 的 TaskRequested（protojson 的
	// lowerCamelCase），归属走 header 不写在体里——Python 的 parse_command 见到体内
	// user 字段就按 foreign_user_id 永久拒绝，这条链顺带把那个口径跑实。
	body, err := json.Marshal(map[string]any{
		"schemaVersion":   inbound.SupportedSchemaVersion,
		"taskType":        "TASK_TYPE_PATTERN_DETECT",
		"evidenceIds":     evidenceIDs,
		"sourceRecordIds": sourceIDs,
		"policyVersion":   "r5-interop",
		"promptVersion":   "v1",
	})
	if err != nil {
		return err
	}
	rep.Payload = string(body)

	pendingBefore, err := countPendingOutbox(ctx, db)
	if err != nil {
		return err
	}
	rep.PendingBefore = pendingBefore

	err = canonical.InTx(ctx, func(inner context.Context, st domain.Store) error {
		return svc.RequestAITask(inner, st, domain.AITaskRequest{
			Kind: domain.AITaskPattern, UserID: user, OperationID: operation, Payload: rep.Payload,
		})
	})
	if err != nil {
		return fmt.Errorf("入队命令：%w", err)
	}
	rep.CommandMessageID = operation

	worker, err := dispatch.New(dispatch.Deps{
		Dial: func(c context.Context, u string, timeout time.Duration) (dispatch.Session, error) {
			sess, err := amqp.Dial(c, u, timeout)
			if err != nil {
				return nil, err
			}
			return sess, nil
		},
		Repo:   canonical,
		Policy: domain.DefaultDeliveryPolicy(),
		Switch: dispatch.Switch{Enabled: true, URL: url, Poll: 200 * time.Millisecond,
			ConfirmTimeout: 5 * time.Second, DialTimeout: 10 * time.Second, DrainTimeout: 10 * time.Second},
		Log: stderrLog(),
	})
	if err != nil {
		return fmt.Errorf("装配 outbox worker：%w", err)
	}
	runCtx, cancel := context.WithCancel(ctx)
	done := make(chan error, 1)
	go func() { done <- worker.Run(runCtx) }()

	// 判据取**库里的状态**而不是 worker 自己的 accepted 计数，两个理由缺一不可：
	//   · 18 §6 要证的正是「broker 确认之后行才 published」，拿 worker 的计数当证据等于
	//     用它自己的话给它作证（变异打不掉）；
	//   · ClaimOutboxBatch 一批 50 条，会把别人留下的 pending 行一起认领。若夹具库里pending
	//     行多于 50，accepted>=1 可能是「发了别人的行」，而我们这条还在原地排队。
	if err := wait(deadline, func() bool {
		_, status, _, err := readOutboxRow(ctx, db, operation)
		return err == nil && status == "published"
	}); err != nil {
		cancel()
		rep.DispatchStats = dispatchView(worker.Stats())
		id, status, attempts, readErr := readOutboxRow(ctx, db, operation)
		rep.OutboxRowID, rep.OutboxStatus, rep.OutboxAttempts = id, status, attempts
		return fmt.Errorf("等这一条命令在 outbox 里变成 published（累计 accepted=%d）：%w（读回 %+v）",
			worker.Stats().Accepted, err, readErr)
	}
	cancel()
	rep.DispatchStats = dispatchView(worker.Stats())
	if err := <-done; err != nil {
		return fmt.Errorf("outbox worker 退出带回错误：%w", err)
	}

	id, status, attempts, err := readOutboxRow(ctx, db, operation)
	if err != nil {
		return err
	}
	rep.OutboxRowID, rep.OutboxStatus, rep.OutboxAttempts = id, status, attempts
	// 重读一次：published 之后到读回之间若有第二个 worker 把它改回 pending，这里会照实报出来。
	if status != "published" {
		return fmt.Errorf("命令的 outbox 行状态是 %q，不是 published", status)
	}
	return nil
}

// ── consume：真 broker → 真 inbound worker → 真 domain 入口 → 真 canonical 表 ──

func consume(rep *report, user, candidateID string, deadline time.Duration) error {
	if user == "" {
		return fmt.Errorf("consume 需要 -user")
	}
	ctx := context.Background()
	db, err := pool(ctx)
	if err != nil {
		return err
	}
	defer db.Close()
	url, err := brokerURL()
	if err != nil {
		return err
	}
	canonical := store.NewCanonical(db)
	rec := &recorder{inner: domain.NewService(canonical, domain.DefaultPolicy())}
	rep.Admissions = map[string]int{}

	candidates, err := inbound.New(inbound.Deps{
		Dial: func(c context.Context, u string, timeout time.Duration) (inbound.Session, error) {
			sess, err := amqp.Dial(c, u, timeout)
			if err != nil {
				return nil, err
			}
			return sess, nil
		},
		// 真领域入口，只是外面套了一层计数（见 recorder）。套这层的理由：
		// 「第二次投递不调用 Admit」这条验收必须在**真 Service** 上可判，
		// 而 R5-② 的调用计数测在 fake Domain 上——fake 只能证编排，证不了真 inbox 挡得住。
		Domain: rec,
		Switch: inbound.Switch{Enabled: true, URL: url,
			Prefetch: inbound.DefaultPrefetch, MaxBodyBytes: inbound.DefaultMaxBodyBytes,
			DialTimeout: 10 * time.Second, DrainTimeout: 10 * time.Second},
		Log: stderrLog(),
	})
	if err != nil {
		return fmt.Errorf("装配 candidate worker：%w", err)
	}
	runCtx, cancel := context.WithCancel(ctx)
	done := make(chan error, 1)
	go func() { done <- candidates.Run(runCtx) }()

	waitErr := wait(deadline, func() bool {
		if candidateID != "" {
			return rec.hasSettled(candidateID)
		}
		return rec.count() >= 1
	})
	stats := candidates.Stats()
	rep.InboundStats = inboundView(stats)
	rep.Settled = rec.snapshot()
	rep.Admissions = rec.admissions()
	cancel()
	if err := <-done; err != nil {
		return fmt.Errorf("candidate worker 退出带回错误：%w", err)
	}
	if waitErr != nil {
		return fmt.Errorf("等到现在共结算 %d 条：%w", rec.count(), waitErr)
	}
	return nil
}

// recorder 是包在真 *domain.Service 外面的一层计数，方法面逐字转交，一条都不拦。
//
// 为什么不是 fake：fake 只能证明「Runner 按编排调了我给它的方法」；而 R5-③ 要证的是
// 真 ConsumeIdempotent 的 inbox 认领位**真的**让第二次投递跳过真 Admit。
// 拦在真 Service 之前才是同一条事务路径上的观测。
type recorder struct {
	inner *domain.Service
	mu    sync.Mutex
	calls map[string]int
	seen  []settled
}

var _ inbound.Domain = (*recorder)(nil)

func (r *recorder) ConsumeIdempotent(ctx context.Context, msg domain.InboundMessage,
	fn func(context.Context, domain.Store) error) (domain.ConsumeResult, error) {
	before := r.admitCount()
	res, err := r.inner.ConsumeIdempotent(ctx, msg, fn)
	after := r.admitCount()
	r.mu.Lock()
	// 每条领域入口各算一次：三类候选的进门判据不同（D-R5-8），但「这条消息有没有引起
	// 领域写」在验收里是同一件事，所以这里合并成一个 admits 计数。
	r.seen = append(r.seen, settled{MessageID: msg.MessageID, Status: string(res.Status),
		Ack: res.Ack, Admits: after - before, Detail: res.Detail, Err: errText(err)})
	r.mu.Unlock()
	return res, err
}

func (r *recorder) Admit(ctx context.Context, c domain.Candidate) (domain.Result, error) {
	r.bump("Admit")
	return r.inner.Admit(ctx, c)
}

func (r *recorder) AdmitEvidence(ctx context.Context, c domain.EvidenceCandidate) (domain.EvidenceResult, error) {
	r.bump("AdmitEvidence")
	return r.inner.AdmitEvidence(ctx, c)
}

func (r *recorder) AdmitProactive(ctx context.Context, c domain.ProactiveCandidate) (domain.ProactiveResult, error) {
	r.bump("AdmitProactive")
	return r.inner.AdmitProactive(ctx, c)
}

func (r *recorder) bump(name string) {
	r.mu.Lock()
	if r.calls == nil {
		r.calls = map[string]int{}
	}
	r.calls[name]++
	r.mu.Unlock()
}

func (r *recorder) admitCount() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	total := 0
	for _, n := range r.calls {
		total += n
	}
	return total
}

func (r *recorder) admissions() map[string]int {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := make(map[string]int, len(r.calls))
	for k, v := range r.calls {
		out[k] = v
	}
	return out
}

func (r *recorder) hasSettled(messageID string) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, s := range r.seen {
		if s.MessageID == messageID {
			return true
		}
	}
	return false
}

func (r *recorder) count() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.seen)
}

func (r *recorder) snapshot() []settled {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]settled(nil), r.seen...)
}

// errText 把 error 拍平成 JSON 里的一个字符串字段：settled 要 omitempty，
// 而 `error` 本身既不能 omitempty 也没法稳定 marshal（nil 与非 nil 的表示不同）。
func errText(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

// ── replay：把同一条命令按同一串字节再投一次 ──

// replay 读回那条已 published 的 outbox 行，用**真 publisher** 原样再投一次。
//
// 为什么直接读库而不是重新入队：`outbox_messages.message_id` 是 UNIQUE 的，
// 同一次逻辑操作重放**必须**给同一个 id（18 §10），重新入队只会撞唯一键——
// 而那恰恰是这条链要的形态：一次真重放（broker 双投、worker 重启后重复消费）
// 在权威侧不许变成第二次生效。
func replay(rep *report, operation string, deadline time.Duration) error {
	if operation == "" {
		return fmt.Errorf("replay 需要 -operation")
	}
	ctx := context.Background()
	db, err := pool(ctx)
	if err != nil {
		return err
	}
	defer db.Close()
	url, err := brokerURL()
	if err != nil {
		return err
	}
	id, _, attempts, err := readOutboxRow(ctx, db, operation)
	if err != nil {
		return err
	}
	env, err := readOutboxEnvelope(ctx, db, operation, attempts)
	if err != nil {
		return err
	}
	rep.OutboxRowID = id
	rep.CommandMessageID = env.MessageID
	rep.BodyBytes = len(env.Payload)

	sess, err := amqp.Dial(ctx, url, 10*time.Second)
	if err != nil {
		return err
	}
	defer sess.Close()
	if err := sess.Declare(); err != nil {
		return fmt.Errorf("声明拓扑：%w", err)
	}
	pub, err := sess.Publisher(5 * time.Second)
	if err != nil {
		return err
	}
	publishDone := make(chan error, 1)
	go func() { publishDone <- pub.Publish(ctx, env) }()
	select {
	case err := <-publishDone:
		if err != nil {
			return fmt.Errorf("重放这一条：%w", err)
		}
	case <-time.After(deadline):
		return fmt.Errorf("重放没在 %v 内拿到 broker 确认", deadline)
	}
	rep.OutboxStatus = "replayed"
	return nil
}

// ── candidate：反向不变性（D-R5-10）────────────────────────

// publishCandidate 往回投面投一条候选形状的消息，用来证明 **Python 不消费这条面**：
// 回投面只有 Go 的入站 worker 订阅（topology.go 的 CandidateRoutes），Python 那四个
// 队列全是命令面。今天 Python 侧一个字节都不写 canonical（D-R5-10 的现役不变性），
// 而这条不变性的**运行时**证据只能是「同一条候选在 broker 上过了一遍，权威表没动」。
func publishCandidate(rep *report, user, candidateID, payload string, deadline time.Duration) error {
	if user == "" || candidateID == "" || payload == "" {
		return fmt.Errorf("candidate 需要 -user、-candidate-id 与 -payload")
	}
	ctx := context.Background()
	url, err := brokerURL()
	if err != nil {
		return err
	}
	env := outbox.Envelope{MessageID: candidateID, MessageType: "candidate_pattern",
		SchemaVersion: 1, RoutingKey: "candidate.pattern", UserID: user,
		CorrelationID: candidateID, CausationID: "r5-interop-reverse", Payload: payload}
	sess, err := amqp.Dial(ctx, url, 10*time.Second)
	if err != nil {
		return err
	}
	defer sess.Close()
	if err := sess.Declare(); err != nil {
		return fmt.Errorf("声明拓扑：%w", err)
	}
	pub, err := sess.Publisher(5 * time.Second)
	if err != nil {
		return err
	}
	publishDone := make(chan error, 1)
	go func() { publishDone <- pub.Publish(ctx, env) }()
	select {
	case err := <-publishDone:
		if err != nil {
			return fmt.Errorf("投这条候选：%w", err)
		}
	case <-time.After(deadline):
		return fmt.Errorf("投候选没在 %v 内拿到 broker 确认", deadline)
	}
	rep.BodyBytes = len(payload)
	rep.CandidateMessageID = candidateID
	return nil
}

// ── cleanup ───────────────────────────────────────────────

// cleanup 按 user 把这一轮种下的行删掉。inbox 那一侧按 message_id 删：
// `inbox_messages` 没有 user 列（18 §7 的唯一键是 (message_id, consumer)），
// 只能拿对面算好的候选 id 当删除条件——所以那条 id 必须由 Python 传进来，
// 在这里再实现一遍派生规则就是自己造第二套口径。
func cleanup(rep *report, user, operation, candidateID string) error {
	if user == "" {
		return fmt.Errorf("cleanup 需要 -user")
	}
	ctx := context.Background()
	db, err := pool(ctx)
	if err != nil {
		return err
	}
	defer db.Close()
	var errs []string
	for _, stmt := range []struct {
		sql  string
		args []any
	}{
		{`DELETE FROM pattern_evidence WHERE user_id = $1`, []any{user}},
		{`DELETE FROM insight_evidence WHERE user_id = $1`, []any{user}},
		{`DELETE FROM fact_evidence WHERE user_id = $1`, []any{user}},
		{`DELETE FROM insight_patterns WHERE user_id = $1`, []any{user}},
		{`DELETE FROM personal_model_events WHERE user_id = $1`, []any{user}},
		{`DELETE FROM personal_model_versions WHERE user_id = $1`, []any{user}},
		{`DELETE FROM patterns WHERE user_id = $1`, []any{user}},
		{`DELETE FROM insights WHERE user_id = $1`, []any{user}},
		{`DELETE FROM personal_facts WHERE user_id = $1`, []any{user}},
		{`DELETE FROM evidence WHERE user_id = $1`, []any{user}},
		{`DELETE FROM source_records WHERE user_id = $1`, []any{user}},
		{`DELETE FROM personal_models WHERE user_id = $1`, []any{user}},
		{`DELETE FROM outbox_messages WHERE user_id = $1`, []any{user}},
	} {
		if _, err := db.Exec(ctx, stmt.sql, stmt.args...); err != nil {
			errs = append(errs, fmt.Sprintf("%s: %v", stmt.sql, err))
		}
	}
	if operation != "" {
		if _, err := db.Exec(ctx, `DELETE FROM outbox_messages WHERE message_id = $1`, operation); err != nil {
			errs = append(errs, err.Error())
		}
	}
	for _, id := range strings.Split(candidateID, ",") {
		id = strings.TrimSpace(id)
		if id == "" {
			continue
		}
		if _, err := db.Exec(ctx,
			`DELETE FROM inbox_messages WHERE message_id = $1 AND consumer = $2`,
			id, inbound.Consumer); err != nil {
			errs = append(errs, err.Error())
		}
	}
	if _, err := db.Exec(ctx, `DELETE FROM users WHERE id = $1`, user); err != nil {
		errs = append(errs, err.Error())
	}
	if len(errs) > 0 {
		return fmt.Errorf("清理没清干净：%s", strings.Join(errs, " | "))
	}
	return nil
}

// ── 夹具与只读面（本命令直连 SQL 的三处，都不越架构边界）──
//
// 这三处是**测试夹具**，不是生产读写面：users 行与证据行的种法与 internal/store
// 那批集成测逐字同形，outbox 行的读回是这一轮断言要独立核实的对象（不能拿 worker
// 自己的计数当证据）。domain 的写入口一个都没绕。

func seedUser(ctx context.Context, db *pgxpool.Pool, user string) error {
	_, err := db.Exec(ctx, `
		INSERT INTO users (id, timezone, streak_count, relationship_level, chat_count,
			daily_quota_used, tier, created_at, push_frequency, push_enabled,
			ai_consent, status)
		VALUES ($1, 'Asia/Shanghai', 0, 'stranger', 0, 0, 'free', now(), 'daily', true, true, 'active')
		ON CONFLICT (id) DO NOTHING`, user)
	if err != nil {
		return fmt.Errorf("种 users 行：%w", err)
	}
	return nil
}

// seedEvidence 种两条**已算分**的支撑证据（分日：03 §6 的同日观测只算一条独立支撑）。
// 走的是 domain.Store 的真写面，不碰 sqlc 生成物——那条闸（arch_test.go）管的是全模块，
// 本命令在 tools/ 下也一样不许绕。
func seedEvidence(ctx context.Context, canonical *store.Canonical, user string) ([]string, []string, error) {
	var evidenceIDs, sourceIDs []string
	err := canonical.InTx(ctx, func(inner context.Context, st domain.Store) error {
		if _, _, err := st.EnsurePersonalModel(inner, user); err != nil {
			return err
		}
		for i, daysAgo := range []int{3, 7} {
			at := time.Now().UTC().AddDate(0, 0, -daysAgo)
			at = time.Date(at.Year(), at.Month(), at.Day(), 12, 0, 0, 0, time.UTC)
			sourceID, err := st.UpsertSourceRecord(inner, domain.SourceRecordRow{
				ID: idgen.New(), UserID: user, SourceType: "journal_entry",
				SourceTable: "memories", SourceID: idgen.New(),
				OccurredAt: at, HasOccurredAt: true,
			})
			if err != nil {
				return err
			}
			id := idgen.New()
			if err := st.InsertEvidence(inner, domain.EvidenceWrite{
				ID: id, UserID: user, SourceRecordID: sourceID,
				EvidenceType: "direct", Role: "supporting",
				Strength: 0.9, HasStrength: true,
				Observation:     fmt.Sprintf("r5 interop planted observation %d", i),
				Excerpt:         "r5 interop excerpt",
				Metadata:        "{}",
				GenerationRunID: idgen.New(),
			}); err != nil {
				return err
			}
			sourceIDs = append(sourceIDs, sourceID)
			evidenceIDs = append(evidenceIDs, id)
		}
		return nil
	})
	if err != nil {
		return nil, nil, fmt.Errorf("种证据：%w", err)
	}
	return evidenceIDs, sourceIDs, nil
}

func countPendingOutbox(ctx context.Context, db *pgxpool.Pool) (int, error) {
	var n int
	if err := db.QueryRow(ctx,
		`SELECT count(*) FROM outbox_messages WHERE status = 'pending'`).Scan(&n); err != nil {
		return 0, fmt.Errorf("数待投行：%w", err)
	}
	return n, nil
}

func readOutboxRow(ctx context.Context, db *pgxpool.Pool, messageID string) (id, status string, attempts int, err error) {
	err = db.QueryRow(ctx, `
		SELECT id, status, attempt_count FROM outbox_messages WHERE message_id = $1`,
		messageID).Scan(&id, &status, &attempts)
	if err != nil {
		return "", "", 0, fmt.Errorf("读回 outbox 行 %s：%w", messageID, err)
	}
	return id, status, attempts, nil
}

func readOutboxEnvelope(ctx context.Context, db *pgxpool.Pool, messageID string,
	attempts int) (outbox.Envelope, error) {
	var env outbox.Envelope
	err := db.QueryRow(ctx, `
		SELECT message_id, message_type, schema_version, routing_key, user_id,
		       correlation_id, causation_id, payload
		FROM outbox_messages WHERE message_id = $1`, messageID).
		Scan(&env.MessageID, &env.MessageType, &env.SchemaVersion, &env.RoutingKey,
			&env.UserID, &env.CorrelationID, &env.CausationID, &env.Payload)
	if err != nil {
		return outbox.Envelope{}, fmt.Errorf("读回重放用的 outbox 行 %s：%w", messageID, err)
	}
	env.AttemptCount = attempts
	return env, nil
}

// ── 指标面（R5-④ 的同形口径）─────────────────────────────

// dispatchView / inboundView 把两个 Stats 结构落成**显式命名**的 JSON 面。
//
// 为什么不直接 marshal 结构体：Go 的字段名是大写驼峰，而对面 Python 的
// `WorkerStats.snapshot()` 是小写下划线。这一层把命名写死在这里，
// pytest 那条同形闸（test_metrics_surfaces_share_the_vocabulary）才有可比的东西，
// 也让「改 Go 字段名会连带改指标名」这件事变成一次显式决定而不是顺手重构。
func dispatchView(s dispatch.Stats) map[string]any {
	return map[string]any{
		"batches": s.Batches, "claimed": s.Claimed, "accepted": s.Accepted,
		"rejected": s.Rejected, "dead_lettered": s.DeadLettered, "lost": s.Lost,
		"unrecorded": s.Unrecorded, "dials": s.Dials, "redials": s.Redials,
		"claim_errors": s.ClaimErrors,
	}
}

func inboundView(s inbound.Stats) map[string]any {
	return map[string]any{
		"received": s.Received, "processed": s.Processed, "duplicates": s.Duplicates,
		"rejected": s.Rejected, "transient": s.Transient, "acks": s.Acks,
		"nacks": s.Nacks, "settle_errors": s.SettleErrs, "dials": s.Dials,
		"redials": s.Redials, "rejects_by_reason": s.RejectsByReason,
	}
}

// wait 每 100ms 轮一次判据；超 deadline 回错误（**不是**静默返回）。
// 这一条是「别把它当绿」的进程面版本：这一步没走到，pytest 必须拿到非零退出码。
func wait(deadline time.Duration, ok func() bool) error {
	deadlineAt := time.Now().Add(deadline)
	for time.Now().Before(deadlineAt) {
		if ok() {
			return nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	return fmt.Errorf("超过 %v 仍没等到预期结果", deadline)
}
