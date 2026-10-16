"""R3-⑤ · 运行时依赖与部署面闸（`requirements`／镜像 `EXPOSE`／k8s 端口与探针）。

为什么这一批要为「两份清单 + 两份清单外的 YAML」写测：这三样都是**跨进程后才生效**的配置，
而它们的失效方式全都是「本机看不见、上线当场炸」——
少一个 grpcio 会让 `python -m backend.ai.rpc.server` 起不来（ImagePullPolicy=IfNotPresent
时甚至要等到 pod CrashLoop 才知道），而**探针被顺手挪到 gRPC 端口**改的不是端口，
是灰度语义（见下面 test_py_api_probes_stay_on_http_health 的注释）。
`scripts/mutate_r3_gates.py` 第 ④ 条就是把它打红用的。

判据出处（逐字）：
  · `AI进程面工作单-2026-10-16.md` §三.6：「镜像 `EXPOSE` 加 50051、k8s Service 加 grpc 端口名
    ——**但探针不动**（readiness 仍 `/health`，否则改了探针等于改了灰度语义）」
  · `tasks.md:1019`：grpcio/protobuf 升为运行时依赖的**条件**写在清单注释里，R3 建 server 才触发；
    grpcio-tools 依旧是构建期（`pyproject.toml` dev extra），装进运行镜像是白放 22M（D-R3-6）
  · `tasks.md:1105`（R7）：gRPC 只在集群内，**不出 Ingress** ⇒ 本批不许把 ai-rpc 挂到对外路由上
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = ROOT / "backend" / "requirements.txt"
PYPROJECT = ROOT / "pyproject.toml"
DOCKERFILE = ROOT / "deploy" / "Dockerfile.py-api"
PY_API_YAML = ROOT / "deploy" / "k8s" / "40-py-api.yaml"
AI_RPC_YAML = ROOT / "deploy" / "k8s" / "50-ai-rpc.yaml"
INGRESS_YAML = ROOT / "deploy" / "k8s" / "50-ingress.yaml"

# server.py 里的默认端口（清单里的 containerPort／Service port／env 三处必须与它同值）
EXPECTED_GRPC_PORT = 50051


def _requirement_lines(path: Path) -> set[str]:
    """清单里的**包名**集合（注释与空行剔除，extras/版本约束剥掉）。"""
    out: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if match:
            out.add(match.group(1).lower())
    return out


def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]


def _by_kind(docs: list[dict], kind: str) -> dict:
    found = [d for d in docs if d["kind"] == kind]
    assert len(found) == 1, f"{kind} 应恰好一个，实际 {len(found)}"
    return found[0]


# ── ① 两份清单：grpcio/protobuf 进运行时，grpcio-tools 留在构建期 ──


def test_requirements_txt_has_runtime_grpc_and_not_tools():
    reqs = _requirement_lines(REQUIREMENTS)
    assert {"grpcio", "protobuf"} <= reqs, (
        f"backend/requirements.txt 缺运行时依赖：运行镜像按它装包，"
        f"缺了就等于 pod 起来才 ImportError（现有 {[r for r in reqs if 'grpc' in r or r == 'protobuf']}）")
    assert "grpcio-tools" not in reqs, (
        "grpcio-tools 是构建期工具（带 protoc + 插件，实测 22M），不该进运行镜像")


def test_pyproject_runtime_and_dev_extras():
    """pyproject 与 requirements 是**两份**清单：运行镜像按后者装包，`pip install -e .` 按前者。
    两份口径不同＝本机绿、线上少包。这里用 tomllib 真解析而不是文本匹配——当天踩过：
    `uvicorn[standard]` 里的方括号会把非贪婪正则截断在第一个 `]`，
    于是「主依赖里没有 grpcio」变成一条假红。
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    names = lambda specs: [re.split(r"[\[<>=!~;]", s, 1)[0].strip().lower() for s in specs]  # noqa: E731
    runtime = names(data["project"]["dependencies"])
    for pkg in ("grpcio", "protobuf"):
        assert pkg in runtime, f"pyproject 主依赖缺 {pkg}（与 requirements 必须同一份口径）"

    dev = names(data["project"]["optional-dependencies"]["dev"])
    assert "grpcio-tools" in dev, f"grpcio-tools 必须留在 dev extra（codegen 用）：{dev}"
    assert not {"grpcio", "protobuf"} & set(dev), (
        "dev 里不许再重复运行时包：两处版本约束迟早漂，而漂的那一处是构建期装的版本")


# ── ② 镜像：两个端口都 EXPOSE ──


def test_dockerfile_exposes_http_and_grpc():
    exposes = [
        line.split("EXPOSE", 1)[1].split("#")[0].split()
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("EXPOSE")
    ]
    assert len(exposes) == 1, f"EXPOSE 只该有一处，实际 {exposes}"
    assert set(exposes[0]) == {"8000", str(EXPECTED_GRPC_PORT)}, (
        f"EXPOSE 应为 8000 与 {EXPECTED_GRPC_PORT}，实际 {sorted(exposes[0])}")


# ── ③ py-api 的探针**不许**挪到 gRPC 端口（§三.6 那句「探针不动」的可执行形态）──


