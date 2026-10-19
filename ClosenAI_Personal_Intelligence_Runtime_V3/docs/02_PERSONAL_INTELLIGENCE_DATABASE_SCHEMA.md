# ClosenAI Personal Intelligence PostgreSQL Schema

> Document: `02_PERSONAL_INTELLIGENCE_DATABASE_SCHEMA.md`
> Version: V1.0
> Status: Engineering Baseline
> Date: 2026-10
>
> This schema implements `01_PERSONAL_MODEL_DOMAIN_ARCHITECTURE.md`.
>
> Database: PostgreSQL
> Primary key strategy: UUID
> Time: `timestamptz`
> JSON: `jsonb` only for extensible payloads, not core relational state.

---

# 1. Design Principles

## 1.1 Source of Truth

Canonical state lives in relational domain tables.

```text
source_records
personal_facts
evidence
patterns
insights
proactives
outcomes
```

LLM context snapshots and JSON blobs are derived caches.

---

## 1.2 Immutable History

Historical source and domain events must remain auditable.

Prefer:

```text
append event
+
new state
```

over silent destructive mutation.

---

## 1.3 Explicit Provenance

Every derived object must be traceable.

```text
Insight
 → Pattern
 → Evidence
 → SourceRecord
```

---

# 2. PostgreSQL Extensions

Recommended:

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

UUID generation:

```sql
gen_random_uuid()
```

---

# 3. Enumerations

Use PostgreSQL enums only for states that are stable and strongly bounded.

```sql
CREATE TYPE source_record_type AS ENUM (
    'journal_entry',
    'chat_message',
    'voice_transcript',
    'note',
    'mood_checkin',
    'goal_input',
    'commitment_input',
    'external_event'
);

CREATE TYPE source_record_status AS ENUM (
    'active',
    'revised',
    'deleted'
);

CREATE TYPE fact_status AS ENUM (
    'candidate',
    'active',
    'rejected',
    'outdated',
    'invalidated'
);

CREATE TYPE fact_origin AS ENUM (
    'explicit_user',
    'inferred',
    'imported'
);

CREATE TYPE evidence_role AS ENUM (
    'supporting',
    'contradicting',
    'contextual'
);

CREATE TYPE evidence_type AS ENUM (
    'direct',
    'repeated',
    'temporal',
    'behavioral',
    'user_confirmed',
    'user_corrected'
);

CREATE TYPE pattern_status AS ENUM (
    'candidate',
    'observed',
    'supported',
    'confirmed',
    'active',
    'weakened',
    'expired',
    'rejected'
);

CREATE TYPE insight_status AS ENUM (
    'draft',
    'validated',
    'published',
    'acknowledged',
    'confirmed',
    'corrected',
    'dismissed',
    'retracted',
    'expired'
);

CREATE TYPE feedback_type AS ENUM (
    'confirm',
    'reject',
    'partially_confirm',
    'correct',
    'outdated',
    'forget_from_model'
);

CREATE TYPE proactive_status AS ENUM (
    'candidate',
    'eligible',
    'blocked',
    'scheduled',
    'sent',
    'responded',
    'expired',
    'cancelled'
);

CREATE TYPE outcome_status AS ENUM (
    'recorded',
    'corrected',
    'invalidated'
);

CREATE TYPE outcome_result AS ENUM (
    'better',
    'same',
    'worse',
    'mixed',
    'not_relevant',
    'unknown'
);
```

---

# 4. users

Assume the existing ClosenAI `users` table already exists.

The following tables reference:

```sql
users(id)
```

Do not duplicate user identity data here.

---

# 5. personal_models

One logical Personal Model per user.

```sql
CREATE TABLE personal_models (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL UNIQUE
        REFERENCES users(id) ON DELETE CASCADE,

    current_version BIGINT NOT NULL DEFAULT 0,

    status VARCHAR(32) NOT NULL DEFAULT 'active',

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Important:

`personal_models` is the aggregate root.

It is not a giant JSON document.

---

# 6. personal_model_versions

Tracks logical model versions.

```sql
CREATE TABLE personal_model_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    personal_model_id UUID NOT NULL
        REFERENCES personal_models(id) ON DELETE CASCADE,

    version BIGINT NOT NULL,

    reason VARCHAR(64) NOT NULL,

    source_event_id UUID NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (personal_model_id, version)
);
```

Example reasons:

```text
fact_added
fact_confirmed
fact_corrected
pattern_confirmed
pattern_rejected
outcome_recorded
rebuild
```

---

# 7. source_records

Source records are canonical user-originated information.

```sql
CREATE TABLE source_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,

    type source_record_type NOT NULL,

    content TEXT NOT NULL,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    occurred_at TIMESTAMPTZ NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    status source_record_status NOT NULL DEFAULT 'active',

    revision_of UUID NULL
        REFERENCES source_records(id)
);
```

Indexes:

```sql
CREATE INDEX idx_source_records_user_created
ON source_records(user_id, created_at DESC);

