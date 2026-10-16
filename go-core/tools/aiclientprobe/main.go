// Command aiclientprobe 是 R3-④ 跨语言互操作闸的 Go 那一半（沿 tools/protocross 的形态：
// Python 起真 gRPC server，本命令用**真 aiclient** 连上去打场景，结果以 JSON 回吐）。
//
// 为什么要有这一层：R2 的四类失败（超时／429／5xx／畸形）是在 Go 进程内的 bufconn 假 server 上
// 测的，那验的是「本包的 classify 表对不对」；而两边真正上线时中间隔着 gRPC 编码、
// HTTP/2 流控和一套 Python handler。只有把 R2 的 client 接到 R3 的 server 上，
// 「Python abort 的状态码在 Go 侧落成哪个 Kind／哪个 Cause」这件事才算被测过。
//
// 本命令不做断言——断言在 backend/tests/test_r3_grpc_server_interop.py，
// 这样期望值只有一份（Python 侧），Go 侧只负责如实报告观测值。
//
// 跑法（由 pytest 自动起停；手工跑法留在这里）：
//
//	go run ./tools/aiclientprobe -target 127.0.0.1:51234 -cases embed_ok,safety_crisis_maps
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/aiclient"
)

type chunkView struct {
	Content   string `json:"content"`
	Finish    string `json:"finish"`
	Memories  int    `json:"memories"`
	ErrorCode int    `json:"errorCode"`
	HasCode   bool   `json:"hasErrorCode"`
	Failure   string `json:"failure"`
}

// result 是一次场景的**观测值**（不是断言）：期望值只写在 pytest 那一侧，本命令不许顺手判。
// 字段按 op 复用——同名在不同 op 里的含义写在各自的 fill* 函数里，不在此处再抄一遍。
type result struct {
	Case       string      `json:"case"`
	Err        string      `json:"err"`
	Kind       string      `json:"kind"`
	Failure    string      `json:"failure"`
	Attempts   int         `json:"attempts"`
	ElapsedMS  int64       `json:"elapsedMs"`
	Status     string      `json:"status"`
	Degraded   bool        `json:"degraded"`
	Model      string      `json:"model"`
	Vectors    int         `json:"vectors"`
	Dimensions uint32      `json:"dimensions"`
	Reason     string      `json:"reason"`
	Matched    []string    `json:"matched"`
	Categories []string    `json:"categories"`
	Advice     bool        `json:"adviceBlocked"`
	Chunks     []chunkView `json:"chunks"`
	SSE        []string    `json:"sse"`
	Note       string      `json:"note"`
}

