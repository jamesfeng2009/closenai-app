# ClosenAI Evidence / Pattern / Insight Engine

**Version:** V1.0\
**Status:** Engineering Baseline\
**Depends on:** `01_PERSONAL_MODEL_DOMAIN_ARCHITECTURE.md`,
`02_PERSONAL_INTELLIGENCE_DATABASE_SCHEMA.md`

## 1. Core Pipeline

``` text
SourceRecord
  ↓
Extraction
  ↓
EvidenceCandidate
  ↓
FactCandidate / PatternCandidate
  ↓
Evidence Policy
  ↓
Domain Validation
  ↓
Persist
  ↓
Pattern Scoring
  ↓
Insight Candidate
  ↓
Insight Gate
  ↓
Published Insight
  ↓
User Confirmation / Correction
  ↓
Model Re-evaluation
```

**Hard rule:** LLM output is an inference proposal, never canonical
truth. Only Domain Services may mutate canonical Personal Model state.

## 2. Responsibilities

### LLM may

-   extract observations
-   classify source content
-   propose Facts/Patterns
-   select candidate evidence
-   draft Insight wording

### LLM may not

-   directly mutate canonical state
-   confirm disputed Facts/Patterns
-   override Evidence/Safety policy
-   create diagnoses or unsupported causal claims
-   infer an Outcome from silence

## 3. Extraction

Extraction produces atomic observations:

``` json
{
  "observations": [
    {
      "type": "behavior",
      "key": "delayed_communication",
      "value": true,
      "explicitness": "explicit",
      "source_span": "...",
      "confidence": 0.94
    }
  ]
}
```

Every observation must distinguish `explicit`, `inferred`, and
`unknown`.

## 4. EvidenceCandidate

``` text
source_record_id
observation
evidence_type
role
strength
rationale
```

It becomes canonical Evidence only after deterministic policy
validation.

## 5. Evidence Strength

Final Evidence Strength is calculated by domain code:

``` text
EvidenceStrength =
SourceReliability
× Explicitness
× Specificity
× Repetition
× TemporalRelevance
```

LLM confidence is only one input; it is never the final Evidence score.

### Baseline source reliability

  Source                               Score
  ---------------------------------- -------
  Explicit user statement               1.00
  Explicit user correction              1.00
  Explicit user selection               0.95
  Explicit behavior/action              0.90
  User-reported emotional state         0.90
  AI extraction from explicit text      0.85
  AI semantic inference                 0.55
  Generic framework inference           0.20

Generic framework inference cannot independently promote a Personal Fact
or Pattern.

### Explicitness

``` text
explicit = 1.00
strongly implied = 0.75
weakly implied = 0.45
speculative = 0.10
```

### Temporal relevance

Default configurable decay:

``` text
0-30 days    = 1.00
31-90 days   = 0.85
91-180 days  = 0.65
181-365 days = 0.40
>365 days    = 0.25
```

Stable explicit Facts may disable time decay.

## 6. Evidence Classes

``` text
A: 0.85-1.00
B: 0.65-0.8499
C: 0.45-0.6499
D: <0.45
```

A/B can support normal derived claims. C is exploratory. D cannot
support user-facing claims.

Evidence from the same source/date must not be counted as independent
repetitions. Evidence diversity considers distinct dates, contexts and
source types.

## 7. Fact Promotion

Explicit user statement:

``` text
explicit statement
→ candidate
→ active
```

Inferred Fact:

``` text
≥2 independent observations
+
combined evidence ≥0.75
→ eligible for active state
```

Otherwise it remains a candidate.

Conflicting evidence must be represented as supporting/contradicting
evidence, not averaged away.

## 8. Pattern Definition

A Pattern is a longitudinal, observable relationship---not a personality
label.

Allowed V1 pattern types:

``` text
recurrence
frequency_change
temporal_association
behavioral_sequence
emotional_context
goal_progress
commitment_followthrough
```

Do not use V1 pattern types such as:

``` text
attachment_style
personality_type
diagnosis
trauma_type
```

## 9. Pattern Detection Algorithm

Deterministic score:

``` text
PatternScore =
    0.30 EvidenceStrength
  + 0.20 EvidenceDiversity
  + 0.15 RecurrenceStrength
  + 0.15 TemporalCoherence
  + 0.10 Recency
  + 0.10 UserConfirmation
```

All components are `[0,1]`.

Before confirmation:

``` text
UserConfirmation = 0
```

After explicit confirmation:

``` text
UserConfirmation = 1
```

### Components

**EvidenceStrength:** weighted evidence quality, not record count.

