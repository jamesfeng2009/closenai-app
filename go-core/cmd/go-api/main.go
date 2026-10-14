// Command go-api serves the public HTTP contract for the domains Go Core owns.
package main

import (
	"context"
	"errors"
	"log"
	"net/http"
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
	explainer := domain.NewInsightExplainer(store.NewCanonical(pool), domain.DefaultPolicy())

	// N8 批 1（analytics + discovery）的依赖面。灰度百分比未到的请求逐字转交 py-api，
	// 所以 CLOSEN_PY_API_URL 缺失而百分比 <100 时是**拒绝启动**而不是静默 404。
	gate, err := cutover.New(cfg.CutoverPyAPIURL, map[string]int{
		"analytics":  cfg.CutoverAnalytics,
		"discovery":  cfg.CutoverDiscovery,
		"curriculum": cfg.CutoverCurriculum,
		"journey":    cfg.CutoverJourney,
		"onboarding": cfg.CutoverOnboarding,
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

	srv := &http.Server{
		Addr: cfg.HTTPAddr,
		Handler: httpapi.NewServer(cfg, signer, service, explainer,
			batch1, batch2, batch3, batch4).Handler(),
		ReadHeaderTimeout: 10 * time.Second,
	}

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
