# ClosenAI System Components and Boundaries

## 1. Runtime Components

``` text
Client
  │
  ▼
Go API
  │
  ├── Application
  ├── Domain
  ├── Repository
  ├── Policy
  └── Messaging
       ├── Outbox
       ├── RabbitMQ
       ├── Protobuf
       └── Inbox
                    │
                    ▼
              Python AI Runtime
                    │
              ┌─────┼─────┐
              │     │     │
             NLP  Pattern  LLM
              │     │     │
              └─────┼─────┘
                    │
                    ▼
                Candidate
                    │
                    ▼
              RabbitMQ
                    │
                    ▼
                 Go Gate
                    │
                    ▼
              PostgreSQL
```

## 2. Go Core Modules

Recommended package boundaries:

``` text
/internal/
  api/
  application/
  personalmodel/
    domain/
    repository/
    service/
    policy/
  evidence/
  pattern/
  insight/
  proactive/
  outcome/
  ai/
  messaging/
    amqp/
    protobuf/
    envelope/
    outbox/
    inbox/
    retry/
  postgres/
  events/
  safety/
  authorization/
  observability/
```

The exact folder structure can evolve, but ownership must remain
explicit.

## 3. Python AI Modules

``` text
ai_worker/
  worker/
    consumer/
    producer/
    handlers/
  ai/
    extraction/
    pattern/
    insight/
    proactive/
    llm/
    embeddings/
    retrieval/
  contracts/
  messaging/
    rabbitmq/
    protobuf/
  evaluation/
  observability/
  tests/
```

## 4. Boundary Rules

### Go → Python

Go sends:

-   source references
-   bounded evidence context
-   task type
-   model/prompt policy metadata
-   correlation identifiers
-   contract version

Go must not send unnecessary personal data.

### Python → Go

Python sends:

-   candidate type
-   candidate content
-   evidence references
-   confidence/probability as a candidate score
-   detector/model metadata
-   reasoning metadata where permitted

Python must not send authoritative:

-   lifecycle
-   canonical status
-   confirmed state
-   final domain score
-   final authorization decision

## 5. Direct Database Access Rule

Python must not connect to canonical PostgreSQL tables.

If Python needs data, use:

-   an explicit Go API,
-   an AI context task,
-   a bounded event payload,
-   or a dedicated read model explicitly designed for AI.

This prevents accidental bypass of domain policy.

## 6. Sync vs Async

Use **RabbitMQ + Protobuf** for:

-   source processing
-   candidate generation
-   event propagation
-   pattern detection
-   insight drafting
-   proactive drafting
-   asynchronous embeddings
-   reconciliation jobs

Use **gRPC + Protobuf** when a synchronous low-latency RPC is actually
required:

-   embedding service
-   reranking service
-   model capability check
-   low-latency inference
-   health/capability calls

Do not introduce gRPC merely because both runtimes use Protobuf.

## 7. Architecture Test

A proposed feature is correctly placed if the following questions have
deterministic answers:

1.  Who owns the canonical state?
2.  Who validates it?
3.  Which transaction persists it?
4.  Which event announces it?
5.  Which queue carries it?
6.  Which protobuf message represents it?
7.  Which consumer owns idempotency?
8.  What happens if the consumer crashes after commit?
9.  What happens if the message is delivered twice?
10. Can the state be rebuilt from canonical data?
