// 契约面的闸（R5-②）。
//
// 这里判的是「这张表本身对不对」，与 inbound_test.go（判行为）分开放：
// 表错了，行为用例可以全绿——因为每条用例的夹具都是照着这张表写的（自证循环）。
// 所以这几条一律拿**表之外的**事实源对：`contracts/events.md`（由 topology.go 生成）、
// 生成物里的 `Kind` 枚举、以及 Python 侧 `backend/ai/mq/contract.py` 的码面。
package inbound

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// TestEntriesAreRoutableAndComplete 拿生成物契约文档当外部事实源：
//   - events.md 里逐字声明的每条 candidate 绑定键（通配除外），本表**必须**有进门分支；
//   - 本表里每个键，必须真能被某条声明的绑定路由过来（否则消息永远到不了这一格，
//     表写着等于没写）；
//   - 消费队列名必须就是 `Queue`（本包只消费这一个队列）。
func TestEntriesAreRoutableAndComplete(t *testing.T) {
	rows := candidateBindingRows(t)
	if len(rows) == 0 {
		t.Fatal("contracts/events.md 里没有 candidate 绑定行 ⇒ 拓扑被清空了，比对通过也不代表契约还在")
	}
	var explicit, wildcards []string
	for _, r := range rows {
		if r.queue != Queue {
			t.Errorf("绑定行把 %s 指向队列 %q，而本消费者只消费 %q", r.key, r.queue, Queue)
		}
		if strings.HasSuffix(r.key, "*") {
			wildcards = append(wildcards, r.key)
			continue
		}
		explicit = append(explicit, r.key)
		if _, ok := CandidateEntries[r.key]; !ok {
			t.Errorf("已声明的绑定 %s 在映射表里没有分支 ⇒ 消息会进来却永远 unknown_routing_key", r.key)
		}
	}
	if len(wildcards) == 0 {
		t.Error("events.md 里没有 candidate.* 通配绑定 ⇒ fact/evidence/proactive 家族到不了这个队列")
	}
	for key := range CandidateEntries {
		if !anyMatch(wildcards, explicit, key) {
			t.Errorf("映射表里的 %s 没有任何已声明的绑定能路由它 ⇒ 这一格是死键", key)
		}
	}
}

// TestClosedTableHasNoDefaultBranch 是 D-R5-8 点名的那条反面：
// 通配绑定让 `candidate.<任何Thing>` 都能进到队列里，而**进门≠谁都能写 canonical 表**。
// 默认落 `Admit` 的形态是「一条 candidate.observed 被写成 fact」——落库就收不回。
func TestClosedTableHasNoDefaultBranch(t *testing.T) {
	for _, key := range []string{
		"candidate.observed", "candidate.memory", "candidate.", "candidate",
		"CANDIDATE.PATTERN", "candidate.pattern ", " candidate.pattern",
	} {
		d := deliveryFor(key)
		call, rej := Decode(d, DefaultMaxBodyBytes)
		if call != nil {
			t.Errorf("%q 竟然拿到了领域调用 ⇒ 表里出现了默认分支，D-R5-8 的封闭性没了", key)
		}
		if rej == nil || rej.Code != ReasonUnknownRoutingKey {
			t.Errorf("%q 的结论是 %+v，期望 unknown_routing_key", key, rej)
		}
	}
}

// TestEntriesEachHaveADecoderBranch 钉表与分支同进退：
// `Decode` 末尾那条「到不了就拒」是给人看的，编译器不会为新增的第四扇门报错。
func TestEntriesEachHaveADecoderBranch(t *testing.T) {
	seen := map[Entry]bool{}
	for key, entry := range CandidateEntries {
		if _, ok := CandidateKinds[key]; ok != (entry == EntryAdmit) {
			t.Errorf("%s ⇒ entry=%s 与 CandidateKinds 的归属不一致（只有走 Admit 的三条需要在体内自报 kind）",
				key, entry)
		}
		seen[entry] = true
	}
	for _, want := range []Entry{EntryAdmit, EntryAdmitEvidence, EntryAdmitProactive} {
		if !seen[want] {
			t.Errorf("映射表不再覆盖 %s ⇒ D-R5-8 的三条绑定少了一条进门", want)
		}
	}
	if len(seen) != 3 {
		t.Errorf("表里出现了第四扇门 %v，而 Decode 的 switch 只有三个分支", seen)
	}
}

