// N8 批 3 的推送时刻核闸：夹具的 push_moment_corpus 由 **Python 现行实现**逐条算出
// （scripts/gen_n8_contract_goldens.py 直接调 `journey_service._push_moment`），
// 这里逐条比 domain.PushMoment 折出的 UTC 瞬间。
//
// 为什么单独成闸，而不是靠 journey 那 46 条 HTTP 用例：录制与回放只隔几分钟，
// 那批用例的 scheduled_at 永远落在同几天里——「本地午夜缺位」「重复的 01:30」
// 「全角/阿拉伯-印度数字」「24:99 越界钳制」「𝟙𝟚:𝟘𝟛（数学粗体，不是 Nd）」
// 一次都没被走到。而 push_time 是 PATCH /user/settings **现在就能写进去**的列
// （VARCHAR(5)、PG 按字符计宽，而 Python 的 `\d` 认 Unicode 十进制数字），
// 所以这不是只有历史脏值才碰得到的路径。
//
// 与 n8_window_test.go 同一手法：两侧读同一批输入，逐字比，并且**把覆盖面本身做成闸**——
// 语料一旦退化成「只测 UTC 与 09:00」，上面的断言全绿也不证明任何事。
package httpapi

import (
	"strconv"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

func TestN8PushMomentCorpus(t *testing.T) {
	file := loadN8Fixture(t)

	zones := map[string]bool{}
	pushes := map[string]bool{}
	days := map[string]bool{}
	pairs := map[string]bool{}
	irreversible := map[string]bool{}
	var transitions int
	for _, e := range file.PushMomentCorpus {
		day, err := time.Parse(n8WallLayout, e.Day)
		if err != nil {
			t.Fatalf("语料的 day %q 不是 date.isoformat() 形状：%v", e.Day, err)
		}
		want, err := time.Parse(n8InstantLayout, e.Want)
		if err != nil {
			t.Fatalf("语料的 want %q 不是 datetime.isoformat() 在 µs==0 时的形状：%v", e.Want, err)
		}

		// NULL 与空串在 Python 的 `user.push_time or "09:00"` 里落同一分支，
		// store 已经把 NULL 折成空串，所以这里也只用空串表达两者（下面单独比它们同值）。
		user := domain.JourneyUser{Timezone: e.TZ}
		shown := "<NULL>"
		if e.PushTime != nil {
			user.PushTimeRaw = *e.PushTime
			shown = strconv.Quote(*e.PushTime)
		}
		got := domain.PushMoment(user, day)
		if got.UTC().Format(n8InstantLayout) != e.Want {
			t.Errorf("push=%s tz=%s day=%s ⇒ Go %s，Python %s",
				shown, e.TZ, e.Day, got.UTC().Format(n8InstantLayout), e.Want)
		}

		zones[e.TZ] = true
		pushes[pushKey(e.PushTime)] = true
		days[e.Day] = true
		pairs[e.TZ+"|"+pushKey(e.PushTime)] = true
		if e.Transition {
			transitions++
		}
		// 「不可逆墙钟」＝ 把 Python 给的那一瞬间换回本地墙钟，再按 Go 原生的
		// time.Date(zone) 折回瞬间，得到的不是同一瞬间。这正是缺位/重复墙钟的签名，
		// 也是批 3 修掉的那一类：裸 time.Date 在这些行会选到另一侧（差一整小时或一天）。
		// 注意这是**超集**（重复墙钟两侧各选一次也算进来），所以只用来数覆盖面，不作判据。
		if loc, err := time.LoadLocation(e.TZ); err == nil {
			local := want.In(loc)
			back := time.Date(local.Year(), local.Month(), local.Day(),
				local.Hour(), local.Minute(), 0, 0, loc).UTC()
			if !back.Equal(want.UTC()) {
				irreversible[e.TZ+"|"+e.Day+"|"+e.Want] = true
			}
		}
	}

	// ── 覆盖面闸 ───────────────────────────────────────────────────────────────
	if len(zones) < 10 {
		t.Errorf("语料只覆盖 %d 个时区名，少于批 3 建闸时的口径", len(zones))
	}
	for _, want := range []string{"", "Not/AZone", "Asia/Shanghai", "Pacific/Apia",
		"Australia/Lord_Howe", "America/Santiago"} {
		if !zones[want] {
			t.Errorf("语料缺时区名 %q（空串与非法名走 safe_zoneinfo 的回退分支，"+
				"Apia 是跨国际日期变更线、Lord_Howe 是半小时 DST、Santiago 是本地午夜缺位）", want)
		}
	}
	if len(pushes) < 20 {
		t.Errorf("语料只有 %d 种 push_time 列原文：这一维多半退化成了常数，"+
			"越界钳制/脏段数/非 ASCII 数字那些分支就没被走过", len(pushes))
	}
	if len(pairs) < 100 {
		t.Errorf("语料只有 %d 个 (时区, push_time) 组合，少于批 3 建闸时的口径", len(pairs))
	}
	if len(days) < 10 {
		t.Errorf("语料只覆盖 %d 个日历日：DST 跳变日多半已被挤出语料", len(days))
	}
	if transitions == 0 {
		t.Error("语料不含任何 DST 跳变日：缺位/重复墙钟这条分支没被走过")
	}
	if len(irreversible) == 0 {
		t.Error("语料里没有任何「墙钟不可逆」的行：这一批最容易写歪的换算没被走过")
	}
	t.Logf("push_moment 语料 %d 条逐字比对（%d 个时区名、%d 种 push_time、%d 个日历日、"+
		"%d 个 (时区,push) 组合、%d 条落在 DST 跳变日、%d 条墙钟不可逆）",
		len(file.PushMomentCorpus), len(zones), len(pushes), len(days), len(pairs),
		transitions, len(irreversible))
}

// TestN8NullPushTimeMatchesEmptyColumn 钉的是**列值**这一维而不是解析：
// NULL 折成空串以后，同一个 (时区, 日) 必须给出与 push_time=” 完全相同的瞬间。
// 两侧都落 `or "09:00"` 是 Python 侧读出来的事实（`backend/services/journey_service.py:231`），
// 但 store 把 NULL 折成空串是 Go 侧的口径——这一条守的是那个折算没有改变语义。
func TestN8NullPushTimeMatchesEmptyColumn(t *testing.T) {
	file := loadN8Fixture(t)
	byKey := make(map[string]n8PushEntry, len(file.PushMomentCorpus))
	for _, e := range file.PushMomentCorpus {
		byKey[e.TZ+"|"+e.Day+"|"+pushKey(e.PushTime)] = e
	}
	var compared, unpaired int
	for _, e := range file.PushMomentCorpus {
		if e.PushTime == nil || *e.PushTime != "" {
			continue
		}
		nullRow, ok := byKey[e.TZ+"|"+e.Day+"|null"]
		if !ok {
			unpaired++ // 语料不是全交叉，这一 (时区,日) 只采了空串那一侧
			continue
		}
		compared++
		if e.Want != nullRow.Want {
			t.Errorf("%s@%s：空串=%s 而 NULL=%s，折算改了语义",
				e.TZ, e.Day, e.Want, nullRow.Want)
		}
	}
	if compared == 0 {
		t.Error("没有 push_time 为 NULL/空串的成对语料行可对照")
	}
	t.Logf("NULL 与空串同值对照 %d 组（另有 %d 组语料只采了空串一侧）", compared, unpaired)
}

// pushKey 把「列值为 NULL」与「列值为空串」分成两个键——这两者在夹具里是两种行，
// 混成一个键就没法证「折算没改语义」了。
func pushKey(p *string) string {
	if p == nil {
		return "null"
	}
	return "str:" + *p
}
