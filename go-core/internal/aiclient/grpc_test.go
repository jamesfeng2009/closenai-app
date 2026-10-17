package aiclient

import (
	"context"
	"testing"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	closenaiv1 "github.com/jamesfeng2009/closenai-app/go-core/gen/closenai/v1"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// 这一批测的是 tasks.md §R2 验收项的第一句：
// 「Go 单测用 fake 覆盖超时/429/5xx/畸形输出四类（畸形输出走 N3 reject 而非重试）」。
//
// ⚠️ 口径要说破：这里的「fake」有两层——
//	· 失败面用真 TCP 上的 stub server（四类 gRPC 状态码都得真的从线路上回来一次，
//	  否则测的是 classify() 的 switch 而不是 classify(grpc 错误) 的路径）；
//	· Embed/Safety 的**调用方行为**另有 Fake（fake.go）供上层单测用，本文件不重复那一层。
// 「Python 真的会给出这四种失败」这一条不在这里测，那是 R3 的 grpc test harness。

// TestUnaryFailureClasses 把四类（其实是六类）失败同时打在 Embed 与 ClassifySafety 上，
// 因为这两个方法**必须**对同一次失败给出不同形状：Embed 降级、Safety 拦下。
// 这一条表就是那个分叉的可执行版本。
func TestUnaryFailureClasses(t *testing.T) {
	cases := []struct {
		name      string
		rpcErr    error
		want      FailureCode
		retryable bool
	}{
		// 出处：17 §8 逐字把 "AI provider timeout" 归 Transient；429/5xx/连接失败同理。
		{"超时", status.Error(codes.DeadlineExceeded, "provider timeout"), FailureDeadline, true},
		{"429", status.Error(codes.ResourceExhausted, "quota rate limited"), FailureRateLimited, true},
		{"5xx", status.Error(codes.Internal, "handler 未捕获异常"), FailureUnavailable, true},
		{"连接失败", status.Error(codes.Unavailable, "connection refused"), FailureTransport, true},
		// permanent 面：重试不会变好（16:160 逐字 do not blindly retry）。
		{"业务拒绝", status.Error(codes.InvalidArgument, "unsupported schema_version"), FailureRejected, false},
		{"版本错配", status.Error(codes.Unimplemented, "no such rpc"), FailureMalformed, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := &stub{embedFn: func(context.Context, *closenaiv1.EmbedRequest) (*closenaiv1.EmbedResponse, error) {
				return nil, tc.rpcErr
			}, safetyFn: func(context.Context, *closenaiv1.TextInput) (*closenaiv1.SafetyResult, error) {
				return nil, tc.rpcErr
			}}
			target := startStub(t, s)
			c := newTestClient(target, noSleep)
			defer c.Close()

			// ── Embed：任何失败都是**降级**而不是错误（ownership.md §1「不得变硬失败」）
			res, err := c.Embed(context.Background(), EmbedRequest{Texts: []string{"我今天很难过"}, UserID: "u1"})
			if err != nil {
				t.Fatalf("Embed 把失败升成了硬错误（fail-soft 破了）：%v", err)
			}
			if !res.Degraded || res.Vectors != nil {
				t.Errorf("Embed 失败面必须 Degraded 且不带向量：degraded=%v vectors=%v", res.Degraded, res.Vectors)
			}
			if res.Failure != tc.want {
				t.Errorf("Embed 的原因码 got=%s want=%s", res.Failure, tc.want)
			}
			wantAttempts := 1
			if tc.retryable {
				wantAttempts = defaultRetries + 1 // 只有 transient 会重投
			}
			if res.Attempts != wantAttempts {
				t.Errorf("Embed 试了 %d 次，want=%d（transient=%v）", res.Attempts, wantAttempts, tc.retryable)
			}

			// ── ClassifySafety：同一次失败必须**拦下**
			dec, err := c.ClassifySafety(context.Background(), TextInput{Text: "我不想活了", Language: "zh"})
			if err == nil {
				t.Fatal("ClassifySafety 失败了却没返回 err")
			}
			if dec.Safety.Status != domain.SafetyUnavailable {
				t.Errorf("D-8：失败面 got=%q want=%s（超时/失败不得读成放行）", dec.Safety.Status, domain.SafetyUnavailable)
			}
			if dec.Failure != tc.want {
				t.Errorf("ClassifySafety 的原因码 got=%s want=%s", dec.Failure, tc.want)
			}
			if IsRetryable(err) != tc.retryable {
				t.Errorf("IsRetryable(%s)=%v want=%v", tc.want, IsRetryable(err), tc.retryable)
			}
			if dec.Attempts != wantAttempts {
				t.Errorf("ClassifySafety 试了 %d 次，want=%d", dec.Attempts, wantAttempts)
			}

			// 真发出去过几次 RPC：这是「重试到底发生了没有」的最后一环，
			// 只有 attempts 数字而没有对面计数的话，客户端自说自话也算「重试过」。
			if e, sf, _ := s.calls(); e != wantAttempts || sf != wantAttempts {
				t.Errorf("对面收到 Embed=%d ClassifySafety=%d 次，want=%d", e, sf, wantAttempts)
			}
		})
	}
}

