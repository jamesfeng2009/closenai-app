"""N2 canonical 权威表：Personal Model 运行时领域层（21 张新表）

Revision ID: c8a2e4b6d0f2
Revises: b7d9e1f3a5c8
Create Date: 2026-10-13 19:30:00.000000

背景（tasks.md N2，V3 01 §27 V1 Scope + 02 §5–§26）：
- 分层改判后 Go Core 持 canonical 权威，而 fact/goal/commitment/pattern/insight/
  outcome/feedback/model_version 此前**全部无表**（pattern/insight 只是
  `models/growth.py:19–20` 的 record_type 枚举值）。本迁移把这层建出来。
- 旧表 memories / chat_messages / growth_records 不动不搬，保留为 source 层与
  展示层；新表增量写入，禁止一次性搬数据（架构方案 §11.3）。
- 《架构与技术方案-2026-09-06》§五-5「承诺=memories 增列，不建新表」在 Go 权威侧
  反转 ⇒ personal_commitments 独立建表（本文件即该改判的落地，登记于 tasks.md N2）。

与 V3 原文的四处覆写（均为已拍板裁定，非本地偏好）：
1. 不用 Postgres ENUM（裁决 §五-4）：状态列一律 VARCHAR，合法取值在 Go domain 层校验，
   沿用 `models/journey.py:67–73` 的既有约定——刻意不加约束，避免脏数据阻塞迁移。
   实测本库 `pg_type where typtype='e'` 为 0 行，加 enum 反而是第一例。
2. 不引入 ON DELETE CASCADE（D-5）：删除走 `user_service.PURGE_TARGETS` 逐表 rowcount。
   代价是关联表（V3 02 §12/14/16/17/20/21/23）本身没有 user_id 就进不了 purge 清单，
   所以本迁移给**每张新表都补 user_id**，含 7 张关联表与 personal_model_versions。
3. PK 用 VARCHAR(36)（app 侧 uuid4 字符串）而非 `UUID + gen_random_uuid()`：
   全库 19 张表现有 PK 皆 VARCHAR(36)，且 pgcrypto 未安装（实测扩展清单无该扩展）。
4. JSON 列用 TEXT 而非 JSONB：全库 `data_type='jsonb'` 为 0 列，
   `growth_records.metadata_json`、`journey_events.payload` 均为 Text 存 JSON 字符串。

V3 文档自身缺列的补齐（带出处，登记于 tasks.md N2）：
- `patterns.scope`：01 §5.2 列为必需字段，02 §13 的 DDL 里没有。
- `insights.claim_type`：03 §14 第 4 关（Claim Type）要求逐条判定，02 §15 无此列。
- `outcomes.outcome_type`：04 §15 定义九类结局，02 §22 的表只有 status/result。
- `evidence.observation`：03 §4 的字段清单含 observation，02 §11 无。
- 各派生表的 `generation_run_id` / `reject_reason` / `model_version`：
  分别由 01 §22 不变量 8（派生物必须指明生成来源与版本）、N3「reject 必带机器可读
  reason 码 + generation_run_id」、N6「派生 claim 记录所依赖版本（陈旧检测）」要求。
- `patterns.fingerprint`：08 §3 要求 `PatternRepository.FindPatternByFingerprint`、
  18 §11 给出指纹字段构成，02 §13 无落库列。

无反向迁移原则照旧：downgrade 仅供本地回退，生产只正向执行。
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c8a2e4b6d0f2'
down_revision: Union[str, Sequence[str], None] = 'b7d9e1f3a5c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 建表顺序 = 依赖顺序（无 FK，但快照与审阅按此顺序读）。
# 每项：表名 -> DDL 主体（不含 CREATE TABLE 外壳与分号）。
_TABLES: tuple[tuple[str, str], ...] = (
    # ── 聚合根与版本面（02 §5–§6、01 §20、02 §32 乐观并发的载体）────────────
    (
        "personal_models",
        """
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            -- 02 §32：UPDATE ... SET current_version = current_version + 1
            --               WHERE id = $1 AND current_version = $2
            -- 0 行即版本冲突 → reload/reconcile/retry，不用全局用户锁。
            current_version BIGINT NOT NULL DEFAULT 0,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (current_version >= 0)
        """,
    ),
    (
        "personal_model_versions",
        """
            id VARCHAR(36) PRIMARY KEY,
            -- D-5 后果：本表带 user_id 才能进 PURGE_TARGETS（不靠级联删除）
            user_id VARCHAR(36) NOT NULL,
            personal_model_id VARCHAR(36) NOT NULL,
            version BIGINT NOT NULL,
            -- 变更原因：02 §6 的示例集（fact_added/fact_confirmed/... /rebuild）
            reason VARCHAR(64) NOT NULL,
            source_event_id VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (personal_model_id, version)
        """,
    ),
    (
        "personal_model_events",
        """
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            personal_model_id VARCHAR(36) NOT NULL,
            event_type VARCHAR(128) NOT NULL,
            aggregate_type VARCHAR(64) NOT NULL,
            aggregate_id VARCHAR(36),
            model_version BIGINT NOT NULL,
            -- 02 §24 用 JSONB；本项目全库 0 个 jsonb 列，沿用 Text 存 JSON 字符串口径
            payload TEXT NOT NULL DEFAULT '{}',
            -- 01 §22 不变量 8：派生物必须能指回产出它的模型调用
            generation_run_id VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    ),
    # ── Source 层薄登记（D-2：只登记类型/时间/指针，不复制原始内容）──────────
    (
        "source_records",
        """
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            -- 02 §3 的 source_record_type 取值（journal_entry/chat_message/...）
            source_type VARCHAR(40) NOT NULL,
            -- D-2 覆写点：V3 在这里放 content TEXT；本项目改为指向既有行的指针，
            -- 避免把 memories/chat_messages/mood_checkins 的原文二次落库。
            source_table VARCHAR(64) NOT NULL,
            source_id VARCHAR(36) NOT NULL,
            occurred_at TIMESTAMPTZ,
            status VARCHAR(16) NOT NULL DEFAULT 'active',
            revision_of VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- 同一既有行只登记一次：重试/重跑管道不得产生第二条登记
            UNIQUE (user_id, source_table, source_id)
        """,
    ),
    # ── Provenance 层（02 §11、03 §4；evidence 是一切派生物的唯一支撑）────────
    (
        "evidence",
        """
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            source_record_id VARCHAR(36) NOT NULL,
            -- 02 §3 evidence_type / evidence_role 的取值落为 String（覆写点 1）
            evidence_type VARCHAR(32) NOT NULL,
            role VARCHAR(24) NOT NULL DEFAULT 'supporting',
            strength NUMERIC(5, 4),
            -- 03 §4 字段清单含 observation，02 §11 的 DDL 无此列
            observation TEXT,
            excerpt TEXT,
            -- 列名用 metadata_json 而非 metadata：前者是全仓既有口径
            -- （growth_records.metadata_json），后者撞 SQLAlchemy Declarative 保留字
            metadata_json TEXT NOT NULL DEFAULT '{}',
            generation_run_id VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (strength IS NULL OR (strength >= 0 AND strength <= 1))
        """,
    ),
    # ── Canonical derived（02 §8–§10）──────────────────────────────────────
    (
        "personal_facts",
        """
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            personal_model_id VARCHAR(36) NOT NULL,
            subject_key VARCHAR(128) NOT NULL,
            predicate VARCHAR(128) NOT NULL,
            -- 02 §8 用 JSONB；同上改 Text。fact 本体不按列展开（02 §8 末段要求）
            object_value TEXT NOT NULL,
            origin VARCHAR(24) NOT NULL,
            -- candidate 是默认态：AI 产出永远是 candidate，终局由 N3 Gate 判（不变量 3）
            status VARCHAR(24) NOT NULL DEFAULT 'candidate',
            confidence NUMERIC(5, 4),
            first_observed_at TIMESTAMPTZ,
            last_observed_at TIMESTAMPTZ,
            confirmed_at TIMESTAMPTZ,
            invalidated_at TIMESTAMPTZ,
            model_version BIGINT,
            generation_run_id VARCHAR(36),
            reject_reason VARCHAR(64),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
        """,
    ),
    (
        "fact_evidence",
        """
            fact_id VARCHAR(36) NOT NULL,
            evidence_id VARCHAR(36) NOT NULL,
            user_id VARCHAR(36) NOT NULL,
            PRIMARY KEY (fact_id, evidence_id)
        """,
    ),
    (
        "personal_goals",
        """
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            personal_model_id VARCHAR(36) NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            started_at TIMESTAMPTZ,
            target_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    ),
    (
        "personal_commitments",
        """
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            personal_model_id VARCHAR(36) NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            due_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    ),
    # ── Pattern 层（02 §13–§14、03 §9–§11）─────────────────────────────────
    (
        "patterns",
        """
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            personal_model_id VARCHAR(36) NOT NULL,
            pattern_type VARCHAR(128) NOT NULL,
            subject_key VARCHAR(128),
            -- 01 §5.2 列为必需，02 §13 的 DDL 缺（V3 文档自身缺陷）
            scope VARCHAR(64),
            description TEXT NOT NULL,
            status VARCHAR(24) NOT NULL DEFAULT 'candidate',
            confidence NUMERIC(5, 4),
            evidence_count INTEGER NOT NULL DEFAULT 0,
            observed_from TIMESTAMPTZ,
            observed_to TIMESTAMPTZ,
            last_supported_at TIMESTAMPTZ,
            confirmed_at TIMESTAMPTZ,
            rejected_at TIMESTAMPTZ,
            expired_at TIMESTAMPTZ,
            detector_version VARCHAR(64),
            -- 08 §3 FindPatternByFingerprint + 18 §11 指纹字段（重跑幂等的落库点）
            fingerprint VARCHAR(160),
            -- 03 §9 六项加权分量的留痕，供「为什么相信」回查（分数本身在 confidence）
            score_detail TEXT,
            model_version BIGINT,
            generation_run_id VARCHAR(36),
            reject_reason VARCHAR(64),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
            CHECK (evidence_count >= 0)
        """,
    ),
    (
        "pattern_evidence",
        """
            pattern_id VARCHAR(36) NOT NULL,
            evidence_id VARCHAR(36) NOT NULL,
            user_id VARCHAR(36) NOT NULL,
            PRIMARY KEY (pattern_id, evidence_id)
        """,
    ),
    # ── Presentation 层（02 §15–§17、03 §14）────────────────────────────────
    (
        "insights",
        """
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            personal_model_id VARCHAR(36) NOT NULL,
            statement TEXT NOT NULL,
            -- 03 §14 第 4 关（Claim Type）逐条判定，02 §15 无此列
            claim_type VARCHAR(32) NOT NULL,
            status VARCHAR(24) NOT NULL DEFAULT 'draft',
            confidence NUMERIC(5, 4),
            time_from TIMESTAMPTZ,
            time_to TIMESTAMPTZ,
            generation_model VARCHAR(128),
            generation_version VARCHAR(64),
            generation_run_id VARCHAR(36),
            reject_reason VARCHAR(64),
            -- 03 §14 九关逐项结论的留痕（N4 验收要求「为什么被拦」可解释）
            gate_detail TEXT,
            model_version BIGINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            published_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
        """,
    ),
    (
        "insight_evidence",
        """
            insight_id VARCHAR(36) NOT NULL,
            evidence_id VARCHAR(36) NOT NULL,
            user_id VARCHAR(36) NOT NULL,
            role VARCHAR(24) NOT NULL DEFAULT 'supporting',
            PRIMARY KEY (insight_id, evidence_id)
        """,
    ),
    (
        "insight_patterns",
        """
            insight_id VARCHAR(36) NOT NULL,
            pattern_id VARCHAR(36) NOT NULL,
            user_id VARCHAR(36) NOT NULL,
            PRIMARY KEY (insight_id, pattern_id)
        """,
    ),
    # ── Proactive 层（02 §19–§21、04 §3–§11）───────────────────────────────
    (
        "proactives",
        """
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            personal_model_id VARCHAR(36) NOT NULL,
            status VARCHAR(24) NOT NULL DEFAULT 'candidate',
            -- 04 §3 七类触发源（N5 的 trigger 必填校验按此白名单）
            trigger_type VARCHAR(64) NOT NULL,
            message TEXT,
            scheduled_at TIMESTAMPTZ,
            sent_at TIMESTAMPTZ,
            responded_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ,
            eligibility_score NUMERIC(5, 4),
            -- 04 §4/§5 硬门 + 发送时刻二次校验的逐项结论
            eligibility_detail TEXT,
            block_reason VARCHAR(64),
            reject_reason VARCHAR(64),
            generation_model VARCHAR(128),
            generation_version VARCHAR(64),
            generation_run_id VARCHAR(36),
            model_version BIGINT,
            -- N5「与现有 deliver 频控链合并为一套管线」的关联点：一条主动只对应一行投递
            journey_event_id VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (eligibility_score IS NULL OR (eligibility_score >= 0 AND eligibility_score <= 1))
        """,
    ),
    (
        "proactive_insights",
        """
            proactive_id VARCHAR(36) NOT NULL,
            insight_id VARCHAR(36) NOT NULL,
            user_id VARCHAR(36) NOT NULL,
            PRIMARY KEY (proactive_id, insight_id)
        """,
    ),
    (
        "proactive_patterns",
        """
            proactive_id VARCHAR(36) NOT NULL,
            pattern_id VARCHAR(36) NOT NULL,
            user_id VARCHAR(36) NOT NULL,
            PRIMARY KEY (proactive_id, pattern_id)
        """,
    ),
    # ── Outcome / 反馈闭环（02 §18、§22–§23、04 §15）───────────────────────
    (
        "outcomes",
        """
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            -- 04 §15 九类；02 §22 的表只有 status/result，缺类型列
            outcome_type VARCHAR(40) NOT NULL,
            status VARCHAR(24) NOT NULL DEFAULT 'recorded',
            result VARCHAR(24) NOT NULL DEFAULT 'unknown',
            description TEXT,
            occurred_at TIMESTAMPTZ,
            -- 04 §15 权威来源仅三种（用户陈述/显式动作/结构化产品事件），LLM 推断只作 candidate
            origin VARCHAR(24) NOT NULL,
            model_version BIGINT,
            generation_run_id VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    ),
    (
        "outcome_links",
        """
            outcome_id VARCHAR(36) NOT NULL,
            -- 02 §23 的 target_type：goal/commitment/pattern/insight/proactive
            target_type VARCHAR(32) NOT NULL,
            target_id VARCHAR(36) NOT NULL,
            user_id VARCHAR(36) NOT NULL,
            PRIMARY KEY (outcome_id, target_type, target_id)
        """,
    ),
    (
        "user_feedback",
        """
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            -- 取值集以 tasks.md N6 的六态为准（answered/confirmed/corrected/rejected/
            -- ignored/dismissed，其中 ignored ≠ 负向），不是 02 §18 的六值
            feedback_type VARCHAR(32) NOT NULL,
            target_type VARCHAR(32) NOT NULL,
            target_id VARCHAR(36) NOT NULL,
            correction_text TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    ),
    # ── 投递幂等（18 §7；outbox 侧归 N7，本批不建）─────────────────────────
    (
        "inbox_messages",
        """
            id VARCHAR(36) PRIMARY KEY,
            -- 18 §4：幂等键是 (message_id, consumer)，不是 message_id 单独
            message_id VARCHAR(128) NOT NULL,
            message_type VARCHAR(128) NOT NULL,
            consumer VARCHAR(64) NOT NULL,
            -- 18 §4 status：pending/processed/failed/dead_lettered
            status VARCHAR(24) NOT NULL DEFAULT 'pending',
            error_code VARCHAR(64),
            attempt_count INTEGER NOT NULL DEFAULT 0,
            received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            processed_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (message_id, consumer),
            CHECK (attempt_count >= 0)
        """,
    ),
)

