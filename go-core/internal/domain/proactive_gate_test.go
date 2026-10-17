// N5 资格门单测（V3 04 §5 硬门 + §6 分类型证据/置信 + §11 交付前十门 + §12 新鲜度，
// 叠上 PRD「四层公式至少满足 2 层」）。
//
// 三条钉死的口径：
//  1. 校验序是契约（golden 全序 + 每个失败样本只报第一关）；
//  2. 一切输入从库里取，提案自报的状态一律不采信（不变量 7 的读侧形式）；
//  3. 九条 N5 拒绝码必须**全部可达**（下面的反查闸真跑一遍表），否则词表是空头支票。
package domain

import (
	"context"
	"strings"
	"testing"
	"time"
)

// proacMessage 是基线文案：51 字（PRD :663 的 50-150 区间内）、含特指疑问词、
// 不含封闭问句，也不含标签词与绝对量词。
const proacMessage = "上周你提到连续几周赶项目到凌晨，我注意到聊天里的工作进度和睡眠时间常常一起出现。你现在最想先聊哪一块？"

func proacCandidate() ProactiveCandidate {
	return ProactiveCandidate{
		UserID: owner, ID: "proac-1",
		TriggerType:   TriggerMemoryRecall,
		TriggerSource: SourceUserRequestedFollow,
		Message:       proacMessage,
		Confidence:    0.90,
		EvidenceIDs:   []string{"e1"},
		RefIDs:        []string{"goal-1"},
		AnchorRefID:   "goal-1",
		Safety:        &Safety{Status: SafetyOK},
	}
}

// proacInputs 是全绿库态：证据属于该用户且够强、goal-1 活着且给得出今天锚点、
// 偏好齐全、无历史、沉默 0 天、时刻落在允许窗。
func proacInputs() ProactiveInputs {
	return ProactiveInputs{
		Prefs: UserPrefs{UserID: owner, Timezone: "UTC", PushEnabled: true,
			PushFrequency: "daily", AIConsent: true, Tier: "free",
			CreatedAt: windowStart, Found: true},
		Evidence: []Evidence{
			{ID: "e1", UserID: owner, Strength: 0.90, HasStrength: true,
				Role: "supporting", SourceID: "s1", ObservedAt: windowStart},
			{ID: "e2", UserID: owner, Strength: 0.88, HasStrength: true,
				Role: "supporting", SourceID: "s2", ObservedAt: windowEnd},
		},
		Refs: []RefState{{RefType: RefGoal, ID: "goal-1", Status: "active",
			AnchorAt: windowEnd, HasTime: true}},
		AnchorRefID: "goal-1", AnchorAt: windowEnd, HasAnchor: true,
		SilentDays: 0, Now: windowEnd, Timezone: "UTC",
	}
}

func runGate(c ProactiveCandidate, in ProactiveInputs) ProactiveOutcome {
	return ProactiveGate{Policy: DefaultPolicy()}.Check(context.Background(), &c, in)
}

type proacGateCase struct {
	name   string
	mutate func(*ProactiveCandidate, *ProactiveInputs)
	reason RejectReason
	action ProactiveAction
	reject bool // true⇒落 reject_reason，false⇒落 block_reason（P-9 的分工）
}

