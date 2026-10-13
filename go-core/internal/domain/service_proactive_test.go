// N5 Service 单测：资格门的结论如何变成 proactives 权威行。
//
// fakeStore 不做任何判定（判定在 proactive_gate_test.go 里验），这里只钉四条口径：
//  1. **拒绝也留行**（04 §22 的 Precision/Annoyance 要有分母），且 reject_reason 与
//     block_reason 按 P-9 分工落列；
//  2. Admit **不 ++ 模型版本**（02:1069：一条触达是「模型打算做什么」，不是
//     「模型相信什么」），只写 personal_model_events；
//  3. 发送时刻必须**重读库态**（P-10）：引用行变失效、当日额度被占、safety 变危机，
//     都要在 Recheck 里被拦下，而不是沿用生成时刻的 eligible；
//  4. 任何写失败都要让事务回滚——不能出现「事件写进去了、权威行没写」的半状态。
package domain

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"
)

// proactiveGreenStore 给出一套全绿库态：偏好行在、goal-1 活着且锚点=今天、
// 证据 e1/e2 属于该用户、无历史触达、刚刚说过话。
func proactiveGreenStore() (*fakeStore, *Service) {
	f := newFakeStore()
	f.refStates = []RefState{
		{RefType: RefGoal, ID: "goal-1", Status: "active",
			AnchorAt: windowEnd, HasTime: true},
	}
	s, _ := newTestService(f)
	return f, s
}

func proactiveAdmit(t *testing.T, s *Service) (ProactiveResult, error) {
	t.Helper()
	return s.AdmitProactive(context.Background(), proacCandidate())
}

func TestProactiveService_AdmitEligibleWritesOneRow(t *testing.T) {
	f, s := proactiveGreenStore()
	res, err := proactiveAdmit(t, s)
	if err != nil {
		t.Fatalf("AdmitProactive: %v", err)
	}
	if res.Status != string(ProactiveStatusEligible) || res.Action != ActionProactiveEligible {
		t.Fatalf("结论 = %s/%s（%s），期望 eligible", res.Status, res.Action, res.Detail)
	}
	if len(f.proactives) != 1 {
		t.Fatalf("proactives 行数 = %d，期望 1", len(f.proactives))
	}
	row := f.proactives[0]
	if row.UserID != owner || row.PersonalModelID != "model-1" {
		t.Errorf("归属 = %s/%s，应取库里的实际模型行", row.UserID, row.PersonalModelID)
	}
	if row.TriggerType != string(TriggerMemoryRecall) || row.Message != proacMessage {
		t.Errorf("trigger/message 未原样落库：%s", row.TriggerType)
	}
	if row.RejectReason != "" || row.BlockReason != "" {
		t.Errorf("全绿行不该有拒因：%s / %s", row.RejectReason, row.BlockReason)
	}
	// 有效期：04 §19 的「沉默则 expired」需要一个可判的到期时刻。
	if !row.HasExpiresAt || !row.ExpiresAt.After(windowEnd) {
		t.Errorf("expires_at = %v/%v，应在发送时刻之后", row.ExpiresAt, row.HasExpiresAt)
	}

	packet := EligibilityPacket{}
	if err := json.Unmarshal([]byte(row.EligibilityDetail), &packet); err != nil {
		t.Fatalf("eligibility_detail 不是可解析 JSON：%v", err)
	}
	if strings.Join(packet.Checks, ",") != strings.Join(gateStepNames, ",") {
		t.Errorf("packet.checks = %v，期望落满十一关", packet.Checks)
	}
	if packet.Trigger != string(TriggerMemoryRecall) ||
		packet.Source != string(SourceUserRequestedFollow) {
		t.Errorf("packet 未留触发源：%s/%s", packet.Trigger, packet.Source)
	}
	if packet.Confidence != 0.90 || packet.Timezone != "UTC" || packet.Cadence != "normal" {
		t.Errorf("packet 复现面缺项：%+v", packet)
	}
	if len(packet.Layers) != 3 || packet.Fail != "" || packet.Reason != "" {
		t.Errorf("packet 层数/失败留痕 = %v/%q/%q", packet.Layers, packet.Fail, packet.Reason)
	}
	if strings.Join(packet.RefIDs, ",") != "goal-1" ||
		strings.Join(packet.EvidenceIDs, ",") != "e1" {
		t.Errorf("packet 引用清单 = %v / %v", packet.RefIDs, packet.EvidenceIDs)
	}
	if packet.AnchorRefID != "goal-1" || packet.AnchorAt == "" {
		t.Errorf("packet 锚点 = %q/%q，「关联当下」必须留可核实时刻",
			packet.AnchorRefID, packet.AnchorAt)
	}

	// 版本口径：触达不改「模型相信什么」，所以既不 ++ 也不写版本行。
	if f.modelVer != 0 || len(f.versions) != 0 {
		t.Errorf("modelVer=%d versions=%d，AdmitProactive 不得推进模型版本",
			f.modelVer, len(f.versions))
	}
	if row.ModelVersion != 0 {
		t.Errorf("行上 model_version = %d，期望快照当时的 0", row.ModelVersion)
	}
	if len(f.events) != 1 {
		t.Fatalf("事件行数 = %d，期望 1", len(f.events))
	}
	event := f.events[0]
	if event.EventType != "proactive_eligible" || event.AggregateType != "proactive" ||
		event.AggregateID != row.ID {
		t.Errorf("事件 = %s/%s/%s，留痕口径不符",
			event.EventType, event.AggregateType, event.AggregateID)
	}
}

