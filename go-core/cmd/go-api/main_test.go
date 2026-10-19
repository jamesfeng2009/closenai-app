// 进程接线的闸（R4-④）。
//
// dispatch 包自己的十三道闸判的是 Runner 的行为，它们**看不见调用点**：
// 把 main.go 里的 spawn 整段删掉，那十三条一条都不会红。所以这个文件判的
// 就是「main 到底有没有接上去、接上去的那段会不会把退出路径搞坏」。
package main

import (
	"bytes"
	"context"
	"errors"
	"log"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/dispatch"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/dlq"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/inbound"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/outbox"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/obs"
)

// blockingSession 的 Close 一直不返回：用来造「worker 卡在收尾里出不来」的形态。
// Publisher 必须**成功**返回，Runner 才会把这条会话登记成当前会话，
// 停机时才走得到 closeSession；回错的话 Run 直接退出，这条用例就测不到挂死。
type blockingSession struct{ release chan struct{} }

func (b *blockingSession) Declare() error { return nil }
func (b *blockingSession) Publisher(time.Duration) (outbox.Publisher, error) {
	return nopPublisher{}, nil
}
func (b *blockingSession) Alive() bool { return true }
func (b *blockingSession) Close() error {
	<-b.release
	return nil
}

type nopPublisher struct{}

func (nopPublisher) Publish(context.Context, outbox.Envelope) error { return nil }

type countingRepo struct{ claims int }

func (r *countingRepo) ClaimOutboxBatch(context.Context, int, int) ([]domain.OutboxClaim, error) {
	r.claims++
	return nil, nil
}
func (r *countingRepo) MarkOutboxPublished(context.Context, string) (bool, error) {
	return true, nil
}
func (r *countingRepo) MarkOutboxRetry(context.Context, string, time.Duration, string) (bool, error) {
	return true, nil
}
func (r *countingRepo) MarkOutboxDeadLettered(context.Context, string, string) (bool, error) {
	return true, nil
}

// logToBuffer 把全局 log 与全局 slog 暂时接进缓冲区，返回取内容的闭包。
// 批 T-C 之后退出路径的话出自 slog（JSON 行），log 那一路只剩 Fatalf 之类还有标准库输出，
// 两路都收进同一个 buf：断言用的子串在 JSON 的 "msg" 里原样可见，判据不必改。
func logToBuffer(t *testing.T, f func()) string {
	t.Helper()
	var buf bytes.Buffer
	orig := log.Writer()
	origSlog := slog.Default()
	log.SetOutput(&buf)
	slog.SetDefault(slog.New(slog.NewJSONHandler(&buf, nil)))
	defer func() {
		log.SetOutput(orig)
		slog.SetDefault(origSlog)
	}()
	f()
	return buf.String()
}

// loggerAdapter 与 main.go 逐字同路：批 T-C 之后这就是同一个函数，
// 「抄写漂移」那种假绿形态被结构排除了。用例走同一条路，否则它会绿在
// 「日志压根没出境」那种假形态上。
func loggerAdapter() func(string, ...any) { return logBridge }

// TestMainSpawnsTheWorker 是调用点闸：接线段被删掉时 dispatch 的十三条全绿，
// 所以这条只能盯源码。它点名的四个符号各对应一种「接了但接错」的形态。
func TestMainSpawnsTheWorker(t *testing.T) {
	src, err := os.ReadFile(filepath.Join(".", "main.go"))
	if err != nil {
		t.Fatalf("main.go 读不出：%v", err)
	}
	for _, phrase := range []string{
		"dispatch.LoadSwitch",   // 开关面走 env，不写死、也不塞进跨语言的 config
		"dispatch.New(",         // 装配期就校验（开了没给 URL 要拒绝启动）
		"worker.Run(workerCtx)", // 真把循环起起来
		"defer closeWorker(",    // 退出路径等它收尾
	} {
		if !strings.Contains(string(src), phrase) {
			t.Errorf("main.go 里没有 %q ⇒ worker 没接上进程，而 dispatch 包的用例照样全绿（假绿）", phrase)
		}
	}
}

