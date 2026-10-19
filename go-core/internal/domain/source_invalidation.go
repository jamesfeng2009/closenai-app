// 来源行删除的传播面（T2-06）。
//
// `source_records` 是 D-2 的**薄指针**（只指不复制），所以用户删掉一行 `memories` 之后，
// 已经入账的 evidence 不会自己失效——依据回链会指着一条已经不存在的记忆。
// 本文件把那件事变成一次可重放的传播：翻登记行的状态，再逐条走
// `PropagateEvidenceInvalidation`（N6 已有实现，**本批给它的第一个生产调用方**）。
//
// 三个口径由 B-08 定案（2026-10-19 按工作单默认值裁，写进
// `CLOSENAI_V3_TASK_BOARD.md` 的 B-08 行）：
//
//  1. **登记行不删，只翻 `status='deleted'`**。删掉登记行会让「谁曾经指过哪一行」
//     这本账读不出来，而 `ListDerivedSupportForEvidence` 的「活支撑」判据
//     （`canonical.sql:531-570`，`s.status <> 'deleted'`）恰恰要读这一列——
//     行没了，那条派生行就永远显示「支撑还在」。
//  2. **证据按 dependent-only 退场**：只有「除这条来源外没有别的活支撑」的派生行才改写，
//     其余只留痕（不变量 9／O-6）。这一格不在本文件重复实现，全在
//     `PropagateEvidenceInvalidation` 里，本文件只决定**对哪些 evidence 调它**。
//  3. **GDPR 硬删不由本文件承接**：`purge_user` 连 canonical 行一起删，
//     回查面天然落 `SourceRowOwner` 的 `found=false`（T2-03 留的那格边界），
//     所以这里不发「第二遍删除」。
//
// 为什么传播必须排在翻转**提交之后**：`live_support` 是按
// `source_records.status <> 'deleted'` 算出来的。同一个事务里先翻再算，读到的还是
// 翻转前的自己（`OnlyDependent()` 恒 false，传播变成空转）；分两笔又各自开事务是
// R5b 已登记的既有形状，本批不改签名。
//
// 于是留下一格必须说破的缺口：翻转已提交而某条传播失败时，重投这一条会被
// `MarkSourceRecordDeletedOnce` 的单向闸跳过、缺口补不回来。本文件的处置是
// **把状态恢复成翻转前的值再报错**（`restore` 那一支），让这条投递回到「还没翻过」
// 的形态，交给 30s/2m/10m 阶梯与 T2-05 的重放面。恢复本身也失败 ⇒ 两个错误一起返回，
// 日志里的 Detail 明说库里现在停在 `deleted`（值班据此知道该手工补哪一行）。
package domain

import (
	"context"
	"errors"
	"fmt"
)

// SourcePointer 是 `source.deleted` 事件说的那一行：谁的、哪张表的、哪一行。
//
// 三件都不许为空，且 `SourceTable` 只认 `AdmissibleSourceTables`（T2-03 那张白名单）：
// 删除事件与候选提案走的是**同一张准入面**，扩来源时只该改那一处。
type SourcePointer struct {
	UserID      string
	SourceTable string
	SourceID    string
}

// SourceRecordState 是传播面需要的登记行三件（`Found=false` ＝没有这条登记行）。
type SourceRecordState struct {
	ID     string
	Status string
	Found  bool
}

// SourceInvalidation 是一次删除传播的账面结果。指标面与日志都从这里出，
// 所以四个计数格的含义必须在名字上就能读出来。
type SourceInvalidation struct {
	SourceRecordID string
	// Marked＝本条投递真的把 status 翻转成了 deleted。false 只有两种成因：
	// 没有登记行，或这一行早就翻过（后者不传播，见 §怎么做.4② 的单向幂等口径）。
	Marked bool
	// EvidenceCount 是这条登记行支撑的 evidence 行数，也就是传播的分母。
	EvidenceCount int
	// 三个计数逐条对着 PropagationResult 的三个面累加，不自发明第四种分类。
	Invalidated           int
	Monitored             int
	RevalidationRequested int
	ModelVersion          int64
	// RolledBack＝「翻转成功但传播中途失败」那一支真的把状态恢复了。
	// false 且 Err 非空时，库里停在 deleted，Detail 里写清是哪一条。
	RolledBack bool
	Detail     string
}

