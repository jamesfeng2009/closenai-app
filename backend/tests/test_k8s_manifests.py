"""N1 部署纵切的验收测试（tasks.md N1 剩余项：k3s 起 go-api + py-api，Ingress 灰度 0%）。

判据分两层：
1. 纯清单结构断言——无集群也能跑，CI 必需。它守的是「灰度 0%」「端口/探针/标签一致」
   「环境变量名跨语言不漂移」「清单里不出现明文密钥」这几条会让上线炸掉的不变量。
2. `kubectl apply --dry-run=server`——有机读 kubeconfig 时跑，把清单交给真实 API server
   校验 schema。本机 OrbStack 无 IngressClass，因此 Ingress 只做 dry-run 不做端到端。

真实共享集群（4c8G k3s）的 `kubectl apply` 不在这里发生：那是需单独授权的动作。
"""

from __future__ import annotations

import json
import re

from go_route_table import go_ops, go_table_ops, normalize_param, route_row
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = ROOT / "deploy" / "k8s"
NAMESPACE = "closenai"


def _load_all() -> list[dict]:
    docs: list[dict] = []
    for path in sorted(MANIFEST_DIR.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if doc is not None:
                docs.append(doc)
    return docs


def _by_kind(kind: str) -> list[dict]:
    return [d for d in _load_all() if d.get("kind") == kind]


def _one(kind: str, name: str) -> dict:
    matches = [d for d in _by_kind(kind) if d["metadata"]["name"] == name]
    assert len(matches) == 1, f"期望恰好 1 个 {kind}/{name}，实际 {len(matches)}"
    return matches[0]


def _deployment(name: str) -> dict:
    return _one("Deployment", name)


def _container(dep: dict) -> dict:
    return dep["spec"]["template"]["spec"]["containers"][0]


def _to_milli(cpu: str) -> int:
    raw = str(cpu)
    return int(float(raw[:-1])) if raw.endswith("m") else int(float(raw) * 1000)


def _to_bytes(mem: str) -> int:
    raw = str(mem)
    units = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4}
    for suffix, factor in units.items():
        if raw.endswith(suffix):
            return int(float(raw[: -len(suffix)]) * factor)
    return int(float(raw))


def test_manifests_parse_and_are_namespaced():
    docs = _load_all()
    assert docs, f"{MANIFEST_DIR} 下没有可解析的清单"
    kinds = [d["kind"] for d in docs]
    for expected in ("Namespace", "ConfigMap", "Deployment", "Service", "Ingress"):
        assert expected in kinds, f"缺少 {expected} 清单"
    for doc in docs:
        if doc["kind"] == "Namespace":
            assert doc["metadata"]["name"] == NAMESPACE
        else:
            assert doc["metadata"].get("namespace") == NAMESPACE, (
                f"{doc['kind']}/{doc['metadata']['name']} 未落在 {NAMESPACE}，"
                "会和同机另一个项目串流量"
            )


@pytest.mark.parametrize("app", ["go-api", "py-api"])
def test_deployment_selector_matches_pods_and_service(app: str):
    dep = _deployment(app)
    selector = dep["spec"]["selector"]["matchLabels"]
    pod_labels = dep["spec"]["template"]["metadata"]["labels"]
    assert selector.items() <= pod_labels.items(), f"{app}: selector 不命中 Pod 标签"

    svc = _one("Service", app)
    assert svc["spec"]["selector"] == selector, f"{app}: Service selector 与 Deployment 不一致"
    port = svc["spec"]["ports"][0]
    port_names = {p["name"] for p in _container(dep)["ports"]}
    assert port["port"] == 8000, f"{app}: Service 端口应为 8000（两侧统一）"
    assert port["targetPort"] in port_names, (
        f"{app}: Service targetPort 引用了容器里不存在的端口名 {port['targetPort']}"
    )


def test_probe_paths_match_what_each_runtime_serves():
    # Go 的 /healthz 是进程就绪探针，不在 105 条契约登记内；Python 的 /health 是契约端点。
    go = _container(_deployment("go-api"))
    py = _container(_deployment("py-api"))
    assert go["readinessProbe"]["httpGet"]["path"] == "/healthz"
    assert go["livenessProbe"]["httpGet"]["path"] == "/healthz"
    assert py["readinessProbe"]["httpGet"]["path"] == "/health"
    assert py["livenessProbe"]["httpGet"]["path"] == "/health"
    for dep, app in ((_deployment("go-api"), "go-api"), (_deployment("py-api"), "py-api")):
        declared = {p["name"] for p in _container(dep)["ports"]}
        for probe in ("readinessProbe", "livenessProbe"):
            assert _container(dep)[probe]["httpGet"]["port"] in declared, (
                f"{app}: {probe} 引用了未声明的端口名"
            )


