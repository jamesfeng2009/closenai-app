// GET /api/v1/memories/{memory_id} 的 Go 侧实现（N8 批 6，memories 域）。
//
// 对应 memory_service.get_memory(:192-197) → memory_repository.get(:61-70)：
// 一条主键查询，WHERE 里带 user_id，**这就是这条端点唯一的越权防线**。
//
// 三件必须由字节钉住的事：
//
//  1. 「这一行不存在」与「这一行是别人的」回**同一个** 40403、同一份文案
//     （router :52-54 只在 service 返回 None 时抛，而 service 的 None 同时来自两支）。
//     回 403 或给两条文案＝替旁人确认这颗 id 是真的，端点会退化成 id 枚举 oracle
//     （口径与 N9-4 逐字一致）。
//  2. **没有 status 过滤**：详情能读到 superseded/reverted 行，而列表读不到
//     （两支 WHERE 的差别在 legacy_batch6.go 里逐字对照）。夹具 detail-superseded 钉这一格。
//  3. path 段是自由字符串：`GET /api/v1/memories/search` 在两侧都命中这条路由、
//     拿 "search" 当主键去查 ⇒ 40403，而不是 405/404 页面路由
//     （Python 的 FastAPI 也是按声明序匹配到 `/memories/{memory_id}` 的，
//     因为同 path 的 search 只注册了 POST）。夹具 list-path-collision 钉这一格。
//
// ⚠️ **一处已登记的两侧发散（本批不修，放量前必须结）**：
// `/api/v1/memories/`（尾斜杠）在 Python 侧走 Starlette 的 redirect_slashes ⇒ 307 到
// `/api/v1/memories`，Go 的 ServeMux 里没有这条 pattern ⇒ 落 handleNotFound 给 404 信封。
// 夹具**没有**种这一格：种了就是把一个「两侧本来就不同形」的字节钉成契约。
// 它与批 5 在 pyquery.go 里登记的那条空白整数发散同一档，属放量前置项而不是本批缺陷。
package httpapi

import (
	"encoding/json"
	"net/http"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
)

// memoryNotFoundMessage 逐字抄 routers/memories.py:53（也是 :130 DELETE 那条用的同一串）。
const memoryNotFoundMessage = "记忆不存在 / Memory not found"

func (s *Server) handleMemoryDetail(w http.ResponseWriter, r *http.Request) {
	userID, apiErr := s.middleware.RequireBearer(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	if s.batch6.Gate.Delegate(w, r, cutoverMemories, userID) {
		return
	}
	rc := fromContext(r.Context())

	// PathValue 拿到的是已解码的段（Starlette 给 FastAPI 的也是解码后的 str），
	// 所以 `%2F` 这类编码在两侧同形；空段不会走到这里——ServeMux 的 `{memory_id}`
	// 要求非空段，而那条发散见文件头。
	memoryID := r.PathValue("memory_id")

	row, found, err := s.batch6.Memories.MemoryByID(r.Context(), memoryID, userID)
	if err != nil {
		writeDBError(w, r)
		return
	}
	if !found {
		writeJSONError(w, r, apierr.New(apierr.CodeMemoryNotFound, memoryNotFoundMessage))
		return
	}

	encoded, err := memoryResponseValue(row).json()
	if err != nil {
		writeDBError(w, r)
		return
	}
	WriteSuccess(w, json.RawMessage(encoded), rc.traceID, rc.userID)
}
