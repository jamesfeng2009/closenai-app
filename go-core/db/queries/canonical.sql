-- N2 canonical 读写面（V3 02 §37「Minimum V1 Queries」+ 08 §3/§7 仓库接口）。
-- 生成物：go-core/internal/store/canonicaldb（sqlc generate，禁手改）。
-- 覆盖口径由 backend/tests/test_canonical_schema.py 的 §37 对账表把关：
--   我们知道什么 / 为何相信 / 置信多少 / 上次何时确认 / 改了什么 /
--   当前在用 / 用户纠正了什么 / 干预后发生了什么 / 存在哪些模式。

-- ── §37-1「我们知道什么」：当前生效的事实（02 §37 active PersonalFacts）────────
-- name: ListActiveFacts :many
SELECT id, subject_key, predicate, object_value, origin, confidence,
       first_observed_at, last_observed_at, confirmed_at, model_version
FROM personal_facts
WHERE user_id = $1 AND personal_model_id = $2 AND status = 'active'
ORDER BY last_observed_at DESC NULLS LAST, created_at DESC;

-- ── §37-2「为何相信」：Fact → Evidence → SourceRecord（02 §37 原链路）─────────
-- name: ListEvidenceForFact :many
SELECT e.id, e.evidence_type, e.role, e.strength, e.observation, e.excerpt,
       e.created_at, e.generation_run_id,
       s.id AS source_record_id, s.source_type, s.source_table, s.source_id,
       s.occurred_at, s.status AS source_status
FROM fact_evidence fe
	JOIN evidence e ON e.id = fe.evidence_id
	JOIN source_records s ON s.id = e.source_record_id
WHERE fe.user_id = $1 AND fe.fact_id = $2
ORDER BY e.created_at DESC;

-- ── §37-3「存在哪些模式」：在用模式（02 §37 active/supported/confirmed）───────
-- name: ListLivePatterns :many
SELECT id, pattern_type, subject_key, scope, description, status, confidence,
       evidence_count, observed_from, observed_to, last_supported_at,
       confirmed_at, detector_version, model_version, updated_at
FROM patterns
WHERE user_id = $1 AND personal_model_id = $2
  AND status IN ('active', 'supported', 'confirmed')
ORDER BY confidence DESC NULLS LAST, updated_at DESC;

-- ── §37-4「模式为何成立」：Pattern → Evidence（02 §37）───────────────────────
-- name: ListEvidenceForPattern :many
SELECT e.id, e.evidence_type, e.role, e.strength, e.observation, e.excerpt,
       e.created_at, e.generation_run_id,
       s.id AS source_record_id, s.source_type, s.source_table, s.source_id,
       s.occurred_at, s.status AS source_status
FROM pattern_evidence pe
	JOIN evidence e ON e.id = pe.evidence_id
	JOIN source_records s ON s.id = e.source_record_id
WHERE pe.user_id = $1 AND pe.pattern_id = $2
ORDER BY e.created_at DESC;

-- ── §37-5「AI 告诉过用户什么」：已发布洞察（02 §37 Insights）──────────────────
-- name: ListPublishedInsights :many
SELECT id, statement, claim_type, confidence, time_from, time_to,
       generation_model, generation_version, generation_run_id, model_version,
       created_at, published_at
FROM insights
WHERE user_id = $1 AND personal_model_id = $2
  AND status IN ('published', 'acknowledged', 'confirmed')
ORDER BY published_at DESC;

-- ── §37-6「洞察为何成立」：Insight → Evidence（02 §37「Why?」前半）───────────
-- name: ListEvidenceForInsight :many
SELECT e.id, e.evidence_type, ie.role, e.role AS evidence_role, e.strength,
       e.observation, e.excerpt,
       e.created_at, e.generation_run_id,
       s.id AS source_record_id, s.source_type, s.source_table, s.source_id,
       s.occurred_at,
       COALESCE(s.occurred_at, e.created_at) AS observed_at
FROM insight_evidence ie
	JOIN evidence e ON e.id = ie.evidence_id
	JOIN source_records s ON s.id = e.source_record_id
WHERE ie.user_id = $1 AND ie.insight_id = $2
ORDER BY e.created_at DESC;

-- ── §37-6 后半：Insight → Pattern（回链的第二跳，N9 读路径也走这里）───────────
-- name: ListPatternsForInsight :many
SELECT p.id, p.pattern_type, p.scope, p.description, p.status, p.confidence,
       p.evidence_count, p.observed_from, p.observed_to
FROM insight_patterns ip
	JOIN patterns p ON p.id = ip.pattern_id
WHERE ip.user_id = $1 AND ip.insight_id = $2
ORDER BY p.confidence DESC NULLS LAST;

-- ── §37-7「用户纠正了什么」：UserFeedback（02 §37）───────────────────────────
-- name: ListUserFeedback :many
SELECT id, feedback_type, target_type, target_id, correction_text,
       metadata_json, created_at
FROM user_feedback
WHERE user_id = $1 AND feedback_type = ANY($2::varchar[])
ORDER BY created_at DESC
LIMIT $3;

-- ── §37-8「改了什么」：PersonalModelEvents（02 §37）──────────────────────────
-- name: ListModelEvents :many
SELECT id, event_type, aggregate_type, aggregate_id, model_version, payload,
       generation_run_id, created_at
FROM personal_model_events
WHERE user_id = $1 AND personal_model_id = $2
ORDER BY created_at DESC, id DESC
LIMIT $3;

-- ── §37-9「干预后发生了什么」：Outcomes + 指向（02 §37）──────────────────────
-- outcome_type 传空串即「不按类型过滤」：sqlc 把可空参数推成 string 而非 pgtype.Text，
-- `IS NULL` 写法会让「不传」变成「传了空值」，判定静默失效，所以用显式哨兵。
-- name: ListOutcomesForUser :many
SELECT id, outcome_type, status, result, description, occurred_at, origin,
       model_version, generation_run_id, created_at
FROM outcomes
WHERE user_id = $1
  AND ($2::varchar = '' OR outcome_type = $2)
ORDER BY occurred_at DESC NULLS LAST, created_at DESC
LIMIT $3;

