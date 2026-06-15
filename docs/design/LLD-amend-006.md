# LLD-amend-006 — Comment enrichment + library lifecycle + reuse + steward-first hero (R3, D52/D53)

**Unit:** U102 · **Status:** design · **Supersedes:** nothing (*extends* LLD-agent-core U4 Reasoner/Template, LLD-data-layer U2 `comment_library`, LLD-review-apply U6 review/apply, LLD-amend-003 E6 deferred library hook, U96/U99 frontend).
**Inputs:** D52 (library lifecycle + reuse) · D53 (enrichment + tags + hero) · D9 (`comment_library`) · D43 (richer comments) · D23 (UX delight) · U55/U84 (apply-time library upsert) · U82 (Reasoner) · U86/U99 (DraftCard/ReviewView) · U62 (LibraryView).

This amendment has two cooperating halves: **(A)** the library becomes a governed, reusable definition store; **(B)** generated comments get richer + tagged, rendered steward-first. They share one spine: *tags + canonical wording flow from approval back into generation*.

---

## PART A — Library lifecycle + reuse-on-generation (D52)

### A1. Status lifecycle (Q4) — migration `002`

`comment_library` gains a **`status`** column with three states and explicit transitions:

```sql
-- migration 002_library_status.sql
ALTER TABLE comment_library
  ADD COLUMN status TEXT NOT NULL DEFAULT 'approved';   -- approved | applied | sunset
ALTER TABLE comment_library
  ADD COLUMN sunset_at  TIMESTAMPTZ NULL,
  ADD COLUMN sunset_by  TEXT NULL;
CREATE INDEX comment_library_reuse_idx
  ON comment_library(scope, match_key, status);          -- reuse lookup (A4)
```

| Trigger | Transition | Writer |
|---|---|---|
| Draft **approved/edited** in review (U6) | (none) → `approved` | `SessionService.review_draft` (**net-new** library write) |
| **Successful** UC apply (U6/U55) | `approved`/(none) → `applied` | `Applier._write_library` (U84) sets `status=applied` |
| **Failed** apply | stays `approved` (or absent) | — (no upgrade on failure, Q4) |
| Operator **sunset** | `approved`/`applied` → `sunset` (+`sunset_at/by`) | `POST /api/library/{id}/sunset` |
| Operator **revive** (Q6) | `sunset` → `approved` *(or `applied` if it had been applied — see A2)* | `POST /api/library/{id}/revive` |

The displayed status mirrors reality (the human directive: *"if the apply is successful it should say applied else say approved"*): a library entry shows **applied** once written to UC, **approved** if only approved, **sunset** if retired.

### A2. One canonical entry per `(scope, match_key)` (Q5)

