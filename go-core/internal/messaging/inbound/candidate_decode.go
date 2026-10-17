// 候选面的解码与分派（D-R5-8 的映射表在这里，只有一处）。
//
// 三条绑定（`candidate.pattern`／`candidate.insight`／`candidate.*` 通配）今天已经声明在
// `amqp/topology.go:86-92`，但**通配不等于谁都能进 `Admit`**：一条 `candidate.evidence`
// 走 `Admit` 会把「依据」塞进「洞察」的领域语义，而写进 canonical 表就收不回。
// 所以这张表是**封闭**的：表里没有的 `candidate.*` 一律 permanent reject，
// 没有任何默认分支（反面就是 D-R5-8 点名要防的那件事）。
//
// 载荷形态按 `content_type` 分派（D-R5-4）：今天线上是 JSON，且这串 JSON 的**形状**是
// 对应 protobuf 消息的 protojson 形态（`schemaVersion`／`sourceRecordIds` 那套 lowerCamelCase，
// 见 `testdata/proto_contract_cases.json` 与 `tools/protocross`）。
// 于是 Go 侧直接 `protojson.Unmarshal` 进 `gen/closenai/v1` 的生成物再映射成 `domain.*`：
// 生成物就是跨语言契约的事实源，手抄第二份 struct 迟早漂。
//
// ⚠️ `protojson` 默认**拒绝未知字段**，这一条是归属面的一部分：`Candidate` 的
// `user_id`／`personal_model_id` 在 proto 里是 `reserved 3,4,5`（closenai_ai.proto:189-198），
// 提案方要是自报这两个字段，这里当场解码失败，而不是「带着它去查库」。
package inbound

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"

	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"

	closenaiv1 "github.com/jamesfeng2009/closenai-app/go-core/gen/closenai/v1"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// 永久拒绝的码面（14 §7 第三类 ACK 写进 `inbox.error_code` 的那串）。
//
// 这一组串与 Python 侧 `backend/ai/mq/contract.py` 的 `REASON_*` **同名同值**：
// 两侧说的是同一件事，排查时一条 `x-reject-reason` 能同时指回两边的代码。
// 那条跨语言闸在 `backend/tests/test_ai_mq_contract.py`（它读本文件的字面量）。
const (
	ReasonUnknownRoutingKey   = "unknown_routing_key"
	ReasonContentType         = "unsupported_content_type"
	ReasonBodyTooLarge        = "body_too_large"
	ReasonMessageTypeMismatch = "message_type_mismatch"
	ReasonSchemaVersion       = "unsupported_schema_version"
	ReasonMissingUserID       = "missing_user_id"
	ReasonMissingMessageID    = "missing_message_id"
	ReasonNotJSON             = "payload_not_json"
	ReasonPayloadInvalid      = "candidate_payload_invalid"
	ReasonKindMismatch        = "candidate_kind_mismatch"
	ReasonMetadataNotJSON     = "metadata_not_json"
	// ReasonUnknownDomainValue 走的是 `domain.ErrUnknownSignal` 这个**已有哨兵**
	// （evidence_score.go:233）：未知算分档位是拼写问题，重投一万次也不会变成合法，
	// 所以它必须转成永久拒绝去留痕，而不是按瞬态在阶梯上无限撞库。
	ReasonUnknownDomainValue = "unknown_domain_value"
)

// SupportedSchemaVersion 是本消费者认得的 schema 版本，**只有一条**。
// 必须与对面 Python 的 `SUPPORTED_SCHEMA_VERSIONS`、以及出箱侧写进
// `x-schema-version` 的那个值三方同值；闸在两边的契约测里。
const SupportedSchemaVersion = "1"

// Entry 是一条候选走哪个领域入口（D-R5-8）。取值逐字用方法名，
// 因为指标面与日志里要能一眼指回 `domain` 的那三个函数。
type Entry string

