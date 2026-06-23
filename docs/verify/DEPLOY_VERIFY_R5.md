# DEPLOY_VERIFY — R5 (overall confidence + bulk high-confidence approve)

**Unit:** U158 · **Feature:** LLD-amend-007 / D59 · **Date:** 2026-06-23 · **Workspace:** `fe-vm-classic`

Verifies the R5 build (U155 backend · U156 ReviewView · U157 SchemaRunView) deployed + behaving.

## 1. Deploy (grant-safe)

- `./deploy.sh -t dev -p fe-vm-classic --code-only` — vite rebuilt the SPA (R5 UI), staged core+backend, **app deploy SUCCEEDED, "App started successfully"**; resource bindings + Lakebase grants untouched. (Step-5 local migrate skipped — system `psycopg2` absent; prod already migrated.)
- `./deploy_jobs.sh -t dev -p fe-vm-classic` — `geniefy_schema_run` redeployed (gets the R5 backend + the SQL-polling fix).
- Live app reachable: `GET /api/sessions` → **200**, `GET /api/config` → `{"keep_threshold":0.75}`, the new `POST /api/sessions/{id}/approve-high-confidence` route is wired (a bogus id returns 500 — the same pre-existing non-UUID→DB-error path as `GET /api/sessions/{id}` and `/regenerate`, NOT an R5 defect; a valid-but-missing id 404s, per the hermetic test).

## 2. Functional verification — real FastAPI routes + components (local devloop, seeded)

The live app is OAuth-walled (can't drive headless), so behavior was verified against the local
devloop (`scripts/local_ui_preview.py`) — the **real** `SessionService` routes + the real React build —
the established U92/U127 pattern. Exercised via the actual HTTP routes (TestClient):

| Check | Result |
|---|---|
| `keep_threshold` exposed (`/api/config`) | **0.75** ✓ |
| seeded session drafts | table 0.91·draft, o_orderkey 0.85·draft, o_orderdate 0.94·draft, o_orderstatus 0.60·**needs_input** ✓ |
| `confidence_summary` (U157, Schema-tab data) | `overall=0.853` (=0.5·0.91+0.5·mean(0.85,0.94,0.60)), `review_ready=3`, `needs_input=1`, `low=0`, **`approvable=3`**, `weakest=o_orderstatus@0.60` ✓ |
| null-draft sessions (s2/s3) | `confidence_summary=None` — graceful ✓ |
| `POST /approve-high-confidence` | `{approved:["__table__","o_orderkey","o_orderdate"], count:3}` — only the unflagged ≥0.75 ✓ |
| after approve — flagged left | `o_orderstatus` **stays `needs_input`** (no blanket approve, D7) ✓ |
| after approve — not applied | the 3 approved drafts have **`apply_status=not_applied`** (approve ≠ apply, nothing written to UC — D48/D7) ✓ |

These confirm the data + invariants the R5 UI renders: the weighted rollup, the Gate-bucket breakdown,
the weakest caveat, the exact "Approve N" count, and that bulk-approve neither blanket-approves nor applies.

## 3. UI rendering coverage

The `ReviewView` (Confidence meter + breakdown + weakest + "Approve N" button) and `SchemaRunView`
(per-table confidence + per-table "Approve N") were **build-verified** (`tsc --noEmit` + `vite build`
clean, 1047 modules), **lint-clean** (eslint, 0 problems), and **independently audited** — U156
PASS-FINDINGS (honesty pairing + distinct-axes + rollup + governance verified; JSX balanced, no nested
buttons) and U157 PASS-FINDINGS (no UC write on approve, correct session targeted, no nested buttons,
type matches the server).

## 4. Not done this session (honest gap)

**In-browser pixel verification via Chrome DevTools did not run** — the chrome-devtools MCP server
disconnected mid-verify (the preview process was killed). The R5 UI rendering is covered by the build +
lint + independent audits + the functional route verification above, but the pixel-level in-browser pass
(the literal rendered meter/breakdown/button) is deferred. Re-run with chrome-devtools once the MCP is
reconnected, or eyeball the deployed app directly.
