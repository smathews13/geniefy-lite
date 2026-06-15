# geniefy-v3 — Design Amendment 001 (resolves Audit-001 findings)

**Status:** draft (U10) · **Last updated:** 2026-06-11
**Inputs:** [`.gotm/audits/audit-001.md`](../../.gotm/audits/audit-001.md) (U7 verdict: PASS-FINDINGS) · amends **U2, U4, U5, U6, U8, U9**
**Decisions touched:** D6 (Lakebase schema), D7/D21 (apply), D17 (SessionState), D5 (context providers), D13 (license), D18 (watchdog)

> **How to read this.** The audited design docs (U2/U4/U5/U6/U8/U9) are `done` and **frozen** — this amendment does not edit them. Where this document conflicts with them, **this amendment is authoritative** and implementation follows it. It resolves the 6 findings in Audit-001 (2 major, 4 minor). Each section names the doc/section it supersedes.

---

## F1 (major) — `running` session status: reconcile watchdog with the enum

**Finding.** U5 §8 / D18 describe a watchdog that finds stale `running` sessions, but `session_status` (U2 §5) has no `running` value (in-flight is modeled as phase-specific `profiling`/`gathering_context`/`reasoning`/`applying`).

**Resolution (supersedes U5 §8 wording; no enum change).** "Running" is not a status — it is the **set of in-flight statuses**: `IN_FLIGHT = {profiling, gathering_context, reasoning, applying}`. The watchdog marks a session `failed` when `status ∈ IN_FLIGHT AND now() - updated_at > stale_timeout`. This keeps the U2 enum unchanged and makes the watchdog precise. (`updated_at` is already on `sessions`, U2 §4.3.)

## F2 (major) — per-item states have no home in `draft_status`

**Finding.** U4 gating and U6 apply produce `low_confidence`, `error`, `conflict`, `unsupported`, `failed`, but `draft_status` (U2 §5) is only `draft, needs_input, reviewed, edited, approved, applied, rejected`.

**Resolution (supersedes U2 §4.5/§4.6/§5 and reconciles U4 §3.6 + U6 §4).** Separate the **review lifecycle** from the **apply outcome** — they are different axes:

- **`draft_status`** (review lifecycle) gains two values → `draft, needs_input, low_confidence, error, reviewed, edited, approved, applied, rejected`.
  - `low_confidence` — gated low in **hands-free** mode (flagged, not asked) (U4 §3.6).
  - `error` — generation failed for this item (U4 §8).
- **New per-item field `apply_status`** on `table_drafts` + `column_drafts` (a distinct axis from `draft_status`), enum `apply_status`: `not_applied (default) | applied | conflict | failed | unsupported | skipped_noop`.
  - `conflict` — live UC comment changed since profiling (D21); `failed` — DDL/permission error; `unsupported` — e.g. some view columns; `skipped_noop` — proposed == live.
  - `draft_status='applied'` is set only when `apply_status='applied'` (or `skipped_noop`).

This formalizes what U6 §4 gestured at as a separate "apply-status result" and gives every produced state a column.

## F3 (minor) — orchestrator phase ≠ `session_status` 1:1

**Finding.** U4 says drafts/phases "map 1:1 to `sessions.status`"; U9 §4 labels `judging`/`gating` as a "(U4/U5 status)" that isn't in the enum.

**Resolution (supersedes U4 §4 "maps 1:1" and U9 §4 labels).** The orchestrator has **internal phases** — `profiling · gathering_context · reasoning · judging · gating` — which are **finer** than the persisted `session_status`. The mapping is **many-to-one**: `judging` and `gating` roll up under the persisted status `reasoning`. The "watch it think" narration (U9 §4) is driven by the **finer phase progress events** the orchestrator emits, not by the coarser `session_status` enum. Persisted `session_status` remains the U2 enum unchanged.

## F4 (minor) — profiling-provider selection sourced from the wrong table

**Finding.** Provider selection appeared to read the context-only `context_providers` table (D5), conflating profiling with context.

**Resolution (supersedes U3 §3 / U4 §3.2 wording).** Profiling-provider selection lives in **`session.config` / `RunConfig`** (U4 §7, D17) — `config.profiling_provider` — **not** `context_providers` (which is exclusively for context retrieval, D5). The two registries are independent: one chooses how to profile, the other which context sources to consult.

## F5 (minor) — `session_state` blob vs normalized rehydration

**Finding.** U5 §3 left "denormalized `session_state` blob vs pure-normalized rehydration" as an open performance choice.

**Resolution (supersedes U5 §3 open item; resolves to BOTH).** Keep the **normalized rows** (`table_drafts`/`column_drafts`/`session_messages`) as the source of truth for UI, history, and queries, **and** add a `session_state jsonb` column on `sessions` as the **rehydration fast-path** snapshot of the U4 `SessionState` (D17). On each write-back the backend updates both in one transaction; on resume it loads the blob (fast) and treats the normalized rows as authoritative if they ever diverge. Closes the open choice.

## F6 (minor) — missing `LICENSE` despite the open-source mandate

**Finding.** D13 mandates open-source (Apache 2.0), but U8's ship list omitted a `LICENSE`.

**Resolution (supersedes U8 §2 layout).** The GitHub repo includes a top-level **`LICENSE`** (Apache 2.0, D13) and a license reference in `README.md`. It lives in the repo (the OSS distribution); like `.gotm/`, it need not be uploaded to the workspace deploy artifact, but it is part of what ships on GitHub.

---

## Consolidated schema delta (for the eventual U2 migration)

Implementation note — the migration that builds the Lakebase schema must reflect:

1. `draft_status` enum += `low_confidence`, `error`.
2. New enum `apply_status` (`not_applied | applied | conflict | failed | unsupported | skipped_noop`) + an `apply_status` column (default `not_applied`) on `table_drafts` and `column_drafts`.
3. New `session_state jsonb` column on `sessions` (rehydration snapshot; F5).
4. `session_status` enum **unchanged** (F1 handled in watchdog logic, not the enum).
5. Config: `RunConfig.profiling_provider` is the source for profiling-provider selection (F4) — no schema change, a config/field clarification.

## What stays unchanged

The architecture, component boundaries, contracts, flows, governance, and all other decisions stand. This amendment is confined to the state-vocabulary seam and the four minor items above. No HIGH/blocker findings were raised; the design is sound.

## Gate note

Per *Audit gates*, this amendment (a design change) should itself get an **independent audit** before any code consumes the amended schema — re-verifying that F1–F6 are closed and no new inconsistency was introduced. U10's `Audit` is `pending` until then.