-- name: ListOutcomeLinks :many
SELECT outcome_id, target_type, target_id
FROM outcome_links
WHERE user_id = $1 AND outcome_id = $2
ORDER BY target_type, target_id;

-- ── 聚合根与版本（02 §5、§32 乐观并发；不变量：变更必须 ++）─────────────────
-- name: GetPersonalModel :one
SELECT id, user_id, current_version, status, created_at, updated_at
FROM personal_models
WHERE user_id = $1;

-- name: InsertPersonalModel :exec
INSERT INTO personal_models (id, user_id, current_version, status)
VALUES ($1, $2, 0, 'active');

-- 02 §32：0 行即版本冲突 → reload / reconcile / retry，不用全局用户锁。
-- name: BumpPersonalModelVersion :one
UPDATE personal_models
SET current_version = current_version + 1, updated_at = now()
WHERE id = $1 AND current_version = $2
RETURNING current_version;

-- name: InsertPersonalModelVersion :exec
INSERT INTO personal_model_versions (
	id, user_id, personal_model_id, version, reason, source_event_id
) VALUES ($1, $2, $3, $4, $5, $6);

-- name: InsertModelEvent :exec
INSERT INTO personal_model_events (
	id, user_id, personal_model_id, event_type, aggregate_type, aggregate_id,
	model_version, payload, generation_run_id
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9);

-- ── Source 层薄登记（D-2：指针不复制内容）───────────────────────────────────
-- 同一既有行只登记一次：ON CONFLICT 复用既有 id，重跑管道不产生第二条登记。
-- name: InsertSourceRecord :one
INSERT INTO source_records (
	id, user_id, source_type, source_table, source_id, occurred_at, status, revision_of
) VALUES ($1, $2, $3, $4, $5, $6, 'active', $7)
ON CONFLICT (user_id, source_table, source_id) DO UPDATE SET updated_at = now()
RETURNING id;

-- name: GetSourceRecord :one
SELECT id, user_id, source_type, source_table, source_id, occurred_at, status,
       revision_of, created_at, updated_at
FROM source_records
WHERE id = $1 AND user_id = $2;

-- name: MarkSourceRecordDeleted :exec
UPDATE source_records
SET status = 'deleted', updated_at = now()
WHERE id = $1 AND user_id = $2;

-- ── T2-06：source.deleted 传播（B-08 定案：登记行只翻状态、不删行）────────────
-- 按指针回查登记行：`source.deleted` 事件带的是 (user_id, source_table, source_id)，
-- 不是登记行 id。归属已在解码面判完（体的 user_id 必须等于头），所以这里按
-- (user_id, source_table, source_id) 取——命中 UNIQUE (user_id, source_table, source_id)。
-- name: GetSourceRecordByPointer :one
SELECT id, user_id, source_type, source_table, source_id, occurred_at, status,
       revision_of, created_at, updated_at
FROM source_records
WHERE user_id = $1 AND source_table = $2 AND source_id = $3;

-- 单向翻转：只有当前不是 deleted 才被改写。回 0 行＝这条删除已经翻过一次，
-- 调用方据此**不再传播**（幂等口径由工作单 T2-06 §怎么做.4② 给出，不靠读状态再猜）。
-- name: MarkSourceRecordDeletedOnce :execrows
UPDATE source_records
SET status = 'deleted', updated_at = now()
WHERE id = $1 AND user_id = $2 AND status <> 'deleted';

-- 一条登记行支撑的 evidence id（传播面的第二跳：逐条走 PropagateEvidenceInvalidation）。
-- created_at 排序是为了让同批多条的失效顺序可复现；id 兜底保证全序。
-- name: ListEvidenceForSource :many
SELECT id
FROM evidence
WHERE user_id = $1 AND source_record_id = $2
ORDER BY created_at, id;

-- 只服务「翻转成功但传播中途失败」那一格的恢复：把状态收回，让这条投递回到还没翻过。
-- 谓词带 status='deleted'——不许把一条本来就 deleted 的行恢复成 active（那等于替
-- 另一次删除撤销结论）。
-- name: RestoreSourceRecordStatus :execrows
UPDATE source_records
SET status = $3, updated_at = now()
WHERE id = $1 AND user_id = $2 AND status = 'deleted';

-- ── Provenance 写入（N3 Gate 的唯一入口，本批只提供语句）────────────────────
-- name: InsertEvidence :exec
INSERT INTO evidence (
	id, user_id, source_record_id, evidence_type, role, strength, observation,
	excerpt, metadata_json, generation_run_id
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10);

-- name: GetEvidence :one
SELECT id, user_id, source_record_id, evidence_type, role, strength, observation,
       excerpt, metadata_json, generation_run_id, created_at
FROM evidence
WHERE id = $1 AND user_id = $2;

-- name: CountEvidenceForSource :one
SELECT count(*) AS evidence_count
FROM evidence
WHERE user_id = $1 AND source_record_id = $2;

-- ── Fact 写入与终局态（AI 只能产出 candidate，status 由 Gate 判）─────────────
-- name: InsertFact :exec
INSERT INTO personal_facts (
	id, user_id, personal_model_id, subject_key, predicate, object_value, origin,
	status, confidence, first_observed_at, last_observed_at, model_version,
	generation_run_id, reject_reason
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14);

-- name: UpdateFactStatus :one
UPDATE personal_facts
SET status = $3,
    confirmed_at = COALESCE($4, confirmed_at),
    invalidated_at = COALESCE($5, invalidated_at),
    reject_reason = $6,
    model_version = $7,
    updated_at = now()
WHERE id = $1 AND user_id = $2
RETURNING id;

-- name: LinkFactEvidence :exec
INSERT INTO fact_evidence (fact_id, evidence_id, user_id)
VALUES ($1, $2, $3)
ON CONFLICT DO NOTHING;

-- name: GetFact :one
SELECT id, user_id, personal_model_id, subject_key, predicate, object_value,
       origin, status, confidence, model_version, generation_run_id, reject_reason,
       created_at, updated_at
FROM personal_facts
WHERE id = $1 AND user_id = $2;

-- ── Goal / Commitment（02 §9–§10；裁决 §五-3 采纳建表）───────────────────────
-- name: InsertGoal :exec
INSERT INTO personal_goals (
	id, user_id, personal_model_id, title, description, status, started_at, target_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8);

