// 18 §8 的原子性：候选行、inbox 行与「下一段」命令行必须是**同一笔 COMMIT**。
//
// 旧形状（三个入口自开事务）在这里会露馅：失败分支里候选行已经提交，而 inbox 行
// 随外层回滚消失——重投会把同一条候选再放一遍，命令那条链也补不回来。
// 单测只能证明「入口拿到的是同一笔 Store」（`domain/inbox_shared_tx_test.go`），
// 真库这一条证明「回滚确实带走了候选行」。
//
// 未设 TEST_DATABASE_URL 时跳过，与其余 store 集成测同口径（缺 env 是「未跑」不是绿）。
package store

import (
	"context"
	"errors"
	"testing"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// candidateConsumer 是 `inbound.Consumer` 的字面值。store 包不 import 入站包
// （依赖方向是 messaging → domain/store，反向会把传输面拖进仓储）；本文件只判事务形状，
// 幂等名的契约面由 inbound 包自己的闸守。
const candidateConsumer = "go.core.candidate"

func TestInboxAndCandidateCommitAtomically(t *testing.T) {
	pool := poolForTest(t)
	const userID = "r5b-shared-tx"
	defer cleanupCanonical(t, pool, userID)
	consumer := candidateConsumer + "-" + userID
	t.Cleanup(func() {
		if _, err := pool.Exec(context.Background(),
			`DELETE FROM inbox_messages WHERE consumer = $1`, consumer); err != nil {
			t.Errorf("cleanup inbox_messages: %v", err)
		}
	})

	p := plantUser(t, pool, userID)
	svc := serviceFor(pool)
	ctx := context.Background()

	// ── 成功分支：候选 + 事件投递行 + 命令行一起提交 ──────────────────────
	okMsg := domain.InboundMessage{MessageID: "msg-shared-ok",
		MessageType: "candidate_fact", Consumer: consumer}
	okCandidate := baseCandidate(p, domain.KindFact)
	okCandidate.Origin = "direct"
	okCommandID := domain.DeriveTaskOperationID(domain.AITaskPattern, okMsg.MessageID)
	okBody, err := domain.TaskRequestBody(domain.AITaskPattern, domain.KindFact,
		okMsg.MessageID, "", "")
	if err != nil {
		t.Fatalf("TaskRequestBody: %v", err)
	}
	res, err := svc.ConsumeIdempotent(ctx, okMsg, func(ctx context.Context, st domain.Store) error {
		if _, err := svc.AdmitWithStore(ctx, st, okCandidate); err != nil {
			return err
		}
		return svc.RequestAITask(ctx, st, domain.AITaskRequest{
			Kind: domain.AITaskPattern, UserID: userID,
			OperationID: okCommandID, Payload: okBody,
		})
	})
	if err != nil {
		t.Fatalf("成功分支：%v", err)
	}
	if res.Status != domain.ConsumeProcessed || !res.Ack {
		t.Fatalf("结论=%s ack=%v，期望 processed/true", res.Status, res.Ack)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_facts WHERE user_id = $1 AND predicate = $2`,
		userID, okCandidate.Predicate); n != 1 {
		t.Fatalf("候选行 %d 条，期望 1", n)
	}
	var inboxState string
	if err := pool.QueryRow(ctx,
		`SELECT status FROM inbox_messages WHERE message_id = $1 AND consumer = $2`,
		okMsg.MessageID, consumer).Scan(&inboxState); err != nil {
		t.Fatalf("inbox 行没落库：%v", err)
	}
	if inboxState != "processed" {
		t.Errorf("inbox 状态=%q，期望 processed", inboxState)
	}
	// 两条投递行：领域事件 fact.created 与命令 ai.pattern.requested。
	// 命令那条是 D-G14-② 的链，候选行与它在同一笔提交才说明接线正确。
	if n := countWhere(t, pool,
		`SELECT count(*) FROM outbox_messages WHERE user_id = $1`, userID); n != 2 {
		t.Fatalf("投递行 %d 条，期望 2（事件 + 命令）", n)
	}
	var commandRouting string
	if err := pool.QueryRow(ctx,
		`SELECT routing_key FROM outbox_messages WHERE user_id = $1 AND message_id = $2`,
		userID, okCommandID).Scan(&commandRouting); err != nil {
		t.Fatalf("命令行没落库：%v", err)
	}
	if commandRouting != domain.AITaskPattern.RoutingKey() {
		t.Errorf("命令 routing key=%q，期望 %q", commandRouting, domain.AITaskPattern.RoutingKey())
	}

	// ── 失败分支：回调在候选与命令都写过之后报错，三样必须一起消失 ──────
	failMsg := domain.InboundMessage{MessageID: "msg-shared-fail",
		MessageType: "candidate_fact", Consumer: consumer}
	failCandidate := baseCandidate(p, domain.KindFact)
	failCandidate.Origin = "direct"
	failCandidate.SubjectKey = "user"
	failCandidate.Predicate = "shared_tx_failure_probe"
	failCommandID := domain.DeriveTaskOperationID(domain.AITaskPattern, failMsg.MessageID)
	boom := errors.New("store test: 命令入队之后的失败")
	failRes, failErr := svc.ConsumeIdempotent(ctx, failMsg,
		func(ctx context.Context, st domain.Store) error {
			if _, err := svc.AdmitWithStore(ctx, st, failCandidate); err != nil {
				return err
			}
			if err := svc.RequestAITask(ctx, st, domain.AITaskRequest{
				Kind: domain.AITaskPattern, UserID: userID,
				OperationID: failCommandID, Payload: okBody,
			}); err != nil {
				return err
			}
			return boom
		})
	if !errors.Is(failErr, boom) {
		t.Fatalf("失败分支的错误没外传：%v", failErr)
	}
	if failRes.Ack || failRes.Status != domain.ConsumeTransient {
		t.Fatalf("失败分支结论=%s ack=%v，期望 transient/false", failRes.Status, failRes.Ack)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_facts WHERE user_id = $1 AND predicate = $2`,
		userID, failCandidate.Predicate); n != 0 {
		t.Errorf("回滚后仍留下候选行 %d 条 ⇒ 候选写入不在 inbox 那笔事务里", n)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM inbox_messages WHERE message_id = $1 AND consumer = $2`,
		failMsg.MessageID, consumer); n != 0 {
		t.Errorf("回滚后仍留下 inbox 行 %d 条", n)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM outbox_messages WHERE user_id = $1 AND message_id = $2`,
		userID, failCommandID); n != 0 {
		t.Errorf("回滚后仍留下命令行 %d 条", n)
	}
	// 成功分支那两行不受影响，且模型版本没被失败分支的 ++ 带脏。
	if n := countWhere(t, pool,
		`SELECT count(*) FROM outbox_messages WHERE user_id = $1`, userID); n != 2 {
		t.Errorf("投递行 %d 条，期望仍为成功分支那 2 条", n)
	}
	var version int64
	if err := pool.QueryRow(ctx,
		`SELECT current_version FROM personal_models WHERE user_id = $1`, userID).
		Scan(&version); err != nil {
		t.Fatalf("读模型版本：%v", err)
	}
	if version != 1 {
		t.Errorf("current_version=%d，期望 1（失败分支的 ++ 必须随回滚消失）", version)
	}
}
