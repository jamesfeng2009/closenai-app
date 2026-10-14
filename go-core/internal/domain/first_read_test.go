// 批 4 的初读卡生成层（first_read_service 的静态直出部分）在 Go 侧的结构闸。
//
// 与 backend/tests/test_n8_batch4_contract.py 的分工：那边比对两侧**同一批模板文本**
// 过闸后的结果（闭包语料），这里钉的是 Go 复刻件自己的结构事实——
// 断言条数区间、去重只在 answer 轮、filler 轮不去重、三类跳过、以及
// 「闸入参永远来自模板」这条可达性前提。
package domain

import (
	"strings"
	"testing"
)

func TestLocaleForAndSafetyLangKeyDiverge(t *testing.T) {
	// 两支函数不是同一件事：locale_for 先 lower，_lang_key 不 lower（:45-47 vs :26-28）。
	for _, tc := range []struct {
		in, locale, langKey string
	}{
		{"zh", "zh", "zh"},
		{"ZH", "zh", "en"},
		{"zh-CN", "zh", "zh"},
		{"ZH-CN", "zh", "en"},
		{"en", "en", "en"},
		{"fr", "en", "en"},
		{"", "en", "en"},
		{"z", "en", "en"},
		{"Zebra", "en", "en"},
	} {
		if got := LocaleFor(tc.in); got != tc.locale {
			t.Errorf("LocaleFor(%q) = %q，期望 %q", tc.in, got, tc.locale)
		}
		if got := SafetyLangKey(tc.in); got != tc.langKey {
			t.Errorf("SafetyLangKey(%q) = %q，期望 %q", tc.in, got, tc.langKey)
		}
	}
}

func TestBuildFirstReadThreeAnswersInOrder(t *testing.T) {
	card := BuildFirstRead([]FirstReadAnswerItem{
		{Q: "conflict", Option: "speak_now"},
		{Q: "uncertainty", Option: "list_out"},
		{Q: "night", Option: "replay"},
	}, "", "zh")
	if card.Locale != "zh" {
		t.Fatalf("locale = %q", card.Locale)
	}
	if len(card.Assertions) != 3 || card.AnswerCount != 3 {
		t.Fatalf("三条已知答案应出三条：%#v", card.Assertions)
	}
	tpl := LoadFirstReadTemplate("zh")
	for i, item := range []FirstReadAnswerItem{
		{Q: "conflict", Option: "speak_now"},
		{Q: "uncertainty", Option: "list_out"},
		{Q: "night", Option: "replay"},
	} {
		want := tpl.Assertions[item.Q][item.Option]
		want = strings.ReplaceAll(want, "{option}", optionText(tpl, item.Q, item.Option))
		if card.Assertions[i] != want {
			t.Errorf("第 %d 条 = %q，期望模板原文 %q", i, card.Assertions[i], want)
		}
	}
	// 未选类型 ⇒ 不出 summary，但 closer 照旧。
	if card.Summary != "" {
		t.Errorf("persona_type 为空却有 summary：%q", card.Summary)
	}
	if card.Closer != tpl.Closer {
		t.Errorf("closer 与模板不一致：%q", card.Closer)
	}
	if card.PersonaType != "" {
		t.Errorf("PersonaType = %q，期望空串（HTTP 层据此写 null）", card.PersonaType)
	}
}

func optionText(t *FirstReadTemplate, q, id string) string {
	question := t.questionByID(q)
	if question == nil {
		return ""
	}
	for _, o := range question.Options {
		if o.ID == id {
			return o.Text
		}
	}
	return ""
}

// TestBuildFirstReadCapAtThree 钉住「[:3]」与「去重」两条：四条已知答案出三条，
// 重复的同一句不占两格。
func TestBuildFirstReadCapAtThree(t *testing.T) {
	card := BuildFirstRead([]FirstReadAnswerItem{
		{Q: "conflict", Option: "speak_now"},
		{Q: "uncertainty", Option: "keep_busy"},
		{Q: "night", Option: "replay"},
		{Q: "conflict", Option: "hold_in"},
	}, "", "en")
	if len(card.Assertions) != FirstReadMaxAssertions {
		t.Fatalf("四条已知答案应截到 %d，得到 %d", FirstReadMaxAssertions, len(card.Assertions))
	}
	if card.AnswerCount != len(card.Assertions) {
		t.Errorf("answer_count=%d 与 assertions 长度 %d 不同源", card.AnswerCount, len(card.Assertions))
	}

	dup := BuildFirstRead([]FirstReadAnswerItem{
		{Q: "conflict", Option: "speak_now"},
		{Q: "conflict", Option: "speak_now"},
		{Q: "uncertainty", Option: "keep_busy"},
		{Q: "night", Option: "replay"},
	}, "", "en")
	if len(dup.Assertions) != 3 {
		t.Fatalf("去重后仍是三条：%#v", dup.Assertions)
	}
	seen := map[string]bool{}
	for _, a := range dup.Assertions {
		if seen[a] {
			t.Fatalf("响应里出现重复断言：%q", a)
		}
		seen[a] = true
	}
}