// TestSafetyTimeoutIsNeverPass 是 D-8 的那一条，也是 R2 变异自证必须打红的靶：
// 把 grpc.go 的 safetyFailure 从 SafetyUnavailable 改成 SafetyOK，这一条当场红。
//
// 造法用「对面真的睡过预算」而不是塞一个假的 DeadlineExceeded：
// 前者会走完 runUnary 的重试与预算逻辑，后者只测 classify() 一行。
func TestSafetyTimeoutIsNeverPass(t *testing.T) {
	s := &stub{safetyFn: func(ctx context.Context, _ *closenaiv1.TextInput) (*closenaiv1.SafetyResult, error) {
		select {
		case <-time.After(200 * time.Millisecond):
			return &closenaiv1.SafetyResult{
				SchemaVersion: schemaVersion,
				Safety:        &closenaiv1.Safety{Status: closenaiv1.SafetyStatus_SAFETY_STATUS_OK},
			}, nil
		case <-ctx.Done():
			return nil, status.FromContextError(ctx.Err()).Err()
		}
	}}
	target := startStub(t, s)
	c := newTestClient(target, func(o *Options) {
		o.Sleep = func(context.Context, time.Duration) error { return nil }
		o.BreakerThreshold = -1
		o.SafetyDeadline = 20 * time.Millisecond // 远短于对面的 200ms
	})
	defer c.Close()

	start := time.Now()
	dec, err := c.ClassifySafety(context.Background(), TextInput{Text: "我不想活了", Language: "zh"})
	elapsed := time.Since(start)

	if err == nil {
		t.Fatal("对面睡过预算却返回 nil err")
	}
	if dec.Safety.Status == domain.SafetyOK {
		t.Errorf("超时被当放行（D-8 破了）：status=%q", dec.Safety.Status)
	}
	if dec.Safety.Status != domain.SafetyUnavailable {
		t.Errorf("status got=%q want=%s", dec.Safety.Status, domain.SafetyUnavailable)
	}
	var e *Error
	if !AsError(err, &e) || e.Cause != FailureDeadline {
		t.Errorf("原因码应该是 deadline got=%v err=%v", e, err)
	}
	// 共享预算的观测点：3 次尝试各 20ms 的话至少 60ms，而这里应当在预算用完就停。
	// 200ms 是对面的耗时——绝不该等它。
	if elapsed > 100*time.Millisecond {
		t.Errorf("等了 %v：预算像是每次尝试重新开始（want <100ms，且远小于对面的 200ms）", elapsed)
	}
	t.Logf("实测：%s 失败于 %v（预算 %v，对面睡了 200ms）", e.Cause, elapsed.Round(time.Millisecond), c.opts.SafetyDeadline)
}

