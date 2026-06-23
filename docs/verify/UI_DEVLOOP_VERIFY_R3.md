# R3 UI devloop — in-browser visual verification (U127)

**Date:** 2026-06-14 · Closes the recurring `UNVERIFIED` (in-browser visual) from the U106 / U112 /
U114 audits. The built SPA was served via the **real** FastAPI routes against an R3-aware seeded
fake store (`scripts/local_ui_preview.py`, refreshed from U92) at http://127.0.0.1:8770 and driven
in Chrome (web-devloop-tester + chrome-devtools). No Databricks infra.

## Verified (all PASS)
1. **Steward-first hero + column pills (U104/U106/U114).** The table card renders as a prominent
   indigo-gradient **hero**: eyebrow "TABLE · DATA STEWARD VIEW", large mono FQN, a **trust** chip
   ("91% High confidence"), **steward-fact chips** (Owner · Freshness · Grain · Keys · Sensitivity,
   icon+label+value), and **table tag pills** (fact/orders/tpch/revenue). Columns read as clearly
   **secondary** (left-rail nest, "COLUMNS (3)") with **data-type pills** (bigint/date/string) +
   **tag pills**. The `o_orderstatus` needs-input question shows a **pre-filled ✨ suggested answer**.
2. **Library lifecycle (U103/U105/U106).** Per-entry **status badges** — `applied` (green),
   `approved` (blue); **Sunset** action per row; the **"Show sunset"** toggle reveals the `sunset`
   entry (greyed, strikethrough) with a **Revive** action.
3. **Hands-off Schema tab (U110/U112).** The "Document a whole schema (hands-off)" start form
   (catalog/schema + skip-already-documented + Document button) and a **schema-run card**
   (`rd_classic_catalog.gaming_data`, status `completed with errors`, **rollup chips** 2 ready · 1
   need input · 1 error). Clicking the run opens a **detail** view with the hero run header + a
   "TABLES (3)" list (gold_arpdau / bronze_events / silver_sessions with statuses), rows clickable
   into the existing review flow.
4. **Console + polish.** No functional console errors (the known favicon 404 + the Starlette httpx
   deprecation are pre-existing/benign). Verdict from the tester: *"delightful and visually stunning
   from a data-steward perspective"* — clean spacing/alignment, strong color hierarchy.

Screenshots: `/tmp/r3_hero_columns.png`, `/tmp/r3_library.png`, `/tmp/r3_schema.png`.

## Finding addressed
- **a11y (LOW):** the tester flagged 3 form fields without `id`/`name` (the SchemaRunView
  catalog/schema inputs + the skip-documented checkbox). **Fixed** — added `id`/`name` to all three;
  SPA rebuilt clean. (The older app fields had the same accepted-LOW class, U92; the new R3 fields
  are now compliant.)

## Result
The R3 frontend is **visually verified in a browser** and polished. This closes the in-browser
`UNVERIFIED` from U106/U112/U114; the live backend was already verified by U107 (006) + U113 (005).
