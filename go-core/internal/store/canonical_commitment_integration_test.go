// T4-01 的真库测：`personal_commitments` 的 goal_id 与 skipped_at 两列（PLAN §5 决策 6）。
//
// 为什么这批只有真库能答（sqlite 上跑同一段代码会给出错误的自信）：
//
//  1. `goal_id` 的「没有目标」是 **NULL**，不是空串。sqlite 侧那列只是兼容用的值，
//     分不清这两者；而 T4-02 的 `CreateAction` 判据正是「goal_id 为 NULL ⇒ 这条行动
//     不挂在任何目标下」——把 NULL 读成空串，未挂目标的行动会聚成一个幽灵目标。
//  2. 跳过的幂等写在 SQL 的 `COALESCE(skipped_at, now())` 里而不是 Go 里。库时钟两次
//     `now()` 必然差若干微秒，所以「第二次跳过之后时间戳与第一次逐位相同」只有真库能证。
//     这一格退化时，用户连点两下就把「什么时候跳过的」改写成「最后一次点击的时刻」。
//  3. 跨用户那次 skip 必须**一个字节都不改**：`WHERE id = $1 AND user_id = $2` 少带半个
//     谓词，别人的行动就变成我的可写对象，而返回的形状一模一样。
//
// 未设 TEST_DATABASE_URL 时跳过，与其余 store 集成测同口径（缺 env 是「未跑」不是绿）。
package store

import (
	"context"
	"errors"
	"testing"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store/canonicaldb"
)

// 关于测试里那句 `st.(*canonicalTx)`：`SkipCommitment` 这一批**没有**挂进
// `domain.Store` 接口——挂上去就等于「active → skipped 有门而无守卫」，而守卫
// （终态不许回退、重复同态幂等返回当前）是 T4-02 的 `TransitionAction`。
// 同包内断言具体类型，是为了让靶落在**仓储方法**上：摘掉 wrap、或把 ErrNoRows
// 分支改错，这里就会红，而不是等 T4-02 接线时才发现这一格从来没被跑过。

func TestCommitmentGoalIDRoundTripsAndNullMeansNoGoal(t *testing.T) {
	pool := poolForTest(t)
	q := canonicaldb.New(pool)
	ctx := context.Background()
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })

	c := plantChain(t, q, userID)

	// 同一份模型下再挂一条**有目标**的行动：两列同时有值与同时为 NULL 都要读得出来。
	hungID := newID()
	if err := q.InsertCommitment(ctx, canonicaldb.InsertCommitmentParams{
		ID: hungID, UserID: userID, PersonalModelID: c.modelID,
		Title: "block Wednesday 9-11 for deep work", Description: nullText(),
		Status: "active", DueAt: ts(c.now), GoalID: txt(c.goalID),
	}); err != nil {
		t.Fatalf("InsertCommitment(goal_id): %v", err)
	}

	rows, err := q.ListCommitments(ctx, canonicaldb.ListCommitmentsParams{
		UserID: userID, Status: "active", RowLimit: 50,
	})
	if err != nil {
		t.Fatalf("ListCommitments: %v", err)
	}
	byID := map[string]canonicaldb.ListCommitmentsRow{}
	for _, r := range rows {
		byID[r.ID] = r
	}
	hung, ok := byID[hungID]
	if !ok {
		t.Fatalf("挂上目标的行动没被读回来：%+v", rows)
	}
	if !hung.GoalID.Valid || hung.GoalID.String != c.goalID {
		t.Errorf("goal_id = %#v，期望 %q ⇒ 行动与目标的连接在读路径上丢了",
			hung.GoalID, c.goalID)
	}
	// 反面那半：种下的那条**没有**目标，读回来必须是 NULL（Valid=false）而不是 ""。
	// 断言写成 `hung.GoalID.String == ""` 会同时放过 NULL 与空串，那一格就白测了。
	planted, ok := byID[c.commitmentID]
	if !ok {
		t.Fatalf("原有行动读不回来：%+v", rows)
	}
	if planted.GoalID.Valid {
		t.Errorf("goal_id.Valid = true（%q）⇒ 没有目标的行动被读成了有目标",
			planted.GoalID.String)
	}
	if planted.SkippedAt.Valid {
		t.Errorf("skipped_at.Valid = true ⇒ 新列在既有行上默认成了非 NULL，expand 形变成 break 形")
	}
}