// TestMessageTypeRuleIsTheCommandsOne 把 message_type 的推定规则钉回命令面那条：
// 键把点换成下划线。两侧规则不同＝对面按另一种写法发来的每条消息都被 message_type_mismatch 挡掉。
func TestMessageTypeRuleIsTheCommandsOne(t *testing.T) {
	for key := range CandidateEntries {
		got := messageTypeFor(key)
		want := strings.ReplaceAll(key, ".", "_")
		if got != want {
			t.Errorf("%s ⇒ %q，与命令面规则（commands.go:47）不同形", key, got)
		}
		if strings.ContainsAny(got, ".*") {
			t.Errorf("%q 里还有点或星号，不是合法的 message_type 形态", got)
		}
	}
}

// TestReasonCodesAreStableIdentifiers 判码面的形状：
// 这些串会进 `inbox.error_code`（VARCHAR(64)）并被 Python 侧逐字比对，
// 空格、大写、超宽都是把一个可查的码变成一个查不到的码。
func TestReasonCodesAreStableIdentifiers(t *testing.T) {
	codes := []string{
		ReasonUnknownRoutingKey, ReasonContentType, ReasonBodyTooLarge, ReasonMessageTypeMismatch,
		ReasonSchemaVersion, ReasonMissingUserID, ReasonMissingMessageID, ReasonNotJSON,
		ReasonPayloadInvalid, ReasonKindMismatch, ReasonMetadataNotJSON, ReasonUnknownDomainValue,
		ReasonSourceTypeNotEnabled, ReasonForeignSource, ReasonUnknownSourceRow,
	}
	seen := map[string]bool{}
	snake := regexp.MustCompile(`^[a-z][a-z0-9_]*$`)
	for _, c := range codes {
		if !snake.MatchString(c) {
			t.Errorf("码 %q 不是小写下划线形态", c)
		}
		if len(c) > 64 {
			t.Errorf("码 %q 长 %d > 64（inbox.error_code 的列宽）", c, len(c))
		}
		if seen[c] {
			t.Errorf("码 %q 重复：两个不同的失败共用一个码，账就读不出发生了什么", c)
		}
		seen[c] = true
	}
	// 计数证人：这张手写的名单必须跟着码面一起改。名单扫空或漏一条都不会红，
	// 而漏掉的那一条恰好是「形状没被审过」的那一条（Python 侧另有一条闸把
	// 这些值逐个比回 contract.py 的 REASON_* 面，见 test_ai_mq_contract.py）。
	if len(codes) != 15 {
		t.Errorf("码面有 %d 条，期望 15（12 条传输/载荷面＋3 条来源指针面）⇒ "+
			"新增的 Reason* 没进这份名单，形状就没被审过", len(codes))
	}
}

func TestSupportedSchemaVersionIsTheOneOnTheWire(t *testing.T) {
	// 上限不许悄悄放开：多认一个版本＝用今天的解码器解明天的载荷。
	if SupportedSchemaVersion != "1" {
		t.Errorf("本消费者认的版本是 %q ⇒ 与出箱侧写进 x-schema-version 的那条不齐；"+
			"两侧要一起换（O1 切 protobuf 时连着 schema_version 一起）", SupportedSchemaVersion)
	}
	if strings.ContainsAny(SupportedSchemaVersion, " ,*") {
		t.Errorf("版本面应当只有一条，实收 %q", SupportedSchemaVersion)
	}
}

