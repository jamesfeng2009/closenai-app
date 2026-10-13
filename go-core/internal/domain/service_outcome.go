package domain

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
)

// N6 的三条领域入口：outcome 记录（含向 pattern/insight/goal 的传播）、用户表态、
// 源证据撤回后的失效传播。
//
// 与 N3/N4/N5 同一分层立场（01 §22 不变量 7）：这三条是 canonical 态的唯一写入口，
// HTTP handler 与 MQ consumer 只能经它们；「Python 提案 → Go 判定 → PG 记账」里
// 的判定环节在 outcome 这一维落到本文件。
//
// 数值面全部来自 Policy.Outcome（默认值 env 可覆盖，标定前不硬编码结论），
// 词表与转移合法性全部来自 outcome.go，本文件不出现第二个状态字符串表。

// ErrInvalidOwnership 是提案指向了不属于该用户的行（不变量 9 的跨用户面：
// 拒绝跨用户 id 引用）。
var ErrInvalidOwnership = errors.New("domain: target not owned by user")

// ErrDuplicateFeedback 是同一用户对同一行的**最后一次**表态又被原样提交（07 §6）。
// 判定用「最近一条是否同型」而不是「历史上出现过」：先 no、后 yes、再 no 是新的信息。
var ErrDuplicateFeedback = errors.New("domain: duplicate feedback")

// ErrPatternNotFound 是该用户名下查不到这条 pattern。
var ErrPatternNotFound = errors.New("domain: pattern not found")

// ErrFactNotFound 是该用户名下查不到这条 fact。归属核验在领域层做过一次
// （verifyTargets），改写时再查不到只可能是并发删除，不能当成功。
var ErrFactNotFound = errors.New("domain: fact not found")

// ErrOutcomeNotFound 是该用户名下查不到这条 outcome，或它的状态已不允许改写
// （MarkOutcomeStatus 的 SQL 守卫只放行 recorded/candidate 起算）。
var ErrOutcomeNotFound = errors.New("domain: outcome not found")

