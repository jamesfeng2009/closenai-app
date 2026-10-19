// T5-04 的真库测：proactives 审计面的**谓词与归属**（工单 §验收 两句话的库侧落点）。
//
// 为什么这几格只有真库能答（domain 的单测与桩都给不出）：
//
//  1. **「到期却没发出去」是三列一起说的**（sent_at IS NULL ∧ 状态在那三态里 ∧
//     scheduled_at/expires_at 已过期）。桩只记录参数，谓词错了它照样回同样的行；
//     而谓词写错的后果是**审计少报**——卡住的触达没被看见，比错报更难发现。
//  2. **跨用户归属在 WHERE 那一格**：少带半格就把别人的触达端给这个用户，
//     而这一面的调用方是运维/客服，那条谎会直接变成一次错误的排障。
//  3. **可空列的两个方向**：block_reason 为 NULL 时必须读成空串再折成 JSON null，
//     而不是读成一句「原因就叫空串」；scheduled_at 为 NULL 必须是零值时间而不是
//     「epoch 那一刻排过程」。
//  4. **LIMIT+1 与排序在库上**：`ORDER BY created_at DESC, id` 的次级键只在同刻落地
//     两行时才起作用（补记/批量），而多取的那一行是 `truncated` 唯一的证据来源。
//  5. **`status` 那个 `OR` 的两个方向**：空串＝全量、给了＝只给那一态、词表外＝零行而不是错。
//     单边写法（`status = $2`）在桩上永远看不出来——桩不看 SQL，它只看参数。
//
// 未设 TEST_DATABASE_URL 时跳过，与其余 store 集成测同口径（缺 env 是「未跑」，不是绿）。
package store

import (
	"context"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store/canonicaldb"
)

// auditSeed 是一行要种进 proactives 的触达。
//
// 时间一律由调用方给死而不是 now()：这条测判的是「超期谓词」，
// 用 now() 会让它随测试运行时刻漂——夜里跑与白天跑结果不同的一条测不是测。
type auditSeed struct {
	id           string
	userID       string
	status       string
	scheduledAt  *time.Time
	sentAt       *time.Time
	expiresAt    *time.Time
	blockReason  *string
	rejectReason *string
	createdAt    time.Time
}

func writeAuditProactive(t *testing.T, pool *pgxpool.Pool, modelID string, seed auditSeed) {
	t.Helper()
	ctx := context.Background()
	// journey_event_id 是 varchar(36)，加前缀必须截断，否则直接 22001。
	// 它**不等于** id：投影把两列接反时，同值夹具什么也照不出来。
	journey := fmt.Sprintf("journey-%s", seed.id)
	if len(journey) > 36 {
		journey = journey[:36]
	}
	_, err := pool.Exec(ctx, `
		INSERT INTO proactives (
			id, user_id, personal_model_id, status, trigger_type, message,
			scheduled_at, sent_at, expires_at, block_reason, reject_reason,
			journey_event_id, created_at, updated_at
		) VALUES ($1,$2,$3,$4,'memory_recall','PRIVATE BODY SHOULD NOT LEAVE THIS TABLE',
			$5,$6,$7,$8,$9,$10,$11,$11)`,
		seed.id, seed.userID, modelID, seed.status,
		seed.scheduledAt, seed.sentAt, seed.expiresAt, seed.blockReason, seed.rejectReason,
		journey, seed.createdAt)
	if err != nil {
		t.Fatalf("种 proactive %s：%v", seed.id, err)
	}
}

func ptr[T any](v T) *T { return &v }

// ── 1. 超期谓词：五种行，四种「不算超期」里每一种都是一句会被人顺口说成的谎 ────