CREATE INDEX idx_source_records_user_occurred
ON source_records(user_id, occurred_at DESC);

CREATE INDEX idx_source_records_user_type
ON source_records(user_id, type);
```

---

# 8. personal_facts

Normalized facts usable for personalization.

```sql
CREATE TABLE personal_facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    personal_model_id UUID NOT NULL
        REFERENCES personal_models(id) ON DELETE CASCADE,

    user_id UUID NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,

    subject_key VARCHAR(128) NOT NULL,

    predicate VARCHAR(128) NOT NULL,

    object_value JSONB NOT NULL,

    origin fact_origin NOT NULL,

    status fact_status NOT NULL DEFAULT 'candidate',

    confidence NUMERIC(5,4) NULL,

    first_observed_at TIMESTAMPTZ NULL,
    last_observed_at TIMESTAMPTZ NULL,

    confirmed_at TIMESTAMPTZ NULL,

    invalidated_at TIMESTAMPTZ NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Example:

```text
subject_key = "user"
predicate = "prefers_work_environment"
object_value = {"value":"quiet"}
```

Do not create dozens of PostgreSQL columns for every possible fact.

The fact ontology can evolve.

---

# 9. personal_goals

Goals are canonical user-facing intentions that can participate in Patterns, Insights, Proactives and Outcomes.

If ClosenAI already has a canonical `goals` table, **reuse that table instead of creating a duplicate**. The domain contract required here is:

```text
goal_id
user_id
title
status
created_at
updated_at
```

If no existing goal domain exists, the V1 implementation is:

```sql
CREATE TABLE personal_goals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    personal_model_id UUID NOT NULL
        REFERENCES personal_models(id) ON DELETE CASCADE,

    user_id UUID NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,

    title TEXT NOT NULL,

    description TEXT NULL,

    status VARCHAR(32) NOT NULL DEFAULT 'active',

    started_at TIMESTAMPTZ NULL,
    target_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_personal_goals_user_status
ON personal_goals(user_id, status);
```

---

# 10. personal_commitments

Commitments represent explicit intentions or promises that can later produce measurable Outcomes.

As with goals, reuse an existing canonical commitments table if the current ClosenAI system already has one.

Required domain contract:

```text
commitment_id
user_id
title
status
created_at
updated_at
```

If not already present:

```sql
CREATE TABLE personal_commitments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    personal_model_id UUID NOT NULL
        REFERENCES personal_models(id) ON DELETE CASCADE,

    user_id UUID NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,

    title TEXT NOT NULL,

    description TEXT NULL,

    status VARCHAR(32) NOT NULL DEFAULT 'active',

    due_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_personal_commitments_user_status
ON personal_commitments(user_id, status);
```

---

# 11. evidence

Evidence is the provenance layer.

```sql
CREATE TABLE evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,

    source_record_id UUID NOT NULL
        REFERENCES source_records(id) ON DELETE RESTRICT,

    evidence_type evidence_type NOT NULL,

    role evidence_role NOT NULL DEFAULT 'supporting',

    strength NUMERIC(5,4) NULL,

    excerpt TEXT NULL,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Indexes:

```sql
CREATE INDEX idx_evidence_user_source
ON evidence(user_id, source_record_id);

CREATE INDEX idx_evidence_user_created
ON evidence(user_id, created_at DESC);
```

Important:

`excerpt` is optional and must respect privacy/redaction policy.

---

# 12. fact_evidence

Facts may have multiple evidence records.

```sql
CREATE TABLE fact_evidence (
    fact_id UUID NOT NULL
        REFERENCES personal_facts(id) ON DELETE CASCADE,

    evidence_id UUID NOT NULL
        REFERENCES evidence(id) ON DELETE RESTRICT,

    PRIMARY KEY (fact_id, evidence_id)
);
```

---

# 13. patterns

Patterns are longitudinal derived states.

```sql
CREATE TABLE patterns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    personal_model_id UUID NOT NULL
        REFERENCES personal_models(id) ON DELETE CASCADE,

    user_id UUID NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,

    pattern_type VARCHAR(128) NOT NULL,

    subject_key VARCHAR(128) NULL,

    description TEXT NOT NULL,

    status pattern_status NOT NULL DEFAULT 'candidate',

    confidence NUMERIC(5,4) NULL,

    evidence_count INTEGER NOT NULL DEFAULT 0,

    observed_from TIMESTAMPTZ NULL,
    observed_to TIMESTAMPTZ NULL,

    last_supported_at TIMESTAMPTZ NULL,
    confirmed_at TIMESTAMPTZ NULL,
    rejected_at TIMESTAMPTZ NULL,
    expired_at TIMESTAMPTZ NULL,

    detector_version VARCHAR(64) NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Indexes:

