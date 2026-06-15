# geniefy-v3 — Low-Level Design: Data Layer & Domain Model

**Status:** draft (U2) · **Last updated:** 2026-06-11
**Inputs:** [`HLD.md`](HLD.md) (U1) · **Decisions:** D6 (Lakebase), D7 (apply/diff/audit), D5 (context providers), D8 (judge scores), D2 (v1 scope)
**Scope:** the Lakebase (Postgres) working store — the backbone every other component reads/writes. Profiling-tool contract, agent-core internals, and app/API surfaces are out of scope (units U3–U6).

> This LLD is a living doc. v1 tables are specified concretely; deferred entities are sketched and marked. Append revisions as follow-on units; do not rewrite history.

---

## 1. Purpose

A durable, transactional store for: **sessions** (with pause/resume + conversation), **drafts** (table & column, current-vs-proposed + lifecycle), the **comment library**, **templates**, **context-provider config**, and an **audit log**. OLTP access patterns (per-session reads/writes, status transitions, history listing) fit Postgres; Lakebase provides it as an attachable App resource (D6).

**Lakebase is the *working* store. Unity Catalog is the *applied* truth; git becomes the deployed source of truth via the deferred CI/CD export (D7).**

---

## 2. Conventions

- **DB:** Postgres (Lakebase). Schema `geniefy`.
- **Keys:** `id uuid primary key default gen_random_uuid()` (pgcrypto). Natural keys carried as columns, not PKs.
- **Timestamps:** `timestamptz`, default `now()`; `updated_at` maintained by a shared trigger.
- **Enums:** Postgres `ENUM` types for status fields (listed in §5).
- **Flexible/evolving shapes** (profile snapshots, judge breakdowns, rubric, provider config): `jsonb`. Anything queried/filtered hot is a real column.
- **UC table reference:** stored as three columns `uc_catalog`, `uc_schema`, `uc_table` (+ `uc_column` where relevant), never a single dotted string — so we can index/join on parts.
- **Soft state, hard audit:** drafts mutate in place through their lifecycle; every transition also appends an immutable `audit_log` row.

---

## 3. Entity-relationship overview

```
templates ─────┐
               │ (template_id)
context_providers      sessions 1──* session_messages
   (global config)        │ 1
                          │
                          ├──* table_drafts  1──* column_drafts
                          │         │                  │
                          │         └──────┬───────────┘
                          │                │ (on approve/apply, may upsert)
                          ▼                ▼
                      audit_log        comment_library
                   (* per session/draft)  (cross-session, reusable)
```

- A **session** targets one UC table, uses one **template**, and produces one **table_draft** + many **column_drafts**.
- **session_messages** is the ordered conversation (agent questions, user answers, system events) powering interactive Q&A + pause/resume.
- **comment_library** is cross-session: approved drafts/glossary terms reused as suggestions.
- **context_providers** is global config (which providers are enabled), referenced by sessions via a snapshot in `session.config`.
- **audit_log** is append-only, references sessions and (optionally) a specific draft.

---

## 4. Tables (v1)

### 4.1 `templates`
The comment spec ("what good looks like"). Versioned; immutable per version.

```sql
create table geniefy.templates (
  id           uuid primary key default gen_random_uuid(),
  name         text not null,
  version      int  not null default 1,
  spec         jsonb not null,            -- parsed template (table_comment / column_comment blocks)
  spec_yaml    text,                      -- original YAML, for round-trip/display
  is_default   boolean not null default false,
  created_by   text not null,
  created_at   timestamptz not null default now(),
  unique (name, version)
);
```

### 4.2 `context_providers`
Global registry of context sources (built-in + MCP). Referenced by sessions via a config snapshot.

```sql
create table geniefy.context_providers (
  id          uuid primary key default gen_random_uuid(),
  name        text not null unique,                 -- e.g. "uc_lineage", "query_history", "glean"
  kind        geniefy.provider_kind not null,       -- builtin_lineage | builtin_query_history | mcp
  enabled     boolean not null default true,
  config      jsonb not null default '{}',          -- mcp endpoint ref, secret scope key, scopes; NEVER raw secrets
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
```
> Secrets (MCP tokens) live in Databricks secret scopes; `config` stores only references.

### 4.3 `sessions`
One documentation run against one table.