func TestAuditStalePredicateSelectsExactlyTheStuckRows(t *testing.T) {
	pool := poolForTest(t)
	ctx := context.Background()
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })
	modelID, _ := plantModelOnly(t, canonicaldb.New(pool), userID)

	stuck := time.Date(2026, 10, 18, 9, 0, 0, 0, time.UTC)
	future := time.Date(2026, 12, 1, 9, 0, 0, 0, time.UTC)
	day := func(n int) time.Time { return time.Date(2026, 10, 10+n, 8, 0, 0, 0, time.UTC) }

	cases := []struct {
		name  string
		seed  auditSeed
		stale bool
	}{
		{"排期已过且没发（scheduled）", auditSeed{
			id: newID(), userID: userID, status: "scheduled",
			scheduledAt: ptr(stuck), createdAt: day(1),
		}, true},
		{"排期已过但状态还停在 candidate", auditSeed{
			id: newID(), userID: userID, status: "candidate",
			scheduledAt: ptr(stuck), createdAt: day(2),
		}, true},
		{"排期已过、expires_at 也过了（eligible）", auditSeed{
			id: newID(), userID: userID, status: "eligible",
			scheduledAt: ptr(stuck), expiresAt: ptr(stuck), createdAt: day(3),
		}, true},
		{"排期在未来 ⇒ 还没轮到，不是超期", auditSeed{
			id: newID(), userID: userID, status: "scheduled",
			scheduledAt: ptr(future), createdAt: day(4),
		}, false},
		{"已发送 ⇒ 不是超期（sent_at 有值就该退场）", auditSeed{
			id: newID(), userID: userID, status: "sent",
			scheduledAt: ptr(stuck), sentAt: ptr(stuck.Add(time.Hour)), createdAt: day(5),
		}, false},
		// 这一格只为 `sent_at IS NULL` 那半句存在：状态停在 scheduled 但时间戳已经写上
		// （worker 写完发送、状态列没推进的那种半途行）。少这一格，摘掉 `sent_at IS NULL`
		// 在真库上是**等价变异**——因为另外三态本来就被状态列表挡住了。
		{"状态没推进但已写出 sent_at ⇒ 发出去了，不是卡住", auditSeed{
			id: newID(), userID: userID, status: "scheduled",
			scheduledAt: ptr(stuck), sentAt: ptr(stuck), createdAt: day(8),
		}, false},
		{"已过期 ⇒ worker 已经处理过它，不是「卡住」", auditSeed{
			id: newID(), userID: userID, status: "expired",
			scheduledAt: ptr(stuck), expiresAt: ptr(stuck), createdAt: day(6),
		}, false},
		{"被挡住的（blocked）⇒ 有 block_reason，属于「为什么不发」而不是「超期」", auditSeed{
			id: newID(), userID: userID, status: "blocked",
			scheduledAt: ptr(stuck), blockReason: ptr("quiet_hours"), createdAt: day(7),
		}, false},
	}
	for _, c := range cases {
		writeAuditProactive(t, pool, modelID, c.seed)
	}

	wantStale := 0
	for _, c := range cases {
		if c.stale {
			wantStale++
		}
	}

	browser := domain.NewProactiveAuditBrowser(NewCanonical(pool))
	view, err := browser.List(ctx, userID, "", 50, true)
	if err != nil {
		t.Fatalf("stale 查询：%v", err)
	}
	if len(view.Items) != wantStale {
		var got []string
		for _, row := range view.Items {
			got = append(got, row.Status+"@"+row.ID)
		}
		t.Errorf("超期行 = %d（%v），期望 %d ⇒ 谓词少一态＝审计少报，多一态＝把已发送的也说成卡住",
			len(view.Items), got, wantStale)
	}
	// 反向证人：不带 stale 时全量都要在（否则「全量」那一句没被证过）。
	all, err := browser.List(ctx, userID, "", 50, false)
	if err != nil {
		t.Fatalf("全量查询：%v", err)
	}
	if len(all.Items) != len(cases) {
		t.Errorf("全量 = %d 行，期望 %d ⇒ 少的那些行在审计面上根本不存在",
			len(all.Items), len(cases))
	}
}

// ── 2. 归属与排序：别人的行、可空列、次级排序键 ──────────────────────────────

