"""AI 生成留痕表 ai_generation_runs + 三张派生表的 generation_run_id 回链

Revision ID: b7d9e1f3a5c8
Revises: a7c1e9b4d2f8
Create Date: 2026-10-13 11:00:00.000000

背景（V3 分层改判 O4，架构方案 §二 M6 合规落点的工程化）：
- 此前「哪段文字是 AI 写的」只能靠代码里的调用点推断，事后无法回查：
  GB 45438-2025 的 AIGC 标识要求可追溯，成本与降级观测也要数据。
- ai_generation_runs 一行 = 一次模型调用（complete/stream/embed），含 provider/model/
  purpose/耗时/token 与结局（ok|error|fallback）。fallback 即「产出被安全闸门丢弃、
  调用方回退模板」，是唯一能量化「模板兜底发生率」的数据源。
- memories / personality_profiles / growth_records 增 generation_run_id：
  只在产出可用时写，空值即「这行内容不是 AI 产出的」——Go Core 接管权威态时
  （N3 领域门控）据此区分 AI candidate 与用户自述。
- 无反向迁移原则照旧：本文件仅供本地回退，生产只正向执行。
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b7d9e1f3a5c8'
down_revision: Union[str, Sequence[str], None] = 'a7c1e9b4d2f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_generation_runs (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36),
            trace_id VARCHAR(64),
            op VARCHAR(12) NOT NULL DEFAULT 'complete',
            purpose VARCHAR(80),
            provider VARCHAR(20),
            model VARCHAR(64),
            lite BOOLEAN NOT NULL DEFAULT FALSE,
            language VARCHAR(10),
            status VARCHAR(12) NOT NULL DEFAULT 'ok',
            fallback_reason VARCHAR(40),
            error_type VARCHAR(60),
            input_chars INTEGER,
            output_chars INTEGER,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            latency_ms INTEGER,
            meta TEXT,
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_generation_runs_user_id "
        "ON ai_generation_runs (user_id)"
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ai_generation_runs_user_created
            ON ai_generation_runs (user_id, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ai_generation_runs_purpose_created
            ON ai_generation_runs (purpose, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ai_generation_runs_status_created
            ON ai_generation_runs (status, created_at)
        """
    )

    # 派生行回链：growth_records 的周报正文由「正文段 + 下周预测段」两次调用拼成，
    # 与 related_memory_ids 同口径用逗号存多值，故列宽取 80
    op.execute(
        "ALTER TABLE memories ADD COLUMN IF NOT EXISTS generation_run_id VARCHAR(36)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memories_generation_run_id "
        "ON memories (generation_run_id)"
    )
    op.execute(
        "ALTER TABLE personality_profiles ADD COLUMN IF NOT EXISTS generation_run_id VARCHAR(36)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_personality_profiles_generation_run_id "
        "ON personality_profiles (generation_run_id)"
    )
    op.execute(
        "ALTER TABLE growth_records ADD COLUMN IF NOT EXISTS generation_run_id VARCHAR(80)"
    )


def downgrade() -> None:
    # 项目约定：仅正向执行，反向迁移供本地回退用，生产不执行
    op.execute("ALTER TABLE growth_records DROP COLUMN IF EXISTS generation_run_id")
    op.execute("ALTER TABLE personality_profiles DROP COLUMN IF EXISTS generation_run_id")
    op.execute("DROP INDEX IF EXISTS ix_memories_generation_run_id")
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS generation_run_id")
    op.execute("DROP TABLE IF EXISTS ai_generation_runs")