// InvalidateSource 把一次「来源行被删」落到账面：翻登记行 → 逐条传播。
//
// 返回的 error 只有两种形状：判定级非法（指针为空／表没开，永久）与库/传播失败
// （瞬态，交回阶梯）。哨兵复用 T2-03 那几个值（`ErrUnsupportedSourceTable`），
// 于是 inbound 的 `classify` 不必新增一行钉码表——同一条准入面的一侧改错，
// 另一边由 `sourcePointerRejections` 那张表自动接住。
//
// 「查不到登记行」**不是**错误也不是拒绝：绝大多数记忆从没被抽成证据，
// 它们被删时库里本来就没有对应的账。把它读成错误会让每次正常删除都上重试阶梯。
func (s *Service) InvalidateSource(ctx context.Context, p SourcePointer) (SourceInvalidation, error) {
	if p.UserID == "" || p.SourceTable == "" || p.SourceID == "" {
		return SourceInvalidation{}, fmt.Errorf(
			"domain: source 指针三件不许为空，收到 user_id=%q table=%q id=%q",
			p.UserID, p.SourceTable, p.SourceID)
	}
	if !contains(AdmissibleSourceTables, p.SourceTable) {
		return SourceInvalidation{}, fmt.Errorf("%w: source_table=%q，本期准入面=%v",
			ErrUnsupportedSourceTable, p.SourceTable, AdmissibleSourceTables)
	}

	out := SourceInvalidation{}
	var evidenceIDs []string
	var statusBefore string
	err := s.db.InTx(ctx, func(ctx context.Context, st Store) error {
		row, err := st.SourceRecordByPointer(ctx, p.UserID, p.SourceTable, p.SourceID)
		if err != nil {
			return err
		}
		if !row.Found {
			out.Detail = "没有这条登记行：该来源从没进过 canonical，无需传播"
			return nil
		}
		out.SourceRecordID = row.ID
		marked, err := st.MarkSourceRecordDeletedOnce(ctx, row.ID, p.UserID)
		if err != nil {
			return err
		}
		if !marked {
			out.Detail = fmt.Sprintf(
				"登记行状态已是 %q：单向翻转闸不再传播（B-08／T2-06 §怎么做.4②）", row.Status)
			return nil
		}
		out.Marked = true
		statusBefore = row.Status
		ids, err := st.ListEvidenceIDsForSource(ctx, p.UserID, row.ID)
		if err != nil {
			return err
		}
		evidenceIDs = ids
		out.EvidenceCount = len(ids)
		return nil
	})
	if err != nil {
		return out, err
	}
	if !out.Marked || len(evidenceIDs) == 0 {
		if out.Marked {
			out.Detail = "登记行已标 deleted，但它没有支撑任何 evidence：账面到此为止"
		}
		return out, nil
	}

	var failures []error
	for _, evidenceID := range evidenceIDs {
		res, err := s.PropagateEvidenceInvalidation(ctx, p.UserID, evidenceID)
		if err != nil {
			failures = append(failures, fmt.Errorf("传播 evidence %s: %w", evidenceID, err))
			continue
		}
		out.Invalidated += len(res.Invalidated)
		out.Monitored += len(res.Monitored)
		out.RevalidationRequested += len(res.RevalidationRequested)
		out.ModelVersion = res.ModelVersion
	}
	if len(failures) == 0 {
		out.Detail = fmt.Sprintf("翻转：%d 条 evidence ⇒ 失效 %d／只留痕 %d／请重算 %d",
			out.EvidenceCount, out.Invalidated, out.Monitored, out.RevalidationRequested)
		return out, nil
	}

	// 传播中途失败：把翻转收回，让这条投递回到「还没翻过」。
	joined := errors.Join(failures...)
	rows, restoreErr := s.restoreSourceRecordStatus(ctx, p.UserID, out.SourceRecordID, statusBefore)
	if restoreErr != nil {
		return out, errors.Join(append(failures,
			fmt.Errorf("恢复登记行状态也失败（库里停在 deleted，需手工补）: %w", restoreErr))...)
	}
	out.RolledBack = rows == 1
	out.Detail = fmt.Sprintf(
		"%d 条传播里有 %d 条失败 ⇒ 状态已恢复成 %q 交给阶梯重投",
		out.EvidenceCount, len(failures), statusBefore)
	return out, joined
}

// restoreSourceRecordStatus 是失败恢复那一笔独立事务。
//
// 为什么单独开事务而不是用上面那一笔：上面的翻转**已经提交**了，回滚它只能靠正向改写。
// 谓词里带 `status = 'deleted'` 是为了不许把一条本来就 deleted 的行恢复成 active
// （那等于替另一次删除撤销结论）。
func (s *Service) restoreSourceRecordStatus(ctx context.Context,
	userID, sourceRecordID, statusBefore string) (int64, error) {
	var rows int64
	err := s.db.InTx(ctx, func(ctx context.Context, st Store) error {
		n, err := st.RestoreSourceRecordStatus(ctx, sourceRecordID, userID, statusBefore)
		rows = n
		return err
	})
	if err != nil {
		return 0, err
	}
	return rows, nil
}
