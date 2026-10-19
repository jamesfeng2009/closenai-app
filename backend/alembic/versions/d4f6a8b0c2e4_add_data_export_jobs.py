"""T1-01 通用数据导出：data_export_jobs（异步 job 的排队/生成/可下载/失败/过期）

Revision ID: d4f6a8b0c2e4
Revises: c3e5a7b9d1f2
Create Date: 2026-10-18

为什么（Baseline §2.2-6「带走」／§3 P0「数据导出入口真实可用」／§4.2 Free 行
「不能让用户无法导出自己的基本数据」）：注销侧有 43 表的 `PURGE_TARGETS`，导出侧却一条
都没有——唯一出口是带 `40304` 权益门禁的 Life Archive PDF（share_service.py 的
export_life_archive_pdf）。免费用户读不到自己的原始数据，这是 P0 合规缺口，不是功能愿望。

形状取**一张 job 表 + 一条 cron**，不取同步返回：导出靶是全部个人数据，同步做会把一次
请求变成分钟级长事务，超时后用户只拿到 500、什么证据都不留（口径与理由见
`backend/models/export.py` 模块 docstring）。

列的两处偏离工作单清单，都写在这里而不是藏在代码里：
1. `attempts INTEGER NOT NULL DEFAULT 0`——工作单要「失败 3 次后置 failed」，那个计数必须
   能活过进程重启；用内存字典记等于每次部署把重试预算清零。
2. 时间列全部可空（started_at/finished_at/expires_at/deleted_at）：queued 时它们本就
   还没有值，用 NULL 表示「未发生」比用 1970-01-01 哨兵值诚实。

沿用本仓既有覆写口径（N2/N7 迁移 docstring 已登记）：状态列 VARCHAR 不用 enum、
不引入外键与 ON DELETE CASCADE（删除靠 PURGE_TARGETS 逐表 rowcount，D-5）、
PK 用 VARCHAR(36)（app 侧 uuid4）。两条索引分别服务 cron 的「取 queued」与 GET 的
「按用户看历史」；`created_at` 都放在第二列，因为这两条读路径都要按时间排序。

无反向迁移原则照旧：downgrade 仅供本地回退，生产只正向执行。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4f6a8b0c2e4'
down_revision: Union[str, Sequence[str], None] = 'c3e5a7b9d1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'data_export_jobs',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('scope', sa.String(length=32), nullable=False, server_default='all'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='queued'),
        sa.Column('file_path', sa.String(length=512), nullable=False, server_default=''),
        sa.Column('byte_size', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error_code', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_data_export_jobs_user_id', 'data_export_jobs', ['user_id'])
    op.create_index(
        'ix_data_export_jobs_user_created', 'data_export_jobs', ['user_id', 'created_at']
    )
    op.create_index(
        'ix_data_export_jobs_status_created', 'data_export_jobs', ['status', 'created_at']
    )


def downgrade() -> None:
    op.drop_index('ix_data_export_jobs_status_created', table_name='data_export_jobs')
    op.drop_index('ix_data_export_jobs_user_created', table_name='data_export_jobs')
    op.drop_index('ix_data_export_jobs_user_id', table_name='data_export_jobs')
    op.drop_table('data_export_jobs')
