# ClosenAI Database Migration and Deployment Architecture

## 1. Persistence Stack

Official baseline:

``` text
PostgreSQL
  +
pgx v5
  +
sqlc
```

## 2. Database Responsibilities

PostgreSQL stores:

-   users
-   personal models
-   model versions
-   source records
-   facts
-   goals
-   commitments
-   evidence
-   patterns
-   insights
-   feedback
-   proactive records
-   outcomes
-   AI generation metadata
-   outbox
-   inbox
-   audit/model events

## 3. Migration Order

A recommended migration sequence:

``` text
001 extensions / base enums
002 users
003 personal_models
004 model_versions
005 source_records
006 facts
007 goals
008 commitments
009 evidence
010 fact_evidence
011 patterns
012 pattern_evidence
013 insights
014 insight_evidence
015 insight_patterns
016 feedback
017 proactive
018 outcomes
019 model_events
020 ai_generation_runs
021 snapshots
022 indexes / constraints
023 inbox_messages
024 outbox indexes
025 pattern_candidate_runs
026 additional messaging metadata
```

Existing migration numbers can be reconciled during implementation; the
important requirement is monotonic migration history.

## 4. Deployment Components

``` text
                    ┌───────────────┐
                    │   PostgreSQL  │
                    └───────┬───────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
     Go API             Go Workers         Go AI Consumer
        │                   │                   │
        └───────────────────┼───────────────────┘
                            │
                       RabbitMQ
                            │
                       Python AI
```

## 5. Separate Scaling

Go API and Python AI should scale independently.

Examples:

``` text
Go API replicas = N
Go outbox workers = M
Go candidate consumers = K
Python AI workers = P
```

Scaling Python must not alter canonical database semantics.

## 6. Configuration

Configuration should include:

``` text
DATABASE_URL
RABBITMQ_URL
RABBITMQ_VHOST
RABBITMQ_EXCHANGE
RABBITMQ_PREFETCH
AI_PROVIDER
AI_MODEL
AI_TIMEOUT
OUTBOX_BATCH_SIZE
RETRY_POLICY
```

Secrets must come from secret management, not source control.

## 7. Database Pooling

Use pgx pool with explicit:

-   max connections
-   min connections
-   idle lifetime
-   health checks

Do not allow worker concurrency to exhaust database connections.

## 8. RabbitMQ Durability

Use durable:

-   exchanges
-   queues
-   messages

where business delivery requires durability.

## 9. Deployment Order

For a compatible schema change:

``` text
1. Deploy additive DB migration
2. Deploy consumers that tolerate old/new schema
3. Deploy producers using new additive fields
4. Enable feature flag
5. Monitor
```

## 10. Contract Deployment

When adding a protobuf field:

``` text
deploy consumers first
→ then producers
```

Consumers should tolerate the missing field before producers start
sending it.

## 11. Rollback

Application rollback must be compatible with the current database
schema.

Avoid destructive migrations in the same release as application code
unless rollback has been explicitly designed.

## 12. Observability

Monitor:

### PostgreSQL

-   connection pool usage
-   transaction latency
-   deadlocks
-   slow queries

### RabbitMQ

-   queue depth
-   publish confirms
-   consumer latency
-   redelivery count
-   DLQ count

### AI

-   task latency
-   token/cost usage
-   provider error rate
-   candidate acceptance rate

### Domain

-   insight trust rate
-   proactive precision
-   pattern confirmation rate