**EvidenceDiversity:**

``` text
0.5 × date_diversity
+ 0.3 × context_diversity
+ 0.2 × source_type_diversity
```

**RecurrenceStrength:** observed occurrences divided by actual known
opportunities. Never invent the denominator.

**TemporalCoherence:** measures whether observations occur in a
consistent time relationship. It does not establish causation.

## 10. Pattern Confidence

``` text
confidence =
PatternScore
× EvidenceValidity
× PolicyMultiplier
```

Default lifecycle thresholds:

``` text
<0.45       candidate
0.45-0.6499 observed
0.65-0.7999 supported
≥0.80       eligible for active consideration
```

`confirmed` requires explicit user confirmation unless a narrow,
approved automatic-confirmation policy exists.

## 11. Pattern Lifecycle

``` text
candidate
 → observed
 → supported
 → active
```

Optional:

``` text
supported → confirmed
```

Degradation:

``` text
active → weakened → expired
```

User rejection:

``` text
any non-terminal state → rejected
```

Expired Patterns are retained. New evidence may reactivate an equivalent
Pattern instead of creating duplicates.

## 12. User Feedback

Supported actions:

``` text
confirm
partially_confirm
reject
outdated
correct
forget_from_model
```

Every correction creates:

``` text
user_feedback
+
domain state transition
+
model version
+
domain event
```

Rejecting a Pattern also creates a suppression signature/cooldown so the
same unsupported Pattern is not regenerated immediately.

## 13. Insight Candidate

An Insight requires:

``` text
statement
supporting evidence
optional supporting patterns
time range
confidence
```

No supporting evidence = reject.

LLM receives a bounded structured evidence packet, not an unrestricted
prompt such as "tell the user what is going on."

## 14. Insight Gate

Every Insight passes:

``` text
1. Schema
2. Evidence
3. Temporal
4. Claim Type
5. Confidence
6. Safety
7. Language
8. Duplicate
9. User History
```

### Claim types allowed in V1

``` text
observation
comparison
trend
recurrence
change
goal_progress
behavioral_sequence
```

Restricted by default:

``` text
cause
diagnosis
personality label
future prediction
relationship judgment
medical conclusion
```

### Confidence

Normal Insight:

``` text
confidence ≥ 0.70
```

Sensitive domain:

``` text
confidence ≥ 0.85
+
strong evidence
+
special policy
```

Below threshold: do not publish.

### Temporal wording

Do not say:

``` text
"You always..."
```

when the evidence only covers a bounded window.

Prefer:

``` text
"Across several entries..."
"Over the last three weeks..."
"I noticed..."
```

### Safety

Safety policy is a hard override. Crisis, self-harm, violence, abuse,
stalking, coercive control and medical-emergency signals exit the normal
Insight pipeline.

## 15. Causality Rule

Observational evidence may say:

``` text
"These events often appeared around the same time."
```

It may not automatically say:

``` text
"X caused Y."
```

Correlation is not causation.

## 16. Evidence Display

Every published Insight should support:

``` text
Why am I seeing this?
```

with:

``` text
time range
evidence count
representative evidence
confidence
```

Sensitive source text is never placed directly into push notifications.

## 17. Incremental + Periodic Detection

New SourceRecord:

``` text
extract
→ find affected Patterns
→ update affected Patterns
→ create candidate Pattern if necessary
```

Do not rescan all history on every record.

Periodic rebuild:

``` text
daily   stale-pattern evaluation
weekly  active-pattern reconciliation
policy/model version change → targeted rebuild
```

## 18. Versioning

Every detector records:

``` text
detector_version
policy_version
```

Every AI generation records:

``` text
model
model_version
prompt_version
```

Algorithm upgrades must be reproducible and must not silently rewrite
historical state.

## 19. Golden Tests

Required cases:

``` text
strong direct fact
weak inference
repeated pattern
contradictory evidence
old evidence
duplicate evidence
user rejection
user correction
pattern expiry
insight without evidence
causal hallucination
prediction hallucination
safety trigger
```

## 20. Metrics

``` text
Evidence Validity Rate
Pattern Confirmation Rate
Pattern Rejection Rate
Insight Evidence Coverage
Insight Correction Rate
Insight Usefulness Rate
False Insight Rate
Duplicate Insight Rate
Pattern Re-emergence Rate
User Correction Recovery Rate
```

Core trust metric:

``` text
Insight Trust Rate =
confirmed_insights /
(insights_with_feedback)
```

The engine is successful when every meaningful claim is evidence-backed,
time-bounded, confidence-scored, policy-checked, and correctable.
