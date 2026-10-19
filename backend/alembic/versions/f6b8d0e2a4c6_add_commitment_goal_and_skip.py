"""T4-01 行动闭环的库面：personal_commitments 加 goal_id 与 skipped_at

Revision ID: f6b8d0e2a4c6
Revises: e5a7b9d1f3a5
Create Date: 2026-10-19

为什么（`CLOSENAI_V3_REMEDIATION_PLAN.md:197` 的决策，`tasks.md` 的基线把它列成缺口 6）：
「行动」这一格现在有两个家——Python 的 `commitment_service` 把它降级成记忆标签＋journey
事件，Go 的 canonical 侧 `personal_commitments`（N2 建，02 §10 是「不建新表」那句在权威侧的
反转落点）却没有另外两样东西：一列 `goal_id`，一列 `skipped_at`。缺前者 ⇒ 行动挂不到目标下，
`洞察 → 目标 → 行动 → 结果` 这条链在库里断成两截；缺后者 ⇒「跳过」只能冒充「完成」
（写 `completed_at`）或者冒充「没发生」（改 `status` 而没时间），而基线要求跳过是一等状态。

形状（逐条对着既有口径，不新造例外）：
- 两列一律**可空**，这是 expand 形：老代码不读不写它们，与本项目「仅正向执行」的生产口径兼容；
  downgrade 仅供本地回退（同 `c8a2e4b6d0f2`、`e5a7b9d1f3a5` 末尾那句约定）。
- `goal_id VARCHAR(36)`，**不加外键**：D-5 让删号走 `PURGE_TARGETS` 逐表 rowcount，FK 会把
  purge 顺序变成硬依赖（`test_canonical_schema.py` 的库级闸 `set(fks) <= {"refresh_tokens"}`
  就是盯这一条的）。目标的归属靠 `user_id` 谓词而不是靠库级引用完整性——`CreateAction`
  校验「goal_id 属于本人且 active」在 Go 的 domain 层做，这一列本身不构成约束。
- `skipped_at TIMESTAMPTZ NULL`：与 trace 件那列 `VARCHAR(128) NOT NULL DEFAULT ''` 刻意不同。
  时间戳没有「空串」这个形状，NULL 就是「没有发生过这次跳过」，sqlc 侧是 `*time.Time`，
  类型上分得清「没跳过」与「跳过了但没记时间」。
- 索引 `(user_id, goal_id, status)`：读路径是「某人名下、某个目标下的行动按状态筛」
  （`ListCommitments` 与 T4-02 的 `GetGoalState` 都走这一条），前导列是 `user_id`
  因此同时满足 D-5「每张 canonical 表都要有 user_id 打头的索引」那条闸。
  工单 §怎么做.1 给的字面 SQL 就是这三列，没有按实况改判。
- **不给 `status` 加 CHECK 或 enum**：裁决 §五-4 定了合法取值在 Go 的 domain 层校验，
  `skipped`／`cancelled` 这两个新值与既有 `active`／`completed` 用的是同一套词汇表，
  库里加约束反而会让一次状态集合的改判变成一次迁移。

`personal_goals` 这一侧不需要动：目标状态沿用表里已有的 `status`／`completed_at`，
T4-02 的 `UpdateGoalStatus` 直接写这两列。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6b8d0e2a4c6'
down_revision: Union[str, Sequence[str], None] = 'e5a7b9d1f3a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "personal_commitments",
        sa.Column("goal_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "personal_commitments",
        sa.Column("skipped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_personal_commitments_goal",
        "personal_commitments",
        ["user_id", "goal_id", "status"],
    )


def downgrade() -> None:
    # 项目约定：仅正向执行，反向迁移供本地回退用，生产不执行。
    op.drop_index("ix_personal_commitments_goal", table_name="personal_commitments")
    op.drop_column("personal_commitments", "skipped_at")
    op.drop_column("personal_commitments", "goal_id")
