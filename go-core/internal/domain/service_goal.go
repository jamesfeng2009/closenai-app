// T4-02 的领域入口：目标与行动的建、读、状态转移。
//
// 分层与 N6 逐字同构（01 §22 不变量 7）：这四条是 `personal_goals` /
// `personal_commitments` 两张表的**唯一写入口**，HTTP handler 只能经它们；
// 词表与转移合法性全部来自 goal.go，本文件不出现第二张转移表。
//
// ── 为什么这里**不** ++模型版本 ──────────────────────────────────────────────
// 02:1069 的判据是「personal model 的内容变了才 ++」。用户自己立一颗目标是
// 一个新的意图，不是模型相信什么的变化——它不改 fact/pattern/insight 任何一列。
// 版本账本（02 §31）如果因为「建了一颗目标」就多一行，N9 的「模型为什么变了」
// 会被一堆与模型无关的行淹没，而不变量 8 的审计面就失去意义。
// 对照：用户纠正一条 fact 是要 ++的（applyFeedback），因为那**就是**改模型相信什么。
//
// ── 为什么事件不进 outbox ────────────────────────────────────────────────────
// `goal_created` / `action_transitioned` 这四个词不在 `outboxRouting`（delivery.go:37）
// 里，`emit` 因此只写权威事件行。这是有意的：新增一个跨进程消息类型要同时有
// 订阅方、契约登记（contracts/events.md）与消费者，本期三条都没有。
// 「留痕」与「广播」是两件事，工单 §怎么做.5 要的是前者。

package domain

import (
	"context"
	"errors"
	"fmt"
	"strings"
)

// CreateGoal 新建一颗目标。
//
// 先 `EnsurePersonalModel` 再 `InsertGoal`：`personal_goals.personal_model_id`
// 是 NOT NULL（0002:263），没有模型行就插不进去；而这一条面是新用户第一次进
// 「行动」屏时最先被调用的，模型行常常确实还不存在。
func (s *Service) CreateGoal(ctx context.Context, p GoalProposal) (GoalReceipt, error) {
	title, err := checkTitle(p.Title, ErrInvalidGoalProposal)
	if err != nil {
		return GoalReceipt{}, err
	}
	if strings.TrimSpace(p.UserID) == "" {
		return GoalReceipt{}, fmt.Errorf("%w: user_id 不能为空", ErrInvalidGoalProposal)
	}
	receipt := GoalReceipt{}
	err = s.db.InTx(ctx, func(ctx context.Context, st Store) error {
		modelID, version, err := st.EnsurePersonalModel(ctx, p.UserID)
		if err != nil {
			return err
		}
		id := s.newID()
		if err := st.InsertGoal(ctx, GoalRow{ID: id, UserID: p.UserID,
			PersonalModelID: modelID, Title: title,
			Description: strings.TrimSpace(p.Description), Status: StatusActive,
			StartedAt:   s.now().UTC(),
			TargetAt:    p.TargetAt,
			HasTargetAt: p.HasTargetAt}); err != nil {
			return err
		}
		eventID := s.newID()
		if err := s.emit(ctx, st, s.outcomeEvent(eventID, p.UserID, modelID, "goal_created",
			"goals", id, version,
			fmt.Sprintf(`{"title":%q,"status":%q}`, title, StatusActive), "")); err != nil {
			return err
		}
		receipt = GoalReceipt{ID: id, Status: StatusActive, Changed: true,
			EventID: eventID, ModelVersion: version}
		return nil
	})
	if err != nil {
		return GoalReceipt{}, err
	}
	return receipt, nil
}

