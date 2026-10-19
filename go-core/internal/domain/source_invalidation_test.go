package domain

import (
	"context"
	"errors"
	"strings"
	"testing"
)

// T2-06 单测：一次「来源行被删」怎么变成 canonical 账面。
//
// 钉七条口径（前六条在这里，第七条「单向翻转的 SQL 谓词真的单向」只有真库给得出，
// 在 internal/store/canonical_source_deleted_integration_test.go）：
//  1. 指针三件缺一件都不许碰库；
//  2. 表不在准入面 ⇒ **复用 T2-03 的哨兵**，inbound 才会钉成永久拒绝；
//  3. 「没登记过」是结论不是错误（否则每次正常删除都上重试阶梯）；
//  4. 翻转与传播分两笔，传播读到的必须是**已提交**的 deleted（次序错了 live_support 就还算它）；
//  5. 已经翻过的行不再传播（重放只变一次）；
//  6. 传播中途失败 ⇒ 状态收回，让这条投递回到「还没翻过」；
//  7. 收回也失败 ⇒ 两个错误一起带出去，Detail 明说库里停在 deleted。

func invalidateStore() (*fakeStore, *Service) {
	f := newFakeStore()
	f.sourceRecord = SourceRecordState{ID: "src-1", Status: "active", Found: true}
	s, _ := newTestService(f)
	return f, s
}

func TestInvalidateSource_RejectsEmptyPointerBeforeTouchingStore(t *testing.T) {
	for _, p := range []SourcePointer{
		{UserID: "", SourceTable: SourceTableMemories, SourceID: "m-1"},
		{UserID: owner, SourceTable: "", SourceID: "m-1"},
		{UserID: owner, SourceTable: SourceTableMemories, SourceID: ""},
	} {
		f, s := invalidateStore()
		_, err := s.InvalidateSource(context.Background(), p)
		if err == nil {
			t.Fatalf("空指针 %v 竟然放行", p)
		}
		if f.deletedMarks != 0 || len(f.sourceLookups) != 0 {
			t.Errorf("空指针仍读了库：%+v", f)
		}
	}
}

func TestInvalidateSource_NonAdmissibleTableUsesTheT203Sentinel(t *testing.T) {
	f, s := invalidateStore()
	_, err := s.InvalidateSource(context.Background(), SourcePointer{
		UserID: owner, SourceTable: "reflections", SourceID: "r-1"})
	if !errors.Is(err, ErrUnsupportedSourceTable) {
		t.Fatalf("err = %v，必须复用 ErrUnsupportedSourceTable（inbound 的 classify 只认哨兵）", err)
	}
	if f.deletedMarks != 0 {
		t.Error("表没开却翻了登记行")
	}
}

func TestInvalidateSource_NoRegistrationRowIsANoOp(t *testing.T) {
	f, s := invalidateStore()
	f.sourceRecord = SourceRecordState{Found: false}
	f.evidenceForSource = []string{"e1"}
	out, err := s.InvalidateSource(context.Background(), SourcePointer{
		UserID: owner, SourceTable: SourceTableMemories, SourceID: "m-1"})
	if err != nil {
		t.Fatalf("没有登记行不是错误：%v", err)
	}
	if out.Marked || out.EvidenceCount != 0 || f.deletedMarks != 0 || f.modelVer != 0 {
		t.Errorf("no-op 不干净：%+v marks=%d ver=%d", out, f.deletedMarks, f.modelVer)
	}
	if !strings.Contains(out.Detail, "没有这条登记行") {
		t.Errorf("Detail 没说实话：%q", out.Detail)
	}
}

