// N5 的真库集成测：资格门的判定逻辑由 internal/domain 的单测证明，
// 这里只证明**只有真库能答**的五件事：
//  1. 一行的列形状——eligibility_score 走 DECIMAL(5,4) 不被截断、eligibility_detail
//     存的是可解析的 packet JSON、expires_at/block_reason/reject_reason 各归各位；
//  2. 读面真的取到库态：ListRefStates 的七表 UNION、users 的 legacy 偏好列、
//     chat_messages 只认 role='user'（沉默阶梯的基准）、user_feedback 的三个负面码；
//  3. 发送落库后**当日第二条被真实历史挡住**（频次关的数据面，fake 证不了）；
//  4. 终态守卫在 SQL 侧生效：08 §7 的条件更新对 sent/cancelled 行返回 0 行；
//  5. Admit 不推进模型版本（02:1069），也不写 personal_model_versions。
//
// 未设 TEST_DATABASE_URL 时跳过，与 N2/N3/N4 的 store 集成测同口径。
package store

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgtype"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store/canonicaldb"
)

const (
	n5Message  = "上周你提到连续几周把项目赶到了凌晨，聊天里的工作进度和睡眠时间常常一起出现。你现在最想先聊哪一块，还是先看这周的记录？"
	n5Message2 = "这周的记录里两次很晚才开始休息，而明天的安排排在上午十点之后。你现在最想先聊哪一块，或者想先看哪一天的记录？"
)

// n5User 是 legacy users 表的触达相关列（GetUserNotificationPrefs 读的就是这些）。
type n5User struct {
	timezone, pushFrequency, tier, status string
	aiConsent                             bool
	quotaUsed                             int
	createdAt                             time.Time
}

func plantNotificationUser(t *testing.T, pool *pgxpool.Pool, userID string, u n5User) {
	t.Helper()
	ctx := context.Background()
	if _, err := pool.Exec(ctx, `
		INSERT INTO users (id, timezone, streak_count, relationship_level, chat_count,
			daily_quota_used, tier, created_at, push_frequency, push_enabled,
			ai_consent, status)
		VALUES ($1, $2, 0, 'stranger', 0, $3, $4, now(), $5, true, $6, $7)`,
		userID, u.timezone, u.quotaUsed, u.tier, u.pushFrequency, u.aiConsent, u.status); err != nil {
		t.Fatalf("plant users row: %v", err)
	}
	if !u.createdAt.IsZero() {
		if _, err := pool.Exec(ctx, `UPDATE users SET created_at = $2 WHERE id = $1`,
			userID, u.createdAt); err != nil {
			t.Fatalf("age user: %v", err)
		}
	}
	t.Cleanup(func() {
		_, _ = pool.Exec(ctx, `DELETE FROM chat_messages WHERE user_id = $1`, userID)
		_, _ = pool.Exec(ctx, `DELETE FROM users WHERE id = $1`, userID)
	})
}

func plantChatMessage(t *testing.T, pool *pgxpool.Pool, userID, role string, at time.Time) {
	t.Helper()
	if _, err := pool.Exec(context.Background(), `
		INSERT INTO chat_messages (id, user_id, message_type, role, content,
			ai_generated, created_at)
		VALUES ($1, $2, 'text', $3, 'planted for N5', $4, $5)`,
		newID(), userID, role, role != "user", at); err != nil {
		t.Fatalf("plant chat message: %v", err)
	}
}

// sendableZone 给出一个「现在不落在本地 00:00-08:00」的真实时区：静默窗按用户本地
// 小时判，而集成测跑在墙上时钟上，必须自带一个与跑测时刻无关的放行窗。
func sendableZone(t *testing.T) string {
	t.Helper()
	for _, zone := range []string{"UTC", "Asia/Shanghai", "Asia/Tokyo", "Europe/Berlin",
		"America/New_York", "America/Los_Angeles", "Australia/Sydney", "Asia/Kolkata"} {
		loc, err := time.LoadLocation(zone)
		if err != nil {
			continue
		}
		if hour := time.Now().In(loc).Hour(); hour >= 8 && hour < 22 {
			return zone
		}
	}
	t.Skip("候选时区此刻都在静默窗内，无法验证放行路径")
	return ""
}

