// N4 Service 单测：证据提案的确定性算分（03 §4-§7）与 insight 重算（§17）。
//
// 断言的重心是分层不变量在代码里成立：
//  1. 落库的 strength **只能是 Go 算出来的**，提案里没有这个字段可填；
//  2. 独立性计数由核验得出，调用方报几条不重要，越权引用直接报错；
//  3. 重算不得覆盖用户已表态的行，也不得在没跌破门槛时动模型版本；
//  4. 来源指针的两格（表名白名单＋归属回查）都排在写入之前（T2-03）。
package domain

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"
)

var observedDay = time.Date(2026, 10, 10, 9, 0, 0, 0, time.UTC)

func okEvidenceCandidate() EvidenceCandidate {
	return EvidenceCandidate{
		// ⚠️ SourceTable 取准入面里那一格（AdmissibleSourceTables）：T2-03 起
		// 没开的表在 checkEvidenceCandidate 就被挡下，夹具写成别的表名只会让
		// 下面每一条「能正常入账」的用例一起变红。
		UserID: owner, SourceKind: SourceAIExtraction,
		SourceTable: SourceTableMemories, SourceID: "entry-1",
		OccurredAt:   observedDay,
		EvidenceType: "direct", Role: "supporting",
		Observation: "23:41 记录", Explicitness: Explicit, Specificity: Specific,
		GenerationRunID: "run-1",
	}
}

func admitEvidence(t *testing.T, s *Service, c EvidenceCandidate) (EvidenceResult, error) {
	t.Helper()
	return s.AdmitEvidence(context.Background(), c)
}

func TestService_AdmitEvidence_StrengthIsComputedHere(t *testing.T) {
	store := newFakeStore()
	s, f := newTestService(store)
	res, err := admitEvidence(t, s, okEvidenceCandidate())
	if err != nil {
		t.Fatalf("AdmitEvidence: %v", err)
	}
	if len(f.written) != 1 {
		t.Fatalf("evidence 行数=%d", len(f.written))
	}
	row := f.written[0]
	if !row.HasStrength {
		t.Fatal("落库证据必须带 strength：03:108 要求终局分由 domain code 算")
	}
	want, err := ScoreEvidence(EvidenceSignals{
		SourceKind: SourceAIExtraction, Explicitness: Explicit, Specificity: Specific,
		Independent: 1, ObservedAt: observedDay,
	}, DefaultPolicy().Evidence, windowEnd)
	if err != nil {
		t.Fatalf("ScoreEvidence: %v", err)
	}
	if row.Strength != want.Strength {
		t.Errorf("strength=%.4f，期望算分值 %.4f", row.Strength, want.Strength)
	}
	if res.Class != want.Class {
		t.Errorf("class=%s，期望 %s", res.Class, want.Class)
	}
	if row.Role != "supporting" || row.EvidenceType != "direct" {
		t.Errorf("role/type=%q/%q", row.Role, row.EvidenceType)
	}
	if row.GenerationRunID != "run-1" {
		t.Errorf("事件必须带留痕号（不变量 8），got %q", row.GenerationRunID)
	}
	if len(f.sources) != 1 || f.sources[0].SourceTable != SourceTableMemories ||
		f.sources[0].SourceType != string(SourceAIExtraction) {
		t.Errorf("source 指针=%+v", f.sources)
	}
	if !f.sources[0].HasOccurredAt || !f.sources[0].OccurredAt.Equal(observedDay) {
		t.Errorf("source.occurred_at 丢了：%+v", f.sources[0])
	}
	if row.SourceRecordID != "src-memories/entry-1" {
		t.Errorf("证据没挂到登记的 source 行：%q", row.SourceRecordID)
	}
	if len(f.sourceLookups) != 1 || f.sourceLookups[0] != "memories|entry-1" {
		t.Errorf("来源回查=%v，期望恰好一次且指向提案里那一行", f.sourceLookups)
	}
}

