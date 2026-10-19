"""T5-02 免打扰时段：users.quiet_start / quiet_end（用户本地 TIME，NULL＝关闭）

Revision ID: b2d4f6a8c0e2
Revises: a1c3e5f7b9d1
Create Date: 2026-10-18

两列都是 NULL able、无默认值：存量用户一律等于「未开启免打扰」，不猜一个窗口。
存 TIME 而非 VARCHAR(5)：窗口判定要跨零点比较，字符串比较在 "09:00" 这类等宽格式上
碰巧正确，但一旦写入端出现 "9:00" 就会静默判错——让数据库类型替口径兜底。

无反向迁移原则照旧：downgrade 仅供本地回退，生产只正向执行。
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b2d4f6a8c0e2'
down_revision: Union[str, Sequence[str], None] = 'a1c3e5f7b9d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN quiet_start TIME")
    op.execute("ALTER TABLE users ADD COLUMN quiet_end TIME")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN quiet_end")
    op.execute("ALTER TABLE users DROP COLUMN quiet_start")
