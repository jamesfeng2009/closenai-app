"""T1-02 PostHog 侧擦除失败的重试台账：posthog_erasure_retries

Revision ID: e5a7b9d1f3a5
Revises: d4f6a8b0c2e4
Create Date: 2026-10-18

为什么（工作单 T1-02「怎么做」第 3 条的验收：PostHog 抛错 ⇒ 用户删除仍成功且留下可重试标记）：
`purge_user` 删完 43 张表后要调 `erase_person_data` 抹 PostHog 侧的人与事件，而它失败时
**刻意不抛**（分析平台抖动不能把注销卡在半途，见 `analytics_service.py:128`）。于是失败
只剩一条 INFO 日志，没有任何东西会再去看那个用户：`users` 行已经删了，
`/cron/purge-deleted-users` 按 `users.status='deleted'` 扫描，扫不到它——外部平台的残留
从这一刻起永久无人认领。「本地删干净」和「PostHog 还留着你的全部行为事件」是同一句承诺
的两半，只兑现一半等于没兑现。

为什么状态必须是一张新表，工作单给的两个选项里排除了 `users.purge_error`：
`purge_user` 在同一段事务里把 `users` 行删掉。把「还没擦干净」写在进行中的删除对象上，
下一轮没有任何读路径能找到它（cron 的谓词是 `status='deleted'`，而行已消失）。所以
台账必须在被删对象的**外面**，且不被 `PURGE_TARGETS` 的通用循环顺手删掉——这张表因此
是既有覆盖闸的第一个显式豁免项（`user_service.PURGE_EXEMPT_TABLES`，带理由登记）。

它不是 J4 否掉的墓碑表：J4 否掉的是「只有孤立 user_id、没有消费方」的留存表。这张表每行
都有唯一消费方（重试路径），并且自我销毁——擦除成功即删行，超过
`settings.posthog_erasure_retry_max_days`（默认 7 天）先打一条 ERROR 告警再删行，
终态只留在日志里。表按设计不会累积。

形状沿用本仓既有覆写口径：无外键、无级联（D-5）、PK 用 VARCHAR(36)（app 侧 uuid4）、
`user_id` 唯一（QStash 重试与多 worker 不许把同一用户叠成多行，同时充当 upsert 幂等键）。
`next_attempt_at` 既是退避记账位也是 CAS 认领谓词——SQLite 不渲染 FOR UPDATE，
`UPDATE … WHERE next_attempt_at <= :now` 的 rowcount 在两种库上语义一致（同 T1-01 的认领）。

无反向迁移原则照旧：downgrade 仅供本地回退，生产只正向执行。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5a7b9d1f3a5'
down_revision: Union[str, Sequence[str], None] = 'd4f6a8b0c2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'posthog_erasure_retries',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('last_reason', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('first_failed_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('user_id', name='uq_posthog_erasure_retries_user_id'),
    )
    op.create_index(
        'ix_posthog_erasure_retries_next_attempt',
        'posthog_erasure_retries',
        ['next_attempt_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_posthog_erasure_retries_next_attempt',
                  table_name='posthog_erasure_retries')
    op.drop_table('posthog_erasure_retries')
