// N8 批 4（onboarding 域）的静态文案出口闸：`safety_service.filter_template` 的 Go 复刻。
//
// 出处逐字对应 backend/services/safety_service.py 的四处：
//
//	_lang_key        (:26-28)   ——**不做 lower**，与 first_read_service.locale_for 不同
//	BANNED_TERMS     (:224-242) ——zh 走 `in`（纯子串），en 走 `\b{term}`（前边界、无后边界）
//	ATTRIBUTION_MARKERS (:244-268) ——(third∧label)∨(third∧causal) 才判越界
//	filter_template  (:412-429) ——空/纯空白⇒""；命中⇒""（调用方自行降级）
//
// 这一层**零 LLM**（:415-416 的注释写明「模板由我方撰写、词表对它有 100% 把握」），
// 所以它是可以逐字复刻、也能逐条穷举验证的：闭包语料闸见 httpapi/n8_template_corpus_test.go。
package domain

import (
	"regexp"
	"strings"
	"unicode/utf8"
)

// 两份词表按 locale 取，键集合与 Python 的两个顶层键一字不差。
// 条目顺序也是 Python 的书写顺序：命中哪一条会进日志，判据本身只看有无命中。
var bannedTerms = map[string][]string{
	"zh": {
		"焦虑症", "抑郁症", "治疗", "疗愈", "诊断", "缓解焦虑", "处方", "康复", "心理健康服务",
		"星座配对", "星座运势", "爱情语言", "爱的五种语言", "话术", "PUA",
	},
	"en": {
		"anxiety disorder", "depression", "depressive", "treatment", "heal",
		"diagnose", "diagnosis", "prescription", "medication", "rehab", "mental health service",
		"zodiac compatibility", "star sign", "horoscope", "fortune telling", "love language",
		"pick-up artist", "pua",
	},
}

// 越界句式三组指纹。字面量必须与 Python 的元组**逐字相同**（含 (?<!…) 那一条），
// Python 侧的跨语言闸读的就是这里的字符串。
var attributionMarkers = map[string]map[string][]string{
	"zh": {
		"third": {"TA", `(?<![其这那每])他`, "她", "对方", "男朋友", "女朋友", "伴侣",
			"老公", "老婆", "前任", "另一半"},
		"label": {"焦虑型", "回避型", "安全型", "依恋", "MBTI", "九型", "星座",
			"人格类型", "性格类型", "爱情语言"},
		"causal": {"导致", "致使", "注定", "说明", "因此", "所以", "因为"},
	},
	"en": {
		"third": {`\bhe\b`, `\bshe\b`, `\bhim\b`, `\bher\b`, `\bthey\b`, `\bpartner\b`,
			`\bex\b`, `\bboss\b`, `\bcolleague\b`},
		"label": {`attachment style`, `anxious attach`, `avoidant`, `\bmbti\b`, `enneagram`,
			`zodiac`, `love language`, `personality type`},
		"causal": {`\bbecause\b`, `\btherefore\b`, `\bmeans\b`, `\bproves\b`, `\bcauses?\b`,
			`\bleads? to\b`, `\bis why\b`, `\bdestined\b`},
	},
}

// markerMatcher 是一条指纹的可执行形态。
//
// lookaway 非空时它来自 `(?<![其这那每])他` 这一条：**RE2 不支持后视断言**，
// `regexp.MustCompile` 会直接 panic。这里按定义实现（Python 的 lookbehind 在这一条里
// 恰好只看一个字符，所以手写等价）：尾串每处匹配都要看它**前一个 rune**，
// 落在禁用集合内就不算命中；行首没有前一个 rune ⇒ 等价于「不在集合内」⇒ 算命中。
type markerMatcher struct {
	pattern  string
	re       *regexp.Regexp
	lookaway string
	tail     *regexp.Regexp
}

func (m markerMatcher) hit(text string) bool {
	if m.re == nil {
		for _, at := range m.tail.FindAllStringIndex(text, -1) {
			if at[0] == 0 {
				return true
			}
			prev, _ := utf8.DecodeLastRuneInString(text[:at[0]])
			if !strings.ContainsRune(m.lookaway, prev) {
				return true
			}
		}
		return false
	}
	return m.re.MatchString(text)
}

var (
	bannedZH = bannedTerms["zh"]
	bannedEN = compileBanned(bannedTerms["en"])
	// 两侧都以「编译一次」收口：Python 每条都现场 re.search（它自己带缓存），
	// 语义上是同一件事——IGNORECASE 对同一模式不换命中集合。
	compiledMarkers = compileMarkers(attributionMarkers)
)