def test_resources_are_ordered_and_pyapi_follows_arch_doc():
    for app in ("go-api", "py-api"):
        res = _container(_deployment(app))["resources"]
        assert _to_milli(res["requests"]["cpu"]) <= _to_milli(res["limits"]["cpu"]), app
        assert _to_bytes(res["requests"]["memory"]) <= _to_bytes(res["limits"]["memory"]), app

    # 《架构与技术方案-2026-09-06》§十：FastAPI api 容器 250m/512Mi → 1c/1.5Gi。
    py = _container(_deployment("py-api"))["resources"]
    assert (_to_milli(py["requests"]["cpu"]), py["requests"]["memory"]) == (250, "512Mi")
    assert (_to_milli(py["limits"]["cpu"]), py["limits"]["memory"]) == (1000, "1.5Gi")


def test_ingress_is_at_zero_percent_rollout():
    """N8 灰度 0% 的可证形态（口径在 2026-10-14 从「没有 path 指 go-api」改成下面四条）。

    go-api 接管一条域 path 但百分比为 0 时，它是**逐字转交** py-api
    （go-core/internal/cutover），所以「0%」不再是「Go 不接流量」，而是
    「Go 接了流量但一条业务逻辑都不跑」。四条判据：
      1. 指 go-api 的每条 path 必须是一个 `/api/v1/<域>` 前缀或 `/api/v1/<域>/<端点>`
         子路径（批 5 起有「同域里只切一部分端点」的形状），且能在 Go 的登记表里找到；
      2. ConfigMap 里有该域的 `CLOSEN_CUTOVER_<域>`，取值 0..100，且本轮**必须为 0**；
      3. `CLOSEN_PY_API_URL` 非空——有域在 0..99 之间时 go-api 要靠它转交，
         这正是 Go 侧「缺值即拒绝启动」的那条约束，清单与代码必须同口径；
      4. py-api 的 Service 仍在（转交目标），/api/v1 与 /health 的兜底 backend 未动。
    """
    ing = _one("Ingress", "closenai")
    cm = _one("ConfigMap", "closenai-config")
    registered = go_ops()

    go_bound: list[tuple[str, str]] = []
    fallbacks: dict[str, str] = {}
    for rule in ing["spec"]["rules"]:
        for path in rule["http"]["paths"]:
            assert path["pathType"] in ("Prefix", "Exact")
            name = path["backend"]["service"]["name"]
            if name == "go-api":
                go_bound.append((path["path"], path["pathType"]))
            else:
                fallbacks[path["path"]] = name
    assert go_bound, "批 1 起 Ingress 必须已有指向 go-api 的域 path，否则切流机制无从验证"

    for path, path_type in go_bound:
        match = re.fullmatch(r"/api/v1(/[a-z][a-z0-9-]*)+", path)
        assert match, f"{path} 不是 /api/v1/<域>[/<端点>] 形态：切流一刀只能落在这两种形状上"
        assert path_type in ("Prefix", "Exact"), f"{path} 的 pathType 不可识别"
        # 灰度键取**第一个域段**：批 5 的两条 mood 端点共用 CLOSEN_CUTOVER_MOOD。
        # 域级键与端点级 path 是**刻意错位**的（决策点 D-5，见 50-ingress.yaml 批 5 段），
        # 这个错位由 test_go_bound_contract_paths_are_registered_in_go 兜住。
        domain = path[len("/api/v1/") :].split("/")[0]
        # 清单声称这个域归 Go，Go 侧就得真有这个域的路由，不然上线即 404。
        assert any(pp.startswith(f"/api/v1/{domain}") for _, pp in registered), (
            f"Ingress 把 {path} 指向 go-api，但 Go 的声明表里没有该域的任何路由"
        )
        key = f"CLOSEN_CUTOVER_{domain.upper().replace('-', '_')}"
        assert key in cm["data"], f"{path} 指向 go-api 却没有 {key}：灰度无法回滚到 0"
        value = cm["data"][key]
        assert re.fullmatch(r"-?\d+", str(value)), f"{key} 必须是整数百分比，实际 {value!r}"
        assert 0 <= int(value) <= 100, f"{key}={value} 越界（Go 侧会拒绝启动）"
        assert int(value) == 0, (
            f"{key}={value}：批 1–5 落地时每个已接管域都必须是 0%。"
            "真要放量是一次有意的上线动作，改这条断言就是那次动作的登记点。"
        )

    assert fallbacks.get("/api/v1") == "py-api", f"/api/v1 兜底 backend = {fallbacks}"
    assert fallbacks.get("/health") == "py-api", f"/health backend = {fallbacks}"
    assert set(fallbacks.values()) == {"py-api"}, fallbacks

    url = cm["data"].get("CLOSEN_PY_API_URL", "")
    assert url.startswith("http"), f"有域处于 0..99 时 CLOSEN_PY_API_URL 必须可拨通：{url!r}"
    _one("Service", "go-api")  # 改 backend 名的前置：Service 不存在会当场 503
    _one("Service", "py-api")  # 转交目标


