-- Canonical schema baseline handed to the Go toolchain (D-7).
-- Provenance: pg_dump of the PostgreSQL 16 database produced by `alembic upgrade head`
--   command: docker exec closenai-pg-dev pg_dump -U postgres -d closenai --schema-only --no-owner --no-privileges -t public.personal_models -t public.personal_model_versions -t public.personal_model_events -t public.source_records -t public.evidence -t public.personal_facts -t public.fact_evidence -t public.personal_goals -t public.personal_commitments -t public.patterns -t public.pattern_evidence -t public.insights -t public.insight_evidence -t public.insight_patterns -t public.proactives -t public.proactive_insights -t public.proactive_patterns -t public.outcomes -t public.outcome_links -t public.user_feedback -t public.inbox_messages
--   captured: 2026-10-19; alembic head revision f6b8d0e2a4c6
-- DDL ownership stays with backend/alembic until N2 defines the canonical tables;
-- this file is the read-only snapshot sqlc/golang tooling generates against.
-- Regenerate with scripts/export_pg_schema.py; do not hand-edit.

--
-- PostgreSQL database dump
--


-- Dumped from database version 16.15 (Debian 16.15-1.pgdg12+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg12+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.evidence (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    source_record_id character varying(36) NOT NULL,
    evidence_type character varying(32) NOT NULL,
    role character varying(24) DEFAULT 'supporting'::character varying NOT NULL,
    strength numeric(5,4),
    observation text,
    excerpt text,
    metadata_json text DEFAULT '{}'::text NOT NULL,
    generation_run_id character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT evidence_strength_check CHECK (((strength IS NULL) OR ((strength >= (0)::numeric) AND (strength <= (1)::numeric))))
);


--
-- Name: fact_evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fact_evidence (
    fact_id character varying(36) NOT NULL,
    evidence_id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL
);


--
-- Name: inbox_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inbox_messages (
    id character varying(36) NOT NULL,
    message_id character varying(128) NOT NULL,
    message_type character varying(128) NOT NULL,
    consumer character varying(64) NOT NULL,
    status character varying(24) DEFAULT 'pending'::character varying NOT NULL,
    error_code character varying(64),
    attempt_count integer DEFAULT 0 NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    processed_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT inbox_messages_attempt_count_check CHECK ((attempt_count >= 0))
);


--
-- Name: insight_evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.insight_evidence (
    insight_id character varying(36) NOT NULL,
    evidence_id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    role character varying(24) DEFAULT 'supporting'::character varying NOT NULL
);


--
-- Name: insight_patterns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.insight_patterns (
    insight_id character varying(36) NOT NULL,
    pattern_id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL
);


--
-- Name: insights; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.insights (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    personal_model_id character varying(36) NOT NULL,
    statement text NOT NULL,
    claim_type character varying(32) NOT NULL,
    status character varying(24) DEFAULT 'draft'::character varying NOT NULL,
    confidence numeric(5,4),
    time_from timestamp with time zone,
    time_to timestamp with time zone,
    generation_model character varying(128),
    generation_version character varying(64),
    generation_run_id character varying(36),
    reject_reason character varying(64),
    gate_detail text,
    model_version bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    published_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT insights_confidence_check CHECK (((confidence IS NULL) OR ((confidence >= (0)::numeric) AND (confidence <= (1)::numeric))))
);


--
-- Name: outcome_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outcome_links (
    outcome_id character varying(36) NOT NULL,
    target_type character varying(32) NOT NULL,
    target_id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL
);


--
-- Name: outcomes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outcomes (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    outcome_type character varying(40) NOT NULL,
    status character varying(24) DEFAULT 'recorded'::character varying NOT NULL,
    result character varying(24) DEFAULT 'unknown'::character varying NOT NULL,
    description text,
    occurred_at timestamp with time zone,
    origin character varying(24) NOT NULL,
    model_version bigint,
    generation_run_id character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: pattern_evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pattern_evidence (
    pattern_id character varying(36) NOT NULL,
    evidence_id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL
);


--
-- Name: patterns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.patterns (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    personal_model_id character varying(36) NOT NULL,
    pattern_type character varying(128) NOT NULL,
    subject_key character varying(128),
    scope character varying(64),
    description text NOT NULL,
    status character varying(24) DEFAULT 'candidate'::character varying NOT NULL,
    confidence numeric(5,4),
    evidence_count integer DEFAULT 0 NOT NULL,
    observed_from timestamp with time zone,
    observed_to timestamp with time zone,
    last_supported_at timestamp with time zone,
    confirmed_at timestamp with time zone,
    rejected_at timestamp with time zone,
    expired_at timestamp with time zone,
    detector_version character varying(64),
    fingerprint character varying(160),
    score_detail text,
    model_version bigint,
    generation_run_id character varying(36),
    reject_reason character varying(64),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT patterns_confidence_check CHECK (((confidence IS NULL) OR ((confidence >= (0)::numeric) AND (confidence <= (1)::numeric)))),
    CONSTRAINT patterns_evidence_count_check CHECK ((evidence_count >= 0))
);


--
-- Name: personal_commitments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.personal_commitments (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    personal_model_id character varying(36) NOT NULL,
    title text NOT NULL,
    description text,
    status character varying(32) DEFAULT 'active'::character varying NOT NULL,
    due_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    goal_id character varying(36),
    skipped_at timestamp with time zone
);


--
-- Name: personal_facts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.personal_facts (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    personal_model_id character varying(36) NOT NULL,
    subject_key character varying(128) NOT NULL,
    predicate character varying(128) NOT NULL,
    object_value text NOT NULL,
    origin character varying(24) NOT NULL,
    status character varying(24) DEFAULT 'candidate'::character varying NOT NULL,
    confidence numeric(5,4),
    first_observed_at timestamp with time zone,
    last_observed_at timestamp with time zone,
    confirmed_at timestamp with time zone,
    invalidated_at timestamp with time zone,
    model_version bigint,
    generation_run_id character varying(36),
    reject_reason character varying(64),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT personal_facts_confidence_check CHECK (((confidence IS NULL) OR ((confidence >= (0)::numeric) AND (confidence <= (1)::numeric))))
);


--
-- Name: personal_goals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.personal_goals (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    personal_model_id character varying(36) NOT NULL,
    title text NOT NULL,
    description text,
    status character varying(32) DEFAULT 'active'::character varying NOT NULL,
    started_at timestamp with time zone,
    target_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: personal_model_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.personal_model_events (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    personal_model_id character varying(36) NOT NULL,
    event_type character varying(128) NOT NULL,
    aggregate_type character varying(64) NOT NULL,
    aggregate_id character varying(36),
    model_version bigint NOT NULL,
    payload text DEFAULT '{}'::text NOT NULL,
    generation_run_id character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: personal_model_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.personal_model_versions (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    personal_model_id character varying(36) NOT NULL,
    version bigint NOT NULL,
    reason character varying(64) NOT NULL,
    source_event_id character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: personal_models; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.personal_models (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    current_version bigint DEFAULT 0 NOT NULL,
    status character varying(32) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT personal_models_current_version_check CHECK ((current_version >= 0))
);


--
-- Name: proactive_insights; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.proactive_insights (
    proactive_id character varying(36) NOT NULL,
    insight_id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL
);


--
-- Name: proactive_patterns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.proactive_patterns (
    proactive_id character varying(36) NOT NULL,
    pattern_id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL
);


--
-- Name: proactives; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.proactives (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    personal_model_id character varying(36) NOT NULL,
    status character varying(24) DEFAULT 'candidate'::character varying NOT NULL,
    trigger_type character varying(64) NOT NULL,
    message text,
    scheduled_at timestamp with time zone,
    sent_at timestamp with time zone,
    responded_at timestamp with time zone,
    expires_at timestamp with time zone,
    eligibility_score numeric(5,4),
    eligibility_detail text,
    block_reason character varying(64),
    reject_reason character varying(64),
    generation_model character varying(128),
    generation_version character varying(64),
    generation_run_id character varying(36),
    model_version bigint,
    journey_event_id character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT proactives_eligibility_score_check CHECK (((eligibility_score IS NULL) OR ((eligibility_score >= (0)::numeric) AND (eligibility_score <= (1)::numeric))))
);


--
-- Name: source_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_records (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    source_type character varying(40) NOT NULL,
    source_table character varying(64) NOT NULL,
    source_id character varying(36) NOT NULL,
    occurred_at timestamp with time zone,
    status character varying(16) DEFAULT 'active'::character varying NOT NULL,
    revision_of character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_feedback; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_feedback (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    feedback_type character varying(32) NOT NULL,
    target_type character varying(32) NOT NULL,
    target_id character varying(36) NOT NULL,
    correction_text text,
    metadata_json text DEFAULT '{}'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: evidence evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_pkey PRIMARY KEY (id);


--
-- Name: fact_evidence fact_evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fact_evidence
    ADD CONSTRAINT fact_evidence_pkey PRIMARY KEY (fact_id, evidence_id);


--
-- Name: inbox_messages inbox_messages_message_id_consumer_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inbox_messages
    ADD CONSTRAINT inbox_messages_message_id_consumer_key UNIQUE (message_id, consumer);


--
-- Name: inbox_messages inbox_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inbox_messages
    ADD CONSTRAINT inbox_messages_pkey PRIMARY KEY (id);


--
-- Name: insight_evidence insight_evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.insight_evidence
    ADD CONSTRAINT insight_evidence_pkey PRIMARY KEY (insight_id, evidence_id);


--
-- Name: insight_patterns insight_patterns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.insight_patterns
    ADD CONSTRAINT insight_patterns_pkey PRIMARY KEY (insight_id, pattern_id);


--
-- Name: insights insights_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.insights
    ADD CONSTRAINT insights_pkey PRIMARY KEY (id);


--
-- Name: outcome_links outcome_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outcome_links
    ADD CONSTRAINT outcome_links_pkey PRIMARY KEY (outcome_id, target_type, target_id);


--
-- Name: outcomes outcomes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outcomes
    ADD CONSTRAINT outcomes_pkey PRIMARY KEY (id);


--
-- Name: pattern_evidence pattern_evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pattern_evidence
    ADD CONSTRAINT pattern_evidence_pkey PRIMARY KEY (pattern_id, evidence_id);


--
-- Name: patterns patterns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patterns
    ADD CONSTRAINT patterns_pkey PRIMARY KEY (id);


--
-- Name: personal_commitments personal_commitments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_commitments
    ADD CONSTRAINT personal_commitments_pkey PRIMARY KEY (id);


--
-- Name: personal_facts personal_facts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_facts
    ADD CONSTRAINT personal_facts_pkey PRIMARY KEY (id);


--
-- Name: personal_goals personal_goals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_goals
    ADD CONSTRAINT personal_goals_pkey PRIMARY KEY (id);


--
-- Name: personal_model_events personal_model_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_model_events
    ADD CONSTRAINT personal_model_events_pkey PRIMARY KEY (id);


--
-- Name: personal_model_versions personal_model_versions_personal_model_id_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_model_versions
    ADD CONSTRAINT personal_model_versions_personal_model_id_version_key UNIQUE (personal_model_id, version);


--
-- Name: personal_model_versions personal_model_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_model_versions
    ADD CONSTRAINT personal_model_versions_pkey PRIMARY KEY (id);


--
-- Name: personal_models personal_models_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_models
    ADD CONSTRAINT personal_models_pkey PRIMARY KEY (id);


--
-- Name: proactive_insights proactive_insights_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proactive_insights
    ADD CONSTRAINT proactive_insights_pkey PRIMARY KEY (proactive_id, insight_id);


--
-- Name: proactive_patterns proactive_patterns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proactive_patterns
    ADD CONSTRAINT proactive_patterns_pkey PRIMARY KEY (proactive_id, pattern_id);


--
-- Name: proactives proactives_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proactives
    ADD CONSTRAINT proactives_pkey PRIMARY KEY (id);


--
-- Name: source_records source_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_records
    ADD CONSTRAINT source_records_pkey PRIMARY KEY (id);


--
-- Name: source_records source_records_user_id_source_table_source_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_records
    ADD CONSTRAINT source_records_user_id_source_table_source_id_key UNIQUE (user_id, source_table, source_id);


--
-- Name: user_feedback user_feedback_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_feedback
    ADD CONSTRAINT user_feedback_pkey PRIMARY KEY (id);


--
-- Name: ix_evidence_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_evidence_run ON public.evidence USING btree (generation_run_id);


--
-- Name: ix_evidence_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_evidence_user_created ON public.evidence USING btree (user_id, created_at);


--
-- Name: ix_evidence_user_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_evidence_user_source ON public.evidence USING btree (user_id, source_record_id);


--
-- Name: ix_fact_evidence_evidence; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fact_evidence_evidence ON public.fact_evidence USING btree (evidence_id);


--
-- Name: ix_fact_evidence_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fact_evidence_user ON public.fact_evidence USING btree (user_id, fact_id);


--
-- Name: ix_inbox_messages_received; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inbox_messages_received ON public.inbox_messages USING btree (received_at);


--
-- Name: ix_inbox_messages_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inbox_messages_type ON public.inbox_messages USING btree (message_type, status);


--
-- Name: ix_insight_evidence_evidence; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_insight_evidence_evidence ON public.insight_evidence USING btree (evidence_id);


--
-- Name: ix_insight_evidence_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_insight_evidence_user ON public.insight_evidence USING btree (user_id, insight_id);


--
-- Name: ix_insight_patterns_pattern; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_insight_patterns_pattern ON public.insight_patterns USING btree (pattern_id);


--
-- Name: ix_insight_patterns_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_insight_patterns_user ON public.insight_patterns USING btree (user_id, insight_id);


--
-- Name: ix_insights_claim_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_insights_claim_type ON public.insights USING btree (user_id, claim_type);


--
-- Name: ix_insights_model_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_insights_model_status ON public.insights USING btree (personal_model_id, status);


--
-- Name: ix_insights_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_insights_run ON public.insights USING btree (generation_run_id);


--
-- Name: ix_insights_user_published; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_insights_user_published ON public.insights USING btree (user_id, published_at);


--
-- Name: ix_insights_user_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_insights_user_status ON public.insights USING btree (user_id, status);


--
-- Name: ix_outcome_links_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outcome_links_target ON public.outcome_links USING btree (target_type, target_id);


--
-- Name: ix_outcome_links_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outcome_links_user ON public.outcome_links USING btree (user_id, target_type, target_id);


--
-- Name: ix_outcomes_user_occurred; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outcomes_user_occurred ON public.outcomes USING btree (user_id, occurred_at);


--
-- Name: ix_outcomes_user_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outcomes_user_type ON public.outcomes USING btree (user_id, outcome_type);


--
-- Name: ix_pattern_evidence_evidence; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pattern_evidence_evidence ON public.pattern_evidence USING btree (evidence_id);


--
-- Name: ix_pattern_evidence_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pattern_evidence_user ON public.pattern_evidence USING btree (user_id, pattern_id);


--
-- Name: ix_patterns_model_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_patterns_model_active ON public.patterns USING btree (personal_model_id, status);


--
-- Name: ix_patterns_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_patterns_run ON public.patterns USING btree (generation_run_id);


--
-- Name: ix_patterns_user_fingerprint_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_patterns_user_fingerprint_unique ON public.patterns USING btree (user_id, fingerprint) WHERE (fingerprint IS NOT NULL);


--
-- Name: ix_patterns_user_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_patterns_user_status ON public.patterns USING btree (user_id, status);


--
-- Name: ix_patterns_user_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_patterns_user_type ON public.patterns USING btree (user_id, pattern_type);


--
-- Name: ix_patterns_user_updated; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_patterns_user_updated ON public.patterns USING btree (user_id, updated_at);


--
-- Name: ix_personal_commitments_goal; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_personal_commitments_goal ON public.personal_commitments USING btree (user_id, goal_id, status);


--
-- Name: ix_personal_commitments_user_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_personal_commitments_user_status ON public.personal_commitments USING btree (user_id, status);


--
-- Name: ix_personal_facts_model_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_personal_facts_model_status ON public.personal_facts USING btree (personal_model_id, status);


--
-- Name: ix_personal_facts_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_personal_facts_run ON public.personal_facts USING btree (generation_run_id);


--
-- Name: ix_personal_facts_user_predicate; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_personal_facts_user_predicate ON public.personal_facts USING btree (user_id, predicate);


--
-- Name: ix_personal_facts_user_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_personal_facts_user_status ON public.personal_facts USING btree (user_id, status);


--
-- Name: ix_personal_goals_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_personal_goals_model ON public.personal_goals USING btree (personal_model_id, status);


--
-- Name: ix_personal_goals_user_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_personal_goals_user_status ON public.personal_goals USING btree (user_id, status);


--
-- Name: ix_personal_model_events_aggregate; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_personal_model_events_aggregate ON public.personal_model_events USING btree (aggregate_type, aggregate_id);


--
-- Name: ix_personal_model_events_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_personal_model_events_run ON public.personal_model_events USING btree (generation_run_id);


--
-- Name: ix_personal_model_events_type_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_personal_model_events_type_created ON public.personal_model_events USING btree (event_type, created_at);


--
-- Name: ix_personal_model_events_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_personal_model_events_user_created ON public.personal_model_events USING btree (user_id, created_at);


--
-- Name: ix_personal_model_versions_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_personal_model_versions_user_created ON public.personal_model_versions USING btree (user_id, created_at);


--
-- Name: ix_personal_models_user_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_personal_models_user_unique ON public.personal_models USING btree (user_id);


--
-- Name: ix_proactive_insights_insight; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_proactive_insights_insight ON public.proactive_insights USING btree (insight_id);


--
-- Name: ix_proactive_insights_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_proactive_insights_user ON public.proactive_insights USING btree (user_id, proactive_id);


--
-- Name: ix_proactive_patterns_pattern; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_proactive_patterns_pattern ON public.proactive_patterns USING btree (pattern_id);


--
-- Name: ix_proactive_patterns_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_proactive_patterns_user ON public.proactive_patterns USING btree (user_id, proactive_id);


--
-- Name: ix_proactives_journey_event; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_proactives_journey_event ON public.proactives USING btree (journey_event_id);


--
-- Name: ix_proactives_model_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_proactives_model_status ON public.proactives USING btree (personal_model_id, status);


--
-- Name: ix_proactives_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_proactives_run ON public.proactives USING btree (generation_run_id);


--
-- Name: ix_proactives_trigger; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_proactives_trigger ON public.proactives USING btree (user_id, trigger_type);


--
-- Name: ix_proactives_user_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_proactives_user_due ON public.proactives USING btree (user_id, status, scheduled_at);


--
-- Name: ix_source_records_pointer; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_records_pointer ON public.source_records USING btree (source_table, source_id);


--
-- Name: ix_source_records_user_occurred; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_records_user_occurred ON public.source_records USING btree (user_id, occurred_at);


--
-- Name: ix_source_records_user_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_records_user_type ON public.source_records USING btree (user_id, source_type);


--
-- Name: ix_user_feedback_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_feedback_target ON public.user_feedback USING btree (user_id, target_type, target_id);


--
-- Name: ix_user_feedback_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_feedback_user_created ON public.user_feedback USING btree (user_id, created_at);


--
-- PostgreSQL database dump complete
--
