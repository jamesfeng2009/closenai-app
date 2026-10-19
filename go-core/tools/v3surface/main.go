// Command v3surface 是 T3-03 跨进程闸的 Go 那一半（沿 tools/aiclientprobe 的形态：
// Python 起本命令、真拨 HTTP、断言只在 pytest 那侧）。
//
// 为什么要有这一层：`backend/tests/test_v3_proxy.py` 用 MockTransport 证的是
// 「BFF 发出的字节长什么样」，而两侧真正上线时中间还隔着 Go 的 mux、鉴权中间件、
// 信封编码器与错误映射表。尤其 §验收 那句「Go 停掉 ⇒ 503 且信封形状正确」——
// mock 出来的 ConnectError 是**我认为**Go 停了会长这样，杀掉一个真进程才是它停了。
//
// 数据是夹具而不是真库：G-05／G-06 那两格（无证据不上线、纠错真落库）由
// `internal/store` 的集成测在真 PG 上钉，这里再造一份只会造出第二个事实源。
// 本命令唯一的职责是把 Go 的**线上形状**如实端出来：path 匹配、头门禁、
// 信封五键、错误码与 HTTP 状态的配对、游标语义、以及「身份只从 X-User-Id 进来」。
// 断言一律在 pytest 侧，本命令不判对错。
//
// 跑法（由 pytest 起停；手工冒烟留给这里）：
//
//	GO_SERVICE_TOKEN=devtoken go run ./tools/v3surface -addr 127.0.0.1:8099
//	# stdout 第一行：{"addr":"127.0.0.1:8099"}；stdin 关闭即退出
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"net/http"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/auth"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/config"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/httpapi"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store"
)

// 夹具只服务**一个用户、两颗洞察**：数字越少，跨进程断言越难被"顺带对上"。
const (
	fixtureUser    = "u-e2e"
	fixtureInsight = "i-e2e-1"
	fixtureOther   = "i-e2e-2"
)

// published 是列表的排序键来源（`(published_at,id) DESC`，与真 SQL 同序）。
var published = []time.Time{
	time.Date(2026, 10, 19, 8, 0, 0, 0, time.UTC),
	time.Date(2026, 10, 18, 8, 0, 0, 0, time.UTC),
}

type row struct {
	id      string
	status  string
	evidenc int64
	at      time.Time
}

func rows() []row {
	return []row{
		{id: fixtureInsight, status: "published", evidenc: 3, at: published[0]},
		{id: fixtureOther, status: "corrected", evidenc: 1, at: published[1]},
	}
}

// ── 三个替身：只搬夹具，不做判断（判断有它自己的闸，见文件头）─────────────────

type fixtureBrowse struct{}

func (fixtureBrowse) ListInsightsForBrowsing(_ context.Context, userID string,
	statuses []string, after domain.InsightKey, hasAfter bool, limit int) ([]domain.InsightListItem, error) {
	if userID != fixtureUser {
		// 真 SQL 的 `WHERE i.user_id=$1`：别人的行在这里必然是空集。
		return []domain.InsightListItem{}, nil
	}
	allowed := map[string]bool{}
	for _, s := range statuses {
		allowed[s] = true
	}
	out := []domain.InsightListItem{}
	for _, r := range rows() {
		if !allowed[r.status] {
			continue
		}
		if hasAfter && !(r.at.Before(after.PublishedAt) ||
			(r.at.Equal(after.PublishedAt) && r.id < after.ID)) {
			continue
		}
		out = append(out, domain.InsightListItem{ID: r.id,
			Statement: "statement " + r.id, ClaimType: "pattern", Status: r.status,
			Confidence: 0.72, HasConfidence: true,
			PublishedAt: r.at, EvidenceCount: r.evidenc})
	}
	sort.Slice(out, func(i, j int) bool {
		if !out[i].PublishedAt.Equal(out[j].PublishedAt) {
			return out[i].PublishedAt.After(out[j].PublishedAt)
		}
		return out[i].ID > out[j].ID
	})
	if limit >= 0 && len(out) > limit {
		out = out[:limit]
	}
	return out, nil
}

type fixtureEvidence struct{}

func (fixtureEvidence) GetInsightForReading(_ context.Context, userID,
	insightID string) (domain.InsightReadable, error) {
	if userID != fixtureUser || insightID != fixtureInsight {
		return domain.InsightReadable{}, domain.ErrInsightNotFound
	}
	return domain.InsightReadable{ID: fixtureInsight, UserID: fixtureUser,
		Statement: "你常在深夜记录", ClaimType: "behaviour", Status: "published",
		Confidence: 0.82, HasConfidence: true,
		TimeFrom: published[0], HasTimeFrom: true}, nil
}