// RecordOutcome 记一条 outcome 并对权威结论做最小传播。
//
// 三个关键口径（决策出处 closenai-prd/V3方案裁决与分层-2026-10-12.md 七-5）：
//   - origin 决定 status（O-1/O-5）：非权威来源（llm_inference）**不可能**写出
//     recorded，且 candidate outcome **一律不传播**——LLM 的推断不是模型的既成事实，
//     让它去削 pattern 分数等于把不变量 7 反过来写；
//   - recorded 才 ++版本（O-5）：reason='outcome_recorded'；candidate 只写事件；
//   - 传播只降不删（O-4）：单个反例不清除历史证据，evidence_count 与回链一律不动。
func (s *Service) RecordOutcome(ctx context.Context, p OutcomeProposal) (OutcomeReceipt, error) {
	if p.Result == "" {
		p.Result = OutcomeResultUnknown
	}
	if err := checkOutcomeProposal(p); err != nil {
		return OutcomeReceipt{}, err
	}
	receipt := OutcomeReceipt{}
	err := s.db.InTx(ctx, func(ctx context.Context, st Store) error {
		modelID, version, err := st.EnsurePersonalModel(ctx, p.UserID)
		if err != nil {
			return err
		}
		states, err := s.verifyTargets(ctx, st, p.UserID, p.Targets)
		if err != nil {
			return err
		}
		status := OutcomeStatusForOrigin(p.Origin)
		written := version
		if status == OutcomeStatusRecorded {
			// 权威结论＝模型内容变了（02:1069），先 ++ 再落行，行上带的就是新版本。
			newVersion, err := st.BumpVersion(ctx, modelID, version)
			if err != nil {
				return err
			}
			written = newVersion
			if err := st.InsertVersion(ctx, VersionRow{ID: s.newID(), UserID: p.UserID,
				PersonalModelID: modelID, Version: newVersion,
				Reason: "outcome_recorded"}); err != nil {
				return err
			}
		}
		id := s.newID()
		row := OutcomeRow{ID: id, UserID: p.UserID, OutcomeType: string(p.Type),
			Status: string(status), Result: string(p.Result), Origin: string(p.Origin),
			Description: p.Description,
			OccurredAt:  p.OccurredAt, HasOccurredAt: !p.OccurredAt.IsZero(),
			ModelVersion: written, GenerationRunID: p.GenerationRunID}
		if err := st.InsertOutcome(ctx, row); err != nil {
			return err
		}
		if err := st.LinkOutcomeTargets(ctx, id, p.UserID, p.Targets); err != nil {
			return err
		}
		eventType := "outcome_recorded"
		if status != OutcomeStatusRecorded {
			eventType = "outcome_candidate"
		}
		payload := fmt.Sprintf(`{"outcome_type":%q,"origin":%q,"result":%q,"targets":%d}`,
			p.Type, p.Origin, p.Result, len(p.Targets))
		if err := s.emit(ctx, st, s.outcomeEvent(s.newID(), p.UserID, modelID, eventType,
			"outcomes", id, written, payload, p.GenerationRunID)); err != nil {
			return err
		}
		receipt = OutcomeReceipt{ID: id, Status: string(status), ModelVersion: written,
			Patterns: []PatternChange{}, Revalidated: []string{},
			CancelledProactives: []string{}}
		if status != OutcomeStatusRecorded {
			// 不变量 5/7 的交集：推断只登记，不改变模型相信什么。
			receipt.Detail = "origin 非权威来源：只登记 candidate，不传播、不 ++版本"
			return nil
		}

		// ── 传播一：pattern（04 §16–§17）───────────────────────────────────
		for _, pid := range targetsOfType(p.Targets, RefPattern) {
			change, changed, err := s.applyOutcomeToPattern(ctx, st, p, pid, written)
			if err != nil {
				return err
			}
			if changed {
				receipt.Patterns = append(receipt.Patterns, change)
			}
			requested, err := s.requestInsightRevalidation(ctx, st, p.UserID, pid, version)
			if err != nil {
				return err
			}
			receipt.Revalidated = append(receipt.Revalidated, requested...)
		}

		// ── 传播二：goal / commitment 完成（02:824 落到真正使用的那张表）────
		completed, err := s.completeTarget(ctx, st, p, states, written, &receipt)
		if err != nil {
			return err
		}
		receipt.CompletedTarget = completed
		return nil
	})
	if err != nil {
		return OutcomeReceipt{}, err
	}
	return receipt, nil
}

// applyOutcomeToPattern 是 O-4 的执行体：反例只削 confidence，
// 削穿 D 级下界才走 weakened 状态；确认升档只在显式打开时发生。
func (s *Service) applyOutcomeToPattern(ctx context.Context, st Store, p OutcomeProposal,
	patternID string, version int64) (PatternChange, bool, error) {
	state, err := st.GetPatternState(ctx, p.UserID, patternID)
	if err != nil {
		return PatternChange{}, false, err
	}
	op := s.gate.Policy.Outcome
	conf, changed := op.DisconfirmedConfidence(state.Confidence, state.HasConfidence)
	if OutcomeDisconfirming(p.Type) && !changed {
		// 没有置信度可降：NULL 不是 0，把未知削成更低的未知是假动作。
		return PatternChange{PatternID: state.ID, From: state.Status, To: state.Status},
			false, nil
	}
	action := PatternWeaken
	if !OutcomeDisconfirming(p.Type) {
		if !op.AutoConfirmFromOutcome || p.Type != OutcomePatternConfirmed {
			return PatternChange{PatternID: state.ID, From: state.Status, To: state.Status},
				false, nil
		}
		action = PatternConfirm
	}
	to, legal := NextPatternStatus(state.Status, action)
	if OutcomeDisconfirming(p.Type) && changed &&
		conf >= s.gate.Policy.EvidenceStrengthFloorForD {
		// 还在 D 级之上：只降分数，状态不动（O-4「否则状态不变」）。
		to, legal = state.Status, true
	}
	if !legal {
		return PatternChange{PatternID: state.ID, From: state.Status, To: state.Status},
			false, nil
	}
	reason := ""
	if action == PatternWeaken && to == PatternWeakened {
		reason = ReasonEvidenceWithdrawn.String()
	}
	row := PatternLifecycleRow{ID: state.ID, UserID: p.UserID, Status: string(to),
		Confidence: conf, HasConfidence: changed, EvidenceCount: state.EvidenceCount,
		ScoreDetail:  s.patternScoreDetail(p, state, conf, changed),
		ConfirmAt:    action == PatternConfirm && to == PatternConfirmed,
		RejectReason: reason, ModelVersion: version}
	if err := st.UpdatePatternLifecycle(ctx, row); err != nil {
		return PatternChange{}, false, err
	}
	if err := s.emit(ctx, st, s.outcomeEvent(s.newID(), p.UserID, state.PersonalModelID,
		"pattern_lifecycle", "patterns", state.ID, version,
		fmt.Sprintf(`{"from":%q,"to":%q,"action":%q,"outcome_type":%q,"confidence":%.4f}`,
			state.Status, to, action, p.Type, conf), p.GenerationRunID)); err != nil {
		return PatternChange{}, false, err
	}
	return PatternChange{PatternID: state.ID, From: state.Status, To: to,
		Confidence: conf, HasConf: changed, Action: action}, true, nil
}

