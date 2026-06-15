-- geniefy-v3 — Lakebase (Postgres) schema, migration 001 (initial)
-- Implements LLD-data-layer.md (U2) with amendments LLD-amend-001 (U10) and
-- LLD-amend-002 (U11): draft_status += low_confidence/error; new apply_status
-- enum + columns; session_state jsonb rehydration snapshot.
-- Idempotent: safe to re-run (guards on existence). Schema: geniefy.

create extension if not exists pgcrypto;
create schema if not exists geniefy;
set search_path to geniefy;

-- ─────────────────────────────────────────────────────────────────────────────
-- Enum types (U2 §5, amended)
-- ─────────────────────────────────────────────────────────────────────────────
do $$ begin
  create type geniefy.session_mode as enum ('interactive','batch');
exception when duplicate_object then null; end $$;

-- session_status UNCHANGED (U10 F1: "running" = the IN_FLIGHT subset
-- {profiling, gathering_context, reasoning, applying}, handled in watchdog logic)
do $$ begin
  create type geniefy.session_status as enum
    ('created','profiling','gathering_context','reasoning','awaiting_input',
     'ready_for_review','applying','applied','paused','failed','cancelled');
exception when duplicate_object then null; end $$;

-- draft_status: review-lifecycle axis (U2 + U10 F2: += low_confidence, error)
do $$ begin
  create type geniefy.draft_status as enum
    ('draft','needs_input','low_confidence','error',
     'reviewed','edited','approved','applied','rejected');
exception when duplicate_object then null; end $$;

-- apply_status: per-item apply-outcome axis (U10 F2 / U11 NF1+NF2). Distinct from
-- draft_status; 'not_applied' is the initial/default ("pending" in U6 prose),
-- 'skipped_noop' = proposed equals live comment (U11 NF2).
do $$ begin
  create type geniefy.apply_status as enum
    ('not_applied','applied','conflict','failed','unsupported','skipped_noop');
exception when duplicate_object then null; end $$;

do $$ begin
  create type geniefy.message_role as enum ('agent','user','system');
exception when duplicate_object then null; end $$;

do $$ begin
  create type geniefy.provider_kind as enum
    ('builtin_lineage','builtin_query_history','mcp');
exception when duplicate_object then null; end $$;

do $$ begin
  create type geniefy.library_scope as enum ('column','table');
exception when duplicate_object then null; end $$;

do $$ begin
  create type geniefy.draft_kind as enum ('table','column');
exception when duplicate_object then null; end $$;

do $$ begin
  create type geniefy.audit_action as enum
    ('generated','edited','approved','applied','rejected','reverted');
exception when duplicate_object then null; end $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- shared updated_at trigger
-- ─────────────────────────────────────────────────────────────────────────────
create or replace function geniefy.set_updated_at() returns trigger as $$
begin new.updated_at = now(); return new; end;
$$ language plpgsql;

-- ─────────────────────────────────────────────────────────────────────────────
-- Tables (U2 §4, amended)
-- ─────────────────────────────────────────────────────────────────────────────

-- 4.1 templates
create table if not exists geniefy.templates (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  version     int  not null default 1,
  spec        jsonb not null,
  spec_yaml   text,
  is_default  boolean not null default false,
  created_by  text not null,
  created_at  timestamptz not null default now(),
  unique (name, version)
);

