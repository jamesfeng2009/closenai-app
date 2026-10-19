// 归属回查面的两条闸（T2-03）：都**不需要数据库**，因此 CI 的 Go 作业（没有
// TEST_DATABASE_URL，见 .github/workflows 与「取证能力缺口」台账）一定跑得到。
//
// 为什么这里要有闸而不是靠集成测：真库测在没有库的环境里会 Skip，而这两张表
// （domain 的准入白名单 ⇄ store 的回查语句）漂了的后果是「每一条正常候选都被
// 永久拒绝」，那是必须天天红的一条判据，不能挂在可能跳过的作业上。
package store

import (
	"sort"
	"strings"
	"testing"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

func TestSourceOwnerQueriesCoverExactlyTheAdmissibleTables(t *testing.T) {
	probeKeys := make([]string, 0, len(sourceOwnerQueries))
	for key := range sourceOwnerQueries {
		probeKeys = append(probeKeys, key)
	}
	sort.Strings(probeKeys)
	want := append([]string(nil), domain.AdmissibleSourceTables...)
	sort.Strings(want)

	if strings.Join(probeKeys, ",") != strings.Join(want, ",") {
		t.Fatalf("回查语句面=%v 与准入面=%v 不同集合：少一格 ⇒ 那条表的候选全被读成"+
			"「行不存在」而永久拒绝；多一格 ⇒ 白名单没开却查了库，等于悄悄扩了准入面",
			probeKeys, want)
	}
	// 正数证人：准入面本身不许被扫成空集（空集合与空 map 也「同集合」）。
	if len(want) == 0 {
		t.Fatal("domain.AdmissibleSourceTables 是空集，上面那条相等判据就永远为真")
	}
}

func TestSourceOwnerQueriesReadOneOwnerColumn(t *testing.T) {
	for table, query := range sourceOwnerQueries {
		lower := strings.ToLower(strings.Join(strings.Fields(query), " "))
		for _, want := range []string{"select user_id from ", table, " where id = $1"} {
			if !strings.Contains(lower, want) {
				t.Errorf("source_table=%q 的语句是 %q，缺 %q：这一格只许回答「这一行的"+
					"user_id 是谁」，多读一列或换个键都会让归属判据指错地方",
					table, query, want)
			}
		}
		if strings.Contains(lower, ";") {
			t.Errorf("source_table=%q 的语句带了第二条语句：%q", table, query)
		}
	}
}