def _ingress_entries() -> list[dict]:
    """把 Ingress 的 path 摊平成 [{path, pathType, backend}]（规则里只有一条 host 无关规则）。"""
    ing = _one("Ingress", "closenai")
    return [
        {"path": p["path"], "pathType": p["pathType"],
         "backend": p["backend"]["service"]["name"]}
        for rule in ing["spec"]["rules"]
        for p in rule["http"]["paths"]
    ]




def _matches(entry: dict, contract_path: str) -> bool:
    """Ingress 规范里两种 pathType 的匹配语义（Traefik/nginx 同口径）。

    Prefix 是**按路径段**的前缀：`/api/v1` 命中 `/api/v1` 与 `/api/v1/x`，
    但不命中 `/api/v1x`。这一条正是批 5 那一刀要从 Prefix 降到 Exact 的原因——
    `/api/v1/reflections` 会把 `/api/v1/reflections/generate` 也搬进 go-api。
    """
    if entry["pathType"] == "Exact":
        return contract_path == entry["path"]
    return contract_path == entry["path"] or contract_path.startswith(
        entry["path"] + "/")


def _operations() -> list[tuple[str, str]]:
    """契约面的每一个 (方法, 路径) 对——**不是**只看路径。

    Ingress 的 path 匹配**不看 HTTP 方法**（规范里只有 path/pathType），
    所以一条 path 一旦被指给 go-api，它身上的**所有方法**一起搬走。
    `/api/v1/reflections` 就是实测到的那一格：GET 与 POST 共用一个 path，
    而本批只复刻了 GET——按路径模拟会判它「没问题」，按操作模拟才判得出 405。
    """
    paths = json.loads(
        (ROOT / "contracts" / "openapi.json").read_text(encoding="utf-8"))["paths"]
    return sorted(
        (method.upper(), path)
        for path, ops in paths.items()
        for method in ops
    )


def _route(op_path: str, entries: list[dict]) -> dict | None:
    """最长前缀优先：返回真正接管这条契约路径的那一条 Ingress 规则。"""
    winners = [e for e in entries if _matches(e, op_path)]
    if not winners:
        return None
    longest = max(len(e["path"]) for e in winners)
    tied = [e for e in winners if len(e["path"]) == longest]
    assert len(tied) == 1, f"{op_path} 有 {len(tied)} 条同长度规则，路由结果不确定"
    return tied[0]


