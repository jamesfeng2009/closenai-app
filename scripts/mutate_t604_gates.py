#!/usr/bin/env python3
"""T6-04（staging／prod 隔离与切流键闸）的变异自证（2026-10-20）。

靶文件十四个（Go 5：config.go／config/env_isolation.go／cutover/approval.go／cmd/go-api/main.go／
httpapi/server.go；Python 4：core/env_isolation.py／main.py／core/auth.py／config.py；
部署面 5：10-config.yaml／docker-compose.staging.yml／install-crontab.sh／crontab／README.md），
红闸 44 条、绿闸 6 条。

工单 §验收 两句，每句都有**它自己的**靶与红闸（[[feedback-mutation-anchor-must-reach-target]]）：

1. 「把 staging 指到生产串 ⇒ 启动失败并有明确报错」
   · I1 把 Go 侧的黑名单比对掏空（`hit(fingerprints` → `hit(nil`）⇒ 红的是
     `TestAcceptanceStagingPointedAtProductionRefusesToBoot` 本身，不是它的邻居；
   · I2 只把那句报错里的 `host=%q dbname=%q` 抹平（**启动仍然失败**，但「明确报错」没了）⇒
     同一条闸的第二个入射角——本票要求的两半「失败」＋「说清为什么」各钉一次；
   · P1／P2 是同一格判据在 **Python 侧** 的两个位置：判据本体（`match_fingerprint` 循环）与
     装配接线（`main.py` 里那句 `if isolation_error:`）。判据写得再对，没人调它等于没有；
   · I4／P7 钉的是这句话的两个方向性前提：「黑名单为空＝没配＝没保护」（staging 必须拒）与
     「生产档连自己的库必须放行」（否则运维第一天就把闸注释掉）。
2. 「把切流键改 50 且无审批 ⇒ 启动失败」
   · A1 把触发条件从 `pct > 0` 改成 `pct > 100`（50 不再算放量）⇒ 红的是
     `TestAcceptanceCutoverFiftyWithoutApprovalRefusesToBoot`；
   · A2 把「只有 ‘1’ 算签字」改成「非空就算」⇒ 出厂默认 `"0"` 变成已签，红的是那条逐域报错的闸；
   · I12 把 `CheckApproval` 喂的那张表换成另一份构造 ⇒ 红的是装配证人
     `TestApprovalSeesTheSameGateMap`（D-T604-10：审批看一份数、灰度看另一份数＝谁都没看）；
   · K1／K2 把 ConfigMap 的审批位抄成 `"1"`、或整行删掉 ⇒ 清单侧的闸红——这一格管的是
     「别在基线里提前把字签好」，而 Go 侧那条只在有域放量时才咬人，两者方向不同。

其余各条钉的是本票其余口径与部署面，按「同一格判据在两侧/两个位置各钉一次」排：
· 词表两侧同集合与逐字判档：I7（Go 加值 → 由 **Python 侧**的对账抓）、I8（Go 折叠大小写）、
  P3／P8（Python 侧同两格，P8 用的是本票改判前的旧形状 `.lower()`）；
· staging 与 production 同受保护档：判据侧 I9（Go 表）与 P3（Python 表），接线侧 I14（server.go 读回
  `IsProduction()`）与 P4（auth.py 读回旧那句）——表对了线接错，换来的仍是设备身份透传；
· 建连前后与同一张表：I13（隔离判在建连之后）、I12（审批看另一份数）、I11／I11b（两个新键的键名漂）、
  I15（内部面 `OutcomeWrites` 那一格从装配字面量里掉出去＝T4-02 缺陷①／T4-03 缺陷③／T6-03 缺陷①
  逐字归本票补齐的那笔欠账）；
· crontab 只装生产：C1（放行分支带上 staging）、C2（标记判据改指不存在的标记 → **正向证人**红）、
  C3／C4（真调度行漂端口 / 判据被放宽）、C5（未知参数被静默吞）、C6／C7（crontab 与 README 的文档面）、
  K3（标记行与脚本判据同时消失）；
· staging overlay 的库键名、必填指纹与端口面：D1-D4 与 C_OVERLAY（D2），D5 钉的是
  **`docker compose config` 的展开结果**（compose 对 overlay 的 sequence 是追加：
  少了 `!override`，prod 的 127.0.0.1:8000 会跟着进 staging，而只读 overlay 文本的 D2 看不见）。

**本机取不了证的那两格，按「未跑」登记、不算绿**：
· `ENVIRONMENT=staging` 真在 VPS 上被 k3s／compose 起一次（本机没有那套宿主 env，
  也没有第二套库）——本票能自证的是 compose overlay 的**展开**（`docker compose config` 实跑两次：
  正常展开＋缺 `PROD_DB_FINGERPRINTS` 时 exit 1）与判据单测；
· go-api／py-api 进程在真机上带生产指纹启动失败的现场日志（同上，只有单测面）。
这两格归 2026-10-19 记下的「取证能力缺口」，不在本票内补。

纪律：串行执行（[[feedback-mutation-gates-no-concurrency]]）、copy2 备份＋`filecmp(shallow=False)`
逐字节写回、不用 git checkout（[[feedback-mutation-restore-no-git-checkout]]）；每条红闸先跑基线，
基线本来就不绿或前置缺失一律记「未跑」（[[feedback-gate-needs-positive-witness]]）；
Go 侧 `--- SKIP` 与 `[build failed]`、pytest 侧 `ERROR <节点>` 与 `no tests ran` 都**不算打红**——
那些红的是环境，不是判据。

用法：
    set -a && . /tmp/t206_env.sh && set +a
    GOROOT=/Users/fengyu/sdk/go1.27.1 ./.venv/bin/python scripts/mutate_t604_gates.py --check
    GOROOT=/Users/fengyu/sdk/go1.27.1 ./.venv/bin/python scripts/mutate_t604_gates.py
    ./.venv/bin/python scripts/mutate_t604_gates.py --only I1,I2,A1,C1
"""
from __future__ import annotations

import argparse
import ast
import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GO_CORE = REPO / "go-core"
VENV_PY = REPO / ".venv" / "bin" / "python"
GOROOT = os.environ.get("GOROOT", "/Users/fengyu/sdk/go1.27.1")
GO_BIN = os.environ.get("GO_BIN", f"{GOROOT}/bin/go")
GO_ENV = {
    **os.environ,
    "GOROOT": GOROOT,
    "PATH": f"{GOROOT}/bin:{os.environ.get('PATH', '')}",
    "GO_BIN": GO_BIN,
    "GOTOOLCHAIN": "local",
}

