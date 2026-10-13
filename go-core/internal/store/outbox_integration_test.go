// N7 投递面的真库集成测（#28）。单测证明的是逻辑，这一批证明的只有真库能答的问题：
//
//  1. 事件行与投递行是不是**真的**在同一笔事务里生死（18 §2）——fake 的回滚是我自己写的，
//     只有 Postgres 能证明 sqlc 的两条语句共享一个 BEGIN/COMMIT；
//  2. 多 worker 不重复认领靠的是 FOR UPDATE SKIP LOCKED（18 §5），不是靠代码记性：
//     两个都还开着的事务必须拿到不相交的行、attempt_count 只能 ++ 一次；
//  3. available_at 一列两用（N7-2）在库上真的两用：租约到期的孤儿行能被重取、
//     退避写回的 30s 之内不可认领、published/dead_lettered 永不进认领面（18 §4/§6）；
//  4. UNIQUE(message_id) 撞了是报错而不是静默（18 §10）；
//  5. 瞬时失败连 inbox 行一起消失、永久拒绝的留痕活过回滚（18 §8/§9、14 §7）。
//
// 与 N2–N6 同口径：未设 TEST_DATABASE_URL 就跳过。
package store

import (
	"context"
	"encoding/json"
	"errors"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/outbox"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store/canonicaldb"
)

// n7WorkerPolicy 是显式给定的投递策略，刻意不读 DefaultDeliveryPolicy：
// 那七个数全部 env 可覆盖（D-4/G-5），用例断言的是「worker 照策略办事」，
// 不是「默认值恰好是 30/120/600」——后者由 delivery_policy_test.go 钉。
func n7WorkerPolicy() domain.DeliveryPolicy {
	return domain.DeliveryPolicy{
		BatchSize: 500, LeaseSeconds: 120, MaxAttempts: 3,
		RetryLadderSeconds: []int{30, 120, 600}, SchemaVersion: 1,
		OutboxRetentionDays: 30, InboxRetentionDays: 90,
	}
}

func n7Exec(t *testing.T, pool *pgxpool.Pool, sql string, args ...any) {
	t.Helper()
	if _, err := pool.Exec(context.Background(), sql, args...); err != nil {
		t.Fatalf("exec (%s): %v", sql, err)
	}
}

// n7PlantOutbox 直接落一行待投递消息。用例要控制 status/attempt_count/available_at
// 这些「崩溃与退避之后剩下的状态」——它们不是领域结论，domain 也不该造它们。
func n7PlantOutbox(t *testing.T, pool *pgxpool.Pool, userID, tag string) string {
	t.Helper()
	id := newID()
	if _, err := canonicaldb.New(pool).InsertOutboxMessage(context.Background(),
		canonicaldb.InsertOutboxMessageParams{
			ID: id, MessageID: "msg-" + tag + "-" + id, MessageType: "fact_admitted",
			SchemaVersion: 1, RoutingKey: "fact.created", UserID: userID,
			CorrelationID: "corr-" + tag, Payload: `{"status":"active"}`,
		}); err != nil {
		t.Fatalf("plant outbox: %v", err)
	}
	return id
}

// n7OutboxState 读投递行的状态面。availableOffset 相对 **数据库时钟** 计算，
// 避免本机与容器的钟差把 30s 退避测成 29.x 或 30.x 的假红。
func n7OutboxState(t *testing.T, pool *pgxpool.Pool, id string) (string, int, time.Duration, bool, string) {
	t.Helper()
	var status string
	var attempts int
	var available, dbNow time.Time
	var published bool
	var lastErr string
	err := pool.QueryRow(context.Background(),
		`SELECT status, attempt_count, available_at, now(), published_at IS NOT NULL,
		        coalesce(last_error, '')
		 FROM outbox_messages WHERE id = $1`, id,
	).Scan(&status, &attempts, &available, &dbNow, &published, &lastErr)
	if err != nil {
		t.Fatalf("read outbox state: %v", err)
	}
	return status, attempts, available.Sub(dbNow), published, lastErr
}

func n7ClaimIDs(t *testing.T, pool *pgxpool.Pool, tx pgx.Tx) []string {
	t.Helper()
	rows, err := canonicaldb.New(tx).ClaimOutboxBatch(context.Background(),
		canonicaldb.ClaimOutboxBatchParams{LeaseSeconds: 120, BatchLimit: 500})
	if err != nil {
		t.Fatalf("claim in tx: %v", err)
	}
	ids := make([]string, 0, len(rows))
	for _, row := range rows {
		ids = append(ids, row.ID)
	}
	return ids
}

// n7PublishOnce 跑一轮真实 worker：repo 是池面仓储（认领/确认都走真库），
// pub 只对点名的 message_id 失败，其余一律「broker 已确认」。
func n7PublishOnce(t *testing.T, pool *pgxpool.Pool, pub *n7Publisher) outbox.Result {
	t.Helper()
	result, err := outbox.NewWorker(NewCanonical(pool), pub, n7WorkerPolicy()).
		RunOnce(context.Background())
	if err != nil {
		t.Fatalf("worker RunOnce: %v", err)
	}
	return result
}

type n7Publisher struct {
	failFor map[string]bool
	seen    []outbox.Envelope
}

func (p *n7Publisher) Publish(_ context.Context, msg outbox.Envelope) error {
	p.seen = append(p.seen, msg)
	if p.failFor[msg.MessageID] {
		return errors.New("broker channel not reachable")
	}
	return nil
}

