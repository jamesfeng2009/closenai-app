// T4-03 的领域判据闸（工单 §验收：四种 result 各一条、非法 outcome_type 400、
// 跨用户 404、outcomes + outcome_links 同事务、§怎么做.5 的同日幂等）。
//
// 三侧不重叠：入口形状在 httpapi/v3_outcomes_test.go（桩，不发 SQL），
// 列与事务在 store/canonical_outcome_integration_test.go（真 PG）。
// 本文件钉的是**判据本身**：origin 由这里钉死、targets 由这里拼装、同日判定由这里读。
package domain

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

func actionOutcome(actionID string) ActionOutcomeProposal {
	return ActionOutcomeProposal{UserID: owner, ActionID: actionID,
		Type: OutcomeCommitmentCompleted, Result: OutcomeResultBetter,
		Note: "这周真的去了三次", OperationID: "op-1"}
}

// ── 1. 提案的钉子：origin、targets、身份 ─────────────────────────────────────

func TestRecordActionOutcomePinsOriginTargetAndVersion(t *testing.T) {
	f := goalStore()
	s, _ := newTestService(f)
	rec, err := s.RecordActionOutcome(context.Background(), actionOutcome("act-1"))
	if err != nil {
		t.Fatalf("RecordActionOutcome: %v", err)
	}
	if len(f.outcomes) != 1 {
		t.Fatalf("outcomes 写入 = %d 行，期望 1：%+v", len(f.outcomes), f.outcomes)
	}
	row := f.outcomes[0]
	// origin 不是客户端给的：它决定了 status 与 ++版本（O-5），
	// 允许请求体自报就等于让非权威侧决定「这条算不算模型相信的事」。
	if row.Origin != string(OriginUserStatement) {
		t.Errorf("origin = %q，期望逐字 user_statement", row.Origin)
	}
	if row.Status != string(OutcomeStatusRecorded) || row.OutcomeType !=
		string(OutcomeCommitmentCompleted) || row.Result != string(OutcomeResultBetter) {
		t.Errorf("行形状 = %q/%q/%q", row.Status, row.OutcomeType, row.Result)
	}
	if row.Description != "这周真的去了三次" {
		t.Errorf("description = %q，备注没有活着走到行上", row.Description)
	}
	// §验收 逐字：outcomes + outcome_links **两行同事务**。
	if len(f.outcomeLinks) != 1 ||
		f.outcomeLinks[0] != rec.OutcomeID+":"+owner+":commitment:act-1" {
		t.Errorf("outcome_links = %v，期望恰好一条 commitment 回链", f.outcomeLinks)
	}
	// recorded ⇒ ++版本并留一行版本账本（与 RecordOutcome 同一判据，不是第二份）。
	if f.modelVer != 8 || len(f.versions) != 1 ||
		f.versions[0].Reason != "outcome_recorded" {
		t.Errorf("版本账本 = %+v（modelVer=%d），期望 recorded 顶一格", f.versions, f.modelVer)
	}
	if rec.ModelVersion != 8 || !rec.Changed || rec.ActionID != "act-1" ||
		rec.OperationID != "op-1" {
		t.Errorf("回执 = %+v", rec)
	}
}

func TestRecordActionOutcomeRejectsBlankIdentityAndTarget(t *testing.T) {
	for _, tc := range []struct{ name, user, action string }{
		{"缺 user_id", "", "act-1"},
		{"缺 action_id（path 空段）", owner, "  "},
	} {
		f := goalStore()
		s, _ := newTestService(f)
		p := actionOutcome(tc.action)
		p.UserID = tc.user
		_, err := s.RecordActionOutcome(context.Background(), p)
		if !errors.Is(err, ErrInvalidOutcomeProposal) {
			t.Errorf("%s：err = %v，期望 ErrInvalidOutcomeProposal ⇒ handler 才能折 400",
				tc.name, err)
		}
		if len(f.outcomes) != 0 || len(f.outcomeLinks) != 0 {
			t.Errorf("%s：校验失败却留下了行 %+v/%+v", tc.name, f.outcomes, f.outcomeLinks)
		}
	}
}

