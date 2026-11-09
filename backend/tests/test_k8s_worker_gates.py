"""#32 的清单闸：k8s 上「broker 在、Python worker 在、Go 两个开关开着」这一条链可证。

为什么单开一个文件而不是塞进 `test_k8s_manifests.py`：那个文件守的是 N1 那一批
（go-api／py-api 的结构、Ingress 灰度、ConfigMap 跨语言键面）。本文件守的是 **worker 链**
（R4/R5 的部署面），判据全部从**代码字面量**取——清单里的键名是抄代码的，
两侧任一改名都必须当场红，而不是等到线上 worker 静默零消费才发现。

反向也钉一条：三个开关与 broker 地址**不许**混进 `closenai-config`。那张表的键面由
`test_k8s_manifests.py::test_env_var_names_do_not_drift_across_languages` 逐键对账
（只认 `config.go` 的 `envString/envInt` 与 `backend/config.py` 的字段），而 worker 开关
是 env-only、两侧 runtime 都不读——放进去要么把那条闸打红，要么变成一个没人读的假旋钮。

跑法：`.venv/bin/pytest backend/tests/test_k8s_worker_gates.py -q`（纯文件解析，不需要集群）。
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = ROOT / "deploy" / "k8s"
README = ROOT / "deploy" / "README.md"
COMPOSE_PROD = ROOT / "deploy" / "compose" / "docker-compose.prod.yml"

PY_MQ_CONFIG = ROOT / "backend" / "ai" / "mq" / "config.py"
GO_DISPATCH = ROOT / "go-core" / "internal" / "messaging" / "dispatch" / "dispatch.go"
GO_INBOUND = ROOT / "go-core" / "internal" / "messaging" / "inbound" / "inbound.go"


def _load() -> list[dict]:
    docs: list[dict] = []
    for path in sorted(MANIFEST_DIR.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if doc is not None:
                docs.append(doc)
    return docs


def _kind(kind: str, name: str) -> dict:
    matches = [d for d in _load() if d.get("kind") == kind and d["metadata"]["name"] == name]
    assert len(matches) == 1, f"期望恰好 1 个 {kind}/{name}，实际 {len(matches)}"
    return matches[0]


def _container(dep: dict) -> dict:
    return dep["spec"]["template"]["spec"]["containers"][0]


def _env(container: dict) -> dict[str, str]:
    return {item["name"]: item.get("value") for item in container.get("env", [])}


def _env_from(container: dict) -> set[tuple[str, str]]:
    refs: set[tuple[str, str]] = set()
    for item in container.get("envFrom", []):
        if "configMapRef" in item:
            refs.add(("configMap", item["configMapRef"]["name"]))
        if "secretRef" in item:
            refs.add(("secret", item["secretRef"]["name"]))
    return refs


def _to_milli(cpu: str) -> int:
    raw = str(cpu)
    return int(float(raw[:-1])) if raw.endswith("m") else int(float(raw) * 1000)


def _to_bytes(mem: str) -> int:
    raw = str(mem)
    for suffix, factor in (("Gi", 1024**3), ("Mi", 1024**2), ("Ki", 1024)):
        if raw.endswith(suffix):
            return int(float(raw[: -len(suffix)]) * factor)
    return int(float(raw))


def _literal(path: Path, pattern: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.M)
    assert match, f"{path.name} 里找不到 {pattern!r}：代码结构变了，这条闸要重读"
    return match.group(1)


# ── broker ─────────────────────────────────────────────────────────────────


def test_broker_is_single_node_with_persistent_data_and_ping_probes():
    dep = _kind("Deployment", "rabbitmq")
    container = _container(dep)

    ports = {p["name"]: p["containerPort"] for p in container["ports"]}
    assert ports == {"amqp": 5672}, f"broker 只该暴露 AMQP 5672，实得 {ports}"

    svc = _kind("Service", "rabbitmq")
    assert svc["spec"]["selector"] == dep["spec"]["selector"]["matchLabels"], (
        "Service selector 与 Deployment 不一致：worker 会拨到一个没有端点的名字上"
    )
    port = svc["spec"]["ports"][0]
    assert port["port"] == 5672 and port["name"] == "amqp", port
    assert port["targetPort"] in ports, (
        f"targetPort={port['targetPort']!r} 引用了容器里不存在的端口名"
    )

    # 单实例队列没有「滚动更新」可谈：两个同名节点同时看到同一份 mnesia 目录会直接拒启。
    assert dep["spec"]["strategy"]["type"] == "Recreate"
    pod = dep["spec"]["template"]["spec"]
    assert pod["hostname"] == "rabbitmq", (
        "节点名默认取 hostname；固定不住的话每次重建都会换节点名，hostPath 里的旧数据对不上"
    )

    volumes = {v["name"]: v for v in pod["volumes"]}
    host_path = volumes["data"]["hostPath"]
    assert host_path["path"] == "/var/lib/closenai/rabbitmq"
    # DirectoryOrCreate 而不是 Directory：这台集群没有必须手工预建宿主目录的前置步骤，
    # 属主由镜像 entrypoint（root 起、chown 后降 uid 999）在容器内修正。
    assert host_path["type"] == "DirectoryOrCreate"
    mounts = {m["name"]: m["mountPath"] for m in container["volumeMounts"]}
    assert mounts["data"] == "/var/lib/rabbitmq", mounts

    for probe in ("readinessProbe", "livenessProbe"):
        assert container[probe]["exec"]["command"] == [
            "rabbitmq-diagnostics", "-q", "ping",
        ], f"{probe} 必须与 compose 的 healthcheck 同一条命令（管理面之外唯一判据）"

    res = container["resources"]
    assert _to_milli(res["requests"]["cpu"]) <= _to_milli(res["limits"]["cpu"])
    assert _to_bytes(res["requests"]["memory"]) <= _to_bytes(res["limits"]["memory"])


def test_broker_credentials_come_only_from_the_out_of_band_secret():
    container = _container(_kind("Deployment", "rabbitmq"))
    assert ("secret", "closenai-rabbitmq") in _env_from(container), (
        "rabbitmq 的账号口令必须来自独立 Secret closenai-rabbitmq"
    )
    inline = [name for name in _env(container) if "RABBITMQ" in name]
    assert not inline, f"口令/账号不许写成容器 env 字面量：{inline}"

    # 清单里不得出现 Secret 资源本体（本仓口径：跨环境各自创建，键面在 deploy/README.md）。
    assert not [d for d in _load() if d.get("kind") == "Secret"]

    cm = _kind("ConfigMap", "closenai-config")
    broker_keys = [key for key in cm["data"] if "RABBITMQ" in key]
    assert not broker_keys, f"broker 凭据不许进共享 ConfigMap：{broker_keys}"


# ── 三个开关 ────────────────────────────────────────────────────────────────


def test_worker_switches_are_code_literals_and_on_in_the_right_containers():
    """名字从代码读、值在清单上必须是 "1"、落点必须是那个进程。

    这条是 #32 的核心：清单写对了名字但写错了容器（比如一并塞进 py-api），
    上线后 go-api 仍然没有消费者，而所有文件看起来都「有配置」。
    """
    py_switch = _literal(PY_MQ_CONFIG, r'^ENV_WORKER = "([A-Z0-9_]+)"')
    outbox_switch = _literal(GO_DISPATCH, r'EnvWorker\s+= "([A-Z0-9_]+)"')
    inbound_switch = _literal(GO_INBOUND, r'EnvWorker\s+= "([A-Z0-9_]+)"')
    url_keys = {
        _literal(PY_MQ_CONFIG, r'^ENV_URL = "([A-Z0-9_]+)"'),
        _literal(GO_DISPATCH, r'EnvURL\s+= "([A-Z0-9_]+)"'),
        _literal(GO_INBOUND, r'EnvURL\s+= "([A-Z0-9_]+)"'),
    }
    assert len(url_keys) == 1, f"三侧读到了不同的 broker 地址键名：{url_keys}"
    url_key = url_keys.pop()
    assert url_key == "CLOSEN_AMQP_URL", "跨语言唯一的地址键被改名了：先同步三侧代码再改清单"

    go_container = _container(_kind("Deployment", "go-api"))
    go_env = _env(go_container)
    # 两个 Go worker 嵌在 go-api 进程里（仓库没有单独的 cmd 二进制）⇒ 开关必须在这一格。
    assert go_env.get(outbox_switch) == "1", (
        f"go-api 容器 env 里 {outbox_switch} 不是 \"1\"：outbox 行只进不出"
    )
    assert go_env.get(inbound_switch) == "1", (
        f"go-api 容器 env 里 {inbound_switch} 不是 \"1\"：候选队列没有消费者"
    )
    # 开了却没地址 ⇒ Go 侧 log.Fatalf。地址只从 Secret 来，清单里不写第二份。
    assert ("secret", "closenai-runtime") in _env_from(go_container)

    worker_container = _container(_kind("Deployment", "py-ai-worker"))
    assert _env(worker_container).get(py_switch) == "1", (
        f"py-ai-worker 容器 env 里 {py_switch} 不是 \"1\"：进程会打印「未开启」后退出 0"
    )
    assert ("secret", "closenai-runtime") in _env_from(worker_container), (
        "worker 的 CLOSEN_AMQP_URL 只从 closenai-runtime 来；缺了就拒绝启动"
    )

    cm_keys = set(_kind("ConfigMap", "closenai-config")["data"])
    leaked = {py_switch, outbox_switch, inbound_switch, url_key} & cm_keys
    assert not leaked, (
        f"worker 开关／broker 地址不许进 closenai-config：{sorted(leaked)}。"
        "那张表的键面要求两侧 runtime 真读（env-only 的键放进去会把跨语言闸打红，"
        "或者变成没人读的假旋钮）"
    )


# ── Python worker ──────────────────────────────────────────────────────────


def test_python_worker_is_the_py_api_image_with_the_worker_entrypoint():
    worker = _kind("Deployment", "py-ai-worker")
    container = _container(worker)
    py_api = _container(_kind("Deployment", "py-api"))

    # 同一个镜像、只换入口：依赖面只有 Dockerfile.py-api 一个真相源。
    assert container["image"] == py_api["image"], (
        "py-ai-worker 必须与 py-api 同一镜像：分开两份就会出现「HTTP 面能跑、worker 少依赖」"
    )
    command = ["python", "-m", "backend.ai.mq.worker"]
    assert container["command"] == command

    # 纯消费者：不服务入站流量，因此没有端口、也没有 http 探针（探针盲区登记在文件注释里）。
    assert not container.get("ports"), "worker 不监听端口，声明 ports 会让 Service 接错东西"
    assert "readinessProbe" not in container and "livenessProbe" not in container

    # SIGTERM 后要先排水 30s（DEFAULT_DRAIN_SECONDS）再关连接。
    grace = worker["spec"]["template"]["spec"]["terminationGracePeriodSeconds"]
    assert grace >= 45, f"terminationGracePeriodSeconds={grace}：比排水预算 30s 还短会硬杀在途任务"

    refs = _env_from(container)
    assert ("configMap", "closenai-config") in refs
    assert ("secret", "closenai-runtime") in refs, "缺库口令与 broker 地址：worker 起不来"

    res = container["resources"]
    assert _to_milli(res["requests"]["cpu"]) <= _to_milli(res["limits"]["cpu"])
    assert _to_bytes(res["requests"]["memory"]) <= _to_bytes(res["limits"]["memory"])

    # 两套编排跑同一个入口：compose prod 与 k8s 的 command 改一处必须改两处。
    compose = yaml.safe_load(COMPOSE_PROD.read_text(encoding="utf-8"))
    assert compose["services"]["py-ai-worker"]["command"] == command, (
        "docker-compose.prod.yml 的 py-ai-worker command 与 k8s 不一致"
    )


# ── 文档与明文 ──────────────────────────────────────────────────────────────


def test_readme_registers_the_broker_secret_and_the_url_key():
    """清单外的两张 Secret 没有登记表，README 就是唯一键面——悬空引用已经出现过一次。"""
    text = README.read_text(encoding="utf-8")
    for token in ("closenai-rabbitmq", "RABBITMQ_DEFAULT_USER", "RABBITMQ_DEFAULT_PASS",
                  "CLOSEN_AMQP_URL", "/var/lib/closenai/rabbitmq", "py-ai-worker"):
        assert token in text, f"deploy/README.md 没有登记 {token}：运维照这份文档创建 Secret"


def test_manifests_carry_no_plaintext_broker_credentials():
    # amqp://<user>:<pass>@host —— 带口令的地址一旦落进清单，它同时进了 git 与 `kubectl get -o yaml`。
    with_password = re.compile(r"amqp://[^/\s:@]+:[^/\s@]+@")
    for path in sorted(MANIFEST_DIR.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        assert not with_password.search(text), f"{path.name} 里出现了带口令的 amqp URL"
        assert "RABBITMQ_DEFAULT_PASS:" not in text, f"{path.name} 里出现了 broker 口令键"
