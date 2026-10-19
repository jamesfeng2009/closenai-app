// T4-03 的真库测：`outcomes` + `outcome_links` 的同事务写入，与
// `LatestOutcomeForTarget` 那条 SQL 自己的语义（工单 §验收 最后两格）。
//
// 为什么这几格只有真库能答（domain 的单测与桩都给不出）：
//
//  1. **「两行同事务」是库的原子性，不是 Go 的两个方法调用**。桩里 InsertOutcome 和
//     LinkOutcomeTargets 各自 append 一条，永远一起成功；库里如果 link 那句写了
//     `outcome_id` 却漏了 `user_id`，外键当场报错、整笔回滚——那才是 §验收 要的形态。
//  2. **「最近一条」的排序键在库上**。`ORDER BY created_at DESC, id DESC` 的次级键
//     只在「同一时刻落地两行」时才起作用（重放、批量补记），桩没有时间列，
//     把 `id DESC` 删掉在单测里照样绿。
//  3. **同日判定读的是库时钟写进 `created_at` 的那个值**，不是 Go 的 `time.Now()`。
//     两边相差一天（库在 UTC、进程在别的时区）时，重复提交会写出第二行，
//     而 04 §22 的完成度量凭空多一个分子。
//  4. **归属谓词只能证在跨用户那次查询上**：`WHERE l.user_id = $1` 少带半格，
//     别人那条行动的同型结果会进我的判定，同日幂等就会**吞掉**我这一行。
//
// 未设 TEST_DATABASE_URL 时跳过，与其余 store 集成测同口径（缺 env 是「未跑」，不是绿）。
package store

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store/canonicaldb"
)

// actionOutcomeIs 是 §验收 要的那个最小提案：一条挂在**真实存在的行动**上的用户自述。
func actionOutcomeIs(userID, actionID string,
	typ domain.OutcomeType) domain.ActionOutcomeProposal {
	return domain.ActionOutcomeProposal{UserID: userID, ActionID: actionID,
		Type: typ, Result: domain.OutcomeResultBetter,
		Note: "went three times this week", OperationID: "op-t403"}
}

func outcomeRowCount(t *testing.T, pool *pgxpool.Pool, userID string) int64 {
	t.Helper()
	return countWhere(t, pool, `SELECT count(*) FROM outcomes WHERE user_id = $1`, userID)
}

// actionOutcomeCount 按型计数而不是按用户：plantChain 会种一条 goal_progress，
// 「用户名下几行」那个数永远掺着别人的行， counts 一旦掺水，
// 「多写了一行」与「库里本来就有一行」就分不出来。
func actionOutcomeCount(t *testing.T, pool *pgxpool.Pool, userID string) int64 {
	t.Helper()
	return countWhere(t, pool,
		`SELECT count(*) FROM outcomes WHERE user_id = $1 AND outcome_type = $2`,
		userID, "commitment_completed")
}

func linkRowCount(t *testing.T, pool *pgxpool.Pool, outcomeID string) int64 {
	t.Helper()
	return countWhere(t, pool,
		`SELECT count(*) FROM outcome_links WHERE outcome_id = $1`, outcomeID)
}

// readCommitment 直接问列：读面把 NULL 折成零值，从读面反推「库里是什么」是同源断言。
func readCommitment(t *testing.T, pool *pgxpool.Pool, id string) (status, completedAt string) {
	t.Helper()
	err := pool.QueryRow(context.Background(),
		`SELECT status, coalesce(completed_at::text, '') FROM personal_commitments
		 WHERE id = $1`, id).Scan(&status, &completedAt)
	if err != nil {
		t.Fatalf("read commitment %s: %v", id, err)
	}
	return
}

// ── 1. 两行同事务 + 完成时刻写在真正使用的那张表上 ────────────────────────────

