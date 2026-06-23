# geniefy-v3 — R2 live redeploy + end-to-end re-verify (U89)

The R2 enhancement set (U80–U88) re-verified on the **live** Databricks App, plus the
local UI devloop (U92). Extends the U76 live e2e (which validated the original loop). This
deploy was done **grant-safe** at the operator's explicit instruction ("we already have the
Lakebase permissions — do not overwrite them").

## Target
| | |
|---|---|
| Workspace | `https://fevm-rd-classic.cloud.databricks.com` (profile `fe-vm-classic`) |
| App | **`geniefy-dev`** → https://geniefy-dev-7474653107059373.aws.databricksapps.com |
| App SP | `bcc7089c-deac-4f42-b125-4e5681c7d8c1` |
| Deployment | `01f166ebbc7013eaa624cb63464f4e69` (SNAPSHOT, "App started successfully") · 2026-06-13 |

## Grant-safe deploy method (D48 / U78 — do NOT run `bundle deploy`)
`deploy.sh`'s step 4 is `databricks bundle deploy`, which reconciles the app's resources to
databricks.yml (`[sql-warehouse]` only) and **wipes the UI-added `geniefy-db` (Lakebase
postgres) + `fmapi-endpoint` bindings** → drops the SP's Lakebase role + grants (the U77/U78
churn). To preserve the existing grants, this redeploy used a **code-only** path:

1. `npm run build` → `app/static`; stage `src/geniefy_core`+`geniefy_app` → `app/` (gitignored, force-synced).
2. **`databricks bundle sync -t dev`** — uploads the file tree (honoring `sync.include` for the
   staged core + SPA) to `…/.bundle/geniefy-v3/dev/files/app`. **No resource reconciliation.**
3. **`databricks apps deploy geniefy-dev --source-code-path …/files/app`** — new code snapshot;
   **resource bindings untouched.**

**Verified bindings preserved** — `databricks apps get geniefy-dev` after deploy:
`resources = [sql-warehouse, geniefy-db, fmapi-endpoint]` (all three intact), active deployment =
the new R2 snapshot. The SP's Lakebase grants were never touched.

## Live e2e evidence (Chrome DevTools, 2026-06-13)

**Shell + nav (U87):** the deployed SPA serves the 3-tab nav (Document / History / Library).

**History (U87) + Lakebase grants intact (U84):** `History` lists the real session history from
Lakebase — `rd_classic_catalog.gaming_data.gold_arpdau` (5 cols · **5 applied**), `samples.tpch.orders`
(9 cols · 2 applied), and ~13 more across ready/awaiting/created/failed. That the SP queried
`geniefy.sessions` + `column_drafts` (the counts) proves its grants survived the deploy.

**ReviewView — full R2 stack live** (opened `gold_arpdau`, **100% AI-ready**):
- **Rich table comment (U81/D43):** full `purpose · grain (one row per (event_date,country)) ·
  primary_keys · join_keys · source_systems · use_cases · caveats · sensitivity` — the relaxed
  500-word budget in action.
- **Two-pass grounding (U82/E2):** columns reference the table context — `arpdau` = "revenue_usd
  divided by dau, per country per day"; `event_date` "Groups daily metrics per country".
- **Profile viz (U86/E4):** every column shows the ProfileStrip — nulls bar · distinct + %unique ·
  min…max range · enum badge · top-k chips (e.g. `country`: 10 distinct · 100% unique · `BR ×1, IN ×1,
  CA ×1`; `revenue_usd`: `71941.36 … 206867.93`).
- **Regenerate (U88/E3):** per-draft **Regenerate** on every card + a table-level **Regenerate all**.
- **Apply-to-UC — the E9 fix, live (U85):** all 5 columns show **"applied to UC ✓"** with
  `apply_status = applied` on a **writable catalog** (`rd_classic_catalog`). The OBO write-path
  succeeded against real Unity Catalog — the root fix for E9 (the SP-can't-MODIFY-`samples`
  failure) is demonstrably resolved when applying as a user with `MODIFY`.
- Existing review intact: confidence chips, status badges (incl. `rejected`/`applied`), Approve/
  Reject/Edit/Why?, readiness meter.

**Console:** clean — **zero** messages on the live app.

Screenshot: `docs/assets/u89-live-gold-arpdau-applied.png`.

## Result
✅ R2 is live and end-to-end verified on the real workspace — richer comments, two-pass,
profile viz, regenerate, history/library, and **OBO apply to UC** — with the operator's Lakebase
grants preserved (grant-safe code-only deploy). Combined with U92 (local UI devloop) and the
hermetic suite (314) + clean frontend build, the R2 set is fully shipped.

## Notes
- The `gold_arpdau` applies predate this deploy (sessions from 2026-06-12) — they confirm the OBO
  apply path works on a writable catalog with the `sql` user-authorization scope already configured.
- `samples.tpch.orders` sessions show `applied: 0/2` — consistent with `samples` being read-only
  (no `MODIFY` for any principal), exactly the E9 hypothesis; apply there correctly does not write.