# ── 靶文件 ───────────────────────────────────────────────────────────────────
GO_CONFIG = GO_CORE / "internal" / "config" / "config.go"
GO_ISO = GO_CORE / "internal" / "config" / "env_isolation.go"
GO_APPROVAL = GO_CORE / "internal" / "cutover" / "approval.go"
GO_MAIN = GO_CORE / "cmd" / "go-api" / "main.go"
GO_SERVER = GO_CORE / "internal" / "httpapi" / "server.go"
PY_ISO = REPO / "backend" / "core" / "env_isolation.py"
PY_MAIN = REPO / "backend" / "main.py"
PY_AUTH = REPO / "backend" / "core" / "auth.py"
PY_CONFIG = REPO / "backend" / "config.py"
CM = REPO / "deploy" / "k8s" / "10-config.yaml"
OVERLAY = REPO / "deploy" / "compose" / "docker-compose.staging.yml"
SH = REPO / "deploy" / "schedule" / "install-crontab.sh"
TAB = REPO / "deploy" / "schedule" / "crontab"
README = REPO / "deploy" / "README.md"

# ── 闸（Go 三条包 ＋ Python 六个文件）───────────────────────────────────────
PKG_CFG = "./internal/config"
PKG_CUT = "./internal/cutover"
PKG_BOOT = "./cmd/go-api"

T_ACC = "^TestAcceptanceStagingPointedAtProductionRefusesToBoot$"
T_NOFP = "^TestStagingWithoutFingerprintsRefusesToBoot$"
T_DEV = "^TestDevelopmentStillBootsWithoutFingerprints$"
T_PRODSELF = "^TestProductionIsNotBlockedByItsOwnFingerprint$"
T_VOCAB = "^TestEnvironmentVocabularyGate$"
T_PROT = "^TestProtectedTierTruthTable$"
T_KEYS = "^TestNewKeysAreActuallyRead$"
T_CORPUS = "^TestSharedCaseTableBothLanguagesShape$"
T_ACC50 = "^TestAcceptanceCutoverFiftyWithoutApprovalRefusesToBoot$"
T_NAMED = "^TestEveryLiveDomainIsNamed$"
T_STAGING_APP = "^TestStagingDoesNotNeedApproval$"
T_ALIAS = "^TestProdAliasSameTier$"
T_EVERYDOM = "^TestEveryConfiguredDomainNeedsApproval$"
T_ORDER = "^TestBootOrderIsolationBeforeConnect$"
T_SAMEMAP = "^TestApprovalSeesTheSameGateMap$"
T_AUTHTIER = "^TestAuthTierWiredToIsProtected$"
T_READKEYS = "^TestConfigReadsTheTwoNewKeys$"
T_WIRE = "^TestMainWiresTheInternalSurfaceToTheSameDomainService$"

XLANG = "backend/tests/test_env_isolation_cross_language.py"
CRON = "backend/tests/test_crontab_install_target.py"
P_ACC = f"{XLANG}::test_staging_pointed_at_production_url_fails_fast"
P_NOFP = f"{XLANG}::test_staging_without_fingerprints_fails_fast"
P_UNKNOWN = f"{XLANG}::test_unknown_environment_fails_fast"
P_PROD = f"{XLANG}::test_production_tier_still_boots"
P_VOCAB = f"{XLANG}::test_environment_vocabulary_is_the_same_table_both_sides"
P_DEFAULTS = f"{XLANG}::test_python_defaults_are_the_shared_table_not_a_copy"
P_BLANK = f"{XLANG}::test_fingerprint_parser_drops_blank_items"
P_WIRING = f"{XLANG}::test_startup_validation_wiring_uses_the_shared_module"
P_SHAREKEY = f"{XLANG}::test_prod_db_fingerprints_is_a_shared_cross_language_key"
P_OVERLAY = f"{XLANG}::test_staging_overlay_names_the_database_key_differently"
K_APPROVAL = "backend/tests/test_k8s_manifests.py::test_cutover_approval_bit_ships_unsigned"
C_REFUSE = f"{CRON}::test_installer_refuses_every_non_production_tier"
C_ACCEPT = f"{CRON}::test_installer_accepts_production_and_its_alias"
C_MARKER = f"{CRON}::test_crontab_declares_its_target"
C_PORTS = f"{CRON}::test_crontab_only_reaches_the_production_port_face"
C_HOLLOW = f"{CRON}::test_installer_refuses_a_hollowed_out_table"
C_ARG = f"{CRON}::test_unknown_argument_is_not_silently_ignored"
C_HEADER = f"{CRON}::test_crontab_header_points_at_the_script"
C_README = f"{CRON}::test_readme_installs_through_the_gate"
C_OVERLAY = f"{CRON}::test_staging_overlay_binds_a_different_host_port"
P_EXPAND = f"{CRON}::test_staging_overlay_expansion_drops_production_ports"

# ── 锚点（逐字取自靶文件，命中数必须恰好 1）────────────────────────────────
GO_HIT_CALL = "\tif fp, matched := hit(fingerprints, target); matched {\n"
GO_ERR_FMT = ('"ENVIRONMENT=%s 不许连 host=%q dbname=%q：它命中了生产库指纹 %q（PROD_DB_FINGERPRINTS）。"+\n'
              '\t\t\t"两套进程写同一个 canonical 库＝两份都看着对的账（Baseline §22 风险 3）。"+\n')
GO_USER_BRANCH = ("\treturn strings.Contains(t.Host, fp) || strings.Contains(t.DBName, fp) ||\n"
                  "\t\tstrings.Contains(t.User, fp)\n")
GO_STAGING_BLANK = ('\t\tif environment == "staging" {\n'
                    '\t\t\treturn &MissingFingerprintsError{Environment: environment}\n\t\t}\n')
GO_PROD_EARLY = "\tif IsProductionEnv(environment) {\n"
GO_VOCAB = 'var EnvironmentValues = []string{"development", "staging", "production", "prod"}\n'
GO_ISVALID = "func IsValidEnvironment(env string) bool { return inList(EnvironmentValues, env) }\n"
GO_PROTECTED = 'var ProtectedEnvironments = []string{"staging", "production", "prod"}\n'
GO_APPROVAL_SWITCH = '\tcase "", "0", "1":\n'
GO_APPROVAL_READ = '\t\tCutoverApproved:        envString("CLOSEN_CUTOVER_APPROVED", ""),\n'
GO_FINGERPRINTS_READ = '\t\tProdDBFingerprints:     envList("PROD_DB_FINGERPRINTS"),\n'
GO_CALL_APPROVAL = ("if err := cutover.CheckApproval(cfg.Environment, cutoverPercents, "
                    "cfg.CutoverApproved); err != nil {")