// patternScoreDetail 把「这一格分数是哪来的」写进 score_detail：04 §22 复盘和
// N9 读路径都要能回答「为什么这条从 0.72 变成 0.648」，只有一个数字不算留痕。
func (s *Service) patternScoreDetail(p OutcomeProposal, state PatternState,
	conf float64, changed bool) string {
	out := map[string]any{"outcome_type": string(p.Type), "from_confidence": state.Confidence,
		"has_from": state.HasConfidence, "confidence": conf, "applied": changed,
		"step": s.gate.Policy.Outcome.DisconfirmStep, "outcome_id": ""}
	encoded, err := json.Marshal(out)
	if err != nil {
		// 留痕序列化失败不该让一次状态改写回滚——Detail 是可解释性的加分项，
		// 权威性在 status/confidence 两列本身。
		return ""
	}
	return string(encoded)
}

// completeTarget 把 goal/commitment 的完成落到既有表，并取消仍指着它的排队触达
// （01 §20 示例「dependent proactive cancelled」：目标已完成，提醒就没有理由存在）。
func (s *Service) completeTarget(ctx context.Context, st Store, p OutcomeProposal,
	states map[string]RefState, version int64, receipt *OutcomeReceipt) (string, error) {
	if !OutcomeCompletesTarget(p.Type) {
		return "", nil
	}
	want := RefGoal
	if p.Type == OutcomeCommitmentCompleted {
		want = RefCommitment
	}
	for _, id := range targetsOfType(p.Targets, want) {
		if ref, ok := states[string(want)+":"+id]; ok && ref.Status == "completed" {
			// 已完成的目标不二次改写：CompleteGoal 会无条件刷 completed_at，
			// 二次写入会把完成时刻推到这次 outcome 上，历史就假了。
			continue
		}
		if want == RefGoal {
			if err := st.CompleteGoal(ctx, p.UserID, id); err != nil {
				return "", err
			}
		} else if err := st.CompleteCommitment(ctx, p.UserID, id); err != nil {
			return "", err
		}
		receipt.CompletedTarget = id
		cancelled, err := s.cancelQueuedFor(ctx, st, p.UserID, id, version, p.GenerationRunID)
		if err != nil {
			return "", err
		}
		receipt.CancelledProactives = append(receipt.CancelledProactives, cancelled...)
		if err := s.emit(ctx, st, s.outcomeEvent(s.newID(), p.UserID, "", "target_completed",
			string(want)+"s", id, version,
			fmt.Sprintf(`{"outcome_type":%q,"cancelled_proactives":%d}`, p.Type, len(cancelled)),
			p.GenerationRunID)); err != nil {
			return "", err
		}
	}
	return receipt.CompletedTarget, nil
}

