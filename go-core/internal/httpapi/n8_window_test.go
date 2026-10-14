// N8 批 2 的日历核闸：夹具的 week_window_corpus 块由 **Python 现行实现**逐条算出
// （scripts/gen_n8_contract_goldens.py 直接调 safe_zoneinfo/local_day_start_utc），
// 这里逐条比 domain.CurriculumWindow 的四个值。
//
// 为什么单独成闸，而不是靠 curriculum 那 28 条 HTTP 用例：录制与回放只隔几分钟，
// 那批用例的 week_start 永远落在**同一个星期**里——「本地今天与 UTC 今天不同周」
// 「DST 缺位的本地午夜」「周日 23:59:59」一次都没被走到。这一份两侧读同一批输入：
// 16 个时区名（含非法名与空串）× 手选/每 10 天扫描的固定瞬间，外加 2015–2034
// 全年逐日扫出的「本地零点缺位或重复」的那些日子。
package httpapi

import (
	"strings"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

const (
	// n8WallLayout 是 Python `date.isoformat()` 的形状（响应里的 week_start/date）。
	n8WallLayout = "2006-01-02"
	// n8InstantLayout 是 Python `datetime.isoformat()` 在 µs==0 时的形状：
	// UTC 写 "+00:00"，**不是** Go RFC3339 的 "Z"。
	n8InstantLayout = "2006-01-02T15:04:05-07:00"
)

// n8UTCMonday 按「UTC 日历日」算周一，只用来数语料覆盖面（不是被测口径）。
func n8UTCMonday(at time.Time) string {
	u := at.UTC()
	return u.AddDate(0, 0, -(int(u.Weekday())+6)%7).Format(n8WallLayout)
}

func TestN8WeekWindowCorpus(t *testing.T) {
	file := loadN8Fixture(t)

	zones := map[string]bool{}
	crossing, midnightGap, dstWeeks := 0, 0, map[string]bool{}
	for _, e := range file.WeekWindowCorpus {
		at, err := time.Parse(time.RFC3339, e.At)
		if err != nil {
			t.Fatalf("语料的 at %q 不是 RFC3339：%v", e.At, err)
		}
		monday, start, end, today := domain.CurriculumWindow(e.TZ, at.UTC())
		if got := monday.Format(n8WallLayout); got != e.Monday {
			t.Errorf("%s@%s monday = %s，Python %s", e.TZ, e.At, got, e.Monday)
		}
		if got := today.Format(n8WallLayout); got != e.Today {
			t.Errorf("%s@%s today = %s，Python %s", e.TZ, e.At, got, e.Today)
		}
		// 窗口两端比的是**瞬间**：非 UTC 时区的本地周一起点不在 UTC 午夜。
		if got := start.UTC().Format(n8InstantLayout); got != e.Start {
			t.Errorf("%s@%s start = %s，Python %s", e.TZ, e.At, got, e.Start)
		}
		if got := end.UTC().Format(n8InstantLayout); got != e.End {
			t.Errorf("%s@%s end = %s，Python %s", e.TZ, e.At, got, e.End)
		}
		// 注：**不**断言 end-start 恰好 168 小时。含 DST 切换的那一周（如美东
		// 2026-03-02 起的一周）Python 侧就是 23 或 25 小时；墙上日历差 7 天才是契约，
		// 而那由上面 monday/end 两条逐字比守着。
		zones[e.TZ] = true
		if e.Monday != n8UTCMonday(at) {
			crossing++
		}
		// 本地零点不在 UTC 午夜：窗口端点带非零秒/分偏移的形态。
		if !strings.HasSuffix(e.Start, "T00:00:00+00:00") {
			midnightGap++
		}
		// gap 由录制侧标：这一行**本地今天**的零点不唯一（春季拨快跳过 00:00，
		// 或秋季回拨重复 00:00）；今天若是周一，窗口左端点就压在这个时刻上。
		if e.Gap {
			dstWeeks[e.TZ+"|"+e.Monday] = true
		}
	}

	// 覆盖面本身就是闸：语料一旦退化成「只测 UTC 午夜」，上面全绿也不证明任何事。
	if len(zones) < 10 {
		t.Errorf("语料只覆盖 %d 个时区名，少于批 2 建闸时的口径", len(zones))
	}
	for _, want := range []string{"", "Not/AZone", "Asia/Shanghai", "Pacific/Kiritimati"} {
		if !zones[want] {
			t.Errorf("语料缺时区名 %q（空串与非法名走 safe_zoneinfo 的回退分支）", want)
		}
	}
	if crossing == 0 {
		t.Error("没有任何一行落在「本地周 ≠ UTC 周」上：周边界这条分支没被走过")
	}
	if midnightGap == 0 {
		t.Error("没有任何一行的窗口起点带非零 UTC 偏移：本地零点折算 UTC 没被走过")
	}
	// 这一条是本轮修掉的真实缺陷的守门员：Go 曾把缺位的本地午夜「补」成前一日
	// （智利 2026-09-06 跳过 00:00–00:59，week_start 因此差一天）。语料一旦不再含
	// 异常午夜，同一份错误实现会重新全绿。
	if len(dstWeeks) == 0 {
		t.Error("语料不含任何「本地零点缺位/重复」的周：这一分支没被走过")
	}
	t.Logf("week_window 语料 %d 条（%d 个时区名，%d 条跨周边界、%d 条非 UTC 午夜，%d 个异常午夜周）",
		len(file.WeekWindowCorpus), len(zones), crossing, midnightGap, len(dstWeeks))
}

// TestN8InvalidZoneMatchesUTCRow 钉的是回退分支的**结果**而不只是「不 panic」：
// 同一个瞬间，非法时区名与空串必须给出与 UTC 完全一样的四个值。
func TestN8InvalidZoneMatchesUTCRow(t *testing.T) {
	file := loadN8Fixture(t)
	byKey := make(map[string]n8WindowEntry, len(file.WeekWindowCorpus))
	for _, e := range file.WeekWindowCorpus {
		byKey[e.TZ+"|"+e.At] = e
	}
	var compared int
	for _, e := range file.WeekWindowCorpus {
		if e.TZ != "Not/AZone" && e.TZ != "" {
			continue
		}
		want, ok := byKey["UTC|"+e.At]
		if !ok {
			t.Fatalf("语料里 %s@%s 没有对应的 UTC 行，回退分支无法对照", e.TZ, e.At)
		}
		compared++
		if e.Monday != want.Monday || e.Today != want.Today ||
			e.Start != want.Start || e.End != want.End {
			t.Errorf("%s@%s 与 UTC 行不符： %+v vs %+v", e.TZ, e.At, e, want)
		}
	}
	if compared == 0 {
		t.Error("没有非法/空时区名的语料行可对照")
	}
	t.Logf("非法与空时区名各按 UTC 回退，对照 %d 组", compared)
}
