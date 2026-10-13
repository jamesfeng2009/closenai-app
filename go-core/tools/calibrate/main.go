// Command calibrate 把 N4 的算分口径摊成可回测的分布：没有真实用户数据可标定（G-5），
// 所以这里枚举 03 §5 的全部档位组合，按 geomean（默认）与 product（V3 字面连乘）
// 两种口径各算一遍 strength，输出 JSONL 给 scripts/calibrate_evidence_score.py 汇总。
//
// 数值一律来自 domain.DefaultPolicy()，也就是 env 覆盖之后的实际生效值——
// 换 env 重跑就是换一套权重回测，脚本里没有第二套权重表。
//
//	go run ./tools/calibrate -mode=both > out.jsonl
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

var (
	sourceKinds = []domain.SourceKind{
		domain.SourceUserStatement, domain.SourceUserCorrection, domain.SourceUserSelection,
		domain.SourceUserBehavior, domain.SourceUserEmotion, domain.SourceAIExtraction,
		domain.SourceAIInference, domain.SourceFrameworkInference,
	}
	explicitness = []domain.Explicitness{
		domain.Explicit, domain.StronglyImplied, domain.WeaklyImplied, domain.Speculative,
	}
	specificity = []domain.Specificity{
		domain.Specific, domain.Contextual, domain.Generic,
	}
	// 独立观测数与观测年龄：§5 的 Repetition/Temporal 两因子只有这两维是
	// 连续量，取覆盖档位的代表值即可（1/2/3/5 条，0/15/45/100/200/400 天）。
	counts = []int{1, 2, 3, 5}
	ages   = []int{0, 15, 45, 100, 200, 400}
)

type sample struct {
	Mode         string               `json:"mode"`
	SourceKind   domain.SourceKind    `json:"source_kind"`
	Explicitness domain.Explicitness  `json:"explicitness"`
	Specificity  domain.Specificity   `json:"specificity"`
	Independent  int                  `json:"independent"`
	AgeDays      int                  `json:"age_days"`
	Strength     float64              `json:"strength"`
	Class        domain.EvidenceClass `json:"class"`
}

func emit(enc *json.Encoder, policy domain.Policy, mode string, now time.Time) {
	ep := policy.Evidence
	ep.ScoreMode = mode
	n := 0
	for _, kind := range sourceKinds {
		for _, ex := range explicitness {
			for _, sp := range specificity {
				for _, count := range counts {
					for _, age := range ages {
						scored, err := domain.ScoreEvidence(domain.EvidenceSignals{
							SourceKind: kind, Explicitness: ex, Specificity: sp,
							Independent: count, ObservedAt: now.AddDate(0, 0, -age),
						}, ep, now)
						if err != nil {
							fmt.Fprintf(os.Stderr, "calibrate: %v\n", err)
							os.Exit(1)
						}
						_ = enc.Encode(sample{Mode: mode, SourceKind: kind,
							Explicitness: ex, Specificity: sp, Independent: count,
							AgeDays: age, Strength: scored.Strength, Class: scored.Class})
						n++
					}
				}
			}
		}
	}
	_ = enc.Encode(map[string]any{"mode": mode, "samples": n,
		"class_floors": map[string]float64{"A": ep.ClassAFloor, "B": ep.ClassBFloor,
			"C": ep.ClassCFloor}})
}

func main() {
	mode := flag.String("mode", "both", "geomean|product|both")
	flag.Parse()
	now := time.Now().UTC()
	policy := domain.DefaultPolicy()
	enc := json.NewEncoder(os.Stdout)
	switch *mode {
	case "geomean":
		emit(enc, policy, "geomean", now)
	case "product":
		emit(enc, policy, "product", now)
	case "both":
		emit(enc, policy, "geomean", now)
		emit(enc, policy, "product", now)
	default:
		fmt.Fprintf(os.Stderr, "calibrate: 未知 -mode=%q\n", *mode)
		os.Exit(2)
	}
}
