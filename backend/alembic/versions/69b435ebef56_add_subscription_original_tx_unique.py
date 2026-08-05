"""subscriptions.original_transaction_id 唯一约束（partial unique index）

Revision ID: 69b435ebef56
Revises: b3c5d7e9f1a2
Create Date: 2026-08-05 18:00:00.000000

背景（B3 修复）：
- subscriptions.original_transaction_id 此前无唯一约束，iap_service._validate
  先查后插存在竞态窗口——两个并发请求可同时通过跨账号检查并各自写入，
  导致同一收据绑定多账号（白嫖）。
- 应用层已用 SAVEPOINT + IntegrityError 兜底重查；DB 层再加 partial unique
  index 做最后一道防线，保证同一 original_transaction_id 全局唯一。
- 用 partial unique index（postgresql_where ... IS NOT NULL）而非列级
  unique=True：现有数据可能存在 NULL（未走收据校验的旧订阅）或历史重复，
  partial index 仅对非 NULL 值强制唯一，避免迁移因存量数据冲突而失败。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '69b435ebef56'
down_revision: Union[str, Sequence[str], None] = 'b3c5d7e9f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_subscriptions_original_tx_unique',
        'subscriptions',
        ['original_transaction_id'],
        unique=True,
        postgresql_where=sa.text('original_transaction_id IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index(
        'ix_subscriptions_original_tx_unique',
        table_name='subscriptions',
    )
