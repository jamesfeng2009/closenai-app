package aiclient

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// TestClientSurfaceIsExactlyThreeMethods 是 tasks.md §R1/§R2 那句
// 「方法面＝R3 收窄后的三个，多一个都不加」的可执行形态。
//
// 判据是 V3 06:173 逐字 "Do not introduce gRPC merely because both runtimes use Protobuf."：
// 每加一个方法都要单独回答「为什么不能等 MQ 回投」（ownership.md §1 第三列）。
// 这里按**名字逐个比对**而不是只数个数——只数个数的话，把 StreamChat 换成一个
// Rerank 也能绿，而那条恰恰是 §4 实测零调用点、刻意不建的方法。
func TestClientSurfaceIsExactlyThreeMethods(t *testing.T) {
	typ := reflect.TypeOf((*Client)(nil)).Elem()
	var got []string
	for i := 0; i < typ.NumMethod(); i++ {
		got = append(got, typ.Method(i).Name)
	}
	want := []string{"ClassifySafety", "Close", "Embed", "StreamChat"} // reflect 给的是字母序
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("Client 的方法面漂了：\n got=%v\nwant=%v\n"+
			"加第四个业务方法要先回答「为什么不能等 MQ 回投」（ownership.md §0 判据 2）", got, want)
	}
}

// TestFailureCodeKindMatchesRetryClassification 把 V3 17 §8 的两类逐行钉住：
// transient ＝ RabbitMQ temporary failure / AI provider timeout / dependency unavailable；
// permanent ＝ malformed protobuf / unsupported schema / invalid ownership / invalid candidate。
//
// 自证：如果哪天有人把 Malformed 挪进 transient（于是畸形输出被重试而不是走 reject），
// 这一条会红，而那条改动的后果是 16:160 禁止的「blindly retry」。
func TestFailureCodeKindMatchesRetryClassification(t *testing.T) {
	transient := []FailureCode{
		FailureDeadline, FailureTransport, FailureRateLimited,
		FailureBreakerOpen, FailureUnavailable, FailureDisabled,
	}
	permanent := []FailureCode{FailureMalformed, FailureRejected}
	for _, c := range transient {
		if c.Kind() != FailureTransient {
			t.Errorf("%s 应归 transient（V3 17 §8）", c)
		}
	}
	for _, c := range permanent {
		if c.Kind() != FailurePermanent {
			t.Errorf("%s 应归 permanent：重试不会变好（16:160 do not blindly retry）", c)
		}
	}
	// 自证两条名单盖住了全部原因码，否则新增一个码就落在测不到的缝里。
	seen := map[FailureCode]bool{FailureNone: true}
	for _, c := range append(transient, permanent...) {
		seen[c] = true
	}
	all := []FailureCode{FailureDisabled, FailureBreakerOpen, FailureDeadline, FailureTransport,
		FailureRateLimited, FailureMalformed, FailureRejected, FailureUnavailable}
	for _, c := range all {
		if !seen[c] {
			t.Fatalf("%s 没进分类表：新原因码必须显式归 transient 或 permanent", c)
		}
	}
}

// TestFallbackRotationMatchesPythonCodePointCount 打的是这一批最容易「看起来一样」的坑：
//
//	`_fallback_reply`（message_generator.py:453-458）用 `len(user_input) % len(templates)` 轮转，
//	而 Python 的 len(str) 数**码点**、Go 的 len(string) 数**字节**。
//
// 选「我今天」做靶：码点 3 ％4＝**3**，字节 9 ％4＝**1**——按字节实现会挑到另一条模板，
// 而两个结果都是通顺的中文，没有任何用户可见的错误。
func TestFallbackRotationMatchesPythonCodePointCount(t *testing.T) {
	const input = "我今天"
	if len(input) == 3 {
		t.Fatalf("这条测的前提变了：Go 的 len(%q) 应为 9 字节", input)
	}
	got, ok := FallbackReply(input, "zh")
	if !ok {
		t.Fatal("zh 的降级模板取不到")
	}
	templates := fallbackCorpus["zh"]
	if len(templates) != 4 {
		t.Fatalf("夹具前提变了：zh 的模板应有 4 条，实测 %d（改源要同步改这条测）", len(templates))
	}
	if got != templates[3] {
		t.Errorf("轮转按字节数走了：got=%q templates[1]=%q，应为 templates[3]=%q",
			got, templates[1], templates[3])
	}
	// 反证：按字节数取会拿到**另一条**，否则这条测不构成闸。
	if templates[1] == templates[3] {
		t.Fatal("夹具里 zh 的第 2 条与第 4 条相同，这条测失去判别力")
	}
}

