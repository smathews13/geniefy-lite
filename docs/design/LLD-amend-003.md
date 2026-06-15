# geniefy-v3 — Design Amendment 003: Enhancement Set R2

**Status:** draft (U79) · **Date:** 2026-06-12
**Amends/extends:** U4 (agent core — Reasoner/Orchestrator/LLMClient), U5/U6 (App backend/API), U2 (data layer — history/library), U9 (UX), U32 (LLMClient). Frozen docs are unedited; the deltas live here (GOTM freeze rule, as with amend-001/002).
**Decisions recorded by this unit:** D43–D47.
**Driver:** the live deploy works end-to-end (U76); this round makes the output richer, the loop resumable/governed, and the UX delightful — plus an apply bug. Nine items (E1–E9).

> Living doc. Each section ends with its **implementing unit(s)**; code is built only after this design passes an independent audit.

---

## E1 — Richer comments + per-phase token budgets (D43)

**Problem.** Table comments are thin: `default.yaml` caps `table_comment.style.max_words: 120` (and `column_comment: 40`), and every call shares `DEFAULT_MAX_TOKENS=4096`.

**Design.**
- **Template:** raise `table_comment.style.max_words` 120→**500** (rich but bounded) and `column_comment.max_words` 40→**150**; keep the `forbid` rules (no marketing/speculation). Teams can still clone/version (U4 §3.1).
- **Per-phase output budgets:** new `RunConfig` fields `reason_table_max_tokens` (**20000**) and `reason_column_max_tokens` (**2000**, per column-batch the reasoner already chunks). The `Reasoner` passes `max_tokens` per call type (table vs column-batch) instead of the shared default. Surfaced as `GENIEFY_REASON_TABLE_MAX_TOKENS` / `GENIEFY_REASON_COLUMN_MAX_TOKENS` (AppConfig), `app.yaml`-configurable.
- **Caveat (documented):** UC comments have a length limit; the Applier (D7) truncates-with-warning if a generated comment exceeds it. (Note in the apply path; not a blocker.)

**Implementing:** U81 (template + RunConfig + Reasoner) → builds on U80's budget plumbing.

## E2 — Two-pass generation: table first, then columns with the table comment as context (D44)

**Problem.** The Reasoner drafts the table comment and the column batch independently; the column pass lacks the table's synthesized purpose/grain, and a single huge pass wastes context.

**Design.** Keep the existing split (`draft_table` then `draft_columns`), but **feed the generated table comment into the column pass** as an explicit context block ("TABLE COMMENT (just generated): …"), so columns are grounded in the table's purpose/grain/keys. This *reduces* per-call context (the column pass no longer needs the full table-reasoning prompt) and improves coherence. The Orchestrator already calls `reasoner.draft(state)`; the Reasoner sequences table→columns and threads `state.table_draft.proposed_comment` into the column messages.

**Implementing:** U82 (Reasoner two-pass threading).

## E3 — Re-generate (table or specific columns) (D45)

**Design.** `POST /api/sessions/{id}/regenerate` with body `{ "targets": ["__table__" | "<column>" , …] }` (or `{"all": true}`). The Orchestrator gains `regenerate(state, targets)`: re-reason → re-judge → re-gate **only the named targets** (reusing the existing profile/context — no re-profiling, like `resume`, D17), merging fresh drafts back and preserving the others' review state. Frontend: a **"Regenerate"** button per `DraftCard` + a table-level one. Runs async + poll (D18), like a run.

**Implementing:** U83 (backend endpoint + Orchestrator.regenerate), U88 (UI buttons).

## E4 — Profile visual cues per column (UX)

**Design.** The profile is already in `state.profile` (and surfaced via `/api/sessions/{id}` → `column_drafts` carry `data_type`; the full profile is in the session). Expose the per-column profile in the API response and render in `DraftCard` using the existing `viz/` primitives: `NullFractionBar` (null_fraction), a distinct/cardinality chip, min/max range, `top_k` as small bars/`Sparkline`, an `is_enum_candidate`/PII badge. Read-only "evidence at a glance" beside each draft.

**Implementing:** U84 (API: include per-column profile in the session view), U86 (DraftCard profile viz).

## E5 — Delightful UI (UX, U9)

