// N5 频次与疲劳单测（V3 04 §7/§8/§9 + PRD 触达数字）。
//
// 三条口径在这里被钉成可执行约束：
//  1. 阶梯**只降不增**（04:178-180「Never increase frequency merely because
//     engagement falls」）——沉默越久间隔越长，任何反向变化都是回归；
//  2. 计数只认**已发送**的行（P-10：把 scheduled 算进去会让发送时刻的二次校验
//     把同一封消息算成两条，从而把它自己挡掉）；
//  3. 一切「日/周」边界按**用户本地日历**算，不按 UTC（04:163）。
//
// 时间基准：windowEnd = 2026-10-12（周一）20:00 UTC，时区取 UTC 时本地日界即 UTC 日界，
// 本地周窗 = [10-12 00:00, 10-19 00:00)。
package domain

import (
	"strings"
	"testing"
	"time"
)

// at 给出相对 windowEnd 的偏移时刻：d 是天、h 是小时，负数往回。
// at(0,-19) = 本周一 01:00，at(4,20) = 下周五（同一本地周内的另一天）。
func at(d, h int) time.Time { return windowEnd.AddDate(0, 0, d).Add(time.Duration(h) * time.Hour) }

// sentRow 造一条**已发送**的历史行。Detail 是那一封的 eligibility_detail：
// 承诺归属看 packet 的 ref_ids（commitment 没有回链表，见 canonical.sql 注释）。
func sentRow(id string, at time.Time, trigger TriggerType, refs ...string) ProactiveSendRow {
	quoted := make([]string, 0, len(refs))
	for _, ref := range refs {
		quoted = append(quoted, `"`+ref+`"`)
	}
	return ProactiveSendRow{ID: id, Status: string(ProactiveStatusSent),
		TriggerType: string(trigger), Message: "另一句话",
		ScheduledAt: at, HasScheduledAt: true, SentAt: at, HasSentAt: true,
		Detail: `{"ref_ids":[` + strings.Join(quoted, ",") + `]}`}
}

func basePrefs() UserPrefs {
	return UserPrefs{UserID: owner, Timezone: "UTC", AIConsent: true,
		PushFrequency: "daily", Tier: "free", CreatedAt: windowStart, Found: true}
}

func baseInput() FrequencyInput {
	return FrequencyInput{Trigger: TriggerMemoryRecall, Prefs: basePrefs(),
		Cadence: CadenceNormal, Now: windowEnd}
}

func TestProactive_CadenceLadder(t *testing.T) {
	p := DefaultProactivePolicy()
	cases := []struct {
		silent int
		want   Cadence
	}{
		{0, CadenceNormal}, {1, CadenceNormal},
		{2, CadenceShortCare}, {4, CadenceShortCare},
		{5, CadenceWeekly}, {13, CadenceWeekly},
		{14, CadenceStopped}, {90, CadenceStopped},
	}
	for _, c := range cases {
		if got := p.CadenceOf(c.silent); got != c.want {
			t.Errorf("沉默 %d 天 ⇒ %s，期望 %s", c.silent, got, c.want)
		}
	}
}

// 阶梯每升一级，最小间隔只能变长（stopped 视为无穷）。
func TestProactive_FatigueNeverIncreasesFrequency(t *testing.T) {
	p := DefaultProactivePolicy()
	ladder := []Cadence{CadenceNormal, CadenceShortCare, CadenceWeekly, CadenceStopped}
	previous := -1
	for _, cadence := range ladder {
		interval := p.MinIntervalDays(cadence)
		current := interval
		if interval < 0 {
			current = 1 << 30 // stopped＝无穷，参与单调比较
		}
		if previous >= 0 && current < previous {
			t.Errorf("档位 %s 的间隔 %d 比上一档 %d 更短：疲劳只能降频，不得因参与度下降而加频"+
				"（04:178-180）", cadence, interval, previous)
		}
		previous = current
	}
	if p.MinIntervalDays(CadenceStopped) != -1 {
		t.Errorf("stopped 档必须是无穷间隔（-1），实际 %d", p.MinIntervalDays(CadenceStopped))
	}
	if p.MinIntervalDays(CadenceWeekly) != p.WeeklyIntervalDays {
		t.Errorf("weekly 档必须落到每周 1 次（closenai.html:674），实际 %d",
			p.MinIntervalDays(CadenceWeekly))
	}
	// short_care 改的是**内容**不是频率（closenai.html:674 第 2 档原文「简短关心」）。
	if p.MinIntervalDays(CadenceShortCare) != 0 {
		t.Errorf("short_care 不该改变间隔，实际 %d", p.MinIntervalDays(CadenceShortCare))
	}
}

