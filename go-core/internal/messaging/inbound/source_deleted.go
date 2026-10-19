// 事件面（T2-06）：`source.deleted` 的解码与进门。
//
// 这条链的方向与候选面**相反**，所以它需要自己的一扇门：候选是「Python 产了东西请 Go 入账」，
// 而删除是「Python 那边的一行业务数据没了，请 Go 把已经入过的账改过来」。
// 两者共用 `closenai.events`／`closenai.ai.candidates` 之外的同一个传输契约
// （content_type、字节上限、message_type 与键同源、schema 版本、归属头），
// 判据全在 `transportGate` 里那一处，本文件只多判一件：**体里自报的 user_id 必须与头一致**。
//
// 为什么体里还要带 user_id（头的 x-user-id 才是归属权威）：
//   - 没有它，值班在 DLQ 里看到一条 `source.deleted` 答不出「这是谁的删除」——
//     头是 broker 层的、重放工具改投时最容易丢的就是头；
//   - 有了它就必须**判相等**：这一格是那条越权删除的靶。事件体的 `source_id` 是别人的行
//     而头是受害者时，靠的是 `MarkSourceRecordDeletedOnce` 谓词里带 `user_id`（库里根本
//     命中不了别人的登记行）；头和体自己就不一致时，连库都不必去——那是拼错的生产者。
//
// 归属判定**不在 store 里做**（分层同 `source_pointer.go`：纯判定排在读库之前）。
package inbound

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// 契约常量（与 `amqp/topology.go` 的 `EventRoutes` 同值，由 inbound_contract_test.go 钉住）。
const (
	// SourceDeletedRoutingKey 是 Python 在删除**提交之后**投的那个键。
	SourceDeletedRoutingKey = "source.deleted"
	// SourceDeletedMessageType 由键按「点换成下划线」推出来（`messageTypeFor`），
	// 写成常量是为了对面 Python 的 `contract.py` 有一个能逐字对的串。
	SourceDeletedMessageType = "source_deleted"
	// SourceDeletedQueue 是 Go 侧认领的队列名。
	SourceDeletedQueue = "go.source.deleted"
	// SourceDeletedConsumer 是这条链在 `inbox_messages.consumer` 里的唯一取值。
	//
	// 为什么不是复用 `Consumer`（候选面那个）：幂等键是 `(message_id, consumer)`，
	// 同一个 message_id 在两个消费者那里是两件事；并成一个串就等于让
	// 「候选面已经吃过这条 id」去替事件面宣告「我也吃过了」——那条删除永远不会被传播。
	SourceDeletedConsumer = "go.core.source"
)

// EntryInvalidateSource 是事件面唯一那扇门（取值逐字用 domain 的方法名，理由同 EntryAdmit*）。
const EntryInvalidateSource Entry = "InvalidateSource"

// ReasonSourceUserIDMismatch 只有 Go 会有：Python 自己投的字节不会头和体不一致，
// 真不一致时它就是那条越权删除尝试（或一个拼错的生产者）。
// 登记在 `test_ai_mq_contract.py` 的 GO_ONLY_REASONS。
const ReasonSourceUserIDMismatch = "source_user_id_mismatch"

// EventEntries 是事件面的 routing key → 领域入口映射表。
//
// 与 `CandidateEntries` 分家而不并表：两张表进的是**两个消费者**（`go.core.candidate`
// 与 `go.core.source`），并进一张表就会让「加了一个键」看起来不需要加认领者——
// 而这条链上少一个认领者的表现正是工作单点名的那种静默丢弃。
var EventEntries = map[string]Entry{
	SourceDeletedRoutingKey: EntryInvalidateSource,
}

// sourceDeletedBody 是事件体的线形。
//
// 键名用 lowerCamelCase 与候选面同一套（对面 `contract.py` 的 protojson 形态），
// 而不是 Python 字典风格的 snake_case——两侧各一套风格迟早让某个夹具「看起来对」地解出空串。
type sourceDeletedBody struct {
	SchemaVersion string `json:"schemaVersion"`
	UserID        string `json:"userId"`
	SourceTable   string `json:"sourceTable"`
	SourceID      string `json:"sourceId"`
}