```sql
CREATE INDEX idx_patterns_user_status
ON patterns(user_id, status);

CREATE INDEX idx_patterns_user_updated
ON patterns(user_id, updated_at DESC);

CREATE INDEX idx_patterns_user_type
ON patterns(user_id, pattern_type);
```

---

# 14. pattern_evidence

```sql
CREATE TABLE pattern_evidence (
    pattern_id UUID NOT NULL
        REFERENCES patterns(id) ON DELETE CASCADE,

    evidence_id UUID NOT NULL
        REFERENCES evidence(id) ON DELETE RESTRICT,

    PRIMARY KEY (pattern_id, evidence_id)
);
```

---

# 15. insights

User-facing derived observations.

```sql
CREATE TABLE insights (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    personal_model_id UUID NOT NULL
        REFERENCES personal_models(id) ON DELETE CASCADE,

    user_id UUID NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,

    statement TEXT NOT NULL,

    status insight_status NOT NULL DEFAULT 'draft',

    confidence NUMERIC(5,4) NULL,

    time_from TIMESTAMPTZ NULL,
    time_to TIMESTAMPTZ NULL,

    generation_model VARCHAR(128) NULL,
    generation_version VARCHAR(64) NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

# 16. insight_evidence

```sql
CREATE TABLE insight_evidence (
    insight_id UUID NOT NULL
        REFERENCES insights(id) ON DELETE CASCADE,

    evidence_id UUID NOT NULL
        REFERENCES evidence(id) ON DELETE RESTRICT,

    role evidence_role NOT NULL DEFAULT 'supporting',

    PRIMARY KEY (insight_id, evidence_id)
);
```

---

# 17. insight_patterns

```sql
CREATE TABLE insight_patterns (
    insight_id UUID NOT NULL
        REFERENCES insights(id) ON DELETE CASCADE,

    pattern_id UUID NOT NULL
        REFERENCES patterns(id) ON DELETE RESTRICT,

    PRIMARY KEY (insight_id, pattern_id)
);
```

---

# 18. user_feedback

All important user corrections/confirmations are durable domain records.

```sql
CREATE TABLE user_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,

    feedback_type feedback_type NOT NULL,

    target_type VARCHAR(32) NOT NULL,

    target_id UUID NOT NULL,

    correction_text TEXT NULL,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Allowed target types:

```text
fact
pattern
insight
```

Do not use an unrestricted polymorphic foreign key without application/domain validation.

---

# 19. proactives

Proactive contact candidates and delivery lifecycle.

```sql
CREATE TABLE proactives (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,

    personal_model_id UUID NOT NULL
        REFERENCES personal_models(id) ON DELETE CASCADE,

    status proactive_status NOT NULL DEFAULT 'candidate',

    trigger_type VARCHAR(128) NOT NULL,

    message TEXT NULL,

    scheduled_at TIMESTAMPTZ NULL,
    sent_at TIMESTAMPTZ NULL,
    responded_at TIMESTAMPTZ NULL,
    expires_at TIMESTAMPTZ NULL,

    eligibility_score NUMERIC(5,4) NULL,

    generation_model VARCHAR(128) NULL,
    generation_version VARCHAR(64) NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

# 20. proactive_insights

```sql
CREATE TABLE proactive_insights (
    proactive_id UUID NOT NULL
        REFERENCES proactives(id) ON DELETE CASCADE,

    insight_id UUID NOT NULL
        REFERENCES insights(id) ON DELETE RESTRICT,

    PRIMARY KEY (proactive_id, insight_id)
);
```

---

# 21. proactive_patterns

```sql
CREATE TABLE proactive_patterns (
    proactive_id UUID NOT NULL
        REFERENCES proactives(id) ON DELETE CASCADE,

    pattern_id UUID NOT NULL
        REFERENCES patterns(id) ON DELETE RESTRICT,

    PRIMARY KEY (proactive_id, pattern_id)
);
```

---

# 22. outcomes

Outcomes must be explicitly recorded.

```sql
CREATE TABLE outcomes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,

    status outcome_status NOT NULL DEFAULT 'recorded',

    result outcome_result NOT NULL DEFAULT 'unknown',

    description TEXT NULL,

    occurred_at TIMESTAMPTZ NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

