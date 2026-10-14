// GET /api/v1/onboarding/first-read 的 Go 侧实现（N8 批 4，onboarding 域）。
//
// 逐字对应 first_read_service.restore（:200-221）——**只读一条 profile**，
// 不碰 users、不预写剧本、不回查模板。这一支是整域最简单的一条，但它的
// 「按键序就地覆盖」是唯一会改 JSON 形状的地方：
//
//	payload = dict(card.get("card_payload") or {})     # 九键，可能为空
//	payload.update({"id":…, "assertions":…, "summary":…, "closer":…,
//	                "language":…, "created_at":…})    # 已有键**就地换值、位置不动**，
//	                                                  # 缺的键**追加在末尾**
//
// 于是形状取决于列里那份 card_payload 有哪些键：POST 写出的九键里既没有 id 也没有
// created_at ⇒ 两者都追加在 generated_at 之后（restore 出十一键，POST 出十键）；
// language/assertions/summary/closer 四个键则是原位被列值覆盖。
// 手工造的行若把 id 放进 card_payload，它就留在原位——两种形状同出一支 set()。
//
// 四种「读不出来」的出口必须分清，因为它们的状态码不同：
//   - 没有行 ⇒ 40402（:203-204 return None，router :45-48 抛 RESOURCE_NOT_FOUND）
//   - first_read 列不是合法 JSON ⇒ 40402（except JSONDecodeError ⇒ None，:207-209）
//   - 列是合法 JSON 但**不是对象**（5、"x"、[]）⇒ Python 在 :210 抛 AttributeError ⇒ 500。
//     这一支经 API 造不出来（upsert 永远写 dict），所以没有观测点；Go 落 50003 同批 2/3
//     对 NaN 的处理，**不许**顺手降级成 40402——那会把「库里坏了」伪装成「没生成过」。
//   - 列是 "{}" ⇒ **200**，且只有 update 追加的六键（card_payload 是假值 ⇒ 空 dict）。
package httpapi

import (
	"encoding/json"
	"net/http"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
)

// notGeneratedMessage 逐字抄 routers/onboarding.py:47。
const notGeneratedMessage = "尚未生成初读卡 / First-read card not generated yet"

func (s *Server) handleFirstReadRestore(w http.ResponseWriter, r *http.Request) {
	userID, apiErr := s.middleware.RequireBearer(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	if s.batch4.Gate.Delegate(w, r, "onboarding", userID) {
		return
	}
	rc := fromContext(r.Context())

	profile, err := s.batch4.Store.OnboardingProfile(r.Context(), userID)
	if err != nil {
		writeDBError(w, r)
		return
	}
	if !profile.Found {
		writeJSONError(w, r, apierr.New(apierr.CodeResourceNotFound, notGeneratedMessage))
		return
	}

	// `profile.first_read or "{}"`：NULL 与空串都落 "{}" 这一支 ⇒ 空对象而不是解析失败。
	raw := profile.FirstRead
	if raw == "" {
		raw = "{}"
	}
	card, decodeErr := pyJSONLoads([]byte(raw))
	if decodeErr != nil {
		writeJSONError(w, r, apierr.New(apierr.CodeResourceNotFound, notGeneratedMessage))
		return
	}
	if card.kind != pyObject {
		// 无观测点的 500 支（见文件头）。与 NaN 同一格：写 50003 而不是新造码。
		writeDBError(w, r)
		return
	}

	payload, ok := firstReadRestorePayload(card)
	if !ok {
		writeDBError(w, r)
		return
	}
	payload.set("id", pyStringValue(profile.ID))
	// 键序 assertions/summary/closer/language/created_at 就是 :211-220 那个字面量的顺序。
	// assertions 取**外层** card 的，不是 card_payload 里那份（两份今天相同）。
	payload.set("assertions", falsyDefault(card, "assertions", pyArrayValue()))
	payload.set("summary", falsyDefault(card, "summary", pyStringValue("")))
	payload.set("closer", falsyDefault(card, "closer", pyStringValue("")))
	// language 与 created_at 来自**列**，不是 card：NULL 就是 JSON null，键不省略。
	if profile.HasLanguage {
		payload.set("language", pyStringValue(profile.Language))
	} else {
		payload.set("language", pyNullValue())
	}
	if profile.HasCreatedAt {
		payload.set("created_at", pyStringValue(pyISOOffset(profile.CreatedAt)))
	} else {
		payload.set("created_at", pyNullValue())
	}

	encoded, err := payload.json()
	if err != nil {
		// 列文本里带 NaN/Infinity：CPython 的 JSONResponse(allow_nan=False) 同样编不出来。
		writeDBError(w, r)
		return
	}
	WriteSuccess(w, json.RawMessage(encoded), rc.traceID, rc.userID)
}

// firstReadRestorePayload 是 `dict(card.get("card_payload") or {})`。
//
// 返回的浅拷贝对应 Python 的 dict()：**复制键值对而不是共享容器**，
// 后面的 update 才只作用于 payload。ok=false 对应 `card_payload` 是「真值但不是对象」
// （如 5、"x"、[{...}]）——那边 `dict(x)` 抛 TypeError/ValueError，同样是 500 支。
func firstReadRestorePayload(card *pyValue) (*pyValue, bool) {
	value, present := card.get("card_payload")
	if !present || pyFalsy(value) {
		return pyObjectValue(), true
	}
	if value.kind != pyObject {
		return nil, false
	}
	out := pyObjectValue()
	for _, member := range value.members {
		out.set(member.key, member.val)
	}
	return out, true
}

// falsyDefault 是 `card.get(k) or default`：缺键与假值都取 default，
// **其余原样**（包括类型不对的值——summary 是 5 就回 5，这一支与闸的判空同源而不同函数）。
func falsyDefault(card *pyValue, key string, def *pyValue) *pyValue {
	value, present := card.get(key)
	if !present || pyFalsy(value) {
		return def
	}
	return value
}

// pyFalsy 是 CPython 的真值判定（这里只用到 JSON 能表达的那几种值）：
// null/false/0/0.0/""/[]/{} 为假。缺这一支就会把 `assertions: []` 当成有值原样回显，
// 而 Python 那边换成了默认值——今天两者都是空数组，但 `summary: ""` 与 `closer: ""`
// 的默认替换在「外层为空、card_payload 里有值」这种手工造出来的行上是可观测的。
func pyFalsy(v *pyValue) bool {
	if v == nil {
		return true
	}
	switch v.kind {
	case pyNull:
		return true
	case pyBool:
		return !v.truthy
	case pyInt:
		// 解析器已把 "-0" 归一成 "0"（pyScanNumber），所以这里只看字面量。
		return v.intLit == "0"
	case pyFloat:
		return v.float == 0
	case pyString:
		return v.str == ""
	case pyArray:
		return len(v.items) == 0
	case pyObject:
		return len(v.members) == 0
	}
	return false
}
