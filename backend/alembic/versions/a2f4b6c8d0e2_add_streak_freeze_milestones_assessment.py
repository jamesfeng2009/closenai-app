"""users 表新增 Streak 冻结券/生日/九型字段 + 新建 user_milestones / personality_assessments 表

Revision ID: a2f4b6c8d0e2
Revises: 9e1b3d5f7a2c
Create Date: 2026-08-04 20:00:00.000000

背景：
- PRD P0-F05：Streak 冻结券（免费每月 1 张 / Pro 每月 3 张，断签日自动消耗保住 streak），
  新增 users.streak_freeze_count（剩余券数）与 users.streak_freeze_grant_month（月度发放标记）。
- PRD P0-F05：里程碑徽章（连续 7/30/100/365 天），新建 user_milestones 表记录达成情况。
- PRD P0-F06：星座维度（用户生日推导太阳星座）与九型人格（用户输入 1-9，P1 接入 36 题测试），
  新增 users.birthday 与 users.enneagram_type。
- PRD P0-F06：8 题对话式评估，新建 personality_assessments 表保存答题状态。
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a2f4b6c8d0e2'
down_revision: Union[str, None] = '9e1b3d5f7a2c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS streak_freeze_count INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS streak_freeze_grant_month VARCHAR(7)")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS birthday DATE")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS enneagram_type VARCHAR(2)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_milestones (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            milestone_type VARCHAR(30) NOT NULL,
            achieved_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_user_milestones_user_type "
        "ON user_milestones (user_id, milestone_type)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS personality_assessments (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            answers TEXT,
            status VARCHAR(20) NOT NULL DEFAULT 'in_progress',
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_personality_assessments_user_id "
        "ON personality_assessments (user_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS personality_assessments")
    op.execute("DROP TABLE IF EXISTS user_milestones")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS enneagram_type")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS birthday")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS streak_freeze_grant_month")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS streak_freeze_count")