# 23. outcome_links

Outcomes may be related to multiple domain objects.

```sql
CREATE TABLE outcome_links (
    outcome_id UUID NOT NULL
        REFERENCES outcomes(id) ON DELETE CASCADE,

    target_type VARCHAR(32) NOT NULL,

    target_id UUID NOT NULL,

    PRIMARY KEY (outcome_id, target_type, target_id)
);
```

Recommended target types:

```text
goal
commitment
pattern
insight
proactive
```

`goal` and `commitment` must resolve to the canonical domain tables actually used by the application; do not maintain duplicate goal/commitment state.

Application/domain validation is required.

---

# 24. Model Events

A durable domain event table is recommended.

```sql
CREATE TABLE personal_model_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,

    personal_model_id UUID NOT NULL
        REFERENCES personal_models(id) ON DELETE CASCADE,

    event_type VARCHAR(128) NOT NULL,

    aggregate_type VARCHAR(64) NOT NULL,

    aggregate_id UUID NULL,

    model_version BIGINT NOT NULL,

    payload JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Indexes:

```sql
CREATE INDEX idx_model_events_user_created
ON personal_model_events(user_id, created_at DESC);

CREATE INDEX idx_model_events_aggregate
ON personal_model_events(aggregate_type, aggregate_id);

CREATE INDEX idx_model_events_type_created
ON personal_model_events(event_type, created_at DESC);
```

---

# 25. AI Generation Runs

All important AI-derived domain operations should be traceable.

```sql
CREATE TABLE ai_generation_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,

    purpose VARCHAR(128) NOT NULL,

    model VARCHAR(128) NOT NULL,

    model_version VARCHAR(64) NULL,

    input_hash VARCHAR(128) NULL,

    output_hash VARCHAR(128) NULL,

    status VARCHAR(32) NOT NULL,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    completed_at TIMESTAMPTZ NULL
);
```

Use this for:

- extraction
- pattern detection
- insight generation
- proactive generation
- model snapshot generation

Do not store raw prompts by default if they contain sensitive personal data. Use references/hashes and controlled observability.

---

# 26. Personal Model Snapshot

For fast AI context.

```sql
CREATE TABLE personal_model_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    personal_model_id UUID NOT NULL
        REFERENCES personal_models(id) ON DELETE CASCADE,

    version BIGINT NOT NULL,

    snapshot JSONB NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (personal_model_id, version)
);
```

This is a cache.

Canonical state remains in relational tables.

---

# 27. Critical Foreign-Key Policy

Do NOT cascade-delete source evidence blindly.

For example:

```text
source_record
    ↓
evidence
    ↓
pattern
    ↓
insight
```

If source data is deleted:

1. mark source unavailable/deleted
2. invalidate dependent Evidence
3. re-evaluate Pattern
4. retract/expire unsupported Insight
5. rebuild Personal Model
6. create audit event

This should be a domain workflow.

---

# 28. Recommended Constraints

Confidence:

```sql
CHECK (
    confidence IS NULL
    OR (confidence >= 0 AND confidence <= 1)
)
```

Evidence strength:

```sql
CHECK (
    strength IS NULL
    OR (strength >= 0 AND strength <= 1)
)
```

Eligibility:

```sql
CHECK (
    eligibility_score IS NULL
    OR (eligibility_score >= 0 AND eligibility_score <= 1)
)
```

Evidence count:

```sql
CHECK (evidence_count >= 0)
```

Add these to the final migration.

---

# 29. Recommended Composite Indexes

```sql
CREATE INDEX idx_facts_model_status
ON personal_facts(personal_model_id, status);

CREATE INDEX idx_facts_user_predicate
ON personal_facts(user_id, predicate);

CREATE INDEX idx_patterns_model_active
ON patterns(personal_model_id, status, updated_at DESC);

CREATE INDEX idx_insights_user_published
ON insights(user_id, published_at DESC)
WHERE status IN ('published', 'acknowledged', 'confirmed');

CREATE INDEX idx_proactives_user_pending
ON proactives(user_id, status, scheduled_at)
WHERE status IN ('candidate', 'eligible', 'scheduled');

