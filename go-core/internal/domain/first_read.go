// N8 批 4（onboarding 域）的初读卡生成：`first_read_service` 静态直出那一层的 Go 复刻。
//
// 出处逐字对应 backend/services/first_read_service.py 的六处：
//
//	_LOCALES/locale_for (:38-47)   ——模板只有 zh/en 两份，其余语言**一律**归 en
//	load_template       (:50-67)   ——带进程内缓存，缺文件时非 en 回退 en
//	_resolve_option     (:77-89)   ——本域只走「按 id 正查」那一支（见下面的可达性注释）
//	_assertions         (:91-120)  ——逐条复述＋放大，跳过三类，去重，最后 [:3]
//	_summary            (:122-131) ——D 级标签只描述用户自己；查不到 trait 就不出 summary
//	build_first_read    (:133-170) ——九键 card_payload，顺序即契约
//
// 「静态直出、零 LLM」(:7-8) 是这一层能整域切到 Go 的前提：全部输出都是模板槽位填充，
// 而模板只有两份 JSON（已按字节内嵌，Python 侧有 sha256 相等闸守着两份副本不分叉）。
package domain

import (
	_ "embed"
	"encoding/json"
	"strings"
	"time"
)

// 断言条数区间（PRD §5.19.2 B01：2–3 条），与 :32-33 同值。
const (
	FirstReadMinAssertions = 2
	FirstReadMaxAssertions = 3
)

// 两份模板的内嵌副本。文件名带 locale 后缀（go:embed 不能跨 module 目录去读
// backend/data/…，已登记的决策点 D-1），内容必须与 backend/data/first_read/{zh,en}.json
// **逐字节相同**——那条 sha256 相等闸在 Python 侧（backend/tests/test_n8_batch4_contract.py）。
var (
	//go:embed templates/first_read_zh.json
	firstReadZhJSON []byte
	//go:embed templates/first_read_en.json
	firstReadEnJSON []byte
)

type FirstReadOption struct {
	ID   string `json:"id"`
	Text string `json:"text"`
}

type FirstReadQuestion struct {
	ID      string            `json:"id"`
	Text    string            `json:"text"`
	Options []FirstReadOption `json:"options"`
}

// 只声明生成用得到的六段；模板里的 `_comment` 是给人看的，不进结构。
// Python 那边全部走 `dict.get(...)`，缺键得到 None/{}，与这里零值后的行为同形。
type FirstReadTemplate struct {
	Questions    []FirstReadQuestion          `json:"questions"`
	Assertions   map[string]map[string]string `json:"assertions"`
	SummaryFrame string                       `json:"summary_frame"`
	Closer       string                       `json:"closer"`
	Fillers      []string                     `json:"fillers"`
	Personas     map[string]string            `json:"personas"`
}

// 模板在 init 期解析一次：Python 的 `_TEMPLATES` 进程内缓存（:42/:66）同一个位置。
//
// 解析失败只可能是内嵌副本坏了——那是编译期就该红的事，不是运行时分支，
// 所以这里 panic 而不是返回 error（Python 的 FileNotFoundError 回退支在嵌入两份之后不可达）。
var firstReadTemplates = map[string]*FirstReadTemplate{
	"zh": mustParseTemplate(firstReadZhJSON),
	"en": mustParseTemplate(firstReadEnJSON),
}

func mustParseTemplate(raw []byte) *FirstReadTemplate {
	var t FirstReadTemplate
	if err := json.Unmarshal(raw, &t); err != nil {
		panic("first_read 模板内嵌副本坏了: " + err.Error())
	}
	return &t
}

// LocaleFor 是 `locale_for`(:45-47)：**先 lower** 再看 zh 前缀，其余一律 en。
// 与 SafetyLangKey 的区别写在那支函数的注释里（一支 lower、一支不 lower），
// 本域把 LocaleFor 的返回值再传给闸，所以两条路在这里汇合但**不是同一支函数**。
func LocaleFor(language string) string {
	if strings.HasPrefix(strings.ToLower(language), "zh") {
		return "zh"
	}
	return "en"
}

func LoadFirstReadTemplate(language string) *FirstReadTemplate {
	return firstReadTemplates[LocaleFor(language)]
}