// proacGateCases 是资格门用例表（包级：反查闸要跑同一张表，不维护第二份清单）。
var proacGateCases = []proacGateCase{
	// ── 第 1 关 schema ──────────────────────────────────────────────────
	{name: "触发类型不在七类里", reason: ReasonTriggerUnknown, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) {
			c.TriggerType = TriggerType("weather_alert")
		}},
	{name: "触发源缺失即无显式触发", reason: ReasonTriggerUnknown, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) { c.TriggerSource = "" }},
	{name: "空文本没有可发内容", reason: ReasonSchemaInvalid, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) { c.Message = "   " }},
	{name: "缺 user_id 无从核验归属", reason: ReasonSchemaInvalid, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) { c.UserID = "" }},
	{name: "引用条数超单候选上限", reason: ReasonSchemaInvalid, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) {
			// 按 policy 取值构造，不写死数字：env 改了上限这条也该跟着改。
			ids := make([]string, DefaultPolicy().MaxEvidencePerCandidate+1)
			for i := range ids {
				ids[i] = "e"
			}
			c.EvidenceIDs = ids
		}},

	// ── 第 2 关 evidence（04 §6:108-127 的分类型最小集）─────────────────
	{name: "引用证据不在库里", reason: ReasonEvidenceMissing, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) { c.EvidenceIDs = []string{"ghost"} }},
	{name: "引用证据不属于该用户", reason: ReasonEvidenceNotOwned, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, in *ProactiveInputs) {
			in.Evidence[0].UserID = "22222222-2222-4222-8222-222222222222"
		}},
	{name: "memory_recall 没有来源", reason: ReasonEvidenceMissing, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) { c.EvidenceIDs = nil }},
	{name: "memory_recall 的来源不够强", reason: ReasonEvidenceBelowFloor, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, in *ProactiveInputs) {
			in.Evidence[0].Strength = 0.30
		}},
	{name: "pattern_followup 没有活 Pattern", reason: ReasonEvidenceMissing,
		action: ActionProactiveReject, reject: true,
		mutate: func(c *ProactiveCandidate, in *ProactiveInputs) {
			c.TriggerType, c.EvidenceIDs = TriggerPatternFollowup, []string{"e1", "e2"}
			in.Refs = []RefState{{RefType: RefPattern, ID: "p1", Status: "invalidated",
				AnchorAt: windowEnd, HasTime: true}}
		}},
	{name: "pattern_followup 独立观测只有一条", reason: ReasonEvidenceBelowFloor,
		action: ActionProactiveReject, reject: true,
		mutate: func(c *ProactiveCandidate, in *ProactiveInputs) {
			c.TriggerType = TriggerPatternFollowup
			in.Refs = []RefState{{RefType: RefPattern, ID: "p1", Status: "active",
				AnchorAt: windowEnd, HasTime: true}}
			// 同 source、同一天：N4 的独立性口径只算一条（03 §6:161-163）。
			in.Evidence = []Evidence{
				{ID: "e1", UserID: owner, Strength: 0.90, HasStrength: true,
					Role: "supporting", SourceID: "s1", ObservedAt: windowStart},
				{ID: "e2", UserID: owner, Strength: 0.88, HasStrength: true,
					Role: "supporting", SourceID: "s1", ObservedAt: windowStart},
			}
		}},
	{name: "goal_followup 没有显式 Goal", reason: ReasonEvidenceMissing,
		action: ActionProactiveReject, reject: true,
		mutate: func(c *ProactiveCandidate, in *ProactiveInputs) {
			c.TriggerType, c.RefIDs, c.AnchorRefID = TriggerGoalFollowup, nil, ""
			in.Refs, in.AnchorRefID, in.HasAnchor = nil, "", false
		}},
	{name: "commitment_followup 没有显式 Commitment", reason: ReasonEvidenceMissing,
		action: ActionProactiveReject, reject: true,
		mutate: func(c *ProactiveCandidate, in *ProactiveInputs) {
			c.TriggerType, c.RefIDs, c.AnchorRefID = TriggerCommitmentFollowup, nil, ""
			in.Refs, in.AnchorRefID, in.HasAnchor = nil, "", false
		}},
	{name: "outcome_check 指不回任何一行", reason: ReasonEvidenceMissing,
		action: ActionProactiveReject, reject: true,
		mutate: func(c *ProactiveCandidate, in *ProactiveInputs) {
			c.TriggerType, c.RefIDs, c.AnchorRefID = TriggerOutcomeCheck, nil, ""
			in.Refs, in.AnchorRefID, in.HasAnchor = nil, "", false
		}},
	{name: "reflection_prompt 没引用已算分证据", reason: ReasonEvidenceMissing,
		action: ActionProactiveReject, reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) {
			c.TriggerType, c.EvidenceIDs = TriggerReflectionPrompt, nil
		}},
	{name: "milestone 的锚点回查不到", reason: ReasonEvidenceMissing,
		action: ActionProactiveReject, reject: true,
		mutate: func(c *ProactiveCandidate, in *ProactiveInputs) {
			c.TriggerType, c.RefIDs, c.AnchorRefID = TriggerMilestone, nil, ""
			in.Refs, in.AnchorRefID, in.HasAnchor = nil, "", false
		}},

	// ── 第 3 关 layers（closenai.html:637-638）──────────────────────────
	// 两类都改判 milestone：memory_recall 的证据最小条数关排在 layers 之前，
	// 拿掉证据会先报 evidence_missing，报不出层数码。
	{name: "只命中一层，宁可不发", reason: ReasonLayerBelowMinimum, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, in *ProactiveInputs) {
			c.TriggerType = TriggerMilestone
			c.EvidenceIDs, c.RefIDs, c.AnchorRefID = nil, nil, ""
			in.Refs, in.AnchorRefID = nil, ""
			// 锚点=今天 ⇒ 只命中③；无证据、无活 Pattern、句里没有开放问句。
			c.Message = "今晚有一件还没聊完的事，先放一放也好。" + strings.Repeat("嗯", 60)
		}},
	{name: "敏感域要多一层", reason: ReasonLayerBelowMinimum, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, in *ProactiveInputs) {
			c.Safety = &Safety{Status: SafetyOK, Categories: []string{"self_harm"}}
			c.Message = "最近聊到睡眠时你提到有些夜里很难受。你现在想聊哪一块？" +
				strings.Repeat("嗯", 40)
			// ①证据 + ④开放问句 = 2 层；常规门槛够，敏感域门槛（≥3）不够。
			in.Refs, in.AnchorRefID, in.HasAnchor = nil, "", false
		}},

	// ── 第 4 关 confidence（04 §6:131-137）──────────────────────────────
	{name: "memory_recall 置信低于 0.75", reason: ReasonConfidenceBelowThresh,
		action: ActionProactiveReject, reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) { c.Confidence = 0.74 }},
	{name: "pattern_followup 置信低于 0.80", reason: ReasonConfidenceBelowThresh,
		action: ActionProactiveReject, reject: true,
		mutate: func(c *ProactiveCandidate, in *ProactiveInputs) {
			c.TriggerType, c.Confidence = TriggerPatternFollowup, 0.79
			c.EvidenceIDs = []string{"e1", "e2"}
			in.Refs = append(in.Refs, RefState{RefType: RefPattern, ID: "p1",
				Status: "active", AnchorAt: windowEnd, HasTime: true})
		}},
	{name: "敏感域置信必须到 0.85", reason: ReasonConfidenceBelowThresh,
		action: ActionProactiveReject, reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) {
			c.Safety = &Safety{Status: SafetyOK, Categories: []string{"self_harm"}}
			c.Confidence = 0.84
		}},

	// ── 第 5 关 safety（04:391 绝对优先 + §21）──────────────────────────
	{name: "缺少安全判定输入", reason: ReasonSafetyUnavailable, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) { c.Safety = nil }},
	{name: "安全判定不可用不得放行", reason: ReasonSafetyUnavailable, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) {
			c.Safety = &Safety{Status: SafetyUnavailable}
		}},
	{name: "命中危机信号即停触达", reason: ReasonSafetyBlocked, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) {
			c.Safety = &Safety{Status: SafetyCrisis, Categories: []string{"self_harm"}}
		}},
	// D-G17（批 4）：非危机的违规面有自己的第四态，同样拦下，但措辞不能说成危机。
	{name: "命中违规内容即停触达但不算危机", reason: ReasonSafetyBlocked, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) {
			c.Safety = &Safety{Status: SafetyUnsafe, Categories: []string{"治疗"}}
		}},
	{name: "安全状态不是四态之一", reason: ReasonSafetyUnavailable, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) {
			c.Safety = &Safety{Status: SafetyStatus("unknown")}
		}},

	// ── 第 6 关 user preference（只认 users 实际列）─────────────────────
	{name: "未授予 AI 权限", reason: ReasonPreferenceOff, action: ActionProactiveBlock,
		mutate: func(_ *ProactiveCandidate, in *ProactiveInputs) {
			in.Prefs.AIConsent = false
		}},
	{name: "频率设为 off", reason: ReasonPreferenceOff, action: ActionProactiveBlock,
		mutate: func(_ *ProactiveCandidate, in *ProactiveInputs) {
			in.Prefs.PushFrequency = "off"
		}},
	{name: "偏好行读不到无法确认同意", reason: ReasonPreferenceOff, action: ActionProactiveBlock,
		mutate: func(_ *ProactiveCandidate, in *ProactiveInputs) {
			in.Prefs = UserPrefs{UserID: owner}
		}},

	// ── 第 7 关 frequency（PRD 触达数字 + 04 §7/§9）─────────────────────
	{name: "本地日已有发送", reason: ReasonFrequencyCap, action: ActionProactiveBlock,
		mutate: func(_ *ProactiveCandidate, in *ProactiveInputs) {
			in.History = []ProactiveSendRow{
				{ID: "h1", Status: string(ProactiveStatusSent),
					TriggerType: string(TriggerMemoryRecall), Message: "另一句话",
					ScheduledAt: windowEnd, HasScheduledAt: true,
					SentAt: windowEnd, HasSentAt: true},
			}
		}},
	{name: "沉默到停推档", reason: ReasonFatigueStopped, action: ActionProactiveBlock,
		mutate: func(_ *ProactiveCandidate, in *ProactiveInputs) { in.SilentDays = 20 }},
	{name: "免费档日额度用尽", reason: ReasonFrequencyCap, action: ActionProactiveBlock,
		mutate: func(_ *ProactiveCandidate, in *ProactiveInputs) {
			in.Prefs.QuotaUsed = 5
		}},

	// ── 第 8 关 quiet hours（04 §8 + 七-4 P-5 的改窗）───────────────────
	{name: "落在本地静默时段", reason: ReasonQuietHours, action: ActionProactiveBlock,
		mutate: func(_ *ProactiveCandidate, in *ProactiveInputs) {
			in.Now = time.Date(2026, 10, 12, 3, 0, 0, 0, time.UTC)
		}},
	{name: "排期时刻落在静默时段", reason: ReasonQuietHours, action: ActionProactiveBlock,
		mutate: func(c *ProactiveCandidate, in *ProactiveInputs) {
			c.ScheduledAt = time.Date(2026, 10, 13, 2, 0, 0, 0, time.UTC)
			c.HasScheduledAt = true
			in.Now = c.ScheduledAt
		}},

	// ── 第 9 关 duplicate（04 §11）──────────────────────────────────────
	{name: "窗口内同一句话已发过", reason: ReasonDuplicateMessage, action: ActionProactiveBlock,
		mutate: func(c *ProactiveCandidate, in *ProactiveInputs) {
			// 必须在查重窗（DuplicateWindowDays=7）内：windowStart 是 22 天前，
			// 用它会判成「窗口外」而放行。
			recent := windowEnd.AddDate(0, 0, -3)
			in.History = []ProactiveSendRow{
				{ID: "d1", Status: string(ProactiveStatusSent),
					TriggerType: string(TriggerMemoryRecall), Message: c.Message,
					ScheduledAt: recent, HasScheduledAt: true,
					SentAt: recent, HasSentAt: true},
			}
		}},

	// ── 第 10 关 freshness（04 §12 六个取消条件，全部现读库态）──────────
	{name: "引用的权威行查不到", reason: ReasonStaleTrigger, action: ActionProactiveCancel,
		reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) { c.RefIDs = []string{"ghost"} }},
	{name: "锚点行给不出时刻", reason: ReasonStaleTrigger, action: ActionProactiveCancel,
		reject: true,
		mutate: func(_ *ProactiveCandidate, in *ProactiveInputs) {
			in.Refs[0].HasTime = false
			in.HasAnchor = false
		}},
	{name: "用户近期纠正过被引用行", reason: ReasonStaleTrigger, action: ActionProactiveCancel,
		reject: true,
		mutate: func(_ *ProactiveCandidate, in *ProactiveInputs) {
			in.Corrected = []string{"goal-1"}
		}},
	{name: "引用行已进失效态", reason: ReasonStaleTrigger, action: ActionProactiveCancel,
		reject: true,
		mutate: func(_ *ProactiveCandidate, in *ProactiveInputs) {
			in.Refs[0].Status = "completed"
		}},
	{name: "超过有效期不得补发", reason: ReasonStaleTrigger, action: ActionProactiveCancel,
		reject: true,
		mutate: func(c *ProactiveCandidate, in *ProactiveInputs) {
			c.ExpiresAt = windowEnd.Add(-time.Hour)
			c.HasExpiresAt = true
		}},

	// ── 第 11 关 message quality（04 §11 + PRD :663 + 规范 §1.2/§1.3）───
	{name: "短于 50 字", reason: ReasonMessageQuality, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) { c.Message = "睡得好吗？" }},
	{name: "长于 150 字", reason: ReasonMessageQuality, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) {
			c.Message = proacMessage + strings.Repeat("再补一句无关的话", 30)
		}},
	{name: "措辞把观察说成诊断", reason: ReasonLanguageOverreach, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) {
			c.Message = "我注意到你最近几周睡眠被工作挤掉，这看起来像抑郁症的表现。你想聊哪一块？" +
				strings.Repeat("嗯", 20)
		}},
	{name: "措辞含绝对量词", reason: ReasonTemporalOverreach, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) {
			c.Message = "我注意到你总是把工作时间排到凌晨，这几周尤其明显。你最想先聊哪一块？" +
				strings.Repeat("嗯", 20)
		}},
	{name: "敏感域未留限定语", reason: ReasonLanguageOverreach, action: ActionProactiveReject,
		reject: true,
		mutate: func(c *ProactiveCandidate, _ *ProactiveInputs) {
			c.Safety = &Safety{Status: SafetyOK, Categories: []string{"medical"}}
			c.Confidence = 0.90
			c.Message = "你提到胸口发紧三次，这是心脏问题的信号。你想聊哪一块？" +
				strings.Repeat("嗯", 30)
		}},
}

