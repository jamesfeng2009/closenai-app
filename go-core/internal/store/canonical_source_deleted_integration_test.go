// T2-06 的真库集成测：判定逻辑由 internal/domain 那 7 条单测证明，
// 这里只证明**只有真库能答**的四件事（B-08 定案的三格＋一格归属）：
//
//  1. `GetSourceRecordByPointer` 的谓词真的带 `user_id`——跨用户的那次删除必须读成
//     「没有这条登记行」，而不是「有，但那是别人的」。这一格是不变量 9 的落点：
//     库里那两个串各自都对，只有组合起来才越权。
//  2. `MarkSourceRecordDeletedOnce` 的单向性在 SQL 谓词里（`status <> 'deleted'`）：
//     第二次影响 0 行，域侧才敢据此「不再传播」。
//  3. 翻转**不删行**（B-08 第①格）：status='deleted' 之后那一行还在，
//     因为 `live_support` 的判据（`s.status <> 'deleted'`）要读它——行没了，
//     派生行就永远显示「支撑还在」。
//  4. 翻转之后传播真的走到底：仅依赖这条来源的 fact 变 `invalidated`、
//     insight 只留重算请求，且同一条重投不会把账翻倍。
//
// 未设 TEST_DATABASE_URL 时跳过，与其余 store 集成测同口径（缺 env 是「未跑」不是绿）。
package store

import (
	"context"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store/canonicaldb"
)

// pointerOf 把登记行的主键折回事件体说的那三件（指针的权威就是这三列，不是 id）。
func pointerOf(t *testing.T, pool *pgxpool.Pool, sourceRecordID string) domain.SourcePointer {
	t.Helper()
	var p domain.SourcePointer
	p.UserID = userIDOfSourceRecord(t, pool, sourceRecordID)
	if err := pool.QueryRow(context.Background(),
		`SELECT source_table, source_id FROM source_records WHERE id = $1`,
		sourceRecordID).Scan(&p.SourceTable, &p.SourceID); err != nil {
		t.Fatalf("读 source 指针: %v", err)
	}
	return p
}

func userIDOfSourceRecord(t *testing.T, pool *pgxpool.Pool, sourceRecordID string) string {
	t.Helper()
	var userID string
	if err := pool.QueryRow(context.Background(),
		`SELECT user_id FROM source_records WHERE id = $1`, sourceRecordID).Scan(&userID); err != nil {
		t.Fatalf("读 source_records.user_id: %v", err)
	}
	return userID
}

func sourceStatus(t *testing.T, pool *pgxpool.Pool, sourceRecordID string) (string, int) {
	t.Helper()
	var status string
	var rows int
	err := pool.QueryRow(context.Background(),
		`SELECT status FROM source_records WHERE id = $1`, sourceRecordID).Scan(&status)
	if err != nil {
		// 「行没了」在这里是一个结论，不是一个错误码：调用方要能把它读成「登记行被删了」
		// 而不是让整条用例死在 Scan 的错误上（那等于第③格没有证人）。
		return "", rows
	}
	rows = 1
	return status, rows
}

func countRows(t *testing.T, pool *pgxpool.Pool, query string, args ...any) int {
	t.Helper()
	var n int
	if err := pool.QueryRow(context.Background(), query, args...).Scan(&n); err != nil {
		t.Fatalf("count %q: %v", query, err)
	}
	return n
}

