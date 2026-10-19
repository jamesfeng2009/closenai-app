package domain

import (
	"context"
	"errors"
	"strings"
	"time"
)

// T5-04：`GET /internal/v3/proactives` 的读模型（proactives 的只读审计面）。
//
// 为什么要有这一面：`proactives` 有八态与一排调度字段（02 §19、04 §4–§11），
// 但到今天零 HTTP——出问题时唯一的看法是连库手写 SQL。而「手写那条 SQL」正是
// 这一族事故的放大器：值班会从聊天工具里复制一条别人三个月前的查询，
// 那条查询里的谓词（哪几态算「卡住」、窗口按 sent_at 还是 scheduled_at）
// 没有任何地方钉着，于是两个人读出两个答案，而两者都自称「库里就是这么写的」。
// 这一面把那条口径收进一处（谓词在 canonical.sql 的 `ListProactivesForAudit`，
// 见该条注释），代价是多一条内部端点，收益是排障口径从此可测。
//
// 分层与前两条读面逐字同构：`store` 只搬行，判断全在这里（07:183-186）。
//
// 为什么不并进 GoalBrowser / InsightBrowser：那两条的判据分别是「你自己写的东西」
// 与「模型说过的话能不能给你看」，这一条的判据是「系统替用户做过什么、为什么没做成」。
// 三者唯一的共同点都是带 user_id；合并的结果是第一次改动就有人为了让审计面多一个
// 过滤器去动用户面的可见集合。

// ProactiveAuditRecord 是一条触达的审计行。
//
// 没有 `message` 正文：审计要回答的是「发没发、为什么没发」，正文是用户内容，
// 把它端进一条运维查询等于给每一次排障多开一条数据外流的路（04 §22 同口径）。
// 可空列一律用「零值＝NULL」的既有口径（`timeOf`/`.String`），由 httpapi 折成 JSON null，
// 不在这里再造第三种空值表示。
type ProactiveAuditRecord struct {
	ID          string
	Status      string
	TriggerType string
	ScheduledAt time.Time
	SentAt      time.Time
	RespondedAt time.Time
	ExpiresAt   time.Time
	BlockReason string
	// RejectReason 与 BlockReason 是工单 §怎么做.1 那个 `skip_reason` 的真身：
	// 两侧 DDL 都没有 skip_reason 这一列（backend/models/canonical.py:451-452 与
	// db/schema/0002_canonical_runtime.sql:366-367），「被挡」与「被拒」本来就有两格。
	RejectReason   string
	JourneyEventID string
	CreatedAt      time.Time
	UpdatedAt      time.Time
}

// ProactiveAuditView 是一次审计读的成品。
//
// AppliedStatus/AppliedStale 是**回显**而不是装饰：这一面的 status 过滤器不校验词表
// （理由与 narrowListArgs 逐字相同——库里没有的那一态必然零行，而抢先判 400 会让
// 同一个错误在两条面上有两种形状）。既然不拦，就必须让调用方看见自己过滤了什么，
// 否则「拼错一个状态」与「这个人真的一条触达都没有」在响应里是同一张脸。
type ProactiveAuditView struct {
	Items         []ProactiveAuditRecord
	Truncated     bool
	Limit         int
	AppliedStatus string
	AppliedStale  bool
}

// ProactiveAuditReader 是这一面需要的唯一一个方法（窄到一条是为了单测不发真 SQL
// 就能验完「三种过滤器各拨出什么参数」——那是一张参数映射表，它的真身由
// `internal/store` 的真库集成测钉住）。
type ProactiveAuditReader interface {
	ListProactiveAuditRecords(ctx context.Context, userID, status string,
		limit int, stale bool) ([]ProactiveAuditRecord, error)
}

// ProactiveAuditBrowser 把「这个人的触达动线走到哪一态了」拼成视图。
type ProactiveAuditBrowser struct{ read ProactiveAuditReader }

func NewProactiveAuditBrowser(read ProactiveAuditReader) *ProactiveAuditBrowser {
	return &ProactiveAuditBrowser{read: read}
}

// 分页数值。与另两条读面同一口径：**不是 env 键**（D-G12「键存在但没人读＝假旋钮」）。
//
// 默认 50／上限 200 与目标面同数：审计一次要看的是「这一周排了几封、卡在哪几封」，
// 20 条会刚好截断在最能说明问题的那批之前，而放开到 1000 会让一次误拨把整张表拉进内存。
const (
	defaultProactiveAuditLimit = 50
	maxProactiveAuditLimit     = 200
)

// ErrProactiveAuditNeedsUser 是「没有身份就来查审计」。
//
// 它必须是显式错误而不是「返回空列表」：空列表在审计面上的意思是「这个人没有触达」，
// 而这里真实情况是「没人告诉我要查谁」——把后者显示成前者，值班会替一个不存在的人
// 排除故障。httpapi 那一层根本不会让它发生（`requireInternalService` 先拦 401），
// 这一格是领域侧的第二道：第二个调用方（比如以后的 CLI 直连）不能绕过它。
var ErrProactiveAuditNeedsUser = errors.New(
	"domain: proactive audit needs an authenticated user")

// List 是这一面唯一的入口。
//
// stale 的谓词不在这里写（见 canonical.sql 那条注释）：这里只决定「传不传这个布尔」
// 与「截不截」，两件事都只由参数形状决定。
func (b *ProactiveAuditBrowser) List(ctx context.Context, userID, status string,
	limit int, stale bool) (ProactiveAuditView, error) {
	if strings.TrimSpace(userID) == "" {
		return ProactiveAuditView{}, ErrProactiveAuditNeedsUser
	}
	if b.read == nil {
		return ProactiveAuditView{}, errors.New("domain: proactive audit has no reader")
	}
	status = strings.TrimSpace(status)
	limit = clampProactiveAuditLimit(limit)
	rows, err := b.read.ListProactiveAuditRecords(ctx, strings.TrimSpace(userID),
		status, limit+1, stale)
	if err != nil {
		return ProactiveAuditView{}, err
	}
	view := ProactiveAuditView{Items: rows, Limit: limit,
		AppliedStatus: status, AppliedStale: stale}
	if len(rows) > limit {
		view.Items = append([]ProactiveAuditRecord(nil), rows[:limit]...)
		view.Truncated = true
	}
	return view, nil
}

func clampProactiveAuditLimit(limit int) int {
	if limit <= 0 {
		return defaultProactiveAuditLimit
	}
	if limit > maxProactiveAuditLimit {
		return maxProactiveAuditLimit
	}
	return limit
}

// IsProactiveStatus 判断一个字符串在不在八态词表里。
//
// 它服务的不是这一面的过滤器（过滤器不校验，见上面），而是**调用方的提示**：
// CLI 在零行且这个词不是八态之一时要说「你过滤了一个不存在的状态」，
// 而那句提示只能从这里取词表——把八态再抄一遍进 CLI 就是第二个事实源。
func IsProactiveStatus(status string) bool {
	for _, s := range ProactiveStatuses {
		if string(s) == status {
			return true
		}
	}
	return false
}

// ProactiveAuditStaleStatuses 是「到期却没发出去」涉及的那三态。
//
// 登记在这里只为**测与文档**：SQL 谓词是真身（canonical.sql 的 ListProactivesForAudit），
// 这一份不许被生产代码读——所以 httpapi 与 domain 的 List 都不引用它，
// 只有 `TestStaleVocabularyMatchesTheSQLPredicate` 拿它与 SQL 源文本对账。
// 两侧的口径一旦分叉，「审计少报」就是它的发生地。
var ProactiveAuditStaleStatuses = []string{"candidate", "eligible", "scheduled"}