func TestProductionDefaultsAreTheDocumentedOnes(t *testing.T) {
	if DefaultMaxBodyBytes != 65536 {
		t.Errorf("MaxBodyBytes=%d ⇒ Python 侧 DEFAULT_MAX_BODY_BYTES 是 65536，"+
			"两侧不一致时「谁拒」变成随机事件", DefaultMaxBodyBytes)
	}
	if DefaultPrefetch != 1 {
		t.Errorf("Prefetch=%d ⇒ 在途条数与领域入口的嵌套事务（R5b）按倍数吃连接池", DefaultPrefetch)
	}
	if DefaultDialTimeout <= 0 || DefaultDrainTimeout <= 0 {
		t.Error("拨号与收尾预算必须是正值")
	}
	if DefaultDrainTimeout > 30*time.Second {
		t.Errorf("DrainTimeout=%s 超过 pod 的 terminationGracePeriodSeconds ⇒ 收尾一定被 SIGKILL 打断",
			DefaultDrainTimeout)
	}
}

func TestConsumerAndQueueAreTheContractPair(t *testing.T) {
	// consumer 串是幂等唯一键的一半（18 §7），它随队列名走是为了让「账上这一列」
	// 能一眼对上队列；两个串各自被改都等于换一个记账 namespace。
	if Consumer != "go.core.candidate" || Queue != "go.candidate.accept" {
		t.Errorf("队列/消费者名漂了：queue=%q consumer=%q", Queue, Consumer)
	}
	if !strings.HasPrefix(Queue, "go.") {
		t.Errorf("队列 %q 不以 go. 开头 ⇒ 与 14:55 的归属命名（Go 侧收件队列）不同形", Queue)
	}
	if ContentTypeJSON != "application/json" {
		t.Errorf("content_type=%q，与 publisher 侧写在信封上的那串不齐", ContentTypeJSON)
	}
}

func TestSwitchEnvNamesAreTheSharedOnes(t *testing.T) {
	// 键名是跨语言的部署面：Python worker 读的也是这两个串（backend/ai/mq/config.py）。
	if EnvURL != "CLOSEN_AMQP_URL" {
		t.Errorf("broker 地址键改名会让两侧读不到同一个 ConfigMap 值：%q", EnvURL)
	}
	if EnvWorker != "CLOSEN_CANDIDATE_WORKER" {
		t.Errorf("入站开关键应为独立的一条（D-R5-9 的分开关），实收 %q", EnvWorker)
	}
	if !strings.HasPrefix(EnvWorker, "CLOSEN_") || !strings.HasPrefix(EnvURL, "CLOSEN_") {
		t.Error("env 键必须带 CLOSEN_ 前缀，与仓内其余键同面")
	}
}

// TestCallSignatureStaysOutOfTheStore 是分层闸的可执行版本：
// 契约面（`Decode`）只判传输形状＋归属＋大小，一条都不许碰 Store——
// 这条由 `Call` 的签名（只接 `Domain`）在编译期保证，所以这里钉的是**签名没被放宽**。
// 哪天有人给它加一个 `domain.Store` 参数，这一行先红。
var _ Call = func(ctx context.Context, d Domain) error {
	_, _ = ctx, d
	return nil
}

// ── 外部事实源：contracts/events.md ─────────────────────────────────────────

type bindingRow struct {
	key   string
	queue string
}

// eventBindingRows 从生成物里取**事件面**已绑定的那些行（§二那张表，Kind 列＝event）。
//
// 为什么这条闸必须存在：`SourceDeletedQueue` 与本包要认领的队列名是两条独立写的串
// （一条在 `topology.go` 的 `EventRoutes`，一条在 `source_deleted.go` 的常量）。
// 拼错的现网后果不是报错而是静默：`Consume` 认一个不存在的队列 ⇒ passive 声明 404、
// 或者更坏——认到一个存在但绑的是别的键的队列，于是删除事件永远不进门，而两侧测试全绿。
// 读生成物不读 `topology.go`：events.md 已有 amqp 包的字节级漂移闸守着（contract_drift_test.go），
// 本包不必 import amqp。
func eventBindingRows(t *testing.T) []bindingRow {
	t.Helper()
	var out []bindingRow
	for _, line := range strings.Split(readEventsMD(t), "\n") {
		if !strings.HasPrefix(strings.TrimSpace(line), "|") {
			continue
		}
		cols := strings.Split(strings.Trim(line, "|"), "|")
		if len(cols) < 4 {
			continue
		}
		exchange, key, queue, kind := trim(cols[0]), trim(cols[1]), trim(cols[2]), trim(cols[3])
		if exchange != "closenai.events" || kind != "event" {
			continue
		}
		out = append(out, bindingRow{key: key, queue: queue})
	}
	return out
}