// TestInvalidateSourceFlipsOnceAndPropagatesInDB 是本批那条主判据的实库形状。
func TestInvalidateSourceFlipsOnceAndPropagatesInDB(t *testing.T) {
	pool := poolForTest(t)
	const userID = "t206-flip-propagate"
	defer cleanupCanonical(t, pool, userID)

	ctx := context.Background()
	q := canonicaldb.New(pool)
	c := plantChain(t, q, userID)
	svc := serviceFor(pool)

	// 前置：登记行是 active，fact 是 active（plantChain 末尾已过 Domain Gate 提升）。
	if status, rows := sourceStatus(t, pool, c.sourceID); rows != 1 || status != "active" {
		t.Fatalf("前置不成立：source_records=(%s,%d)，期望 active/1 行", status, rows)
	}

	res, err := svc.InvalidateSource(ctx, pointerOf(t, pool, c.sourceID))
	if err != nil {
		t.Fatalf("InvalidateSource: %v（res=%+v）", err, res)
	}
	if !res.Marked {
		t.Fatalf("首投没翻成 deleted：%+v ⇒ 单向闸或指针谓词在真库上不成立", res)
	}
	if res.EvidenceCount != 1 {
		t.Errorf("分母=%d，期望 1（plantChain 只挂了一条 evidence）：%+v", res.EvidenceCount, res)
	}
	// B-08 第①格：翻状态、**不删行**。删了行，`live_support` 就没有可读的那一列了。
	status, rows := sourceStatus(t, pool, c.sourceID)
	if rows != 1 || status != "deleted" {
		t.Fatalf("登记行现在=(%q,%d 行)，期望 deleted/1 行 ⇒ 第③格（不删行）破了", status, rows)
	}
	// 第④格：仅依赖这条来源的 fact 被改写。
	var factStatus string
	var invalidatedAtIsNull bool
	if err := pool.QueryRow(ctx, `SELECT status, invalidated_at IS NULL
		FROM personal_facts WHERE id = $1`, c.factID).
		Scan(&factStatus, &invalidatedAtIsNull); err != nil {
		t.Fatalf("读 fact: %v", err)
	}
	if factStatus != "invalidated" || invalidatedAtIsNull {
		t.Errorf("fact=(%s, invalidated_at 为空=%v)，期望 invalidated/false ⇒ "+
			"「status 翻了但证据没传播」就是这一格", factStatus, invalidatedAtIsNull)
	}
	if res.Invalidated < 1 {
		t.Errorf("Invalidated=%d，期望 ≥1：%+v", res.Invalidated, res)
	}
	if res.ModelVersion < 1 {
		t.Errorf("ModelVersion=%d ⇒ 传播没有 ++ 版本，账面读不到这次退场", res.ModelVersion)
	}
	events := countRows(t, pool, `SELECT count(*) FROM personal_model_events
		WHERE user_id = $1 AND event_type = 'evidence_withdrawn'`, userID)
	if events < 1 {
		t.Errorf("evidence_withdrawn 事件行数=%d，期望 ≥1", events)
	}

	// 重投同一次删除（broker at_least_once 的常态）：单向闸跳过，账不许翻倍。
	versionBefore := countRows(t, pool,
		`SELECT count(*) FROM personal_model_versions WHERE user_id = $1`, userID)
	res2, err := svc.InvalidateSource(ctx, pointerOf(t, pool, c.sourceID))
	if err != nil {
		t.Fatalf("重投 InvalidateSource: %v", err)
	}
	if res2.Marked {
		t.Errorf("重投又翻了一次：%+v ⇒ status <> 'deleted' 那道谓词没生效", res2)
	}
	if res2.EvidenceCount != 0 || res2.Invalidated != 0 {
		t.Errorf("重投还在传播：evidence=%d invalidated=%d ⇒ 每次重投都记一遍退场，账被放大",
			res2.EvidenceCount, res2.Invalidated)
	}
	events2 := countRows(t, pool, `SELECT count(*) FROM personal_model_events
		WHERE user_id = $1 AND event_type = 'evidence_withdrawn'`, userID)
	if events2 != events {
		t.Errorf("重投把事件行从 %d 变成 %d ⇒ 同一次删除记了两次", events, events2)
	}
	if after := countRows(t, pool,
		`SELECT count(*) FROM personal_model_versions WHERE user_id = $1`, userID); after != versionBefore {
		t.Errorf("重投把版本行从 %d 变成 %d", versionBefore, after)
	}
}

// TestInvalidateSourceIsScopedToTheOwner 是不变量 9 的实库证人。
//
// 这一格为什么必须在真库判：domain 的单测里 fake 按参数匹配，
// 「WHERE 里到底带没带 user_id」在 fake 上永远为真——把 SQL 的 `user_id = $2` 摘掉，
// 只有这里会红。现网后果是**跨用户删除**：B 投一条指针指向 A 的记忆，
// A 的登记行被翻成 deleted、A 的 fact 被打成 invalidated，
// 而 B 那侧一切正常（Marked=true），日志里连一句可疑都看不见。
func TestInvalidateSourceIsScopedToTheOwner(t *testing.T) {
	pool := poolForTest(t)
	const victim = "t206-scope-victim"
	const attacker = "t206-scope-attacker"
	defer cleanupCanonical(t, pool, victim)
	defer cleanupCanonical(t, pool, attacker)

	ctx := context.Background()
	q := canonicaldb.New(pool)
	c := plantChain(t, q, victim)
	svc := serviceFor(pool)

	p := pointerOf(t, pool, c.sourceID)
	if p.UserID != victim {
		t.Fatalf("指针的 user_id=%q，夹具不是 victim ⇒ 这条用例判空", p.UserID)
	}
	// 只换归属，其余两格逐字不动：这正是「两个串各自都对、组合起来越权」的形状。
	p.UserID = attacker
	res, err := svc.InvalidateSource(ctx, p)
	if err != nil {
		t.Fatalf("跨用户指针应读成「没有这条登记行」而不是报错：%v", err)
	}
	if res.Marked || res.SourceRecordID != "" || res.EvidenceCount != 0 {
		t.Errorf("跨用户那次翻转了别人的账：%+v", res)
	}
	if status, _ := sourceStatus(t, pool, c.sourceID); status != "active" {
		t.Errorf("victim 的登记行现在是 %q（期望仍是 active）⇒ SQL 少带 user_id", status)
	}
	if n := countRows(t, pool, `SELECT count(*) FROM personal_facts
		WHERE user_id = $1 AND status = 'invalidated'`, victim); n != 0 {
		t.Errorf("victim 有 %d 条 fact 被别人的删除打成 invalidated", n)
	}

	// 反向证人：同一格指针换回真主人就翻得动——否则上面那条红可能是「传播整体坏了」。
	res2, err := svc.InvalidateSource(ctx, pointerOf(t, pool, c.sourceID))
	if err != nil || !res2.Marked {
		t.Fatalf("真主人的那次没翻成：%+v err=%v", res2, err)
	}
}