// TestBackoffSequence 把 0.5s 起步、每次翻倍、上限 8s 这三个数钉住，
// 三个数全部来自 openai SDK 的 _constants（INITIAL_RETRY_DELAY / MAX_RETRY_DELAY），
// 见 grpc.go 顶部那段出处注释。
func TestBackoffSequence(t *testing.T) {
	var sleeps []time.Duration
	s := &stub{embedFn: func(context.Context, *closenaiv1.EmbedRequest) (*closenaiv1.EmbedResponse, error) {
		return nil, status.Error(codes.Unavailable, "connection refused")
	}}
	target := startStub(t, s)
	c := newTestClient(target, func(o *Options) {
		o.Sleep = func(_ context.Context, d time.Duration) error {
			sleeps = append(sleeps, d)
			return nil
		}
		o.BreakerThreshold = -1
	})
	defer c.Close()

	if _, err := c.Embed(context.Background(), EmbedRequest{Texts: []string{"a"}}); err != nil {
		t.Fatalf("Embed 应降级不报错：%v", err)
	}
	want := []time.Duration{defaultBackoffInitial, defaultBackoffInitial * 2}
	if len(sleeps) != len(want) || sleeps[0] != want[0] || sleeps[1] != want[1] {
		t.Errorf("退避序列 got=%v want=%v（Retries=%d ⇒ 间隔应为 %d 段）", sleeps, want, c.opts.Retries, c.opts.Retries)
	}

	// 上限：把初始值抬到 4s，第三段必须被 MAX 截住而不是 16s。
	var big []time.Duration
	c2 := newTestClient(target, func(o *Options) {
		o.Retries = 3
		o.BackoffInitial = 4 * time.Second
		o.BreakerThreshold = -1
		o.Sleep = func(_ context.Context, d time.Duration) error { big = append(big, d); return nil }
	})
	defer c2.Close()
	if _, err := c2.Embed(context.Background(), EmbedRequest{Texts: []string{"a"}}); err != nil {
		t.Fatalf("Embed：%v", err)
	}
	if len(big) != 3 || big[0] != 4*time.Second || big[1] != 8*time.Second || big[2] != 8*time.Second {
		t.Errorf("退避没被 %v 截住：%v", defaultBackoffMax, big)
	}
}

