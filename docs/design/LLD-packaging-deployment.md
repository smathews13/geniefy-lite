# geniefy-v3 — Low-Level Design: Packaging & Deployment

**Status:** draft (U8) · **Last updated:** 2026-06-11
**Inputs:** [`HLD.md`](HLD.md) (U1) · **Decisions:** D11 (shippable demo), D12 (DAB from GitHub, FE-VM dev, fully parameterized), D6 (Lakebase), D4 (FMAPI + AI Gateway), D3/D15 (profiling via MCP/UC), D18 (app workers + Job for batch), D16 (immutability hook), D25 (deploy packaging specifics — added by this unit)
**Scope:** how the whole solution is **packaged and deployed** — the Databricks Asset Bundle (DAB), parameterized config, FE-VM dev + GitHub distribution, prerequisites, resource wiring, and what ships vs. what stays dev-only. *Out of scope:* the application/agent code itself (code phase), agent internals (U4), the Lakebase schema (U2).

> Completes the design set. Living doc; CI/CD pipeline and batch-Job packaging are sketched and marked.

---

## 1. Purpose & distribution model (D12)

geniefy-v3 ships as a **GitHub repo containing a Databricks Asset Bundle**. A user (us on **FE-VM** for dev/test; a **customer** in their own workspace) runs `databricks bundle deploy` and gets the App + its resources stood up. Everything environment-specific is a **bundle variable / app config** — no hardcoded workspace, catalog, schema, warehouse, Lakebase instance, or model endpoint.

```
git clone … && cd geniefy-v3
databricks bundle deploy -t dev        # FE-VM (us)   /  -t prod (customer)
databricks bundle run geniefy_setup    # migrations + seed (one-shot)
# open the deployed App
```

## 2. Repo / bundle layout (what ships)

```
databricks.yml              # bundle: variables, targets, resources
app/                        # Databricks App: FastAPI backend + built React frontend
  app.yaml                  # command, env, resource bindings
src/geniefy_core/           # agent core library (U4) — reused by app + (deferred) job
migrations/                 # Lakebase schema (U2 §8): enums, tables, triggers, seed
resources/                  # DAB resource defs (app, db instance, job, grants)
profiling/                  # profiling UC function and/or MCP service (U3/D15)
README.md                   # prerequisites + deploy steps (customer-facing)
.gotm/  .claude/            # GOTM build orchestration + dev hook — dev-only (see §8/D25)
```

## 3. The DAB (`databricks.yml`, illustrative)

```yaml
bundle: { name: geniefy-v3 }

variables:
  catalog:        { description: "UC catalog for app data + targets" }
  schema:         { description: "schema for the Lakebase app + UC objects", default: geniefy }
  warehouse_id:   { description: "SQL warehouse for profiling + apply" }
  lakebase_instance: { description: "Lakebase (Postgres) database instance name" }
  model_endpoint: { description: "FMAPI Claude serving endpoint (AI-Gateway-fronted)" }
  mcp_secret_scope: { description: "secret scope holding MCP context-provider tokens", default: geniefy }

targets:
  dev:   { mode: development, default: true, workspace: { host: "${var.host}" } }   # FE-VM
  prod:  { mode: production,  workspace: { host: "${var.host}" } }                   # customer

resources:
  apps:
    geniefy:
      name: "geniefy-${bundle.target}"
      source_code_path: ./app
      # resource bindings injected as env to the app (warehouse, db, endpoint, secrets)
  jobs:
    geniefy_setup:        # one-shot: run migrations + seed default template + built-in providers
      tasks: [ … run migrations/ … ]
    # geniefy_batch:      # DEFERRED (D2/D18): "profile N tables" job
```

## 4. Parameterization (D12)

Every environment value is a **bundle variable** surfaced to the app as env / resource binding — nothing hardcoded:
`host`, `catalog`, `schema`, `warehouse_id`, `lakebase_instance`, `model_endpoint`, `mcp_secret_scope`, plus tunables from `RunConfig` (U4 §7: `keep_threshold`, batch sizes, `context_token_budget`). Dev (FE-VM) and prod (customer) targets differ only in variable values.