func TestT403ActionOutcomeWritesBothRowsAndCompletesAction(t *testing.T) {
	pool := poolForTest(t)
	ctx := context.Background()
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })

	c := plantChain(t, canonicaldb.New(pool), userID)
	before := readModelVersion(t, pool, userID)
	svc := serviceFor(pool)

	rec, err := svc.RecordActionOutcome(ctx, actionOutcomeIs(userID, c.commitmentID,
		domain.OutcomeCommitmentCompleted))
	if err != nil {
		t.Fatalf("RecordActionOutcome: %v", err)
	}
	// §验收 逐字「outcomes + outcome_links 两行同事务写入」：这一型恰好一行、回链恰好一条。
	if n := actionOutcomeCount(t, pool, userID); n != 1 {
		t.Errorf("commitment_completed %d 行，期望恰好 1", n)
	}
	if n := linkRowCount(t, pool, rec.OutcomeID); n != 1 {
		t.Errorf("outcome_links %d 行，期望恰好 1 ⇒ 只写 outcomes 不回链，"+
			"这条结果在 04 §22 的三个度量里等于不存在", n)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM outcome_links WHERE outcome_id = $1
		   AND target_type = 'commitment' AND target_id = $2 AND user_id = $3`,
		rec.OutcomeID, c.commitmentID, userID); n != 1 {
		t.Errorf("回链 (commitment,%s) %d 条，期望 1", c.commitmentID, n)
	}
	status, result, origin, outcomeType, description, modelVersion :=
		readOutcome(t, pool, rec.OutcomeID)
	if status != "recorded" || result != "better" || origin != "user_statement" ||
		outcomeType != "commitment_completed" {
		t.Errorf("库内 outcome=(%s,%s,%s,%s)：origin 必须是领域钉死的 user_statement，"+
			"否则客户端就能自报「这条算不算模型相信的事」", status, result, origin, outcomeType)
	}
	if !strings.Contains(description, "went three times") {
		t.Errorf("description=%q，备注没落到列上", description)
	}
	if got := readModelVersion(t, pool, userID); got != before+1 {
		t.Errorf("personal_models.current_version=%d，期望 %d（recorded 才 ++）",
			got, before+1)
	}
	if modelVersion != before+1 {
		t.Errorf("outcomes.model_version=%d，行上带的必须是新版本", modelVersion)
	}
	// 完成时刻落在 `personal_commitments`，不是 outcomes 表里的一个影子字段。
	if s, at := readCommitment(t, pool, c.commitmentID); s != "completed" || at == "" {
		t.Errorf("行动 = (%s, completed_at=%q)，期望 (completed, 非空) ⇒ 02:824 的完成要落到真正使用的那张表",
			s, at)
	}
	// 事件流那一格与 N6 同一口径：这一面写了 outcome 就得留下可追溯的一笔。
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE aggregate_id = $1`, rec.OutcomeID); n < 1 {
		t.Errorf("outcome %s 的事件 %d 条，期望 ≥1（不变量 6 的同族口径）", rec.OutcomeID, n)
	}
}

// ── 2. 同日只有一行，跨日才有第二行（§怎么做.5 在真库上的两格）──────────────────

