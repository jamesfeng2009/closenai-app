package aiclient

import (
	"strconv"
)

// internalErrorCode ＝ backend/core/errors.py:72 的 INTERNAL_ERROR（int 500）。
// 配额那条是同文件 :52 的 40301，由服务端自己带出来，本包不造。
const internalErrorCode = 500

// internalErrorMessage 逐字抄自 chat.py:139（那条已做脱敏：异常细节只落日志 :136，不下发客户端）。
// 本包造的错误块必须用同一句，否则同一个故障在两条路径上给用户的字不一样，
// 而前端的 SSE 文案测只认其中一种写法。
const internalErrorMessage = "生成失败，请稍后再试 / Generation failed, please try again later"

// SSELine 把一个 ChatChunk 映射成现网的 SSE 行；返回空串表示这块没有对应的 SSE 事件。
//
// ───────────────────────── 映射表（每行都指回现网字节是谁产的）
//
//	中间块有 content      → data: {"type": "token", "content": ...}   ← chat.py:116
//	中间块有 memories      → data: {"type": "memory_context", "memories": ...} ← chat.py:104
//	FinishDone            → data: {"type": "done"}                     ← chat.py:132
//	FinishError           → data: {"type": "error", "code", "message"} ← chat.py:85-88（配额）/ :137-140（异常）
//	FinishBlocked         → content（安全话术，若有）之后 **done**
//	FinishDegraded        → **done**
//
// BLOCKED / DEGRADED 为什么落成 done 而不是新事件名：ChatFinish 这两个值是 R1 预留的号位，
// 现网流里今天产不出（见 closenai_ai_rpc.proto 的 ChatFinish 注释），而**已发布客户端只认
// token / memory_context / done / error 四种**（前端按 type 分支，第五种没人写过）。
// 降级与截断在用户侧就是「这句话讲完了」，所以传输层收尾仍用 done，
// 语义差异留在 Go 内部（Finish 值 + Failure 原因码）供留痕与指标用。
// 想把它当成新事件名下发，先改前端并过它的 jest 测，再回来改这一行。
func SSELine(chunk ChatChunk) string {
	switch chunk.Finish {
	case FinishChunk:
		// 判据用 len>0 而不是「这块是不是 memory 事件」：proto 的 repeated 字段没有 presence，
		// 空列表解出来就是长度 0，与「不是 memory 事件」不可区分；而 chat.py:103 的
		// `if memories:` 保证现网本来也不会发空的那条。所以契约里没有的判别位不在这造。
		// 反过来 content **不能**加同样的守卫：chat.py:116 对每个 chunk 无条件发 token。
		if len(chunk.Memories) > 0 {
			return sseWrap(pythonDict("memory_context", "memories", func(b []byte) []byte {
				return appendMemories(b, chunk.Memories)
			}))
		}
		return sseWrap(pythonDict("token", "content", func(b []byte) []byte {
			return appendPyString(b, chunk.Content)
		}))
	case FinishDone, FinishDegraded, FinishBlocked:
		return sseWrap(pythonDict(string(FinishDone), "", nil))
	case FinishError:
		code := chunk.ErrorCode
		if !chunk.HasErrorCode {
			code = internalErrorCode // 本包自己造的错误块都带码；这是给外部误用的护栏
		}
		return sseWrap(pythonDict("error", "code", func(b []byte) []byte {
			b = append(b, strconv.Itoa(code)...)
			b = append(b, ',', ' ')
			b = appendPyString(b, `message`)
			b = append(b, ':', ' ')
			return appendPyString(b, chunk.ErrorMessage)
		}))
	default:
		// 未知终止值（跨版本时旧客户端遇到新枚举）：不发事件而不是猜一个，
		// 因为发错一个 type 会被客户端当真处理，不发只是少一句收尾。
		return ""
	}
}

// SSELines 把一块展开成零或多条 SSE 行。今天只有 BLOCKED 需要两块（先安全话术后收尾），
// 所以这个函数存在的全部理由是「一个 proto 块可能对应两条现网事件」这件事已经发生了一次。
func SSELines(chunk ChatChunk) []string {
	if chunk.Finish == FinishBlocked && chunk.Content != "" {
		token := sseWrap(pythonDict("token", "content", func(b []byte) []byte {
			return appendPyString(b, chunk.Content)
		}))
		return []string{token, SSELine(ChatChunk{Finish: FinishDone})}
	}
	line := SSELine(chunk)
	if line == "" {
		return nil
	}
	return []string{line}
}

