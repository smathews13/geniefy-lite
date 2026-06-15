# geniefy-v3 — live deploy + end-to-end verification (U76)

First live deploy to a real Databricks workspace, validating `deploy.sh` (U62) end-to-end and
the agent running in-app against real infra. This is the **D36 "integration-verified at deploy"**
boundary actually exercised — it surfaced a cascade of real integration bugs (all fixed under
**U77**) that hermetic tests + `bundle validate` could not catch.

## Target

| | |
|---|---|
| Workspace | `https://fevm-rd-classic.cloud.databricks.com` (CLI profile `fe-vm-classic`) |
| App | **`geniefy-dev`** → https://geniefy-dev-7474653107059373.aws.databricksapps.com |
| App service principal | `bcc7089c-deac-4f42-b125-4e5681c7d8c1` |
| Lakebase | Autoscaling project `projects/geniefy`, **dev** branch, endpoint `primary` (`ep-curly-sunset-…`), database `geniefy`, schema `geniefy` |
| Warehouse | Serverless Starter (`0336d1a2b47936b4`) · Model: `databricks-claude-sonnet-4-6` (FMAPI) |
| Date | 2026-06-12 |

## What worked first try
`deploy.sh -t dev -p fe-vm-classic`: preflight, `vite build`→`app/static`, ensure-Lakebase, `bundle deploy`, migrate (`run_migrations.py`, idempotent), print URL. Bundle validates on `dev` + `prod`.

## Findings the live deploy surfaced → fixes (U77)
1. **App `database` binding can't target Autoscaling Lakebase** ("instance does not exist"). DAB has no `postgres` app-resource (only classic DB instances). → removed the binding; the app self-OAuths; the Autoscaling **`postgres`** binding is added via the App UI / `databricks apps update --json` (auto-provisions the SP's branch role).
2. **Core packages + built SPA not uploaded** — `geniefy_core`/`geniefy_app`/`app/static` live outside `app/` and are `.gitignore`d, so `bundle sync` dropped them (`ModuleNotFoundError`, then SPA 404). → `deploy.sh` stages the core into `app/`; `databricks.yml` `sync.include` force-uploads the gitignored build artifacts.
3. **Missing runtime deps** → `app/requirements.txt` += `psycopg2-binary`, `openai`, `pyyaml`.
4. **`deploy.sh` never started the app** → added `databricks bundle run geniefy`.
5. **`serving_endpoints.get_open_ai_client()` doesn't exist** in any SDK version. → construct the `openai` client directly against `{host}/serving-endpoints` with a per-call OAuth token.
6. **Lakebase connection used the wrong host/user** — DAB does NOT substitute `${var}` in `app.yaml` env, and the SP's Postgres role name ≠ `me().user_name`. → `_lakebase_connection` consumes the binding-injected `PGHOST/PGPORT/PGDATABASE/PGUSER/PGSSLMODE` (the documented Apps↔Lakebase contract) with `GENIEFY_*` fallback.
7. **A failed statement poisoned the shared connection** (`InFailedSqlTransaction` cascade). → `SessionStore` rolls back before/around every op (self-healing).
8. **FMAPI rejects `response_format=json_object`** ("not supported for this model"). → the transport no longer forwards it; the prompts already demand JSON.
9. **Claude wraps JSON in ```json fences** → strict `json.loads` failed at char 0. → `LLMResponse.json()` strips fences + falls back to the outermost `{…}`.
10. **Column-batch drafts truncated** at `DEFAULT_MAX_TOKENS=1024` (`Unterminated string`). → bumped to 4096 (a cap; cost unchanged).

## Operator steps (not auto-doable via DAB today)
- Add the **`geniefy-db` Lakebase `postgres`** app resource (UI / `apps update`).
- Add the **`fmapi-endpoint` serving-endpoint** app resource (or rely on default FMAPI query access).
- **Grant the app SP** on the working-store schema (run as a Lakebase superuser):
  ```sql
  GRANT USAGE ON SCHEMA geniefy TO "<app-sp-id>";
  GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA geniefy TO "<app-sp-id>";
  GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA geniefy TO "<app-sp-id>";
  ```
- **Known fragility:** `bundle deploy` resets app resources, which re-creates the SP's Lakebase role and **drops the grant** — re-apply the grant after each deploy (or use a persistent role). Tracked follow-on.

## End-to-end run evidence (2026-06-12, `samples.tpch.orders`)
Triggered via the running App (executes **as the SP**): `created → profiling → gathering_context → reasoning → judging → gating → awaiting_input`. Generated, profile-grounded, template-conformant comments:

- **table** (conf 0.91): *purpose: TPC-H customer orders · grain: one row per order (o_orderkey) · primary_keys [o_orderkey] · join_keys o_custkey→customer, o_orderkey→lineitem · use_cases: metric views, churn features, BI demos · caveats: 7.5M rows.*
- `o_orderkey` (0.85) Primary key uniquely identifying each order · `o_custkey` (0.89) FK to the customer · `o_totalprice` (0.86) total order value · `o_orderdate` (0.94) **ranging 1992-01-01 to 1998-08-02** (grounded in the profile min/max) · `o_clerk` (0.88) processing clerk · `o_comment` (0.91) free-text note.
- **needs_input** (Gate confidence-gating worked): `o_orderstatus`, `o_orderpriority` (enum → ask for decode), `o_shippriority` (**always 0 in the data** → ask).

**Result:** the complete designed loop runs live — profile → context → reason → judge → confidence-gated questions → human review. ✅
