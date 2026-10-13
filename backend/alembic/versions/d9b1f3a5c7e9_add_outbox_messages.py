"""N7 投递面：outbox_messages（18 §3 的 13 列 + 3 个偏离列）

Revision ID: d9b1f3a5c7e9
Revises: c8a2e4b6d0f2
Create Date: 2026-10-13 22:10:00.000000

背景（tasks.md N7，裁决七-6 N7-1）：N2 已把 inbox_messages 建出来（`c8a2e4b6d0f2:401-418`），
但**全仓没有 outbox 表**——而 18 §2 与 19 §3 的权威事务形状是「同一笔事务里写 canonical 状态
+ outbox 行」，没有这张表就没有「commit 后崩溃不丢投递」这条保证。

为什么是**新迁移**而不是改 0002：`c8a2e4b6d0f2` 在本机与已升级环境都是 applied 状态，
改已应用的件等于让 alembic 的版本记录与真实 schema 漂移（`test_migration_graph_is_single_head`
会红，而红的是闸不是问题）。

与 V3 原文的三处偏离（全部登记在裁决七-6 N7-1，不是本地偏好）：
1. 加 `user_id`——D-5 不用 ON DELETE CASCADE，删除靠 `PURGE_TARGETS` 逐表 rowcount，
   不带 user_id 就等于「payload 里的个人内容删不净」。payload 存的是事件正文（裁决 §五-1
   的口径下它是用户可读文本），属于个人数据面。
2. 加 `correlation_id`——17 §6「同一次用户动作产生的所有消息共享一个 correlation ID」要求
   这一维度，而 18 §3 的字段清单里没有它。
3. 加 `causation_id`——17 §7 明确 causation（immediate parent）与 correlation 不是同一件事；
   缺它就没法回答「这条消息是哪条入站消息触发的」（17 §9 毒消息排查的唯一线索）。
   两列都取 NOT NULL DEFAULT ''，空串＝无父：刻意不用 NULL，否则 sqlc 侧要处理 *string，
   「没有父」和「没填」在类型上就分不开了。

沿用 N2 的四条覆写口径（原文在 `c8a2e4b6d0f2` 的 docstring）：状态列 VARCHAR 不用 enum、
不引入外键与 CASCADE、PK 用 VARCHAR(36)（app 侧 uuid4）、JSON 用 TEXT 不用 JSONB。
`available_at` 一列两用（N7-2）：既是失败退避的「何时可再取」，也是 `publishing` 的租约到期
时刻——所以不另加 `locked_by`/`lease_token` 列，崩溃 worker 留下的孤儿行到期自动可被别的
worker 重取（17 §2 末句「The worker must tolerate restarts」）。

无反向迁移原则照旧：downgrade 仅供本地回退，生产只正向执行。
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd9b1f3a5c7e9'
down_revision: Union[str, Sequence[str], None] = 'c8a2e4b6d0f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 与 N2 件同一份结构（_TABLES/_INDEXES/_UNIQUE_INDEXES），让 test_canonical_schema.py
# 的三道闸能按同一口径逐列对账；本批只有一张表，仍然按列表写，免得闸为了一件迁移特判。
_TABLES: tuple[tuple[str, str], ...] = (
    (
        "outbox_messages",
        """
            id VARCHAR(36) PRIMARY KEY,
            -- 18 §3 字段清单逐字起：message_id 是逻辑操作边界的稳定 ID（18 §10），
            -- 本项目取 personal_model_events.id——权威事件行本身就是那个边界。
            message_id VARCHAR(128) NOT NULL,
            message_type VARCHAR(128) NOT NULL,
            -- 18 §3：编码版本。本批 payload 是 JSON 文本，切 protobuf 属 O1，
            -- 有了这一列就是 additive 变更（只改编码，不改表、不改幂等闸）。
            schema_version INTEGER NOT NULL DEFAULT 1,
            -- 14 §4 的 topic routing key。唯一事实源在 Go 的 internal/domain.outboxRouting，
            -- 这里只存值不判合法性（裁决 §五-1：白名单不落 DDL 两次）。
            routing_key VARCHAR(128) NOT NULL,
            -- 偏离①：D-5 无级联 ⇒ 带 user_id 才进得了 PURGE_TARGETS
            user_id VARCHAR(36) NOT NULL,
            -- 偏离②/③：17 §6/§7 的关联面，18 §3 漏列。''＝无父消息
            correlation_id VARCHAR(128) NOT NULL DEFAULT '',
            causation_id VARCHAR(128) NOT NULL DEFAULT '',
            -- 14 §10「消息体要有界、大上下文用 ID 引用」：payload 只存事件行原文，
            -- 聚合以 aggregate_type/aggregate_id 的形式在其中被引用，不嵌正文。
            payload TEXT NOT NULL DEFAULT '{}',
            -- 18 §4 五态：pending/publishing/published/failed/dead_lettered
            status VARCHAR(24) NOT NULL DEFAULT 'pending',
            -- 认领时 ++（不是发布失败时才 ++），所以租约到期的重取也计入尝试次数
            attempt_count INTEGER NOT NULL DEFAULT 0,
            -- 两用：失败退避的「何时可再取」+ publishing 的租约到期时刻（N7-2）
            available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            published_at TIMESTAMPTZ,
            last_error VARCHAR(512),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- 18 §10 的稳定 ID 语义落库形式：同一权威事件最多一条投递意图
            UNIQUE (message_id),
            CHECK (attempt_count >= 0),
            CHECK (schema_version >= 1)
        """,
    ),
)

_UNIQUE_INDEXES: tuple[str, ...] = ()

_INDEXES: tuple[str, ...] = (
    # D-5 的后果：purge 靠 `DELETE … WHERE user_id = ?`，没有 user_id 打头的索引就是顺扫
    "ix_outbox_messages_user ON outbox_messages (user_id, created_at)",
    # worker 认领热路径（N7-9）：谓词是 (status, available_at)，ORDER BY available_at, id
    "ix_outbox_messages_claim ON outbox_messages (status, available_at)",
    # 运维面：按消息类型看积压/死信（17 §9 的 failure record 要看得到）
    "ix_outbox_messages_type ON outbox_messages (message_type, status)",
)


def upgrade() -> None:
    for name, body in _TABLES:
        op.execute(f"CREATE TABLE IF NOT EXISTS {name} (\n{body}\n)")
    for stmt in _INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {stmt}")
    for stmt in _UNIQUE_INDEXES:
        op.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {stmt}")


def downgrade() -> None:
    # 项目约定：仅正向执行，反向迁移供本地回退用，生产不执行
    for stmt in _INDEXES + _UNIQUE_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {stmt.split()[0]}")
    for name, _ in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {name}")
