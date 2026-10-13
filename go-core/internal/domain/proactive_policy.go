package domain

// ProactivePolicy 是 N5 的全部数值门限。
//
// 事实源裁定（七-4 P-4）：这些数字来自 **PRD + 现行 Python 频控链**，不是 V3 04 篇。
// V3 的 1/24h·3/7d·8/30d 与 22:00–08:00 只作交叉核对写在注释里，因为它们与
// PRD 的「免费用户每日 5 条 AI 主动总额度」（closenai.html:590）和
// 「睡前复盘 22:00–23:30 固定推送」（:2902）正面冲突——D-4 已拍 PRD 为准。
//
// 每个字段都可被 env 覆盖：标定后只换部署环境变量，代码零改动（G-5 同口径）。
type ProactivePolicy struct {
	// MinLayers：PRD 四层公式「至少满足 2 层才发送」（closenai.html:637-638）。
	MinLayers int
	// SensitiveMinLayers：04:139「Sensitive topics require stricter policy」只给了
	// 名词没给数，取「比常规多一层」的可执行形式（≥3）。
	SensitiveMinLayers int

	// 分类型置信下限（04 §6:131-137 原值，交叉核对用）。
	ConfidenceMinMemoryRecall float64
	ConfidenceMinPattern      float64
	// ConfidenceMinDefault 覆盖 goal/commitment/outcome_check（04 原值 0.70）与
	// §6 未给的两类 reflection_prompt/milestone（七-4 P-3：取已给七类里最宽松的 0.70）。
	ConfidenceMinDefault float64
	// SensitiveConfidenceMin：与 N3 的敏感域下限同值（03 §14 Sensitive ≥0.85）。
	SensitiveConfidenceMin float64

	// MinEvidenceMemoryRecall：04:109-110「1 strong source」。
	MinEvidenceMemoryRecall int
	// StrongEvidenceMinStrength：「strong」在 V3 无量 ⇒ 取 N4 的 B 级下界 0.65
	// （03 §6:152-155 的 A/B 分界是原值，B 及以上算强支撑）。
	StrongEvidenceMinStrength float64
	// MinIndependentPatternFollowup：04:112-115「1 active Pattern + ≥2 independent
	// evidence records」。独立条数用 N4 的同源/同日去重口径算，不采信提案。
	MinIndependentPatternFollowup int
	// MinEvidenceReflection：七-4 P-3 给 reflection_prompt 的下限。
	MinEvidenceReflection int

	// MaxPerDay：主动触达 1 条/日（closenai.html:596；现行链同一口径
	// journey_service.py:272-282「今日已有剧本触达／今日已有主动消息」双向让路）。
	MaxPerDay int
	// FreeTierDailyAI / ProTierDailyAI / ProPlusTierDailyAI：P0 统一消息配额
	// （closenai.html:590「免费用户每日 5 条 AI 主动消息（总额度）」，数值与
	// backend/config.py:138-143 的 free 5 / pro 100 / pro_plus 1000 同源）。
	FreeTierDailyAI    int
	ProTierDailyAI     int
	ProPlusTierDailyAI int
	// MinIntervalHours：PRD **没有**两条之间的最小间隔（取证缺口），所以默认 0
	// ＝不设这一门。留 env 口子是为了标定后能开，而不是现在造一个数。
	MinIntervalHours int
	// CommitmentWeeklyLimit / CommitmentMaxFollowUps：closenai.html:2953
	//「每用户每周最多 2 条回访；同一承诺最多 2 次」，与现行链
	// commitment_service.py:39-40 的两个常量同值。
	CommitmentWeeklyLimit  int
	CommitmentMaxFollowUps int

	// 沉默阶梯（closenai.html:674 逐字：连续 2 天→第 3 天简短关心；5 天→降频为
	// 每周 1 次；14 天→停止主动发消息，等待用户主动回归。现行链同一组数：
	// cron_service.py:79-81 SILENT_SHORT_DAYS/SILENT_WEEKLY_DAYS/SILENT_STOP_DAYS）。
	SilentCareDays   int
	SilentWeeklyDays int
	SilentStopDays   int
	// WeeklyIntervalDays：「降频为每周 1 次」的最小间隔形式。
	WeeklyIntervalDays int

	// 静默时段（七-4 P-5）：默认拦本地 00:00–08:00。
	// **不采** V3 的 22:00–08:00：那会把 PRD :2902 的睡前固定推送窗口整段拦掉，
	// 而现行链的 BEDTIME_HOUR=22（cron_service.py:92）就发在那个时段。
	QuietStartHour int
	QuietEndHour   int

	// DuplicateWindowDays：04 §11 的 Duplicate 门没给窗口 ⇒ 取阶梯里的「每周」档，
	// 与现行链的今日去重（cron_service.py:402-408）叠加。
	DuplicateWindowDays int
	// StaleCheckDays：用户在此窗口内纠正过被引用行 ⇒ Freshness 关否决
	// （04:231「user corrected relevant fact」）。V3 无量，属待标定默认值。
	StaleCheckDays int

	// 消息质量（Message Quality 关，04 §11 第 10 门）：
	// closenai.html:663「消息长度：50-150 字，口语化，包含一个开放式提问引导回复」。
	MinMessageRunes int
	MaxMessageRunes int

	// DefaultValidityHours：04 §19 只说沉默之后 proactive → expired，没给时长。
	// 取「一个日内必须重评」的最小形式（24h），env 可改；这是待标定值不是结论。
	DefaultValidityHours int

	// HistoryDays：ListProactiveHistory 的取数窗口。
	//
	// 窗口必须盖住频次关可能参照的最远一行：normal 档只看本地日，weekly 档要看
	// 「上一次发送」（用户沉默 5 天才降档，那次发送可能已在 7 天之前），查重关看
	// DuplicateWindowDays。60 天盖住这三者且有余量；再远的行对「每日 1 条 / 每周 1 次」
	// 的判定必然已经放行，多取只是行数。MinIntervalHours 若标定到超过 60 天要同步放大。
	HistoryDays int
}

