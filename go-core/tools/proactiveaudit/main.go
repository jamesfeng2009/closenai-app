// Command proactiveaudit 是 ProactiveTask 的只读审计客户端（T5-04，工单 §怎么做.3 点名的那件 CLI 包装）。
//
// 跑法：
//
//	# 环境变量只有两个，且与 BFF 用的是同一对键（两侧不同值＝每次 403）
//	export GO_API_URL=http://127.0.0.1:8000 GO_SERVICE_TOKEN=<集群内密钥>
//	go run ./tools/proactiveaudit --user <user-id>              // 全部触达，最新在前
//	go run ./tools/proactiveaudit --user <user-id> --stale      // 只看「到期却没发出去」
//	go run ./tools/proactiveaudit --user <user-id> --status sent --limit 20
//
// ⚠️ 四条形状，每条都有判据：
//
//  1. **身份只走 `X-User-Id` 头，绝不拼进 query**。工单 §怎么做.1 写的是 `?user_id=`，
//     而 ADR-002 钉的是「Go 一律不信 body/query 里的 user_id」；服务端这一面认的
//     只有 status/limit/stale 三个键，带 `?user_id=` 会被显式 400（不是静默忽略）。
//     所以客户端也不给那条路留口子：这里根本没有 `--user-id` 之外的身份来源，
//     而 `--user` 的值只进头。
//  2. **密钥只从环境变量读，不做 flag**。flag 的值在 `ps`、shell history、CI 日志里
//     都是明文，一条运维口令一旦进了命令行就等于进了别人的终端记录。
//     URL 同理：与 BFF 同一个 `GO_API_URL` 键，不新造第三个。
//  3. **stdout 只有一行 JSON，日志与提示一律 stderr**（沿 tools/dlqreplay 的同一形状）：
//     这个工具的产物要能被进程闸解析，混进一行日志就会把那次解析打成「解析失败」
//     而不是「值不对」——后者才有诊断价值。
//  4. **失败时把服务端的信封 code／message 原样带进 stderr**。审计面最常见的三种失败
//     （403 密钥不合、401 没带头、404 这一面根本没注册）在客户端看都叫「请求失败」，
//     而它们的处置完全不同；把码抄出来是唯一能让人当场分清那三件事的办法。
//
// 本工具**不是生产入口**，也不进部署：它由人敲，或由 CI 的进程闸敲。它只读——
// 这一面唯一的写动作是零，删一个任务、改一个状态都得走别的票。
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

const auditPath = "/internal/v3/proactives"

type options struct {
	userID  string
	status  string
	limit   int
	stale   bool
	timeout time.Duration
}

// report 是 stdout 那一行 JSON 的形状。
//
// 它**回显生效的过滤条件**而不是只报条数：这一面的判据是「有没有少报」，
// 而「20 条」在不知道 status／limit／stale 的情况下什么都证明不了
// （domain 与 httpapi 两侧同一口径，见 v3_proactiveListPayload 的 Limit 注释）。
type report struct {
	UserID    string            `json:"user_id"`
	Query     string            `json:"query"`
	Applied   applied           `json:"applied"`
	Total     int               `json:"total"`
	Truncated bool              `json:"truncated"`
	Items     []json.RawMessage `json:"items"`
}

type applied struct {
	Status  string `json:"status"`
	Limit   int    `json:"limit"`
	Stale   bool   `json:"stale"`
	HTTP    int    `json:"http_status"`
	Code    int    `json:"code"`
	TraceID string `json:"trace_id"`
}

// envelope 只需要五格中的四格：data 留成 RawMessage，由 items／truncated 那一层再解。
// 这里**故意不复用 httpapi 的结构体**：那一面是服务端内部形状，工具跟它共用的只有
// JSON 线上格式；import 进来会把 tools 钉在 httpapi 的重构上。
type envelope struct {
	Code    int             `json:"code"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data"`
	TraceID string          `json:"trace_id"`
	UserID  string          `json:"user_id"`
}

