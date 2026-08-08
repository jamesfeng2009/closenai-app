"""mood_checkins 新增 checkin_date（用户本地日历日）+ (user_id, checkin_date) 唯一索引

Revision ID: e4f6a8b0c2d4
Revises: f8c9a1b3d5e7
Create Date: 2026-08-07

背景：
- mood_repository.create_once_today 的 IntegrityError 并发兜底依赖
  (user_id, 本地日) 唯一约束，但表上一直没有该约束（死代码）。
- 本迁移新增 mood_checkins.checkin_date（DATE，可空），历史行按 created_at
  取日期近似回填（历史行的用户本地日已不可考，近似即可；NULL 不参与唯一冲突），
  并创建唯一索引 uq_mood_checkins_user_day，使兜底分支成为真实防线。
- 执行前置检查：若历史数据在回填后出现 (user_id, checkin_date) 重复，
  需人工合并后再建索引（迁移不删除任何业务数据）。
- 写法 SQLite/PG 兼容（ADD COLUMN / CREATE UNIQUE INDEX IF NOT EXISTS）。
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e4f6a8b0c2d4'
down_revision: Union[str, None] = 'f8c9a1b3d5e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE mood_checkins ADD COLUMN checkin_date DATE")
    # 历史行回填：按 created_at 取日期（用户本地日不可考，近似回填）
    op.execute(
        "UPDATE mood_checkins SET checkin_date = DATE(created_at) "
        "WHERE checkin_date IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_mood_checkins_user_day "
        "ON mood_checkins (user_id, checkin_date)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_mood_checkins_user_day")
    op.execute("ALTER TABLE mood_checkins DROP COLUMN checkin_date")
