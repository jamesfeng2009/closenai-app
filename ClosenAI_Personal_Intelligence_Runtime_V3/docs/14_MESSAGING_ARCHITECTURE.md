# ClosenAI Messaging Architecture --- RabbitMQ + Protobuf

## 1. Decision

The asynchronous cross-runtime transport is:

> **RabbitMQ + Protobuf**

RabbitMQ is transport. Protobuf is wire contract.

They solve different problems.

## 2. Logical Layers

``` text
Application
   ↓
Message Object
   ↓
Protobuf Encoder
   ↓
AMQP Publisher
   ↓
RabbitMQ Exchange
   ↓
RabbitMQ Queue
   ↓
AMQP Consumer
   ↓
Protobuf Decoder
   ↓
Handler
   ↓
Application / Domain
```

## 3. RabbitMQ Topology

Recommended logical topology:

``` text
closenai.events
closenai.ai.tasks
closenai.ai.candidates
closenai.dlq
```

Queues should be purpose-oriented, for example:

``` text
ai.observation.extract
ai.pattern.detect
ai.insight.draft
ai.proactive.draft
go.candidate.accept
```

Exact topology can be refined after throughput testing.

## 4. Exchange Strategy

Use topic-style routing keys where event types benefit from routing:

``` text
source.created
evidence.accepted
pattern.created
pattern.updated
candidate.pattern
candidate.insight
```

The protobuf message type remains authoritative for payload decoding.

## 5. AMQP Properties

Recommended properties:

``` text
message_id
type
content_type = application/protobuf
correlation_id
timestamp
delivery_mode = persistent
```

Additional metadata may be carried in headers.

## 6. Body

The AMQP body is the serialized protobuf bytes.

``` text
body = proto.Marshal(message)
```

Do not wrap protobuf bytes inside JSON unless an explicit
interoperability requirement exists.

## 7. ACK Semantics

### ACK

Use when:

-   processing succeeded
-   duplicate was safely identified and committed/no-op
-   permanent rejection was recorded according to policy

### NACK/Requeue

Use only for transient failures.

### Reject/DLQ

Use for:

-   invalid protobuf
-   unsupported schema version
-   permanent contract violation
-   unrecoverable business input

## 8. Retry

Prefer explicit retry queues over unlimited immediate requeue.

Example:

``` text
queue
  ↓
retry.30s
  ↓
retry.2m
  ↓
retry.10m
  ↓
DLQ
```

This prevents hot retry loops.

## 9. Ordering

RabbitMQ ordering is not a substitute for domain concurrency control.

If ordering matters for a user/model:

-   use appropriate routing/consumer partitioning
-   include sequence/version information
-   enforce canonical ordering in PostgreSQL

## 10. Message Size

Keep messages bounded.

Large context should be referenced by IDs or a controlled context
object, not by embedding arbitrary large transcripts.

## 11. Publisher Confirms

The outbox publisher should use RabbitMQ publisher confirms so that a
published message is not marked successfully delivered merely because a
client library returned without error.

## 12. Connection Management

Each runtime should have:

-   connection lifecycle management
-   channel lifecycle management
-   reconnect strategy
-   topology declaration
-   health checks
-   metrics

## 13. RabbitMQ Is Not the Source of Truth

If RabbitMQ loses a transient message before durable publication is
confirmed, the outbox remains authoritative for retry.

RabbitMQ is not the canonical event database.