func TestInvalidateSource_FlipsOnceAndPropagates(t *testing.T) {
	f, s := invalidateStore()
	f.evidenceForSource = []string{"e1"}
	f.derived = []DerivedSupport{
		{Kind: KindFact, ID: "fact-1", Status: "active", TotalSupport: 1, LiveSupport: 0},
		{Kind: KindPattern, ID: "pat-1", Status: string(PatternSupported),
			TotalSupport: 2, LiveSupport: 0},
		{Kind: KindInsight, ID: "ins-2", Status: "published", TotalSupport: 3, LiveSupport: 2},
	}
	f.patternSt = &PatternState{}
	*f.patternSt = patternGreen()

	out, err := s.InvalidateSource(context.Background(), SourcePointer{
		UserID: owner, SourceTable: SourceTableMemories, SourceID: "m-1"})
	if err != nil {
		t.Fatalf("InvalidateSource: %v", err)
	}
	if !out.Marked || out.SourceRecordID != "src-1" || out.EvidenceCount != 1 {
		t.Errorf("翻转面 = %+v", out)
	}
	if f.sourceRecord.Status != "deleted" || f.deletedMarks != 1 {
		t.Errorf("登记行 = %+v marks=%d", f.sourceRecord, f.deletedMarks)
	}
	if out.Invalidated != 2 || out.Monitored != 1 || out.RevalidationRequested != 0 {
		t.Errorf("传播计数 = %+v", out)
	}
	if len(f.factWrites) != 1 || f.factWrites[0].Status != "invalidated" {
		t.Errorf("fact 失效写行 = %+v", f.factWrites)
	}
	if f.modelVer != 1 {
		t.Errorf("版本 = %d，一次删除传播应只 ++ 一次", f.modelVer)
	}
}

func TestInvalidateSource_AlreadyDeletedDoesNotPropagateAgain(t *testing.T) {
	f, s := invalidateStore()
	f.sourceRecord.Status = "deleted"
	f.evidenceForSource = []string{"e1"}
	f.derived = []DerivedSupport{
		{Kind: KindFact, ID: "fact-1", Status: "active", TotalSupport: 1, LiveSupport: 0},
	}

	out, err := s.InvalidateSource(context.Background(), SourcePointer{
		UserID: owner, SourceTable: SourceTableMemories, SourceID: "m-1"})
	if err != nil {
		t.Fatalf("第二次投递不该报错：%v", err)
	}
	if out.Marked || out.Invalidated != 0 || f.deletedMarks != 0 {
		t.Errorf("第二次投递又传播了一遍：%+v marks=%d", out, f.deletedMarks)
	}
	if len(f.factWrites) != 0 || f.modelVer != 0 {
		t.Errorf("账面第二次就变了：%d 写行 / 版本 %d", len(f.factWrites), f.modelVer)
	}
	if !strings.Contains(out.Detail, "单向翻转闸") {
		t.Errorf("Detail 没指回口径：%q", out.Detail)
	}
}

func TestInvalidateSource_PropagationFailureRestoresTheFlip(t *testing.T) {
	f, s := invalidateStore()
	f.evidenceForSource = []string{"e1", "e2"}
	f.derivedBy = map[string][]DerivedSupport{
		"e1": {{Kind: KindFact, ID: "fact-1", Status: "active", TotalSupport: 1, LiveSupport: 0}},
	}
	f.failPropagateFor = "e2"

	out, err := s.InvalidateSource(context.Background(), SourcePointer{
		UserID: owner, SourceTable: SourceTableMemories, SourceID: "m-1"})
	if err == nil {
		t.Fatal("第二条传播失败却被当成成功")
	}
	if !strings.Contains(err.Error(), "e2") {
		t.Errorf("错误里认不出是哪一条：%v", err)
	}
	if !out.RolledBack || f.sourceRecord.Status != "active" || f.restores != 1 {
		t.Errorf("状态没收回：%+v 行=%+v restores=%d", out, f.sourceRecord, f.restores)
	}
	if !strings.Contains(out.Detail, "恢复") {
		t.Errorf("Detail 没说实话：%q", out.Detail)
	}
}

func TestInvalidateSource_RestoreFailureReportsBothErrors(t *testing.T) {
	f, s := invalidateStore()
	f.evidenceForSource = []string{"e1"}
	f.failPropagateFor = "e1"
	f.restoreErr = errors.New("fake: 恢复也写不进去")

	out, err := s.InvalidateSource(context.Background(), SourcePointer{
		UserID: owner, SourceTable: SourceTableMemories, SourceID: "m-1"})
	if err == nil {
		t.Fatal("传播与恢复双双失败却回 nil")
	}
	if !strings.Contains(err.Error(), "传播 evidence e1") ||
		!strings.Contains(err.Error(), "恢复登记行状态也失败") {
		t.Errorf("两个错误少了一个：%v", err)
	}
	if out.RolledBack {
		t.Error("恢复失败却把 RolledBack 记成 true")
	}
	if !strings.Contains(err.Error(), "库里停在 deleted") {
		t.Error("值班读不出「现在库里是什么形态」")
	}
}
