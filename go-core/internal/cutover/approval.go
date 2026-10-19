// T6-04（2026-10-20）· 切流放量的审批位。
//
// 工单 §为什么 那句话是这道闸的全部理由：`10-config.yaml` 的八个 `CLOSEN_CUTOVER_*`
// 现在全是 "0"，那是**当前状态**，不是约束。今天把一个域拨到 50 的动作是
// 「kubectl edit configmap 一行」——没有第二个人签字，也没有任何一处代码反对。
// 于是本文件只回答一个问题：production 档里，只要有一个百分比 > 0，
// 就必须有人把 `CLOSEN_CUTOVER_APPROVED` 显式设成 "1"；否则**拒绝启动**。
//
// 三条口径（决策表 D-T604-7，逐字在 CLOSENAI_V3_TASK_BOARD.md 的 T6-04 节）：
//
//	· 只有 "1" 算已签。取值面（只认 ""|0|1，别的拒启）在 config.Load，
//	  这里只管「没签就不能放量」，两侧不重复判一遍同一个字符串。
//	· 触发条件是 production/prod，**不含 staging**：staging 的百分比只落在它自己的库上，
//	  而它打到生产库那条路已由 config/env_isolation.go 切断（拒启，不是告警）。
//	· 判的是 main.go 里那张**同一个** map（八格只构造一次，D-T604-10）：
//	  审批看一份数、灰度闸看另一份数，就等于没看。
package cutover

import (
	"fmt"
	"sort"
	"strings"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/config"
)

// ApprovalError 是「放量了，但没人签字」。
type ApprovalError struct {
	Environment string
	Domains     []string // 正在放量（>0）的那些域，已按字典序排好
	Approved    string   // CLOSEN_CUTOVER_APPROVED 的原值（""＝根本没设）
}

func (e *ApprovalError) Error() string {
	keys := make([]string, 0, len(e.Domains))
	for _, d := range e.Domains {
		keys = append(keys, EnvKeyOf(d))
	}
	return fmt.Sprintf(
		"ENVIRONMENT=%s 下这些域正在放量：%s（%s）——必须先有审批位 CLOSEN_CUTOVER_APPROVED=1，现在读到 %q。"+
			"放量是一次独立上线动作（双签），不是改一行 ConfigMap：把键设回 0 回滚，或按流程签字后设成 1",
		e.Environment, strings.Join(keys, ", "), strings.Join(e.Domains, "/"), e.Approved)
}

// CheckApproval 是判据本体（不读 env、不碰 Config，好让闸直接喂表）。
//
// 顺序是有意如此：先问「这一档要不要签字」，再数「有没有域在放量」。
// 反过来写会让 development 的单测进程因为一张没人签过的空表起不来。
func CheckApproval(environment string, percents map[string]int, approved string) error {
	if !config.IsProductionEnv(environment) {
		return nil
	}
	var live []string
	for domain, pct := range percents {
		if pct > 0 {
			live = append(live, domain)
		}
	}
	if len(live) == 0 {
		return nil
	}
	if approved == "1" {
		return nil
	}
	sort.Strings(live)
	return &ApprovalError{Environment: environment, Domains: live, Approved: approved}
}
