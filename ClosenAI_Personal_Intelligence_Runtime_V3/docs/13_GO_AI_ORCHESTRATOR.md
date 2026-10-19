# ClosenAI Go AI Orchestrator

## 1. Purpose

The Go AI Orchestrator turns domain events and application requests into
bounded AI tasks.

It is the policy-controlled bridge between canonical state and Python.

## 2. Flow

``` text
Domain Event
  ↓
AI Orchestrator
  ↓
EvidenceContextBuilder
  ↓
AI Task
  ↓
Protobuf
  ↓
RabbitMQ
  ↓
Python
```

## 3. EvidenceContextBuilder

The builder decides:

-   which evidence is relevant
-   time range
-   maximum number of items
-   source diversity
-   privacy filtering
-   policy version

Python receives the resulting bounded context, not unrestricted database
access.

## 4. Trigger Examples

### New source record

May trigger:

``` text
ExtractObservations
```

### New evidence cluster

May trigger:

``` text
GeneratePatternCandidates
```

### Supported pattern

May trigger:

``` text
GenerateInsightDraft
```

### Proactive trigger

May trigger:

``` text
GenerateProactiveDraft
```

## 5. Deduplication

Task messages should carry a deterministic task identity where possible:

``` text
task_type
personal_model_id
source_scope
detector_version
policy_version
```

This reduces duplicate AI work.

## 6. Backpressure

The orchestrator must respect:

-   RabbitMQ queue depth
-   per-user rate limits
-   AI provider limits
-   cost budgets
-   task priority

## 7. Priority

Suggested priority classes:

``` text
P0 — user-requested synchronous assistance
P1 — important follow-up
P2 — normal background analysis
P3 — periodic reconciliation
```

## 8. Failure

If AI is unavailable:

-   canonical user data remains available
-   outbox remains durable
-   AI task can retry
-   user-facing product must degrade gracefully

## 9. Safety

The orchestrator attaches the relevant policy version and task
constraints.

Python may apply local checks, but Go remains the final policy
authority.

## 10. Observability

Every task should be traceable:

``` text
correlation_id
causation_id
message_id
task_id
source_record_ids
candidate_id
```
