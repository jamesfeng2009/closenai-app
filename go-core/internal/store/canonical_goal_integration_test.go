// T4-02 的真库测：`personal_goals` / `personal_commitments` 的写读闭环（工单 §验收
// 「`personal_goals`/`personal_commitments` 各有真行」）。
//
// 为什么这几格只有真库能答（单测与桩都给不出这条证据）：
//
//  1. **NULL 与空串的分界只在库里存在**。桩把 `Description: ""` 与 `GoalID: ""` 原样
//     记在结构体上，两边永远「一致」；而 `textOf` 把空串折成 NULL、`row.GoalID.String`
//     把 NULL 折回空串——这两个方向只要错一个，「没写说明」会存成一行空白，
//     「没挂在目标下」会变成挂在某个幽灵目标下。断言必须打到列上（`IS NULL`），
//     不能只看读面对称地错。
//  2. **`completed_at` 是 SQL 写的，不是 Go 写的**。取消那两句刻意不带 `completed_at`，
//     而 04 §22 的完成度量只看这一列：如果 cancel 误刷了它，Go 侧读面、domain 判定、
//     桩记账全都「正常」，只有库里的分子多了一格。
//  3. **`updated_at` 与截断行为靠库时钟与 `LIMIT`**。`created_at/updated_at` 是列默认值，
//     桩里没有时间列；`LIMIT` 少了谓词只会多返回行，单测看不见。
//  4. **归属谓词只能证在跨用户那次查询上**：`WHERE user_id = $1` 少带半格，
//     别人的目标会进我的列表，而行的形状一模一样。
//
// 未设 TEST_DATABASE_URL 时跳过，与其余 store 集成测同口径（缺 env 是「未跑」不是绿）。
package store

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store/canonicaldb"
)

// plantModelOnly 只登记模型行。plantChain 会连一颗目标和一条行动一起种下，而那两张表
// 在链里各占一行——这一批的每一格都要「行数恰好等于我写的行数」：计数被种下的行掺水之后，
// 「过滤器被放宽成全集」与「库里本来就多一行」分不出来。
func plantModelOnly(t *testing.T, q *canonicaldb.Queries, userID string) (string, time.Time) {
	t.Helper()
	ctx := context.Background()
	now := time.Now().UTC().Truncate(time.Microsecond)
	modelID := newID()
	if err := q.InsertPersonalModel(ctx, canonicaldb.InsertPersonalModelParams{
		ID: modelID, UserID: userID}); err != nil {
		t.Fatalf("InsertPersonalModel: %v", err)
	}
	return modelID, now
}

// writeGoal 走**仓储方法**而不是 canonicaldb：这一面的靶是 `InsertGoal` 的折叠逻辑，
// 绕过它等于测了 sqlc 生成物。
func writeGoal(t *testing.T, pool *pgxpool.Pool, row domain.GoalRow) {
	t.Helper()
	ctx := context.Background()
	err := NewCanonical(pool).InTx(ctx, func(ctx context.Context, st domain.Store) error {
		return st.InsertGoal(ctx, row)
	})
	if err != nil {
		t.Fatalf("InsertGoal(%s): %v", row.ID, err)
	}
}

func writeCommitment(t *testing.T, pool *pgxpool.Pool, row domain.CommitmentRow) {
	t.Helper()
	ctx := context.Background()
	err := NewCanonical(pool).InTx(ctx, func(ctx context.Context, st domain.Store) error {
		return st.InsertCommitment(ctx, row)
	})
	if err != nil {
		t.Fatalf("InsertCommitment(%s): %v", row.ID, err)
	}
}

func cancelGoal(t *testing.T, pool *pgxpool.Pool, userID, goalID string) error {
	t.Helper()
	return NewCanonical(pool).InTx(context.Background(),
		func(ctx context.Context, st domain.Store) error {
			return st.CancelGoal(ctx, userID, goalID)
		})
}

func cancelCommitment(t *testing.T, pool *pgxpool.Pool, userID, commitmentID string) error {
	t.Helper()
	return NewCanonical(pool).InTx(context.Background(),
		func(ctx context.Context, st domain.Store) error {
			return st.CancelCommitment(ctx, userID, commitmentID)
		})
}

