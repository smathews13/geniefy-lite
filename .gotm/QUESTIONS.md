# Open questions — geniefy-v3

> Questions whose resolution requires the human. When answered, the resolution moves into `DECISIONS.md` (with a new D-entry) and any blocked units become unblocked.

---

## Q1 — Ratify the mission statement

**Asked:** 2026-06-11
**Blocking:** non-blocking (design proceeds; mission is the frame everything hangs on)
**Context.** Mission was drafted by the agent from the kickoff discussion, not yet explicitly ratified by the human. Mission is a Mission-layer decision per the ratification ladder.

**Need from human:** Confirm or edit — *"An agent, delivered as a Databricks App, that turns any Unity Catalog table into an AI/Genie-ready asset by profiling its data and usage context and generating reviewable, template-conformant table and column comments, with a governed path to apply them to Unity Catalog."*
**Status:** answered (see D10) — confirmed verbatim 2026-06-11.

---

## Q2 — Who is the audience / who operates the App?

**Asked:** 2026-06-11
**Blocking:** non-blocking for HLD/data-layer; shapes app auth model and UX (units U5/U6)
**Context.** Auth model, on-behalf-of vs. service-principal access to UC, and review/approval UX differ depending on whether the operators are just you/SAs internally, broader Databricks FE, or customers running it in their own workspace.

**Need from human:** Primary audience for v1 — internal (you/SAs), Databricks-FE-wide, or customer-shippable?
**Status:** answered (see D11) — shippable demo for internal + external customers, hardened by them for prod.

---

## Q3 — Target workspace/catalog and a golden set for evaluation

**Asked:** 2026-06-11
**Blocking:** blocks the eval harness and the first real end-to-end test (not the designs)
**Context.** D8's judge needs a reference set to score against; the interactive loop needs a real table to develop on. We need a workspace/catalog to point at and ~5–10 already-well-documented tables as the golden set.

**Need from human:** Which workspace + catalog.schema to develop against, and candidate well-documented tables to use as the golden set.
**Status:** answered (see D12) — deployable via DAB from GitHub; developed on FE-VM with Databricks sample datasets; dedicated golden eval set deferred with the eval harness (D2/D8).

---

## Q4 — Delivery anchor and sharing/license model

**Asked:** 2026-06-11
**Blocking:** non-blocking
**Context.** Whether there is a date/event/milestone for v1, and whether this stays an internal FE tool, gets open-sourced, or is packaged to ship to customers — affects packaging and licensing choices.

**Need from human:** Any delivery anchor (date/event)? Intended sharing model (internal / open-source / customer-shippable)?
**Status:** answered (see D13) — open-source / customer-shippable; no fixed delivery anchor.

---

<!--
Conventions:
- Open the question with a one-line title.
- Name the units that are blocked on the answer.
- When answered, mark status and reference the D# in DECISIONS.md that resolved it.
- Resolved questions stay in this file for audit-trail purposes; do not delete.
- New questions appended below the marker.
-->

## Q5 — Config surface for MCP providers + model (FMAPI) selection

**Asked:** 2026-06-12
**Blocking:** blocks closing NFR-C end-to-end (and the "choose the model" capability); not blocking the rest.
**Context.** Human review caught that the config/extensibility surface is incomplete vs. its own design. Verified in code: `build_service` ignores `config.mcp_providers` (only built-in lineage/query-history are wired); there is **no concrete MCP session factory**; the API has only a read-only `GET /api/config`; the model endpoint is settable only via `app.yaml` (deploy-time). U24 §3 had said MCP providers would be settable "via `GENIEFY_MCP_PROVIDERS` (bootstrap) **or the U5 config endpoint (runtime)**" — the runtime path + the actual MCP wiring were never built. D33 made `app.yaml` "the single config surface" (deploy-time).

**Need from human:** which config surface for v1 — (a) in-app **settings UI + backend CRUD endpoints** (persisted in Lakebase; add/remove MCP servers + pick the serving endpoint live, no redeploy), (b) **deploy-time via `app.yaml`** only (wire the existing env JSON path + a documented model var; changes need redeploy), or (c) **both** (env as bootstrap, UI for runtime — U24 §3's original intent). NB: the **MCP session factory + gatherer wiring** must be built regardless of the answer (it's the actual connect-to-server code).
**Status:** answered (see D39) — config surface = **`app.yaml`** (deploy-time): MCP providers via `GENIEFY_MCP_PROVIDERS`, model via `GENIEFY_MODEL_ENDPOINT`, wired into the gatherer/build at startup (U63). Runtime config UI deferred.

<!-- Append new questions below this line. -->
