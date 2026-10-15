"""N8 切流闸门的变异自证（批 1：analytics + discovery；批 2：curriculum；批 3：journey；批 4：onboarding；批 5：mood + reflections）。

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
8. 批 3②：`replayN8Case` 里那句「Go 侧掩码次数必须等于夹具的 id_masks」**不打独立红**，
   所以不进变异表。根因是它与逐字比看的是同一支：uuid 形状的行数一变，响应里的
   `events` 数组长度就变，掩完的字节先红。它的价值只在诊断（坏的时候能一眼说出是
   「行数变了」还是「字节变了」），不是一条独立口径——写在这里免得被当成第五条已证。
9. 批 3④（2026-10-15）的分工与批 1 同一条：**坏 Go 源、红 Python 闸**（#99–#110，12 条）
   ＋**坏夹具/录制面、红 Python 闸**（#111–#116，6 条）。这一批的口径大半是
   「两侧的常量、SQL 谓词、掩码正则还同不同源」，Go 的逐字节回放看不见这些——
   它只比字节，常量漂了只要两侧一起漂它就全绿。所以 journey 那 16 条 Python 闸
   每条都要能被打红一次，否则改 Go 的人本地 CI 一片绿、上线放量才现形。
   实测（2026-10-15，串行三趟）：**18/18 打红**，覆盖 journey 那 16 条 Python 闸
   （`test_list_limit_and_push_fallback_literals_match_go` 与
   `test_ack_contract_literals_match_go` 各由两条不同变异打红）；
   备份写回后 Go 五个包与四份 Python 闸文件全绿。
   本批**没做**变异的只剩两处，都不是闸门缺位而是没有观测点：
   - Go 侧 `TestN8MaskedFieldsStayRedacted` 的「录制体再掩一次必须无变化」：夹具体里的
     uuid 早就掩成 `<event_id>` 了，形状再掩一次恒等；白名单漂一个键名由 #108（改正则）
     与 Python 侧 `masked_fields == [generated_at, journey_event_id]` 双向挡住。
   - Ingress 的 `path == /api/v1/<域>` 形状闸在既有 k8s 文件里（批 1 建），本批只往
     登记表加一个 journey，加错域名的红由 #110 与那条既有闸各自打，不另立条目。

批 4（2026-10-15，onboarding 域）实测：**10/10 打红**（#117–#126）。这一批的条目形态与
前三批不同，因为批 4 的口径大半落在**函数**上而不是 HTTP 字节上（措辞闸、CPython 的 upper、
深链的 rstrip），HTTP 面看不见它们；两处「只有这一批才发现打不红」的假口径也当场补成可证：
- #117 第一轮**打不红**，根因不在闸而在夹具：`persona_upper_corpus` 原先一律记 `char×4`，
  而 ß 的大写本身是两个字符 ⇒ `ßßßß`→`SSSSSSSS` 永远不合法 ⇒ 「多码位展开」这一整支
  在这张表里没有观测点。录制侧与 Go 侧同改为「按 k∈{1,2,3,4} 找见证」，合法码位 28⇒34，
  #117 随即打红；同一处坏实现另立 #118 打 HTTP 字节面，两层各证一次。
- 措辞闸的闭包 92 条实测**一条都不被闸掉** ⇒ 「命中即吞」没有观测点（把 Go 的整条判据
  删掉也全绿）。生成脚本补了 14 条 `hit-*`/`safe-*` 探针（词条从 safety_service 的表里
  现取、方向由录制侧断言），于是 #119–#122 四条各能被打红：少黑名单、third 不必需、
  `\b` 补后边界、RE2 手写的后视失效。
- #124/#126 证的是本批新写的两条**语义**：掩码按夹具计数分派（记 0＝确定值，刻意留原文
  逐字比），以及 profile 的 created_at 种子值必须与冻时钟**不同值**——否则「从列取」与
  「从钟算」同形，那条支路会被巧合蒙过。
- #125 不是假想变异：第一版 Go 就是按形状猜写成 `model_type`＋`class_name` 的，
  `onb-answer-item-not-dict` 的逐字节比对把它打红之后才按实测改正。

批 4④（2026-10-15）实测：**23/23 打红**（#127–#149），一条不多一条不少——这一批的
受害面换成了 Python 侧新写的 `test_n8_batch4_contract.py`（23 条闸），因为本批的口径
大半是「两侧的常量、表、键序、SQL 谓词还同不同源」，Go 的逐字节回放看不见这些：
常量两边一起漂它就全绿。所以那 23 条每条都要能被打红一次，否则改 Go 的人本地 CI 一片绿、
放量那次才在现网现形。几处值得留名的：
- #127/#128 打的是 `go:embed` 的形状：模板副本分叉在**两侧各自都合法**，回放比不出差异。
- #132/#133/#136/#146 坏的是夹具本身（探针方向、可达性计数、upper 闭包、组名）——
  语料过期不是 Go 的错，但只有 Python 侧现算才看得见它过期。
- #137 与 #123 是**同一处坏法打两层**（Go 的语料测看结果、Python 的闸看源码形状）；
  #131/#134 打的都是「判据形状的字面量」，语料面碰不到那一格时只有文本比对拦得住。
- 备份写回后复跑：Go 五个包全绿，四份 Python 闸文件（批 1–4）76 passed / 1 skipped。

批 5④（2026-10-15，mood + reflections 两个域）实测：**24/24 打红**（#150–#173）。
这一批的口径集中在四种只有跨语言闸看得见的东西：**表**（MOOD_MAP 六键、15×4=60 格文案闭包）、
**序**（SQL 的 ASC/DESC、`CopySupportedLocales` 的前缀匹配顺序、响应键序）、
**边界数**（limit=30、days 的 ge/le、`avg >= 3` 那个等号）、**登记面不对称**（D-10）。
四处值得留名的处理：
- #164 与 #165 是**同一格坏两次、两侧各一次**：先坏 Go 内嵌的 `templates/copy_mood.json`
  副本（Python 现算不动），再坏 `backend/data/copy/zh.json` 原件（副本不动）。
  60 格闭包若只比被 HTTP 用例命中的那一格，其余 59 格会静默分叉——这两条就是那 59 格的证据。
- #167 打的不是「Go 少实现一条」，而是**本批新登记的不对称本身**（决策点 D-10：
  Ingress 的 path 匹配不看 HTTP 方法，`/api/v1/reflections` 一个 path 上挂着 GET 与 POST，
  把 GET 搬走＝POST 打在没注册它的 mux 上＝405）。所以这条变异是「把那一刀补回去」，
  红的正是清单闸里按 (方法, 路径) 模拟路由的那条判据。
- #171/#172/#173 三条守的是**录制侧的冻钟**，而它源于本轮真实踩到的空转：
  `backend/repositories/__init__.py:26` 把子模块 `mood_repository` 遮蔽成单例实例，
  `from backend.repositories import mood_repository` 后 `实例.utc_now = lambda: ANCHOR`
  全局永远读不到 ⇒ 时钟看着钉了、其实没钉。批 2–4 全绿只是判据离边界够远；
  本批 `mood-t-edge` 有一行正好压在 14 天窗口边界上，才把空转变成可证的字节差。
  ↳ 连带修的闸：`test_n8_batch2_contract.py::test_recorder_freezes_python_clock_for_curriculum`
  原先只正则匹配 `\.utc_now\s*=`，录制脚本改成钉四个模块后，单独撤掉 curriculum 那一行
  **打不红**（别家模块那行把正则喂饱了）⇒ 与批 3 的 journey 闸统一为「行首 + 模块名」，
  `gen_n8_contract_goldens.py` 的 freeze_clock 也从 `for mod in modules.values()` 回退成
  逐行点名赋值，就是为了保住「这一行能被单独写坏」这件事。
- #170 的锚点第一轮写错（`"days_ago": 0.5,` 在夹具里 0 命中），按实测值改成
  `"days_ago": 2.5`（5 处）；#158 的 claim 字符串里嵌套引号导致整份脚本 `SyntaxError`，
  一并改正。`--check` 与全表跑之前都过了一遍锚点自检：**173 条，坏锚点 0 个**。

用法：
    .venv/bin/python scripts/mutate_n8_gates.py            # 全部（串行）
    .venv/bin/python scripts/mutate_n8_gates.py --check    # 只验锚点命中数，不跑测试
    .venv/bin/python scripts/mutate_n8_gates.py 3 12 27     # 只跑点名几条
"""
from __future__ import annotations

import filecmp
import json
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

# 批 3（journey 域）
JE = GO / "internal/httpapi/journey_events.go"
JA = GO / "internal/httpapi/journey_ack.go"
PB = GO / "internal/httpapi/pybody.go"
B3 = GO / "internal/httpapi/batch3.go"
LB3 = GO / "internal/store/legacy_batch3.go"
DJ = GO / "internal/domain/journey.go"
RJ = PY / "routers/journey.py"

# 批 4（onboarding 域：措辞闸 / CPython upper / 深链 / 两条端点的字节面）
PT = GO / "internal/domain/pytext.go"
FR = GO / "internal/domain/first_read.go"
FG = GO / "internal/domain/first_read_gate.go"
OB = GO / "internal/httpapi/onboarding_body.go"
OS = GO / "internal/httpapi/onboarding_submit.go"
OR = GO / "internal/httpapi/onboarding_read.go"
B4 = GO / "internal/httpapi/batch4.go"
LS = GO / "internal/store/legacy_batch4.go"
TPL_ZH = GO / "internal/domain/templates/first_read_zh.json"
RO = PY / "routers/onboarding.py"
B4_PYTEST = "backend/tests/test_n8_batch4_contract.py"

# ── 批 5（mood + reflections 三条纯读）的文件面 ──────────────────────────────
DMOOD = GO / "internal/domain/mood.go"
LB5 = GO / "internal/store/legacy_batch5.go"
MHIST = GO / "internal/httpapi/mood_history.go"
MTREND = GO / "internal/httpapi/mood_trend.go"
REFL = GO / "internal/httpapi/reflections_list.go"
CMOOD = GO / "internal/domain/templates/copy_mood.json"
PYZH = PY / "data/copy/zh.json"
B5_PYTEST = "backend/tests/test_n8_batch5_contract.py"

API_TESTS = GO / "internal/httpapi"
DOMAIN_TESTS = GO / "internal/domain"
CUT_TESTS = GO / "internal/cutover"
CFG_TESTS = GO / "internal/config"
STORE_TESTS = GO / "internal/store"

