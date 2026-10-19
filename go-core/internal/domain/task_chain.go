// 候选入库后的「下一段」映射（批 3 · D-G14-②）。
//
// R4 交付的是发射器、R5 交付的是管道，而 `RequestAITask` 一直没有生产调用方
// （只有取证驱动器 `tools/r5interop` 调过它，那是第三条路，不算现网）。
// 本文件把「谁在什么时候下发任务」这件口头上的事变成一张表：**候选入库之后，
// 由入站消费面请对面算下一段**。这是 R5b 登记的连带账之一，也是工作单 D-G14 点名
// 「只接两条无争议的」里的第二条。
//
// 三条硬约束，都由这张表的形状而不是调用方来保证：
//  1. **链必须是 DAG**。Python 侧一个任务只产一种候选（`backend/ai/mq/registry.py`
//     的 handler 签名：observation 任务产 fact、pattern 任务产 pattern、insight 任务产
//     insight），所以「fact 入库请 pattern、pattern 入库请 insight、insight 入库什么都不请」
//     是唯一不会自转的形状。反向边（insight→observation 之类）会让一条候选永久地
//     把自己重放下去，而 outbox 的幂等键每次都是新的——那才是本文件要防的事故。
//     闸：`TestFollowUpChainIsAcyclic`。
//  2. **命令 id 必须由来源候选确定性派生**，不拿时间、不拿 uuid。理由与对面
//     `candidate_message_id`（`contract.py:341`）逐字同一条：重试不许换新 id。
//     派生串里带上 stage，是因为将来同一张表若给同一个 kind 加第二条边，
//     两条命令不能拿到同一个 message_id 而互相吃掉。
//  3. **体里不许出现用户身份**。归属只有 header 一个来源
//     （`contract.py` 的 `foreign_user_id` 会把体内任何 user-id 形态永久拒绝），
//     所以这里连 `user_id` 这个键名都不写，不是「写了再指望对面忽略」。
package domain

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"sort"
	"strings"
	"time"
)

// followUpAfter 是那张链表。键是**已入库的候选 kind**，值是下一段要请对面做的任务。
//
// 为什么 insight 不在表里：洞察就是这条链的产物，再往下没有「下一段」。
// 为什么 evidence/proactive 不在表里：它们各自有别的触发面
// （证据分走 `service_evidence.go`，主动消息走 proactive gate），
// 把它们塞进这条链等于凭空多发明一条边。
var followUpAfter = map[Kind]AITaskKind{
	KindFact:    AITaskPattern,
	KindPattern: AITaskInsight,
}

// taskTypeOf 是 `contracts/proto` 的 `TaskKind` 枚举名，也是 Python 侧
// `ROUTING_KEY_TO_TASK_TYPE`（`backend/ai/mq/contract.py:101-105`）逐字比对的取值。
// 命令体里的 `taskType` 必须与键同义，否则对面按 `task_type_mismatch` 永久拒绝。
var taskTypeOf = map[AITaskKind]string{
	AITaskObservation: "TASK_TYPE_OBSERVATION_EXTRACT",
	AITaskPattern:     "TASK_TYPE_PATTERN_DETECT",
	AITaskInsight:     "TASK_TYPE_INSIGHT_DRAFT",
	AITaskProactive:   "TASK_TYPE_PROACTIVE_DRAFT",
}

// TaskType 是命令体 `taskType` 字段的取值（契约源在 domain，与 RoutingKey 同一取向）。
func (k AITaskKind) TaskType() string { return taskTypeOf[k] }

// NextAITaskForKind 返回「这一种候选入库之后该请谁」。ok=false ⇒ 这一种没有下一段。
func NextAITaskForKind(kind Kind) (AITaskKind, bool) {
	next, ok := followUpAfter[kind]
	return next, ok
}

// DeriveTaskOperationID 由「来源候选的 message_id ＋ 这一段的种类」派生命令 id。
//
// 形状与对面 `candidate_message_id` 一致：`"task-" + sha256(...)[:40]`，
// 定长 45 字符 ⇒ 永远落在 `outbox_messages.message_id` 的 128 列宽里
// （`validateAITaskRequest` 那条越界即拒的检查因此不会在运行中咬人）。
// 不写来源 id 原文而是取摘要，是因为来源 id 本身可能是对面拼出来的长串，
// 「前缀 + 原文」迟早超宽；超宽之后被拒 = 一条合法候选永久进不了下一段。
func DeriveTaskOperationID(stage AITaskKind, sourceMessageID string) string {
	sum := sha256.Sum256([]byte("task|" + string(stage) + "|" + sourceMessageID))
	return "task-" + hex.EncodeToString(sum[:])[:40]
}