// ── 2. result 与 outcome_type 的词表面（§验收 前两格）────────────────────────

func TestRecordActionOutcomeAcceptsEveryResultAndDefaultsToUnknown(t *testing.T) {
	// 工单 §怎么做.4 的原话是「result 四值必须全支持——不许只留正向」。
	// 这一面的真身是 02:182-189 的六值（same=没变化、worse=变差、unknown=说不清），
	// 所以断言取**整个词表**而不是四格：只测四值的话，把第六值加进词表却忘了放开
	// 这条面，这里不会红。
	for _, want := range OutcomeResults {
		f := goalStore()
		s, _ := newTestService(f)
		p := actionOutcome("act-1")
		p.Result = want
		if _, err := s.RecordActionOutcome(context.Background(), p); err != nil {
			t.Errorf("result=%s 被拒：%v（六值都必须收）", want, err)
			continue
		}
		if f.outcomes[0].Result != string(want) {
			t.Errorf("落库 result = %q，期望 %q", f.outcomes[0].Result, want)
		}
	}
	f := goalStore()
	s, _ := newTestService(f)
	p := actionOutcome("act-1")
	p.Result = ""
	if _, err := s.RecordActionOutcome(context.Background(), p); err != nil {
		t.Fatalf("空 result：%v", err)
	}
	// 缺省归一化成 unknown，与 RecordOutcome 同一格（02:182-189），不是 400。
	if f.outcomes[0].Result != string(OutcomeResultUnknown) {
		t.Errorf("空 result 落库 = %q，期望 unknown", f.outcomes[0].Result)
	}
}

func TestRecordActionOutcomeRejectsUnknownVocabulary(t *testing.T) {
	cases := []struct {
		name   string
		mutate func(*ActionOutcomeProposal)
		want   string
	}{
		{"未知 outcome_type", func(p *ActionOutcomeProposal) { p.Type = "astrology_reading" },
			"outcome_type"},
		{"未知 result", func(p *ActionOutcomeProposal) { p.Result = "great" }, "result"},
		{"空 outcome_type", func(p *ActionOutcomeProposal) { p.Type = "" }, "outcome_type"},
	}
	for _, tc := range cases {
		f := goalStore()
		s, _ := newTestService(f)
		p := actionOutcome("act-1")
		tc.mutate(&p)
		_, err := s.RecordActionOutcome(context.Background(), p)
		if !errors.Is(err, ErrInvalidOutcomeProposal) {
			t.Errorf("%s：err = %v，期望包 ErrInvalidOutcomeProposal（HTTP 面按它折 400）",
				tc.name, err)
			continue
		}
		if !strings.Contains(err.Error(), tc.want) {
			t.Errorf("%s：错误文案 %q 没提 %s ⇒ 排查时看不出没过的关", tc.name, err, tc.want)
		}
		if len(f.outcomes) != 0 || f.modelVer != 7 {
			t.Errorf("%s：拒了却还是写了 %+v / 版本 %d", tc.name, f.outcomes, f.modelVer)
		}
	}
}

// ── 3. outcome_type 与对象类型的匹配（§怎么做.3 点名的既有逻辑）───────────────

func TestRecordActionOutcomeRefusesTargetsOfAnotherKind(t *testing.T) {
	// 这一面只 link **这一条行动**（commitment），所以 `OutcomeTargetRequirement`
	// 要求挂 goal/pattern/proactive 的那几类必须在这里被拒——不是悄悄写出一条
	// 「挂在行动上却描述目标」的结果，那一行会让 04 §22 的目标完成度量凭空多分子。
	for _, typ := range []OutcomeType{OutcomeGoalProgress, OutcomeGoalCompleted,
		OutcomePatternConfirmed, OutcomePatternDisconfirmed, OutcomeProactiveHelpful} {
		f := goalStore()
		s, _ := newTestService(f)
		p := actionOutcome("act-1")
		p.Type = typ
		_, err := s.RecordActionOutcome(context.Background(), p)
		if !errors.Is(err, ErrInvalidOutcomeProposal) {
			t.Errorf("%s：err = %v，期望 400 那一格（这条面不 link %s）", typ, err,
				func() string { need, _ := OutcomeTargetRequirement(typ); return string(need) }())
			continue
		}
		if !strings.Contains(err.Error(), "必须 link") {
			t.Errorf("%s：错误文案 %q 没交代缺哪种 link", typ, err)
		}
	}
	// 反面证人：不需要 link 的两类（用户全局自述）在这一面上照样通，
	// 否则「只收 commitment 级」会被误写成「只收 commitment_completed」。
	for _, typ := range []OutcomeType{OutcomeBehaviorChange, OutcomeUserReportedChange,
		OutcomeCommitmentCompleted} {
		f := goalStore()
		s, _ := newTestService(f)
		p := actionOutcome("act-1")
		p.Type = typ
		if _, err := s.RecordActionOutcome(context.Background(), p); err != nil {
			t.Errorf("%s 应被这一面接受：%v", typ, err)
		}
	}
}