B1_PYTEST = "backend/tests/test_n8_batch1_contract.py"
B2_PYTEST = "backend/tests/test_n8_batch2_contract.py"
B2PY = PY / "tests" / "test_n8_batch2_contract.py"
B3_PYTEST = "backend/tests/test_n8_batch3_contract.py"
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


def patch_all(path: Path, old: str, new: str):
    """夹具那种「同一形态重复多次」的面：要坏就整批坏，命中 0 次才算锚点失效。"""

    def run() -> None:
        text = path.read_text(encoding="utf-8")
        n = text.count(old)
        assert n >= 1, f"变异锚点命中 0 次：{old[:70]!r}"
        path.write_text(text.replace(old, new), encoding="utf-8")

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
CIVIL_START = ('\treturn wallClockToUTC(zone, civil.Year(), civil.Month(), civil.Day(), '
               '0, 0)\n')
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
FX_SCHED = f'"scheduled_at": "{json.loads(_fx_text)["seed"]["journey_events"][0]["scheduled_at"]}"'
DEP_WEEK = '@router.get("/curriculum/week", deprecated=True)\n'
READ_ROUTE = '@router.post("/curriculum/{event_id}/read", deprecated=True)\n'

# ── 批 3（journey 域）的锚点 ─────────────────────────────────────────────────
PAST_STRICT = '\t\t\tPast:        day.Before(today),\n'
PAST_LOOSE = '\t\t\tPast:        !day.After(today),\n'
SPACED_COMMA = '\t\t\t\tb.WriteString(", ")\n'
PREFILL_HEAD = ('func (s *Server) ensurePrefilled(ctx context.Context, userID string) '
                '*apierr.Error {\n')
PREFILL_HEAD_NOOP = PREFILL_HEAD + '\treturn nil\n'
LIST_CALL = ('\trows, err := s.batch3.Store.JourneyEvents(r.Context(), userID, '
             'domain.JourneyListLimit)\n')
LIST_CALL_AFTER_COUNT = ('\t_, _ = s.batch3.Store.JourneyUnseenCount(r.Context(), userID)\n'
                         + LIST_CALL)
SCHED_ISO = '\t\titem.set("scheduled_at", pyStringValue(pyISOOffset(row.ScheduledAt)))\n'
SCHED_ISO_Z = '\t\titem.set("scheduled_at", pyStringValue(pyISO(row.ScheduledAt)))\n'
PAYLOAD_RAW = '\t\titem.set("payload", parseJSONColumn(row.Payload, pyObjectValue()))\n'
PAYLOAD_DICT = '\t\titem.set("payload", payloadObject(row.Payload))\n'
ACK_OWNER_GATE = '\tif !owner.Found || owner.UserID != userID {\n'
ACK_ECHO_REQ = '\tout.set("acknowledged", pyStringValue(eventID))\n'
ACK_ECHO_COL = '\tout.set("acknowledged", pyStringValue(owner.ID))\n'
ACK_LEN_RUNE = '\tcase utf8.RuneCountInString(value.str) > maxJourneyEventID:\n'
ACK_LEN_BYTE = '\tcase len(value.str) > maxJourneyEventID:\n'
UTF8_IMPORT = '\t"unicode/utf8"\n'
SKIP_GATE = '\t\tif row.Past && status == "pending" {\n'
SKIP_NO_STATUS = '\t\tif row.Past {\n'
INSERT_IN_TX = ('\t\t\tif _, err := tx.Exec(ctx, insertJourneyEvent, id, userID, '
                'row.Kind, row.DayNo,\n')
INSERT_AUTOCOMMIT = INSERT_IN_TX.replace("tx.Exec", "p.pool.Exec")
BATCH3_GUARD = '\tif s.batch3 != nil {\n'
CFG_JOURNEY = '\t\tCutoverJourney:         envInt("CLOSEN_CUTOVER_JOURNEY", 0),\n'
# int() 的字符集四支（#91–#94）：全按「最可能顺手写成的 ASCII 版」破坏。
ND_LOOKUP = '\tlo, ok := ndRunStart(r)\n'
ND_NO_ND = '\tlo, ok := rune(0), false\n'
ND_MOD10 = '\treturn int((r - lo) % 10)\n'
ND_RAW_OFFSET = '\treturn int(r - lo)\n'
USCORE_GATE = ('\t\t\tif !prevDigit || i+1 >= len(runes) '
               '|| digitValue(runes[i+1]) < 0 {\n')
USCORE_LOOSE = '\t\t\tif !prevDigit && false {\n'
SPACE_TRIM = '\tfor len(runes) > 0 && unicode.IsSpace(runes[0]) {\n'
SPACE_ASCII = '\tfor len(runes) > 0 && strings.IndexRune(" \\t\\n\\r\\v\\f", runes[0]) >= 0 {\n'

# 批 3②（2026-10-15）：wallClockToUTC 换成「按定义求自洽瞬间集合」之后的四支。
# 起因是 push_moment_corpus 第一次把**重复墙钟**摆到闸面前——批 2 的 week_window 语料
# 只撞得上「缺位」那一支，而 Go 原生 time.Date 在重复墙钟上按区给相反答案
# （纽约取切换前、伦敦/悉尼/豪勋爵岛取切换后），老的「先信 guess、只有不可逆才重算」
# 写法对后者整支隐形。
WALL_LATEST = '\t\tif !found || at > earliest {\n'
WALL_EARLIEST = '\t\tif !found || at < earliest {\n'
WALL_CONSISTENCY = '\t\tif _, check := time.Unix(at, 0).In(zone).Zone(); check != o {\n'
WALL_CONSISTENCY_OFF = '\t\tif _, check := time.Unix(at, 0).In(zone).Zone(); check != o && false {\n'
WALL_NARROW_SCAN = '\tfor k := 0; k <= 1; k++ { // 变异：只采 W 附近一小时\n'
WALL_SCAN = '\tfor k := -32; k <= 32; k++ { // ±16h，30 分钟一步\n'
WALL_GAP_MIN = '\tif o2 < o1 {\n'
WALL_GAP_MIN_OFF = '\tif false && o2 < o1 {\n'

# 批 3④（2026-10-15）：坏 **Go 源**、打红的是 **Python 侧** test_n8_batch3_contract.py。
# 分工与批 1 相同：这一支的存在理由是「改 Go 的人本地 pytest 必须红」——
# 只让 Go 的回放红，等于改错了要等上线放量才发现。
SCRIPT_ORDER = '\t{"day3_weak_assert", 3},\n\t{"day5_stat", 5},\n'
SCRIPT_SWAPPED = '\t{"day5_stat", 5},\n\t{"day3_weak_assert", 3},\n'
LIST_LIMIT_50 = 'const JourneyListLimit = 50\n'
LIST_LIMIT_200 = 'const JourneyListLimit = 200\n'
PUSH_HOUR_9 = '\tdefaultPushHour   = 9\n'
PUSH_HOUR_8 = '\tdefaultPushHour   = 8\n'
ACK_MSG_FULL = 'const forbiddenJourneyMessage = "事件不存在 / Event not found"\n'
ACK_MSG_ZH_ONLY = 'const forbiddenJourneyMessage = "事件不存在"\n'
ACK_ID_36 = "const maxJourneyEventID = 36\n"
ACK_ID_64 = "const maxJourneyEventID = 64\n"
UNSEEN_IS_FALSE = "AND acknowledged IS FALSE`"
UNSEEN_EQ_FALSE = "AND acknowledged = false`"
ACK_SQL_OWNER = "SET status = 'seen', acknowledged = true WHERE id = $1"
ACK_SQL_OWNER_ADD = "SET status = 'seen', acknowledged = true WHERE id = $1 AND user_id = $2"
SILENT_USER_RETURN = '\tif !user.Found {\n\t\treturn nil\n\t}\n'
SILENT_USER_40401 = ('\tif !user.Found {\n'
                      '\t\treturn apierr.New(apierr.CodeUserNotFound, '
                      '"用户不存在 / User not found")\n\t}\n')
MASK_EVENT_RE = '"id":"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"'
MASK_EVENT_RE_TIGHT = '"id":"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{11}"'
DELEGATE_DOMAIN = 'Gate.Delegate(w, r, "journey", userID)'
DELEGATE_DOMAIN_TYPO = 'Gate.Delegate(w, r, "journeys", userID)'
CM_JOURNEY_ZERO = '  CLOSEN_CUTOVER_JOURNEY: "0"\n'
CM_JOURNEY_FULL = '  CLOSEN_CUTOVER_JOURNEY: "100"\n'
# 夹具面与录制面（#111–#114）：坏的是**语料/登记**，不是 Go 实现。
FX_GROUP_JOURNEY = '"group": "journey"'
FX_GROUP_TYPO = '"group": "journeyp"'
FX_CORPUS_KEY = '"push_moment_corpus"'
FX_CORPUS_KEY_TYPO = '"push_moment_corpusz"'
GS_FREEZE_JOURNEY_TODAY = '    journey_module.local_today = local_today_at\n'
GS_FREEZE_WRONG_MODULE = '    time_utils.local_today = local_today_at\n'
RJ_GET_DEPRECATED = '@router.get("/journey/events", deprecated=True)\n'
RJ_GET_LIVE = '@router.get("/journey/events")\n'
UPDATE_IN_TX = '\t\t\tif _, err := tx.Exec(ctx, updateJourneySchedule, id, row.ScheduledAt,\n'
UPDATE_AUTOCOMMIT = '\t\t\tif _, err := p.pool.Exec(ctx, updateJourneySchedule, id, row.ScheduledAt,\n'
FX_SEED_FULLWIDTH = ('"status": "active",\n'
                     '        "push_time": "\uff11\uff12:\uff13\uff14",')
FX_SEED_FULLWIDTH_ASCII = ('"status": "active",\n'
                           '        "push_time": "12:34",')

# 批 4（onboarding）：措辞闸的四种抄错方式、CPython upper 的展开表、深链的 rstrip、
# 掩码的「按计数应用」语义，以及两条最容易「看起来一样」的字节出口。
PT_UPPER_SHARP = '\t0xDF:   "SS", // ß\n'
FG_BANNED_AND_ATTR = ('\tif bannedHit(text, locale) || attributionHit(text, locale) {\n'
                      '\t\treturn ""\n\t}\n')
