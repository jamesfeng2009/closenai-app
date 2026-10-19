-- Canonical schema baseline handed to the Go toolchain (D-7).
-- Provenance: pg_dump of the PostgreSQL 16 database produced by `alembic upgrade head`
--   command: docker exec closenai-pg-dev pg_dump -U postgres -d closenai --schema-only --no-owner --no-privileges -t public.users -t public.refresh_tokens
--   captured: 2026-10-18; alembic head revision c3e5a7b9d1f2
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
-- Name: refresh_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.refresh_tokens (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    token_hash character varying(64) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    issued_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    replaced_by character varying(36)
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id character varying(36) NOT NULL,
    timezone character varying(64) NOT NULL,
    streak_count integer NOT NULL,
    last_active_date date,
    relationship_level character varying(20) NOT NULL,
    chat_count integer NOT NULL,
    daily_quota_used integer NOT NULL,
    tier character varying(20) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    language character varying(10) DEFAULT 'en'::character varying NOT NULL,
    name character varying(100),
    push_time character varying(5),
    chat_style character varying(30),
    active_days integer DEFAULT 0 NOT NULL,
    push_frequency character varying(10) DEFAULT 'daily'::character varying NOT NULL,
    push_enabled boolean DEFAULT false NOT NULL,
    mbti_type character varying(4),
    ai_consent boolean DEFAULT false NOT NULL,
    special_push_count integer DEFAULT 0 NOT NULL,
    special_push_month character varying(7),
    streak_freeze_count integer DEFAULT 0 NOT NULL,
    streak_freeze_grant_month character varying(7),
    birthday date,
    enneagram_type character varying(2),
    email character varying(255),
    username character varying(50),
    password_hash character varying(255),
    apple_id character varying(255),
    email_verified_at timestamp with time zone,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    deleted_at timestamp with time zone,
    analytics_consent boolean DEFAULT false NOT NULL,
    conversation_style character varying(20) DEFAULT 'warm'::character varying NOT NULL,
    referral_code character varying(12),
    referred_by_code character varying(12),
    quiet_start time without time zone,
    quiet_end time without time zone
);


--
-- Name: refresh_tokens refresh_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_pkey PRIMARY KEY (id);


--
-- Name: users uq_users_referral_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT uq_users_referral_code UNIQUE (referral_code);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: ix_refresh_tokens_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_refresh_tokens_hash ON public.refresh_tokens USING btree (token_hash);


--
-- Name: ix_refresh_tokens_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_refresh_tokens_user ON public.refresh_tokens USING btree (user_id);


--
-- Name: ix_users_apple_id_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_users_apple_id_unique ON public.users USING btree (apple_id) WHERE ((apple_id IS NOT NULL) AND (deleted_at IS NULL));


--
-- Name: ix_users_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_deleted ON public.users USING btree (deleted_at) WHERE (deleted_at IS NOT NULL);


--
-- Name: ix_users_email_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_users_email_unique ON public.users USING btree (email) WHERE ((email IS NOT NULL) AND (deleted_at IS NULL));


--
-- Name: ix_users_username_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_users_username_unique ON public.users USING btree (username) WHERE ((username IS NOT NULL) AND (deleted_at IS NULL));


--
-- Name: refresh_tokens refresh_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- PostgreSQL database dump complete
--
