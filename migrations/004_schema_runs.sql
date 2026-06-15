-- geniefy-v3 — Lakebase (Postgres) migration 004: schema_runs (hands-off / schema-batch).
-- Implements LLD-amend-005 §4 (U109 / D51): a parent record grouping the per-table sessions of a
-- "point at a schema, document every table" run. The Job (U111) creates a run, enumerates tables,
-- and persists one session per table linked via sessions.schema_run_id. uuid PK + geniefy.-qualified
-- + idempotent, matching 001's house style (U101 audit M3). The 'hands_off' session_mode value is
-- added separately in migration 003 (U108).

set search_path to geniefy;

do $$ begin
  create type geniefy.schema_run_status as enum
    ('enumerating','running','completed','completed_with_errors','failed','cancelled');
exception when duplicate_object then null; end $$;

create table if not exists geniefy.schema_runs (
  id            uuid primary key default gen_random_uuid(),
  uc_catalog    text not null,
  uc_schema     text not null,
  status        geniefy.schema_run_status not null default 'enumerating',
  filters       jsonb not null default '{}',     -- {skip_documented, name_like, max_tables}
  total_tables  int,                              -- null until enumerated
  counts        jsonb not null default '{}',      -- rollup: {ready, needs_input, applied, error, skipped}
  job_run_id    bigint,                           -- Databricks Job run id (status/cancel)
  created_by    text not null,                    -- the human who triggered (D48 attribution)
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists idx_schema_runs_recent on geniefy.schema_runs (created_at desc);

-- Link sessions to their parent run (null for ordinary single-table sessions).
alter table geniefy.sessions
  add column if not exists schema_run_id uuid references geniefy.schema_runs(id);
create index if not exists idx_sessions_schema_run on geniefy.sessions (schema_run_id);

-- updated_at trigger (reuse the shared function from 001).
do $$ begin
  drop trigger if exists trg_set_updated_at on geniefy.schema_runs;
  create trigger trg_set_updated_at before update on geniefy.schema_runs
    for each row execute function geniefy.set_updated_at();
end $$;
