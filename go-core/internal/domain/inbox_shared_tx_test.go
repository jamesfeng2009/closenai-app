// 18 §8 的单事务接线：入站回调拿到的 Store 必须一路传到三个候选入口。
//
// 判据落在 fakeStore.InTx 的**嵌套守卫**上：`ConsumeIdempotent` 已经开了一笔，
// 入口若退回自开事务的 `Admit` 一族，第二层 InTx 当场报错 → 用例红。
// 真库上的「候选行与 inbox 行同生同死」由 `internal/store` 的
// `TestInboxAndCandidateCommitAtomically` 证；这里守的是接线，不是事务实现。
package domain

import (
	"context"
	"errors"
	"testing"
)

func consumeMsg(id string) InboundMessage {
	return InboundMessage{MessageID: id, MessageType: "candidate_fact",
		Consumer: "go.core.candidate"}
}

func TestAdmitWithStoreRunsInsideTheInboxTransaction(t *testing.T) {
	f := newFakeStore()
	s, _ := newTestService(f)
	res, err := s.ConsumeIdempotent(context.Background(), consumeMsg("m-fact-shared"),
		func(ctx context.Context, st Store) error {
			_, err := s.AdmitWithStore(ctx, st, okFact())
			return err
		})
	if err != nil {
		t.Fatalf("AdmitWithStore 在 inbox 事务里报错（自开第二笔会撞 fake 的嵌套守卫）：%v", err)
	}
	if res.Status != ConsumeProcessed || !res.Ack {
		t.Fatalf("结论=%s ack=%v，期望 processed/true", res.Status, res.Ack)
	}
	if f.rolledBack {
		t.Error("fake 标记了回滚：候选写入没有与 inbox 行走同一笔")
	}
	if len(f.facts) != 1 || len(f.processed) != 1 {
		t.Errorf("facts=%d processed=%d，期望各 1", len(f.facts), len(f.processed))
	}
}

func TestAdmitEvidenceWithStoreRunsInsideTheInboxTransaction(t *testing.T) {
	f := newFakeStore()
	s, _ := newTestService(f)
	res, err := s.ConsumeIdempotent(context.Background(), consumeMsg("m-evidence-shared"),
		func(ctx context.Context, st Store) error {
			_, err := s.AdmitEvidenceWithStore(ctx, st, okEvidenceCandidate())
			return err
		})
	if err != nil {
		t.Fatalf("AdmitEvidenceWithStore 在 inbox 事务里报错：%v", err)
	}
	if res.Status != ConsumeProcessed || !res.Ack {
		t.Fatalf("结论=%s ack=%v，期望 processed/true", res.Status, res.Ack)
	}
	if len(f.written) != 1 || len(f.processed) != 1 {
		t.Errorf("evidence=%d processed=%d，期望各 1", len(f.written), len(f.processed))
	}
}

func TestAdmitProactiveWithStoreRunsInsideTheInboxTransaction(t *testing.T) {
	f, s := proactiveGreenStore()
	res, err := s.ConsumeIdempotent(context.Background(), consumeMsg("m-proactive-shared"),
		func(ctx context.Context, st Store) error {
			_, err := s.AdmitProactiveWithStore(ctx, st, proacCandidate())
			return err
		})
	if err != nil {
		t.Fatalf("AdmitProactiveWithStore 在 inbox 事务里报错：%v", err)
	}
	if res.Status != ConsumeProcessed || !res.Ack {
		t.Fatalf("结论=%s ack=%v，期望 processed/true", res.Status, res.Ack)
	}
	if len(f.proactives) != 1 || len(f.processed) != 1 {
		t.Errorf("proactives=%d processed=%d，期望各 1", len(f.proactives), len(f.processed))
	}
}

// 失败分支：入口跑过之后回调报错，inbox 行必须随整笔消失（fake 的投递面按真库语义回滚）。
// 这一格防的是「把领域入口挪到 ConsumeIdempotent 之外」那种写法——那样 inbox 回了、
// 候选已经提交，重投就会把同一条候选再放一遍。
func TestInboxRollbackDiscardsTheInboxRowAfterEntryRan(t *testing.T) {
	f := newFakeStore()
	s, _ := newTestService(f)
	boom := errors.New("fake: 命令入队失败")
	res, err := s.ConsumeIdempotent(context.Background(), consumeMsg("m-rollback-shared"),
		func(ctx context.Context, st Store) error {
			if _, err := s.AdmitWithStore(ctx, st, okFact()); err != nil {
				return err
			}
			return boom
		})
	if !errors.Is(err, boom) {
		t.Fatalf("错误没外传：%v", err)
	}
	if res.Ack || res.Status != ConsumeTransient {
		t.Fatalf("结论=%s ack=%v，期望 transient/false", res.Status, res.Ack)
	}
	if !f.rolledBack {
		t.Error("回调失败却没有回滚标记")
	}
	if len(f.processed) != 0 || len(f.inbox) != 0 {
		t.Errorf("processed=%d inbox=%d，整笔回滚后不该留下幂等行",
			len(f.processed), len(f.inbox))
	}
}