// 三种失败落三种列归属（P-9）：reject→reject_reason，block→block_reason，
// freshness→cancelled+reject_reason。
func TestProactiveService_AdmitVerdictColumnOwnership(t *testing.T) {
	cases := []struct {
		name       string
		mutate     func(*fakeStore, *ProactiveCandidate)
		wantStatus string
		wantReason string
		inReject   bool
		inBlock    bool
		eventType  string
	}{
		{name: "本身没资格走 reject", mutate: func(_ *fakeStore, c *ProactiveCandidate) {
			c.TriggerType = TriggerType("weather_alert")
		}, wantStatus: string(ProactiveStatusBlocked), wantReason: "trigger_unknown",
			inReject: true, eventType: "proactive_blocked"},
		{name: "此刻不能发走 block", mutate: func(f *fakeStore, _ *ProactiveCandidate) {
			f.prefs.AIConsent = false
		}, wantStatus: string(ProactiveStatusBlocked), wantReason: "preference_off",
			inBlock: true, eventType: "proactive_blocked"},
		{name: "引用失效走 cancel", mutate: func(f *fakeStore, _ *ProactiveCandidate) {
			f.refStates[0].Status = "completed"
		}, wantStatus: string(ProactiveStatusCancelled), wantReason: "stale_trigger",
			inReject: true, eventType: "proactive_cancelled"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f, s := proactiveGreenStore()
			cand := proacCandidate()
			c.mutate(f, &cand)
			res, err := s.AdmitProactive(context.Background(), cand)
			if err != nil {
				t.Fatalf("AdmitProactive: %v", err)
			}
			if len(f.proactives) != 1 {
				t.Fatalf("拒绝也必须留行，实际 %d 行（04 §22 的分母）", len(f.proactives))
			}
			row := f.proactives[0]
			if row.Status != c.wantStatus {
				t.Errorf("status = %s，期望 %s", row.Status, c.wantStatus)
			}
			wantReject, wantBlock := "", ""
			if c.inReject {
				wantReject = c.wantReason
			}
			if c.inBlock {
				wantBlock = c.wantReason
			}
			if row.RejectReason != wantReject || row.BlockReason != wantBlock {
				t.Errorf("列归属 reject=%q block=%q，期望 reject=%q block=%q",
					row.RejectReason, row.BlockReason, wantReject, wantBlock)
			}
			if res.Reason.String() != c.wantReason {
				t.Errorf("返回值 reason = %q，期望 %q", res.Reason, c.wantReason)
			}
			if len(f.events) != 1 || f.events[0].EventType != c.eventType {
				t.Errorf("事件 = %+v，期望 %s", f.events, c.eventType)
			}
			// 失败行也要把拒因写进 packet，N9 读路径与 O 侧复盘只看这一列。
			packet := EligibilityPacket{}
			if err := json.Unmarshal([]byte(row.EligibilityDetail), &packet); err != nil {
				t.Fatalf("eligibility_detail 不可解析：%v", err)
			}
			if packet.Reason != c.wantReason || packet.Fail == "" {
				t.Errorf("packet 未留失败结论：%q/%q", packet.Reason, packet.Fail)
			}
		})
	}
}

