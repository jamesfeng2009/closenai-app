-- Canonical schema baseline handed to the Go toolchain (D-7).
-- Provenance: pg_dump of the PostgreSQL 16 database produced by `alembic upgrade head`
--   command: docker exec closenai-pg-dev pg_dump -U postgres -d closenai --schema-only --no-owner --no-privileges -t public.outbox_messages
--   captured: 2026-10-13; alembic head revision d9b1f3a5c7e9
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
-- Name: outbox_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outbox_messages (
    id character varying(36) NOT NULL,
    message_id character varying(128) NOT NULL,
    message_type character varying(128) NOT NULL,
    schema_version integer DEFAULT 1 NOT NULL,
    routing_key character varying(128) NOT NULL,
    user_id character varying(36) NOT NULL,
    correlation_id character varying(128) DEFAULT ''::character varying NOT NULL,
    causation_id character varying(128) DEFAULT ''::character varying NOT NULL,
    payload text DEFAULT '{}'::text NOT NULL,
    status character varying(24) DEFAULT 'pending'::character varying NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    published_at timestamp with time zone,
    last_error character varying(512),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT outbox_messages_attempt_count_check CHECK ((attempt_count >= 0)),
    CONSTRAINT outbox_messages_schema_version_check CHECK ((schema_version >= 1))
);


--
-- Name: outbox_messages outbox_messages_message_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbox_messages
    ADD CONSTRAINT outbox_messages_message_id_key UNIQUE (message_id);


--
-- Name: outbox_messages outbox_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbox_messages
    ADD CONSTRAINT outbox_messages_pkey PRIMARY KEY (id);


--
-- Name: ix_outbox_messages_claim; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbox_messages_claim ON public.outbox_messages USING btree (status, available_at);


--
-- Name: ix_outbox_messages_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbox_messages_type ON public.outbox_messages USING btree (message_type, status);


--
-- Name: ix_outbox_messages_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbox_messages_user ON public.outbox_messages USING btree (user_id, created_at);


--
-- PostgreSQL database dump complete
--