const (
	EntryAdmit          Entry = "Admit"
	EntryAdmitEvidence  Entry = "AdmitEvidence"
	EntryAdmitProactive Entry = "AdmitProactive"
)

// CandidateEntries 是 routing key → 领域入口的**唯一**映射表。
//
// 五个键逐字对着 `topology.go` 的三条绑定展开：通配 `candidate.*` 覆盖的是
// fact／evidence／proactive 家族，而**每个家族各自进门**，不是全都进门。
var CandidateEntries = map[string]Entry{
	"candidate.fact":      EntryAdmit,
	"candidate.pattern":   EntryAdmit,
	"candidate.insight":   EntryAdmit,
	"candidate.evidence":  EntryAdmitEvidence,
	"candidate.proactive": EntryAdmitProactive,
}

// CandidateKinds 是三个走 `Admit` 的键必须在体内自报的那件事
// （`closenai_ai.proto:52-56` 的 `Kind` 枚举）。evidence／proactive 没有这个字段。
var CandidateKinds = map[string]closenaiv1.Kind{
	"candidate.fact":    closenaiv1.Kind_KIND_FACT,
	"candidate.pattern": closenaiv1.Kind_KIND_PATTERN,
	"candidate.insight": closenaiv1.Kind_KIND_INSIGHT,
}

// Call 是一次已经决定好走哪扇门的领域调用。
//
// 它只接 `Domain`，不接 `Store`：三个入口各自开事务（`store/canonical_repo.go:53` 的
// `InTx` 每次都从池里 `Begin`，不复用 ctx 上已有的事务）。这件事让 18 §8 的
// 「副作用与幂等行同一事务」在 Go 侧做不到严格成立，风险与两种改法登记在 tasks.md 的 R5b；
// 本批不改 `domain`／`store` 的签名（授权文件清单里没有它们）。
type Call func(ctx context.Context, d Domain) error

// Decode 校验传输契约并把载荷映射成一次领域调用。
//
// 第二个返回值是 `*domain.PermanentRejection`：非 nil ⇒ 这条消息永远不该再投。
// `Runner.Handle` 把本函数放进 inbox 事务里返回它，于是走 delivery.go:341-362 那条
// 「主事务回滚＋事务外补 failed 留痕」的分道，然后 ACK。
//
// 这里**不碰数据库、不补上下文**：契约面只判传输形状＋归属＋大小（D-R5-8b 的有界黑盒，
// V3 12:84-97「Go should construct bounded context」），领域闸门（证据强度、越权引用、
// 置信度）全在 `domain` 那三个入口里。
func Decode(d Delivery, maxBodyBytes int) (Call, *domain.PermanentRejection) {
	entry, ok := CandidateEntries[d.RoutingKey]
	if !ok {
		return nil, reject(ReasonUnknownRoutingKey,
			fmt.Sprintf("routing key=%q 不在映射表里（表=%v）；按 D-R5-8，表里没有的 candidate.* 两侧都拒，"+
				"不许默认走 Admit", d.RoutingKey, knownKeys()))
	}
	if d.ContentType != ContentTypeJSON {
		return nil, reject(ReasonContentType,
			fmt.Sprintf("content_type=%q，本消费者只解 %s。14 §5 要求的 application/protobuf "+
				"要连着 schema_version 一起切，那一批是 O1（见 D-R5-4 与 amqp/publisher.go:27-33）",
				d.ContentType, ContentTypeJSON))
	}
	if len(d.Body) > maxBodyBytes {
		return nil, reject(ReasonBodyTooLarge,
			fmt.Sprintf("消息体 %d 字节 > 上限 %d（14 §9：超限不许解码，也不许转存后继续）",
				len(d.Body), maxBodyBytes))
	}
	// message_type 与键同源：命令面那条规则是「键把点换成下划线」（commands.go:47），
	// 回投面同形：`candidate.pattern` ⇒ `candidate_pattern`。
	if want, got := messageTypeFor(d.RoutingKey), strings.TrimSpace(d.MessageType); want != got {
		return nil, reject(ReasonMessageTypeMismatch,
			fmt.Sprintf("routing key=%q 要求 message_type=%q，实收 %q", d.RoutingKey, want, got))
	}
	if sv := d.HeaderVersion(HeaderSchemaVersion); sv != SupportedSchemaVersion {
		return nil, reject(ReasonSchemaVersion,
			fmt.Sprintf("x-schema-version=%q，本消费者只认 %q（不认识还重投＝毒消息在阶梯上无限撞）",
				sv, SupportedSchemaVersion))
	}
	userID := strings.TrimSpace(d.HeaderString(HeaderUserID))
	if userID == "" {
		// 归属只有这一个来源：`Candidate` 里那两列是 reserved（本文件头），
		// 提案体不许自报用户，所以「没有 x-user-id」不等于「让库里去猜」。
		return nil, reject(ReasonMissingUserID,
			"消息头没有 x-user-id：候选的归属只能从 Envelope 取，缺了就等于把一条产出交给未知用户")
	}

	switch entry {
	case EntryAdmit:
		return decodeCandidate(d, userID)
	case EntryAdmitEvidence:
		return decodeEvidence(d, userID)
	case EntryAdmitProactive:
		return decodeProactive(d, userID)
	}
	// 到不了：CandidateEntries 的值只有这三扇门。写成「到不了就拒」而不是 panic，
	// 是因为这张表将来加第四扇门时编译器不会替这条 switch 报错，而闸会（contract_test.go）。
	return nil, reject(ReasonUnknownRoutingKey,
		fmt.Sprintf("entry=%q 没有解码分支（表与分支漂了）", entry))
}

