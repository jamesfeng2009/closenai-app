# ClosenAI Personal Intelligence Runtime Implementation Runbook

## 1. Implementation Order

Do not start by building the entire AI layer.

Build the canonical runtime first.

``` text
01 Domain
  ↓
02 Database Schema
  ↓
03 Evidence / Pattern / Insight Engine
  ↓
04 Proactive / Outcome
  ↓
05 Runtime Architecture
  ↓
06 Components / Boundaries
  ↓
07 Go Domain
  ↓
08 Repository
  ↓
09 Service
  ↓
10 AI Contract
  ↓
11 Event Contract
  ↓
12 Python AI Runtime
  ↓
13 Go AI Orchestrator
  ↓
14 Messaging
  ↓
15 Protobuf
  ↓
16 Serialization
  ↓
17 Go/Python Runtime
  ↓
18 Outbox/Inbox
  ↓
19 End-to-End Flow
  ↓
20 Deployment
  ↓
Integration Tests
```

## 2. Phase A --- Domain

Implement:

-   aggregates
-   value objects
-   domain errors
-   lifecycle transitions
-   scoring
-   feedback
-   ownership rules

Acceptance:

``` text
go test ./...
```

with no database required for pure domain tests.

## 3. Phase B --- PostgreSQL

Implement:

-   migrations
-   constraints
-   indexes
-   sqlc schema
-   query files
-   repository integration

Acceptance:

-   migrations run against PostgreSQL
-   sqlc generation succeeds
-   repository integration tests pass

## 4. Phase C --- Services

Implement:

-   candidate acceptance
-   feedback
-   outcome
-   reconciliation
-   deletion/invalidation

Acceptance:

``` text
candidate → domain → repository → transaction
```

works without RabbitMQ.

## 5. Phase D --- Contracts

Create:

``` text
contracts/proto/
  closenai_common.proto
  closenai_ai.proto
  closenai_events.proto
```

Generate:

``` text
Go pb.go
Python *_pb2.py
```

Add cross-language contract tests.

## 6. Phase E --- RabbitMQ

Implement:

``` text
connection
topology
publisher
consumer
retry
DLQ
publisher confirms
```

Do not connect domain logic directly to AMQP clients.

## 7. Phase F --- Outbox / Inbox

Implement:

``` text
OutboxRepository
OutboxWorker
InboxRepository
IdempotentConsumer
```

Acceptance:

-   crash before commit → retry
-   crash after commit before ACK → duplicate safely ignored
-   publish failure → outbox remains retryable

## 8. Phase G --- Python

Start with a deterministic AI detector.

Do not start with a complex LLM pipeline.

Acceptance:

``` text
Go task
→ RabbitMQ
→ Python
→ candidate
→ RabbitMQ
→ Go
→ canonical state
```

## 9. Phase H --- Real LLM

Add:

-   provider adapter
-   structured output
-   prompt versioning
-   model metadata
-   evaluation dataset
-   cost controls
-   safety policy

LLM output must still enter through the same candidate contract.

## 10. Phase I --- Insight / Proactive

Implement:

``` text
Pattern
 ↓
Insight Candidate
 ↓
Insight Gate
 ↓
Published Insight
 ↓
Proactive Eligibility
 ↓
Proactive Draft
 ↓
Delivery
 ↓
Outcome
```

## 11. Phase J --- Production Hardening

Before production:

-   rate limits
-   privacy controls
-   auditability
-   DLQ operations
-   replay tooling
-   metrics
-   tracing
-   alerting
-   migration rollback strategy
-   contract compatibility tests

## 12. Required Test Matrix

### Domain

-   lifecycle
-   scoring
-   evidence
-   feedback
-   invalid transitions

### Repository

-   CRUD
-   constraints
-   transactions
-   concurrency

### Messaging

-   serialization
-   deserialization
-   ACK/NACK
-   retry
-   DLQ

### Idempotency

-   duplicate message
-   duplicate candidate
-   redelivery after commit

### AI

-   deterministic detector
-   malformed output
-   missing evidence
-   stale candidate

### End-to-End

``` text
source
→ event
→ AI task
→ candidate
→ domain
→ canonical state
→ event
```

## 13. Definition of Done

A feature is not done when the code compiles.

It is done when:

1.  Domain behavior exists.
2.  PostgreSQL persistence is transactional.
3.  Repository is typed through sqlc.
4.  AI boundary has a protobuf contract.
5.  RabbitMQ delivery is durable.
6.  Outbox protects publication.
7.  Inbox protects idempotency.
8.  Domain Gate protects canonical truth.
9.  Observability exists.
10. Failure/retry behavior is tested.

## 14. Final Architecture Principle

> **Go 管什么是真的，Python 管什么值得被考虑。**

And operationally:

> **RabbitMQ 是 Transport，Protobuf 是 Wire Contract，Serialization 是
> Infrastructure Concern，Go/Python Worker 是 Runtime Adapter，Domain
> Service 才是 Canonical State 的唯一业务入口。**