// TestMalformedIsRejectedNotRetried 是验收项括号里那句「畸形输出走 N3 reject 而非重试」的落点。
// 三条畸形面都从**线路**回来（不是 Go 自己造的），这样「解不出来」与「没发出去」不会混。
func TestMalformedIsRejectedNotRetried(t *testing.T) {
	embedBad := []struct {
		name string
		resp *closenaiv1.EmbedResponse
	}{
		{"条数不符（顺序即契约就废了）", &closenaiv1.EmbedResponse{
			SchemaVersion: schemaVersion, Dimensions: 2,
			Vectors: []*closenaiv1.EmbeddingVector{{Values: []float32{1, 2}}, {Values: []float32{1, 2}}},
		}},
		{"空向量", &closenaiv1.EmbedResponse{
			SchemaVersion: schemaVersion, Dimensions: 2,
			Vectors: []*closenaiv1.EmbeddingVector{{Values: nil}},
		}},
		{"维度与声明不符", &closenaiv1.EmbedResponse{
			SchemaVersion: schemaVersion, Dimensions: 3,
			Vectors: []*closenaiv1.EmbeddingVector{{Values: []float32{1, 2}}},
		}},
	}
	for _, tc := range embedBad {
		t.Run("Embed/"+tc.name, func(t *testing.T) {
			s := &stub{embedFn: func(context.Context, *closenaiv1.EmbedRequest) (*closenaiv1.EmbedResponse, error) {
				return tc.resp, nil
			}}
			target := startStub(t, s)
			c := newTestClient(target, noSleep)
			defer c.Close()

			res, err := c.Embed(context.Background(), EmbedRequest{Texts: []string{"一条"}})
			if err != nil {
				t.Fatalf("Embed 不该报错：%v", err)
			}
			if res.Failure != FailureMalformed || res.Attempts != 1 {
				t.Errorf("畸形 got=%s/%d 次，want=malformed/1 次（重试不会变好）", res.Failure, res.Attempts)
			}
			if e, _, _ := s.calls(); e != 1 {
				t.Errorf("畸形被重投了：对面收到 %d 次", e)
			}
		})
	}

	safetyBad := []struct {
		name string
		resp *closenaiv1.SafetyResult
	}{
		// 「缺 safety 字段」与「未知枚举值」都必须 fail-closed：
		// V3 15 §8 的容忍未知枚举值指的是消费方不许崩，而这里的风险是**把没见过的状态读成 OK**。
		{"没有 safety 字段", &closenaiv1.SafetyResult{SchemaVersion: schemaVersion}},
		{"未知 SafetyStatus", &closenaiv1.SafetyResult{
			SchemaVersion: schemaVersion,
			Safety:        &closenaiv1.Safety{Status: closenaiv1.SafetyStatus(999)},
		}},
	}
	for _, tc := range safetyBad {
		t.Run("ClassifySafety/"+tc.name, func(t *testing.T) {
			s := &stub{safetyFn: func(context.Context, *closenaiv1.TextInput) (*closenaiv1.SafetyResult, error) {
				return tc.resp, nil
			}}
			target := startStub(t, s)
			c := newTestClient(target, noSleep)
			defer c.Close()

			dec, err := c.ClassifySafety(context.Background(), TextInput{Text: "x"})
			if err == nil {
				t.Fatal("畸形判定必须带 err")
			}
			if dec.Safety.Status != domain.SafetyUnavailable {
				t.Errorf("got=%q want=%s：解不出判定不等于安全", dec.Safety.Status, domain.SafetyUnavailable)
			}
			_, sf, _ := s.calls()
			if dec.Failure != FailureMalformed || sf != 1 {
				t.Errorf("got=%s 对面收到 %d 次，want=malformed/1", dec.Failure, sf)
			}
		})
	}
}

// TestEmbedKeepsFailSoft 单独把「成功面」也测一遍：
// fail-soft 的另一半是「成功时别把向量弄丢」，而顺序就是这里的契约。
func TestEmbedKeepsFailSoft(t *testing.T) {
	s := &stub{embedFn: okEmbed([][]float32{{1, 2, 3, 4}, {5, 6, 7, 8}})}
	target := startStub(t, s)
	c := newTestClient(target, noSleep)
	defer c.Close()

	dim := uint32(4)
	res, err := c.Embed(context.Background(), EmbedRequest{
		Texts: []string{"第一条", "第二条"}, Dimensions: &dim,
		Model: "text-embedding-v4", UserID: "u1", Purpose: "memory_store", TraceID: "t1",
	})
	if err != nil || res.Degraded {
		t.Fatalf("成功面 got err=%v degraded=%v", err, res.Degraded)
	}
	if len(res.Vectors) != 2 || res.Vectors[0][0] != 1 || res.Vectors[1][0] != 5 {
		t.Errorf("向量顺序/内容不对：%v", res.Vectors)
	}
	if res.Dimensions != 4 || res.Model != "text-embedding-v4" {
		t.Errorf("dimensions=%d model=%q", res.Dimensions, res.Model)
	}
	if res.Attempts != 1 {
		t.Errorf("成功也记了 %d 次尝试", res.Attempts)
	}
}