// n5Candidate 指向 plantChain 种好的对象图：证据 0.9（≥ 强支撑下限）、
// goal 的 target_at 在 30 天后（第③层的锚点）、pattern 与 insight 都活着。
func n5Candidate(userID string, c chain, message string) domain.ProactiveCandidate {
	return domain.ProactiveCandidate{
		UserID:          userID,
		ID:              newID(),
		TriggerType:     domain.TriggerMemoryRecall,
		TriggerSource:   domain.SourceUserRequestedFollow,
		Message:         message,
		Confidence:      0.90,
		EvidenceIDs:     []string{c.evidenceID},
		RefIDs:          []string{c.goalID, c.patternID, c.insightID},
		AnchorRefID:     c.goalID,
		GenerationRunID: newID(),
		GenerationModel: "test-model",
		Safety:          &domain.Safety{Status: domain.SafetyOK},
	}
}

func readProactiveRow(t *testing.T, pool *pgxpool.Pool,
	userID, id string) (status, detail, reject, block string, score float64, expires pgtype.Timestamptz) {
	t.Helper()
	err := pool.QueryRow(context.Background(), `
		SELECT status, coalesce(eligibility_detail, ''), coalesce(reject_reason, ''),
		       coalesce(block_reason, ''), eligibility_score, expires_at
		FROM proactives WHERE id = $1 AND user_id = $2`, id, userID).
		Scan(&status, &detail, &reject, &block, &score, &expires)
	if err != nil {
		t.Fatalf("read proactive: %v", err)
	}
	return status, detail, reject, block, score, expires
}

