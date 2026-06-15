# geniefy-v3 — Low-Level Design: Review & Apply (UI + UC Write Path)

**Status:** draft (U6) · **Last updated:** 2026-06-11
**Inputs:** [`HLD.md`](HLD.md) (U1), [`LLD-data-layer.md`](LLD-data-layer.md) (U2) · builds on U5's draft/question read endpoints and the U4 drafts · **Decisions:** D7 (apply is separate, diff-first, never clobber), D6 (Lakebase), D11 (demo-simple), D12 (parameterized), D17 (state), D20 (per-item loop granularity), D21 (apply re-checks live comment + idempotent — added by this unit)
**Scope:** the **review/edit/approve UX** and the **governed Unity Catalog write path** (`COMMENT ON` / `ALTER COLUMN`). *Out of scope:* agent internals (U4), session lifecycle/async (U5), the Lakebase schema (U2), packaging/deploy (U8), the approval → export-as-code → CI/CD path (deferred, D7).

> This closes the v1 interactive loop: generated drafts → human review → governed apply to UC. Living doc; CI/CD export and revert are sketched and marked.

---

## 1. Purpose

After the agent produces drafts (U4) and the backend persists them (U5/U2), a human **reviews, edits, approves**, and then **applies** comments to Unity Catalog — diff-first, audited, and without ever silently clobbering curated comments (D7). Generation never writes to UC; **Apply is a separate, explicit, governed action.**

---

## 2. Review model

The review surface lists, for the session's table:
- **One table row** + **one row per column**: `current_comment` (live in UC at profile time) vs `proposed_comment`, a rendered **diff**, the **confidence** (Judge `overall`, U4), the model's **rationale**, the **judge issues**, and the draft **status**.
- **Sort/filter by confidence** so reviewers triage low-confidence items first; high-confidence items can be bulk-approved.
- **Outstanding questions** (when the session was `awaiting_input`) are answered here, routing back through U5 `POST /answers` → the core re-reasons just those items (U4 §4).
- **Library suggestions** (U2 `comment_library`, v1 name-match): if an approved comment exists for a like-named column, surface it as an alternative.

## 3. Edit & approve

- **Inline edit** of `proposed_comment` (and conditional fields) → draft status `edited`; the human's text supersedes the model's for apply.
- **Status transitions** (U2 §6.2): `draft → reviewed | edited → approved | rejected`. Only `approved` drafts are eligible to apply.
- **Bulk approve** above a confidence threshold; **reject** drops an item from the apply set.
- Every edit/approve/reject appends an `audit_log` row (U2 §4.8) with actor + before/after.

## 4. The apply write path (the governed write — D7, D21)

Apply turns approved drafts into UC DDL, executed via the SQL warehouse (U5 backend runs the statement). It is **diff-first, conflict-aware, idempotent, and per-item**.

**Per approved item, in order:**
1. **Re-read the live comment** from UC (`current` at *apply* time), not the profile-time snapshot.
2. **Conflict check (D21, optimistic concurrency):** if the live comment differs from the stored `current_comment`, someone changed it since profiling → **do not write**; mark a **conflict**, surface both versions, require explicit re-confirmation. No silent clobber.
3. **No-op check (idempotent):** if the approved text equals the live comment → skip, mark `applied` (nothing to write).
4. **Write** the statement with **properly escaped text** (single quotes/backslashes; never string-concatenate raw user text into DDL):
   - Table: `COMMENT ON TABLE {catalog}.{schema}.{table} IS :comment`
   - Column: `ALTER TABLE {catalog}.{schema}.{table} ALTER COLUMN {col} COMMENT :comment`
   - (Identifiers validated/quoted; comment value bound, not interpolated.)
5. **Persist per item:** set draft `status=applied`, `applied_comment`, `applied_at`, `applied_by`; append an `audit_log` row.