// sseWrap 加现网的前后缀：`data: <json>\n\n`（routers/chat.py:39-42 逐字）。
// SSE 的 `event:` 字段本仓从来不用，前端是按 payload 里的 type 分支的，
// 这里加一行注释免得下一个人以为漏了：不是漏，是现网就没有。
func sseWrap(body string) string { return "data: " + body + "\n\n" }

// pythonDict 产出 `{"type": <typeName>, <key>: <value>}` 形态；value==nil 时只有 type 一个键。
//
// 分隔符逐字照 Python json.dumps 的默认值：键值对之间 `", "`、key 与 value 之间 `": "`。
// 少写一个空格，现网字节就变了——而这层字节是 SSE 客户端逐行 split 的。
func pythonDict(typeName, key string, value func([]byte) []byte) string {
	b := make([]byte, 0, 64)
	b = append(b, '{')
	b = appendPyString(b, "type")
	b = append(b, ':', ' ')
	b = appendPyString(b, typeName)
	if value != nil {
		b = append(b, ',', ' ')
		b = appendPyString(b, key)
		b = append(b, ':', ' ')
		b = value(b)
	}
	b = append(b, '}')
	return string(b)
}

func appendMemories(b []byte, ms []MemoryContext) []byte {
	b = append(b, '[')
	for i, m := range ms {
		if i > 0 {
			b = append(b, ',', ' ')
		}
		b = append(b, '{')
		// 键序 id, content, category ＝ message_generator.py:398-404 的字面顺序，
		// chat.py:104 原样下发。合一套键面（与 MemoryRef）是另一件事，见 proto 的注释。
		b = appendPyKeyValue(b, "id", m.ID)
		b = append(b, ',', ' ')
		b = appendPyKeyValue(b, "content", m.Content)
		b = append(b, ',', ' ')
		b = appendPyKeyValue(b, "category", m.Category)
		b = append(b, '}')
	}
	return append(b, ']')
}

func appendPyKeyValue(b []byte, key, value string) []byte {
	b = appendPyString(b, key)
	b = append(b, ':', ' ')
	return appendPyString(b, value)
}

// appendPyString 复刻 CPython json 编码器在 ensure_ascii=True 下的字符串转义
// （纯 stdlib encoding/json 做不到：它不转义非 ASCII，而默认又转义 < > &，两头都不对）。
//
// 规则（逐字对齐）：
//   - `"` 与 `\` 加反斜杠；
//   - 0x08→\b、0x0C→\f、0x0A→\n、0x0D→\r、0x09→\t；
//   - 其余 < 0x20 用 \u00XX；
//   - 0x20..0x7E 原样；
//   - >= 0x7F 用 \uXXXX，星平面（> 0xFFFF）拆成一对代理码点。
//
// 夹具 sse_event_golden.json 里每一条都由真 _sse_event() 产出，Go 这边逐字节比；
// `\\u007f` 与 emoji 那两条就是最后一条规则的观测点。
func appendPyString(b []byte, s string) []byte {
	b = append(b, '"')
	for _, r := range s {
		switch r {
		case '"':
			b = append(b, '\\', '"')
		case '\\':
			b = append(b, '\\', '\\')
		case '\b':
			b = append(b, '\\', 'b')
		case '\f':
			b = append(b, '\\', 'f')
		case '\n':
			b = append(b, '\\', 'n')
		case '\r':
			b = append(b, '\\', 'r')
		case '\t':
			b = append(b, '\\', 't')
		default:
			switch {
			case r < 0x20:
				b = appendHex4(b, uint16(r))
			case r < 0x7f:
				b = append(b, byte(r))
			case r <= 0xFFFF:
				b = appendHex4(b, uint16(r))
			default:
				v := r - 0x10000
				b = appendHex4(b, uint16(0xD800+(v>>10)))
				b = appendHex4(b, uint16(0xDC00+(v&0x3FF)))
			}
		}
	}
	return append(b, '"')
}

func appendHex4(b []byte, v uint16) []byte {
	const hexDigits = "0123456789abcdef"
	b = append(b, '\\', 'u')
	return append(b,
		hexDigits[(v>>12)&0xf],
		hexDigits[(v>>8)&0xf],
		hexDigits[(v>>4)&0xf],
		hexDigits[v&0xf])
}