// nullColumn 直接问库那一格是不是 NULL——读面把 NULL 折成零值，
// 从读面反推「列上是 NULL」是同源断言（两侧同一套代码，恒真）。
func nullColumn(t *testing.T, pool *pgxpool.Pool, table, column, id string) bool {
	t.Helper()
	var isNull bool
	query := `SELECT ` + column + ` IS NULL FROM ` + table + ` WHERE id = $1`
	if err := pool.QueryRow(context.Background(), query, id).Scan(&isNull); err != nil {
		t.Fatalf("查询 %s.%s 是否为 NULL: %v", table, column, err)
	}
	return isNull
}

func TestGoalWriteReadRoundTripKeepsNullAndTargetAt(t *testing.T) {
	pool := poolForTest(t)
	ctx := context.Background()
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })

	modelID, now := plantModelOnly(t, canonicaldb.New(pool), userID)

	plainID, fullID := newID(), newID()
	target := now.AddDate(0, 0, 30).Truncate(time.Microsecond)
	writeGoal(t, pool, domain.GoalRow{
		ID: plainID, UserID: userID, PersonalModelID: modelID,
		Title: "finish the retirement plan", Status: domain.StatusActive,
		StartedAt: now,
	})
	writeGoal(t, pool, domain.GoalRow{
		ID: fullID, UserID: userID, PersonalModelID: modelID,
		Title: "run a half marathon", Description: "three sessions a week",
		Status: domain.StatusActive, StartedAt: now,
		TargetAt: target, HasTargetAt: true,
	})

	// 列上：没写说明＝NULL，不是空串；没给期限＝NULL。
	if !nullColumn(t, pool, "personal_goals", "description", plainID) {
		t.Errorf("description 存成了非 NULL ⇒「没写说明」被存成一行空白，前端会渲染一个空段落")
	}
	if !nullColumn(t, pool, "personal_goals", "target_at", plainID) {
		t.Errorf("target_at 存成了非 NULL ⇒ 没有期限的目标被安上了一个期限")
	}
	if !nullColumn(t, pool, "personal_goals", "completed_at", fullID) {
		t.Errorf("新建的目标 completed_at 非 NULL ⇒「刚立起来」被读成「已经达成」")
	}

	rows, err := NewCanonical(pool).ListGoalRecords(ctx, userID, "", 50)
	if err != nil {
		t.Fatalf("ListGoalRecords: %v", err)
	}
	byID := map[string]domain.GoalRecord{}
	for _, r := range rows {
		byID[r.ID] = r
	}
	if len(rows) != 2 {
		t.Fatalf("目标行数 = %d（%+v），期望恰好两条", len(rows), rows)
	}
	bare, full := byID[plainID], byID[fullID]
	if bare.Description != "" {
		t.Errorf("无说明的目标读回 description = %q，期望空串（NULL 的读面形状）", bare.Description)
	}
	if !bare.TargetAt.IsZero() {
		t.Errorf("无期限的目标读回 target_at = %v，期望零值", bare.TargetAt)
	}
	if full.Description != "three sessions a week" {
		t.Errorf("description = %q，原文没回来", full.Description)
	}
	if !full.TargetAt.Equal(target) {
		t.Errorf("target_at = %v，期望 %v ⇒ 期限在带时区的往返里被改写", full.TargetAt, target)
	}
	// 两列默认值必须由库给出：created_at/updated_at 为零 ⇒ 读面排序键（ORDER BY created_at）失效。
	if bare.CreatedAt.IsZero() || bare.UpdatedAt.IsZero() {
		t.Errorf("时间列未落库：created_at=%v updated_at=%v", bare.CreatedAt, bare.UpdatedAt)
	}
}