-- name: ListGoals :many
-- 内部面读面（T4-02）。`$2` 空串＝不按状态过滤（「我的目标」默认要给全部四态，
-- 只显示 active 会让用户找不到自己刚完成的那一颗）。
-- 不再按 personal_model_id 过滤：一人一模型由 ix_personal_models_user_unique 兜住
-- （02 §5），而这一面唯一的调用方只拿到 user_id，加这一格等于把「模型行换了」
-- 变成「目标看不见」那种最难归因的空。
SELECT id, title, description, status, started_at, target_at, completed_at,
       created_at, updated_at
FROM personal_goals
WHERE user_id = $1 AND (sqlc.arg(status)::varchar = '' OR status = sqlc.arg(status))
ORDER BY created_at DESC
LIMIT sqlc.arg(row_limit);

-- name: CompleteGoal :one
UPDATE personal_goals
SET status = 'completed', completed_at = now(), updated_at = now()
WHERE id = $1 AND user_id = $2
RETURNING id;

-- name: CancelGoal :one
-- 取消**不写 completed_at**：那条列的语义是「达成了」，把「不想做了」写成达成
-- 会让 04 §22 的目标完成度量凭空多一个分子。这一张表没有 cancelled_at 列
-- （N2 建表时没有这一格，T4-01 只按 Baseline 的要求加了 skipped_at），
-- 所以取消留下的时间是 updated_at；要精确到「什么时候取消的」需要另开一次迁移，本期不做。
UPDATE personal_goals
SET status = 'cancelled', updated_at = now()
WHERE id = $1 AND user_id = $2
RETURNING id;

-- name: InsertCommitment :exec
-- goal_id 追加在末尾而不是插在 personal_model_id 之后：sqlc 的参数结构体按位置生成，
-- 插中间会让既有调用方的字段顺序整体平移，而这一列对老代码是「没有目标」＝ NULL。
INSERT INTO personal_commitments (
	id, user_id, personal_model_id, title, description, status, due_at, goal_id
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8);

-- name: ListCommitments :many
-- 与 ListGoals 同一格口径（T4-02）：`$2` 空串＝不过滤，按 user 而非 model 取。
SELECT id, title, description, status, due_at, completed_at, skipped_at,
       goal_id, created_at, updated_at
FROM personal_commitments
WHERE user_id = $1 AND (sqlc.arg(status)::varchar = '' OR status = sqlc.arg(status))
ORDER BY created_at DESC
LIMIT sqlc.arg(row_limit);

-- name: CompleteCommitment :one
UPDATE personal_commitments
SET status = 'completed', completed_at = now(), updated_at = now()
WHERE id = $1 AND user_id = $2
RETURNING id;

-- name: SkipCommitment :one
-- T4-01 交付的是这一格的**库面**，状态机不在这里：「active → skipped、终态不许回退」
-- 归 domain（T4-02），SQL 里加 `AND status = 'active'` 会把同一套判据写第二遍。
-- COALESCE 是幂等的那一半：同一句跳过重复执行时，`skipped_at` 保持第一次的时间——
-- 「什么时候跳过的」是审计事实，被第二次点击刷掉就等于改写历史。
UPDATE personal_commitments
SET status = 'skipped', skipped_at = COALESCE(skipped_at, now()), updated_at = now()
WHERE id = $1 AND user_id = $2
RETURNING id;

-- name: CancelCommitment :one
-- 与 CancelGoal 同一格决定：这一张表也没有 cancelled_at，取消只留 status 与 updated_at。
-- 「跳到哪一列、哪个时间要不要留」是列语义，留在 SQL；「active → cancelled 许不许」
-- 是状态机，留在 domain（T4-02 的 TransitionAction），这里同样不加 `AND status=`。
UPDATE personal_commitments
SET status = 'cancelled', updated_at = now()
WHERE id = $1 AND user_id = $2
RETURNING id;

-- ── Pattern 写入（08 §3 指纹幂等 + 08 §7 FOR UPDATE）────────────────────────
-- name: FindPatternByFingerprint :one
SELECT id, user_id, personal_model_id, pattern_type, subject_key, scope,
       description, status, confidence, evidence_count, fingerprint,
       detector_version, model_version, created_at, updated_at
FROM patterns
WHERE user_id = $1 AND fingerprint = $2;

-- name: GetPatternForUpdate :one
SELECT id, user_id, personal_model_id, pattern_type, subject_key, scope,
       description, status, confidence, evidence_count, score_detail,
       detector_version, model_version, generation_run_id, reject_reason,
       created_at, updated_at
FROM patterns
WHERE id = $1 AND user_id = $2
FOR UPDATE;

-- name: InsertPattern :exec
INSERT INTO patterns (
	id, user_id, personal_model_id, pattern_type, subject_key, scope, description,
	status, confidence, evidence_count, observed_from, observed_to, detector_version,
	fingerprint, score_detail, model_version, generation_run_id, reject_reason
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18);

-- name: UpdatePatternLifecycle :one
-- last_supported_at 用 COALESCE：生命周期转移（确认/削弱/过期）不是「新的支撑观测」，
-- 把 NULL 直写进去会抹掉 N3 建立的「最近何时仍被证据支撑」，而 04:231 的
-- recent_correction 与 N5 的 freshness 都读它。
UPDATE patterns
SET status = $3,
    confidence = $4,
    evidence_count = $5,
    score_detail = $6,
    last_supported_at = COALESCE($7, last_supported_at),
    confirmed_at = COALESCE($8, confirmed_at),
    rejected_at = COALESCE($9, rejected_at),
    expired_at = COALESCE($10, expired_at),
    reject_reason = $11,
    model_version = $12,
    updated_at = now()
WHERE id = $1 AND user_id = $2
RETURNING id;

-- name: LinkPatternEvidence :exec
INSERT INTO pattern_evidence (pattern_id, evidence_id, user_id)
VALUES ($1, $2, $3)
ON CONFLICT DO NOTHING;

-- ── Insight 写入（status 默认 draft，九关结论落 gate_detail）────────────────
-- name: InsertInsight :exec
INSERT INTO insights (
	id, user_id, personal_model_id, statement, claim_type, status, confidence,
	time_from, time_to, generation_model, generation_version, generation_run_id,
	reject_reason, gate_detail, model_version
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15);