// TestFallbackCoversEveryLocale 是 copy.py:26-28 那条 15 语言纪律在 Go 侧的副本：
// 每个 locale 都必须能取到非空模板，且 `locale_for` 的归一表与 domain 共用同一张
// （CopySupportedLocales / CopyLocaleFor，mood.go:173-190）。
//
// 空数组不是「少一句文案」而是**除零**：t_list 兜底返回 []，`len(input) % 0` 当场抛，
// 而它发生在降级路径上——原本要兜底的那次调用反而炸掉。
func TestFallbackCoversEveryLocale(t *testing.T) {
	for _, locale := range domain.CopySupportedLocales {
		got, ok := FallbackReply("测试", locale)
		if !ok || strings.TrimSpace(got) == "" {
			t.Errorf("%s 取不到降级模板（ok=%v got=%q）", locale, ok, got)
		}
		if len(fallbackCorpus[locale]) != len(fallbackCorpus["en"]) {
			t.Errorf("%s 的模板条数 %d ≠ en 的 %d：轮转下标会分叉",
				locale, len(fallbackCorpus[locale]), len(fallbackCorpus["en"]))
		}
	}
	// 未知语言码回退 en（copy.py:33-41），不能回退成「没有模板」。
	if v, ok := FallbackReply("x", "sw"); !ok || v != fallbackCorpus["en"][1%len(fallbackCorpus["en"])] {
		t.Errorf("未知语言码没回退到 en 轮转：ok=%v v=%q", ok, v)
	}
}

// TestSSEGoldenReplay 逐字节回放 sse_event_golden.json。
//
// 期望值**不是手打的**：scripts/gen_go_ai_side.py 调 routers/chat.py:39-42 的
// `_sse_event()` 现录，所以这条测比的是「Go 的输出 == Python 真函数的输出」。
// 五处最容易凭空写对的细节都被夹具覆盖：
//
//	· 分隔符 `", "` / `": "`（Go 的 encoding/json 不这么写）
//	· ensure_ascii：非 ASCII 转 \uXXXX，星平面拆代理对（Go 默认输出裸 UTF-8）
//	· `<` `>` `&` **不**转义（Go 默认转义成 \u003c 之类）
//	· 0x7f / 0x00 / 0x85 转义（Go 的默认编码器对 0x7f 与 0x85 原样输出，NUL 更是要截断）
//	· U+2028 / U+2029 转义（它们在 JS 里是行终止符，Python 转而 Go 不转）
func TestSSEGoldenReplay(t *testing.T) {
	type mem struct {
		ID       string `json:"id"`
		Content  string `json:"content"`
		Category string `json:"category"`
	}
	type payload struct {
		Content  string `json:"content"`
		Memories []mem  `json:"memories"`
		Code     int    `json:"code"`
		Message  string `json:"message"`
	}
	var golden struct {
		Note  string `json:"note"`
		Cases []struct {
			Event   string          `json:"event"`
			Payload json.RawMessage `json:"payload"`
			Line    string          `json:"line"`
		} `json:"cases"`
	}
	path := filepath.Join("..", "..", "testdata", "sse_event_golden.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("读不到夹具 %s（先跑 scripts/gen_go_ai_side.py）：%v", path, err)
	}
	if err := json.Unmarshal(raw, &golden); err != nil {
		t.Fatalf("夹具不是合法 JSON：%v", err)
	}
	// 13 ＝ 录制脚本里的用例数（四类事件 × 五个转义面 + 两个错误码）。
	// 少了就是夹具被裁过——这条闸的判别力全靠用例面，砍一条等于砍掉一个转义分支的覆盖。
	if len(golden.Cases) < 13 {
		t.Fatalf("夹具只剩 %d 条，判据面不够（四类事件 × 转义面）", len(golden.Cases))
	}
	if !strings.Contains(golden.Note, "_sse_event") {
		t.Errorf("夹具来源标注变了：%q（期望值只能来自真 _sse_event）", golden.Note)
	}

	for i, c := range golden.Cases {
		var p payload
		if err := json.Unmarshal(c.Payload, &p); err != nil {
			t.Fatalf("case %d 的 payload 解不开：%v", i, err)
		}
		var chunk ChatChunk
		switch c.Event {
		case "token":
			chunk = ChatChunk{Content: p.Content}
		case "memory_context":
			if len(p.Memories) == 0 {
				t.Fatalf("case %d 是空 memories 的 memory_context：现网产不出它\n"+
					"（chat.py:103 的 if memories: 不发事件，proto 的 repeated 字段又没有 presence），\n"+
					"录制脚本已按这条删掉该用例", i)
			}
			for _, m := range p.Memories {
				chunk.Memories = append(chunk.Memories, MemoryContext{ID: m.ID, Content: m.Content, Category: m.Category})
			}
		case "done":
			chunk = ChatChunk{Finish: FinishDone}
		case "error":
			chunk = ChatChunk{Finish: FinishError, ErrorCode: p.Code, HasErrorCode: true, ErrorMessage: p.Message}
		default:
			t.Fatalf("case %d 的事件类型 %q 不在映射表里（加一种要同时改 sse.go 与录制脚本）", i, c.Event)
		}
		if got := SSELine(chunk); got != c.Line {
			t.Errorf("case %d（%s）字节不同步：\n got=%q\nwant=%q", i, c.Event, got, c.Line)
		}
	}
}

