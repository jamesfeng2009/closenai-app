"""通用数据导出 job 表模型（T1-01，Baseline §2.2-6／§14.2）。

为什么要有这张表而不是同步返回 zip：导出的靶是**用户全部个人数据**（43 张表，含
canonical 权威表），同步做会把一次 HTTP 请求变成分钟级长事务，而长事务在 py-api 的
`CommitBeforeResponseRoute` 口径下会占住连接；超时后用户只拿到一个 500，什么证据都没有。
异步 job 的代价是多一张表与一条 cron，换来的是「排队中／生成中／可下载／失败（带码）／
已过期」五个可对用户说明的状态。

列的三条口径（都在别处有据，不是本地偏好）：
- `status` 用 VARCHAR 不用 enum：与 N2 覆写口径一致（裁决 §五-4），改状态不碰 DDL。
- 不建外键、不靠级联删：删除走 `PURGE_TARGETS` 逐表 rowcount（D-5），所以本表必须带
  `user_id` 并登记进清单，否则注销后残留 job 行与产物文件。
- `file_path` 存**相对 `settings.export_dir` 的路径**，不存绝对路径：绝对路径会把宿主
  目录结构带进用户的下载响应与日志里，而它对本机以外的一切消费方都没有意义。

`attempts` 是工作单列清单之外的新增列，理由见 T1-01 结案行（cron 的「3 次后 failed」
需要一个能跨进程重启存活的计数，内存计数器不算）。
"""
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base

# 状态机（顺序即生命周期）：queued → running → ready|failed，ready → expired。
# 判据集中在 export_service 的迁移表里，这里只固定落库取值集合。
EXPORT_STATUSES = ("queued", "running", "ready", "failed", "expired")


class DataExportJob(Base):
    __tablename__ = "data_export_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    # 导出范围。当前只有 all（全量带走）；留着这一列是因为 §14.2 的口径是
    # 「可导出」，将来按时间窗或按表裁剪时不必改主键以外的形状。
    scope: Mapped[str] = mapped_column(String(32), default="all")
    status: Mapped[str] = mapped_column(String(16), default="queued")
    # 相对 settings.export_dir 的产物路径；未生成为空串（不用 NULL：sqlc 侧
    # 同 N7 的口径，空串＝「没有」，NULL＝「没填」，两者在类型上分不开）。
    file_path: Mapped[str] = mapped_column(String(512), default="")
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    # 失败原因的稳定机器码（不是给用户看的文案）：EXPORT_TOO_LARGE／EXPORT_BUILD_FAILED 等。
    error_code: Mapped[str] = mapped_column(String(64), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # ready 后的可下载截止时间；过期由 cron 置 expired 并删文件。
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 产物文件被删除的时刻（过期清理或注销）；行本身留在 purge 清单里一起删净。
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # 「取 queued」与「按用户看历史」是 cron 与 GET 的两条读路径，各一条索引。
        Index("ix_data_export_jobs_user_created", "user_id", "created_at"),
        Index("ix_data_export_jobs_status_created", "status", "created_at"),
    )
