// N8 批 1（discovery 域）的判定层：每日发现的对比口径与文案分级。
//
// 逐字对照 backend/services/discovery_service.py，两侧在灰度期会同时在线（同一用户
// 可能被任一侧回答），所以阈值、分支次序、文案与 emoji 分值都必须一致——
// 「同一条数据两边读出不同的话」是切流期最贵的一类 bug，
// 由 go-core/testdata/n8_contract.json 的逐字节回放钉住（internal/httpapi 的 N8 契约测）。
//
// 本文件不碰 HTTP、不碰 DB（G-7 依赖闸），输入是已经按窗口取好的签到行。
package domain

import (
	"fmt"
	"strings"
	"time"
)

// 阈值出处 backend/services/discovery_service.py:23-26（数值一字不改）。
const (
	MoodShiftHigh    = 0.8 // ≥0.8：新发现模式（显著）
	MoodShiftLow     = 0.4 // 0.4≤Δ<0.8：轻微漂移（低价值，主动消息里跳过）
	FreqDiffHigh     = 3   // |Δ|≥3：签到频率大幅变化
	FreqDiffMedium   = 2   // |Δ|=2：中等变化
	DiscoveryWeekGap = 7   // 本周/上周的分界（天）
)

// MoodValue 是 MOOD_MAP 的情绪分值（backend/services/mood_service.py:50-57）。
// 未登记的 emoji 取默认 3：Python 侧是 `MOOD_MAP.get(emoji, (None, 3))[1]`，
// 「未知情绪」既不算 5 也不算 0，均值里当一个中性样本。
func MoodValue(emoji string) float64 {
	if v, ok := moodValues[emoji]; ok {
		return v
	}
	return 3
}

var moodValues = map[string]float64{
	"😊": 5,
	"😌": 4,
	"😰": 2,
	"😢": 1,
	"😡": 1,
	"😴": 3,
}

// Checkin 是发现判定需要的一行签到（其余列不参与计算，故不搬）。
type Checkin struct {
	Emoji     string
	CreatedAt time.Time
}

// Discovery 是一条每日发现文案及其分级；nil = 「无显著变化，不发」。
type Discovery struct {
	Content        string
	ComparisonType string
	Priority       string
}

// CompareWeeks 是 discovery_service.py::_compare 的逐字移植。
//
// 分支次序就是优先级：记录中断 > 里程碑 > 频率变化 > 情绪显著偏移 > 轻微漂移 > 无。
// language 只有「zh 开头」与「其它」两条文案，非中英文一律走英文（实测见契约夹具
// discovery-es-header-overrides-zh-db 那条）。
func CompareWeeks(thisWeek, lastWeek []Checkin, language string) *Discovery {
	zh := strings.HasPrefix(language, "zh")
	if len(thisWeek) == 0 && len(lastWeek) == 0 {
		return nil
	}
	if len(thisWeek) == 0 {
		return &Discovery{
			Content:        pickCopy(zh, "最近一周没有再记录心情了，要不要花一分钟说说今天怎样？", "You haven't recorded your mood this week — want to take a minute to share how today is going?"),
			ComparisonType: "checkin_frequency",
			Priority:       "medium",
		}
	}
	if len(lastWeek) == 0 {
		return &Discovery{
			Content:        pickCopy(zh, "这一周你开始记录心情了，这是个很好的开始。", "You started recording your mood this week — that's a great start."),
			ComparisonType: "checkin_frequency",
			Priority:       "high",
		}
	}

	countDiff := len(thisWeek) - len(lastWeek)
	if absInt(countDiff) >= FreqDiffMedium {
		priority := "medium"
		if absInt(countDiff) >= FreqDiffHigh {
			priority = "high"
		}
		zhDirection, enDirection := "更少", "fewer"
		if countDiff > 0 {
			zhDirection, enDirection = "更多", "more"
		}
		return &Discovery{
			Content: pickCopy(zh,
				fmt.Sprintf("这一周你记录了 %d 次心情，比上周%s，是最近生活有变化吗？", len(thisWeek), zhDirection),
				fmt.Sprintf("You recorded your mood %d times this week, %s than last week — has something changed in your life lately?", len(thisWeek), enDirection)),
			ComparisonType: "checkin_frequency",
			Priority:       priority,
		}
	}

	moodDiff := averageValue(thisWeek) - averageValue(lastWeek)
	switch {
	case moodDiff >= MoodShiftHigh:
		return &Discovery{
			Content:        pickCopy(zh, "这一周的整体心情比上周好了不少，是什么样的好事发生了？", "Your overall mood this week is quite a bit better than last week — did something good happen?"),
			ComparisonType: "mood_improvement",
			Priority:       "high",
		}
	case moodDiff <= -MoodShiftHigh:
		return &Discovery{
			Content:        pickCopy(zh, "这一周的心情比上周低落了一些，想聊聊发生了什么吗？", "Your mood this week has dipped a little compared to last week — want to talk about what happened?"),
			ComparisonType: "mood_decline",
			Priority:       "high",
		}
	case moodDiff >= MoodShiftLow:
		return &Discovery{
			Content:        pickCopy(zh, "这一周的心情比上周略有起色，是什么让一切轻快了一点？", "Your mood this week is a touch brighter than last week — what's been lifting things?"),
			ComparisonType: "mood_improvement",
			Priority:       "low",
		}
	case moodDiff <= -MoodShiftLow:
		return &Discovery{
			Content:        pickCopy(zh, "这一周的心情比上周略微低了一点，最近是不是有点累？", "Your mood this week has dipped just slightly compared to last week — have you been a bit tired lately?"),
			ComparisonType: "mood_decline",
			Priority:       "low",
		}
	}
	return nil
}

func averageValue(rows []Checkin) float64 {
	var sum float64
	for _, row := range rows {
		sum += MoodValue(row.Emoji)
	}
	return sum / float64(len(rows))
}

func pickCopy(zh bool, zhText, enText string) string {
	if zh {
		return zhText
	}
	return enText
}

func absInt(n int) int {
	if n < 0 {
		return -n
	}
	return n
}
