# geniefy-v3 — Design Amendment 002 (closes U10-audit findings)

**Status:** draft (U11) · **Last updated:** 2026-06-11
**Inputs:** [`.gotm/audits/U10.md`](../../.gotm/audits/U10.md) (independent audit of U10: PASS-FINDINGS) · amends **U6** and **U10**
**Decisions touched:** D7/D21 (apply), D17 (RunConfig)

> Closes the three findings (NF1 medium, NF2/NF3 low) the independent audit raised against Amendment 001 (U10). Same rule as U10: the frozen docs are not edited; where this conflicts, this amendment is authoritative. These are pure reconciliations of the auditor's own recommendations — no new design.

---

## NF1 (medium) — reconcile `apply_status` with U6's `pending`/error names

**Finding.** Amendment 001 introduced `apply_status` with default `not_applied`, but U6 §6 (the `apply-status` endpoint) and U6 §8 (error table) still speak in terms of a `pending` per-item state — two names for one state, unreconciled.

**Resolution (supersedes U6 §6 + §8 vocabulary, and extends U10 F2).** The per-item apply axis is **exactly** the `apply_status` enum from U10 F2: `not_applied | applied | conflict | failed | unsupported | skipped_noop`.
- U6 §6's `GET /apply-status` returns each item's **`apply_status`** value (the word "pending" there maps to **`not_applied`** — an approved item not yet applied).
- U6 §8's error rows are **`apply_status='failed'`** (DDL/permission error) — not a separate vocabulary.
- There is one source of truth for "what happened on apply": the `apply_status` column. `not_applied` is the default/initial value; nothing else is called "pending" on the apply axis. (The review axis keeps its own `draft_status`; the two never share a value — confirmed by the auditor.)

## NF2 (low) — which `apply_status` a no-op records

**Finding.** U6 §8 says a proposed-equals-live item is "marked `applied`"; U10 added `skipped_noop`; which one wins was unstated.

**Resolution (supersedes U6 §8).** A no-op (proposed comment equals the live UC comment) records **`apply_status='skipped_noop'`** — *not* `applied`. Rationale: `applied` means "we wrote it"; a no-op wrote nothing, and the distinction matters for the audit trail and the readiness meter. For readiness/UX purposes (U9), both `applied` and `skipped_noop` count as "resolved/AI-ready" (the column is correct either way), so the glow-up still completes.

## NF3 (low) — `RunConfig.profiling_provider` is introduced, not pre-existing

**Finding.** U10 F4 cited `config.profiling_provider` as "(U4 §7, D17)", implying U4 §7's `RunConfig` already lists it; it does not — the field is being introduced.

**Resolution (clarifies U10 F4 / extends U4 §7).** `profiling_provider` is a **new** field added to `RunConfig` by Amendment 001 (F4), not a pre-existing one. The `RunConfig` field set is therefore U4 §7's list **plus** `profiling_provider` (selects the U3 profiling provider: in-app default, external MCP server, or UC function — D25). No other `RunConfig` change.

---

## Net effect

After Amendments 001 + 002 the state vocabulary is internally consistent and single-sourced:
- **Session:** `session_status` (U2, unchanged); "running" = the `IN_FLIGHT` set (U10 F1).
- **Draft review axis:** `draft_status` = `draft, needs_input, low_confidence, error, reviewed, edited, approved, applied, rejected`.
- **Apply axis:** `apply_status` = `not_applied, applied, conflict, failed, unsupported, skipped_noop` (the *only* per-item apply vocabulary; U6's "pending" = `not_applied`, U6 errors = `failed`).
- **Config:** `RunConfig` = U4 §7 fields + `profiling_provider`.

No HIGH findings were ever raised across either audit; the design is sound and consumable. The consolidated schema delta in U10 stands, with NF1/NF2 folding cleanly into the `apply_status` enum already listed there.