**Design.** A restrained delight pass (U9 §2 principles, D22/D23): smooth phase transitions in RunView, the readiness meter glow-up animation, confident micro-interactions on approve/apply, the profile viz from E4, consistent spacing/typography. No overboard motion (D23). Code-split stays (U70).

**Implementing:** U88 (delight pass, with E3 buttons).

## E6 — Session history + comment library in Lakebase (D46)

**Design.** Sessions already persist (U45/SessionStore, D6/D17) — every run/draft/status is in `geniefy.sessions` + drafts. Add:
- **History:** `GET /api/sessions?status=&table=&limit=&offset=` → a paginated list (id, target, status, created/updated, counts) via a new `SessionStore.list_sessions(...)`. A **History** view in the SPA lists past runs; clicking one loads it (the existing `GET /api/sessions/{id}` already rehydrates) → resume/review/apply.
- **Library:** the `geniefy.comment_library` table already exists (scope · match_key · canonical_comment · tags · usage_count · source_session). On **apply** (D7), the Applier writes each applied comment to the library (`SessionStore.upsert_library_entry`), so approved comments are reusable. A **Library** view lists entries; reuse-on-generation is a future hook (the Reasoner can be fed library matches — deferred).

**Implementing:** U84 (`list_sessions` + `upsert_library_entry` + endpoints), U87 (History/Library UI).

## E7 — Generate now, approve later (D46)

**Design.** A consequence of E6 + D17: a session persists at any phase (`awaiting_input`, `ready_for_review`), so a user can generate now, leave, and later open it from History → answer questions / review / apply. No new state — the working store + the history list + the existing load/resume/apply already provide it; E6's History view is the entry point.

**Implementing:** covered by U84 + U87 (no separate unit).

## E8 — LLM exponential backoff + 429 protection (D47)

**Problem.** `LLMClient` retries transient errors with `backoff_base * 2**attempt` but: only 2 retries, no jitter, and no 429/`RateLimitError`-specific handling (the OpenAI client + AI Gateway return 429s).

**Design.** Enhance the retry: detect **rate-limit/429** (the `openai.RateLimitError` / a 429 status, surfaced as a transient) and a server-5xx as retryable; **exponential backoff with full jitter** (`random.uniform(0, base * 2**attempt)`), honor a `Retry-After` header when present; raise the retry budget (e.g. `DEFAULT_MAX_RETRIES` 2→**5** for rate limits) — capped. Hermetic: the `sleep` + the transport are injected (tests assert backoff growth + 429 retried). Ties to D4 (AI-Gateway rate limits) + NFR-B.

**Implementing:** U80 (LLMClient backoff/jitter/429).

## E9 — Apply-to-UC not working (bug)

**Hypotheses (to verify in the fix unit, by impact):**
1. **Target is read-only** — applying to `samples.tpch.orders` requires `MODIFY`, but `samples` is a Databricks-managed read-only catalog → every item fails. *Most likely;* verify by applying to a writable table (the SP's own catalog) + confirm the per-item error surfaces (U70 added apply-error display).
2. **No approvable drafts** — apply only writes `approved`/`edited` drafts (D7/U6); if nothing was approved, `applied: 0` looks like "nothing happened."
3. **A real wiring/SQL bug** in the Applier or the `/apply` route.

**Design.** Diagnose live (apply on a writable target, read the per-item results + logs), ensure the apply **result + per-item conflict/failed reasons are clearly surfaced** in the UI (extend U70's banner with the per-target outcomes), and fix any genuine defect. Confirm the SP has `MODIFY` on the chosen target (operator grant, like the Lakebase one).

**Implementing:** U85 (diagnose + fix/surface).

---

## Sequencing (foundation-first)

1. **Agent core:** U80 (backoff) → U81 (token budgets + template) → U82 (two-pass).
2. **Backend:** U83 (regenerate) → U84 (history list + library + per-column profile in the view) → U85 (apply fix).
3. **Frontend:** U86 (profile viz) → U87 (history/library UI) → U88 (delight + regenerate buttons).
4. **Verify:** U89 (redeploy + live e2e re-verify, D36).

Each code unit gets an independent unit-wise audit (D41). This design is audited (U79) before any code unit consumes it.
