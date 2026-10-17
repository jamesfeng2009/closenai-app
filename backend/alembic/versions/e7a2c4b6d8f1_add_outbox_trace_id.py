"""trace 全链路批 T-B：outbox_messages 加 trace_id 列（工作单 T-2）

Revision ID: e7a2c4b6d8f1
Revises: d9b1f3a5c7e9
Create Date: 2026-10-17 11:00:00.000000

背景：`X-Trace-Id` 在 HTTP 两侧已通，但 outbox 行不携带 trace ⇒ Go 发布命令时
无值可写上 AMQP header，「用户一次点击」与「异步两段」永远是两条 id。
这一列是异步段续链的唯一载体：emit 从 ctx 写入，ClaimOutboxBatch 带回，
`amqp/publisher.go` 非空才投 `x-trace-id` header。

形状照 N7-1 对 correlation_id/causation_id 的既定取舍：VARCHAR(128) NOT NULL
DEFAULT ''，空串＝「产生这一行的上下文没有 trace」（后台 cron、迁移前的存量行）。
刻意不用 NULL——NULL 会让 sqlc 侧变 *string，「没有」与「没填」在类型上分不清。

expand 形：老代码不读不写这列、默认值兜住，故与「仅正向执行」的生产口径兼容；
downgrade 仅供本地回退（项目约定，见 `c8a2e4b6d0f2:508` 同一行注释）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a2c4b6d8f1'
down_revision: Union[str, None] = 'd9b1f3a5c7e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "outbox_messages",
        sa.Column(
            "trace_id",
            sa.String(length=128),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    # 项目约定：仅正向执行，反向迁移供本地回退用，生产不执行
    op.drop_column("outbox_messages", "trace_id")
