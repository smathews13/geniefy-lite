# geniefy-v3 — deploy & operator runbook (U78)

`./deploy.sh` orchestrates the deploy (NFR-D/D35). This runbook covers the **first deploy**, the
**grant-safe redeploy**, and the **one-time operator steps** the bundle can't express today.

## First deploy (creates the app + bundle resources)
```bash
./deploy.sh -t dev -p <cli-profile>            # or -t prod --host https://<workspace>
```
Runs: preflight → `vite build`→`app/static` → ensure Lakebase (Autoscaling project/branch/endpoint)
→ **`bundle deploy`** (App + `geniefy_setup` job + the `sql-warehouse` binding) → migrate → grants
guidance → print URL.

### One-time operator steps (not expressible in DAB today)
After the first deploy, in the **Databricks App UI** (or `databricks apps update --json`):
1. **Lakebase `postgres` binding** — add the `geniefy-db` resource (Autoscaling Lakebase projects
   aren't a bundle `database` resource, U77). This auto-provisions the app SP's branch role and
   injects `PGHOST/PGPORT/PGDATABASE/PGUSER/PGSSLMODE`.
2. **`fmapi-endpoint` serving-endpoint binding** (or rely on default FMAPI access).
3. **OBO user-authorization scope (for apply-to-UC, D48/U85):** App → **User authorization →
   +Add scope → `sql`**. Without it, `X-Forwarded-Access-Token` isn't injected and apply fails with
   a clear per-item error rather than writing as the SP.
4. **Schema grant** for the SP on the working store (run as a Lakebase superuser), and UC grants on
   target tables for the **end user** doing apply: `SELECT` (profiling, the SP) + `MODIFY` + warehouse
   `CAN_USE` (apply, the user). deploy.sh prints the exact `GRANT` lines.

## Grant-safe REDEPLOY (code changes only) — use this for every redeploy
```bash
./deploy.sh -t dev -p <cli-profile> --code-only
```
**Why `--code-only` matters (U77/U78/D48):** a plain `bundle deploy` reconciles the app's `resources`
to `databricks.yml` (which declares only `sql-warehouse`) and **wipes the UI-added `geniefy-db` +
`fmapi-endpoint` bindings → drops the SP's Lakebase role + your schema grants** (the role-churn). The
`--code-only` path instead does **`bundle sync`** (upload files, honoring `sync.include` for the
gitignored staged core + built SPA — *no resource reconciliation*) + **`databricks apps deploy
--source-code-path <files/app>`** (a new code snapshot), leaving resource bindings + grants untouched.
It also skips the SP role re-provision in step 6. This is the path used for all R2 redeploys; verified
bindings preserved (`[sql-warehouse, geniefy-db, fmapi-endpoint]`) across every deploy.

> Requires the app to already exist (run a full deploy once first). `apps deploy` resolves the
> workspace source path from `apps get … .default_source_code_path`.

## Notes / known follow-ons
- **Persistent role (future):** the cleanest long-term fix is a persistent Lakebase role for the SP
  that survives a full `bundle deploy`, or expressing the Autoscaling `postgres` binding in DAB once
  supported — then a full `bundle deploy` would be grant-safe too. Until then, prefer `--code-only`.
- **AI-Gateway routing (optional, D4/NFR-B):** routing the model through `{host}/ai-gateway/mlflow/v1`
  instead of `{host}/serving-endpoints` is a future config (`GENIEFY_MODEL_BASE_PATH`) — not yet wired.
- **IP ACL:** if the workspace enforces an IP allowlist, the deploying machine's egress IP must be
  allowlisted (a `403 … Source IP … blocked` means it isn't).