// TestEmbedDegradedNeverCarriesVectors 守住 aiclient.go 里那句「Degraded=true 时 Vectors 必为 nil」
// 的不变量。调用方一旦按 `if !res.Degraded { 用 res.Vectors }` 写，这个不变量漂了就是数据丢失。
func TestEmbedDegradedNeverCarriesVectors(t *testing.T) {
	s := &stub{
		embedFn: func(context.Context, *closenaiv1.EmbedRequest) (*closenaiv1.EmbedResponse, error) {
			return nil, status.Error(codes.Unavailable, "down")
		},
	}
	target := startStub(t, s)
	c := newTestClient(target, noSleep)
	defer c.Close()

	for _, req := range []EmbedRequest{{Texts: []string{"a"}}, {}} {
		res, err := c.Embed(context.Background(), req)
		if req.Texts == nil {
			// 空 texts 是编程错误面：带 err、不降级。
			if err == nil {
				t.Error("空 texts 该报错（护栏面）")
			}
			continue
		}
		if err != nil {
			t.Fatalf("err=%v", err)
		}
		if res.Degraded && res.Vectors != nil {
			t.Errorf("Degraded 与向量同现：%v", res.Vectors)
		}
	}
	// 没接线那条路也一样：降级就是没向量。
	off := New(Options{})
	defer off.Close()
	res, err := off.Embed(context.Background(), EmbedRequest{Texts: []string{"a"}})
	if err != nil || !res.Degraded || res.Failure != FailureDisabled || res.Vectors != nil {
		t.Errorf("没接线 got=%v err=%v want={Degraded:true, Failure:ai_disabled}", res, err)
	}
}

// ---------------------------------------------------------------- 熔断

// TestBreakerOpensAndSkipsRPC 打的是这一包存在的理由之一（V3 13 §8 degrade gracefully）：
// 对面重启期间，每条用户请求都烧满 deadline 才降级，产品体感就是「整个对话卡住」，
// 而熔断打开后应当**不发 RPC**、直接降级。
func TestBreakerOpensAndSkipsRPC(t *testing.T) {
	now := time.Unix(1000, 0)
	s := &stub{embedFn: func(context.Context, *closenaiv1.EmbedRequest) (*closenaiv1.EmbedResponse, error) {
		return nil, status.Error(codes.Unavailable, "down")
	}, safetyFn: func(context.Context, *closenaiv1.TextInput) (*closenaiv1.SafetyResult, error) {
		return nil, status.Error(codes.Unavailable, "down")
	}}
	target := startStub(t, s)
	c := newTestClient(target, func(o *Options) {
		o.Sleep = func(context.Context, time.Duration) error { return nil }
		o.BreakerThreshold = 2
		o.BreakerCooldown = 60 * time.Second
		o.Now = func() time.Time { return now }
	})
	defer c.Close()

	for i := 0; i < 2; i++ {
		if _, err := c.ClassifySafety(context.Background(), TextInput{Text: "x"}); err == nil {
			t.Fatal("该失败")
		}
	}
	if open, _ := c.breaker.state(); !open {
		t.Fatalf("两次连续 transient 失败后熔断没打开（threshold=%d）", c.opts.BreakerThreshold)
	}

	before, _, _ := s.calls()
	res, err := c.Embed(context.Background(), EmbedRequest{Texts: []string{"a"}})
	if err != nil || !res.Degraded || res.Failure != FailureBreakerOpen {
		t.Errorf("熔断打开后 Embed got=%v err=%v want=breaker_open 降级", res, err)
	}
	dec, err := c.ClassifySafety(context.Background(), TextInput{Text: "x"})
	if err == nil || dec.Safety.Status != domain.SafetyUnavailable || dec.Failure != FailureBreakerOpen {
		t.Errorf("熔断打开后 Safety got=%v err=%v want=breaker_open+unavailable", dec, err)
	}
	after, _, _ := s.calls()
	if after != before {
		t.Errorf("熔断打开还发了 RPC（对面收到 %d→%d 次）——省掉这段等待就是这一包的目的", before, after)
	}

	// half-open：冷却窗口过后**由调用本身**拿走那一次试探名额（不能手动 allow()，
	// 那会把名额吃掉、后面的调用就变成「熔断拒绝」而不是「试探」）。
	now = now.Add(61 * time.Second)
	if _, err := c.ClassifySafety(context.Background(), TextInput{Text: "x"}); err == nil {
		t.Fatal("试探该失败")
	}
	if open, left := c.breaker.state(); !open || left <= 0 {
		t.Errorf("试探失败后没重新打开：open=%v 剩余=%d（不重开就等于冷却期一过就放全量流量进去）", open, left)
	}
	probeDone, _, _ := s.calls()
	if _, err := c.Embed(context.Background(), EmbedRequest{Texts: []string{"a"}}); err != nil {
		t.Fatalf("冷却期内的调用应降级不报错：%v", err)
	}
	cooling, _, _ := s.calls()
	if cooling != probeDone {
		t.Errorf("冷却期内又发了 RPC（%d→%d）", probeDone, cooling)
	}

	// 成功一次就清零：对面起来了，不能让它永远背着那两次失败。
	now = now.Add(61 * time.Second)
	s.mu.Lock()
	s.safetyFn = safety(closenaiv1.SafetyStatus_SAFETY_STATUS_OK)
	s.mu.Unlock()
	if dec, err := c.ClassifySafety(context.Background(), TextInput{Text: "x"}); err != nil || dec.Safety.Status != domain.SafetyOK {
		t.Fatalf("试探成功后没恢复：dec=%v err=%v", dec, err)
	}
	if open, _ := c.breaker.state(); open {
		t.Error("成功之后熔断还是打开的")
	}
}