// CreateAction 新建一条行动。
//
// `GoalID` 空串＝不挂在目标下（库里落成 NULL，T4-01 的测点），非空时两件事都要成立：
// 这一颗存在**且属于本人**（否则 ErrInvalidOwnership ⇒ 404，不给 403：
// 区分「不存在」与「不是你的」等于替旁人确认这个 id 是真的，v3_feedback.go 同判据），
// 且当前是 active（否则 ErrGoalNotActive ⇒ 409）。
// 后一格是工单 §怎么做.1 的「校验 goal_id 属于本人且 status='active'」，
// 读的是 `ListRefStates` 那一格既有 UNION——goal/commitment 两支本来就在里面，
// 再造一条 `GetGoalState` 就是第二个读事实源。
func (s *Service) CreateAction(ctx context.Context, p ActionProposal) (ActionReceipt, error) {
	title, err := checkTitle(p.Title, ErrInvalidActionProposal)
	if err != nil {
		return ActionReceipt{}, err
	}
	if strings.TrimSpace(p.UserID) == "" {
		return ActionReceipt{}, fmt.Errorf("%w: user_id 不能为空", ErrInvalidActionProposal)
	}
	goalID := strings.TrimSpace(p.GoalID)
	receipt := ActionReceipt{}
	err = s.db.InTx(ctx, func(ctx context.Context, st Store) error {
		modelID, version, err := st.EnsurePersonalModel(ctx, p.UserID)
		if err != nil {
			return err
		}
		if goalID != "" {
			state, err := s.refState(ctx, st, p.UserID, RefGoal, goalID)
			if err != nil {
				return err
			}
			if state.Status != StatusActive {
				return fmt.Errorf("%w: goal=%s status=%s", ErrGoalNotActive, goalID, state.Status)
			}
		}
		id := s.newID()
		if err := st.InsertCommitment(ctx, CommitmentRow{ID: id, UserID: p.UserID,
			PersonalModelID: modelID, Title: title,
			Description: strings.TrimSpace(p.Description), Status: StatusActive,
			DueAt: p.DueAt, HasDueAt: p.HasDueAt, GoalID: goalID}); err != nil {
			return err
		}
		eventID := s.newID()
		if err := s.emit(ctx, st, s.outcomeEvent(eventID, p.UserID, modelID, "action_created",
			"commitments", id, version,
			fmt.Sprintf(`{"title":%q,"goal_id":%q,"status":%q}`, title, goalID, StatusActive),
			"")); err != nil {
			return err
		}
		receipt = ActionReceipt{ID: id, Status: StatusActive, GoalID: goalID,
			Changed: true, EventID: eventID, ModelVersion: version}
		return nil
	})
	if err != nil {
		return ActionReceipt{}, err
	}
	return receipt, nil
}

// TransitionGoal 按动词改写目标状态。语义与 TransitionAction 同构，只在两处不同：
// skip 这一枚动词在目标侧非法（personal_goals 没有 skipped_at 列），
// 且完成走既有的 CompleteGoal（它会刷 completed_at，那是 04 §22 的度量分子）。
func (s *Service) TransitionGoal(ctx context.Context, userID, goalID string,
	verb LifecycleVerb) (GoalReceipt, error) {
	if !verb.Valid() {
		return GoalReceipt{}, fmt.Errorf("%w: 未知的动作 %q", ErrInvalidGoalProposal, verb)
	}
	if !validVerbForGoal(verb) {
		return GoalReceipt{}, fmt.Errorf("%w: goal 不接受 %s（personal_goals 没有 skipped_at 列）",
			ErrGoalIllegalTransition, verb)
	}
	receipt := GoalReceipt{}
	err := s.db.InTx(ctx, func(ctx context.Context, st Store) error {
		_, version, err := st.EnsurePersonalModel(ctx, userID)
		if err != nil {
			return err
		}
		state, err := s.refState(ctx, st, userID, RefGoal, goalID)
		if err != nil {
			return err
		}
		to, legal := NextGoalStatus(state.Status, verb)
		if !legal {
			if state.Status == verbStatus(verb) {
				receipt = GoalReceipt{ID: goalID, Status: state.Status, Changed: false,
					ModelVersion: version,
					Detail:       fmt.Sprintf("已经是 %s，重复请求不产生第二次变更", state.Status)}
				return nil
			}
			return fmt.Errorf("%w: %s --%s-->", ErrGoalIllegalTransition,
				state.Status, verb)
		}
		if verb == VerbComplete {
			if err := st.CompleteGoal(ctx, userID, goalID); err != nil {
				return err
			}
		} else if err := st.CancelGoal(ctx, userID, goalID); err != nil {
			return err
		}
		eventID := s.newID()
		if err := s.emit(ctx, st, s.outcomeEvent(eventID, userID, "", "goal_transitioned",
			"goals", goalID, version,
			fmt.Sprintf(`{"from":%q,"to":%q,"verb":%q}`, state.Status, to, verb), "")); err != nil {
			return err
		}
		receipt = GoalReceipt{ID: goalID, Status: to, Changed: true,
			EventID: eventID, ModelVersion: version}
		return nil
	})
	if err != nil {
		return GoalReceipt{}, err
	}
	return receipt, nil
}