// 静默时段按用户本地小时判；时区不可解析退 UTC（与现行链 safe_zoneinfo 同一条退路）。
func TestProactive_QuietHoursUseUserTimezone(t *testing.T) {
	p := DefaultProactivePolicy()
	// 同一时刻：UTC 是 07:00（静默），Asia/Shanghai 是 15:00（不静默）。
	morning := time.Date(2026, 10, 12, 7, 0, 0, 0, time.UTC)
	if !p.InQuietHours(morning, "UTC") {
		t.Error("UTC 07:00 应落在默认静默窗 00:00-08:00 内")
	}
	if p.InQuietHours(morning, "Asia/Shanghai") {
		t.Error("同一时刻在 Asia/Shanghai 是 15:00，不得按 UTC 误判成深夜")
	}
	// 不可解析时区的退路是「按 UTC 判」，不是放行也不是报错拦死：该时刻 UTC 是
	// 07:00，所以结论应与 UTC 一致（落进静默窗）。
	if !p.InQuietHours(morning, "Not/AZone") {
		t.Error("不可解析时区应退 UTC 判定，实际放行了一个 UTC 深夜时刻")
	}
	// 22:00 不拦：PRD closenai.html:2902 的睡前复盘窗口就发在 22 点（七-4 P-5）。
	if p.InQuietHours(time.Date(2026, 10, 12, 22, 30, 0, 0, time.UTC), "UTC") {
		t.Error("22:30 是 PRD 的睡前推送窗口，默认静默窗不得覆盖它（V3 04 §8 的 22:00 起不采纳）")
	}
	if !p.InQuietHours(time.Date(2026, 10, 12, 0, 0, 0, 0, time.UTC), "UTC") {
		t.Error("00:00 属于静默窗起点")
	}
	if p.InQuietHours(time.Date(2026, 10, 12, 8, 0, 0, 0, time.UTC), "UTC") {
		t.Error("08:00 是静默窗右开界，必须放行")
	}
	// start==end 是「不设这一门」，不是「全天静默」。
	same := p
	same.QuietStartHour, same.QuietEndHour = 5, 5
	if same.InQuietHours(morning, "UTC") {
		t.Error("start==end 应表示关闭静默门")
	}
	// 跨零点窗（22–08）也要能判对，标定后换 env 即可用。
	overnight := p
	overnight.QuietStartHour, overnight.QuietEndHour = 22, 8
	if !overnight.InQuietHours(time.Date(2026, 10, 12, 23, 0, 0, 0, time.UTC), "UTC") {
		t.Error("23:00 应落在跨零点窗 22:00-08:00 内")
	}
	if !overnight.InQuietHours(time.Date(2026, 10, 12, 3, 0, 0, 0, time.UTC), "UTC") {
		t.Error("03:00 应落在跨零点窗内")
	}
	if overnight.InQuietHours(time.Date(2026, 10, 12, 9, 0, 0, 0, time.UTC), "UTC") {
		t.Error("09:00 在跨零点窗 22:00-08:00 之外，必须放行")
	}
}

