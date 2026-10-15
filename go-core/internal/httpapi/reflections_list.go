// GET /api/v1/reflections 的 Go 侧实现（N8 批 5，reflections 域）。
//
// 对应 reflection_service.list（:127-142）：一条 growth_records 查询、**零 users 读**
// （这条路由既不取 users.timezone，也没有 `language` 依赖——所以灰度期两侧
// 各自只打一条 SQL，不存在批 4 那种「同一次请求两侧读不同行数」的面）。
//
// 三处容易被「顺手修正」的地方，实测字节都站在「不许修正」这边：
//  1. `related_memory_ids` 判的是 **falsy**不是 NULL：列里是空串也给 null，
//     而 "," 给 ["",""]、"a,,b" 给 ["a","","b"]（夹具 reflections-nulls 五格）。
//  2. `record_type` 是**精确**等值：夹具里种了一条 `Reflection`（首字母大写）被排除，
//     所以 Go 侧不许做 lower/前缀匹配这类「更宽松」的实现。
//  3. 空态给 `[]` 而不是 `null`——data 直接是 List，Python 侧空列表序列化就是空数组。
//
// created_at 与 history 同理走 pyISO（"Z"）：ReflectionResponse 是 pydantic 模型。
package httpapi

import (
	"net/http"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

func (s *Server) handleReflectionsList(w http.ResponseWriter, r *http.Request) {
	userID, apiErr := s.middleware.RequireBearer(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	if s.batch5.Gate.Delegate(w, r, cutoverReflections, userID) {
		return
	}
	rc := fromContext(r.Context())

	limit, verr := queryInt(r, "limit", domain.ReflectionsLimitSpec)
	if verr != nil {
		writeJSONError(w, r, verr.AsError())
		return
	}

	rows, err := s.batch5.Reflections.Reflections(
		r.Context(), userID, domain.ReflectionRecordType, limit)
	if err != nil {
		writeDBError(w, r)
		return
	}

	data := make([]reflectionPayload, 0, len(rows))
	for _, row := range rows {
		data = append(data, reflectionPayload{
			ID:               row.ID,
			Content:          row.Content,
			RelatedMemoryIDs: domain.SplitMemoryIDs(row.MemoryIDs),
			CreatedAt:        pyISO(row.CreatedAt),
		})
	}
	WriteSuccess(w, data, rc.traceID, rc.userID)
}

// reflectionPayload 的字段序就是 ReflectionResponse 的声明序（schemas.py:190-196）。
// RelatedMemoryIDs 是 nil⇒null、非 nil⇒数组，这个区分由 domain.SplitMemoryIDs 给。
type reflectionPayload struct {
	ID               string   `json:"id"`
	Content          string   `json:"content"`
	RelatedMemoryIDs []string `json:"related_memory_ids"`
	CreatedAt        string   `json:"created_at"`
}