// TestRestoreSourceRecordStatusPredicate 判恢复那一支的两个方向。
//
// `status = 'deleted'` 这个谓词不许松：松了它就变成「把任意登记行改回任意状态」，
// 而恢复只在「翻转已提交＋传播中途失败」那一条路上被调用，那时库里必然停在 deleted。
func TestRestoreSourceRecordStatusPredicate(t *testing.T) {
	pool := poolForTest(t)
	const userID = "t206-restore-predicate"
	defer cleanupCanonical(t, pool, userID)

	ctx := context.Background()
	q := canonicaldb.New(pool)
	now := time.Now().UTC().Truncate(time.Microsecond)
	sourceID, err := q.InsertSourceRecord(ctx, canonicaldb.InsertSourceRecordParams{
		ID: newID(), UserID: userID, SourceType: "journal_entry",
		SourceTable: "memories", SourceID: newID(), OccurredAt: ts(now),
		RevisionOf: nullText(),
	})
	if err != nil {
		t.Fatalf("InsertSourceRecord: %v", err)
	}
	st := NewCanonical(pool)

	// active 行：恢复谓词不命中（0 行），库里仍是 active。
	var rows int64
	if err := st.InTx(ctx, func(ctx context.Context, tx domain.Store) error {
		n, err := tx.RestoreSourceRecordStatus(ctx, sourceID, userID, "active")
		rows = n
		return err
	}); err != nil {
		t.Fatalf("Restore（active 行）: %v", err)
	}
	if rows != 0 {
		t.Errorf("对 active 行恢复影响了 %d 行，期望 0 ⇒ 谓词少了 status = 'deleted' 这一半", rows)
	}

	var marked bool
	if err := st.InTx(ctx, func(ctx context.Context, tx domain.Store) error {
		m, err := tx.MarkSourceRecordDeletedOnce(ctx, sourceID, userID)
		marked = m
		return err
	}); err != nil {
		t.Fatalf("Mark: %v", err)
	}
	if !marked {
		t.Fatal("首翻没命中 ⇒ 后面的恢复无从谈起")
	}
	// 同一笔再来一次＝0 行（单向闸），而带着错的用户也＝0 行（归属闸）。
	// 这两格都放在**同一个事务**里跑：分两个事务就成了「上一次的结果决定下一次的输入」，
	// 一旦单向闸失效，第二个断言会因为库里已经变了而失去意义。
	var again, foreign bool
	var restored, foreignRestore int64
	if err := st.InTx(ctx, func(ctx context.Context, tx domain.Store) error {
		var err error
		if again, err = tx.MarkSourceRecordDeletedOnce(ctx, sourceID, userID); err != nil {
			return err
		}
		if foreign, err = tx.MarkSourceRecordDeletedOnce(ctx, sourceID, "someone-else"); err != nil {
			return err
		}
		if restored, err = tx.RestoreSourceRecordStatus(ctx, sourceID, userID, "active"); err != nil {
			return err
		}
		foreignRestore, err = tx.RestoreSourceRecordStatus(ctx, sourceID, "someone-else", "active")
		return err
	}); err != nil {
		t.Fatalf("同事务里的四连查: %v", err)
	}
	if again {
		t.Error("第二次翻转仍命中 ⇒ status <> 'deleted' 那道闸没生效")
	}
	if foreign {
		t.Error("带别人的 user_id 也能翻 ⇒ SQL 少了 user_id 谓词（不变量 9）")
	}
	if restored != 1 {
		t.Errorf("deleted 行的恢复=%d，期望 1", restored)
	}
	if foreignRestore != 0 {
		t.Errorf("别人的 user_id 恢复了这一行（%d 行）⇒ 恢复面也越权", foreignRestore)
	}
	if status, _ := sourceStatus(t, pool, sourceID); status != "active" {
		t.Errorf("恢复后库里是 %q，期望 active", status)
	}
}