```sql
create table geniefy.sessions (
  id            uuid primary key default gen_random_uuid(),
  uc_catalog    text not null,
  uc_schema     text not null,
  uc_table      text not null,
  mode          geniefy.session_mode   not null default 'interactive',  -- interactive | batch
  status        geniefy.session_status not null default 'created',
  template_id   uuid references geniefy.templates(id),
  config        jsonb not null default '{}',   -- sample_size, gate thresholds, enabled-provider snapshot, warehouse_id
  mlflow_run_id text,                           -- trace linkage (D8)
  created_by    text not null,
  error         text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index on geniefy.sessions (created_by, status);
create index on geniefy.sessions (uc_catalog, uc_schema, uc_table);
```

### 4.4 `session_messages`
Ordered conversation for interactive Q&A + resume.

```sql
create table geniefy.session_messages (
  id          uuid primary key default gen_random_uuid(),
  session_id  uuid not null references geniefy.sessions(id) on delete cascade,
  seq         int  not null,                       -- monotonic within session
  role        geniefy.message_role not null,       -- agent | user | system
  content     text not null,
  metadata    jsonb not null default '{}',         -- e.g. which draft a question targets, confidence at ask time
  created_at  timestamptz not null default now(),
  unique (session_id, seq)
);
```

### 4.5 `table_drafts`
The proposed table-level comment.

```sql
create table geniefy.table_drafts (
  id               uuid primary key default gen_random_uuid(),
  session_id       uuid not null references geniefy.sessions(id) on delete cascade,
  uc_catalog       text not null,
  uc_schema        text not null,
  uc_table         text not null,
  current_comment  text,                            -- existing comment in UC at profile time (diff source)
  proposed_comment text,
  rationale        text,                             -- model's reasoning
  confidence       numeric(4,3),                     -- 0.000–1.000 (D8)
  judge_scores     jsonb,                            -- rubric breakdown (completeness/specificity/grounded)
  status           geniefy.draft_status not null default 'draft',
  profile_snapshot jsonb,                            -- sanitized table-level profile used (D4)
  applied_comment  text,                             -- exactly what was written to UC (may differ from proposed after edits)
  applied_at       timestamptz,
  applied_by       text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (session_id)                                -- one table_draft per session
);
```

### 4.6 `column_drafts`
Proposed per-column comments.

```sql
create table geniefy.column_drafts (
  id                 uuid primary key default gen_random_uuid(),
  session_id         uuid not null references geniefy.sessions(id) on delete cascade,
  table_draft_id     uuid not null references geniefy.table_drafts(id) on delete cascade,
  column_name        text not null,
  ordinal            int,                            -- column position
  data_type          text,
  current_comment    text,
  proposed_comment   text,
  rationale          text,
  confidence         numeric(4,3),
  judge_scores       jsonb,
  conditional_fields jsonb,                          -- units, allowed_values, null_meaning, fk_reference, derivation, sensitivity
  status             geniefy.draft_status not null default 'draft',
  profile_snapshot   jsonb,                          -- sanitized column-level stats
  applied_comment    text,
  applied_at         timestamptz,
  applied_by         text,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  unique (session_id, column_name)
);
create index on geniefy.column_drafts (session_id);
create index on geniefy.column_drafts (session_id, status);
```

### 4.7 `comment_library`
Cross-session reusable comments + glossary terms (v1: suggestion by column-name match; D2 defers richer reuse).

```sql
create table geniefy.comment_library (
  id                 uuid primary key default gen_random_uuid(),
  scope              geniefy.library_scope not null,    -- column | table
  match_key          text not null,                     -- normalized column name (or semantic key) for lookup
  canonical_comment  text not null,
  conditional_fields jsonb,
  tags               jsonb not null default '[]',
  source_session_id  uuid references geniefy.sessions(id) on delete set null,
  source_table_ref   text,                              -- "catalog.schema.table[.column]" for provenance/display
  usage_count        int not null default 0,
  approved_by        text,
  approved_at        timestamptz,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);
create index on geniefy.comment_library (scope, match_key);
```

### 4.8 `audit_log`
Append-only. Never updated/deleted.

```sql
create table geniefy.audit_log (
  id          uuid primary key default gen_random_uuid(),
  session_id  uuid references geniefy.sessions(id) on delete set null,
  draft_kind  geniefy.draft_kind,                    -- table | column | null (session-level event)
  draft_id    uuid,                                  -- table_drafts.id or column_drafts.id (loose ref by design)
  action      geniefy.audit_action not null,         -- generated | edited | approved | applied | rejected | reverted
  actor       text not null,
  before      jsonb,
  after       jsonb,
  created_at  timestamptz not null default now()
);
create index on geniefy.audit_log (session_id, created_at);
```