// TestN7AdmitWritesOutboxRowInSameTransaction 是 N7 的主链：Go 决策落库的那一刻
// 就留下投递意图，且列面按 18 §3 + 三列偏离对齐真实 DDL。
//
// 单测里这些列都是 fake 的字段；这里证明它们真的存在、宽度够用、默认值没被写歪。
func TestN7AdmitWritesOutboxRowInSameTransaction(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n7-admit"
	defer cleanupCanonical(t, pool, userID)

	p := plantUser(t, pool, userID)
	c := baseCandidate(p, domain.KindFact)
	c.Origin = "direct"

	// 关联标识从 ctx 注入（N7-6）：Admit 的签名里没有它们，也不该有。
	ctx := domain.WithEnvelope(context.Background(), domain.Envelope{
		CorrelationID: "corr-admit-chain", CausationID: "caus-upstream-msg",
	})
	res, err := serviceFor(pool).Admit(ctx, c)
	if err != nil {
		t.Fatalf("Admit: %v", err)
	}
	if res.Action != domain.ActionInsert || res.Status != "active" {
		t.Fatalf("Action=%q Status=%q，期望 insert/active", res.Action, res.Status)
	}

	// message_id 取事件行 PK（18 §10）：这一条断言同时是「同事务」的证据——
	// 事件行已提交，投递行才存在；两者任一失败都应该整笔回滚（见 rollback 用例）。
	if n := countWhere(t, pool,
		`SELECT count(*) FROM outbox_messages WHERE user_id = $1`, userID); n != 1 {
		t.Fatalf("投递行 %d 条，一次 accept 只该有一条投递意图", n)
	}
	var (
		eventID, messageType, routingKey, rowUserID, correlation, causation, payload, status string
		schemaVersion, attempts                                                              int
		published                                                                            bool
		due, dbNow                                                                           time.Time
	)
	err = pool.QueryRow(context.Background(),
		`SELECT o.message_id, o.message_type, o.schema_version, o.routing_key, o.user_id,
		        o.correlation_id, o.causation_id, o.payload, o.status, o.attempt_count,
		        o.published_at IS NOT NULL, o.available_at, now()
		 FROM outbox_messages o
		 WHERE o.user_id = $1`, userID,
	).Scan(&eventID, &messageType, &schemaVersion, &routingKey, &rowUserID,
		&correlation, &causation, &payload, &status, &attempts, &published, &due, &dbNow)
	if err != nil {
		t.Fatalf("投递行没跟着权威态落库: %v", err)
	}
	if got := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE id = $1`, eventID); got != 1 {
		t.Errorf("message_id=%s 在 personal_model_events 里查不到行（rows=%d），"+
			"「稳定 ID＝逻辑操作边界」落空", eventID, got)
	}
	if messageType != "fact_admitted" || routingKey != "fact.created" {
		t.Errorf("message_type=%q routing_key=%q，期望 fact_admitted/fact.created（14 §4）",
			messageType, routingKey)
	}
	if schemaVersion != domain.DefaultPolicy().Delivery.SchemaVersion {
		t.Errorf("schema_version=%d，应与策略一致（N7-5）", schemaVersion)
	}
	if rowUserID != userID {
		t.Errorf("user_id=%q，偏离列必须是真值，否则 D-5 purge 无从下手", rowUserID)
	}
	if correlation != "corr-admit-chain" || causation != "caus-upstream-msg" {
		t.Errorf("correlation=%q causation=%q，ctx 注入的关联标识没穿过事务", correlation, causation)
	}
	body := map[string]any{}
	if err := json.Unmarshal([]byte(payload), &body); err != nil {
		t.Fatalf("payload 不是合法 JSON（下游按 14:71 解码）: %v", err)
	}
	if body["status"] != "active" {
		t.Errorf("payload=%s，应带事件正文", payload)
	}
	if status != "pending" || attempts != 0 || published {
		t.Errorf("status=%q attempt=%d published=%v，新入队应是 pending/0/未发布（18 §4/§6）",
			status, attempts, published)
	}
	if due.Sub(dbNow) > 0 {
		t.Errorf("available_at 在未来 %s，新行应立刻可认领", due.Sub(dbNow))
	}

	// 拒绝路径不投递（判据 2 之外的判据 1：权威模型内容没变）。
	rejected := baseCandidate(p, domain.KindInsight)
	rejected.Confidence = 0.33
	rej, err := serviceFor(pool).Admit(context.Background(), rejected)
	if err != nil {
		t.Fatalf("Admit(insight): %v", err)
	}
	if rej.Action != domain.ActionReject {
		t.Fatalf("insight Action=%q，这条用例要靠拒绝路径", rej.Action)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM outbox_messages WHERE user_id = $1`, userID); n != 1 {
		t.Errorf("拒绝后投递行 %d 条，candidate_rejected 不该进 outbox（七-6 N7-3）", n)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE user_id = $1`, userID); n != 2 {
		t.Errorf("事件行 %d 条，拒绝也要留可审计事件（不变量 6）", n)
	}
}

// TestN7DeliveryWriteRollsBackWithAuthorityWrite 把 18 §2 的「同一笔事务」钉成库侧事实：
// 提交前失败，事件行与投递行都不许留下。fake 的回滚是我自己写的，证明不了这条。
//
// 同一笔写入先跑一次**提交**的对照组：没有对照组，「两样都是 0 行」可能只是
// 语句根本没执行，断言就成了空转。
func TestN7DeliveryWriteRollsBackWithAuthorityWrite(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n7-rollback"
	defer cleanupCanonical(t, pool, userID)

	p := plantUser(t, pool, userID)
	repo := NewCanonical(pool)

	// write 在给定事务里落「一条权威事件 + 一条投递意图」，两行都挂在同一个 message_id 上。
	write := func(eventMessage string) func(context.Context, domain.Store) error {
		return func(ctx context.Context, st domain.Store) error {
			if err := st.AppendEvent(ctx, domain.EventRow{ID: eventMessage, UserID: userID,
				PersonalModelID: p.modelID, EventType: "fact_admitted",
				AggregateType: "personal_facts", AggregateID: newID(), ModelVersion: 1,
				Payload: `{"status":"active"}`}); err != nil {
				return err
			}
			return st.EnqueueOutbox(ctx, domain.OutboxRow{ID: newID(),
				MessageID: eventMessage, MessageType: "fact_admitted", SchemaVersion: 1,
				RoutingKey: "fact.created", UserID: userID, Payload: `{"status":"active"}`})
		}
	}

	committed := "evt-committed-control"
	if err := repo.InTx(context.Background(), write(committed)); err != nil {
		t.Fatalf("对照组 InTx: %v", err)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE id = $1`, committed); n != 1 {
		t.Fatalf("对照组事件行 %d 条，前提不成立", n)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM outbox_messages WHERE message_id = $1`, committed); n != 1 {
		t.Fatalf("对照组投递行 %d 条，两条语句没接上同一个事务", n)
	}

	boom := errors.New("用例故意在提交前失败")
	rolled := "evt-rolled-back"
	err := repo.InTx(context.Background(), func(ctx context.Context, st domain.Store) error {
		if err := write(rolled)(ctx, st); err != nil {
			return err
		}
		return boom
	})
	if !errors.Is(err, boom) {
		t.Fatalf("InTx err=%v，期望原样带出业务失败", err)
	}
	for _, probe := range []struct{ what, query string }{
		{"personal_model_events", `SELECT count(*) FROM personal_model_events WHERE id = $1`},
		{"outbox_messages", `SELECT count(*) FROM outbox_messages WHERE message_id = $1`},
	} {
		if n := countWhere(t, pool, probe.query, rolled); n != 0 {
			t.Errorf("%s 留下 %d 行：投递意图与权威态不同生共死，崩溃后要么双投要么漏投",
				probe.what, n)
		}
	}
}

// TestN7OutboxUniqueMessageIdIsNotSwallowed：18 §10 的 message_id 是稳定 ID，
// 同一条权威事件入队两次是编程错误，必须报错而不是 ON CONFLICT DO NOTHING 咽掉。
func TestN7OutboxUniqueMessageIdIsNotSwallowed(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n7-unique"
	defer cleanupCanonical(t, pool, userID)

	messageID := "msg-stable-" + newID()
	first := n7PlantOutbox(t, pool, userID, "dup")
	n7Exec(t, pool, `UPDATE outbox_messages SET message_id = $2 WHERE id = $1`, first, messageID)

	second := newID()
	_, err := canonicaldb.New(pool).InsertOutboxMessage(context.Background(),
		canonicaldb.InsertOutboxMessageParams{
			ID: second, MessageID: messageID, MessageType: "fact_admitted", SchemaVersion: 1,
			RoutingKey: "fact.created", UserID: userID, Payload: "{}",
		})
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) || pgErr.Code != uniqueViolation {
		t.Fatalf("重复 message_id err=%v，期望 %s 唯一冲突", err, uniqueViolation)
	}

	// 仓储面也必须把它当错误传出去（domain 的 EnqueueOutbox 走同一条语句）。
	err = NewCanonical(pool).InTx(context.Background(), func(ctx context.Context,
		st domain.Store) error {
		return st.EnqueueOutbox(ctx, domain.OutboxRow{ID: newID(), MessageID: messageID,
			MessageType: "fact_admitted", SchemaVersion: 1, RoutingKey: "fact.created",
			UserID: userID, Payload: "{}"})
	})
	if err == nil || !strings.Contains(err.Error(), "outbox_messages") {
		t.Errorf("仓储吞掉了唯一冲突: %v", err)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM outbox_messages WHERE message_id = $1`, messageID); n != 1 {
		t.Errorf("同 message_id 有 %d 行，18 §10 的稳定 ID 语义失效", n)
	}
}