type dataShape struct {
	Items     []json.RawMessage `json:"items"`
	Truncated bool              `json:"truncated"`
	Limit     int               `json:"limit"`
	Status    string            `json:"status"`
	Stale     bool              `json:"stale"`
}

func main() {
	if err := execute(os.Args[1:], os.Getenv, http.DefaultClient, os.Stdout, os.Stderr); err != nil {
		fmt.Fprintln(os.Stderr, "proactiveaudit:", err)
		os.Exit(1)
	}
}

// execute 分成这一层只为让「取环境／发请求／写产物」三件事都能在没有网络、没有服务器的情况下被跑一遍。
// 参数一律注入而不是就地读全局：否则唯一的测法是真起一个 Go，而那正是这一格不该要的依赖。
func execute(argv []string, getenv func(string) string, client *http.Client,
	stdout, stderr io.Writer) error {
	opts, err := parse(argv, stderr)
	if err != nil {
		return err
	}
	base := strings.TrimRight(strings.TrimSpace(getenv("GO_API_URL")), "/")
	if base == "" {
		return errors.New("GO_API_URL 未设置 ⇒ 本工具一条都没查，别把它当绿")
	}
	token := strings.TrimSpace(getenv("GO_SERVICE_TOKEN"))
	if token == "" {
		return errors.New("GO_SERVICE_TOKEN 未设置 ⇒ 这一面唯一的鉴权口是空的，" +
			"服务端会整面不注册（404）而不是回你一片空列表")
	}
	ctx, cancel := context.WithTimeout(context.Background(), opts.timeout)
	defer cancel()

	rep, err := run(ctx, client, base, token, opts)
	if rep != nil {
		if err := emit(stdout, rep); err != nil {
			return err
		}
	}
	return err
}

// parse 只做参数面的判断；`--user` 空白在这里就退，不发请求。
// 空 user id 在服务端是 401（头缺格）或零行，两者都不如「命令行就拦住」诚实。
func parse(argv []string, stderr io.Writer) (options, error) {
	fs := flag.NewFlagSet("proactiveaudit", flag.ContinueOnError)
	fs.SetOutput(stderr)
	user := fs.String("user", "", "要审计的 user id（必填；只进 X-User-Id 头，不进 query）")
	status := fs.String("status", "", "只看某一态；八态词表见 domain.ProactiveStatuses")
	limit := fs.Int("limit", 0, "最多几条；0＝用服务端默认，超上限由服务端收敛并回显")
	stale := fs.Bool("stale", false, "只看「到期却没发出去」的那几行（谓词在 canonical.sql，不在这里）")
	timeout := fs.Duration("timeout", 15*time.Second, "整轮上限（含建连与读响应）")
	if err := fs.Parse(argv); err != nil {
		return options{}, err
	}
	if len(fs.Args()) > 0 {
		return options{}, fmt.Errorf("多余的位置参数 %q：这一面只有 --user/--status/--limit/--stale",
			fs.Args()[0])
	}
	if strings.TrimSpace(*user) == "" {
		return options{}, errors.New("--user 必填 ⇒ 跨用户查询在这条面上不存在，" +
			"没有身份就只能查不到（而不是查所有人）")
	}
	if *limit < 0 {
		return options{}, fmt.Errorf("--limit=%d 非法（负数在服务端会退到默认，"+
			"在这里先拦住是为了让「我以为我限了 5 条」不成立）", *limit)
	}
	if *timeout <= 0 {
		return options{}, fmt.Errorf("--timeout=%v 非法（必须为正）", *timeout)
	}
	o := options{userID: strings.TrimSpace(*user), status: strings.TrimSpace(*status),
		limit: *limit, stale: *stale, timeout: *timeout}
	// 词表提示走 stderr，且在请求**之前**：`--status pending` 这种词服务端不报错、
	// 只回零行（与 insights 的 narrowListArgs 同口径），于是「这个人没有触达」与
	// 「这个词根本不存在的」在 stdout 上长得一模一样。提示不改判：照样发请求，
	// 因为真正的证据只有库里有。
	if o.status != "" && !domain.IsProactiveStatus(o.status) {
		fmt.Fprintf(stderr, "proactiveaudit: 提示：--status %q 不在八态词表里（%s）"+
			"⇒ 这一面会安静地回零行；先把词改对再判断「到底有没有」\n",
			o.status, statusVocabulary())
	}
	return o, nil
}

