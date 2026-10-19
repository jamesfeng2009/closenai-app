"""通用数据导出服务（T1-01，Baseline §2.2-6「带走」／§3 P0／§4.2 Free 行）。

这条链的形状：**申请 → 排队 → 生成 → 下载 → 到期清理**，五个状态、一张表、一条 cron。
下面三条是本批最关键的设计口径，出处与偏离都在 T1-01 结案行里登记过一遍。

一、导出靶从 `PURGE_TARGETS` 派生，不另立一份表清单。
注销清单回答「删哪些表」，导出清单回答「给哪些表」，两者必须是同一批表减去两类例外：
登录凭据（`refresh_tokens`）与支付收据原文（`receipts`）——它们同样是个人数据，但把
它们打进一个可一键下载、还能被转发给别人的 zip，是本批最容易制造的第二次泄露。
派生而不是手抄的理由很实际：新表登记进 purge 清单是既有闸门（`test_account_deletion.py`
扫 `Base.metadata`）的硬要求，派生就自动继承「新表一定出现在导出里」这条保证；手抄一份
则会漂移，而漂移的方向恰好是「用户拿不到自己的新数据」——那种缺陷没有人会报错。

二、读 canonical 表是 D-5 例外的第二个消费面（读，不写）。
V3 06 §5 禁 Python 直连 canonical，N2 给 purge 开了一次例外。本模块刻意不引用 canonical
模型（`test_canonical_schema.py` 那条闸会红，也确实该红），而是复用 user_service 里已登记
的 `PURGE_TARGETS` 类引用。要完全绕开就得给 Go 加一个全表 dump 端点，那属 W6 架构收敛面，
不在本批顺手做——但「读侧多了一个消费面」这件事必须显形，所以写在这里。

三、产物落本地盘、下载走同一条鉴权路径，不发签名 URL。
本部署没有接通任何对象存储（`backend/config.py` 的 `supabase_url` 只用于 Auth，全仓
没有一处 storage/bucket 调用），造一个 15 分钟有效的 HMAC 公开链接等于在无对象存储的
前提下新增一个「谁拿到 URL 谁就读得到全部个人数据」的 bearer 凭证。所以取工作单给的
第二个选项：`GET /api/v1/user/export/{job_id}/download` 流式返回，与既有 5 条 PDF 端点
同一条鉴权与字节流口径（`contracts/endpoints.md` 第七节）。
"""
import json
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.errors import AppException, ErrorCode
from backend.core.logging import get_logger
from backend.core.time_utils import utc_now
from backend.repositories import export_repository, user_repository
from backend.services.user_service import PURGE_TARGETS

logger = get_logger("closenai.export")

# 唯一的导出范围。留着这一格（而不是删掉 scope 参数）是为了 §14.2「说明数据范围」
# 有一个机器可读的落点：将来按时间窗裁剪时，job 行上记的就是当时申请的范围。
EXPORT_SCOPES = ("all",)

# 从导出靶里排除的表 → 理由。理由为空等于没有排除；闸门逐条验这张表。
#
# 这三条（user_consents／safety_events／push_tokens）是 T1-02 补的，不是新决定：
# `docs/data-lineage.md` 从 T5-01 起就把它们写成「不进导出」，而 `test_data_lineage_ledger.py`
# 当时只核「文档点名的表还在不在代码里」这一个方向，于是「代码把审计表和凭据打进了用户的
# 下载包」这条漂移没有任何东西会报错。现在闸是双向的（见那个文件），所以这里必须与文档同值。
EXPORT_EXCLUDE_REASONS = {
    "refresh_tokens": "登录凭据本体：可下载、可转发的归档里不该有它（§14.1 禁止导出物含 access token）",
    "receipts": "支付收据原文含交易凭证，放进导出对个人数据无增益、对泄露有增益",
    "inbox_messages": "投递元数据、无 user_id，不是用户可读数据（N2 已登记同一口径）",
    "push_tokens": "设备推送凭据（APNs/FCM device token）：导出无意义，落进一个可转发的 zip 就是风险",
    "user_consents": (
        "同意台账属**审计**（谁在哪个版本表过态），不是用户创作的内容；"
        "把它打进导出包等于替平台做一份「你同意过什么」的二次分发（T5-01 裁决，"
        "见 docs/data-lineage.md）。注销仍删净。"
    ),
    "safety_events": (
        "危机命中审计（§5.16 ④）：摘录只在 24h 人工抽检链路内可见，不进导出；注销仍删净。"
    ),
}

