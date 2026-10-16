// GET /api/v1/memories 的 Go 侧实现（N8 批 6，memories 域）。
//
// 对应 memory_service.list_memories(:181-190) → memory_repository.list(:103-121)：
// **一条 memories 查询、零 users 读、零 LLM、零 embed**。参数只有两个，
// 而且两侧的行为差异都落在这两个参数上：
//
//   - `limit`：`Query(20, ge=1, le=100)`（routers/memories.py:34）。越界给的是
//     HTTP 400 / 40001，**不是** endpoints.md:156 登记的那个 422——那一格是登记面漂移
//     （契约表由 FastAPI 生成，实测字节站在 400 这边，同一形状的证人见夹具里
//     reflections-limit-101），本批只记录、不手改生成物。
//   - `category`：`str = None`，没有任何校验面，且判的是**假值**（repo :117 的 `if category:`）
//     ⇒ `?category=` 与不带参数同形、都返回全量。Go 若写成「空串也当一次过滤」，
//     就会把该出现的行筛没（夹具 list-category-empty 钉这一格）。
//
// 空态给 `[]` 不是 `null`：service 返回的是 List，Python 侧空列表就编成空数组。
// created_at 走 pyISO（`Z` 形）——判据不是「哪一批」而是「data 里放的是不是 pydantic 模型」，
// 这条路由返回 `List[MemoryResponse]`（memory_service.py:190），与批 5 三条同档。
package httpapi

import (
	"encoding/json"
	"net/http"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

func (s *Server) handleMemoriesList(w http.ResponseWriter, r *http.Request) {
	userID, apiErr := s.middleware.RequireBearer(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	if s.batch6.Gate.Delegate(w, r, cutoverMemories, userID) {
		return
	}
	rc := fromContext(r.Context())

	limit, verr := queryInt(r, "limit", domain.MemoriesLimitSpec)
	if verr != nil {
		writeJSONError(w, r, verr.AsError())
		return
	}
	category := queryString(r, "category")

	rows, err := s.batch6.Memories.MemoryList(r.Context(), userID, limit, category)
	if err != nil {
		writeDBError(w, r)
		return
	}

	items := make([]*pyValue, 0, len(rows))
	for _, row := range rows {
		items = append(items, memoryResponseValue(row))
	}
	encoded, err := pyArrayValue(items...).json()
	if err != nil {
		// conviction 列里是 NaN/Infinity：CPython 的 JSONResponse(allow_nan=False) 同样编不出来，
		// 口径同批 4 对 NaN 那一支的处理（写 50003，不降级成 404）。
		writeDBError(w, r)
		return
	}
	WriteSuccess(w, json.RawMessage(encoded), rc.traceID, rc.userID)
}