// cancelQueuedFor 取消 packet 引用清单里指着这条目标的排队触达。
//
// 归属判定读 eligibility_detail 的 packet.ref_ids（N5 落的可复现输入），不在 SQL 里
// 拆 JSON——store 只搬类型不做决策。改写走 N5 的 SetProactiveStatus，终态守卫由 SQL 兜。
func (s *Service) cancelQueuedFor(ctx context.Context, st Store, userID, targetID string,
	version int64, generationRunID string) ([]string, error) {
	rows, err := st.ListQueuedProactives(ctx, userID)
	if err != nil {
		return nil, err
	}
	cancelled := []string{}
	for _, row := range rows {
		var packet EligibilityPacket
		if row.Detail != "" {
			if err := json.Unmarshal([]byte(row.Detail), &packet); err != nil {
				// 读不出引用清单就不知道它是否指着这条目标。这里**不取消**：
				// 沉默地撤掉一封可能无关的触达，比漏撤一封的代价高（04 §12 的取消
				// 条件要求「显式依赖」可核实）。
				continue
			}
		}
		if !containsOne(packet.RefIDs, targetID) {
			continue
		}
		detail, err := json.Marshal(map[string]any{"cancelled_because": "target_completed",
			"target_id": targetID, "outcome_model_version": version})
		if err != nil {
			return nil, fmt.Errorf("domain: encode cancellation detail: %w", err)
		}
		if err := st.SetProactiveStatus(ctx, ProactiveStatusRow{UserID: userID, ID: row.ID,
			Status: string(ProactiveStatusCancelled), BlockReason: ReasonStaleTrigger.String(),
			Detail: string(detail)}); err != nil {
			if errors.Is(err, ErrProactiveTerminal) {
				// 已被并发投递/过期：不是错误，取消目标达成。
				continue
			}
			return nil, err
		}
		cancelled = append(cancelled, row.ID)
		if err := s.emit(ctx, st, s.outcomeEvent(s.newID(), userID, "", "proactive_cancelled",
			"proactives", row.ID, version, string(detail), generationRunID)); err != nil {
			return nil, err
		}
	}
	return cancelled, nil
}

// RecordUserFeedback 记一次用户表态：**必然**留痕（不变量 6），改写只在合法时发生。
//
// 口径：
//   - 重复判定用「该目标的最近一条是否同型」（LatestFeedbackForTarget），表上刻意
//     没有唯一约束——历史要留，但同一个按钮连点两次不产生第二条权威记录；
//   - pattern 的改写一律经 PatternActionForFeedback + NextPatternStatus（07 §3
//     「生命周期转移是领域行为」），不存在第二张「feedback→状态字符串」的直写表；
//   - 状态不允许改写时 Changed=false：留痕与事件照写，绝不因为「改不动」而丢掉用户说过什么。
func (s *Service) RecordUserFeedback(ctx context.Context,
	f FeedbackProposal) (FeedbackReceipt, error) {
	if err := checkFeedbackProposal(f); err != nil {
		return FeedbackReceipt{}, err
	}
	receipt := FeedbackReceipt{}
	err := s.db.InTx(ctx, func(ctx context.Context, st Store) error {
		modelID, version, err := st.EnsurePersonalModel(ctx, f.UserID)
		if err != nil {
			return err
		}
		states, err := s.verifyTargets(ctx, st, f.UserID,
			[]TargetRef{{Type: f.TargetType, ID: f.TargetID}})
		if err != nil {
			return err
		}
		current := states[string(f.TargetType)+":"+f.TargetID]

		latest, err := st.LatestFeedbackForTarget(ctx, f.UserID, f.TargetType, f.TargetID)
		if err != nil {
			return err
		}
		if latest.Found && latest.FeedbackType == string(f.Type) {
			return fmt.Errorf("%w: target=%s type=%s of=%s", ErrDuplicateFeedback,
				f.TargetID, f.Type, latest.ID)
		}

		metadata, err := feedbackMetadata(f, current.Status)
		if err != nil {
			return err
		}
		id := s.newID()
		if err := st.InsertFeedback(ctx, FeedbackRow{ID: id, UserID: f.UserID,
			FeedbackType: string(f.Type), TargetType: string(f.TargetType),
			TargetID: f.TargetID, CorrectionText: f.CorrectionText, Metadata: metadata}); err != nil {
			return err
		}
		receipt = FeedbackReceipt{ID: id, TargetStatus: current.Status, ModelVersion: version}

		written, status, reason, changed, err := s.applyFeedback(ctx, st, modelID, f, current, version)
		if err != nil {
			return err
		}
		receipt.Changed, receipt.TargetStatus, receipt.ModelVersion = changed, status, written
		eventPayload := fmt.Sprintf(
			`{"feedback_type":%q,"target_type":%q,"target_id":%q,"changed":%t,"status":%q}`,
			f.Type, f.TargetType, f.TargetID, changed, receipt.TargetStatus)
		if err := s.emit(ctx, st, s.outcomeEvent(s.newID(), f.UserID, modelID, "user_feedback_recorded",
			string(f.TargetType)+"s", f.TargetID, receipt.ModelVersion,
			eventPayload, "")); err != nil {
			return err
		}
		if reason != "" {
			receipt.Detail = reason
		}
		return nil
	})
	if err != nil {
		return FeedbackReceipt{}, err
	}
	return receipt, nil
}

