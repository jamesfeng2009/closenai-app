# ClosenAI Go ↔ Python Messaging Runtime

## 1. Full Communication Logic

### Go → Python

``` text
Go Domain Event
  ↓
Outbox
  ↓
Outbox Worker
  ↓
Build protobuf message
  ↓
Serialize
  ↓
RabbitMQ publish
  ↓
Python queue
```

### Python → Go

``` text
Python consumer
  ↓
Deserialize protobuf
  ↓
AI pipeline
  ↓
Build candidate protobuf
  ↓
Serialize
  ↓
RabbitMQ publish
  ↓
Go candidate queue
  ↓
Deserialize
  ↓
Inbox
  ↓
Domain Gate
  ↓
PostgreSQL
```

## 2. Go Outbox Worker

Responsibilities:

1.  read pending outbox records
2.  lock/batch records safely
3.  map event to protobuf
4.  serialize
5.  publish with confirm
6.  mark published
7.  record failure/attempt

The worker must tolerate restarts.

## 3. Python Consumer

Responsibilities:

1.  consume message
2.  validate AMQP metadata
3.  decode protobuf
4.  validate task contract
5.  execute AI handler
6.  produce candidate
7.  publish candidate
8.  ACK original task

## 4. Go Candidate Consumer

Responsibilities:

1.  consume candidate
2.  decode protobuf
3.  validate candidate
4.  start DB transaction
5.  insert Inbox record
6.  call domain service
7.  write canonical state
8.  write outbox
9.  commit
10. ACK

## 5. ACK Ordering

For candidate acceptance:

``` text
DB COMMIT
   ↓
ACK
```

Never ACK before the canonical transaction commits.

For Python task execution:

``` text
candidate publish confirmed
   ↓
ACK input task
```

If candidate publication fails, do not ACK the task unless the system
has safely persisted an equivalent retry state.

## 6. Correlation

All messages generated from one user action should share a correlation
ID.

Example:

``` text
user_action = C

source.created
  correlation_id=C

ai.task
  correlation_id=C

ai.candidate
  correlation_id=C

pattern.created
  correlation_id=C
```

## 7. Causation

`causation_id` points to the immediate triggering message.

This is different from correlation:

``` text
correlation = whole workflow
causation = immediate parent
```

## 8. Retry Classification

### Transient

-   RabbitMQ temporary failure
-   AI provider timeout
-   database deadlock
-   temporary dependency unavailable

### Permanent

-   malformed protobuf
-   unsupported schema
-   invalid ownership
-   invalid candidate
-   rejected claim type

## 9. Poison Messages

A message repeatedly failing due to the same permanent issue must not
block the queue.

Use:

``` text
retry limit
→ DLQ
→ failure record
→ operational alert
```

## 10. Backpressure

Control:

-   consumer prefetch
-   worker concurrency
-   per-user concurrency
-   AI provider concurrency
-   queue priority

Do not scale consumers without considering provider rate limits.

## 11. Graceful Shutdown

Workers should:

1.  stop accepting new work
2.  finish in-flight work
3.  ACK only successfully completed messages
4.  close channels
5.  close connections

## 12. Health

Expose separate health states:

``` text
process healthy
database healthy
RabbitMQ connected
AI provider available
```

A worker may be alive while RabbitMQ is unhealthy.