-- 4.2 context_providers  (context retrieval only — NOT profiling, U10 F4)
create table if not exists geniefy.context_providers (
  id          uuid primary key default gen_random_uuid(),
  name        text not null unique,
  kind        geniefy.provider_kind not null,
  enabled     boolean not null default true,
  config      jsonb not null default '{}',     -- secret-scope refs only, never raw secrets
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

-- 4.3 sessions  (+ session_state jsonb rehydration snapshot, U10 F5)
create table if not exists geniefy.sessions (
  id            uuid primary key default gen_random_uuid(),
  uc_catalog    text not null,
  uc_schema     text not null,
  uc_table      text not null,
  mode          geniefy.session_mode   not null default 'interactive',
  status        geniefy.session_status not null default 'created',
  template_id   uuid references geniefy.templates(id),
  config        jsonb not null default '{}',   -- RunConfig incl. profiling_provider (U11 NF3)
  session_state jsonb,                          -- rehydration fast-path snapshot (U10 F5)
  mlflow_run_id text,
  created_by    text not null,
  error         text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists idx_sessions_creator_status on geniefy.sessions (created_by, status);
create index if not exists idx_sessions_table on geniefy.sessions (uc_catalog, uc_schema, uc_table);
-- watchdog support (U10 F1): stale in-flight sessions via (status, updated_at)
create index if not exists idx_sessions_status_updated on geniefy.sessions (status, updated_at);

-- 4.4 session_messages
create table if not exists geniefy.session_messages (
  id          uuid primary key default gen_random_uuid(),
  session_id  uuid not null references geniefy.sessions(id) on delete cascade,
  seq         int  not null,
  role        geniefy.message_role not null,
  content     text not null,
  metadata    jsonb not null default '{}',
  created_at  timestamptz not null default now(),
  unique (session_id, seq)
);

-- 4.5 table_drafts  (+ apply_status, U10 F2 / U11)
create table if not exists geniefy.table_drafts (
  id               uuid primary key default gen_random_uuid(),
  session_id       uuid not null references geniefy.sessions(id) on delete cascade,
  uc_catalog       text not null,
  uc_schema        text not null,
  uc_table         text not null,
  current_comment  text,
  proposed_comment text,
  rationale        text,
  confidence       numeric(4,3),
  judge_scores     jsonb,
  status           geniefy.draft_status not null default 'draft',
  apply_status     geniefy.apply_status not null default 'not_applied',
  profile_snapshot jsonb,
  applied_comment  text,
  applied_at       timestamptz,
  applied_by       text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (session_id)
);

-- 4.6 column_drafts  (+ apply_status, U10 F2 / U11)
create table if not exists geniefy.column_drafts (
  id                 uuid primary key default gen_random_uuid(),
  session_id         uuid not null references geniefy.sessions(id) on delete cascade,
  table_draft_id     uuid not null references geniefy.table_drafts(id) on delete cascade,
  column_name        text not null,
  ordinal            int,
  data_type          text,
  current_comment    text,
  proposed_comment   text,
  rationale          text,
  confidence         numeric(4,3),
  judge_scores       jsonb,
  conditional_fields jsonb,
  status             geniefy.draft_status not null default 'draft',
  apply_status       geniefy.apply_status not null default 'not_applied',
  profile_snapshot   jsonb,
  applied_comment    text,
  applied_at         timestamptz,
  applied_by         text,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  unique (session_id, column_name)
);
create index if not exists idx_column_drafts_session on geniefy.column_drafts (session_id);
create index if not exists idx_column_drafts_session_status on geniefy.column_drafts (session_id, status);

-- 4.7 comment_library
create table if not exists geniefy.comment_library (
  id                 uuid primary key default gen_random_uuid(),
  scope              geniefy.library_scope not null,
  match_key          text not null,
  canonical_comment  text not null,
  conditional_fields jsonb,
  tags               jsonb not null default '[]',
  source_session_id  uuid references geniefy.sessions(id) on delete set null,
  source_table_ref   text,
  usage_count        int not null default 0,
  approved_by        text,
  approved_at        timestamptz,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);
create index if not exists idx_comment_library_lookup on geniefy.comment_library (scope, match_key);

-- 4.8 audit_log  (append-only)
create table if not exists geniefy.audit_log (
  id          uuid primary key default gen_random_uuid(),
  session_id  uuid references geniefy.sessions(id) on delete set null,
  draft_kind  geniefy.draft_kind,
  draft_id    uuid,
  action      geniefy.audit_action not null,
  actor       text not null,
  before      jsonb,
  after       jsonb,
  created_at  timestamptz not null default now()
);
create index if not exists idx_audit_session_time on geniefy.audit_log (session_id, created_at);

-- ─────────────────────────────────────────────────────────────────────────────
-- updated_at triggers
-- ─────────────────────────────────────────────────────────────────────────────
do $$
declare t text;
begin
  foreach t in array array['context_providers','sessions','table_drafts','column_drafts','comment_library']
  loop
    execute format(
      'drop trigger if exists trg_set_updated_at on geniefy.%I; '
      'create trigger trg_set_updated_at before update on geniefy.%I '
      'for each row execute function geniefy.set_updated_at();', t, t);
  end loop;
end $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- Seed: built-in context providers (D5) + a default template placeholder
-- ─────────────────────────────────────────────────────────────────────────────
insert into geniefy.context_providers (name, kind, enabled)
values ('uc_lineage', 'builtin_lineage', true),
       ('query_history', 'builtin_query_history', true)
on conflict (name) do nothing;

-- Default template seeded by app setup (spec loaded from the YAML template file);
-- placeholder row guarded so re-runs are safe.
insert into geniefy.templates (name, version, spec, is_default, created_by)
values ('default', 1, '{}'::jsonb, true, 'system')
on conflict (name, version) do nothing;