// T2-03：来源指针的两格判据（表名白名单＋归属回查）都在写入之前，而且
// 「表没开／行不存在／行是别人的」各用各的哨兵——inbound 的 classify 按这三个
// 分「永久拒绝留痕」与「瞬时重试」，判错方向就是让毒消息在 30s/2m/10m 阶梯上撞库。
func TestService_AdmitEvidence_SourcePointerIsChecked(t *testing.T) {
	const otherUser = "22222222-2222-4222-8222-222222222222"

	t.Run("表不在准入面：不回查也不写", func(t *testing.T) {
		store := newFakeStore()
		s, f := newTestService(store)
		c := okEvidenceCandidate()
		c.SourceTable = "journal_entries"
		_, err := admitEvidence(t, s, c)
		if !errors.Is(err, ErrUnsupportedSourceTable) {
			t.Fatalf("err=%v，期望 ErrUnsupportedSourceTable", err)
		}
		if len(f.sourceLookups) != 0 {
			t.Errorf("没开的表却去回查了库：%v（纯判定必须排在读库之前，否则 reason 指错方向）",
				f.sourceLookups)
		}
		if len(f.sources) != 0 || len(f.written) != 0 {
			t.Errorf("挡住之前不许有任何写入：%+v %+v", f.sources, f.written)
		}
	})

	t.Run("来源行查不到：ErrUnknownSourceRow", func(t *testing.T) {
		store := newFakeStore()
		store.sourceOwner = ""
		s, f := newTestService(store)
		if _, err := admitEvidence(t, s, okEvidenceCandidate()); !errors.Is(err,
			ErrUnknownSourceRow) {
			t.Fatalf("err=%v，期望 ErrUnknownSourceRow", err)
		}
		if len(f.sources) != 0 || len(f.written) != 0 {
			t.Errorf("查不到却写了库：%+v %+v", f.sources, f.written)
		}
	})

	t.Run("来源行是别人的：ErrForeignSource 且不留半状态", func(t *testing.T) {
		store := newFakeStore()
		store.sourceOwner = otherUser
		s, f := newTestService(store)
		_, err := admitEvidence(t, s, okEvidenceCandidate())
		if !errors.Is(err, ErrForeignSource) {
			t.Fatalf("err=%v，期望 ErrForeignSource（不变量 9：拒绝跨用户 id 引用）", err)
		}
		if strings.Contains(err.Error(), otherUser) {
			t.Errorf("拒绝理由里回显了真正主人的标识符：%v", err)
		}
		if len(f.sources) != 0 || len(f.written) != 0 {
			t.Errorf("越权提案却写了库：%+v %+v", f.sources, f.written)
		}
		if !f.rolledBack {
			t.Error("归属核验在事务里失败必须回滚")
		}
	})

	t.Run("回查本身报错时不许当成「行不存在」", func(t *testing.T) {
		store := newFakeStore()
		boom := errors.New("fake: 归属回查故障")
		store.sourceErr = boom
		s, f := newTestService(store)
		_, err := admitEvidence(t, s, okEvidenceCandidate())
		if !errors.Is(err, boom) {
			t.Fatalf("err=%v，期望原样上抛（一次抖动不能记成永久拒绝）", err)
		}
		for _, sentinel := range []error{ErrForeignSource, ErrUnknownSourceRow,
			ErrUnsupportedSourceTable} {
			if errors.Is(err, sentinel) {
				t.Errorf("库故障被读成了 %v，那会让它跳过重试阶梯直接进 DLQ", sentinel)
			}
		}
		if len(f.sources) != 0 {
			t.Errorf("故障路径不留登记：%+v", f.sources)
		}
	})
}

func TestService_AdmitEvidence_MetadataCarriesTheDerivation(t *testing.T) {
	store := newFakeStore()
	s, f := newTestService(store)
	c := okEvidenceCandidate()
	c.Metadata = json.RawMessage(`{"llm_confidence":0.42,"span":"23:41"}`)
	if _, err := admitEvidence(t, s, c); err != nil {
		t.Fatalf("AdmitEvidence: %v", err)
	}
	var box struct {
		Caller struct {
			LLMConfidence float64 `json:"llm_confidence"`
			Span          string  `json:"span"`
		} `json:"caller"`
		Strength    float64            `json:"strength"`
		Class       EvidenceClass      `json:"class"`
		Independent int                `json:"independent_observations"`
		Dropped     int                `json:"proposed_but_not_counted"`
		Components  map[string]float64 `json:"components"`
		Detail      string             `json:"detail"`
	}
	if err := json.Unmarshal([]byte(f.written[0].Metadata), &box); err != nil {
		t.Fatalf("metadata_json 不是合法 JSON: %v（%q）", err, f.written[0].Metadata)
	}
	if len(box.Components) != 5 {
		t.Errorf("components=%v，五因子取值必须逐个留痕", box.Components)
	}
	if box.Caller.LLMConfidence != 0.42 || box.Caller.Span != "23:41" {
		t.Errorf("调用方原文必须原样保留，got %+v", box.Caller)
	}
	if box.Strength != f.written[0].Strength {
		t.Errorf("metadata.strength=%.4f 与列值 %.4f 不一致", box.Strength, f.written[0].Strength)
	}
	if !strings.Contains(box.Detail, "mode=geomean") {
		t.Errorf("detail=%q，口径要写在同行，换 env 标定后老行仍可复现", box.Detail)
	}
}

