// N8 批 3 的两件逐字口径：请求体面（journey acknowledge）与**写库文本**的序列化。
//
// 为什么不复用批 1 的 `validateAnalyticsBody`：那支函数把「列表 + 逐条 item +
// too_long 短路」和 analytics 的字段表写在一起，批 1 的变异锚点（#75/#76/#77 等）
// 逐行钉在它身上——为批 3 去抽公共层会打散已证过的锚点。这里的 body 面只有
// 「一个必填字符串 + max_length」，所以另写一支、只共用稳定件（isJSONContentType、
// pyBytesRepr、pyJSONLoads、pyFieldError）。**重复的那段是刻意的**，已按日期登记在
// tasks.md 的批 4 欠账里（等 body 面出现第三个域再一次性收口，锚点跟着搬）。
package httpapi

import (
	"strings"
	"unicode/utf8"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// maxJourneyEventID 是 schemas.py:532-534 的 `Field(..., max_length=36)`。
// 36 不是随手写的：库里 PK 是 VARCHAR(36)（uuid4 的字符串长度），
// 越界必须在校验层挡住，而不是让 PG 报 22001 变成 50003。
const maxJourneyEventID = 36

// validateJourneyAckBody 返回校验通过的 event_id，或 pydantic 的错误列表。
//
// 判据的出处与批 1 同源（FastAPI 先按 Content-Type 决定 body 是 str 还是 bytes，
// 再把整个 body 交给 pydantic 的模型校验器），差别只在字段面：
//   - `string_too_long` 的 ctx 实测**只有** `max_length`，**没有** `actual_length`
//     （列表的 `too_long` 两个都有，见批 1）——所以这里照抄实测形状而不是照抄列表那一支。
//   - 长度按 **Unicode 码点**数（Python 的 len()），不是字节数：
//     `"你好"×20` 是 40 个字符⇒越界，而它的 UTF-8 是 60 字节。
//   - 多余的键被**忽略**（pydantic 的模型默认 extra='ignore'），所以
//     `{"event_id":"x","other":1}` 是 200 而不是 422。
func validateJourneyAckBody(raw []byte, contentType string) (string, []pyFieldError) {
	if len(raw) == 0 {
		return "", []pyFieldError{missingBody()}
	}
	if !isJSONContentType(contentType) {
		return "", []pyFieldError{{
			kind:  "model_attributes_type",
			loc:   []any{"body"},
			msg:   "Input should be a valid dictionary or object to extract fields from",
			input: pyStringValue(pyBytesRepr(raw)),
		}}
	}
	body, err := pyJSONLoads(raw)
	if err != nil {
		return "", []pyFieldError{jsonInvalidPyEntry(err)}
	}
	switch body.kind {
	case pyNull:
		return "", []pyFieldError{missingBody()}
	case pyObject:
	default:
		return "", []pyFieldError{{
			kind:  "model_attributes_type",
			loc:   []any{"body"},
			msg:   "Input should be a valid dictionary or object to extract fields from",
			input: body,
		}}
	}

	value, present := body.get("event_id")
	switch {
	case !present:
		return "", []pyFieldError{{
			kind: "missing", loc: []any{"body", "event_id"},
			msg: "Field required", input: body,
		}}
	case value.kind != pyString:
		return "", []pyFieldError{{
			kind: "string_type", loc: []any{"body", "event_id"},
			msg: "Input should be a valid string", input: value,
		}}
	case utf8.RuneCountInString(value.str) > maxJourneyEventID:
		return "", []pyFieldError{{
			kind: "string_too_long", loc: []any{"body", "event_id"},
			msg:   "String should have at most 36 characters",
			input: value,
			ctx:   pyObjectOf("max_length", pyIntValue(maxJourneyEventID)),
		}}
	}
	return value.str, nil
}

// prefillPayload 是 prefill 传给 schedule 的那个 dict（journey_service.py:182）：
// 三键、书写序 kind/day_no/language。列里存的文本形态由 pyDumpsSpaced 决定。
func prefillPayload(user domain.JourneyUser, row domain.JourneyRow) *pyValue {
	v := pyObjectValue()
	v.set("kind", pyStringValue(row.Kind))
	v.set("day_no", pyIntValue(row.DayNo))
	v.set("language", pyStringValue(user.LanguageOrDefault()))
	return v
}

// pyDumpsSpaced 是 `json.dumps(value, ensure_ascii=False)` 的**默认分隔符**形态：
// `", "` 与 `": "`（冒号后面也有一个空格）。
//
// 为什么响应面用不到它而写库用得到：Starlette 的 JSONResponse 显式传
// `separators=(",", ":")`，而 journey_repository.schedule 写 payload 列时用的是
// json.dumps 的默认参数——**库里的文本**因此带空格，两侧写得不一样时，
// 下一轮读取虽然 parse_json 结果相同，但 Python 的 UPDATE 与 Go 的 UPDATE
// 落进列里的字节不同，跨语言审计与「切流前后契约 diff」的行级比对会看到假差异。
//
// 叶子值的编码与紧凑形完全同源（复用 pyValue.json），只在容器层换分隔符，
// 所以 NaN/Infinity 那一支的失败口径也与响应面一致。
func pyDumpsSpaced(v *pyValue) (string, error) {
	if v == nil {
		return "null", nil
	}
	switch v.kind {
	case pyArray:
		parts := make([]string, 0, len(v.items))
		for _, item := range v.items {
			encoded, err := pyDumpsSpaced(item)
			if err != nil {
				return "", err
			}
			parts = append(parts, encoded)
		}
		return "[" + strings.Join(parts, ", ") + "]", nil
	case pyObject:
		var b strings.Builder
		b.WriteByte('{')
		for i, member := range v.members {
			if i > 0 {
				b.WriteString(", ")
			}
			pyWriteString(&b, member.key)
			b.WriteString(": ")
			encoded, err := pyDumpsSpaced(member.val)
			if err != nil {
				return "", err
			}
			b.WriteString(encoded)
		}
		b.WriteByte('}')
		return b.String(), nil
	default:
		return v.json()
	}
}
