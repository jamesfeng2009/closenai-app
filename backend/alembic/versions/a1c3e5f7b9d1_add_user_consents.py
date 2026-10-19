"""T5-01 同意台账：user_consents（六用途 × 文案版本 × 授予/撤回时刻）

Revision ID: a1c3e5f7b9d1
Revises: e7a2c4b6d8f1
Create Date: 2026-10-18

背景（Baseline §14.2）：合规要求区分六种用途并记录版本与撤回时间，而库里只有
users.ai_consent / users.analytics_consent 两个布尔位——它们答得了「现在开没开」，
答不了「在哪个版本同意了哪一项、什么时候撤的」。本表是台账，布尔位留作兼容期开关
（双写口径见 backend/models/consent.py 的 PURPOSE_LINKED_FLAGS）。

写法沿用桥接批（f1a3b5c7d9e2）：TIMESTAMPTZ、IF NOT EXISTS、无外键（D-5 不用级联删，
注销靠 PURGE_TARGETS 逐表 rowcount）。唯一约束直接进建表语句，PG/SQLite 都吃。

无反向迁移原则照旧：downgrade 仅供本地回退，生产只正向执行。
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a1c3e5f7b9d1'
down_revision: Union[str, Sequence[str], None] = 'e7a2c4b6d8f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_consents (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            purpose VARCHAR(32) NOT NULL,
            version VARCHAR(20) NOT NULL,
            granted_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT uq_user_consents_user_purpose_version
                UNIQUE (user_id, purpose, version)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_consents_user_id ON user_consents (user_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_user_consents_user_id")
    op.execute("DROP TABLE IF EXISTS user_consents")