Upsert key stays `(scope, match_key)` (existing U84 behavior) — **one canonical definition per column-name / table-FQN**. On a new approval for an existing key: overwrite `canonical_comment` + `tags`, refresh `source_session_id/source_table_ref`, `usage_count++`, set `updated_at`. Revive restores to **`applied`** if `applied_at`/an apply marker exists, else `approved` (so reviving an entry that *was* applied doesn't downgrade its truth). Generic-name churn (`id`, `status`) is an accepted trade-off, mitigated by **edit** (fix the canonical) and **sunset** (retire a bad one) — not by changing the key.

### A3. Write-on-approve (net-new) — extends U6 `review_draft`

Today only **apply** writes the library (U84). New: when `SessionService.review_draft(action ∈ {approve, edit})` accepts a draft, it **best-effort upserts** the library at `status=approved` (same `upsert_library_entry`, now status-aware). Best-effort = a library write failure **never** blocks the review action (mirrors D50's best-effort discipline; logged, not surfaced as an error). Apply later upgrades the same key to `applied`.

### A4. Reuse-on-generation (Q1) — new `LibraryProvider` in the ContextGatherer (U4/D5)

A new context provider (so reuse flows through the existing ranked/budgeted context pipeline, D5/D34 — *not* a side-channel in the Reasoner):

- **Lookup:** for each target (table FQN; each column by name), exact `match_key` match, `status ∈ {approved, applied}`, **`sunset` excluded**, ordered by `usage_count DESC, updated_at DESC`, top-K (small, e.g. 3).
- **Emit:** a context block `library_matches` → the Reasoner prompt renders it as **suggestion-only** grounding:
  > *"Previously-approved canonical comments for columns named `country` (reuse the wording when it fits THIS table's data; adapt or ignore if the profile differs): …"*
- **Guardrail:** the Reasoner still grounds primarily in **this** table's profile (D43); the library is a consistency nudge, never a blind copy. The provider is **read-as-SP** (D48), same as other context.
- **Scope as SP context only:** library matches are part of generation context; they do not pre-fill or auto-approve anything.

### A5. Sunset / revive (Q6) — soft, revivable

- `POST /api/library/{id}/sunset` → `status=sunset`, `sunset_at/by` from identity (D48); excluded from A4 reuse + from the default Library view filter (shown under a "Sunset" filter).
- `POST /api/library/{id}/revive` → back to `applied`/`approved` per A2.
- Soft only — **no hard delete** (audit retention, Q6). `usage_count` is preserved across sunset/revive.

### A6. Library UI (extends U62 LibraryView)

Each entry row shows: scope · `match_key` · a **status badge** (applied / approved / sunset) · `usage_count` · tags · source table. Actions: **Sunset** (on approved/applied) / **Revive** (on sunset). Default filter hides sunset; a toggle reveals them (greyed).

---

## PART B — Comment enrichment + tags + steward-first hero (D53)

### B1. Richer table comment (Q3) — extends the Template (U4) + Reasoner table prompt (U82)

The table-comment template gains a structured field set serving humans + agents (Genie):

- **Required:** purpose · **business definition** (plain-business meaning, distinct from technical purpose) · grain · primary keys · join keys.
- **Recommended:** **technical owner / data owner** · **freshness / SLA** (update cadence) · source systems · downstream consumers · **known business rules** · **known data-quality issues** · common join patterns · related tables · sensitivity / access · **example questions** (Genie-readiness: "this table answers questions like…") · caveats.

The bolded fields are the human's explicit set (D53) + the additions I proposed. The table-comment word cap rises to accommodate the richer content (deterministic value, NFR-A config surface — `GENIEFY_REASON_TABLE_MAX_TOKENS` already 20000 covers output; the *template's* `max_words` guidance rises, e.g. ~500→~900). The Reasoner table prompt is extended to elicit these fields and to **fold in `library_matches`** (A4) for the table FQN.

### B2. Free-form tags (Q2) — new draft fields

- **`TableDraft.tags: list[str]`** and **`ColumnDraft.tags: list[str]`** (2–4 per column) added to `state.py` (+ `to_dict/from_dict`, like U100's `suggested_answer`).
- The Reasoner table + column prompts ask for **free-form** tags, **seeded** with suggestions (`identifier`, `metric`, `dimension`, `PII`, `temporal`, `enum`, `key`, `fact`, `dimension`, `deprecated`, …) but **not constrained** to a fixed taxonomy (Q2) — the model may coin apt tags (e.g. `revenue`, `arpdau`, `cohort`). Tags are grounded in the profile (a `PII` tag should track the existing PII signal; an `enum` tag the low-distinct signal).
- Tags persist with the draft and into `comment_library.tags` (already exists) on approve/apply, so reuse (A4) can also surface canonical tags.

### B3. Data-type + tag pills (#2) — extends DraftCard/ProfileStrip (U96/U99)

- Column card: the **data type** moves from inline text into a **pill**; **column tags** render as pills beside it (compact, color-keyed by a small tag→hue map; PII/sensitivity tags get an alert hue).
- Pills are a small shared `Pill`/`PillRow` component (reused for table + column).

### B4. Steward-first hero table card (#4) — extends ReviewView/DraftCard (U99)

Redesign the table card (today `variant='table'`, indigo accent) into a **governance-forward hero** read from a data-steward / business-owner / data-owner POV:

- **Hero header band:** table FQN + a **trust/confidence signal** (the Judge verdict as a prominent badge) + **table tags** as pills.
- **Steward facts row** (scannable, icon-keyed): **owner** · **freshness/SLA** · **grain** · **keys** · **sensitivity** — the things an owner checks first.
- **Body:** purpose + business definition prominent; known business rules / quality issues / example questions in labeled sub-sections (collapsible if long).
- **Visual weight:** the hero is clearly the page's primary object (full-width, elevated, stronger accent); the **columns** below read as a secondary, denser list (smaller cards) — sharpening the U99 hierarchy the human liked, now "more distinct."
- Honesty guardrail (D23): confidence/verdict shown truthfully; low-confidence fields are visibly flagged, not hidden.

### B5. What this does NOT change

- No new apply mechanics (OBO/D48/D7 unchanged); tags + richer fields ride the existing draft → review → apply → library path.
- No locked tag taxonomy (Q2) — governance over tags is a future option.
- Reuse is suggestion-only (A4) — generation still grounds in the live profile (D43).

---

## C. Build-unit sketch (registered as R3 units after audit PASS)

1. **Data:** migration `002_library_status.sql` + `SessionStore` (status-aware `upsert_library_entry`, `sunset/revive`, reuse query) + tests.
2. **Core:** `TableDraft.tags`/`ColumnDraft.tags` (+ to/from_dict) + `LibraryProvider` context provider + Reasoner prompt updates (richer table fields, tags, library_matches folding) + tests.
3. **Backend:** write-on-approve in `review_draft`; `status=applied` on apply; `/api/library/{id}/sunset` + `/revive`; reuse wired into `build_service` context providers + tests.
4. **Frontend:** `Pill`/`PillRow` + data-type & tag pills on column cards; steward-first hero table card; LibraryView status badges + sunset/revive actions + types/hooks.
5. **Verify:** local devloop + live run (richer comment + tags + hero + reuse on a previously-applied column) via grant-safe `--code-only` deploy.

Each atomic (module+test = one unit), independently audited (D24/D41), grant-safe deploys (D48/U78).