-- name: GetInsight :one
SELECT id, user_id, personal_model_id, statement, claim_type, status, confidence,
       time_from, time_to, generation_model, generation_version,
       generation_run_id, reject_reason, gate_detail, model_version,
       created_at, published_at, updated_at
FROM insights
WHERE id = $1 AND user_id = $2;

-- name: UpdateInsightStatus :one
UPDATE insights
SET status = $3,
    published_at = COALESCE($4, published_at),
    reject_reason = $5,
    gate_detail = $6,
    model_version = $7,
    updated_at = now()
WHERE id = $1 AND user_id = $2
RETURNING id;

-- name: LinkInsightEvidence :exec
INSERT INTO insight_evidence (insight_id, evidence_id, user_id, role)
VALUES ($1, $2, $3, $4)
ON CONFLICT DO NOTHING;

-- name: LinkInsightPattern :exec
INSERT INTO insight_patterns (insight_id, pattern_id, user_id)
VALUES ($1, $2, $3)
ON CONFLICT DO NOTHING;

-- ── Proactive 写入与投递（04 §4–§11；journey_event_id 是与现行频控链的关联点）─
-- 拒因必须在**插入时**就落 reject_reason/block_reason：生成时刻被挡的候选不会再走
-- SetProactiveVerdict（那是发送时刻的改写），漏了这两列 04 §22 的 Precision 就没有分母。
-- name: InsertProactive :exec
INSERT INTO proactives (
	id, user_id, personal_model_id, status, trigger_type, message, scheduled_at,
	expires_at, eligibility_score, eligibility_detail, block_reason, reject_reason,
	generation_model, generation_version, generation_run_id, model_version,
	journey_event_id
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17);

-- name: GetProactive :one
SELECT id, user_id, personal_model_id, status, trigger_type, message,
       scheduled_at, sent_at, responded_at, expires_at, eligibility_score,
       eligibility_detail, block_reason, reject_reason, journey_event_id,
       model_version, created_at, updated_at
FROM proactives
WHERE id = $1 AND user_id = $2;

-- 08 §7 MarkProactiveDelivered：只有非终态才可置 sent，0 行即已被投递/已过期。
-- name: MarkProactiveDelivered :one
UPDATE proactives
SET status = 'sent', sent_at = now(), updated_at = now()
WHERE id = $1 AND user_id = $2 AND status IN ('candidate', 'eligible', 'scheduled')
RETURNING id;

-- name: BlockProactive :one
UPDATE proactives
SET status = 'blocked', block_reason = $3, updated_at = now()
WHERE id = $1 AND user_id = $2
RETURNING id;

-- name: ExpireProactive :one
UPDATE proactives
SET status = 'expired', updated_at = now()
WHERE id = $1 AND user_id = $2 AND status IN ('candidate', 'eligible', 'scheduled')
RETURNING id;

-- name: LinkProactiveInsight :exec
INSERT INTO proactive_insights (proactive_id, insight_id, user_id)
VALUES ($1, $2, $3)
ON CONFLICT DO NOTHING;

-- name: LinkProactivePattern :exec
INSERT INTO proactive_patterns (proactive_id, pattern_id, user_id)
VALUES ($1, $2, $3)
ON CONFLICT DO NOTHING;

-- ── N5：主动触达的资格评估输入（04 §5–§12 + PRD 触达数字）───────────────────
-- 一条 proactive 的重评要同时看：引用行的当前状态（04 §12 取消条件）、用户偏好列
-- （04 §5 user preference pass）、历史触达（频次/疲劳/查重）、最后一次用户发言
-- （沉默阶梯）、近期纠正（04:231）。这些一律从库里读，不采信提案自报。

-- name: GetProactiveState :one
SELECT id, user_id, personal_model_id, status, trigger_type, message,
       scheduled_at, sent_at, responded_at, expires_at, eligibility_score,
       eligibility_detail, block_reason, reject_reason, journey_event_id,
       model_version, generation_run_id, created_at, updated_at
FROM proactives
WHERE id = $1 AND user_id = $2;

-- 引用行的当前库态：UNION 一次取齐七类，免得一条候选发七条查询。
-- anchor_at 每类取各自最有意义的时间列（goal 看 target_at、承诺看 due_at…），
-- 「关联当下」那一层只认这些库里时刻，不认提案给的时间。
-- name: ListRefStates :many
SELECT 'pattern'::varchar AS ref_type, p.id, p.status,
       COALESCE(p.last_supported_at, p.created_at) AS anchor_at
FROM patterns p
WHERE p.user_id = $1 AND p.id = ANY ($2::varchar[])
UNION ALL
SELECT 'goal', g.id, g.status, COALESCE(g.target_at, g.created_at)
FROM personal_goals g
WHERE g.user_id = $1 AND g.id = ANY ($2::varchar[])
UNION ALL
SELECT 'commitment', c.id, c.status, COALESCE(c.due_at, c.created_at)
FROM personal_commitments c
WHERE c.user_id = $1 AND c.id = ANY ($2::varchar[])
UNION ALL
SELECT 'insight', i.id, i.status, COALESCE(i.time_to, i.created_at)
FROM insights i
WHERE i.user_id = $1 AND i.id = ANY ($2::varchar[])
UNION ALL
SELECT 'fact', f.id, f.status, COALESCE(f.last_observed_at, f.created_at)
FROM personal_facts f
WHERE f.user_id = $1 AND f.id = ANY ($2::varchar[])
UNION ALL
SELECT 'outcome', o.id, o.status, COALESCE(o.occurred_at, o.created_at)
FROM outcomes o
WHERE o.user_id = $1 AND o.id = ANY ($2::varchar[])
UNION ALL
SELECT 'proactive', pr.id, pr.status,
       COALESCE(pr.sent_at, pr.scheduled_at, pr.created_at)
FROM proactives pr
WHERE pr.user_id = $1 AND pr.id = ANY ($2::varchar[]);

-- users 的偏好列（legacy 表；读 legacy 表在 Go 侧已有先例 internal/store/users.go）。
-- status='active' 与 auth 侧同一口径：注销/封禁用户不得被主动触达。
-- name: GetUserNotificationPrefs :one
SELECT id, timezone, push_enabled, push_frequency, ai_consent, tier,
       daily_quota_used, created_at