func TestProactive_GateRejectsEachHardGate(t *testing.T) {
	for _, c := range proacGateCases {
		t.Run(c.name, func(t *testing.T) {
			cand, in := proacCandidate(), proacInputs()
			c.mutate(&cand, &in)
			out := runGate(cand, in)
			if out.Reason != c.reason {
				t.Fatalf("拒因 = %q（%s），期望 %q", out.Reason, out.Detail, c.reason)
			}
			if out.Action != c.action {
				t.Errorf("动作 = %s，期望 %s", out.Action, c.action)
			}
			if out.IsReject != c.reject {
				t.Errorf("reject_reason/block_reason 归属 = %v，期望 %v（P-9 的分工）",
					out.IsReject, c.reject)
			}
			// 失败结论也必须给出落库 status（拒绝留行的前提是那一列有值）。
			if out.Status == "" {
				t.Error("失败结论缺少 status")
			}
			// P-9 的列归属由 action 决定：reject→reject_reason，其余→block_reason。
			wantStatus := map[ProactiveAction]string{
				ActionProactiveBlock:  string(ProactiveStatusBlocked),
				ActionProactiveReject: string(ProactiveStatusBlocked),
				ActionProactiveCancel: string(ProactiveStatusCancelled),
			}[c.action]
			if out.Status != wantStatus {
				t.Errorf("action=%s 应落 status=%s，实际 %s", c.action, wantStatus, out.Status)
			}
		})
	}
}

