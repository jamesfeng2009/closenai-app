package domain

import (
	"context"
	"errors"
	"fmt"
)

// N6 的 fake 桩：与 N4/N5 同口径，只搬类型不做决策——桩里复刻的是**数据库的守卫**
// （0 行/查不到 ⇒ 同一批错误），不是业务判断。哪些 outcome 该传播、该不该 ++，
// 由被测的 domain 代码决定，桩只记账供断言。

func (f *fakeStore) InsertOutcome(ctx context.Context, row OutcomeRow) error {
	if f.insertErr != nil {
		return f.insertErr
	}
	f.outcomes = append(f.outcomes, row)
	return nil
}

func (f *fakeStore) LinkOutcomeTargets(ctx context.Context, outcomeID, userID string,
	targets []TargetRef) error {
	if f.linkErr != nil {
		return f.linkErr
	}
	for _, t := range targets {
		f.outcomeLinks = append(f.outcomeLinks,
			fmt.Sprintf("%s:%s:%s:%s", outcomeID, userID, t.Type, t.ID))
	}
	return nil
}

// MarkOutcomeStatus 复刻 SQL 守卫：只放行 recorded/candidate 起算，其余 0 行。
func (f *fakeStore) MarkOutcomeStatus(ctx context.Context, userID, outcomeID string,
	status OutcomeStatus) error {
	for i, row := range f.outcomes {
		if row.ID != outcomeID || row.UserID != userID {
			continue
		}
		if row.Status != string(OutcomeStatusRecorded) &&
			row.Status != string(OutcomeStatusCandidate) {
			return fmt.Errorf("%w: %s", ErrOutcomeNotFound, outcomeID)
		}
		f.outcomes[i].Status = string(status)
		f.outcomeStatuses = append(f.outcomeStatuses, outcomeID+":"+string(status))
		return nil
	}
	return fmt.Errorf("%w: %s", ErrOutcomeNotFound, outcomeID)
}

func (f *fakeStore) InsertFeedback(ctx context.Context, row FeedbackRow) error {
	if f.insertErr != nil {
		return f.insertErr
	}
	f.feedbacks = append(f.feedbacks, row)
	return nil
}

// LatestFeedbackForTarget 优先用显式桩（用例要构造「历史上最后一条是同型」），
// 没设桩时回落到本会话已写入的 feedbacks 尾部。
func (f *fakeStore) LatestFeedbackForTarget(ctx context.Context, userID string,
	target RefType, targetID string) (LatestFeedback, error) {
	if f.latestFB != nil {
		return *f.latestFB, nil
	}
	key := string(target) + ":" + targetID
	for i := len(f.feedbacks) - 1; i >= 0; i-- {
		row := f.feedbacks[i]
		if row.UserID == userID && row.TargetType+":"+row.TargetID == key {
			return LatestFeedback{ID: row.ID, FeedbackType: row.FeedbackType, Found: true}, nil
		}
	}
	return LatestFeedback{}, nil
}

func (f *fakeStore) GetPatternState(ctx context.Context,
	userID, patternID string) (PatternState, error) {
	if f.readErr != nil {
		return PatternState{}, f.readErr
	}
	if f.patternSt == nil || f.patternSt.ID != patternID {
		return PatternState{}, fmt.Errorf("%w: %s", ErrPatternNotFound, patternID)
	}
	return *f.patternSt, nil
}

func (f *fakeStore) UpdatePatternLifecycle(ctx context.Context,
	row PatternLifecycleRow) error {
	if f.insertErr != nil {
		return f.insertErr
	}
	f.lifecycleWrites = append(f.lifecycleWrites, row)
	if f.patternSt != nil && f.patternSt.ID == row.ID {
		f.patternSt.Status = PatternStatus(row.Status)
		if row.HasConfidence {
			f.patternSt.Confidence = row.Confidence
		}
		f.patternSt.ModelVersion = row.ModelVersion
	}
	return nil
}

func (f *fakeStore) UpdateFactStatus(ctx context.Context, row FactStatusRow) error {
	if f.insertErr != nil {
		return f.insertErr
	}
	f.factWrites = append(f.factWrites, row)
	return nil
}

func (f *fakeStore) CompleteGoal(ctx context.Context, userID, goalID string) error {
	f.goalDone = append(f.goalDone, goalID)
	return nil
}

func (f *fakeStore) CompleteCommitment(ctx context.Context, userID, commitmentID string) error {
	f.commitmentDone = append(f.commitmentDone, commitmentID)
	return nil
}

func (f *fakeStore) ListInsightsForPattern(ctx context.Context, userID,
	patternID string) ([]PatternInsightRef, error) {
	if f.readErr != nil {
		return nil, f.readErr
	}
	return f.patternInsights, nil
}

func (f *fakeStore) ListQueuedProactives(ctx context.Context,
	userID string) ([]QueuedProactive, error) {
	if f.readErr != nil {
		return nil, f.readErr
	}
	return f.queued, nil
}

func (f *fakeStore) ListDerivedSupportForEvidence(ctx context.Context, userID,
	evidenceID string) ([]DerivedSupport, error) {
	if f.readErr != nil {
		return nil, f.readErr
	}
	// T2-06：让指定那一条 evidence 的传播失败，是「翻转已提交、传播中途断」那格的靶。
	if f.failPropagateFor != "" && f.failPropagateFor == evidenceID {
		return nil, errors.New("fake: 传播中途失败")
	}
	if rows, ok := f.derivedBy[evidenceID]; ok {
		return rows, nil
	}
	return f.derived, nil
}