// TransitionAction 按动词改写行动状态：active → completed | skipped | cancelled。
//
// 三条判据各有各自的坏法要挡：
//   - **终态不可回退**：NextActionStatus 只放行 active 起算；已完成的行动被改回
//     active 等于凭空多一次「未完成」，而那条行动的 skipped_at/completed_at 列还留着，
//     行会自相矛盾；
//   - **重复同态 ⇒ 幂等返回当前**（工单 §怎么做.1）：不改写、不写事件，
//     Changed=false 带 Detail 说明。返回 200 而不是 409：连点两次按钮是网络重试，
//     不是一个非法请求；
//   - **0 行折算成归属错误**：store 侧把 `ErrNoRows` 折成 ErrInvalidOwnership，
//     于是「并发被别人删了」与「这一颗本来就是别人的」在同一格里回 404。
//
// skipped_at 的幂等由列上的 `COALESCE(skipped_at, now())` 兜（T4-01），
// 不在这里判——这一层的判据只兜得住顺序执行，并发那半只有 SQL 兜得住。
func (s *Service) TransitionAction(ctx context.Context, userID, actionID string,
	verb LifecycleVerb) (ActionReceipt, error) {
	if !verb.Valid() {
		return ActionReceipt{}, fmt.Errorf("%w: 未知的动作 %q", ErrInvalidActionProposal, verb)
	}
	receipt := ActionReceipt{}
	err := s.db.InTx(ctx, func(ctx context.Context, st Store) error {
		_, version, err := st.EnsurePersonalModel(ctx, userID)
		if err != nil {
			return err
		}
		state, err := s.refState(ctx, st, userID, RefCommitment, actionID)
		if err != nil {
			return err
		}
		to, legal := NextActionStatus(state.Status, verb)
		if !legal {
			if state.Status == verbStatus(verb) {
				receipt = ActionReceipt{ID: actionID, Status: state.Status, Changed: false,
					ModelVersion: version,
					Detail:       fmt.Sprintf("已经是 %s，重复请求不产生第二次变更", state.Status)}
				return nil
			}
			return fmt.Errorf("%w: %s --%s-->", ErrActionIllegalTransition,
				state.Status, verb)
		}
		switch verb {
		case VerbComplete:
			err = st.CompleteCommitment(ctx, userID, actionID)
		case VerbSkip:
			err = st.SkipCommitment(ctx, userID, actionID)
		case VerbCancel:
			err = st.CancelCommitment(ctx, userID, actionID)
		}
		if err != nil {
			return err
		}
		eventID := s.newID()
		if err := s.emit(ctx, st, s.outcomeEvent(eventID, userID, "", "action_transitioned",
			"commitments", actionID, version,
			fmt.Sprintf(`{"from":%q,"to":%q,"verb":%q}`, state.Status, to, verb), "")); err != nil {
			return err
		}
		receipt = ActionReceipt{ID: actionID, Status: to, Changed: true,
			EventID: eventID, ModelVersion: version}
		return nil
	})
	if err != nil {
		return ActionReceipt{}, err
	}
	return receipt, nil
}

// refState 取一条引用对象的当前状态，查不到／不是本人 ⇒ ErrInvalidOwnership。
//
// 它和 verifyTargets 读的是同一条 SQL（ListRefStates），只省掉一层 []TargetRef 的拼装：
// 这一面每次只对**一颗**已知的 id 做判断，而 verifyTargets 是为 outcome 的
// 多目标引用写的。两处共用同一张 UNION，因此「这一颗属于你」这件事只有一个判据。
func (s *Service) refState(ctx context.Context, st Store, userID string,
	kind RefType, id string) (RefState, error) {
	rows, err := st.ListRefStates(ctx, userID, []string{id})
	if err != nil {
		return RefState{}, err
	}
	key := string(kind) + ":" + id
	for _, r := range rows {
		if string(r.RefType)+":"+r.ID == key {
			return r, nil
		}
	}
	return RefState{}, fmt.Errorf("%w: %s", ErrInvalidOwnership, key)
}

