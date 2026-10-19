// Command dlqreplay 是死信面的人工巡检与重放工具（T2-05，Baseline §13 点名要的那件）。
//
// 跑法：
//
//	dlqreplay                                  // = -list：全部队列的深度／最老年龄／x-death 摘要
//	dlqreplay -list -queue dlq.ai.observation.extract
//	dlqreplay -replay -queue dlq.ai.observation.extract -limit 100
//	dlqreplay -purge  -queue dlq.ai.observation.extract -confirm dlq.ai.observation.extract
//
// ⚠️ 三条形状的取舍，都有判据：
//  1. **stdout 只有一行 JSON**，日志一律走 stderr。这个工具的产物要能被 pytest 的进程闸
//     解析（沿 tools/r5interop 的同一形状），而一行日志混进 stdout 就会把那次解析打成
//     「解析失败」而不是「值不对」——后者才有诊断价值。
//  2. **退出码只有两种**：0＝这一档按预期跑完了，1＝没跑完（参数不合法、连不上 broker、
//     中途失败）。没有「警告退 2」那一档：运维脚本与 CI 判的是成与不成，不是程度。
//  3. **`-purge` 在没有逐字相等的 `-confirm` 时拒绝，且拒绝发生在拨号之前**。
//     清空是不可逆的；把校验放在连接之后等于「先建立一次能毁掉东西的会话，再问要不要」。
//
// 地址只有一个键：`CLOSEN_AMQP_URL`，与两侧 worker、出箱、入站同一个（D-R5-9）。
// 本工具**不是生产入口**，也不进部署：它由人敲，或由 CI 的进程闸敲。
//
// ⚠️ 第三条形状约束：本工具**只读拓扑、不改拓扑**。取深度走 `queue.declare passive`
// （`Broker.Inspect`），一次 `amqp.Declare` 都不做——巡检顺手把队列参数改了，
// 表现是下一次 worker 启动撞 PRECONDITION_FAILED，而那天没人改过代码。
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"os"
	"strings"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/amqp"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/dlq"
)

const (
	modeList   = "list"
	modeReplay = "replay"
	modePurge  = "purge"
)

type options struct {
	mode    string
	queues  []string
	limit   int
	confirm string
	timeout time.Duration
}

func main() {
	log.SetOutput(os.Stderr)
	log.SetPrefix("dlqreplay: ")

	if err := execute(os.Args[1:]); err != nil {
		// 参数不合法、连不上 broker、中途失败都走这一格：stdout 是产物面，
		// 那里出现的东西必须是一次真巡检的结果，而不是「我以为我在巡检」。
		// 错误走 stderr＋退出码 1（文件头第 2 条：只有成与不成两种）。
		fmt.Fprintln(os.Stderr, "dlqreplay:", err)
		os.Exit(1)
	}
}

// execute 分成这一层只为一个原因：连接的关闭需要 defer，而 os.Exit 会跳过 defer。
// 主函数只负责把「错误」翻译成「退出码」。
func execute(argv []string) error {
	opts, err := parse(argv)
	if err != nil {
		return err
	}

	ctx, cancel := context.WithTimeout(context.Background(), opts.timeout)
	defer cancel()

	if opts.purgeConfirmPrecheck() {
		// -purge 的确认串在拨号之前先判：见文件头第 3 条。
		if _, err := dlq.Purge(ctx, noopBroker{}, opts.queues[0], opts.confirm); err != nil {
			return err
		}
	}

	url := amqpURL()
	if url == "" {
		return errors.New("CLOSEN_AMQP_URL 未设置 ⇒ 本工具一条都没跑，别把它当绿")
	}
	sess, err := amqp.Dial(ctx, url, 10*time.Second)
	if err != nil {
		return err
	}
	// 连接挂在 defer 上：emit 之后再退出也会走到这里，句柄不留给 OS 收尸。
	// 巡检工具跑一次就结束，但「结束」和「泄漏」是两件事，这里要的是前者。
	defer func() { _ = sess.Close() }()

	out, runErr := run(ctx, opts, dlq.NewBrokerAdapter(sess.Channel()))
	if out != nil {
		// 半途而废的那一轮也要把**已经看到的**数字摊出来，所以报告先出、错误后返。
		if err := emit(out); err != nil {
			return err
		}
	}
	return runErr
}

// amqpURL 取 broker 地址。它是本工具唯一的键（D-R5-9：两侧 worker、出箱、入站同一个），
// 没给值时报的是「一条都没跑」而不是「跑出来是空」——后者会被读成绿灯。
func amqpURL() string {
	return strings.TrimSpace(os.Getenv("CLOSEN_AMQP_URL"))
}