func TestAuditReadScopesToUserKeepsNullsAndOrders(t *testing.T) {
	pool := poolForTest(t)
	ctx := context.Background()
	userID, other := newID(), newID()
	t.Cleanup(func() {
		cleanupCanonical(t, pool, userID)
		cleanupCanonical(t, pool, other)
	})
	modelID, _ := plantModelOnly(t, canonicaldb.New(pool), userID)
	otherModel, _ := plantModelOnly(t, canonicaldb.New(pool), other)

	same := time.Date(2026, 10, 11, 8, 0, 0, 0, time.UTC)
	// 两行同刻：次级键 `id` 唯一的用途就是这里，删掉它在单测里照样绿。
	idA, idB := "a-fixed-id", "b-fixed-id"
	seeds := []auditSeed{
		{id: idB, userID: userID, status: "sent", sentAt: ptr(same), createdAt: same},
		{id: idA, userID: userID, status: "blocked", blockReason: ptr("frequency_cap"),
			createdAt: same},
		{id: newID(), userID: userID, status: "scheduled",
			createdAt: time.Date(2026, 10, 12, 8, 0, 0, 0, time.UTC)},
		// 别人的两行：这一面唯一的越权路径就是 WHERE 少带 user_id。
		{id: newID(), userID: other, status: "scheduled", createdAt: same},
		{id: newID(), userID: other, status: "sent", sentAt: ptr(same), createdAt: same},
	}
	for _, s := range seeds {
		if s.userID == other {
			writeAuditProactive(t, pool, otherModel, s)
		} else {
			writeAuditProactive(t, pool, modelID, s)
		}
	}

	browser := domain.NewProactiveAuditBrowser(NewCanonical(pool))
	view, err := browser.List(ctx, userID, "", 50, false)
	if err != nil {
		t.Fatalf("读审计面：%v", err)
	}
	if len(view.Items) != 3 {
		t.Errorf("读到 %d 行，期望 3 ⇒ 跨用户泄漏了 %d 行（审计端出去的是别人的触达动线）",
			len(view.Items), len(view.Items)-3)
	}
	for _, row := range view.Items {
		if row.ID == "" || row.Status == "" || row.TriggerType != "memory_recall" {
			t.Errorf("行形状坏：%+v", row)
		}
		// 正文不在这面的投影里：它一旦能被读出来，每次排障就多一条数据外流的路。
		if strings.Contains(fmt.Sprintf("%+v", row), "PRIVATE BODY") {
			t.Errorf("审计行里出现了正文：%+v", row)
		}
	}
	// created_at DESC 的那一行必须是 10-12 那条。
	if !view.Items[0].CreatedAt.Equal(seeds[2].createdAt) {
		t.Errorf("首行 created_at = %v，期望 %v ⇒ 排序键不生效，翻页与「最新一条」都会错",
			view.Items[0].CreatedAt, seeds[2].createdAt)
	}
	// 同刻两行按 id 升序（a 在 b 前）：次级键没了就是「每次读到的顺序不一样」。
	if view.Items[1].ID != idA || view.Items[2].ID != idB {
		t.Errorf("同刻两行的顺序 = %q,%q，期望 %q,%q ⇒ 去掉次级排序键在真库里静默",
			view.Items[1].ID, view.Items[2].ID, idA, idB)
	}

	// 可空列的两个方向：NULL ⇒ 零值时间/空串，由 httpapi 折成 JSON null。
	var blocked, plain *domain.ProactiveAuditRecord
	for i := range view.Items {
		switch view.Items[i].ID {
		case idA:
			blocked = &view.Items[i]
		case idB:
			plain = &view.Items[i]
		}
	}
	if blocked == nil || plain == nil {
		t.Fatal("夹具行没读到，上面的顺序判据全部失真")
	}
	if blocked.BlockReason != "frequency_cap" {
		t.Errorf("block_reason = %q，期望 frequency_cap ⇒ 工单点名的 skip_reason 真身在这一列",
			blocked.BlockReason)
	}
	if plain.BlockReason != "" {
		t.Errorf("NULL 的 block_reason 读成了 %q ⇒「没有原因」被读成「原因是一个空串」",
			plain.BlockReason)
	}
	// 12 列按位置搬进结构体：这一格证明 journey 不是 id 的复读（同值就照不出接反）。
	if blocked.JourneyEventID != "journey-a-fixed-id" || blocked.ID == blocked.JourneyEventID {
		t.Errorf("journey_event_id = %q（id %q），期望 journey-a-fixed-id ⇒ 投影列接反",
			blocked.JourneyEventID, blocked.ID)
	}
	// 非 NULL 的 sent_at 必须原样读回来：§验收 那句「最近发送记录」判的就是这一格。
	if !plain.SentAt.Equal(same) {
		t.Errorf("sent_at = %v，期望 %v ⇒ 发过的那一刻读丢了，审计上的发送史不可信",
			plain.SentAt, same)
	}
	if !blocked.SentAt.IsZero() {
		t.Errorf("NULL 的 sent_at 读成了 %v ⇒ 库里没发过，读面却有一个发送时刻", blocked.SentAt)
	}
}