// checkBodyVersion 让体内自报的版本与 header 对上（对面 Python 在
// `contract.py:265-270` 做的是同一件事，两侧不许一侧严一侧松）。
// 只判「不一致」：体内没写版本不算违规——`x-schema-version` 是 publisher 唯一写它的那一处，
// 载荷里那个字段是提案消息自带的，历史消息与手写夹具都可能留空。
//
// 两侧都用 `HeaderVersion` 而不是 `HeaderString`：这一格的上线形态本来就有 int 与 str 两种
// （对面写 str、我们的发布侧写 int，见 inbound.go 的 HeaderVersion），
// 只吃其中一种会把对面正常发来的消息判成契约级非法。
func checkBodyVersion(d Delivery, bodyVersion string) *domain.PermanentRejection {
	got := strings.TrimSpace(bodyVersion)
	if got == "" {
		return nil
	}
	if want := d.HeaderVersion(HeaderSchemaVersion); got != want {
		return reject(ReasonSchemaVersion,
			fmt.Sprintf("体内 schemaVersion=%q 与 header 的 %q 不一致：一条消息里两个版本号，"+
				"按哪一个解都不知道，所以两侧都不解", got, want))
	}
	return nil
}

func decodeCandidate(d Delivery, userID string) (Call, *domain.PermanentRejection) {
	var msg closenaiv1.Candidate
	if bad := unmarshalBody(d, &msg); bad != nil {
		return nil, bad
	}
	if bad := checkBodyVersion(d, msg.GetSchemaVersion()); bad != nil {
		return nil, bad
	}
	// routing key 与体内 kind 必须同义：不一致时「谁进门」和「进门写成什么」会分叉，
	// 而 `Admit` 只信体内的 kind（它按 kind 选落库分支）。
	want := CandidateKinds[d.RoutingKey]
	if msg.GetKind() != want {
		return nil, reject(ReasonKindMismatch,
			fmt.Sprintf("routing key=%q 要求 kind=%s，体内是 %s", d.RoutingKey, want, msg.GetKind()))
	}
	kind, ok := mappedKind(want)
	if !ok {
		return nil, reject(ReasonKindMismatch, fmt.Sprintf("kind=%s 映射不到 domain.Kind", want))
	}
	c := domain.Candidate{
		UserID:          userID,
		Kind:            kind,
		Origin:          msg.GetOrigin(),
		SubjectKey:      msg.GetSubjectKey(),
		Predicate:       msg.GetPredicate(),
		ObjectValue:     msg.GetObjectValue(),
		PatternType:     msg.GetPatternType(),
		Scope:           msg.GetScope(),
		Description:     msg.GetDescription(),
		Fingerprint:     msg.GetFingerprint(),
		Statement:       msg.GetStatement(),
		ClaimType:       msg.GetClaimType(),
		Confidence:      msg.GetConfidence(),
		DetectorVersion: msg.GetDetectorVersion(),
		EvidenceIDs:     msg.GetEvidenceIds(),
		PatternIDs:      msg.GetPatternIds(),
		GenerationRunID: msg.GetGenerationRunId(),
		GenerationModel: msg.GetGenerationModel(),
		GenerationVer:   msg.GetGenerationVer(),
		Safety:          toSafety(msg.GetSafety()),
	}
	// 窗口是「两个都在才算」，不许用非零值猜（proto3 的 0 值陷阱，closenai_ai.proto:221-224）。
	if from, to := msg.GetWindowFrom(), msg.GetWindowTo(); from != nil && to != nil {
		c.WindowFrom, c.WindowTo, c.HasWindow = from.AsTime(), to.AsTime(), true
	}
	return func(ctx context.Context, dm Domain) error {
		_, err := dm.Admit(ctx, c)
		return classify(err)
	}, nil
}