// TestN7ConcurrentClaimsAreDisjoint 是两个都还开着的事务先后认领（18 §5）：
// 第一个锁住的行对第二个不可见，靠 SKIP LOCKED 而不是靠租约时序。
//
// 用「顺序开启的两个事务」而不是两个 goroutine 竞速：锁重叠是真的，结论却是确定的，
// 竞速版本会在 CI 上随机红，且红了也分不清是 SKIP LOCKED 失效还是调度问题。
func TestN7ConcurrentClaimsAreDisjoint(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n7-skiplocked"
	defer cleanupCanonical(t, pool, userID)
	ctx := context.Background()

	mine := make([]string, 0, 6)
	for i := 0; i < 6; i++ {
		mine = append(mine, n7PlantOutbox(t, pool, userID, "sl"))
	}

	connA, err := pool.Acquire(ctx)
	if err != nil {
		t.Fatalf("acquire A: %v", err)
	}
	defer connA.Release()
	connB, err := pool.Acquire(ctx)
	if err != nil {
		t.Fatalf("acquire B: %v", err)
	}
	defer connB.Release()

	txA, err := connA.Begin(ctx)
	if err != nil {
		t.Fatalf("begin A: %v", err)
	}
	idsA := n7ClaimIDs(t, pool, txA)
	txATouched := n7Intersect(mine, idsA)
	if len(txATouched) != len(mine) {
		t.Fatalf("事务 A 只认领到我这 6 行里的 %d 行，认领面或被别的状态挡住了：%v",
			len(txATouched), idsA)
	}

	// A 还没提交：它锁住的行必须被 B 跳过。
	txB, err := connB.Begin(ctx)
	if err != nil {
		t.Fatalf("begin B: %v", err)
	}
	idsB := n7ClaimIDs(t, pool, txB)
	if overlap := n7Intersect(idsA, idsB); len(overlap) != 0 {
		t.Fatalf("两个事务认领重叠 %v，多 worker 会双投（18 §5）", overlap)
	}
	if touched := n7Intersect(mine, idsB); len(touched) != 0 {
		t.Errorf("B 拿到了 A 锁住的行 %v，SKIP LOCKED 没生效", touched)
	}

	if err := txA.Commit(ctx); err != nil {
		t.Fatalf("commit A: %v", err)
	}
	if err := txB.Commit(ctx); err != nil {
		t.Fatalf("commit B: %v", err)
	}
	// 最强的一条：每行只被 ++ 一次。双认领在这一步就会暴露成 attempt_count=2。
	for _, id := range mine {
		status, attempts, _, _, _ := n7OutboxState(t, pool, id)
		if status != "publishing" || attempts != 1 {
			t.Errorf("行 %s status=%q attempt=%d，期望 publishing/1（各 worker 只认领一次）",
				id, status, attempts)
		}
	}
}

func n7Intersect(a, b []string) []string {
	var out []string
	for _, x := range a {
		if slices.Contains(b, x) {
			out = append(out, x)
		}
	}
	return out
}

// TestN7StaleLeaseIsReclaimed 测 available_at 一列两用（N7-2）的租约那一半：
// 崩溃 worker 留下的 publishing 孤儿行，到期后必须被别的 worker 重取并计入一次尝试。
func TestN7StaleLeaseIsReclaimed(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n7-lease"
	defer cleanupCanonical(t, pool, userID)
	repo := NewCanonical(pool)
	ctx := context.Background()

	id := n7PlantOutbox(t, pool, userID, "lease")
	claims, err := repo.ClaimOutboxBatch(ctx, 500, n7WorkerPolicy().LeaseSeconds)
	if err != nil {
		t.Fatalf("first claim: %v", err)
	}
	n7MustContain(t, claims, id, "首轮认领")

	status, attempts, leaseLeft, _, _ := n7OutboxState(t, pool, id)
	if status != "publishing" || attempts != 1 {
		t.Fatalf("status=%q attempt=%d，首轮认领后应 publishing/1", status, attempts)
	}
	if leaseLeft <= 115*time.Second || leaseLeft > 121*time.Second {
		t.Errorf("租约剩 %s，期望 ~120s（策略给的 LeaseSeconds）", leaseLeft)
	}

	// 租约没到期：孤儿行还不该被别人拿走。
	claims, err = repo.ClaimOutboxBatch(ctx, 500, 120)
	if err != nil {
		t.Fatalf("second claim: %v", err)
	}
	if n7ContainsClaim(claims, id) {
		t.Errorf("租约未到期就被重认领，慢发布会被并发双投")
	}

	// worker 崩溃：库侧留下的就是「publishing + 未来某时刻到期」。把到期时刻挪到过去，
	// 等价于等 120s，但结论确定（不靠 sleep 赌时序）。
	n7Exec(t, pool, `UPDATE outbox_messages SET available_at = now() - interval '1 second'
		WHERE id = $1`, id)
	claims, err = repo.ClaimOutboxBatch(ctx, 500, 120)
	if err != nil {
		t.Fatalf("third claim: %v", err)
	}
	n7MustContain(t, claims, id, "租约到期后的重取")
	if _, attempts, _, _, _ := n7OutboxState(t, pool, id); attempts != 2 {
		t.Errorf("attempt=%d，重取也要计入尝试次数（DDL 注释：认领时 ++）", attempts)
	}
}