func (fixtureEvidence) item() domain.EvidenceItem {
	return domain.EvidenceItem{ID: "e-e2e", EvidenceType: "quote", Role: "supporting",
		Strength: 0.9, HasStrength: true,
		Excerpt: "今晚又三点才睡", HasExcerpt: true,
		ObservedAt: published[0], HasObservedAt: true,
		Source: domain.EvidenceSource{RecordID: "s-e2e", SourceType: "memory",
			SourceTable: "memories", SourceID: "m-e2e"}}
}

func (f fixtureEvidence) ListLiveEvidenceForInsight(context.Context, string,
	string) ([]domain.EvidenceItem, error) {
	return []domain.EvidenceItem{f.item()}, nil
}

func (fixtureEvidence) CountInsightEvidenceSupport(context.Context, string,
	string) (domain.SupportCounts, error) {
	// Total 3／Live 1 与列表行的 `evidence_count=3` 是同一个数：一颗洞察在两个面上
	// 给出的依据总数必须能对上（`summarize` 用 len(live) 当 count、Total-Live 当 withdrawn）。
	return domain.SupportCounts{Total: 3, Live: 1}, nil
}

func (fixtureEvidence) ListPatternsForInsightReading(context.Context, string,
	string) ([]domain.PatternReadable, error) {
	return []domain.PatternReadable{{ID: "p-e2e", PatternType: "temporal",
		Description: "深夜记录", Status: "supported", EvidenceCount: 3}}, nil
}

func (f fixtureEvidence) ListLivePatternEvidenceForInsight(context.Context, string,
	string) ([]domain.PatternEvidenceLink, error) {
	return []domain.PatternEvidenceLink{{PatternID: "p-e2e", Item: f.item()}}, nil
}

// fixtureFeedback 把**领域层真正收到的那个提案**原样吐回 Detail 串：
// 跨进程唯一能证明「身份来自 header、目标来自 path」的位置就是这里。
type fixtureFeedback struct{}

func (fixtureFeedback) RecordUserFeedback(_ context.Context,
	p domain.FeedbackProposal) (domain.FeedbackReceipt, error) {
	if p.UserID != fixtureUser {
		return domain.FeedbackReceipt{}, domain.ErrInvalidOwnership
	}
	if p.Type == domain.FeedbackReject {
		return domain.FeedbackReceipt{}, domain.ErrDuplicateFeedback
	}
	op := p.Metadata["operation_id"]
	return domain.FeedbackReceipt{ID: "fb-e2e", TargetStatus: "confirmed", Changed: true,
		ModelVersion: 7,
		Detail: fmt.Sprintf("type=%s target=%s user=%s text=%q op=%s",
			string(p.Type), p.TargetID, p.UserID, p.CorrectionText, op)}, nil
}

// fixtureGoal 是 T4-02 读面的夹具：一个用户一颗目标、一条行动。
//
// 为什么这里必须有行而不是空切片：`internalEnabled()` 现在把 goals 也算进注册判据，
// 装配漏了这一格的表现不是「目标面 404」而是**整个内部面 404**（四格依赖一起判，
// v3.go 的注释同口径）。空列表至少还能回 200，有行才让跨进程那侧看得见形状。
type fixtureGoal struct{}

func (fixtureGoal) ListGoalRecords(_ context.Context, userID, status string,
	limit int) ([]domain.GoalRecord, error) {
	if userID != fixtureUser || (status != "" && status != "active") {
		return []domain.GoalRecord{}, nil
	}
	return []domain.GoalRecord{{ID: "g-e2e", Title: "早睡一小时", Description: "",
		Status: "active", StartedAt: published[0], CreatedAt: published[0],
		UpdatedAt: published[0]}}, nil
}

func (fixtureGoal) ListActionRecords(_ context.Context, userID, status string,
	limit int) ([]domain.ActionRecord, error) {
	if userID != fixtureUser || (status != "" && status != "active") {
		return []domain.ActionRecord{}, nil
	}
	return []domain.ActionRecord{{ID: "a-e2e", Title: "提前上床", Status: "active",
		GoalID: "g-e2e", CreatedAt: published[0], UpdatedAt: published[0]}}, nil
}

// fixtureGoalWrites 与 fixtureFeedback 同一格职责：把领域层**真正收到的那一格**
// 原样吐回 Detail——跨进程唯一能证明「身份来自 header、goal_id 来自 path」的位置。
type fixtureGoalWrites struct{}