func TestService_AdmitEvidence_IndependenceIsVerifiedNotProposed(t *testing.T) {
	earlier := time.Date(2026, 9, 28, 8, 0, 0, 0, time.UTC)
	t.Run("提案给了 1 条、核验通过则计 2", func(t *testing.T) {
		store := newFakeStore()
		store.evidence["e1"] = Evidence{ID: "e1", UserID: owner, SourceID: "s1",
			Role: "supporting", ObservedAt: earlier, Strength: 0.8, HasStrength: true}
		s, f := newTestService(store)
		c := okEvidenceCandidate()
		c.ProposedIndependentIDs = []string{"e1"}
		res, err := admitEvidence(t, s, c)
		if err != nil {
			t.Fatalf("AdmitEvidence: %v", err)
		}
		if res.Independent != 2 {
			t.Errorf("independent=%d，期望本条+1 条已核实", res.Independent)
		}
		if !strings.Contains(res.Detail, "counted_independent=2") {
			t.Errorf("detail=%q", res.Detail)
		}
		if f.written[0].Strength <= 0.8740 {
			t.Errorf("两条独立观测的 strength=%.4f 应高于单条 0.8740（§5 Repetition）",
				f.written[0].Strength)
		}
	})
	t.Run("同源或同日的提案引用只丢弃，不拒整条", func(t *testing.T) {
		store := newFakeStore()
		// 同一 source_record 的另一条；同一天另一条。
		store.evidence["e1"] = Evidence{ID: "e1", UserID: owner, SourceID: "src-memories/entry-1",
			Role: "supporting", ObservedAt: earlier}
		store.evidence["e2"] = Evidence{ID: "e2", UserID: owner, SourceID: "s2",
			Role: "supporting", ObservedAt: time.Date(2026, 10, 10, 23, 0, 0, 0, time.UTC)}
		s, f := newTestService(store)
		c := okEvidenceCandidate()
		c.ProposedIndependentIDs = []string{"e1", "e2"}
		res, err := admitEvidence(t, s, c)
		if err != nil {
			t.Fatalf("AdmitEvidence: %v", err)
		}
		if res.Independent != 1 {
			t.Errorf("independent=%d，同源/同日不得计独立重复（03 §6:161-163）", res.Independent)
		}
		if !strings.Contains(res.Detail, "proposed_independent=2 counted_independent=1") {
			t.Errorf("detail=%q，丢弃的引用要留痕", res.Detail)
		}
		if len(f.written) != 1 {
			t.Errorf("证据行仍要写入，got %d", len(f.written))
		}
	})
	t.Run("晚于本条的引用不计入", func(t *testing.T) {
		store := newFakeStore()
		store.evidence["e1"] = Evidence{ID: "e1", UserID: owner, SourceID: "s1",
			Role: "supporting", ObservedAt: time.Date(2026, 10, 12, 19, 0, 0, 0, time.UTC)}
		s, _ := newTestService(store)
		c := okEvidenceCandidate()
		c.OccurredAt = observedDay
		c.ProposedIndependentIDs = []string{"e1"}
		res, err := admitEvidence(t, s, c)
		if err != nil {
			t.Fatalf("AdmitEvidence: %v", err)
		}
		if res.Independent != 1 {
			t.Errorf("independent=%d，未来的行不能支撑过去的重复", res.Independent)
		}
	})
	t.Run("越权引用别人的证据直接报错", func(t *testing.T) {
		store := newFakeStore()
		store.evidence["e1"] = Evidence{ID: "e1", UserID: "other-user", SourceID: "s1",
			Role: "supporting", ObservedAt: earlier}
		s, f := newTestService(store)
		c := okEvidenceCandidate()
		c.ProposedIndependentIDs = []string{"e1"}
		if _, err := admitEvidence(t, s, c); err == nil {
			t.Fatal("期望报错（不变量 9：拒绝跨用户 id 引用）")
		}
		if len(f.written) != 0 || len(f.sources) != 0 {
			t.Errorf("报错后不得留半状态：%+v %+v", f.written, f.sources)
		}
		if !f.rolledBack {
			t.Errorf("报错必须回滚事务")
		}
	})
	t.Run("引用不存在的 id 也报错", func(t *testing.T) {
		store := newFakeStore()
		s, _ := newTestService(store)
		c := okEvidenceCandidate()
		c.ProposedIndependentIDs = []string{"hallucination"}
		if _, err := admitEvidence(t, s, c); err == nil {
			t.Fatal("幻觉引用必须报错，不能静默按 1 条算")
		}
	})
}

