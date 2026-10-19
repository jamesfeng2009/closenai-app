// T4-02 的领域判据闸（工单 §验收：非法转移、跨用户、重复同态幂等）。
//
// 这一文件钉的是**判据本身**；入口形状在 httpapi/v3_goals_test.go，
// 列的写法在 store/canonical_goal_integration_test.go（真库）。
// 三侧不重叠：这里不复刻 SQL 守卫（skip 的 COALESCE 不在 fake 里），
// 桩里也不做转移判断（goal_fake_test.go 的头注释就是这条分工）。
package domain

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

func goalStore(extra ...RefState) *fakeStore {
	f := &fakeStore{modelID: "model-1", modelVer: 7}
	f.refStates = append([]RefState{
		{RefType: RefGoal, ID: "goal-1", Status: StatusActive},
		{RefType: RefCommitment, ID: "act-1", Status: StatusActive},
	}, extra...)
	return f
}

func refState(kind RefType, id, status string) RefState {
	return RefState{RefType: kind, ID: id, Status: status}
}

// ── 1. 词表与转移表（唯一那张，两侧不许同源）────────────────────────────────

func TestGoalAndActionVocabulariesDifferOnSkip(t *testing.T) {
	// 断言两侧不同源：skipped 在行动侧合法、在目标侧不存在，这一格的事实源是
	// **列**（personal_goals 没有 skipped_at，personal_commitments 有），
	// 不是这两个切片的长度差。所以证人取库里读得到的形状：GoalStatuses 里逐字没有
	// "skipped"，而 ActionStatuses 里有——把 goal.go 头注释那句「目标上没有这一格」
	// 变成可执行的断言。
	if strings.Join(GoalStatuses, ",") != "active,completed,cancelled" {
		t.Errorf("GoalStatuses = %v，期望逐字三值", GoalStatuses)
	}
	if strings.Join(ActionStatuses, ",") != "active,completed,skipped,cancelled" {
		t.Errorf("ActionStatuses = %v，期望逐字四值（in_progress 本期不做）", ActionStatuses)
	}
	for _, v := range []LifecycleVerb{VerbComplete, VerbSkip, VerbCancel} {
		if !v.Valid() {
			t.Errorf("%s 应合法", v)
		}
		if _, ok := StatusForVerb(v); !ok {
			t.Errorf("%s 合不成目标态", v)
		}
	}
	if LifecycleVerb("archive").Valid() {
		t.Error("archive 不是本期词表里的动词")
	}
}

func TestTransitionTablesAdmitOnlyActiveOrigin(t *testing.T) {
	cases := []struct {
		from      string
		verb      LifecycleVerb
		wantTo    string
		wantLegal bool
		isGoal    bool
	}{
		{StatusActive, VerbComplete, StatusCompleted, true, true},
		{StatusActive, VerbCancel, StatusCancelled, true, true},
		{StatusActive, VerbSkip, "", false, true},
		{StatusCompleted, VerbCancel, "", false, true},
		{StatusCancelled, VerbComplete, "", false, true},
		{"", VerbComplete, "", false, true},
		{StatusActive, VerbComplete, StatusCompleted, true, false},
		{StatusActive, VerbSkip, StatusSkipped, true, false},
		{StatusActive, VerbCancel, StatusCancelled, true, false},
		{StatusSkipped, VerbComplete, "", false, false},
		{StatusCompleted, VerbSkip, "", false, false},
	}
	for _, c := range cases {
		to, legal := NextGoalStatus(c.from, c.verb)
		if !c.isGoal {
			to, legal = NextActionStatus(c.from, c.verb)
		}
		if to != c.wantTo || legal != c.wantLegal {
			t.Errorf("%s --%s--> (%q,%t)，期望 (%q,%t) 面=%v",
				c.from, c.verb, to, legal, c.wantTo, c.wantLegal,
				map[bool]string{true: "goal", false: "action"}[c.isGoal])
		}
	}
}

// ── 2. CreateGoal ───────────────────────────────────────────────────────────