func decodeEvidence(d Delivery, userID string) (Call, *domain.PermanentRejection) {
	var msg closenaiv1.EvidenceCandidate
	if bad := unmarshalBody(d, &msg); bad != nil {
		return nil, bad
	}
	if bad := checkBodyVersion(d, msg.GetSchemaVersion()); bad != nil {
		return nil, bad
	}
	// metadata 原样进 `evidence.metadata_json`。在这里判语法而不是让库里撞：
	// 非法 JSON 撞进 JSON 列是**可恢复错误的形态**（DB 回错），于是瞬态路径会让它在
	// 阶梯上无限重投——而它永远不会变合法。
	if raw := msg.GetMetadataJson(); raw != "" && !json.Valid([]byte(raw)) {
		return nil, reject(ReasonMetadataNotJSON,
			"metadata_json 不是合法 JSON：它要原样落进 evidence.metadata_json，非法语法属契约级非法")
	}
	source := msg.GetSource()
	c := domain.EvidenceCandidate{
		UserID:          userID,
		SourceKind:      domain.SourceKind(enumValue(sourceKindPrefix, msg.GetSourceKind().String())),
		SourceTable:     source.GetSourceTable(),
		SourceID:        source.GetSourceId(),
		EvidenceType:    msg.GetEvidenceType(),
		Role:            msg.GetRole(),
		Observation:     msg.GetObservation(),
		Excerpt:         msg.GetExcerpt(),
		Metadata:        json.RawMessage(msg.GetMetadataJson()),
		Explicitness:    domain.Explicitness(enumValue(explicitnessPrefix, msg.GetExplicitness().String())),
		Specificity:     domain.Specificity(enumValue(specificityPrefix, msg.GetSpecificity().String())),
		GenerationRunID: msg.GetGenerationRunId(),
		// 一个都不采信（service_evidence.go:46-48 逐字），这里只搬运，不判定。
		ProposedIndependentIDs: msg.GetProposedIndependentIds(),
	}
	if at := source.GetOccurredAt(); at != nil {
		c.OccurredAt = at.AsTime()
	}
	return func(ctx context.Context, dm Domain) error {
		_, err := dm.AdmitEvidence(ctx, c)
		return classify(err)
	}, nil
}

