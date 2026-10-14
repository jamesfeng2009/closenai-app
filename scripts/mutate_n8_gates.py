"""N8 批 1（analytics + discovery）切流闸门的变异自证。

要证的不是「Go 抄得像」，而是**每一条口径都真的有人守着**：把实现按最可能的抄错方式
破坏一次，对应闸门必须红。打不红的口径＝那条口径其实没闸，写进代码也只是口头声明。

手法与 mutate_n2…n7_gates.py、mutate_n9_gates.py 相同：
备份 → 写坏 → **只跑那一条**测试 → 备份写回 → filecmp 核验。
**不用 git checkout**（仓里有并行未提交工作），**严禁并发跑**（并发会把变异态当原件写回）。

两侧都跑：Go 侧逐字节回放（`go test -run TestN8…`）与 Python 侧跨语言/清单闸
（`pytest backend/tests/…`）。这条分工是有意的——批 1 的漂移有两类，
「Go 实现抄错」由 Go 的回放抓，「Go 常量与 Python 字面量分家」由 Python 侧的
test_n8_batch1_contract.py 抓；后者必须能被打红，否则改 Go 的人本地 CI 一片绿。

库口令只从环境取（CLOSEN_PG_PASSWORD，否则现取容器 env 的白名单点名键），
脚本与输出都不落明文。

几处刻意不做的事（写了免得被当成漏了）：
1. `pyBytesRepr` 的引号选择（单引号体改双引号）与非 ASCII→`\\xNN` 两支不做变异：
   夹具里非 JSON 请求体（batch-content-type-*）全是 ASCII 且都含双引号，
   改这两支打不出红——不是闸门，是没有观测点。要钉它得先往生成脚本补一条
   「非 ASCII 的 text/plain 体」用例，那是批 2 的补夹具，不在本批口径内。
2. `anonymizeProperties` 的过滤动作本身不做变异：白名单在 Go 侧失效只会改变**外发事件
   的内容**，而 relayed 计数不看内容、测试里也不联网，逐字节回放看不到。
   它由 #15（白名单表加内容字段⇒ Python 侧红）从**另一侧**守：那张表是隐私红线，
   两侧的表必须逐字相同，这才是可证的那半。
3. `pyISO` 的非 UTC 入参不做变异：调用点只有 `time.Now().UTC()`，那一支不可达。
4. `CompareWeeks` 里 `zh := strings.HasPrefix(language, "zh")` 不做变异：
   改成 `language == "zh"` 在两侧所有观测点上等价（进来的语言码都已归一化）。
5. `posthog.Timeout`、`context.WithoutCancel` 不做变异：测试不联网、也不杀请求上下文，
   打不出红。它们靠 cutover 的转交测与真机灰度观察，不属这批可变异的面。
6. 2026-10-14 勘误（第一轮跑出来的）：`cutover.New` 里原本有一句显式
   `pr.Out.Header.Set("X-Trace-Id", trace)`，把它删掉**打不出红**——ReverseProxy 的
   `pr.Out` 本来就是 `pr.In` 的克隆，头一定会带过去。按 N9 的口径（打不出红的守卫不是
   闸门是冗余防御），那句已删；#32 改成在转交处**删头**，红的是可观测的那一面。

用法：
    .venv/bin/python scripts/mutate_n8_gates.py            # 全部（串行）
    .venv/bin/python scripts/mutate_n8_gates.py --check    # 只验锚点命中数，不跑测试
    .venv/bin/python scripts/mutate_n8_gates.py 3 12 27     # 只跑点名几条
"""
from __future__ import annotations

import filecmp
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/fengyu/Downloads/myproject/workspace/closenai-app")
GO = ROOT / "go-core"
PY = ROOT / "backend"
DEPLOY = ROOT / "deploy" / "k8s"