# 表内排除列：table → {column: 理由}。与表级排除同一个诚实要求，每格都要有理由。
EXCLUDED_COLUMNS = {
    "memories": {
        "embedding": "512 维向量是派生物、不可阅读，且让 zip 体积翻数倍；"
        "用户带走的是记忆文本行，向量可由记忆重建"
    },
}

# error_code 列的取值（机器码，不是给用户看的文案）：超限与卡死是终态语义，构建失败进重试预算。
EXPORT_ERROR_TOO_LARGE = "EXPORT_TOO_LARGE"
EXPORT_ERROR_STALE = "EXPORT_STALE_RUNNING"
EXPORT_ERROR_BUILD_FAILED = "EXPORT_BUILD_FAILED"
# 行写 ready、盘上却没有文件（卷漂到别的节点、宿主侧被清过）。不烧掉这一格，用户就要对着
# 下载 500 等到 7 天后过期才能重新申请——「可带走」的承诺在这一格里破掉，所以自愈。
EXPORT_ERROR_ARTIFACT_MISSING = "EXPORT_ARTIFACT_MISSING"


class ExportTooLarge(Exception):
    """产物超过 `settings.export_max_bytes`：设计上的终态，不重试（工作单风险节的原话）。"""


def export_targets() -> tuple[tuple[object, str], ...]:
    """导出靶 ＝ PURGE_TARGETS − `EXPORT_EXCLUDE_REASONS` 的键。"""
    return tuple(
        (model, key)
        for model, key in PURGE_TARGETS
        if key not in EXPORT_EXCLUDE_REASONS
    )


