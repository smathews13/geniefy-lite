# geniefy-v3 — Low-Level Design: App Backend (Session Loop & Interactive Q&A)

**Status:** draft (U5) · **Last updated:** 2026-06-11
**Inputs:** [`HLD.md`](HLD.md) (U1), [`LLD-data-layer.md`](LLD-data-layer.md) (U2) · consumes the U4 core API + D17 `SessionState` · **Decisions:** D1, D2, D6 (Lakebase), D8, D11 (demo-simple), D12 (parameterized), D17 (stateless core / caller owns persistence), D18 (async execution model — added by this unit)
**Scope:** the Databricks App **backend** — the session lifecycle, the interactive Q&A loop, persistence of `SessionState` ↔ Lakebase, history, and template/provider configuration endpoints. *Out of scope:* the review/edit UI and the UC apply write-path (U6), the agent internals (U4), the Lakebase schema itself (U2), packaging/deploy (U8).

> Living doc. v1 (interactive single-table) is concrete; batch/Job triggering and live-progress streaming are sketched and marked.

---

## 1. Purpose & role

The backend is the **caller that owns persistence** (D17): it drives the U4 `DocumentationOrchestrator`, serializes `SessionState` to Lakebase between turns (U2), and exposes a REST API the frontend (U6) uses to create runs, poll status, answer questions, and browse history. It is the *orchestration* layer — heavy compute lives in the profiling provider (U3) and the model endpoint (U4); long/batch runs delegate to a Job (D2, deferred).

---

## 2. Stack, auth, resources

- **Runtime:** Databricks App running **FastAPI** (Python) + a React frontend (U6). One repo, deployed via DAB (U8).
- **Identity (demo-simple, D11):** the App's **service principal** is the default actor for warehouse + Lakebase + model-endpoint access. On-behalf-of-user is an optional toggle for UC reads/writes where per-user authz matters; v1 default is the SP. No multi-tenant RBAC in v1.
- **Attached resources (DAB-declared, parameterized — D12):** a SQL warehouse (profiling provider + apply path), a Lakebase database instance (state), the FMAPI/AI-Gateway model serving endpoint (U4), and secret scopes (MCP provider tokens, U2 `context_providers`). No hardcoded IDs — all injected via app config / env.

---

## 3. Persistence boundary (D17 ↔ U2)

The core hands back a `SessionState` blob; the backend is responsible for mapping it onto the normalized U2 tables and rehydrating it on the next call.

| `SessionState` field | Persisted to (U2) |
|---|---|
| target, mode, template_id, config, mlflow_run_id, phase, status | `sessions` (one row) |
| profile snapshot, schema_meta, context snippets | `sessions.config`/dedicated JSON columns (or a `session_state` blob column) |
| table draft | `table_drafts` |
| column drafts (+confidence, judge_scores, status) | `column_drafts` |
| open questions / answers / agent messages | `session_messages` |

The backend writes **after every orchestrator step** (the write-back is transactional per step) so a crash or pause never loses progress. On `resume`, it **rehydrates** `SessionState` from these rows and passes it back to the core — the core re-runs only the affected items (D17). Normalized rows power the review UI (U6) and history queries (U2 §7) directly; a denormalized `session_state` blob can back fast rehydration (decide in U2 follow-on — flagged).

---

## 4. Execution model (D18)

Profiling + multiple LLM calls take seconds-to-minutes; Databricks Apps have short HTTP request timeouts. So **runs are asynchronous**:

- `POST /sessions/{id}/run` enqueues the orchestrator on an **in-process background task** (FastAPI background task / worker thread), flips `sessions.status` to `profiling`, and returns immediately.
- The background worker drives `orchestrator.run(...)`, writing status + partials to Lakebase as it advances through phases (`profiling → gathering_context → reasoning → …`).
- The frontend **polls** `GET /sessions/{id}` until `status` is `awaiting_input` (questions ready) or `ready_for_review` (drafts ready) or `failed`.
- **Batch / "profile N tables" (deferred, D2):** delegated to a **Databricks Job** running the same core; the App becomes a launcher + viewer. In-process background tasks are for single-table interactive only.

This matches the agreed compute split (App-first; Jobs only for large batch). Live-progress streaming (WebSocket/SSE) instead of polling is a deferred enhancement.

---

## 5. Interactive Q&A loop

