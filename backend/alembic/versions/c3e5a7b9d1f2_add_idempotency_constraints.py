"""T5-03 幂等唯一约束：chat_messages.idempotency_key ＋ journey_events 日序唯一

Revision ID: c3e5a7b9d1f2
Revises: b2d4f6a8c0e2
Create Date: 2026-10-18

为什么（Baseline 不变量 9）：主动消息与剧本事件的「今天只发一条」此前只靠
`FOR UPDATE` 行锁 + 当日计数（cron_service）与仓库层查重（journey_repository）。
行锁在多实例/重试下不是防线——两个事务可以各自拿到各自的锁、都通过去重检查，
于是同一本地日写出两行。表上没有唯一约束时，"不会重复"这句话没有兜底。

两处部分唯一索引的 WHERE 条件都带理由，不是随手加的：
1. chat_messages 只管 proactive：普通对话回复一天几十条，全表唯一等于把功能打死；
   idempotency_key IS NOT NULL 让历史行与所有非主动写入天然不参与冲突。
2. journey_events 排除 commitment_follow_up：§5.18 同一日可以回访**不同**承诺
   （journey_repository.schedule 的 dedupe=False 分支就是这条业务口径的实现），
   全 kind 唯一会把一次合法写入变成 500。
3. journey_events 排除 status='skipped'：重复行去重时保留最早一条、其余置 skipped
   （永不删行的既有口径），它们继续占着 (user_id,event_kind,day_no) 会让索引建不起来。

回填不在本件里做：`scripts/backfill_idempotency.py`（dry-run 默认）负责给历史
proactive 行补键——补键要先知道每行的用户本地日，那是业务判定，不是 DDL。

无反向迁移原则照旧：downgrade 仅供本地回退，生产只正向执行。
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c3e5a7b9d1f2'
down_revision: Union[str, Sequence[str], None] = 'b2d4f6a8c0e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE chat_messages ADD COLUMN idempotency_key VARCHAR(128)")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_messages_proactive_key
            ON chat_messages (idempotency_key)
            WHERE message_type = 'proactive' AND idempotency_key IS NOT NULL
        """
    )

    # journey_events 去重：同一 (user_id, event_kind, day_no) 保留最早一条，其余置
    # skipped——不删行，审计链保持完整。判据写成「存在更早的兄弟行」而不是窗口函数：
    # array_agg/ROW_NUMBER 只有 PG 有，本件在 SQLite 上也要能跑（本地开发库、往返测试）。
    op.execute(
        """
        UPDATE journey_events
            SET status = 'skipped'
          WHERE event_kind <> 'commitment_follow_up'
            AND status <> 'skipped'
            AND EXISTS (
                SELECT 1 FROM journey_events AS earlier
               WHERE earlier.user_id = journey_events.user_id
                 AND earlier.event_kind = journey_events.event_kind
                 AND earlier.day_no = journey_events.day_no
                 AND earlier.status <> 'skipped'
                 AND (earlier.created_at < journey_events.created_at
                      OR (earlier.created_at = journey_events.created_at
                          AND earlier.id < journey_events.id))
            )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_journey_events_user_kind_day
            ON journey_events (user_id, event_kind, day_no)
            WHERE event_kind <> 'commitment_follow_up' AND status <> 'skipped'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_journey_events_user_kind_day")
    op.execute("DROP INDEX IF EXISTS uq_chat_messages_proactive_key")
    op.execute("ALTER TABLE chat_messages DROP COLUMN idempotency_key")