// OnboardingProfile 是 restore（GET）读到的那一行。
//
// 三个 Has* 位不是冗余：这一行的四列在 DDL 里都可空（models/journey.py:82-92），
// 而 Python 把 None 直接写进响应（`payload["language"]=profile.language`、
// `payload["created_at"]=profile.created_at`），NULL 在 JSON 里是 **null 而不是缺键**，
// 也与「空串」「零值时间」不同形。store 只搬列、不做判断（口径同批 3 的 JourneyEventRow）。
type OnboardingProfile struct {
	Found        bool
	ID           string
	FirstRead    string // 列原文（TEXT，NULL 折成空串）
	Language     string
	HasLanguage  bool
	CreatedAt    time.Time
	HasCreatedAt bool
}

// OnboardingSubmitInput 是 submit 那次事务里**已经定下来**的输入：账号列与本次要用的码。
//
// Code 由 store 在事务里定：列里已有值就原样复用（user_repository.py:373-374），为空才走
// 「随机⇒查重⇒UPDATE」。查重必须在事务里、而 first_read 列的文本又依赖查重结果
// （share_url 在 :186-188 就写死了，restore 之后按列复现、不再回查），所以装配卡片
// 用的是**回调**而不是「先把文本算好再传进去」：撞码重生成时文本要重算，
// 那是同一个事务里的第二轮。Python 侧同一段是 :182-195 的同一个 session，没有拆开的余地。
type OnboardingSubmitInput struct {
	User JourneyUser
	// Code 是 share_url 要用的码。User.Found 为 false 时恒为空串，对应 Python 的
	// 「不覆盖 share_url，保留 build_first_read 写的落地链接（Python 的 share_landing_url）」。
	Code string
}

// OnboardingWrite 是 submit 那次事务要落的三处写的文本面。
// 文本全部由调用方折好（域层不产字节，口径同 JourneyPrefill.Payload）。
type OnboardingWrite struct {
	// PersonaType 空串＝Python 的 None（列可空，models/journey.py:82）。
	PersonaType string
	// AnswersText / FirstReadText 走 `json.dumps(ensure_ascii=False)` 的**默认分隔符**
	// （库里带空格），与响应面的紧凑形不同——两侧写混会在行级比对上出假差异。
	AnswersText   string
	FirstReadText string
	// Language 是**请求原文**（submit 把 request.language 直接给 upsert，:190），
	// 不是 locale_for 折出来的 zh/en——「重进 App 按生成当时的语言复现」靠的是这一列。
	Language string
	// JourneyRows 只在 User.Found 时非空：Python 的 prefill 在 `if user is not None` 块里。
	JourneyRows []JourneyPrefill
}

// OnboardingSubmitBuilder 由 HTTP 层提供：给定事务里定下的输入，返回要落的文本。
// 撞码时 store 会带着新码再调用一次。
type OnboardingSubmitBuilder func(OnboardingSubmitInput) (OnboardingWrite, error)

// FirstReadAnswerItem 是请求里的一道快问答案（`FirstReadAnswer` 的两个字段，
// schemas.py:489-497）。落库的 `answers` 列文本就是这个结构按 q、option 声明序编码的数组。
type FirstReadAnswerItem struct {
	Q      string
	Option string
}

// FirstReadCard 是 build_first_read(:133-170) 的产物。
//
// PersonaType 用空串表示「没有」，由 HTTP 层决定写成 null 还是字符串——
// Python 那边是 `(persona_type or "").upper() or None`，两种假值同源。
type FirstReadCard struct {
	Assertions  []string
	Summary     string
	Closer      string
	Locale      string
	PersonaType string
	AnswerCount int
}

// resolveOption 是 `_resolve_option`(:77-89) 的**可达那一半**。
//
// Python 还有第二支「按选项文案反查 id」，它读的是 `item.get("text")`——而本端点的
// 模型只声明 q/option、`extra` 默认 ignore（:77-89 与本文件头注释），请求里的 text
// 进不了 dict ⇒ 那一支从 HTTP 面**永远走不到**，所以这里不复刻，已按「无观测点」
// 登记进 tasks.md 的批 4 节。内部调用方（不走 HTTP）要用时得另开一条路。
func resolveOption(question *FirstReadQuestion, optionID string) (string, string, bool) {
	if optionID == "" {
		return "", "", false
	}
	for i := range question.Options {
		if question.Options[i].ID == optionID {
			return question.Options[i].ID, question.Options[i].Text, true
		}
	}
	return "", "", false
}