// ── 3. 投影里没有正文列（§怎么做.2「仅供运维/客服」的最省事证法）─────────────

func TestAuditProjectionHasNoUserContentColumns(t *testing.T) {
	pool := poolForTest(t)
	ctx := context.Background()
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })
	modelID, _ := plantModelOnly(t, canonicaldb.New(pool), userID)
	writeAuditProactive(t, pool, modelID, auditSeed{
		id: newID(), userID: userID, status: "sent",
		sentAt:    ptr(time.Date(2026, 10, 10, 8, 0, 0, 0, time.UTC)),
		createdAt: time.Date(2026, 10, 10, 8, 0, 0, 0, time.UTC),
	})

	// 直接问库：正文那两列**在表里有值**，而这一面读不出来（读出来就是外流）。
	var message string
	err := pool.QueryRow(ctx,
		`SELECT message FROM proactives WHERE user_id = $1`, userID).Scan(&message)
	if err != nil {
		t.Fatalf("取正文：%v", err)
	}
	if message == "" {
		t.Fatal("夹具正文没种进去 ⇒ 下面那条判据在扫空集，恒真")
	}
	view, err := domain.NewProactiveAuditBrowser(NewCanonical(pool)).
		List(ctx, userID, "", 50, false)
	if err != nil {
		t.Fatalf("读审计面：%v", err)
	}
	if len(view.Items) != 1 {
		t.Fatalf("items = %d，期望 1", len(view.Items))
	}
	// 结构体里没有正文类字段这一事实由编译期保证；这里证的是**取值全空**，
	// 也就是说投影真的没搬那两列（搬了就会在这里非空）。
	row := view.Items[0]
	if row.BlockReason != "" || row.RejectReason != "" || row.JourneyEventID == "" {
		t.Errorf("reason/journey 形状坏：%q/%q/%q", row.BlockReason, row.RejectReason,
			row.JourneyEventID)
	}
}

// ── 4. status 过滤器那个 `OR` 的两个方向（写成单边就是审计面上「全量」永远空）──