func TestT403SameDayRepeatWritesNoSecondRowButNextDayDoes(t *testing.T) {
	pool := poolForTest(t)
	ctx := context.Background()
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })

	c := plantChain(t, canonicaldb.New(pool), userID)
	svc := serviceFor(pool)
	first, err := svc.RecordActionOutcome(ctx, actionOutcomeIs(userID, c.commitmentID,
		domain.OutcomeCommitmentCompleted))
	if err != nil {
		t.Fatalf("第一次: %v", err)
	}
	// 第二次刻意换 result：如果幂等那一格写成了「覆盖」，首次结论就没了，
	// 而用户看到的屏上会写着一句她自己没再说过的话。
	second, err := svc.RecordActionOutcome(ctx, func(p domain.ActionOutcomeProposal) domain.ActionOutcomeProposal {
		p.Result = domain.OutcomeResultWorse
		return p
	}(actionOutcomeIs(userID, c.commitmentID, domain.OutcomeCommitmentCompleted)))
	if err != nil {
		t.Fatalf("第二次: %v", err)
	}
	if second.Changed {
		t.Errorf("同日重复 Changed=true，期望 false：%+v", second)
	}
	if second.OutcomeID != first.OutcomeID {
		t.Errorf("第二次回的是 outcome=%s，期望首次的 %s", second.OutcomeID, first.OutcomeID)
	}
	if second.Result != "better" {
		t.Errorf("第二次回 result=%q，期望首次的 better ⇒ 幂等不许顺手改结论", second.Result)
	}
	if n := actionOutcomeCount(t, pool, userID); n != 1 {
		t.Fatalf("同日两次写出 %d 行这一型，期望 1", n)
	}
	if n := linkRowCount(t, pool, first.OutcomeID); n != 1 {
		t.Errorf("第二次还补了回链（%d 条）⇒ 幂等只挡了一半", n)
	}
	// 把库里那一行的 created_at 推到昨天：判定读的是**库时钟**，
	// 所以这一格只有真库能证（桩里 created_at 是 Go 现造的）。
	if _, err := pool.Exec(ctx, `UPDATE outcomes SET created_at = now() - interval '26 hours'
		WHERE id = $1`, first.OutcomeID); err != nil {
		t.Fatalf("拨回 created_at: %v", err)
	}
	third, err := svc.RecordActionOutcome(ctx, actionOutcomeIs(userID, c.commitmentID,
		domain.OutcomeCommitmentCompleted))
	if err != nil {
		t.Fatalf("跨日第三次: %v", err)
	}
	if !third.Changed || third.OutcomeID == first.OutcomeID {
		t.Errorf("跨日第二次 Changed=%v、outcome=%s，期望写第二行 ⇒ "+
			"同日那一格被写成了「这条行动一辈子只能有一次结果」", third.Changed, third.OutcomeID)
	}
	if n := actionOutcomeCount(t, pool, userID); n != 2 {
		t.Errorf("跨日后这一型 %d 行，期望 2（04 §22 的完成度量按天记账）", n)
	}
	if n := linkRowCount(t, pool, third.OutcomeID); n != 1 {
		t.Errorf("第二行的回链 %d 条，期望 1", n)
	}
}

// ── 3. LatestOutcomeForTarget 那条 SQL 自己的语义 ────────────────────────────

// plantOutcomeRow 用 canonicaldb 直接种一条 outcome＋回链，绕开领域层：
// 这一节的靶是**读面 SQL**（排序键、归属谓词、join 条件），不是判定逻辑。
func plantOutcomeRow(t *testing.T, pool *pgxpool.Pool, userID, modelID, targetID, id string,
	outcomeType, result string, createdAt time.Time) {
	t.Helper()
	ctx := context.Background()
	q := canonicaldb.New(pool)
	if err := q.InsertOutcome(ctx, canonicaldb.InsertOutcomeParams{
		ID: id, UserID: userID, OutcomeType: outcomeType, Status: "recorded",
		Result: result, Origin: "user_statement", Description: txt("planted"),
		OccurredAt: ts(createdAt), ModelVersion: i8(9), GenerationRunID: txt(newID()),
	}); err != nil {
		t.Fatalf("InsertOutcome(%s): %v", id, err)
	}
	if _, err := pool.Exec(ctx, `UPDATE outcomes SET created_at = $2 WHERE id = $1`,
		id, createdAt); err != nil {
		t.Fatalf("拨 created_at(%s): %v", id, err)
	}
	if _, err := pool.Exec(ctx,
		`INSERT INTO outcome_links (outcome_id, user_id, target_type, target_id)
		 VALUES ($1, $2, 'commitment', $3)`, id, userID, targetID); err != nil {
		t.Fatalf("种 outcome_link(%s): %v", id, err)
	}
	_ = modelID
}

func latestOutcome(t *testing.T, pool *pgxpool.Pool, userID string, targetID string,
	typ domain.OutcomeType) (domain.LatestOutcome, error) {
	t.Helper()
	var got domain.LatestOutcome
	err := NewCanonical(pool).InTx(context.Background(),
		func(ctx context.Context, st domain.Store) error {
			var err error
			got, err = st.LatestOutcomeForTarget(ctx, userID, domain.RefCommitment,
				targetID, typ)
			return err
		})
	return got, err
}

