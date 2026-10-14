// N8 批 4 的三块「核」语料闸（夹具的 template_corpus / persona_upper_corpus /
// referral_corpus 三块）。
//
// 为什么这三块要单独成语料、而不是塞进 HTTP 用例：它们判的是**函数**，而 HTTP 用例
// 只能抽到函数的一小片输出。三处各自的具体理由：
//
//   - 措辞闸（filter_template）：本域两份模板产出的每一条候选文案，实测**一条都不该被闸掉**。
//     用例面只能证「今天没掉」；Go 侧若判据写歪（比如把 `\b` 前边界写成两侧边界、
//     或把 en 的词表套到 zh 上），某条文案会被**静默吞掉**——那张卡少一句话，
//     响应形状仍然合法。只有把闭包全列出来比输出文本才可证。
//   - CPython 的 str.upper()：`ßß⇒SSSS`、`ﬁnt⇒FINT` 这两支靠用例已经能证，
//     但「还有哪一支非 ASCII 字符也能拼出合法 persona」是用例面**穷举不到**的表。
//   - 深链拼装：`site_url.rstrip('/')` 剥一串斜杠。用例只能走到**当前配置的那一个**
//     site_url；配置写歪（多个尾斜杠）时两侧给出的链接不同，而那是落进列里的字节。
//
// 三块都按「夹具给的是 Python 现算的值，Go 侧就地重算同一支函数」的方式比，
// 不做「看起来差不多」的近似断言。
package httpapi

