# geniefy-lite — Deployment Guide

`./deploy.sh` is the single-command deploy orchestrator. It handles the frontend build, Lakebase Autoscaling provisioning, bundle deploy, database migrations, and prints grant guidance.

## Prerequisites

| Tool | Minimum version | Install |
|------|----------------|---------|
| Databricks CLI | 0.285.0 | `brew install databricks` / [docs](https://docs.databricks.com/dev-tools/cli/install.html) |
| Node.js + npm | 18+ | `brew install node` |
| jq | any | `brew install jq` |
| Python 3 + psycopg2-binary | 3.9+ | `pip install psycopg2-binary` (local migrate path) |

Authenticate the CLI before deploying:
```bash
databricks auth login --host https://<your-workspace-url> -p <profile-name>
```

---

## First deploy

Builds the frontend, provisions Lakebase, deploys the bundle, applies migrations, and prints the app URL:

```bash
./deploy.sh -t prod --host https://<your-workspace-url> -p <cli-profile>
```

Override bundle variables with `--var key=value`:
```bash
./deploy.sh -t prod -p <profile> \
  --var catalog=<your-catalog> \
  --var warehouse_id=<sql-warehouse-id>
```

### What `deploy.sh` does (each step is idempotent — safe to re-run)

1. **Preflight** — checks CLI version ≥ 0.285, jq, npm, and workspace auth
2. **Build frontend** — runs `vite build` in `app/frontend/`, outputs to `app/static/`
3. **Lakebase** — creates the Autoscaling project/branch/endpoint if absent; waits for it to become ACTIVE
4. **Bundle deploy** — runs `databricks bundle deploy` (App + `geniefy_setup` job + SQL warehouse binding)
5. **Migrations** — applies `migrations/*.sql` to the `geniefy` database (local runner; pass `--use-job` for the bundle-native path)
6. **Grants** — prints the Unity Catalog and Lakebase grants the app service principal needs
7. **URL** — prints the deployed app URL

### One-time post-deploy steps (in the Databricks App UI)

After the first deploy, add these resource bindings in the **App resource bindings** panel:

1. **Lakebase `postgres` binding** — add the `geniefy-db` resource. This provisions the app SP's database role and injects the Postgres connection environment variables.
2. **`fmapi-endpoint` serving-endpoint binding** — (or rely on default FMAPI access if your workspace allows it).
3. **User authorization scope** — App → **User authorization → +Add scope → `sql`**. Without this, apply-to-UC writes fail with a clear per-item error.
4. **Grant the app SP on UC tables** (run as metastore/table owner):
   ```sql
   GRANT SELECT ON TABLE <catalog>.<schema>.<table> TO `<app-sp-client-id>`;
   GRANT MODIFY ON TABLE <catalog>.<schema>.<table> TO `<app-sp-client-id>`;
   ```
   The exact grant lines are also printed at the end of `deploy.sh`.

---

## Redeploying (code changes)

Use `--code-only` for all redeployments after the first. This uploads changed files without reconciling bundle resources, so your Lakebase and serving-endpoint bindings and the SP's Postgres role are preserved.

```bash
./deploy.sh -t prod -p <cli-profile> --code-only
```

> **Why `--code-only`?** A plain `bundle deploy` reconciles the app's declared resources to `databricks.yml` (SQL warehouse only) and removes any bindings you added through the UI — including the Lakebase `postgres` binding — which drops the app SP's Postgres role. `--code-only` syncs files and deploys a new code snapshot without touching resource bindings or grants.

---

## Configuration

Bundle variables can be set at deploy time with `--var key=value`:

| Variable | Default | Description |
|---|---|---|
| `catalog` | `<your-catalog>` | UC catalog for geniefy objects |
| `schema` | `geniefy` | UC schema for geniefy objects |
| `warehouse_id` | _(required)_ | SQL warehouse ID for profiling and the apply write-path |
| `model_endpoint` | `databricks-claude-sonnet-4-6` | FMAPI serving endpoint for AI generation |
| `demo_catalog` | `samples` | Catalog with demo tables |
| `lakebase_instance` | `geniefy` | Lakebase Autoscaling project name |
| `pg_host` | _(set by deploy.sh)_ | Lakebase endpoint host — `deploy.sh` resolves this automatically |

Model and agent tunables (temperature, token budgets, retry limits) are in `app/app.yaml`.

---

## Flags

```
./deploy.sh [flags]

  -t <dev|prod>         Bundle target (default: dev)
  -p <cli-profile>      Databricks CLI auth profile
  --host <url>          Workspace URL (alternative to profile)
  --skip-build          Skip the vite frontend build (reuse existing app/static/)
  --use-job             Run migrations via the geniefy_setup bundle job instead of locally
  --code-only           Grant-safe redeploy: sync files + deploy code without bundle reconciliation
  -h / --help           Show usage
```

---

## Notes

- **IP allowlist**: if the workspace enforces an IP allowlist, the machine running `deploy.sh` must have its egress IP allowlisted. A `403 … Source IP … blocked` error means it is not.
- **Lakebase endpoint stability**: `deploy.sh` uses the `production` Lakebase branch by default (for both dev and prod targets), since the production endpoint host is stable. Override with `GENIEFY_LAKEBASE_BRANCH=dev ./deploy.sh` to target an isolated branch.
- **Frontend build**: the built frontend (`app/static/`) is gitignored and must be produced locally by `deploy.sh`. Pass `--skip-build` on a redeploy to reuse an existing build.
- **Persistent role (future)**: the cleanest long-term improvement is a persistent Lakebase role for the SP that survives a full `bundle deploy`, or DAB support for Autoscaling Lakebase `postgres` bindings. Until then, use `--code-only` for redeployments.
