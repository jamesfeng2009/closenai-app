package aiclient

import (
	_ "embed"
	"encoding/json"
	"unicode/utf8"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// fallbackCorpusJSON 是 backend/data/copy/{locale}.json 里 `msg.fallback` 的 15 语言逐字节副本，
// 由 scripts/gen_go_ai_side.py 录制，`--check` 是它的漂移闸（同一条闸也守 SSE 夹具）。
//
// //go:embed 的形态沿用本仓既有先例 internal/domain/templates/copy_mood.json；
// 之所以必须带副本而不是「让 Python 兜」：这一条路径的定义就是**Python 联系不上**，
// 那时 message_generator.py:136 的就地兜底不会执行，而 V3 13:110-117 §8 逐条要求
// 「user-facing product must degrade gracefully」＋「canonical user data remains available」。
//
//go:embed templates/copy_msg_fallback.json
var fallbackCorpusJSON []byte

// fallbackCorpus 是 locale → 模板数组。值面判据（空数组视为没有）与 copy.py:78-90 的
// t_list 一致：目标语言 → en → 没有。
var fallbackCorpus = func() map[string][]string {
	var raw struct {
		Note     string              `json:"note"`
		Locales  []string            `json:"locales"`
		Fallback map[string][]string `json:"fallback"`
	}
	if err := json.Unmarshal(fallbackCorpusJSON, &raw); err != nil {
		panic("templates/copy_msg_fallback.json 解析失败: " + err.Error())
	}
	// 生成物坏了要在启动时就炸而不是等到降级那一刻：降级路径本来就是最不容易被测到的路，
	// 让它带着一份坏副本上线，等于把这条兜底换成了一个更深的坑。
	if len(raw.Fallback) == 0 {
		panic("templates/copy_msg_fallback.json 的 fallback 是空的：重跑 scripts/gen_go_ai_side.py")
	}
	return raw.Fallback
}()

// FallbackReply 是 message_generator.py:453-458 的 `_fallback_reply` 同形实现：
//
//	templates = copy.t_list("msg", "fallback", language)
//	idx = len(user_input) % len(templates)
//	return templates[idx]
//
// ⚠️ 唯一一处不能照抄的是 `len()`：Python 的 len(str) 数**码点**，Go 的 len(string) 数**字节**。
// 一句「我今天很难过」在 Python 是 6、在 Go 的 len() 是 18 ⇒ 同一段中文在两侧轮转到不同模板，
// 而这种分叉没有任何用户可见的报错。用 utf8.RuneCountInString 才是同形，
// TestFallbackRotationMatchesPythonCodePointCount 是它的闸。
//
// 返回 ok=false 表示这份副本里连 en 都是空的——调用方必须报错而不是发空串
// （对齐 Python：t_list 兜底给 [] 之后 `% 0` 当场 ZeroDivisionError → chat.py:137 的 error 事件）。
func FallbackReply(userInput, language string) (string, bool) {
	templates := fallbackTemplates(language)
	if len(templates) == 0 {
		return "", false
	}
	return templates[utf8.RuneCountInString(userInput)%len(templates)], true
}

// fallbackTemplates 走 copy.py:78-90 的三级回退：目标语言 → en → 空。
// 语言码归一复用 domain.CopyLocaleFor（core/copy.py:33-41 的同一条前缀匹配表），
// 不在这里再造第二张表——两张表一定会漂，而漂了也没人看得见。
func fallbackTemplates(language string) []string {
	if v := fallbackCorpus[domain.CopyLocaleFor(language)]; len(v) > 0 {
		return v
	}
	return fallbackCorpus["en"]
}
