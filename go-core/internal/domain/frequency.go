package domain

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

// N5 的频次与疲劳面（V3 04 §7/§8/§9 + PRD 触达数字）。
//
// 这一层的产出只有两件事：**间隔**与**停不停**。它不给候选打「魅力分」——
// 04:84 那个减 FatiguePenalty 的加权式全文没定义减项（七-4 P-6），
// 所以疲劳在这里落成可核对的阶梯，而不是一个系数。

// CadenceOf 把「连续未留下记录的本地日历天数」映射到疲劳档。
//
// 口径与现行链同源：closenai.html:674 的 2/5/14 三档，与
// journey_service.py:370-378 `_days_silent` 的「最后一条用户消息或注册日的
// 本地日历日差」同一定义（两侧必须用同一个基准，否则同一用户会落到不同档）。
func (p ProactivePolicy) CadenceOf(silentDays int) Cadence {
	switch {
	case silentDays >= p.SilentStopDays:
		return CadenceStopped
	case silentDays >= p.SilentWeeklyDays:
		return CadenceWeekly
	case silentDays >= p.SilentCareDays:
		return CadenceShortCare
	default:
		return CadenceNormal
	}
}

// MinIntervalDays 是该档要求的最小发送间隔，-1 表示停推（无穷大间隔）。
//
// short_care 不改变间隔（PRD :674 那一档说的是「变为简短关心」，改的是**内容**不是
// 频率），weekly 是「每周 1 次」＝7 天，stopped 是「停止主动发消息，等待用户主动回归」。
//
// 只降不增（04:178-180「Never increase frequency merely because engagement falls」）
// 由 TestProactive_FatigueNeverIncreasesFrequency 钉住：档位每升一级，间隔不得变短。
func (p ProactivePolicy) MinIntervalDays(cadence Cadence) int {
	switch cadence {
	case CadenceStopped:
		return -1
	case CadenceWeekly:
		return p.WeeklyIntervalDays
	default:
		return 0
	}
}

// InQuietHours 判断某个时刻落在用户本地静默时段内。
//
// start==end 视为「不设静默门」；start>end 是跨零点窗口（例如 22–8），
// start<end 是当日内的窗口（默认 0–8）。
//
// 默认只拦 00:00–08:00 而**不采** V3 的 22:00–08:00：后者与 PRD closenai.html:2902
// 「睡前复盘每日 22:00–23:30 固定推送」正面冲突，而现行链的 BEDTIME_HOUR=22
// （cron_service.py:92）确实发在 22 点。裁定与出处见七-4 P-5。
func (p ProactivePolicy) InQuietHours(at time.Time, timezone string) bool {
	start, end := p.QuietStartHour, p.QuietEndHour
	if start == end {
		return false
	}
	hour := localHour(at, timezone)
	if start < end {
		return hour >= start && hour < end
	}
	return hour >= start || hour < end
}

// localHour 取用户本地小时。时区名不可解析时退 UTC——静默时段是「别在深夜吵到用户」
// 的软门，把它做成 fail-closed 会让缺时区数据的用户一条都收不到；现行链
// backend/core/time_utils.py 的 safe_zoneinfo 是同一条退路。
func localHour(at time.Time, timezone string) int {
	return at.In(loadUserZone(timezone)).Hour()
}

func loadUserZone(timezone string) *time.Location {
	zone, err := time.LoadLocation(timezone)
	if err != nil || zone == nil {
		return time.UTC
	}
	return zone
}

// localDayStartUTC 取该时刻所在**本地日历日**的起点，折算回 UTC。
// 口径同 backend/core/time_utils.py:36-44（现行链的「每日一条」用它）。
func localDayStartUTC(at time.Time, timezone string) time.Time {
	zone := loadUserZone(timezone)
	local := at.In(zone)
	return time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, zone).UTC()
}

// localWeekStartUTC 取本地周一 00:00（承诺回访的周窗口口径，
// 同 backend/services/commitment_service.py:250-256）。
func localWeekStartUTC(at time.Time, timezone string) time.Time {
	day := localDayStartUTC(at, timezone)
	zone := loadUserZone(timezone)
	offset := (int(day.In(zone).Weekday()) + 6) % 7 // 周一=0
	return day.AddDate(0, 0, -offset)
}