import (
	"strings"
	"testing"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// TestN8TemplateCorpus 逐条重放 `safety_service.filter_template(文案, locale)`。
//
// 语料分两面：
//   - **闭包面**（kind＝assertion/filler/closer/summary）：两份模板能产出的每一条候选文案，
//     交叉格（不同题的选项填进本行 pattern）Python 走不到，但多给不亏——它把「pattern
//     之间互相拼接」这类改判据也一并钉住。这一面实测**一条都不该被闸掉**，所以
//     dropped 必须为 0。
//   - **探针对**（kind 以 `hit-` / `safe-` 开头）：闭包全放行 ⇒ 「命中即吞」那一支没有
//     观测点，把 Go 的整条判据删掉也照样绿。这一面按词表现取词条造出 hit/safe 两个方向，
//     逐条要求 hit-* 被吞、safe-* 放行——它钉的是判据本身，含 en 的 `\b` **只有前边界**、
//     zh 的子串必须连续、`(?<!…)` 后视的三格，以及「label∧causal 而无 third 不算越界」。
func TestN8TemplateCorpus(t *testing.T) {
	file := loadN8Fixture(t)
	var zh, en, reachable, dropped, probes, probeErrors int
	for _, entry := range file.TemplateCorpus {
		locale := entry.Locale
		if locale != "zh" && locale != "en" {
			t.Fatalf("语料里出现未知 locale %q（first_read 模板只有这两份）", locale)
		}
		want := entry.Want
		if got := domain.FilterTemplate(entry.Raw, locale); got != want {
			t.Errorf("%s/%s 的闸输出不符\n go: %q\n py: %q\n 原文: %q",
				locale, entry.Kind, got, want, entry.Raw)
		}
		switch locale {
		case "zh":
			zh++
		case "en":
			en++
		}
		if entry.Reachable {
			reachable++
		}
		// 探针的方向由 kind 前缀声明（录制侧也按同一条断言过一遍 Python 的现算结果，
		// 所以「Go 与夹具同形」不够，还得「夹具与这一格声明的方向同形」）。
		if kind := entry.Kind; strings.HasPrefix(kind, "hit-") || strings.HasPrefix(kind, "safe-") {
			probes++
			wantDropped := strings.HasPrefix(kind, "hit-")
			if (want == "") != wantDropped {
				t.Errorf("探针 %s/%s 的方向与 kind 前缀不符：want=%q（%s 应该%s）",
					locale, kind, want, kind, map[bool]string{true: "被闸掉", false: "放行"}[wantDropped])
				probeErrors++
			}
			continue
		}
		if want == "" {
			dropped++
		}
	}
	// 闸别：两块 locale 都必须有观测点（en 走 `\b` 正则、zh 走纯子串，两支判据不同），
	// 且四类文案都要在场——closer/filler 只在特定分支才进响应，最容易整类漏掉。
	if zh == 0 || en == 0 {
		t.Fatalf("措辞闸语料只剩一侧：zh=%d en=%d", zh, en)
	}
	seen := map[string]int{}
	for _, entry := range file.TemplateCorpus {
		seen[entry.Kind]++
	}
	for _, kind := range []string{"assertion", "filler", "closer", "summary"} {
		if seen[kind] == 0 {
			t.Fatalf("措辞闸语料缺 %s 一类（实测各类 %v）——那一类的判据没有观测点", kind, seen)
		}
	}
	// 探针面：两侧都必须有「被吞」与「放行」，且点名四个判据各有一对。
	// 少一对＝那一支判据回到看不见的地步，Go 抄错只能在灰度期靠用户看出来。
	for _, kind := range []string{
		"hit-banned", "safe-broken-term", "safe-boundary-pre", "hit-boundary-post",
		"hit-label-third", "hit-causal-third", "safe-no-third",
		"hit-third-at-start", "hit-third-after-verb", "safe-third-lookbehind",
	} {
		if seen[kind] == 0 {
			t.Errorf("措辞闸语料缺探针 %s——那一支判据没有观测点（实测各类 %v）", kind, seen)
		}
	}
	if probes == 0 || probeErrors != 0 {
		t.Errorf("探针面退化：共 %d 条、方向不符 %d 条", probes, probeErrors)
	}
	if reachable == 0 {
		t.Fatal("措辞闸语料里没有一条 reachable=true：真实流程走得到的那些文案消失了")
	}
	// 今天这条必须为 0（两份模板一条都不该被闸掉）。不为 0 不一定是错——
	// 但那是**模板被改过**的信号，必须重看词表而不是顺手改闸。
	if dropped != 0 {
		t.Errorf("有 %d 条模板文案被 Python 侧闸掉了（实测口径是 0 条）："+
			"改模板时这一格要连同 tasks.md 一起重新取证", dropped)
	}
	t.Logf("措辞闸闭包 %d 条（zh %d / en %d / 可达 %d / 被闸掉 %d）＋探针 %d 条逐条比输出",
		len(file.TemplateCorpus), zh, en, reachable, dropped, probes)
}

// n8PersonaLegal 是本域 persona_type 的合法判据（schemas.py:514 的 re.fullmatch("[A-Z]{4}")）：
// 校验器的**结果**是观测点，upper 的中间形态不是——非 ASCII 的展开结果两侧都过不了
// [A-Z]{4}，而 400 体回显的是**原值**，所以下面只比「合不合法」与合法时的那一串。
func n8PersonaLegal(upper string) bool {
	if len(upper) != 4 {
		return false
	}
	for _, b := range []byte(upper) {
		if b < 'A' || b > 'Z' {
			return false
		}
	}
	return true
}

// n8PersonaWitness 是夹具侧挑 raw 的同一条规则：**存在** k∈{1,2,3,4} 使 char×k 大写后
// 正好是四个 ASCII 字母。一律按 ×4 扫会把 ß 这一支扫没了——它的大写是**两个**字符，
// ×4 得 8 个，永远不合法，于是这张表唯一 motivates 展开表的码位没有观测点
// （2026-10-15 变异自证 #117 打不红的根因就在这里，夹具与这道闸同日改成按 k 找见证）。
func n8PersonaWitness(char string) bool {
	rep := char
	for k := 1; k <= 4; k++ {
		if n8PersonaLegal(domain.PyUpper(rep)) {
			return true
		}
		rep += char
	}
	return false
}

// TestN8PersonaUpperCorpusIsClosed 钉的是「Go 复刻的是 **CPython** 的 upper，
// 不是 ASCII 的 upper」，而且是**闭包**而不是抽样。
//
// 两条断言：
//  1. 逐条比 verdict：夹具给的每个码位四位输入，Go 判合法必须与 Python 同，
//     且合法时那一串逐字相同（它要落进列与响应）。
//  2. Go 自己扫一遍 BMP，把「PyUpper 后成为合法 persona」的码位收集起来，要求与
//     「夹具的 ascii4 码位 ∪ A-Z」相等——少一条＝Go 漏了某个码位，多一条＝Go 凭空地
//     把非法值放行了（后者会在灰度期表现为「同一个 persona_type 一边 400 一边 200」）。
//     并集那一半是**按定义补的**：夹具只收「upper 会变样」的码位，而 'A' 恒等不在表里，
//     四位重复后却照样合法。
//
// 为什么第 2 条不能按夹具的筛选条件扫（「upper 变了，且结果全 ASCII 或多码位」）：
// Go 的 strings.ToUpper 只有简单映射，那 94 个「Python 展开成多个非 ASCII 码位」的字符
// 在 Go 侧长度恒为 1，扫出来必然少一批。集合相等只要求在**影响响应的那一支**成立。
func TestN8PersonaUpperCorpusIsClosed(t *testing.T) {
	file := loadN8Fixture(t)
	pyLegal := map[string]bool{}
	for _, entry := range file.PersonaUpperCorpus {
		got := domain.PyUpper(entry.Raw)
		if n8PersonaLegal(got) != entry.ASCII4 {
			t.Errorf("%q 的合法性判定不符：Go=%v Python=%v（Go upper=%q py upper=%q）",
				entry.Raw, n8PersonaLegal(got), entry.ASCII4, got, entry.Upper)
		}
		// 只有「结果是 ASCII」时那一串才会进响应/列；非 ASCII 的展开两侧都过不了
		// [A-Z]{4}，中间形态不可观测，所以不强求逐字。
		if entry.ASCII4 && got != entry.Upper {
			t.Errorf("%q 的 upper 逐字不符：Go=%q Python=%q", entry.Raw, got, entry.Upper)
		}
		if entry.ASCII4 {
			// Raw 是「同一个码位重复四次」，取**第一个 rune** 而不是第一个字节：
			// ı（U+0131）与 ſ（U+017F）都是多字节字符。
			pyLegal[string([]rune(entry.Raw)[0])] = true
		}
	}
	var goLegal []string
	for cp := 0x20; cp < 0x10000; cp++ {
		if cp >= 0xD800 && cp <= 0xDFFF {
			continue // 代理区：不是合法码位，Go 的 string(rune(cp)) 会给出替换字符
		}
		char := string(rune(cp))
		if n8PersonaWitness(char) {
			goLegal = append(goLegal, char)
		}
	}
	if len(goLegal) == 0 {
		t.Fatal("Go 侧自扫 BMP 一个合法 persona 都没扫出来——PyUpper 或判据坏了")
	}
	// 期望集合＝夹具的 ascii4 码位 ∪ **A-Z 自己**。
	// 后半是推出来的、不是猜的：夹具那张表只收「upper 会变样」的码位
	// （Python 侧的筛选条件是 `upper != char`），而 `'A'.upper()=='A'` 恒等，
	// 于是它按定义不在表里；但四位重复后仍是 [A-Z]{4} ⇒ 也合法。
	// 「恒等且四位重复后合法」的码位只有 ASCII 大写字母本身，所以这一支可在 Go 侧现算。
	expect := map[string]bool{}
	for char := range pyLegal {
		expect[char] = true
	}
	for c := 'A'; c <= 'Z'; c++ {
		expect[string(c)] = true
	}
	for _, char := range goLegal {
		if !expect[char] {
			t.Errorf("Go 认为 %q×4 能拼出合法 persona，Python 侧的语料里没有这一条"+
				"（灰度期表现为同一个输入一边 200 一边 400）", char)
		}
	}
	for char := range expect {
		if !n8PersonaWitness(char) {
			t.Errorf("Python 侧 %q 能拼出合法 persona，Go 侧却不合法", char)
		}
	}
	if len(goLegal) != len(expect) {
		t.Errorf("合法 persona 的码位集合大小不符：Go %d 个，Python %d 个（Go：%s）",
			len(goLegal), len(expect), strings.Join(goLegal, ""))
	}
	t.Logf("persona upper 闭包：夹具 %d 个「upper 会变样」的码位、其中 %d 个能拼出合法值，"+
		"加上 26 个恒等的大写字母＝%d 个；Go 自扫 BMP 得到同样 %d 个",
		len(file.PersonaUpperCorpus), len(pyLegal), len(expect), len(goLegal))
}

// TestN8ReferralLinkCorpus 钉 `site_url.rstrip('/')`：剥的是**一串**斜杠而不是一个。
// Go 若写成 strings.TrimSuffix，"https://a//" 会留下双斜杠，深链指错地方——
// 而那个串会**落进 first_read 列**并在 restore 里复现，切流前后就成了两条链接。
func TestN8ReferralLinkCorpus(t *testing.T) {
	file := loadN8Fixture(t)
	code := "QQ22WW33" // 夹具生成时用的那一个
	for _, entry := range file.ReferralCorpus {
		if !strings.HasSuffix(entry.Link, "/r/"+code) {
			t.Fatalf("夹具的 link %q 不是由码 %s 拼出来的——生成脚本换了输入面，"+
				"这一节的比对口径要跟着改", entry.Link, code)
		}
		if got := referralLink(entry.SiteURL, code); got != entry.Link {
			t.Errorf("site_url %q 的深链不符：Go=%q Python=%q", entry.SiteURL, got, entry.Link)
		}
	}
	// 闸别：必须真的有「尾部多斜杠」那一格，否则 TrimSuffix 与 TrimRight 在这一块同形。
	var slashed bool
	for _, entry := range file.ReferralCorpus {
		if strings.HasSuffix(entry.SiteURL, "//") {
			slashed = true
		}
	}
	if !slashed {
		t.Fatal("深链语料里没有以 // 结尾的 site_url：这条判据回到「看不出来」的状态")
	}
	t.Logf("深链拼装 %d 个 site_url 变体逐字重放", len(file.ReferralCorpus))
}
