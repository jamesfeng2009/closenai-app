# ClosenAI Proactive Outcome Loop

**Version:** V1.0\
**Status:** Engineering Baseline\
**Depends on:** `01`, `02`, `03`

## 1. Core Decision

Proactive AI is not "AI has something to say."

It is:

``` text
Trigger
 ↓
Evidence
 ↓
Relevance
 ↓
Confidence
 ↓
Safety
 ↓
Timing
 ↓
Frequency
 ↓
User Preference
 ↓
Proactive Candidate
 ↓
Delivery
 ↓
Response
 ↓
Outcome
 ↓
Model Update
```

The objective is **precision, relevance and measurable outcomes**, not
message volume.

## 2. V1 Proactive Types

``` text
memory_recall
pattern_followup
goal_followup
commitment_followup
reflection_prompt
outcome_check
milestone
```

Do not implement engagement-bait or relationship-escalation proactives.

## 3. Trigger Sources

Every proactive must have an explicit trigger:

``` text
new Pattern
Pattern recurrence
Goal due
Commitment due
previous Proactive
user-requested follow-up
pending Outcome
```

## 4. Eligibility Score

Recommended deterministic score:

``` text
ProactiveScore =
0.25 Relevance
+ 0.20 Evidence
+ 0.20 Confidence
+ 0.10 Timing
+ 0.10 UserPreference
+ 0.15 Safety
- FatiguePenalty
```

Safety is a hard gate, not merely a score. If Safety fails, block
regardless of total score.

## 5. Hard Gates

Must have:

``` text
trigger
supporting evidence
confidence threshold
safety pass
user preference pass
frequency pass
quiet-hours pass
```

At send time, eligibility is evaluated again.

## 6. Minimum Evidence

``` text
memory_recall:
1 strong source

pattern_followup:
1 active Pattern
+
≥2 independent evidence records

goal_followup:
explicit Goal

commitment_followup:
explicit Commitment

outcome_check:
previous proactive/goal/commitment
+
defined follow-up window
```

Default confidence thresholds:

``` text
memory_recall      ≥0.75
pattern_followup   ≥0.80
goal_followup      ≥0.70
commitment_followup≥0.70
outcome_check      ≥0.70
```

Sensitive topics require stricter policy.

## 7. Frequency

Default:

``` text
max 1 proactive / 24h
max 3 / 7d
max 8 / 30d
```

User-initiated conversations do not count as proactive messages.

Safety workflows are outside normal engagement caps.

## 8. Quiet Hours

Default:

``` text
22:00-08:00
```

based on user local timezone.

## 9. Fatigue

Track:

``` text
recent proactive count
open rate
response rate
dismissals
mute actions
negative feedback
```

High fatigue reduces frequency.

Never increase frequency merely because engagement falls.

## 10. Message Generation

LLM receives only a constrained packet:

``` text
trigger
evidence
pattern/fact/goal
time context
user preference
safety policy
tone policy
```

It returns:

``` text
message
evidence references
action type
```

It must not introduce unsupported facts.

## 11. Proactive Gate

Before delivery:

``` text
Schema
Evidence
Confidence
Safety
User Preference
Frequency
Quiet Hours
Duplicate
Freshness
Message Quality
```

Any hard failure blocks delivery.

## 12. Freshness

Cancel a queued proactive if:

``` text
trigger changed
user corrected relevant fact
goal completed
commitment cancelled
outcome already recorded
pattern rejected/expired
```

Never send stale queued intelligence.

## 13. State Machine

``` text
candidate
 → eligible
 → scheduled
 → sent
 → responded
```

Alternative:

``` text
candidate → blocked
candidate → cancelled
scheduled → expired
```

## 14. Response

Possible response states:

``` text
answered
confirmed
corrected
rejected
ignored
dismissed
```

Important:

``` text
ignored ≠ negative outcome
```

## 15. Outcome

Canonical Outcomes should come from:

1.  explicit user statement
2.  explicit action completion
3.  direct structured product event

LLM inference is a candidate only.

V1 outcome types:

``` text
goal_progress
goal_completed
commitment_completed
behavior_change
user_reported_change
proactive_helpful
proactive_not_helpful
pattern_confirmed
pattern_disconfirmed
```

## 16. Outcome → Pattern

``` text
Outcome
 ↓
find related Patterns
 ↓
recalculate evidence/score
 ↓
update lifecycle
```

One counterexample does not erase historical evidence.

## 17. Outcome → Insight

If a Pattern materially changes:

``` text
revalidate old Insight
+
retract/expire unsupported Insight
+
generate new candidate
```

Example:

``` text
Old:
"You've repeatedly postponed difficult conversations."

New:
"That pattern appears to be changing."
```

## 18. Outcome → Personal Model

Only Domain Services may mutate canonical state.

Example:

``` text
Goal completed
→ Goal status = completed
→ PersonalModel version++
→ dependent proactive cancelled
→ event emitted
```

## 19. No Outcome From Silence

If user does not respond:

``` text
do nothing
```

Later:

``` text
proactive → expired
```

Do not infer success, failure or emotion from silence.

## 20. User Controls

Expose:

``` text
proactive notifications on/off
quiet hours
frequency
category controls
```

Categories:

``` text
memory
patterns
goals
commitments
reflection
outcomes
```

## 21. Safety

Safety policy has absolute priority. If a proactive intersects crisis,
self-harm, violence, abuse, stalking, coercive control or
medical-emergency signals, the normal proactive pipeline stops and the
safety workflow takes control.

## 22. Metrics

``` text
Proactive Trust Rate =
helpful_or_correct_responses /
responded_proactives

Proactive Precision =
correct_proactives /
sent_proactives

Annoyance Rate =
dismissed_or_muted /
sent
```

Open rate is secondary.

## 23. V1 Rollout

First:

``` text
pattern_followup
goal_followup
commitment_followup
outcome_check
```

Then:

``` text
memory_recall
reflection_prompt
```

Only after correctness/trust metrics are healthy.

## 24. Final Loop

``` text
Pattern
 ↓
Proactive
 ↓
User Action
 ↓
Outcome
 ↓
Evidence
 ↓
Pattern Update
 ↓
Insight
 ↓
Next Proactive
```

The product objective is:

**Insight → Action → Outcome → Learning**, not simulated companionship.