// TestN7PublishedRequiresLease 是 18 §6 的库侧形式：broker 确认之前不得 published，
// 而确认写入的 `AND status='publishing'` 守卫必须在真库上放行/拦得住。
func TestN7PublishedRequiresLease(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n7-confirm"
	defer cleanupCanonical(t, pool, userID)
	repo := NewCanonical(pool)
	ctx := context.Background()

	id := n7PlantOutbox(t, pool, userID, "confirm")
	if ok, err := repo.MarkOutboxPublished(ctx, id); err != nil || ok {
		t.Fatalf("pending 行的确认 ok=%v err=%v，租约守卫必须拦住（18 §6）", ok, err)
	}
	if status, _, _, published, _ := n7OutboxState(t, pool, id); status != "pending" || published {
		t.Errorf("status=%q published=%v，被拦的确认不该改状态", status, published)
	}

	claims, err := repo.ClaimOutboxBatch(ctx, 500, 120)
	if err != nil {
		t.Fatalf("claim: %v", err)
	}
	n7MustContain(t, claims, id, "认领")
	ok, err := repo.MarkOutboxPublished(ctx, id)
	if err != nil || !ok {
		t.Fatalf("持有租约的确认 ok=%v err=%v", ok, err)
	}
	status, _, due, published, lastErr := n7OutboxState(t, pool, id)
	if status != "published" || !published {
		t.Errorf("status=%q published=%v，确认后应 published 且有 published_at", status, published)
	}
	if due > 0 {
		t.Errorf("published 行的 available_at 还在未来 %s，不该再参与退避", due)
	}
	if lastErr != "" {
		t.Errorf("last_error=%q，确认要清空失败原因", lastErr)
	}

	// 终态之后：重复确认被守卫拒绝，且这行永不进认领面（否则会被二次投递）。
	if ok, err := repo.MarkOutboxPublished(ctx, id); err != nil || ok {
		t.Errorf("二次确认 ok=%v err=%v，published 是终态", ok, err)
	}
	claims, err = repo.ClaimOutboxBatch(ctx, 500, 120)
	if err != nil {
		t.Fatalf("claim after publish: %v", err)
	}
	if n7ContainsClaim(claims, id) {
		t.Error("已投递的行又被认领，会双投（18 §4 的终态排除失效）")
	}
}

// TestN7WorkerRetriesAndDeadLetters 跑真 worker 对真库：14 §8 的阶梯、17 §9 的死信、
// 以及「认领面尊重退避时刻」三件事在库上成立。
func TestN7WorkerRetriesAndDeadLetters(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n7-worker"
	defer cleanupCanonical(t, pool, userID)

	failing := &n7Publisher{failFor: map[string]bool{}}
	succeeding := &n7Publisher{failFor: map[string]bool{}}

	// ① 第一次发布失败 ⇒ failed + 阶梯第一档（30s），且到期前不可再认领。
	retryID := n7PlantOutbox(t, pool, userID, "retry")
	failing.failFor["msg-retry-"+retryID] = true
	result := n7PublishOnce(t, pool, failing)
	if result.Claimed == 0 {
		t.Fatal("worker 一条都没认领到，投递面没接上库")
	}
	status, attempts, delay, published, lastErr := n7OutboxState(t, pool, retryID)
	if status != "failed" || attempts != 1 {
		t.Fatalf("status=%q attempt=%d，首次失败应 failed/1", status, attempts)
	}
	if delay < 25*time.Second || delay > 35*time.Second {
		t.Errorf("available_at 在未来 %s，期望 ~30s（14 §8 第一档）", delay)
	}
	if lastErr == "" {
		t.Error("last_error 空，失败原因没记账（17 §9 的 failure record）")
	}
	if claims, err := NewCanonical(pool).ClaimOutboxBatch(context.Background(), 500, 120); err != nil {
		t.Fatalf("claim during backoff: %v", err)
	} else if n7ContainsClaim(claims, retryID) {
		t.Error("退避期内就被重认领，阶梯形同虚设")
	}

	// ② 到期后重发成功 ⇒ published，attempt 记第二次尝试。
	n7Exec(t, pool, `UPDATE outbox_messages SET available_at = now() - interval '1 second'
		WHERE id = $1`, retryID)
	n7PublishOnce(t, pool, succeeding)
	status, attempts, _, published, lastErr = n7OutboxState(t, pool, retryID)
	if status != "published" || !published || attempts != 2 {
		t.Fatalf("status=%q published=%v attempt=%d，重发成功后应 published/是/2",
			status, published, attempts)
	}
	if lastErr != "" {
		t.Errorf("last_error=%q，确认时要清空", lastErr)
	}

	// ③ 到 MaxAttempts 为止：认领把 attempt 抬到 3（策略里是上限）⇒ dead_lettered。
	poisonID := n7PlantOutbox(t, pool, userID, "poison")
	n7Exec(t, pool, `UPDATE outbox_messages SET attempt_count = 2 WHERE id = $1`, poisonID)
	failing.failFor["msg-poison-"+poisonID] = true
	result = n7PublishOnce(t, pool, failing)
	if result.DeadLetted == 0 {
		t.Errorf("Result=%+v，到达上限的失败应转死信", result)
	}
	status, attempts, _, _, lastErr = n7OutboxState(t, pool, poisonID)
	if status != "dead_lettered" || attempts != 3 || lastErr == "" {
		t.Errorf("status=%q attempt=%d last_error=%q，期望 dead_lettered/3/有原因（17 §9）",
			status, attempts, lastErr)
	}
	if claims, err := NewCanonical(pool).ClaimOutboxBatch(context.Background(), 500, 120); err != nil {
		t.Fatalf("claim after dead letter: %v", err)
	} else if n7ContainsClaim(claims, poisonID) {
		t.Error("死信又进认领面，毒消息会永久阻塞队列")
	}
}