def _aware(value: datetime) -> datetime:
    """把库里读出的 naive datetime 当 UTC 补齐 tzinfo（SQLite 的列存不出偏移）。"""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _jsonable(value: object) -> object:
    """把一行 ORM 值变成可 JSON 化的值。

    未知类型一律 `str()`，不抛：一行读不懂的向量不该让整个导出 job 失败——那等于把工程
    缺陷变成用户的合规障碍。datetime 之外的日期/时间对象走它自己的 `isoformat()`。
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return iso()
    return str(value)


def artifact_root() -> Path:
    """产物根目录的唯一取法；不存在就建（宿主侧由 compose/k8s 挂卷覆盖）。"""
    root = settings.export_dir_path
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_artifact(rel: str) -> Path:
    """把库里的相对路径解析成绝对路径，并拒绝任何越出产物根目录的写法。

    `file_path` 今天只由本服务写入，但一旦有人写进 `../../etc/passwd`（新的写入路径、
    手工改库、或未来的管理面），下载端点就变成任意文件读。判据放在**读取处**：这里是
    唯一能把越界变成动作的地方，写入口的校验挡不住已经在库里的那一行。
    """
    root = artifact_root().resolve()
    candidate = (root / rel).resolve()
    if not candidate.is_relative_to(root):
        raise AppException(
            ErrorCode.EXPORT_ARTIFACT_MISSING, "导出产物路径不合法 / invalid artifact path"
        )
    return candidate


def job_to_dict(job, *, now: datetime | None = None) -> dict:
    """状态响应形状（POST 与 GET 共用一份，前端按这一份渲染）。"""
    now = now or utc_now()
    payload = {
        "job_id": job.id,
        "scope": job.scope,
        "status": job.status,
        "created_at": _jsonable(job.created_at),
        "started_at": _jsonable(job.started_at),
        "finished_at": _jsonable(job.finished_at),
        "expires_at": _jsonable(job.expires_at),
        "byte_size": job.byte_size,
        "error_code": job.error_code or None,
        "attempts": job.attempts,
        "download_ready": bool(
            job.status == "ready"
            and job.file_path
            and (job.expires_at is None or _aware(job.expires_at) > now)
        ),
        "file_name": f"closenai-export-{job.id}.zip" if job.status == "ready" else None,
    }
    if payload["download_ready"]:
        payload["download_path"] = f"/api/v1/user/export/{job.id}/download"
    return payload


class ExportService:
    """data_export_jobs 的业务面：申请、生成、下载、重试预算、到期与注销清理。"""

    # ── 申请与查询 ──

    async def request_export(
        self, db: AsyncSession, user_id: str, scope: str = "all"
    ) -> dict:
        """建一个 queued job；同一用户 24h 内已有未失败的 job 时拒绝（工作单的 1 次/24h）。

        限频判据用库里的事实，不用 `core/rate_limit.py` 的进程内固定窗口：后者重启即清零，
        而这条限制的是「一个人反复把全库拷一遍」，它必须活得过部署。
        failed/expired 不占额度——失败不该让人再等 24 小时，过期本就是设计上的重新申请点。
        """
        if scope not in EXPORT_SCOPES:
            raise AppException(
                ErrorCode.INVALID_PARAMS,
                f"未知导出范围：{scope}（当前只支持 {'/'.join(EXPORT_SCOPES)}）",
            )
        await user_repository.require_user(db, user_id)
        now = utc_now()
        since = now - timedelta(hours=settings.export_request_window_hours)
        existing = await export_repository.latest_blocking(db, user_id, since)
        if existing is not None:
            raise AppException(
                ErrorCode.EXPORT_REQUEST_TOO_FREQUENT,
                f"{settings.export_request_window_hours} 小时内只能申请一次导出，"
                f"已有 job {existing.id}（状态 {existing.status}）",
                data={"job_id": existing.id, "status": existing.status},
            )
        job = await export_repository.create(
            db, job_id=str(uuid4()), user_id=user_id, scope=scope, now=now
        )
        logger.info("导出已排队 job=%s user=%s scope=%s", job.id, user_id, scope)
        return job_to_dict(job, now=now)

    async def get_job(self, db: AsyncSession, user_id: str, job_id: str) -> dict:
        return job_to_dict(await self._require_own(db, user_id, job_id))

    async def list_jobs(self, db: AsyncSession, user_id: str, *, limit: int = 20) -> dict:
        """该用户最近的 job（倒序）＋保存周期。只回这个用户的行，没有跨用户读的面。

        带上 `retention_days` 是因为 §14.2 要求界面说明「保存周期」，而那个数字的真源是
        `settings.export_retention_days`：前端各自写一份「7 天」，改配置那天就开始说谎。
        """
        rows = await export_repository.list_for_user(db, user_id, limit=limit)
        return {
            "jobs": [job_to_dict(r) for r in rows],
            "retention_days": settings.export_retention_days,
        }

    async def resolve_download(self, db: AsyncSession, user_id: str, job_id: str) -> tuple[Path, str]:
        """返回 (绝对路径, 下载文件名)；任何「拿不到」的形状都抛错，不返回 None。"""
        job = await self._require_own(db, user_id, job_id)
        now = utc_now()
        if job.status != "ready" or not job.file_path:
            raise AppException(
                ErrorCode.EXPORT_NOT_READY,
                f"导出尚未就绪（当前状态 {job.status}）/ export not ready",
                data={"status": job.status},
            )
        if job.expires_at is not None and _aware(job.expires_at) <= now:
            raise AppException(
                ErrorCode.EXPORT_NOT_READY,
                f"导出文件已过期（保留 {settings.export_retention_days} 天），请重新申请",
                data={"status": "expired"},
            )
        path = resolve_artifact(job.file_path)
        if not path.is_file():
            # 行说 ready、盘上没有：卷没挂对的那一种真失败，报 5xx 而不是 404
            logger.error("导出产物缺失 job=%s user=%s path=%s", job.id, user_id, job.file_path)
            raise AppException(
                ErrorCode.EXPORT_ARTIFACT_MISSING, "导出产物已不可用，请联系支持 / artifact missing"
            )
        return path, f"closenai-export-{job.id}.zip"

    async def _require_own(self, db: AsyncSession, user_id: str, job_id: str):
        """跨用户隔离：别人的 job_id 与不存在的 job_id 同形回 404。

        不按「存在但不属于你」回 403——那会把「这个 id 属于谁」变成可枚举的旁证。
        §14.1 要求每条读写路径都有跨用户隔离测试，这里就是被测的那条。
        """
        job = await export_repository.get(db, job_id)
        if job is None or job.user_id != user_id:
            raise AppException(ErrorCode.RESOURCE_NOT_FOUND, "导出任务不存在 / export job not found")
        return job

    # ── 生成 ──

    def _columns(self, model) -> list:
        return [
            c
            for c in model.__table__.columns
            if c.name not in EXCLUDED_COLUMNS.get(model.__tablename__, {})
        ]

    async def build_artifact(self, db: AsyncSession, job) -> dict:
        """逐表流式写 NDJSON 打进一个 zip，返回 `{"path","byte_size","manifest"}`。

        读表用 `db.stream()` ＋ `stream_results`：一个大用户的 chat_messages 可以是十万行，
        `.all()` 会把它们一次拉进 py-api 的 1.5Gi 内存上限（deploy/k8s/40-py-api.yaml）。
        先写 `.part` 再 rename：中途崩溃只留一个不会被任何状态引用到的临时文件。
        """
        root = artifact_root()
        rel = f"{job.user_id}/{job.id}.zip"
        final = (root / rel).resolve()
        final.parent.mkdir(parents=True, exist_ok=True)
        part = final.with_name(final.name + ".part")
        tables: list[dict] = []
        written = 0
        limit = settings.export_max_bytes
        try:
            with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as zf:
                for model, key in export_targets():
                    columns = self._columns(model)
                    rows = 0
                    with zf.open(f"tables/{key}.ndjson", "w") as sink:
                        target = model.id if model.__tablename__ == "users" else model.user_id
                        # 逐列 select（不是 select(model)）：mappings() 的键才是列名，
                        # 而且排除列根本不会从库里读出来——向量那种大列一次都不碰。
                        stmt = (
                            select(*columns)
                            .where(target == job.user_id)
                            .execution_options(stream_results=True)
                        )
                        result = await db.stream(stmt)
                        try:
                            async for row in result.mappings():
                                line = json.dumps(
                                    {c.name: _jsonable(row[c.name]) for c in columns},
                                    ensure_ascii=False,
                                ).encode("utf-8") + b"\n"
                                sink.write(line)
                                rows += 1
                                written += len(line)
                                if written > limit:
                                    raise ExportTooLarge(
                                        f"已读 {key} 第 {rows} 行、未压缩 {written} 字节 > 上限 {limit}"
                                    )
                        finally:
                            # 显式关掉游标：超限抛出时游标还开着，紧接着的 SAVEPOINT 回滚
                            # 会在同一条连接上撞车（超试用例就是这么变成「回滚失败」的假根因）
                            await result.close()
                    tables.append({"table": key, "rows": rows, "file": f"tables/{key}.ndjson"})
                manifest = self._manifest(job, tables)
                zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            size = part.stat().st_size
            os.replace(part, final)
        finally:
            part.unlink(missing_ok=True)
        return {"path": rel, "byte_size": size, "manifest": manifest}

    def _manifest(self, job, tables: list[dict]) -> dict:
        """档案包的自我介绍：包里有哪些表、删了哪些列、保留多久，都要在包里说得清。"""
        return {
            "generated_at": _jsonable(utc_now()),
            "job_id": job.id,
            "scope": job.scope,
            "format": "ndjson-per-table（tables/<表名>.ndjson，一行一个 JSON 对象）",
            "tables": tables,
            "total_rows": sum(t["rows"] for t in tables),
            "excluded_tables": [
                {"table": t, "reason": r} for t, r in sorted(EXPORT_EXCLUDE_REASONS.items())
            ],
            "excluded_columns": [
                {"table": table, "column": column, "reason": reason}
                for table, cols in sorted(EXCLUDED_COLUMNS.items())
                for column, reason in sorted(cols.items())
            ],
            "retention_days": settings.export_retention_days,
        }

    # ── cron 侧 ──

    async def process_jobs(self, db: AsyncSession, *, limit: int = 5) -> dict:
        """一次调度做完四件事：回收卡死 → 到期清理 → 认领生成（含失败预算）。

        逐 job 独立 SAVEPOINT（与 `purge_deleted_users` 同一口径）：一个用户的导出炸了
        不能把整批带回滚，更不能让 cron 的 HTTP 响应变成 500——那样下一个 5 分钟也没人来。

        顺序是有意的：ready 那一趟排在认领**之前**。盘上没文件的 ready job 在这里退回
        queued，同一轮就会被下面的认领循环重做出来，用户不会等到下一个 5 分钟。
        """
        now = utc_now()
        result = {"requeued": 0, "failed": 0, "ready": 0, "expired": 0, "skipped": 0}
        stale_before = now - timedelta(minutes=settings.export_stale_running_minutes)

        # 卡死回收：running 的行只可能是「崩在做完认领之后、写完之前」。行数量级是并发数
        # 而不是用户数，所以状态在 SQL 里筛、时刻在 Python 里比——SQLite 的 DateTime 列
        # 存不出偏移，把时刻比较写进 SQL 会让两种库各判一套。
        for job_id in await export_repository.ids_by_status(db, ("running",)):
            job = await export_repository.get(db, job_id)
            if job is None or job.started_at is None or _aware(job.started_at) >= stale_before:
                continue
            attempts = await self._consume_attempt(
                db, job, EXPORT_ERROR_STALE,
                detail=RuntimeError("running 超时，判为进程崩在半路"),
            )
            result["failed" if attempts >= settings.export_max_attempts else "requeued"] += 1

        for job_id in await export_repository.ids_by_status(db, ("ready",)):
            job = await export_repository.get(db, job_id)
            if job is None:
                continue
            if job.expires_at is not None and _aware(job.expires_at) <= now:
                self._unlink(job.file_path)
                await export_repository.finish(
                    db, job.id, status="expired", now=now, deleted_at=now, attempts=job.attempts
                )
                result["expired"] += 1
                continue
            try:
                present = resolve_artifact(job.file_path).is_file()
            except AppException:  # 路径写法越界：同样按「拿不到」处理，不猜
                present = False
            if present:
                continue
            attempts = await self._consume_attempt(
                db, job, EXPORT_ERROR_ARTIFACT_MISSING,
                detail=RuntimeError(f"行是 ready，盘上没有：{job.file_path!r}"),
            )
            result["failed" if attempts >= settings.export_max_attempts else "requeued"] += 1

        for job_id in (await export_repository.ids_by_status(db, ("queued",)))[:limit]:
            job = await export_repository.get(db, job_id)
            if job is None:
                continue
            if not await export_repository.claim(db, job.id, now):
                result["skipped"] += 1  # 被另一个实例抢先认领：正常形状，不是失败
                continue
            try:
                async with db.begin_nested():
                    built = await self.build_artifact(db, job)
                await export_repository.finish(
                    db,
                    job.id,
                    status="ready",
                    now=utc_now(),
                    file_path=built["path"],
                    byte_size=built["byte_size"],
                    expires_at=utc_now() + timedelta(days=settings.export_retention_days),
                    attempts=job.attempts,
                )
                result["ready"] += 1
                logger.info(
                    "导出生成完成 job=%s user=%s bytes=%d tables=%d",
                    job.id,
                    job.user_id,
                    built["byte_size"],
                    len(built["manifest"]["tables"]),
                )
            except ExportTooLarge as exc:
                # 超限是终态：重试只会把同一堵墙再撞三次，所以不走重试预算（工作单风险节）
                await self._discard_part(job)
                await export_repository.finish(
                    db, job.id, status="failed", now=utc_now(),
                    error_code=EXPORT_ERROR_TOO_LARGE, attempts=job.attempts + 1,
                )
                result["failed"] += 1
                logger.warning("导出超限 job=%s user=%s: %s", job.id, job.user_id, exc)
            except Exception as exc:  # noqa: BLE001  单 job 失败不连锁
                await self._discard_part(job)
                attempts = await self._consume_attempt(
                    db, job, EXPORT_ERROR_BUILD_FAILED, detail=exc
                )
                result["failed" if attempts >= settings.export_max_attempts else "requeued"] += 1
        return result

    async def _consume_attempt(
        self, db: AsyncSession, job, error_code: str, *, detail: Exception | None = None
    ) -> int:
        """烧掉一次重试预算：未满上限退回 queued，满了置 failed。

        预算落在 `attempts` 列而不是内存：滚动发布会把进程内计数清零，于是「3 次后 failed」
        永远数不满，坏 job 被 cron 每 5 分钟重做一次。
        """
        attempts = job.attempts + 1
        await export_repository.finish(
            db,
            job.id,
            status="failed" if attempts >= settings.export_max_attempts else "queued",
            now=utc_now(),
            error_code=error_code,
            attempts=attempts,
            file_path="",
            byte_size=0,
        )
        if detail is not None:
            logger.warning(
                "导出失败 job=%s user=%s attempts=%d code=%s: %s",
                job.id, job.user_id, attempts, error_code, detail,
            )
        return attempts

    async def _discard_part(self, job) -> None:
        """生成失败时把临时文件收掉；路径本身不合法时什么都不碰（越界防护优先于整洁）。"""
        try:
            final = resolve_artifact(job.file_path or f"{job.user_id}/{job.id}.zip")
        except AppException:
            return
        final.with_name(final.name + ".part").unlink(missing_ok=True)

    def _unlink(self, rel: str) -> None:
        if not rel:
            return
        try:
            path = resolve_artifact(rel)
        except AppException:
            logger.warning("导出产物路径越界，拒绝删除：库里写的是 %r", rel)
            return
        if path.is_file():
            path.unlink()
            logger.info("导出产物已删除 %s", rel)

    # ── 注销清理（由 user_service.purge_user 调用）──

    async def delete_user_artifacts(self, db: AsyncSession, user_id: str) -> int:
        """删掉该用户全部已落盘产物，返回实际删除的文件数（行由 PURGE_TARGETS 删）。

        必须在 DELETE 之前调用：`file_path` 只存在于行里，行没了文件就变成库上无记录的
        孤儿，而「注销即删干净」的取证口径是**盘上也找不到**。
        """
        deleted = 0
        for _job_id, rel in await export_repository.file_paths_for_user(db, user_id):
            try:
                path = resolve_artifact(rel)
            except AppException:
                logger.warning("注销时跳过越界的导出路径 %r", rel)
                continue
            if path.is_file():
                path.unlink()
                deleted += 1
        user_dir = artifact_root() / user_id
        if user_dir.is_dir() and not any(user_dir.iterdir()):
            user_dir.rmdir()
        return deleted


export_service = ExportService()