## 5. Prerequisites (documented for customers in README) — D12

The target workspace must have: **Lakebase enabled**; an **FMAPI Claude serving endpoint fronted by the AI Gateway** (D4) — or the customer points `model_endpoint` at theirs; a **SQL warehouse**; **Unity Catalog**; and the deploying identity / app **service principal** granted: `SELECT` on target tables (profiling), `MODIFY` on tables to be documented (apply, D7/U6), and access to the Lakebase instance + secret scope. The README ships a one-page checklist + the `bundle deploy` steps.

## 6. App config & resource wiring

The App (Databricks App, FastAPI + React per U5) gets its dependencies via **DAB resource bindings → env**: the SQL warehouse, the Lakebase database instance (D6), the model serving endpoint (D4), and the secret scope for MCP provider tokens (D5; only scope refs in config, never raw secrets). `app.yaml` declares the run command, worker sizing (for in-process background runs, D18), and these bindings. The app SP is the default actor (D11); on-behalf-of is a config toggle.

## 7. Lakebase provisioning + migrations (U2 §8)

The DAB creates or binds the Lakebase **database instance**; the `geniefy_setup` Job (or app first-run) applies `migrations/` in order: `pgcrypto` → enum types → tables → indexes → `updated_at` trigger → seed (default template + built-in `context_providers`). Idempotent so re-deploys are safe.

## 8. Profiling provider deployment (D3/D15 → D25)

D15 made the reference profiling provider an MCP **service** that runs SQL on a warehouse. For a **demo**, standing up a separate service is heavy, so:
- **Default (demo):** the profiling `profile_table` contract (U3) is implemented **inside the app backend** against the bound SQL warehouse — no extra service to deploy.
- **Pluggable:** `config` can instead point at an **external/customer MCP profiling server** or a **UC function** (managed MCP, `…/api/2.0/mcp/functions/{catalog}/{schema}`) for the SQL-expressible subset.
This keeps the demo one-command-deployable while preserving the pluggability customers need to harden (D11). Recorded as **D25**.

## 9. What ships vs. dev-only (D25)

- **`.gotm/`** (GOTM build orchestration) and **`.claude/`** (the dev immutability hook, D16) are **dev-time tooling, not the product**. They remain in the GitHub repo as build provenance, but are **excluded from the deploy artifact** (`bundle sync` excludes) so they're never uploaded to the workspace.
- The immutability hook only affects someone running Claude Code *in the repo*; it's inert for a customer who only deploys. README notes it as a contributor tool.

## 10. Environments / targets

- **dev (FE-VM):** our development + demo workspace; sample datasets (`samples.tpch`, `samples.nyctaxi`, D12) as documentation targets.
- **prod (customer):** customer workspace; their catalogs/tables; their warehouse/endpoint/Lakebase.

## 11. CI/CD (deferred)

- **App deployment CI** (future): a GitHub Action runs `databricks bundle validate` on PR and `bundle deploy -t dev` on merge.
- **Comment-apply CI/CD** (separate, deferred, D7): approved comments export as SQL/DDL → PR → CI applies to UC. This is the *comments* pipeline, distinct from *app* deployment.

## 12. Interfaces to other units

- **U2:** `migrations/` is the schema; setup Job runs it.
- **U4/U5:** the app packages the core + backend; `RunConfig`/provider config surface as bundle vars/env.
- **U3/D15:** profiling provider packaging (§8).
- **U9:** the built frontend (design language) ships in `app/`.

## 13. Deferred / sketched

- **Batch Job** (`geniefy_batch`) for "profile N tables" (D2/D18).
- **Comment-apply CI/CD export** (D7).
- **Separate profiling MCP service** deployment (vs. in-app) for customers who want it isolated.
- **App deployment GitHub Action**.
- **Multi-workspace / parameter presets** for repeated customer deploys.
