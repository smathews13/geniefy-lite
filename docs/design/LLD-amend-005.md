# LLD-amend-005 — Hands-off / schema-level batch generation + deferred review (R3, D51)

**Unit:** U101 · **Status:** design · **Supersedes:** nothing (net-new capability; *extends* LLD-app-backend U5/D18, LLD-agent-core U4/D17, LLD-amend-004 U90/D48, LLD-review-apply U6/D7).
**Inputs:** D51 · D18 (batch-via-Job) · D17 (stateless core, serializable `SessionState`, caller owns persistence) · D48 (SP reads / user OBO writes) · D7/D21 (governed, human-in-the-loop UC apply) · U45/U52 (`SessionStore`, `SessionService`) · U59 (`geniefy_setup` Job packaging).

---

## 1. Problem & intent

Today geniefy documents **one table at a time**, interactively: a human starts a run, watches it think, answers clarifying questions inline, reviews, and applies. The human directive (R3 #1) is a **hands-off mode**:

> Point the app at a **schema**; let it run generation for **all tables**; a human comes back **later** to approve or clarify. The hard part is **resuming the agent loop** to address open questions when the user returns to the app at a later time. "We thought of using a job here."

This amendment designs that capability **without** weakening two invariants:

- **No autonomous UC writes** (D7/D48/Q7): the batch only *generates + persists drafts*; every apply stays human-in-the-loop via OBO.
- **Stateless resume reuse** (D17/Q8): the batch must persist clarifying questions as `awaiting_input` so the **existing** answer→resume path (U6/U52 `execute_resume`) handles deferred clarification with no new agent-loop machinery.

## 2. Shape (end to end)

```
                    ┌─ app: POST /api/schema-runs {catalog, schema, filters}
   data steward ───►│   (OBO identity recorded as schema_run.created_by; SP runs the work)
                    └─ creates schema_runs row (status=enumerating) ──► triggers Job
                                                                          │
   Databricks Job  ◄──────────────────────────────────────────────────┘  (run as app SP, D48)
   "geniefy_schema_run" (params: schema_run_id, catalog, schema, filters)
     1. enumerate tables (information_schema, SP)         ─► schema_run.total_tables, status=running
     2. for each table (skipping already-documented):
          run Orchestrator in HANDS-OFF mode  ─► generate table+column drafts (NO apply)
          gate → persist questions as awaiting_input (NO pause; continue)
          SessionStore.create/save session  (session.schema_run_id = this run)
          update schema_run rollup counters
     3. status=completed (or completed_with_errors)
                                                                          │
   data steward ───► app later: GET /api/schema-runs, open a run ────────┘
     • per-table sessions grouped under the run (ready · needs-input · applied)
     • open a needs-input session → answer (D50 suggested answers) → execute_resume (D17, UNCHANGED)
     • review → apply  (OBO, human-in-the-loop, D48/D7 — UNCHANGED)
```

The only genuinely new agent behavior is a **non-pausing pass**; everything after "open a session" reuses the live single-table flow.

## 3. Core: hands-off run mode (extends U4 Orchestrator, D17)

A new **`RunMode.HANDS_OFF`** (alongside the existing interactive/batch modes) on the Orchestrator's run, selected via `RunConfig.mode`. Semantics:

| Concern | Interactive (today) | **Hands-off (new)** |
|---|---|---|
| Low-confidence draft | Gate → **pause**, surface question, await human | Gate → **persist `awaiting_input` question, do NOT pause**; keep the best draft; continue |
| Suggested answers (D50) | Computed when the run pauses | **Computed + persisted** with each question (so the later UI is pre-filled) |
| UC apply | Human clicks apply (OBO) | **Never** — run ends at persisted drafts |
| Terminal state | `awaiting_input` or `ready_for_review` | `ready_for_review` (all confident) **or** `awaiting_input` (≥1 open question), persisted |

Key point: hands-off **does not invent a resume loop**. It produces exactly the same persisted `SessionState` an interactive run produces *at the moment it would pause* — questions attached, `suggested_answer` filled (D50) — then stops cleanly. When the human later answers, `SessionService.execute_resume` (U52) feeds the answers back into the **same** Orchestrator loop (D17 rehydrate from `session_state` jsonb), which re-reasons the affected drafts. **Zero new resume code.**

Crash-safety (D19/D20): the Job checkpoints **per table** — each table is its own persisted session, so a Job failure costs at most the in-flight table; on Job retry, already-persisted tables are skipped (idempotent by `schema_run_id + table_ref`).

## 4. Data model (extends LLD-data-layer / U2, migration `003`)

A new **`schema_runs`** parent table (Q9) + a FK on `sessions`:

```sql
-- migration 003_schema_runs.sql
CREATE TABLE schema_runs (
  id              TEXT PRIMARY KEY,              -- uuid
  catalog         TEXT NOT NULL,
  schema          TEXT NOT NULL,
  status          TEXT NOT NULL,                 -- enumerating|running|completed|completed_with_errors|failed
  filters         JSONB NOT NULL DEFAULT '{}',   -- {skip_documented:true, name_like:"fact_%", max_tables:null}
  total_tables    INT,                           -- null until enumerated
  counts          JSONB NOT NULL DEFAULT '{}',   -- {ready, needs_input, applied, error, skipped}
  job_run_id      BIGINT,                        -- Databricks Job run id (for status/cancel)
  created_by      TEXT NOT NULL,                 -- OBO identity (X-Forwarded-Email, D48) of the trigger
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE sessions ADD COLUMN schema_run_id TEXT NULL REFERENCES schema_runs(id);
CREATE INDEX sessions_schema_run_idx ON sessions(schema_run_id);
```

`counts` is a derived rollup the Job (and apply) update; the app can also recompute from `sessions WHERE schema_run_id=…` as a consistency check. `created_by` is the **human** who triggered (attribution, D48), even though the Job body runs as the **SP**.

## 5. App backend (extends U52 `SessionService`, U43 routes)

New routes (all SP-authenticated except where OBO already applies):

- `POST /api/schema-runs` `{catalog, schema, filters?}` → validates the schema exists (SP, information_schema), inserts a `schema_runs` row (`status=enumerating`, `created_by` from identity D48), **triggers the Job** with `{schema_run_id, catalog, schema, filters}`, returns the run id. *(Hard-to-reverse / outward action → this is the documented operator-initiated trigger; the Job is the existing `geniefy_setup`-style packaging, U59.)*
- `GET /api/schema-runs` → list (most-recent first) with rollup counts.
- `GET /api/schema-runs/{id}` → the run + its sessions grouped by status (ready · needs-input · applied · error).
- `POST /api/schema-runs/{id}/cancel` → cancel the Job run (best-effort; persisted-so-far sessions remain).

**Enumeration query** (SP): `SELECT table_name FROM <catalog>.information_schema.tables WHERE table_schema = :schema AND table_type='MANAGED'|'EXTERNAL'`; "already-documented" = `comment IS NOT NULL AND comment <> ''` on the table (skip when `filters.skip_documented`, the default). `filters.name_like` → SQL `LIKE`; `filters.max_tables` → cap. **Reads are the SP** (D48) — no per-table OBO during the batch (the human isn't in the loop yet).

**Identity boundary (unchanged, D48):** the Job runs as the **app SP** (enumerate + profile + reason + persist). The **only** UC writer remains the human via OBO at `/api/sessions/{id}/apply` — which the steward triggers later, per table, after review. Hands-off never holds or uses a user token.

## 6. The Job (extends D18 / U59 packaging)

A new Job `geniefy_schema_run` (parameterized), packaged like `geniefy_setup`. Its entrypoint:

```python
def run_schema(schema_run_id, catalog, schema, filters):
    store = build_store()                      # SP Lakebase (D49 per-call conn)
    svc   = build_batch_service()              # SP providers (profiler, FMAPI), HANDS_OFF mode
    tables = enumerate_tables(catalog, schema, filters)   # SP information_schema
    store.update_schema_run(schema_run_id, status="running", total_tables=len(tables))
    for t in tables:
        if store.session_exists(schema_run_id, t):   # idempotent retry (D20)
            continue
        try:
            state = svc.run_hands_off(table_ref=f"{catalog}.{schema}.{t}",
                                      schema_run_id=schema_run_id)   # generate + persist, NO apply
            store.bump_counts(schema_run_id, terminal_state_of(state))
        except Exception as e:
            store.record_table_error(schema_run_id, t, e)   # one table's failure ≠ run failure
    store.finalize_schema_run(schema_run_id)   # completed | completed_with_errors
```

Concurrency: **sequential v1** (simplest, warehouse-friendly, deterministic ledgering); a `max_workers` knob is a future optimization, out of scope here. Long-running schemas are fine — the Job is durable and the app polls `schema_run.status`.

## 7. Frontend (extends U87/U99)

- A **"Hands-off" entry** on the start screen: pick `catalog.schema` + optional filters → "Document the whole schema" → `POST /api/schema-runs`.
- A **Schema-runs view** (new tab or a section of History): each run shows `catalog.schema`, a progress bar (`enumerated/total`), and rollup chips — **N ready · N need input · N applied**. Polls while `status ∈ {enumerating, running}`.
- Clicking a run → the run detail: the per-table sessions grouped by status; each row opens the **existing** session screens (review / questions / apply). **Needs-input** tables route to the existing `QuestionsPanel` (D50 pre-filled) → answer → `execute_resume`. No new review/apply UI.

## 8. What this explicitly does NOT do (scope guard)

- No autonomous apply / no scheduled re-runs (every UC write stays human + OBO, Q7).
- No new resume engine — deferred clarification rides the D17/U52 path unchanged (Q8).
- No cross-table semantic linking beyond what D52's library reuse already provides.
- No parallel table fan-out in v1 (sequential; `max_workers` deferred).

## 9. Risks / open follow-ons

- **R1 — long schemas vs Job timeout:** mitigated by per-table checkpointing + idempotent retry; if a single Job run can't finish, a retry resumes (skips done tables). A future chunked/parallel driver is the escalation.
- **R2 — `created_by` vs SP actor in the audit log:** the apply audit actor remains the **human** (OBO, D48); generation actions in the batch are attributed to the **SP** with `schema_run.created_by` recording who *initiated* — the audit trail must not imply the SP "approved" anything (it only generated). Build units must keep `audit_log.actor` semantics intact.
- **R3 — enumeration permission:** the SP needs `USE SCHEMA` + `SELECT` on the schema's `information_schema` and tables (already required for profiling, D48); the operator runbook (`docs/DEPLOY.md`) must note schema-level grants for hands-off.
- **R4 — trigger is outward/expensive:** `POST /api/schema-runs` kicks a Job that reads + calls the model for every table; the UI must confirm scope (table count preview) before launch.

## 10. Build-unit sketch (registered as R3 units after audit PASS)

1. **Core:** `RunMode.HANDS_OFF` + `Orchestrator` non-pausing path (gate persists `awaiting_input` instead of pausing; D50 suggested answers persisted) + tests.
2. **Data:** migration `003_schema_runs.sql` + `SessionStore` (`create/update/finalize_schema_run`, `bump_counts`, `session_exists`, `list/get` runs, `sessions.schema_run_id`) + tests.
3. **Backend:** `SessionService.run_hands_off` + enumeration + the 4 `/api/schema-runs` routes + Job trigger wiring + tests.
4. **Job:** `geniefy_schema_run` entrypoint + packaging (extends U59) + DAB job def.
5. **Frontend:** hands-off start affordance + schema-runs list/detail views + polling + wiring to existing session screens.
6. **Verify:** local devloop (seeded fake) + live schema run on a small real schema (grant-safe `--code-only` deploy).

Each is atomic (module+test = one unit), independently audited (D24/D41), grant-safe deploys (D48/U78).