def test_go_bound_contract_operations_are_registered_in_go():
    """切过去的那部分流量，Go 必须**每一个操作都答得了**——按契约面逐条验，不是按域名抽查。

    为什么不是「Ingress 的 path 在 server.go 里出现就行」（那是上一版）：
    批 5 第一次出现**同域混切**（mood 的两条 GET 切、POST /mood/checkin 不切），
    一刀的粒度与灰度键的粒度不再是同一个东西。这时唯一可证的判据是把 Ingress 的
    匹配语义**真的模拟一遍**：拿 contracts/openapi.json 的每一个 (方法, 路径) 求
    「哪条规则接管」，凡结果是 go-api 的，server.go 里就必须有那条**带方法**的注册。
    少一条就是上线即 404/405，而且**与灰度百分比无关**——转交发生在 handler 内部，
    路径没匹配上进不到 handler，0% 也救不回来。
    """
    entries = _ingress_entries()
    operations = _operations()
    registered = go_ops()

    go_answered: list[tuple[str, str]] = []
    for method, path in operations:
        winner = _route(path, entries)
        assert winner, f"{path} 不被任何一条 Ingress 规则覆盖＝生产里根本进不来"
        if winner["backend"] == "go-api":
            # 方法与路径**一起**比，少一个方法就是 405（真源见 go_registered_ops 的注释）。
            assert (method, normalize_param(path)) in registered, (
                f"{method} {path} 被 {winner['path']}（{winner['pathType']}）判给 go-api，"
                f"但 Go 没注册这个操作"
            )
            go_answered.append((method, path))
    assert go_answered, "没有任何一个契约操作归 go-api：切流机制在清单上不成立"

    # 每条指 go-api 的规则至少要命中一个契约操作——拼错的子路径（少一个字母）
    # 不会命中任何 openapi 条目，也就不会被上面那段查到，只能在这里显形。
    for entry in entries:
        if entry["backend"] != "go-api":
            continue
        hit = [op for op in operations if _matches(entry, op[1])]
        assert hit, f"{entry['path']} 指向 go-api 却不命中任何契约操作＝清单里的一条幽灵规则"

    # 批 5 那一刀的两面，各自钉一次（上面两条全绿也可能整批 mood 消失）：
    # 切过来的两条必须在 go-api 侧，同域**没切**的 POST 必须还在 py-api 侧。
    answered = set(go_answered)
    assert {("GET", "/api/v1/mood/history"), ("GET", "/api/v1/mood/trend")} <= answered
    for still_python in ("POST /api/v1/mood/checkin", "POST /api/v1/reflections",
                         "POST /api/v1/reflections/generate"):
        method, path = still_python.split(" ", 1)
        winner = _route(path, entries)
        assert winner["backend"] == "py-api", (
            f"{still_python} 落到了 {winner['backend']}（规则 {winner['path']}）："
            "它依赖 LLM 或写库，批 5 不切，被同 path 顺手搬走就是上线即 405"
        )
    # 逐条数一遍，作为「这一批改了什么」的留痕：批 1–5 共 10 个操作归 Go。
    # 这条也是 reflections 那一格的对账：GET /api/v1/reflections 与它的 POST 共用
    # 一个 path，Ingress 分不开，所以本批**没有**给它上 path（tasks.md 批 5 段 D-10）。
    assert len(go_answered) == 10, f"归 go-api 的契约操作 {len(go_answered)} 个：{go_answered}"


