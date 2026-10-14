// N8 批 2（curriculum 域）在 HTTP 层的依赖面与逐字口径。
//
// 接口声明在使用方（httpapi）而不是实现方（store），沿用批 1：切流域的**响应字节**
// 才是契约，SQL 是否命中真实表由 store 的实库测单独证。
package httpapi

import (
	"context"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/cutover"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// CurriculumStore 是 curriculum 域需要的四个库动作。
type CurriculumStore interface {
	CurriculumUser(ctx context.Context, userID string) (domain.CurriculumUser, error)
	CurriculumCards(ctx context.Context, userID, kind string, start, end time.Time,
		limit int) ([]domain.CurriculumCard, error)
	JourneyEventOwner(ctx context.Context, eventID string) (domain.CurriculumEvent, error)
	SetJourneyPayload(ctx context.Context, eventID, payload string) error
}

// Batch2 是这批切流域的依赖集合。为 nil 时两条路由不注册（见 server.go 的 routes）。
type Batch2 struct {
	Store CurriculumStore
	Gate  *cutover.Gate
}

// errAIAgreementRequired 的文案逐字抄 backend/services/user_service.py:227-229。
const errAIAgreementRequired = "使用 AI 功能前，请先阅读并同意 AI 数据共享说明 / " +
	"Please review and agree to the AI data sharing notice before using AI features"

// requireCurriculumConsent 复现 router 里那两行的**顺序与次数**：
//
//	await user_service.require_ai_consent(db, user_id)      # get_user → 40401 / 40303
//	user = await user_repository.require_user(db, user_id)  # 同一条 SELECT 再走一次
//
// 两次读同一行看着冗余，但顺序是契约：两次都在「还没碰事件表」之前，
// 颠倒或合并会让「用户已注销」与「卡片越权」谁先报错换一侧。
func (s *Server) requireCurriculumConsent(ctx context.Context, userID string) (
	domain.CurriculumUser, *apierr.Error) {
	first, err := s.batch2.Store.CurriculumUser(ctx, userID)
	if err != nil {
		return first, apierr.New(apierr.CodeDatabaseError, "数据库错误 / Database error")
	}
	if !first.Found {
		return first, apierr.New(apierr.CodeUserNotFound, "用户不存在 / User not found")
	}
	if !first.AIConsent {
		return first, apierr.New(apierr.CodeAIConsentRequired, errAIAgreementRequired)
	}
	// 第二趟：Python 侧的 require_user 再打一条同样的 SELECT，也再判一次 40401。
	second, err := s.batch2.Store.CurriculumUser(ctx, userID)
	if err != nil {
		return second, apierr.New(apierr.CodeDatabaseError, "数据库错误 / Database error")
	}
	if !second.Found {
		return second, apierr.New(apierr.CodeUserNotFound, "用户不存在 / User not found")
	}
	return second, nil
}

// pyTruthy 是 Python 的 `bool(value)`，作用在 json.loads 出来的值上：
// None/false/0/0.0/""/[]/{} 为假，其余为真。
//
// 为什么不能用 isNullish：payload 里的 read_at 可能是空串或 0（历史写入分支），
// `bool("")` 是假而「非 null」是真，拿判空当判真会让这些卡永远算未读，
// unread_count 与 read 两个键一起逐字不同。
func pyTruthy(v *pyValue) bool {
	if v == nil {
		return false
	}
	switch v.kind {
	case pyNull:
		return false
	case pyBool:
		return v.truthy
	case pyInt:
		return v.intLit != "0"
	case pyFloat:
		return v.float != 0
	case pyString:
		return v.str != ""
	case pyArray:
		return len(v.items) > 0
	case pyObject:
		return len(v.members) > 0
	}
	return true
}

// parseJSONColumn 是 journey_repository.parse_json(raw, default) 的那一段：
// 空文本或坏 JSON 都回退默认值，**不抛异常**（历史脏行不能把整个视图打挂）。
func parseJSONColumn(raw string, def *pyValue) *pyValue {
	if raw == "" {
		return def
	}
	v, err := pyJSONLoads([]byte(raw))
	if err != nil {
		return def
	}
	return v
}

// payloadObject 是 CurriculumService._payload：解析结果不是 dict 时同样回退空对象
// （`payload if isinstance(payload, dict) else {}`），所以列里存了数组也当空卡处理。
func payloadObject(raw string) *pyValue {
	v := parseJSONColumn(raw, pyObjectValue())
	if v.kind != pyObject {
		return pyObjectValue()
	}
	return v
}

// refsArray 是 CurriculumService._refs 的 `refs if isinstance(refs, list) else []`。
// 坏值给**空数组而不是 nil**：nil 会序列化成 null，而 Python 那边是 []。
func refsArray(raw string) *pyValue {
	v := parseJSONColumn(raw, pyArrayValue())
	if v.kind != pyArray {
		return pyArrayValue()
	}
	return v
}

// payloadGet 是 `payload.get(key)`：缺键回 None（响应里是 null，不是省略这个键）。
func payloadGet(payload *pyValue, key string) *pyValue {
	if v, ok := payload.get(key); ok {
		return v
	}
	return pyNullValue()
}

// pyISOOffset 是 `datetime.now(timezone.utc).isoformat()` 的口径：与 pyISO 同一条
// 微秒规则（非零固定 6 位、为零整段省略），但 UTC 写 "+00:00" 而不是 "Z"。
// 这两个格式**不是**同一个函数的产物：响应里的 datetime 走 pydantic 序列化器（Z），
// 而 payload.read_at 是 Python 自己 isoformat() 后塞进 JSON 文本的（+00:00）。
func pyISOOffset(t time.Time) string {
	return pyISO(t)[:len(pyISO(t))-1] + "+00:00"
}
