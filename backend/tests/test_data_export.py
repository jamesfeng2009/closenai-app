"""T1-01 通用数据导出闸（Baseline §2.2-6「带走」／§3 P0／§4.2 Free 行／§14.1 隔离）。

工作单点名的三条判据是 `test_export_targets_subset_of_purge`、
`test_export_cross_user_isolation`、`test_export_excludes_secrets`，其余是验收那句
「zip 可下载、可打开、含 memories/chat_messages/mood_checkins/canonical 表；
删除后产物与 job 行都被清」的取证面。分五组：

1. 靶的派生（导出表集合 ⊆ 注销表集合，且新表一定会进导出）；
2. API 形状（申请、限频、跨用户隔离、下载鉴权）；
3. 产物内容（zip 打得开、NDJSON 逐行可解析、凭据与向量不进包）；
4. 队列语义（超限终态、3 次失败、卡死回收、到期删文件）；
5. 注销与调度面（盘上残留为零、cron 排程与密钥）。
"""
import io
import json
import zipfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from backend.config import settings
from backend.core.auth import get_current_user
from backend.core.errors import AppException, ErrorCode
from backend.core.middleware import register_exception_handlers
from backend.core.time_utils import utc_now
from backend.database import get_db
from backend.models.auth import RefreshToken
from backend.models.canonical import Insight
from backend.models.chat import ChatMessage
from backend.models.export import DataExportJob
from backend.models.memory import Memory
from backend.models.mood import MoodCheckin
from backend.models.subscription import Receipt
from backend.repositories import export_repository
from backend.routers import cron as cron_router
from backend.routers import user as user_router
from backend.services.export_service import (
    EXCLUDED_COLUMNS,
    EXPORT_ERROR_BUILD_FAILED,
    EXPORT_ERROR_STALE,
    EXPORT_ERROR_TOO_LARGE,
    EXPORT_EXCLUDE_REASONS,
    EXPORT_SCOPES,
    export_service,
    export_targets,
    resolve_artifact,
)
from backend.services.user_service import PURGE_TARGETS, user_service

ROOT = Path(__file__).resolve().parents[2]

# 明文哨兵：出现在产物字节里就是泄露，比「断言表名不在 namelist」更硬
SECRET_REFRESH = "SECRET-REFRESH-TOKEN-HASH-DO-NOT-EXPORT"
SECRET_RECEIPT = "SECRET-RECEIPT-RAW-BASE64-DO-NOT-EXPORT"
SECRET_EMBEDDING = 9.99  # memories.embedding 的每维取值，导出里不该有 9.99

PURGE_KEYS = {key for _model, key in PURGE_TARGETS}
EXPORT_KEYS = {key for _model, key in export_targets()}


@pytest.fixture
def export_root(tmp_path, monkeypatch):
    """把产物根目录指到 tmp：默认目录是仓库里的 data/exports，测试不许往那儿写。"""
    root = tmp_path / "exports"
    monkeypatch.setattr(settings, "export_dir", str(root))
    return root


@pytest.fixture
def api(db, export_root):
    """挂 user＋cron 两个 router；当前用户放在可变 holder 里，便于跨用户取证。"""
    holder = {"user_id": "u1"}
    app = FastAPI()
    app.include_router(user_router.router, prefix="/api/v1", tags=["user"])
    app.include_router(cron_router.router, prefix="/api/v1", tags=["cron"])
    register_exception_handlers(app)

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: holder["user_id"]
    return SimpleNamespace(app=app, holder=holder)


async def _request(api, method, path, payload=None, headers=None):
    transport = ASGITransport(app=api.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, json=payload, headers=headers)