---

## 5. Enum types

```sql
create type geniefy.session_mode   as enum ('interactive','batch');
create type geniefy.session_status as enum
  ('created','profiling','gathering_context','reasoning','awaiting_input',
   'ready_for_review','applying','applied','paused','failed','cancelled');
create type geniefy.draft_status   as enum
  ('draft','needs_input','reviewed','edited','approved','applied','rejected');
create type geniefy.message_role   as enum ('agent','user','system');
create type geniefy.provider_kind  as enum ('builtin_lineage','builtin_query_history','mcp');
create type geniefy.library_scope  as enum ('column','table');
create type geniefy.draft_kind     as enum ('table','column');
create type geniefy.audit_action   as enum ('generated','edited','approved','applied','rejected','reverted');
```

---

## 6. State machines

### 6.1 Session status
```
created
  → profiling → gathering_context → reasoning
  → awaiting_input ⇄ reasoning         (interactive: ask question, resume on answer)
  → ready_for_review
  → applying → applied
  (any) → paused → (resume to prior)   (pause/resume)
  (any) → failed                       (error recorded in sessions.error)
  (any) → cancelled
```
Hands-free mode skips `awaiting_input` (low-confidence drafts are flagged, not asked) and lands in `ready_for_review`.

### 6.2 Draft status
```
draft
  → needs_input → draft                 (interactive clarification raised/resolved)
  → reviewed                            (human looked, no edit)
  → edited                              (human changed proposed_comment)
  → approved                            (cleared to apply)
  → applied                             (written to UC; applied_* set)
  → rejected                            (will not be applied)
```
`reviewed`/`edited` both lead to `approved`. Only `approved` drafts are eligible for the Apply write path (D7).

---

## 7. Key access patterns

| # | Operation | Query shape |
|---|---|---|
| P1 | Load a session for the review UI | `sessions` by id + `table_drafts` (1) + `column_drafts` (by session_id, ordered by `ordinal`) + recent `session_messages` |
| P2 | List a user's sessions (history) | `sessions where created_by=? order by updated_at desc` (index on `created_by,status`) |
| P3 | Resume an interactive session | latest `session_messages.seq`; session.status `awaiting_input`→`reasoning` |
| P4 | Library suggestion for a column | `comment_library where scope='column' and match_key=normalize(column_name)` (index) |
| P5 | Apply eligible drafts | `table_drafts`/`column_drafts where session_id=? and status='approved'` |
| P6 | Audit trail for a table | `audit_log` joined via `session_id`, filtered by table ref through `sessions` |
| P7 | Diff render | `current_comment` vs `proposed_comment`/`applied_comment` per draft (no extra query) |

---

## 8. Migrations & lifecycle

- **Tooling:** plain SQL migration files (`migrations/NNN_*.sql`) applied at app start / via a one-shot Job. (Alembic optional later; v1 keeps it simple.)
- **Bootstrap order:** `pgcrypto` extension → enum types (§5) → tables (§4) → indexes → `updated_at` trigger.
- **`updated_at` trigger:** shared `before update` trigger setting `updated_at = now()` on `sessions`, `*_drafts`, `comment_library`, `context_providers`.
- **Seeding:** insert the default template and the two built-in `context_providers` (`uc_lineage`, `query_history`, `enabled=true`).
- **Retention:** none in v1 (sessions/drafts retained). Revisit when batch produces volume.

---

## 9. Deferred / sketched (not built in v1)

- **`batch_runs`** — groups many sessions from one "profile N tables" Job invocation (D2 batch).
- **`approvals` / export tracking** — for the approval → export-as-code → CI/CD path (D7): reviewer assignment, export artifact ref (git PR URL), deployed status.
- **`profile_cache`** — cache sanitized profiles keyed by (table ref, table version) to avoid re-profiling on re-runs.
- **Richer library reuse** — semantic match (embeddings) beyond exact `match_key`; promotion workflow from draft → library.

These get their own follow-on LLD units and migrations when their scope opens.

---

## 10. Open questions touching this layer

- **Q2 (audience)** — drives whether `created_by` is an end-user (on-behalf-of) or the app SP, and whether row-level access controls are needed.
- **Q3 (workspace/catalog)** — the warehouse/catalog stored in `session.config` for development.

See [`.gotm/QUESTIONS.md`](../../.gotm/QUESTIONS.md).
