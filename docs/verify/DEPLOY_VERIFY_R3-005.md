# R3 / LLD-amend-005 — live deploy + verification of hands-off mode (U113)

**Date:** 2026-06-13/14 · **Target:** `geniefy-dev` (`fevm-rd-classic`, profile `fe-vm-classic`) ·
**App:** https://geniefy-dev-7474653107059373.aws.databricksapps.com · **App SP:** `bcc7089c-…`

Verifies the hands-off / schema-batch feature (U108–U112, U119–U123) end-to-end against the live app
+ a real schema, including the **separate-bundle, grant-safe deploy** design (D54).

## Deploy (two independent bundles)
- **App** — `./deploy.sh -t dev -p fe-vm-classic --code-only`: ships the Schema tab + hands-off
  backend; the U117 branch-aware migrate resolved the **dev** branch (`ep-curly-sunset`) and applied
  **migrations 001–004 (incl. 003 hands_off enum + 004 schema_runs) to the dev branch the app reads**.
- **Job** — `./deploy_jobs.sh -t dev -p fe-vm-classic`: the standalone `geniefy_schema_run` job in its
  OWN bundle (`jobs-bundle/`), `run_as` the app SP, with the SP granted `CAN_MANAGE` (file read) +
  `CAN_MANAGE_RUN`.
- **Grant-safety (D54) CONFIRMED:** across the app `--code-only` deploy and **every** `geniefy-jobs`
  `bundle deploy`, the app's `geniefy-db` · `fmapi-endpoint` · `sql-warehouse` bindings stayed intact.
  Deploying the job never reconciles the app → the SP's Lakebase role + grants are never dropped.

## Live findings (each fixed + re-verified — these only surface outside the App runtime)
1. **U121** — the app SP couldn't trigger the job (`jobs.list()` omitted a job it had no permission on).
   Fix: declare SP `CAN_MANAGE_RUN` in the jobs bundle → SP triggers it.
2. **U122** — `spark_python_task` runs the file via `exec()`, so `__file__` is undefined → the sys.path
   code crashed. Fix: resolve `geniefy_app` without `__file__` (direct import, else cwd/argv hunt).
3. **U123 (operator)** — `run_as`=SP needs the deployer to have `servicePrincipal.user` on the SP
   (human granted it); then the SP couldn't read the dev-mode bundle files in the deployer's home →
   added a bundle-level `permissions` block (SP `CAN_MANAGE`) so DAB grants the SP file access.
4. **U123 (code)** — `w.config.oauth_token()` works on the App runtime but **not on a Job cluster**
   (no such method on the runtime credential strategy). Fix: `lakebase_db_token` mints a Lakebase
   credential via `POST /api/2.0/postgres/credentials` (per the cross-workspace reference) and
   `workspace_bearer` falls back to `config.authenticate()` for FMAPI — **App path unchanged**
   (`oauth_token()` tried first). `GENIEFY_LAKEBASE_ENDPOINT` wired through the job.
5. **U123 (SystemExit)** — the entrypoint's `sys.exit(0)` raised `SystemExit` under `exec()` → the job
   reported FAILED + a spurious retry. Fix: return on success, exit non-zero only on failure.

## End-to-end evidence (real schema `rd_classic_catalog.gaming_data`, job run AS the SP)
- **Run `d572002a` → `status=completed`, 3 tables:** generated + persisted one session per table with
  **rich PROSE comments + free-form tags** — e.g. gold_arpdau: *"This gold-layer aggregation table
  delivers Average Revenue Per Daily Active User (ARPDAU) broken down by calendar date and country…"*,
  table tags `[fact, metric, temporal, dimension]`, column tags `[identifier, key, internal]`; sessions
  in `ready_for_review` (1) + `awaiting_input` (2, **clarifying questions persisted for deferred answer**).
  The job (as the SP) minted the Lakebase credential, called FMAPI (bearer), profiled the warehouse, and
  never wrote to UC (generation only, Q7).
- **Idempotent retry (D20) CONFIRMED:** the SystemExit-induced retry of `d572002a` re-ran the loop and
  **skipped all 3 already-done tables** (`skipped: 3`) — session_exists short-circuit works.
- **Run `f089bd82` (post-SystemExit-fix, 1 table) → JOB `result_state=SUCCESS`, run `completed`** — the
  job now reports green cleanly (no spurious retry).
- **App reads the results:** `GET /api/schema-runs/{id}` returns the run + rollup counts + per-table
  sessions; the deferred-answer→`execute_resume`→apply path reuses the verified single-table flow (D17).

## Covered hermetically (not re-run live here)
- 370 hermetic tests (incl. the U123 cross-runtime auth helpers — App vs cluster branches).
- The full deferred answer→resume→apply round-trip is the unchanged D17/U52/U6 path (live-verified for
  single-table sessions in R3-006); a hands-off session opens into those same screens.

## Follow-ons (tracked, non-blocking)
- **U124** — `enumerate_tables` surfaced internal `__materialization_mat_*` metric-view artifacts; refine
  the predicate to exclude internal/system tables.
- **U125** — remove/realize the dead `config.lakebase_endpoint` fallback (U123 audit MED).
- **U114** (steward-facts chips), **U118** (`--use-job` branch param).

## Result
Hands-off mode is **live and working** on `geniefy-dev`: point at a schema → SP-run Job (separate,
grant-safe bundle) → enumerate → generate rich tagged prose comments → persist per-table sessions with
deferred questions → finalize, **never writing to UC**, with the app's grants untouched throughout.