func TestT403LatestOutcomeForTargetPicksMostRecentSameType(t *testing.T) {
	pool := poolForTest(t)
	userID := newID()
	otherTarget := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })

	_, now := plantModelOnly(t, canonicaldb.New(pool), userID)
	old := now.AddDate(0, 0, -3).Truncate(time.Microsecond)
	recent := now.AddDate(0, 0, -1).Truncate(time.Microsecond)

	plantOutcomeRow(t, pool, userID, "", c0(old), "o-old", "commitment_completed", "worse", old)
	plantOutcomeRow(t, pool, userID, "", c0(recent), "o-new", "commitment_completed", "better", recent)
	// 同一条行动上的**另一种** outcome_type，且时刻**更晚**：这一格挡的是「查询漏了
	// outcome_type 谓词」——漏了就会把 behavior_change 当成今天的重复提交，把该写的一行吞掉。
	// （2026-10-19 变异自证的勘误：这一行原先种在 `c0(now)` 那个**别的对象**上，
	// 于是 `target_id` 谓词替 `outcome_type` 谓词挡了一切——把后者摘掉照样全绿。
	// 证人必须站在被证的那一格里，不能站在邻居格里。）
	plantOutcomeRow(t, pool, userID, "", c0(recent), "o-other-type", "behavior_change",
		"same", now)

	got, err := latestOutcome(t, pool, userID, c0(recent), "commitment_completed")
	if err != nil {
		t.Fatalf("LatestOutcomeForTarget: %v", err)
	}
	if !got.Found || got.ID != "o-new" || got.Result != "better" ||
		got.Status != "recorded" || got.OutcomeType != "commitment_completed" {
		t.Fatalf("最近一条 = %+v，期望 (found,o-new,better,recorded,commitment_completed) ⇒ "+
			"排序或 outcome_type 谓词不对", got)
	}
	if got.ModelVersion != 9 {
		t.Errorf("model_version=%d，期望列上的 9（回执要拿它顶出「首次结果」的版本）",
			got.ModelVersion)
	}
	if got.CreatedAt.UTC().Format("2006-01-02") != recent.UTC().Format("2006-01-02") {
		t.Errorf("created_at=%v，期望 %v ⇒ 驱动同日判定的正是这一格，读到别的日期就全错了",
			got.CreatedAt, recent)
	}
	// 别的对象（同一用户、同一型）查不到：归属谓词之外的第二道是 target_id。
	if g, err := latestOutcome(t, pool, userID, otherTarget, "commitment_completed"); err != nil ||
		g.Found {
		t.Errorf("未种过的行动查回 %+v/%v，期望 Found=false", g, err)
	}
}

// c0 把 time 变成「以它为种子」的稳定主键后缀（种行要能按日期认出顺序）。
func c0(at time.Time) string { return at.Format("20060102150405") }

func TestT403LatestOutcomeForTargetTieBreaksOnID(t *testing.T) {
	// 同一时刻落两行时 `id DESC` 必须给出全序：重放/批量补记会造出这种形状，
	// 而 LIMIT 1 在没有次级键时是「库里随便一行」，同日幂等就会时灵时不灵。
	pool := poolForTest(t)
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })

	_, now := plantModelOnly(t, canonicaldb.New(pool), userID)
	at := now.Truncate(time.Microsecond)
	plantOutcomeRow(t, pool, userID, "", "a-early", "o-1", "commitment_completed", "worse", at)
	plantOutcomeRow(t, pool, userID, "", "a-late", "o-2", "commitment_completed", "better", at)
	// 同一 target 上同刻第二行：期望 id 大的那条。
	plantOutcomeRow(t, pool, userID, "", "a-early", "o-3", "commitment_completed", "same", at)
	got, err := latestOutcome(t, pool, userID, "a-early", "commitment_completed")
	if err != nil {
		t.Fatalf("LatestOutcomeForTarget(a-early): %v", err)
	}
	if got.ID != "o-3" {
		t.Errorf("同刻两行查回 %q，期望 id 大的 o-3 ⇒ 排序漏了次级键，重放时结果不稳定",
			got.ID)
	}
	// 另一条行动只有 o-2：证明上面那次命中不是「忘了 target 谓词」蒙对的。
	other, err := latestOutcome(t, pool, userID, "a-late", "commitment_completed")
	if err != nil {
		t.Fatalf("LatestOutcomeForTarget(a-late): %v", err)
	}
	if other.ID != "o-2" {
		t.Errorf("a-late 查回 %q，期望 o-2 ⇒ target_id 谓词漏了会串行动", other.ID)
	}
}