// 重跑同一次生成（同 id）不得插第二行，也不得重新评估——上游重试是常态。
func TestProactiveService_AdmitIsIdempotentByID(t *testing.T) {
	f, s := proactiveGreenStore()
	f.proacState = &ProactiveState{ID: "proac-1", UserID: owner, PersonalModelID: "model-1",
		Status: string(ProactiveStatusScheduled), TriggerType: string(TriggerMemoryRecall),
		RejectReason: "preference_off", Eligibility: 0.5}
	res, err := proactiveAdmit(t, s)
	if err != nil {
		t.Fatalf("AdmitProactive: %v", err)
	}
	if len(f.proactives) != 0 || len(f.events) != 0 {
		t.Errorf("幂等命中不该插行也不该写事件：%d 行 / %d 事件",
			len(f.proactives), len(f.events))
	}
	if res.ID != "proac-1" || res.Status != string(ProactiveStatusScheduled) {
		t.Errorf("返回值 = %s/%s，应原样回吐已存在的行", res.ID, res.Status)
	}
	if res.Reason != RejectReason("preference_off") {
		t.Errorf("拒因 = %q，应带回原结论", res.Reason)
	}
}

// 提案指向别人的模型行：这是越权引用，报错回滚而不是改写。
func TestProactiveService_AdmitRejectsForeignModelID(t *testing.T) {
	f, s := proactiveGreenStore()
	cand := proacCandidate()
	cand.PersonalModelID = "model-999"
	if _, err := s.AdmitProactive(context.Background(), cand); err == nil {
		t.Fatal("模型 id 不符必须报错")
	} else if !strings.Contains(err.Error(), "model-999") {
		t.Errorf("报错应带上冲突的 id，实际 %v", err)
	}
	if !f.rolledBack {
		t.Error("事务未回滚")
	}
	if len(f.proactives) != 0 || len(f.events) != 0 {
		t.Errorf("越权提案不得留任何行：%d/%d", len(f.proactives), len(f.events))
	}
}

// 写失败必须整笔回滚：权威行与事件要么都写、要么都不写。
func TestProactiveService_AdmitWriteFailureRollsBack(t *testing.T) {
	f, s := proactiveGreenStore()
	f.proacErr = errProactiveStub
	if _, err := proactiveAdmit(t, s); err == nil {
		t.Fatal("InsertProactive 失败应把错误抛出")
	}
	if !f.rolledBack {
		t.Error("写权威行失败必须回滚")
	}

	f, s = proactiveGreenStore()
	f.insertErr = errProactiveStub // AppendEvent 用的桩
	res, err := s.AdmitProactive(context.Background(), proacCandidate())
	if err == nil {
		t.Fatalf("事件写失败也必须回滚，实际返回 %+v", res)
	}
	if !f.rolledBack {
		t.Error("事件写失败必须回滚，否则出现「有行无留痕」")
	}
}