// TestSSEMapsDegradedAndBlockedToKnownEventNames 守的是「不给已发布客户端造新事件名」：
// DEGRADED / BLOCKED 都落成 done，降级的那句话术落成 token。
//
// 两条都写成**整行**相等而不是前缀/后缀：这两个事件的字节面就是前端逐行 split 的依据，
// 前缀漏掉尾巴、后缀漏掉键序（上一版就是这么写的，红得毫无信息量）。
func TestSSEMapsDegradedAndBlockedToKnownEventNames(t *testing.T) {
	done := "data: " + `{"type": "done"}` + "\n\n"
	if got := SSELine(ChatChunk{Finish: FinishDegraded}); got != done {
		t.Errorf("DEGRADED 应落成 done 事件，got=%q", got)
	}
	if got := SSELine(ChatChunk{Finish: FinishBlocked}); got != done {
		t.Errorf("BLOCKED 不带话术时只有一条 done，got=%q", got)
	}
	lines := SSELines(ChatChunk{Finish: FinishBlocked, Content: "我很在意你的安全"})
	want := []string{
		"data: {\"type\": \"token\", \"content\": \"\\u6211\\u5f88\\u5728\\u610f\\u4f60\\u7684\\u5b89\\u5168\"}\n\n",
		done,
	}
	if len(lines) != 2 || lines[0] != want[0] || lines[1] != want[1] {
		t.Errorf("BLOCKED 带话术要展开成 token＋done 两条且逐字节相等：\n got=%q\nwant=%q", lines, want)
	}
	// 未知收尾值不发事件（发错 type 会被客户端当真，不发只是少一句收尾）。
	if got := SSELine(ChatChunk{Finish: Finish("从没见过")}); got != "" {
		t.Errorf("未知收尾值不该发事件，got=%q", got)
	}
}

// TestMemoryEventNeedsNonEmptyMemories 钉住「夹具里为什么没有空 memories」这条裁决，
// 顺手把中间块的两个判据一次说清：
//
//	memory_context 的充要条件＝memories 非空（chat.py:103 `if memories:`，
//	  而 proto 的 repeated 字段没有 presence ⇒ 空列表与「这块不是 memory 事件」不可区分）
//	token 这块**没有**非空守卫（chat.py:116 对每个 chunk 无条件发），所以空串也要发
//
// 第二条最容易被「顺手加个 if content != ""」改坏：看着像清理，
// 实际是吞掉现网每流都会有的空块，而客户端的收尾判据依赖 done 之前的块数时就会变。
func TestMemoryEventNeedsNonEmptyMemories(t *testing.T) {
	if got := SSELine(ChatChunk{Content: "好", Memories: []MemoryContext{}}); !strings.HasPrefix(got, `data: {"type": "token"`) {
		t.Errorf("空 memories 不该抢走 token 事件：got=%q", got)
	}
	if got := SSELine(ChatChunk{Content: "", Memories: []MemoryContext{{ID: "m1"}}}); !strings.HasPrefix(got, `data: {"type": "memory_context"`) {
		t.Errorf("memories 非空时优先 memory_context（哪怕 content 为空）：got=%q", got)
	}
	empty := "data: " + `{"type": "token", "content": ""}` + "\n\n"
	if got := SSELine(ChatChunk{}); got != empty {
		t.Errorf("空中间块必须照 chat.py:116 无条件发 token：\n got=%q\nwant=%q", got, empty)
	}
}

