"""add referral_code / referred_by_code to users

M4 分享卡体系（架构方案 §二 M4 / 决策点 3）：分享卡上的深链统一为
`https://closenai.app/r/{referral_code}`。新用户经他人链接安装时把来源 code 记在
`referred_by_code`，安装归因即「谁带来了这个新用户」，不需要额外的点击表。

referral_code **惰性生成**（首次导出分享卡时才写，见 user_repository.ensure_referral_code），
不在注册路径预生成：本迁移面对的是已有全量用户，预生成要么给老用户回填随机码、
要么让不分享的用户永远挂着 NULL 走两条分支。唯一性用 UNIQUE 索引兜底，
索引建在两列可空之后，历史行全为 NULL（PG/SQLite 的 UNIQUE 均不约束 NULL）。

两列都不建外键：referral 是归因标签而非关系引用，指向的用户可能已注销
（users 软删除 + 同标识可重注册），外键会把归因数据锁成删除约束。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a7c1e9b4d2f8'
down_revision: Union[str, None] = 'f1a3b5c7d9e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('referral_code', sa.String(length=12), nullable=True)
        )
        batch_op.add_column(
            sa.Column('referred_by_code', sa.String(length=12), nullable=True)
        )
        batch_op.create_unique_constraint(
            'uq_users_referral_code', ['referral_code']
        )


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('uq_users_referral_code', type_='unique')
        batch_op.drop_column('referred_by_code')
        batch_op.drop_column('referral_code')