// run 是全部动作面：拿一个 *http.Client（测试里换成一个假 RoundTripper）跑一次 GET。
func run(ctx context.Context, client *http.Client, base, token string, o options) (*report, error) {
	q := url.Values{}
	if o.status != "" {
		q.Set("status", o.status)
	}
	if o.limit > 0 {
		q.Set("limit", strconv.Itoa(o.limit))
	}
	if o.stale {
		q.Set("stale", "true")
	}
	endpoint := base + auditPath
	if enc := q.Encode(); enc != "" {
		endpoint += "?" + enc
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, fmt.Errorf("请求构造失败：%w", err)
	}
	// 两格头是这一面唯一的身份与唯一的鉴权（ADR-002）：没有第三处可以伪造。
	req.Header.Set("X-Service-Token", token)
	req.Header.Set("X-User-Id", o.userID)

	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("查询 %s 失败：%w", auditPath, err)
	}
	defer func() { _ = resp.Body.Close() }()

	// 上限是为了不把整张表读进内存：这一面的 limit 服务端已经收到 200，
	// 但一个不守 limit 的响应（或一个走错端口的 HTML 页）不该让工具 OOM。
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		return nil, fmt.Errorf("读响应失败：%w", err)
	}
	var env envelope
	if err := json.Unmarshal(raw, &env); err != nil {
		// 状态码留着一起报：非 JSON 的响应基本都是「打错了地址」（404 HTML）或「网关页」，
		// 只报解析失败会让人去查 JSON 而不是查 URL。
		return nil, fmt.Errorf("响应不是信封（HTTP %d，%d 字节）：%w",
			resp.StatusCode, len(raw), err)
	}
	if resp.StatusCode != http.StatusOK || env.Code != apierr.CodeOK {
		// 文件头第 4 条：码与 message 原样抄出来，别替服务端总结。
		return nil, fmt.Errorf("HTTP %d／code %d：%s（trace_id %s）",
			resp.StatusCode, env.Code, env.Message, env.TraceID)
	}
	var body dataShape
	if err := json.Unmarshal(env.Data, &body); err != nil {
		return nil, fmt.Errorf("data 不是审计列表的形状：%w", err)
	}
	return &report{
		UserID: o.userID,
		Query:  q.Encode(),
		Applied: applied{Status: body.Status, Limit: body.Limit, Stale: body.Stale,
			HTTP: resp.StatusCode, Code: env.Code, TraceID: env.TraceID},
		Total:     len(body.Items),
		Truncated: body.Truncated,
		Items:     body.Items,
	}, nil
}

// emit 把这一轮的产物写成 stdout 上的**一行** JSON（文件头第 3 条）。
func emit(w io.Writer, r *report) error {
	encoded, err := json.Marshal(r)
	if err != nil {
		return fmt.Errorf("报告编码失败：%w", err)
	}
	fmt.Fprintln(w, string(encoded))
	return nil
}

// statusVocabulary 把八态拼成一句提示用的人话。
//
// 为什么不直接 print `domain.ProactiveStatuses`：那是 `[]domain.ProactiveStatus`，
// `%v` 会输出 `[candidate eligible …]` 这种「Go 切片的形状」，而提示是给敲命令的人看的。
// 词表本身只此一份（domain），这里只搬不抄 —— 抄一份字面量就是第二个会漂的地方。
func statusVocabulary() string {
	parts := make([]string, 0, len(domain.ProactiveStatuses))
	for _, s := range domain.ProactiveStatuses {
		parts = append(parts, string(s))
	}
	return strings.Join(parts, "|")
}