// gateStepNames 是 Check 里的校验序（与 proactive_gate.go 的 steps 一一对应）。
var gateStepNames = []string{"schema", "evidence", "layers", "confidence", "safety",
	"preference", "frequency", "quiet_hours", "duplicate", "freshness", "message_quality"}

// 全绿路径：十一关按 04 §11 原序逐条通过，Checks 落满十一个名字。
func TestProactive_GateOrderIsTheContract(t *testing.T) {
	out := runGate(proacCandidate(), proacInputs())
	if out.Action != ActionProactiveEligible {
		t.Fatalf("基线候选应全绿，实际 %s 停在 %s：%s", out.Action, out.Reason, out.Detail)
	}
	if strings.Join(out.Checks, ",") != strings.Join(gateStepNames, ",") {
		t.Errorf("校验序 = %v，期望 %v", out.Checks, gateStepNames)
	}
	if out.Status != string(ProactiveStatusEligible) {
		t.Errorf("status = %s，期望 eligible", out.Status)
	}
	if len(out.HitLayers) != 3 {
		t.Errorf("基线应命中 3 层（①③④），实际 %v", out.HitLayers)
	}
	if out.Score != 0.75 {
		t.Errorf("eligibility_score 按 P-8＝命中层数/4，期望 0.75，实际 %v", out.Score)
	}
	if out.Cadence != CadenceNormal || out.SilentDays != 0 {
		t.Errorf("阶梯 = %s/%d 天，期望 normal/0", out.Cadence, out.SilentDays)
	}
}