func TestProactive_CheckFrequencyCaps(t *testing.T) {
	p := DefaultProactivePolicy()
	base := baseInput()

	if verdict := p.CheckFrequency(base); !verdict.OK {
		t.Fatalf("空历史应当放行，实际被 %s 挡住：%s", verdict.Reason, verdict.Detail)
	}

	// 每日 1 条（closenai.html:596）：本周一 01:00 已发一条，周一 20:00 不能再发。
	today := base
	today.History = []ProactiveSendRow{sentRow("h1", at(0, -19), TriggerMemoryRecall)}
	if v := p.CheckFrequency(today); v.OK || v.Reason != ReasonFrequencyCap {
		t.Errorf("今日已发 1 条应撞日上限，实际 OK=%v reason=%s", v.OK, v.Reason)
	}

	// 昨天发的不算进今天。
	yesterday := base
	yesterday.History = []ProactiveSendRow{sentRow("h2", at(-1, 0), TriggerMemoryRecall)}
	if v := p.CheckFrequency(yesterday); !v.OK {
		t.Errorf("昨天那条不占今日额度，实际被挡住：%s", v.Detail)
	}

	// 未发送的排期行不占「每日一条」（P-10 的计数口径）。
	scheduledOnly := base
	scheduledOnly.History = []ProactiveSendRow{{ID: "h3", Status: string(ProactiveStatusScheduled),
		TriggerType: string(TriggerMemoryRecall), Message: "另一句话",
		ScheduledAt: windowEnd, HasScheduledAt: true}}
	if v := p.CheckFrequency(scheduledOnly); !v.OK {
		t.Errorf("scheduled 还没吵到用户，不得占用每日额度：%s", v.Detail)
	}

	// 档位总额度（closenai.html:590 免费 5 条／config.py:138-143）。
	quota := base
	quota.Prefs.QuotaUsed = 5
	if v := p.CheckFrequency(quota); v.OK || v.Reason != ReasonFrequencyCap {
		t.Errorf("免费档额度用尽应拦下，实际 OK=%v", v.OK)
	}
	proQuota := quota
	proQuota.Prefs.Tier = "pro"
	if v := p.CheckFrequency(proQuota); !v.OK {
		t.Errorf("pro 档额度 100 不该被 5 次用尽挡住：%s", v.Detail)
	}

	// 停推档优先于一切：04:178 之后没有任何「再试一次」的余地。
	stopped := base
	stopped.Cadence = CadenceStopped
	if v := p.CheckFrequency(stopped); v.OK || v.Reason != ReasonFatigueStopped {
		t.Errorf("stopped 档必须落到 fatigue_stopped，实际 OK=%v reason=%s", v.OK, v.Reason)
	}

	// weekly 档：距上次发送不足 7 天。
	weekly := base
	weekly.Cadence = CadenceWeekly
	weekly.History = []ProactiveSendRow{sentRow("h4", at(-3, 0), TriggerMemoryRecall)}
	if v := p.CheckFrequency(weekly); v.OK || v.Reason != ReasonFrequencyCap {
		t.Errorf("weekly 档 3 天前发过应拦下，实际 OK=%v reason=%s", v.OK, v.Reason)
	}
	weeklyLate := weekly
	weeklyLate.History = []ProactiveSendRow{sentRow("h5", at(-9, 0), TriggerMemoryRecall)}
	if v := p.CheckFrequency(weeklyLate); !v.OK {
		t.Errorf("9 天前发过，weekly 档应放行：%s", v.Detail)
	}

	// MinIntervalHours 默认 0（PRD 没有这一门），标定后才生效：把日上限抬到 5 条
	// 才能隔离出这一道闸，否则同本地日的那条会先撞「每日 1 条」。
	if p.MinIntervalHours != 0 {
		t.Fatalf("默认不该有两条之间的最小间隔（取证缺口），实际 %d", p.MinIntervalHours)
	}
	spaced := p
	spaced.MaxPerDay, spaced.MinIntervalHours = 5, 12
	recent := base
	recent.History = []ProactiveSendRow{sentRow("h6", at(0, -1), TriggerMemoryRecall)}
	if v := p.CheckFrequency(recent); v.OK != false || !strings.Contains(v.Detail, "本地日内已发") {
		t.Errorf("1 小时前发过一条，默认口径应撞日上限，实际 OK=%v detail=%q", v.OK, v.Detail)
	}
	if v := spaced.CheckFrequency(recent); v.OK || !strings.Contains(v.Detail, "不足 12 小时") {
		t.Errorf("标定最小间隔后 1 小时前那条应把它挡住，实际 OK=%v detail=%q", v.OK, v.Detail)
	}
	spaced.MinIntervalHours = 0
	if v := spaced.CheckFrequency(recent); !v.OK {
		t.Errorf("默认口径下 1 小时前的另一条不该被最小间隔挡住：%s", v.Detail)
	}
}

