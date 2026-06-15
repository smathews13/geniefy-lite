-- geniefy-v3 — Lakebase (Postgres) migration 002: comment_library status lifecycle
-- Implements LLD-amend-006 §A1/A2 (U102 / D52): the comment library becomes a governed
-- definition store with an approved → applied → sunset lifecycle.
--   • approved : a draft was approved/edited in review (write-on-approve, §A3 — net-new;
--                previously only apply wrote the library)
--   • applied  : that comment was SUCCESSFULLY written to UC (apply upgrades it)
--   • sunset   : manually retired — soft, revivable (§A5); excluded from reuse (§A4)
-- Follows the 001_init.sql house style: geniefy.-qualified, lowercase, set search_path,
-- a geniefy enum (not bare TEXT), idempotent (safe to re-run). Schema: geniefy.

set search_path to geniefy;

-- library_status lifecycle enum (matches the enum convention of 001's session/draft/apply status)
do $$ begin
  create type geniefy.library_status as enum ('approved','applied','sunset');
exception when duplicate_object then null; end $$;

-- Lifecycle columns on comment_library. Default 'approved' so any pre-existing rows
-- (written by the old apply-only path, U84) read as the safe baseline; the apply path
-- (U105) upgrades genuinely-applied entries to 'applied'.
alter table geniefy.comment_library
  add column if not exists status     geniefy.library_status not null default 'approved',
  add column if not exists sunset_at  timestamptz,
  add column if not exists sunset_by  text;

-- Reuse lookup (§A4): exact (scope, match_key) + status filter, usage-ranked. The
-- existing idx_comment_library_lookup (scope, match_key) stays; this composite covers
-- the reuse query's status predicate.
create index if not exists idx_comment_library_reuse
  on geniefy.comment_library (scope, match_key, status);