// DefaultProactivePolicy 的取值逐条见结构体注释。0 一律表示「该门不设限」，
// 不是我造的门限。
func DefaultProactivePolicy() ProactivePolicy {
	return ProactivePolicy{
		MinLayers:                     envInt("CLOSEN_PROACTIVE_MIN_LAYERS", 2),
		SensitiveMinLayers:            envInt("CLOSEN_PROACTIVE_SENSITIVE_MIN_LAYERS", 3),
		ConfidenceMinMemoryRecall:     envFloat("CLOSEN_PROACTIVE_CONF_MEMORY_RECALL", 0.75),
		ConfidenceMinPattern:          envFloat("CLOSEN_PROACTIVE_CONF_PATTERN", 0.80),
		ConfidenceMinDefault:          envFloat("CLOSEN_PROACTIVE_CONF_DEFAULT", 0.70),
		SensitiveConfidenceMin:        envFloat("CLOSEN_PROACTIVE_CONF_SENSITIVE", 0.85),
		MinEvidenceMemoryRecall:       envInt("CLOSEN_PROACTIVE_MIN_EVIDENCE_MEMORY", 1),
		StrongEvidenceMinStrength:     envFloat("CLOSEN_PROACTIVE_STRONG_EVIDENCE_MIN", 0.65),
		MinIndependentPatternFollowup: envInt("CLOSEN_PROACTIVE_MIN_INDEPENDENT_PATTERN", 2),
		MinEvidenceReflection:         envInt("CLOSEN_PROACTIVE_MIN_EVIDENCE_REFLECTION", 1),

		MaxPerDay:              envInt("CLOSEN_PROACTIVE_MAX_PER_DAY", 1),
		FreeTierDailyAI:        envInt("CLOSEN_QUOTA_FREE_DAILY", 5),
		ProTierDailyAI:         envInt("CLOSEN_QUOTA_PRO_DAILY", 100),
		ProPlusTierDailyAI:     envInt("CLOSEN_QUOTA_PRO_PLUS_DAILY", 1000),
		MinIntervalHours:       envInt("CLOSEN_PROACTIVE_MIN_INTERVAL_HOURS", 0),
		CommitmentWeeklyLimit:  envInt("CLOSEN_COMMITMENT_WEEKLY_LIMIT", 2),
		CommitmentMaxFollowUps: envInt("CLOSEN_COMMITMENT_MAX_FOLLOW_UPS", 2),

		SilentCareDays:     envInt("CLOSEN_SILENT_CARE_DAYS", 2),
		SilentWeeklyDays:   envInt("CLOSEN_SILENT_WEEKLY_DAYS", 5),
		SilentStopDays:     envInt("CLOSEN_SILENT_STOP_DAYS", 14),
		WeeklyIntervalDays: envInt("CLOSEN_PROACTIVE_WEEKLY_INTERVAL_DAYS", 7),

		QuietStartHour: envInt("CLOSEN_PROACTIVE_QUIET_START_HOUR", 0),
		QuietEndHour:   envInt("CLOSEN_PROACTIVE_QUIET_END_HOUR", 8),

		DuplicateWindowDays: envInt("CLOSEN_PROACTIVE_DUPLICATE_WINDOW_DAYS", 7),
		StaleCheckDays:      envInt("CLOSEN_PROACTIVE_STALE_CHECK_DAYS", 30),

		MinMessageRunes: envInt("CLOSEN_PROACTIVE_MIN_MESSAGE_RUNES", 50),
		MaxMessageRunes: envInt("CLOSEN_PROACTIVE_MAX_MESSAGE_RUNES", 150),

		DefaultValidityHours: envInt("CLOSEN_PROACTIVE_VALIDITY_HOURS", 24),
		HistoryDays:          envInt("CLOSEN_PROACTIVE_HISTORY_DAYS", 60),
	}
}

// ConfidenceMinFor 按 trigger type 取置信下限（04 §6:131-137 + 七-4 P-3）。
func (p ProactivePolicy) ConfidenceMinFor(trigger TriggerType, sensitive bool) float64 {
	value := p.ConfidenceMinDefault
	switch trigger {
	case TriggerMemoryRecall:
		value = p.ConfidenceMinMemoryRecall
	case TriggerPatternFollowup:
		value = p.ConfidenceMinPattern
	}
	if sensitive && value < p.SensitiveConfidenceMin {
		// 04:139 的「更严」在敏感域至少不低于 N3 已拍的敏感下限。
		value = p.SensitiveConfidenceMin
	}
	return value
}

// MinLayersFor 取该候选需要的最少层数（P-8：敏感域 ≥3）。
func (p ProactivePolicy) MinLayersFor(sensitive bool) int {
	if sensitive {
		return p.SensitiveMinLayers
	}
	return p.MinLayers
}

// TierDailyAI 按档位取 P0 统一 AI 消息日额度（backend/config.py:138-143 同值）。
func (p ProactivePolicy) TierDailyAI(tier string) int {
	switch tier {
	case "pro":
		return p.ProTierDailyAI
	case "pro_plus":
		return p.ProPlusTierDailyAI
	default:
		return p.FreeTierDailyAI
	}
}