// 前置关必须先于后置关报出（同一候选同时踩两关时，golden 决定落哪个码）。
func TestProactive_GateFirstFailureWins(t *testing.T) {
	cand, in := proacCandidate(), proacInputs()
	cand.TriggerType = TriggerType("not_in_seven")
	in.Prefs.AIConsent = false
	cand.Safety = nil
	out := runGate(cand, in)
	if out.Reason != ReasonTriggerUnknown {
		t.Errorf("schema 关必须最先报，实际 %s", out.Reason)
	}
	if len(out.Checks) != 0 {
		t.Errorf("第一关就停，Checks 应为空，实际 %v", out.Checks)
	}

	cand, in = proacCandidate(), proacInputs()
	in.Prefs.AIConsent = false
	in.Now = time.Date(2026, 10, 12, 4, 0, 0, 0, time.UTC)
	out = runGate(cand, in)
	if out.Reason != ReasonPreferenceOff {
		t.Errorf("preference 关必须先于 quiet_hours，实际 %s", out.Reason)
	}
	if strings.Join(out.Checks, ",") != "schema,evidence,layers,confidence,safety" {
		t.Errorf("通过的前五关没按序记录：%v", out.Checks)
	}
}

// 反查闸：N5 加进词表的九码必须每个都被**真跑过一遍的用例**打中。
// gateCaseReasons 跑的是上面那张表，不是另抄一份码名——抄的那份证明不了实现。
func TestProactive_AllN5ReasonCodesAreReachable(t *testing.T) {
	want := []RejectReason{
		ReasonTriggerUnknown, ReasonLayerBelowMinimum, ReasonPreferenceOff,
		ReasonFrequencyCap, ReasonQuietHours, ReasonDuplicateMessage,
		ReasonStaleTrigger, ReasonMessageQuality, ReasonFatigueStopped,
	}
	got := map[RejectReason]int{}
	for _, c := range proacGateCases {
		cand, in := proacCandidate(), proacInputs()
		c.mutate(&cand, &in)
		out := runGate(cand, in)
		if out.Reason == "" {
			t.Errorf("用例 %q 没有产出任何拒因（本该被拦下却全绿）", c.name)
			continue
		}
		got[out.Reason]++
	}
	for _, reason := range want {
		if got[reason] == 0 {
			t.Errorf("N5 码 %s 没有任何资格门用例打中（词表与实现脱节）", reason)
		}
	}
}

