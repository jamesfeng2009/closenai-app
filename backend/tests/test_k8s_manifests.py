"""N1 部署纵切的验收测试（tasks.md N1 剩余项：k3s 起 go-api + py-api，Ingress 灰度 0%）。

判据分两层：
1. 纯清单结构断言——无集群也能跑，CI 必需。它守的是「灰度 0%」「端口/探针/标签一致」
   「环境变量名跨语言不漂移」「清单里不出现明文密钥」这几条会让上线炸掉的不变量。
2. `kubectl apply --dry-run=server`——有机读 kubeconfig 时跑，把清单交给真实 API server
   校验 schema。本机 OrbStack 无 IngressClass，因此 Ingress 只做 dry-run 不做端到端。

真实共享集群（4c8G k3s）的 `kubectl apply` 不在这里发生：那是需单独授权的动作。
"""

from __future__ import annotations

import re
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
    # Go 的 /healthz 是进程就绪探针，不在 98 条契约登记内；Python 的 /health 是契约端点。
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
    """灰度 0% 的证据 = Ingress 里不存在任何指向 go-api 的 backend。"""
    ing = _one("Ingress", "closenai")
    backends = []
    for rule in ing["spec"]["rules"]:
        for path in rule["http"]["paths"]:
            assert path["pathType"] in ("Prefix", "Exact")
            backends.append(path["backend"]["service"]["name"])
    assert backends, "Ingress 没有任何 path 规则"
    assert set(backends) == {"py-api"}, f"初始状态必须全指 py-api，实际 {sorted(set(backends))}"
    # 切流的前置：go-api 的 Service 必须已经存在，否则改 backend 名会当场 503。
    _one("Service", "go-api")


def test_contract_paths_are_routed():
    """契约面只有两类入口：/api/v1/* 与 app 级 /health（N0 计数的 98 = 97 + 1）。"""
    ing = _one("Ingress", "closenai")
    paths = {
        p["path"]
        for rule in ing["spec"]["rules"]
        for p in rule["http"]["paths"]
    }
    assert paths == {"/api/v1", "/health"}, paths


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
        for key in ("DATABASE_URL", "JWT_SECRET", "CRON_SECRET", "RESEND_API_KEY"):
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