func TestService_AdmitEvidence_RejectsMalformedProposals(t *testing.T) {
	// sentinel 这一列钉的是**永久 vs 瞬态**的分界（工单 T2-04 §口径.3），不只是「有没有报错」：
	// inbound.classify 只认哨兵，挂上 ErrCandidateShape/ErrUnknownSignal 的进 inbox 留痕，
	// 一个都不挂的（只有「观测时刻晚于当下」）走 NACK → 30s/2m/10m 阶梯。
	// 少挂一个＝永久非法在阶梯上撞三次库再进 DLQ、账上写「重试过」；多挂一个＝本可重投成功
	// 的时序错误被永久拒死，那条观测再也进不来。两侧都由这张表当证人。
	cases := []struct {
		name     string
		mutate   func(*EvidenceCandidate)
		sentinel error
	}{
		{"缺 user_id", func(c *EvidenceCandidate) { c.UserID = "" }, ErrCandidateShape},
		{"缺 source_table", func(c *EvidenceCandidate) { c.SourceTable = "" }, ErrCandidateShape},
		{"缺 source_id", func(c *EvidenceCandidate) { c.SourceID = "" }, ErrCandidateShape},
		{"缺 generation_run_id", func(c *EvidenceCandidate) { c.GenerationRunID = "" }, ErrCandidateShape},
		{"未知来源类", func(c *EvidenceCandidate) { c.SourceKind = SourceKind("telepathy") }, ErrUnknownSignal},
		{"未知明确度", func(c *EvidenceCandidate) { c.Explicitness = Explicitness("half_said") }, ErrUnknownSignal},
		{"未知具体度", func(c *EvidenceCandidate) { c.Specificity = Specificity("vibes") }, ErrUnknownSignal},
		{"evidence_type 不在六值集合", func(c *EvidenceCandidate) { c.EvidenceType = "vibes" }, ErrCandidateShape},
		{"role 只允许两个值", func(c *EvidenceCandidate) { c.Role = "contextual" }, ErrCandidateShape},
		{"source_id 超列宽", func(c *EvidenceCandidate) { c.SourceID = strings.Repeat("i", 37) }, ErrCandidateShape},
		{"excerpt 超产物面上限", func(c *EvidenceCandidate) {
			c.Excerpt = strings.Repeat("e", MaxExcerptChars+1)
		}, ErrCandidateShape},
		{"观测时刻晚于当下", func(c *EvidenceCandidate) {
			c.OccurredAt = windowEnd.Add(time.Hour)
		}, nil},
		{"metadata 不是合法 JSON", func(c *EvidenceCandidate) { c.Metadata = json.RawMessage("{oops") }, ErrCandidateShape},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			store := newFakeStore()
			s, f := newTestService(store)
			c := okEvidenceCandidate()
			tc.mutate(&c)
			_, err := admitEvidence(t, s, c)
			if err == nil {
				t.Fatal("畸形提案必须在写库前挡住（01 §25）")
			}
			if tc.sentinel == nil {
				if errors.Is(err, ErrCandidateShape) || errors.Is(err, ErrUnknownSignal) {
					t.Errorf("这一格必须保持瞬态（时钟追上来就能重投），却挂了永久哨兵：%v", err)
				}
			} else if !errors.Is(err, tc.sentinel) {
				t.Errorf("err=%v 没挂哨兵 %v ⇒ 永久/瞬态判错一类", err, tc.sentinel)
			}
			if len(f.written) != 0 || len(f.sources) != 0 {
				t.Errorf("校验失败却写了库：%+v %+v", f.sources, f.written)
			}
		})
	}
}

func TestService_AdmitEvidence_ExcerptCap(t *testing.T) {
	// 500 是**字符**上限（工单 §口径.2）：按字节数会把一条 500 字的中文观察判成非法，
	// 而那正是用户在「为何相信这条」里看得见的那句话。第三格就是钉这一点的。
	for _, tc := range []struct {
		name    string
		excerpt string
		ok      bool
	}{
		{"恰好 500 个 ASCII", strings.Repeat("a", MaxExcerptChars), true},
		{"恰好 500 个汉字（1500 字节）", strings.Repeat("啊", MaxExcerptChars), true},
		{"501 个 ASCII", strings.Repeat("a", MaxExcerptChars+1), false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			store := newFakeStore()
			s, f := newTestService(store)
			c := okEvidenceCandidate()
			c.Excerpt = tc.excerpt
			res, err := admitEvidence(t, s, c)
			if tc.ok {
				if err != nil {
					t.Fatalf("上限内的摘录不该被拒：%v", err)
				}
				if len(f.written) != 1 || f.written[0].Excerpt != tc.excerpt {
					t.Errorf("摘录没原样落库：%+v（res=%+v）", f.written, res)
				}
				return
			}
			if err == nil {
				t.Fatal("超限摘录必须在写库前挡住")
			}
			if !errors.Is(err, ErrCandidateShape) {
				t.Errorf("超限是永远非法的形状问题，要挂哨兵：%v", err)
			}
			if len(f.written) != 0 || len(f.sources) != 0 {
				t.Errorf("校验失败却写了库：%+v %+v", f.sources, f.written)
			}
		})
	}
}