func TestSkipCommitmentIsIdempotentOnTheClock(t *testing.T) {
	pool := poolForTest(t)
	q := canonicaldb.New(pool)
	ctx := context.Background()
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })

	c := plantChain(t, q, userID)

	tx := func(id string) error {
		return NewCanonical(pool).InTx(ctx, func(ctx context.Context, st domain.Store) error {
			return st.(*canonicalTx).SkipCommitment(ctx, userID, id)
		})
	}
	if err := tx(c.commitmentID); err != nil {
		t.Fatalf("SkipCommitment: %v", err)
	}
	first := skippedRow(t, q, userID)
	if first.Status != "skipped" {
		t.Errorf("status = %q，期望 skipped ⇒「跳过」仍然只是一句前端文案", first.Status)
	}
	if !first.SkippedAt.Valid {
		t.Fatal("skipped_at 为 NULL：状态改了而时间没落，Baseline 要的「一等状态」少了一半")
	}
	if first.CompletedAt.Valid {
		t.Errorf("completed_at = %v ⇒ 跳过被写成完成（02 的旧退化），两个时间必须各归各",
			first.CompletedAt.Time)
	}

	// 第二次跳过：状态还是 skipped，而 skipped_at **必须逐位相同**。
	// 摘掉 SQL 里的 COALESCE（改成 `skipped_at = now()`）只会在这一格红——
	// 单看第一次跳过什么都对，因为第一次本来就该写时间。
	if err := tx(c.commitmentID); err != nil {
		t.Fatalf("第二次 SkipCommitment 应当是幂等成功而不是错误：%v", err)
	}
	second := skippedRow(t, q, userID)
	if !second.SkippedAt.Time.Equal(first.SkippedAt.Time) {
		t.Errorf("重复跳过把时间从 %v 刷成 %v ⇒ 审计事实被最后一次点击改写",
			first.SkippedAt.Time, second.SkippedAt.Time)
	}

	// 完成仍是另一条时间线：先跳过再完成，两个时间戳各自都在、互不覆盖。
	if _, err := q.CompleteCommitment(ctx, canonicaldb.CompleteCommitmentParams{
		ID: c.commitmentID, UserID: userID,
	}); err != nil {
		t.Fatalf("CompleteCommitment: %v", err)
	}
	rows, err := q.ListCommitments(ctx, canonicaldb.ListCommitmentsParams{
		UserID: userID, Status: "completed", RowLimit: 50,
	})
	if err != nil {
		t.Fatalf("ListCommitments(completed): %v", err)
	}
	if len(rows) != 1 || !rows[0].CompletedAt.Valid || !rows[0].SkippedAt.Valid {
		t.Fatalf("完成后的行 = %+v ⇒ completed_at 与 skipped_at 必须共存，谁也不许清掉谁", rows)
	}
	if rows[0].SkippedAt.Time.Equal(rows[0].CompletedAt.Time) {
		t.Errorf("两个时间戳相等（%v）⇒ 大概率是同一次 now() 被写了两遍",
			rows[0].SkippedAt.Time)
	}
}

func TestSkipCommitmentRejectsForeignUser(t *testing.T) {
	pool := poolForTest(t)
	q := canonicaldb.New(pool)
	ctx := context.Background()
	owner := newID()
	stranger := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, owner) })
	t.Cleanup(func() { cleanupCanonical(t, pool, stranger) })

	c := plantChain(t, q, owner)

	err := NewCanonical(pool).InTx(ctx, func(ctx context.Context, st domain.Store) error {
		return st.(*canonicalTx).SkipCommitment(ctx, stranger, c.commitmentID)
	})
	if !errors.Is(err, domain.ErrInvalidOwnership) {
		t.Fatalf("跨用户跳过 = %v，期望 ErrInvalidOwnership ⇒ 0 行被读成了成功", err)
	}

	// 被拒的那一次必须**什么都没改**：只看错误码的闸兜不住「先写后判」这种坏法。
	rows, err := q.ListCommitments(ctx, canonicaldb.ListCommitmentsParams{
		UserID: owner, Status: "active", RowLimit: 50,
	})
	if err != nil {
		t.Fatalf("ListCommitments: %v", err)
	}
	if len(rows) != 1 || rows[0].ID != c.commitmentID {
		t.Fatalf("归属校验之后 active 行集 = %+v ⇒ 别人的跳过动到了我的行", rows)
	}
	if rows[0].SkippedAt.Valid {
		t.Errorf("skipped_at 被外人写成了 %v", rows[0].SkippedAt.Time)
	}
}

// skippedRow 按状态读回那颗承诺：跳过之后它只存在于 `status='skipped'` 这一格里，
// 所以「读不回来」与「没跳成功」在这里是同一种红，这是想要的形状而不是含糊。
func skippedRow(t *testing.T, q *canonicaldb.Queries, userID string) canonicaldb.ListCommitmentsRow {
	t.Helper()
	rows, err := q.ListCommitments(context.Background(), canonicaldb.ListCommitmentsParams{
		UserID: userID, Status: "skipped", RowLimit: 50,
	})
	if err != nil {
		t.Fatalf("ListCommitments(skipped): %v", err)
	}
	if len(rows) != 1 {
		t.Fatalf("skipped 行集 = %+v，期望恰好一条", rows)
	}
	return rows[0]
}
