# ClosenAI AI Contract

## 1. Purpose

The AI Contract defines the boundary between Go Core and Python AI.

It describes **candidate requests and candidate results**, not canonical
domain state.

The contract must remain intentionally narrower than the domain model.

## 2. Request Types

V1 request types:

``` text
ExtractObservations
GenerateFactCandidates
GeneratePatternCandidates
GenerateInsightDraft
GenerateProactiveDraft
GenerateOutcomeCandidate
```

## 3. Candidate Principle

Python may return:

-   candidate content
-   evidence references
-   confidence
-   detector metadata
-   semantic fingerprint
-   model/prompt information

Python must not return authoritative:

-   lifecycle
-   canonical status
-   confirmed flag
-   final score
-   authorization result

## 4. Request Envelope

Logical fields:

``` text
message_id
task_type
schema_version
correlation_id
causation_id
user_id
personal_model_id
source_record_ids
evidence_ids
context
policy_version
prompt_version
requested_at
```

## 5. Fact Candidate

Logical fields:

``` text
candidate_id
candidate_type
subject
predicate
object/value
evidence_refs[]
confidence
temporal_scope
semantic_fingerprint
detector_version
model
prompt_version
```

## 6. Pattern Candidate

Logical fields:

``` text
candidate_id
pattern_type
description
evidence_refs[]
observed_time_range
candidate_strength
semantic_fingerprint
detector_version
model
prompt_version
```

## 7. Insight Draft

Logical fields:

``` text
candidate_id
claim_type
title
body
evidence_refs[]
pattern_refs[]
candidate_confidence
time_range
model
prompt_version
policy_version
```

## 8. Proactive Draft

Logical fields:

``` text
candidate_id
trigger_type
reason
message
evidence_refs[]
pattern_refs[]
candidate_relevance
candidate_confidence
recommended_window
```

## 9. Validation

Go validates:

-   required fields
-   IDs
-   ownership
-   source existence
-   evidence existence
-   allowed claim types
-   confidence ranges
-   payload size
-   freshness
-   policy restrictions

## 10. Versioning

Every contract message has:

``` text
schema_version
```

Protobuf field numbers must never be reused.

Removed fields should be marked `reserved`.

Prefer additive evolution.

## 11. Serialization

The canonical implementation is:

``` text
Generated protobuf message
  ↓
Marshal
  ↓
[]byte
```

and:

``` text
[]byte
  ↓
Unmarshal
  ↓
Generated protobuf message
```

Do not serialize business objects as ad-hoc JSON when the cross-language
protobuf contract already exists.

## 12. Contract Testing

Contract tests must verify:

-   Go can decode Python-produced messages
-   Python can decode Go-produced messages
-   unknown additive fields do not break older consumers
-   invalid required semantics are rejected
-   IDs and timestamps preserve precision
-   enums remain compatible