func TestCreateGoalWritesRowEventWithoutVersionBump(t *testing.T) {
	f := goalStore()
	s, _ := newTestService(f)
	target := time.Date(2026, 11, 30, 0, 0, 0, 0, time.UTC)
	rec, err := s.CreateGoal(context.Background(), GoalProposal{
		UserID: "u1", Title: "  早睡一小时  ", Description: "  ",
		TargetAt: target, HasTargetAt: true})
	if err != nil {
		t.Fatalf("CreateGoal: %v", err)
	}
	if len(f.goals) != 1 {
		t.Fatalf("goals = %+v，期望恰好一行", f.goals)
	}
	row := f.goals[0]
	if row.Status != StatusActive {
		t.Errorf("初值 = %q，期望 active（唯一初值由领域给，不让仓储猜）", row.Status)
	}
	if row.PersonalModelID != "model-1" || row.UserID != "u1" {
		t.Errorf("行上 (user, model) = (%q,%q)，期望先 EnsurePersonalModel 再插行",
			row.UserID, row.PersonalModelID)
	}
	if row.Title != "早睡一小时" {
		t.Errorf("title = %q，期望去掉首尾空白", row.Title)
	}
	// description 只有空白 ⇒ 落成空串，仓储侧 textOf 把它折成 NULL。
	if row.Description != "" {
		t.Errorf("description = %q，期望空白折叠成空串", row.Description)
	}
	if !row.HasTargetAt || !row.TargetAt.Equal(target) {
		t.Errorf("target_at = %v/%t", row.TargetAt, row.HasTargetAt)
	}
	if row.StartedAt.IsZero() {
		t.Error("started_at 为空：列上 NOT NULL 之外，用户看的是「从哪天开始算」")
	}
	// 留痕一条，且**不入 outbox**（新事件类型不在 outboxRouting，见 service_goal.go 头注释）。
	if len(f.events) != 1 || f.events[0].EventType != "goal_created" {
		t.Fatalf("events = %+v，期望恰一条 goal_created", f.events)
	}
	if f.events[0].AggregateType != "goals" || f.events[0].AggregateID != rec.ID {
		t.Errorf("事件指向 = %s/%s", f.events[0].AggregateType, f.events[0].AggregateID)
	}
	if len(f.outbox) != 0 {
		t.Errorf("outbox = %+v，内部面的四条新事件本期不广播", f.outbox)
	}
	// 版本账本：建一颗目标不改模型相信什么 ⇒ 不 ++、不写 versions 行。
	if f.modelVer != 7 {
		t.Errorf("模型版本从 7 涨到 %d ⇒ 用户新建意图不是模型内容变了", f.modelVer)
	}
	if len(f.versions) != 0 {
		t.Errorf("versions = %+v，期望空", f.versions)
	}
	if rec.ModelVersion != 7 || !rec.Changed || rec.EventID == "" {
		t.Errorf("receipt = %+v", rec)
	}
}

func TestCreateGoalTitleBounds(t *testing.T) {
	for _, tc := range []struct {
		name      string
		title     string
		wantError bool
	}{
		{"空", "", true},
		{"只有空白", "   ", true},
		{"两百字", strings.Repeat("字", 200), false},
		{"两百零一字", strings.Repeat("字", 201), true},
	} {
		f := goalStore()
		s, _ := newTestService(f)
		_, err := s.CreateGoal(context.Background(), GoalProposal{
			UserID: "u1", Title: tc.title})
		if got := errors.Is(err, ErrInvalidGoalProposal); got != tc.wantError {
			t.Errorf("%s：ErrInvalidGoalProposal = %t，期望 %t（err=%v）",
				tc.name, got, tc.wantError, err)
		}
		if !tc.wantError && len(f.goals) != 1 {
			t.Errorf("%s 合法却没插行", tc.name)
		}
		if tc.wantError && len(f.goals) != 0 {
			t.Errorf("%s 被拒却插了行 ⇒ 校验发生在写之后", tc.name)
		}
	}
}

// ── 3. CreateAction ─────────────────────────────────────────────────────────