async def seed(db, user_id: str) -> None:
    """造四类可读数据＋两类不得导出的数据（凭据/收据）＋一个派生向量列。"""
    db.add_all(
        [
            ChatMessage(
                id=f"chat-{user_id}", user_id=user_id, role="user",
                content="今天在会上被否了，我一直没敢说第二次",
            ),
            Memory(
                id=f"mem-{user_id}", user_id=user_id, content="我回避冲突",
                embedding=[SECRET_EMBEDDING] * 512,
            ),
            MoodCheckin(id=f"mood-{user_id}", user_id=user_id, mood_emoji="😔", mood_label="低落"),
            Insight(
                id=f"ins-{user_id}", user_id=user_id, personal_model_id=f"pm-{user_id}",
                statement="被否定后我会沉默，而不是辩驳", claim_type="pattern",
            ),
            RefreshToken(
                id=f"rt-{user_id}", user_id=user_id, token_hash=SECRET_REFRESH,
                expires_at=utc_now() + timedelta(days=30),
            ),
            Receipt(
                id=f"rc-{user_id}", user_id=user_id, product_id="pro_monthly",
                receipt_data=SECRET_RECEIPT, status="valid",
            ),
        ]
    )
    await db.flush()


async def build_ready_job(db, user_id: str = "u1") -> DataExportJob:
    """走完「申请 → cron 生成」，返回状态为 ready 的行（下游用例复用这一段公共前缀）。"""
    await export_service.request_export(db, user_id)
    await export_service.process_jobs(db)
    job_id = (await export_repository.ids_by_status(db, ("ready",)))[0]
    return await export_repository.get(db, job_id)


def read_zip(path: Path) -> dict[str, str]:
    """把产物解成 {包内路径: 文本}；打不开就在这里红，而不是让下载用例各炸一次。"""
    with zipfile.ZipFile(path) as zf:
        assert zf.testzip() is None, "zip 已损坏"
        return {n: zf.read(n).decode("utf-8") for n in zf.namelist()}


def ndjson(text: str) -> list[dict]:
    """NDJSON 逐行解析：任何一行不是合法 JSON 对象都在这里红。"""
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        assert isinstance(value, dict), f"NDJSON 行不是对象：{line[:80]}"
        rows.append(value)
    return rows


# ── 1. 靶的派生 ──

def test_export_targets_subset_of_purge():
    """工作单点名判据①：导出靶必须 ⊆ 注销靶。

    反向不成立（凭据与收据要删、但不给导出）。这条一旦不等，说明有人手抄了一份清单，
    于是「新表登记进 purge 就一定进导出」这条继承保证当场失效。
    """
    assert EXPORT_KEYS < PURGE_KEYS, (
        f"导出靶越出注销靶：{sorted(EXPORT_KEYS - PURGE_KEYS)}"
    )
    assert PURGE_KEYS - EXPORT_KEYS == set(EXPORT_EXCLUDE_REASONS) & PURGE_KEYS, (
        "导出靶与注销靶的差集必须正好等于带理由的排除表"
    )


def test_every_exclusion_has_a_reason_and_a_live_table():
    """排除是合规判断，不是省事：每条要有理由，且指向的表今天还得存在（防清单腐烂）。"""
    from backend.database import Base

    for key, reason in EXPORT_EXCLUDE_REASONS.items():
        assert reason.strip(), f"排除 {key} 没写理由"
        assert key in Base.metadata.tables, f"排除了不存在的表 {key}，理由该删"
    for table, columns in EXCLUDED_COLUMNS.items():
        assert table in EXPORT_KEYS, f"列级排除指向了不导出的表 {table}"
        live = {c.name for c in Base.metadata.tables[table].columns}
        assert set(columns) <= live, f"{table} 的列级排除里有已删的列"
        for column, reason in columns.items():
            assert reason.strip(), f"{table}.{column} 的排除没写理由"


def test_new_user_scoped_table_would_be_exported():
    """正向证人：按 `Base.metadata` 现推「带 user_id 的表」，没进导出的必须都有排除理由。

    这条是派生设计的卖点：以后加一张用户表，只要它进了注销清单就会自动进导出；
    若它连注销清单都没进，`test_purge_covers_every_user_scoped_table` 先红。

    `PURGE_EXEMPT_TABLES` 也列在这里，是因为豁免表按派生规则**天然**不在导出靶里
    （导出靶＝PURGE_TARGETS − 排除清单），而它不进导出的理由由
    `docs/data-lineage.md` §二 那一行与 `test_doc_matches_code_both_ways` 的列核负责，
    不该由这张排除表兼职。
    """
    from backend.database import Base
    from backend.services.user_service import PURGE_EXEMPT_TABLES

    user_scoped = {t.name for t in Base.metadata.tables.values() if "user_id" in t.columns}
    not_exported = sorted(user_scoped - EXPORT_KEYS)
    assert set(not_exported) <= set(EXPORT_EXCLUDE_REASONS) | set(PURGE_EXEMPT_TABLES), (
        f"以下用户表既没进导出、也没有排除理由：{not_exported}"
    )


