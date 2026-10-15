// query 参数层的 int 校验（N8 批 5 新面）。
//
// 批 1–4 的校验都落在 **body**（pybody.go / validate.go），这两条端点是第一批
// 带 int query 的：`days: int = Query(14, ge=1, le=90)` 与
// `limit: int = Query(20, ge=1, le=100)`。它们给的 400 面与 body 面同形
// （HTTP 400 / code 40001 / data 是 pydantic-core 的条目），只有三处不同，
// 三处都是 2026-10-15 从真 FastAPI 录下来、不是推定：
//
//  1. `loc` 是 `["query","days"]`（没有 "body" 那一段，也就没有偏移量）。
//  2. `input` 是**原始字符串**，不是解析后的数：`days=0` 给 `"input":"0"`，
//     `days=abc` 给 `"input":"abc"`。所以越界那条也带着字符串回显，不能填 int64。
//  3. 同一个键出现两次时，Starlette 把整个 MultiDict 交给 pydantic，pydantic 取
//     **最后一个**值去做校验（夹具 trend-days-duplicate `days=1&days=14` 按 14 出字节、
//     `?days=&days=3` 按 3 出空数组）。Go 的 `URL.Query().Get()` 取的是**第一个**，
//     所以这里必须自己取末位。
//
// 已登记但未取证的两侧发散（放量前要结，见 tasks.md 批 5 段 D-8）：
// `" 14 "` 这种带空白的合法整数，Python 的 `int()` 吃、pydantic-core 的 Rust 解析
// 与 Go 的 ParseInt 都不吃——夹具只录了 `" "`（纯空白，两侧都拒）。
package httpapi

import (
	"errors"
	"net/http"
	"strconv"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// queryInt 校验一个声明为 `int = Query(default, ge=…, le=…)` 的查询参数。
// 返回值是校验通过的取值；出错时给 *ValidationError（调用方转 40001 信封）。
func queryInt(r *http.Request, name string, spec domain.IntQuerySpec) (int64, *ValidationError) {
	values := r.URL.Query()[name]
	if len(values) == 0 {
		return spec.Default, nil
	}
	raw := values[len(values)-1]

	n, err := strconv.ParseInt(raw, 10, 64)
	if err != nil {
		// i64 溢出对 Python 不是错误（int 无界），它只会在 ge/le 上被拦下；
		// 按符号落到对应那一档，input 仍回显原始字符串。
		var numErr *strconv.NumError
		if !errors.As(err, &numErr) || numErr.Err != strconv.ErrRange {
			return 0, intParsingError(name, raw)
		}
		if n < 0 {
			return 0, rangeError(name, raw, spec.Ge, true)
		}
		return 0, rangeError(name, raw, spec.Le, false)
	}
	if n < spec.Ge {
		return 0, rangeError(name, raw, spec.Ge, true)
	}
	if n > spec.Le {
		return 0, rangeError(name, raw, spec.Le, false)
	}
	return n, nil
}

// intParsingError 是 `int_parsing` 那一格：msg 逐字来自夹具
// （trend-days-abc / -float / -blank / -space / -fullwidth 五条共用它）。
func intParsingError(name, raw string) *ValidationError {
	return &ValidationError{Entries: []FieldError{{
		Type:  "int_parsing",
		Loc:   []any{"query", name},
		Msg:   "Input should be a valid integer, unable to parse string as an integer",
		Input: raw,
	}}}
}

// rangeError 是越界两档：ge ⇒ greater_than_equal、le ⇒ less_than_equal，
// ctx 只有一个键（所以 Go 的 map 序列化顺序不构成风险）。
func rangeError(name, raw string, bound int64, isGe bool) *ValidationError {
	entry := FieldError{
		Loc:   []any{"query", name},
		Input: raw,
	}
	if isGe {
		entry.Type = "greater_than_equal"
		entry.Msg = "Input should be greater than or equal to " + strconv.FormatInt(bound, 10)
		entry.Ctx = map[string]any{"ge": bound}
	} else {
		entry.Type = "less_than_equal"
		entry.Msg = "Input should be less than or equal to " + strconv.FormatInt(bound, 10)
		entry.Ctx = map[string]any{"le": bound}
	}
	return &ValidationError{Entries: []FieldError{entry}}
}
