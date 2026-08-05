"""share_exports 导出缓存表 + subscriptions 试用字段

Revision ID: c4d6e8f0a2b4
Revises: 9e1b3d5f7a2c
Create Date: 2026-08-04 20:00:00.000000

背景：
- P1-F04：导出缓存——同一用户同 period 同周期（period_key）的导出结果只生成
  一次（唯一约束 user_id+period+period_key），重复请求直接返回缓存，
  不重复调 LLM、不重复写 growth_records。
- P1-F02 §4：7 天免费试用，到期前 24 小时推送提醒——subscriptions 增加
  is_trial（收据 is_trial_period 解析）与 trial_reminder_sent（防重复提醒）。
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c4d6e8f0a2b4'
down_revision: Union[str, Sequence[str], None] = '9e1b3d5f7a2c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS share_exports (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            period VARCHAR(20) NOT NULL,
            period_key VARCHAR(20) NOT NULL,
            payload TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT uq_share_exports_user_period_key
                UNIQUE (user_id, period, period_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_share_exports_user_id ON share_exports (user_id)"
    )
    op.execute(
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS is_trial BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_reminder_sent BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE subscriptions DROP COLUMN IF EXISTS trial_reminder_sent")
    op.execute("ALTER TABLE subscriptions DROP COLUMN IF EXISTS is_trial")
    op.execute("DROP TABLE IF EXISTS share_exports")