// FrequencyInput 是频次关的一次求值输入。
type FrequencyInput struct {
	Trigger      TriggerType
	CommitmentID string
	Prefs        UserPrefs
	Cadence      Cadence
	// History 是窗口内的 proactive 行，窗口长度由 ProactivePolicy.HistoryDays 决定。
	History []ProactiveSendRow
	Now     time.Time
}

// FrequencyVerdict 是频次/疲劳的结论。
type FrequencyVerdict struct {
	OK      bool
	Reason  RejectReason
	Detail  string
	Sent24h int
}

// CheckFrequency 依次判：档位停推 → 当日条数 → 档内最小间隔 → 档位总额度 →
// 承诺回访（周上限 + 同承诺上限）→ 可选的两条最小间隔。
//
// 计数只算**已发送**（sent_at 非空）的行：eligible/scheduled 还没吵到用户，
// 把它们计入会让发送时刻的二次校验把同一封消息算成两条（P-10）。
// 「用户主动发起的对话不计入主动消息额度」（04:151）由数据面天然满足：
// proactives 只有 AI 主动行，用户消息在 chat_messages。
func (p ProactivePolicy) CheckFrequency(in FrequencyInput) FrequencyVerdict {
	dayStart := localDayStartUTC(in.Now, in.Prefs.Timezone)
	dayEnd := dayStart.AddDate(0, 0, 1)
	weekStart := localWeekStartUTC(in.Now, in.Prefs.Timezone)
	// 周窗是本地周一 00:00 起**整 7 天**（commitment_service.py:250-256 的
	// 「本周」）；写成 +1 天会让「每周 2 条回访」退化成「每天 2 条」。
	weekEnd := weekStart.AddDate(0, 0, 7)

	var sent24h, sentWeek, sentCommitment int
	lastSent := time.Time{}
	for _, row := range in.History {
		if !row.HasSentAt {
			continue
		}
		if !row.SentAt.Before(dayStart) && row.SentAt.Before(dayEnd) {
			sent24h++
		}
		if !row.SentAt.Before(weekStart) && row.SentAt.Before(weekEnd) {
			sentWeek++
		}
		if in.CommitmentID != "" && row.TriggerType == string(TriggerCommitmentFollowup) &&
			historyMentionsCommitment(row, in.CommitmentID) {
			sentCommitment++
		}
		if row.SentAt.After(lastSent) {
			lastSent = row.SentAt
		}
	}

	verdict := func(reason RejectReason, detail string) FrequencyVerdict {
		return FrequencyVerdict{OK: false, Reason: reason, Detail: detail, Sent24h: sent24h}
	}

	if in.Cadence == CadenceStopped {
		return verdict(ReasonFatigueStopped, fmt.Sprintf(
			"连续沉默 %d 天（阶梯 stop=%d），停止主动触达，等待用户主动回归（closenai.html:674）",
			p.SilentStopDays, p.SilentStopDays))
	}
	if sent24h >= p.MaxPerDay {
		return verdict(ReasonFrequencyCap, fmt.Sprintf(
			"本地日内已发 %d 条，上限 %d（closenai.html:596 + journey_service.py:272-282）",
			sent24h, p.MaxPerDay))
	}
	if in.Cadence == CadenceWeekly && !lastSent.IsZero() {
		minNext := lastSent.AddDate(0, 0, p.WeeklyIntervalDays)
		if in.Now.Before(minNext) {
			return verdict(ReasonFrequencyCap, fmt.Sprintf(
				"疲劳档 %s 要求每周至多 1 次，下次不早于 %s",
				in.Cadence, minNext.UTC().Format(time.RFC3339)))
		}
	}
	limit := p.TierDailyAI(in.Prefs.Tier)
	if limit > 0 && in.Prefs.QuotaUsed >= limit {
		return verdict(ReasonFrequencyCap, fmt.Sprintf(
			"档位 %s 的每日 AI 主动总额度 %d 已用尽（已用 %d，closenai.html:590/config.py:138-143）",
			in.Prefs.Tier, limit, in.Prefs.QuotaUsed))
	}
	if in.Trigger == TriggerCommitmentFollowup {
		if in.CommitmentID != "" && sentCommitment >= p.CommitmentMaxFollowUps {
			return verdict(ReasonFrequencyCap, fmt.Sprintf(
				"同一承诺已回访 %d 次，上限 %d（closenai.html:2953）",
				sentCommitment, p.CommitmentMaxFollowUps))
		}
		if sentWeek >= p.CommitmentWeeklyLimit {
			return verdict(ReasonFrequencyCap, fmt.Sprintf(
				"本周回访已达 %d 条，上限 %d（closenai.html:2953）",
				sentWeek, p.CommitmentWeeklyLimit))
		}
	}
	if p.MinIntervalHours > 0 && !lastSent.IsZero() {
		minNext := lastSent.Add(time.Duration(p.MinIntervalHours) * time.Hour)
		if in.Now.Before(minNext) {
			return verdict(ReasonFrequencyCap, fmt.Sprintf(
				"距上次发送不足 %d 小时，下次不早于 %s",
				p.MinIntervalHours, minNext.UTC().Format(time.RFC3339)))
		}
	}
	return FrequencyVerdict{OK: true, Sent24h: sent24h}
}