AB = GO / "internal/httpapi/analytics_batch.go"
B1 = GO / "internal/httpapi/batch1.go"
PJ = GO / "internal/httpapi/pyjson.go"
DD = GO / "internal/httpapi/discovery_daily.go"
RP = GO / "internal/httpapi/n8_replay_test.go"
DM = GO / "internal/domain/discovery.go"
AK = GO / "internal/domain/analytics.go"
CU = GO / "internal/cutover/cutover.go"
CF = GO / "internal/config/config.go"
ING = DEPLOY / "50-ingress.yaml"
CFG = DEPLOY / "10-config.yaml"
RA = PY / "routers/analytics.py"
RD = PY / "routers/discovery.py"
RM = PY / "routers/mood.py"

API_TESTS = GO / "internal/httpapi"
DOMAIN_TESTS = GO / "internal/domain"
CUT_TESTS = GO / "internal/cutover"
CFG_TESTS = GO / "internal/config"

B1_PYTEST = "backend/tests/test_n8_batch1_contract.py"
K8S_PYTEST = "backend/tests/test_k8s_manifests.py"


def pg_dsn() -> str:
    pw = os.environ.get("CLOSEN_PG_PASSWORD")
    if not pw:
        env = subprocess.run(
            ["docker", "inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}",
             "closenai-pg-dev"],
            capture_output=True, text=True, check=True).stdout
        pw = next(line[len("POSTGRES_PASSWORD="):] for line in env.splitlines()
                  if line.startswith("POSTGRES_PASSWORD="))
    return f"postgresql://postgres:{pw}@127.0.0.1:55432/closenai"


GOROOT = "/Users/fengyu/sdk/go1.27.1"
ENV = {
    **os.environ,
    "GOROOT": GOROOT,
    "PATH": f"{GOROOT}/bin:" + os.environ["PATH"],
    "TEST_DATABASE_URL": pg_dsn(),
    "CLOSEN_REQUIRE_PG": "1",
}
BACKUPS: dict[Path, Path] = {}


def backup(*paths: Path):
    tmp = Path(tempfile.mkdtemp(prefix="n8-gate-backup-"))

    def run() -> None:
        for path in paths:
            bak = tmp / path.name
            shutil.copy2(path, bak)
            BACKUPS[path] = bak

    return run


def restore_files() -> None:
    for path, bak in BACKUPS.items():
        shutil.copy2(bak, path)
        assert filecmp.cmp(bak, path, shallow=False), f"恢复失败：{path}"
    BACKUPS.clear()


def patch(path: Path, old: str, new: str):
    def run() -> None:
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, f"变异锚点命中 {text.count(old)} 次，应为 1：{old[:70]!r}"
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    return run


def disabled(anchor: str) -> str:
    return re.sub(r" \{$", " && false {", anchor, count=1)


def run_go(target: Path, test_name: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["go", "test", "./" + str(target.relative_to(GO)).strip("/"), "-count=1",
         "-run", test_name, "-v"],
        cwd=GO, capture_output=True, text=True, env=ENV, timeout=900)
    return proc.returncode, proc.stdout + proc.stderr


def run_pytest(node: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "pytest", node, "-q", "--no-header"],
        cwd=ROOT, capture_output=True, text=True, env=ENV, timeout=900)
    return proc.returncode, proc.stdout + proc.stderr


def m(num: int, claim: str, kind: str, target, test: str, *steps):
    return (num, claim, kind, target, test, steps)


# ── 变异锚点（逐字取自当前源码，命中数由 patch 断言）─────────────────────────
CT_RETURN = '\treturn main == "application" && (sub == "json" || strings.HasSuffix(sub, "+json"))\n'
CT_LOOSE = '\treturn (sub == "json" || strings.HasSuffix(sub, "+json") || main != "") && sub != ""\n'
EMPTY_BODY = "\tif len(raw) == 0 {\n"
NULL_BODY = ("\tcase pyNull:\n"
             "\t\t// FastAPI 把空 body 与 JSON null 都当成「没给 body」⇒ missing，而不是类型错误。\n"
             "\t\treturn nil, []pyFieldError{missingBody()}\n")