# 唯一索引单列一组：CREATE INDEX 与 CREATE UNIQUE INDEX 是两个不同的东西，
# 混在一份清单里靠字符串前缀区分最容易看漏（2026-10-13 实测：指纹索引最初被建成
# 普通索引，同一指纹的第二条 pattern 直接写进去了，是 Go 侧幂等测试抓出来的）。
_UNIQUE_INDEXES: tuple[str, ...] = (
    # personal_models：一个用户一个模型（02 §5 的 UNIQUE 语义）
    "ix_personal_models_user_unique ON personal_models (user_id)",
    # 指纹查重是热路径（08 §3 FindPatternByFingerprint），NULL 行不参与唯一性
    "ix_patterns_user_fingerprint_unique "
    "ON patterns (user_id, fingerprint) WHERE fingerprint IS NOT NULL",
)

_INDEXES: tuple[str, ...] = (
    # 02 §29 组合索引（偏索引不建：状态白名单的唯一事实源在 Go domain，不落 DDL 两次）
    "ix_personal_model_versions_user_created "
    "ON personal_model_versions (user_id, created_at)",
    "ix_personal_model_events_user_created "
    "ON personal_model_events (user_id, created_at)",
    "ix_personal_model_events_aggregate "
    "ON personal_model_events (aggregate_type, aggregate_id)",
    "ix_personal_model_events_type_created "
    "ON personal_model_events (event_type, created_at)",
    "ix_personal_model_events_run "
    "ON personal_model_events (generation_run_id)",
    "ix_source_records_user_occurred ON source_records (user_id, occurred_at)",
    "ix_source_records_user_type ON source_records (user_id, source_type)",
    "ix_source_records_pointer ON source_records (source_table, source_id)",
    "ix_evidence_user_source ON evidence (user_id, source_record_id)",
    "ix_evidence_user_created ON evidence (user_id, created_at)",
    "ix_evidence_run ON evidence (generation_run_id)",
    "ix_personal_facts_user_status ON personal_facts (user_id, status)",
    "ix_personal_facts_user_predicate ON personal_facts (user_id, predicate)",
    "ix_personal_facts_model_status "
    "ON personal_facts (personal_model_id, status)",
    "ix_personal_facts_run ON personal_facts (generation_run_id)",
    "ix_fact_evidence_user ON fact_evidence (user_id, fact_id)",
    "ix_fact_evidence_evidence ON fact_evidence (evidence_id)",
    "ix_personal_goals_user_status ON personal_goals (user_id, status)",
    "ix_personal_goals_model ON personal_goals (personal_model_id, status)",
    "ix_personal_commitments_user_status "
    "ON personal_commitments (user_id, status)",
    "ix_patterns_user_status ON patterns (user_id, status)",
    "ix_patterns_user_updated ON patterns (user_id, updated_at)",
    "ix_patterns_user_type ON patterns (user_id, pattern_type)",
    "ix_patterns_model_active ON patterns (personal_model_id, status)",
    "ix_patterns_run ON patterns (generation_run_id)",
    "ix_pattern_evidence_user ON pattern_evidence (user_id, pattern_id)",
    "ix_pattern_evidence_evidence ON pattern_evidence (evidence_id)",
    "ix_insights_user_status ON insights (user_id, status)",
    "ix_insights_user_published ON insights (user_id, published_at)",
    "ix_insights_model_status ON insights (personal_model_id, status)",
    "ix_insights_claim_type ON insights (user_id, claim_type)",
    "ix_insights_run ON insights (generation_run_id)",
    "ix_insight_evidence_user ON insight_evidence (user_id, insight_id)",
    "ix_insight_evidence_evidence ON insight_evidence (evidence_id)",
    "ix_insight_patterns_user ON insight_patterns (user_id, insight_id)",
    "ix_insight_patterns_pattern ON insight_patterns (pattern_id)",
    "ix_proactives_user_due "
    "ON proactives (user_id, status, scheduled_at)",
    "ix_proactives_model_status ON proactives (personal_model_id, status)",
    "ix_proactives_trigger ON proactives (user_id, trigger_type)",
    "ix_proactives_journey_event ON proactives (journey_event_id)",
    "ix_proactives_run ON proactives (generation_run_id)",
    "ix_proactive_insights_user ON proactive_insights (user_id, proactive_id)",
    "ix_proactive_insights_insight ON proactive_insights (insight_id)",
    "ix_proactive_patterns_user ON proactive_patterns (user_id, proactive_id)",
    "ix_proactive_patterns_pattern ON proactive_patterns (pattern_id)",
    "ix_outcomes_user_occurred ON outcomes (user_id, occurred_at)",
    "ix_outcomes_user_type ON outcomes (user_id, outcome_type)",
    "ix_outcome_links_user ON outcome_links (user_id, target_type, target_id)",
    "ix_outcome_links_target ON outcome_links (target_type, target_id)",
    "ix_user_feedback_user_created ON user_feedback (user_id, created_at)",
    "ix_user_feedback_target ON user_feedback (user_id, target_type, target_id)",
    "ix_inbox_messages_type ON inbox_messages (message_type, status)",
    "ix_inbox_messages_received ON inbox_messages (received_at)",
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