def test_named_readables_are_in_targets():
    """验收点名的可读表必须在靶里（含 canonical 一张，作为 D-5 第二消费面的证人）。"""
    for key in ("users", "chat_messages", "memories", "mood_checkins", "insights"):
        assert key in EXPORT_KEYS, f"{key} 没进导出靶，用户带不走它"


# ── 2. API 形状 ──

async def test_request_returns_queued_job(db, make_user, export_root):
    await make_user("u1")
    payload = await export_service.request_export(db, "u1")
    assert payload["status"] == "queued"
    assert payload["download_ready"] is False
    assert payload["file_name"] is None
    assert payload["scope"] in EXPORT_SCOPES


async def test_unknown_scope_is_rejected(db, make_user, export_root):
    await make_user("u1")
    with pytest.raises(AppException) as exc:
        await export_service.request_export(db, "u1", "last_30_days")
    assert exc.value.code == int(ErrorCode.INVALID_PARAMS)


async def test_request_rate_limited_per_database(db, make_user, api, export_root):
    """判据落在库里：进程内限流重启即清零，管不住「一个人反复把全库拷一遍」。"""
    await make_user("u1")
    first = await _request(api, "POST", "/api/v1/user/export", {"scope": "all"})
    assert first.status_code == 200, first.text
    second = await _request(api, "POST", "/api/v1/user/export", {"scope": "all"})
    assert second.status_code == 429, second.text
    assert second.json()["code"] == int(ErrorCode.EXPORT_REQUEST_TOO_FREQUENT)
    assert second.json()["data"]["status"] == "queued"


async def test_failed_job_does_not_consume_the_window(db, make_user, export_root):
    """失败/过期不占额度：否则用户得为一个没拿到手的文件再等 24 小时。"""
    await make_user("u1")
    await export_service.request_export(db, "u1")
    job = await export_repository.get(db, (await export_repository.ids_by_status(db, ("queued",)))[0])
    await export_repository.finish(
        db, job.id, status="failed", now=utc_now(), error_code=EXPORT_ERROR_BUILD_FAILED,
        attempts=job.attempts + 1,
    )
    again = await export_service.request_export(db, "u1")
    assert again["status"] == "queued"
    assert again["job_id"] != job.id