func TestService_AdmitEvidence_StrengthMustStayInUnitInterval(t *testing.T) {
	// 提案体里没有 strength 这一键（03:108），所以这一格判的**是我们自己的因子表**：
	// 有人把可靠度改成 >1（或做成让 math.Pow 出 NaN 的负值）时，终局分会越出 [0,1]，
	// 而那个数是要进「为何相信这条」的账面百分比。整笔 InTx 必须回滚。
	store := newFakeStore()
	p := DefaultPolicy()
	p.Evidence.ReliabilityStatement = 3
	s, f := newTestService(store)
	s2 := NewService(store, p)
	s2.now = func() time.Time { return windowEnd }
	c := okEvidenceCandidate()
	c.SourceKind = SourceUserStatement
	if _, err := admitEvidence(t, s2, c); err == nil {
		t.Fatal("因子表被改坏到 strength>1 时必须拒绝入账（T2-04 §怎么做.2）")
	} else if !errors.Is(err, ErrCandidateShape) {
		t.Errorf("越界分是配置错误，重投一万次也不会回定义域，要挂永久哨兵：%v", err)
	}
	if len(f.written) != 0 {
		t.Errorf("越界分却落了 evidence 行：%+v", f.written)
	}
	// `f.sources` 可以有一行：这一格是在 UpsertSourceRecord **之后**判的（要拿到算分结果），
	// 而 fake 刻意不回滚权威行（service_test.go:241-242 的理由）。真库里整笔回滚由
	// 下面的 rolledBack 标记 + canonical 集成测试看住。
	if !f.rolledBack {
		t.Errorf("越界分必须整笔回滚事务，而不是只丢 evidence 行")
	}
	// 同一夹具在默认策略下必须正常入账：否则上面的红是「什么都写不进」而不是「越界被挡」。
	if _, err := admitEvidence(t, s, c); err != nil {
		t.Fatalf("默认策略下同一条不该被拒（证人格）：%v", err)
	}
	if len(f.written) != 1 {
		t.Errorf("written=%d", len(f.written))
	}
	if f.written[0].Strength < 0 || f.written[0].Strength > 1 {
		t.Errorf("落库分=%v 不在 [0,1]", f.written[0].Strength)
	}
}

func TestService_AdmitEvidence_RoleDefaultsToSupporting(t *testing.T) {
	store := newFakeStore()
	s, f := newTestService(store)
	c := okEvidenceCandidate()
	c.Role = ""
	if _, err := admitEvidence(t, s, c); err != nil {
		t.Fatalf("AdmitEvidence: %v", err)
	}
	if f.written[0].Role != "supporting" {
		t.Errorf("role=%q，空值应按 N2 DDL 默认 supporting 落库", f.written[0].Role)
	}
}

func TestService_AdmitEvidence_FrameworkInferenceStaysD(t *testing.T) {
	// 03:126「Generic framework inference cannot independently promote…」：
	// 就算取几何平均，框架推断也只能停在 D 级。
	store := newFakeStore()
	s, f := newTestService(store)
	c := okEvidenceCandidate()
	c.SourceKind = SourceFrameworkInference
	c.Explicitness = Speculative
	c.Specificity = Generic
	res, err := admitEvidence(t, s, c)
	if err != nil {
		t.Fatalf("AdmitEvidence: %v", err)
	}
	if res.Class != ClassD {
		t.Errorf("class=%s strength=%.4f，期望 D", res.Class, res.Strength)
	}
	if res.Strength >= DefaultPolicy().Evidence.ClassCFloor {
		t.Errorf("strength=%.4f 越过了 C 级下限", res.Strength)
	}
	if f.written[0].Strength != res.Strength {
		t.Errorf("落库分与结论分不一致：%.4f vs %.4f", f.written[0].Strength, res.Strength)
	}
}

func strongSupport() []EvidenceRow {
	return []EvidenceRow{
		{ID: "e1", UserID: owner, Strength: 0.90, HasStrength: true, Role: "supporting",
			SourceID: "s1", ObservedAt: time.Date(2026, 9, 20, 9, 0, 0, 0, time.UTC)},
		{ID: "e2", UserID: owner, Strength: 0.88, HasStrength: true, Role: "supporting",
			SourceID: "s2", ObservedAt: time.Date(2026, 9, 27, 9, 0, 0, 0, time.UTC)},
	}
}

func publishedState(status string) *InsightState {
	return &InsightState{
		ID: "insight-1", UserID: owner, PersonalModelID: "model-1",
		Statement: "过去三周记录时间后移。", ClaimType: "trend", Status: status,
		TimeFrom: windowStart, HasTimeFrom: true,
		TimeTo:    time.Date(2026, 10, 1, 0, 0, 0, 0, time.UTC),
		HasTimeTo: true, ModelVersion: 7,
	}
}

