// 并发首建 personal model 的恢复路径。冲突会把 PG 事务置为 aborted（25P02）：
// 旧实现在**同一事务**里直接重读，重读必然失败——这里用两个真事务把那条路径压出来。
//
// Skips unless TEST_DATABASE_URL is set, e.g.
//
//	TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/closenai \
//	    go test ./internal/store/ -run EnsurePersonalModelRecovers
package store

import (
	"context"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store/canonicaldb"
)

// waitForPersonalModelInsertBlocked 等到 B 事务真的堵在唯一索引上。
// 只等固定时长会让测试在慢机器上时快时慢；看 pg_stat_activity 才是确定性的。
func waitForPersonalModelInsertBlocked(t *testing.T, pool *pgxpool.Pool) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		var n int
		err := pool.QueryRow(context.Background(),
			`SELECT count(*) FROM pg_stat_activity
			  WHERE wait_event_type = 'Lock'
			    AND query ILIKE '%INSERT INTO personal_models%'`).Scan(&n)
		if err == nil && n > 0 {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("EnsurePersonalModel 没有进入唯一索引等待")
}

func TestEnsurePersonalModelRecoversFromConcurrentFirstCreate(t *testing.T) {
	pool := poolForTest(t)
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })

	ctx := context.Background()

	// 事务 A：先占住唯一键但不提交（模拟另一个 worker 正在首建）。
	txA, err := pool.Begin(ctx)
	if err != nil {
		t.Fatalf("begin A: %v", err)
	}
	defer func() { _ = txA.Rollback(ctx) }()
	holderID := newID()
	if err := canonicaldb.New(txA).InsertPersonalModel(ctx,
		canonicaldb.InsertPersonalModelParams{ID: holderID, UserID: userID}); err != nil {
		t.Fatalf("holder insert: %v", err)
	}

	// 事务 B：Get 看不到未提交行 → INSERT 阻塞；A 提交后拿 23505，恢复路径
	// 必须回滚 savepoint 再重读，而不是让整个事务停在 aborted 状态。
	type result struct {
		id      string
		version int64
		err     error
	}
	done := make(chan result, 1)
	go func() {
		var id string
		var version int64
		werr := NewCanonical(pool).InTx(ctx, func(ctx context.Context, st domain.Store) error {
			var e error
			id, version, e = st.EnsurePersonalModel(ctx, userID)
			return e
		})
		done <- result{id: id, version: version, err: werr}
	}()

	waitForPersonalModelInsertBlocked(t, pool)
	if err := txA.Commit(ctx); err != nil {
		t.Fatalf("commit A: %v", err)
	}

	select {
	case got := <-done:
		if got.err != nil {
			t.Fatalf("并发首建失败（唯一冲突后事务未恢复？）: %v", got.err)
		}
		if got.id != holderID {
			t.Fatalf("id=%q，期望复用已提交行 %q", got.id, holderID)
		}
		if got.version != 0 {
			t.Fatalf("version=%d，期望 0", got.version)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("等待 EnsurePersonalModel 超时")
	}
}