func readEventsMD(t *testing.T) string {
	t.Helper()
	path := filepath.Join(repoRootFromCwd(t), "contracts", "events.md")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("%s 读不出：%v", path, err)
	}
	return string(raw)
}

// TestEventQueueNamesMatchTheGeneratedTopology 把本包事件面的两个串钉回生成物。
func TestEventQueueNamesMatchTheGeneratedTopology(t *testing.T) {
	rows := eventBindingRows(t)
	if len(rows) != 1 {
		t.Fatalf("events.md §二 的 event 行有 %d 条，本批只准有 source.deleted 一条：%v", len(rows), rows)
	}
	if rows[0].key != SourceDeletedRoutingKey {
		t.Errorf("拓扑绑的键是 %q，本包解码器认的是 %q ⇒ 这条消息进门判据第一跳就 unknown_routing_key",
			rows[0].key, SourceDeletedRoutingKey)
	}
	if rows[0].queue != SourceDeletedQueue {
		t.Errorf("拓扑上的队列是 %q，本包 Consume 的是 %q ⇒ 认了一个没人绑定的队列，"+
			"删除事件全数堆在拓扑那个队列里", rows[0].queue, SourceDeletedQueue)
	}
	// consumer 串与队列名同源（与 Consumer/Queue 那一对同一规矩）：幂等键的一半。
	if SourceDeletedConsumer != "go.core.source" {
		t.Errorf("consumer=%q，期望 go.core.source（与候选面的 go.core.candidate 必须不同，"+
			"否则两条面共用一个幂等 namespace）", SourceDeletedConsumer)
	}
	if SourceDeletedConsumer == Consumer {
		t.Error("事件面与候选面的 consumer 串相同 ⇒ 候选面的「吃过了」会替事件面宣告「我也吃过了」")
	}
	// message_type 由键折叠而来，与命令面/候选面同一条规则。
	if SourceDeletedMessageType != strings.ReplaceAll(SourceDeletedRoutingKey, ".", "_") {
		t.Errorf("message_type=%q 不是键 %q 按点换下划线的形状", SourceDeletedMessageType, SourceDeletedRoutingKey)
	}
}

// candidateBindingRows 从**生成物**里取绑定面。读生成物而不是读 topology.go，
// 是因为 events.md 由 `go run ./tools/eventscontract -write` 从那张表生成，
// 已经有 amqp 包的字节级漂移闸守着它（contract_drift_test.go）——本包不必再 import amqp。
func candidateBindingRows(t *testing.T) []bindingRow {
	t.Helper()
	path := filepath.Join(repoRootFromCwd(t), "contracts", "events.md")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("%s 读不出：%v", path, err)
	}
	var out []bindingRow
	for _, line := range strings.Split(string(raw), "\n") {
		if !strings.HasPrefix(strings.TrimSpace(line), "|") {
			continue
		}
		cols := strings.Split(strings.Trim(line, "|"), "|")
		if len(cols) < 4 {
			continue
		}
		exchange, key, queue := trim(cols[0]), trim(cols[1]), trim(cols[2])
		if exchange != "closenai.ai.candidates" || !strings.HasPrefix(key, "candidate") {
			continue
		}
		out = append(out, bindingRow{key: key, queue: queue})
	}
	return out
}

func anyMatch(wildcards, explicit []string, key string) bool {
	for _, k := range explicit {
		if k == key {
			return true
		}
	}
	for _, w := range wildcards {
		if amqpMatch(strings.TrimSuffix(w, "*"), key) {
			return true
		}
	}
	return false
}

// amqpMatch 按 AMQP topic 通配的最小形状判：`candidate.` 前缀覆盖 `candidate.<单个词>`。
// 这里只有 `candidate.*` 这一条通配，所以不需要实现 `#` 与多级段。
func amqpMatch(prefix, key string) bool {
	if !strings.HasPrefix(key, prefix) {
		return false
	}
	rest := strings.TrimPrefix(key, prefix)
	return rest != "" && !strings.Contains(rest, ".") && !strings.ContainsAny(rest, "*#")
}