FG_ATTR_ONLY = '\tif attributionHit(text, locale) {\n\t\treturn ""\n\t}\n'
FG_THIRD_REQUIRED = ('\tthird := groupHit(groups["third"], text)\n'
                     '\tif !third {\n\t\treturn false\n\t}\n')
FG_THIRD_IGNORED = '\tthird := groupHit(groups["third"], text)\n\t_ = third\n'
FG_LOOKBEHIND = ('\t\tm.lookaway = lookaway\n'
                 '\t\tm.tail = regexp.MustCompile(`(?i)` + tail)\n\t\treturn m\n')
FG_LOOKBEHIND_DROPPED = '\t\tm.re = regexp.MustCompile(`(?i)` + tail)\n\t\treturn m\n'
FG_BANNED_LEADING_ONLY = ('\t\tout = append(out, regexp.MustCompile(`(?i)\\b`'
                          '+regexp.QuoteMeta(term)))\n')
FG_BANNED_BOTH_EDGES = ('\t\tout = append(out, regexp.MustCompile(`(?i)\\b`'
                        '+regexp.QuoteMeta(term)+`\\b`))\n')
B4_LINK_TRIM_RIGHT = '\treturn strings.TrimRight(siteURL, "/") + "/r/" + code\n'
B4_LINK_TRIM_SUFFIX = '\treturn strings.TrimSuffix(siteURL, "/") + "/r/" + code\n'
RP_MASK_BY_COUNT = '\tif want == 0 {\n\t\treturn body, 0\n\t}\n'
RP_MASK_ALWAYS = '\tif want < 0 {\n\t\treturn body, 0\n\t}\n'
OB_ITEM_NOT_DICT = ('\t\t\tkind:  "model_attributes_type",\n'
                    '\t\t\tloc:   []any{"body", "answers", index},\n'
                    '\t\t\tmsg:   "Input should be a valid dictionary or object '
                    'to extract fields from",\n'
                    '\t\t\tinput: item,\n')
OB_ITEM_MODEL_TYPE = ('\t\t\tkind:  "model_type",\n'
                      '\t\t\tloc:   []any{"body", "answers", index},\n'
                      '\t\t\tmsg:   "Input should be a valid dictionary or instance '
                      'of FirstReadAnswer",\n'
                      '\t\t\tinput: item,\n'
                      '\t\t\tctx:   pyObjectOf("class_name", pyStringValue('
                      '"FirstReadAnswer")),\n')
OR_CREATED_FROM_COLUMN = '\t\tpayload.set("created_at", pyStringValue(pyISOOffset(profile.CreatedAt)))\n'
OR_CREATED_FROM_CLOCK = '\t\tpayload.set("created_at", pyStringValue(pyISOOffset(s.now())))\n'

# 批 4④（#127–#149）：受害的是 **pytest 那一条闸**，坏的东西仍在 Go 源/夹具/清单/路由面上。
# 分工与批 3④（#99–#116）同一条口径：Go 的逐字节回放只比字节，两侧常量漂在一起它就全绿，
# 所以「字面量同源」这一半必须由 test_n8_batch4_contract.py 自己被打红一次。
TPL_ZH_CLOSER = '"closer": "30 天后，你会收到第一份《每月故事》——那时它比今天更懂你。"'
TPL_ZH_CLOSER_FORK = '"closer": "30 天后，你会收到第一份《每月故事》，那时它比今天更懂你。"'
GEN_TPL_EN = '\t"en": mustParseTemplate(firstReadEnJSON),\n'
GEN_TPL_EN_TYPO = '\t"eng": mustParseTemplate(firstReadEnJSON),\n'
FG_BANNED_ZH_ITEM = '"焦虑症", "抑郁症",'
FG_CAUSAL_ZH_ITEM = '"causal": {"导致", '
FG_COMPILE_LEADING = ('out = append(out, regexp.MustCompile(`(?i)\\b`'
                      '+regexp.QuoteMeta(term)))')
FG_COMPILE_NO_EDGE = ('out = append(out, regexp.MustCompile(`(?i)`'
                      '+regexp.QuoteMeta(term)))')
GATE_LANGKEY_HEAD = ('func SafetyLangKey(language string) string {\n'
                     '\tif strings.HasPrefix(language, "zh") {')
GATE_LANGKEY_FUSED = ('func SafetyLangKey(language string) string {\n'
                      '\tlanguage = strings.ToLower(language)\n'
                      '\tif strings.HasPrefix(language, "zh") {')
GEN_MIN_BOUND = '\tFirstReadMinAssertions = 2\n'
OB_ANSWER_LIMIT = '\tmaxFirstReadAnswers = 10\n'
SUB_KEYS_NINE = ('\tv.set("summary", pyStringValue(card.Summary))\n'
                 '\tv.set("closer", pyStringValue(card.Closer))\n'
                 '\tv.set("answer_count"')
SUB_KEYS_NINE_SWAP = ('\tv.set("closer", pyStringValue(card.Closer))\n'
                      '\tv.set("summary", pyStringValue(card.Summary))\n'
                      '\tv.set("answer_count"')
SUB_ANSWER_KEYS = ('item.set("q", pyStringValue(answer.Q))\n'
                   '\t\titem.set("option", pyStringValue(answer.Option))')
SUB_ANSWER_KEYS_SWAP = ('item.set("option", pyStringValue(answer.Option))\n'
                        '\t\titem.set("q", pyStringValue(answer.Q))')
SUB_GENERATED_OFFSET = 'v.set("generated_at", pyStringValue(pyISOOffset(now)))'
SUB_GENERATED_Z = 'v.set("generated_at", pyStringValue(pyISO(now)))'
OR_RESTORE_ID = '\tpayload.set("id", pyStringValue(profile.ID))\n'
# 「加一道 consent 闸」在代码面留不下别的痕迹时，至少这个名字会——本条证的是那个字面量闸
# 真的看代码面，而不是只看注释（注释会被 _code 剔掉，写注释声明「不检查 consent」不算证据）。
OR_CONSENT_PROBE = '\tconst consentColumn = "ai_consent"\n' + OR_RESTORE_ID
LS_CODE_GUARD = 'if user.Found && code == "" {'
LS_CODE_GUARD_FUSED = 'if code == "" {'
RP_REFERRAL_CHARS = 'regexp.MustCompile(`/r/[A-Z2-7]{8}`)'
RP_REFERRAL_CHARS_TIGHT = 'regexp.MustCompile(`/r/[A-Z2-6]{8}`)'
CM_ONBOARDING_ZERO = '  CLOSEN_CUTOVER_ONBOARDING: "0"\n'
CM_ONBOARDING_FULL = '  CLOSEN_CUTOVER_ONBOARDING: "100"\n'
RO_GET_DEPRECATED = '@router.get("/onboarding/first-read", deprecated=True)\n'
RO_GET_LIVE = '@router.get("/onboarding/first-read")\n'
FX_GROUP_ONBOARDING = '"group": "onboarding"'
FX_GROUP_ONBOARDING_TYPO = '"group": "onboardingp"'
FX_PROBE_HIT_BANNED = '"kind": "hit-banned"'
FX_PROBE_HIT_BANNED_FLIP = '"kind": "safe-banned"'
FX_REACHABLE_TRUE = '"reachable": true'
FX_PERSONA_SHARP = '"raw": "\u00df\u00df",\n      "upper": "SSSS"'
FX_PERSONA_SHARP_BAD = '"raw": "\u00df\u00df",\n      "upper": "SSss"'
B2_CUT_ROLLCALL = '"journey", "onboarding", "mood")'
B2_CUT_ROLLCALL_DROP = '"journey", "onboardingx", "mood")'

# ── 批 5④（2026-10-15）：mood + reflections 三条纯读的锚点 ──────────────────
# 分工沿用批 3④/批 4④：**坏 Go 源也能红 Python 闸**（这一批的口径大半是
# 「两侧常量、文案副本、SQL 谓词同不同源」，Go 的逐字节回放看不见这些——它只比字节，
# 两侧一起漂它就全绿）；反过来坏文案源文件/坏夹具红的也是 Python 闸。
B5_HISTORY_LIMIT = 'const MoodHistoryLimit = 30\n'
B5_AVG_MID = '\tcase avg >= 3:\n'
B5_SAMEDAY = '\t\tif _, ok := seen[label]; ok {\n\t\t\tcontinue\n\t\t}\n'
B5_LABEL_FMT = '\t\tlabel := local.Format("01-02")\n'
B5_DAYS_MAX = '\tMoodTrendMaxDays     = 90\n'
B5_COPY_LOCALE = '\tif v := moodCopy[locale][key]; v != "" {'
B5_FALSY = '\tif raw == "" {\n\t\treturn nil\n\t}'
B5_TREND_ASC = '\tORDER BY created_at ASC`'
B5_HIST_DESC = '\tORDER BY created_at DESC\n\tLIMIT $2'
B5_ACTIVE_WHERE = "\tWHERE id = $1 AND status = 'active'`"
B5_TYPE_WHERE = '\tWHERE user_id = $1 AND record_type = $2\n'
B5_HIST_PYISO = '\t\t\tCreatedAt:        pyISO(row.CreatedAt),\n'
B5_LEVEL_TABLE = 'var MoodLevels = map[string]int{\n\t"\U0001f60a": 5,\n'
B5_LOCALE_ORDER = 'var CopySupportedLocales = []string{\n\t"zh", "en", "es", "ko", "ja", "ru",\n'
# 文案库与内嵌副本的同一格：两侧各坏一次，红的是同一格（60 格闭包只比被命中的那一格＝空转）。
B5_ZH_HIGH = '"high": "\u6574\u4f53\u60c5\u7eea\u72b6\u6001\u4e0d\u9519\uff0c\u4fdd\u6301\u8fd9\u4e2a\u8282\u594f\u3002"'
B5_MOOD_GROUP = '"group": "mood"'
CM_MOOD_ZERO = '  CLOSEN_CUTOVER_MOOD: "0"\n'
ING_MOOD_TREND = ('          - path: /api/v1/mood/trend\n'
                  '            pathType: Exact\n'
                  '            backend:\n'
                  '              service:\n'
                  '                name: go-api\n'
                  '                port:\n'
                  '                  name: http\n')
