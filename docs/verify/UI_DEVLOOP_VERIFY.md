# geniefy-v3 — local UI devloop verification (U92)

Closes the "did we test the UI locally?" gap before the live U89. The built SPA was served
through the **real** FastAPI routes (`geniefy_app.api.create_app`) against a **seeded
in-memory `SessionService`** — no Databricks infra (no Lakebase / warehouse / FMAPI) — and
exercised in a real browser via Chrome DevTools. This validates the R2 frontend units
(U86 profile viz · U87 history/library · U88 regenerate + delight) render and wire correctly
against realistic data, which the hermetic suite + `tsc`/`vite build` gates cannot show.

## Harness
`scripts/local_ui_preview.py` (U92 output): builds `create_app(SessionService(fake store + no-op
orchestrator), static_dir=app/static)`. The fake store implements the U84 contract
(`load`/`list_sessions`/`list_library`) seeded with a `ready_for_review` session for
`samples.tpch.orders` — table draft (conf 0.91) + 3 column drafts + a per-column profile (E4) +
1 open question + a library entry.

Run: `PYTHONPATH=src /tmp/geniefy_pgvenv/bin/python scripts/local_ui_preview.py 8770` → http://127.0.0.1:8770

## What was verified (Chrome DevTools, 2026-06-13)

**Shell + nav (U87):** SPA boots; header + 3-tab nav (Document / History / Library) render; the
input gate disables "Document" until a valid `catalog.schema.table`.

**History (U87/E6):** lists the seeded run — `samples.tpch.orders` · 3 cols · 0 applied ·
`app-service-principal` · `6/13/2026, 1:05 AM` · **ready for review** badge · **Open**. Clicking
**Open** loads the session into the review flow (generate-now-approve-later, E7).

**ReviewView (U60 + U86 + U88):** readiness meter (0% AI-ready), **Regenerate all** + **Apply
approved (0)** (disabled — none approved), the **questions panel** ("needs your input (1)" → the
`o_orderstatus` decode question + answer box). Each draft card: confidence chip + status badge +
the comment + Approve/Reject/Edit/**Regenerate**/Why?. The table comment shows the full rich text.

**Profile viz — ProfileStrip (U86/E4):** rendered per column with correct data:
- `o_orderkey` — nulls 0% · 7,500,000 distinct · 100% unique · `1 … 6000000`
- `o_orderdate` — nulls 0% · 2,406 distinct · 0% unique · `1992-01-01 … 1998-08-02`
- `o_orderstatus` — nulls 0% · 3 distinct · **enum** badge · **top**: `O ×3793296`, `F ×3793218`, `P ×13486`

**Regenerate (U88/E3):** clicking a per-draft **Regenerate** fires with no console error;
server confirms the routes: `POST /regenerate {targets:["__table__"]}` → **202**,
`{all:true}` → **202**, `POST /answers` → **202**.

**Library (U87/E6):** lists the entry — `o_orderdate` · column · used 4× · "Date the order was
placed." · `temporal` tag.

**Bundle:** `index-*.js` 200, `index-*.css` 200; `JudgeRadar` stays a separate lazy chunk
(not loaded until "Why?" is opened). Screenshot: `docs/assets/u92-review-profileviz.png`.

## Console / findings
- **favicon.ico → 404 (benign):** the browser's default favicon request; the SPA never
  references one and all real chunks return 200. Cosmetic only — *LOW, accepted* (could ship a
  favicon later).
- **form-field a11y warning:** the QuestionsPanel answer `textarea` has no `id`/`name`. Minor
  a11y nit — *LOW, accepted* (no functional impact).
- No JavaScript errors; no server tracebacks.

## Scope note
The harness wires **no `applier`**, so `POST /apply` returns the 501 "not yet wired" stub — the
real OBO apply path is covered by U85's hermetic tests and is re-verified live in **U89** (which
needs the operator to redeploy + add the `sql` user-authorization scope). The agent run loop
(profiling→reason→judge) is the no-op fake here; its real behavior is covered by U41/U42 + the
U76 live e2e. This unit verifies the **UI render + wiring**, not the live agent/UC write.