// deliveryFor 用给定键造一条其余面都合法的交付。
// body 里 kind 与键同义（否则第一道门之后还会撞上 kind mismatch，测不出这一格）。
func deliveryFor(routingKey string) Delivery {
	body := fmt.Sprintf(`{"kind":"KIND_PATTERN","fingerprint":"f","patternType":"p",` +
		`"safety":{"status":"SAFETY_STATUS_OK"}}`)
	return Delivery{
		RoutingKey: routingKey, MessageID: "cnd-1", ContentType: ContentTypeJSON,
		MessageType: strings.ReplaceAll(routingKey, ".", "_"),
		Headers: map[string]any{
			HeaderSchemaVersion: SupportedSchemaVersion, HeaderUserID: "user-1",
		},
		Body: []byte(body),
	}
}

// ── 仓库根 ────────────────────────────────────────────────────────────────

// repoRootFromCwd 从测试工作目录（本包目录）向上找仓库根。
// 判据用 contracts/ ＋ go-core/go.mod 同时存在：只认 contracts/ 会在别的项目里撞库。
// 与 amqp 包 contract_drift_test.go 里那一份逐字同判据（跨包没法共用私有函数）。
func repoRootFromCwd(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	for {
		if isDir(filepath.Join(dir, "contracts")) && isFile(filepath.Join(dir, "go-core", "go.mod")) {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Fatalf("从 %s 向上没找到仓库根（判据：同时有 contracts/ 与 go-core/go.mod）", dir)
		}
		dir = parent
	}
}

func isDir(p string) bool {
	st, err := os.Stat(p)
	return err == nil && st.IsDir()
}

func isFile(p string) bool {
	st, err := os.Stat(p)
	return err == nil && !st.IsDir()
}

func trim(s string) string {
	// 文档里每格都是 `` `值` `` 形态（markdown 的行内代码），先脱掉反引号再比字符串；
	// 不脱会让「表被清空」与「格式变了」两种情况看起来一模一样。
	return strings.Trim(strings.TrimSpace(s), "`")
}

// TestNoCandidateKindIsMappedTwice 保证「一条键只能有一扇门」：
// map 的键唯一性由类型系统给，这里补的是**值面**不许出现同一个 Kind 两个写法。
func TestNoCandidateKindIsMappedTwice(t *testing.T) {
	byEntry := map[Entry][]string{}
	for key, entry := range CandidateEntries {
		byEntry[entry] = append(byEntry[entry], key)
	}
	for entry, keys := range byEntry {
		sort.Strings(keys)
		if entry == EntryAdmit && len(keys) != 3 {
			t.Errorf("%s 门后有 %d 条键（%v）⇒ 应当只有 fact/pattern/insight 三条", entry, len(keys), keys)
		}
		if entry != EntryAdmit && len(keys) != 1 {
			t.Errorf("%s 门后有 %d 条键（%v）⇒ evidence/proactive 各自只有一条", entry, len(keys), keys)
		}
	}
}

// TestKindStringsMatchDomainConstants 把 `mappedKind` 的三条取值钉回 domain 的常量：
// proto 的枚举名去前缀小写后必须**正好**是 domain 那三个串，否则 Kind 会静默变成未知。
func TestKindStringsMatchDomainConstants(t *testing.T) {
	for key, want := range map[string]domain.Kind{
		"candidate.fact":    domain.KindFact,
		"candidate.pattern": domain.KindPattern,
		"candidate.insight": domain.KindInsight,
	} {
		got, ok := mappedKind(CandidateKinds[key])
		if !ok || got != want {
			t.Errorf("%s ⇒ (%q,%v)，期望 (%q,true)", key, got, ok, want)
		}
	}
	if _, ok := mappedKind(0); ok {
		t.Error("UNSPECIFIED 的 kind 被映射成功了 ⇒ Safety/Kind 的 fail-closed 方向被打穿")
	}
}