// TestOnlyAIClientImportsTheRPCSurface 是 ownership.md §5 的边界纪律落闸。
//
// 两条黑名单**各有各的射程**，不许并成一张表：
//   - `google.golang.org/grpc` ⇒ 只许 `internal/aiclient/`。这是「谁握着 RPC 出口」，
//     入站面是 AMQP 不是 RPC，R5-② 没有、也不该给它开这一条。
//   - `closenai-app/go-core/gen/` ⇒ 许 `internal/aiclient/` 与 `internal/messaging/inbound/`。
//     后者按 D-R5-4 用 `protojson` 把线上字节解进生成物（生成物是跨语言契约的事实源，
//     手抄第二份 struct 迟早漂；而 protojson 默认拒未知字段，正好挡住 `Candidate` 的
//     `reserved 3,4,5`）。ownership.md §5 的 2026-10-16 那一行登记的就是这次放开。
//
// 为什么单独来一遍而不信 domain/arch_test.go：那份闸的射程是 **domain 一个包**
// （transportImports 黑名单只在 domain 的文件上跑）。R1 加进 grpc/protobuf 依赖之后，
// 「别的运行时包随手 import gen/」是它管不到的——而那种写法一旦散开，
// R2 的接口面就退化成一层没人用的装饰。
//
// 自证：两条黑名单各自必须在**允许面**里命中过，否则这条测又是在数空气。
func TestOnlyAIClientImportsTheRPCSurface(t *testing.T) {
	// 遍历根＝go module 根（不是 internal/）：这条闸要管的是**全仓**，
	// cmd/ 里的装配代码 import gen/ 和 domain 里 import grpc 是同一种越界。
	root := filepath.Join("..", "..")
	rules := []struct {
		path    string
		allowed map[string]bool
		why     string
	}{
		{"google.golang.org/grpc", map[string]bool{"internal/aiclient": true},
			"AI 出口的 gRPC client 只许在 internal/aiclient/"},
		{"closenai-app/go-core/gen/", map[string]bool{"internal/aiclient": true,
			"internal/messaging/inbound": true},
			"proto 生成物只许握着传输边界的两个包（RPC 出口＋AMQP 入站），domain 只收已解码对象"},
	}
	var scanned int

	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			switch info.Name() {
			case "gen", "tools", "testdata", ".git", "node_modules", "vendor":
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		rel = filepath.ToSlash(rel)
		dir := filepath.ToSlash(filepath.Dir(rel))
		scanned++
		src, readErr := os.ReadFile(path)
		if readErr != nil {
			return readErr
		}
		for _, r := range rules {
			if r.allowed[dir] {
				continue // 允许面内部当然可以，跳过它自己的文件
			}
			if strings.Contains(string(src), r.path) {
				t.Errorf("%s  import 了 %q：%s", rel, r.path, r.why)
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("扫不动：%v", err)
	}
	// 扫到的文件数也得非空：只扫到允许面那几个包的话，这条闸同样是在数空气。
	if scanned < 20 {
		t.Fatalf("遍历根 %s 下只扫到 %d 个 .go 文件 ⇒ 遍历面变了，这条闸测不到越界",
			root, scanned)
	}
	// 每条黑名单必须在**允许面**里真的命中过：串写错了、包名改了，都会让上面那个循环
	// 一声不响地全绿（这是「闸在数空气」的第二种形态，与 R1 落闸时同一个担心）。
	for _, r := range rules {
		hits := 0
		for dir := range r.allowed {
			_ = filepath.Walk(filepath.Join(root, dir), func(path string, info os.FileInfo, err error) error {
				if err != nil || info.IsDir() {
					return err
				}
				if !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
					return nil
				}
				src, readErr := os.ReadFile(path)
				if readErr != nil {
					return readErr
				}
				if strings.Contains(string(src), r.path) {
					hits++
				}
				return nil
			})
		}
		if hits == 0 {
			t.Errorf("%q 在所有允许面里 0 命中 ⇒ 这条黑名单是空气，闸根本没在管任何东西", r.path)
		}
	}
}