NULL_AS_TYPE = ("\tcase pyNull:\n"
                "\t\treturn nil, []pyFieldError{{\n"
                "\t\t\tkind: \"model_attributes_type\", loc: []any{\"body\"},\n"
                "\t\t\tmsg: \"Input should be a valid dictionary or object to extract fields from\",\n"
                "\t\t\tinput: body,\n"
                "\t\t}}\n")
TOO_LONG = "\tif len(eventsValue.items) > maxAnalyticsEvents {\n"
LIMIT = "const maxAnalyticsEvents = 100\n"
KEY_ORDER = ('\tRelayed int `json:"relayed"`\n\tDropped int `json:"dropped"`\n')
KEY_ORDER_SWAP = ('\tRelayed int `json:"dropped"`\n\tDropped int `json:"relayed"`\n')
CONSENT = "\tif !profile.Found || !profile.AnalyticsConsent {\n"
ORDER_BLOCK = ("\tevents, entries := validateAnalyticsBody(raw, r.Header.Get(\"Content-Type\"))\n"
               "\tif len(entries) > 0 {\n"
               "\t\twritePyInvalid(w, r, entries)\n"
               "\t\treturn\n"
               "\t}\n"
               "\n"
               "\tprofile, err := s.batch1.Users.UserProfile(r.Context(), userID)\n"
               "\tif err != nil {\n"
               "\t\twriteDBError(w, r)\n"
               "\t\treturn\n"
               "\t}\n")
ORDER_FLIP = ("\tprofile, err := s.batch1.Users.UserProfile(r.Context(), userID)\n"
              "\tif err != nil {\n"
              "\t\twriteDBError(w, r)\n"
              "\t\treturn\n"
              "\t}\n"
              "\tevents, entries := validateAnalyticsBody(raw, r.Header.Get(\"Content-Type\"))\n"
              "\tif len(entries) > 0 {\n"
              "\t\twritePyInvalid(w, r, entries)\n"
              "\t\treturn\n"
              "\t}\n")
JSON_INPUT = "\t\tinput: pyObjectOf(),\n"
FLOAT_FMT = "\tsci := strconv.FormatFloat(f, 'e', -1, 64)\n"
SPLIT_LANG = '\tcode := strings.ToLower(strings.SplitN(strings.TrimSpace(raw), "-", 2)[0])\n'
DB_LANG = ("\t\tif isSupportedLanguage(dbLang) {\n\t\t\treturn dbLang\n\t\t}\n")
DB_LANG_NORM = ("\t\tif code, ok := normalizeLanguageCode(dbLang); ok {\n"
                "\t\t\treturn code\n\t\t}\n")
LANG_TABLE = '\t"vi", "km",\n}\n'
ISO_Z = '\treturn out + "Z"\n'
ISO_PAD = '\t\tout += fmt.Sprintf(".%06d", micros)\n'
ISO_PAD_TRIM = '\t\tout += strings.TrimRight(fmt.Sprintf(".%06d", micros), "0")\n'
KEYS_TABLE = '\t"source":             true,\n'
INTERRUPT = "\tif len(thisWeek) == 0 {\n"
FREQ_MED = "\tFreqDiffMedium   = 2   // |Δ|=2：中等变化\n"
SHIFT_HIGH = "\tMoodShiftHigh    = 0.8 // ≥0.8：新发现模式（显著）\n"
PICK_ZH = "func pickCopy(zh bool, zhText, enText string) string {\n\tif zh {\n"
DEFAULT_VALUE = "\treturn 3\n"
WEEK = "\tdiscoveryWeek      = 7 * 24 * time.Hour\n"
LASTWEEK_FILTER = "\t\tif row.CreatedAt.Before(cutoff) {\n"
NOT_FOUND = "\tif !profile.Found {\n"
NIL_GATE = "\tif g == nil {\n\t\treturn true\n\t}\n"
NIL_GATE_NO = "\tif g == nil {\n\t\treturn false\n\t}\n"
ZERO_PCT = "\tif pct <= 0 {\n\t\treturn false\n\t}\n"
UNKNOWN_DOMAIN = "\tpct, ok := g.percents[domain]\n\tif !ok || pct >= 100 {\n"
RANGE_CHECK = "\t\tif pct < 0 || pct > 100 {\n"
UPSTREAM_CHECK = "\tif err != nil || parsed.Host == \"\" {\n"
HOST_LINE = "\t\t\tpr.Out.Host = pr.In.Host\n"
SET_URL = "\t\t\tpr.SetURL(g.upstream)\n"
PCT_DEFAULT = '\t\tCutoverAnalytics:       envInt("CLOSEN_CUTOVER_ANALYTICS", 0),\n'
CFG_RANGE = "\t\tif pct < 0 || pct > 100 {\n"
POSTHOG_HOST = '\t\tPosthogHost:            envString("POSTHOG_HOST", "https://eu.i.posthog.com"),\n'
MASK_RE = 'var n8GeneratedAtRe = regexp.MustCompile(`"generated_at":"[^"]*"`)\n'
CM_PCT = '  CLOSEN_CUTOVER_ANALYTICS: "0"\n'
ING_CATCHALL = ("          - path: /api/v1\n"
                "            pathType: Prefix\n"
                "            backend:\n"
                "              service:\n"
                "                name: py-api\n")