// applyFeedback 按 O-9 矩阵改写目标状态，返回改写后的版本号、新状态与说明。
// 「不动」不是一种失败：它是一次有记录的判定（Changed=false + Detail）。
func (s *Service) applyFeedback(ctx context.Context, st Store, modelID string,
	f FeedbackProposal, current RefState, version int64) (int64, string, string, bool, error) {
	if f.Type == FeedbackPartiallyConfirm {
		return version, current.Status, "部分认可不改写状态（policy.go:86 同口径）", false, nil
	}
	switch f.TargetType {
	case RefPattern:
		action, ok := PatternActionForFeedback(f.Type)
		if !ok {
			return version, current.Status, "这种表态不对应 pattern 的生命周期动作", false, nil
		}
		to, legal := NextPatternStatus(PatternStatus(current.Status), action)
		if !legal {
			return version, current.Status,
				fmt.Sprintf("状态 %s 不接受 %s 转移（终态或已在目标态）", current.Status, action),
				false, nil
		}
		state, err := st.GetPatternState(ctx, f.UserID, f.TargetID)
		if err != nil {
			return version, current.Status, "", false, err
		}
		newVersion, err := st.BumpVersion(ctx, modelID, version)
		if err != nil {
			return version, current.Status, "", false, err
		}
		if err := st.InsertVersion(ctx, VersionRow{ID: s.newID(), UserID: f.UserID,
			PersonalModelID: modelID, Version: newVersion,
			Reason: versionReason(f.TargetType, string(to), f.Type)}); err != nil {
			return version, current.Status, "", false, err
		}
		// 只降不删的另一半：用户表态**不碰** confidence 与 evidence_count——
		// 分数是 N4 算分的产物，由证据决定；把它交给一句「我不同意」会让分数
		// 变成第二个状态列（01:677「model invalidation ≠ data deletion」同一立场）。
		row := PatternLifecycleRow{ID: state.ID, UserID: f.UserID, Status: string(to),
			Confidence: state.Confidence, HasConfidence: state.HasConfidence,
			EvidenceCount: state.EvidenceCount, ScoreDetail: state.ScoreDetail,
			ConfirmAt: action == PatternConfirm, RejectAt: action == PatternReject,
			ExpireAt:     action == PatternMarkExpired,
			ModelVersion: newVersion}
		if err := st.UpdatePatternLifecycle(ctx, row); err != nil {
			return version, current.Status, "", false, err
		}
		return newVersion, string(to), "", true, nil
	case RefFact, RefInsight, RefOutcome:
		status, ok := StatusForDerivedTarget(f.TargetType, f.Type)
		if !ok {
			return version, current.Status, "这种表态不改写该类目标的状态", false, nil
		}
		if f.TargetType == RefOutcome && !outcomeWritable(current.Status) {
			return version, current.Status,
				fmt.Sprintf("outcome 已是 %s，二次纠正只留痕（01 §13 不覆盖已落定的历史）",
					current.Status), false, nil
		}
		newVersion, err := st.BumpVersion(ctx, modelID, version)
		if err != nil {
			return version, current.Status, "", false, err
		}
		if err := st.InsertVersion(ctx, VersionRow{ID: s.newID(), UserID: f.UserID,
			PersonalModelID: modelID, Version: newVersion,
			Reason: versionReason(f.TargetType, status, f.Type)}); err != nil {
			return version, current.Status, "", false, err
		}
		switch f.TargetType {
		case RefFact:
			if err := st.UpdateFactStatus(ctx, FactStatusRow{ID: f.TargetID, UserID: f.UserID,
				Status: status, InvalidateAt: status != "active",
				ModelVersion: newVersion}); err != nil {
				return version, current.Status, "", false, err
			}
		case RefInsight:
			if err := st.SetInsightStatus(ctx, InsightStatusRow{UserID: f.UserID, ID: f.TargetID,
				Status: status, ModelVersion: newVersion,
				GateDetail: fmt.Sprintf(`{"by_user_feedback":%q}`, f.Type)}); err != nil {
				return version, current.Status, "", false, err
			}
		case RefOutcome:
			if err := st.MarkOutcomeStatus(ctx, f.UserID, f.TargetID,
				OutcomeStatus(status)); err != nil {
				return version, current.Status, "", false, err
			}
		}
		return newVersion, status, "", true, nil
	default:
		// goal/commitment/proactive：完成与发送是**动作**的结果，不是表态的结果
		// （不变量 5：沉默与口头都不构成完成）。留痕即目的。
		return version, current.Status,
			fmt.Sprintf("%s 的状态不由表态改写", f.TargetType), false, nil
	}
}