// TestPermanentAndCancelDoNotOpenBreaker 是熔断的「别乱开」那一面，两条都得测：
//
//	· permanent（畸形）不计入：对面好好的，是我们的校验没过（breaker.go 文件头）；
//	· 调用方自己取消不计入：晚高峰用户关页面，不该把 AI 面熔断掉。
func TestPermanentAndCancelDoNotOpenBreaker(t *testing.T) {
	s := &stub{embedFn: func(context.Context, *closenaiv1.EmbedRequest) (*closenaiv1.EmbedResponse, error) {
		return &closenaiv1.EmbedResponse{SchemaVersion: schemaVersion, Dimensions: 3,
			Vectors: []*closenaiv1.EmbeddingVector{{Values: []float32{1, 2}}}}, nil
	}}
	target := startStub(t, s)
	c := newTestClient(target, func(o *Options) {
		o.Sleep = func(context.Context, time.Duration) error { return nil }
		o.BreakerThreshold = 1
	})
	defer c.Close()

	for i := 0; i < 3; i++ {
		res, err := c.Embed(context.Background(), EmbedRequest{Texts: []string{"a"}})
		// 先单独判「这一次的失败原因是熔断而不是畸形」：把 permanent 计进连续失败时，
		// 第 2 次起就再也到不了 FailureMalformed 这一格，通用断言只会留下一行 got=… breaker_open，
		// 读者得自己反推「熔断阈值才 1，所以是一次畸形把它开的」。这一句把因果写出来。
		if res.Failure == FailureBreakerOpen {
			t.Fatalf("第 %d 次畸形（permanent）把熔断打开了：一次字段误解不该把整条 AI 面关进小黑屋（got=%v err=%v）", i, res, err)
		}
		if err != nil || res.Failure != FailureMalformed {
			t.Fatalf("第 %d 次 got=%v err=%v", i, res, err)
		}
	}
	if open, _ := c.breaker.state(); open {
		t.Error("畸形（permanent）把熔断打开了：一次字段误解不该把整条 AI 面关进小黑屋")
	}

	// 取消面：ctx 已取消 ⇒ 失败是 transient 类，但**调用方**的原因不能计进对面。
	canceled, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := c.ClassifySafety(canceled, TextInput{Text: "x"}); err == nil {
		t.Fatal("已取消的 ctx 该失败")
	}
	if open, _ := c.breaker.state(); open {
		t.Error("调用方取消被计成了对面故障")
	}
}

