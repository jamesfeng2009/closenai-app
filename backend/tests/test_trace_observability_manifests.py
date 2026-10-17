"""批 T-F · 闸 G-F1：60-observability 清单的静态断言（不上刀，只判文件）。

判据形状沿 N1 的 `test_k8s_manifests.py`：纯结构断言、无集群也能跑。这里刻意**不**把
子目录塞进那份闸的全局 glob——N1 的资源/端口/Dockerfile 判据钉的是业务运行时，
把 grafana 镜像卷进去只会逼人给采集器写 Dockerfile。本文件自己起一套完整扫描，
判的四件事：

1. 采集面＝三个业务 Deployment 各一条静态 scrape（多一个 Deployment 必须在这份文件里显形）；
2. Go 行走 json pipeline、Python 行走 regex，**两侧都把 trace_id 提成索引标签**，
   且 regex 里的形状字面量必须与 `core/logging.TRACE_SHAPE`（=T-8 消毒形状）逐字同源；
3. 卫生格：镜像钉 tag 且 loki/promtail 同发布线、hostPath 只读、无明文密钥、全量落 closenai ns；
4. `10-config.yaml` 的 T-F 口径注释在场（注释是「采集面不新增运行时键」这个裁决的载体，
   删注释＝有人打算把开关塞回 ConfigMap，先在这里红）。

变异靶见 `scripts/mutate_trace_tf_gates.py`（F1–F6 各打红一次）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from backend.core.logging import TRACE_SHAPE

ROOT = Path(__file__).resolve().parents[2]
OBS_DIR = ROOT / "deploy" / "k8s" / "60-observability"
CONFIG_YAML = ROOT / "deploy" / "k8s" / "10-config.yaml"
NAMESPACE = "closenai"

JOBS = {
    "closenai-go-api": "go-api",
    "closenai-py-api": "py-api",
    "closenai-ai-rpc": "ai-rpc",
}


def _docs_in(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]


def _all_docs() -> list[dict]:
    files = sorted(OBS_DIR.glob("*.yaml"))
    assert files, f"{OBS_DIR} 下没有任何清单——本闸不许扫空集永远绿"
    out: list[dict] = []
    for f in files:
        out.extend(_docs_in(f))
    return out


def _one(kind: str, name: str) -> dict:
    matches = [d for d in _all_docs() if d.get("kind") == kind and d["metadata"]["name"] == name]
    assert len(matches) == 1, f"期望恰好 1 个 {kind}/{name}，实际 {len(matches)}"
    return matches[0]


def _cfg(kind: str, name: str, key: str) -> dict:
    cm = _one(kind, name)
    return yaml.safe_load(cm["data"][key])


def _promtail() -> dict:
    return _cfg("ConfigMap", "promtail-config", "promtail.yaml")


def _loki() -> dict:
    return _cfg("ConfigMap", "loki-config", "loki.yaml")


def _job(name: str) -> dict:
    jobs = {j["job_name"]: j for j in _promtail()["scrape_configs"]}
    assert name in jobs, f"promtail 少了 {name} 这条 scrape"
    return jobs[name]


def _stages(job: dict) -> list[dict]:
    return job.get("pipeline_stages") or []


def _stage(job: dict, kind: str) -> dict | None:
    for s in _stages(job):
        if kind in s:
            return s[kind]
    return None


def test_manifests_parse_and_are_namespaced():
    docs = _all_docs()
    kinds = {d["kind"] for d in docs}
    assert {"ConfigMap", "Deployment", "DaemonSet", "Service",
            "PersistentVolumeClaim"} <= kinds, kinds
    for doc in docs:
        assert doc["metadata"].get("namespace") == NAMESPACE, (
            f"{doc['kind']}/{doc['metadata']['name']} 未落在 {NAMESPACE}"
        )


def test_promtail_pushes_into_loki_service():
    svc = _one("Service", "loki")
    assert svc["spec"]["ports"][0]["port"] == 3100
    dep = _one("Deployment", "loki")
    assert svc["spec"]["selector"].items() <= (
        dep["spec"]["template"]["metadata"]["labels"].items())
    url = _promtail()["clients"][0]["url"]
    assert url == f"http://loki.{NAMESPACE}.svc.cluster.local:3100/loki/api/v1/push", url


def test_three_jobs_cover_exactly_three_deployments():
    jobs = {j["job_name"] for j in _promtail()["scrape_configs"]}
    assert jobs == set(JOBS), f"采集面漂移：{jobs} ≠ {set(JOBS)}"
    for job_name, app in JOBS.items():
        labels = _job(job_name)["static_configs"][0]["labels"]
        assert labels["__path__"] == f"/var/log/pods/{NAMESPACE}_{app}-*/*/*.log", (
            f"{job_name}: __path__ 通配没指到 {app} 的 Pod 目录"
        )
        assert labels["app"] == app


def test_go_job_indexes_trace_via_json_pipeline():
    job = _job("closenai-go-api")
    js = _stage(job, "json")
    assert js, "go-api 的 slog JSON 行没有 json 阶段——trace_id 进不了索引"
    assert "trace_id" in js["expressions"], js
    assert js["expressions"]["trace_id"] == "trace_id"
    labels = _stage(job, "labels")
    assert labels and "trace_id" in labels, "json 抽了但没提标签"
    assert "user_id" in labels, "T-C 的双字段口径在采集面也得齐"


def test_python_jobs_index_trace_with_the_shared_shape():
    """regex 里的形状必须与 T-8 消毒形状**逐字同源**：漂移＝一边收得到、一边查不到。"""
    for job_name in ("closenai-py-api", "closenai-ai-rpc"):
        job = _job(job_name)
        rx = _stage(job, "regex")
        assert rx, f"{job_name}: 文本行没有 regex 阶段"
        assert TRACE_SHAPE.pattern in rx["expression"], (
            f"{job_name}: regex 形状与 core/logging.TRACE_SHAPE 漂移：{rx['expression']}"
        )
        assert "(?P<trace_id>" in rx["expression"]
        labels = _stage(job, "labels")
        assert labels and "trace_id" in labels, f"{job_name}: trace_id 没提成索引标签"


def test_images_pinned_on_one_release_line():
    images = {
        "loki": _one("Deployment", "loki")["spec"]["template"]["spec"]["containers"][0]["image"],
        "promtail": _one("DaemonSet", "promtail")["spec"]["template"]["spec"]["containers"][0]["image"],
    }
    tags = {}
    for name, image in images.items():
        m = re.fullmatch(rf"grafana/{name}:(\d[\d.]*)", image)
        assert m, f"{name} 镜像必须是 grafana/{name}:<钉死版本>，实际 {image}"
        tags[name] = m.group(1)
    assert tags["loki"] == tags["promtail"], f"采集器与存储不同发布线：{tags}"


def test_host_path_is_read_only_and_positions_writable():
    pod = _one("DaemonSet", "promtail")["spec"]["template"]["spec"]
    mounts = {m["name"]: m for m in pod["containers"][0]["volumeMounts"]}
    logs = mounts["pods-logs"]
    assert logs["mountPath"] == "/var/log/pods"
    assert logs.get("readOnly") is True, "采集面挂载必须只读——promtail 不许写节点日志目录"
    vols = {v["name"]: v for v in pod["volumes"]}
    assert vols["pods-logs"]["hostPath"]["path"] == "/var/log/pods"
    assert "emptyDir" in vols["run"], "positions 落 emptyDir（跨重启保位点属运维改判，不是默认）"


def test_loki_is_single_binary_filesystem_with_retention():
    loki = _loki()
    assert loki["auth_enabled"] is False, (
        "多租户没接线；开 auth 的前提是 3100 只留在 ClusterIP 内且本清单不接 Ingress"
    )
    assert loki["common"]["storage"]["filesystem"], "单节点没有对象存储，filesystem 是唯一形态"
    assert loki["limits_config"]["retention_period"] == "168h", (
        "trace_id 是高基数标签，检索窗口由这一格封顶；改动要连带改文件头注释"
    )
    assert loki["compactor"]["retention_enabled"] is True, "没有 compactor 留存承诺就是空头"
    sc = loki["schema_config"]["configs"][0]
    assert sc["store"] == "tsdb" and sc["object_store"] == "filesystem"


def test_no_plaintext_secrets_in_observability_dir():
    # 白名单点名列（[[feedback-env-forensics-whitelist-keys]]），不做前缀通配
    for path in sorted(OBS_DIR.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        assert "stringData" not in text, path.name
        for key in ("DATABASE_URL", "JWT_SECRET", "CRON_SECRET", "RESEND_API_KEY",
                    "POSTHOG_API_KEY"):
            assert not re.search(rf"{key}\s*[:=]\s*\S", text), f"{path.name} 内含 {key} 明文"


def test_config_map_carries_the_cutover_note():
    """`10-config.yaml` 的 T-F 口径注释是「采集面不进运行时键」这条裁决的载体。

    正向证人：注释里必须同时点出 60-observability 与 trace_id——
    只剩一个词都说明注释被改薄成了目录指针，裁决本体丢了。
    """
    text = CONFIG_YAML.read_text(encoding="utf-8")
    assert "60-observability" in text and "trace_id" in text, (
        "10-config.yaml 丢了批 T-F 口径注释（或有人开始往里塞日志开关）"
    )
    assert "CLOSEN_LOG" not in text and "LOKI_URL" not in text, (
        "ConfigMap 里出现了日志开关键——采集面属部署配置，不是运行时开关"
    )
    docs = [d for d in yaml.safe_load_all(text) if d]
    assert any(d.get("kind") == "ConfigMap" for d in docs)


@pytest.mark.parametrize("path", sorted(OBS_DIR.glob("*.yaml")),
                         ids=lambda p: p.name)
def test_every_file_parses(path: Path):
    docs = _docs_in(path)
    assert docs, f"{path.name} 解析出 0 个文档"