GO_ISOLATION_BLOCK = ('\tif err := cfg.RequireDBIsolation(); err != nil {\n'
                      '\t\tlog.Fatalf("go-api: %v", err)\n\t}\n')
GO_NEWPG = "\tpg := store.NewPG(pool)\n"
GO_MIDDLEWARE = "\t\tmiddleware: NewMiddleware(signer, cfg.IsProtected()),\n"
GO_V3_SLOTS = "\t\tGoals: goalBrowser, GoalWrites: candidateDomain, OutcomeWrites: candidateDomain,\n"
CUT_PCT_TRIGGER = "\t\tif pct > 0 {\n"
CUT_SIGNED = '\tif approved == "1" {\n\t\treturn nil\n\t}\n'
CUT_TIER_GATE = "\tif !config.IsProductionEnv(environment) {\n"

PY_MATCH_LOOP = "        if match_fingerprint(fp, target):\n"
PY_FINGERPRINTS_IF = "    if is_production(environment):\n"
PY_PROTECTED = 'PROTECTED_ENVIRONMENTS = ("staging", "production", "prod")\n'
PY_VOCAB = 'ENVIRONMENT_VALUES = ("development", "staging", "production", "prod")\n'
PY_ISPROD = '    return value in ("production", "prod")\n'
PY_BLANK_FILTER = '    return [item.strip() for item in raw.split(",") if item.strip()]\n'
PY_MAIN_WIRING = "    if isolation_error:\n        raise RuntimeError("
PY_AUTH_WIRING = "    return env_isolation.is_protected(settings.environment)\n"
PY_CONFIG_FIELD = '    prod_db_fingerprints: str = ""\n'

SH_CASE_PRODUCTION = "  production|prod) ;;\n"
SH_MARKER_NEEDLE = "grep -q '^# INSTALL-TARGET: production-only$' \"$CRONTAB_FILE\" \\\n"
SH_PORT_GUARD = "if printf '%s\\n' \"$PORTS\" | grep -qv '^127\\.0\\.0\\.1:8000$'; then\n"
SH_UNKNOWN_ARG = ('    *) echo "install-crontab: 未知参数 $1（只认 --check / --env-file / '
                  '--crontab-file）" >&2; exit 2 ;;\n')
TAB_HEALTH_LINE = ("*/5 * * * * . /etc/closenai/cron.env && curl -fsS -H "
                  '"Authorization: Bearer $CRON_SECRET" http://127.0.0.1:8000/api/v1/cron/health >/dev/null\n')
TAB_HEADER_INSTALL = "# 安装（VPS，UTC 服务器时区）：deploy/schedule/install-crontab.sh\n"
README_CONCLUSION = "3. **crontab 只装生产。** 见上一节的脚本与 `schedule/crontab` 顶部的\n"
CM_APPROVAL_LINE = '  CLOSEN_CUTOVER_APPROVED: "0"\n'
OVERLAY_PYAPI_HEAD = "  py-api:\n    environment:\n      ENVIRONMENT: staging\n"
OVERLAY_PORTS = '      - "127.0.0.1:18000:8000"   # 宿主 crontab 打的是 8000，那一行永远落不到这里\n'
OVERLAY_FP_REQUIRED = ('      PROD_DB_FINGERPRINTS: '
                       '${PROD_DB_FINGERPRINTS:?staging 不给生产库指纹＝这道闸一条都不判，拒启}\n')
OVERLAY_DB_URL = ("      DATABASE_URL: ${STAGING_DATABASE_URL:?staging 必须给指给自己那套库的串，"
                  "禁止复用 DATABASE_URL}\n")
OVERLAY_PYAPI_OVERRIDE = ('    ports: !override\n'
                          '      - "127.0.0.1:18000:8000"')
MARKER_LINE = "# INSTALL-TARGET: production-only\n"
SHELL_LINE = "SHELL=/bin/sh\n"
CHECK_APPROVAL_SIG = "func CheckApproval(environment string, percents map[string]int, approved string) error {\n"
ISOLATION_SIG = "func CheckDBIsolation(environment, databaseURL string, fingerprints []string) error {\n"
PY_ISOLATION_DEF = "def check_db_isolation(environment: str, database_url: str,\n"

# 绿闸用的 15 行注释（各靶文件按自己的注释语法）
GO_COMMENTS = "".join(
    f"// 绿闸注脚 {i:02d}：这条判据读的是集合与调用点，不钉行号也不钉排版。\n" for i in range(1, 16)
)
PY_COMMENTS = "".join(
    f"# 绿闸注脚 {i:02d}：这一格判的是语义，不是这个文件排了几行。\n" for i in range(1, 16)
)
SH_COMMENTS = "".join(
    f"# 绿闸注脚 {i:02d}：判据读的是 ENVIRONMENT 那一行与调度行的端口，不看注释密度。\n"
    for i in range(1, 16)
)