func TestCreateActionRejectsNonActiveAndForeignGoal(t *testing.T) {
	f := goalStore(refState(RefGoal, "goal-done", StatusCompleted))
	s, _ := newTestService(f)
	if _, err := s.CreateAction(context.Background(), ActionProposal{
		UserID: "u1", GoalID: "goal-done", Title: "x"}); !errors.Is(err, ErrGoalNotActive) {
		t.Errorf("已完成目标下新建行动 = %v，期望 ErrGoalNotActive ⇒ 409", err)
	}
	// 别人的（或不存在的那颗）：refStates 里没有 ⇒ 与「不存在」同一条 ErrInvalidOwnership。
	if _, err := s.CreateAction(context.Background(), ActionProposal{
		UserID: "u1", GoalID: "goal-other", Title: "x"}); !errors.Is(err, ErrInvalidOwnership) {
		t.Errorf("跨用户 goal_id = %v，期望 ErrInvalidOwnership ⇒ 404 而不是 403", err)
	}
	if len(f.commitments) != 0 || len(f.events) != 0 {
		t.Errorf("两次拒绝写了 %+v 行 / %+v 事件 ⇒ 校验必须在插行之前",
			f.commitments, f.events)
	}
	if f.rolledBack != true {
		t.Error("事务没回滚：失败的一批要整笔消失")
	}
}

func TestCreateActionWithoutGoalStoresNullGoalID(t *testing.T) {
	f := goalStore()
	s, _ := newTestService(f)
	due := time.Date(2026, 10, 25, 9, 0, 0, 0, time.UTC)
	rec, err := s.CreateAction(context.Background(), ActionProposal{
		UserID: "u1", Title: "散步二十分钟", DueAt: due, HasDueAt: true})
	if err != nil {
		t.Fatalf("CreateAction: %v", err)
	}
	if len(f.commitments) != 1 {
		t.Fatalf("commitments = %+v", f.commitments)
	}
	// GoalID 空串＝仓储侧写成 NULL（T4-01 的测点在 store 的真库测里，这里钉的是
	// domain 不把空串换成任何占位词）。
	if f.commitments[0].GoalID != "" {
		t.Errorf("goal_id = %q，期望空串 ⇒ NULL", f.commitments[0].GoalID)
	}
	if rec.GoalID != "" || rec.Status != StatusActive {
		t.Errorf("receipt = %+v", rec)
	}
	if f.commitments[0].Status != StatusActive {
		t.Errorf("初值 = %q", f.commitments[0].Status)
	}
}

func TestCreateActionChecksOwnershipBeforeInsert(t *testing.T) {
	f := goalStore()
	f.refStates = nil // 一颗都不认识
	s, _ := newTestService(f)
	if _, err := s.CreateAction(context.Background(), ActionProposal{
		UserID: "u1", GoalID: "goal-1", Title: "t"}); !errors.Is(err, ErrInvalidOwnership) {
		t.Fatalf("err = %v，期望 ErrInvalidOwnership", err)
	}
	if len(f.commitments) != 0 {
		t.Error("归属没过就插了行")
	}
	// 反向证人：不带 goal_id 时同一份空 refStates 不该挡路（NULL 是合法形状）。
	if _, err := s.CreateAction(context.Background(), ActionProposal{
		UserID: "u1", Title: "t"}); err != nil {
		t.Fatalf("无目标行动被拒 = %v ⇒ 空 goal_id 被当成越权引用了", err)
	}
	if len(f.commitments) != 1 {
		t.Errorf("commitments = %+v", f.commitments)
	}
}

// ── 4. TransitionAction ─────────────────────────────────────────────────────