func compileBanned(terms []string) []*regexp.Regexp {
	out := make([]*regexp.Regexp, 0, len(terms))
	for _, term := range terms {
		// Python 写的是 rf"\b{re.escape(term)}"：**只有前边界**。
		// 补上后边界会把 "heal" 从 "healing" 里放出来，那是改判据不是复刻。
		//
		// 已证的跨语言发散（2026-10-15 两侧实测）：`\b` 的词字符集不同。Python 的
		// `\w` 默认 Unicode，CJK 算词字符 ⇒ `re.search(r"\bhe\b", "你好he你好")` 得 None；
		// Go RE2 的 `\b` 只认 ASCII 词字符 ⇒ 同一句命中等价于 true。也就是说
		// 「中文卡里夹一个裸 he/her」这类输入，Python 放行、Go 会拦。
		// 本域闸的入参只有两份模板生成的断言/filler/closer/summary（模板 sha256 由
		// #18 的跨语言闸钉住），这些文本里没有「CJK 紧贴英文代词」的形状，所以今天没有
		// 观测点；改模板时由 #16 的 template_corpus 逐条闭包证等来兜。
		out = append(out, regexp.MustCompile(`(?i)\b`+regexp.QuoteMeta(term)))
	}
	return out
}

// splitLookbehind 认得 Python 里唯一用到的那一种后视形状：`(?<!单字符集)` 前缀。
// 出现别的形状（多字符定长、正向后视）时返回 ok=false，调用方直接编译——
// 那时 MustCompile 会 panic 而不是静默给一条错判据，这是刻意的失败方式。
func splitLookbehind(pattern string) (lookaway, tail string, ok bool) {
	const prefix = `(?<!`
	if !strings.HasPrefix(pattern, prefix) {
		return "", "", false
	}
	rest := pattern[len(prefix):]
	end := strings.Index(rest, "])")
	if end < 0 {
		return "", "", false
	}
	return rest[:end], rest[end+2:], true
}

func compileMarker(pattern string) markerMatcher {
	m := markerMatcher{pattern: pattern}
	if lookaway, tail, ok := splitLookbehind(pattern); ok {
		m.lookaway = lookaway
		m.tail = regexp.MustCompile(`(?i)` + tail)
		return m
	}
	m.re = regexp.MustCompile(`(?i)` + pattern)
	return m
}

func compileMarkers(source map[string]map[string][]string) map[string]map[string][]markerMatcher {
	out := make(map[string]map[string][]markerMatcher, len(source))
	for locale, groups := range source {
		compiled := make(map[string][]markerMatcher, len(groups))
		for group, patterns := range groups {
			list := make([]markerMatcher, 0, len(patterns))
			for _, pattern := range patterns {
				list = append(list, compileMarker(pattern))
			}
			compiled[group] = list
		}
		out[locale] = compiled
	}
	return out
}

// SafetyLangKey 是 `_lang_key`(:26-28)：只有**以小写 zh 开头**才算 zh。
//
// 它与 LocaleFor 不是一支函数：那边 `language.lower()` 后才比前缀，这边没有 lower
// ⇒ `"ZH"` 在这里落 en、在那里落 zh。本域走不到这个差（first_read 传给闸的永远是
// LocaleFor 的返回值 zh|en），但两支函数不能合并成一支——合并会改掉另一条调用路的判据。
func SafetyLangKey(language string) string {
	if strings.HasPrefix(language, "zh") {
		return "zh"
	}
	return "en"
}

func bannedHit(text, locale string) bool {
	if locale == "zh" {
		for _, term := range bannedZH {
			if strings.Contains(text, term) {
				return true
			}
		}
		return false
	}
	for _, re := range bannedEN {
		if re.MatchString(text) {
			return true
		}
	}
	return false
}

func groupHit(list []markerMatcher, text string) bool {
	for _, m := range list {
		if m.hit(text) {
			return true
		}
	}
	return false
}

// attributionHit 是 :337-354：第三方指称 +(类型标签 | 因果连接) 才算越界。
// 「标签∧因果但没有第三方」刻意不算——那是 Python 注释(:341-346)写明的口径。
func attributionHit(text, locale string) bool {
	groups := compiledMarkers[locale]
	third := groupHit(groups["third"], text)
	if !third {
		return false
	}
	return groupHit(groups["label"], text) || groupHit(groups["causal"], text)
}

// FilterTemplate 是 `filter_template`(:412-429)：命中即返回空串，由调用方降级。
//
// 空白判定走 PyTrimSpace 而不是 strings.TrimSpace：Python 是 `if not text or not text.strip()`，
// 而 `str.strip()` 认的 001C-001F 四个码位在 Go 的 unicode.IsSpace 里是 false（实测见
// pytext.go）。本域的闸入参全部来自两份模板（不是请求原文），所以这一支今天碰不到；
// 但判据抄错不会在今天显现，只在模板里出现一个 \x1c 时显现——按实测集合实现，不留这种延迟。
func FilterTemplate(text, language string) string {
	if PyTrimSpace(text) == "" {
		return ""
	}
	locale := SafetyLangKey(language)
	if bannedHit(text, locale) || attributionHit(text, locale) {
		return ""
	}
	return text
}
