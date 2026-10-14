package config

import (
	"os"
	"strings"
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

// TestN8CutoverDefaults 钉住「部署了但没开灰度」这一安全态：
// 每个已切流域的百分比默认 0，于是 go-api 起来后一条都不自己答，只当 py-api 的代理。
// 默认值变了就等于把「上线即全量」写进了镜像，这条测是唯一的拦法。
// 批 2 加了 curriculum：每接一个域就要在这里加一个键，漏一个键＝那个域逃过了默认值闸。
func TestN8CutoverDefaults(t *testing.T) {
	t.Setenv("JWT_SECRET", "unit-test-secret-unit-test-unit-test")
	for _, key := range []string{"CLOSEN_PY_API_URL", "CLOSEN_CUTOVER_ANALYTICS",
		"CLOSEN_CUTOVER_DISCOVERY", "CLOSEN_CUTOVER_CURRICULUM",
		"POSTHOG_API_KEY", "POSTHOG_HOST"} {
		if os.Getenv(key) != "" {
			t.Fatalf("环境里已有 %s，这条用例会比错值", key)
		}
	}
	cfg, err := Load()
	if err != nil {
		t.Fatalf("默认配置 Load: %v", err)
	}
	if cfg.CutoverAnalytics != 0 || cfg.CutoverDiscovery != 0 || cfg.CutoverCurriculum != 0 {
		t.Errorf("灰度默认 = (%d, %d, %d)，期望 (0, 0, 0)",
			cfg.CutoverAnalytics, cfg.CutoverDiscovery, cfg.CutoverCurriculum)
	}
	if cfg.CutoverPyAPIURL != "" {
		t.Errorf("CLOSEN_PY_API_URL 不该有默认值：%q", cfg.CutoverPyAPIURL)
	}
	// host 的默认值是 Python 侧的字面量，不是 posthog-python 自己的 app.posthog.com。
	if cfg.PosthogHost != "https://eu.i.posthog.com" {
		t.Errorf("POSTHOG_HOST 默认 = %q，期望与 backend/config.py 同收件箱", cfg.PosthogHost)
	}
	if cfg.PosthogAPIKey != "" {
		t.Errorf("POSTHOG_API_KEY 不该有默认值：%q", cfg.PosthogAPIKey)
	}
}

// TestN8CutoverPercentRefusesOutOfRange 是那句「越界拒绝启动而不是夹紧」：
// 把 150 当 100、把 -5 当 0 都是「运维以为切了/以为关了，其实另一回事」。
func TestN8CutoverPercentRefusesOutOfRange(t *testing.T) {
	t.Setenv("JWT_SECRET", "unit-test-secret-unit-test-unit-test")
	for _, key := range []string{"CLOSEN_CUTOVER_ANALYTICS", "CLOSEN_CUTOVER_DISCOVERY",
		"CLOSEN_CUTOVER_CURRICULUM"} {
		for _, bad := range []string{"-5", "101", "1000"} {
			t.Setenv("CLOSEN_INSIGHT_EVIDENCE_READ", "on")
			t.Setenv(key, bad)
			_, err := Load()
			if err == nil {
				t.Errorf("%s=%s 被接受了，百分比只允许 0..100", key, bad)
				continue
			}
			// 报错文案必须点名是哪个变量，运维才知道改哪一个。
			if !strings.Contains(err.Error(), key) {
				t.Errorf("%s=%s 的报错没点名变量：%v", key, bad, err)
			}
		}
		t.Setenv(key, "")
	}
	// 合法边界与非法文本：非数字按 envInt 的口径落回默认 0（与既有 JWT_* 同）。
	t.Setenv("CLOSEN_CUTOVER_ANALYTICS", "100")
	t.Setenv("CLOSEN_CUTOVER_DISCOVERY", "abc")
	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.CutoverAnalytics != 100 || cfg.CutoverDiscovery != 0 {
		t.Errorf("= (%d, %d)，期望 (100, 0)", cfg.CutoverAnalytics, cfg.CutoverDiscovery)
	}
}