// TestN7ConsumeIdempotentProcessedThenDuplicate 是 18 §8 的事务形状在真库上的样子：
// 第二次投递必须 ACK，而且**副作用一次都不许多**。
func TestN7ConsumeIdempotentProcessedThenDuplicate(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n7-consume"
	const consumer = "go.n7.consume"
	defer func() {
		cleanupCanonical(t, pool, userID)
		n7Exec(t, pool, `DELETE FROM inbox_messages WHERE consumer = $1`, consumer)
	}()

	p := plantUser(t, pool, userID)
	msgID := "in-" + newID()
	runs := 0
	fn := func(ctx context.Context, st domain.Store) error {
		runs++
		return st.AppendEvent(ctx, domain.EventRow{ID: newID(), UserID: userID,
			PersonalModelID: p.modelID, EventType: "pattern_lifecycle",
			AggregateType: "patterns", AggregateID: newID(), ModelVersion: 1,
			Payload: `{"status":"weakened"}`})
	}

	res, err := serviceFor(pool).ConsumeIdempotent(context.Background(),
		domain.InboundMessage{MessageID: msgID, MessageType: "pattern.updated",
			Consumer: consumer}, fn)
	if err != nil {
		t.Fatalf("首次消费: %v", err)
	}
	if res.Status != domain.ConsumeProcessed || !res.Ack {
		t.Fatalf("首次结果=%+v，期望 processed+ACK（14 §7）", res)
	}
	firstRun := runs

	res, err = serviceFor(pool).ConsumeIdempotent(context.Background(),
		domain.InboundMessage{MessageID: msgID, MessageType: "pattern.updated",
			Consumer: consumer}, fn)
	if err != nil {
		t.Fatalf("重复投递: %v", err)
	}
	if res.Status != domain.ConsumeDuplicate || !res.Ack {
		t.Errorf("重复结果=%+v，期望 duplicate+ACK", res)
	}
	if runs != firstRun {
		t.Errorf("业务副作用跑了 %d 次，幂等闸在真库上没拦住", runs)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE user_id = $1`, userID); n != 1 {
		t.Errorf("事件行 %d 条，重复投递不该留第二条", n)
	}
	var status string
	var processed bool
	var attempts int
	err = pool.QueryRow(context.Background(),
		`SELECT status, processed_at IS NOT NULL, attempt_count FROM inbox_messages
		 WHERE message_id = $1 AND consumer = $2`, msgID, consumer,
	).Scan(&status, &processed, &attempts)
	if err != nil {
		t.Fatalf("read inbox: %v", err)
	}
	if status != "processed" || !processed {
		t.Errorf("inbox status=%q processed=%v，期望 processed 且有 processed_at（18 §8）",
			status, processed)
	}
	if attempts != 0 {
		t.Errorf("attempt_count=%d，正常消费路径不动它（计数只在永久拒绝里有意义）", attempts)
	}

	// 幂等键的另一半：同一个 message_id 换个消费者就是它自己的第一次。
	other := "go.n7.other"
	defer n7Exec(t, pool, `DELETE FROM inbox_messages WHERE consumer = $1`, other)
	res, err = serviceFor(pool).ConsumeIdempotent(context.Background(),
		domain.InboundMessage{MessageID: msgID, MessageType: "pattern.updated",
			Consumer: other}, fn)
	if err != nil {
		t.Fatalf("第二消费者: %v", err)
	}
	if res.Status != domain.ConsumeProcessed {
		t.Errorf("另一消费者结果=%+v，(message_id, consumer) 才是唯一键（18 §7）", res)
	}
}

// TestN7ConsumeIdempotentTransientLeavesNoInboxRow 落 18 §9 case 1 的库侧事实：
// 瞬时失败连幂等行一起回滚，所以重投就是重来——这也是「毒消息上限归 broker」的依据。
func TestN7ConsumeIdempotentTransientLeavesNoInboxRow(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n7-transient"
	const consumer = "go.n7.transient"
	defer func() {
		cleanupCanonical(t, pool, userID)
		n7Exec(t, pool, `DELETE FROM inbox_messages WHERE consumer = $1`, consumer)
	}()

	p := plantUser(t, pool, userID)
	msgID := "in-" + newID()
	boom := errors.New("下游暂时不可用")
	failing := func(ctx context.Context, st domain.Store) error {
		if err := st.AppendEvent(ctx, domain.EventRow{ID: newID(), UserID: userID,
			PersonalModelID: p.modelID, EventType: "evidence_withdrawn",
			AggregateType: "evidence", AggregateID: newID(), ModelVersion: 1,
			Payload: `{"reason":"source deleted"}`}); err != nil {
			t.Errorf("AppendEvent: %v", err)
		}
		return boom
	}

	res, err := serviceFor(pool).ConsumeIdempotent(context.Background(),
		domain.InboundMessage{MessageID: msgID, MessageType: "evidence.withdrawn",
			Consumer: consumer}, failing)
	if !errors.Is(err, boom) {
		t.Fatalf("瞬时失败 err=%v，原因要原样带回（Ack=false 才是 broker 动作依据）", err)
	}
	if res.Status != domain.ConsumeTransient || res.Ack {
		t.Fatalf("结果=%+v，期望 transient+NACK（14 §7）", res)
	}
	for _, probe := range []struct{ what, query, arg string }{
		{"inbox_messages", `SELECT count(*) FROM inbox_messages WHERE message_id = $1`, msgID},
		{"personal_model_events",
			`SELECT count(*) FROM personal_model_events WHERE user_id = $1`, userID},
	} {
		if n := countWhere(t, pool, probe.query, probe.arg); n != 0 {
			t.Errorf("%s 留下 %d 行：瞬时失败必须整笔回滚，否则重投会被当成重复而跳过",
				probe.what, n)
		}
	}

	// 重投真的重来：这次成功，就该有恰好一条副作用。
	res, err = serviceFor(pool).ConsumeIdempotent(context.Background(),
		domain.InboundMessage{MessageID: msgID, MessageType: "evidence.withdrawn",
			Consumer: consumer}, func(ctx context.Context, st domain.Store) error {
			return st.AppendEvent(ctx, domain.EventRow{ID: newID(), UserID: userID,
				PersonalModelID: p.modelID, EventType: "evidence_withdrawn",
				AggregateType: "evidence", AggregateID: newID(), ModelVersion: 1,
				Payload: `{"reason":"source deleted"}`})
		})
	if err != nil || res.Status != domain.ConsumeProcessed {
		t.Fatalf("重投结果=%+v err=%v，回滚后重投应正常处理", res, err)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE user_id = $1`, userID); n != 1 {
		t.Errorf("事件行 %d 条，回滚那次不该留下行", n)
	}
}