FROM users
WHERE id = $1 AND status = 'active';

-- 频次/疲劳/查重的历史行。ref 清单取自 eligibility_detail 的 packet：goal/commitment
-- 没有回链表（N2 DDL 只有 proactive_insights / proactive_patterns），而「同一承诺
-- 最多 2 次回访」（closenai.html:2953）要按 commitment 计数——packet 里的 ref_ids
-- 已在 Freshness 关逐个核实过存在于库，是可信的。解析在领域侧做（store 不拆 JSON）。
-- 窗口条件写 sent_at 与 scheduled_at 两个：未发送的排期行也要挡住重复排第二封。
-- name: ListProactiveHistory :many
SELECT p.id, p.status, p.trigger_type, p.message, p.scheduled_at, p.sent_at,
       p.responded_at, p.eligibility_detail
FROM proactives p
WHERE p.user_id = $1
  AND (p.sent_at >= $2 OR p.scheduled_at >= $2)
ORDER BY p.scheduled_at NULLS LAST, p.id;

-- 沉默阶梯的数据面：用户最后一次主动发言（现行链同一口径，
-- journey_service.py:370-378 走 chat_repository.get_last_user_message）。
--
-- chat_messages 是 legacy 表，**不在** sqlc 的 schema 快照里（0001 只有 users/
-- refresh_tokens，0002 只有 21 张 canonical 表；导出命令见 0002 头部）。为一条
-- 读查询去扩 schema 快照面＝动 scripts/export_pg_schema.py 的表白名单和
-- backend/tests/test_canonical_schema.py 的漂移闸，那不是 N5 的范围。所以这条
-- 留在 Go 侧手写 SQL，先例同 internal/store/users.go:21-23「legacy 表 1:1 翻译」。
-- 实现见 internal/store/canonical_repo.go 的 LastUserActivityAt。

-- 近期被用户否定过的行（04:231「user corrected relevant fact」）。
-- 词表取 02:157-163 的三个负面值，partial_confirm 不算纠正。
-- name: RecentCorrectionTargets :many
SELECT DISTINCT target_id
FROM user_feedback
WHERE user_id = $1 AND feedback_type = ANY ($2::varchar[]) AND created_at >= $3;

-- 重评结论的改写：只有非终态可改（08 §7 的条件更新立场），sent/responded/
-- expired/cancelled 不回头。eligibility_score 保持生成时刻的值——它是那一版
-- 判定的产物，重评的过程写进 eligibility_detail，两侧都要能复现。
-- name: SetProactiveVerdict :one
UPDATE proactives
SET status = $3, block_reason = $4, reject_reason = $5,
    eligibility_detail = $6, updated_at = now()
WHERE id = $1 AND user_id = $2
  AND status IN ('candidate', 'eligible', 'scheduled')
RETURNING id;

-- 发送时刻二次校验通过后的置 sent（与 MarkProactiveDelivered 同一状态守卫，
-- 多带一份重评后的 detail）。
-- name: MarkProactiveSentWithDetail :one
UPDATE proactives
SET status = 'sent', sent_at = now(), eligibility_detail = $3, updated_at = now()
WHERE id = $1 AND user_id = $2
  AND status IN ('candidate', 'eligible', 'scheduled')
RETURNING sent_at;

-- ── Outcome 闭环（04 §15；origin 只允许三种权威来源，由 N6 领域层校验）───────
-- name: InsertOutcome :exec
INSERT INTO outcomes (
	id, user_id, outcome_type, status, result, description, occurred_at, origin,
	model_version, generation_run_id
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10);

-- name: LinkOutcome :exec
INSERT INTO outcome_links (outcome_id, target_type, target_id, user_id)
VALUES ($1, $2, $3, $4)
ON CONFLICT DO NOTHING;

-- ── T4-03：行动结果的「同目标同型最近一条」读面（同日判定在领域层，SQL 只取最近一条）
-- name: LatestOutcomeForTarget :one
-- 与 `LatestFeedbackForTarget` 同一族判据：`outcomes` 表上刻意没有唯一约束
-- （04 §22 的完成度量要按天记账，历史上同一条行动确实可以有多次同型结果），
-- 所以「这次是不是重复提交」只能在领域层判。SQL 只负责给出**最近一条**，
-- `ORDER BY created_at DESC, id DESC` 的次级键与 T4-02 读面同口径（同一时刻落地时
-- 要有全序，否则 LIMIT 1 在重放里不稳定）。
-- `model_version` 在列上是可空的（0002:148-161），这里 COALESCE 成 0：
-- 领域层拿它只为回填回执，NULL 与 0 在客户端是同一个「没带版本」，不必多带一层指针。
SELECT o.id, o.outcome_type, o.status, o.result,
       COALESCE(o.model_version, 0)::bigint AS model_version,
       o.created_at
FROM outcomes o
JOIN outcome_links l ON l.outcome_id = o.id AND l.user_id = o.user_id
WHERE l.user_id = $1 AND l.target_type = $2 AND l.target_id = $3 AND o.outcome_type = $4
ORDER BY o.created_at DESC, o.id DESC
LIMIT 1;

-- ── 反馈（02 §18；重复反馈由领域层判 07 §6 ErrDuplicateFeedback，表上不加唯一约束）
-- name: InsertUserFeedback :exec
INSERT INTO user_feedback (
	id, user_id, feedback_type, target_type, target_id, correction_text, metadata_json
) VALUES ($1, $2, $3, $4, $5, $6, $7);

-- name: LatestFeedbackForTarget :one
-- 「同一个人对同一行的最后一次表态是什么」。表上刻意没有唯一约束（N2 口径：同一目标
-- 可以有多次表态，历史要留），所以 07 §6 的 ErrDuplicateFeedback 只能在领域层判：
-- **最近一条就是同一种表态**才算重复提交；中间只要夹过别的表态（先 no、后 yes、
-- 再 no），那就是新的信息，不是重复。
SELECT id, feedback_type
FROM user_feedback
WHERE user_id = $1 AND target_type = $2 AND target_id = $3
ORDER BY created_at DESC, id DESC
LIMIT 1;