func TestProactive_EligibilityScoreIsLayerFraction(t *testing.T) {
	g := ProactiveGate{Policy: DefaultPolicy()}
	for hit, want := range map[int]float64{0: 0, 1: 0.25, 2: 0.5, 3: 0.75, 4: 1.0} {
		layers := make([]Layer, 0, hit)
		for i := 0; i < hit; i++ {
			layers = append(layers, Layers[i])
		}
		if got := g.eligibilityScore(layers); got != want {
			t.Errorf("命中 %d 层 ⇒ %v，期望 %v（P-8：DECIMAL(5,4) 只允许四档）", hit, got, want)
		}
	}
}

// 第②层只认库里的活行：引用到一行不等于「发现了模式」。
func TestProactive_PatternLayerNeedsLiveRow(t *testing.T) {
	cand, in := proacCandidate(), proacInputs()
	cand.RefIDs = []string{"goal-1", "p1"}
	in.Refs = append(in.Refs, RefState{RefType: RefPattern, ID: "p1",
		Status: "invalidated", AnchorAt: windowEnd, HasTime: true})
	out := runGate(cand, in)
	for _, layer := range out.HitLayers {
		if layer == LayerPatternFound {
			t.Error("失效的 Pattern 不得算「发现了模式」这一层")
		}
	}
	in.Refs[1].Status = "active"
	if out = runGate(cand, in); len(out.HitLayers) != 4 || out.Score != 1.0 {
		t.Errorf("活 Pattern 应凑满四层，实际 %v / %v", out.HitLayers, out.Score)
	}
}