func TestTransitionActionAdmitsThreeVerbsFromActive(t *testing.T) {
	for _, tc := range []struct {
		verb LifecycleVerb
		want string
	}{
		{VerbComplete, StatusCompleted}, {VerbSkip, StatusSkipped}, {VerbCancel, StatusCancelled},
	} {
		f := goalStore()
		s, _ := newTestService(f)
		f.refStates = []RefState{{RefType: RefCommitment, ID: "act-1", Status: StatusActive}}
		rec, err := s.TransitionAction(context.Background(), "u1", "act-1", tc.verb)
		if err != nil {
			t.Fatalf("%s: %v", tc.verb, err)
		}
		if !rec.Changed || rec.Status != tc.want {
			t.Errorf("%s 的 receipt = %+v，期望 Changed 且 %s", tc.verb, rec, tc.want)
		}
		want := map[LifecycleVerb]int{VerbComplete: len(f.commitmentDone),
			VerbSkip: len(f.commitmentSkips), VerbCancel: len(f.commitmentCancels)}[tc.verb]
		if want != 1 {
			t.Errorf("%s 落库 = %+v/%+v/%+v，期望恰好一次", tc.verb,
				f.commitmentDone, f.commitmentSkips, f.commitmentCancels)
		}
		// 另外两枚动词一条都不许碰：dispatch 写错 case 只会在这里红。
		switch tc.verb {
		case VerbComplete:
			if len(f.commitmentSkips)+len(f.commitmentCancels) != 0 {
				t.Error("complete 同时写了 skip 或 cancel")
			}
		case VerbSkip:
			if len(f.commitmentDone)+len(f.commitmentCancels) != 0 {
				t.Error("skip 同时写了 complete 或 cancel")
			}
		case VerbCancel:
			if len(f.commitmentDone)+len(f.commitmentSkips) != 0 {
				t.Error("cancel 同时写了 complete 或 skip")
			}
		}
		if len(f.events) != 1 || f.events[0].EventType != "action_transitioned" {
			t.Fatalf("%s 的事件 = %+v", tc.verb, f.events)
		}
		if f.modelVer != 7 || len(f.versions) != 0 {
			t.Errorf("%s 动了版本账本（ver=%d versions=%+v）", tc.verb, f.modelVer, f.versions)
		}
	}
}

func TestTransitionActionIsIdempotentOnSameState(t *testing.T) {
	f := goalStore()
	s, _ := newTestService(f)
	f.refStates = []RefState{{RefType: RefCommitment, ID: "act-1", Status: StatusCompleted}}
	rec, err := s.TransitionAction(context.Background(), "u1", "act-1", VerbComplete)
	if err != nil {
		t.Fatalf("重复同态被拒 = %v ⇒ 连点两次按钮的第二下不该是 409", err)
	}
	if rec.Changed || rec.Status != StatusCompleted {
		t.Errorf("receipt = %+v，期望 Changed=false 且回当前态", rec)
	}
	if rec.Detail == "" {
		t.Error("Detail 为空：客户端只知道没改，不知道「已经是这一态」")
	}
	if len(f.commitmentDone) != 0 || len(f.events) != 0 {
		t.Errorf("幂等那一次写了 %+v/%+v ⇒ 重复请求产生了第二次变更与第二条历史",
			f.commitmentDone, f.events)
	}
}

func TestTransitionActionRefusesTerminalAndForeign(t *testing.T) {
	f := goalStore()
	s, _ := newTestService(f)
	f.refStates = []RefState{{RefType: RefCommitment, ID: "act-1", Status: StatusSkipped}}
	if _, err := s.TransitionAction(context.Background(), "u1", "act-1",
		VerbComplete); !errors.Is(err, ErrActionIllegalTransition) {
		t.Errorf("已跳过再完成 = %v，期望 ErrActionIllegalTransition ⇒ 409", err)
	}
	f.refStates = nil
	if _, err := s.TransitionAction(context.Background(), "u1", "act-1",
		VerbCancel); !errors.Is(err, ErrInvalidOwnership) {
		t.Errorf("查不到的行动 = %v，期望 ErrInvalidOwnership ⇒ 404", err)
	}
	if len(f.commitmentCancels) != 0 || len(f.events) != 0 {
		t.Error("被拒的转移留下了写痕")
	}
	if _, err := s.TransitionAction(context.Background(), "u1", "act-1",
		LifecycleVerb("undo")); !errors.Is(err, ErrInvalidActionProposal) {
		t.Errorf("未知动词 = %v，期望 ErrInvalidActionProposal ⇒ 400", err)
	}
}

