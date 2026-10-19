# ClosenAI Python AI Runtime

## 1. Purpose

Python is the AI intelligence runtime.

It owns experimentation and AI computation while remaining outside
canonical state ownership.

## 2. Responsibilities

Python may own:

-   NLP
-   observation extraction
-   semantic similarity
-   embeddings
-   retrieval
-   pattern detection
-   LLM orchestration
-   prompt execution
-   AI evaluation
-   model experimentation

## 3. Responsibilities It Must Not Own

Python must not own:

-   canonical fact lifecycle
-   canonical pattern lifecycle
-   final insight publication
-   user authorization
-   canonical PostgreSQL writes
-   final safety policy
-   final score calculation
-   user-facing truth claims

## 4. Worker Lifecycle

``` text
RabbitMQ message
  ↓
AMQP properties
  ↓
Protobuf decode
  ↓
Contract validation
  ↓
Handler dispatch
  ↓
AI pipeline
  ↓
Candidate construction
  ↓
Protobuf encode
  ↓
RabbitMQ publish
  ↓
ACK input
```

## 5. AI Pipeline

``` text
Context
  ↓
Observation Extraction
  ↓
Normalization
  ↓
Candidate Generation
  ↓
Evidence Linking
  ↓
Confidence Estimation
  ↓
Candidate Validation
  ↓
Output
```

## 6. Context Budget

Go should construct bounded context.

Python must not assume it can retrieve the user's entire life history.

Context should include only:

-   task-relevant source records
-   relevant evidence
-   bounded history
-   policy constraints
-   required metadata

## 7. LLM Output

LLM output must be parsed into a typed candidate schema.

Prefer:

``` text
LLM
  ↓
structured output
  ↓
Pydantic/internal typed model
  ↓
protobuf candidate
```

Never send raw natural-language model output directly to Go as if it
were a domain object.

## 8. Deterministic Detector

A deterministic detector should exist before complex LLM logic.

This provides:

-   reproducible contract tests
-   baseline evaluation
-   failure isolation
-   pipeline verification

## 9. Model Metadata

Every AI result should be traceable to:

``` text
provider
model
model_version
prompt_version
detector_version
policy_version
generated_at
```

## 10. Retry Safety

Python handlers must be idempotent at the application level where
practical.

However, final canonical idempotency remains a Go responsibility.

## 11. Privacy

Only send the minimum context required for the task.

Do not log:

-   raw personal content
-   full prompts
-   secrets
-   access tokens

unless explicitly protected by an approved privacy/debugging policy.