func TestRecordActionOutcomeCompletesTheCommitmentItDescribes(t *testing.T) {
	f := goalStore()
	s, _ := newTestService(f)
	p := actionOutcome("act-1")
	p.Type = OutcomeCommitmentCompleted
	rec, err := s.RecordActionOutcome(context.Background(), p)
	if err != nil {
		t.Fatalf("RecordActionOutcome: %v", err)
	}
	if len(f.commitmentDone) != 1 || f.commitmentDone[0] != "act-1" {
		t.Errorf("commitment 完成痕 = %v，期望 ['act-1']（02:824 落到真正使用的那张表）",
			f.commitmentDone)
	}
	if rec.CompletedTarget != "act-1" {
		t.Errorf("回执 CompletedTarget = %q，客户端要按它决定屏上写哪句话", rec.CompletedTarget)
	}
}

// ── 4. 跨用户（§验收：404，且一行都不写）─────────────────────────────────────

func TestRecordActionOutcomeForForeignActionIsOwnershipError(t *testing.T) {
	// act-9 不在 f.refStates 里：ListRefStates 的 UNION 按 user_id 过滤，
	// 别人的那行不会回来 ⇒ verifyTargets 折成 ErrInvalidOwnership。
	f := goalStore()
	s, _ := newTestService(f)
	_, err := s.RecordActionOutcome(context.Background(), actionOutcome("act-9"))
	if !errors.Is(err, ErrInvalidOwnership) {
		t.Fatalf("err = %v，期望 ErrInvalidOwnership（handler 折成 404，与「不存在」同码）", err)
	}
	if len(f.outcomes) != 0 || len(f.outcomeLinks) != 0 || f.modelVer != 7 {
		t.Errorf("越权之后留下了行：%+v / %+v / 版本 %d", f.outcomes, f.outcomeLinks, f.modelVer)
	}
}

// ── 5. 同日幂等（§怎么做.5）──────────────────────────────────────────────────

func TestRecordActionOutcomeSameDaySubmitReturnsFirstResult(t *testing.T) {
	f := goalStore()
	s, _ := newTestService(f)
	first, err := s.RecordActionOutcome(context.Background(), actionOutcome("act-1"))
	if err != nil {
		t.Fatalf("第一次： %v", err)
	}
	// 显式桩＝「今天已经有一条同型结果」。这里不复刻 outcomes 的扫描逻辑
	// （那是真库集成测的活），只喂判据的输入。
	f.latestOutcome = &LatestOutcome{ID: first.OutcomeID,
		OutcomeType: string(OutcomeCommitmentCompleted), Status: first.Status,
		Result: string(OutcomeResultBetter), ModelVersion: first.ModelVersion,
		CreatedAt: windowEnd, Found: true}
	second, err := s.RecordActionOutcome(context.Background(), actionOutcome("act-1"))
	if err != nil {
		t.Fatalf("第二次： %v", err)
	}
	if second.Changed {
		t.Errorf("重复提交 Changed=true，期望 false：%+v", second)
	}
	if second.OutcomeID != first.OutcomeID || second.Result != first.Result ||
		second.Status != first.Status || second.ModelVersion != first.ModelVersion {
		t.Errorf("第二次回的不是首次结果：%+v vs %+v", second, first)
	}
	if second.Detail == "" {
		t.Error("Changed=false 却没有 Detail ⇒ 客户端只能猜为什么没写")
	}
	if len(f.outcomes) != 1 || len(f.outcomeLinks) != 1 || f.modelVer != 8 {
		t.Errorf("同日重复写出了第二行/第二格版本：%d/%d/版本 %d",
			len(f.outcomes), len(f.outcomeLinks), f.modelVer)
	}
}