// TestN7ConsumeIdempotentPermanentSurvivesRollback 是 14 §7 第三类 ACK 的库侧证据：
// 主事务回滚了，但永久拒绝的留痕必须在账上，且第二次投递据此 ACK 而不是无限重投。
func TestN7ConsumeIdempotentPermanentSurvivesRollback(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n7-permanent"
	const consumer = "go.n7.permanent"
	defer func() {
		cleanupCanonical(t, pool, userID)
		n7Exec(t, pool, `DELETE FROM inbox_messages WHERE consumer = $1`, consumer)
	}()

	p := plantUser(t, pool, userID)
	msgID := "in-" + newID()
	rejected := func(ctx context.Context, st domain.Store) error {
		if err := st.AppendEvent(ctx, domain.EventRow{ID: newID(), UserID: userID,
			PersonalModelID: p.modelID, EventType: "insight_admitted",
			AggregateType: "insights", AggregateID: newID(), ModelVersion: 1,
			Payload: `{}`}); err != nil {
			t.Errorf("AppendEvent: %v", err)
		}
		return domain.PermanentRejection{Code: "unsupported_schema_version",
			Detail: "schema_version 9 不在解码面"}
	}

	res, err := serviceFor(pool).ConsumeIdempotent(context.Background(),
		domain.InboundMessage{MessageID: msgID, MessageType: "insight.created",
			Consumer: consumer}, rejected)
	if res.Status != domain.ConsumeRejected || !res.Ack {
		t.Fatalf("结果=%+v err=%v，永久拒绝要留痕后 ACK", res, err)
	}
	var status, errorCode string
	var attempts int
	err = pool.QueryRow(context.Background(),
		`SELECT status, coalesce(error_code, ''), attempt_count FROM inbox_messages
		 WHERE message_id = $1 AND consumer = $2`, msgID, consumer).
		Scan(&status, &errorCode, &attempts)
	if err != nil {
		t.Fatalf("留痕没写进库（18 §8 之外的独立事务失败）: %v", err)
	}
	if status != "failed" || errorCode != "unsupported_schema_version" || attempts != 1 {
		t.Errorf("inbox=%q/%q/%d，期望 failed/unsupported_schema_version/1", status, errorCode, attempts)
	}
	// 主事务回滚：业务事件不留，幂等行留——两者不共生死是刻意的。
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE user_id = $1`, userID); n != 0 {
		t.Errorf("永久拒绝却留下 %d 条业务事件，回滚没发生", n)
	}

	res, err = serviceFor(pool).ConsumeIdempotent(context.Background(),
		domain.InboundMessage{MessageID: msgID, MessageType: "insight.created",
			Consumer: consumer}, rejected)
	if res.Status != domain.ConsumeRejected || !res.Ack {
		t.Errorf("二次投递结果=%+v err=%v，已记录拒绝的消息不该再回队列", res, err)
	}
	err = pool.QueryRow(context.Background(),
		`SELECT status, coalesce(error_code, ''), attempt_count FROM inbox_messages
		 WHERE message_id = $1 AND consumer = $2`, msgID, consumer).
		Scan(&status, &errorCode, &attempts)
	if err != nil {
		t.Fatalf("read inbox: %v", err)
	}
	// 二次投递在 InsertInbox 那一步就被判重复，回调根本没跑，所以**不会**再记一次账：
	// attempt_count 记的是「永久拒绝被记录了几次」，不是「消息被投了几次」。
	// 若这里变成 2，说明重复投递又执行了一遍永久拒绝路径，幂等闸漏了。
	if attempts != 1 || status != "failed" || errorCode != "unsupported_schema_version" {
		t.Errorf("inbox=%q/%q/%d，重投应原样 ACK 且不二次记账", status, errorCode, attempts)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE user_id = $1`, userID); n != 0 {
		t.Errorf("重投留下 %d 条业务事件，回调不该被执行", n)
	}

	// upsert 的累加面单独证：绕过消费闸直接按同一 (message_id, consumer) 记两次账。
	// 这条路径真实存在——并发下两个 worker 可能同时判定同一条消息永久非法。
	upsertKey := "in-" + newID()
	for want := 1; want <= 2; want++ {
		if err := NewCanonical(pool).RecordInboxFailure(context.Background(),
			domain.InboxFailureRow{ID: newID(), MessageID: upsertKey,
				MessageType: "insight.created", Consumer: consumer,
				ErrorCode: "unsupported_schema_version"}); err != nil {
			t.Fatalf("RecordInboxFailure #%d: %v", want, err)
		}
		var got int
		var rowStatus string
		err = pool.QueryRow(context.Background(),
			`SELECT status, attempt_count FROM inbox_messages
			 WHERE message_id = $1 AND consumer = $2`, upsertKey, consumer).
			Scan(&rowStatus, &got)
		if err != nil {
			t.Fatalf("read upsert row #%d: %v", want, err)
		}
		if got != want || rowStatus != "failed" {
			t.Errorf("第 %d 次留痕后 status=%q attempt=%d", want, rowStatus, got)
		}
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM inbox_messages WHERE message_id = $1`, upsertKey); n != 1 {
		t.Errorf("同键 %d 行，upsert 写成了插入", n)
	}
}

// TestN7ConsumeIdempotentOutboxCarriesInboundCausation 证明 ctx 里的关联标识
// 真的穿过了真事务（17 §6/§7）：消费事务内产出的投递行，直接原因就是这条入站消息。
func TestN7ConsumeIdempotentOutboxCarriesInboundCausation(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n7-causation"
	const consumer = "go.n7.causation"
	defer func() {
		cleanupCanonical(t, pool, userID)
		n7Exec(t, pool, `DELETE FROM inbox_messages WHERE consumer = $1`, consumer)
	}()

	p := plantUser(t, pool, userID)
	inboundID := "in-" + newID()
	res, err := serviceFor(pool).ConsumeIdempotent(context.Background(),
		domain.InboundMessage{MessageID: inboundID, MessageType: "ai.insight.draft",
			Consumer: consumer, CorrelationID: "corr-user-action"},
		func(ctx context.Context, st domain.Store) error {
			env, ok := domain.EnvelopeFrom(ctx)
			if !ok {
				return errors.New("消费事务里没拿到 envelope")
			}
			if err := st.AppendEvent(ctx, domain.EventRow{ID: newID(), UserID: userID,
				PersonalModelID: p.modelID, EventType: "insight_retracted",
				AggregateType: "insights", AggregateID: newID(), ModelVersion: 1,
				Payload: `{"status":"retracted"}`}); err != nil {
				return err
			}
			return st.EnqueueOutbox(ctx, domain.OutboxRow{ID: newID(),
				MessageID: "msg-" + newID(), MessageType: "insight_retracted",
				SchemaVersion: domain.DefaultPolicy().Delivery.SchemaVersion,
				RoutingKey:    "insight.retracted",
				UserID:        userID, CorrelationID: env.CorrelationID,
				CausationID: env.CausationID, Payload: `{"status":"retracted"}`})
		})
	if err != nil {
		t.Fatalf("ConsumeIdempotent: %v", err)
	}
	if res.Status != domain.ConsumeProcessed || !res.Ack {
		t.Fatalf("结果=%+v", res)
	}

	var correlation, causation string
	err = pool.QueryRow(context.Background(),
		`SELECT correlation_id, causation_id FROM outbox_messages WHERE user_id = $1`,
		userID).Scan(&correlation, &causation)
	if err != nil {
		t.Fatalf("read outbox: %v", err)
	}
	if correlation != "corr-user-action" {
		t.Errorf("correlation=%q，入站值没穿过事务（17 §6）", correlation)
	}
	if causation != inboundID {
		t.Errorf("causation=%q，应指向入站 message_id %q（N7-6）", causation, inboundID)
	}
}

// TestN7ConsumeRejectsOverWideKeyAndValidatesMessage 覆盖两条只有真库能证的边界：
// 缺字段的入站消息直接报错（不猜消费者），而**列宽是库给的**——超宽 consumer 必须在
// 留下半行幂等状态之前失败，fake 那一层永远撞不到这种宽度。
func TestN7ConsumeRejectsOverWideKeyAndValidatesMessage(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n7-validate"
	defer cleanupCanonical(t, pool, userID)
	svc := serviceFor(pool)
	noop := func(ctx context.Context, st domain.Store) error { return nil }

	if _, err := svc.ConsumeIdempotent(context.Background(),
		domain.InboundMessage{MessageID: "", Consumer: "go.n7.validate"}, noop); err == nil {
		t.Error("缺 message_id 应当报错（18 §7 的唯一键两面）")
	}
	if _, err := svc.ConsumeIdempotent(context.Background(),
		domain.InboundMessage{MessageID: "m", Consumer: ""}, noop); err == nil {
		t.Error("缺 consumer 应当报错")
	}
	if _, err := svc.ConsumeIdempotent(context.Background(),
		domain.InboundMessage{MessageID: "m", Consumer: "go.n7.validate"}, nil); err == nil {
		t.Error("缺业务回调应当报错")
	}

	// 超宽 consumer（inbox_messages.consumer 是 VARCHAR(64)，N2 DDL 实测）：
	// 这一笔必须在库里留下**零行**，并且按可重投处理——幂等行都没写成，绝不能 ACK。
	// domain 只截 error_code，consumer/message_id 的宽度只有库能拦，所以这条留在集成测。
	longConsumer := strings.Repeat("c", 65)
	res, err := svc.ConsumeIdempotent(context.Background(),
		domain.InboundMessage{MessageID: "in-" + newID(), MessageType: "fact.created",
			Consumer: longConsumer}, noop)
	if err == nil {
		t.Fatal("超宽 consumer 必须报错，不能静默当成一次正常消费")
	}
	if res.Status != domain.ConsumeTransient || res.Ack {
		t.Errorf("结果=%+v，幂等行没写成就不该 ACK（17 §5 的 COMMIT→ACK 次序）", res)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM inbox_messages WHERE consumer = $1`, longConsumer); n != 0 {
		t.Errorf("超宽 consumer 留下了 %d 行", n)
	}
}