RO_HISTORY_DEPRECATED = '@router.get("/mood/history", deprecated=True)\n'
# 批 5 第 12 条（mood 时钟钉在**真模块**）的三支锚点：赋值目标、取模块那一支、钉后自证。
# 2026-10-15 的空转就是这三者里少了第三支才会发生：`_module()` 换成包属性查找后，
# 赋值打在 MoodRepository 单例实例上，全局读不到而两侧字节仍可能全绿。
GS_MOOD_FREEZE = '    mood_module.utc_now = lambda: ANCHOR\n'
GS_MOOD_MODULE_GET = 'mood_module = _module("backend.repositories.mood_repository")'
GS_MOOD_ATTR_GET = 'mood_module = __import__("backend.repositories").mood_repository'
GS_MOOD_SELFCHECK = 'if mod.utc_now() != ANCHOR:'

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
          "在不同日期读到不同一周，28 条回放的字节就成了掷骰子"
          "（批 3 重录夹具后同一 scheduled_at 形态出现 15 次，所以整批换掉）",
      "pytest", f"{B2_PYTEST}::test_anchor_is_recorded_for_the_frozen_clock", "",
      backup(FX), patch_all(FX, FX_SCHED, '"scheduled_at": "days_ago:2"')),

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

    # ── 批 3（journey 域）：只有这里能打红的四类口径 ────────────────────────────
    # 分工：#78–#84 打的是 Go 单元/集成测（假件与真库），逐字节回放看不到「为什么」；
    # #85–#87 打的是机制存在性（接线开关、灰度默认值）。
    m(78, "Past 的边界从 `day < today` 松成 `day <= today`：day0 恰好是基准日那天时，"
          "Python 不判过期、Go 判过期 ⇒ 当天就把自己刚生成的 day0 事件 skip 掉，"
          "unseen 计数与剧本行状态同时分家（domain 表测钉的就是这个边界）",
      "go", DOMAIN_TESTS, "TestJourneyPrefillPlanShape",
      backup(DJ), patch(DJ, PAST_STRICT, PAST_LOOSE)),

    m(79, "写进 payload 列的文本用响应面的紧凑分隔符：Python 那边是 "
          "`json.dumps(dict)` 的**默认**分隔符（逗号后带空格、冒号后带空格），"
          "两侧写出的列文本不同⇒下一轮读取时「谁覆盖谁」就可能比出差异",
      "go", API_TESTS, "PyDumpsSpaced|TestJourneyPrefillPayloadText",
      backup(PB), patch(PB, SPACED_COMMA, '\t\t\t\tb.WriteString(",")\n')),

    m(80, "GET 不做预写：journey 的列表端点是**惰性写入者**（ensure_prefilled），"
          "把它撤掉后新用户的 events 直接空数组，而响应形状看不出少了六行",
      "go", API_TESTS, "TestJourneyEventsCallOrderMatchesListEvents",
      backup(B3), patch(B3, PREFILL_HEAD, PREFILL_HEAD_NOOP)),

    m(81, "未读数挪到列表之前：Python 的 list_events 是「预写⇒列表⇒count_unseen」，"
          "count 在预写之后才把刚插入的 pending 算进 unseen_count 的**反面**（sent 才算）。"
          "顺序换侧在单条响应上可能同值，所以这里钉的是调用序列",
      "go", API_TESTS, "TestJourneyEventsCallOrderMatchesListEvents",
      backup(JE), patch(JE, LIST_CALL, LIST_CALL_AFTER_COUNT)),

    m(82, "时间戳改用 pydantic 的 Z 形：这条端点没有 response_model，"
          "走的是 jsonable_encoder ⇒ datetime.isoformat() ⇒ `+00:00`；"
          "写成 Z 就是逐字节不同（批 1 的 discovery 有模型，那边才是 Z）",
      "go", API_TESTS, "TestJourneyEventsDatetimeISOHasNoZAndKeepsSixMicroDigits",
      backup(JE), patch(JE, SCHED_ISO, SCHED_ISO_Z)),

    m(83, "文本列照抄批 2 的 dict 强制：journey 用的是 parse_json，"
          "**没有** isinstance 检查——列里存 5 就回 5、存数组就回数组；"
          "换成 payloadObject 会把非对象静默变成 {}（契约里是值，不是疏忽）",
      "go", API_TESTS, "TestJourneyEventsPassTextColumnsThroughWithoutKindCheck",
      backup(JE), patch(JE, PAYLOAD_RAW, PAYLOAD_DICT)),

    m(84, "max_length 按 UTF-8 字节数而不是码点：26 个汉字码点＝39 字节，"
          "Python 判合法、按字节判就成 400（37 个 ASCII 与 40 个汉字两侧同结论，"
          "只有这个差集能打红）",
      "go", API_TESTS, "TestJourneyAckLengthCountsCodePointsNotBytes",
      backup(PB), patch(PB, ACK_LEN_RUNE, ACK_LEN_BYTE),
      patch(PB, UTF8_IMPORT, '\t_ "unicode/utf8"\n')),

    m(85, "acknowledge 丢掉归属判定：repository.get 不带 user_id 谓词，"
          "「别人的事件」只能靠 service 那句比对挡下；撤掉后这条端点变成"
          "把事件改成 seen 的跨用户写入口",
      "go", API_TESTS, "TestJourneyAckMissingAndForeignAreIdentical",
      backup(JA), patch(JA, ACK_OWNER_GATE, '\tif !owner.Found {\n')),

    m(86, "响应回显列值而不是请求里的 event_id（routers/journey.py:41 回的是入参）："
          "假件把列值故意设成另一个串，所以这一条改法必然打红",
      "go", API_TESTS, "TestJourneyAckSuccessAndOwnership",
      backup(JA), patch(JA, ACK_ECHO_REQ, ACK_ECHO_COL)),

    m(87, "注册挪出「批 3 已接线」开关：机制没接线时 Go 也答这一域，"
          "Ingress 一刀指过来就是 panic/500，而不是回落到 py-api",
      "go", API_TESTS, "TestJourneyRoutesAbsentWithoutBatch3",
      backup(SV), patch(SV, BATCH3_GUARD, '\tif true {\n')),

    m(88, "journey 灰度默认 100：忘了配键的部署直接把这一域全给 Go，"
          "「部署了但没开灰度」这一安全态就没了（与批 1/批 2 同一条口径）",
      "go", CFG_TESTS, "TestN8CutoverDefaults",
      backup(CF), patch(CF, CFG_JOURNEY,
                        '\t\tCutoverJourney:         envInt("CLOSEN_CUTOVER_JOURNEY", 100),\n')),

    m(89, "skip 不看**落库前**的 status：Python 在 schedule() 之后读的是既有行的 status"
          "（schedule 不改 status），已 sent 的行即使落在过去也必须留在 sent；"
          "写成「Past 就 skip」等于把已送达的推送记录擦成 skipped",
      "go", STORE_TESTS, "TestPrefillJourneyNeverRewritesSentRow",
      backup(LB3), patch(LB3, SKIP_GATE, SKIP_NO_STATUS)),

    m(90, "prefill 的 INSERT 走 autocommit：Python 那六组改/插在同一请求事务里，"
          "任何一步抛异常整批回滚；逐条提交会留下「前六行已写、第七行失败」的半份剧本，"
          "两侧下一轮读出的字节就不同（真库用 day_no 溢出打断第七行）",
      "go", STORE_TESTS, "TestPrefillJourneyRollsBackWholeBatch",
      backup(LB3), patch(LB3, INSERT_IN_TX, INSERT_AUTOCOMMIT)),

    # 2026-10-15 追加：push_time 的 int() 字符集。起因是实测发现入口校验
    # `re.fullmatch(r"\d{2}:\d{2}")`（backend/models/schemas.py:400）里的 \d 是
    # **Unicode 感知**的，「１２:３４」5 个字符正好落进 VARCHAR(5)（models/user.py:29）
    # ⇒ 这不是历史脏值才碰得到的分支。四支各打一条，打红的是同一个表测。
    m(91, "int() 只认 ASCII 数字（撤掉 Nd 查表）：全角/阿拉伯-印度 push_time 在 Python "
          "那边是 12:30，在 Go 这边静默落回 9:00 兜底⇒预写进库的 scheduled_at 差 3.5 小时，"
          "而响应只回日期时间，看不出是谁算错的",
      "go", DOMAIN_TESTS, "TestJourneyPushTimeParsing",
      backup(DJ), patch(DJ, ND_LOOKUP, ND_NO_ND)),

    m(92, "Nd 位值不做 %10：Go 的 unicode.Nd.R32 把五个数学数字 run 并成 "
          "1D7CE-1D7FF 一条，𝟙 的偏移是 11 而不是 1⇒「𝟙𝟚:𝟘𝟛」算成 11:3（Python 给 12:3）",
      "go", DOMAIN_TESTS, "TestJourneyPushTimeParsing",
      backup(DJ), patch(DJ, ND_MOD10, ND_RAW_OFFSET)),

    m(93, "下划线闸门撤掉（当成任意位置都合法）：CPython 只认两个数字之间的单个下划线，"
          "「1__0」在它那边是 ValueError⇒9:00；松判定会读成 10:00",
      "go", DOMAIN_TESTS, "TestJourneyPushTimeParsing",
      backup(DJ), patch(DJ, USCORE_GATE, USCORE_LOOSE)),

    m(94, "首尾只剥 ASCII 空白：CPython 的空白集与 Go 的 unicode.IsSpace 实测是同一个"
          "25 码位集合（含 NBSP/ideographic space，不含 001C-001F），退回 ASCII 表 "
          "就让「\\xa09:30」落回 9:00 而不是 9:30",
      "go", DOMAIN_TESTS, "TestJourneyPushTimeParsing",
      backup(DJ), patch(DJ, SPACE_TRIM, SPACE_ASCII)),

    # ── 批 3②：墙钟→瞬间的四支（push_moment_corpus 736 条才第一次打得到的那面）──
    # 老写法是「先信 time.Date 的 guess，只有墙钟不可逆才重算」，而 Go 原生在
    # **重复墙钟**上按区给相反答案：纽约给切换前（与 Python 同），伦敦/悉尼/豪勋爵岛
    # 给切换后（与 Python 差 30–60 分钟）。批 2 的 week_window 语料只撞得上缺位，
    # 所以那支错误实现整批绿；下面四条就是把「重复」这一支钉住。
    m(95, "重复墙钟取**第二次**出现（`at < earliest` 换成 `>`）：伦敦 2024-10-27 01:30 "
          "从 00:30Z 变 01:30Z，悉尼 2024-04-07、豪勋爵岛 2026-04-05 同步差 30–60 分钟；"
          "Python 的 fold=0 恒取第一次",
      "go", API_TESTS, "TestN8PushMomentCorpus",
      backup(DC), patch(DC, WALL_EARLIEST, WALL_LATEST)),

    m(96, "撤掉自洽回代检验（`check != o` 松成永假）：候选集合里会混进「该瞬间的本地墙钟"
          "其实不是 W」的偏移，取到的最早瞬间落在另一小时；缺位/重复两支都失去判据",
      "go", API_TESTS, "TestN8PushMomentCorpus",
      backup(DC), patch(DC, WALL_CONSISTENCY, WALL_CONSISTENCY_OFF)),

    m(97, "缺位分支不再取两个邻域探测里的较小偏移：o1＝把墙钟当 unix 瞬间查到的偏移，"
          "在拨快之后就是**切换后**偏移 ⇒ Tehran 2016-03-21 00:30、Santiago 2026-09-06 "
          "00:30、Apia 2011-12-30 各差一小时或一整天（批 3① 修掉的那条真实缺陷）",
      "go", DOMAIN_TESTS, "TestWallClockToUTC|TestCivilDayStartUTC|TestPushMomentOnDST",
      backup(DC), patch(DC, WALL_GAP_MIN, WALL_GAP_MIN_OFF)),

    m(98, "采样窗口收缩到 W 附近一小时：切换前的偏移落在窗外就采不到，重复墙钟只剩"
          "「切换后」那一个候选 ⇒ 与伦敦/悉尼那几条同形；这一条证的是 ±16h 那个"
          "「覆盖 tzdata 全部合法偏移」的界不是随手写的数",
      "go", API_TESTS, "TestN8PushMomentCorpus",
      backup(DC), patch(DC, WALL_SCAN, WALL_NARROW_SCAN)),

    # ── 批 3④：坏 Go 源 ⇒ 红的是 Python 侧的跨语言登记闸（backend/tests/
    #    test_n8_batch3_contract.py）。这一批的口径大半在「两侧字面量/谓词是否还同源」，
    #    Go 的回放闸看不见（它只比字节），所以必须有一条能让**改 Go 的人本地立刻红**的闸。──
    m(99, "Go 的 ScriptEvents 把 D3/D5 两行调换顺序：Python 按 SCRIPT_EVENTS 的顺序在同一"
          "事务里写六行，行序就是响应数组的字节序；顺序抄混逐字节回放会红，但「常量表本身"
          "还与不 Python 同字面量」这件事只有这一条闸看得见",
      "pytest", B3_PYTEST + "::test_script_events_table_matches_go_row_for_row", "",
      backup(DJ), patch(DJ, SCRIPT_ORDER, SCRIPT_SWAPPED)),

    m(100, "JourneyListLimit 从 50 抬到 200（仓库默认值那一批 Python 常量最容易「顺手统一」）："
           "limit=50 在 Python 侧有两处——journey_repository.list_for_user 的默认值与 "
           "journey_service.py:888 的显式实参；只改 Go 就与其中一处分家，第 51 行起两侧不同",
      "pytest", B3_PYTEST + "::test_list_limit_and_push_fallback_literals_match_go", "",
      backup(DJ), patch(DJ, LIST_LIMIT_50, LIST_LIMIT_200)),

    m(101, "兜底推送时刻从 9:00 改成 8:00：`user.push_time or \"09:00\"` 与 "
           "`except …: hour, minute = 9, 0` 两条分支在 Go 合成这一对常量，改一个数就让"
           "所有没设偏好的用户整条剧本平移一小时",
      "pytest", B3_PYTEST + "::test_list_limit_and_push_fallback_literals_match_go", "",
      backup(DJ), patch(DJ, PUSH_HOUR_9, PUSH_HOUR_8)),

    m(102, "越权文案丢掉后半句英文（journey_service.py:911 是双语字面量）："
           "404 体逐字节不同，而这条端点的「越权与不存在合成同一条」正是防 id 枚举的那格",
      "pytest", B3_PYTEST + "::test_ack_contract_literals_match_go", "",
      backup(JA), patch(JA, ACK_MSG_FULL, ACK_MSG_ZH_ONLY)),

    m(103, "event_id 的长度上限从 36 抬到 64：schemas.py:534 的 max_length=36 是 422/400 的"
           "判据来源，Go 放宽后 37 字符的入参在两侧一个回 400 一个回 40402",
      "pytest", B3_PYTEST + "::test_ack_contract_literals_match_go", "",
      backup(PB), patch(PB, ACK_ID_36, ACK_ID_64)),

    m(104, "未读数的 `acknowledged IS FALSE` 写成 `= false`：SQLAlchemy 的 `.is_(False)` "
           "与前者同形，后者在 NULL 行上不给 true；acknowledged 可空，这是谓词不是风格",
      "pytest", B3_PYTEST + "::test_count_unseen_predicate_and_call_order_match_go", "",
      backup(LB3), patch(LB3, UNSEEN_IS_FALSE, UNSEEN_EQ_FALSE)),

    m(105, "acknowledge 的 UPDATE 加进 user_id 谓词：归属判定在 service（"
           "journey_service.py:910），写进 SQL 会让「越权」从 40402 变成 0 行受影响⇒200，"
           "错误形状就换了；这条也是「SQL 里没有 user_id」这一形状的闸",
      "pytest", B3_PYTEST + "::test_ack_update_sets_two_columns_without_owner_predicate", "",
      backup(LB3), patch(LB3, ACK_SQL_OWNER, ACK_SQL_OWNER_ADD)),

    m(106, "惰性写入者的沉默升成 40401：`if !user.Found` 在 Python 那边是 "
           "`if user is None: return`（get_user 过滤 status='active'），静默返回后照样列行"
           "⇒注销用户回 200。改成报错看着更合理，但灰度期同一账号两侧状态码不同",
      "pytest", B3_PYTEST + "::test_lazy_writer_silence_is_not_promoted_to_an_error", "",
      backup(B3), patch(B3, SILENT_USER_RETURN, SILENT_USER_40401)),

    m(107, "scheduled_at 改用批 1 的 pyISO（\"Z\"）：journey 的路由没有 response_model，"
           "FastAPI 走 jsonable_encoder⇒isoformat⇒\"+00:00\"；两处格式不同是两条序列化路径，"
           "「统一成一种」就是改契约",
      "pytest", B3_PYTEST + "::test_journey_datetimes_serialize_with_offset_not_z", "",
      backup(JE), patch(JE, SCHED_ISO, SCHED_ISO_Z)),

    m(108, "uuid 掩码正则的最后一段从 12 收成 11：末段没被掩掉的预写行走的是另一条出口"
           "（Go 少掩 ⇒ 常红，多掩 ⇒ 把真发散掩成绿），两侧的正则必须是同一条字面量",
      "pytest", B3_PYTEST + "::test_event_id_mask_definition_is_the_same_on_both_sides", "",
      backup(RP), patch(RP, MASK_EVENT_RE, MASK_EVENT_RE_TIGHT)),

    m(109, "转交判定的域名写成 \"journeys\"：编译通过、测试里假件也不看这个串（它是 cutover "
           "登记表的键），后果是这一域永远不在桶里⇒永远只多一跳代理，放量那次才发现",
      "pytest", B3_PYTEST + "::test_go_registers_both_journey_routes_under_the_batch3_switch", "",
      backup(JE, JA), patch(JE, DELEGATE_DOMAIN, DELEGATE_DOMAIN_TYPO),
      patch(JA, DELEGATE_DOMAIN, DELEGATE_DOMAIN_TYPO)),

    m(110, "ConfigMap 的 journey 灰度键从 0 抬到 100：清单是运维侧唯一说「这一批只接不放量」"
           "的地方，改它是一次上线动作，必须被闸拦住（与批 1/批 2 的 k8s 闸同一条口径）",
      "pytest", B3_PYTEST + "::test_cutover_journey_switch_exists_in_go_and_manifest_not_python",
      "", backup(CFG), patch(CFG, CM_JOURNEY_ZERO, CM_JOURNEY_FULL)),

    # 夹具面与录制面：坏的不是 Go 实现，而是「语料/登记」本身。
    m(111, "夹具里 journey 组的组名漂成 journey p（46 处一起改）：回放闸按组取用例，"
           "组名一漂，Python 侧的「46 条 + 惰性写入者各分支点名」就看见空组——"
           "这一条证的是覆盖面闸不是只在数自己写的数",
      "pytest", B3_PYTEST + "::test_journey_group_cases_cover_the_lazy_writer_branches", "",
      backup(FX), patch_all(FX, FX_GROUP_JOURNEY, FX_GROUP_TYPO)),

    m(112, "录制侧把时钟钉到**错的模块**（time_utils 而不是 journey_service 的模块全局）："
           "prefill 里的 `local_today(...)` 是晚绑定，钉 time_utils 一点作用都没有，"
           "录出来的 scheduled_at 属于录制那一刻；这一条证的是 test_recorder_freezes… 的锚点"
           "必须按行首匹配（注释掉的那行也不算冻钟）",
      "pytest", B3_PYTEST + "::test_recorder_freezes_the_journey_clock", "",
      backup(GS), patch(GS, GS_FREEZE_JOURNEY_TODAY, GS_FREEZE_WRONG_MODULE)),

    m(113, "语料键名漂移（push_moment_corpus ⇒ push_moment_corpusz）：Go 的结构体标签与"
           "Python 的 `fixture[...]` 都按这个键取，键一漂两侧就在**空语料**上空跑；"
           "与 #41 同一条口径，只是换到批 3 新加的这支日历核",
      "pytest", B3_PYTEST + "::test_push_moment_corpus_does_not_degenerate", "",
      backup(FX), patch(FX, FX_CORPUS_KEY, FX_CORPUS_KEY_TYPO)),

    m(114, "第④步少标一条：GET /journey/events 的 deprecated 被摘掉。客户端在 openapi 里"
           "看到的路径归属与 Ingress/ConfigMap 说的不再一致，放量那次会以为自己在切一条"
           "其实还全量由 Python 答的线（批 2 的 #71/#72 证过同一条机制，这里补 journey）",
      "pytest", B3_PYTEST + "::test_both_journey_routes_deprecated_yet_still_routable", "",
      backup(RJ), patch(RJ, RJ_GET_DEPRECATED, RJ_GET_LIVE)),

    m(115, "prefill 的 UPDATE 那一支挪出事务（改完计划时刻就 autocommit）：Go 侧「事务内"
           "四条语句」的形状是原子性的唯一可见证据，真库回滚测（#90）看的是插入支，"
           "这一条补上命中既有行那一支",
      "pytest", B3_PYTEST + "::test_prefill_is_one_transaction_on_both_sides", "",
      backup(LB3), patch(LB3, UPDATE_IN_TX, UPDATE_AUTOCOMMIT)),

    m(116, "播种面把全角 push_time 写成 ASCII（列值形状没了）：`１２:３４` 是这一批唯一"
           "让「CPython 的 int() 认 Nd」走到**对外字节**那一层的输入（events-push-fullwidth "
           "那条用例），摘掉它，Nd 支只剩 domain 表测，HTTP 面上两侧一致与否无人可证",
      "pytest", B3_PYTEST + "::test_seed_face_has_the_journey_row_shapes", "",
      backup(FX), patch(FX, FX_SEED_FULLWIDTH, FX_SEED_FULLWIDTH_ASCII)),

    # ── 批 4（onboarding）：三块「核」语料 + 两条掩码的新语义 + 两条字节出口 ──────
    m(117, "把 ß⇒SS 那条展开从 pyUpperExpand 里删掉：Go 的 strings.ToUpper 对 ß 恒等，"
           "于是「Python 复刻的是 CPython 而不是 ASCII」这张表少了一个成员——"
           "闭包闸按「夹具 ascii4 ∪ A-Z」与 Go 自扫 BMP 相等，少一个就大小不符",
      "go", API_TESTS, "TestN8PersonaUpperCorpusIsClosed",
      backup(PT), patch(PT, PT_UPPER_SHARP, "")),

    m(118, "同一处删 ß 的坏实现，换打 **HTTP 字节面**：onb-submit-persona-sharp-s 那条 "
           "200（「ßß」⇒ 列里存「SSSS」）会掉成 400。#117 证的是函数闭包，这一条证的才是"
           "「客户端真会看见的那个形状」有人守着",
      "go", API_TESTS, "TestN8OnboardingContractBytes",
      backup(PT), patch(PT, PT_UPPER_SHARP, "")),

    m(119, "措辞闸只看越界句式、不看黑名单（`bannedHit(...) || attributionHit(...)` 少一半）："
           "闭包那 92 条本来一条不被闸掉，删掉整支也全绿——正是批 4 补探针面要抓的那一格："
           "hit-banned 两条（zh/en）当场放行、方向不符 ⇒ 红",
      "go", API_TESTS, "TestN8TemplateCorpus",
      backup(FG), patch(FG, FG_BANNED_AND_ATTR, FG_ATTR_ONLY)),

    m(120, "越界判据把「third 必需」写成「label 或 causal 就行」：Python 的注释"
           "（safety_service.py:341-346）写明「标签∧因果但没有第三方」刻意不算越界，"
           "抄成 or 就会把 safe-no-third 两条吞掉，那张卡少一句而响应形状照样合法",
      "go", API_TESTS, "TestN8TemplateCorpus",
      backup(FG), patch(FG, FG_THIRD_REQUIRED, FG_THIRD_IGNORED)),

    m(121, "en 黑名单的 `\\b{term}` 补上后边界（写成 `\\b{term}\\b` 是「顺手统一」的经典）："
           "Python 那条 rf-string 只有前边界，补上后「healing」就不再命中「heal」。"
           "hit-boundary-post（`depressioning`）当场变放行 ⇒ 红",
      "go", API_TESTS, "TestN8TemplateCorpus",
      backup(FG), patch(FG, FG_BANNED_LEADING_ONLY, FG_BANNED_BOTH_EDGES)),

    m(122, "RE2 不支持后视，Go 按定义手写了 `(?<![其这那每])他`；把那段手写换成"
           "「只编译尾串」＝后视断言被丢掉：safe-third-lookbehind（「和其他焦虑型有关」）"
           "会被判成越界。这一支只有探针面能证——闭包那 92 条模板文案走不到这一格",
      "go", API_TESTS, "TestN8TemplateCorpus",
      backup(FG), patch(FG, FG_LOOKBEHIND, FG_LOOKBEHIND_DROPPED)),

    m(123, "深链把 `rstrip('/')` 抄成 `TrimSuffix`（剥一个而不是剥一串）：site_url 配成 "
           "多一个尾斜杠时两侧给出两条链接，而那个串会落进 first_read 列并在 restore 复现。"
           "深链语料 11 个变体里真的有 `//` 结尾的那一个，当场不符 ⇒ 红",
      "go", API_TESTS, "TestN8ReferralLinkCorpus",
      backup(B4), patch(B4, B4_LINK_TRIM_RIGHT, B4_LINK_TRIM_SUFFIX)),

    m(124, "三条新掩码的「按夹具计数应用」改成无条件应用：夹具记 0 的语义是"
           "「这个值是确定值，刻意留原文逐字比」（种子已带码 ⇒ /r/QQ22WW33 全串、"
           "种子行 ⇒ created_at 的绝对时刻）。无条件掩就把这 8 条证据掩成占位符，"
           "与录制体逐字不符 ⇒ 红。这一条证的是本批把「掩码次数」写进判据之后，"
           "计数本身也有观测点",
      "go", API_TESTS, "TestN8OnboardingContractBytes",
      backup(RP), patch(RP, RP_MASK_BY_COUNT, RP_MASK_ALWAYS)),

    m(125, "把 answers 列表项不是对象那一条退回「按形状猜」的 model_type＋class_name："
           "真 FastAPI 给的是 model_attributes_type、无 ctx（批 1 的 events 同一条）。"
           "这一条不是假想变异——2026-10-15 第一版就是这么写的，被 onb-answer-item-not-dict "
           "的逐字节比对打红之后才按实测改正",
      "go", API_TESTS, "TestN8ValidationContractBytes",
      backup(OB), patch(OB, OB_ITEM_NOT_DICT, OB_ITEM_MODEL_TYPE)),

    m(126, "restore 的 created_at 从列取改成从钟算（`profile.CreatedAt` ⇒ `s.now()`）："
           "两侧都把时钟冻在 recorded_at，若种子行的 created_at 也种成同一个时刻，这一支"
           "会被巧合蒙过——所以录制侧把 profile 的默认 created_at 钉在 ago(4)。"
           "onb-restore-seeded-full 等 8 条 created_at_masks=0 的用例当场差一串字节 ⇒ 红",
      "go", API_TESTS, "TestN8OnboardingContractBytes",
      backup(OR), patch(OR, OR_CREATED_FROM_COLUMN, OR_CREATED_FROM_CLOCK)),

    # ── 批 4④：23 条 Python 闸（test_n8_batch4_contract.py），每条点一个受害测试 ──────
    m(127, "只改 Go 内嵌模板的一个标点（「——」改成「，」）：go:embed 的副本与 backend/data "
           "是两份文件，Python 的 sha256 闸看不见 Go 那份，Go 的回放也看不见——它只比字节，"
           "两侧各自生成的卡都「合法」。文案分叉只能在这条闸上现形",
      "pytest", B4_PYTEST + "::test_both_embedded_templates_are_byte_identical_to_python_data",
      "", backup(TPL_ZH), patch(TPL_ZH, TPL_ZH_CLOSER, TPL_ZH_CLOSER_FORK)),

    m(128, "Go 的模板表把键名漂成 eng：这条闸验的是「恰好两格、键就是 zh/en」。"
           "多一格＝往闸里塞了一份没过措辞审校的文案，而 LocaleFor 永远查不到它 ⇒ "
           "非中文用户静默拿到零模板。表在 Go 侧，红在 Python 侧",
      "pytest", B4_PYTEST + "::test_templates_locales_are_exactly_two_and_default_en",
      "", backup(FR), patch(FR, GEN_TPL_EN, GEN_TPL_EN_TYPO)),

    m(129, "Go 黑名单删一条（zh 的「抑郁症」）：两张表逐条同序比对，条目顺序还会进日志"
           "（命中哪一条是运维面）。少一条不改响应形状——模板文案本来一条都不命中——"
           "所以只有表本身可比",
      "pytest", B4_PYTEST + "::test_banned_terms_match_python_table_entry_by_entry_in_order",
      "", backup(FG), patch(FG, FG_BANNED_ZH_ITEM, '"焦虑症",')),

    m(130, "Go 的 causal 组删一条（zh 的「导致」）：越界判据是 (third∧label)∨(third∧causal)，"
           "少一条指纹＝一类因果句从此放行。三组×两 locale 按序逐条比，"
           "探针面另有 hit-causal-third 那一格",
      "pytest", B4_PYTEST + "::test_attribution_markers_match_python_table_entry_by_entry_in_order",
      "", backup(FG), patch(FG, FG_CAUSAL_ZH_ITEM, '"causal": {')),

    m(131, "en 黑名单丢掉前边界：Python 那支是「词边界＋QuoteMeta」，Go 用 (?i) 拼出来；"
           "去掉边界就是把 zh 的纯子串匹配搬到 en 上，「heal」会从「healing」里命中。"
           "判据形状的字面量闸不是装饰，探针面的 hit-boundary-post 就是它的观测点",
      "pytest", B4_PYTEST + "::test_gate_semantics_are_the_replicated_ones_not_a_rewrite",
      "", backup(FG), patch(FG, FG_COMPILE_LEADING, FG_COMPILE_NO_EDGE)),

    m(132, "把 hit-banned 两条探针的 kind 翻成 safe-*：探针面若只剩一个方向，"
           "「删掉整条判据也全绿」就会回来（批 4 实测过：闭包 92 条一条都不被闸掉）。"
           "这一条证的是 kind 词表本身就是判据，不是给人看的注释",
      "pytest", B4_PYTEST + "::test_corpus_probes_are_two_sided_not_closure_only",
      "", backup(FX), patch_all(FX, FX_PROBE_HIT_BANNED, FX_PROBE_HIT_BANNED_FLIP)),

    m(133, "夹具的可达性计数被改掉（reachable 的 56 条全翻成 false）：这一条是"
           "「夹具 == Python」那半段的接缝——Go 比的是「Go == 夹具」，输入是同一份 JSON。"
           "夹具对 Python 的可达性一旦过期（改了 Python 侧没重录）必须在这里红，"
           "而不是等回放出现代差",
      "pytest", B4_PYTEST + "::test_template_corpus_is_still_python_answered", "",
      backup(FX), patch_all(FX, FX_REACHABLE_TRUE, '"reachable": false')),

    m(134, "把 SafetyLangKey 并进 lower 那一支（加一行 ToLower）：两支函数**答案不同**"
           "（ZH 在 locale_for 落 zh、在 _lang_key 落 en）。合并看着是清理，实际同时改掉"
           "了 safety 其它 scope 的语言判定，而 Go 的回放只覆盖本域那一条路",
      "pytest", B4_PYTEST + "::test_locale_normalization_stays_two_functions_with_two_answers",
      "", backup(FG), patch(FG, GATE_LANGKEY_HEAD, GATE_LANGKEY_FUSED)),

    m(135, "断言条数下限从 2 抬到 3（PRD §5.19.2 B01 是 2–3）：MIN 决定补几条 filler、"
           "MAX 决定截到几条，两个数各自进字节面。抄成一个数，就是那张卡少一句话"
           "而形状照样合法",
      "pytest", B4_PYTEST + "::test_assertion_count_bounds_shared_with_go", "",
      backup(FR), patch(FR, GEN_MIN_BOUND, '\tFirstReadMinAssertions = 3\n')),

    m(136, "夹具 upper 闭包里 ßß 那条写成 SSss：本条是把 BMP 全码位重扫一遍后逐条相等。"
           "「多码位展开」在 persona_type 的校验（四张大写字母）上是响应面问题，"
           "手挑几条永远说不出「Python 复刻的是 CPython 的 upper，不是 ASCII 的」",
      "pytest", B4_PYTEST + "::test_persona_upper_corpus_is_a_python_closure", "",
      backup(FX), patch(FX, FX_PERSONA_SHARP, FX_PERSONA_SHARP_BAD)),

    m(137, "深链的 rstrip 写成 TrimSuffix（同一个坏法，这次打 Python 侧）：Go 的语料测看"
           "拼装结果，这条闸看的是**源码形状**——剥单个还是剥一串只有在 site_url 配成"
           "多尾斜杠时才现形，而那个串要落进 first_read 列、restore 按列复现不回查设置",
      "pytest", B4_PYTEST + "::test_referral_link_and_alphabet_shared_by_both_sides", "",
      backup(B4), patch(B4, B4_LINK_TRIM_RIGHT, B4_LINK_TRIM_SUFFIX)),

    m(138, "九键里 summary 与 closer 换序：POST 直接把这份 dict 当 data 回，键序逐字进"
           "响应体；同一份内容还以默认分隔符落进 first_read 列。pydantic 与 Go 的 "
           "encoding/json 都不看序，所以只有「按出现序取 set」这条闸看得见",
      "pytest", B4_PYTEST + "::test_card_payload_key_order_is_the_literal_order_on_both_sides",
      "", backup(OS), patch(OS, SUB_KEYS_NINE, SUB_KEYS_NINE_SWAP)),

    m(139, "restore 少写 id 那一键：GET 的 payload.update 里 id 是**追加在九键之后**的"
           "第十键（已有键就地换值、位置不动），出参形状取决于列里那份 card_payload 有哪些键"
           "——整域唯一会改 JSON 形状的地方，少一键就是十一键变十键",
      "pytest", B4_PYTEST + "::test_restore_payload_keys_overwrite_in_place_and_append_the_missing",
      "", backup(OR), patch(OR, OR_RESTORE_ID, "")),

    m(140, "answers 列每项的两键换序（option 写在 q 前）：落库文本是**重建**的"
           "（多余键已被 extra=ignore 丢掉），模型声明序就是字节序。"
           "第一版 Go 就是按 JSON 习惯写成别的序，被这条闸拦下后才按声明序改正",
      "pytest", B4_PYTEST + "::test_answers_column_two_keys_in_declaration_order", "",
      backup(OS), patch(OS, SUB_ANSWER_KEYS, SUB_ANSWER_KEYS_SWAP)),

    m(141, "推荐码的生成条件从「有账号且列为空」松成「列为空」：三处写的条件不同源"
           "（码有守卫、profile 无条件 upsert、剧本另有守卫）。账号不存在或已注销时 "
           "Python 照样写 profile、但**不**生成码；合并守卫＝给无账号用户发码，"
           "而那条全表唯一约束在夹具里没有观测点",
      "pytest", B4_PYTEST + "::test_three_writes_keep_three_different_conditions", "",
      backup(LS), patch(LS, LS_CODE_GUARD, LS_CODE_GUARD_FUSED)),

    m(142, "generated_at 走 pyISO（Z 形）而不是 pyISOOffset：这一键不过 pydantic 的 "
           "datetime，是 service 自己 isoformat 折好的**字符串**，格式由 isoformat 决定"
           "（批 3 同判据）。看着都是 UTC 时刻，字节不同，而它同时进响应与列文本",
      "pytest", B4_PYTEST + "::test_generated_at_is_isoformat_with_offset_and_id_is_appended",
      "", backup(OS), patch(OS, SUB_GENERATED_OFFSET, SUB_GENERATED_Z)),

    m(143, "answers 的道上限从 10 收成 9：同一份请求在一侧 422、另一侧 200。"
           "四道上限（10/30/40/10）逐字比的就是这个——校验层不在响应字节里，"
           "「回放照绿」看不见它",
      "pytest", B4_PYTEST + "::test_body_limits_come_from_the_same_field_params", "",
      backup(OB), patch(OB, OB_ANSWER_LIMIT, '\tmaxFirstReadAnswers = 9\n')),

    m(144, "Go 的推荐码掩码字符集从 2-7 收成 2-6：四条掩码两侧必须是**同一条**正则。"
           "Go 少掩就常红、多掩就把真发散掩成绿；字符集写成码表的超集是刻意的"
           "（宁多认不漏认，多掩由 code_masks 计数暴露成红）",
      "pytest", B4_PYTEST + "::test_four_masks_are_the_same_four_regexes_on_both_sides",
      "", backup(RP), patch(RP, RP_REFERRAL_CHARS, RP_REFERRAL_CHARS_TIGHT)),

    m(145, "ConfigMap 把 onboarding 灰度键从 0 抬到 100：清单是运维侧唯一写"
           "「这一批只接不放量」的地方。0% 的可证形态是四格同时成立（Ingress 一刀＋"
           "键为 0＋Go 注册了这个域名＋py-api 地址非空），改任一格都让「灰度」变成换后端",
      "pytest", B4_PYTEST + "::test_cutover_wiring_agrees_on_all_four_planes", "",
      backup(CFG), patch(CFG, CM_ONBOARDING_ZERO, CM_ONBOARDING_FULL)),

    m(146, "夹具 onboarding 组的组名漂一个字母（28 处一起改）：回放闸按组取用例，"
           "组名一漂 Python 侧就看见空组——这一条证的是覆盖面闸不是只在数自己写的数"
           "（GET 17/POST 11 的写面观测点分布、三条计数掩键的存在都挂在这张表上）",
      "pytest",
      B4_PYTEST + "::test_fixture_onboarding_group_covers_both_paths_exactly_once_each_writing_side",
      "", backup(FX), patch_all(FX, FX_GROUP_ONBOARDING, FX_GROUP_ONBOARDING_TYPO)),

    m(147, "在 restore 的代码面留一个 ai_consent 的名字（「顺手加一道 consent 闸」的形状）："
           "初读卡是静态直出、零 LLM，两侧都不该检查它。判据只看代码面（Go 剔整行注释、"
           "Python 剔 docstring），所以在注释里声明「不检查 consent」不算证据",
      "pytest", B4_PYTEST + "::test_this_domain_has_no_ai_consent_check_on_either_side", "",
      backup(OR), patch(OR, OR_RESTORE_ID, OR_CONSENT_PROBE)),

    m(148, "第④步少标一条：GET 的 deprecated 被摘掉。客户端在 openapi 里看到的路径归属"
           "与 Ingress/ConfigMap 说的不再一致——放量那次会以为自己在切一条其实仍全量由 "
           "Python 答的线（路由本体必须留着，回滚＝把键改回 0）",
      "pytest", B4_PYTEST + "::test_both_onboarding_routes_deprecated_yet_still_routable",
      "", backup(RO), patch(RO, RO_GET_DEPRECATED, RO_GET_LIVE)),

    m(149, "已切流域的全量登记表漏掉 onboarding：三方对齐闸（Ingress 域集 ≡ ConfigMap 键集 "
           "≡ openapi deprecated 域集）少一格就永远判不出「清单漏了一刀」，"
           "而每条单域闸各自都还是绿的",
      "pytest", B4_PYTEST + "::test_batch4_registered_in_the_domain_roll_call", "",
      backup(B2PY), patch(B2PY, B2_CUT_ROLLCALL, B2_CUT_ROLLCALL_DROP)),

    # ── 批 5④（mood + reflections 三条纯读，2026-10-15）───────────────────────
    m(150, "history 的 limit 从 30 改成 29：这一个数写在 service 层而不是端点上，"
           "少一行就是响应数组少一格（夹具 mood-t-sh 有 30 行以上才打得出来）",
      "go", API_TESTS, "TestN8MoodContractBytes",
      backup(DMOOD), patch(DMOOD, B5_HISTORY_LIMIT, 'const MoodHistoryLimit = 29\n')),

    m(151, "四档判据把 `avg >= 3` 写成 `avg > 3`：夹具 trend-exactly3 特意种成 [2,5]"
           "让均值**恰好**等于 3，就是为了让这条边界有观测点",
      "go", API_TESTS, "TestN8MoodContractBytes",
      backup(DMOOD), patch(DMOOD, B5_AVG_MID, '\tcase avg > 3:\n')),

    m(152, "同一本地日的「取首个」撤掉：升序进、后行覆盖前行 ⇒ 同一天两行时取到最后一次"
           "签到，而 Python 是 day_map 的 `if key not in day_map`（trend-sameday 那格）",
      "go", API_TESTS, "TestN8MoodContractBytes",
      backup(DMOOD), patch(DMOOD, B5_SAMEDAY, "")),

    m(153, "日历日标签从 `%m-%d` 改成完整日期：labels 是响应字节，Python 侧写的是 "
           "day.strftime(\"%m-%d\")，跨年撞键那条推论也挂在这个形状上",
      "go", API_TESTS, "TestN8MoodContractBytes",
      backup(DMOOD), patch(DMOOD, B5_LABEL_FMT, '\t\tlabel := local.Format("2006-01-02")\n')),

    m(154, "trend 的窗口查询从升序改成倒序：SQL 的 ORDER BY 就是「当日取首个」取哪一行的"
           "判据，逐字节回放只看行序变化后的那一格",
      "go", API_TESTS, "TestN8MoodContractBytes",
      backup(LB5), patch(LB5, B5_TREND_ASC, '\tORDER BY created_at DESC`')),

    m(155, "history 的 ORDER BY 从倒序改成正序：checkins 数组的字节序跟着翻"
           "（get_recent 的 .desc() 是本端点唯一的排序面）",
      "go", API_TESTS, "TestN8MoodContractBytes",
      backup(LB5), patch(LB5, B5_HIST_DESC, '\tORDER BY created_at ASC\n\tLIMIT $2')),

    m(156, "时区那一读撤掉 `status='active'`：Python 的 get_user 对 deleted 账号给 None"
           "⇒ 趋势按 UTC 折算（不是 40401），撤掉过滤就改成读那个账号自己的时区"
           "（夹具 trend-deleted-3 钉的就是这一格）",
      "go", API_TESTS, "TestN8MoodContractBytes",
      backup(LB5), patch(LB5, B5_ACTIVE_WHERE, "\tWHERE id = $1`")),

    m(157, "reflections 的 record_type 从精确等值改成 LOWER() 两边归一：夹具种了一条 "
           "`Reflection`（首字母大写）专门用来打红这种「更宽松的实现」",
      "go", API_TESTS, "TestN8ReflectionContractBytes",
      backup(LB5), patch(LB5, B5_TYPE_WHERE,
                         '\tWHERE user_id = $1 AND LOWER(record_type) = LOWER($2)\n')),

    m(158, "related_memory_ids 的 falsy 判据撤掉：空串列本来给 null，"
           "撤掉后 split(\"\") 给 [\"\"]（夹具 reflections-nulls 的五格同时测 NULL、空串、"
           "\",\"、\"a,,b\"、\"only\"）",
      "go", API_TESTS, "TestN8ReflectionContractBytes",
      backup(DMOOD), patch(DMOOD, B5_FALSY, '\tif false && raw == "" {\n\t\treturn nil\n\t}')),

    m(159, "文案取值跳过目标语言直接取 en：非英文用户的 summary 逐字变样，"
           "而 /mood/trend 的 summary 是这条端点唯一的文案面",
      "go", API_TESTS, "TestN8MoodContractBytes",
      backup(DMOOD), patch(DMOOD, B5_COPY_LOCALE, '\tif v := moodCopy["en"][key]; v != "" {')),

    m(160, "days 的上界从 90 收成 89：`?days=90` 在一侧 200、另一侧 400，"
           "三件套（默认/下界/上界）都在 validation 组的 400 体上有观测点",
      "go", API_TESTS, "TestN8ValidationContractBytes",
      backup(DMOOD), patch(DMOOD, B5_DAYS_MAX, '\tMoodTrendMaxDays     = 89\n')),

    m(161, "history 的 created_at 走 pyISOOffset（+00:00）而不是 pyISO（Z）："
           "这条路由的 data 是 pydantic 模型，走 pydantic-core 的 json mode，"
           "分界不在「哪一批」而在「有没有模型」——两边看着都是 UTC 时刻，字节不同",
      "go", API_TESTS, "TestN8MoodContractBytes",
      backup(MHIST), patch(MHIST, B5_HIST_PYISO,
                           '\t\t\tCreatedAt:        pyISOOffset(row.CreatedAt),\n')),

    m(162, "坏 Go 侧的分值表（😊 从 5 改成 4）：Python 的现算与 Go 的常量分家，"
           "而逐字节回放两侧一起漂就全绿——这条只有跨语言闸看得见",
      "pytest", B5_PYTEST + "::test_mood_levels_table_is_one_table_on_both_sides", "",
      backup(DMOOD), patch(DMOOD, B5_LEVEL_TABLE,
                           'var MoodLevels = map[string]int{\n\t"\U0001f60a": 4,\n')),

    m(163, "坏 Go 侧的文案语言表顺序（把 en 提到 zh 前）：locale_for 做的是**前缀**匹配，"
           "顺序就是判据；两张表（supported_languages / SUPPORTED_LOCALES）一旦被人"
           "合成同一张，zh_CN 那一格的分叉就消失而字节仍可能全绿",
      "pytest", B5_PYTEST + "::test_two_language_branches_stay_two_functions", "",
      backup(DMOOD), patch(DMOOD, B5_LOCALE_ORDER,
                           'var CopySupportedLocales = []string{\n\t"en", "zh", "es", "ko", "ja", "ru",\n')),

    m(164, "内嵌文案副本坏一格（zh/trend.high 末尾加一个字）：60 格闭包比的是"
           "「源文件 == 副本 == 现算」，只比被 HTTP 用例命中的那一格会让其余格静默分叉",
      "pytest", B5_PYTEST + "::test_mood_copy_corpus_is_a_sixty_cell_closure_on_three_sides",
      "", backup(CMOOD), patch(CMOOD, B5_ZH_HIGH, B5_ZH_HIGH[:-1] + "」" + '"')),

    m(165, "坏**源文件**同一格（backend/data/copy/zh.json 的 trend.high）：这次动的不是副本"
           "而是原件，Python 现算先变，副本与夹具仍是旧值——两头各红一次才算闭包",
      "pytest", B5_PYTEST + "::test_mood_copy_corpus_is_a_sixty_cell_closure_on_three_sides",
      "", backup(PYZH), patch(PYZH, B5_ZH_HIGH, B5_ZH_HIGH[:-1] + "」" + '"')),

    m(166, "ConfigMap 把 mood 灰度键从 0 抬到 100：清单是运维侧唯一写「这一批只接不放量」"
           "的地方，而放量是两次独立动作（改数字 ≠ 发版）",
      "pytest", B5_PYTEST + "::test_cutover_keys_exist_only_in_go_and_manifests_and_both_are_zero",
      "", backup(CFG), patch(CFG, CM_MOOD_ZERO, '  CLOSEN_CUTOVER_MOOD: "100"\n')),

    m(167, "给 /api/v1/reflections 补一条 Exact 指 go-api（把 D-10 撤掉）："
           "Ingress 的 path 匹配不看 HTTP 方法，这一个 path 上挂着 GET 与 POST，"
           "而 Go 的 mux 只注册了 GET ⇒ 上线即 405。清单闸按 (方法,路径) 模拟路由，"
           "这一刀必须当场判出来",
      "pytest", f"{K8S_PYTEST}::test_go_bound_contract_operations_are_registered_in_go", "",
      backup(ING), patch(ING, ING_MOOD_TREND, ING_MOOD_TREND + (
          '          - path: /api/v1/reflections\n'
          '            pathType: Exact\n'
          '            backend:\n'
          '              service:\n'
          '                name: go-api\n'
          '                port:\n'
          '                  name: http\n'))),

    m(168, "第④步少标一条（GET /mood/history 的 deprecated 被摘）：path 级登记闸看得见，"
           "但**方法级**的「POST /mood/checkin 不许跟着一起标」只有这一条看得见——"
           "同 path 多方法是批 5 之后的常态",
      "pytest", B5_PYTEST + "::test_only_the_two_cut_gets_are_deprecated", "",
      backup(RM), patch(RM, RO_HISTORY_DEPRECATED, '@router.get("/mood/history")\n')),

    m(169, "夹具 mood 组的组名漂一个字母（40 处一起改）：批 5 的覆盖面闸按组取用例，"
           "组名一漂就看见空组——这条证的是「mood 组 40 条／reflection 组 11 条」"
           "那个下界不是只在数自己写的数",
      "pytest", B5_PYTEST + "::test_batch5_groups_are_mask_free_and_purely_read", "",
      backup(FX), patch_all(FX, B5_MOOD_GROUP, '"group": "moodp"')),

    m(170, "播种行的两种时刻合并给（5 条相对行同时带上绝对 created_at）：Go 侧 seeding 的"
           "两条分支按「有没有 created_at」分派，同时给就让批 5 钉边界的行位置不确定；"
           "批 1 的窗口余量闸也按这个互斥口径只数相对行",
      "pytest", B5_PYTEST + "::test_batch5_groups_are_mask_free_and_purely_read", "",
      backup(FX), patch_all(FX, '"days_ago": 2.5\n',
                             '"days_ago": 2.5,\n'
                             '      "created_at": "2026-10-14T00:00:00+00:00"\n')),

    m(171, "录制侧漏钉 mood 仓库的 utc_now：`count_by_day` 的窗口下界是 "
           "`utc_now() − days*86400`，而 `mood-t-edge` 有一行正好压在 14 天边界上，"
           "录制与回放之间差一秒就让那一行在窗口内外换边（trend 少一格）",
      "pytest", B5_PYTEST + "::test_recorder_pins_the_mood_clock_to_the_real_module", "",
      backup(GS), patch(GS, GS_MOOD_FREEZE, '    # 变异：忘了冻 mood 的时钟\n')),

    m(172, "取模块退回**包属性**查找（`__import__` 拿到包、再点出 mood_repository）："
           "`repositories/__init__.py:26` 把同名子模块遮蔽成 MoodRepository 单例，"
           "于是冻钟打在实例属性上、全局永远读不到——这是 2026-10-15 真实踩过的空转形状，"
           "而字节面在批 2–4 那种远离边界的判据上根本看不出差别",
      "pytest", B5_PYTEST + "::test_recorder_pins_the_mood_clock_to_the_real_module", "",
      backup(GS), patch(GS, GS_MOOD_MODULE_GET, GS_MOOD_ATTR_GET)),

    m(173, "撤掉冻钟后的自证（把 `!= ANCHOR` 那行改成永假）：前两条锚点各守一支，"
           "但「补丁落在读不到的名字上」只有那一次回读会当场停下；撤掉它，"
           "下一次遮蔽换形状时录制会安静地产出一份错基准的夹具",
      "pytest", B5_PYTEST + "::test_recorder_pins_the_mood_clock_to_the_real_module", "",
      backup(GS), patch(GS, GS_MOOD_SELFCHECK, 'if mod.utc_now() != ANCHOR and False:')),
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
        [str(ROOT / ".venv/bin/python"), "-m", "pytest", B1_PYTEST, B2_PYTEST, B3_PYTEST,
         B4_PYTEST, B5_PYTEST, K8S_PYTEST, "-q", "--no-header"],
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
