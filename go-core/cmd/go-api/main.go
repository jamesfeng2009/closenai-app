// Command go-api serves the public HTTP contract for the domains Go Core owns.
package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os/signal"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/auth"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/config"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/httpapi"
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

	poolCfg, err := pgxpool.ParseConfig(postgresURL(cfg.DatabaseURL))
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

	signer := auth.NewSigner(cfg.JWTSecret,
		time.Duration(cfg.JWTAccessExpireMinutes)*time.Minute,
		time.Duration(cfg.JWTRefreshExpireDays)*time.Hour*24)
	service := auth.NewService(store.NewPG(pool), signer, time.Duration(cfg.JWTRefreshExpireDays)*time.Hour*24)
	// N9 的读面：同一个池，但走 Canonical 的**池面只读**方法（不开事务，
	// 见 internal/store/canonical_read.go 的分段注释）。
	explainer := domain.NewInsightExplainer(store.NewCanonical(pool), domain.DefaultPolicy())

	srv := &http.Server{
		Addr:              cfg.HTTPAddr,
		Handler:           httpapi.NewServer(cfg, signer, service, explainer).Handler(),
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

// postgresURL accepts the SQLAlchemy-style DATABASE_URL the Python stack already
// exports (postgresql+asyncpg://...) so one variable serves both runtimes.
func postgresURL(raw string) string {
	for _, prefix := range []string{"postgresql+asyncpg://", "postgresql+psycopg2://", "postgresql+psycopg://"} {
		if len(raw) >= len(prefix) && raw[:len(prefix)] == prefix {
			return "postgresql://" + raw[len(prefix):]
		}
	}
	return raw
}