**Per-item loop granularity (D20):** apply loops over columns and **checkpoints after each one**. A failure or crash mid-apply leaves precise per-column state (`applied` vs still-`approved`); recovery resumes at the next un-applied approved column — never re-applies a done one, never loses the set. Partial failures don't block the rest.

**Apply scope:** a reviewer can apply one item, or batch-apply all `approved` items; the engine still processes per-item with per-item status.

## 5. Permissions (D7 / HLD watch-item)

Applying requires **`MODIFY`** on the table for the acting identity (app SP by default, or on-behalf-of user — D11). A missing grant surfaces a **clear, specific error** (which object, which grant) and marks the item `approved` (not `applied`) — never a silent failure. Read/profile side needs `SELECT` (surfaced earlier, U3/U5).

## 6. API additions (on top of U5 §6)

| Method & path | Purpose |
|---|---|
| `GET /sessions/{id}/drafts` | (from U5) table+column drafts with current/proposed/diff/confidence/status |
| `PATCH /sessions/{id}/drafts/{draftId}` | edit `proposed_comment` / conditional fields → `edited` |
| `POST /sessions/{id}/drafts/{draftId}/approve` · `/reject` | status transition |
| `POST /sessions/{id}/approve-bulk` | approve all above a confidence threshold |
| `POST /sessions/{id}/apply` | apply approved items (per-item or batch); returns per-item results incl. `conflict` |
| `POST /sessions/{id}/drafts/{draftId}/apply?confirm=true` | re-confirm + apply a conflicted item |
| `GET /sessions/{id}/apply-status` | per-item applied/conflict/failed/pending |
| `GET /sessions/{id}/audit` | audit trail for the session/table |

## 7. Frontend (React)

Review table (table row + column rows), **side-by-side diff** (current vs proposed) with edit-in-place, **confidence badges** (and why — judge issues on hover), a **question panel** for `awaiting_input` items, and an **Apply** control with a **confirmation dialog** that lists what will be written and flags any **conflicts**. A history view lists past sessions and their applied outcomes (U2 §7). Poll-based status (D18).

## 8. Errors & edge cases

| Condition | Behavior |
|---|---|
| Live comment changed since profiling | `conflict`; no write; show both; require `confirm=true` to proceed (D21) |
| Proposed == live comment | no-op; mark `applied` (idempotent) |
| Missing `MODIFY` | clear per-item error; item stays `approved`; others proceed |
| Warehouse / DDL error on one item | mark that item `failed` with the message; continue the rest (D20) |
| Empty/whitespace proposed comment | reject at edit time (template requires a definition); never write an empty comment over a good one |
| Re-apply an already-`applied` item | idempotent no-op (re-reads live, equal → skip) |
| Special chars in comment | bound/escaped, never interpolated |
| View / MV columns | apply where UC supports column comments; otherwise mark `unsupported` with reason |

## 9. Interfaces to other units

- **U2 (data layer):** writes `*_drafts` status + `applied_*`, and `audit_log`; reads drafts for review.
- **U5 (app backend):** executes the warehouse DDL and owns the HTTP endpoints; this unit specifies their review/apply semantics.
- **U4 (agent core):** consumes its drafts (current/proposed/confidence/rationale); answers route back to `resume`.
- **U8 (deploy):** warehouse + `MODIFY` grant wiring; whether `.gotm/` + dev hooks ship to customers.

## 10. Deferred / sketched

- **Approval → export-as-code → CI/CD** (D7): instead of (or in addition to) direct apply, export approved comments as SQL/DDL or declarative YAML to git → PR → CI applies on merge. Lakebase = working store; git = deployed source of truth.
- **Library promotion:** on approve, optionally promote a comment into `comment_library` for cross-table reuse (the quality flywheel, HLD §5.2).
- **Revert / rollback** of applied comments (needs prior-value capture — already in `audit_log.before`).
- **Cross-table bulk apply** for batch/hands-free mode (D2).
- **Optimistic-concurrency tokens** finer than comment-equality (e.g., table version) if needed.
