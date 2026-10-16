// Command go-api serves the public HTTP contract for the domains Go Core owns.
package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	// 运行镜像是 alpine + ca-certificates，**没有** tzdata 包，也没有
	// /usr/share/zoneinfo；不嵌这份数据库的话 time.LoadLocation 一律失败，
	// curriculum 的本地周窗口会对每个非 UTC 用户静默退回 UTC（见 domain.loadUserZone）。
	"time"
	_ "time/tzdata"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/auth"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/config"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/cutover"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/dsn"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/httpapi"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/amqp"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/dispatch"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/posthog"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store"
)

func main() {
	cfg, err := config.Load()
	if err != nil {
		log.Fatalf("go-api: config: %v", err)
	}
	if err := cfg.RequireAuthSecret(); err != nil {
		log.Fatalf("go-api: %v", err)
	}
	if cfg.JWTSecret == "" {
		log.Printf("go-api: warning: JWT_SECRET empty, every bearer token will be rejected")
	}

	poolCfg, err := pgxpool.ParseConfig(dsn.Postgres(cfg.DatabaseURL))
	if err != nil {
		log.Fatalf("go-api: DATABASE_URL: %v", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	pool, err := pgxpool.NewWithConfig(ctx, poolCfg)
	if err != nil {
		log.Fatalf("go-api: postgres: %v", err)
	}
	defer pool.Close()
	if err := pool.Ping(ctx); err != nil {
		log.Fatalf("go-api: postgres ping: %v", err)
	}

	pg := store.NewPG(pool)
	signer := auth.NewSigner(cfg.JWTSecret,
		time.Duration(cfg.JWTAccessExpireMinutes)*time.Minute,
		time.Duration(cfg.JWTRefreshExpireDays)*time.Hour*24)
	service := auth.NewService(pg, signer, time.Duration(cfg.JWTRefreshExpireDays)*time.Hour*24)
	// N9 的读面：同一个池，但走 Canonical 的**池面只读**方法（不开事务，
	// 见 internal/store/canonical_read.go 的分段注释）。
	// outbox 的认领/写回也在同一个 Canonical 面上（store/canonical_repo.go:884 起），
	// 所以这里只建一次、两个消费方共用，不要再各建一份。
	canonical := store.NewCanonical(pool)
	explainer := domain.NewInsightExplainer(canonical, domain.DefaultPolicy())

	// N8 批 1（analytics + discovery）的依赖面。灰度百分比未到的请求逐字转交 py-api，
	// 所以 CLOSEN_PY_API_URL 缺失而百分比 <100 时是**拒绝启动**而不是静默 404。
	gate, err := cutover.New(cfg.CutoverPyAPIURL, map[string]int{
		"analytics":  cfg.CutoverAnalytics,
		"discovery":  cfg.CutoverDiscovery,
		"curriculum": cfg.CutoverCurriculum,
		"journey":    cfg.CutoverJourney,
		"onboarding": cfg.CutoverOnboarding,
		// 批 5：mood 键盖 /mood/history 与 /mood/trend 两条已上 path 的 GET；
		// reflections 键**先声明不上 path**（D-10：Ingress 的 path 匹配不看方法，
		// /api/v1/reflections 的 GET 与 POST 共用一个 path，而两个 POST 仍归 py-api）。
		"mood":        cfg.CutoverMood,
		"reflections": cfg.CutoverReflections,
	})
	if err != nil {
		log.Fatalf("go-api: cutover: %v", err)
	}
	batch1 := &httpapi.Batch1{
		Users: pg,
		Moods: pg,
		Relay: posthog.New(cfg.PosthogAPIKey, cfg.PosthogHost),
		Gate:  gate,
	}

	// N8 批 2（curriculum 域）：两条端点都只碰 users + journey_events，零 LLM。
	batch2 := &httpapi.Batch2{Store: pg, Gate: gate}

	// N8 批 3（journey 域）：同样是 users + journey_events，但 GET 会补写剧本，
	// 所以这个域是三条里唯一**带写**的（见 httpapi/batch3.go 的事务口径）。
	batch3 := &httpapi.Batch3{Store: pg, Gate: gate}

	// N8 批 4（onboarding 域）：一次 POST 写三处（users.referral_code、
	// onboarding_profiles、journey_events 六行），三处包在同一个 pgx 事务里。
	batch4 := &httpapi.Batch4{Store: pg, Gate: gate}

	// N8 批 5（mood + reflections 三条纯读）：零写入、零 LLM、零外发。
	batch5 := &httpapi.Batch5{Moods: pg, Reflections: pg, Users: pg, Gate: gate}

	srv := &http.Server{
		Addr: cfg.HTTPAddr,
		Handler: httpapi.NewServer(cfg, signer, service, explainer,
			batch1, batch2, batch3, batch4, batch5).Handler(),
		ReadHeaderTimeout: 10 * time.Second,
	}

	// R4-④ outbox worker 的进程接线。**默认关**：`CLOSEN_OUTBOX_WORKER` 不给值时
	// Runner.Run 打一行「未启动」就返回，不拨号、不声明拓扑、一行 DB 都不碰，
	// 所以「部署了但没开这个键」与接线之前的现网行为逐字一致
	// （闸在 dispatch_test.go 的 TestKillSwitchOffTouchesNothing，M4 打红它）。
	//
	// 键与配置面分开走 env-only 是有意的：这六个键 Python 侧一个都没有，
	// 放进 config 会让那份「跨语言变量名单」多出一批只有 Go 认得的字段。
	sws := dispatch.LoadSwitch(os.Getenv)
	worker, err := dispatch.New(dispatch.Deps{
		// 一层薄闭包：Go 不会把 `func … (*amqp.Session, error)` 当成
		// `func … (dispatch.Session, error)`（方法也不能偏应用），所以适配器写在调用点。
		Dial: func(c context.Context, url string, timeout time.Duration) (dispatch.Session, error) {
			sess, err := amqp.Dial(c, url, timeout)
			if err != nil {
				return nil, err
			}
			return sess, nil
		},
		Repo:   canonical,
		Policy: domain.DefaultDeliveryPolicy(),
		Switch: sws,
		Log:    func(format string, args ...any) { log.Printf("go-api: "+format, args...) },
	})
	if err != nil {
		// 「开了但没给 broker 地址」拒绝启动，与上面的 cutover 同一条口径：
		// 空转的 worker 比没有 worker 更坏——运维会把它读成「投递面在跑」。
		log.Fatalf("go-api: outbox worker: %v", err)
	}
	workerCtx, stopWorker := context.WithCancel(context.Background())
	workerDone := make(chan error, 1)
	go func() { workerDone <- worker.Run(workerCtx) }()
	// 停机顺序靠 defer 的后进先出：这条登记在 srv.Shutdown **之前**，因此在它
	// **之后**执行，正好是 17 §11 要的「HTTP 面先不再产生新行，worker 再把在途那批发完」；
	// 而它又登记在 `defer pool.Close()` 之后 ⇒ 退出时先等 worker，最后才关池。
	defer closeWorker(worker, stopWorker, workerDone, sws.DrainTimeout+5*time.Second)

	shutdownCtx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	errCh := make(chan error, 1)
	go func() { errCh <- srv.ListenAndServe() }()
	log.Printf("go-api: listening on %s (environment=%s)", cfg.HTTPAddr, cfg.Environment)

	select {
	case err := <-errCh:
		if err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("go-api: serve: %v", err)
		}
	case <-shutdownCtx.Done():
		drain, cancelDrain := context.WithTimeout(context.Background(), time.Duration(cfg.ShutdownTimeoutSeconds)*time.Second)
		defer cancelDrain()
		if err := srv.Shutdown(drain); err != nil {
			log.Printf("go-api: shutdown: %v", err)
		}
	}
}

// closeWorker 停掉 outbox worker 并等它退出。
//
// 这里的等待是**有上限**的，而且超时时只打日志不报错：pod 的
// terminationGracePeriodSeconds 到点后是 SIGKILL，一个卡在退出路径上的进程
// 会被读成「优雅停机成功」，而它其实什么都没确认完。
// 没发完的行留在 outbox 里，租约到期后由下一个 worker 重投（18 §5 的 SKIP LOCKED）。
func closeWorker(r *dispatch.Runner, cancel context.CancelFunc, done chan error, budget time.Duration) {
	// cancel 只关掉「认领下一批」的闸；Runner 给在途那一批的是 WithoutCancel 的
	// 收尾 ctx，所以这里等到的是「把最后一批发完」而不是「立刻打断最后一次发布」。
	cancel()
	timer := time.NewTimer(budget)
	defer timer.Stop()
	select {
	case err := <-done:
		st := r.Stats()
		if err != nil {
			log.Printf("go-api: outbox worker 退出带回错误：%v（accepted=%d rejected=%d dlq=%d）",
				err, st.Accepted, st.Rejected, st.DeadLettered)
			return
		}
		if st.Batches > 0 {
			log.Printf("go-api: outbox worker 已退出：batches=%d claimed=%d accepted=%d rejected=%d dlq=%d lost=%d unrecorded=%d dials=%d redials=%d",
				st.Batches, st.Claimed, st.Accepted, st.Rejected, st.DeadLettered,
				st.Lost, st.Unrecorded, st.Dials, st.Redials)
		}
	case <-timer.C:
		log.Printf("go-api: outbox worker 在 %v 内没退出 ⇒ 放弃等待，未确认的行按租约到期后重投", budget)
	}
}