func (fixtureGoalWrites) CreateGoal(_ context.Context,
	p domain.GoalProposal) (domain.GoalReceipt, error) {
	if p.UserID != fixtureUser {
		return domain.GoalReceipt{}, domain.ErrInvalidOwnership
	}
	return domain.GoalReceipt{ID: "g-e2e", Status: "active", Changed: true,
		EventID: "ev-e2e", ModelVersion: 7,
		Detail: fmt.Sprintf("goal title=%q target=%t", p.Title, p.HasTargetAt)}, nil
}

func (fixtureGoalWrites) TransitionGoal(_ context.Context, userID, goalID string,
	verb domain.LifecycleVerb) (domain.GoalReceipt, error) {
	if userID != fixtureUser {
		return domain.GoalReceipt{}, domain.ErrInvalidOwnership
	}
	if verb == domain.VerbSkip {
		return domain.GoalReceipt{}, domain.ErrGoalIllegalTransition
	}
	return domain.GoalReceipt{ID: goalID, Status: "cancelled", Changed: true,
		EventID: "ev-e2e", ModelVersion: 7,
		Detail: fmt.Sprintf("goal verb=%s", string(verb))}, nil
}

func (fixtureGoalWrites) CreateAction(_ context.Context,
	p domain.ActionProposal) (domain.ActionReceipt, error) {
	if p.UserID != fixtureUser {
		return domain.ActionReceipt{}, domain.ErrInvalidOwnership
	}
	return domain.ActionReceipt{ID: "a-e2e", GoalID: p.GoalID, Status: "active",
		Changed: true, EventID: "ev-e2e", ModelVersion: 7,
		Detail: fmt.Sprintf("action goal=%s title=%q", p.GoalID, p.Title)}, nil
}

func (fixtureGoalWrites) TransitionAction(_ context.Context, userID, actionID string,
	verb domain.LifecycleVerb) (domain.ActionReceipt, error) {
	if userID != fixtureUser {
		return domain.ActionReceipt{}, domain.ErrInvalidOwnership
	}
	return domain.ActionReceipt{ID: actionID, GoalID: "g-e2e", Status: "completed",
		Changed: true, EventID: "ev-e2e", ModelVersion: 7,
		Detail: fmt.Sprintf("action verb=%s", string(verb))}, nil
}

// ── 装配：与 cmd/go-api 同一组构造函数，只有依赖换成了夹具 ────────────────────

func buildHandler(token string) http.Handler {
	cfg := config.Config{AppName: "ClosenAI", Environment: "development",
		HTTPAddr: ":0", InsightEvidenceRead: "on", GoServiceToken: token}
	signer := auth.NewSigner("v3surface-e2e-secret-v3surface-e2e-secret",
		30*time.Minute, 30*24*time.Hour)
	explainer := domain.NewInsightExplainer(fixtureEvidence{},
		domain.Policy{Read: domain.ReadPolicy{EvidenceMaxItems: 20, PatternMaxItems: 6}})
	browser := domain.NewInsightBrowser(fixtureBrowse{}, explainer)
	return httpapi.NewServer(cfg, signer,
		auth.NewService(store.NewFakeStore(), signer, 30*24*time.Hour), explainer,
		nil, nil, nil, nil, nil, nil,
		&httpapi.V3{Insights: browser, Outcomes: fixtureFeedback{},
			Goals:      domain.NewGoalBrowser(fixtureGoal{}),
			GoalWrites: fixtureGoalWrites{}}).Handler()
}

func main() {
	addr := flag.String("addr", "127.0.0.1:0", "监听地址；:0 由内核分配后回吐")
	flag.Parse()
	token := strings.TrimSpace(os.Getenv("GO_SERVICE_TOKEN"))
	if token == "" {
		// 不静默降级成「没有内部面」：那样 pytest 只会看到一串 404，
		// 而归因会指向 BFF——缺的其实是本机没导环境变量。
		fmt.Fprintln(os.Stderr, "GO_SERVICE_TOKEN 为空 ⇒ 这一面本来就不该注册，本命令拒绝启动")
		os.Exit(2)
	}
	ln, err := net.Listen("tcp", *addr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "listen %s: %v\n", *addr, err)
		os.Exit(2)
	}
	out, err := json.Marshal(map[string]string{"addr": ln.Addr().String(), "pid": fmt.Sprint(os.Getpid())})
	if err != nil {
		os.Exit(2)
	}
	fmt.Println(string(out))
	os.Stdout.Sync()

	// stdin 关闭＝驱动进程走了：这是 pytest 侧最省事的停机方式（不必记 pid、不靠信号投递）。
	go func() {
		_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
		_ = ln.Close()
		os.Exit(0)
	}()
	if err := http.Serve(ln, buildHandler(token)); err != nil {
		fmt.Fprintf(os.Stderr, "serve: %v\n", err)
		os.Exit(1)
	}
}