// 承诺回访两条上限：每周 2 条 + 同一承诺 2 次（closenai.html:2953）。
func TestProactive_CommitmentFollowUpCaps(t *testing.T) {
	p := DefaultProactivePolicy()
	in := FrequencyInput{Trigger: TriggerCommitmentFollowup, CommitmentID: "commit-1",
		Prefs: basePrefs(), Cadence: CadenceNormal, Now: windowEnd}

	// 同一承诺已回访 2 次（都在上一个本地周，避开日上限/周上限的干扰）。
	two := in
	two.History = []ProactiveSendRow{
		sentRow("c1", at(-2, 0), TriggerCommitmentFollowup, "commit-1"),
		sentRow("c2", at(-3, 0), TriggerCommitmentFollowup, "commit-1"),
	}
	if v := p.CheckFrequency(two); v.OK || !strings.Contains(v.Detail, "同一承诺") {
		t.Errorf("同一承诺回访已达上限应拦下并给出归属，实际 OK=%v detail=%q", v.OK, v.Detail)
	}

	// 别的承诺不受这条计数影响。
	other := in
	other.CommitmentID = "commit-2"
	other.History = two.History
	if v := p.CheckFrequency(other); !v.OK {
		t.Errorf("另一承诺不该被同承诺上限挡住：%s", v.Detail)
	}

	// 周回访上限 2：本周一与本周二各一条不同承诺，判定时点在本周五。
	week := in
	week.Now = at(4, 20) // 同一本地周内（周一~周日），且远离那两条的本地日
	week.History = []ProactiveSendRow{
		sentRow("c3", at(0, -19), TriggerCommitmentFollowup, "commit-9"),
		sentRow("c4", at(1, -19), TriggerCommitmentFollowup, "commit-10"),
	}
	if v := p.CheckFrequency(week); v.OK || !strings.Contains(v.Detail, "本周回访") {
		t.Errorf("本周回访已达 2 条上限应拦下，实际 OK=%v detail=%q", v.OK, v.Detail)
	}

	// 周窗必须是 7 天而不是 1 天：跨到本周一的那两条如果按日窗算就会漏。
	if p.CommitmentWeeklyLimit != 2 {
		t.Fatalf("周回访上限应取自 closenai.html:2953，实际 %d", p.CommitmentWeeklyLimit)
	}

	// 非承诺类触达不吃承诺周配额（否则 1/日的主动触达会被自己挤掉）。
	nonCommitment := week
	nonCommitment.Trigger = TriggerPatternFollowup
	if v := p.CheckFrequency(nonCommitment); !v.OK {
		t.Errorf("承诺配额不该挡非承诺触达：%s", v.Detail)
	}

	// packet 解析不出来的历史行按「命中同一承诺」算：反骚扰上限宁可少发。
	unparsed := func(id string, when time.Time) ProactiveSendRow {
		return ProactiveSendRow{ID: id, Status: string(ProactiveStatusSent),
			TriggerType: string(TriggerCommitmentFollowup), Message: "另一句话",
			ScheduledAt: when, HasScheduledAt: true, SentAt: when, HasSentAt: true,
			Detail: "{不是 JSON"}
	}
	unknown := in
	unknown.History = []ProactiveSendRow{
		sentRow("c5", at(-2, 0), TriggerCommitmentFollowup, "commit-9"),
		unparsed("c6", at(-3, 0)),
		unparsed("c7", at(-4, 0)),
	}
	if v := p.CheckFrequency(unknown); v.OK {
		t.Error("承诺归属无法核实时不得再多发一次（上限是反骚扰门）")
	}
}

func TestProactive_DuplicateIsExactWithinWindow(t *testing.T) {
	p := DefaultProactivePolicy()
	text := "这一句完全一样的话不该在窗口内发第二遍。"
	history := []ProactiveSendRow{{ID: "d1", Status: string(ProactiveStatusSent),
		TriggerType: string(TriggerMemoryRecall), Message: text + "  ",
		ScheduledAt: at(-1, 0), HasScheduledAt: true, SentAt: at(-1, 0), HasSentAt: true}}

	// 只压空白/去尾部标点，不做相似度推断。
	if id := p.duplicateOf(text, history, windowEnd, "UTC"); id != "d1" {
		t.Errorf("窗口内同句应判重，实际命中 %q", id)
	}
	if id := p.duplicateOf("完全不同的另一句话内容", history, windowEnd, "UTC"); id != "" {
		t.Errorf("不同内容不该判重，实际命中 %q", id)
	}
	// 窗口外不判重（DuplicateWindowDays=7，取阶梯的「每周」档）。
	old := []ProactiveSendRow{{ID: "d2", Status: string(ProactiveStatusSent),
		TriggerType: string(TriggerMemoryRecall), Message: text,
		ScheduledAt: at(-8, 0), HasScheduledAt: true, SentAt: at(-8, 0), HasSentAt: true}}
	if id := p.duplicateOf(text, old, windowEnd, "UTC"); id != "" {
		t.Errorf("超出 %d 天窗口不该判重，实际命中 %q", p.DuplicateWindowDays, id)
	}
	// 未发送但已排期的行也挡住重复排第二封。
	scheduled := []ProactiveSendRow{{ID: "d3", Status: string(ProactiveStatusScheduled),
		TriggerType: string(TriggerMemoryRecall), Message: text,
		ScheduledAt: at(-1, 0), HasScheduledAt: true}}
	if id := p.duplicateOf(text, scheduled, windowEnd, "UTC"); id != "d3" {
		t.Errorf("已排期的同句应判重，实际命中 %q", id)
	}
	// 既没发送也没排期的行没有可比时刻。
	ghost := []ProactiveSendRow{{ID: "d4", Status: string(ProactiveStatusCandidate),
		TriggerType: string(TriggerMemoryRecall), Message: text}}
	if id := p.duplicateOf(text, ghost, windowEnd, "UTC"); id != "" {
		t.Errorf("无时刻的行不该判重，实际命中 %q", id)
	}
	if id := p.duplicateOf("   ", history, windowEnd, "UTC"); id != "" {
		t.Error("空文本不该判重")
	}
	// 多条命中时取**最早**那一封：结论要能复现，不能随历史行顺序变。
	both := append([]ProactiveSendRow{}, history...)
	both = append(both, ProactiveSendRow{ID: "d5", Status: string(ProactiveStatusSent),
		TriggerType: string(TriggerMemoryRecall), Message: text,
		SentAt: at(-6, 0), HasSentAt: true})
	if id := p.duplicateOf(text, both, windowEnd, "UTC"); id != "d5" {
		t.Errorf("窗口内两封同句应返回更早的 d5，实际 %q", id)
	}
}

