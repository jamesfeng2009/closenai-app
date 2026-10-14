// POST /api/v1/onboarding/first-read 的请求体校验（N8 批 4）。
//
// 复刻的是 backend/models/schemas.py:489-516 的 `FirstReadAnswer` ＋ `FirstReadRequest`，
// 错误面按 **CPython 实测**而不是按形状猜（2026-10-15 用 ./.venv/bin/python 现跑，
// 十组用例的字节落进夹具后由 TestN8OnboardingContractBytes 逐字回放）。实测要点：
//
//   - 三个字段**各自**校验：`{"answers":"x","persona_type":1,"language":2}` 一次回
//     三条错误，顺序就是声明序 answers → persona_type → language。
//     这与批 1 的 analytics 不同源（那边只有一个字段），别照抄「第一错即返回」。
//   - `answers` 越界（>10）时**不再**逐条校验，只回一条 too_long（ctx 三键
//     field_type/max_length/actual_length）——与批 1 的列表同一支判据。
//   - 列表项不是对象时与批 1 的 events **同形**：type=model_attributes_type、
//     msg 是 "Input should be a valid dictionary or object to extract fields from"、
//     无 ctx。先前按「嵌套模型该回 model_type＋class_name」的推理写错过一次，
//     由夹具 onb-answer-item-not-dict 的逐字节比对打红后按实测改正。
//   - `string_too_long` 的 ctx 只有 max_length（无 actual_length），与批 3 同源。
//   - 多余的键被忽略（extra='ignore'）⇒ `{"q":..,"option":..,"text":..}` 里那个 text
//     既不进模型也不进落库文本，这条同时证明了 `_resolve_option` 的「按文案反查」支不可达。
//   - `persona_type` 的归一是 `v.strip().upper()` 后才 `re.fullmatch(r"[A-Z]{4}")`，
//     而 ValueError 的文案回显的是**原始 v**（未 strip、未 upper）。
//     strip/upper 两支都按实测集合走 PyTrimSpace/PyUpper（见 domain/pytext.go）：
//     "\x1cINTJ"⇒200 "INTJ"、"ßß"⇒200 "SSSS"，用 strings.* 都会错成 422。
package httpapi