func main() {
	target := flag.String("target", "", "gRPC target，形如 127.0.0.1:51234")
	cases := flag.String("cases", "", "逗号分隔的场景名")
	retries := flag.Int("retries", -1, "额外重投次数；-1＝显式关掉重试，2＝走包内默认口径")
	breaker := flag.Int("breaker", -1, "熔断阈值；-1＝关掉熔断器")
	embedMs := flag.Int("embedDeadlineMs", 0, "0＝用包内默认（10s）")
	safetyMs := flag.Int("safetyDeadlineMs", 0, "0＝用包内默认（800ms）")
	streamMs := flag.Int("streamDeadlineMs", 0, "0＝用包内默认（60s）")
	flag.Parse()

	if *target == "" || *cases == "" {
		fmt.Fprintln(os.Stderr, "必须给 -target 与 -cases")
		flag.Usage()
		os.Exit(2)
	}

	opts := aiclient.Options{
		Target:           *target,
		Retries:          *retries,
		BreakerThreshold: *breaker,
	}
	// 只给非零覆盖：0 在 withDefaults 里意味着「用默认」，不是「不设」。
	if *embedMs > 0 {
		opts.EmbedDeadline = time.Duration(*embedMs) * time.Millisecond
	}
	if *safetyMs > 0 {
		opts.SafetyDeadline = time.Duration(*safetyMs) * time.Millisecond
	}
	if *streamMs > 0 {
		opts.StreamDeadline = time.Duration(*streamMs) * time.Millisecond
	}

	c := aiclient.New(opts)
	defer c.Close()

	out := struct {
		Results []result `json:"results"`
	}{}
	for _, name := range strings.Split(*cases, ",") {
		name = strings.TrimSpace(name)
		if name == "" {
			continue
		}
		out.Results = append(out.Results, run(c, name))
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(out); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run(c *aiclient.GRPC, name string) (r result) {
	ctx := context.Background()
	r = result{Case: name, Matched: []string{}, Categories: []string{}, Chunks: []chunkView{}, SSE: []string{}}
	started := time.Now()
	// ⚠️ 返回值必须是**具名**的（`r result`）：早先写成局部变量 + `return r`，
	// defer 改的是已经被复制走的那个 r，elapsedMs 永远是 0——本机实测踩过。
	defer func() { r.ElapsedMS = time.Since(started).Milliseconds() }()

	switch name {
	case "embed_ok":
		res, err := c.Embed(ctx, aiclient.EmbedRequest{
			Texts: []string{"第一条", "第二条"}, UserID: "u-1", Purpose: "interop.embed",
		})
		fillEmbed(&r, res, err)

	case "embed_zero_dims_rejected":
		// dimensions=0 是 Python handler 的 INVALID_ARGUMENT 面（落不进 HALFVEC(512)）。
		dim := uint32(0)
		res, err := c.Embed(ctx, aiclient.EmbedRequest{Texts: []string{"a"}, Dimensions: &dim})
		fillEmbed(&r, res, err)

	case "embed_429_retried":
		res, err := c.Embed(ctx, aiclient.EmbedRequest{Texts: []string{"a"}, Purpose: "interop.embed"})
		fillEmbed(&r, res, err)

	case "embed_500_transient":
		res, err := c.Embed(ctx, aiclient.EmbedRequest{Texts: []string{"a"}, Purpose: "interop.embed"})
		fillEmbed(&r, res, err)

	case "embed_malformed_count":
		res, err := c.Embed(ctx, aiclient.EmbedRequest{Texts: []string{"a", "b", "c"}})
		fillEmbed(&r, res, err)

	case "breaker_open_second_call":
		// 连续两次 transient 之后，第三次应当根本不发请求（阈值由 -breaker 给）。
		first, err1 := c.Embed(ctx, aiclient.EmbedRequest{Texts: []string{"a"}})
		_, _ = c.Embed(ctx, aiclient.EmbedRequest{Texts: []string{"b"}})
		third, err3 := c.Embed(ctx, aiclient.EmbedRequest{Texts: []string{"c"}})
		fillEmbed(&r, third, err3)
		r.Note = fmt.Sprintf("firstFailure=%s attempts=%d err=%v", first.Failure, first.Attempts, err1 != nil)

	case "safety_ok_maps":
		d, err := c.ClassifySafety(ctx, aiclient.TextInput{Text: "今天天气不错", Language: "zh"})
		fillSafety(&r, d, err)

	case "safety_crisis_maps":
		d, err := c.ClassifySafety(ctx, aiclient.TextInput{Text: "我已经不想活了", Language: "zh"})
		fillSafety(&r, d, err)

	case "safety_blank_maps_server_side":
		// 空文本的判定留在服务端：Go 不短路，所以这一格必须真的产生一次 RPC（attempts>=1）。
		d, err := c.ClassifySafety(ctx, aiclient.TextInput{Text: "   ", Language: "zh"})
		fillSafety(&r, d, err)

	case "safety_slow_is_unavailable":
		d, err := c.ClassifySafety(ctx, aiclient.TextInput{Text: "一句正常的话", Language: "zh"})
		fillSafety(&r, d, err)

	case "safety_429_never_ok":
		d, err := c.ClassifySafety(ctx, aiclient.TextInput{Text: "一句正常的话", Language: "zh"})
		fillSafety(&r, d, err)

	case "safety_unknown_scope_rejected":
		d, err := c.ClassifySafety(ctx, aiclient.TextInput{Text: "a", Language: "zh", Scope: "all"})
		fillSafety(&r, d, err)

	case "safety_out_of_contract_status":
		d, err := c.ClassifySafety(ctx, aiclient.TextInput{Text: "a", Language: "zh"})
		fillSafety(&r, d, err)

	case "stream_ok":
		fillStream(&r, c, ctx, aiclient.ChatRequest{UserID: "u-1", Content: "今天过得怎样", Language: "zh"})

	case "stream_overlong_rejected":
		fillStream(&r, c, ctx, aiclient.ChatRequest{
			UserID: "u-1", Content: strings.Repeat("很", 5000), Language: "zh",
		})

	case "stream_429_midway":
		fillStream(&r, c, ctx, aiclient.ChatRequest{UserID: "u-1", Content: "a", Language: "zh"})

	case "stream_slow_degraded":
		fillStream(&r, c, ctx, aiclient.ChatRequest{UserID: "u-1", Content: "a", Language: "zh"})

	case "stream_malformed_same_chunk":
		fillStream(&r, c, ctx, aiclient.ChatRequest{UserID: "u-1", Content: "a", Language: "zh"})

	case "stream_malformed_done":
		fillStream(&r, c, ctx, aiclient.ChatRequest{UserID: "u-1", Content: "a", Language: "zh"})

	default:
		r.Err = "unknown case"
	}
	return r
}

func fillEmbed(r *result, res aiclient.EmbedResult, err error) {
	r.Failure = string(res.Failure)
	r.Attempts = res.Attempts
	r.Model = res.Model
	r.Dimensions = res.Dimensions
	r.Vectors = len(res.Vectors)
	// fail-soft 的载体：Degraded=true 表示「这次没向量但调用方应当继续」，err 恒 nil
	r.Degraded = res.Degraded
	r.Status = "degraded"
	if !res.Degraded {
		r.Status = "ok"
	}
	r.Err = errText(err)
	// Embed 的失败面**不带** err（那是它的设计），所以 kind 只能从原因码反射出来。
	// 反过来 ClassifySafety 一定给 err——两侧形状不同这件事本身就在 V3 17 §8 的两类里，
	// 本命令如实报告，不许为了字段好看把 Embed 也改成返回 err。
	r.Kind = kindOf(r.Failure, err)
}

func fillSafety(r *result, d aiclient.SafetyDecision, err error) {
	r.Status = string(d.Safety.Status)
	r.Failure = string(d.Failure)
	r.Attempts = d.Attempts
	r.Reason = d.Reason
	r.Matched = orEmpty(d.Matched)
	r.Categories = orEmpty(d.Safety.Categories)
	r.Advice = d.AdviceBlocked
	r.Err = errText(err)
	r.Kind = kindOf(r.Failure, err)
}

func fillStream(r *result, c *aiclient.GRPC, ctx context.Context, req aiclient.ChatRequest) {
	ch, err := c.StreamChat(ctx, req)
	r.Err = errText(err)
	if ch == nil {
		return
	}
	for chunk := range ch {
		r.Chunks = append(r.Chunks, chunkView{
			Content: chunk.Content, Finish: string(chunk.Finish), Memories: len(chunk.Memories),
			ErrorCode: chunk.ErrorCode, HasCode: chunk.HasErrorCode, Failure: string(chunk.Failure),
		})
		// SSE 面一并带出：R2 的透传映射接上真 server 字节之后，事件名与载荷仍然是产品契约。
		for _, line := range aiclient.SSELines(chunk) {
			r.SSE = append(r.SSE, line)
		}
	}
	if len(r.Chunks) == 0 {
		// StreamChat 的契约是「channel 必关且最后一块 Finish 非空」，走到这里就是违约，
		// 如实报给 pytest 让它红，本命令不自己补一块。
		r.Kind = "stream"
		r.Status = "no-chunks"
		return
	}
	last := r.Chunks[len(r.Chunks)-1]
	r.Kind = "stream"
	r.Failure = last.Failure
	r.Attempts = len(r.Chunks)
	r.Status = last.Finish
}

func errText(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

// kindOf 给出 transient / permanent。优先用 err 里的 *Error.Kind（判定最直接），
// Embed 那条 fail-soft 路径 err 恒 nil，就退回到原因码的 Kind()。
func kindOf(failure string, err error) string {
	var e *aiclient.Error
	if aiclient.AsError(err, &e) {
		if e.Kind == aiclient.FailureTransient {
			return "transient"
		}
		return "permanent"
	}
	if failure == "" {
		return ""
	}
	if aiclient.FailureCode(failure).Kind() == aiclient.FailureTransient {
		return "transient"
	}
	return "permanent"
}

func orEmpty(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}
