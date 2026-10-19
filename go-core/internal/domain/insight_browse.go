package domain

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"
)

// T3-01／T3-02：`/internal/v3/insights` 这一面的读模型（裁决七-10）。
//
// 为什么另开一个解释器而不把列表塞进 `InsightExplainer`：那条是 N9 的**依据展开**面，
// 一次请求只对**一颗已知的 id** 取数；这一条要的是「这个用户现在有哪些洞察」，
// 判据完全不同（分页、状态集合、无证据不上线）。把它们合成一个方法集，
// 第一次改动就会有人为了列表去动 N9 的可见性判据，而那条判据有它自己的闸（七-7）。
//
// 分层与 N9 逐字同构：`store` 只搬行，判断全在这里（07:183-186）。

// InsightReturnableStatuses 是**服务间读面**允许出现的状态集合。
//
// 它等于 N9 的 `InsightVisibleStatuses`（三态）∪ 用户自己表态造成的三态：
// `corrected`／`expired`／`dismissed` 都是「用户对一条已上线洞察做过什么」的结果，
// 不让它们回来，用户就会看到「我明明纠正过它，它消失了」——工单 T3-01 §怎么做.5
// 要的就是「用户要能回看自己纠错过的」。
//
// `retracted` 仍然不在这里：那是**系统**替用户撤回的历史（`evidence_read.go:31-33`
// 的理由一行没变），把它铺进列表等于替系统收回撤回声明。
// `draft`／`validated` 是 AI 候选内部态，任何对外面都不给。
var InsightReturnableStatuses = append(
	append([]string{}, InsightVisibleStatuses...),
	"corrected", "expired", "dismissed")

// isReturnableStatus 空串一律不可见（脏行不对外），判据口径与 isVisibleStatus 同形。
func isReturnableStatus(status string) bool {
	if status == "" {
		return false
	}
	for _, s := range InsightReturnableStatuses {
		if s == status {
			return true
		}
	}
	return false
}

// 分页数值。**不是 env 键**（D-G12「键存在但没人读＝假旋钮」）：
// 这两个数进 env 会让「调大 limit」变成一次运维动作，而真正的上限由这一面的
// 响应体积决定，改它的人应该同时改这里并跑一遍形状闸。
const (
	defaultInsightListLimit = 20
	maxInsightListLimit     = 100
)

// ErrInsightCursorInvalid 是游标合不上形的错误。
// 它必须是**显式错误**而不是「当成第一页」：静默回第一页会让翻页的调用方
// 永远拿不到后面的行，而它看到的状态码是 200。
var ErrInsightCursorInvalid = errors.New("domain: insight page cursor is invalid")

// InsightKey 是 keyset 游标的内容：上一页最后一行的 `(published_at, id)`。
type InsightKey struct {
	PublishedAt time.Time
	ID          string
}

// Encode 打成 URL 安全的短串（`<unixMicro>|<id>` 走 base64url）。
//
// 为什么自己编码而不是把两列直接摊进 query：游标是**这一面的实现细节**，
// 调用方只该把它当不透明串；摊开列名等于把排序键写进契约，
// 以后换排序（比如按 confidence）要改调用方。
func (k InsightKey) Encode() string {
	raw := fmt.Sprintf("%d|%s", k.PublishedAt.UnixMicro(), k.ID)
	return base64.RawURLEncoding.EncodeToString([]byte(raw))
}

// DecodeInsightKey 反解游标。分隔符只认第一个 `|`：id 里不该有竖线，
// 但就算有也不该被拆成两段导致「游标悄悄变成另一格」。
func DecodeInsightKey(cursor string) (InsightKey, error) {
	raw, err := base64.RawURLEncoding.DecodeString(strings.TrimSpace(cursor))
	if err != nil {
		return InsightKey{}, fmt.Errorf("%w: %v", ErrInsightCursorInvalid, err)
	}
	microsText, id, found := strings.Cut(string(raw), "|")
	if !found || id == "" {
		return InsightKey{}, fmt.Errorf("%w: 缺分隔符或 id", ErrInsightCursorInvalid)
	}
	micros, err := strconv.ParseInt(microsText, 10, 64)
	if err != nil {
		return InsightKey{}, fmt.Errorf("%w: 时刻合不成整数 %q", ErrInsightCursorInvalid, microsText)
	}
	return InsightKey{PublishedAt: time.UnixMicro(micros).UTC(), ID: id}, nil
}

// InsightListItem 是列表的一行。**没有证据正文**（工单 T3-01 §怎么做.3）：
// 列表是「有哪些」，正文属详情，一次列表把每颗洞察的 excerpt 都拖出去，
// 4c8G 单机上第一页就够把带宽花光。
type InsightListItem struct {
	ID            string
	Statement     string
	ClaimType     string
	Status        string
	Confidence    float64
	HasConfidence bool
	PublishedAt   time.Time
	EvidenceCount int64
}

