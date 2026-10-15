// POST /api/v1/onboarding/first-read 的 Go 侧实现（N8 批 4，onboarding 域）。
//
// 逐字对应 first_read_service.submit（:172-198）的五步：
//
//	card    = self.build_first_read(answers, persona_type, language)   # 纯计算，零 I/O
//	user    = await user_repository.get_user(db, user_id)              # status='active'
//	if user is not None: card["card_payload"]["share_url"] = referral_link(site, code)
//	profile = await onboarding_repository.upsert(db, user_id, persona_type, answers, card, language)
//	if user is not None: await journey_service.prefill(db, user, base_day=local_today(tz))
//	return dict(card["card_payload"]) | {"id": profile.id}
//
// 三处「容易各自顺手改好」的地方，逐条都是契约：
//  1. share_url **在落库前**就定死（:184-185 的注释写明「事后改不落盘」）。所以响应里那个
//     串必须与 first_read 列里那个逐字相同——而码是事务里定的（撞码会重生成），
//     于是 handler 把回调收到的**最后一次**输入带出来复用，不再另算一次。
//  2. upsert 的条件与另两处不同源：账号不存在/已注销时照样写 profile（见 batch4.go 头注释）。
//  3. prefill 的 base_day 是 `local_today(user.timezone)`（:195），而 GET /journey/events 的
//     ensure_prefilled 用的是 profile.created_at 的本地日。同一个 POST 之后紧跟 GET
//     会把同一批行的 scheduled_at 按另一个基准日改写——两侧都这样，不是缺陷，
//     但夹具必须按「POST 再 GET」的真实顺序录才观测得到（批 3 的 first_read 连带后果同源）。
package httpapi

