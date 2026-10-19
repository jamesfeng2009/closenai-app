"""T1-01 的实库面（2026-10-18）：工作单验收那句「真库跑一遍」里 sqlite 给不出的三半。

sqlite 那批（`test_data_export.py`，38 条）证的是清单派生、状态机与 API 形状；
只有真库能证的在这里：

① 读路径打在 PG 的列型上——`memories.embedding` 是 pgvector HALFVEC（`conftest.py` 专门为
   它写过 sqlite 兼容方案），排除列是**根本不 SELECT**（逐列 select），真库上这条才叫成立；
   sqlite 里那列只是 JSON，删不删都对得上，等于没测。
② 认领是 CAS（`UPDATE … WHERE status='queued'` 的 rowcount）。`export_repository.py` 的
   注释把赌注写在「多实例并发认领也只会各领各的」上——sqlite 是单写者，两条 session 永远
   排不成「同时」，这句承诺在 sqlite 侧是不可测的。
③ 注销删除走的是 PG 上的真实 DELETE＋产物文件同步收掉；`data_export_jobs` 的清理排在
   用户本体之前，顺序错了 `file_paths_for_user` 就读不到路径、文件变孤儿。

一次性空库，跑完即删（本机是工作机，不长占）：
    docker exec closenai-pg-dev psql -U postgres -c 'CREATE DATABASE t101_e2e'
    DATABASE_URL='postgresql+psycopg://postgres:***@127.0.0.1:55432/t101_e2e' \\
        .venv/bin/python -m alembic -c backend/alembic.ini upgrade head
    TEST_DATABASE_URL='postgresql://postgres:***@127.0.0.1:55432/t101_e2e' \\
        CLOSEN_REQUIRE_PG=1 .venv/bin/pytest backend/tests/test_data_export_pg.py -q
    docker exec closenai-pg-dev psql -U postgres -c 'DROP DATABASE t101_e2e'
"""
from __future__ import annotations

import asyncio
import io
import json
import zipfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from _pg_env import pg_test_url
from backend.config import settings
from backend.core.alembic_chain import head_revision
from backend.core.time_utils import utc_now
from backend.models.auth import RefreshToken
from backend.models.canonical import Insight
from backend.models.chat import ChatMessage
from backend.models.erasure import PostHogErasureRetry
from backend.models.export import DataExportJob
from backend.models.memory import Memory
from backend.models.mood import MoodCheckin
from backend.models.subscription import Receipt
from backend.models.user import User
from backend.repositories import export_repository
from backend.repositories import user_repository
from backend.services.export_service import export_targets
from backend.services.export_service import export_service
from backend.services.user_service import user_service

pytestmark = pytest.mark.asyncio

PREFIX = "t101"
SECRET_REFRESH = "SECRET-PG-REFRESH-HASH-DO-NOT-EXPORT"
SECRET_RECEIPT = "SECRET-PG-RECEIPT-RAW-DO-NOT-EXPORT"
SECRET_EMBEDDING = 9.99
EXPORT_KEYS = {key for _model, key in export_targets()}