MUTATIONS: list[tuple] = [
    # ── §验收 第一句：staging 指到生产串 ⇒ 启动失败（并有明确报错）──
    (
        "I1", "把 Go 侧的黑名单比对掏空（`hit(fingerprints, target)` → `hit(nil, target)`，"
              "§验收 逐字第一句的判据本体）⇒ 隔离闸一条都不判，"
              "`TestAcceptanceStagingPointedAtProductionRefusesToBoot` 红——staging 带着生产串起来了",
        [(GO_ISO, GO_HIT_CALL, GO_HIT_CALL.replace("hit(fingerprints, target)", "hit(nil, target)"))],
        "go", PKG_CFG, T_ACC,
    ),
    (
        "I2", "只抹平那句报错里的 `host=%q dbname=%q`（仍然拒启，但不再说清是哪台库）⇒ §验收 第一句的"
              "后半「**并有明确报错**」红：报错必须能指到 host 与 dbname，否则运维只能猜"
              "——这是同一条验收句的第二个入射角",
        [(GO_ISO, GO_ERR_FMT,
          '"ENVIRONMENT=%s 不许连这个库：%q %q %q（细节略）"+\n'
          '\t\t\t"两套进程写同一个 canonical 库＝两份都看着对的账（Baseline §22 风险 3）。"+\n')],
        "go", PKG_CFG, T_ACC,
    ),
    (
        "I3", "裸令牌分支不再比用户名（Supabase pooler 的 project ref 只长在 `postgres.<ref>` 里）⇒ "
              "用例表 `staging_project_ref裸令牌命中用户名` 那格在 Go 侧漏判：静默放行＝双写",
        [(GO_ISO, GO_USER_BRANCH,
          "\treturn strings.Contains(t.Host, fp) || strings.Contains(t.DBName, fp)\n")],
        "go", PKG_CFG, T_CORPUS,
    ),
    (
        "I4", "staging 没配黑名单从「拒启」改成「放行」（D-T604-6）⇒ 这道闸最阴的坏法：没人配＝没保护，"
              "而它在日志里长得和「配了且通过」一模一样",
        [(GO_ISO, GO_STAGING_BLANK, GO_STAGING_BLANK.replace('environment == "staging"', "false"))],
        "go", PKG_CFG, T_NOFP,
    ),
    (
        "I5", "反过来：把「空黑名单拒启」判给**所有**档 ⇒ development 的本机编排第一天就起不来，"
              "红的是可用性证人 `TestDevelopmentStillBootsWithoutFingerprints`——"
              "过紧的闸与过松的闸同样会被人整条注释掉（[[feedback-gate-needs-positive-witness]]）",
        [(GO_ISO, GO_STAGING_BLANK, GO_STAGING_BLANK.replace('environment == "staging"', "true"))],
        "go", PKG_CFG, T_DEV,
    ),
    (
        "I6", "取消「生产档不参与黑名单比较」那半边 ⇒ production 连自己的库也被判死，"
              "红的是 `TestProductionIsNotBlockedByItsOwnFingerprint`。这一格红了不代表更安全",
        [(GO_ISO, GO_PROD_EARLY, "\tif false {\n")],
        "go", PKG_CFG, T_PRODSELF,
    ),
    (
        "I7", "Go 侧词表多塞一个 'Production'（把拼错的值当合法档）⇒ 红的是 **Python 侧**的跨语言对账："
              "两侧各自实现、共用一张规格，任何一侧改表而没改另一侧就抓到——这是「不许同源」的正向证人",
        [(GO_CONFIG, GO_VOCAB, GO_VOCAB.replace('"prod"}', '"prod", "Production"}'))],
        "pytest", P_VOCAB, P_VOCAB,
    ),
    (
        "I8", "Go 侧词表判据加 `.ToLower()`（大小写折叠）⇒ `ENVIRONMENT=Production` 在 Go 变成合法，"
              "红的是 `TestEnvironmentVocabularyGate`：那一格原来就是裸着的（D-T604-2）",
        [(GO_CONFIG, GO_ISVALID,
          "func IsValidEnvironment(env string) bool { return inList(EnvironmentValues, strings.ToLower(env)) }\n")],
        "go", PKG_CFG, T_VOCAB,
    ),
    (
        "I9", "受保护档里删掉 staging（本票改判的那一格被悄悄改回去）⇒ staging 缺 JWT_SECRET 又能起，"
              "红的是 `TestProtectedTierTruthTable`（它单独钉这一格，不靠用例表）",
        [(GO_CONFIG, GO_PROTECTED, GO_PROTECTED.replace('"staging", ', ""))],
        "go", PKG_CFG, T_PROT,
    ),
    (
        "I10", "审批位取值面放行 'yes'（「以为签了字其实没签」的形状）⇒ 红的是 `TestNewKeysAreActuallyRead`："
               "这一格与 CLOSEN_INSIGHT_EVIDENCE_READ 同口径，只认 \"\"|0|1",
        [(GO_CONFIG, GO_APPROVAL_SWITCH, '\tcase "", "0", "1", "yes":\n')],
        "go", PKG_CFG, T_KEYS,
    ),
    (
        "I11", "审批位的**键名**漂一格（`CLOSEN_CUTOVER_APPROVED` → `CLOSEN_CUTOVER_SIGN`）⇒ 读的是空气，"
               "现网 ConfigMap 里那一行永远没人读；红的是装配证人 `TestConfigReadsTheTwoNewKeys`",
        [(GO_CONFIG, GO_APPROVAL_READ, GO_APPROVAL_READ.replace("CLOSEN_CUTOVER_APPROVED", "CLOSEN_CUTOVER_SIGN"))],
        "go", PKG_BOOT, T_READKEYS,
    ),
    (
        "I11b", "指纹键名漂一格（`PROD_DB_FINGERPRINTS` → `CLOSEN_PROD_DB_FINGERPRINTS`）⇒ 同一装配证人红；"
                "另一侧的证人：带 CLOSEN_ 前缀在这个仓里的含义是「只有 Go 认得」，而 py-api 必须也读它（D-T604-4）",
        [(GO_CONFIG, GO_FINGERPRINTS_READ, GO_FINGERPRINTS_READ.replace("PROD_DB_FINGERPRINTS", "CLOSEN_PROD_DB_FINGERPRINTS"))],
        "go", PKG_BOOT, T_READKEYS,
    ),
    (
        "I12", "`CheckApproval` 改喂一张**另造的**表（D-T604-10：审批看一份数、灰度闸看另一份数＝谁都没看）⇒ "
               "红的是 `TestApprovalSeesTheSameGateMap`——真源分裂这条形状在本仓被抓过一次（D-G12）",
        [(GO_MAIN, GO_CALL_APPROVAL,
          'if err := cutover.CheckApproval(cfg.Environment, map[string]int{"analytics": cfg.CutoverAnalytics}, cfg.CutoverApproved); err != nil {')],
        "go", PKG_BOOT, T_SAMEMAP,
    ),
    (
        "I13", "把隔离判据挪到**建连之后**（§怎么做.1 那句「判在建连之前」）⇒ 那一次连接本身已经进了 PG 的"
               "日志与连接计数，报错再准也留下了痕迹；红的是 `TestBootOrderIsolationBeforeConnect`",
        [(GO_MAIN, GO_ISOLATION_BLOCK, ""),
         (GO_MAIN, GO_NEWPG, GO_ISOLATION_BLOCK + GO_NEWPG)],
        "go", PKG_BOOT, T_ORDER,
    ),
    (
        "I14", "Go 的鉴权档读回 `IsProduction()`（表对了、线接错）⇒ staging 拿到 development 的设备身份透传，"
               "X-User-Id 直接当身份——比本票要防的双写更糟；红的是 `TestAuthTierWiredToIsProtected`",
        [(GO_SERVER, GO_MIDDLEWARE, GO_MIDDLEWARE.replace("IsProtected()", "IsProduction()"))],
        "go", PKG_BOOT, T_AUTHTIER,
    ),
    (
        "I15", "内部面的 `OutcomeWrites` 那一格从装配字面量里掉出去（T4-02 缺陷①／T6-03 缺陷①逐字归本票的那三格）"
               "⇒ httpapi 与 domain 两侧照绿、进程里却没有这个写口，客户端拿到的是整面 404；"
               "红的是装配证人 `TestMainWiresTheInternalSurfaceToTheSameDomainService`。"
               "刻意只摘 `OutcomeWrites`：`candidateDomain` 别处还在用，摘 `Goals: goalBrowser` 会让 "
               "`goalBrowser` 变成未使用变量，红的是编译而不是判据（等价于「未跑」）",
        [(GO_MAIN, GO_V3_SLOTS,
          "\t\tGoals: goalBrowser, GoalWrites: candidateDomain,\n")],
        "go", PKG_BOOT, T_WIRE,
    ),
    (
        "A1", "审批闸的触发条件从 `pct > 0` 改成 `pct > 100`（§验收 逐字第二句：改到 50 且无审批要启动失败）⇒ "
              "50% 不再算放量，进程照起；红的是 `TestAcceptanceCutoverFiftyWithoutApprovalRefusesToBoot`",
        [(GO_APPROVAL, CUT_PCT_TRIGGER, CUT_PCT_TRIGGER.replace("pct > 0", "pct > 100"))],
        "go", PKG_CUT, T_ACC50,
    ),
    (
        "A2", "「只有 \"1\" 算签字」放宽成「非空就算」⇒ 出厂默认的 `\"0\"`（ConfigMap 里那一行）被当成已签，"
              "双签流程直接归零；红的是逐域点名那条 `TestEveryLiveDomainIsNamed`",
        [(GO_APPROVAL, CUT_SIGNED, '\tif approved != "" {\n\t\treturn nil\n\t}\n')],
        "go", PKG_CUT, T_NAMED,
    ),
    (
        "A3", "审批面扩大到 staging／development ⇒ 与 D-T604-7 反判（staging 的百分比只落在自己的库上，"
              "它打生产库那条路已由隔离闸判成拒启）；红的是 `TestStagingDoesNotNeedApproval`——"
              "两道闸叠成一道没人能读的闸，结果是没人过它",
        [(GO_APPROVAL, CUT_TIER_GATE, "\tif !config.IsValidEnvironment(environment) {\n")],
        "go", PKG_CUT, T_STAGING_APP,
    ),
    (
        "A4", "审批面只认逐字 `production`，丢掉既有别名 `prod` ⇒ 把 ENVIRONMENT 设成 prod 就能不签字放量，"
              "那是本票自己留的后门；红的是 `TestProdAliasSameTier`",
        [(GO_APPROVAL, CUT_TIER_GATE,
          '\tif !config.IsProductionEnv(environment) || environment != "production" {\n')],
        "go", PKG_CUT, T_ALIAS,
    ),
    (
        "A5", "触发条件反向放宽成 `pct >= 0` ⇒ 八个键全 0 的**现网默认**也要签字，进程起不来；"
              "红的是逐格证人 `TestEveryConfiguredDomainNeedsApproval`（它两向都判：>0 要签、=0 不许拦）。"
              "过紧的闸与过松的闸同样会被人整条注释掉（[[feedback-gate-needs-positive-witness]]）",
        [(GO_APPROVAL, CUT_PCT_TRIGGER, CUT_PCT_TRIGGER.replace("pct > 0", "pct >= 0"))],
        "go", PKG_CUT, T_EVERYDOM,
    ),
    # ── Python 侧：判据本体与两处接线 ────────────────────────
    (
        "P1", "Python 侧的指纹循环掏空（`if match_fingerprint(...)` → `if False:`）⇒ py-api 带着生产串起来了："
              "py-api 是 canonical 的**写方**，这一半不需要任何切流百分比也会双写，所以那道闸不能只长在 Go 那一侧",
        [(PY_ISO, PY_MATCH_LOOP, "        if False:  # 变异：判据掏空\n")],
        "pytest", P_ACC, P_ACC,
    ),
    (
        "P2", "判据还在、**接线断了**（`main.py` 的 `if isolation_error:` 永不成立）⇒ "
              "`_validate_startup_config` 拿到错误字符串却不抛；这是「函数写对了但没人调」那一类，"
              "与 P1 是同一条验收句的两个位置",
        [(PY_MAIN, PY_MAIN_WIRING, "    if False and isolation_error:\n        raise RuntimeError(")],
        "pytest", P_ACC, P_ACC,
    ),
    (
        "P3", "Python 的受保护档删掉 staging ⇒ 与 I9 同向、另一侧实现；红的是"
              "`test_python_defaults_are_the_shared_table_not_a_copy`（`is_protected('staging')` 那一行）",
        [(PY_ISO, PY_PROTECTED, PY_PROTECTED.replace('"staging", ', ""))],
        "pytest", P_DEFAULTS, P_DEFAULTS,
    ),
    (
        "P4", "Python 的鉴权档读回旧那句（`is_protected` → `is_production`）⇒ staging 的 py-api 走设备身份透传，"
              "红的是装配证人 `test_startup_validation_wiring_uses_the_shared_module`",
        [(PY_AUTH, PY_AUTH_WIRING, "    return env_isolation.is_production(settings.environment)\n")],
        "pytest", P_WIRING, P_WIRING,
    ),
    (
        "P5", "指纹拆法丢掉「丢空项」⇒ `\" , , \"` 变成三条空指纹，而空串是指纹的通配（任何 host 都命中）："
              "这道闸退化成永久拒启，第一天就有人整条注释掉；红的是 `test_fingerprint_parser_drops_blank_items`",
        [(PY_ISO, PY_BLANK_FILTER,
          '    return [item.strip() for item in raw.split(",")]\n')],
        "pytest", P_BLANK, P_BLANK,
    ),
    (
        "P6", "`backend/config.py` 的字段改名（pydantic 按字段名大写映射 env）⇒ py-api 读不到指纹，"
              "红的是跨语言键名证人 `test_prod_db_fingerprints_is_a_shared_cross_language_key`",
        [(PY_CONFIG, PY_CONFIG_FIELD, '    prod_db_fingerprint: str = ""\n')],
        "pytest", P_SHAREKEY, P_SHAREKEY,
    ),
    (
        "P7", "Python 侧取消「生产档不参与比较」⇒ production 连自己的库被自己的黑名单判死，"
              "红的是正向证人 `test_production_tier_still_boots`（与 I6 同一条口径，另一侧各钉一次）",
        [(PY_ISO, PY_FINGERPRINTS_IF, "    if False:  # 变异：生产档不再豁免\n")],
        "pytest", P_PROD, P_PROD,
    ),
    (
        "P8", "Python 词表折叠大小写（`is_production` 里加 `.lower()`）⇒ 这就是本票改判前的旧形状："
              "`Production` 在 Python 算生产、在 Go 不算，同一份 ConfigMap 喂出两套鉴权档；"
              "红的是 `test_python_defaults_are_the_shared_table_not_a_copy`",
        [(PY_ISO, PY_ISPROD, '    return value.lower() in ("production", "prod")\n')],
        "pytest", P_DEFAULTS, P_DEFAULTS,
    ),
    # ── 部署面：crontab 只装生产 ─────────────────────────────
    (
        "C1", "安装脚本的放行分支带上 staging（§怎么做.3 那句「crontab 只装生产」的执行者）⇒ "
              "staging 那台也能装表，两套调度源同一时刻打同一组 /cron/*：没有报错，只有重复投递",
        [(SH, SH_CASE_PRODUCTION, "  production|prod|staging) ;;\n")],
        "pytest", C_REFUSE, C_REFUSE,
    ),
    (
        "C2", "脚本里的标记判据改指一个不存在的标记 ⇒ 每一次安装都被拒；红的是那条**正向证人**"
              "`test_installer_accepts_production_and_its_alias`——「永远返回非零」的闸与「永远放行」的闸"
              "一样没用，所以要有一格专门钉它今天能装（[[feedback-gate-needs-positive-witness]]）",
        [(SH, SH_MARKER_NEEDLE, SH_MARKER_NEEDLE.replace("production-only", "staging-only"))],
        "pytest", C_ACCEPT, C_ACCEPT,
    ),
    (
        "C3", "往 crontab 里加一条**真调度行**打 staging 的 18000 ⇒ 端口面那条判据红（它判的是会执行的那几行）",
        [(TAB, TAB_HEALTH_LINE, TAB_HEALTH_LINE +
          TAB_HEALTH_LINE.replace("127.0.0.1:8000", "127.0.0.1:18000").replace("/cron/health", "/cron/daily-insight"))],
        "pytest", C_PORTS, C_PORTS,
    ),
    (
        "C4", "端口判据被「顺手兼容 staging」放宽（8000 之外再放行 18000）⇒ 漂到 staging 那张表照样装得上，"
              "红的是 `test_crontab_only_reaches_the_production_port_face` 里那条漂移靶。"
              "（第一轮跑这条时我用的是 BRE 的 `[0-9]+`——那是个**等价变异**：BRE 里 `+` 不是量词，"
              "放宽没生效、闸照旧拒，所以那条靶当时并没有被真的打红。记在这里免得下轮重猜。）",
        [(SH, SH_PORT_GUARD, "if printf '%s\\n' \"$PORTS\" | grep -qvE '^127\\.0\\.0\\.1:(8000|18000)$'; then\n")],
        "pytest", C_PORTS, C_PORTS,
    ),
    (
        "C5", "未知参数从「exit 2」改成静默跳过 ⇒ 一次拼错的 `--env-flle`（判据来源覆盖）会被当成「没传」，"
             "脚本改用 /etc/closenai/env 而运维以为它在判自己给的那份；红的是 `test_unknown_argument_is_not_silently_ignored`",
        [(SH, SH_UNKNOWN_ARG, "    *) ;;\n")],
        "pytest", C_ARG, C_ARG,
    ),
    (
        "C6", "crontab 顶部的安装说明改回裸 `crontab deploy/schedule/crontab`（那行命令不问环境）⇒ "
              "红的是 `test_crontab_header_points_at_the_script`——文档面也是闸的一部分，"
              "因为运维照抄的就是这一行",
        [(TAB, TAB_HEADER_INSTALL, "# 安装（VPS，UTC 服务器时区）：crontab deploy/schedule/crontab\n")],
        "pytest", C_HEADER, C_HEADER,
    ),
    (
        "C7", "README 的三条结论里删掉「crontab 只装生产」那半句 ⇒ 红的是 `test_readme_installs_through_the_gate`："
              "工单 §怎么做.3 的交付物有一半是「运维读得到这句话」，脚本与文档对账才是完整的一条闸",
        [(README, README_CONCLUSION, "3. **调度源见上一节。** 见上一节的脚本与 `schedule/crontab` 顶部的\n")],
        "pytest", C_README, C_README,
    ),
    # ── 部署面：staging overlay 与清单 ───────────────────────
    (
        "D1", "staging overlay 的 py-api 不再被覆写成 staging（回落成 prod 编排的 production）⇒ "
              "「staging」这一档在部署面根本不存在，库面隔离的那半句（受保护档）也没了；"
              "红的是跨语言闸里的 overlay 段 `test_staging_overlay_names_the_database_key_differently`",
        [(OVERLAY, OVERLAY_PYAPI_HEAD, "  py-api:\n    environment:\n      ENVIRONMENT: production\n")],
        "pytest", P_OVERLAY, P_OVERLAY,
    ),
    (
        "D2", "overlay 里 py-api 的宿主端口漂回 8000（与生产那套抢同一个口）⇒ 红的是"
              "`test_staging_overlay_binds_a_different_host_port`：端口面隔离是「crontab 物理上够不到 staging」"
              "的另一半，少了它 C3/C4 那两条判据只是纸上的",
        [(OVERLAY, OVERLAY_PORTS, '      - "127.0.0.1:8000:8000"\n')],
        "pytest", C_OVERLAY, C_OVERLAY,
    ),
    (
        "D3", "把必填的指纹覆写改成可选（`${PROD_DB_FINGERPRINTS:?}` → `:-`）⇒ 空黑名单那台能起来，"
              "正是 D-T604-6 要消灭的形状；红的是同一条 overlay 闸",
        [(OVERLAY, OVERLAY_FP_REQUIRED, "      PROD_DB_FINGERPRINTS: ${PROD_DB_FINGERPRINTS:-}\n")],
        "pytest", P_OVERLAY, P_OVERLAY,
    ),
    (
        "D4", "overlay 直接引用生产那把库键名（`${STAGING_DATABASE_URL:?…}` → `${DATABASE_URL:-}`）⇒ "
              "两套编排指向同一个库只需要「别改这一行」，而工单 §为什么 画的正是这一格；红的是同一条 overlay 闸",
        [(OVERLAY, OVERLAY_DB_URL, "      DATABASE_URL: ${DATABASE_URL:-}\n")],
        "pytest", P_OVERLAY, P_OVERLAY,
    ),
    (
        "D5", "摘掉 overlay 里 py-api 那格 `ports: !override`（**本轮实跑抓到的真缺陷**：compose 对 "
              "overlay 的 sequence 是追加而不是替换，基线 prod 的 127.0.0.1:8000 会跟着进 staging 的展开，"
              "于是「宿主 crontab 那一行物理上打不到 staging」这句直接落空）⇒ 红的是展开证人 "
              "`test_staging_overlay_expansion_drops_production_ports`；D2 那条只读 overlay 自己的文本，"
              "看不见合并语义，这一格正是它补不上来的那条腿",
        [(OVERLAY, OVERLAY_PYAPI_OVERRIDE,
          '    ports:\n      - "127.0.0.1:18000:8000"')],
        "pytest", P_EXPAND, P_EXPAND,
    ),
    (
        "K1", "ConfigMap 出厂带着**已签字**的审批位（`CLOSEN_CUTOVER_APPROVED: \"1\"`）⇒ 每次部署都自动放量，"
              "「改一行键 ＋ 改一格审批位」那次双签被合并成零次；红的是清单侧那条专门的闸"
              "（Go 侧那条只在有域放量时才咬人，两者方向不同）",
        [(CM, CM_APPROVAL_LINE, '  CLOSEN_CUTOVER_APPROVED: "1"\n')],
        "pytest", K_APPROVAL, K_APPROVAL,
    ),
    (
        "K2", "把审批位那一行整行删掉 ⇒ 清单上「未设」与「这一面不存在」长得一模一样，"
              "而 Go 侧读到空串照样判未签——漂移要看得见才算漂移；红的是同一条清单闸",
        [(CM, CM_APPROVAL_LINE, "")],
        "pytest", K_APPROVAL, K_APPROVAL,
    ),
    (
        "K3", "把标记判据的白名单去掉、顺手在 crontab 里删掉 `INSTALL-TARGET` 那行 ⇒ "
              "「谁都能装」这条口径只活在脚本里，改表的人看不见；红的是 `test_crontab_declares_its_target`",
        [(TAB, MARKER_LINE, ""), (SH, SH_MARKER_NEEDLE, "true \\\n")],
        "pytest", C_MARKER, C_MARKER,
    ),
]

