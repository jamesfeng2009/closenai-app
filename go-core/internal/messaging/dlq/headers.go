// headers 取值面：`x-death` 的形态由 broker 与客户端库决定，不由本包决定。
//
// 这一层唯一的工作是「把可能是四种形状的字段读成一个结构」，并且**读不出就当没有**——
// 不做 `fmt.Sprint` 那种兜底：把一坨结构印成字符串，表现是摘要里多了一条看不懂的东西，
// 而值班拿它判不了「这条为什么死的」。宁可报「没有死信记录」。
package dlq

import (
	"time"

	amqp091 "github.com/rabbitmq/amqp091-go"
)

// deathEntries 把 `x-death` 那一格归一成「一串 map」。
//
// broker／客户端库给的形状不止一种，这里逐字列全（少一形的表现是**读成没有死信记录**，
// 而那份摘要正是值班判断「这条为什么死的」唯一的输入）：
//   - `[]amqp091.Table`：真 broker 经 amqp091-go v1.10.0 解码后的形状（`integration_broker_test.go` 跑实）；
//   - `[]any`（元素是 Table）：headers 被搬进 `map[string]any` 后再往返一次的形状；
//   - `[]map[string]any`／`map[string]any`：夹具与别的客户端摊平后的形状，单格 map 是「只死过一次」；
//   - 其余（字符串、数字、元素不对）一律当「没有死信记录」，不做 `fmt.Sprint` 兜底：
//     把一坨结构印成字符串，表现是摘要里多了一条看不懂的东西，而它据此判不了死因。
func deathEntries(raw any) []map[string]any {
	switch v := raw.(type) {
	case []amqp091.Table:
		out := make([]map[string]any, 0, len(v))
		for _, t := range v {
			m, _ := asTable(t)
			out = append(out, m)
		}
		return out
	case []map[string]any:
		return append([]map[string]any(nil), v...)
	case []any:
		var out []map[string]any
		for _, item := range v {
			if t, ok := asTable(item); ok {
				out = append(out, t)
			}
		}
		return out
	case amqp091.Table:
		m, _ := asTable(v)
		return []map[string]any{m}
	case map[string]any:
		return []map[string]any{v}
	case nil:
		return nil
	default:
		return nil
	}
}

// asTable 把一个 x-death 元素归一成 map。amqp091.Table 本身就是 map[string]any，
// 但类型断言不认「底层类型相同」，所以要显式搬一次。
func asTable(v any) (map[string]any, bool) {
	switch t := v.(type) {
	case amqp091.Table:
		out := make(map[string]any, len(t))
		for k, val := range t {
			out[k] = val
		}
		return out, true
	case map[string]any:
		return t, true
	default:
		return nil, false
	}
}

// str 只认字符串与字节数组两种形态（与 `inbound.Delivery.HeaderString` 同一判据）。
func str(v any) string {
	switch s := v.(type) {
	case string:
		return s
	case []byte:
		return string(s)
	default:
		return ""
	}
}

// count 取 `count` 那格。amqp091 给的是 int64，HTTP 管理与别家客户端可能给 int 或 float，
// 三种都吃；其余形态一律 0（「没数到」与「数是负的」在两回事，负的不是计数）。
func count(v any) int64 {
	switch n := v.(type) {
	case int64:
		return n
	case int:
		return int64(n)
	case int32:
		return int64(n)
	case float64:
		return int64(n)
	default:
		return 0
	}
}

// timeOf 取时刻。broker 在 header 里放的是 time.Time（AMQP 的 timestamp 类型），
// 但经 JSON 往返的夹具给的是 RFC3339 串——两种都吃，第三种（数字秒）不吃：
// 那要么是 Unix 秒要么是 Unix 纳秒，猜错一个数量级就把「三分钟前」读成「1970 年」。
func timeOf(v any) time.Time {
	switch t := v.(type) {
	case time.Time:
		return t.UTC()
	case string:
		parsed, err := time.Parse(time.RFC3339, t)
		if err != nil {
			return time.Time{}
		}
		return parsed.UTC()
	default:
		return time.Time{}
	}
}

// keys 取 `routing-keys`（AMQP 里是 array 类型，元素是 short_string）。
func keys(v any) []string {
	switch list := v.(type) {
	case []string:
		return append([]string(nil), list...)
	case []any:
		out := make([]string, 0, len(list))
		for _, item := range list {
			s := str(item)
			if s != "" {
				out = append(out, s)
			}
		}
		return out
	case string:
		// 只有一条键时有些客户端会摊平成串；这是**退化**形态，只在没有别的可用信息时用。
		if list == "" {
			return nil
		}
		return []string{list}
	default:
		return nil
	}
}
