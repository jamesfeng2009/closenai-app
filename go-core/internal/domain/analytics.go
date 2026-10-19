// N8 批 1（analytics 域）的判定层：匿名化白名单。
//
// 红线出处 backend/services/analytics_service.py:12-16——**只允许行为元数据出网**，
// 反思文本/记忆/对话原文/情绪具体值一律丢弃。灰度期两侧都在跑，
// 一侧多放行一个键就等于给用户数据开了第二条出口，
// 所以这张表由 backend/tests/test_n8_batch1_contract.py 逐字对 Python 的字面量。
package domain

// AllowedPropertyKeys 逐字对照 analytics_service.py::ALLOWED_PROPERTY_KEYS。
var AllowedPropertyKeys = map[string]bool{
	"screen":      true,
	"screen_from": true,
	"screen_to":   true,
	"section":     true,
	"feature":     true,
	"action":      true,
	"source":      true,
	"trigger":     true,
	"result":      true,
	// 失败原因的机器码（T1-04 付费交付可观测）；只允许枚举值，不许塞自由文本。
	"reason":             true,
	"duration_ms":        true,
	"count":              true,
	"value":              true,
	"tier":               true,
	"language":           true,
	"platform":           true,
	"app_version":        true,
	"locale":             true,
	"is_new_user":        true,
	"streak_count":       true,
	"relationship_level": true,
}

// 过滤动作本身在 httpapi（它才知道 JSON 值的原文字节），这里只持有这张表：
// 「哪些键可以出网」是业务红线，不是 HTTP 细节。
