# ClosenAI Personal Intelligence Runtime --- Architecture Overview

## 1. Purpose

This document is the top-level architecture baseline for the ClosenAI
Personal Intelligence Runtime.

The system is deliberately split into two unequal runtimes:

-   **Go Core Runtime** --- canonical truth, domain policy,
    transactions, authorization, lifecycle, API and durable state.
-   **Python AI Runtime** --- AI intelligence, NLP, semantic retrieval,
    pattern detection, LLM orchestration and candidate generation.

The most important invariant is:

> **Python proposes. Go decides. PostgreSQL records the canonical
> state.**

AI output is never treated as canonical truth merely because an LLM
produced it.

## 2. System Shape

``` text
                         ClosenAI
                            │
             ┌──────────────┴──────────────┐
             │                             │
        GO CORE RUNTIME               PYTHON AI RUNTIME
        Canonical Truth               AI Intelligence
             │                             │
       ┌─────┼─────┐                ┌──────┼──────┐
       │     │     │                │      │      │
     Domain Repo Service           NLP   Pattern   LLM
       │     │     │                │      │      │
       └─────┼─────┘                └──────┼──────┘
             │                             │
        PostgreSQL                         │
             │                             │
          Outbox                           │
             │                             │
             └──────── RabbitMQ ───────────┘
                      Protobuf
                         │
                Candidate / Event Message
                         │
                         ▼
                     GO CORE
                         │
                    Domain Gate
                         │
                  Canonical Database
```

## 3. Responsibility Matrix

  ------------------------------------------------------------------------------
  Concern                        Go Core                 Python AI
  ------------------------------ ----------------------- -----------------------
  HTTP/API                       Owner                   No

  Authentication/authorization   Owner                   No

  Canonical personal facts       Owner                   Candidate only

  Pattern lifecycle              Owner                   Detection candidate

  Insight publication            Owner                   Draft only

  Proactive eligibility          Owner                   Message draft only

  Outcome recording              Owner                   Candidate only

  PostgreSQL canonical tables    Owner                   No direct writes

  SQL transactions               Owner                   No

  Outbox/Inbox                   Owner                   Consumer/producer
                                                         adapter

  RabbitMQ transport             Adapter/worker          Adapter/worker

  Protobuf serialization         Adapter                 Adapter

  NLP/LLM                        Orchestrator            Owner

  Embeddings/vector retrieval    Can consume; policy     Primary implementation
                                 remains Go              

  Model experimentation          No                      Owner

  AI evaluation                  No                      Owner

  Safety/domain policy           Final authority         Candidate-time checks
                                                         only
  ------------------------------------------------------------------------------

## 4. Canonical Flow

### 4.1 User input to AI candidate

``` text
Client
  ↓
Go API
  ↓
Domain Service
  ↓
PostgreSQL transaction
  ├── SourceRecord
  └── Outbox
        ↓
Outbox Worker
        ↓
Protobuf Serialize
        ↓
RabbitMQ
        ↓
Python Consumer
        ↓
Protobuf Deserialize
        ↓
AI Pipeline
        ↓
Candidate
        ↓
Protobuf Serialize
        ↓
RabbitMQ
```

### 4.2 Candidate to canonical state

``` text
RabbitMQ
  ↓
Go Consumer
  ↓
Protobuf Deserialize
  ↓
Contract Validation
  ↓
Inbox Idempotency
  ↓
Application Service
  ↓
Domain Validation / Domain Gate
  ↓
PostgreSQL transaction
  ├── canonical state
  ├── model version / audit
  └── Outbox
        ↓
       ACK
```

## 5. Transport vs Contract vs Business

These layers must not be conflated.

### RabbitMQ

RabbitMQ is the **transport and delivery mechanism**.

It provides:

-   exchange/routing
-   queues
-   acknowledgements
-   retry topology
-   dead-lettering
-   consumer concurrency
-   delivery semantics

RabbitMQ does not define the business meaning of a message.

### Protobuf

Protobuf is the **cross-language wire contract**.

It defines:

-   field types
-   field numbers
-   optionality
-   schema versions
-   message structure

The `.proto` files are the source of truth for Go and Python generated
types.

### Serialization / Deserialization

Serialization is an infrastructure concern:

``` text
Go struct / generated protobuf message
        ↓
protobuf.Marshal
        ↓
bytes
        ↓
RabbitMQ
        ↓
bytes
        ↓
protobuf.ParseFromString / proto.Unmarshal
        ↓
generated message
```

No domain logic should be hidden inside serialization code.

### Domain Gate

The Domain Gate is the business authority:

``` text
candidate
   ↓
schema validation
   ↓
ownership validation
   ↓
evidence policy
   ↓
domain invariants
   ↓
lifecycle / score recalculation
   ↓
canonical persistence
```

## 6. Delivery Semantics

The baseline is:

> **At-least-once delivery + Inbox idempotency.**

Do not claim true exactly-once semantics.

A message may be delivered more than once. The same `message_id` must
therefore be safely processed more than once.

The canonical consumer transaction is:

``` text
BEGIN

INSERT inbox_messages(message_id)
  -- unique constraint

if duplicate:
    COMMIT
    ACK

otherwise:
    Domain Service
    Canonical State
    Outbox

COMMIT

ACK
```

## 7. Failure Model

### Go publishes but RabbitMQ is unavailable

The event remains in PostgreSQL Outbox and is retried.

### RabbitMQ delivers but Python crashes

The message remains unacknowledged and is redelivered according to the
retry topology.

### Python publishes candidate but Go crashes

The candidate remains in RabbitMQ until the Go consumer successfully
processes it.

### Go commits canonical state but crashes before ACK

The message may be redelivered. Inbox idempotency prevents duplicate
business effects.

### Permanent contract failure

Reject the message into a dead-letter queue and record the failure.

## 8. Data Ownership

PostgreSQL canonical state is authoritative for:

-   facts
-   goals
-   commitments
-   evidence
-   patterns
-   insights
-   proactive records
-   outcomes
-   feedback
-   model versions
-   audit/event records

Python may own rebuildable derived artifacts such as:

-   embeddings
-   vector indexes
-   NLP intermediate representations
-   model-specific caches
-   evaluation artifacts

These must not become a second source of truth.

## 9. Architecture Principles

1.  Go owns canonical truth.
2.  Python owns AI intelligence.
3.  PostgreSQL owns durable canonical state.
4.  RabbitMQ owns asynchronous transport.
5.  Protobuf owns cross-language wire schema.
6.  Outbox protects database-to-message publication.
7.  Inbox protects consumer idempotency.
8.  Domain Services are the only canonical business mutation path.
9.  LLM output is always a candidate.
10. At-least-once is explicit; exactly-once is not assumed.
11. Every cross-runtime message is traceable by
    message/correlation/causation IDs.
12. Schema evolution is additive and backward-compatible.
