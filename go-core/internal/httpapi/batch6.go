// N8 批 6（memories 域两条纯读）在 HTTP 层的依赖面与响应形状。
//
// 这批是六个已实现域里**唯一两条 path 都不能上刀**的一家（决策点 D-10 的直接命中）：
// `/api/v1/memories` 同 path 还挂着 POST（schemas 里的 MemoryCreate），
// `/api/v1/memories/{memory_id}` 同 path 还挂着 DELETE——而 Ingress 的 path 匹配不看方法，
// Exact 也会把这两个方法搬进只注册了 GET 的 Go ⇒ 当场 405，与百分比无关。
// 所以 CLOSEN_CUTOVER_MEMORIES 与批 5 的 REFLECTIONS 同一处置：**只声明键、不上 path**，
// 这两条 GET 今天「已实现、已逐字节回放、但从入口不可达」，放量要等同 path 的写侧一起搬。
//
// 响应形状三件必须照抄的事，出处都在 backend 那一侧：
//
//  1. **13 个键的序**是 MemoryResponse 的声明序（models/schemas.py:49-63），
//     其中 `similarity` 与 `regressed` 在这两条路上**恒为 null / false**：
//     列表与详情都走 `_to_response`(:115-129)，它不填这两个键，
//     填 similarity 的那条 `_to_response_from_row`(:132-147) 只服务语义搜索（不在本批）。
//     这两键排在 labels **之前**，是声明序而不是字母序，别顺手整理。
//  2. **conviction 走 pyFloatValue**：列是 Float，Python 序列化给 `0.0`，
//     Go 的 encoding/json 给 `0`（批 1–5 没有非整型浮点字段，所以这是第一条）。
//  3. **labels 是解析出来的数组，不是列原文**：`_parse_labels`(memory_service.py:102-112)
//     四条规则全部可观测，见 memoryLabelsValue。
package httpapi