// DecodeEvent 是事件面的入口（与 `Decode` 平级，不共用映射表）。
//
// 第二个返回值是 `*domain.PermanentRejection`：非 nil ⇒ 这条字节永远不该再投，
// 由 `Runner.Handle` 走 delivery.go 那条「主事务回滚＋事务外补 failed 留痕」的分道。
func DecodeEvent(d Delivery, maxBodyBytes int) (Call, *domain.PermanentRejection) {
	entry, ok := EventEntries[d.RoutingKey]
	if !ok {
		return nil, reject(ReasonUnknownRoutingKey,
			fmt.Sprintf("routing key=%q 不在事件面映射表里（表=%v）；事件面没有默认分支，"+
				"加了键不加解码分支就是让那条键进队列后被永久拒绝", d.RoutingKey, knownEventKeys()))
	}
	userID, bad := transportGate(d, maxBodyBytes)
	if bad != nil {
		return nil, bad
	}
	if entry != EntryInvalidateSource {
		return nil, reject(ReasonUnknownRoutingKey,
			fmt.Sprintf("entry=%q 没有解码分支（表与分支漂了）", entry))
	}

	var body sourceDeletedBody
	dec := json.NewDecoder(bytes.NewReader(d.Body))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&body); err != nil {
		var typeErr *json.UnmarshalTypeError
		if errors.As(err, &typeErr) {
			return nil, reject(ReasonPayloadInvalid,
				fmt.Sprintf("%s 的字段类型不对（%s 不是 %s）：重投一万次也不会变成对，"+
					"所以这是永久拒绝而不是瞬态：%v", d.RoutingKey, typeErr.Field, typeErr.Type, err))
		}
		return nil, reject(ReasonNotJSON,
			fmt.Sprintf("%s 的体解不进 sourceDeletedBody（未知字段也会被这一条抓住，"+
				"因为「体里多了一个没人认的键」与「体不是 JSON」在排查时是同一句话）：%v",
				d.RoutingKey, err))
	}

	// 体内自报的版本与 header 必须对上：候选面那条判据（checkBodyVersion）在这条面上
	// 同一条理由，两侧也不许一侧严一侧松。
	if bad := checkBodyVersion(d, body.SchemaVersion); bad != nil {
		return nil, bad
	}

	// 归属：头是权威，体只是留痕与对账。两个都在却不一致 ⇒ 这条投递说的是谁都不知道。
	if got := strings.TrimSpace(body.UserID); got != "" && got != userID {
		return nil, reject(ReasonSourceUserIDMismatch,
			fmt.Sprintf("x-user-id=%q 而体里 userId=%q：删除事件的归属只有头一个来源，"+
				"两者不一致时不许「按头查库」——那等于替体里那个人销账", userID, got))
	}
	table := strings.TrimSpace(body.SourceTable)
	rowID := strings.TrimSpace(body.SourceID)
	if table == "" || rowID == "" {
		return nil, reject(ReasonPayloadInvalid,
			fmt.Sprintf("source.deleted 缺 sourceTable/sourceId（收到 %q/%q）：指针少一半就没法回查登记行，"+
				"而「把它当没发生过」与「传播错了别人的账」在这条消息上长得一样", table, rowID))
	}
	pointer := domain.SourcePointer{UserID: userID, SourceTable: table, SourceID: rowID}
	return func(ctx context.Context, dm Domain) error {
		_, err := dm.InvalidateSource(ctx, pointer)
		if errors.Is(err, domain.ErrUnsupportedSourceTable) {
			// 复用 T2-03 那张钉码表：表没开是永久拒绝，不许上重试阶梯。
			return domain.PermanentRejection{Code: ReasonSourceTypeNotEnabled, Detail: err.Error()}
		}
		return err
	}, nil
}

func knownEventKeys() []string {
	out := make([]string, 0, len(EventEntries))
	for key := range EventEntries {
		out = append(out, key)
	}
	sort.Strings(out)
	return out
}