-- ── N6：Outcome 的状态改写（02:176-180 三值；被用户纠正的 outcome 不是删除，
-- 是 corrected/invalidated——01 §13「Correction must NOT simply overwrite」）────
-- name: MarkOutcomeStatus :one
UPDATE outcomes
SET status = $3, updated_at = now()
WHERE id = $1 AND user_id = $2 AND status IN ('recorded', 'candidate')
RETURNING id;

-- ── N6：源失效传播（不变量 9「按 policy 让依赖它的派生 claim 失效」）─────────
-- 一条 evidence 被撤回后，谁还站着它？同时给**总支撑数**与**活支撑数**：
-- 「活」＝所在 source_records.status <> 'deleted'（失效标记只落在 source 层，
-- evidence 行本身不删——O-6/O-7 的不硬删口径）。
-- live_support = 0 的那些行才是「仅依赖它」的派生 claim。
-- name: ListDerivedSupportForEvidence :many
SELECT 'fact'::varchar AS kind, fe.fact_id AS id, f.status,
       (SELECT count(*)
        FROM fact_evidence x WHERE x.fact_id = fe.fact_id) AS total_support,
       (SELECT count(*)
        FROM fact_evidence x
        	JOIN evidence e ON e.id = x.evidence_id
        	JOIN source_records s ON s.id = e.source_record_id
        WHERE x.fact_id = fe.fact_id AND s.status <> 'deleted') AS live_support
FROM fact_evidence fe
	JOIN evidence e ON e.id = fe.evidence_id
	JOIN personal_facts f ON f.id = fe.fact_id
WHERE fe.user_id = $1 AND fe.evidence_id = $2
UNION ALL
SELECT 'pattern', pe.pattern_id, p.status,
       (SELECT count(*)
        FROM pattern_evidence x WHERE x.pattern_id = pe.pattern_id),
       (SELECT count(*)
        FROM pattern_evidence x
        	JOIN evidence e ON e.id = x.evidence_id
        	JOIN source_records s ON s.id = e.source_record_id
        WHERE x.pattern_id = pe.pattern_id AND s.status <> 'deleted')
FROM pattern_evidence pe
	JOIN evidence e ON e.id = pe.evidence_id
	JOIN patterns p ON p.id = pe.pattern_id
WHERE pe.user_id = $1 AND pe.evidence_id = $2
UNION ALL
SELECT 'insight', ie.insight_id, i.status,
       (SELECT count(*)
        FROM insight_evidence x WHERE x.insight_id = ie.insight_id),
       (SELECT count(*)
        FROM insight_evidence x
        	JOIN evidence e ON e.id = x.evidence_id
        	JOIN source_records s ON s.id = e.source_record_id
        WHERE x.insight_id = ie.insight_id AND s.status <> 'deleted')
FROM insight_evidence ie
	JOIN evidence e ON e.id = ie.evidence_id
	JOIN insights i ON i.id = ie.insight_id
WHERE ie.user_id = $1 AND ie.evidence_id = $2
ORDER BY kind, id;

-- ── N6：Outcome → Pattern → Insight 的传播链第二跳（04 §17）──────────────────
-- name: ListInsightsForPattern :many
SELECT ip.insight_id AS id, i.status
FROM insight_patterns ip
	JOIN insights i ON i.id = ip.insight_id
WHERE ip.user_id = $1 AND ip.pattern_id = $2
ORDER BY ip.insight_id;

-- ── N6：完成目标后要取消的排队触达（01 §20 示例「dependent proactive cancelled」）
-- 只取仍在队列里的三态；已 sent/expired/cancelled 的行不是「待取消」，且改写会被
-- N5 的终态守卫拦成 0 行。归属由 eligibility_detail 里的 packet.ref_ids 决定。
-- name: ListQueuedProactives :many
SELECT p.id, p.status, p.trigger_type, p.eligibility_detail
FROM proactives p
WHERE p.user_id = $1 AND p.status IN ('candidate', 'eligible', 'scheduled')
ORDER BY p.scheduled_at NULLS LAST, p.id;

-- ── 投递幂等（18 §4 唯一键 (message_id, consumer)；冲突即已消费，N7 使用）─────
-- 0 行（pgx.ErrNoRows）就是「重复投递」的信号，业务副作用据此跳过。
-- name: InsertInboxMessage :one
INSERT INTO inbox_messages (
	id, message_id, message_type, consumer, status, attempt_count
) VALUES ($1, $2, $3, $4, 'pending', 0)
ON CONFLICT (message_id, consumer) DO NOTHING
RETURNING id;

-- 18 §8：消费事务里要把「已处理」写回幂等表，processed 是唯一的正常终态。
-- name: MarkInboxProcessed :one
UPDATE inbox_messages
SET status = 'processed', processed_at = now(), updated_at = now()
WHERE message_id = $1 AND consumer = $2
RETURNING id;

-- 14 §7 + 17 §9：重复投递必须先回答「为什么重复」——processed（已消费）与
-- failed（已按策略记录永久拒绝）两个终态都要 ACK，只有瞬时失败才 NACK/重投。
-- 本项目里瞬时失败**不落** inbox 行（18 §8 的单事务把 inbox 行一起回滚，重投即重试），
-- 所以 failed 只可能来自永久拒绝路径；两者都跳过业务副作用，但留痕不同。
-- name: GetInboxStatus :one
SELECT status, attempt_count
FROM inbox_messages
WHERE message_id = $1 AND consumer = $2;

-- 18 §8 + 14 §7「permanent rejection was recorded according to policy」：永久拒绝的
-- 留痕必须**活过主事务回滚**，所以这是池面上的独立事务，不走消费事务的 Store。
-- 行可能已存在（并发下另一消费者刚写过），也可能完全不存在（首次投递即被判非法输入）：
-- 必须是 upsert——原来的 UPDATE-only 形状在行不存在时静默 0 行，永久失败就查无实据。
-- name: RecordInboxFailure :one
INSERT INTO inbox_messages (
    id, message_id, message_type, consumer, status, error_code, attempt_count
) VALUES (
    sqlc.arg(id), sqlc.arg(message_id), sqlc.arg(message_type), sqlc.arg(consumer),
    'failed', sqlc.arg(error_code), 1
)
ON CONFLICT (message_id, consumer) DO UPDATE
SET status = 'failed', error_code = excluded.error_code,
    attempt_count = inbox_messages.attempt_count + 1, updated_at = now()
