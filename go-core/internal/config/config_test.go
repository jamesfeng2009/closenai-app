package config

import (
	"os"
	"testing"
)

// TestN9EvidenceReadSwitchValues 钉 D-3 回退开关的取值面：默认 on、可显式 off、
// 拼错一律拒绝启动——「以为关了其实没关」的开关比没有开关更糟。
func TestN9EvidenceReadSwitchValues(t *testing.T) {
	t.Setenv("JWT_SECRET", "unit-test-secret-unit-test-unit-test")

	if os.Getenv("CLOSEN_INSIGHT_EVIDENCE_READ") != "" {
		t.Fatal("环境里已有 CLOSEN_INSIGHT_EVIDENCE_READ，这条用例会比错值")
	}
	cfg, err := Load()
	if err != nil {
		t.Fatalf("默认配置 Load: %v", err)
	}
	if cfg.InsightEvidenceRead != "on" {
		t.Errorf("默认 = %q，期望 on（新增端点没有既有消费者，默认开着才对）",
			cfg.InsightEvidenceRead)
	}

	t.Setenv("CLOSEN_INSIGHT_EVIDENCE_READ", "off")
	cfg, err = Load()
	if err != nil {
		t.Fatalf("off 配置 Load: %v", err)
	}
	if cfg.InsightEvidenceRead != "off" {
		t.Errorf("off 没生效：%q", cfg.InsightEvidenceRead)
	}

	// 空串不算非法值：envString 把「存在但为空」当成没配，落回默认 on，
	// 与 JWT_SECRET 等既有键同一口径。
	for _, bad := range []string{"false", "OFF", "0", "off "} {
		t.Setenv("CLOSEN_INSIGHT_EVIDENCE_READ", bad)
		if _, err := Load(); err == nil {
			t.Errorf("取值 %q 被接受了，开关只有 on|off", bad)
		}
	}
	// 空串走 envString 的 fallback（env 存在但为空＝没配），必须落回 on 而不是 off。
	t.Setenv("CLOSEN_INSIGHT_EVIDENCE_READ", "")
	cfg, err = Load()
	if err != nil {
		t.Fatalf("空值 Load: %v", err)
	}
	if cfg.InsightEvidenceRead != "on" {
		t.Errorf("空值 = %q，期望回落到 on", cfg.InsightEvidenceRead)
	}
}