import (
	"fmt"
	"regexp"
	"unicode/utf8"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// 三道快问与语言的约束，全部取自 schemas.py:496-506 的 Field 参数。
const (
	maxFirstReadAnswers = 10
	maxFirstReadQ       = 30
	maxFirstReadOption  = 40
	maxFirstReadLang    = 10
)

// personaPattern 对应 schemas.py:514 的 `re.fullmatch(r"[A-Z]{4}", upper)`。
// Go 的 RE2 在没有 (?m) 时 `$` 就是「文本末尾」（不是「末尾换行之前」），
// 所以 ^…$ 与 Python 的 fullmatch 同形；字符类 [A-Z] 两侧都只认 ASCII 五个字母段。
var personaPattern = regexp.MustCompile(`^[A-Z]{4}$`)

// firstReadBody 是校验通过后的请求。
//
// PersonaType 用空串表示 Python 的 None（缺键、null、纯空白三种假值都归到这里，
// 与 schemas.py:511-512 的 `if v is None or not v.strip(): return None` 同一支）；
// 生成层与响应层各自决定写成 null 还是字符串，所以这里不带 has 位。
type firstReadBody struct {
	Answers     []domain.FirstReadAnswerItem
	PersonaType string
	Language    string
}

// validateFirstReadBody 返回通过校验的请求，或 pydantic 的错误列表（非空表示拒绝）。
func validateFirstReadBody(raw []byte, contentType string) (*firstReadBody, []pyFieldError) {
	if len(raw) == 0 {
		return nil, []pyFieldError{missingBody()}
	}
	if !isJSONContentType(contentType) {
		return nil, []pyFieldError{{
			kind:  "model_attributes_type",
			loc:   []any{"body"},
			msg:   "Input should be a valid dictionary or object to extract fields from",
			input: pyStringValue(pyBytesRepr(raw)),
		}}
	}
	body, err := pyJSONLoads(raw)
	if err != nil {
		return nil, []pyFieldError{jsonInvalidPyEntry(err)}
	}
	switch body.kind {
	case pyNull:
		// FastAPI 把空 body 与 JSON null 都当成「没给 body」⇒ missing（与批 1/批 3 同形）。
		return nil, []pyFieldError{missingBody()}
	case pyObject:
	default:
		return nil, []pyFieldError{{
			kind:  "model_attributes_type",
			loc:   []any{"body"},
			msg:   "Input should be a valid dictionary or object to extract fields from",
			input: body,
		}}
	}

	out := &firstReadBody{Language: "en"}
	var entries []pyFieldError

	// 三个字段按声明序各判各的：任何一支出错都不短路掉后面两支（实测 multi 用例）。
	answerEntries, answers := firstReadAnswersField(body)
	entries = append(entries, answerEntries...)
	out.Answers = answers

	personaEntries, persona := firstReadPersonaField(body)
	entries = append(entries, personaEntries...)
	out.PersonaType = persona

	langEntries, language := firstReadLanguageField(body)
	entries = append(entries, langEntries...)
	out.Language = language

	if len(entries) > 0 {
		return nil, entries
	}
	return out, nil
}

// firstReadAnswersField 是 `answers: List[FirstReadAnswer] = Field(default_factory=list, max_length=10)`。
// 缺键 ⇒ 空列表、无错误（默认值路径）。
func firstReadAnswersField(body *pyValue) ([]pyFieldError, []domain.FirstReadAnswerItem) {
	value, present := body.get("answers")
	if !present {
		return nil, nil
	}
	loc := []any{"body", "answers"}
	if value.kind != pyArray {
		return []pyFieldError{{
			kind: "list_type", loc: loc,
			msg: "Input should be a valid list", input: value,
		}}, nil
	}
	if len(value.items) > maxFirstReadAnswers {
		// 实测（模型层同样成立，不只批 1）：越界时 pydantic 不再逐条校验 item。
		return []pyFieldError{{
			kind: "too_long", loc: loc,
			msg: fmt.Sprintf("List should have at most %d items after validation, not %d",
				maxFirstReadAnswers, len(value.items)),
			input: value,
			ctx: pyObjectOf(
				"field_type", pyStringValue("List"),
				"max_length", pyIntValue(maxFirstReadAnswers),
				"actual_length", pyIntValue(len(value.items)),
			),
		}}, nil
	}

	var entries []pyFieldError
	var out []domain.FirstReadAnswerItem
	for i, item := range value.items {
		answer, itemEntries := firstReadAnswerItem(item, i)
		if len(itemEntries) > 0 {
			entries = append(entries, itemEntries...)
			continue
		}
		out = append(out, answer)
	}
	if len(entries) > 0 {
		return entries, nil
	}
	return nil, out
}

// firstReadAnswerItem 校验列表里的一项；字段按 q、option 的声明序（:496-497）。
func firstReadAnswerItem(item *pyValue, index int) (domain.FirstReadAnswerItem, []pyFieldError) {
	if item.kind != pyObject {
		return domain.FirstReadAnswerItem{}, []pyFieldError{{
			// 与批 1 的 events 列表**同一条**：真 FastAPI 实测 type=model_attributes_type、
			// msg 不提模型名、无 ctx（夹具 onb-answer-item-not-dict）。
			kind:  "model_attributes_type",
			loc:   []any{"body", "answers", index},
			msg:   "Input should be a valid dictionary or object to extract fields from",
			input: item,
		}}
	}
	var entries []pyFieldError
	var out domain.FirstReadAnswerItem
	// 声明序 q、option（schemas.py:496-497）：同一条 item 里两个都缺时，
	// 错误列表里 q 在前——这条顺序由夹具的 onb-answer-both-missing 用例钉住。
	for _, spec := range []struct {
		name string
		max  int
		dest *string
	}{
		{"q", maxFirstReadQ, &out.Q},
		{"option", maxFirstReadOption, &out.Option},
	} {
		value, present := item.get(spec.name)
		loc := []any{"body", "answers", index, spec.name}
		switch {
		case !present:
			entries = append(entries, pyFieldError{
				kind: "missing", loc: loc, msg: "Field required", input: item,
			})
		case value.kind != pyString:
			entries = append(entries, pyFieldError{
				kind: "string_type", loc: loc,
				msg: "Input should be a valid string", input: value,
			})
		case utf8.RuneCountInString(value.str) > spec.max:
			entries = append(entries, pyFieldError{
				kind:  "string_too_long",
				loc:   loc,
				msg:   pluralize("String should have at most", spec.max, "character"),
				input: value,
				ctx:   pyObjectOf("max_length", pyIntValue(spec.max)),
			})
		default:
			*spec.dest = value.str
		}
	}
	return out, entries
}

// firstReadPersonaField 是 `persona_type: Optional[str]` ＋ `_normalize_persona`（:503-516）。
// 缺键与 null 都落 None；纯空白也落 None（`not v.strip()`）；其余必须归一成 [A-Z]{4}。
func firstReadPersonaField(body *pyValue) ([]pyFieldError, string) {
	value, present := body.get("persona_type")
	if !present || value.kind == pyNull {
		return nil, ""
	}
	if value.kind != pyString {
		return []pyFieldError{{
			kind: "string_type", loc: []any{"body", "persona_type"},
			msg: "Input should be a valid string", input: value,
		}}, ""
	}
	// 文案与判据用的不是同一个值：判据看归一后的，文案回显原始 v（:515 的 f-string）。
	trimmed := domain.PyTrimSpace(value.str)
	if trimmed == "" {
		return nil, ""
	}
	upper := domain.PyUpper(trimmed)
	if !personaPattern.MatchString(upper) {
		detail := "非法的性格类型: " + value.str
		return []pyFieldError{{
			kind:  "value_error",
			loc:   []any{"body", "persona_type"},
			msg:   "Value error, " + detail,
			input: value,
			ctx:   pyObjectOf("error", pyStringValue(detail)),
		}}, ""
	}
	return nil, upper
}

// firstReadLanguageField 是 `language: str = Field("en", max_length=10)`（:506）。
// **非** Optional：null 走 string_type（实测 lang_null），缺键才落默认 "en"。
func firstReadLanguageField(body *pyValue) ([]pyFieldError, string) {
	value, present := body.get("language")
	if !present {
		return nil, "en"
	}
	loc := []any{"body", "language"}
	if value.kind != pyString {
		return []pyFieldError{{
			kind: "string_type", loc: loc,
			msg: "Input should be a valid string", input: value,
		}}, ""
	}
	if utf8.RuneCountInString(value.str) > maxFirstReadLang {
		return []pyFieldError{{
			kind:  "string_too_long",
			loc:   loc,
			msg:   pluralize("String should have at most", maxFirstReadLang, "character"),
			input: value,
			ctx:   pyObjectOf("max_length", pyIntValue(maxFirstReadLang)),
		}}, ""
	}
	return nil, value.str
}