RETURNING id;

-- ── N3 Domain Gate 专用读写（判定与写入钉在同一事务，见 internal/domain.Service.Admit）──
-- 第 2 关要区分「不存在」与「属于别人」两个 reject 码，所以这里**故意不按 user_id 过滤**：
-- 按 user 过滤会让别人的证据查不到，从而把 evidence_not_owned 误报成 evidence_not_found。
-- name: ListEvidenceForGate :many
SELECT e.id, e.user_id, e.strength, e.role, e.source_record_id,
       COALESCE(s.occurred_at, e.created_at) AS observed_at
FROM evidence e
LEFT JOIN source_records s ON s.id = e.source_record_id
WHERE e.id = ANY($1::varchar[]);

-- 第 7 关（fact）：同 key 是否已有活事实。活＝candidate|active（02 §3 fact_status 五值里
-- 只有这两个不是终局），rejected/outdated/invalidated 放行，否则用户纠正过的旧值会永久挡住新值。
-- name: FindLiveFactKey :one
SELECT id
FROM personal_facts
WHERE user_id = $1 AND subject_key = $2 AND predicate = $3 AND object_value = $4
  AND status IN ('candidate', 'active')
ORDER BY created_at DESC
LIMIT 1;

-- 指纹命中既有 pattern ⇒ 刷新，不再插第二条（03:310-311「reactivate equivalent pattern,
-- no duplicates」）。status 不在此处翻：被拒行的复活属 N4 生命周期算分（G-4 采 03 §11），
-- evidence_count 由关联表实数重算，不信任提案自带的计数。
-- name: ReactivatePattern :one
UPDATE patterns p
SET last_supported_at = now(),
    evidence_count = (SELECT count(*) FROM pattern_evidence pe WHERE pe.pattern_id = p.id),
    updated_at = now()
WHERE p.id = $1 AND p.user_id = $2
RETURNING p.evidence_count;

-- ── N4 Evidence 算分 + Insight 九关（03 §5-§7、§14 第 9 关、§17 重算）────────
-- 第 9 关 user history：用户亲手否定过的同指纹 pattern（03 §12:335-338 的抑制签名）。
-- 只认 user_feedback：Gate 自己写的 rejected 行是「领域层没接纳」，不是用户否决，
-- 两者语义不同，拿后者当抑制签名会让一次误判永久封掉一个指纹。
-- name: RecentUserRejection :one
SELECT p.id
FROM patterns p
	JOIN user_feedback uf ON uf.target_type = 'pattern' AND uf.target_id = p.id
WHERE p.user_id = $1 AND p.fingerprint = $2
	AND uf.feedback_type = 'reject'
	AND uf.created_at >= now() - ($3::int * interval '1 day')
ORDER BY uf.created_at DESC
LIMIT 1;


-- ── N7 投递面：outbox（18 §2–§6、17 §2、14 §8/§11；裁决七-6 N7-1…N7-11）──────
-- 口径：at-least-once + 幂等消费，不声称 exactly-once（V3 00 §6、18 §12）。
-- 写入必须与 canonical 状态改写钉在同一事务（18 §2、19 §3），所以这条走 canonicalTx；
-- 认领/确认三条走连接池（worker 在自己的事务里跑，18 §6 的 confirm 之前不得置 published）。

-- 18 §10：message_id 是逻辑操作边界的稳定 ID（本项目取事件行 PK），
-- UNIQUE (message_id) 撞了就是 bug，刻意不用 ON CONFLICT DO NOTHING 把它咽掉。
-- name: InsertOutboxMessage :one
INSERT INTO outbox_messages (
    id, message_id, message_type, schema_version, routing_key, user_id,
    correlation_id, causation_id, trace_id, payload, status, attempt_count, available_at
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'pending', 0, now()
)
RETURNING id;

-- 多 worker 不重复认领的唯一机制是 FOR UPDATE SKIP LOCKED（18 §5、20 §5「Go outbox workers = M」）。
-- available_at 一列两用（N7-2）：失败退避的「何时可再取」+ publishing 的租约到期时刻，
-- 所以谓词只看「到点了没」，不需要 locked_by/lease_token 列；崩溃 worker 的孤儿行到期自动可被重取。
-- 终态 published/dead_lettered 一律排除在认领面之外。
-- name: ClaimOutboxBatch :many
WITH picked AS (
    SELECT id
    FROM outbox_messages
    WHERE status IN ('pending', 'failed', 'publishing')
        AND available_at <= now()
    ORDER BY available_at, id
    LIMIT (sqlc.arg(batch_limit)::int)
    FOR UPDATE SKIP LOCKED
)
UPDATE outbox_messages o
SET status = 'publishing',
    attempt_count = o.attempt_count + 1,
    available_at = now() + (sqlc.arg(lease_seconds)::int * interval '1 second'),
    updated_at = now()
FROM picked
WHERE o.id = picked.id
RETURNING o.id, o.message_id, o.message_type, o.schema_version, o.routing_key,
    o.user_id, o.correlation_id, o.causation_id, o.trace_id, o.payload, o.status,
    o.attempt_count, o.available_at;

-- 18 §6 + 14 §11：只有 broker 确认投递才置 published。`AND status='publishing'` 是租约守卫——
-- 0 行意味着这一笔的认领权已被别的 worker 接管，调用方必须把它当「我没发布成」而不是异常。
-- name: MarkOutboxPublished :one
UPDATE outbox_messages
SET status = 'published', published_at = now(), available_at = now(),
    last_error = NULL, updated_at = now()
WHERE id = $1 AND status = 'publishing'
RETURNING id;

-- 退避阶梯取 14 §8 的原文三级（30s/2m/10m，第 4 次起恒 10m），秒数由领域层算好传进来：
-- SQL 不判「第几次」，判级是领域行为（07:183-186）。
-- sqlc.arg() 是为了让生成物的参数名可读（batch_limit/lease_seconds/retry_after_seconds），
-- 不是 SQL 语义的一部分。
-- name: MarkOutboxRetry :one
UPDATE outbox_messages
SET status = 'failed', last_error = sqlc.arg(last_error),
    available_at = now() + (sqlc.arg(retry_after_seconds)::int * interval '1 second'),
    updated_at = now()