func TestAuditStatusFilterKeepsBothDirections(t *testing.T) {
	pool := poolForTest(t)
	ctx := context.Background()
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })
	modelID, _ := plantModelOnly(t, canonicaldb.New(pool), userID)

	// 四种态：两行 sent 是为了让「按态筛」真的少掉几行（各一行的夹具证不出过滤器）。
	seeds := []auditSeed{
		{id: newID(), userID: userID, status: "sent",
			sentAt:    ptr(time.Date(2026, 10, 10, 8, 0, 0, 0, time.UTC)),
			createdAt: time.Date(2026, 10, 10, 8, 0, 0, 0, time.UTC)},
		{id: newID(), userID: userID, status: "sent",
			sentAt:    ptr(time.Date(2026, 10, 11, 8, 0, 0, 0, time.UTC)),
			createdAt: time.Date(2026, 10, 11, 8, 0, 0, 0, time.UTC)},
		{id: newID(), userID: userID, status: "scheduled",
			createdAt: time.Date(2026, 10, 12, 8, 0, 0, 0, time.UTC)},
		{id: newID(), userID: userID, status: "cancelled", rejectReason: ptr("user_opt_out"),
			createdAt: time.Date(2026, 10, 13, 8, 0, 0, 0, time.UTC)},
	}
	for _, s := range seeds {
		writeAuditProactive(t, pool, modelID, s)
	}
	browser := domain.NewProactiveAuditBrowser(NewCanonical(pool))

	// 方向一：空串＝全量。`status = $2` 那种「单边写法」在这里当场露出来
	// （库里没有一个态叫空串 ⇒ 审计面的「全部触达」永远是零行）。
	all, err := browser.List(ctx, userID, "", 50, false)
	if err != nil {
		t.Fatalf("空过滤器：%v", err)
	}
	if len(all.Items) != len(seeds) || all.AppliedStatus != "" {
		t.Errorf("空串过滤器 = %d 行／回显 %q，期望 %d/空 ⇒ 空串那一格被当成了真的要查的值",
			len(all.Items), all.AppliedStatus, len(seeds))
	}
	// 方向二：给了就只给那一态。
	sent, err := browser.List(ctx, userID, "sent", 50, false)
	if err != nil {
		t.Fatalf("sent 过滤器：%v", err)
	}
	if len(sent.Items) != 2 {
		t.Errorf("sent = %d 行，期望 2 ⇒ 过滤器变成了装饰，审计口径不可复现", len(sent.Items))
	}
	for _, row := range sent.Items {
		if row.Status != "sent" {
			t.Errorf("按 sent 筛却回了 %q（id %s）", row.Status, row.ID)
		}
	}
	// 两格原因列各归各：全量那一趟里有**一行**只有 reject_reason、没有 block_reason。
	// 仓储把两列接反时上面每条判据都还绿——只有这一格看得见（工单 §怎么做.1 的
	// `skip_reason` 之所以是改判，就是因为库里从来是两格而不是一格）。
	var rejected, blocked int
	for _, row := range all.Items {
		if row.RejectReason != "" {
			rejected++
			if row.BlockReason != "" {
				t.Errorf("id %s 两格都有值 ⇒ 夹具里分开种的，读出来却并成了一格", row.ID)
			}
		}
		if row.BlockReason != "" {
			blocked++
		}
	}
	if rejected != 1 || blocked != 0 {
		t.Errorf("reject_reason 非空 %d 行／block_reason 非空 %d 行，期望 1/0", rejected, blocked)
	}
	// 方向三（不校验词表那一格的库侧证据）：库里没有的那一态是**零行**而不是错误。
	// 报错的话「这个人没有这种触达」与「这个词不存在」就分成两种形状，
	// 而 domain 与 insights 那条面用的是同一个判据（narrowListArgs）。
	none, err := browser.List(ctx, userID, "pending", 50, false)
	if err != nil || len(none.Items) != 0 || none.AppliedStatus != "pending" {
		t.Errorf("pending = err %v／items %d／回显 %q，期望无错、零行、看得见过滤器",
			err, len(none.Items), none.AppliedStatus)
	}
}

// ── 5. limit+1 与 truncated 的配合只能在库上跑一次 ──────────────────────────

func TestAuditOverfetchRowBecomesTruncated(t *testing.T) {
	pool := poolForTest(t)
	ctx := context.Background()
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })
	modelID, _ := plantModelOnly(t, canonicaldb.New(pool), userID)
	for i := 0; i < 3; i++ {
		writeAuditProactive(t, pool, modelID, auditSeed{
			id: newID(), userID: userID, status: "scheduled",
			createdAt: time.Date(2026, 10, 10+i, 8, 0, 0, 0, time.UTC),
		})
	}
	browser := domain.NewProactiveAuditBrowser(NewCanonical(pool))
	view, err := browser.List(ctx, userID, "", 2, false)
	if err != nil {
		t.Fatalf("limit=2：%v", err)
	}
	if len(view.Items) != 2 || !view.Truncated {
		t.Errorf("items %d／truncated %v，期望 2/true ⇒ SQL 的 LIMIT 与领域的 +1 没接上，"+
			"审计会安静地少报", len(view.Items), view.Truncated)
	}
	view, err = browser.List(ctx, userID, "", 3, false)
	if err != nil {
		t.Fatalf("limit=3：%v", err)
	}
	if len(view.Items) != 3 || view.Truncated {
		t.Errorf("恰好取满 3 行却 items %d／truncated %v，期望 3/false ⇒ 「还有」是假话",
			len(view.Items), view.Truncated)
	}
}