func TestActionGoalLinkAndUnlinkedAreBothReadable(t *testing.T) {
	pool := poolForTest(t)
	ctx := context.Background()
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })

	modelID, now := plantModelOnly(t, canonicaldb.New(pool), userID)
	goalID := newID()
	writeGoal(t, pool, domain.GoalRow{
		ID: goalID, UserID: userID, PersonalModelID: modelID,
		Title: "run a half marathon", Status: domain.StatusActive, StartedAt: now,
	})

	unlinkedID, linkedID := newID(), newID()
	// GoalID 空串这一格是 T4-02 的「行动不属于任何目标」，必须在列上落成 NULL。
	writeCommitment(t, pool, domain.CommitmentRow{
		ID: unlinkedID, UserID: userID, PersonalModelID: modelID,
		Title: "drink water at 15:00", Status: domain.StatusActive,
		DueAt: now.Add(3 * time.Hour), HasDueAt: true,
	})
	writeCommitment(t, pool, domain.CommitmentRow{
		ID: linkedID, UserID: userID, PersonalModelID: modelID,
		Title: "train Tuesday", Description: "easy 5k", Status: domain.StatusActive,
		GoalID: goalID,
	})

	if !nullColumn(t, pool, "personal_commitments", "goal_id", unlinkedID) {
		t.Errorf("goal_id 存成了非 NULL ⇒「不属于任何目标」的行动聚成了一个幽灵目标分组")
	}
	if nullColumn(t, pool, "personal_commitments", "goal_id", linkedID) {
		t.Fatalf("goal_id 落成了 NULL ⇒ 行动与目标的连接在写路径上就丢了")
	}
	if !nullColumn(t, pool, "personal_commitments", "skipped_at", linkedID) {
		t.Errorf("新行动 skipped_at 非 NULL ⇒ expand 形的新列在既有写入点上变成脏值")
	}

	rows, err := NewCanonical(pool).ListActionRecords(ctx, userID, "", 50)
	if err != nil {
		t.Fatalf("ListActionRecords: %v", err)
	}
	if len(rows) != 2 {
		t.Fatalf("行动行数 = %d（%+v），期望恰好两条", len(rows), rows)
	}
	var linked, bare domain.ActionRecord
	for _, r := range rows {
		switch r.ID {
		case linkedID:
			linked = r
		case unlinkedID:
			bare = r
		}
	}
	if linked.GoalID != goalID {
		t.Errorf("goal_id 读回 %q，期望 %q", linked.GoalID, goalID)
	}
	// 反向：NULL ⇒ 空串。前端按「空串＝不分组」渲染，这里若回来一个随机 id 就多一个分组。
	if bare.GoalID != "" {
		t.Errorf("未挂目标的行动读回 goal_id = %q，期望空串", bare.GoalID)
	}
	if bare.DueAt.IsZero() {
		t.Errorf("due_at 读回零值 ⇒ 有期限的行动丢了期限")
	}
	if !linked.DueAt.IsZero() {
		t.Errorf("无期限的行动读回 due_at = %v", linked.DueAt)
	}
}

func TestCancelGoalNeverWritesCompletedAt(t *testing.T) {
	pool := poolForTest(t)
	ctx := context.Background()
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })

	modelID, now := plantModelOnly(t, canonicaldb.New(pool), userID)
	goalID := newID()
	writeGoal(t, pool, domain.GoalRow{
		ID: goalID, UserID: userID, PersonalModelID: modelID,
		Title: "learn to swim", Status: domain.StatusActive, StartedAt: now,
	})

	before, err := NewCanonical(pool).ListGoalRecords(ctx, userID, domain.StatusActive, 50)
	if err != nil {
		t.Fatalf("ListGoalRecords(active): %v", err)
	}
	if len(before) != 1 {
		t.Fatalf("active 目标行数 = %d，期望恰好一条", len(before))
	}

	if err := cancelGoal(t, pool, userID, goalID); err != nil {
		t.Fatalf("CancelGoal: %v", err)
	}

	// completed_at 的判据打在**列**上：这一格是 04 §22 完成度量唯一的分子来源，
	// 而 domain 与桩都不经手这一列。
	if !nullColumn(t, pool, "personal_goals", "completed_at", goalID) {
		t.Errorf("取消之后 completed_at 非 NULL ⇒「不想做了」被计入「达成了」")
	}
	rows, err := NewCanonical(pool).ListGoalRecords(ctx, userID, domain.StatusCancelled, 50)
	if err != nil {
		t.Fatalf("ListGoalRecords(cancelled): %v", err)
	}
	if len(rows) != 1 || rows[0].ID != goalID {
		t.Fatalf("cancelled 行集 = %+v，期望恰好刚取消的那颗", rows)
	}
	if !rows[0].CompletedAt.IsZero() {
		t.Errorf("读面把 completed_at 读成 %v ⇒ 列上确实有值，上面那条 NULL 判据兜不住",
			rows[0].CompletedAt)
	}
	// 这一张表没有 cancelled_at（N2 建表时没有这一格），取消留下的唯一时间痕是 updated_at。
	if !rows[0].UpdatedAt.After(before[0].UpdatedAt) {
		t.Errorf("updated_at = %v，取消前 %v ⇒ 取消成了唯一不留时间痕的转移，归因只能靠猜",
			rows[0].UpdatedAt, before[0].UpdatedAt)
	}
	// active 那一格必须空掉：状态列真的被改写，而不是新增了一行。
	active, err := NewCanonical(pool).ListGoalRecords(ctx, userID, domain.StatusActive, 50)
	if err != nil {
		t.Fatalf("ListGoalRecords(active) after cancel: %v", err)
	}
	if len(active) != 0 {
		t.Errorf("取消后仍在 active 格里：%+v ⇒ 状态没改，只是又被读了一遍", active)
	}
}