# ── 绿闸：这些坏法**不该**红，红了就说明判据退化成了文本 diff ────────────────
GREEN: list[tuple] = [
    (
        "GR1", "Go 判据文件里插 15 行注释 ⇒ §验收 第一句那条靶照旧红得动（绿闸：判据不钉自己的行号）",
        [(GO_ISO, ISOLATION_SIG, GO_COMMENTS + ISOLATION_SIG)],
        "go", PKG_CFG, T_ACC,
    ),
    (
        "GR2", "config.go 词表上方插 15 行注释 ⇒ `TestEnvironmentVocabularyGate` 照旧绿："
               "词表对账抓的是切片字面量，不是排版",
        [(GO_CONFIG, GO_VOCAB, GO_COMMENTS + GO_VOCAB)],
        "go", PKG_CFG, T_VOCAB,
    ),
    (
        "GR3", "approval.go 判据上方插 15 行注释 ⇒ §验收 第二句那条靶照旧绿（同 GR1 的另一条腿）",
        [(GO_APPROVAL, "func CheckApproval(environment string, percents map[string]int, approved string) error {\n",
          GO_COMMENTS + "func CheckApproval(environment string, percents map[string]int, approved string) error {\n")],
        "go", PKG_CUT, T_ACC50,
    ),
    (
        "GR4", "Python 判据文件里插 15 行注释 ⇒ `test_staging_pointed_at_production_url_fails_fast` 照旧绿",
        [(PY_ISO, "def check_db_isolation(environment: str, database_url: str,\n",
          PY_COMMENTS + "def check_db_isolation(environment: str, database_url: str,\n")],
        "pytest", P_ACC, P_ACC,
    ),
    (
        "GR5", "crontab 里加一行**注释**提到 127.0.0.1:18000 ⇒ 端口判据不误伤：判的是会执行的那几行，"
               "不是文件里出现过哪些数字（与 C3 成对——那才是真调度行）",
        [(TAB, SHELL_LINE, "# 注意：staging 那套 bind 的是 127.0.0.1:18000，这张表不许指到那里去。\n" + SHELL_LINE)],
        "pytest", C_PORTS, C_PORTS,
    ),
    (
        "GR6", "安装脚本里插 15 行注释 ⇒ 判据通过那格照旧绿：脚本读的是 ENVIRONMENT 那一行与端口面，"
               "不看自己排了几行",
        [(SH, 'CHECK_ONLY=0\n', SH_COMMENTS + 'CHECK_ONLY=0\n')],
        "pytest", C_ACCEPT, C_ACCEPT,
    ),
]


