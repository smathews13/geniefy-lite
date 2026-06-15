# Applying geniefy-v3 migrations to Lakebase (U17)

Runbook + apply log for getting `migrations/001_init.sql` (U12) onto a Lakebase
Postgres instance. First validated against live infra on **2026-06-12** — this
closes U12's UNVERIFIED audit item (the migration was authored infra-free).

## Target (dev / demo — D28, D29)

| | |
|---|---|
| Workspace | `https://fevm-rd-classic.cloud.databricks.com` (CLI profile `fe-vm-classic`) |
| Lakebase tier | **Autoscaling** (`databricks postgres`; Project → Branch → Endpoint) |
| Project | `projects/geniefy` (UI URL: `/lakebase/projects/595f9dad-0f09-4f60-80bf-9897a8793415`) |
| Branch / endpoint | `production` / `primary` (read-write, `ACTIVE`) |
| Database | **`geniefy`** (created by this runbook — see *Database convention* below) |
| Schema | `geniefy` (created by the migration) |
| Server | PostgreSQL 17.10 |

## Prerequisites

- Databricks CLI **v0.285.0+** (Autoscaling Lakebase uses the `databricks postgres` group). Verified with v0.298.0.
- A Postgres client. `databricks psql` does **not** work with Autoscaling projects — use direct `psql` or a driver. We used `psycopg2-binary` in a throwaway venv (no `psql` on the box).
- The acting identity is the Lakebase project owner (or has `CREATEDB` + privileges on the target database).

## Runbook

```bash
P=fe-vm-classic
PROJ=projects/geniefy
EP=$PROJ/branches/production/endpoints/primary

# Connection coordinates (token is short-lived — generate immediately before use)
HOST=$(databricks postgres list-endpoints $PROJ/branches/production -p $P -o json | jq -r '.[0].status.hosts.host')
TOKEN=$(databricks postgres generate-database-credential $EP -p $P -o json | jq -r '.token')
EMAIL=$(databricks current-user me -p $P -o json | jq -r '.userName')

# 1. Create the app working-store database once (CREATE DATABASE cannot run in a txn → autocommit).
PGPASSWORD=$TOKEN psql "host=$HOST port=5432 dbname=postgres user=$EMAIL sslmode=require" \
  -c 'CREATE DATABASE "geniefy";'

# 2. Apply the migration (idempotent — safe to re-run).
PGPASSWORD=$TOKEN psql "host=$HOST port=5432 dbname=geniefy user=$EMAIL sslmode=require" \
  -f migrations/001_init.sql
```

> Without `psql`: connect with a driver (`psycopg2`/`psycopg`) and run the whole file
> in one `cursor.execute(open('migrations/001_init.sql').read())` inside a transaction,
> then `commit()`. psycopg2's no-parameter `execute` uses the simple-query protocol, so
> the multi-statement script — `DO $$ … $$` blocks, function bodies, seeds — runs as one
> unit. `CREATE DATABASE` must be on a separate **autocommit** connection.

## Database convention (recorded as D30)

The migration creates **schema** `geniefy` inside whatever database it is applied to;
it does not create a database. We apply it to a dedicated database named **`geniefy`**
(not the default `postgres`, whose `public` schema is restricted), so the app's working
store is cleanly isolated. The App backend (U5) connects to database `geniefy` and uses
schema `geniefy`. If the DAB needs this explicit, add a `database` bundle variable
(default `geniefy`) alongside `lakebase_instance`.

## Apply log

**2026-06-12** — applied via `psycopg2-binary` (throwaway venv), profile `fe-vm-classic`:

- `database 'geniefy': created`
- `apply #1: OK (committed)`
- `apply #2 (idempotency re-run): OK (committed)` ✅ idempotency confirmed
- Objects created (full DDL committed without error, so every column/type/index/
  trigger/FK/seed in `001_init.sql` succeeded):
  - **9 enum types:** `apply_status`, `audit_action`, `draft_kind`, `draft_status`,
    `library_scope`, `message_role`, `provider_kind`, `session_mode`, `session_status`
  - **8 tables:** `audit_log`, `column_drafts`, `comment_library`, `context_providers`,
    `session_messages`, `sessions`, `table_drafts`, `templates`

A clean two-pass commit is strong validation: a bad enum value, missing column, or
syntax error would have raised and rolled the transaction back. Field-level spot-checks
(exact `apply_status`/`draft_status` enum labels, seed-row contents, the watchdog index
`idx_sessions_status_updated`) are an optional deeper pass; on 2026-06-12 that follow-up
read was gated by the live-Lakebase-read permission policy and can be re-run with explicit
approval.

## Notes

- **OAuth tokens expire (~1h).** Regenerate `generate-database-credential` per session.
- **Re-running is safe** (the migration is guarded: `if not exists`, guarded enum
  creation, `on conflict do nothing`).
- This is the **dev/demo** target. For a customer (`prod`), the same runbook applies
  against their Lakebase project; the bundle deploy (later unit) wires the App to it.