// putInsight 装载待重算的 insight，并把桩里的模型版本对齐到 state.ModelVersion：
// BumpVersion 拿 personal_models 的实际版本做乐观锁，两边不同步会假报版本冲突。
func putInsight(f *fakeStore, st *InsightState) {
	f.insightSt = st
	f.modelVer = st.ModelVersion
}

func reevaluate(t *testing.T, s *Service, userID, id string) (Result, error) {
	t.Helper()
	return s.ReevaluateInsight(context.Background(), userID, id)
}

func TestService_ReevaluateInsight_KeepsStrongSupport(t *testing.T) {
	store := newFakeStore()
	putInsight(store, publishedState("published"))
	store.insightEv = strongSupport()
	s, f := newTestService(store)
	res, err := reevaluate(t, s, owner, "insight-1")
	if err != nil {
		t.Fatalf("ReevaluateInsight: %v", err)
	}
	if res.Status != "published" {
		t.Errorf("status=%q，支撑没跌破门槛不得改写", res.Status)
	}
	if len(f.statusSet) != 0 || len(f.versions) != 0 || len(f.events) != 0 {
		t.Errorf("没跌破门槛却写了库：%+v %+v %+v", f.statusSet, f.versions, f.events)
	}
	if f.modelVer != 7 {
		t.Errorf("模型版本=%d，期望保持 7（02 §32：无变化不 ++）", f.modelVer)
	}
}

func TestService_ReevaluateInsight_RetractsOnConflict(t *testing.T) {
	store := newFakeStore()
	putInsight(store, publishedState("published"))
	store.insightEv = append(strongSupport(), EvidenceRow{ID: "e3", UserID: owner,
		Strength: 0.95, HasStrength: true, Role: "contradicting", SourceID: "s3",
		ObservedAt: time.Date(2026, 10, 3, 9, 0, 0, 0, time.UTC)})
	s, f := newTestService(store)
	res, err := reevaluate(t, s, owner, "insight-1")
	if err != nil {
		t.Fatalf("ReevaluateInsight: %v", err)
	}
	if res.Status != "retracted" {
		t.Fatalf("status=%q，期望 retracted", res.Status)
	}
	if len(f.statusSet) != 1 {
		t.Fatalf("statusSet=%+v", f.statusSet)
	}
	row := f.statusSet[0]
	if row.RejectReason != ReasonEvidenceConflict.String() {
		t.Errorf("reject_reason=%q，期望 evidence_conflict", row.RejectReason)
	}
	if !strings.Contains(row.GateDetail, `"conflict":true`) {
		t.Errorf("gate_detail=%q", row.GateDetail)
	}
	if len(f.events) != 1 || f.events[0].EventType != "insight_retracted" {
		t.Fatalf("事件=%+v，期望且仅期望一条 insight_retracted", f.events)
	}
	if f.events[0].AggregateType != "insights" || f.events[0].AggregateID != "insight-1" {
		t.Errorf("事件指向错行：%+v", f.events[0])
	}
	if len(f.versions) != 1 || f.versions[0].Reason != "insight_retracted" {
		t.Errorf("versions=%+v", f.versions)
	}
	if res.ModelVersion != 8 || row.ModelVersion != 8 {
		t.Errorf("ModelVersion=%d/%d，期望 7→8", res.ModelVersion, row.ModelVersion)
	}
}

func TestService_ReevaluateInsight_RetractsWhenSupportDecays(t *testing.T) {
	cases := []struct {
		name       string
		claimType  string
		rows       []EvidenceRow
		wantReason string
	}{
		{"跌到 D 级", "observation", []EvidenceRow{
			{ID: "e1", UserID: owner, Strength: 0.30, HasStrength: true, Role: "supporting",
				SourceID: "s1", ObservedAt: windowStart}}, ReasonEvidenceBelowFloor.String()},
		{"派生 claim 只剩 C 级支撑", "trend", []EvidenceRow{
			{ID: "e1", UserID: owner, Strength: 0.50, HasStrength: true, Role: "supporting",
				SourceID: "s1", ObservedAt: windowStart},
			{ID: "e2", UserID: owner, Strength: 0.55, HasStrength: true, Role: "supporting",
				SourceID: "s2", ObservedAt: windowEnd}}, ReasonEvidenceBelowFloor.String()},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			store := newFakeStore()
			state := publishedState("validated")
			state.ClaimType = tc.claimType
			putInsight(store, state)
			store.insightEv = tc.rows
			s, f := newTestService(store)
			res, err := reevaluate(t, s, owner, "insight-1")
			if err != nil {
				t.Fatalf("ReevaluateInsight: %v", err)
			}
			if res.Status != "retracted" {
				t.Fatalf("status=%q detail=%q，期望 retracted", res.Status, res.Detail)
			}
			if f.statusSet[0].RejectReason != tc.wantReason {
				t.Errorf("reject_reason=%q，期望 %q", f.statusSet[0].RejectReason, tc.wantReason)
			}
		})
	}
}