func decodeProactive(d Delivery, userID string) (Call, *domain.PermanentRejection) {
	var msg closenaiv1.ProactiveDraft
	if bad := unmarshalBody(d, &msg); bad != nil {
		return nil, bad
	}
	if bad := checkBodyVersion(d, msg.GetSchemaVersion()); bad != nil {
		return nil, bad
	}
	c := domain.ProactiveCandidate{
		UserID:          userID,
		ID:              msg.GetCandidateId(), // 四个提案里唯一带提案侧 id 的那条（幂等重跑位）
		TriggerType:     domain.TriggerType(enumValue(triggerTypePrefix, msg.GetTriggerType().String())),
		TriggerSource:   domain.TriggerSource(enumValue(triggerSourcePrefix, msg.GetTriggerSource().String())),
		Message:         msg.GetMessage(),
		Confidence:      msg.GetConfidence(),
		EvidenceIDs:     msg.GetEvidenceIds(),
		RefIDs:          msg.GetRefIds(),
		AnchorRefType:   domain.RefType(enumValue(refTypePrefix, msg.GetAnchorRefType().String())),
		AnchorRefID:     msg.GetAnchorRefId(),
		JourneyEventID:  msg.GetJourneyEventId(),
		GenerationRunID: msg.GetGenerationRunId(),
		GenerationModel: msg.GetGenerationModel(),
		GenerationVer:   msg.GetGenerationVer(),
		Safety:          toSafety(msg.GetSafety()),
	}
	// 显式在场性：proto 的 optional 指针在不在，比时间值是不是零更有意义
	// （proactive.go:210-213 的 HasScheduledAt/HasExpiresAt 就是这个意思）。
	if at := msg.GetScheduledAt(); at != nil {
		c.ScheduledAt, c.HasScheduledAt = at.AsTime(), true
	}
	if at := msg.GetExpiresAt(); at != nil {
		c.ExpiresAt, c.HasExpiresAt = at.AsTime(), true
	}
	return func(ctx context.Context, dm Domain) error {
		_, err := dm.AdmitProactive(ctx, c)
		return classify(err)
	}, nil
}

// unmarshalBody 先判 JSON 语法、再进生成物。分两步是为了给出**可诊断的 reason**：
// 「body 根本不是 JSON」与「JSON 合法但字段不认识」在排查里是两件事，
// 而后者通常意味着对面换了一版 proto 没通知这边（自报 reserved 字段也算这一类）。
func unmarshalBody(d Delivery, msg proto.Message) *domain.PermanentRejection {
	if !json.Valid(d.Body) {
		return reject(ReasonNotJSON,
			fmt.Sprintf("消息体不是合法 JSON（%d 字节）：payload_not_json 属 14 §7 永久拒绝的四类之一",
				len(d.Body)))
	}
	if err := protojson.Unmarshal(d.Body, msg); err != nil {
		return reject(ReasonPayloadInvalid,
			fmt.Sprintf("protojson 解码 %T 失败：%v（未知字段会被拒，这是归属面的一部分，见本文件头）",
				msg, err))
	}
	return nil
}

// classify 只动**已有哨兵**那一类：
//   - `domain.ErrUnknownSignal`（枚举拼写）⇒ 转成永久拒绝，让它去 inbox 留痕而不是上阶梯；
//   - 其余（DB 抖动、连接、租约）⇒ 原样返回，由 Handle 按瞬态处理、NACK 重投。
//
// 这里刻意不做「看错误串里有没有 required 就当永久」那种猜测：
// 猜错的方向是把一次真 DB 抖动永久记账，那比多撞几次阶梯贵得多。
func classify(err error) error {
	if err == nil {
		return nil
	}
	if errors.Is(err, domain.ErrUnknownSignal) {
		return domain.PermanentRejection{Code: ReasonUnknownDomainValue, Detail: err.Error()}
	}
	return err
}