def test_cutover_keys_without_ingress_path_stay_unrouted():
    """「键先行、path 不上刀」这一档可证——它今天是既有清单闸的反向盲区。

    `test_ingress_is_at_zero_percent_rollout` 遍历的是**指 go-api 的 path**，
    从 path 去要键（有 path ⇒ 键必须存在、必须是 0、Go 侧必须有该域路由）。
    反方向一条断言都没有：实测证据是本批把 `CLOSEN_CUTOVER_MEMORIES` 写进
    `10-config.yaml` 前后跑那个文件都是 13 passed / 1 skipped，
    先红的是另一侧的三段登记闸
    （batch2::test_three_registers_agree_on_the_cut_domains，它 own 这一档的**成员集合**）。

    所以分工写清楚，免得两处各登记一份域名清单：
      · 「这一档有哪些域」＝ batch2 的 `PATHLESS_KEYS`（唯一登记表）；
      · 本条从清单本身**推出**同一个集合（声明了键、域却没有 go-api path 的那些），
        只钉这一档每一格的**内容**：
        1. 键的值必须是 "0"：无 path 的域放量落在 path 上，键调高也不放量，
           但清单里留一个非零值＝运维侧读到「这域正在放量」；
        2. 该域在 Ingress 里**没有任何规则**，连指 py-api 的预铺规则也不行——
           下一刀的人会按「这个域已经有入口规则了」去改百分比；
        3. 契约面上该域的**每一个方法**（含 Go 没实现的 POST/DELETE）按最长前缀模拟
           仍落 py-api——D-10 的实质不是「GET 不切」，是「整条 path 不切」；
        4. Go 侧该域的每一个 **GET** 已在声明表里带方法注册。**两侧各读一遍**：
           生成物 `contracts/cutover-matrix.md`（T6-02 起的对外事实源）＋ 源码
           `go-core/internal/httpapi/routes.go` 的声明表。只读生成物会漏掉
           「改了源码没重跑生成器」这一步（2026-10-19 由 mutate_n8_gates 的
           #227／#228 抓到，两侧同读才是这条的本意）：前三格全绿也可能是
           实现被删（键、值、path 都没变），这一条把那个退路显形。
        5. **本文件盯不到的一格，靠 Go 侧那条闸补**：键进了 ConfigMap 不等于进了
           `cmd/go-api/main.go` 传给 `cutover.New` 的 gate map，而 `cutover.go:91` 对
           查不到的域判 `if !ok || pct >= 100 { return true }`——「没登记」和「100%」同形。
           `CLOSEN_CUTOVER_MEMORIES` 就是这样一条死开关（D-G12，批 6 交付的漏项）：
           上面四格全绿，下一刀真把 path 放上 Ingress 时 Go 会无视百分比全量自答、
           键调回 0 也回滚不了。对账面在
           `go-core/cmd/go-api/gate_map_coverage_test.go`（双向：声明⇒接线、接线⇒声明）。
           改这一档的人**两面都要跑**，只跑本文件会漏。
    """
    cm = _one("ConfigMap", "closenai-config")
    entries = _ingress_entries()
    operations = _operations()
    registered = go_ops()
    table = go_table_ops()

    def domain_of(key: str) -> str:
        return key[len("CLOSEN_CUTOVER_"):].lower().replace("_", "-")

    routed = {
        entry["path"][len("/api/v1/"):].split("/")[0]
        for entry in entries if entry["backend"] == "go-api"
    }
    declared = {
        domain_of(key) for key in cm["data"] if key.startswith("CLOSEN_CUTOVER_")
    }
    key_only = sorted(declared - routed)
    assert key_only, (
        "声明了键却没有 go-api path 的域一个都不剩：这一档是空的，"
        "下面的四格判据全部空转（成员集合变更请回 batch2 的 PATHLESS_KEYS 对账）"
    )

    for domain in key_only:
        key = f"CLOSEN_CUTOVER_{domain.upper().replace('-', '_')}"
        assert cm["data"][key] == "0", (
            f"{key}={cm['data'][key]!r}：这一域没有入口 path，键非零＝清单在谎报放量状态"
        )

        prefix = f"/api/v1/{domain}"
        blocking = [entry for entry in entries
                    if entry["path"] == prefix or entry["path"].startswith(prefix + "/")]
        assert not blocking, (
            f"{prefix} 在 Ingress 里出现了规则（{[b['path'] for b in blocking]}）："
            "这一档的域不该有任何入口规则——把 path 指给 go-api 会把同 path 的写方法"
            "搬进只注册了 GET 的 Go，指给 py-api 则让下一刀误判自己已经上过刀"
        )

        ops = [op for op in operations
               if op[1] == prefix or op[1].startswith(prefix + "/")]
        assert ops, f"{prefix} 在契约面上一个操作都没有：域键名和 path 对不上"
        for method, path in ops:
            winner = _route(path, entries)
            assert winner["backend"] == "py-api", (
                f"{method} {path} 落到了 {winner['backend']}（规则 {winner['path']}）："
                f"{domain} 是 D-10 的「只声明不上刀」档，本批一条方法都不该切"
            )
            if method == "GET":
                assert ("GET", normalize_param(path)) in registered, (
                    f"GET {path} 在 Go 的声明表里没有带方法的注册：{domain} 的读面没实现，"
                    "D-10 的理由（只等同 path 的写侧）不成立，这一格得重新谈"
                )
                # 同一句话的**源码侧**读法：删了声明但没重跑生成器时，上面那条看不见
                #（它读的是生成物）。2026-10-19 由 mutate_n8_gates 的 #227／#228 抓到。
                assert ("GET", normalize_param(path)) in table, (
                    f"GET {path} 在 routes.go 的声明表里找不到（域键带上的那种形状）："
                    "生成物还写着它 ⇒ md 已过期，重跑 `cd go-core && go run ./tools/cutovermatrix -write`"
                )


def test_contract_paths_are_routed():
    """契约面只有两类入口：/api/v1/* 与 app 级 /health（N0 计数的 105 = 104 + 1）。

    N8 之后 /api/v1 之下允许**整段域前缀**（切流一刀），但不得出现别的入口形态。
    """
    ing = _one("Ingress", "closenai")
    paths = {
        p["path"]
        for rule in ing["spec"]["rules"]
        for p in rule["http"]["paths"]
    }
    assert "/api/v1" in paths and "/health" in paths, paths
    for path in paths - {"/health"}:
        assert path == "/api/v1" or path.startswith("/api/v1/"), (
            f"{path} 不在契约的两类入口里"
        )
    assert "/healthz" not in paths, "Go 的进程就绪探针不对外占 path"