// formatOption 是 `pattern.format(option=opt_text)`。
//
// 只认 `{option}` 这一个槽位：录制脚本对模板里每条 pattern 的替换字段做了穷举断言
// （字段集合必须恰好是 {option} / {persona,trait}），所以这里不需要 Python 那套
// 通用 format 语法——真出现 `{0}` 或 `{{` 时 Python 抛 KeyError/换字面，那是 500，
// 而 500 在这条路上没有任何夹具观测点。
func formatOption(pattern, option string) string {
	return strings.ReplaceAll(pattern, "{option}", option)
}

func (t *FirstReadTemplate) questionByID(id string) *FirstReadQuestion {
	// Python 是 `{q.get("id"): q for q in questions}`：同 id 后写的覆盖先写的。
	// 模板里 id 不重复，但覆盖口径要一致，所以这里从后往前找。
	for i := len(t.Questions) - 1; i >= 0; i-- {
		if t.Questions[i].ID == id {
			return &t.Questions[i]
		}
	}
	return nil
}

// assertions 是 `_assertions`(:91-120)：三类跳过（未知题、未知选项、无槽位）
// ＋过闸后非空且不重复才 append，最后截到 MAX。
func assertions(t *FirstReadTemplate, answers []FirstReadAnswerItem, locale string) []string {
	out := make([]string, 0, FirstReadMaxAssertions)
	for _, item := range answers {
		// :99 那一条 `item.get("q") or item.get("question_id") or item.get("question")`：
		// 本端点只有 "q"，空串在 Python 里继续往后 fallback 最终得到 None，
		// 与这里「按空串查不到题」同形。
		question := t.questionByID(item.Q)
		if question == nil {
			continue
		}
		optID, optText, ok := resolveOption(question, item.Option)
		if !ok {
			continue
		}
		pattern := t.Assertions[item.Q][optID]
		if pattern == "" {
			continue
		}
		text := FilterTemplate(formatOption(pattern, optText), locale)
		if text == "" {
			continue
		}
		if !containsString(out, text) {
			out = append(out, text)
		}
	}
	if len(out) > FirstReadMaxAssertions {
		out = out[:FirstReadMaxAssertions]
	}
	return out
}

func containsString(list []string, want string) bool {
	for _, have := range list {
		if have == want {
			return true
		}
	}
	return false
}

func summary(t *FirstReadTemplate, personaType, locale string) string {
	upper := PyUpper(personaType)
	trait := t.Personas[upper]
	if trait == "" {
		return ""
	}
	frame := strings.ReplaceAll(t.SummaryFrame, "{persona}", upper)
	frame = strings.ReplaceAll(frame, "{trait}", trait)
	return FilterTemplate(frame, locale)
}

// BuildFirstRead 是 `build_first_read`(:133-170)：断言 ⇒ filler 补齐 ⇒ summary ⇒ closer。
//
// filler 那一轮**不做去重**（Python 没有 `not in out` 那一步），且被闸弃用的那条
// 仍然让 idx 前移——所以「fillers[0] 命中闸」会让 fillers[1] 顶上第 2 格。
// persona_type 的归一（strip⇒upper⇒[A-Z]{4}）在请求校验层做，这里收到的是已经归一后的值。
func BuildFirstRead(answers []FirstReadAnswerItem, personaType, language string) FirstReadCard {
	locale := LocaleFor(language)
	t := firstReadTemplates[locale]
	list := assertions(t, answers, locale)
	for idx := 0; len(list) < FirstReadMinAssertions && idx < len(t.Fillers); idx++ {
		if text := FilterTemplate(t.Fillers[idx], locale); text != "" {
			list = append(list, text)
		}
	}
	return FirstReadCard{
		Assertions:  list,
		Summary:     summary(t, personaType, locale),
		Closer:      FilterTemplate(t.Closer, locale),
		Locale:      locale,
		PersonaType: PyUpper(personaType),
		AnswerCount: len(list),
	}
}