def _apply(edits: list) -> None:
    """锚点替换成变异体；命中数必须恰好 1——命中两次说明靶写错了。"""
    for path, old, new in edits:
        assert path.exists(), f"{path}：靶文件不存在"
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:160]!r}"
        path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _mutant_parses(path: Path, old: str, new: str) -> str | None:
    """变异体自己必须是**语法合法**的文件。

    这条不是洁癖：`sh` 语法坏掉时脚本会以「非零退出」的形态让 pytest 那条节点红，
    于是「红的是解释器」被记成「红的是判据」——本仓的口径是那种红不算打红
    （[[feedback-mutation-anchor-must-reach-target]]），所以要在打靶前先排掉。
    """
    text = path.read_text(encoding="utf-8").replace(old, new, 1)
    if path.suffix == ".py":
        try:
            ast.parse(text)
        except SyntaxError as exc:
            return f"变异体不是合法 Python：{exc}"
    elif path.suffix in (".sh", ".go"):
        with tempfile.NamedTemporaryFile("w", suffix=path.suffix, delete=False,
                                         encoding="utf-8") as fh:
            fh.write(text)
            probe = Path(fh.name)
        try:
            checker = ["sh", "-n"] if path.suffix == ".sh" else ["gofmt", "-e", "-l"]
            proc = subprocess.run(checker + [str(probe)], capture_output=True, text=True,
                                  env=GO_ENV)
            # gofmt -e 把语法错误打到 stderr 并且仍然打印内容，所以两条都看。
            if proc.returncode != 0 or (path.suffix == ".go" and proc.stderr.strip()):
                return f"变异体 {path.suffix} 语法坏掉：{(proc.stdout + proc.stderr)[:200]}"
        except FileNotFoundError as exc:
            return f"缺少语法检查工具（不算绿，也不许把它的缺位当通过）：{exc}"
        finally:
            probe.unlink(missing_ok=True)
    return None