async def test_export_cross_user_isolation(db, make_user, api, export_root):
    """工作单点名判据②（§14.1）：别人的 job_id 与不存在的 job_id 同形 404。"""
    await make_user("u1")
    await make_user("u2")
    mine = await export_service.request_export(db, "u1")
    api.holder["user_id"] = "u2"
    got = await _request(api, "GET", f"/api/v1/user/export/{mine['job_id']}")
    assert got.status_code == 404, got.text
    assert got.json()["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)
    download = await _request(api, "GET", f"/api/v1/user/export/{mine['job_id']}/download")
    assert download.status_code == 404, download.text
    assert download.json()["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)
    missing = await _request(api, "GET", "/api/v1/user/export/does-not-exist")
    assert missing.status_code == 404, missing.text
    assert missing.json() == got.json(), "存在但不属于你 / 根本不存在 必须同形，否则 job_id 可枚举"


async def test_list_returns_only_own_jobs(db, make_user, api, export_root):
    await make_user("u1")
    await make_user("u2")
    await export_service.request_export(db, "u1")
    await export_service.request_export(db, "u2")
    api.holder["user_id"] = "u1"
    resp = await _request(api, "GET", "/api/v1/user/export")
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]["jobs"]
    assert len(rows) == 1 and rows[0]["job_id"], rows
    assert all(r["status"] for r in rows)


async def test_list_carries_the_retention_window_from_settings(db, make_user, api, export_root):
    """§14.2 要界面说清「保存周期」，那个数字只能出自 `settings.export_retention_days`。

    前端自己写一份「7 天」＝第二真源；改配置那天，界面开始对用户说谎。
    """
    await make_user("u1")
    resp = await _request(api, "GET", "/api/v1/user/export")
    assert resp.json()["data"]["retention_days"] == settings.export_retention_days


async def test_download_not_ready_is_conflict(db, make_user, api, export_root):
    await make_user("u1")
    job = await export_service.request_export(db, "u1")
    resp = await _request(api, "GET", f"/api/v1/user/export/{job['job_id']}/download")
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == int(ErrorCode.EXPORT_NOT_READY)


async def test_download_returns_zip_bytes_with_filename(db, make_user, api, export_root):
    """验收那句「可下载」：走 HTTP 拿到 200＋application/zip＋逐字文件名。"""
    await make_user("u1")
    await seed(db, "u1")
    job = await build_ready_job(db)
    resp = await _request(api, "GET", f"/api/v1/user/export/{job.id}/download")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/zip"), resp.headers
    disp = resp.headers["content-disposition"]
    assert f"filename*=UTF-8''closenai-export-{job.id}.zip" in disp or (
        f'filename="closenai-export-{job.id}.zip"' in disp
    ), disp
    assert zipfile.is_zipfile(io.BytesIO(resp.content))
    status = await _request(api, "GET", f"/api/v1/user/export/{job.id}")
    assert status.json()["data"]["download_ready"] is True
    assert status.json()["data"]["download_path"] == f"/api/v1/user/export/{job.id}/download"


# ── 3. 产物内容 ──

async def test_artifact_opens_and_is_readable_ndjson(db, make_user, export_root):
    await make_user("u1")
    await seed(db, "u1")
    job = await build_ready_job(db)
    files = read_zip(export_root / job.file_path)

    assert set(job.file_path.split("/")) == {"u1", f"{job.id}.zip"}, "产物必须落在 <user_id>/<job_id>.zip"
    assert "manifest.json" in files and "tables/memories.ndjson" in files
    memories = ndjson(files["tables/memories.ndjson"])
    assert memories[0]["content"] == "我回避冲突"
    chats = ndjson(files["tables/chat_messages.ndjson"])
    assert chats[0]["content"].startswith("今天在会上被否了")
    assert ndjson(files["tables/users.ndjson"])[0]["id"] == "u1"
    # canonical 表也在包里（D-5 例外的第二个消费面，读侧）
    assert ndjson(files["tables/insights.ndjson"])[0]["statement"] == "被否定后我会沉默，而不是辩驳"
    # 中文逐字保留：ensure_ascii=False，否则档案包变成一堆 \uXXXX
    assert "\\u" not in files["tables/memories.ndjson"]


async def test_export_excludes_secrets(db, make_user, export_root):
    """工作单点名判据③（§14.1 禁止导出物含 access token/支付凭证）：按字节取证。"""
    await make_user("u1")
    await seed(db, "u1")
    job = await build_ready_job(db)
    files = read_zip(export_root / job.file_path)
    blob = "\n".join(files.values())

    assert "tables/refresh_tokens.ndjson" not in files, files.keys()
    assert "tables/receipts.ndjson" not in files, files.keys()
    assert SECRET_REFRESH not in blob, "登录凭据进了可下载、可转发的归档"
    assert SECRET_RECEIPT not in blob, "支付收据原文进了归档"
    assert str(SECRET_EMBEDDING) not in files["tables/memories.ndjson"], "派生向量列没排掉"

    manifest = json.loads(files["manifest.json"])
    excluded = {e["table"] for e in manifest["excluded_tables"]}
    assert {"refresh_tokens", "receipts"} <= excluded
    assert all(e["reason"].strip() for e in manifest["excluded_tables"])
    assert {c["column"] for c in manifest["excluded_columns"]} == {"embedding"}


async def test_manifest_describes_the_package(db, make_user, export_root):
    """§14.2「说明数据范围、保存周期」：包自己要说得清包里有什么、留多久。"""
    await make_user("u1")
    await seed(db, "u1")
    job = await build_ready_job(db)
    manifest = json.loads(read_zip(export_root / job.file_path)["manifest.json"])
    assert manifest["job_id"] == job.id and manifest["scope"] == "all"
    assert manifest["retention_days"] == settings.export_retention_days
    assert manifest["total_rows"] == sum(t["rows"] for t in manifest["tables"])
    assert {t["table"] for t in manifest["tables"]} == EXPORT_KEYS
    assert manifest["format"].startswith("ndjson-per-table")


async def test_other_users_rows_are_not_in_the_package(db, make_user, export_root):
    """§14.1 的产物面隔离：包里的每一行 user_id 都只能是申请人。"""
    await make_user("u1")
    await make_user("u2")
    await seed(db, "u1")
    await seed(db, "u2")
    job = await build_ready_job(db, "u1")
    files = read_zip(export_root / job.file_path)
    assert {r["id"] for r in ndjson(files["tables/users.ndjson"])} == {"u1"}
    offenders = []
    for name, text in files.items():
        if not name.startswith("tables/") or name == "tables/users.ndjson":
            continue
        for row in ndjson(text):
            if "user_id" in row and row["user_id"] != "u1":
                offenders.append(f"{name}:{row['user_id']}")
    assert not offenders, f"产物里混进了别人的行：{offenders}"


async def test_artifact_path_traversal_is_refused(export_root):
    """下载端点不能变成任意文件读：越出产物根的写法一律拒绝（判据放在读取处）。"""
    with pytest.raises(AppException) as exc:
        resolve_artifact("../../etc/passwd")
    assert exc.value.code == int(ErrorCode.EXPORT_ARTIFACT_MISSING)


async def test_missing_file_on_disk_is_5xx_not_404(db, make_user, api, export_root):
    """行说 ready、盘上没有＝卷没挂对，那是服务端故障，不能对用户报「任务不存在」。"""
    await make_user("u1")
    job = await build_ready_job(db)
    (export_root / job.file_path).unlink()
    resp = await _request(api, "GET", f"/api/v1/user/export/{job.id}/download")
    assert resp.status_code == 500, resp.text
    assert resp.json()["code"] == int(ErrorCode.EXPORT_ARTIFACT_MISSING)


# ── 4. 队列语义 ──

async def test_too_large_is_terminal_without_retry_budget(db, make_user, export_root, monkeypatch):
    """超限是设计上的终态：重试只会把同一堵墙再撞三次（工作单风险节）。"""
    await make_user("u1")
    await seed(db, "u1")
    await export_service.request_export(db, "u1")
    monkeypatch.setattr(settings, "export_max_bytes", 64)
    result = await export_service.process_jobs(db)
    assert result == {"requeued": 0, "failed": 1, "ready": 0, "expired": 0, "skipped": 0}, result

    job = await export_repository.get(
        db, (await export_repository.ids_by_status(db, ("failed",)))[0]
    )
    assert job.error_code == EXPORT_ERROR_TOO_LARGE
    assert job.attempts == 1, "超限只烧一次预算，不该走 3 次重试"
    assert job.file_path == "", "失败的 job 不留产物路径"
    assert not list((export_root / "u1").glob("*.part")), "临时文件必须收干净"


async def test_build_failure_consumes_budget_then_fails(db, make_user, export_root, monkeypatch):
    """重试预算落在 attempts 列：内存计数会被滚动发布清零，于是「3 次后 failed」永远数不满。"""
    await make_user("u1")
    await export_service.request_export(db, "u1")
    job_id = (await export_repository.ids_by_status(db, ("queued",)))[0]

    async def boom(_db, _job):
        raise RuntimeError("模拟生成崩溃")

    monkeypatch.setattr(export_service, "build_artifact", boom)
    for expected in (1, 2):
        await export_service.process_jobs(db)
        job = await export_repository.get(db, job_id)
        assert (job.status, job.attempts) == ("queued", expected), (job.status, job.attempts)
        assert job.error_code == EXPORT_ERROR_BUILD_FAILED

    await export_service.process_jobs(db)
    job = await export_repository.get(db, job_id)
    assert (job.status, job.attempts) == ("failed", 3), (job.status, job.attempts)


async def test_stale_running_is_reclaimed(db, make_user, export_root):
    """running 的行只可能是进程崩在半路；不回收就永远没人再认领它。

    这一枪打 `limit=0`：只跑回收、不跑认领，否则刚退回 queued 的 job 会在同一轮里
    被重新生成成 ready，attempts 与 error_code 都被写回干净，回收这件事就没证人。
    """
    await make_user("u1")
    await export_service.request_export(db, "u1")
    job = await export_repository.get(db, (await export_repository.ids_by_status(db, ("queued",)))[0])
    await export_repository.claim(db, job.id, utc_now() - timedelta(minutes=41))
    result = await export_service.process_jobs(db, limit=0)
    assert result["requeued"] == 1, result
    job = await export_repository.get(db, job.id)
    assert (job.status, job.attempts, job.error_code) == ("queued", 1, EXPORT_ERROR_STALE)


async def test_claim_is_compare_and_swap(db, make_user, export_root):
    """认领靠状态条件更新，不靠 FOR UPDATE（SQLite 不渲染它，两种库会各判一套形状）。"""
    await make_user("u1")
    await export_service.request_export(db, "u1")
    job_id = (await export_repository.ids_by_status(db, ("queued",)))[0]
    assert await export_repository.claim(db, job_id, utc_now()) is True
    assert await export_repository.claim(db, job_id, utc_now()) is False, "已 running 不能再被认领"


async def test_ready_sets_expiry_and_cron_removes_the_file(db, make_user, export_root, time_travel):
    """验收「保留 7 天后由 cron 清理」：到期置 expired、删文件、下载路径改口 409。"""
    await make_user("u1")
    await seed(db, "u1")
    job = await build_ready_job(db)
    assert job.expires_at is not None
    assert job.expires_at - job.finished_at == timedelta(days=settings.export_retention_days)
    artifact = export_root / job.file_path

    time_travel.set(utc_now() + timedelta(days=settings.export_retention_days + 1))
    result = await export_service.process_jobs(db)
    assert result["expired"] == 1, result
    after = await export_repository.get(db, job.id)
    assert (after.status, after.deleted_at is not None) == ("expired", True)
    assert not artifact.exists(), "过期后盘上还留着用户的档案包"
    # expired 这一格把 file_path/byte_size 写回空：文件已经删了，留着路径就是留一个指
    # 向不存在产物的引用（下载端点会把它变成 50007，而正确答案是 409「请重新申请」）。
    assert (after.file_path, after.byte_size) == ("", 0)


async def test_missing_artifact_is_rebuilt_not_left_dangling(db, make_user, export_root):
    """行说 ready、盘上没有（卷漂到别的节点、宿主侧被清过）：退回队列并当场重做。

    不自愈的话，用户要对着下载 500 等到 7 天过期才能重新申请——`ready` 是占用 24h 额度的
    阻塞状态，「可带走」这句承诺正是破在这一格最安静（没人报错，只有用户拿不到东西）。
    """
    await make_user("u1")
    await seed(db, "u1")
    job = await build_ready_job(db)
    artifact = export_root / job.file_path
    artifact.unlink()

    result = await export_service.process_jobs(db)
    assert (result["requeued"], result["ready"]) == (1, 1), result
    after = await export_repository.get(db, job.id)
    assert after.status == "ready" and after.attempts == 1, (after.status, after.attempts)
    assert (export_root / after.file_path).is_file(), "重做之后盘上还是空的"


async def test_persistent_missing_artifact_exhausts_the_budget(db, make_user, export_root):
    """重做也拿不回文件时要有尽头：3 次后 failed，而不是每 5 分钟重拷一遍全库。

    「拿不回」在这里用每轮抹掉上一轮新建出来的文件来模拟（真机对应卷只读／被别的进程清掉）。
    """
    await make_user("u1")
    await seed(db, "u1")
    job = await build_ready_job(db)
    artifact = export_root / job.file_path

    for expected in (1, 2, 3):
        assert artifact.is_file(), f"第 {expected} 轮开始前应有上一轮重做的文件"
        artifact.unlink()
        await export_service.process_jobs(db)
        after = await export_repository.get(db, job.id)
        assert after.attempts == expected, (after.attempts, expected)
    assert (after.status, after.file_path) == ("failed", ""), (after.status, after.file_path)
    assert not artifact.exists(), "终态 failed 之后不该留下一份没人认领的产物"


async def test_downloading_an_expired_job_is_conflict(db, make_user, api, export_root, time_travel):
    await make_user("u1")
    job = await build_ready_job(db)
    await export_repository.finish(
        db, job.id, status="ready", now=utc_now() - timedelta(days=8),
        file_path=job.file_path, byte_size=job.byte_size, expires_at=utc_now() - timedelta(days=1),
        attempts=job.attempts,
    )
    resp = await _request(api, "GET", f"/api/v1/user/export/{job.id}/download")
    assert resp.status_code == 409, resp.text
    assert resp.json()["data"]["status"] == "expired", resp.text


# ── 5. 注销与调度面 ──

def test_export_jobs_table_is_in_purge_targets():
    assert "data_export_jobs" in PURGE_KEYS


async def test_purge_deletes_rows_and_artifact_files(db, make_user, export_root):
    """验收「删除后产物与 job 行都被清」：库里零行＋盘上零文件，两样都要取证。"""
    await make_user("u1")
    await seed(db, "u1")
    first = await build_ready_job(db)
    artifact = export_root / first.file_path
    assert artifact.is_file()

    counts = await user_service.purge_user(db, "u1")
    assert counts["data_export_jobs"] == 1, counts
    assert (await db.execute(select(DataExportJob))).scalars().all() == []
    assert not artifact.exists(), "行删了、包还在盘上——注销即删干净在这里破"
    assert not (export_root / "u1").exists(), "用户目录没跟着收掉"


async def test_purge_of_user_without_exports_is_noop(db, make_user, export_root):
    await make_user("u1")
    counts = await user_service.purge_user(db, "u1")
    assert counts["data_export_jobs"] == 0


async def test_cron_endpoint_requires_secret(db, make_user, api, export_root, monkeypatch):
    """新排程这条也得验密钥：未配置 CRON_SECRET 时 fail-closed，配了就必须带对。"""
    await make_user("u1")
    monkeypatch.setattr(settings, "cron_secret", "")
    assert (await _request(api, "GET", "/api/v1/cron/export-jobs")).status_code == 401
    monkeypatch.setattr(settings, "cron_secret", "s3cret")
    assert (await _request(api, "GET", "/api/v1/cron/export-jobs")).status_code == 401
    ok = await _request(
        api, "GET", "/api/v1/cron/export-jobs", headers={"Authorization": "Bearer s3cret"}
    )
    assert ok.status_code == 200, ok.text
    assert set(ok.json()["data"]) == {"requeued", "failed", "ready", "expired", "skipped"}


def test_export_queue_is_scheduled_every_five_minutes():
    """正向证人：这条队列在宿主清单里真的被排了，而且是 */5（端点不可达＝用户永远拿不到包）。"""
    lines = (ROOT / "deploy" / "schedule" / "crontab").read_text(encoding="utf-8").splitlines()
    hits = [ln for ln in lines if "/api/v1/cron/export-jobs" in ln and not ln.startswith("#")]
    assert len(hits) == 1, hits
    assert hits[0].split()[0] == "*/5", hits[0]


# ── 6. 形状护栏 ──

def test_status_vocabulary_is_stable():
    """状态取值是对外口径（前端按它渲染文案），增删要同时改模型清单与这里。"""
    from backend.models.export import EXPORT_STATUSES

    assert EXPORT_STATUSES == ("queued", "running", "ready", "failed", "expired")
    assert set(EXPORT_SCOPES) == {"all"}


def test_cron_reads_use_the_declared_indexes():
    """cron 的两条读路径（按状态取 queued、按 expires_at 扫过期）靠复合索引，不靠运气。"""
    names = {i.name for i in DataExportJob.__table__.indexes}
    assert {"ix_data_export_jobs_user_created", "ix_data_export_jobs_status_created"} <= names
    assert {"ix_data_export_jobs_status_created"} == {
        i.name for i in DataExportJob.__table__.indexes
        if [c.name for c in i.columns] == ["status", "created_at"]
    }
