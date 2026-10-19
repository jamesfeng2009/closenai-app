# ClosenAI Go Personal Model Service

## 1. Application Service Role

The Application/Service layer coordinates:

-   authorization
-   transaction boundaries
-   repository calls
-   domain methods
-   candidate validation
-   outbox writes
-   idempotency
-   event creation

It does not replace domain logic.

## 2. Candidate Acceptance

Canonical sequence:

``` text
Receive Candidate
  ↓
Validate message contract
  ↓
Authorize target user/model
  ↓
Start DB transaction
  ↓
Check Inbox
  ↓
Load aggregate / related evidence
  ↓
Construct domain candidate
  ↓
Domain validation
  ↓
Apply mutation
  ↓
Persist canonical state
  ↓
Write model event / audit
  ↓
Write Outbox
  ↓
Commit
  ↓
ACK
```

## 3. Transaction Boundary

One logical mutation should use one database transaction whenever
canonical state and its event must remain atomic.

Example:

``` text
BEGIN
  inbox(message_id)
  pattern
  pattern_evidence
  model_version
  outbox(event)
COMMIT
```

## 4. Outbox Rule

Never do:

``` text
DB COMMIT
RabbitMQ PUBLISH
```

as the only consistency strategy.

Instead:

``` text
DB transaction
  ├── canonical state
  └── outbox record
COMMIT
```

Then a worker publishes the outbox record.

## 5. Authorization

Every candidate must prove:

``` text
candidate.user_id
candidate.personal_model_id
        ↓
Go authorization
        ↓
owned by authenticated principal
```

Python cannot authorize itself.

## 6. Candidate Freshness

Candidates should carry:

-   source record ID
-   evidence IDs
-   generated_at
-   detector version
-   model version
-   prompt version
-   policy version

Go may reject or downgrade candidates based on stale source state.

## 7. Reconciliation

If the same semantic candidate arrives multiple times:

-   detect via idempotency/fingerprint
-   load existing aggregate
-   merge evidence
-   recalculate score
-   preserve history
-   avoid duplicate canonical records

## 8. Feedback

User feedback must always pass through Go domain services.

Examples:

``` text
Confirm pattern
Reject pattern
Correct fact
Mark outdated
Forget from model
```

A correction must trigger dependent recalculation.

## 9. Deletion / Forgetting

For source invalidation:

``` text
Invalidate source
  ↓
Invalidate dependent evidence
  ↓
Recalculate facts/patterns
  ↓
Revalidate insights
  ↓
Cancel affected proactive items
  ↓
Rebuild snapshot
  ↓
Emit audit/event
```

## 10. Final Rule

> Service orchestration decides how a use case is executed; the domain
> decides what state transition is valid.