// TestCloseWorkerExitsFastWhenSwitchOff：默认（谁都不给键）时停机不许多等一秒。
// 这条是「接线不改变现网行为」在退出路径上的证据——灰度 0 时 pod 的停止耗时
// 不该因为这段代码而变长，而且要说一句「未启动」。
func TestCloseWorkerExitsFastWhenSwitchOff(t *testing.T) {
	repo := &countingRepo{}
	off := dispatch.Switch{Poll: time.Millisecond, ConfirmTimeout: time.Second,
		DialTimeout: time.Second, DrainTimeout: time.Second}
	runs, err := dispatch.New(dispatch.Deps{
		Dial: func(context.Context, string, time.Duration) (dispatch.Session, error) {
			t.Error("开关关着还拨号")
			return nil, errors.New("fake: 不该拨号")
		},
		Repo: repo, Policy: domain.DefaultDeliveryPolicy(), Switch: off, Log: loggerAdapter(),
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- runs.Run(ctx) }()

	started := time.Now()
	logs := logToBuffer(t, func() { closeWorker(runs, cancel, done, 3*time.Second) })
	if elapsed := time.Since(started); elapsed > time.Second {
		t.Errorf("开关关着却等了 %v ⇒ 这段接线给停机加了不必要的等待", elapsed)
	}
	if !strings.Contains(logs, "batches=0") {
		// 判据故意**不**用「未启动」那四个字：Run 里那行也这么说，而它是另一个 goroutine
		// 打的，与这里的 log 重定向没有任何同步（实测 300 轮抖 1 次）。
		// `batches=0` 只有 closeWorker 会说 ⇒ 摘掉退出路径那句日志，这条必红（M-e 同形）。
		t.Errorf("关着的时候退出路径没交代零批次这件事：%q", logs)
	}
	if repo.claims != 0 {
		t.Errorf("开关关着却认领了 %d 批", repo.claims)
	}
}

// TestCloseWorkerGivesUpOnBudget：worker 出不来时**放弃等待**而不是挂死。
// 挂在退出路径上的进程会被 k8s 在 gracePeriod 到点后 SIGKILL，表现是「停机失败」，
// 而这里要的是「按预算收尾，没发完的行交给租约到期重投」。
func TestCloseWorkerGivesUpOnBudget(t *testing.T) {
	release := make(chan struct{})
	sess := &blockingSession{release: release}
	t.Cleanup(func() { close(release) })

	on := dispatch.Switch{Enabled: true, URL: "amqp://127.0.0.1:5672/",
		Poll: time.Millisecond, ConfirmTimeout: time.Second,
		DialTimeout: time.Second, DrainTimeout: time.Second}
	runs, err := dispatch.New(dispatch.Deps{
		Dial: func(context.Context, string, time.Duration) (dispatch.Session, error) {
			return sess, nil
		},
		Repo: &countingRepo{}, Policy: domain.DefaultDeliveryPolicy(), Switch: on,
		Log: loggerAdapter(),
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- runs.Run(ctx) }()
	// 先让 worker 进一次会话，再走停机。
	time.Sleep(30 * time.Millisecond)

	budget := 200 * time.Millisecond
	started := time.Now()
	logs := logToBuffer(t, func() { closeWorker(runs, cancel, done, budget) })
	if elapsed := time.Since(started); elapsed > 3*time.Second {
		t.Errorf("closeWorker 等了 %v ⇒ 预算 %v 没生效，退出路径会挂死", elapsed, budget)
	}
	if !strings.Contains(logs, "没退出") {
		t.Errorf("超时却不说「放弃等待」，运维读到的是一次静默停机：%q", logs)
	}
}

// ── R5-②：candidate 入站 worker ────────────────────────────────────────────

// blockingInboundSession 的 Close 一直不返回：造「入站 worker 卡在收尾里出不来」的形态。
// Consume 返回一条**永不交付**的通道，于是 Run 停在 select 上，取消 ctx 就能走到 closeSession。
type blockingInboundSession struct{ release chan struct{} }

func (b *blockingInboundSession) Declare() error { return nil }

func (b *blockingInboundSession) Consume(context.Context, string, int) (<-chan inbound.Delivery, error) {
	return make(chan inbound.Delivery), nil
}

func (b *blockingInboundSession) Alive() bool { return true }

func (b *blockingInboundSession) Close() error {
	<-b.release
	return nil
}

// noopDomain 只为让 `inbound.New` 的装配期校验过关：这几条在退出路径的用例里
// 一次都不该被调用（真被调用了就是「开关关着却消费了」，由 inbound 包自己的用例判）。
type noopDomain struct{}

func (noopDomain) ConsumeIdempotent(context.Context, domain.InboundMessage,
	func(context.Context, domain.Store) error) (domain.ConsumeResult, error) {
	return domain.ConsumeResult{}, errors.New("main_test: 退出路径不该消费")
}

func (noopDomain) Admit(context.Context, domain.Candidate) (domain.Result, error) {
	return domain.Result{}, errors.New("main_test: 退出路径不该写 canonical")
}

func (noopDomain) AdmitEvidence(context.Context, domain.EvidenceCandidate) (domain.EvidenceResult, error) {
	return domain.EvidenceResult{}, errors.New("main_test: 退出路径不该写 canonical")
}

func (noopDomain) AdmitProactive(context.Context, domain.ProactiveCandidate) (domain.ProactiveResult, error) {
	return domain.ProactiveResult{}, errors.New("main_test: 退出路径不该写 canonical")
}

// D-G14-② 之后这条也在接口上：命令若在这几条用例里被入队，就是「开关关着却写了两张表」。
func (noopDomain) RequestAITask(context.Context, domain.Store, domain.AITaskRequest) error {
	return errors.New("main_test: 退出路径不该入队命令")
}

// T2-06 事件面同一条理由：这几条用例只验停机编排，删除传播若在其中被跑起来，
// 就是「开关关着却翻了 source_records」。
func (noopDomain) InvalidateSource(context.Context,
	domain.SourcePointer) (domain.SourceInvalidation, error) {
	return domain.SourceInvalidation{}, errors.New("main_test: 退出路径不该翻登记行状态")
}

// TestMainSpawnsTheCandidateWorker 是 R5-② 的调用点闸。
//
// inbound 包那二十来条闸判的是 Runner 的行为，它们**看不见 main**：
// 把这段接线整段删掉，inbound 的用例一条都不会红。这里点名的五个符号各挡一种形态：
//   - `inbound.LoadSwitch`  ⇒ 开关走 env，不写死（灰度 0 由它保证）；
//   - `domain.NewService(canonical` ⇒ 领域写入口是 canonical 的那个 `*domain.Service`，
//     不是同名直觉上的 auth `service`（那是一条能编译、能启动、却把候选投给错对象的接错线）；
//   - `inbound.New(` ⇒ 装配期就校验（开了没给 URL 要拒绝启动）；
//   - `candidates.Run(inboundCtx)` ⇒ 循环真起起来了；
//   - `defer closeInbound(` ⇒ 退出路径等它收尾。
func TestMainSpawnsTheCandidateWorker(t *testing.T) {
	src, err := os.ReadFile(filepath.Join(".", "main.go"))
	if err != nil {
		t.Fatalf("main.go 读不出：%v", err)
	}
	text := string(src)
	for _, phrase := range []string{
		"inbound.LoadSwitch",
		"domain.NewService(canonical",
		"inbound.New(",
		"candidates.Run(inboundCtx)",
		"defer closeInbound(",
	} {
		if !strings.Contains(text, phrase) {
			t.Errorf("main.go 里没有 %q ⇒ 入站面没接上进程，而 inbound 包的用例照样全绿（假绿）", phrase)
		}
	}
	// defer 的**登记顺序**就是停机顺序：入站必须在出箱之后登记（因而先执行），
	// 否则出箱先等完，入站刚产生的那批 outbox 行要多等一个租约周期。
	if strings.Index(text, "defer closeWorker(") > strings.Index(text, "defer closeInbound(") {
		t.Error("closeInbound 登记在 closeWorker 之前 ⇒ 停机顺序反了：入站会在出箱收尾之后才开始写 outbox 行")
	}
}

// TestMainSpawnsTheSourceDeletedWorker 是 T2-06 的调用点闸，形状与上面那条同。
//
// 为什么必须单独一条：`inbound` 包的用例判的是 Runner 与 `DecodeEvent`，它们**看不见 main**。
// 把 `inbound.NewSource(...)` 那一段删掉，事件面的每一条闸都还是绿的——而现网的表现是
// 「Python 投了 source.deleted，Go 的 `go.source.deleted` 里堆着没人认领，证据永远不失效」，
// 这正是 18 §13 点名要防的那种静默。
//
// 五格各挡一种「看起来接了其实没接」：
//   - `inbound.NewSource(` ⇒ 起的是事件面那条 Runner，不是又起了一个候选面；
//   - `sourceEvents.Run(sourceCtx)` ⇒ 循环真起起来了（只 New 不 Run 是本包最省事的假接线）；
//   - `defer closeInbound(sourceEvents` ⇒ 退出路径等它收尾；
//   - `Domain: candidateDomain,` 出现**两次** ⇒ 两条面共用同一个 canonical Service，
//     没有第二个领域入口来源；
//   - defer 登记顺序 ⇒ 事件面登记在候选面之后（因而先执行），候选面登记在出箱之前。
func TestMainSpawnsTheSourceDeletedWorker(t *testing.T) {
	src, err := os.ReadFile(filepath.Join(".", "main.go"))
	if err != nil {
		t.Fatalf("main.go 读不出：%v", err)
	}
	text := string(src)
	for _, phrase := range []string{
		"inbound.NewSource(",
		"sourceEvents.Run(sourceCtx)",
		"defer closeInbound(sourceEvents",
	} {
		if !strings.Contains(text, phrase) {
			t.Errorf("main.go 里没有 %q ⇒ 事件面没接上进程，而 inbound 包与 domain 包的用例照样全绿（假绿）", phrase)
		}
	}
	if n := strings.Count(text, "Domain: candidateDomain,"); n != 2 {
		t.Errorf("Domain: candidateDomain, 命中 %d 次，期望 2（候选面＋事件面）⇒ "+
			"少一次是没接上，多一次是有人新造了第二个领域入口来源", n)
	}
	iWorker := strings.Index(text, "defer closeWorker(")
	iCand := strings.Index(text, "defer closeInbound(candidates")
	iSrc := strings.Index(text, "defer closeInbound(sourceEvents")
	if iWorker < 0 || iCand < 0 || iSrc < 0 {
		t.Fatalf("defer 锚找不到：closeWorker=%d candidates=%d sourceEvents=%d", iWorker, iCand, iSrc)
	}
	if !(iWorker < iCand && iCand < iSrc) {
		t.Errorf("defer 登记顺序不是 closeWorker→candidates→sourceEvents（%d/%d/%d）⇒ "+
			"LIFO 下停机顺序反了：删除传播写出的 outbox 行会在出箱 worker 收尾之后才产生", iWorker, iCand, iSrc)
	}
	// kill switch：事件面**不许**有第二个 env 键（本批的裁决，理由在 NewSource 注释里）。
	lower := strings.ToLower(text)
	for _, banned := range []string{"closen_source_worker", "closen_event_worker", "closen_source_deleted"} {
		if strings.Contains(lower, banned) {
			t.Errorf("main.go 出现了 %q ⇒ 事件面发明了第二个旋钮，与「两共用一个 kill switch」的裁决冲突", banned)
		}
	}
}

// TestMainSpawnsTheQueueMetrics 是 T2-05 的调用点闸，形状与上面两条同。
//
// `dlq` 包那三十二道闸判的是「巡检会不会算错账」，`obs` 那八道判的是「日志形状对不对」，
// 两边都**看不见 main**：把这段接线整段删掉，它们一条都不会红。而现网的表现是
// 「消息在死信里堆着，值班查不到有没有人看」——18 §13 点名的正是这种静默。
//
// 五格各挡一种「看起来接了其实没接」：
//   - `queueMetricsTarget(sws, inboundSw)` ⇒ 起不起由**两个既有 kill switch** 决定，不是第三个旋钮；
//   - `metricsDone <- obs.RunQueueMetrics(metricsCtx, newQueueReader(url))` ⇒ 循环真起起来了，
//     而且取数口拿的是同一个地址（只 New 不 Run 是本包最省事的假接线）；
//   - `defer closeQueueMetrics(` ⇒ 退出路径收掉它；
//   - `dlq.ListAll(ctx, dlq.NewBrokerAdapter(sess.Channel()), nil)` ⇒ 复用 Session 里那条
//     **已经在 confirm 模式**的通道，且一次 `Declare` 都不做（巡检改了拓扑参数 ⇒ 下一次
//     worker 启动撞 PRECONDITION_FAILED，而那天没人改过代码）；
//   - `"queue metrics 未启动"` ⇒ 关着的时候在启动日志上交代一句，形状与两个 worker 的「未启动」一致。
//
// defer 登记顺序也钉住：指标面登记在三个 worker **之后** ⇒ LIFO 下它**先**停；它只读不写，
// 先停不欠任何补偿，反过来则会白白多占一次停机预算。
func TestMainSpawnsTheQueueMetrics(t *testing.T) {
	src, err := os.ReadFile(filepath.Join(".", "main.go"))
	if err != nil {
		t.Fatalf("main.go 读不出：%v", err)
	}
	text := string(src)
	for _, phrase := range []string{
		"queueMetricsTarget(sws, inboundSw)",
		"metricsDone <- obs.RunQueueMetrics(metricsCtx, newQueueReader(url))",
		"defer closeQueueMetrics(",
		"dlq.ListAll(ctx, dlq.NewBrokerAdapter(sess.Channel()), nil)",
		"queue metrics 未启动",
	} {
		if !strings.Contains(text, phrase) {
			t.Errorf("main.go 里没有 %q ⇒ 异步指标没接上进程，而 dlq 与 obs 两个包的用例照样全绿（假绿）", phrase)
		}
	}
	// 巡检面**不改拓扑**：`amqp.Declare(` 在装配点出现一次都不许。
	// 顺手声明一遍拓扑的代价不在今天——它把参数改了，下一次 worker 启动撞 PRECONDITION_FAILED，
	// 而那天没人改过代码，最难查。（正向证人：下面那条 Dial 计数读的是同一个文件、同一个计数函数，
	// 它此刻是 3；于是「扫到 0」不是因为脚本没读到内容。）
	if n := strings.Count(text, "amqp.Declare("); n != 0 {
		t.Errorf("main.go 里有 %d 处 amqp.Declare( ⇒ 巡检或装配动了拓扑，判据见上", n)
	}
	// 三处拨号逐字对应三个面：出箱 worker（:156 的拨号辅助）、入站 worker（:199）、巡检每轮重拨。
	// 多出来一处＝有人给某一面开了第二条会话；少一处＝那一面没接上。两侧不同源：这里数源码，
	// 期望值是 2026-10-19 数出来的字面量，不是从 main.go 里读回来的。
	if n := strings.Count(text, "amqp.Dial("); n != 3 {
		t.Errorf("amqp.Dial( 命中 %d 次，期望 3（出箱／入站／巡检各一处）⇒ 会话数与接线面数已经不一致", n)
	}
	iSrc := strings.Index(text, "defer closeInbound(sourceEvents")
	iMetrics := strings.Index(text, "defer closeQueueMetrics(")
	if iSrc < 0 || iMetrics < 0 {
		t.Fatalf("defer 锚找不到：sourceEvents=%d closeQueueMetrics=%d", iSrc, iMetrics)
	}
	if iMetrics < iSrc {
		t.Errorf("closeQueueMetrics 登记在事件面之前（%d<%d）⇒ LIFO 下它最后停，只读面白白占着停机预算", iMetrics, iSrc)
	}
	// kill switch：指标面**不许**有第二个 env 键（D-G12「键存在但没人读＝假旋钮」）。
	lower := strings.ToLower(text)
	for _, banned := range []string{
		"closen_queue_metrics", "closen_dlq_metrics", "closen_async_metrics", "closen_metrics_interval",
	} {
		if strings.Contains(lower, banned) {
			t.Errorf("main.go 出现了 %q ⇒ 指标面发明了第二个旋钮，与「复用两个 worker 的 kill switch ＋ CLOSEN_AMQP_URL」的裁决冲突", banned)
		}
	}
	// 巡检周期是包内常量，注释里写明**不是 env 键**：装配点若去读它，就是给一条会漂的常量开口子。
	if strings.Contains(text, "QueueMetricsEvery()") {
		t.Error("main.go 调了 obs.QueueMetricsEvery() ⇒ 周期从包内常量变成了可拨的面，本批的裁决是它不读 env")
	}
}

func TestCloseInboundExitsFastWhenSwitchOff(t *testing.T) {
	sess := &blockingInboundSession{release: make(chan struct{})}
	close(sess.release) // 这条用例不该走到 Close；走到的话让它立刻放行，好把「走到了」暴露成计数
	dials := 0
	off := inbound.LoadSwitch(func(string) string { return "" })
	runs, err := inbound.New(inbound.Deps{
		Dial: func(context.Context, string, time.Duration) (inbound.Session, error) {
			dials++
			return sess, nil
		},
		Domain: noopDomain{}, Switch: off, Log: inboundLogger(),
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- runs.Run(ctx) }()

	started := time.Now()
	logs := logToBuffer(t, func() { closeInbound(runs, cancel, done, 3*time.Second) })
	if elapsed := time.Since(started); elapsed > time.Second {
		t.Errorf("开关关着却等了 %v ⇒ 这段接线给停机加了不必要的等待", elapsed)
	}
	if !strings.Contains(logs, "received=0") {
		// 同 closeWorker 那条：判据不用「未启动」，那四个字 Run 的 goroutine 也会说，
		// 而它落不落在缓冲区里没有任何同步保证；`received=0` 只有退出路径会说。
		t.Errorf("关着的时候退出路径没交代零消费这件事：%q", logs)
	}
	if dials != 0 {
		t.Errorf("开关关着却拨了 %d 次号", dials)
	}
	if st := runs.Stats(); st.Received != 0 || st.Acks != 0 {
		t.Errorf("关着的 worker 有计数：%+v", st)
	}
}

func TestCloseInboundGivesUpOnBudget(t *testing.T) {
	release := make(chan struct{})
	sess := &blockingInboundSession{release: release}
	t.Cleanup(func() { close(release) })

	on := onInboundSwitch()
	runs, err := inbound.New(inbound.Deps{
		Dial: func(context.Context, string, time.Duration) (inbound.Session, error) {
			return sess, nil
		},
		Domain: noopDomain{}, Switch: on, Log: inboundLogger(),
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- runs.Run(ctx) }()
	time.Sleep(30 * time.Millisecond) // 先进一次会话，停机才走得到 closeSession

	budget := 200 * time.Millisecond
	started := time.Now()
	logs := logToBuffer(t, func() { closeInbound(runs, cancel, done, budget) })
	if elapsed := time.Since(started); elapsed > 3*time.Second {
		t.Errorf("closeInbound 等了 %v ⇒ 预算 %v 没生效，退出路径会挂死", elapsed, budget)
	}
	if !strings.Contains(logs, "没退出") {
		t.Errorf("超时却不说「放弃等待」，运维读到的是一次静默停机：%q", logs)
	}
	// 放弃等待的正确性依据写在这一格里：没 ack 的消息由 broker 重投，
	// 幂等键保证领域入口不跑第二次——所以这条路径的代价是延迟，不是重复生效。
	if !strings.Contains(logs, "broker 重投") {
		t.Errorf("超时日志没交代消息的去向：%q", logs)
	}
}

func inboundLogger() func(string, ...any) { return logBridge }

func onInboundSwitch() inbound.Switch {
	sw := inbound.LoadSwitch(func(k string) string {
		if k == inbound.EnvWorker {
			return "1"
		}
		return map[string]string{inbound.EnvURL: "amqp://127.0.0.1:5672/"}[k]
	})
	sw.DialTimeout = time.Second
	sw.DrainTimeout = time.Second
	return sw
}

// ── T2-05：异步指标的接线面 ─────────────────────────────────────────────

// TestQueueMetricsTargetTruthTable 判「该不该起、拿哪个地址」这四条边。
//
// 这一格是那段接线里唯一会**改变现网行为**的判据：两个 worker 都关着（＝灰度 0）时，
// 进程必须像这段代码不存在一样——不拨号、不打点。写反了的表现不是报错，是每分钟白拨一次号
// 并在 Loki 里造出一堆与任何真实流量无关的读数。
func TestQueueMetricsTargetTruthTable(t *testing.T) {
	cases := []struct {
		name      string
		outbox    dispatch.Switch
		candidate inbound.Switch
		wantURL   string
		wantOn    bool
	}{
		{"两个都关（现网默认）⇒ 不起", dispatch.Switch{}, inbound.Switch{}, "", false},
		{"只开出箱 ⇒ 用出箱的地址", dispatch.Switch{Enabled: true, URL: "amqp://o/"}, inbound.Switch{}, "amqp://o/", true},
		{"只开入站 ⇒ 地址退回入站的键", dispatch.Switch{}, inbound.Switch{Enabled: true, URL: "amqp://i/"}, "amqp://i/", true},
		{"两个都开 ⇒ 出箱优先（同一条链同一个 broker）",
			dispatch.Switch{Enabled: true, URL: "amqp://o/"}, inbound.Switch{Enabled: true, URL: "amqp://i/"}, "amqp://o/", true},
		{"开着但地址是空白 ⇒ 不起，也不拿空白去拨号",
			dispatch.Switch{Enabled: true, URL: "   "}, inbound.Switch{Enabled: true, URL: ""}, "", false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			url, ok := queueMetricsTarget(c.outbox, c.candidate)
			if ok != c.wantOn || url != c.wantURL {
				t.Errorf("queueMetricsTarget = (%q, %v)，期望 (%q, %v)", url, ok, c.wantURL, c.wantOn)
			}
		})
	}
}

// TestQueueStatsFromCarriesEveryField 是搬运面的闸：`obs` 不认识 AMQP、`dlq` 不认识日志形状，
// 两个结构之间的对应只有这一格，字段漏一个的表现是 Loki 里那一栏永远是 0 或 null。
//
// 断言两侧不同源：左边是手写的 dlq.Summary 字面量，右边是手写的 obs.QueueStat 字面量，
// 不是「拿同一个 struct 比它自己」那种恒真。
func TestQueueStatsFromCarriesEveryField(t *testing.T) {
	got := queueStatsFrom([]dlq.Summary{
		{
			Queue: "dlq.ai.observation.extract", Purpose: "ai.observation.extract", Kind: "dlq",
			Depth: 7, Consumers: 0, OldestAgeSeconds: 900.5, OldestKnown: true,
			Deaths: []dlq.DeathEntry{{Queue: "ai.observation.extract"}},
		},
		{
			Queue: "retry.30s.go.candidate.accept", Purpose: "go.candidate.accept", Kind: "retry",
			Depth: 0, Consumers: 3, OldestAgeSeconds: 0, OldestKnown: false,
			OldestUnknown: "queue has consumers",
		},
	})
	want := []obs.QueueStat{
		{Queue: "dlq.ai.observation.extract", Kind: "dlq", Depth: 7, Consumers: 0,
			OldestAgeSecs: 900.5, OldestAgeKnown: true},
		{Queue: "retry.30s.go.candidate.accept", Kind: "retry", Depth: 0, Consumers: 3,
			OldestAgeSecs: 0, OldestAgeKnown: false},
	}
	if len(got) != len(want) {
		t.Fatalf("搬了 %d 条，期望 %d：%+v", len(got), len(want), got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("第 %d 条搬运不符：\n got %+v\nwant %+v", i, got[i], want[i])
		}
	}
	// 空入参必须回空切片而不是 nil 里带 panic 风险：`tick` 直接 range，这一格是它的第一道依赖。
	if n := len(queueStatsFrom(nil)); n != 0 {
		t.Errorf("queueStatsFrom(nil) 回了 %d 条", n)
	}
}

// TestNewQueueReaderAgainstUnreachableBrokerErrorsNotEmpty 是失败恢复那一格：
// 拨号失败**必须回错误**，绝不能回「零条读数 + nil」。
//
// 表现差别在汇总行上：回错误时循环打的是 `queue_metrics_read_failed`（值班看得见「读不到」），
// 回空读数时打的是 `queue_metrics queues_scanned=0 queue_depth=0`——那是一条**假的健康记录**，
// 而 broker 挂着的时候恰恰是最需要有人知道的时候。
//
// 端口 1 上没有监听，所以这条不需要本机有 broker，也不会依赖 `CLOSEN_AMQP_URL` 有没有设。
func TestNewQueueReaderAgainstUnreachableBrokerErrorsNotEmpty(t *testing.T) {
	read := newQueueReader("amqp://127.0.0.1:1/")
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	stats, err := read(ctx)
	if err == nil {
		t.Fatalf("拨号失败却回了 nil 错误（%d 条读数）⇒ 巡检会把 broker 挂了读成一切正常", len(stats))
	}
	if stats != nil {
		t.Errorf("回了错误还带 %d 条读数，调用方该信哪一个：%+v", len(stats), stats)
	}
}

// TestCloseQueueMetricsCancelsExitsQuietly 判正常收尾：取消被调用、循环回来就收、
// 而 `context.Canceled` 这个**预期内**的退出理由不许打成错误。
func TestCloseQueueMetricsCancelsExitsQuietly(t *testing.T) {
	canceled := false
	done := make(chan error, 1)
	done <- context.Canceled
	logs := logToBuffer(t, func() {
		closeQueueMetrics(func() { canceled = true }, done)
	})
	if !canceled {
		t.Error("没取消 ctx ⇒ 循环会一直跑到进程被 SIGKILL，停机顺序变成运气问题")
	}
	if strings.Contains(logs, "没退出") {
		t.Errorf("循环明明回来了却说放弃等待：%q", logs)
	}
	if strings.Contains(logs, "带回错误") {
		t.Errorf("把预期内的取消打成了错误，值班会被一条假告警叫醒：%q", logs)
	}
}

// TestMainWiresTheInternalSurfaceToTheSameDomainService 是 T3-01／T3-02 的调用点闸，
// 形状与上面三条同（`httpapi`／`domain` 包的用例都看不见 main，摘掉接线它们照旧全绿）。
//
// 它挡的是本票裁决里最贵的一格：ADR 七-10 的 T3-02-5「内部面写口与两个 worker 共用同一个
// `*domain.Service`」。有人在这里新造一颗 `domain.NewService(...)` 喂给 `Outcomes`，
// 表现不是报错而是**两份乐观锁与两本版本账本各算各的**——反馈改写了洞察，worker 那边读到
// 的却还是旧版本，而两侧的用例都是绿的。所以这一条必须数符号而不是只查存在。
func TestMainWiresTheInternalSurfaceToTheSameDomainService(t *testing.T) {
	src, err := os.ReadFile(filepath.Join(".", "main.go"))
	if err != nil {
		t.Fatalf("main.go 读不出：%v", err)
	}
	text := string(src)
	for _, phrase := range []string{
		"Outcomes: candidateDomain",
		"Insights: insightBrowser",
		"domain.NewInsightBrowser(canonical, explainer)",
		"batch6, v3)",
	} {
		if !strings.Contains(text, phrase) {
			t.Errorf("main.go 里没有 %q ⇒ 内部面没接进进程，而 httpapi／domain 两侧的用例照样全绿（假绿）", phrase)
		}
	}
	// canonical 的唯一写入口只许有一个 Service 实例：三颗 worker 加内部面共用同一颗。
	if n := strings.Count(text, "domain.NewService("); n != 1 {
		why := "有人造了第二颗 canonical Service，乐观锁与版本账本各算一份"
		if n == 0 {
			why = "内部面拿到的是别的来源，写口分叉"
		}
		t.Errorf("domain.NewService( 命中 %d 次，期望 1 ⇒ %s", n, why)
	}
	if n := strings.Count(text, "Outcomes: candidateDomain"); n != 1 {
		t.Errorf("Outcomes: candidateDomain 命中 %d 次，期望 1 ⇒ 反馈写口要么没接上，要么长了第二个", n)
	}
	// 这一面**不许**有第二个旋钮：开关只有 `GO_SERVICE_TOKEN` 一格（空 ⇒ 不注册）。
	lower := strings.ToLower(text)
	for _, banned := range []string{"closen_v3", "closen_internal", "go_internal_enabled", "v3_enabled"} {
		if strings.Contains(lower, banned) {
			t.Errorf("main.go 出现了 %q ⇒ 内部面发明了第二个旋钮（D-G12「假旋钮」红线）", banned)
		}
	}
}

// TestCloseQueueMetricsReportsRealError：读数的错误从循环里带回来时必须出声。
// 静默的那一格表现是「指标连续三天没更新」，而没人知道是循环死了还是根本没起。
func TestCloseQueueMetricsReportsRealError(t *testing.T) {
	done := make(chan error, 1)
	done <- errors.New("amqp: dial: connection refused")
	logs := logToBuffer(t, func() { closeQueueMetrics(func() {}, done) })
	if !strings.Contains(logs, "带回错误") || !strings.Contains(logs, "connection refused") {
		t.Errorf("退出带回的错误没被打进日志：%q", logs)
	}
}

// TestCloseQueueMetricsGivesUpOnBudget：卡在最后一次读数上时**放弃等待**，不挂退出路径。
// 这条的正确性依据同时写进日志：peek 用的是 nack(requeue=true)，通道一关那条消息由 broker
// 自动回队 ⇒ 放弃等待不丢消息，也不需要任何补偿动作。
func TestCloseQueueMetricsGivesUpOnBudget(t *testing.T) {
	done := make(chan error) // 永远不放值：造「循环出不来」
	started := time.Now()
	logs := logToBuffer(t, func() { closeQueueMetrics(func() {}, done) })
	if elapsed := time.Since(started); elapsed > 3*time.Second {
		t.Errorf("closeQueueMetrics 等了 %v ⇒ 一秒预算没生效，退出路径会挂死到 gracePeriod 被 SIGKILL", elapsed)
	}
	if !strings.Contains(logs, "没退出") {
		t.Errorf("超时却不说放弃等待，运维读到的是一次静默停机：%q", logs)
	}
	if !strings.Contains(logs, "回队") {
		t.Errorf("放弃等待没交代那条 peek 出来的消息去哪：%q", logs)
	}
}