DEP_ANALYTICS = '@router.post("/analytics/batch", deprecated=True)\n'
DEP_MOOD = '@router.post("/mood/checkin")\n'
DISC_PATH = '@router.get("/discovery/daily", deprecated=True)\n'

MUTATIONS = [
    # ── analytics 的 400 体（FastAPI 的 body 读取面）──────────────────────────
    m(1, "Content-Type 的主类型闸：把「只有 application/json / *+json 才走 JSON 解析」"
         "松成「有子类型就行」，text/plain 就会被当 JSON 解析，400 体的 input 从 "
         "bytes 的 repr 变成另一形态（routing.py:428-446 实测口径）",
      "go", API_TESTS, "TestN8ValidationContractBytes",
      backup(AB), patch(AB, CT_RETURN, CT_LOOSE)),

    m(2, "空 body ⇒ missing：`if body_bytes:` 不 strip，去掉这一格空串就落到 JSON 解析，"
         "报 json_invalid 而不是 missing",
      "go", API_TESTS, "TestN8ValidationContractBytes",
      backup(AB), patch(AB, EMPTY_BODY, disabled(EMPTY_BODY))),

    m(3, "JSON null 的归类：FastAPI 把 `null` 与空 body 一样当「没给 body」，"
         "按普通类型错误回 ⇒ batch-json-null-body 那条 400 体逐字变样",
      "go", API_TESTS, "TestN8ValidationContractBytes",
      backup(AB), patch(AB, NULL_BODY, NULL_AS_TYPE)),

    m(4, "越界短路：实测 pydantic 在条数超限时**不再**逐条校验，只回一条 too_long；"
         "拆掉短路就变成「too_long + 每条 item 的错误」混合体",
      "go", API_TESTS, "TestN8ValidationContractBytes",
      backup(AB), patch(AB, TOO_LONG, disabled(TOO_LONG))),

    m(5, "上限数字 100：这一个数同时决定 too_long 的文案与 openapi 的 maxItems，"
         "抄错就是 400 体逐字不同——让 Go 侧改数，Python 侧的跨语言闸必须红",
      "pytest", f"{B1_PYTEST}::test_max_events_limit_matches_go", "",
      backup(AB), patch(AB, LIMIT, "const maxAnalyticsEvents = 99\n")),

    m(6, "成功信封的键序：relayed/dropped 的先后就是 Python 返回 dict 的声明序，"
         "换 tag 等于换响应字节",
      "go", API_TESTS, "TestN8SeededContractBytes",
      backup(AB), patch(AB, KEY_ORDER, KEY_ORDER_SWAP)),

    m(7, "analytics_consent 的丢弃分支：未同意用户照样外发，是隐私红线；"
         "同时 relayed/dropped 两个计数会互换",
      "go", API_TESTS, "TestN8SeededContractBytes",
      backup(AB), patch(AB, CONSENT, "\tif false && !profile.Found {\n")),

    m(8, "依赖顺序「校验先于读库」：FastAPI 先解 body 校验错误再进 service；"
         "顺序倒了，validation 组（Python 侧一次 store 都没碰）会去读库——"
         "假件 n8ForbiddenStore 就是为此存在",
      "go", API_TESTS, "TestN8ValidationContractBytes",
      backup(AB), patch(AB, ORDER_BLOCK, ORDER_FLIP)),

    m(9, "json_invalid 的 input 必须是空对象 `{}`：那是 pydantic-core 实测回显的形态，"
         "写成错误文案就等于换了契约字段",
      "go", API_TESTS, "TestN8ValidationContractBytes",
      backup(AB), patch(AB, JSON_INPUT, '\t\tinput: pyStringValue("x"),\n')),

    m(10, "float 的 repr 用最短可回读数字（`-1` 精度）：改成定宽精度，"
          "1.5 会回显成 1.500000000000，回显体逐字变",
      "go", API_TESTS, "TestN8ValidationContractBytes",
      backup(PJ), patch(PJ, FLOAT_FMT, "\tsci := strconv.FormatFloat(f, 'e', 12, 64)\n")),

    # ── discovery 的两级语言优先与窗口 ────────────────────────────────────────
    m(11, "X-Language 归一化（取 '-' 前一段）：不做归一化时 'zh-CN' 落回库偏好/英文，"
          "中文用户的发现文案变英文",
      "go", API_TESTS, "TestN8SeededContractBytes",
      backup(B1), patch(B1, SPLIT_LANG,
                        '\tcode := strings.ToLower(strings.TrimSpace(raw))\n')),

    m(12, "库偏好侧**不**做归一化（Python 的两级口径就是不对称的）：把 users.language="
          "'zh-CN' 也归一化成 zh，discovery-zh-region-db-falls-back-en 这条实测形态就翻了",
      "go", API_TESTS, "TestN8SeededContractBytes",
      backup(B1), patch(B1, DB_LANG, DB_LANG_NORM)),

    m(13, "语言表两侧必须是同一张表：Go 多加一个码，Python 侧的**双向差集**闸要红"
          "（改 Go 的人本地 CI 必须看得见）",
      "pytest", f"{B1_PYTEST}::test_supported_languages_match_go_both_directions", "",
      backup(B1), patch(B1, LANG_TABLE, '\t"vi", "km", "fil",\n}\n')),

    m(14, "白名单表加一个内容字段：reflection_text 出网＝给用户数据开第二条出口，"
          "Python 侧逐字对表必须红",
      "pytest", f"{B1_PYTEST}::test_allowed_property_keys_match_go_literal", "",
      backup(AK), patch(AK, KEYS_TABLE,
                        '\t"source":             true,\n\t"reflection_text":    true,\n')),

    m(15, "generated_at 的 UTC 写法：pydantic-core 序列化 UTC aware datetime 写 'Z'"
          "（实测），不是 RFC3339 的 '+00:00'；掩码遮掉了值，格式仍是契约",
      "go", API_TESTS, "TestN8PyISOFormMatchesPydanticSerializer",
      backup(B1), patch(B1, ISO_Z, '\treturn out + "+00:00"\n')),

    m(16, "generated_at 的微秒不去尾零：Python 侧固定 6 位，"
          "Go 的 RFC3339Nano 习惯会 TrimRight——一 trim 就成了另一种字符串",
      "go", API_TESTS, "TestN8PyISOFormMatchesPydanticSerializer",
      backup(B1), patch(B1, ISO_PAD, ISO_PAD_TRIM)),

    # ── CompareWeeks 的阈值与优先级 ──────────────────────────────────────────
    m(17, "优先级第一格「本周无记录」：去掉后中断用户落到频率/情绪分支（均值除零得 NaN，"
          "所有比较皆假）⇒ 最该发的那条不发了",
      "go", API_TESTS, "TestN8SeededContractBytes",
      backup(DM), patch(DM, INTERRUPT, disabled(INTERRUPT))),

    m(18, "中等频率变化的阈值 2：抄成 3 时 |Δ|=2 的用户不再收到「比上周更多/更少」",
      "go", API_TESTS, "TestN8SeededContractBytes",
      backup(DM), patch(DM, FREQ_MED, "\tFreqDiffMedium   = 3   // |Δ|=2：中等变化\n")),

    m(19, "显著情绪偏移的阈值 0.8：抄成 0.3 时轻微漂移被升级成「好了不少」的高优先级文案",
      "go", API_TESTS, "TestN8SeededContractBytes",
      backup(DM), patch(DM, SHIFT_HIGH, "\tMoodShiftHigh    = 0.3 // ≥0.8：新发现模式（显著）\n")),

    m(20, "文案二选一的方向：zh 为假时才取中文，翻过来所有中文用户的 content 变英文",
      "go", API_TESTS, "TestN8SeededContractBytes",
      backup(DM), patch(DM, PICK_ZH,
                        "func pickCopy(zh bool, zhText, enText string) string {\n\tif !zh {\n")),

    m(21, "未登记 emoji 的默认分值 3（Python 是 MOOD_MAP.get(emoji, (None, 3))[1]）："
          "当成 5 或 0 都会把均值算成假的方向",
      "go", API_TESTS, "TestN8SeededContractBytes",
      backup(DM), patch(DM, DEFAULT_VALUE, "\treturn 5\n")),

    m(22, "上周窗口的过滤方向：cutoff 之后取反，本周的行会被当成上周、上周的当成本周",
      "go", API_TESTS, "TestN8SeededContractBytes",
      backup(DD), patch(DD, LASTWEEK_FILTER, "\t\tif !row.CreatedAt.Before(cutoff) {\n")),

    m(23, "require_user 的 404 分支：用户不存在照样回 200，等于把「查无此人」"
          "变成一条空的个人洞察（discovery 走的是 40401，与 analytics 不同路）",
      "go", API_TESTS, "TestN8SeededContractBytes",
      backup(DD), patch(DD, NOT_FOUND, "\tif false && !profile.Found {\n")),

    m(24, "7 天窗口的数字：窗口边界落在哪一侧就是响应内容本身，"
          "Go 侧改一个数字，Python 侧的跨语言闸要红",
      "pytest", f"{B1_PYTEST}::test_discovery_week_windows_match_go", "",
      backup(DD), patch(DD, WEEK, "\tdiscoveryWeek      = 8 * 24 * time.Hour\n")),

    m(25, "posthog_host 的默认值：Go 若落回 posthog-python 的 app.posthog.com，"
          "灰度期同一批事件分叉进两个项目",
      "pytest", f"{B1_PYTEST}::test_posthog_host_default_matches_across_languages", "",
      backup(CF), patch(CF, POSTHOG_HOST,
                        '\t\tPosthogHost:            envString("POSTHOG_HOST", '
                        '"https://app.posthog.com"),\n')),

    # ── 灰度闸（0% 必须是真 0%，且转交逐字）──────────────────────────────────
    m(26, "nil 接收者的兜底：没接开关时按 100% 由 Go 回答；改成「无人回答」"
          "等于本地开发/未切流部署整条路由 500",
      "go", CUT_TESTS, "TestNilGateAnswersEveryDomain",
      backup(CU), patch(CU, NIL_GATE, NIL_GATE_NO)),

    m(27, "0% 不回答：这一格失效后「灰度 0%」是假的——Go 会真的跑业务逻辑",
      "go", CUT_TESTS, "TestSelectedIsMonotoneInPercent",
      backup(CU), patch(CU, ZERO_PCT, "\tif pct <= 0 {\n\t\treturn true\n\t}\n")),

    m(28, "未登记的域必须全量由 Go 回答：把它当成 0% 会让「新增一个域但忘了配百分比」"
          "表现为 Go 侧一条都不答",
      "go", CUT_TESTS, "TestUnknownDomainIsFullyGoServed",
      backup(CU), patch(CU, UNKNOWN_DOMAIN, "\tpct, ok := g.percents[domain]\n\tif ok && pct >= 100 {\n")),

    m(29, "百分比越界拒绝构造：150 当 100、-5 当 0 都是「运维以为切了其实另一回事」",
      "go", CUT_TESTS, "TestNewRejectsPercentOutOfRange",
      backup(CU), patch(CU, RANGE_CHECK, "\t\tif false {\n")),

    m(30, "低于 100% 必须有上游：没有 CLOSEN_PY_API_URL 还允许起灰度，"
          "未选中的请求就无处可转",
      "go", CUT_TESTS, "TestNewRequiresUpstreamBelowFullCut",
      backup(CU), patch(CU, UPSTREAM_CHECK, "\tif err != nil {\n")),

    m(31, "转交时保留原始 Host：py-api 侧的 CORS/回调与绝对 URL 生成都看 Host，"
          "换了 Host 就不是「逐字转交」",
      "go", CUT_TESTS, "TestDelegateForwardsVerbatim",
      backup(CU), patch(CU, HOST_LINE, "\t\t\t_ = pr.Out.Host\n")),

    m(32, "转交时逐字带过去客户端的头：ReverseProxy 的 pr.Out 是 In 的克隆，"
          "在转交处删掉 X-Trace-Id 就断了跨侧排障的链（2026-10-14 取证：原先代码里"
          "显式 re-Set 那一格是冗余的，删了也打不出红，已按本条改成打在可观测面上）",
      "go", CUT_TESTS, "TestDelegateForwardsVerbatim",
      backup(CU), patch(CU, SET_URL,
                        SET_URL + '\t\t\tpr.Out.Header.Del("X-Trace-Id")\n')),

    m(33, "百分比配置默认 0：默认 100 意味着清单里忘了配键的部署直接把流量全给 Go",
      "go", CFG_TESTS, "TestN8CutoverDefaults",
      backup(CF), patch(CF, PCT_DEFAULT,
                        '\t\tCutoverAnalytics:       envInt("CLOSEN_CUTOVER_ANALYTICS", 100),\n')),

    m(34, "配置层的越界拒绝（与 cutover 的构造期拒绝各守一段）："
          "两处都要红，缺任一处就有一条路径能把 150 带进运行时",
      "go", CFG_TESTS, "TestN8CutoverPercentRefusesOutOfRange",
      backup(CF), patch(CF, CFG_RANGE, "\t\tif false {\n")),

    # ── 清单与登记面（放量/回滚的可证形态）───────────────────────────────────
    m(35, "放量登记点：本轮判据是「百分比仍为 0」，把 ConfigMap 改成 1 而不同步更新"
          "这条断言，就等于悄悄放量没人知道",
      "pytest", f"{K8S_PYTEST}::test_ingress_is_at_zero_percent_rollout", "",
      backup(CFG), patch(CFG, CM_PCT, '  CLOSEN_CUTOVER_ANALYTICS: "1"\n')),

    m(36, "Ingress 的兜底入口仍归 py-api：把 /api/v1 整条指到 go-api＝一次性全切，"
          "域级一刀的边界就不存在了",
      "pytest", f"{K8S_PYTEST}::test_ingress_is_at_zero_percent_rollout", "",
      backup(ING), patch(ING, ING_CATCHALL,
                         ING_CATCHALL.replace("name: py-api", "name: go-api"))),

    m(37, "第④步只标这一批：analytics 的 deprecated 一撤，"
          "「Go 已是权威面」这件事就没有登记处了",
      "pytest", f"{B1_PYTEST}::test_only_batch1_paths_are_deprecated", "",
      backup(RA), patch(RA, DEP_ANALYTICS, '@router.post("/analytics/batch")\n')),

    m(38, "提前标废弃未切流的域：mood 还在 py-api 手里就写 deprecated，"
          "客户端会收到「这条路要没了」的错误信号",
      "pytest", f"{B1_PYTEST}::test_only_batch1_paths_are_deprecated", "",
      backup(RM), patch(RM, DEP_MOOD, '@router.post("/mood/checkin", deprecated=True)\n')),

    m(39, "回滚面：路由本体一改路径，灰度调回 0 时 Python 也答不上这条 URL——"
          "「只标 deprecated、不删路由」是回滚的前提",
      "pytest", f"{B1_PYTEST}::test_deprecated_paths_are_still_routable_for_rollback", "",
      backup(RD), patch(RD, DISC_PATH,
                        '@router.get("/discovery/day", deprecated=True)\n')),

    m(40, "掩码闸自身：把「掩掉整个值」改成「掩掉 8 个字符」，录制体里就会残留没被掩掉"
          "的时间戳，逐字比变成比时间戳",
      "go", API_TESTS, "TestN8MaskOnlyGeneratedAt",
      backup(RP), patch(RP, MASK_RE,
                        'var n8GeneratedAtRe = regexp.MustCompile(`"generated_at":"[^"]{8}"`)\n')),

    m(41, "夹具非空闸：occurred_at_corpus 一旦被解码成空切片（生成脚本换了键名），"
          "语料测会在空跑——loadN8Fixture 的拒绝为空必须有效",
      "go", API_TESTS, "TestN8FixtureCorpusCoversBothRejectShapes",
      backup(RP, GO / "internal/httpapi/n8_contract_test.go"),
      patch(GO / "internal/httpapi/n8_contract_test.go",
            '\tOccurredAtCorpus   []n8CorpusEntry `json:"occurred_at_corpus"`\n',
            '\tOccurredAtCorpus   []n8CorpusEntry `json:"occurred_at_corpuss"`\n')),
]