// 层判定必须在函数级隔离验证：整跑资格门时 freshness 关（第 10 关）会先把
// 「引用了失效行」拦成 cancelled，HitLayers 于是恒为空——那条路径证明不了第②层
// 自己是否真的只认活行（变异自证 #8 就是这么暴露出覆盖缺口的）。
func TestProactive_LayerTwoOnlyCountsLiveRows(t *testing.T) {
	g := ProactiveGate{Policy: DefaultPolicy()}
	cand, in := proacCandidate(), proacInputs()
	cand.RefIDs = []string{"goal-1", "p1"}
	in.Refs = append(in.Refs, RefState{RefType: RefPattern, ID: "p1",
		Status: "invalidated", AnchorAt: windowEnd, HasTime: true})
	if containsLayer(g.hitLayers(&cand, in), LayerPatternFound) {
		t.Error("失效的 Pattern 行不得贡献第②层「发现了模式」")
	}
	// 同一行换成活态就必须命中，否则上面那条断言是空跑。
	in.Refs[1].Status = "active"
	if !containsLayer(g.hitLayers(&cand, in), LayerPatternFound) {
		t.Error("活 Pattern 应命中第②层（正向对照）")
	}
	for _, dead := range []string{"rejected", "expired", "outdated"} {
		in.Refs[1].Status = dead
		if containsLayer(g.hitLayers(&cand, in), LayerPatternFound) {
			t.Errorf("status=%s 的 Pattern 不该算活行（词表取自 isStaleStatus）", dead)
		}
	}
}

// 「关联当下」只认库里时刻，且必须落在本地今天或未来。
func TestProactive_ContextLayerUsesLocalDayAnchor(t *testing.T) {
	cand, in := proacCandidate(), proacInputs()
	in.AnchorAt = windowEnd.AddDate(0, 0, -3)
	out := runGate(cand, in)
	for _, layer := range out.HitLayers {
		if layer == LayerContextNow {
			t.Error("三天前的锚点不该算「关联当下」")
		}
	}
	in.AnchorAt = windowEnd.AddDate(0, 0, 3)
	if out = runGate(cand, in); !containsLayer(out.HitLayers, LayerContextNow) {
		t.Error("未来的排期锚点应算「关联当下」（04 §3 Goal due 类比）")
	}
}

func containsLayer(layers []Layer, want Layer) bool {
	for _, layer := range layers {
		if layer == want {
			return true
		}
	}
	return false
}

// 封闭问句不算第④层：判不出来就少算一层，不能凑够 2 层把消息发出去。
func TestProactive_ClosedQuestionIsNotLayerFour(t *testing.T) {
	for _, text := range []string{
		"你最近是不是又熬到凌晨了？我注意到这周有三次。这样对身体不好，你想聊聊吗？",
		"Have you finished the plan you mentioned last week, again and again?",
	} {
		if hasOpenQuestion(text) {
			t.Errorf("封闭问句被判成了第④层：%q", text)
		}
	}
	for _, text := range []string{
		"这周有几次你把工作排到了凌晨，你最想先聊哪一块？",
		"You moved work past midnight three times this week, how did that feel?",
	} {
		if !hasOpenQuestion(text) {
			t.Errorf("开放问句没被认出来：%q", text)
		}
	}
}

// 沉默到期不得事后补发（04 §19），且到期判定走 reject ⇒ cancelled。
func TestProactive_ExpiredCandidateCancels(t *testing.T) {
	cand, in := proacCandidate(), proacInputs()
	cand.ExpiresAt = in.Now
	cand.HasExpiresAt = true
	out := runGate(cand, in)
	if out.Reason != ReasonStaleTrigger || out.Status != string(ProactiveStatusCancelled) {
		t.Errorf("到期应落 cancelled+stale_trigger，实际 %s/%s", out.Status, out.Reason)
	}
}