// TestDisabledAndDefaults 把两件没有 stub 的事测掉：
// 「没接线」（Target 空 / Disabled）与「四组默认值没被后来人改动」。
func TestDisabledAndDefaults(t *testing.T) {
	for name, opts := range map[string]Options{
		"没配地址": {},
		"显式关掉": {Target: "127.0.0.1:59999", Disabled: true},
	} {
		t.Run(name, func(t *testing.T) {
			c := New(opts)
			defer c.Close()
			if c.opts.Disabled != opts.Disabled || c.opts.Target != opts.Target {
				t.Fatalf("New 改了 Options：%+v", c.opts)
			}
			res, err := c.Embed(context.Background(), EmbedRequest{Texts: []string{"a"}})
			if err != nil || !res.Degraded || res.Failure != FailureDisabled {
				t.Errorf("Embed got=%v err=%v want=ai_disabled 降级", res, err)
			}
			dec, err := c.ClassifySafety(context.Background(), TextInput{Text: "x"})
			if err == nil || dec.Failure != FailureDisabled || dec.Safety.Status != domain.SafetyUnavailable {
				t.Errorf("ClassifySafety got=%v err=%v want=ai_disabled+unavailable", dec, err)
			}
			// 流：不发 RPC，直接两块（模板 + DEGRADED）。能连上 59999 才叫接错了。
			ch, err := c.StreamChat(context.Background(), ChatRequest{Content: "我今天", Language: "zh"})
			if err != nil {
				t.Fatalf("StreamChat 在没接线时不该报错：%v", err)
			}
			var got []ChatChunk
			for cc := range ch {
				got = append(got, cc)
			}
			if len(got) != 2 || got[0].Finish != FinishChunk || got[1].Finish != FinishDegraded {
				t.Fatalf("没接线的流应有模板+DEGRADED 两块，got=%v", got)
			}
			if got[0].Content == "" || !containsStr(fallbackCorpus["zh"], got[0].Content) {
				t.Errorf("降级块不是副本里的模板：%q", got[0].Content)
			}
		})
	}

	// 默认值＝出处（grpc.go 顶部逐条标了来源），改动必须有理由并重录。
	c := New(Options{Target: "x"})
	defer c.Close()
	if c.opts.EmbedDeadline != defaultEmbedDeadline || c.opts.SafetyDeadline != defaultSafetyDeadline ||
		c.opts.StreamDeadline != defaultStreamDeadline {
		t.Errorf("三个 deadline 默认值漂了：%v / %v / %v", c.opts.EmbedDeadline, c.opts.SafetyDeadline, c.opts.StreamDeadline)
	}
	if c.opts.Retries != defaultRetries || c.opts.BackoffInitial != defaultBackoffInitial ||
		c.opts.BackoffMax != defaultBackoffMax {
		t.Errorf("重试三件套漂了：%d / %v / %v", c.opts.Retries, c.opts.BackoffInitial, c.opts.BackoffMax)
	}
	if c.opts.StreamBuffer != defaultStreamBuffer || c.opts.BreakerCooldown != defaultBreakerSeconds*time.Second {
		t.Errorf("缓冲/冷却漂了：%d / %v", c.opts.StreamBuffer, c.opts.BreakerCooldown)
	}
	open, _ := c.breaker.state()
	if open || c.breaker.threshold != defaultBreakerCount {
		t.Errorf("熔断阈值漂了：%d（open=%v）", c.breaker.threshold, open)
	}
}

