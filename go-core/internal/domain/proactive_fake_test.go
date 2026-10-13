// fakeStore 的 N5 面：主动触达的读桩与写桩。
//
// 与 service_test.go 里的 N3/N4 桩同一立场：**不做任何判定**，只按预置数据回答，
// 把写进来的行原样留下供断言。判定逻辑的验证在 proactive_gate_test.go。
package domain

import (
	"context"
	"errors"
	"fmt"
	"time"
)

func (f *fakeStore) GetProactiveUserPrefs(ctx context.Context,
	userID string) (UserPrefs, error) {
	if f.proacErr != nil {
		return UserPrefs{}, f.proacErr
	}
	prefs := f.prefs
	prefs.UserID = userID
	if !prefs.Found {
		return UserPrefs{UserID: userID}, nil
	}
	return prefs, nil
}

func (f *fakeStore) ListRefStates(ctx context.Context, userID string,
	ids []string) ([]RefState, error) {
	if f.proacErr != nil {
		return nil, f.proacErr
	}
	wanted := map[string]bool{}
	for _, id := range ids {
		wanted[id] = true
	}
	out := []RefState{}
	for _, ref := range f.refStates {
		if wanted[ref.ID] {
			out = append(out, ref)
		}
	}
	return out, nil
}

func (f *fakeStore) ListProactiveHistory(ctx context.Context, userID string,
	since time.Time) ([]ProactiveSendRow, error) {
	if f.proacErr != nil {
		return nil, f.proacErr
	}
	out := []ProactiveSendRow{}
	for _, row := range f.history {
		if row.HasSentAt && row.SentAt.Before(since) {
			continue
		}
		if !row.HasSentAt && row.HasScheduledAt && row.ScheduledAt.Before(since) {
			continue
		}
		out = append(out, row)
	}
	return out, nil
}

func (f *fakeStore) LastUserActivityAt(ctx context.Context,
	userID string) (time.Time, bool, error) {
	if f.proacErr != nil {
		return time.Time{}, false, f.proacErr
	}
	return f.lastActivity, f.hasActivity, nil
}

func (f *fakeStore) RecentCorrections(ctx context.Context, userID string,
	withinDays int) ([]string, error) {
	if f.proacErr != nil {
		return nil, f.proacErr
	}
	return f.corrected, nil
}

func (f *fakeStore) InsertProactive(ctx context.Context, row ProactiveRow) error {
	if f.proacErr != nil {
		return f.proacErr
	}
	f.proactives = append(f.proactives, row)
	return nil
}

func (f *fakeStore) GetProactiveState(ctx context.Context, userID,
	proactiveID string) (ProactiveState, error) {
	if f.proacState == nil {
		return ProactiveState{}, fmt.Errorf("%w: %s", ErrProactiveNotFound, proactiveID)
	}
	if f.proacState.UserID != userID || f.proacState.ID != proactiveID {
		return ProactiveState{}, fmt.Errorf("%w: %s", ErrProactiveNotFound, proactiveID)
	}
	state := *f.proacState
	if state.UserID == "" {
		state.UserID = userID
	}
	return state, nil
}

func (f *fakeStore) SetProactiveStatus(ctx context.Context, row ProactiveStatusRow) error {
	if f.proacErr != nil {
		return f.proacErr
	}
	f.statusWrites = append(f.statusWrites, row)
	return nil
}

func (f *fakeStore) MarkProactiveSent(ctx context.Context, userID, proactiveID,
	detail string) (time.Time, error) {
	if f.proacErr != nil {
		return time.Time{}, f.proacErr
	}
	f.sentMarks = append(f.sentMarks, proactiveID)
	return windowEnd, nil
}

func (f *fakeStore) ExpireProactive(ctx context.Context, userID, proactiveID string) error {
	if f.proacErr != nil {
		return f.proacErr
	}
	f.expiredIDs = append(f.expiredIDs, proactiveID)
	return nil
}

func (f *fakeStore) LinkProactiveRefs(ctx context.Context, proactiveID, userID string,
	refs []RefState) error {
	if f.proacErr != nil {
		return f.proacErr
	}
	for _, ref := range refs {
		if ref.RefType == RefInsight || ref.RefType == RefPattern {
			f.linkedProac = append(f.linkedProac,
				fmt.Sprintf("%s:%s:%s", proactiveID, ref.RefType, ref.ID))
		}
	}
	return nil
}

// 编译期闸：fakeStore 必须始终等于 domain.Store 的完整面（少实现一个方法，
// 这里先红，而不是等某个用例的桩静默走空）。
var _ Store = (*fakeStore)(nil)

var errProactiveStub = errors.New("fake: proactive 读写故障")