// TaskRequestBody 是那条命令的输入体（protojson 的 lowerCamelCase 面）。
//
// 刻意**不写** `schemaVersion`：对面 `parse_command`（contract.py:263-270）只在
// 「header 与体内都写了版本且不一致」时才拒，只写 header 是合法形态；而这里能确定
// 只有 header 那一处（版本由 `outbox_messages.schema_version` → publisher 写），
// 少写一处就少一个可能对不上的来源。
//
// 也刻意**不写用户身份**：归属只有 `x-user-id` 一个来源，体内出现任何
// user-id 形态都会被 `foreign_user_id` 永久拒绝（contract.py:281-288）。
//
// map 的 json.Marshal 按键名排序 ⇒ 同一个输入永远得到同一个字节串，
// 幂等重放与夹具都比得出。
//
// T2-01 起体里多两个键（`sourceRef`／`sourceOccurredAt`），**恒写**、空串表示
// 「这一段没有体外来源」（今天 fact→pattern、pattern→insight 两条边就是这种形态，
// 来源仍由 `sourceMessageId` 承载）。为什么不条件写：条件写会让同一个函数产出两种
// 键集，而两侧对「体的形状」的闸比的正是键集——形状不确定，闸就成了摆设。
func TaskRequestBody(
	stage AITaskKind,
	admitted Kind,
	sourceMessageID string,
	sourceRef string,
	sourceOccurredAt string,
) (string, error) {
	if err := ValidateSourceRef(sourceRef, sourceOccurredAt); err != nil {
		return "", err
	}
	body, err := json.Marshal(map[string]string{
		"taskType":         stage.TaskType(),
		"sourceKind":       string(admitted),
		"sourceMessageId":  sourceMessageID,
		"sourceRef":        sourceRef,
		"sourceOccurredAt": sourceOccurredAt,
	})
	if err != nil {
		// 不 panic：这条路径在入站消费者里，一个 panic 会打断整个 worker 的循环，
		// 而「载荷编不出来」与「这条消息暂时处理不了」是同一处置——交回阶梯并留日志。
		return "", fmt.Errorf("domain: 组装 %s 的命令体：%w", stage.RoutingKey(), err)
	}
	return string(body), nil
}

// sourceRefPattern 是命令体 `sourceRef` 的取值面（T2-01）：四张来源表之一 ＋ `:` ＋ 36 位 uuid。
//
// 表名写死在正则里而不是「任意标识符」，是因为对面 `parse_command` 拿同一个形状判非法：
// 一边放行 `orders:xxx` 另一边拒，就是一条永久进 DLQ 的命令，而两侧单测各自全绿。
// 号段用 `[0-9a-f-]{36}`（uuid 的规范长度）而不是 `.{36}`：多宽一点就把
// 「拼错了的表名撞上巧是 36 个字符」放成合法。
var sourceRefPattern = regexp.MustCompile(
	`^(memories|reflections|mood_checkins|chat_messages):[0-9a-f-]{36}$`,
)

// enabledSourceTables 是本期真启用的来源（ADR-003）。
//
// 形状合法但这里没开的表走 `source_type_not_enabled` **永久拒绝**（不是 requeue）：
// 重试不会让一个没开的来源变开，只会把队列填满。扩来源＝往这张表加一行，判据不动——
// 这是工作单点名要的「只改一个开关表」。
var enabledSourceTables = map[string]bool{"memories": true}

// 三条 reason 与 Python `contract.py` 的 `REASON_*` 逐字同值：拒绝日志与
// `rejects_by_reason` 计数两侧要能对着同一串排查。
const (
	ReasonSourceRefShape          = "invalid_source_ref"
	ReasonSourceTypeNotEnabled    = "source_type_not_enabled"
	ReasonSourceOccurredAtInvalid = "invalid_source_occurred_at"
)

var (
	ErrSourceRefShape          = errors.New(ReasonSourceRefShape)
	ErrSourceTypeNotEnabled    = errors.New(ReasonSourceTypeNotEnabled)
	ErrSourceOccurredAtInvalid = errors.New(ReasonSourceOccurredAtInvalid)
)

// ValidateSourceRef 判命令体那两个来源键；两个都空＝合法（这一段没有体外来源）。
//
// `sourceOccurredAt` 单独出现是非法的：一个没有来源行的发生时刻，下游只能拿消费时刻兜底，
// 而「最近 30 天」这类窗口一旦用消费时刻，重放的老消息就被算成新近证据。
func ValidateSourceRef(sourceRef, sourceOccurredAt string) error {
	if sourceRef == "" {
		if sourceOccurredAt != "" {
			return fmt.Errorf("%w: 带了 %s 却没带 source_ref，发生时刻没有归属的主体",
				ErrSourceOccurredAtInvalid, sourceOccurredAt)
		}
		return nil
	}
	if !sourceRefPattern.MatchString(sourceRef) {
		return fmt.Errorf("%w: %q（形状＝memories|reflections|mood_checkins|chat_messages 之一 + 冒号 + 36 位 uuid）",
			ErrSourceRefShape, sourceRef)
	}
	table := sourceRef[:strings.Index(sourceRef, ":")]
	if !enabledSourceTables[table] {
		return fmt.Errorf("%w: %s（本期只开 %v，扩来源改 enabledSourceTables）",
			ErrSourceTypeNotEnabled, table, enabledTableNames())
	}
	if sourceOccurredAt != "" {
		if _, err := time.Parse(time.RFC3339, sourceOccurredAt); err != nil {
			return fmt.Errorf("%w: %q 不是 RFC3339", ErrSourceOccurredAtInvalid, sourceOccurredAt)
		}
	}
	return nil
}

func enabledTableNames() []string {
	names := make([]string, 0, len(enabledSourceTables))
	for name := range enabledSourceTables {
		names = append(names, name)
	}
	sort.Strings(names) // map 遍历无序：日志与夹具都要比得出
	return names
}