// toSafety 把 proto 的 Safety 落成 Go 侧的指针。
//
// D-8 的方向：`SAFETY_STATUS_UNSPECIFIED` **不当成放行**，落成 nil ⇒ Gate 第 6 关按
// 「判定缺失」fail-closed（candidate.go:65 逐字）。对面 Python 已经在出网前拦掉这一种
// （`contract.py` 的 `missing_safety_status`），这里再落一次不是重复劳动，
// 而是「不许任何一侧把 UNSPECIFIED 读成 ok」。
func toSafety(s *closenaiv1.Safety) *domain.Safety {
	if s == nil {
		return nil
	}
	status, ok := mappedSafetyStatus(s.GetStatus())
	if !ok {
		return nil
	}
	return &domain.Safety{Status: status, Categories: s.GetCategories()}
}

// 枚举值名的前缀（`closenai_ai.proto:52-116`／`closenai_common.proto:84-101`）。
//
// proto 的值名是「前缀＋域内值」的大写下划线形态（`SOURCE_KIND_USER_STATEMENT`），
// `domain` 那侧是小写下划线（`user_statement`）；两边逐字对齐过（对比
// evidence_score.go:20-27 与 proactive.go:18-52 的常量表），所以这里只做去前缀＋小写。
const (
	sourceKindPrefix    = "SOURCE_KIND_"
	explicitnessPrefix  = "EXPLICITNESS_"
	specificityPrefix   = "SPECIFICITY_"
	triggerTypePrefix   = "TRIGGER_TYPE_"
	triggerSourcePrefix = "TRIGGER_SOURCE_"
	refTypePrefix       = "REF_TYPE_"
	safetyStatusPrefix  = "SAFETY_STATUS_"
	kindPrefix          = "KIND_"
)

// enumValue 去掉前缀并小写；不匹配前缀或是 UNSPECIFIED 时回空串。
//
// 空串不是「未知的一种新写法」，而是**领域侧已经认得的失败输入**：
// `checkEvidenceCandidate` 会带 `ErrUnknownSignal` 回来（service_evidence.go:198-206），
// proactive 的 Gate 会把未知 trigger 判成 reject ⇒ 不在这里发明第二种拒绝语言。
func enumValue(prefix, name string) string {
	rest, ok := strings.CutPrefix(name, prefix)
	if !ok || rest == "UNSPECIFIED" {
		return ""
	}
	return strings.ToLower(rest)
}

func mappedKind(k closenaiv1.Kind) (domain.Kind, bool) {
	switch domain.Kind(enumValue(kindPrefix, k.String())) {
	case domain.KindFact:
		return domain.KindFact, true
	case domain.KindPattern:
		return domain.KindPattern, true
	case domain.KindInsight:
		return domain.KindInsight, true
	default:
		return "", false
	}
}

func mappedSafetyStatus(s closenaiv1.SafetyStatus) (domain.SafetyStatus, bool) {
	switch domain.SafetyStatus(enumValue(safetyStatusPrefix, s.String())) {
	case domain.SafetyOK:
		return domain.SafetyOK, true
	case domain.SafetyCrisis:
		return domain.SafetyCrisis, true
	case domain.SafetyUnsafe:
		return domain.SafetyUnsafe, true
	case domain.SafetyUnavailable:
		return domain.SafetyUnavailable, true
	default:
		return "", false
	}
}

// messageTypeFor 把 routing key 换成 message_type（点→下划线，与命令面同一条规则）。
// 通配键 `candidate.*` 过不了 Decode 的第一道门，所以这里不必为它特判。
func messageTypeFor(routingKey string) string {
	return strings.ReplaceAll(routingKey, ".", "_")
}

func knownKeys() []string {
	out := make([]string, 0, len(CandidateEntries))
	for k := range CandidateEntries {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func reject(code, detail string) *domain.PermanentRejection {
	return &domain.PermanentRejection{Code: code, Detail: detail}
}
