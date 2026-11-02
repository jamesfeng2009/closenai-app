// T7-03：MQ 体内身份键的归一与拒绝（两条入站面共用）。
//
// 归属只认受信 header（`x-user-id`，15:49 的 Envelope 口径）。体内出现任何读起来像
// user id 的顶层键都必须拒绝，而不是交给 protojson／DisallowUnknownFields 各给一个码：
// 那样同一个「体内自报归属」的尝试在候选面记 `candidate_payload_invalid`、在事件面记
// `payload_not_json`，值班要维护三份记忆才能认出同一件事。
//
// 归一规则与 Python 侧 `contract.py:normalise_identity_key` 逐条同形：
// 小写 → 剥掉 `_` 与 `-` → 等于 `userid`。跨语言用例表是
// `contracts/mq_identity_cases.json`，两侧测试读同一份文件。
//
// ⚠️ 只扫**顶层键**：归属是 envelope 级事实，嵌套键属于各域自己的载荷。
// 事件面是唯一例外——`source.deleted` 的 `userId` 是契约上留给 DLQ 对账的痕，
// 只许这一个拼写，其余变体一律按 `foreign_user_id` 拒绝（source_deleted.go 再判头体相等）。
package inbound

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

const identityKeyCanonical = "userid"

func normaliseIdentityKey(key string) string {
	key = strings.ToLower(strings.TrimSpace(key))
	key = strings.ReplaceAll(key, "_", "")
	return strings.ReplaceAll(key, "-", "")
}

func isIdentityKey(key string) bool {
	return normaliseIdentityKey(key) == identityKeyCanonical
}

// foreignIdentityKeys 返回顶层里归一成 user id、但不在 allowed 里的键。
//
// body 不是 JSON 对象时回 nil：语法与形状由调用方的解码器用它自己的 reason 判，
// 这里只负责「合法 JSON 对象里有没有身份键」这一件事。
func foreignIdentityKeys(body []byte, allowed ...string) []string {
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(body, &obj); err != nil {
		return nil
	}
	allow := make(map[string]struct{}, len(allowed))
	for _, key := range allowed {
		allow[key] = struct{}{}
	}
	var out []string
	for key := range obj {
		if !isIdentityKey(key) {
			continue
		}
		if _, ok := allow[key]; ok {
			continue
		}
		out = append(out, key)
	}
	sort.Strings(out)
	return out
}

// rejectForeignIdentity 是两条入站面在解码之前调用的同一道门。
func rejectForeignIdentity(body []byte, allowed ...string) *domain.PermanentRejection {
	keys := foreignIdentityKeys(body, allowed...)
	if len(keys) == 0 {
		return nil
	}
	return reject(ReasonForeignUserID,
		fmt.Sprintf("消息体顶层出现了身份键 %v：归属只认 header %s，"+
			"体内任何身份键变体（user_id／userId／user-id／大小写）都拒绝（T7-03）",
			keys, HeaderUserID))
}