func TestCancelCommitmentKeepsCompletedAndSkippedNull(t *testing.T) {
	pool := poolForTest(t)
	ctx := context.Background()
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })

	modelID, now := plantModelOnly(t, canonicaldb.New(pool), userID)
	goalID := newID()
	writeGoal(t, pool, domain.GoalRow{
		ID: goalID, UserID: userID, PersonalModelID: modelID,
		Title: "run a half marathon", Status: domain.StatusActive, StartedAt: now,
	})
	actionID := newID()
	writeCommitment(t, pool, domain.CommitmentRow{
		ID: actionID, UserID: userID, PersonalModelID: modelID,
		Title: "no meetings before 11", Status: domain.StatusActive,
		GoalID: goalID,
	})

	if err := cancelCommitment(t, pool, userID, actionID); err != nil {
		t.Fatalf("CancelCommitment: %v", err)
	}
	for _, column := range []string{"completed_at", "skipped_at"} {
		if !nullColumn(t, pool, "personal_commitments", column, actionID) {
			t.Errorf("取消之后 %s 非 NULL ⇒ 三种收尾共用了一格时间", column)
		}
	}
	rows, err := NewCanonical(pool).ListActionRecords(ctx, userID, domain.StatusCancelled, 50)
	if err != nil {
		t.Fatalf("ListActionRecords(cancelled): %v", err)
	}
	if len(rows) != 1 || rows[0].GoalID != goalID {
		t.Fatalf("cancelled 行动 = %+v，期望那一条且 goal_id 仍在 ⇒ 取消顺手清了归属", rows)
	}
}

func TestCancelRejectsForeignUserAndLeavesRowUntouched(t *testing.T) {
	pool := poolForTest(t)
	ctx := context.Background()
	owner, stranger := newID(), newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, owner) })
	t.Cleanup(func() { cleanupCanonical(t, pool, stranger) })

	modelID, now := plantModelOnly(t, canonicaldb.New(pool), owner)
	goalID := newID()
	writeGoal(t, pool, domain.GoalRow{
		ID: goalID, UserID: owner, PersonalModelID: modelID,
		Title: "keep the streak", Status: domain.StatusActive, StartedAt: now,
	})

	if err := cancelGoal(t, pool, stranger, goalID); !errors.Is(err, domain.ErrInvalidOwnership) {
		t.Fatalf("外人取消目标 = %v，期望 ErrInvalidOwnership ⇒ 0 行被读成了成功", err)
	}
	// 错误码兜不住「先写后判」：状态必须仍是 active。
	rows, err := NewCanonical(pool).ListGoalRecords(ctx, owner, "", 50)
	if err != nil {
		t.Fatalf("ListGoalRecords: %v", err)
	}
	if len(rows) != 1 || rows[0].Status != domain.StatusActive {
		t.Fatalf("归属校验之后 owner 的目标 = %+v ⇒ 别人的取消动到了我的行", rows)
	}
	// 陌生人名下必须一行都没有：`WHERE user_id = $1` 少半格就会在这里红。
	empty, err := NewCanonical(pool).ListGoalRecords(ctx, stranger, "", 50)
	if err != nil {
		t.Fatalf("ListGoalRecords(stranger): %v", err)
	}
	if len(empty) != 0 {
		t.Errorf("陌生人的目标列表 = %+v ⇒ 读面漏了归属谓词，别人的目标进了我的列表", empty)
	}
	emptyActions, err := NewCanonical(pool).ListActionRecords(ctx, stranger, "", 50)
	if err != nil {
		t.Fatalf("ListActionRecords(stranger): %v", err)
	}
	if len(emptyActions) != 0 {
		t.Errorf("陌生人的行动列表 = %+v ⇒ 行动读面漏了归属谓词", emptyActions)
	}
}