def test_env_var_names_do_not_drift_across_languages():
    """ConfigMap 的键必须是 Go/Python 任一侧真正读取的名字。

    跨语言互认靠同一批变量名（JWT_SECRET 等）；一侧改名另一侧不知道，token 就互不认。
    """
    cm = _one("ConfigMap", "closenai-config")
    declared = set(cm["data"])

    go_src = (ROOT / "go-core" / "internal" / "config" / "config.go").read_text(encoding="utf-8")
    go_names = set(re.findall(r'env(?:String|Int)\("([A-Z0-9_]+)"', go_src))

    py_src = (ROOT / "backend" / "config.py").read_text(encoding="utf-8")
    py_names = set(re.findall(r"^\s+([A-Z0-9_]+)\s*[:=]", py_src, re.M))

    infra_only = {"PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED"}
    unknown = declared - go_names - py_names - infra_only
    assert not unknown, f"ConfigMap 里有两侧都不读的名字：{sorted(unknown)}"

    assert {"JWT_ALGORITHM", "JWT_ACCESS_EXPIRE_MINUTES", "JWT_REFRESH_EXPIRE_DAYS"} <= go_names
    assert "JWT_SECRET" not in declared, "JWT_SECRET 属敏感项，只能进 Secret"


def test_manifests_contain_no_plaintext_secrets():
    """取证口径是白名单点名列，不做前缀通配：逐个密钥名自证没写进清单。"""
    assert not _by_kind("Secret"), "清单里不该出现 Secret 资源本体"
    for path in sorted(MANIFEST_DIR.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        assert "stringData" not in text, path.name
        for key in ("DATABASE_URL", "JWT_SECRET", "CRON_SECRET", "RESEND_API_KEY",
                    "POSTHOG_API_KEY"):
            assert not re.search(rf"{key}\s*[:=]\s*\S", text), f"{path.name} 内含 {key} 明文"


@pytest.mark.parametrize("image_kind", ["go-api", "py-api"])
def test_images_are_pinned_and_dockerfiles_agree_on_the_port(image_kind: str):
    image = _container(_deployment(image_kind))["image"]
    assert "latest" not in image and "<" not in image and "CHANGEME" not in image, image

    dockerfile = {
        "go-api": ROOT / "go-core" / "Dockerfile",
        "py-api": ROOT / "deploy" / "Dockerfile.py-api",
    }[image_kind]
    assert dockerfile.is_file(), f"缺少 {dockerfile}"
    text = dockerfile.read_text(encoding="utf-8")
    assert "EXPOSE 8000" in text, f"{dockerfile.name} 暴露端口须与清单 containerPort 一致"
    assert text.count("FROM ") >= 2, f"{dockerfile.name} 应为多阶段构建"


def test_gocore_dockerfile_builds_the_binary_the_deployment_runs():
    text = (ROOT / "go-core" / "Dockerfile").read_text(encoding="utf-8")
    assert "CGO_ENABLED=0" in text, "distroless 无 libc，CGO 必须关"
    assert "./cmd/go-api" in text, "构建目标须与唯一入口一致"
    assert 'ENTRYPOINT ["/go-api"]' in text
    assert 'ENV HTTP_ADDR=:8000' in text


def test_kubectl_dry_run_if_cluster_available():
    """有可读 kubeconfig 时让真实 API server 校验 schema；没有就跳过，不算失败。"""
    if shutil.which("kubectl") is None:
        pytest.skip("kubectl 不在 PATH")
    probe = subprocess.run(
        ["kubectl", "cluster-info"], capture_output=True, text=True, timeout=20
    )
    if probe.returncode != 0:
        pytest.skip(f"无可达集群：{probe.stderr.strip()[:120]}")

    ns = subprocess.run(
        ["kubectl", "get", "namespace", NAMESPACE], capture_output=True, text=True
    )
    if ns.returncode != 0:
        pytest.skip(f"集群里没有 {NAMESPACE} 命名空间，未创建（建 ns 属部署动作，需授权）")

    run = subprocess.run(
        ["kubectl", "apply", "--dry-run=server", "-f", str(MANIFEST_DIR)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 0, run.stderr[-2000:]