// 列形状与留痕：一行 eligible 必须能读回可解析的 packet，且不动模型版本。
func TestProactiveAdmitWritesCanonicalRowInDB(t *testing.T) {
	pool := poolForTest(t)
	ctx := context.Background()
	userID := "n5-admit-" + newID()[:8]
	defer func() { cleanupCanonical(t, pool, userID) }()
	plantNotificationUser(t, pool, userID, n5User{timezone: sendableZone(t),
		pushFrequency: "daily", tier: "free", aiConsent: true, status: "active"})
	c := plantChain(t, canonicaldb.New(pool), userID)

	versionBefore := countWhere(t, pool,
		`SELECT current_version FROM personal_models WHERE user_id = $1`, userID)
	svc := serviceFor(pool)
	res, err := svc.AdmitProactive(ctx, n5Candidate(userID, c, n5Message))
	if err != nil {
		t.Fatalf("AdmitProactive: %v", err)
	}
	if res.Status != string(domain.ProactiveStatusEligible) {
		t.Fatalf("status = %s，reason=%s detail=%s", res.Status, res.Reason, res.Detail)
	}

	status, detail, reject, block, score, expires := readProactiveRow(t, pool, userID, res.ID)
	if status != "eligible" || reject != "" || block != "" {
		t.Errorf("库内 status=%q reject=%q block=%q，全绿行不该有拒因", status, reject, block)
	}
	// 命中四层 ⇒ 1.0000；DECIMAL(5,4) 若被截断这里就会差。
	if score != 1.0 {
		t.Errorf("eligibility_score = %v，期望 1.0（P-8：命中层数/4）", score)
	}
	if !expires.Valid || !expires.Time.After(time.Now().UTC().Add(-time.Minute)) {
		t.Errorf("expires_at = %v，04 §19 的到期判定需要这一列", expires)
	}

	packet := domain.EligibilityPacket{}
	if err := json.Unmarshal([]byte(detail), &packet); err != nil {
		t.Fatalf("eligibility_detail 不是可解析 packet：%v / %s", err, detail)
	}
	if len(packet.Checks) != 11 || len(packet.Layers) != 4 {
		t.Errorf("packet checks/layers = %d/%d，期望 11/4", len(packet.Checks), len(packet.Layers))
	}
	if packet.AnchorRefID != c.goalID || packet.AnchorAt == "" {
		t.Errorf("packet 锚点 = %q/%q，期望 goal 的 target_at", packet.AnchorRefID, packet.AnchorAt)
	}
	// goal 没有回链表：它只能以 packet.ref_ids 的形式留下（同承诺回访的计数依据）。
	if !strings.Contains(detail, c.goalID) {
		t.Errorf("packet.ref_ids 没带 goal：%s", detail)
	}

	// 回链只写两张表。
	if n := countWhere(t, pool,
		`SELECT count(*) FROM proactive_patterns WHERE proactive_id = $1`, res.ID); n != 1 {
		t.Errorf("proactive_patterns=%d，期望 1", n)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM proactive_insights WHERE proactive_id = $1`, res.ID); n != 1 {
		t.Errorf("proactive_insights=%d，期望 1", n)
	}

	// 版本口径：触达不是「模型相信什么」⇒ current_version 不动、版本行不写，
	// 但 personal_model_events 必须留一行（02 §24 的可回溯性）。
	if after := countWhere(t, pool,
		`SELECT current_version FROM personal_models WHERE user_id = $1`, userID); after != versionBefore {
		t.Errorf("current_version %d→%d，AdmitProactive 不得 ++（02:1069）", versionBefore, after)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_versions WHERE user_id = $1`, userID); n != 0 {
		t.Errorf("personal_model_versions=%d，一条触达不该产生版本行", n)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE user_id = $1
           AND event_type = 'proactive_eligible' AND aggregate_id = $2`, userID, res.ID); n != 1 {
		t.Errorf("proactive_eligible 事件 %d 条，期望 1", n)
	}

	// 行上快照的版本 = 当时模型行的版本（不是 ++ 之后的）。
	var modelVersion int64
	if err := pool.QueryRow(ctx,
		`SELECT model_version FROM proactives WHERE id = $1`, res.ID).Scan(&modelVersion); err != nil {
		t.Fatalf("read model_version: %v", err)
	}
	if modelVersion != versionBefore {
		t.Errorf("model_version = %d，期望快照值 %d", modelVersion, versionBefore)
	}
}

// 发送时刻的二次校验在真库上走通，且**已发的那条**会把当日第二条挡住；
// 终态行由 SQL 的条件更新守住（fake 只能证明 Go 侧不调用，证不了 0 行守卫）。
func TestProactiveRecheckMarksSentAndRealHistoryBlocksSecondSend(t *testing.T) {
	pool := poolForTest(t)
	ctx := context.Background()
	userID := "n5-send-" + newID()[:8]
	defer func() { cleanupCanonical(t, pool, userID) }()
	plantNotificationUser(t, pool, userID, n5User{timezone: sendableZone(t),
		pushFrequency: "daily", tier: "free", aiConsent: true, status: "active"})
	c := plantChain(t, canonicaldb.New(pool), userID)
	svc := serviceFor(pool)

	first, err := svc.AdmitProactive(ctx, n5Candidate(userID, c, n5Message))
	if err != nil {
		t.Fatalf("AdmitProactive: %v", err)
	}
	sent, err := svc.RecheckProactive(ctx, userID, first.ID, &domain.Safety{Status: domain.SafetyOK})
	if err != nil {
		t.Fatalf("RecheckProactive: %v", err)
	}
	if sent.Status != string(domain.ProactiveStatusSent) {
		t.Fatalf("status = %s（%s），全绿应置 sent", sent.Status, sent.Detail)
	}
	var sentAt pgtype.Timestamptz
	if err := pool.QueryRow(ctx,
		`SELECT sent_at FROM proactives WHERE id = $1`, first.ID).Scan(&sentAt); err != nil {
		t.Fatalf("read sent_at: %v", err)
	}
	if !sentAt.Valid {
		t.Fatal("sent_at 为空，投递留痕没落到库上")
	}

	// 换一句话绕开查重关，撞的就必须是「本地日 1 条」这一门。
	second, err := svc.AdmitProactive(ctx, n5Candidate(userID, c, n5Message2))
	if err != nil {
		t.Fatalf("AdmitProactive 2: %v", err)
	}
	if second.Reason != domain.ReasonFrequencyCap ||
		second.Status != string(domain.ProactiveStatusBlocked) {
		t.Fatalf("第二条 = %s/%s，期望 blocked/frequency_cap（真历史没被读进来？）detail=%s",
			second.Status, second.Reason, second.Detail)
	}
	if !strings.Contains(second.Detail, "本地日内已发") {
		t.Errorf("结论没引用当日计数：%s", second.Detail)
	}

	// 终态守卫：同一行不能再置 sent，也不能被改写。
	if _, err := svc.RecheckProactive(ctx, userID, first.ID,
		&domain.Safety{Status: domain.SafetyOK}); !errors.Is(err, domain.ErrProactiveTerminal) {
		t.Errorf("重复 recheck 应报 ErrProactiveTerminal，实际 %v", err)
	}
	if err := NewCanonical(pool).InTx(ctx, func(ctx context.Context, st domain.Store) error {
		if _, err := st.MarkProactiveSent(ctx, userID, first.ID, "{}"); !errors.Is(err,
			domain.ErrProactiveTerminal) {
			t.Errorf("SQL 侧置 sent 应 0 行⇒ErrProactiveTerminal，实际 %v", err)
		}
		err := st.SetProactiveStatus(ctx, domain.ProactiveStatusRow{
			UserID: userID, ID: first.ID, Status: string(domain.ProactiveStatusCancelled),
			BlockReason: "manual"})
		if !errors.Is(err, domain.ErrProactiveTerminal) {
			t.Errorf("SQL 侧改写终态行应 0 行⇒ErrProactiveTerminal，实际 %v", err)
		}
		if err := st.ExpireProactive(ctx, userID, first.ID); !errors.Is(err,
			domain.ErrProactiveTerminal) {
			t.Errorf("终态行不得再被置 expired，实际 %v", err)
		}
		return nil
	}); err != nil {
		t.Fatalf("InTx: %v", err)
	}
	if status, _, _, _, _, _ := readProactiveRow(t, pool, userID, first.ID); status != "sent" {
		t.Errorf("守卫之后的 status = %s，期望仍是 sent", status)
	}

	// 历史行的 packet 必须真能被读出来（同承诺回访的计数依赖 ref_ids）。
	history, err := proactiveHistory(ctx, pool, userID, time.Now().UTC().AddDate(0, 0, -60))
	if err != nil {
		t.Fatalf("ListProactiveHistory: %v", err)
	}
	if len(history) < 2 {
		t.Fatalf("历史 %d 行，两条都应落在 60 天窗内", len(history))
	}
	seenSent, seenDetail := false, false
	for _, row := range history {
		if row.ID == first.ID && row.HasSentAt {
			seenSent = true
		}
		if row.ID == first.ID && strings.Contains(row.Detail, `"ref_ids"`) {
			seenDetail = true
		}
	}
	if !seenSent || !seenDetail {
		t.Errorf("历史读回 sent=%v detail=%v，频次关与承诺计数都依赖这两项", seenSent, seenDetail)
	}
}

func proactiveHistory(ctx context.Context, pool *pgxpool.Pool, userID string,
	since time.Time) ([]domain.ProactiveSendRow, error) {
	out := []domain.ProactiveSendRow{}
	err := NewCanonical(pool).InTx(ctx, func(ctx context.Context, st domain.Store) error {
		rows, err := st.ListProactiveHistory(ctx, userID, since)
		if err != nil {
			return err
		}
		out = append(out, rows...)
		return nil
	})
	return out, err
}

// 七表 UNION：引用行的当前状态与锚点时刻只能来自库里那一行。
func TestProactiveListRefStatesReadsSevenTables(t *testing.T) {
	pool := poolForTest(t)
	userID := "n5-refs-" + newID()[:8]
	defer func() { cleanupCanonical(t, pool, userID) }()
	plantNotificationUser(t, pool, userID, n5User{timezone: "UTC",
		pushFrequency: "daily", tier: "free", aiConsent: true, status: "active"})
	c := plantChain(t, canonicaldb.New(pool), userID)

	ids := []string{c.goalID, c.patternID, c.insightID, c.factID, c.commitmentID,
		c.outcomeID, c.proactiveID, "ghost-row"}
	wantType := map[string]domain.RefType{
		c.goalID: domain.RefGoal, c.patternID: domain.RefPattern,
		c.insightID: domain.RefInsight, c.factID: domain.RefFact,
		c.commitmentID: domain.RefCommitment, c.outcomeID: domain.RefOutcome,
		c.proactiveID: domain.RefProactive,
	}

	got := map[string]domain.RefState{}
	err := NewCanonical(pool).InTx(context.Background(),
		func(ctx context.Context, st domain.Store) error {
			rows, err := st.ListRefStates(ctx, userID, ids)
			if err != nil {
				return err
			}
			for _, row := range rows {
				got[row.ID] = row
			}
			return nil
		})
	if err != nil {
		t.Fatalf("ListRefStates: %v", err)
	}
	if len(got) != len(wantType) {
		t.Errorf("返回 %d 行，期望 %d 行；不存在的 ghost-row 不得凭空出现：%+v",
			len(got), len(wantType), got)
	}
	for id, kind := range wantType {
		row, ok := got[id]
		if !ok {
			t.Errorf("%s（%s）没被 UNION 取到", id, kind)
			continue
		}
		if row.RefType != kind {
			t.Errorf("%s 的 ref_type = %s，期望 %s", id, row.RefType, kind)
		}
		// 「关联当下」只认这些库里时刻：goal→target_at、承诺→due_at、
		// proactive→COALESCE(sent_at, scheduled_at, created_at)。
		if !row.HasTime || row.AnchorAt.IsZero() {
			t.Errorf("%s/%s 的 anchor_at 取不到，第③层就永远命中不了", kind, id)
		}
	}
	if _, ok := got["ghost-row"]; ok {
		t.Error("越权/不存在的 id 被返回了")
	}
	// 空 id 集合不发查询（Store 侧的短路，避免把整表扫一遍）。
	empty, err := refStates(context.Background(), pool, userID, nil)
	if err != nil || len(empty) != 0 {
		t.Errorf("空引用集合返回 %d 行 / %v", len(empty), err)
	}
}

func refStates(ctx context.Context, pool *pgxpool.Pool, userID string,
	ids []string) ([]domain.RefState, error) {
	out := []domain.RefState{}
	err := NewCanonical(pool).InTx(ctx, func(ctx context.Context, st domain.Store) error {
		rows, err := st.ListRefStates(ctx, userID, ids)
		if err != nil {
			return err
		}
		out = append(out, rows...)
		return nil
	})
	return out, err
}

// 偏好来自 legacy users 行的实际列：ai_consent=false 与注销用户都必须被挡（fail-closed）。
func TestProactivePrefsComeFromLegacyUsersRow(t *testing.T) {
	pool := poolForTest(t)
	ctx := context.Background()
	cases := []struct {
		name   string
		user   n5User
		wantSt string
	}{
		{name: "未授予 AI 权限", user: n5User{timezone: "UTC", pushFrequency: "daily",
			tier: "free", aiConsent: false, status: "active"}},
		{name: "非 active 用户查不到偏好行", user: n5User{timezone: "UTC",
			pushFrequency: "daily", tier: "free", aiConsent: true, status: "deleted"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			userID := "n5-pref-" + newID()[:8]
			defer func() { cleanupCanonical(t, pool, userID) }()
			plantNotificationUser(t, pool, userID, c.user)
			chain := plantChain(t, canonicaldb.New(pool), userID)
			res, err := serviceFor(pool).AdmitProactive(ctx,
				n5Candidate(userID, chain, n5Message))
			if err != nil {
				t.Fatalf("AdmitProactive: %v", err)
			}
			if res.Reason != domain.ReasonPreferenceOff {
				t.Errorf("reason = %s（%s），期望 preference_off", res.Reason, res.Detail)
			}
			if res.Status != string(domain.ProactiveStatusBlocked) {
				t.Errorf("status = %s，偏好关是「此刻不能发」⇒ blocked", res.Status)
			}
			_, _, _, block, _, _ := readProactiveRow(t, pool, userID, res.ID)
			if block != "preference_off" {
				t.Errorf("block_reason = %q", block)
			}
		})
	}
}

// 沉默阶梯的数据面：只数 role='user' 的消息；一条都没有时退到注册日。
func TestProactiveSilenceFallsBackToRegistrationWhenOnlyAssistantSpoke(t *testing.T) {
	pool := poolForTest(t)
	ctx := context.Background()
	userID := "n5-silent-" + newID()[:8]
	defer func() { cleanupCanonical(t, pool, userID) }()
	zone := sendableZone(t)
	plantNotificationUser(t, pool, userID, n5User{timezone: zone, pushFrequency: "daily",
		tier: "free", aiConsent: true, status: "active",
		createdAt: time.Now().UTC().AddDate(0, 0, -20)})
	plantChatMessage(t, pool, userID, "assistant", time.Now().UTC().Add(-time.Hour))
	chain := plantChain(t, canonicaldb.New(pool), userID)
	svc := serviceFor(pool)

	stopped, err := svc.AdmitProactive(ctx, n5Candidate(userID, chain, n5Message))
	if err != nil {
		t.Fatalf("AdmitProactive: %v", err)
	}
	if stopped.Reason != domain.ReasonFatigueStopped ||
		stopped.Cadence != domain.CadenceStopped {
		t.Fatalf("只有助手说过话 + 注册 20 天 ⇒ 应停推，实际 %s/%s（%s）",
			stopped.Status, stopped.Reason, stopped.Detail)
	}
	if packet := decodeStoredPacket(t, pool, userID, stopped.ID); packet.Cadence != "stopped" ||
		packet.SilentDays < 14 {
		t.Errorf("packet 阶梯 = %s/%d 天，结论要能复现", packet.Cadence, packet.SilentDays)
	}

	// 用户自己说了话 ⇒ 阶梯回 normal（同一份数据面，只多一行 chat_messages）。
	plantChatMessage(t, pool, userID, "user", time.Now().UTC().Add(-time.Minute))
	again, err := svc.AdmitProactive(ctx, n5Candidate(userID, chain, n5Message2))
	if err != nil {
		t.Fatalf("AdmitProactive 2: %v", err)
	}
	if again.Status != string(domain.ProactiveStatusEligible) {
		t.Errorf("用户刚说过话，实际 %s/%s（%s）", again.Status, again.Reason, again.Detail)
	}
	if packet := decodeStoredPacket(t, pool, userID, again.ID); packet.SilentDays != 0 {
		t.Errorf("silent_days = %d，期望 0（role='user' 那一行才算数）", packet.SilentDays)
	}
}

func decodeStoredPacket(t *testing.T, pool *pgxpool.Pool,
	userID, id string) domain.EligibilityPacket {
	t.Helper()
	_, detail, _, _, _, _ := readProactiveRow(t, pool, userID, id)
	packet := domain.EligibilityPacket{}
	if err := json.Unmarshal([]byte(detail), &packet); err != nil {
		t.Fatalf("stored packet 不可解析：%v / %s", err, detail)
	}
	return packet
}

// 用户近期纠正过被引用的那一行 ⇒ 队列里的触达作废（04:231），落成 cancelled。
func TestProactiveUserCorrectionCancelsQueuedSend(t *testing.T) {
	pool := poolForTest(t)
	ctx := context.Background()
	userID := "n5-correct-" + newID()[:8]
	defer func() { cleanupCanonical(t, pool, userID) }()
	plantNotificationUser(t, pool, userID, n5User{timezone: sendableZone(t),
		pushFrequency: "daily", tier: "free", aiConsent: true, status: "active"})
	chain := plantChain(t, canonicaldb.New(pool), userID)
	svc := serviceFor(pool)

	// 先证明「没有纠正时它是绿的」，否则这条用例可能被别的原因挡下而假通过。
	clean, err := svc.AdmitProactive(ctx, n5Candidate(userID, chain, n5Message))
	if err != nil {
		t.Fatalf("基线 Admit: %v", err)
	}
	if clean.Status != string(domain.ProactiveStatusEligible) {
		t.Fatalf("基线应 eligible，实际 %s/%s（%s）", clean.Status, clean.Reason, clean.Detail)
	}

	if err := canonicaldb.New(pool).InsertUserFeedback(ctx,
		canonicaldb.InsertUserFeedbackParams{
			ID: newID(), UserID: userID, FeedbackType: "reject",
			TargetType: "goal", TargetID: chain.goalID,
			CorrectionText: pgtype.Text{}, MetadataJson: "{}",
		}); err != nil {
		t.Fatalf("InsertUserFeedback: %v", err)
	}

	// 生成时刻的新候选直接被 Freshness 关毙掉。
	cancelled, err := svc.AdmitProactive(ctx, n5Candidate(userID, chain, n5Message2))
	if err != nil {
		t.Fatalf("AdmitProactive 2: %v", err)
	}
	if cancelled.Status != string(domain.ProactiveStatusCancelled) ||
		cancelled.Reason != domain.ReasonStaleTrigger {
		t.Errorf("纠正后的候选 = %s/%s，期望 cancelled/stale_trigger",
			cancelled.Status, cancelled.Reason)
	}
	if _, _, reject, _, _, _ := readProactiveRow(t, pool, userID, cancelled.ID); reject !=
		"stale_trigger" {
		t.Errorf("reject_reason = %q，取消是「本身没资格」不是「此刻不能发」", reject)
	}

	// 已在队列里的那条（eligible）到点也必须被拦下：重评现读库态，不沿用上次结论。
	recheck, err := svc.RecheckProactive(ctx, userID, clean.ID, &domain.Safety{Status: domain.SafetyOK})
	if err != nil {
		t.Fatalf("RecheckProactive: %v", err)
	}
	if recheck.Status != string(domain.ProactiveStatusCancelled) {
		t.Errorf("重评 status = %s（%s），期望 cancelled", recheck.Status, recheck.Detail)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM proactives WHERE user_id = $1 AND sent_at IS NOT NULL`,
		userID); n != 0 {
		t.Errorf("有 %d 条被sent：用户纠正过的行绝不能事后补发", n)
	}
}