func TestService_ReevaluateInsight_ObservationSurvivesWeakIndependentCount(t *testing.T) {
	// observation 是对所引证据的复述，一条 A 级支撑就够（§6 的 A/B 门槛只管派生 claim）。
	store := newFakeStore()
	putInsight(store, publishedState("published"))
	store.insightSt.ClaimType = "observation"
	store.insightEv = []EvidenceRow{{ID: "e1", UserID: owner, Strength: 0.90,
		HasStrength: true, Role: "supporting", SourceID: "s1", ObservedAt: windowStart}}
	s, f := newTestService(store)
	res, err := reevaluate(t, s, owner, "insight-1")
	if err != nil {
		t.Fatalf("ReevaluateInsight: %v", err)
	}
	if res.Status != "published" || len(f.statusSet) != 0 {
		t.Errorf("status=%q statusSet=%+v，单条 A 级支撑的观察不该被撤", res.Status, f.statusSet)
	}
}

func TestService_ReevaluateInsight_ExpiresAfterStaleWindow(t *testing.T) {
	store := newFakeStore()
	putInsight(store, publishedState("published"))
	// 支撑仍然强，但窗口结束已远超 InsightStaleAfterDays（默认 90 天，Service.now=10-12）。
	store.insightSt.TimeTo = time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC)
	store.insightEv = strongSupport()
	s, f := newTestService(store)
	res, err := reevaluate(t, s, owner, "insight-1")
	if err != nil {
		t.Fatalf("ReevaluateInsight: %v", err)
	}
	if res.Status != "expired" {
		t.Fatalf("status=%q，期望 expired", res.Status)
	}
	// 过期是生命周期退化，不是拒绝：reject_reason 必须留空，否则 O 侧把过期算成误判。
	if f.statusSet[0].RejectReason != "" {
		t.Errorf("reject_reason=%q，期望空", f.statusSet[0].RejectReason)
	}
	if len(f.events) != 1 || f.events[0].EventType != "insight_expired" {
		t.Errorf("事件=%+v，期望 insight_expired", f.events)
	}
}

func TestService_ReevaluateInsight_NeverTouchesUserStatedStatus(t *testing.T) {
	for _, status := range []string{"confirmed", "corrected", "dismissed",
		"retracted", "expired", "draft"} {
		t.Run(status, func(t *testing.T) {
			store := newFakeStore()
			putInsight(store, publishedState(status))
			// 证据弱到不行，用户表态仍然优先（03 §12 的用户动作结果）。
			store.insightEv = []EvidenceRow{{ID: "e1", UserID: owner, Strength: 0.10,
				HasStrength: true, Role: "contradicting", SourceID: "s1", ObservedAt: windowStart}}
			s, f := newTestService(store)
			res, err := reevaluate(t, s, owner, "insight-1")
			if err != nil {
				t.Fatalf("ReevaluateInsight: %v", err)
			}
			if res.Status != status {
				t.Errorf("status=%q，期望原样 %q", res.Status, status)
			}
			if len(f.statusSet) != 0 || len(f.events) != 0 || f.modelVer != 7 {
				t.Errorf("越权改写：%+v %+v ver=%d", f.statusSet, f.events, f.modelVer)
			}
		})
	}
}

func TestService_ReevaluateInsight_Errors(t *testing.T) {
	t.Run("查无此条", func(t *testing.T) {
		store := newFakeStore()
		s, _ := newTestService(store)
		if _, err := reevaluate(t, s, owner, "nope"); !errors.Is(err, ErrInsightNotFound) {
			t.Fatalf("err=%v，期望 ErrInsightNotFound", err)
		}
	})
	t.Run("缺参数是调用错误", func(t *testing.T) {
		store := newFakeStore()
		s, _ := newTestService(store)
		if _, err := reevaluate(t, s, "", "insight-1"); err == nil {
			t.Error("期望报错")
		}
		if _, err := reevaluate(t, s, owner, ""); err == nil {
			t.Error("期望报错")
		}
	})
	t.Run("版本冲突原样上抛", func(t *testing.T) {
		store := newFakeStore()
		putInsight(store, publishedState("published"))
		store.insightEv = []EvidenceRow{{ID: "e1", UserID: owner, Strength: 0.10,
			HasStrength: true, Role: "supporting", SourceID: "s1", ObservedAt: windowStart}}
		store.bumpErr = ErrVersionConflict
		s, f := newTestService(store)
		if _, err := reevaluate(t, s, owner, "insight-1"); !errors.Is(err, ErrVersionConflict) {
			t.Fatalf("err=%v", err)
		}
		if !f.rolledBack {
			t.Error("冲突必须回滚")
		}
	})
}

