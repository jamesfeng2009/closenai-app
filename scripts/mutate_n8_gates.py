"""N8 切流闸门的变异自证（批 1：analytics + discovery；批 2：curriculum）。

要证的不是「Go 抄得像」，而是**每一条口径都真的有人守着**：把实现按最可能的抄错方式
破坏一次，对应闸门必须红。打不红的口径＝那条口径其实没闸，写进代码也只是口头声明。

手法与 mutate_n2…n7_gates.py、mutate_n9_gates.py 相同：
备份 → 写坏 → **只跑那一条**测试 → 备份写回 → filecmp 核验。
**不用 git checkout**（仓里有并行未提交工作），**严禁并发跑**（并发会把变异态当原件写回）。

两侧都跑：Go 侧逐字节回放（`go test -run TestN8…`）与 Python 侧跨语言/清单闸
（`pytest backend/tests/…`）。这条分工是有意的——批 1 的漂移有两类，
「Go 实现抄错」由 Go 的回放抓，「Go 常量与 Python 字面量分家」由 Python 侧的
test_n8_batch1_contract.py 抓；后者必须能被打红，否则改 Go 的人本地 CI 一片绿。

批 2 多出三类只有这边能打的口径，所以条目形态和批 1 不同：
- **库谓词**（窗口半开、正序、status='active'、UPDATE 不碰 memory_refs）：
  逐字节回放只看到「这批行」，看不到 SQL 为什么这样写；实库测才看得见谓词。
- **日历**（本地周一、civil 日折瞬间、day_no % 7、revealed 含今天）：
  899 条 week_window 语料 + domain 表测。
- **机制存在性**（Ingress/ConfigMap/openapi 三个登记面必须给出同一个域集合、
  路由挂在接线开关下、二进制嵌了 IANA 时区库）：只有 Python 侧的源面/清单闸看得见。

库口令只从环境取（CLOSEN_PG_PASSWORD，否则现取容器 env 的白名单点名键），
脚本与输出都不落明文。

几处刻意不做的事（写了免得被当成漏了）：
1. ~~`pyBytesRepr` 的引号选择与非 ASCII→`\\xNN` 两支不做变异~~（批 1 登记：夹具里
   非 JSON 请求体全是 ASCII 且都含双引号，改这两支打不出红——不是闸门，是没有观测点）。
   **批 2 已按登记的口径补夹具**：生成脚本新增「非 ASCII 的 text/plain 体」「含单引号
   不含双引号的体」「重复逗号的 Content-Type」三条，于是 #75/#76 第一次可证，#77 补上
   `isJSONContentType` 的 `+json` 判定面（此前只由 #1 从主类型那一侧打过）。
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
7. 批 2：`Server.now` 钉回真实时钟（把夹具锚点撤掉）**打不出红**，因此不做这条变异。
   根因是 curriculum 的响应里没有时刻字段（`week_start`/`days[].date` 是日历日，
   写回库的 `read_at` 不进响应），而锚点 2026-10-14 与跑测那天同处一个本地周
   （周一 2026-10-12..周日 10-18）⇒ 撤掉锚点这一轮看不出差别。
   冻时钟的口径因此由**机制存在**两面守：#72–#74 让「录制侧真的冻了钟」「锚点形状」
   「播种用绝对时刻」各能被打红一次；跨过本地周一之后回放会自然红，那是它的真实生效时刻。
   留这条勘误，免得后来人把「回放全绿」当成冻时钟已被验证。

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

# 批 2（curriculum 域）
CW = GO / "internal/httpapi/curriculum_week.go"
CR = GO / "internal/httpapi/curriculum_read.go"
B2 = GO / "internal/httpapi/batch2.go"
DC = GO / "internal/domain/curriculum.go"
LB = GO / "internal/store/legacy_batch2.go"
MG = GO / "cmd/go-api/main.go"
SV = GO / "internal/httpapi/server.go"
FX = GO / "testdata/n8_contract.json"
GS = ROOT / "scripts/gen_n8_contract_goldens.py"
RC = PY / "routers/curriculum.py"

API_TESTS = GO / "internal/httpapi"
DOMAIN_TESTS = GO / "internal/domain"
CUT_TESTS = GO / "internal/cutover"
CFG_TESTS = GO / "internal/config"
STORE_TESTS = GO / "internal/store"

B1_PYTEST = "backend/tests/test_n8_batch1_contract.py"
B2_PYTEST = "backend/tests/test_n8_batch2_contract.py"
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
# 批 2 补夹具后才有的两个观测点（非 ASCII 的 text/plain 体、含单引号不含双引号的体）
CT_JSONISH = '\treturn main == "application" && strings.Contains(sub, "json")\n'
BYTES_QUOTE = "\tif bytes.ContainsRune(raw, '\\'') && !bytes.ContainsRune(raw, '\"') {\n"
BYTES_ESC = "\t\tcase c < 0x20 || c >= 0x7f:\n"
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

# ── 批 2（curriculum）的锚点 ──────────────────────────────────────────────────
SECOND_GATE = (
    '\t// 第二趟：Python 侧的 require_user 再打一条同样的 SELECT，也再判一次 40401。\n'
    '\tsecond, err := s.batch2.Store.CurriculumUser(ctx, userID)\n'
    '\tif err != nil {\n'
    '\t\treturn second, apierr.New(apierr.CodeDatabaseError, "数据库错误 / Database error")\n'
    '\t}\n'
    '\tif !second.Found {\n'
    '\t\treturn second, apierr.New(apierr.CodeUserNotFound, "用户不存在 / User not found")\n'
    '\t}\n'
    '\treturn second, nil\n')
PYSTRING_TRUTHY = '\tcase pyString:\n\t\treturn v.str != ""\n'
REFS_FALLBACK = '\t\treturn pyArrayValue()\n'
WEEK_TZ = '\tmonday, start, end, today := domain.CurriculumWindow(user.Timezone, now)\n'
WEEK_KEYS = '\t\tfor _, key := range []string{"title", "assertion", "question"} {\n'
READ_CALL = '\t\tpayload.set("read_at", pyStringValue(pyISOOffset(s.now().UTC())))\n'
READ_HELPER = '\treturn pyISO(t)[:len(pyISO(t))-1] + "+00:00"\n'
READ_GUARD = '\tif !pyTruthy(payloadGet(payload, "read_at")) {\n'
READ_FORBIDDEN = (
    '\tif !event.Found || event.UserID != user.ID || event.Kind != domain.CurriculumKind {\n'
    '\t\twriteJSONError(w, r, apierr.New(apierr.CodeForbidden, forbiddenCardMessage))\n'
    '\t\treturn\n'
    '\t}\n')
REVEALED = '\t\trevealed := !day.After(today)\n'
UNREAD_COND = '\t\tif revealed && !card.HasReadAt {\n'
MONDAY_ZERO = '\treturn (int(d.Weekday()) + 6) % 7\n'
CIVIL_START = '\treturn time.Date(civil.Year(), civil.Month(), civil.Day(), 0, 0, 0, 0, zone).UTC()\n'
SLOT_NEG = '\tif slot < 0 {\n'
SQL_WINDOW = ('\tWHERE user_id = $1 AND event_kind = $2 AND scheduled_at >= $3 '
              'AND scheduled_at < $4\n')
SQL_ORDER = '\tORDER BY scheduled_at ASC\n'
SQL_ACTIVE = "\tWHERE id = $1 AND status = 'active'"
SQL_UPDATE = '\tUPDATE journey_events SET payload = $2 WHERE id = $1`\n'
LIMIT_50 = '\tCurriculumListLimit = 50\n'
KIND_LIT = '\tCurriculumKind = "curriculum"\n'
FORBID_MSG = 'const forbiddenCardMessage = "无权访问该主题卡"\n'
CONSENT_MSG = ('const errAIAgreementRequired = "使用 AI 功能前，请先阅读并同意 AI 数据共享说明 / " +\n'
               '\t"Please review and agree to the AI data sharing notice before using AI features"\n')
BATCH2_GUARD = '\tif s.batch2 != nil {\n'
CFG_CURR = '\t\tCutoverCurriculum:      envInt("CLOSEN_CUTOVER_CURRICULUM", 0),\n'
CM_CURR = '  CLOSEN_CUTOVER_CURRICULUM: "0"\n'
ING_CURR = ('          - path: /api/v1/curriculum\n'
            '            pathType: Prefix\n'
            '            backend:\n'
            '              service:\n'
            '                name: go-api\n')
TZDATA_LINE = '\t_ "time/tzdata"\n'
FREEZE_UTC_NOW = '    curriculum_module.utc_now = lambda: ANCHOR\n'
# recorded_at 每次重录都会变，锚点从夹具现读而不是写死日期；形状仍要求 +00:00，
# 因为 #73 要证的就是「写成 Z 结尾」这一形态分家。
_fx_text = FX.read_text(encoding="utf-8")
_fx_anchor = re.search(
    r'"recorded_at": "(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\+00:00"', _fx_text)
assert _fx_anchor, "夹具 recorded_at 不再是 +00:00 形状，#73 的锚点要跟着改"
FX_ANCHOR = f'"recorded_at": "{_fx_anchor.group(1)}+00:00"'
FX_ANCHOR_Z = f'"recorded_at": "{_fx_anchor.group(1)}Z"'
FX_SCHED = '"scheduled_at": "2026-10-12T01:00:00+00:00"'
DEP_WEEK = '@router.get("/curriculum/week", deprecated=True)\n'
READ_ROUTE = '@router.post("/curriculum/{event_id}/read", deprecated=True)\n'

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
          "客户端会收到「这条路要没了」的错误信号（2026-10-15 改指批 2 的 exact-set 闸： "
          "批 1 那条已改成子集口径，反向判据由 `test_deprecated_set_is_exactly_the_cut_domains` 全量守）",
      "pytest", f"{B2_PYTEST}::test_deprecated_set_is_exactly_the_cut_domains", "",
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
            '\tOccurredAtCorpus []n8CorpusEntry `json:"occurred_at_corpus"`\n',
            '\tOccurredAtCorpus []n8CorpusEntry `json:"occurred_at_corpuss"`\n')),

    # ══ 批 2：curriculum 域 ══════════════════════════════════════════════════
    # ── HTTP 层：同意门、时区、真值判定与回退分支 ─────────────────────────────
    m(42, "两道同意门合并成一次 SELECT：Python 是 require_ai_consent 与 require_user 各查一次，"
          "合并后「用户已注销」与「卡片越权」谁先报错换一侧（次序本身是契约）",
      "go", API_TESTS, "TestCurriculumWeekReadsConsentBeforeCards",
      backup(B2), patch(B2, SECOND_GATE, "\treturn first, nil\n")),

    m(43, "周窗口取的是**用户**时区：写成 UTC 后非 UTC 用户（夹具里的 curr-sh，Asia/Shanghai）"
          "整周 week_start/date/revealed 平移，28 条回放全红",
      "go", API_TESTS, "TestN8CurriculumContractBytes",
      backup(CW), patch(CW, WEEK_TZ,
                        '\tmonday, start, end, today := domain.CurriculumWindow("UTC", now)\n')),

    m(44, "payload 三个内容键的书写序＝Python get_week 里那个 dict 字面量的顺序："
          "换序后响应字节不同（键序是契约，不是排版）",
      "go", API_TESTS, "TestN8CurriculumContractBytes",
      backup(CW), patch(CW, WEEK_KEYS,
                        '\t\tfor _, key := range []string{"title", "question", "assertion"} {\n')),

    m(45, "`bool(payload.get(\"read_at\"))` 写成「非空即可」：payload 里 read_at 若是空串，"
          "Python 判未读而 Go 判已读 ⇒ read 与 unread_count 一起逐字不同",
      "go", API_TESTS, "TestPyTruthyIsPythonBoolNotNullCheck",
      backup(B2), patch(B2, PYSTRING_TRUTHY, '\tcase pyString:\n\t\treturn true\n')),

    m(46, "坏 memory_refs 的回退值：Python `_refs` 回 []，nil/null 会让前端拿到 null。"
          "回退成 pyNull 后 refsArray 的 kind 就不是数组",
      "go", API_TESTS, "TestRefsArrayFallsBackToEmptyArrayNotNull",
      backup(B2), patch(B2, REFS_FALLBACK, '\t\treturn pyNullValue()\n')),

    m(47, "isoformat 的 UTC 后缀（helper 本体）：read_at 走的是 Python 自己的 "
          "`datetime.isoformat()`（+00:00），不是 pydantic 序列化器的 Z；"
          "两条格式合并成一条，写回库的文本就换了一种形状",
      "go", API_TESTS, "TestPyISOOffsetAndPyISOAreDifferentFormats",
      backup(B2), patch(B2, READ_HELPER, '\treturn pyISO(t)\n')),

    m(48, "越权三情形拆开报错：行不存在改回 40401 ⇒ 这个端点变成 id 枚举 oracle"
          "（能区分「不存在」与「无权」），Python 侧是合成同一条 403",
      "go", API_TESTS, "TestCurriculumReadForbiddenIsOneMergedCode",
      backup(CR), patch(CR, READ_FORBIDDEN,
                        '\tif !event.Found {\n'
                        '\t\twriteJSONError(w, r, apierr.New(apierr.CodeUserNotFound, '
                        '"用户不存在 / User not found"))\n'
                        '\t\treturn\n'
                        '\t}\n'
                        '\tif event.UserID != user.ID || event.Kind != domain.CurriculumKind {\n'
                        '\t\twriteJSONError(w, r, apierr.New(apierr.CodeForbidden, '
                        'forbiddenCardMessage))\n'
                        '\t\treturn\n'
                        '\t}\n')),

    m(49, "已读门的幂等性：`if !pyTruthy(...)` 失效后第二次调用照样 UPDATE，"
          "read_at 被覆盖成当下 ⇒ 同一张卡两次请求改了两行历史",
      "go", API_TESTS, "TestCurriculumReadIsIdempotentInStoreCalls",
      backup(CR), patch(CR, READ_GUARD,
                        '\tif !pyTruthy(payloadGet(payload, "read_at")) || true {\n')),

    m(50, "read_at 的写回格式（调用点）：换成 pyISO 就是 Z 结尾，"
          "而 Python 写进库的是 +00:00；这个文本不进响应，只有这里的写回断言看得见",
      "go", API_TESTS, "TestCurriculumReadIsIdempotentInStoreCalls",
      backup(CR), patch(CR, READ_CALL,
                        '\t\tpayload.set("read_at", pyStringValue(pyISO(s.now().UTC())))\n')),

    # ── 域层：日历算术（批 2 独有的负担）──────────────────────────────────────
    m(51, "revealed 的今日界：Python 是 `day <= today`，改成严格小于 ⇒ 今天那张卡"
          "不出卡、unread_count 少 1（周视图最右侧一整格消失）",
      "go", DOMAIN_TESTS, "TestBuildCurriculumWeek",
      backup(DC), patch(DC, REVEALED, '\t\trevealed := day.Before(today)\n')),

    m(52, "unread 只算已揭开且未读：去掉 revealed 条件后未来格子也计未读，"
          "unread_count 比 Python 大 ⇒ 前端红点数量错",
      "go", DOMAIN_TESTS, "TestBuildCurriculumWeek",
      backup(DC), patch(DC, UNREAD_COND, '\t\tif !card.HasReadAt {\n')),

    m(53, "Go 的 Sunday=0 换 Python 的 Monday=0：少这一折算时，周日用户的「本周」"
          "少一整天，899 条 week_window 语料当场对不上",
      "go", API_TESTS, "TestN8WeekWindowCorpus",
      backup(DC), patch(DC, MONDAY_ZERO, '\treturn int(d.Weekday())\n')),

    m(54, "日历日与瞬间的分家：把本地周一直接当 UTC 午夜用，Santiago/Tehran 那两条"
          "「本地零点缺位」的语料窗口两端各错一小时（gap 分支正是为此存在）",
      "go", DOMAIN_TESTS, "TestCivilDayStartUTCUsesPreTransitionOffset",
      backup(DC), patch(DC, CIVIL_START, '\treturn civil.UTC()\n')),

    m(55, "day_no % 7 的负数支：Python 的 % 对负数返回非负，Go 跟着被除数符号；"
          "少了归一，历史脏值（day_no=-1）那一格在 Go 永远不出卡而 Python 会出",
      "go", DOMAIN_TESTS, "TestCurriculumSlotMatchesPythonModulo",
      backup(DC), patch(DC, SLOT_NEG, '\tif false {\n')),

    # ── 库层谓词（实库测才看得见：逐字节回放只看「这批行」）───────────────────
    m(56, "周窗口右端必须开：`scheduled_at < end` 改成闭区间后，下周一 00:00:00 整的卡"
          "同时属于两周，Python 的 list_kind_between 不会这样取",
      "go", STORE_TESTS, "TestCurriculumCardsWindowIsHalfOpen",
      backup(LB), patch(LB, SQL_WINDOW,
                        SQL_WINDOW.replace("scheduled_at < $4", "scheduled_at <= $4"))),

    m(57, "取行顺序不能动：同一格「后写覆盖先写」，DESC 之后被覆盖的是另一行，"
          "响应里的 id/payload 跟着换（Python 侧 ORDER BY scheduled_at ASC）",
      "go", STORE_TESTS, "TestCurriculumCardsOrderAndLimit",
      backup(LB), patch(LB, SQL_ORDER, '\tORDER BY scheduled_at DESC\n')),

    m(58, "users 的 status='active' 谓词：去掉后已注销用户变成「查得到但没同意」，"
          "Python 侧这里直接 40401（user_repository.get_user 同一条 WHERE）",
      "go", STORE_TESTS, "TestCurriculumUserStatusAndConsent",
      backup(LB), patch(LB, SQL_ACTIVE, "\tWHERE id = $1")),

    m(59, "写回 payload 时不许碰 memory_refs：Python 的 set_payload 只带 payload 参数时"
          "不写这列；顺带清空 = 回链数据被读卡动作销毁",
      "go", STORE_TESTS, "TestSetJourneyPayloadLeavesOtherColumns",
      backup(LB), patch(LB, SQL_UPDATE,
                        '\tUPDATE journey_events SET payload = $2, memory_refs = NULL '
                        'WHERE id = $1`\n')),

    # ── Python 侧：跨语言字面量、登记面与机制存在性 ────────────────────────────
    m(60, "周窗行数上限 50（journey_repository.list_kind_between 的默认 limit）："
          "Go 侧改一个数字，Python 的跨语言闸必须红",
      "pytest", f"{B2_PYTEST}::test_kind_week_days_and_limit_match_go", "",
      backup(DC), patch(DC, LIMIT_50, '\tCurriculumListLimit = 51\n')),

    m(61, "event_kind 字面量：curriculum 的存储刻意复用 journey_events，"
          "改一个字母就查不到任何卡，而 Go 侧全绿（回空数组）",
      "pytest", f"{B2_PYTEST}::test_kind_week_days_and_limit_match_go", "",
      backup(DC), patch(DC, KIND_LIT, '\tCurriculumKind = "curriculums"\n')),

    m(62, "越权文案逐字：Go 少抄或改写「无权访问该主题卡」，403 体就与 Python 不同字节",
      "pytest", f"{B2_PYTEST}::test_forbidden_card_message_and_codes_match_go", "",
      backup(CR), patch(CR, FORBID_MSG, 'const forbiddenCardMessage = "无权访问该主题卡哦"\n')),

    m(63, "AI 同意文案的英文半句：40303 的 message 是中英拼在一起的一条字面量，"
          "少半句就是另一种契约字节",
      "pytest", f"{B2_PYTEST}::test_ai_consent_message_matches_go_literal", "",
      backup(B2), patch(B2, CONSENT_MSG,
                        'const errAIAgreementRequired = "使用 AI 功能前，请先阅读并同意 '
                        'AI 数据共享说明"\n')),

    m(64, "同一处「合并第二趟 SELECT」从**另一侧**看：改 Go 的人本地只跑 Go 测也行，"
          "但 Python 侧的源面闸必须同时红——否则跨语言口径靠人记得",
      "pytest", f"{B2_PYTEST}::test_consent_checks_run_before_any_event_read", "",
      backup(B2), patch(B2, SECOND_GATE, "\treturn first, nil\n")),

    m(65, "第④步的 curriculum/week 撤掉 deprecated：openapi 与 Ingress 归属互相说谎"
          "（清单说这条已归 Go，契约却说 Python 权威）",
      "pytest", f"{B2_PYTEST}::test_deprecated_set_is_exactly_the_cut_domains", "",
      backup(RC), patch(RC, DEP_WEEK, '@router.get("/curriculum/week")\n')),

    m(66, "回滚面：路由本体一改路径，灰度调回 0 时 Python 答不上这条 URL",
      "pytest", f"{B2_PYTEST}::test_cut_paths_are_still_routable_for_rollback", "",
      backup(RC), patch(RC, READ_ROUTE,
                        '@router.post("/curriculum/{event_id}/mark-read", deprecated=True)\n')),

    m(67, "注册挪出「批 2 已接线」开关：机制没接线时 Go 也答这一域，"
          "Ingress 一刀指过来就是 500/空响应，而不是回落到 py-api",
      "pytest", f"{B2_PYTEST}::test_go_registers_both_curriculum_routes", "",
      backup(SV), patch(SV, BATCH2_GUARD, '\tif true {\n')),

    m(68, "三个登记面的一致性：把 curriculum 的 Ingress backend 改回 py-api，"
          "0% 下**功能没有变化**（Go 本来只逐字转交），但「这一域已交给 Go」在"
          "Ingress 处失联——放量那次会以为自己在切一条没接管的域",
      "pytest", f"{B2_PYTEST}::test_three_registers_agree_on_the_cut_domains", "",
      backup(ING), patch(ING, ING_CURR, ING_CURR.replace("name: go-api", "name: py-api"))),

    m(69, "二进制不嵌 IANA 时区库：运行镜像是 alpine 且没装 tzdata（2026-10-14 实测），"
          "LoadLocation 对每个真实用户失败 ⇒ 静默退回 UTC，"
          "而那条回退与 Python 的 safe_zoneinfo 同形，899 条语料与 28 条回放全都看不出",
      "pytest", f"{B2_PYTEST}::test_runtime_images_agree_on_where_zoneinfo_comes_from", "",
      backup(MG), patch(MG, TZDATA_LINE, '\t// 变异：撤掉嵌入的时区数据库\n')),

    m(70, "curriculum 灰度默认 100：忘了配键的部署直接把这一域全给 Go，"
          "「部署了但没开灰度」这一安全态就没了（与批 1 的 analytics 同一条口径）",
      "go", CFG_TESTS, "TestN8CutoverDefaults",
      backup(CF), patch(CF, CFG_CURR,
                        '\t\tCutoverCurriculum:      envInt("CLOSEN_CUTOVER_CURRICULUM", 100),\n')),

    m(71, "ConfigMap 里 curriculum 放量到 1：本轮判据是「百分比仍为 0」，"
          "改这里而不同步改断言＝悄悄放量没人知道",
      "pytest", f"{K8S_PYTEST}::test_ingress_is_at_zero_percent_rollout", "",
      backup(CFG), patch(CFG, CM_CURR, '  CLOSEN_CUTOVER_CURRICULUM: "1"\n')),

    m(72, "录制侧不冻 utc_now：录出来的 read_at/week 属于录制那一刻，"
          "而 Go 侧锚点钉在夹具的 recorded_at，两侧比的不是同一个星期",
      "pytest", f"{B2_PYTEST}::test_recorder_freezes_python_clock_for_curriculum", "",
      backup(GS), patch(GS, FREEZE_UTC_NOW, '    # 变异：忘了冻 utc_now\n')),

    m(73, "锚点的形状：recorded_at 写成 Z 结尾后，Go 侧 `time.Parse(RFC3339)` 仍能读，"
          "但夹具与 Python 的 isoformat 口径分家（播种行、read_at 都是 +00:00 形）",
      "pytest", f"{B2_PYTEST}::test_anchor_is_recorded_for_the_frozen_clock", "",
      backup(FX), patch(FX, FX_ANCHOR, '"recorded_at": "2026-10-14T14:45:30Z"')),

    m(74, "播种行改回相对当下：curriculum 的窗口按「本周」取行，相对播种会让两侧"
          "在不同日期读到不同一周，28 条回放的字节就成了掷骰子",
      "pytest", f"{B2_PYTEST}::test_anchor_is_recorded_for_the_frozen_clock", "",
      backup(FX), patch(FX, FX_SCHED, '"scheduled_at": "days_ago:2"')),

    # ── 批 1 登记「没有观测点」的两支，补夹具后第一次可证 ─────────────────────
    m(75, "repr(bytes) 的引号选择：体里含单引号且不含双引号时 Python 用双引号包裹 "
          "（`b\"it's fine\"`）。固定用单引号会写成 `b'it\\'s fine'`，"
          "400 体的 input 逐字不同（batch-content-type-text-plain-single-quote）",
      "go", API_TESTS, "TestN8ValidationContractBytes",
      backup(AB), patch(AB, BYTES_QUOTE, "\tif false {\n")),

    m(76, "repr(bytes) 的 >=0x7f 转义：非 ASCII 字节的 repr 必须是 `\\xNN`，"
          "原样写出 UTF-8 字节会让 400 体里的 input 变成另一串字节"
          "（batch-content-type-text-plain-nonascii）",
      "go", API_TESTS, "TestN8ValidationContractBytes",
      backup(AB), patch(AB, BYTES_ESC, "\t\tcase c < 0x20:\n")),

    m(77, "Content-Type 的 +json 判定松成「子类型里含 json 就行」：重复逗号头 "
          "`application/json, application/json` 的 sub 是 `json, application/json`，"
          "两侧实测都判**非 JSON**（走 bytes repr）；松判定会让 Go 去解 JSON，"
          "400 体换成 json_invalid 形态（batch-content-type-duplicate-json）",
      "go", API_TESTS, "TestN8ValidationContractBytes",
      backup(AB), patch(AB, CT_RETURN, CT_JSONISH)),
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
                "./internal/config/", "./internal/store/"):
        proc = subprocess.run(["go", "test", pkg, "-count=1"], cwd=GO,
                              capture_output=True, text=True, env=ENV, timeout=900)
        tail = (proc.stdout + proc.stderr).strip().splitlines()
        print(f"[{'GREEN' if proc.returncode == 0 else 'BROKEN'}] 复跑 {pkg}："
              f"{tail[-1] if tail else ''}")
        if proc.returncode != 0:
            print((proc.stdout + proc.stderr)[-2000:])
            return 1
    proc = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "pytest", B1_PYTEST, B2_PYTEST, K8S_PYTEST,
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