def _read_zip(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as zf:
        assert zf.testzip() is None, "真库跑出来的包是坏的"
        return {n: zf.read(n).decode("utf-8") for n in zf.namelist()}


def _ndjson(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@pytest.fixture
async def pg(monkeypatch, tmp_path):
    """真库会话＋隔离产物目录；开局与收尾都清 `t101` 前缀，同一库可重跑。

    开局那道闸门判的是「这库除了本用例的前缀行以外一行用户都不许有」：`purge_user`
    按 user_id 删，指错库就等于替别人删干净数据——所以缺 env 是 fail（`CLOSEN_REQUIRE_PG`），
    库里有别人也是 fail，不是 skip。
    """
    url = pg_test_url(purpose="T1-01 真库 E2E，需 alembic upgrade head 后的一次性空库")
    engine = create_async_engine(url)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    root = tmp_path / "exports"
    monkeypatch.setattr(settings, "export_dir", str(root))

    async def _purge(session: AsyncSession) -> None:
        like = f"{PREFIX}%"
        await session.execute(delete(DataExportJob).where(DataExportJob.user_id.like(like)))
        # T1-02 之后 `purge_user` 在擦除失败时会写一行重试台账：它刻意不进 PURGE_TARGETS
        # （豁免表），所以一次性库的清理也得自己点名它，否则残行会漂到下一个用同一库的文件里。
        await session.execute(delete(PostHogErasureRetry).where(
            PostHogErasureRetry.user_id.like(like)))
        await session.execute(delete(ChatMessage).where(ChatMessage.user_id.like(like)))
        await session.execute(delete(Memory).where(Memory.user_id.like(like)))
        await session.execute(delete(MoodCheckin).where(MoodCheckin.user_id.like(like)))
        await session.execute(delete(Insight).where(Insight.user_id.like(like)))
        await session.execute(delete(RefreshToken).where(RefreshToken.user_id.like(like)))
        await session.execute(delete(Receipt).where(Receipt.user_id.like(like)))
        await session.execute(delete(User).where(User.id.like(like)))
        await session.commit()

    async with factory() as probe:
        head = await probe.scalar(text("select version_num from alembic_version"))
        pg_tables = await probe.scalar(text(
            "select array_agg(tablename) from pg_tables where schemaname = 'public'"
        )) or []
        others = int(await probe.scalar(
            select(func.count(User.id)).where(User.id.not_like(f"{PREFIX}%"))
        ) or 0)
    # head 必须是**最新**那条迁移，否则本轮新增的表根本不存在，用例全在打空库。
    # T6-03（2026-10-20）之前这一句是字面量 `== "f6b8d0e2a4c6"`，而同一句话在
    # `test_canonical_schema.py`、`test_posthog_erasure_retry_pg.py`、
    # `test_delivery_audit_pg.py` 里各有一份＝四个第二真源，每落一件迁移要同步改四处。
    # 现在期望值由 `backend/core/alembic_chain.py` 离线从 versions 目录推（不连库），
    # 实测值从库里读——两侧不同源，红了只可能是「库没跟上链」这一个意思。
    assert head == head_revision(), (
        f"库的 alembic head={head}，链的 head={head_revision()}：库落后了")
    # 本文件真正依赖的东西是下一行那张表在不在库里（head 相等只是「库够新」的证据）。
    assert "data_export_jobs" in pg_tables, "库里没有 data_export_jobs：本文件的靶不在，跑它没有意义"
    assert others == 0, f"库里已有 {others} 个别的用户：本用例会在真库上删数据，只能跑在一空库"

    async with factory() as session:
        await _purge(session)
        yield SimpleNamespace(db=session, factory=factory, root=root)
        await _purge(session)

    await engine.dispose()


async def _make(db: AsyncSession, user_id: str) -> None:
    await user_repository.get_or_create_user(db, user_id)
    await db.commit()


async def _seed(db: AsyncSession, user_id: str) -> None:
    """可读四类＋不得导出的两类（凭据/收据）＋一个 pgvector 向量列。"""
    db.add_all([
        ChatMessage(id=f"{user_id}-chat", user_id=user_id, role="user",
                    content="在会上被否了，我没敢说第二次"),
        Memory(id=f"{user_id}-mem", user_id=user_id, content="我回避冲突",
               embedding=[SECRET_EMBEDDING] * 512),
        MoodCheckin(id=f"{user_id}-mood", user_id=user_id, mood_emoji="😔", mood_label="低落"),
        Insight(id=f"{user_id}-ins", user_id=user_id, personal_model_id=f"{user_id}-pm",
                statement="被否定后我会沉默，而不是辩驳", claim_type="pattern"),
        RefreshToken(id=f"{user_id}-rt", user_id=user_id, token_hash=SECRET_REFRESH,
                     expires_at=utc_now() + timedelta(days=30)),
        Receipt(id=f"{user_id}-rc", user_id=user_id, product_id="pro_monthly",
               receipt_data=SECRET_RECEIPT, status="valid"),
    ])
    await db.commit()


async def test_real_pg_package_is_readable_and_excludes_vector(pg):
    """① 逐列 select 打在真 PG 上：包能打开、中文逐字、向量列一个字节都不进。"""
    db = pg.db
    await _make(db, f"{PREFIX}a")
    await _seed(db, f"{PREFIX}a")

    queued = await export_service.request_export(db, f"{PREFIX}a")
    await db.commit()
    assert queued["status"] == "queued"

    built = await export_service.process_jobs(db)
    await db.commit()
    assert built["ready"] == 1, f"cron 在真库没把 job 做成 ready：{built}"

    job = await export_repository.get(db, queued["job_id"])
    assert job.status == "ready" and job.byte_size > 0

    path, file_name = await export_service.resolve_download(db, f"{PREFIX}a", job.id)
    assert path.is_file() and file_name == f"closenai-export-{job.id}.zip"

    files = _read_zip(path)
    assert set(files) == {f"tables/{k}.ndjson" for k in EXPORT_KEYS} | {"manifest.json"}
    # 中文逐字（PG 的 UTF-8 库＋ensure_ascii=False，两处任一退化都会在这里红）
    assert _ndjson(files["tables/memories.ndjson"])[0]["content"] == "我回避冲突"
    assert "\\u" not in files["tables/memories.ndjson"]
    assert _ndjson(files["tables/insights.ndjson"])[0]["claim_type"] == "pattern"
    # 排除列不是「读出来再删」，而是根本没进 SELECT：真库上这半句才可测
    assert "embedding" not in files["tables/memories.ndjson"], "pgvector 大列被读进了档案包"
    assert str(SECRET_EMBEDDING) not in files["tables/memories.ndjson"]
    assert "tables/refresh_tokens.ndjson" not in files
    assert "tables/receipts.ndjson" not in files
    blob = "\n".join(files.values())
    assert SECRET_REFRESH not in blob and SECRET_RECEIPT not in blob
    # 时间列在 PG 上是 TIMESTAMP（无偏移），序列化必须是 isoformat 字符串而不是报错
    created = _ndjson(files["tables/chat_messages.ndjson"])[0]["created_at"]
    assert isinstance(created, str) and created[:4] == str(utc_now().year), created

    manifest = json.loads(files["manifest.json"])
    assert {t["table"] for t in manifest["tables"]} == EXPORT_KEYS
    assert manifest["retention_days"] == settings.export_retention_days
    assert manifest["total_rows"] == sum(t["rows"] for t in manifest["tables"])
    # 下载的字节与盘上的包同源（zip 头＋大小对得上库里的 byte_size）
    assert zipfile.is_zipfile(io.BytesIO(path.read_bytes()))


async def test_real_pg_two_workers_claim_the_same_job_once(pg):
    """② CAS 认领在真并发下只交出去一次——sqlite 单写者证不了这句。"""
    db = pg.db
    await _make(db, f"{PREFIX}c")
    queued = await export_service.request_export(db, f"{PREFIX}c")
    await db.commit()
    job_id = queued["job_id"]

    async def claim_once() -> bool:
        async with pg.factory() as session:
            got = await export_repository.claim(session, job_id, utc_now())
            await session.commit()
            return got

    results = await asyncio.gather(claim_once(), claim_once())
    assert sorted(results) == [False, True], (
        f"两条 session 同时认领同一个 queued job，结果 {results}——CAS 没做到「只交出去一次」"
    )
    job = await export_repository.get(db, job_id)
    assert job.status == "running" and job.started_at is not None


async def test_real_pg_concurrent_process_builds_one_artifact(pg):
    """② 的下半句：两个 worker 同轮跑 `process_jobs`，库里一行 ready、盘上一个包。

    认领谁先谁后取决于调度，所以判据钉在**不变量**上而不是「谁 skipped」：
    两轮合计只可能有一个 `ready`，产物目录里只可能落一个 zip——
    多实例部署真跑起来时，这条就是「不会双倍吃磁盘和 CPU」的唯一证人。
    """
    db = pg.db
    await _make(db, f"{PREFIX}conc")
    queued = await export_service.request_export(db, f"{PREFIX}conc")
    await db.commit()

    async def run_once() -> dict:
        async with pg.factory() as session:
            out = await export_service.process_jobs(session)
            await session.commit()
            return out

    results = await asyncio.gather(run_once(), run_once())
    assert sum(r["ready"] for r in results) == 1, results
    job = await export_repository.get(db, queued["job_id"])
    assert job.status == "ready", f"并发一轮后 job 不是 ready：{job.status} {job.error_code}"

    zips = sorted(p.name for p in (pg.root / f"{PREFIX}conc").glob("*.zip"))
    assert zips == [f"{queued['job_id']}.zip"], f"包不止一个或名字不对：{zips}"
    parts = list((pg.root / f"{PREFIX}conc").glob("*.part"))
    assert parts == [], f".part 临时文件没被收掉：{parts}"


async def test_real_pg_purge_clears_rows_and_artifact(pg):
    """③ 注销在真库上删干净：库里零行＋盘上零文件，顺序错了就在这里红。"""
    db = pg.db
    await _make(db, f"{PREFIX}d")
    await _seed(db, f"{PREFIX}d")
    queued = await export_service.request_export(db, f"{PREFIX}d")
    await db.commit()
    await export_service.process_jobs(db)
    await db.commit()

    job = await export_repository.get(db, queued["job_id"])
    artifact = pg.root / job.file_path
    assert artifact.is_file(), "前置没建立：真库上的包不在盘上"

    counts = await user_service.purge_user(db, f"{PREFIX}d")
    await db.commit()
    assert counts["data_export_jobs"] == 1, counts

    left = (await db.execute(select(DataExportJob))).scalars().all()
    assert left == [], f"注销后 data_export_jobs 还剩 {len(left)} 行"
    assert not artifact.exists(), "行删了、包还在盘上——注销即删干净在这里破"
    assert not (pg.root / f"{PREFIX}d").exists(), "用户目录没跟着收掉"
    assert await db.scalar(
        select(func.count(Memory.id)).where(Memory.user_id == f"{PREFIX}d")
    ) == 0