// versionReason 给 personal_model_versions.reason 一个可读码，词表口径取
// 02:255-267 的示例（fact_confirmed / fact_corrected / pattern_confirmed /
// pattern_rejected / outcome_recorded），其余按 `<目标>_<新状态>` 同形派生。
// 为什么必须写这一行：02 §31 的版本表是「谁在什么时候因为什么变了」的唯一账本，
// 只 ++ 计数不记 reason 的版本号无法用于回滚与陈旧检测（不变量 8 的审计面）。
func versionReason(target RefType, next string, f FeedbackType) string {
	if target == RefFact && f == FeedbackCorrect {
		// 逐字对齐 02:260 的示例词。
		return "fact_corrected"
	}
	if target == RefOutcome && f == FeedbackCorrect {
		return "outcome_corrected"
	}
	return string(target) + "_" + next
}

func outcomeWritable(status string) bool {
	return status == string(OutcomeStatusRecorded) || status == string(OutcomeStatusCandidate)
}

// PropagateEvidenceInvalidation 是一条 source/evidence 被撤回后的传播入口
// （01 §22 不变量 9：源证据失效必须按 policy 让依赖它的派生 claim 失效）。
//
// 「policy」在这里的具体形式（七-5 O-6，含本批勘误）：
//   - **仅依赖它**（live_support=0 且曾依赖）的派生行：走真实的失效路径——
//     insight 复用 N4 的重算（零支撑必然跌破 C 级下界 ⇒ retracted），
//     pattern 走 weakened 转移，fact 置 invalidated，三者都带 evidence_withdrawn 码；
//     把「状态不动、只留痕」当成失效路径会让一条用户可见的 claim 站在零证据上，
//     那是不变量 9 的字面违反，所以文档里那半句按本条改判。
//   - 还有其他活支撑的行：只留痕（不动状态、不 ++），是否变弱由 N4 的分数说话。
//   - 全程**不删任何行**（O-7：0 处 CASCADE，删除只归 GDPR purge 的逐表 rowcount）。
func (s *Service) PropagateEvidenceInvalidation(ctx context.Context,
	userID, evidenceID string) (PropagationResult, error) {
	if userID == "" || evidenceID == "" {
		return PropagationResult{}, errors.New("domain: propagate 需要 user_id 与 evidence_id")
	}
	result := PropagationResult{EvidenceID: evidenceID, Invalidated: []DerivedChange{},
		Monitored: []string{}, RevalidationRequested: []string{}}
	err := s.db.InTx(ctx, func(ctx context.Context, st Store) error {
		modelID, version, err := st.EnsurePersonalModel(ctx, userID)
		if err != nil {
			return err
		}
		rows, err := st.ListDerivedSupportForEvidence(ctx, userID, evidenceID)
		if err != nil {
			return err
		}
		result.ModelVersion = version
		written := version
		bumped := false
		for _, row := range rows {
			if !row.OnlyDependent() {
				result.Monitored = append(result.Monitored, row.ID)
				continue
			}
			if row.Kind == KindInsight {
				// insight 的失效结论由 N4 的重算给（同一事务里改会撞它的乐观锁）。
				if err := s.emit(ctx, st, s.outcomeEvent(s.newID(), userID, modelID,
					"insight_revalidation_requested", "insights", row.ID, written,
					fmt.Sprintf(`{"evidence_id":%q,"current_status":%q}`, evidenceID, row.Status),
					"")); err != nil {
					return err
				}
				result.RevalidationRequested = append(result.RevalidationRequested, row.ID)
				continue
			}
			if !bumped {
				newVersion, err := st.BumpVersion(ctx, modelID, version)
				if err != nil {
					return err
				}
				written, bumped = newVersion, true
				if err := st.InsertVersion(ctx, VersionRow{ID: s.newID(), UserID: userID,
					PersonalModelID: modelID, Version: newVersion,
					Reason: ReasonEvidenceWithdrawn.String()}); err != nil {
					return err
				}
			}
			change, err := s.invalidateDerived(ctx, st, userID, modelID, evidenceID, row, written)
			if err != nil {
				return err
			}
			result.Invalidated = append(result.Invalidated, change)
		}
		result.ModelVersion = written
		if !bumped {
			if len(result.RevalidationRequested) == 0 {
				result.Detail = "没有「仅依赖这条证据」的派生行：不动状态、不 ++版本"
			} else {
				result.Detail = fmt.Sprintf(
					"只有 insight 需要重算（%d 条）：状态改写交给 N4 的 ReevaluateInsight，"+
						"本事务只留请求事件、不 ++版本", len(result.RevalidationRequested))
			}
			return nil
		}
		return s.emit(ctx, st, s.outcomeEvent(s.newID(), userID, modelID, "evidence_withdrawn",
			"evidence", evidenceID, written,
			fmt.Sprintf(`{"invalidated":%d,"monitored":%d,"revalidation_requested":%d}`,
				len(result.Invalidated), len(result.Monitored),
				len(result.RevalidationRequested)), ""))
	})
	if err != nil {
		return PropagationResult{}, err
	}
	return result, nil
}