func TestService_UserHistorySuppressionDoesNotInsertRow(t *testing.T) {
	store := newFakeStore()
	store.rejectionHit = "rejected-pattern"
	s, f := newTestService(store)
	c := okPattern()
	res, err := admit(t, s, c)
	if err != nil {
		t.Fatalf("Admit: %v", err)
	}
	if res.Reason != ReasonSuppressedByUserHistory {
		t.Fatalf("reason=%q", res.Reason)
	}
	if len(f.patterns) != 0 {
		t.Errorf("抑制路径插了新行：%+v（会撞 ix_patterns_user_fingerprint_unique）", f.patterns)
	}
	if res.Status != "suppressed" || res.ID != "rejected-pattern" {
		t.Errorf("status=%q id=%q，期望指向既有行", res.Status, res.ID)
	}
	if len(f.events) != 1 || f.events[0].EventType != "candidate_suppressed" {
		t.Fatalf("事件=%+v，期望且仅期望一条 candidate_suppressed", f.events)
	}
	if f.events[0].AggregateID != "rejected-pattern" {
		t.Errorf("事件指向 %q，应指向既有行", f.events[0].AggregateID)
	}
	if len(f.versions) != 0 || f.modelVer != 0 {
		t.Errorf("versions=%+v ver=%d，抑制不动模型版本", f.versions, f.modelVer)
	}
	if len(f.links) != 0 {
		t.Errorf("抑制不得回链新证据：%v", f.links)
	}
	if f.lookedFingerprint != c.Fingerprint {
		t.Errorf("查抑制签名用了 %q", f.lookedFingerprint)
	}
	if want := DefaultPolicy().RejectionCooldownDays; f.cooldownDays != want {
		t.Errorf("冷却窗=%d，期望 policy 的 %d", f.cooldownDays, want)
	}
}

func TestService_FactPromotionNeedsScoredIndependentEvidence(t *testing.T) {
	t.Run("两条已算分独立证据 + combined≥0.75 → active", func(t *testing.T) {
		store := newFakeStore()
		s, f := newTestService(store)
		c := okFact()
		c.Origin = "repeated"
		c.EvidenceIDs = []string{"e1", "e2"}
		res, err := admit(t, s, c)
		if err != nil {
			t.Fatalf("Admit: %v", err)
		}
		if res.Status != "active" {
			t.Errorf("status=%q，03 §7:175-181 的推断路径应晋升 active（combined=%.4f）",
				res.Status, f.facts[0].Confidence)
		}
	})
	t.Run("推断事实证据不足留在 candidate", func(t *testing.T) {
		store := newFakeStore()
		s, _ := newTestService(store)
		c := okFact()
		c.Origin = "repeated"
		c.EvidenceIDs = []string{"e1"}
		res, err := admit(t, s, c)
		if err != nil {
			t.Fatalf("Admit: %v", err)
		}
		if res.Status != "candidate" {
			t.Errorf("status=%q，期望 candidate", res.Status)
		}
	})
	t.Run("冲突证据不得平均掉", func(t *testing.T) {
		store := newFakeStore()
		store.evidence["e2"] = Evidence{ID: "e2", UserID: owner, Strength: 0.99,
			HasStrength: true, Role: "contradicting", SourceID: "s2", ObservedAt: windowEnd}
		s, _ := newTestService(store)
		c := okFact()
		c.Origin = "repeated"
		c.EvidenceIDs = []string{"e1", "e2"}
		res, err := admit(t, s, c)
		if err != nil {
			t.Fatalf("Admit: %v", err)
		}
		if res.Status != "candidate" {
			t.Errorf("status=%q，冲突未解时不得晋升（03 §7:186-187）", res.Status)
		}
	})
	t.Run("未算分的证据不参与晋升判定", func(t *testing.T) {
		store := newFakeStore()
		store.evidence["e1"] = Evidence{ID: "e1", UserID: owner, SourceID: "s1",
			Role: "supporting", ObservedAt: windowStart}
		store.evidence["e2"] = Evidence{ID: "e2", UserID: owner, SourceID: "s2",
			Role: "supporting", ObservedAt: windowEnd}
		s, _ := newTestService(store)
		c := okFact()
		c.Origin = "behavioral"
		c.EvidenceIDs = []string{"e1", "e2"}
		res, err := admit(t, s, c)
		if err != nil {
			t.Fatalf("Admit: %v", err)
		}
		if res.Status != "candidate" {
			t.Errorf("status=%q，strength 为 NULL 的行不能当算分输入", res.Status)
		}
	})
}