// 只有 Insight/Pattern 有回链表；goal 靠 packet.ref_ids 追（canonical.sql 注释）。
func TestProactiveService_AdmitLinksOnlyInsightAndPattern(t *testing.T) {
	f, s := proactiveGreenStore()
	f.refStates = append(f.refStates,
		RefState{RefType: RefPattern, ID: "p1", Status: "active",
			AnchorAt: windowEnd, HasTime: true},
		RefState{RefType: RefInsight, ID: "i1", Status: "published",
			AnchorAt: windowEnd, HasTime: true})
	cand := proacCandidate()
	cand.RefIDs = []string{"goal-1", "p1", "i1"}
	if _, err := s.AdmitProactive(context.Background(), cand); err != nil {
		t.Fatalf("AdmitProactive: %v", err)
	}
	if len(f.linkedProac) != 2 {
		t.Fatalf("回链数 = %v，期望 pattern+insight 两条", f.linkedProac)
	}
	joined := strings.Join(f.linkedProac, ",")
	if !strings.Contains(joined, ":pattern:p1") || !strings.Contains(joined, ":insight:i1") {
		t.Errorf("回链内容 = %v", f.linkedProac)
	}
	if strings.Contains(joined, "goal-1") {
		t.Error("goal 没有回链表，不得硬塞进 proactive_refs")
	}
	// 命中第②层（活 Pattern）后分数应是满层。
	packet := EligibilityPacket{}
	if err := json.Unmarshal([]byte(f.proactives[0].EligibilityDetail), &packet); err != nil {
		t.Fatalf("packet 不可解析：%v", err)
	}
	if len(packet.Layers) != 4 {
		t.Errorf("packet.layers = %v，期望四层", packet.Layers)
	}
	if f.proactives[0].EligibilityScore != 1.0 {
		t.Errorf("eligibility_score = %v，期望 1.0（P-8：命中层数/4）",
			f.proactives[0].EligibilityScore)
	}
}

// ── RecheckProactive：发送时刻的二次校验 ────────────────────────────────────

// greenProactiveState 造一条生成时刻判定为 eligible 的排期行（packet 是真 JSON，
// 重评时 Service 只拿 id 当引子，状态一律回库里取）。
func greenProactiveState(t *testing.T) ProactiveState {
	t.Helper()
	packet := EligibilityPacket{
		Trigger: string(TriggerMemoryRecall), Source: string(SourceUserRequestedFollow),
		Confidence: 0.90, EvidenceIDs: []string{"e1"}, RefIDs: []string{"goal-1"},
		AnchorRefID: "goal-1", Timezone: "UTC", Cadence: string(CadenceNormal),
	}
	encoded, err := json.Marshal(packet)
	if err != nil {
		t.Fatalf("marshal packet: %v", err)
	}
	return ProactiveState{
		ID: "proac-1", UserID: owner, PersonalModelID: "model-1",
		Status: string(ProactiveStatusScheduled), TriggerType: string(TriggerMemoryRecall),
		Message: proacMessage, Eligibility: 0.75, Detail: string(encoded),
		ExpiresAt: windowEnd.Add(12 * time.Hour), HasExpiresAt: true, ModelVersion: 0,
	}
}

func recheck(t *testing.T, f *fakeStore, safety *Safety) (ProactiveResult, error) {
	t.Helper()
	s, _ := newTestService(f)
	return s.RecheckProactive(context.Background(), owner, "proac-1", safety)
}

var safetyOK = &Safety{Status: SafetyOK}

