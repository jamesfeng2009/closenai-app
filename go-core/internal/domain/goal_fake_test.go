package domain

// T4-02 的 fake 桩：与 N6 同一纪律——只复刻**数据库的守卫**，不复刻业务判断。
//
// 复刻的三格：
//   - `personal_model_id NOT NULL` / `user_id NOT NULL`：桩在这里报错，
//     于是「Service 忘了先 EnsurePersonalModel」会在单测里红，而不是等真库集成测；
//   - 「按 (id, user_id) 找不到 ⇒ 0 行 ⇒ ErrInvalidOwnership」：refStates 里没有
//     这一颗就是查不到（跨用户与不存在同形，与真 SQL 一致）；
//   - 转移合法性**不**在桩里：真 SQL 没有 `AND status='active'`（canonical.sql 的
//     SkipCommitment 注释就是这条口径），桩里加了它，domain 漏判就会假绿。

import (
	"context"
	"errors"
	"fmt"
)

func (f *fakeStore) InsertGoal(ctx context.Context, row GoalRow) error {
	if f.insertErr != nil {
		return f.insertErr
	}
	if row.PersonalModelID == "" || row.UserID == "" {
		return errors.New("fake: personal_model_id / user_id 是 NOT NULL 列")
	}
	f.goals = append(f.goals, row)
	return nil
}

func (f *fakeStore) InsertCommitment(ctx context.Context, row CommitmentRow) error {
	if f.insertErr != nil {
		return f.insertErr
	}
	if row.PersonalModelID == "" || row.UserID == "" {
		return errors.New("fake: personal_model_id / user_id 是 NOT NULL 列")
	}
	f.commitments = append(f.commitments, row)
	return nil
}

// skipWrite 是 Skip/Cancel 三格共用的记账：形状 `<verb>:<id>`，
// 用例断言的是「哪一枚动词打到了哪一颗」，不带状态——状态判据在 domain 的测里。
func (f *fakeStore) skipWrite(verb, id string) error {
	if f.insertErr != nil {
		return f.insertErr
	}
	switch verb {
	case "skip":
		f.commitmentSkips = append(f.commitmentSkips, id)
	case "cancel-commitment":
		f.commitmentCancels = append(f.commitmentCancels, id)
	case "cancel-goal":
		f.goalCancels = append(f.goalCancels, id)
	default:
		return fmt.Errorf("fake: 未知的写痕 %q", verb)
	}
	return nil
}

func (f *fakeStore) SkipCommitment(ctx context.Context, userID, commitmentID string) error {
	return f.skipWrite("skip", commitmentID)
}

func (f *fakeStore) CancelCommitment(ctx context.Context, userID, commitmentID string) error {
	return f.skipWrite("cancel-commitment", commitmentID)
}

func (f *fakeStore) CancelGoal(ctx context.Context, userID, goalID string) error {
	return f.skipWrite("cancel-goal", goalID)
}
