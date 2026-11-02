"""KG-26 反思提示库：内容完整性 + 校验器真的会红 + 只有一份数据源。

三条判据的来处：
- **30 条 / 四簇 8-8-7-7 / id 唯一 / en+zh 非空 / function 枚举 / 禁词**：台账 KG-26 的验收原文。
  这批提示会进商店截图和落地页，一条「诊断」措辞就把产品推到医疗边界外（KG-01）。
- **校验器逐条反证**：`validate()` 是内容文件的唯一守门人，它自己必须先证明能被打红。
  只跑「真文件通过」的正向用例，等于没测——把条数改成 29 也不会有人发现。
- **数据源唯一**：`scripts/export_prompts.py` 从同一个 JSON 生成站点草稿，草稿提交进仓、
  由 `--check` 逐字节比对；如果站点里手写了第二份 30 条，「这条提示依据哪条记录」就没有答案了。
  所以 `rp-0` 这个 id 前缀只许出现在内容文件、服务与脚本里。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.routers import reflection_prompts as reflection_prompts_router
from backend.services import prompt_library

ROOT = Path(__file__).resolve().parents[2]
CONTENT = ROOT / "backend" / "content" / "reflection_prompts.json"


def raw() -> dict:
    return json.loads(CONTENT.read_text(encoding="utf-8"))


@pytest.fixture
def app(db):
    """只挂提示库那一个 router，前缀与 `backend/main.py` 同口径（`/api/v1`）。

    故意**不**覆盖 `get_current_user`：这条端点按设计不认人（静态内容、无用户数据），
    一旦有人给它加上 auth 依赖，这里会直接 401 而不是被覆盖掉——下面的 `test_endpoint_is_readable_without_a_token` 就是证人。
    """
    app = FastAPI()
    app.include_router(reflection_prompts_router.router, prefix="/api/v1", tags=["reflections"])
    return app


async def _get(app, headers=None, path="/api/v1/reflection-prompts"):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, headers=headers or {})


# ═══ 内容文件本体 ═══

def test_library_file_passes_its_own_validator():
    assert prompt_library.validate(raw()) == []


def test_thirty_prompts_in_four_clusters_8_8_7_7():
    data = raw()
    assert len(data["prompts"]) == 30
    counts: dict[str, int] = {}
    for p in data["prompts"]:
        counts[p["cluster"]] = counts.get(p["cluster"], 0) + 1
    assert counts == prompt_library.EXPECTED_CLUSTER_COUNTS
    assert [c["key"] for c in data["clusters"]] == list(prompt_library.EXPECTED_CLUSTER_COUNTS)


def test_ids_are_unique_and_stable_shaped():
    ids = [p["id"] for p in raw()["prompts"]]
    assert len(set(ids)) == 30
    assert all(i.startswith("rp-") and len(i) == 6 and i[3:].isdigit() for i in ids)
    # id 连续＝「不许按顺序重排」的可查形式：删一条会立刻在序列上留洞。
    assert ids == [f"rp-{n:03d}" for n in range(1, 31)]


def test_every_prompt_has_en_and_zh_for_all_four_fields():
    for p in raw()["prompts"]:
        for field in prompt_library.LOCALIZED_FIELDS:
            entry = p[field]
            assert set(entry) == {"en", "zh"}, f"{p['id']}.{field} 的语言键不是 en/zh"
            for lang in ("en", "zh"):
                assert entry[lang].strip(), f"{p['id']}.{field}.{lang} 为空"


def test_function_is_one_of_the_three_and_all_three_are_used():
    funcs = {p["function"] for p in raw()["prompts"]}
    assert funcs <= set(prompt_library.FUNCTIONS)
    assert funcs == set(prompt_library.FUNCTIONS), f"三类可关联功能里有一类整批没人用：{funcs}"


def test_questions_are_not_duplicated_within_the_library():
    """同一个问题写两遍＝那一条是凑数的，30 条其实是 29 条。"""
    for lang in ("en", "zh"):
        seen = {}
        for p in raw()["prompts"]:
            key = p["question"][lang].strip().lower()
            assert key not in seen, f"{p['id']} 的问题与 {seen.get(key)} 重复（{lang}）"
            seen[key] = p["id"]


def test_no_banned_medical_or_absolute_wording_in_the_library():
    hits = []
    for path, text in prompt_library._walk_strings(raw()):
        for label, pattern in prompt_library.BANNED_PATTERNS:
            m = pattern.search(text)
            if m:
                hits.append(f"{path} 命中[{label}]「{m.group(0)}」")
    assert hits == []


# ═══ 校验器的反证：每类破坏都必须被它抓到 ═══

def _mutate(**changes) -> dict:
    data = raw()
    data.update(changes)
    return data


def test_validator_catches_wrong_total_and_cluster_count():
    data = raw()
    data["prompts"] = data["prompts"][:-1]
    problems = prompt_library.validate(data)
    assert any("应为 30" in p for p in problems), problems
    assert any("簇 ai_credibility 应有 7 条，实得 6" in p for p in problems), problems


def test_validator_catches_duplicate_ids():
    data = raw()
    data["prompts"][1]["id"] = data["prompts"][0]["id"]
    assert any("id 重复" in p for p in prompt_library.validate(data))


def test_validator_catches_missing_localized_text():
    data = raw()
    data["prompts"][0]["question"]["zh"] = "  "
    problems = prompt_library.validate(data)
    assert any("rp-001.question.zh 为空" in p for p in problems), problems


def test_validator_catches_bad_function_and_unregistered_cluster():
    data = raw()
    data["prompts"][0]["function"] = "diagnose"
    data["prompts"][1]["cluster"] = "therapy"
    problems = prompt_library.validate(data)
    assert any("rp-001 的 function 不在枚举" in p for p in problems), problems
    assert any("rp-002 的 cluster 未登记" in p for p in problems), problems


def test_validator_catches_banned_wording():
    data = raw()
    data["prompts"][0]["title"]["en"] = "A quick self-diagnosis"
    problems = prompt_library.validate(data)
    assert any("rp-001.title.en 命中禁词" in p for p in problems), problems
    data2 = raw()
    data2["prompts"][3]["why"]["zh"] = "这是为了让它诊断你的情绪"
    assert any("命中禁词[zh 诊断/确诊]" in p for p in prompt_library.validate(data2))


def test_validator_catches_missing_cluster_and_empty_version():
    data = _mutate(version="")
    assert any("version" in p for p in prompt_library.validate(data))
    data = _mutate(clusters=raw()["clusters"][:3])
    problems = prompt_library.validate(data)
    assert any("缺簇" in p for p in problems), problems


def test_library_raises_on_broken_content(tmp_path, monkeypatch):
    """内容坏掉＝接口 500，而不是端着一个残缺提示库继续答。"""
    broken = raw()
    broken["prompts"] = broken["prompts"][:2]
    path = tmp_path / "reflection_prompts.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    monkeypatch.setattr(prompt_library, "CONTENT_PATH", path)
    monkeypatch.setattr(prompt_library, "_cache", None)
    with pytest.raises(prompt_library.PromptLibraryError) as exc:
        prompt_library.library()
    assert "应为 30" in str(exc.value)
    monkeypatch.setattr(prompt_library, "_cache", None)


# ═══ 语言回退口径 ═══

@pytest.mark.parametrize(
    "requested,used,fallback",
    [
        ("en", "en", False),
        ("zh", "zh", False),
        ("zh-CN", "zh", False),
        ("fr", "en", True),
        ("id", "en", True),
        (None, "en", False),
    ],
)
def test_resolve_language(requested, used, fallback):
    assert prompt_library.resolve_language(requested) == (used, fallback)


def test_untranslated_language_stays_marked_as_fallback_in_payload():
    payload = prompt_library.list_prompts(language="th")
    assert payload["language_used"] == "en"
    assert payload["fallback"] is True
    zh = prompt_library.list_prompts(language="zh")
    assert zh["language_used"] == "zh" and zh["fallback"] is False
    # 回退不许悄悄换字：同一条 id 的标题在两种请求下必须真的不同。
    first = next(p for p in payload["prompts"] if p["id"] == "rp-001")
    zh_first = next(p for p in zh["prompts"] if p["id"] == "rp-001")
    assert first["title"] != zh_first["title"]


def test_list_prompts_returns_flat_strings_not_language_objects():
    payload = prompt_library.list_prompts()
    assert payload["total"] == 30
    for p in payload["prompts"]:
        for field in prompt_library.LOCALIZED_FIELDS:
            assert isinstance(p[field], str), f"{p['id']}.{field} 没被本地化取平"
        assert set(p) == {
            "id", "cluster", "function", "title", "question", "why", "follow_up"
        }
    assert [c["count"] for c in payload["clusters"]] == [8, 8, 7, 7]


def test_cluster_filter_narrows_prompts_but_keeps_all_cluster_counts():
    """切簇 tab 时其余簇的计数不许变 0，否则前端的 tab 会自己塌掉。"""
    payload = prompt_library.list_prompts(cluster="action_outcome")
    assert payload["total"] == 7
    assert {p["cluster"] for p in payload["prompts"]} == {"action_outcome"}
    assert [c["count"] for c in payload["clusters"]] == [8, 8, 7, 7]


def test_unregistered_cluster_returns_empty_not_an_error():
    payload = prompt_library.list_prompts(cluster="legacy_cluster")
    assert payload["total"] == 0 and payload["prompts"] == []
    assert len(payload["clusters"]) == 4


# ═══ 端点 ═══

async def test_endpoint_returns_wrapped_success(app):
    resp = await _get(app)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    data = body["data"]
    assert data["total"] == 30
    assert data["version"] == raw()["version"]
    assert data["language_used"] == "en"


async def test_endpoint_honours_x_language_header(app):
    resp = await _get(app, headers={"X-Language": "zh"})
    data = resp.json()["data"]
    assert data["language_used"] == "zh" and data["fallback"] is False
    assert data["prompts"][0]["question"].startswith("写下今天")


async def test_endpoint_marks_untranslated_language_as_fallback(app):
    """`de`／`id` 这类设备语言拿到的是英文正文，但响应必须自己说清楚。

    静默回退＝用户在界面上看到「没翻译的英文」却以为是加载坏了；
    `fallback` 这一格是前端那条说明的唯一依据（`showsFallbackNotice`）。
    """
    data = (await _get(app, headers={"X-Language": "de"})).json()["data"]
    assert data["fallback"] is True and data["language_used"] == "en"
    data2 = (await _get(app, headers={"X-Language": "en"})).json()["data"]
    assert data2["language_used"] == "en" and data2["fallback"] is False


async def test_endpoint_is_readable_without_a_token(app):
    """静态内容不认人：游客先看得见提示，才谈得上注册再存。

    fixture 故意没覆盖 `get_current_user`，所以这条同时是反向证人——
    有人给这条路由加上 auth 依赖时，它会 401 而不是被覆盖掉悄悄通过。
    """
    resp = await _get(app)
    assert resp.status_code == 200, resp.text
    assert resp.json()["code"] == 200


async def test_cluster_param_narrows_prompts_but_keeps_full_library_counts(app):
    """切簇只裁 `prompts`，`clusters[].count` 仍按全库给（tab 徽章不许跟着塌成 0）。"""
    data = (await _get(app, path="/api/v1/reflection-prompts?cluster=action_outcome")).json()["data"]
    assert data["total"] == prompt_library.EXPECTED_CLUSTER_COUNTS["action_outcome"]
    assert {p["cluster"] for p in data["prompts"]} == {"action_outcome"}
    assert [c["count"] for c in data["clusters"]] == [
        prompt_library.EXPECTED_CLUSTER_COUNTS[c["key"]] for c in data["clusters"]
    ]
    assert sum(c["count"] for c in data["clusters"]) == prompt_library.EXPECTED_TOTAL


# ═══ 数据源唯一（站点不许手写第二份） ═══

def test_prompt_ids_live_only_in_the_library_and_its_consumers():
    """提示 id 只许在库里和它的直接消费者里出现。

    站点／App 里硬编码一份 30 条的副本＝两套文案，「这条提示依据哪条记录」失去答案。
    扫描面按目录点名（同 `test_absolute_memory_claims.py` 的口径），不做全仓 rglob：
    前者漏一个目录会红，后者慢且会把 node_modules 的噪音算进来。
    """
    allowed = {
        ROOT / "backend" / "content" / "reflection_prompts.json",
        ROOT / "backend" / "services" / "prompt_library.py",
        ROOT / "backend" / "routers" / "reflection.py",
        ROOT / "backend" / "models" / "schemas.py",
        ROOT / "scripts" / "export_prompts.py",
        Path(__file__),
    }
    faces = [ROOT / "backend", ROOT / "frontend" / "src", ROOT / "web", ROOT / "scripts"]
    offenders = []
    for face in faces:
        for p in face.rglob("*"):
            if not p.is_file() or p in allowed or "__pycache__" in p.parts:
                continue
            if p.suffix not in {".py", ".ts", ".tsx", ".json", ".html"}:
                continue
            # 测试里当然会出现 rp-0xx（那是夹具）；这条闸管的是**可发布面**不许有副本。
            if "/tests/" in str(p) or "__tests__" in p.parts:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            if '"rp-0' in text or "'rp-0" in text or "rp-001" in text:
                offenders.append(str(p.relative_to(ROOT)))
    assert offenders == [], f"提示 id 在库外被硬编码：{offenders}"


# ═══ 站点草稿与库同批（§6.1.5 的可执行版本） ═══

DRAFTS = {
    "en": ROOT / "docs" / "content" / "drafts" / "journal-prompts.en.md",
    "zh": ROOT / "docs" / "content" / "drafts" / "journal-prompts.zh.md",
}


def test_site_drafts_exist_with_all_thirty_prompts():
    """草稿存在且 30 条齐全——少几条不是「页面短一点」，是站点与库分家。"""
    for language, path in DRAFTS.items():
        assert path.exists(), f"缺 {path.relative_to(ROOT)}（跑 scripts/export_prompts.py）"
        text = path.read_text(encoding="utf-8")
        ids = [ln.split(" ")[1] for ln in text.splitlines() if ln.startswith("### rp-")]
        assert len(ids) == prompt_library.EXPECTED_TOTAL, (
            f"{language} 草稿里 {len(ids)} 条，库里 {prompt_library.EXPECTED_TOTAL} 条"
        )
        assert len(set(ids)) == len(ids), f"{language} 草稿里有重复 id"


def test_site_drafts_are_regenerated_from_the_library_file():
    """草稿必须逐字节等于「现在这份 JSON 生成出来的样子」（`export_prompts.py --check`）。

    判据跑脚本本体，不在测试里重抄一遍渲染逻辑：重抄＝第二真源，
    改了 JSON 忘了重跑才会红，改了渲染而草稿没跟上也会红。
    """
    proc = subprocess.run(
        [sys.executable, "scripts/export_prompts.py", "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        "站点草稿与提示库不一致，重跑 .venv/bin/python scripts/export_prompts.py\n"
        f"{proc.stdout}{proc.stderr}"
    )
    for language, path in DRAFTS.items():
        assert f"journal-prompts.{language}.md 一致" in proc.stdout, (
            f"--check 没有真的检查 {language} 这一份（输出：{proc.stdout}）"
        )