func TestGoalAndActionListsHonourStatusFilterAndLimit(t *testing.T) {
	pool := poolForTest(t)
	ctx := context.Background()
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })

	modelID, now := plantModelOnly(t, canonicaldb.New(pool), userID)

	statuses := []string{domain.StatusActive, domain.StatusCompleted,
		domain.StatusCancelled, domain.StatusSkipped}
	for i, status := range statuses {
		writeGoal(t, pool, domain.GoalRow{
			ID: newID(), UserID: userID, PersonalModelID: modelID,
			Title: "goal " + status, Status: status, StartedAt: now,
		})
		writeCommitment(t, pool, domain.CommitmentRow{
			ID: newID(), UserID: userID, PersonalModelID: modelID,
			Title: "action " + status, Status: status,
			DueAt: now.Add(time.Duration(i) * time.Hour), HasDueAt: true,
		})
	}

	// 空串过滤器＝「四态都要」：把它退化成 `status = $2` 会让刚完成的那颗直接从列表消失。
	all, err := NewCanonical(pool).ListGoalRecords(ctx, userID, "", 50)
	if err != nil {
		t.Fatalf("ListGoalRecords(\"\"): %v", err)
	}
	if len(all) != 4 {
		t.Fatalf("空过滤器返回 %d 行（%+v），期望四态各一条", len(all), statusesOf(all))
	}
	one, err := NewCanonical(pool).ListGoalRecords(ctx, userID, domain.StatusCompleted, 50)
	if err != nil {
		t.Fatalf("ListGoalRecords(completed): %v", err)
	}
	if len(one) != 1 || one[0].Status != domain.StatusCompleted {
		t.Errorf("completed 过滤器 = %+v，期望恰好一条", one)
	}
	// 库里没有的状态：原词进 SQL 必然 0 行（domain 不校验词表，判据在这一格）。
	none, err := NewCanonical(pool).ListGoalRecords(ctx, userID, "archived", 50)
	if err != nil {
		t.Fatalf("ListGoalRecords(archived): %v", err)
	}
	if len(none) != 0 {
		t.Errorf("不存在的状态返回 %d 行 ⇒ 过滤器被放宽成了全集，拼错的请求会把历史都拉出来",
			len(none))
	}
	actions, err := NewCanonical(pool).ListActionRecords(ctx, userID, "", 50)
	if err != nil {
		t.Fatalf("ListActionRecords(\"\"): %v", err)
	}
	if len(actions) != 4 {
		t.Fatalf("行动空过滤器返回 %d 行，期望四态各一条", len(actions))
	}
	// LIMIT 生效是 `Truncated` 唯一的地基：少了这句，domain 的 limit+1 证人恒为「没截断」。
	page, err := NewCanonical(pool).ListGoalRecords(ctx, userID, "", 2)
	if err != nil {
		t.Fatalf("ListGoalRecords(limit 2): %v", err)
	}
	if len(page) != 2 {
		t.Errorf("limit=2 返回 %d 行 ⇒ LIMIT 没落到 SQL，截断标志永远不会红", len(page))
	}
	// 仓储面不许替调用方猜 limit：0 与负数在 SQL 里是两个完全不同的形状。
	for _, bad := range []int{0, -3} {
		if _, err := NewCanonical(pool).ListGoalRecords(ctx, userID, "", bad); err == nil {
			t.Errorf("limit=%d 静默返回 ⇒「取 0 条」被当成了「取全部」", bad)
		}
		if _, err := NewCanonical(pool).ListActionRecords(ctx, userID, "", bad); err == nil {
			t.Errorf("行动侧 limit=%d 静默返回", bad)
		}
	}
}

func statusesOf(rows []domain.GoalRecord) []string {
	out := make([]string, 0, len(rows))
	for _, r := range rows {
		out = append(out, r.Status)
	}
	return out
}