func TestRecordActionOutcomeNextDayWritesASecondRow(t *testing.T) {
	// 04 §22 的完成度量按天记账：**跨日**的同型结果必须是第二条，
	// 否则同日幂等那一格会被写成「这一条行动一辈子只能有一次结果」。
	f := goalStore()
	s, _ := newTestService(f)
	f.latestOutcome = &LatestOutcome{ID: "o-old",
		OutcomeType: string(OutcomeCommitmentCompleted), Status: "recorded",
		Result: string(OutcomeResultBetter), ModelVersion: 7,
		CreatedAt: windowEnd.AddDate(0, 0, -1), Found: true}
	rec, err := s.RecordActionOutcome(context.Background(), actionOutcome("act-1"))
	if err != nil {
		t.Fatalf("RecordActionOutcome: %v", err)
	}
	if !rec.Changed || len(f.outcomes) != 1 {
		t.Errorf("跨日重复应当写第二行：Changed=%v 行数=%d", rec.Changed, len(f.outcomes))
	}
}

func TestRecordActionOutcomeKeepsOccurredAtAndOperationID(t *testing.T) {
	f := goalStore()
	s, _ := newTestService(f)
	p := actionOutcome("act-1")
	p.OccurredAt = time.Date(2026, 10, 11, 2, 3, 4, 0, time.FixedZone("UTC+8", 8*3600))
	rec, err := s.RecordActionOutcome(context.Background(), p)
	if err != nil {
		t.Fatalf("RecordActionOutcome: %v", err)
	}
	row := f.outcomes[0]
	if !row.HasOccurredAt || !row.OccurredAt.Equal(p.OccurredAt) {
		t.Errorf("occurred_at = %v（has=%v），期望原样带过去（%v）",
			row.OccurredAt, row.HasOccurredAt, p.OccurredAt)
	}
	if !row.OccurredAt.Equal(p.OccurredAt.UTC()) {
		t.Errorf("occurred_at 折成 UTC 后变了值：%v vs %v", row.OccurredAt, p.OccurredAt)
	}
	if rec.OperationID != "op-1" {
		t.Errorf("回执 operation_id = %q，客户端没法把重试对上自己那次点击", rec.OperationID)
	}
}

func TestSameUTCDateComparesDaysNotInstants(t *testing.T) {
	morning := time.Date(2026, 10, 12, 0, 5, 0, 0, time.UTC)
	night := time.Date(2026, 10, 12, 23, 55, 0, 0, time.UTC)
	nextDay := time.Date(2026, 10, 13, 0, 1, 0, 0, time.UTC)
	// 东八区的 10-13 凌晨 = UTC 的 10-12 傍晚：**本地日历跨了日，UTC 没跨**。
	eveningInPlus8 := time.Date(2026, 10, 13, 0, 30, 0, 0, time.FixedZone("UTC+8", 8*3600))
	if !sameUTCDate(morning, night) {
		t.Error("同一天相隔 23 小时被判成不同日 ⇒ 一次连点会写两条")
	}
	if sameUTCDate(night, nextDay) {
		t.Error("跨日被判成同日 ⇒ 明天的第二次观测会被吞掉")
	}
	if !sameUTCDate(morning, eveningInPlus8) {
		t.Error("判据读到了写入方时区的日历日，期望只认 UTC（否则同一次连点按客户端时区各算一天）")
	}
}

func stringsContain(hay, needle string) bool {
	return len(hay) >= len(needle) && (hay == needle ||
		len(needle) == 0 || indexOf(hay, needle) >= 0)
}

func indexOf(hay, needle string) int {
	for i := 0; i+len(needle) <= len(hay); i++ {
		if hay[i:i+len(needle)] == needle {
			return i
		}
	}
	return -1
}