// 沉默天数按本地日历日，且「从没说过话」退到注册日（同 _days_silent 的 fallback）。
func TestProactive_SilenceDaysUseLocalCalendar(t *testing.T) {
	utcNow := time.Date(2026, 10, 12, 1, 0, 0, 0, time.UTC)
	last := time.Date(2026, 10, 11, 17, 0, 0, 0, time.UTC)
	if got := SilenceDays(utcNow, last, time.Time{}, "UTC"); got != 1 {
		t.Errorf("UTC 口径应为 1 天，实际 %d", got)
	}
	// 同一组时刻在 Asia/Shanghai：now 是 10-12 09:00、last 是 10-12 01:00，同一天。
	if got := SilenceDays(utcNow, last, time.Time{}, "Asia/Shanghai"); got != 0 {
		t.Errorf("同一时刻在 Asia/Shanghai 是同一天，应为 0 天，实际 %d", got)
	}
	created := time.Date(2026, 10, 2, 9, 0, 0, 0, time.UTC)
	if got := SilenceDays(utcNow, time.Time{}, created, "UTC"); got != 10 {
		t.Errorf("无发言记录应按注册日起算，实际 %d", got)
	}
	if got := SilenceDays(utcNow, time.Time{}, time.Time{}, "UTC"); got != 0 {
		t.Errorf("既无发言也无注册时刻应得 0（不得据此停推），实际 %d", got)
	}
	if got := SilenceDays(utcNow, utcNow.AddDate(0, 0, 5), time.Time{}, "UTC"); got != 0 {
		t.Errorf("未来时刻必须截到 0，实际 %d", got)
	}
}

func TestProactive_SentOnLocalDayCountsOnlySent(t *testing.T) {
	p := DefaultProactivePolicy()
	history := []ProactiveSendRow{
		sentRow("s1", windowEnd, TriggerMemoryRecall),
		{ID: "s2", ScheduledAt: windowEnd, HasScheduledAt: true},
		{ID: "s3", SentAt: windowEnd, HasSentAt: true},
	}
	if n := p.sentOnLocalDay(history, windowEnd, "UTC"); n != 2 {
		t.Errorf("本地日只数已发送行，期望 2，实际 %d", n)
	}
	// 跨零点的本地日界：UTC 15:00 是上海的 23:00（10-12），
	// 而参照时刻 17:00 UTC 在上海已属 10-13 ⇒ 同一行在两个时区落下不同的本地日。
	row := sentRow("s4", time.Date(2026, 10, 12, 15, 0, 0, 0, time.UTC), TriggerMemoryRecall)
	reference := time.Date(2026, 10, 12, 17, 0, 0, 0, time.UTC)
	if n := p.sentOnLocalDay([]ProactiveSendRow{row}, reference, "UTC"); n != 1 {
		t.Errorf("UTC 口径两者同属 10-12，应计 1，实际 %d", n)
	}
	if n := p.sentOnLocalDay([]ProactiveSendRow{row}, reference, "Asia/Shanghai"); n != 0 {
		t.Errorf("该行落在 Asia/Shanghai 的 10-12，参照日已是 10-13，计数应为 0，实际 %d", n)
	}
}