// invalidateDerived 处理「仅依赖这条证据」的一行。返回实际改写后的状态（可能等于
// 原状态——非法转移或不可重算的态只留痕，绝不越权改写）。
func (s *Service) invalidateDerived(ctx context.Context, st Store, userID, modelID,
	evidenceID string, row DerivedSupport, version int64) (DerivedChange, error) {
	change := DerivedChange{Kind: row.Kind, ID: row.ID, From: row.Status, To: row.Status,
		OnlySupport: true}
	switch row.Kind {
	case KindInsight:
		// 不在这里改状态：见 requestInsightRevalidation 的根因说明。
		change.Reason = "revalidation_requested"
		return change, nil
	case KindPattern:
		state, err := st.GetPatternState(ctx, userID, row.ID)
		if err != nil {
			return change, err
		}
		to, legal := NextPatternStatus(state.Status, PatternWeaken)
		if !legal {
			change.Reason = "状态不允许 weakened 转移"
			return change, nil
		}
		reason := ReasonEvidenceWithdrawn.String()
		row := PatternLifecycleRow{ID: state.ID, UserID: userID, Status: string(to),
			Confidence: state.Confidence, HasConfidence: state.HasConfidence,
			EvidenceCount: state.EvidenceCount, ScoreDetail: state.ScoreDetail,
			RejectReason: reason, ModelVersion: version}
		if err := st.UpdatePatternLifecycle(ctx, row); err != nil {
			return change, err
		}
		change.To = string(to)
		change.Reason = reason
		return change, nil
	case KindFact:
		status := "invalidated"
		if err := st.UpdateFactStatus(ctx, FactStatusRow{ID: row.ID, UserID: userID,
			Status: status, InvalidateAt: true, RejectReason: ReasonEvidenceWithdrawn.String(),
			ModelVersion: version}); err != nil {
			return change, err
		}
		change.To, change.Reason = status, ReasonEvidenceWithdrawn.String()
		return change, nil
	default:
		return change, fmt.Errorf("domain: 未知的派生类型 %q", row.Kind)
	}
}