```
create → run (bg) ──profiling/gathering/reasoning/judging/gating──┐
                                                                   ▼
            ┌──────────────── status = awaiting_input ◄── NeedsInput(questions)
            │   (questions persisted to session_messages; frontend renders them)
            │
   POST /sessions/{id}/answers (answers persisted)
            │
            └─► resume (bg) ── re-reason affected items ──► (loop) ──► ready_for_review
                                                                          │
                                                              (handed to U6: review/edit/apply)
```

- Hands-free mode (deferred batch) skips `awaiting_input`: low-confidence drafts are flagged `low_confidence` and the session lands directly in `ready_for_review`.
- Guard: at most **one active background run per session** (enforced via `status` — reject `run`/`answers` if a run is already in flight) to prevent concurrent mutation of the same `SessionState`.

---

## 6. REST API (v1)

| Method & path | Purpose | Notes |
|---|---|---|
| `POST /sessions` | Create a session for a `TableRef` + template + config | returns `id`, `status=created` |
| `POST /sessions/{id}/run` | Start the first pass (async) | 202; flips to `profiling`; rejects if already running |
| `GET /sessions/{id}` | Poll status + summary | drives frontend polling |
| `GET /sessions/{id}/drafts` | Table + column drafts (current vs proposed, confidence, status) | feeds review UI (U6) |
| `GET /sessions/{id}/questions` | Open questions (when `awaiting_input`) | from `session_messages` |
| `POST /sessions/{id}/answers` | Submit answers → resume (async) | 202; rejects if running or not `awaiting_input` |
| `POST /sessions/{id}/pause` · `POST /sessions/{id}/cancel` | Pause / cancel | pause is implicit (state already persisted); cancel sets terminal status |
| `GET /sessions?created_by=&status=` | History list (paginated) | U2 §7 P2 |
| `GET /templates` · `POST /templates` | List / register templates | the "what good looks like" spec (U2 `templates`) |
| `GET /providers` · `POST /providers` · `PATCH /providers/{id}` | List / register / enable-disable context providers (incl. MCP) | D5; secrets via scope refs only (U2) |

Apply/approve endpoints and the UC write-path are **U6** (this backend exposes the draft reads they build on).

## 7. Concurrency, integrity, idempotency

- **Single-flight per session** (§5 guard) via `sessions.status`.
- **Step-transactional writes**: each orchestrator phase's output is persisted in one transaction; status flips last, so a partially-written step is never observed as complete.
- **Idempotent run/resume**: re-issuing `run` on a non-`created` session is rejected; `answers` are keyed to the open question set to avoid double-application.
- **Audit**: generate/edit/answer events appended to `audit_log` (U2 §4.8).

## 8. Error handling

| Condition | Backend behavior |
|---|---|
| Orchestrator `Failed` (profiling perm/timeout, persistent LLM failure) | persist `status=failed` + `sessions.error`; surface verbatim to frontend; partials remain viewable |
| MCP context provider down | core degrades (U4 §8); backend records a warning on the session, run continues |
| Background task dies (process restart) | on next poll, a watchdog detects a stale `running` session past a timeout and marks it `failed` (recoverable via re-run, since state is persisted) |
| Lakebase unavailable | 503; no partial state corruption (writes are transactional) |
| Missing `MODIFY`/`SELECT` grants | surfaced as a clear error (apply-side handled in U6; read/profile-side here) |

## 9. Interfaces to other units

- **U2 (data layer):** the only writer of `sessions`/`*_drafts`/`session_messages`/`audit_log`; owns the `SessionState` ↔ rows mapping (§3).
- **U4 (agent core):** calls `run`/`resume`; passes/receives `SessionState`; never reaches into core internals.
- **U6 (review & apply):** consumes the draft/question read endpoints; adds approve/apply write-path + UI.
- **U8 (deploy):** declares the App + resources + parameters in the DAB; injects config/secrets.

## 10. Deferred / sketched

- **Batch endpoints** + Databricks Job trigger for "profile N tables" (D2); App as launcher/viewer.
- **Live progress** via WebSocket/SSE instead of polling.
- **Auth hardening** — per-user RBAC, on-behalf-of as default, row-level access (beyond demo scope, D11).
- **`session_state` blob vs. pure-normalized rehydration** — performance choice to settle in a U2 follow-on.
- **Optimistic concurrency** on edits once U6's interactive editing lands.
