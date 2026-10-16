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
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/dispatch"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/outbox"
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

// logToBuffer 把全局 log 暂时接进缓冲区，返回取内容的闭包。
func logToBuffer(t *testing.T, f func()) string {
	t.Helper()
	var buf bytes.Buffer
	orig := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(orig)
	f()
	return buf.String()
}

// loggerAdapter 与 main.go 里那一份逐字同一句话：dispatch 不 import log，
// 是装配点把 deps.Log 接到 log.Printf 上的。用例走同一条路，否则它会绿在
// 「日志压根没出境」那种假形态上。
func loggerAdapter() func(string, ...any) {
	return func(format string, args ...any) { log.Printf("go-api: "+format, args...) }
}

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
	if !strings.Contains(logs, "未启动") {
		t.Errorf("关着的时候整条退出路径一句话都没有：%q", logs)
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