// TestN7MessageDedupIsNotBusinessDedup 落 18 §11 的那句「消息幂等挡不住语义重复」：
// 两条**不同 message_id**、业务内容同一指纹的入站消息，幂等闸必须都放行（它们确实
// 不是同一条消息），把第二次的权威写入拦下来的是 `patterns.fingerprint` 那把闸。
//
// 回调里的查重用的是与 `Gate` 同一个判据（`FindPatternByFingerprint`）——N7 要钉的
// 不是那条闸本身（N3 已交付，`TestAdmitReactivatesPatternOnFingerprint` 钉过），
// 而是**它不能被 inbox 闸代替**：只有真库能同时给出「inbox 三行 + patterns 一行」。
func TestN7MessageDedupIsNotBusinessDedup(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n7-msg-vs-biz"
	const consumer = "go.n7.msgvsbiz"
	defer func() {
		cleanupCanonical(t, pool, userID)
		n7Exec(t, pool, `DELETE FROM inbox_messages WHERE consumer = $1`, consumer)
	}()
	ctx := context.Background()
	p := plantUser(t, pool, userID)
	svc := serviceFor(pool)

	fingerprint := baseCandidate(p, domain.KindPattern).Fingerprint
	if fingerprint == "" {
		t.Fatal("baseCandidate 没带指纹，这条用例的前提不成立")
	}
	ran := 0
	consume := func(messageID string) domain.ConsumeResult {
		res, err := svc.ConsumeIdempotent(ctx, domain.InboundMessage{
			MessageID: messageID, MessageType: "pattern.created", Consumer: consumer},
			func(ctx context.Context, st domain.Store) error {
				ran++
				_, found, err := st.FindPatternByFingerprint(ctx, userID, fingerprint)
				if err != nil || found {
					return err
				}
				return st.InsertPattern(ctx, domain.PatternRow{
					ID: newID(), UserID: userID, PersonalModelID: p.modelID,
					PatternType: "temporal_rhythm", SubjectKey: "user", Scope: "work",
					Description: "workday entries cluster after 23:00", Status: "candidate",
					Confidence: 0.6, HasConfidence: true, DetectorVersion: "v1",
					Fingerprint: fingerprint, ModelVersion: 1, GenerationRunID: newID(),
				})
			})
		if err != nil {
			t.Fatalf("consume %s: %v", messageID, err)
		}
		return res
	}

	firstID := "in-" + newID()
	first := consume(firstID)
	second := consume("in-" + newID())
	if first.Status != domain.ConsumeProcessed || second.Status != domain.ConsumeProcessed {
		t.Fatalf("两次结果=%+v / %+v，不同 message_id 必须各自都是首次投递（18 §11）",
			first, second)
	}
	if ran != 2 {
		t.Errorf("业务回调跑了 %d 次，期望 2：语义重复不是消息重复", ran)
	}

	// 对照的那一轴：同一个 message_id 再投一次，这次拦下来的是幂等闸，
	// 副作用不再执行——两套判据正交，缺一条就有另一条漏掉的重复。
	replay := consume(firstID)
	if replay.Status != domain.ConsumeDuplicate || ran != 2 {
		t.Errorf("重放结果=%+v ran=%d，同 message_id 必须跳过副作用（18 §8）", replay, ran)
	}

	if n := countWhere(t, pool, `SELECT count(*) FROM patterns WHERE user_id = $1`,
		userID); n != 1 {
		t.Errorf("patterns=%d，同一指纹落进权威表两行（18 §11 的业务幂等失效）", n)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM inbox_messages WHERE consumer = $1`, consumer); n != 2 {
		t.Errorf("inbox=%d，期望 2：两个 message_id 各一行（18 §7 的键面）", n)
	}
}

// TestN7CleanupDeliveryRecordsKeepsPendingAndDeadLetters 是 18 §13 的落法：
// 保留期只搬走尘埃落定的投递，待办与死信一律留着排查。
func TestN7CleanupDeliveryRecordsKeepsPendingAndDeadLetters(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n7-cleanup"
	const consumer = "go.n7.cleanup"
	defer func() {
		cleanupCanonical(t, pool, userID)
		n7Exec(t, pool, `DELETE FROM inbox_messages WHERE consumer = $1`, consumer)
	}()
	ctx := context.Background()

	oldPublished := n7PlantOutbox(t, pool, userID, "old-pub")
	n7Exec(t, pool, `UPDATE outbox_messages SET status = 'published',
		published_at = now() - make_interval(days => 40) WHERE id = $1`, oldPublished)
	recentPublished := n7PlantOutbox(t, pool, userID, "new-pub")
	n7Exec(t, pool, `UPDATE outbox_messages SET status = 'published',
		published_at = now() - interval '1 hour' WHERE id = $1`, recentPublished)
	ancientPending := n7PlantOutbox(t, pool, userID, "old-pending")
	n7Exec(t, pool, `UPDATE outbox_messages SET available_at = now() - make_interval(days => 400)
		WHERE id = $1`, ancientPending)
	ancientDead := n7PlantOutbox(t, pool, userID, "old-dead")
	n7Exec(t, pool, `UPDATE outbox_messages SET status = 'dead_lettered',
		last_error = 'broker gone', available_at = now() - make_interval(days => 400)
		WHERE id = $1`, ancientDead)
	// 终态限定与时间限定是两件事，这一行把它们分开：一条**非终态却带着旧
	// published_at** 的行。现役语句造不出它（published 是终态、不再被认领），
	// 所以这条断言防的是「以后有人拿 published_at 当『已发布』的判据」——
	// 18 §13 认的是状态，时间戳只是历史戳。
	weakened := n7PlantOutbox(t, pool, userID, "old-but-not-terminal")
	n7Exec(t, pool, `UPDATE outbox_messages SET status = 'failed',
		published_at = now() - make_interval(days => 40), last_error = 'confirm lost'
		WHERE id = $1`, weakened)

	oldProcessed := n7PlantInbox(t, pool, "in-"+newID(), consumer, "processed", 100)
	recentProcessed := n7PlantInbox(t, pool, "in-"+newID(), consumer, "processed", 1)
	// failed 行没有 processed_at（-1 走 NULL 分支）：它靠状态就被排除在清理之外。
	oldFailed := n7PlantInbox(t, pool, "in-"+newID(), consumer, "failed", -1)

	policy := domain.DefaultPolicy()
	policy.Delivery = n7WorkerPolicy()
	result, err := domain.NewService(NewCanonical(pool), policy).CleanupDeliveryRecords(ctx)
	if err != nil {
		t.Fatalf("CleanupDeliveryRecords: %v", err)
	}
	if result.OutboxDeleted < 1 || result.InboxDeleted < 1 {
		t.Errorf("清理计数=%+v，至少该搬走我埋的那两条超期行", result)
	}
	if result.OutboxBefore.IsZero() || !result.OutboxBefore.Before(time.Now()) {
		t.Errorf("outbox 截止=%v，应是 now()-保留期", result.OutboxBefore)
	}

	n7MustExist(t, pool, "outbox_messages", "id", oldPublished, false)
	n7MustExist(t, pool, "outbox_messages", "id", recentPublished, true)
	n7MustExist(t, pool, "outbox_messages", "id", ancientPending, true)
	n7MustExist(t, pool, "outbox_messages", "id", ancientDead, true)
	n7MustExist(t, pool, "outbox_messages", "id", weakened, true)
	n7MustExist(t, pool, "inbox_messages", "id", oldProcessed, false)
	n7MustExist(t, pool, "inbox_messages", "id", recentProcessed, true)
	n7MustExist(t, pool, "inbox_messages", "id", oldFailed, true)
}

func n7PlantInbox(t *testing.T, pool *pgxpool.Pool, messageID, consumer,
	status string, processedDaysAgo int) string {
	t.Helper()
	id := newID()
	n7Exec(t, pool,
		`INSERT INTO inbox_messages (id, message_id, message_type, consumer, status,
		                                processed_at, received_at)
		 VALUES ($1, $2, 'fact.created', $3, $4,
		         CASE WHEN $5::int < 0 THEN NULL
		              ELSE now() - make_interval(days => $5::int) END, now())`,
		id, messageID, consumer, status, processedDaysAgo)
	return id
}

// TestN7OutboxRowsPurgeWithUser 是 D-5 的后果核验：没有 ON DELETE CASCADE，
// 「payload 里的个人正文删得净」全靠 outbox_messages 在按 user_id 的清理清单里。
func TestN7OutboxRowsPurgeWithUser(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n7-purge"

	p := plantUser(t, pool, userID)
	c := baseCandidate(p, domain.KindFact)
	c.Origin = "direct"
	if _, err := serviceFor(pool).Admit(context.Background(), c); err != nil {
		t.Fatalf("Admit: %v", err)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM outbox_messages WHERE user_id = $1`, userID); n != 1 {
		t.Fatalf("投递行 %d 条，purge 用例的前提不成立", n)
	}

	cleanupCanonical(t, pool, userID)
	for _, probe := range []struct{ table, query string }{
		{"outbox_messages", `SELECT count(*) FROM outbox_messages WHERE user_id = $1`},
		{"personal_model_events", `SELECT count(*) FROM personal_model_events WHERE user_id = $1`},
		{"personal_facts", `SELECT count(*) FROM personal_facts WHERE user_id = $1`},
	} {
		if n := countWhere(t, pool, probe.query, userID); n != 0 {
			t.Errorf("%s 残留 %d 行：注销后个人内容还留在库里（七-6 N7-1 偏离①的意义）",
				probe.table, n)
		}
	}
}

// ── 断言小工具 ──────────────────────────────────────────────────────────────

func n7ContainsClaim(claims []domain.OutboxClaim, id string) bool {
	for _, claim := range claims {
		if claim.ID == id {
			return true
		}
	}
	return false
}

func n7MustContain(t *testing.T, claims []domain.OutboxClaim, id, what string) {
	t.Helper()
	if !n7ContainsClaim(claims, id) {
		t.Fatalf("%s没拿到行 %s（认领面共 %d 条）", what, id, len(claims))
	}
}

func n7MustExist(t *testing.T, pool *pgxpool.Pool, table, column, value string, want bool) {
	t.Helper()
	n := countWhere(t, pool, `SELECT count(*) FROM `+table+` WHERE `+column+` = $1`, value)
	if (n == 1) != want {
		t.Errorf("%s %s=%s 存在=%v（rows=%d），期望存在=%v", table, column, value, n == 1, n, want)
	}
}