func TestProactiveService_RecheckAllGreenMarksSent(t *testing.T) {
	f := newFakeStore()
	f.refStates = []RefState{{RefType: RefGoal, ID: "goal-1", Status: "active",
		AnchorAt: windowEnd, HasTime: true}}
	state := greenProactiveState(t)
	f.proacState = &state
	res, err := recheck(t, f, safetyOK)
	if err != nil {
		t.Fatalf("RecheckProactive: %v", err)
	}
	if res.Status != string(ProactiveStatusSent) || !res.HasSentAt {
		t.Errorf("全绿应置 sent，实际 %s（sent_at %v）", res.Status, res.SentAt)
	}
	if len(f.sentMarks) != 1 || f.sentMarks[0] != "proac-1" {
		t.Errorf("MarkProactiveSent = %v", f.sentMarks)
	}
	if len(f.statusWrites) != 0 {
		t.Errorf("全绿不该走状态改写：%+v", f.statusWrites)
	}
	if len(f.events) != 1 || f.events[0].EventType != "proactive_sent" ||
		f.events[0].AggregateID != "proac-1" {
		t.Errorf("事件 = %+v", f.events)
	}
	// 发送时刻的 packet 重写成新的判定结果（含 checks），而不是沿用生成时刻那份。
	packet := EligibilityPacket{}
	if err := json.Unmarshal([]byte(f.events[0].Payload), &packet); err != nil {
		t.Fatalf("sent 事件的 payload 不是 packet：%v", err)
	}
	if strings.Join(packet.Checks, ",") != strings.Join(gateStepNames, ",") {
		t.Errorf("payload.checks = %v，期望十一关全过", packet.Checks)
	}
	if f.modelVer != 0 || len(f.versions) != 0 {
		t.Errorf("发送也不推进模型版本（modelVer=%d）", f.modelVer)
	}
}

// 终态行不得被回头改：重复投递比漏发一次更贵。
func TestProactiveService_RecheckTerminalRefuses(t *testing.T) {
	for _, status := range []string{string(ProactiveStatusSent), string(ProactiveStatusExpired),
		string(ProactiveStatusCancelled), string(ProactiveStatusResponded)} {
		t.Run(status, func(t *testing.T) {
			f := newFakeStore()
			state := greenProactiveState(t)
			state.Status = status
			f.proacState = &state
			_, err := recheck(t, f, safetyOK)
			if !errors.Is(err, ErrProactiveTerminal) {
				t.Errorf("终态 %s 应报 ErrProactiveTerminal，实际 %v", status, err)
			}
			if len(f.statusWrites)+len(f.sentMarks)+len(f.expiredIDs)+len(f.events) != 0 {
				t.Errorf("终态行不得有任何写入：%+v/%+v", f.statusWrites, f.events)
			}
		})
	}
}

// 沉默到期：04 §19「Later: proactive → expired」，不得事后补发。
func TestProactiveService_RecheckExpiresAtDeadline(t *testing.T) {
	f := newFakeStore()
	state := greenProactiveState(t)
	state.ExpiresAt = windowEnd.Add(-time.Minute)
	f.proacState = &state
	res, err := recheck(t, f, safetyOK)
	if err != nil {
		t.Fatalf("RecheckProactive: %v", err)
	}
	if res.Status != string(ProactiveStatusExpired) ||
		res.Action != ActionProactiveCancel {
		t.Errorf("到期应落 expired/cancel，实际 %s/%s", res.Status, res.Action)
	}
	if len(f.expiredIDs) != 1 || f.expiredIDs[0] != "proac-1" {
		t.Errorf("ExpireProactive = %v", f.expiredIDs)
	}
	if len(f.events) != 1 || f.events[0].EventType != "proactive_expired" {
		t.Errorf("事件 = %+v", f.events)
	}
	if len(f.sentMarks) != 0 {
		t.Error("到期行不得被置 sent")
	}
}

