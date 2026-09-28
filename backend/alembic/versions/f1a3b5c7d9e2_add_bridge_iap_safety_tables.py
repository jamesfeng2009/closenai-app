"""过桥编排 / 买断权益 / 危机审计四张新表 + memories.labels 增列

Revision ID: f1a3b5c7d9e2
Revises: c5d7e9f1a3b6
Create Date: 2026-09-29 10:00:00.000000

背景（架构方案 §三 数据模型变更汇总，单次正向迁移）：
- journey_events：第一周剧本（§5.19.3）与 §5.15a 等级情感回报共用的一张确定性事件表，
  onboarding 完成事务内预写 pending 行，由 dispatch_journey_events 按 scheduled_at 投递。
- onboarding_profiles：性格初读卡（§5.19.2 B01）答案与断言结果。
- user_entitlements：Life Archive 一次性买断（M6），与 subscriptions/tier 解耦。
- safety_events：§5.16 ④ 危机转介命中审计（两类触发词，24h 人工抽检）。
- memories.labels：§5.18 承诺回访状态，遵循"无新表"原则挂在记忆行上。
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f1a3b5c7d9e2'
down_revision: Union[str, Sequence[str], None] = 'c5d7e9f1a3b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS journey_events (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            day_no INTEGER NOT NULL DEFAULT 0,
            event_kind VARCHAR(30) NOT NULL,
            status VARCHAR(12) NOT NULL DEFAULT 'pending',
            scheduled_at TIMESTAMPTZ NOT NULL,
            sent_at TIMESTAMPTZ,
            payload TEXT,
            memory_refs TEXT,
            acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_journey_events_user_id ON journey_events (user_id)"
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_journey_events_status_scheduled
            ON journey_events (status, scheduled_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_journey_events_user_kind_day
            ON journey_events (user_id, event_kind, day_no)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS onboarding_profiles (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL UNIQUE,
            persona_type VARCHAR(30),
            answers TEXT,
            first_read TEXT,
            language VARCHAR(10),
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_entitlements (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            product_id VARCHAR(64) NOT NULL,
            scope VARCHAR(20) NOT NULL,
            transaction_id VARCHAR(255) NOT NULL UNIQUE,
            original_transaction_id VARCHAR(255),
            purchase_date TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ,
            environment VARCHAR(12),
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_entitlements_user_id "
        "ON user_entitlements (user_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS safety_events (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            kind VARCHAR(30) NOT NULL,
            source VARCHAR(30) NOT NULL DEFAULT 'user_message',
            matched_terms TEXT,
            hotline VARCHAR(60),
            language VARCHAR(10),
            excerpt VARCHAR(280),
            advice_blocked BOOLEAN NOT NULL DEFAULT FALSE,
            review_status VARCHAR(16) NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_safety_events_user_id ON safety_events (user_id)"
    )

    op.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS labels TEXT")


def downgrade() -> None:
    # 项目约定：仅正向执行，反向迁移供本地回退用，生产不执行
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS labels")
    op.execute("DROP TABLE IF EXISTS safety_events")
    op.execute("DROP TABLE IF EXISTS user_entitlements")
    op.execute("DROP TABLE IF EXISTS onboarding_profiles")
    op.execute("DROP TABLE IF EXISTS journey_events")
