# ClosenAI Go Personal Model Repository

## 1. Stack

The official persistence stack is:

-   PostgreSQL
-   pgx v5
-   sqlc

No ORM is required for the Personal Intelligence Runtime.

## 2. Repository Responsibility

Repositories translate between:

``` text
Domain
  ↕
Repository Interface
  ↕
sqlc Generated Queries
  ↕
pgx
  ↕
PostgreSQL
```

Repositories do not:

-   calculate pattern scores
-   decide lifecycle
-   call LLMs
-   publish RabbitMQ messages
-   bypass authorization

## 3. Repository Interfaces

Representative interfaces:

``` go
type PersonalModelRepository interface {
    Get(ctx context.Context, tx Tx, id ModelID) (PersonalModel, error)
    Save(ctx context.Context, tx Tx, model PersonalModel) error
}

type PatternRepository interface {
    FindByFingerprint(ctx context.Context, tx Tx, fingerprint string) (Pattern, error)
    Create(ctx context.Context, tx Tx, pattern Pattern) error
    Update(ctx context.Context, tx Tx, pattern Pattern) error
}

type EvidenceRepository interface {
    Create(ctx context.Context, tx Tx, evidence Evidence) error
    LinkToPattern(ctx context.Context, tx Tx, patternID, evidenceID ID) error
}
```

Actual interfaces should follow aggregate ownership.

## 4. sqlc Rule

SQL is authored explicitly in:

``` text
db/queries/*.sql
```

sqlc generates typed Go accessors.

Generated code must not be manually edited.

## 5. Transaction Rule

Repositories receive a transaction-scoped query object or equivalent
unit-of-work abstraction.

The same PostgreSQL transaction must contain:

``` text
canonical state mutation
+
outbox insert
```

## 6. Concurrency

Use PostgreSQL constraints and transaction isolation for correctness.

Where required:

-   `SELECT ... FOR UPDATE`
-   unique constraints
-   optimistic version columns
-   idempotency keys

Do not rely on process-local mutexes for distributed correctness.

## 7. Query Design

Prefer purpose-built queries:

``` text
GetPatternForUpdate
FindPatternByFingerprint
ListEvidenceForPattern
InsertInboxMessage
InsertOutboxMessage
MarkProactiveDelivered
RecordOutcome
```

Avoid generic repository methods that encourage unbounded querying.

## 8. Pagination

All user-facing collections should define:

-   deterministic ordering
-   cursor strategy
-   maximum page size
-   stable filtering semantics

## 9. Read Models

Read-heavy projections can exist, but must be rebuildable from canonical
data.

A snapshot is an optimization, not a source of truth.

## 10. Migration Discipline

Migrations must be:

-   forward ordered
-   idempotent where practical
-   reviewed with sqlc impact
-   tested against a real PostgreSQL instance
-   compatible with application rollout strategy