// ── 5. TransitionGoal ───────────────────────────────────────────────────────

func TestTransitionGoalRejectsSkipButAdmitsCompleteAndCancel(t *testing.T) {
	f := goalStore()
	s, _ := newTestService(f)
	f.refStates = []RefState{{RefType: RefGoal, ID: "goal-1", Status: StatusActive}}
	if _, err := s.TransitionGoal(context.Background(), "u1", "goal-1",
		VerbSkip); !errors.Is(err, ErrGoalIllegalTransition) {
		t.Errorf("goal 的 skip = %v，期望 ErrGoalIllegalTransition ⇒ 409（列上没有 skipped_at）", err)
	}
	if len(f.commitmentSkips) != 0 {
		t.Error("goal 的 skip 打到承诺表上了")
	}
	if _, err := s.TransitionGoal(context.Background(), "u1", "goal-1", VerbComplete); err != nil {
		t.Fatalf("complete: %v", err)
	}
	if len(f.goalDone) != 1 {
		t.Errorf("goalDone = %+v，期望复用既有的 CompleteGoal", f.goalDone)
	}
	// 桩不替 domain 更新现状（那是真库才会发生的事），这里手动把行推到 completed，
	// 于是「completed → cancel」这一格打的是转移表本身，不是桩的记账。
	f.refStates = []RefState{{RefType: RefGoal, ID: "goal-1", Status: StatusCompleted}}
	if _, err := s.TransitionGoal(context.Background(), "u1", "goal-1", VerbCancel); !errors.Is(
		err, ErrGoalIllegalTransition) {
		t.Errorf("completed → cancel = %v，期望非法", err)
	}
	f.refStates = []RefState{{RefType: RefGoal, ID: "goal-1", Status: StatusActive}}
	rec, err := s.TransitionGoal(context.Background(), "u1", "goal-1", VerbCancel)
	if err != nil || rec.Status != StatusCancelled || !rec.Changed {
		t.Fatalf("cancel = %+v err %v", rec, err)
	}
	if len(f.goalCancels) != 1 {
		t.Errorf("goalCancels = %+v", f.goalCancels)
	}
	if len(f.goalDone) != 1 {
		t.Error("cancel 走了 CompleteGoal ⇒ completed_at 会被刷，度量凭空多一个分子")
	}
	if f.events[1].EventType != "goal_transitioned" {
		t.Errorf("事件类型 = %q", f.events[1].EventType)
	}
}

func TestTransitionGoalIsIdempotentOnSameState(t *testing.T) {
	// 与行动侧同一格判据，但必须有自己的一条：goal 的幂等分支在 `TransitionGoal` 里，
	// 行动侧的闸打不到它（工单 §验收「重复 PATCH 幂等」说的是两面）。
	f := goalStore()
	s, _ := newTestService(f)
	f.refStates = []RefState{{RefType: RefGoal, ID: "goal-1", Status: StatusCancelled}}
	rec, err := s.TransitionGoal(context.Background(), "u1", "goal-1", VerbCancel)
	if err != nil {
		t.Fatalf("重复取消被拒 = %v ⇒ 连点两下的第二下不该回 409", err)
	}
	if rec.Changed || rec.Status != StatusCancelled || rec.Detail == "" {
		t.Errorf("receipt = %+v，期望 Changed=false、回当前态、带一句 Detail", rec)
	}
	if len(f.goalCancels) != 0 || len(f.events) != 0 {
		t.Errorf("幂等那一次写了 %+v/%+v ⇒ 重复请求产生了第二次变更与第二条历史",
			f.goalCancels, f.events)
	}
}

func TestTransitionGoalForeignIDIsOwnershipError(t *testing.T) {
	f := goalStore()
	s, _ := newTestService(f)
	f.refStates = nil
	if _, err := s.TransitionGoal(context.Background(), "u1", "goal-x",
		VerbCancel); !errors.Is(err, ErrInvalidOwnership) {
		t.Errorf("err = %v，期望 ErrInvalidOwnership ⇒ 404", err)
	}
	if len(f.goalCancels) != 0 {
		t.Error("越权的取消改了行")
	}
}