// requestInsightRevalidation 给该 pattern 支撑的每条 insight 留一条重校验请求事件
// （04 §17「触发受影响 Insight 重校验」的落法：触发，而不是在本事务里改写）。
//
// 为什么不直接复用 reevaluateWithin（2026-10-13 实测根因 + 同日勘误）：起初挡住它的是
// N4 重算入口的一个机械错误——它把**insight 行自己的 model_version** 当乐观锁期望值
// （`BumpVersion(state.PersonalModelID, state.ModelVersion)`），同一事务里 outcome 已经
// ++ 过一次，期望值必然落后 ⇒ ErrVersionConflict ⇒ 整笔回滚，outcome 也记不进去。
// 该期望值已由 N7-14 改为在紧邻 BumpVersion 处取模型行当前版本（七-5.1 第 2 条结案）。
// 分工本身不变：这里仍只留事件，状态改写交给 ReevaluateInsight 的独立入口
// （O 侧消费者 / N7 触发）——04 §17 说的是「触发重校验」，而一笔 outcome 事务同时改
// pattern 与 insight 两种状态会把失败面扩大到整条 outcome 都记不进去。
func (s *Service) requestInsightRevalidation(ctx context.Context, st Store, userID,
	patternID string, version int64) ([]string, error) {
	insights, err := st.ListInsightsForPattern(ctx, userID, patternID)
	if err != nil {
		return nil, err
	}
	requested := make([]string, 0, len(insights))
	for _, ref := range insights {
		if err := s.emit(ctx, st, s.outcomeEvent(s.newID(), userID, "",
			"insight_revalidation_requested", "insights", ref.ID, version,
			fmt.Sprintf(`{"pattern_id":%q,"current_status":%q}`, patternID, ref.Status),
			"")); err != nil {
			return nil, err
		}
		requested = append(requested, ref.ID)
	}
	return requested, nil
}

// verifyTargets 核验每条引用的目标确实存在于该用户名下（02 §18 末句的更严实现：
// 多态引用必须有领域校验）。返回 ref_type:id → 当前库态，供后续分支复用（避免二次读）。
func (s *Service) verifyTargets(ctx context.Context, st Store, userID string,
	targets []TargetRef) (map[string]RefState, error) {
	if len(targets) == 0 {
		return map[string]RefState{}, nil
	}
	ids := make([]string, 0, len(targets))
	for _, t := range targets {
		ids = append(ids, t.ID)
	}
	rows, err := st.ListRefStates(ctx, userID, ids)
	if err != nil {
		return nil, err
	}
	found := make(map[string]RefState, len(rows))
	for _, r := range rows {
		found[string(r.RefType)+":"+r.ID] = r
	}
	for _, t := range targets {
		if _, ok := found[t.String()]; !ok {
			return nil, fmt.Errorf("%w: %s", ErrInvalidOwnership, t)
		}
	}
	return found, nil
}

func (s *Service) outcomeEvent(id, userID, modelID, eventType, aggregateType, aggregateID string,
	version int64, payload, generationRunID string) EventRow {
	return EventRow{ID: id, UserID: userID, PersonalModelID: modelID,
		EventType: eventType, AggregateType: aggregateType, AggregateID: aggregateID,
		ModelVersion: version, Payload: payload, GenerationRunID: generationRunID}
}

// feedbackMetadata 把 04 §14 的响应态原词与调用方附带的键一起存进 metadata_json：
// 状态列用 02 的六值（O-1），但「用户当时是怎么回一条主动消息」这个事实只有原词
// 能表达，N9 读路径和 O 侧的 Trust/Annoyance 度量都要读它。
func feedbackMetadata(f FeedbackProposal, targetStatus string) (string, error) {
	box := map[string]any{"target_status_at_feedback": targetStatus}
	if f.ResponseState != "" {
		box["response_state"] = f.ResponseState
	}
	if len(f.Metadata) > 0 {
		extra := make(map[string]string, len(f.Metadata))
		for k, v := range f.Metadata {
			extra[k] = v
		}
		box["extra"] = extra
	}
	encoded, err := json.Marshal(box)
	if err != nil {
		return "", fmt.Errorf("domain: encode feedback metadata: %w", err)
	}
	return string(encoded), nil
}