def _verify_anchor(edits: list) -> str | None:
    for path, old, new in edits:
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if n != 1:
            return f"锚点命中 {n} 次（要 1）：{old[:80]!r}"
        problem = _mutant_parses(path, old, new)
        if problem:
            return problem
    return None


def run_pytest(node: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(VENV_PY), "-m", "pytest", node, "-q", "--no-header", "-p", "no:randomly"],
        cwd=REPO, capture_output=True, text=True, timeout=2400,
    )
    return proc.returncode, proc.stdout + proc.stderr


def run(kind: str, target: str, marker: str) -> tuple[int, str]:
    """marker 决定 Go 那条 `-run` 的形态：只跑点名的判据，不跑整包。

    只跑点名那条是有意的——整包跑会让「同包另一条本来就该红的测」冒充成本靶的红。
    """
    if kind == "go":
        if not Path(GO_BIN).is_file():
            return -2, f"{GO_BIN} 不存在：Go 侧闸门没跑，不算绿"
        proc = subprocess.run([GO_BIN, "test", target, "-count=1", "-run", marker,
                               "-timeout", "300s"],
                              cwd=GO_CORE, env=GO_ENV, capture_output=True, text=True, timeout=900)
        return proc.returncode, proc.stdout + proc.stderr
    if kind == "pytest":
        if not VENV_PY.is_file():
            return -2, f"解释器不存在：{VENV_PY}"
        return run_pytest(target)
    return -2, f"未知的闸类型 {kind}"