import (
	"context"
	"strings"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/cutover"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// cutoverMemories 是这批的灰度域键名（与 config 的 CLOSEN_CUTOVER_MEMORIES 对齐）。
const cutoverMemories = "memories"

// MemoryStore 是这两条端点需要的两个读动作，对应 memory_repository 的
// list(:103-121) 与 get(:61-70)。**两条分开**不是冗余：它们的 WHERE 不同
// （列表多一条 status='current'，详情没有），合并成一支会让 superseded 行
// 在详情里也读不到，而那是今天可观测的行为差异。
type MemoryStore interface {
	// MemoryList 是 list()：category 为空串时不加那条过滤（Python 的 `if category:` 是假值判据）。
	MemoryList(ctx context.Context, userID string, limit int64,
		category string) ([]domain.MemoryRow, error)
	// MemoryByID 是 get()：found=false 同时覆盖「没有这一行」与「这行是别人的」。
	MemoryByID(ctx context.Context, memoryID,
		userID string) (row domain.MemoryRow, found bool, err error)
}

// Batch6 是这批的依赖集合。为 nil 时两条路由不注册（见 server.go），口径同批 1–5。
type Batch6 struct {
	Memories MemoryStore
	Gate     *cutover.Gate
}

// memoryResponseValue 把一个 MemoryRow 折成 MemoryResponse 的 JSON 值，
// 键序＝声明序，可空列的 NULL 原样进 null（不许折成 ""）。
func memoryResponseValue(row domain.MemoryRow) *pyValue {
	v := pyObjectValue()
	v.set("id", pyStringValue(row.ID))
	v.set("user_id", pyStringValue(row.UserID))
	v.set("content", pyStringValue(row.Content))
	v.set("category", pyStringValue(row.Category))
	v.set("status", pyStringValue(row.Status))
	v.set("source", pyStringValue(row.Source))
	v.set("conviction", pyFloatValue(row.Conviction))
	v.set("created_at", pyStringValue(pyISO(row.CreatedAt)))
	v.set("superseded_at", optionalISO(row.HasSupersededAt, row.SupersededAt))
	v.set("transition_reason", optionalText(row.HasTransitionReason, row.TransitionReason))
	v.set("similarity", pyNullValue())
	v.set("regressed", pyBoolValue(false))
	v.set("labels", memoryLabelsValue(row.Labels))
	return v
}

// optionalISO / optionalText 是两个可空列的形状。
//
// 分开的理由是可观测性不同：superseded_at 只有 NULL 与时刻两支；
// transition_reason 是 Text，NULL 给 null、空串给 ""（pydantic 的 Optional[str]
// 不会把 "" 当 None），夹具各留一格钉住这一对。
func optionalISO(present bool, t time.Time) *pyValue {
	if !present {
		return pyNullValue()
	}
	return pyStringValue(pyISO(t))
}

func optionalText(present bool, s string) *pyValue {
	if !present {
		return pyNullValue()
	}
	return pyStringValue(s)
}

// memoryLabelsValue 逐字复刻 memory_service._parse_labels(:102-112) 的四条规则：
//
//	if not raw: return None                                  // NULL 与 "" 同判
//	try: data = json.loads(raw)
//	except (JSONDecodeError, TypeError): return None         // 列里坏了 ⇒ null，不是 500
//	if isinstance(data, dict) and isinstance(data.get("labels"), list):
//	    return [str(item) for item in data["labels"]] or None // 空数组也是 null
//	return None                                              // 顶层是数组/标量 ⇒ null
//
// 两处最容易「顺手修正」的：顶层是**合法 JSON 数组**（`["commitment"]`）时 Python 给
// null（它只认 dict 里那个 "labels" 键），以及非字符串元素走 Python 的 `str()`
// （1⇒"1"、true⇒"True"、null⇒"None"、嵌套表 {…}⇒"{'a': 1}"），见 pyStrOf。
func memoryLabelsValue(raw string) *pyValue {
	if raw == "" {
		return pyNullValue()
	}
	data, decodeErr := pyJSONLoads([]byte(raw))
	if decodeErr != nil || data == nil || data.kind != pyObject {
		return pyNullValue()
	}
	list, present := data.get("labels")
	if !present || list == nil || list.kind != pyArray || len(list.items) == 0 {
		return pyNullValue()
	}
	items := make([]*pyValue, 0, len(list.items))
	for _, item := range list.items {
		items = append(items, pyStringValue(pyStrOf(item)))
	}
	return pyArrayValue(items...)
}

// pyStrOf 是 CPython 的 `str(x)`，x 只可能是 JSON 能表达的那几种值。
// 这条不是可有可无的容错：`{"labels": [1, true, null]}` 这种行今天真的能落库
// （写入侧没有校验 labels 的元素类型），而它的响应是 ["1","True","None"]。
func pyStrOf(v *pyValue) string {
	if v == nil {
		return "None"
	}
	switch v.kind {
	case pyNull:
		return "None"
	case pyBool:
		if v.truthy {
			return "True"
		}
		return "False"
	case pyInt:
		return v.intLit
	case pyFloat:
		// Python 的 str(float) 就是 repr(float)；nan/inf 那两支同样沿用。
		return pyFloatRepr(v.float)
	case pyString:
		return v.str
	case pyArray:
		parts := make([]string, 0, len(v.items))
		for _, item := range v.items {
			parts = append(parts, pyReprOf(item))
		}
		return "[" + strings.Join(parts, ", ") + "]"
	case pyObject:
		parts := make([]string, 0, len(v.members))
		for _, member := range v.members {
			parts = append(parts, "'"+member.key+"': "+pyReprOf(member.val))
		}
		return "{" + strings.Join(parts, ", ") + "}"
	}
	return ""
}

// pyReprOf 是 CPython 的 `repr(x)`：嵌套进 list/dict 里的元素用 repr，
// 与外层 str() 唯一的差别是字符串带引号。
func pyReprOf(v *pyValue) string {
	if v != nil && v.kind == pyString {
		return pyReprString(v.str)
	}
	return pyStrOf(v)
}

// pyReprString 是 CPython 的 repr(str)：引号优先 `'`，串里有 `'` 且没有 `"` 时换成 `"`；
// 反斜杠、当前引号、\n \r \t 走两字符转义，其余（含非 ASCII 可印刷字符）原样保留。
//
// **未复刻的一支，登记不假装已证**：不可打印字符的 `\xNN`/`\uNNNN` 转义，以及 Unicode
// 非印刷类别（Cf/Cs/Co/Cn）在 repr 里被转义这件事。它今天不可观测——写 labels 列的唯一
// 路径是 `commitment_service.py:150` 那一次 append，它只往数组里放字面量 "commitment"，
// 所以嵌套容器与不可打印字符都只能靠手工播种的行出现。放量前若要播种这类行，先补这条。
func pyReprString(s string) string {
	quote := byte('\'')
	if strings.ContainsRune(s, '\'') && !strings.ContainsRune(s, '"') {
		quote = '"'
	}
	var b strings.Builder
	b.Grow(len(s) + 2)
	b.WriteByte(quote)
	for _, r := range s {
		switch {
		case r == '\\':
			b.WriteString(`\\`)
		case r == rune(quote):
			b.WriteByte('\\')
			b.WriteByte(quote)
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case r == '\t':
			b.WriteString(`\t`)
		default:
			b.WriteRune(r)
		}
	}
	b.WriteByte(quote)
	return b.String()
}
