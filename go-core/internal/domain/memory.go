// N8 批 6（memories 域两条纯读）的判据常量与行形状。
//
// 这两条端点是整批里唯一「读出来的列要原样进响应」的一家：11 列里有 3 列可空，
// 而 Python 侧的可空与空串**给出不同字节**（transition_reason 是 null 还是 ""），
// 所以这里用 Has*/值成对，而不是在 SQL 里 COALESCE 把两种情形折成一支
// （批 5 的 mood 那两条可以折，是因为 service 层 `x or ""` 本来就同判，见 legacy_batch5.go）。
package domain

import "time"

// MemoriesDefaultLimit / Min / Max 是 `limit: int = Query(20, ge=1, le=100)`
// （routers/memories.py:34）。它与批 5 的 reflections 那条逐字同参，
// 所以两档界共用同一个报错形状（pyquery.go）。
const (
	MemoriesDefaultLimit = 20
	MemoriesMinLimit     = 1
	MemoriesMaxLimit     = 100
)

// MemoryStatusCurrent 是 list 那条 SQL 的状态过滤字面量
// （memory_repository.py:109 的默认参数 `status: str = "current"`）。
// ⚠️ 只有**列表**带这条过滤：详情那条 `get`(:61-70) 只过 user_id 与 deleted，
// 所以一条 superseded 记忆「列表里消失、详情里仍能读到」是今天的真实行为，
// 不是漏筛——夹具必须两边各留一格证它（决策点 ①/摸底表「两条不对称」那格）。
const MemoryStatusCurrent = "current"

// MemoriesLimitSpec 是 GET /api/v1/memories 的 limit 参数三件套。
var MemoriesLimitSpec = IntQuerySpec{
	Default: MemoriesDefaultLimit, Ge: MemoriesMinLimit, Le: MemoriesMaxLimit,
}

// MemoryRow 是一行 memories 里这两条端点会读到的列（**不含 embedding**：
// 那一列是 halfvec，Go 全仓没有它的解析器，且响应里也不出现——所以 SQL 必须
// 显式收缩列，禁 SELECT *，见 legacy_batch6.go）。
//
// 字段序按 MemoryResponse 的声明序（models/schemas.py:49-63）排，
// 只有 conviction_score→Conviction、transition_reason→TransitionReason 这类
// 列名与键名的差异；status 列与 status 键同名同值（原样回显，不做映射）。
type MemoryRow struct {
	ID       string
	UserID   string
	Content  string
	Category string
	Status   string
	Source   string
	// Conviction 对应 `conviction: float`——列是 REAL/DOUBLE，Python 侧拿到的
	// 是 float，序列化走 repr(float)（0.0 不是 0），所以 httpapi 用 pyFloatValue
	// 而不是 encoding/json 的 float（后者把 0.0 写成 0）。
	Conviction float64
	CreatedAt  time.Time
	// 可空三件套：NULL 与空串在这一层必须还能分得开。
	HasSupersededAt     bool
	SupersededAt        time.Time
	HasTransitionReason bool
	TransitionReason    string
	// Labels 是列原文（TEXT，存的是 `{"labels": [...], "commitment": {...}}` 那种
	// JSON 字符串）。NULL 折成空串即可：Python 的 _parse_labels 对 NULL 与 "" 同判。
	Labels string
}