// validVerbForGoal 是 goal 侧那一格「skip 不存在」的判据。
// 判据本体在转移表 NextGoalStatus 里（那里 skip 落不进任何一态），这一格只是
// 把「未知的动词」与「这一面不接受的动词」分给两个错误码：skip 是合法动词、
// 只是不属于目标，所以它属于转移非法（409），不属于入参不合形（400）。
func validVerbForGoal(v LifecycleVerb) bool { return v == VerbComplete || v == VerbCancel }

// verbStatus 只用于幂等判定：当前态**就是**这一枚动词的目标态时，那是一次重复请求。
// 未知动词在这里得到空串，永远合不上当前态，于是落到非法分支——不会误判成幂等。
func verbStatus(v LifecycleVerb) string {
	s, _ := StatusForVerb(v)
	return s
}

// GoalReader 是两条列表面需要的持久化面，实现只有一个：internal/store 的池面只读仓储。
//
// `status` 空串＝不过滤（SQL 里那一格 `= ” OR status = $`），非空时**已由领域层**
// 收敛到该面的合法词表（见 GoalBrowser），仓储因此不做词法判断。
// `limit` 是取多少行，不是「用户要多少」：Browser 多要一条来判断有没有截断。
type GoalReader interface {
	ListGoalRecords(ctx context.Context, userID, status string,
		limit int) ([]GoalRecord, error)
	ListActionRecords(ctx context.Context, userID, status string,
		limit int) ([]ActionRecord, error)
}

// GoalBrowser 把「这个用户立了哪些目标／行动」拼成视图。
//
// 为什么不塞进 InsightBrowser：那一面的判据是「模型说过的话能不能给你看」（可见集合），
// 这一面的判据是「你自己写的东西」——两者唯一的共同点是都带 user_id。
// 合并的结果是第一次改动就有人为了让行动列表多出 cancelled 去动洞察的可见集合。
type GoalBrowser struct{ read GoalReader }

func NewGoalBrowser(read GoalReader) *GoalBrowser { return &GoalBrowser{read: read} }

// 分页数值。与 T3-01 同一口径：**不是 env 键**（D-G12「键存在但没人读＝假旋钮」）。
const (
	defaultGoalListLimit = 50
	maxGoalListLimit     = 200
)

func (b *GoalBrowser) ListGoals(ctx context.Context, userID, status string,
	limit int) (GoalListView, error) {
	if strings.TrimSpace(userID) == "" {
		return GoalListView{}, errors.New("domain: goal list needs an authenticated user")
	}
	if b.read == nil {
		return GoalListView{}, errors.New("domain: goal list has no reader")
	}
	status, limit = narrowListArgs(status, limit)
	rows, err := b.read.ListGoalRecords(ctx, userID, status, limit+1)
	if err != nil {
		return GoalListView{}, err
	}
	view := GoalListView{Items: rows}
	if len(rows) > limit {
		view.Items = append([]GoalRecord(nil), rows[:limit]...)
		view.Truncated = true
	}
	return view, nil
}

func (b *GoalBrowser) ListActions(ctx context.Context, userID, status string,
	limit int) (ActionListView, error) {
	if strings.TrimSpace(userID) == "" {
		return ActionListView{}, errors.New("domain: action list needs an authenticated user")
	}
	if b.read == nil {
		return ActionListView{}, errors.New("domain: action list has no reader")
	}
	status, limit = narrowListArgs(status, limit)
	rows, err := b.read.ListActionRecords(ctx, userID, status, limit+1)
	if err != nil {
		return ActionListView{}, err
	}
	view := ActionListView{Items: rows}
	if len(rows) > limit {
		view.Items = append([]ActionRecord(nil), rows[:limit]...)
		view.Truncated = true
	}
	return view, nil
}

// narrowListArgs 收敛两个列表参数：status 只做去空白，**不**校验词表。
//
// 为什么不校验：库里没有的那一态必然返回零行，而「拼错一个过滤器」如果被 BFF/Go
// 抢先判成 400，同一个错误在两条面上就有两种形状（T3-03 头注释的同一件事）。
// 词表（GoalStatuses / ActionStatuses）在 goal.go 只服务**写入侧**那一格判定。
// limit 收敛到 [1, maxGoalListLimit]，0 与负数取默认值。
func narrowListArgs(status string, limit int) (string, int) {
	return strings.TrimSpace(status), clampListLimit(limit)
}

func clampListLimit(limit int) int {
	if limit <= 0 {
		return defaultGoalListLimit
	}
	if limit > maxGoalListLimit {
		return maxGoalListLimit
	}
	return limit
}