// ── 6. 列表面 ───────────────────────────────────────────────────────────────

type stubGoalReader struct {
	goals    []GoalRecord
	actions  []ActionRecord
	gotStat  string
	gotLimit int
	which    string
}

func (s *stubGoalReader) ListGoalRecords(_ context.Context, _, status string,
	limit int) ([]GoalRecord, error) {
	s.which, s.gotStat, s.gotLimit = "goals", status, limit
	return s.goals, nil
}

func (s *stubGoalReader) ListActionRecords(_ context.Context, _, status string,
	limit int) ([]ActionRecord, error) {
	s.which, s.gotStat, s.gotLimit = "actions", status, limit
	return s.actions, nil
}

func TestGoalBrowserClampsLimitAndMarksTruncation(t *testing.T) {
	read := &stubGoalReader{}
	b := NewGoalBrowser(read)
	if _, err := b.ListGoals(context.Background(), "u1", "", 0); err != nil {
		t.Fatal(err)
	}
	if read.gotLimit != defaultGoalListLimit+1 {
		t.Errorf("limit=0 取到 %d，期望默认 %d 再加一条证人",
			read.gotLimit, defaultGoalListLimit)
	}
	if _, err := b.ListGoals(context.Background(), "u1", "  active ", 9_999); err != nil {
		t.Fatal(err)
	}
	if read.gotLimit != maxGoalListLimit+1 {
		t.Errorf("limit=9999 → %d，期望收敛到 %d+1", read.gotLimit, maxGoalListLimit)
	}
	if read.gotStat != "active" {
		t.Errorf("status = %q，期望去掉首尾空白", read.gotStat)
	}

	filled := make([]GoalRecord, 0, maxGoalListLimit+1)
	for i := 0; i <= maxGoalListLimit; i++ {
		filled = append(filled, GoalRecord{ID: string(rune('a' + i%26))})
	}
	read.goals = filled
	view, err := b.ListGoals(context.Background(), "u1", "", maxGoalListLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(view.Items) != maxGoalListLimit || !view.Truncated {
		t.Errorf("截断面 = %d 条 truncated=%t", len(view.Items), view.Truncated)
	}
	read.goals = filled[:2]
	view, _ = b.ListGoals(context.Background(), "u1", "", maxGoalListLimit)
	if view.Truncated {
		t.Error("没满也报截断 ⇒ 前端会显示一个不存在的「还有更多」")
	}
}

func TestGoalBrowserRejectsMissingIdentityOrReader(t *testing.T) {
	b := NewGoalBrowser(&stubGoalReader{})
	if _, err := b.ListGoals(context.Background(), "  ", "", 10); err == nil {
		t.Error("匿名身份查出了列表 ⇒ 这一面的身份只能来自 X-User-Id")
	}
	if _, err := b.ListActions(context.Background(), "", "", 10); err == nil {
		t.Error("空身份查行动")
	}
	empty := NewGoalBrowser(nil)
	if _, err := empty.ListGoals(context.Background(), "u1", "", 10); err == nil {
		t.Error("没接读面却回成功")
	}
	if _, err := empty.ListActions(context.Background(), "u1", "", 10); err == nil {
		t.Error("没接读面却回成功")
	}
}

func TestGoalBrowserPassesUnknownStatusThrough(t *testing.T) {
	read := &stubGoalReader{}
	if _, err := NewGoalBrowser(read).ListGoals(context.Background(), "u1", "archived", 5); err != nil {
		t.Fatal(err)
	}
	// 原词进 SQL ⇒ 库里必然 0 行；在这里改成「忽略过滤器」会让一次拼错的请求
	// 把用户以为已经过滤掉的历史全拉出来。
	if read.gotStat != "archived" {
		t.Errorf("status = %q，期望原词透传给 SQL", read.gotStat)
	}
	if len(read.gotStat) == 0 {
		t.Error("过滤器被吞掉了")
	}
}