WHERE id = sqlc.arg(id) AND status = 'publishing'
RETURNING id;

-- 17 §9：到达重试上限的消息转 dead_lettered 并留下 failure record，不再进认领面（不阻塞队列）。
-- name: MarkOutboxDeadLettered :one
UPDATE outbox_messages
SET status = 'dead_lettered', last_error = $2, updated_at = now()
WHERE id = $1 AND status = 'publishing'
RETURNING id;

-- 18 §13：保留期只走显式清理入口，且**只清已完成的投递**（pending/failed/publishing/
-- dead_lettered 都不在此列——激进删除会让旧重投绕过幂等窗口的反面是丢掉待办与死信）。
-- name: DeletePublishedOutboxBefore :execrows
DELETE FROM outbox_messages
WHERE status = 'published' AND published_at < $1;

-- 18 §13：inbox 的保留期比 outbox 长，理由是「旧重投必须仍能被识别为重复」，
-- 删早了二次业务副作用就回来了。dead_lettered/failed 留给人排查（17 §9）。
-- name: DeleteProcessedInboxBefore :execrows
DELETE FROM inbox_messages
WHERE status = 'processed' AND processed_at < $1;

-- ── N9：洞察依据展开（05:379 `GET /v1/personal-model/insights/:id/evidence`）────
-- 三条只读查询都是 **additive**：既有 §37 的 `ListEvidenceForInsight`（:60）是 N4 算分
-- 入口（store 的 LoadInsightEvidence），给它加过滤会改 N4 已钉过变异的行为。
-- 「活」的判据 `s.status <> 'deleted'` 与 N6 传播（:539/552/565）逐字同一，
-- evidence 行本身不删（O-6/O-7 不硬删口径）。
-- name: ListLiveEvidenceForInsight :many
SELECT e.id, e.evidence_type, e.role AS evidence_role, e.strength, e.excerpt,
       e.created_at,
       s.id AS source_record_id, s.source_type, s.source_table, s.source_id,
       COALESCE(s.occurred_at, e.created_at) AS observed_at
FROM insight_evidence ie
	JOIN evidence e ON e.id = ie.evidence_id
	JOIN source_records s ON s.id = e.source_record_id
WHERE ie.user_id = $1 AND ie.insight_id = $2
  AND s.status <> 'deleted'
ORDER BY e.created_at DESC;

-- 依据凭空消失必须有解释（N9-6）：同一条洞察给**总支撑**与**活支撑**，
-- withdrawn_count = total - live 由领域层算，SQL 只搬两个数。
-- name: CountInsightEvidenceSupport :one
SELECT count(*)::bigint AS total_support,
       count(*) FILTER (WHERE s.status <> 'deleted')::bigint AS live_support
FROM insight_evidence ie
	JOIN evidence e ON e.id = ie.evidence_id
	JOIN source_records s ON s.id = e.source_record_id
WHERE ie.user_id = $1 AND ie.insight_id = $2;

-- 02 §37「Insight → Pattern → Evidence」的第二跳：一条 join 取回该洞察**所有** pattern
-- 的活依据，不在应用层循环调 ListEvidenceForPattern（N 个 pattern ⇒ N+1 次往返）。
-- name: ListLiveEvidenceForInsightPatterns :many
SELECT pe.pattern_id,
       e.id, e.evidence_type, e.role AS evidence_role, e.strength, e.excerpt,
       e.created_at,
       s.id AS source_record_id, s.source_type, s.source_table, s.source_id,
       COALESCE(s.occurred_at, e.created_at) AS observed_at
FROM insight_patterns ip
	JOIN pattern_evidence pe ON pe.pattern_id = ip.pattern_id AND pe.user_id = ip.user_id
	JOIN evidence e ON e.id = pe.evidence_id
	JOIN source_records s ON s.id = e.source_record_id
WHERE ip.user_id = $1 AND ip.insight_id = $2
  AND s.status <> 'deleted'
ORDER BY pe.pattern_id, e.created_at DESC;

-- ── T3-01（`GET /internal/v3/insights`）：洞察列表的一次聚合读 ────────────────
-- 判据 G-05「无证据不上线」写在 SQL 层而不是应用层：下面那条**内连**
-- insight_evidence→evidence→source_records 带 `s.status <> 'deleted'`，所以
-- 「从来没有依据」与「依据全被撤回」的洞察在这里同形（都不出现）——T2-06 的传播链
-- 要的正是后一种：撤回生效后那条洞察不该还挂在列表里冒充「有依据」。
-- evidence_count 数的是**活依据**，与详情面 `ListLiveEvidenceForInsight`（:795）用
-- 逐字同一条谓词；不在应用层按条数一遍，那是工单点名要避开的 N+1。
-- 分页用 keyset（`(published_at, id)` 严格在游标之后）而不是 OFFSET：两次翻页之间
-- 若有新洞察发布，OFFSET 会让同一条洞察被跳过或重复，而用户据此判「系统漏了我的东西」。
-- `published_at IS NOT NULL`＝从未上线的行不参与这个排序，也不该出现在列表里。
-- name: ListInsightsForBrowsing :many
SELECT i.id, i.statement, i.claim_type, i.status, i.confidence, i.published_at,
       COUNT(*)::bigint AS evidence_count
FROM insights i
	JOIN insight_evidence ie ON ie.insight_id = i.id AND ie.user_id = i.user_id
	JOIN evidence e ON e.id = ie.evidence_id
	JOIN source_records s ON s.id = e.source_record_id
WHERE i.user_id = sqlc.arg(user_id) AND i.status = ANY(sqlc.arg(statuses)::varchar[])
  AND i.published_at IS NOT NULL
  AND s.status <> 'deleted'
  AND (sqlc.arg(after_at)::timestamptz IS NULL OR i.published_at < sqlc.arg(after_at)
       OR (i.published_at = sqlc.arg(after_at) AND i.id < sqlc.arg(after_id)))
GROUP BY i.id, i.statement, i.claim_type, i.status, i.confidence, i.published_at
ORDER BY i.published_at DESC, i.id DESC
LIMIT (sqlc.arg(row_limit)::int);