// TestOptionSentinels 是今天踩出来的那条坑的闸：withDefaults 原先写的是
// `if o.Retries < 0 { o.Retries = 0 }`，于是零值 0 一路直通 ⇒
// **默认值 2 从来没生效过**，等于把 openai SDK 已经给现网的两次重投悄悄砍成零次
// （出处：openai/_constants.py DEFAULT_MAX_RETRIES=2）。熔断阈值同理，0 也被当成「关掉」。
//
// 现在的口径：0 ＝ 用默认，-1 ＝ 显式关掉。这两个数各有一个观测点，缺一侧都会红。
func TestOptionSentinels(t *testing.T) {
	cases := []struct {
		name             string
		retries, breaker int
		wantRetries      int
		breakerOff       bool
		wantBreaker      int
	}{
		{"都没设＝用默认", 0, 0, defaultRetries, false, defaultBreakerCount},
		{"显式关重试", -1, 0, 0, false, defaultBreakerCount},
		{"显式关熔断", 0, -1, defaultRetries, true, -1},
		{"显式给值", 5, 3, 5, false, 3},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c := New(Options{Target: "x", Retries: tc.retries, BreakerThreshold: tc.breaker})
			defer c.Close()
			if c.opts.Retries != tc.wantRetries {
				t.Errorf("Retries got=%d want=%d", c.opts.Retries, tc.wantRetries)
			}
			if c.breaker.threshold != tc.wantBreaker {
				t.Errorf("threshold got=%d want=%d", c.breaker.threshold, tc.wantBreaker)
			}
			// threshold<=0 时 allow() 恒真且不计数，这才叫「关掉」。
			if tc.breakerOff {
				for i := 0; i < 20; i++ {
					if !c.breaker.allow() {
						t.Fatal("关掉熔断后还会拒绝请求")
					}
					c.breaker.recordTransient()
				}
				if open, _ := c.breaker.state(); open {
					t.Error("关掉熔断后 state 还是打开的")
				}
			}
		})
	}
}

func containsStr(hay []string, needle string) bool {
	for _, s := range hay {
		if s == needle {
			return true
		}
	}
	return false
}

// TestSafetyStatusDecodeCoversFourStates 是 D-G17 在客户端那侧的落点：proto 的四个合法状态
// 各自翻译成对应的 Go 值，且 **unsafe 不许翻成 crisis**。
//
// 为什么 domain 那侧已经对过值集，这里还要再测一遍：`toDomainSafetyStatus` 是 grpc 字节进
// Go 之后的第一道翻译，漏一个 case 就走 default ⇒ 违规内容以「判定缺失」的身份被拦下。
// 拦是对的，身份是错的——而两道 gate 的措辞与指标面就按这个身份计数。
//
// want 一律写字面量而不是 `domain.SafetyXxx`：写常量等于让被测那侧自己证明自己没写错，
// 字面量才指得回 `contracts/proto/closenai_common.proto:84-101`。
func TestSafetyStatusDecodeCoversFourStates(t *testing.T) {
	cases := []struct {
		name string
		got  closenaiv1.SafetyStatus
		want domain.SafetyStatus
	}{
		{"OK", closenaiv1.SafetyStatus_SAFETY_STATUS_OK, "ok"},
		{"CRISIS", closenaiv1.SafetyStatus_SAFETY_STATUS_CRISIS, "crisis"},
		{"UNSAFE", closenaiv1.SafetyStatus_SAFETY_STATUS_UNSAFE, "unsafe"},
		{"UNAVAILABLE", closenaiv1.SafetyStatus_SAFETY_STATUS_UNAVAILABLE, "unavailable"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := &stub{safetyFn: safety(tc.got)}
			target := startStub(t, s)
			c := newTestClient(target, noSleep)
			defer c.Close()

			dec, err := c.ClassifySafety(context.Background(), TextInput{Text: "x"})
			if err != nil {
				t.Fatalf("四个合法状态都不该带 err：%v", err)
			}
			if dec.Safety.Status != tc.want {
				t.Errorf("status got=%q want=%q", dec.Safety.Status, tc.want)
			}
		})
	}
}