// TestBuildFirstReadSkipsUnknown 钉三类跳过：未知题、未知选项、模板无槽位/无 pattern。
// 三类都不报错，只是少一条 ⇒ 剩下由 filler 补到 2 条。
func TestBuildFirstReadSkipsUnknown(t *testing.T) {
	card := BuildFirstRead([]FirstReadAnswerItem{
		{Q: "no-such-question", Option: "speak_now"},
		{Q: "conflict", Option: "no-such-option"},
		{Q: "", Option: ""},
	}, "", "zh")
	tpl := LoadFirstReadTemplate("zh")
	if len(card.Assertions) != len(tpl.Fillers) {
		t.Fatalf("三条全跳过 ⇒ 由 filler 补齐，得到 %#v", card.Assertions)
	}
	for i, filler := range tpl.Fillers {
		if card.Assertions[i] != filler {
			t.Errorf("filler[%d] = %q，期望 %q", i, card.Assertions[i], filler)
		}
	}
}

// TestBuildFirstReadFillerRoundNoDedup 是本域最容易「顺手改好」的一格：
// Python 的 filler 补齐循环**没有** `not in out` 判重（:144-150），
// 而去重只存在于 answer 轮（:116-118）。今天两份模板的 filler 互不相同、
// 也与断言不同，所以这条只在模板改动时才显现——因此按结构钉，而不是靠夹具。
func TestBuildFirstReadFillerRoundNoDedup(t *testing.T) {
	got := assertionsRound(tpl("zh"), nil, "zh", []string{"同一句", "同一句"})
	if len(got) != 2 || got[0] != got[1] {
		t.Fatalf("filler 轮应当不去重，得到 %#v", got)
	}
}

// TestBuildFirstReadSummaryNeedsTrait 「D 级标签仅描述用户自己」：persona 在词表里没有
// trait ⇒ summary 为空串（键仍在，值不同形）。
func TestBuildFirstReadSummaryNeedsTrait(t *testing.T) {
	withTrait := BuildFirstRead(nil, "INTJ", "zh")
	tpl := LoadFirstReadTemplate("zh")
	want := strings.ReplaceAll(tpl.SummaryFrame, "{persona}", "INTJ")
	want = strings.ReplaceAll(want, "{trait}", tpl.Personas["INTJ"])
	if withTrait.Summary != want {
		t.Errorf("summary = %q，期望 %q", withTrait.Summary, want)
	}
	// 模板里只有 16 型；其余四个字母的组合一律没有 trait。
	for _, p := range []string{"QQQQ", "INTX", "ZZZZ"} {
		if got := BuildFirstRead(nil, p, "zh").Summary; got != "" {
			t.Errorf("%s 不该有 summary：%q", p, got)
		}
	}
	// 两侧大小写：调用方已在请求校验层 upper 过，这里再给小写也应当同形
	// （PyUpper 幂等），但**不作为契约**——校验层是唯一入口。
	if got := BuildFirstRead(nil, "intj", "zh").Summary; got != withTrait.Summary {
		t.Errorf("同一型的大小写两路 summary 不同：%q vs %q", got, withTrait.Summary)
	}
}

// TestBuildFirstReadEveryTemplateStringPassesGate 是本层的**存在性**闸：
// 两份模板里每一条会被直出的文本（断言、filler、closer、summary_frame 的 16 型展开）
// 过闸后必须原样返回——否则现网某一张卡会**静默少一条断言**。
// 它同时是「闸入参全来自模板」这条可达性前提的落点：请求原文进不了 FilterTemplate。
func TestBuildFirstReadEveryTemplateStringPassesGate(t *testing.T) {
	for _, locale := range []string{"zh", "en"} {
		tpl := LoadFirstReadTemplate(locale)
		for _, q := range tpl.Questions {
			for _, o := range q.Options {
				pattern := tpl.Assertions[q.ID][o.ID]
				if pattern == "" {
					t.Fatalf("%s/%s/%s 没有 assertion pattern", locale, q.ID, o.ID)
				}
				text := strings.ReplaceAll(pattern, "{option}", o.Text)
				if got := FilterTemplate(text, locale); got != text {
					t.Errorf("%s 断言被闸吞掉：%q ⇒ %q", locale, text, got)
				}
			}
		}
		for i, filler := range tpl.Fillers {
			if got := FilterTemplate(filler, locale); got != filler {
				t.Errorf("%s filler[%d] 被闸吞掉：%q", locale, i, filler)
			}
		}
		if got := FilterTemplate(tpl.Closer, locale); got != tpl.Closer {
			t.Errorf("%s closer 被闸吞掉：%q", locale, got)
		}
		for persona, trait := range tpl.Personas {
			frame := strings.ReplaceAll(tpl.SummaryFrame, "{persona}", persona)
			frame = strings.ReplaceAll(frame, "{trait}", trait)
			if got := FilterTemplate(frame, locale); got != frame {
				t.Errorf("%s summary(%s) 被闸吞掉：%q", locale, persona, got)
			}
		}
	}
}

func tpl(locale string) *FirstReadTemplate { return LoadFirstReadTemplate(locale) }

// assertionsRound 直接跑 build_first_read 的两轮，fillers 由调用方给
// （为了造出「两条 filler 相同」这种今天模板里不存在、但判据要钉住的形状）。
func assertionsRound(t *FirstReadTemplate, answers []FirstReadAnswerItem,
	locale string, fillers []string) []string {
	out := assertions(t, answers, locale)
	save := t.Fillers
	t.Fillers = fillers
	defer func() { t.Fillers = save }()
	for idx := 0; len(out) < FirstReadMinAssertions && idx < len(t.Fillers); idx++ {
		if text := FilterTemplate(t.Fillers[idx], locale); text != "" {
			out = append(out, text)
		}
	}
	return out
}