def test_py_api_probes_stay_on_http_health():
    """HTTP 面的 readiness/liveness 必须仍是 `httpGet: /health`。

    为什么值得钉死：把探针改成 tcpSocket:50051（或挪去 ai-rpc 的端口）看着只是换个检查，
    实际改的是**灰度语义**——`40-py-api.yaml` 的 `periodSeconds: 5` 是 Go 侧熔断冷却的引用来源
    （`go-core/internal/aiclient/grpc.go` 的注释逐字指向这一份清单），而 `/health` 变红会让
    Ingress 摘掉 py-api 这个 backend，也就是**把还在用 HTTP 面的用户一起摘掉**。
    gRPC 面自己的可用性归 `50-ai-rpc.yaml` 的探针（R7 换成 grpc health），两边不许互相代表。
    """
    dep = _by_kind(_docs(PY_API_YAML), "Deployment")
    container = dep["spec"]["template"]["spec"]["containers"][0]
    for name in ("readinessProbe", "livenessProbe"):
        probe = container[name]
        assert "httpGet" in probe, f"{name} 不再是 httpGet ⇒ 探针被挪走了：{probe}"
        assert probe["httpGet"]["path"] == "/health", f"{name} 的路径被改了：{probe['httpGet']}"
        assert probe["httpGet"]["port"] == "http", f"{name} 的端口不再指向 HTTP：{probe['httpGet']}"

    svc = _by_kind(_docs(PY_API_YAML), "Service")
    assert [p["port"] for p in svc["spec"]["ports"]] == [8000], (
        "py-api 的 Service 只暴露 HTTP：AI 面的端口属于独立 Deployment（D-R3-2 的改判）")


# ── ④ ai-rpc 清单：端口三处同值、Service 指得对、不出 Ingress ──


def test_ai_rpc_manifest_ports_and_selector():
    docs = _docs(AI_RPC_YAML)
    dep = _by_kind(docs, "Deployment")
    svc = _by_kind(docs, "Service")
    container = dep["spec"]["template"]["spec"]["containers"][0]

    assert container["command"] == ["python", "-m", "backend.ai.rpc.server"], (
        f"入口必须是 server 模块：{container['command']}")
    ports = [p["containerPort"] for p in container["ports"]]
    assert ports == [EXPECTED_GRPC_PORT], f"containerPort 只该有 gRPC 一格：{ports}"

    env = {e["name"]: e.get("value") for e in container.get("env", [])}
    assert env.get("CLOSEN_AI_RPC_PORT") == str(EXPECTED_GRPC_PORT), (
        f"main() 读的就是这一格（server.py:274），与 containerPort 不同值就会监听错端口：{env}")
    assert container["image"] == "ghcr.io/jamesfeng2009/closenai-api:0.1.0", (
        "与 py-api 必须同一个镜像：拆的是进程，不拆「装了什么」")

    assert svc["spec"]["selector"] == dep["spec"]["template"]["metadata"]["labels"], (
        "Service 选择器与 pod 标签不一致 ⇒ ClusterIP 后面永远没 endpoint")
    (sport,) = svc["spec"]["ports"]
    assert (sport["port"], sport["targetPort"]) == (EXPECTED_GRPC_PORT, "grpc"), sport


def test_ai_rpc_grace_period_covers_stream_stop():
    """terminationGracePeriodSeconds 必须**大于** server 里的 `stop(grace=3)`。

    等式反过来（grace < stop 的等待）就是 SIGKILL 掐断正在流式的对话，
    Go 侧读到的是断流而不是 done——那是 R2 四类失败里的 transport 面，属于运维自己造故障。
    """
    from backend.ai.rpc import server as rpc_server

    dep = _by_kind(_docs(AI_RPC_YAML), "Deployment")
    grace = dep["spec"]["template"]["spec"]["terminationGracePeriodSeconds"]
    source = (ROOT / "backend" / "ai" / "rpc" / "server.py").read_text(encoding="utf-8")
    stop_grace = int(re.search(r"await server\.stop\(grace=(\d+)\)", source).group(1))
    assert grace > stop_grace, f"pod 只给 {grace}s，而进程自己要求 {stop_grace}s 收尾"
    assert rpc_server.DEFAULT_PORT == EXPECTED_GRPC_PORT, (
        f"清单写的端口与 server 的默认端口不同值：{rpc_server.DEFAULT_PORT}")


def test_ai_rpc_is_not_exposed_through_ingress():
    """gRPC 只在集群内：Ingress 里不许出现 ai-rpc 这个 backend（`tasks.md:1105`）。

    对外仍只有 Go 的 HTTP/SSE；把 50051 挂上 Ingress 等于把内部契约面变成公网攻击面，
    而本批的 server 没有任何鉴权（proto 侧靠集群内网络边界，mTLS 归 R7）。
    """
    text = INGRESS_YAML.read_text(encoding="utf-8")
    assert "ai-rpc" not in text, "Ingress 里出现了 ai-rpc ⇒ AI 面被对外暴露"
    services = set(re.findall(r"name:\s*(go-api|py-api|ai-rpc)\b", text))
    assert "py-api" in services and "go-api" in services, (
        f"Ingress 的 backend 面变了（本批只该新增 ai-rpc 之外的清单）：{services}")