// 生成时刻的资格留痕读不出来 ⇒ 无从重跑判定 ⇒ fail-closed 拦下。
func TestProactiveService_RecheckUnparseableDetailFailsClosed(t *testing.T) {
	for _, detail := range []string{"", "{不是 JSON"} {
		f := newFakeStore()
		state := greenProactiveState(t)
		state.Detail = detail
		f.proacState = &state
		res, err := recheck(t, f, safetyOK)
		if err != nil {
			t.Fatalf("RecheckProactive(%q): %v", detail, err)
		}
		if res.Status != string(ProactiveStatusBlocked) ||
			res.Reason != ReasonEvidenceNotFound {
			t.Errorf("detail=%q 应 fail-closed 落 blocked/evidence_not_found，实际 %s/%s",
				detail, res.Status, res.Reason)
		}
		if len(f.statusWrites) != 1 {
			t.Fatalf("状态改写行数 = %d", len(f.statusWrites))
		}
		if f.statusWrites[0].BlockReason != "evidence_not_found" {
			t.Errorf("block_reason = %q", f.statusWrites[0].BlockReason)
		}
		// 落库的 eligibility_detail 仍必须是合法 packet（N9 只按一个形状拆）。
		packet := EligibilityPacket{}
		if err := json.Unmarshal([]byte(f.statusWrites[0].Detail), &packet); err != nil {
			t.Fatalf("改写后的 detail 不可解析：%v", err)
		}
		if packet.Reason != "evidence_not_found" || packet.Fail == "" {
			t.Errorf("packet 结论 = %+v", packet)
		}
		if len(f.sentMarks) != 0 {
			t.Error("读不出判定输入绝不能置 sent")
		}
	}
}

// 库态变化必须在发送时刻被抓住：引用失效→cancel、额度被占→block、危机→block。
func TestProactiveService_RecheckReadsFreshState(t *testing.T) {
	cases := []struct {
		name       string
		mutate     func(*fakeStore)
		safety     *Safety
		wantStatus string
		wantReason string
		inReject   bool
	}{
		{name: "引用行已被完成", mutate: func(f *fakeStore) {
			f.refStates[0].Status = "completed"
		}, safety: safetyOK, wantStatus: string(ProactiveStatusCancelled),
			wantReason: "stale_trigger", inReject: true},
		{name: "引用行查不到了", mutate: func(f *fakeStore) { f.refStates = nil },
			safety: safetyOK, wantStatus: string(ProactiveStatusCancelled),
			wantReason: "stale_trigger", inReject: true},
		{name: "用户刚纠正过这一行", mutate: func(f *fakeStore) {
			f.corrected = []string{"goal-1"}
		}, safety: safetyOK, wantStatus: string(ProactiveStatusCancelled),
			wantReason: "stale_trigger", inReject: true},
		{name: "当天额度已被另一条占掉", mutate: func(f *fakeStore) {
			f.history = []ProactiveSendRow{
				sentRow("h1", windowEnd, TriggerPatternFollowup),
			}
		}, safety: safetyOK, wantStatus: string(ProactiveStatusBlocked),
			wantReason: "frequency_cap"},
		{name: "发送时刻命中危机", mutate: func(_ *fakeStore) {},
			safety:     &Safety{Status: SafetyCrisis, Categories: []string{"self_harm"}},
			wantStatus: string(ProactiveStatusBlocked), wantReason: "safety_blocked",
			inReject: true},
		{name: "发送时刻安全判定不可用", mutate: func(_ *fakeStore) {},
			safety:     &Safety{Status: SafetyUnavailable},
			wantStatus: string(ProactiveStatusBlocked), wantReason: "safety_unavailable",
			inReject: true},
		{name: "排期时刻落在静默时段", mutate: func(f *fakeStore) {
			f.proacState.ScheduledAt = time.Date(2026, 10, 13, 3, 0, 0, 0, time.UTC)
			f.proacState.HasScheduledAt = true
		}, safety: safetyOK, wantStatus: string(ProactiveStatusBlocked),
			wantReason: "quiet_hours"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := newFakeStore()
			f.refStates = []RefState{{RefType: RefGoal, ID: "goal-1", Status: "active",
				AnchorAt: windowEnd, HasTime: true}}
			state := greenProactiveState(t)
			f.proacState = &state
			c.mutate(f)
			res, err := recheck(t, f, c.safety)
			if err != nil {
				t.Fatalf("RecheckProactive: %v", err)
			}
			if res.Status != c.wantStatus || res.Reason.String() != c.wantReason {
				t.Errorf("结论 = %s/%s（%s），期望 %s/%s",
					res.Status, res.Reason, res.Detail, c.wantStatus, c.wantReason)
			}
			if len(f.sentMarks) != 0 {
				t.Error("被拦下却置了 sent")
			}
			if len(f.statusWrites) != 1 {
				t.Fatalf("状态改写行数 = %d", len(f.statusWrites))
			}
			row := f.statusWrites[0]
			wantReject, wantBlock := "", ""
			if c.inReject {
				wantReject = c.wantReason
			} else {
				wantBlock = c.wantReason
			}
			if row.RejectReason != wantReject || row.BlockReason != wantBlock {
				t.Errorf("列归属 reject=%q block=%q，期望 reject=%q block=%q",
					row.RejectReason, row.BlockReason, wantReject, wantBlock)
			}
			if row.Status != c.wantStatus {
				t.Errorf("落库 status = %s，期望 %s", row.Status, c.wantStatus)
			}
			if len(f.events) != 1 {
				t.Errorf("事件数 = %d，失败也必须留痕", len(f.events))
			}
		})
	}
}