// InsightListView 是列表面的产物；NextCursor 空串＝后面没有行了。
type InsightListView struct {
	Items      []InsightListItem
	NextCursor string
}

// InsightBrowseReader 是列表需要的持久化面，实现只有一个：`internal/store` 的池面只读仓储。
//
// `hasAfter=false`＝第一页；`statuses` 由领域层给（调用方不许自己拼状态词表），
// 仓储因此**不做**可见性判断——判据只能有一份，否则同一个用户在列表与详情上
// 会得到两套「能不能看到」。
// `limit` 是**取多少行**，不是「用户要多少」：Browser 会多要一条来判断还有没有下一页。
type InsightBrowseReader interface {
	ListInsightsForBrowsing(ctx context.Context, userID string, statuses []string,
		after InsightKey, hasAfter bool, limit int) ([]InsightListItem, error)
}

// InsightBrowser 把「这个用户能看到哪些洞察」拼成视图。
type InsightBrowser struct {
	browse InsightBrowseReader
	// explain 复用 N9 的取数与裁剪：详情要的证据正文、二跳 pattern、上限口径
	// 都是**同一份**，只是可见性判据换成 Returnable。再造一套取数就是第二个事实源。
	explain *InsightExplainer
}

func NewInsightBrowser(browse InsightBrowseReader, explain *InsightExplainer) *InsightBrowser {
	return &InsightBrowser{browse: browse, explain: explain}
}

// List 是一页洞察。
//
// `status` 是**可选过滤器**，但它不能突破可见集合：传 `draft` 得到的是空列表，
// 而不是「把内部态给我看」——过滤器与授权集取交集，这一条写在测试里。
// `cursor` 空＝第一页；合不成形 ⇒ 显式错误（不是静默回第一页）。
func (b *InsightBrowser) List(ctx context.Context, userID, status string,
	limit int, cursor string) (InsightListView, error) {
	if userID == "" {
		return InsightListView{}, errors.New("domain: insight list needs an authenticated user")
	}
	if b.browse == nil {
		return InsightListView{}, errors.New("domain: insight list has no reader")
	}
	if limit <= 0 {
		limit = defaultInsightListLimit
	}
	if limit > maxInsightListLimit {
		limit = maxInsightListLimit
	}
	statuses := InsightReturnableStatuses
	if status != "" {
		statuses = narrowToReturnable(status)
	}
	var after InsightKey
	hasAfter := false
	if strings.TrimSpace(cursor) != "" {
		decoded, err := DecodeInsightKey(cursor)
		if err != nil {
			return InsightListView{}, err
		}
		after, hasAfter = decoded, true
	}
	// 多取一条：只有「这一页之外还有行」这件事被**证实**，才许发 next_cursor。
	// 按恰好 limit 条发游标会让调用方在真尽头多打一次空请求，而那次请求的形状
	// 与「游标坏了」在日志里分不开。
	rows, err := b.browse.ListInsightsForBrowsing(ctx, userID, statuses, after, hasAfter, limit+1)
	if err != nil {
		return InsightListView{}, err
	}
	view := InsightListView{Items: rows}
	if len(rows) > limit {
		view.Items = append([]InsightListItem(nil), rows[:limit]...)
		last := rows[limit-1]
		view.NextCursor = InsightKey{PublishedAt: last.PublishedAt, ID: last.ID}.Encode()
	}
	return view, nil
}

// Detail 是一颗洞察的详情（含证据正文与 pattern）。
//
// 顺序与 N9 逐字一致：先归属、再可见性、最后取数（05 §20 不许信调用方的 user_id，
// 归属判据就是 SQL 的 `WHERE id=$1 AND user_id=$2`，别人的行在这里必然 NotFound）。
func (b *InsightBrowser) Detail(ctx context.Context, userID, insightID string) (InsightEvidenceView, error) {
	if b.explain == nil {
		return InsightEvidenceView{}, errors.New("domain: insight detail has no explainer")
	}
	return b.explain.ExplainReturnable(ctx, userID, insightID)
}

// narrowToReturnable 把调用方给的状态过滤器与可见集合取交集。
// 交集为空时返回**那一个**传入值，让 SQL 自然返回零行——不返回全集，
// 否则「过滤一个不该看的态」会变成「看到全部」，那是授权判据的反面。
func narrowToReturnable(status string) []string {
	for _, allowed := range InsightReturnableStatuses {
		if allowed == status {
			return []string{status}
		}
	}
	return []string{status}
}