import (
	"encoding/json"
	"net/http"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

func (s *Server) handleFirstReadSubmit(w http.ResponseWriter, r *http.Request) {
	userID, apiErr := s.middleware.RequireBearer(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	if s.batch4.Gate.Delegate(w, r, "onboarding", userID) {
		return
	}
	raw, err := readBody(r)
	if err != nil {
		writeJSONError(w, r, asAPIError(err))
		return
	}
	body, entries := validateFirstReadBody(raw, r.Header.Get("Content-Type"))
	if len(entries) > 0 {
		writePyInvalid(w, r, entries)
		return
	}
	rc := fromContext(r.Context())

	// 一次请求只读一次时钟。Python 侧 build_first_read 的 utc_now()（:163）与
	// prefill 用的 local_today()（:195）是两次 datetime.now()，相差微秒；两处落点
	// 又分属不同观测面（generated_at 进响应与列文本，today 只决定 scheduled_at 的日序），
	// 所以「合并成一次」不改变任何可比对的字节，只避免同一次请求跨到另一日。
	now := s.now().UTC()
	card := domain.BuildFirstRead(body.Answers, body.PersonaType, body.Language)

	var resolved domain.OnboardingSubmitInput
	profileID, storeErr := s.batch4.Store.SubmitOnboarding(r.Context(), userID,
		func(in domain.OnboardingSubmitInput) (domain.OnboardingWrite, error) {
			resolved = in
			return s.buildFirstWrite(in, card, body, now)
		})
	if storeErr != nil {
		// 三处写任一失败 ⇒ 整事务回滚（Python 用的是同一个 session）。
		// 推荐码「连撞 5 次」那条显式失败支也落在这同一格：2^38 的空间取 5 次，
		// 夹具与真库都造不出观测点，所以不为它单独成码（已在 tasks.md 批 4 节登记）。
		writeDBError(w, r)
		return
	}

	shareURL := s.cfg.ShareLandingURL()
	if resolved.User.Found {
		shareURL = referralLink(s.cfg.SiteURL, resolved.Code)
	}
	out := firstReadCardPayload(card, shareURL, now)
	// data = dict(card["card_payload"]); data["id"] = profile.id（:196-197）
	// ⇒ id 是**追加**在九键之后的第十键，不是插在中间。
	out.set("id", pyStringValue(profileID))
	encoded, err := out.json()
	if err != nil {
		writeJSONError(w, r, apierr.New(apierr.CodeInternalError,
			"服务器内部错误 / Internal server error"))
		return
	}
	WriteSuccess(w, json.RawMessage(encoded), rc.traceID, rc.userID)
}

// buildFirstWrite 把「校验后的卡片」折成三处写要的文本。每次重试（撞码）都会重新折，
// 因为 first_read 列里嵌着 share_url，而 share_url 嵌着码。
//
// 分隔符口径沿用批 3：列文本走 pyDumpsSpaced（`", "` / `": "`），响应面走紧凑形。
// first_read 列尤其要紧——GET 是拿它复现响应的，两侧写出的字节不同就会在
// 「切流前后同一个账号的同一次恢复」上表现为不同文本。
func (s *Server) buildFirstWrite(in domain.OnboardingSubmitInput, card domain.FirstReadCard,
	body *firstReadBody, now time.Time) (domain.OnboardingWrite, error) {
	shareURL := s.cfg.ShareLandingURL()
	if in.User.Found {
		shareURL = referralLink(s.cfg.SiteURL, in.Code)
	}
	outer := firstReadOuter(card, shareURL, now)
	firstReadText, err := pyDumpsSpaced(outer)
	if err != nil {
		return domain.OnboardingWrite{}, err
	}
	answersText, err := pyDumpsSpaced(firstReadAnswersValue(body.Answers))
	if err != nil {
		return domain.OnboardingWrite{}, err
	}
	out := domain.OnboardingWrite{
		PersonaType:   card.PersonaType,
		AnswersText:   answersText,
		FirstReadText: firstReadText,
		Language:      body.Language,
	}
	if !in.User.Found {
		return out, nil
	}
	// base_day=None ⇒ day0 用「此刻用户本地的今天」，与 :195 的 local_today(tz) 同形。
	// 于是六行的 day_no 全 ≥0、Past 全假 ⇒ 一行都不会被置 skipped。
	plan := domain.JourneyPrefillPlan(in.User, nil, now)
	rows := make([]domain.JourneyPrefill, 0, len(plan))
	for _, row := range plan {
		text, err := pyDumpsSpaced(prefillPayload(in.User, row))
		if err != nil {
			return domain.OnboardingWrite{}, err
		}
		rows = append(rows, domain.JourneyPrefill{
			Kind: row.Kind, DayNo: row.DayNo, ScheduledAt: row.ScheduledAt,
			Payload: text, Past: row.Past,
		})
	}
	out.JourneyRows = rows
	return out, nil
}

// firstReadCardPayload 是 build_first_read 的九键 card_payload（:154-164）。
// 书写序就是那个 dict 字面量的顺序；persona_type 为 None 时写 null、**键不省略**。
func firstReadCardPayload(card domain.FirstReadCard, shareURL string, now time.Time) *pyValue {
	v := pyObjectValue()
	v.set("card_type", pyStringValue("first_read"))
	v.set("language", pyStringValue(card.Locale))
	v.set("persona_type", personaValue(card.PersonaType))
	v.set("assertions", pyStringsValue(card.Assertions))
	v.set("summary", pyStringValue(card.Summary))
	v.set("closer", pyStringValue(card.Closer))
	v.set("answer_count", pyIntValue(card.AnswerCount))
	v.set("share_url", pyStringValue(shareURL))
	// `utc_now().isoformat()` ⇒ 带 "+00:00" 而不是 "Z"：这条键不是 datetime 对象
	// 过 pydantic，而是 service 自己折好的**字符串**，所以格式由 isoformat 决定（批 3 同判据）。
	v.set("generated_at", pyStringValue(pyISOOffset(now)))
	return v
}

// firstReadOuter 是 submit 落库的那个四层 dict（build_first_read 的返回值 :165-170）。
// 键序 assertions/summary/closer/card_payload；card_payload 与响应面是**同一份**内容。
func firstReadOuter(card domain.FirstReadCard, shareURL string, now time.Time) *pyValue {
	v := pyObjectValue()
	v.set("assertions", pyStringsValue(card.Assertions))
	v.set("summary", pyStringValue(card.Summary))
	v.set("closer", pyStringValue(card.Closer))
	v.set("card_payload", firstReadCardPayload(card, shareURL, now))
	return v
}

// firstReadAnswersValue 是 upsert 的 answers 列内容：
// `[a.model_dump() for a in request.answers]`⇒每项两键、声明序 q/option。
// 请求里多余的键已经在校验层被 extra='ignore' 丢掉，所以这里从校验后的结构重建。
func firstReadAnswersValue(answers []domain.FirstReadAnswerItem) *pyValue {
	items := make([]*pyValue, 0, len(answers))
	for _, answer := range answers {
		item := pyObjectValue()
		item.set("q", pyStringValue(answer.Q))
		item.set("option", pyStringValue(answer.Option))
		items = append(items, item)
	}
	return pyArrayValue(items...)
}

func pyStringsValue(list []string) *pyValue {
	items := make([]*pyValue, 0, len(list))
	for _, item := range list {
		items = append(items, pyStringValue(item))
	}
	return pyArrayValue(items...)
}

// personaValue 是 `(persona_type or "").upper() or None`（:157）：
// 假值写 null，但键保留。
func personaValue(personaType string) *pyValue {
	if personaType == "" {
		return pyNullValue()
	}
	return pyStringValue(personaType)
}