// purgeConfirmPrecheck 只服务一件事：把「-purge 先验确认串」这件事写成一个能读的条件。
// 用 noopBroker 走一次 dlq.Purge 的判定面，是为了**不复制那条判据**——
// 工具里再写一遍「confirm != queue 就拒」，就会出现两处口径，而其中一处一定会漂。
func (o options) purgeConfirmPrecheck() bool {
	return o.mode == modePurge && len(o.queues) == 1
}

// noopBroker 是给那次预检用的空实现：确认串不合法时它一次都不会被叫到；
// 真被叫到说明预检写错了，报错比静默通过好。
type noopBroker struct{}

func (noopBroker) Inspect(context.Context, string) (int, int, error) {
	return 0, 0, errors.New("dlqreplay: 预检不该碰 broker")
}
func (noopBroker) Get(context.Context, string) (dlq.Message, bool, error) {
	return dlq.Message{}, false, errors.New("dlqreplay: 预检不该碰 broker")
}
func (noopBroker) Publish(context.Context, string, string, dlq.Outbound) error {
	return errors.New("dlqreplay: 预检不该碰 broker")
}
func (noopBroker) Purge(context.Context, string) (int, error) {
	return 0, errors.New("dlqreplay: 预检不该碰 broker")
}

func parse(argv []string) (options, error) {
	fs := flag.NewFlagSet("dlqreplay", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	list := fs.Bool("list", false, "列出队列的深度／最老消息年龄／x-death 摘要（默认档）")
	replay := fs.Bool("replay", false, "把终态 DLQ 里的消息原样回投到 purpose 队列（清掉重试计数）")
	purge := fs.Bool("purge", false, "清空一个队列，必须带逐字相等的 -confirm")
	queue := fs.String("queue", "", "队列名；留空＝-list 全部队列。带点与 dlq./retry. 前缀的都算")
	limit := fs.Int("limit", 100, "-replay：这一轮最多捞几条")
	confirm := fs.String("confirm", "", "-purge：二次确认串，必须与 -queue 逐字相等")
	timeout := fs.Duration("timeout", 60*time.Second, "整轮上限（含拨号与 broker 确认）")
	if err := fs.Parse(argv); err != nil {
		return options{}, err
	}
	chosen := 0
	for _, b := range []bool{*list, *replay, *purge} {
		if b {
			chosen++
		}
	}
	if chosen > 1 {
		return options{}, errors.New("-list／-replay／-purge 三者互斥（一次运行只做一件事）")
	}
	o := options{limit: *limit, confirm: *confirm, timeout: *timeout}
	switch {
	case *replay:
		o.mode = modeReplay
	case *purge:
		o.mode = modePurge
	default:
		o.mode = modeList
	}
	if *queue != "" {
		o.queues = []string{strings.TrimSpace(*queue)}
	}
	if o.mode != modeList && len(o.queues) != 1 {
		return options{}, fmt.Errorf("-%s 需要 -queue（留空不行：一次只对**一个**队列动手，"+
			"才可能事后说清那一次动了谁）", o.mode)
	}
	if len(o.queues) == 1 && o.queues[0] == "" {
		return options{}, errors.New("-queue 给了空白串")
	}
	if o.timeout <= 0 {
		return options{}, fmt.Errorf("-timeout=%v 非法（必须为正）", o.timeout)
	}
	return o, nil
}

// run 是工具的全部动作面：拿着一个 Broker 端口跑，所以没有 broker 也能测。
func run(ctx context.Context, o options, b dlq.Broker) (any, error) {
	switch o.mode {
	case modeList:
		summaries, err := dlq.ListAll(ctx, b, o.queues)
		if err != nil {
			return map[string]any{"mode": o.mode, "queues": summaries}, err
		}
		return map[string]any{
			"mode":        o.mode,
			"queues":      summaries,
			"dlq_total":   dlq.DLQTotal(summaries),
			"queue_kinds": len(summaries),
		}, nil
	case modeReplay:
		res, err := dlq.Replay(ctx, b, o.queues[0], o.limit)
		return map[string]any{"mode": o.mode, "replay": res}, err
	case modePurge:
		n, err := dlq.Purge(ctx, b, o.queues[0], o.confirm)
		return map[string]any{"mode": o.mode, "purged": map[string]any{
			"queue": o.queues[0], "discarded": n,
		}}, err
	}
	return nil, fmt.Errorf("dlqreplay: 未知档位 %q", o.mode)
}

// emit 把这一轮的产物写成 stdout 上的**一行** JSON（文件头第 1 条）。
func emit(out any) error {
	encoded, err := json.Marshal(out)
	if err != nil {
		return fmt.Errorf("报告编码失败：%w", err)
	}
	fmt.Println(string(encoded))
	return nil
}