def main() -> int:
    if "--check" in sys.argv[1:]:
        bad = 0
        for num, _, _, _, _, steps in MUTATIONS:
            for step in steps:
                try:
                    step()
                except AssertionError as exc:
                    bad += 1
                    print(f"[ANCHOR-BAD] #{num}: {exc}")
                    break
            restore_files()
        print(f"锚点自检：{len(MUTATIONS)} 条，坏锚点 {bad} 个")
        return 1 if bad else 0

    wanted = {int(a) for a in sys.argv[1:] if not a.startswith("--")}
    failures = []
    attempted = 0
    try:
        for num, claim, kind, target, test, steps in MUTATIONS:
            if wanted and num not in wanted:
                continue
            attempted += 1
            rc, output = 0, ""
            try:
                for step in steps:
                    step()
                rc, output = run_go(target, test) if kind == "go" else run_pytest(target)
            finally:
                restore_files()
            red = rc != 0
            print(f"[{'RED' if red else 'GREEN-FAIL'}] #{num:<3} {claim[:72]}")
            if not red:
                failures.append((num, target, test, kind, claim, output))
                print(output[-1200:])
        print(f"\n{attempted - len(failures)}/{attempted} 条变异打红")
        for num, target, test, kind, claim, _ in failures:
            label = test or target
            print(f"  #{num} {label} 仍然通过：{claim[:60]}")
    finally:
        restore_files()

    # 复跑受影响面：证明每份备份都写回去了。变异脚本最怕的不是打红，是打红后把源码留在坏状态。
    for pkg in ("./internal/httpapi/", "./internal/domain/", "./internal/cutover/",
                "./internal/config/"):
        proc = subprocess.run(["go", "test", pkg, "-count=1"], cwd=GO,
                              capture_output=True, text=True, env=ENV, timeout=900)
        tail = (proc.stdout + proc.stderr).strip().splitlines()
        print(f"[{'GREEN' if proc.returncode == 0 else 'BROKEN'}] 复跑 {pkg}："
              f"{tail[-1] if tail else ''}")
        if proc.returncode != 0:
            print((proc.stdout + proc.stderr)[-2000:])
            return 1
    proc = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "pytest", B1_PYTEST, K8S_PYTEST,
         "-q", "--no-header"],
        cwd=ROOT, capture_output=True, text=True, env=ENV, timeout=900)
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    print(f"[{'GREEN' if proc.returncode == 0 else 'BROKEN'}] 复跑 Python 侧闸门："
          f"{tail[-1] if tail else ''}")
    if proc.returncode != 0:
        print((proc.stdout + proc.stderr)[-2000:])
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