// sentOnLocalDay 统计该用户本地日历日内**已发送**的 proactive 条数。
// 口径与现行链 has_sent_on（journey_repository.py:710-716）一致：只数 sent_at，
// 不数 scheduled/eligible——还没吵到用户的不占「每日一条」的坑（P-10）。
func (p ProactivePolicy) sentOnLocalDay(history []ProactiveSendRow, at time.Time,
	timezone string) int {
	start := localDayStartUTC(at, timezone)
	end := start.AddDate(0, 0, 1)
	var n int
	for _, row := range history {
		if row.HasSentAt && !row.SentAt.Before(start) && row.SentAt.Before(end) {
			n++
		}
	}
	return n
}

// historyMentionsCommitment 判断历史那一封回访是不是同一个承诺。
//
// 引用清单在 eligibility_detail 的 packet 里（commitment 没有回链表，见
// canonical.sql 的 ListProactiveHistory 注释）。packet 解析不出来时**算命中**：
// 「同一承诺最多 2 次回访」（closenai.html:2953）是反骚扰上限，证明不了它是别的
// 承诺就不能再多发一次——宁可少发，不能骚扰。
func historyMentionsCommitment(row ProactiveSendRow, commitmentID string) bool {
	if row.Detail == "" {
		return true
	}
	packet := EligibilityPacket{}
	if err := json.Unmarshal([]byte(row.Detail), &packet); err != nil {
		return true
	}
	return contains(packet.RefIDs, commitmentID)
}

// SilenceDays 算连续沉默天数：有用户消息按最后一条算，从没发过则按注册日算
// （journey_service.py:370-378 的同一 fallback）。未来时刻按 0 截。
func SilenceDays(now, lastUserActivity, userCreatedAt time.Time, timezone string) int {
	base := lastUserActivity
	if base.IsZero() {
		base = userCreatedAt
	}
	if base.IsZero() {
		return 0
	}
	days := int(localDayStartUTC(now, timezone).Sub(localDayStartUTC(base, timezone)).Hours() / 24)
	if days < 0 {
		days = 0
	}
	return days
}

// normalizeMessage 把文本折成可比较形态：压空白、去尾部标点、转小写。
// 04 §11 的 Duplicate 门没给相似度定义 ⇒ 只判**精确重复**（保守：判不出相似
// 不算重复，避免把合法的再次回访一句毙掉）。
func normalizeMessage(text string) string {
	joined := strings.Join(strings.Fields(text), " ")
	joined = strings.TrimRight(joined, "。！?？.!?\u3000 \n")
	return strings.ToLower(joined)
}

// duplicateOf 在窗口内找同一句话，命中即返回那一行的 id（落 DuplicateOfID 可解释）。
// 窗口起点取本地日界回推 DuplicateWindowDays 天——现行链的「今日去重」
// （cron_service.py:402-408）是它的子集。
func (p ProactivePolicy) duplicateOf(message string, history []ProactiveSendRow,
	now time.Time, timezone string) string {
	normalized := normalizeMessage(message)
	if normalized == "" {
		return ""
	}
	windowStart := localDayStartUTC(now, timezone).AddDate(0, 0, -p.DuplicateWindowDays)
	hit := ""
	hitAt := now.AddDate(100, 0, 0)
	for _, row := range history {
		at := row.SentAt
		if !row.HasSentAt {
			if !row.HasScheduledAt {
				continue
			}
			at = row.ScheduledAt
		}
		if at.Before(windowStart) || normalizeMessage(row.Message) != normalized {
			continue
		}
		if at.Before(hitAt) {
			hit, hitAt = row.ID, at
		}
	}
	return hit
}