func TestT403LatestOutcomeForTargetIsUserScoped(t *testing.T) {
	// 归属谓词那一格少半格，别人那条行动的同型结果会进我的判定，
	// 同日幂等就**吞掉**我这一行——用户看到的是「我记了，但屏幕上什么也没变」。
	pool := poolForTest(t)
	victim := newID()
	attacker := newID()
	t.Cleanup(func() {
		cleanupCanonical(t, pool, victim)
		cleanupCanonical(t, pool, attacker)
	})
	q := canonicaldb.New(pool)
	_, victimNow := plantModelOnly(t, q, victim)
	plantModelOnly(t, q, attacker)

	targetID := newID()
	if err := q.InsertCommitment(context.Background(), canonicaldb.InsertCommitmentParams{
		ID: targetID, UserID: victim, PersonalModelID: newID(),
		Title: "victim action", Description: nullText(), Status: "active",
		DueAt: ts(victimNow.AddDate(0, 0, 1)), GoalID: nullText(),
	}); err != nil {
		t.Fatalf("InsertCommitment: %v", err)
	}
	plantOutcomeRow(t, pool, victim, "", targetID, "o-victim", "commitment_completed",
		"better", victimNow)

	if g, err := latestOutcome(t, pool, attacker, targetID, "commitment_completed"); err != nil ||
		g.Found {
		t.Errorf("跨用户查回 Found=%v/%v，期望 false+ErrNoRows ⇒ SQL 的 user_id 谓词漏了",
			g.Found, err)
	}
	// 反向证人：归属方本人查得到（否则上一条是在「谁都查不到」的世界里绿的）。
	g, err := latestOutcome(t, pool, victim, targetID, "commitment_completed")
	if err != nil || !g.Found || g.ID != "o-victim" {
		t.Errorf("本人查回 %+v/%v，期望 (o-victim, found)", g, err)
	}
}

// ── 4. 越权一行都不写（§验收：404，且不许留下半笔）────────────────────────────

func TestT403ForeignActionWritesNoRowsAtAll(t *testing.T) {
	pool := poolForTest(t)
	ctx := context.Background()
	victim := newID()
	attacker := newID()
	t.Cleanup(func() {
		cleanupCanonical(t, pool, victim)
		cleanupCanonical(t, pool, attacker)
	})
	c := plantChain(t, canonicaldb.New(pool), victim)
	plantModelOnly(t, canonicaldb.New(pool), attacker)

	svc := serviceFor(pool)
	_, err := svc.RecordActionOutcome(ctx, actionOutcomeIs(attacker, c.commitmentID,
		domain.OutcomeCommitmentCompleted))
	if !errors.Is(err, domain.ErrInvalidOwnership) {
		t.Fatalf("越权 err = %v，期望 ErrInvalidOwnership（HTTP 面按它折 404）", err)
	}
	if n := outcomeRowCount(t, pool, attacker); n != 0 {
		t.Errorf("越权后 attacker 名下 outcomes %d 行，期望 0", n)
	}
	// 关键一格：回链也不许留。outcomes 那一行因为外键会当场失败，
	// 但 link 先写、outcome 后写的话，库里会多一颗指向不存在结果的孤儿链。
	if n := countWhere(t, pool,
		`SELECT count(*) FROM outcome_links WHERE user_id = $1`, attacker); n != 0 {
		t.Errorf("越权后 outcome_links %d 条，期望 0 ⇒ 同事务回滚没兜住第二张表", n)
	}
	// 别人的行动不许被顺手改成 completed（verifyTargets 在写之前就挡）。
	if s, at := readCommitment(t, pool, c.commitmentID); s != "active" || at != "" {
		t.Errorf("victim 的行动被越权请求改成 (%s, %q)，期望 (active, '')", s, at)
	}
}