CREATE INDEX idx_outcomes_user_occurred
ON outcomes(user_id, occurred_at DESC);
```

---

# 30. Transaction Boundaries

## Fact Confirmation

One transaction:

```text
create feedback
→ update fact
→ increment model version
→ append model event
```

## Pattern Confirmation

One transaction:

```text
create feedback
→ update pattern
→ increment model version
→ append model event
```

## Outcome

One transaction:

```text
create outcome
→ link outcome
→ increment model version if model state changes
→ append model event
```

AI generation itself is NOT part of a long database transaction.

---

# 31. Idempotency

AI pipelines must be idempotent.

Recommended keys:

```text
source_record_id
+
pipeline_type
+
pipeline_version
```

For example:

```text
(source_id, "pattern_detection", "v3")
```

must not generate duplicate candidates when the same job is retried.

---

# 32. Concurrency

Personal Model mutations should use optimistic concurrency.

Example:

```sql
UPDATE personal_models
SET current_version = current_version + 1
WHERE id = $1
  AND current_version = $2;
```

If zero rows are updated:

```text
version conflict
→ reload
→ reconcile
→ retry
```

Do not use a global user lock for every AI operation.

---

# 33. Data Classification

Recommended conceptual classification:

```text
Tier 1 — Source
source_records

Tier 2 — Provenance
evidence
user_feedback

Tier 3 — Canonical Derived Model
personal_facts
patterns

Tier 4 — Presentation
insights
proactives

Tier 5 — Outcomes
outcomes

Tier 6 — Infrastructure
events
ai_generation_runs
snapshots
```

This hierarchy should be reflected in service boundaries.

---

# 34. What Should NOT Be in Personal Model

Do not store:

```text
raw chat transcript
LLM prompt
LLM chain-of-thought
temporary completion
UI copy
marketing content
generic psychological labels
unverified predictions
diagnoses
medical conclusions
relationship judgments
```

These belong elsewhere or should not be generated.

---

# 35. Migration Strategy

Do not attempt a big-bang migration.

Recommended:

```text
Migration 1
personal_models
personal_model_versions

Migration 2
source_records
evidence

Migration 3
personal_facts
fact_evidence

Migration 5
personal_goals / existing goals integration
personal_commitments / existing commitments integration

Migration 6
patterns
pattern_evidence

Migration 6
insights
insight_evidence
insight_patterns

Migration 7
user_feedback

Migration 8
proactives

Migration 9
outcomes
outcome_links

Migration 10
events
ai_generation_runs
snapshots
```

Each migration should be independently deployable.

---

# 36. V1 API Aggregate Boundaries

Recommended service boundaries:

```text
PersonalModelService
EvidenceService
PatternService
InsightService
ProactiveService
OutcomeService
```

API handlers must not directly manipulate multiple domain tables.

Bad:

```text
HTTP Handler
 → UPDATE patterns
 → INSERT insights
 → UPDATE users
```

Better:

```text
HTTP Handler
 → PatternService.confirm(...)
 → transaction
```

---

# 37. Minimum V1 Queries

The system must efficiently answer:

### What do we know?

```text
active PersonalFacts
```

### Why do we know?

```text
Fact → Evidence → SourceRecord
```

### What patterns exist?

```text
active/supported/confirmed Patterns
```

### Why does a pattern exist?

```text
Pattern → Evidence
```

### What did the AI tell the user?

```text
Insights
```

### Why?

```text
Insight → Pattern → Evidence
```

### What did the user correct?

```text
UserFeedback
```

### What changed?

```text
PersonalModelEvents
```

### What happened after intervention?

```text
Outcomes
```

---

# 38. Final Relational Graph

```text
users
  │
  └── personal_models
        │
        ├── personal_model_versions
        │
        ├── personal_facts
        │      └── fact_evidence
        │              └── evidence
        │                    └── source_records
        │
        ├── patterns
        │      └── pattern_evidence
        │              └── evidence
        │
        ├── insights
        │      ├── insight_evidence
        │      └── insight_patterns
        │
        ├── proactives
        │      ├── proactive_insights
        │      └── proactive_patterns
        │
        └── snapshots

users
  ├── user_feedback
  ├── outcomes
  │     └── outcome_links
  │
  ├── personal_model_events
  └── ai_generation_runs
```

---

# 39. Final Engineering Decision

The most important rule in the schema is:

```text
Source Record
    ↓
Evidence
    ↓
Derived Domain State
    ↓
User-facing Interpretation
```

Never reverse this relationship.

In particular:

```text
LLM output
≠
truth
```

and:

```text
Insight
≠
Personal Model
```

The Personal Model is built by domain-controlled state transitions backed by evidence and user feedback.

This architecture allows ClosenAI to evolve from:

```text
AI Journal
```

to:

```text
Personal Intelligence System
```

without rebuilding the database later.