def report_name(marker: str) -> str:
    name = marker[1:] if marker.startswith("^") else marker
    return name[:-1] if name.endswith("$") else name


def is_red(code: int, out: str, kind: str, target: str, marker: str) -> bool:
    """「红」＝**那条判据自己**失败；编译坏掉、没匹配到用例、skip、收集期报错都不算。"""
    if code == 0:
        return False
    if kind == "go":
        if "[build failed]" in out or "no tests to run" in out or "no test files" in out:
            return False
        if "--- SKIP:" in out and "--- FAIL:" not in out:
            return False
        return f"--- FAIL: {report_name(marker)}" in out
    if "no tests ran" in out:
        return False
    if f"ERROR {target}" in out:
        return False
    return f"FAILED {target}" in out


BASELINE: dict[tuple[str, str, str], tuple[int, str]] = {}


def baseline(kind: str, target: str, marker: str) -> tuple[int, str]:
    key = (kind, target, marker)
    if key not in BASELINE:
        BASELINE[key] = run(kind, target, marker)
    return BASELINE[key]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数，不改任何文件")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()
    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    cases = [*MUTATIONS, *GREEN]
    if args.check:
        bad = 0
        for case in cases:
            problem = _verify_anchor(case[2])
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        print(f"锚点校验：{'全部命中一次且变异体合法' if bad == 0 else f'{bad} 条有问题'}（共 {len(cases)} 条）")
        return 1 if bad else 0

    if not VENV_PY.is_file():
        print(f"解释器不存在：{VENV_PY}（变异自证要求先备好 .venv）")
        return 2

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-t604-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            rel = path.relative_to(REPO).as_posix().replace("/", "+")
            bak = Path(tmp) / f"{idx:02d}-{rel}"
            assert bak not in originals.values(), f"备份名碰撞：{bak}"
            shutil.copy2(path, bak)
            originals[path] = bak

        def restore() -> None:
            for path, bak in originals.items():
                shutil.copy2(bak, path)
                assert filecmp.cmp(bak, path, shallow=False), f"{path} 恢复后字节不一致"

        red_ok = red_fail = green_ok = green_fail = 0
        unrun: list[str] = []
        failures: list[str] = []
        try:
            for cid, desc, edits, kind, target, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(kind, target, marker)
                if base_code == -2:
                    unrun.append(cid)
                    print(f"[{cid}] 未跑（{base_out}）：缺前置不是绿，也不把它的红记在闸头上")
                    continue
                if base_code != 0:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 未跑（基线本来就不绿，exit={base_code}）："
                          "不能把环境的红记成闸的红\n  " + "\n  ".join(base_out.splitlines()[-8:]))
                    continue
                try:
                    _apply(edits)
                    code, out = run(kind, target, marker)
                finally:
                    restore()
                if code != 0 and kind == "go" and "[build failed]" in out:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（变异体编译不过，红的是工具链不是判据）：\n  "
                          + "\n  ".join(out.splitlines()[-10:]))
                    continue
                if is_red(code, out, kind, target, marker):
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗  {desc}\n  " + "\n  ".join(out.splitlines()[-14:]))

            for cid, desc, edits, kind, target, marker in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(kind, target, marker)
                if base_code != 0:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（基线就不绿，exit={base_code}）：{desc}")
                    continue
                try:
                    _apply(edits)
                    code, out = run(kind, target, marker)
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 被误伤 ✗  {desc}\n  " + "\n  ".join(out.splitlines()[-14:]))
        finally:
            restore()
            residue = [p for p in paths if not filecmp.cmp(originals[p], p, shallow=False)]
            print(f"\n残留复核：{'0 个靶文件与原件逐字节一致' if not residue else ''}")
            if residue:
                print("  以下靶文件恢复后仍不一致：" + "、".join(str(p) for p in residue))

        total_red = red_ok + red_fail
        total_green = green_ok + green_fail
        print(f"\n汇总：红闸 {red_ok}/{total_red}｜绿闸 {green_ok}/{total_green}｜"
              f"未跑 {len(unrun)}{'（' + ','.join(unrun) + '）' if unrun else ''}")
        if failures:
            print("失败条目：" + ",".join(failures))
        if unrun:
            print("ⓘ 有靶未跑：缺 env／缺工具链一律不算绿")
        return 1 if (red_fail or green_fail or unrun) else 0


if __name__ == "__main__":
    sys.exit(main())
