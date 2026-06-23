# LLD amendment 007 — overall table confidence + bulk high-confidence approve

**Status:** design · **Decision:** D59 · **Extends:** U6 (review/apply), U9 (UX delight), U112 (hands-off Schema view) ·
**Guardrails:** D8 (Judge confidence is authoritative), D22/D23 (honest confidence UX), D7/D48 (apply is human-approved, OBO).

This amendment adds two paired review affordances — an **overall table-confidence** read and a **bulk
"Approve high-confidence"** action — to **both** the interactive review (`ReviewView`) and the hands-off
schema view (`SchemaRunView`). They share one mental model: *see how good the drafts are overall → act on
the ones the agent already vetted → focus your attention on the few that are flagged.*

## 1. Two distinct top-of-review axes

The review header shows **two** meters, deliberately separate (they answer different questions):

| Meter | Formula | Answers |
|---|---|---|
| **AI-ready** (existing, unchanged) | `applied / total` | "how much have I written back to UC?" (governance progress) |
| **Confidence** (NEW) | weighted rollup of Judge per-draft confidences | "how good are these drafts, overall?" (quality) |

They must not be conflated — a table can be 92% Confidence and 0% AI-ready (good drafts, nothing applied yet).

## 2. Overall confidence — formula (deterministic, client-side)

The rollup reuses the Judge's per-draft `confidence` (D8) already present on every draft — **no new LLM call.**

```
overall = 0.5 * table_conf + 0.5 * mean(column_conf)      # table comment is the headline; columns share the rest
        (if no columns:  overall = table_conf)
        (drafts with confidence == null are excluded from the mean; if all null, overall = null → render "—")
```

The single number is **never shown alone** (D22/D23). It is always accompanied by:

- **A breakdown using the Gate's buckets:** `N review-ready · M needs-input · K low-confidence`, where
  **review-ready** = `confidence ≥ keep_threshold` AND not flagged (`status ∈ {draft, approved, edited}` and no
  hard-signal trip). `needs-input`/`low-confidence` come straight from the draft `status` the Gate already set.
- **A "weakest" caveat:** `weakest: <target> @ X%` (the lowest-confidence draft), so a high mean can't mask a
  single risky column.

Rendered as a compact `ConfidenceChip`-styled meter + a one-line breakdown row, in the header next to the
AI-ready ring. `keep_threshold` is already exposed to the client (`RunConfig.keep_threshold`, types.ts).

## 3. Bulk "Approve N high-confidence"

A single button up top: **"Approve N high-confidence"** where `N` = count of currently-approvable
high-confidence drafts. It approves **only** drafts that are:

- `confidence ≥ keep_threshold`, AND
- not flagged: `status == 'draft'` (NOT `needs_input` / `low_confidence` / `error`, and not already
  `approved`/`edited`/applied), AND
- carry no hard-signal trip (the Gate already encodes this — a tripped draft is `needs_input`/`low_confidence`,
  so the status check subsumes it).

Everything else — the flagged minority — stays untouched and **requires explicit per-draft review**. There is
**no blanket "Approve all"** (a one-click blind approve defeats the governed-review reason the product exists,
D7). If `N == 0` the button is disabled with a hint ("nothing above the confidence bar — review the flagged
drafts individually").

**Apply is unchanged and still explicit:** bulk-approve only sets `status = approved`; the separate
**"Apply approved (N)"** step writes to UC, on-behalf-of the user (D48/D7). Bulk-APPROVE ≠ apply.

### Server endpoint (single source of truth)

`POST /api/sessions/{id}/approve-high-confidence` → `{ approved: ["__table__", "o_orderkey", …], count }`.
Server-side it loads the session, selects drafts by the rule above (the threshold/flag logic lives **once**,
server-side, reused by both surfaces), marks each `approved`, writes the `audit_log` `approved` rows
(actor = the request identity, D48), persists, and returns what it approved. Atomic, one round-trip — not N
client calls. (Reuses the existing per-draft review path's approve semantics.)

## 4. Hands-off Schema view (extend both)

In `SchemaRunView`'s per-table list (the sessions of a schema run):

- **Per-table overall confidence + breakdown** — shown per row, so a steward scans a 40-table run and sees
  which tables are solid vs which need attention without opening each. This needs the schema-run view to
  carry a small **per-session confidence summary** (`overall`, `review_ready`, `needs_input`, `low`, `weakest`)
  — computed server-side from the session's drafts when the run detail is assembled (the sessions are already
  loaded there). Either the same client rollup applied to a returned per-session draft summary, or a computed
  summary field; the LLD prefers a **server-computed `confidence_summary` per session** so the list needn't
  fetch every session's full drafts.
- **Per-table "Approve N high-confidence"** — a row action that calls the same
  `POST /api/sessions/{id}/approve-high-confidence` for that table's session, **without opening it**. After it
  returns, refresh the row's counts. Apply still happens later, explicitly, per table (human/OBO).

The governance copy stays explicit: bulk-approve at the schema level **never applies** anything to UC.

## 5. Honesty + governance invariants (must hold)

- Confidence is shown **truthfully** — the aggregate is always paired with the breakdown + weakest caveat; a
  weak draft is never hidden behind a green number (D22/D23).
- The **Confidence** meter and the **AI-ready** ring are visibly distinct and never conflated.
- **No blanket approve-all**; flagged drafts always require explicit review (D7).
- **Bulk-approve ≠ apply**; all UC writes remain human-approved + OBO (D48/D7). Schema-level bulk-approve
  applies to nothing.
- The bulk-approve selection rule lives **server-side, once**, used identically by interactive + hands-off.

## 6. Build units (proposed; register on design audit PASS)

1. **Backend** — `POST /api/sessions/{id}/approve-high-confidence` (threshold/flag selection + audit rows +
   persist) + a per-session `confidence_summary` in the schema-run view; + tests.
2. **Frontend / interactive** — `ReviewView`: the **Confidence** meter + breakdown + weakest caveat, and the
   **"Approve N high-confidence"** button (calls the endpoint, then re-polls). Reuse `ConfidenceChip`.
3. **Frontend / hands-off** — `SchemaRunView`: per-table confidence + breakdown column and the per-table
   **"Approve N high-confidence"** row action (same endpoint).
4. **Verify** — local devloop (seeded) + live: overall confidence + breakdown render honestly; bulk-approve
   approves only the high-confidence set and leaves flagged drafts; schema-level bulk-approve writes nothing
   to UC.