// 跨用户读取：别人队列里的触达不是你的，报 not found 而不是改写。
func TestProactiveService_RecheckCrossUserIsNotFound(t *testing.T) {
	f := newFakeStore()
	state := greenProactiveState(t)
	f.proacState = &state
	s, _ := newTestService(f)
	_, err := s.RecheckProactive(context.Background(),
		"99999999-9999-4999-8999-999999999999", "proac-1", safetyOK)
	if !errors.Is(err, ErrProactiveNotFound) {
		t.Errorf("跨用户应报 ErrProactiveNotFound，实际 %v", err)
	}
	if len(f.statusWrites)+len(f.sentMarks)+len(f.events) != 0 {
		t.Error("越权读取不得产生任何写入")
	}
}

// 提案不能指定「现在就发」绕过静默窗之外的判定：排期时刻优先于系统时钟。
func TestProactiveService_SendMomentPrefersSchedule(t *testing.T) {
	quiet := time.Date(2026, 10, 13, 3, 0, 0, 0, time.UTC)
	cand := proacCandidate()
	cand.ScheduledAt, cand.HasScheduledAt = quiet, true
	if got := sendMoment(cand, windowEnd); !got.Equal(quiet) {
		t.Errorf("有排期时应按排期时刻判，实际 %v", got)
	}
	cand.HasScheduledAt = false
	if got := sendMoment(cand, windowEnd); !got.Equal(windowEnd) {
		t.Errorf("无排期时应退回当前时刻，实际 %v", got)
	}

	// 落库面：排期在静默窗内的候选必须在生成时刻就被挡（block_reason=quiet_hours）。
	f, s := proactiveGreenStore()
	res, err := s.AdmitProactive(context.Background(), cand)
	if err != nil {
		t.Fatalf("AdmitProactive: %v", err)
	}
	if res.Reason != "" {
		t.Fatalf("基线候选应全绿，实际停在 %s：%s", res.Reason, res.Detail)
	}
	scheduled := cand
	scheduled.ID = "proac-2"
	scheduled.ScheduledAt, scheduled.HasScheduledAt = quiet, true
	res, err = s.AdmitProactive(context.Background(), scheduled)
	if err != nil {
		t.Fatalf("AdmitProactive(scheduled): %v", err)
	}
	if res.Reason != ReasonQuietHours || f.proactives[len(f.proactives)-1].BlockReason !=
		"quiet_hours" {
		t.Errorf("排期落在静默窗应 block，实际 %s / %q", res.Reason,
			f.proactives[len(f.proactives)-1].BlockReason)
	}
}
