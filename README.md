# geniefy-lite

**Make your lakehouse AI ready.** geniefy-lite is an agent that documents Unity Catalog tables — it
profiles a table's data and gathers usage context, then has Claude draft reviewable,
template-conformant **table and column comments**, plus steward facts and tags, that make the table
AI-/Genie-ready. It runs **interactively** (point at one table, answer the agent's questions, edit,
apply) or **hands-off** (point at a whole schema; a Databricks Job documents every table in the
background). Delivered as a Databricks App over a reusable, hermetic agent core, with a **governed,
human-approved** path to write comments back to Unity Catalog.

> **Architecture:** see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the component map, the agent
> pipeline, the two run modes, the data + security model, and the deploy topology.

## Why

Unity Catalog table/column comments are exactly what Genie, AI/BI, and text-to-SQL agents read to
understand a table. Better comments → fewer hallucinated joins, correct filters, the right grain. The
deliverable is **structured metadata an LLM can act on**, not prose for humans.

## What it does

- **Interactive documentation** — profile → gather context → draft grounded comments/tags/steward facts
  → review, answer clarifying questions, edit, and apply.
- **Hands-off schema runs** — point at a `catalog.schema`; a Job documents every table; come back to
  review. **The Job never writes to Unity Catalog** — apply is always human-in-the-loop.
- **Comment library** — approved comments are stored and reused (suggestion-only) on future runs, with a
  lifecycle of `approved → applied → sunset` (sunset is soft + revivable).
- **Steward-first review** — a confidence-scored hero card with steward facts (owner · freshness · grain
  · keys · sensitivity) + tags, columns clearly secondary, and an explainability/Judge view.
- **Governed apply** — writes go to UC **on-behalf-of you** (your grants), stamped with you as the actor.

## Screenshots

See [`docs/SCREENSHOTS.md`](docs/SCREENSHOTS.md) for a visual product tour — point at a table, review
drafts with per-column confidence and profile fingerprints, answer the agent's clarifying questions
(with suggested answers), inspect the Judge's "why," and apply to make the table AI-/Genie-ready.

## Deploy

geniefy-lite ships as a [Databricks Asset Bundle](databricks.yml) plus a thin shell orchestrator. Clone
it and run [`./deploy.sh`](deploy.sh) to stand up the App and what it needs; environment-specific values
are bundle variables where the DAB allows (workspace host comes from your CLI profile). The hands-off Job
lives in a **separate bundle** ([`jobs-bundle/`](jobs-bundle)) deployed by
[`./deploy_jobs.sh`](deploy_jobs.sh). Full design:
[`docs/design/LLD-packaging-deployment.md`](docs/design/LLD-packaging-deployment.md).

### Prerequisites

The target workspace needs:

- **Unity Catalog** enabled.
- **Lakebase** (Autoscaling managed Postgres) — the app's working store.
- A **SQL warehouse** — for profiling and the comment-apply write path.
- An **FMAPI Claude serving endpoint** (AI-Gateway-fronted), or point `model_endpoint` at your own.
- The deploying identity / app service principal granted: `SELECT` on tables to profile, `MODIFY` on
  tables you'll document (apply), and access to the Lakebase instance + secret scope.

### Deploy steps

```bash
git clone https://github.com/RohitDashora/geniefy-lite.git && cd geniefy-lite

# 1. App + migrations (dev FE-VM defaults target fevm-rd-classic; see databricks.yml)
./deploy.sh -t dev -p fe-vm-classic

# 1b. Customer workspace
./deploy.sh -t prod --host https://<your-workspace>.cloud.databricks.com -p <profile>

# 2. The hands-off schema-run Job (separate bundle, grant-safe)
./deploy_jobs.sh -t dev -p fe-vm-classic
```

`deploy.sh` is idempotent: preflight → build the frontend → ensure Lakebase → `bundle deploy` → apply
migrations → print the grants you need → print the App URL.

> **Grant-safe code redeploys.** Once the App's Lakebase/FMAPI resource bindings are wired (some are
> added in the UI), redeploy app code with **`./deploy.sh -t dev -p fe-vm-classic --code-only`** — this
> runs `bundle sync` + `databricks apps deploy` only, so it **never reconciles app resources** and your
> bindings + SP grants survive. A full `bundle deploy` is for first-time / fresh-customer setup. The
> hands-off Job is in its own bundle precisely so deploying it can't disturb the app's grants.

### Database setup

geniefy-lite applies its schema into a dedicated **`geniefy`** Postgres database (schema `geniefy`) on
Lakebase. **`deploy.sh` does this for you** via
[`migrations/run_migrations.py`](migrations/run_migrations.py) — idempotent, branch-aware (dev vs prod),
safe to re-run. The bundle also declares a **`geniefy_setup`** job that runs the same migration on a job
cluster. Manual driver + originally-validated commands: [`docs/verify/APPLY.md`](docs/verify/APPLY.md).

## Run the tests

The agent core + backend are fully **hermetic** — the 375-test suite runs with fakes, no Databricks infra:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=src .venv/bin/python -m pytest -q          # -> 375 passed, 1 skipped
```

Frontend build/typecheck:

```bash
cd app/frontend && npm install && npm run build        # tsc --noEmit && vite build
```

## How this project is organized

This project is driven by the [GOTM operating protocol](.gotm/PROTOCOL.md). Orchestration and produced
assets are deliberately separated:

| Path | Role |
|---|---|
| [`src/geniefy_core/`](src/geniefy_core) | hermetic agent core (state · profiler · context · reasoner · judge · gate · orchestrator) |
| [`src/geniefy_app/`](src/geniefy_app) | FastAPI backend · Lakebase store · providers · OBO apply · identity · config · hands-off driver |
| [`app/frontend/`](app/frontend) | Vite + React + TypeScript + Tailwind SPA |
| [`migrations/`](migrations) | idempotent Lakebase schema migrations + runner |
| [`jobs-bundle/`](jobs-bundle) | the standalone hands-off Job bundle |
| [`docs/`](docs) | [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) + [`design/`](docs/design) (HLD + per-component LLDs) |
| [`.gotm/`](.gotm) | GOTM orchestration — protocol, ledger, decisions, questions, audits (build provenance) |

GOTM files:

- [`.gotm/PROTOCOL.md`](.gotm/PROTOCOL.md) — operating protocol; read first every session
- [`.gotm/LEDGER.md`](.gotm/LEDGER.md) — canonical list of units (working state)
- [`.gotm/DECISIONS.md`](.gotm/DECISIONS.md) — append-only log of ratified decisions
- [`.gotm/audits/`](.gotm/audits) — independent audit outputs

> **Contributor note:** `.gotm/` (build orchestration) and `.claude/` (a dev-only immutability hook) are
> `sync.exclude`d in [`databricks.yml`](databricks.yml) — they stay in the repo as build provenance but
> never upload to the workspace. The Databricks App *resource* is named `geniefy-dev`; the product/repo is
> geniefy-lite.

## License

[Apache License 2.0](LICENSE) — open-source and customer-shippable (D13). Swappable to the Databricks
License if distributed via official Databricks demo channels (e.g., dbdemos).
