# Audit 001 — geniefy-v3 design set (U1–U6, U8, U9)

**Date:** 2026-06-11   **Auditor:** independent dispatched subagent (did not author the work)
**Verdict:** PASS-WITH-FINDINGS

---

## Scope & method

I read, in full:

- Governance: `.gotm/PROTOCOL.md`, `.gotm/LEDGER.md`, `.gotm/DECISIONS.md` (D1–D25), `.gotm/QUESTIONS.md` (Q1–Q4).
- Design set under audit: `docs/design/HLD.md` (U1), `LLD-data-layer.md` (U2), `LLD-profiling-tool.md` (U3), `LLD-agent-core.md` (U4), `LLD-app-backend.md` (U5), `LLD-review-apply.md` (U6), `LLD-ux-delight.md` (U9), `LLD-packaging-deployment.md` (U8).

Method: applied the PROTOCOL "Audit gates" 5-point checklist (existence · spec-match · cross-reference integrity · internal consistency · decision fidelity) to every document. Mechanically enumerated every `D#`/`U#`/`Q#` citation across all eight docs and resolved each against DECISIONS/LEDGER/QUESTIONS. Spot-checked the load-bearing seams the brief named: profiling contract (U3) → consumer (U4); SessionState/persistence (U4 D17) → U2 schema → U5; apply path (U6) → U2 draft fields → D7/D21; UX (U9) consuming U4/U5 fields; deployment (U8) referencing resources the other docs assume. I did **not** apply any fixes.

---

## Checklist results

### 1. Existence — PASS
All eight promised outputs exist at their exact LEDGER paths under `docs/design/`. `.gotm/audits/` exists (this file is being written into it). U7's output is this audit; U8 (just completed) is present and complete. No `done` row has a missing output.

### 2. Spec match — PASS
Each doc delivers what its LEDGER row + its own Scope promised:
- **U1 (HLD)** — core concepts, components, modes, pipeline, template, governance, v1 scope. Foundation-level, as promised. ✓
- **U2 (data layer)** — full Lakebase schema (8 tables, 8 enums, state machines, access patterns, migrations, deferred sketch). ✓
- **U3 (profiling tool)** — `profile_table` request/response contract, sanitized profile schema, PII rules, sampling, wide-table batching, conformance. ✓
- **U4 (agent core)** — Template/Profiler/ContextGatherer/Reasoner/Judge/Gate, orchestration loop, public API, model integration, observability, config. ✓
- **U5 (app backend)** — persistence boundary, async execution model, Q&A loop, REST API, concurrency, errors. ✓
- **U6 (review & apply)** — review model, edit/approve, governed write path, permissions, API additions, frontend, errors. ✓
- **U9 (UX & delight)** — design language + the three centerpieces, confidence-as-visual-language, honesty guardrails, screens, deferred. ✓
- **U8 (packaging & deployment)** — DAB layout, parameterization, prerequisites, resource wiring, migrations, profiling deployment, ships-vs-dev-only, CI/CD deferred. ✓
None overruns its declared out-of-scope boundary.

### 3. Cross-reference integrity — PASS
Every citation resolves. Enumerated set of cited decisions: D1–D9, D11, D12, D15–D18, D20–D23, D25 — all exist in DECISIONS.md (which defines D1–D25) and each says what the citing doc claims (spot-checked the load-bearing ones: D17 stateless/SessionState, D21 apply re-check/idempotent, D7 diff-first apply, D4 sanitized profiles + AI Gateway, D15 MCP-service-vs-UC-function, D25 in-app profiling + `.gotm` exclusion, D18 async/poll — all correctly attributed). No doc cites a decision ≥ D26 or any undefined D#. Every cited unit (U1–U6, U8, U9) exists; every cited question (Q1–Q4) exists and HLD §11 maps them correctly. No dangling or misattributed reference found.

### 4. Internal consistency — PASS-WITH-FINDINGS
The architecture, persistence boundary, profiling contract, and apply path are coherent across docs. The **one real seam crack** is the **status/state vocabulary**: several per-item and in-flight state values used by the consuming docs (U4, U5, U6, U9) do not exist in the frozen U2 enums. See Findings 1–3. These are reconcilable (mostly by carrying the values as separate result fields or a follow-on enum amendment) and do not break the design's logic, but they are genuine cross-document mismatches against the authoritative schema.

### 5. Decision fidelity — PASS
The docs honor the decisions they cite; no silent divergence found. Checked specifically: D4 (no egress / sanitized-only) is honored consistently in U3 §5 + U4 §5 + U9 §3/§11; D7 "never clobber" + D21 conflict-aware idempotent apply is faithfully realized in U6 §4; D17 stateless caller-owns-persistence is consistent across U4 §2/§9, U5 §3, U6; D2 deferrals (batch/CI-CD/Genie-artifacts/eval) are marked deferred everywhere; D11/D22/D23 demo-but-delightful is honored by U9 with explicit honesty guardrails; D25 in-app profiling default + pluggable is consistent between U3 §3 and U8 §8.

---

## Findings

**Finding 1 — `running` session status used but absent from the U2 enum.** *(major)*
`LLD-app-backend.md` §8 (watchdog row): "a watchdog detects a stale **`running`** session past a timeout." D18's Consequences likewise say "watchdog marks stale **`running`** sessions `failed`." But `LLD-data-layer.md` §5 `session_status` enum has no `running` value — in-flight is modeled as the phase-specific `profiling`/`gathering_context`/`reasoning`/`applying`. A watchdog querying `status='running'` would match nothing.
*Pinned:* `LLD-app-backend.md:104`, `DECISIONS.md` D18 vs `LLD-data-layer.md:232-234`.
*Suggested fix (do not apply):* in a follow-on unit, either define the watchdog over the set of non-terminal in-flight statuses, or add a generic `running` status — and record which in a new D#; U2 is frozen, so the enum change is a follow-on migration unit.

**Finding 2 — Per-item draft states `low_confidence` / `error` / `conflict` / `unsupported` / `failed` have no home in the frozen U2 `draft_status` enum.** *(major)*
The U2 `draft_status` enum is `('draft','needs_input','reviewed','edited','approved','applied','rejected')`. Downstream docs introduce item states not in it: `low_confidence` (U4 §3.6/§8, U5 §5), `error` (U4 §8: "mark the affected batch's drafts `error`"), and `conflict`/`unsupported`/`failed` (U6 §8 and the `apply-status` endpoint U6 §6). U6 partly sidesteps this by returning `applied/conflict/failed/pending` from a *separate* `apply-status` result rather than the draft row — but the design set never states where `low_confidence`/`error`/`conflict`/`unsupported` are persisted given the frozen enum.
*Pinned:* `LLD-data-layer.md:235-236` vs `LLD-agent-core.md:88,134`, `LLD-app-backend.md:69`, `LLD-review-apply.md:64,78,82`.
*Suggested fix (do not apply):* a follow-on U2-amendment unit that either extends `draft_status` (migration) or formalizes a separate `apply_status`/flags column to carry these per-item states; cite it from U4/U6.

**Finding 3 — U4's "Maps 1:1 to … the `sessions.status` enum" overstates the mapping; U9 labels `judging`/`gating` as a status that does not exist.** *(minor)*
`LLD-agent-core.md:46` claims SessionState "Maps 1:1 to the U2 tables and the `sessions.status` enum," yet the orchestrator has five phases (PROFILING, GATHERING, REASONING, **JUDGING**, **GATING**) while the enum has no `judging`/`gating` value. U4 §4 itself reconciles this (folds judge/gate under `reasoning` in the status mapping), so the "1:1" wording is the only defect in U4. But `LLD-ux-delight.md:46` then presents a table row "`judging`/`gating` **(U4/U5 status)**" — treating as a poll-able status something U5 never exposes and U2 never defines.
*Pinned:* `LLD-agent-core.md:46`, `LLD-ux-delight.md:46`.
*Suggested fix (do not apply):* soften U4's "1:1" to "phases map onto the status enum, with judge/gate folded into `reasoning`"; in U9 relabel the row as a *phase* (sub-state of `reasoning`) not a status. (Both are follow-on edits — both source docs are `done`/frozen.)

**Finding 4 — Profiling provider selection is sourced from `context_providers`, but that table models context, not profiling tools.** *(minor)*
`LLD-profiling-tool.md:45` (§3) says provider selection "records the chosen provider in `sessions.config` (from the `context_providers`/tool registry in the data layer, U2)." But U2's `context_providers` table (and its `provider_kind` enum `builtin_lineage | builtin_query_history | mcp`) is explicitly the **context** registry (D5), not a profiling-tool registry — there is no profiling-provider entity in U2. U8 §8 resolves the runtime default cleanly (in-app profiling, pluggable via `config`), so this is a naming/registry-modeling gap, not a functional contradiction.
*Pinned:* `LLD-profiling-tool.md:45` vs `LLD-data-layer.md:76-88`.
*Suggested fix (do not apply):* clarify (follow-on) that the profiling provider is selected from `sessions.config` directly (per U8 §8), or add a profiling-tool registry to U2 in a follow-on if a first-class entity is wanted.

**Finding 5 — `session_state` blob vs. pure-normalized rehydration left unresolved across U4/U5/U2.** *(minor)*
U5 §3 stores SessionState's profile/schema_meta/context into "`sessions.config`/dedicated JSON columns (or a `session_state` blob column)" and flags the choice for a "U2 follow-on." U2 §4.3 `sessions.config jsonb` exists, but there is **no** `session_state` blob column nor dedicated profile/context JSON columns in the U2 schema as written, and `profile_snapshot` lives only per-draft (U2 §4.5/§4.6), not at session level for `schema_meta`/`context snippets`. This is openly flagged as deferred (not hidden), so it is a known open seam rather than a contradiction.
*Pinned:* `LLD-app-backend.md:32,37` vs `LLD-data-layer.md:102-103` (no session-level state/context columns).
*Suggested fix (do not apply):* resolve in the flagged U2 follow-on — add the column(s) or commit to normalized rehydration — before code consumes the persistence boundary.

**Finding 6 — D13 (license) consequence not surfaced in U8 packaging.** *(minor)*
D13 says the repo "carries a `LICENSE` + OSS-appropriate README/contribution notes" (open-source, Apache-2.0-proposed). U8 §2 "what ships" lists `README.md` but **no `LICENSE` file**, and never mentions licensing despite being the packaging/distribution doc for an explicitly open-source, customer-shippable artifact (D11/D13). Minor omission of a decision consequence in the doc where it most belongs.
*Pinned:* `LLD-packaging-deployment.md:22-34` (repo layout) — no `LICENSE`; D13 in `DECISIONS.md`.
*Suggested fix (do not apply):* add `LICENSE` (+ contribution notes) to the U8 repo layout / ship list in a follow-on.

---

## Cross-reference audit (spot-checked references → resolves?)

| Reference | Where cited | Resolves? | Note |
|---|---|---|---|
| D1 (core is library) | HLD §5.1, U4 §1 | yes | accurate |
| D4 (sanitized + AI Gateway) | U3 §5, U4 §5, U9 §3/§11 | yes | honored, no egress |
| D5 (context providers) | U2 §4.2, U4 §3.3 | yes | context ≠ profile boundary held |
| D7 (diff-first apply) | HLD §8, U6 §4 | yes | accurate |
| D8 (judge = confidence + eval) | U4 §3.5 | yes | accurate |
| D15 (MCP service vs UC fn) | U3 §2, U8 §8 | yes | accurate refinement of D3 |
| D17 (stateless SessionState) | U4 §2/§9, U5 §3 | yes | accurate, consistent |
| D18 (async + poll) | U5 §4, U9 §4 | yes | but introduces `running` (Finding 1) |
| D20 (per-iteration loop) | U6 §4 | yes | per-item apply checkpoint |
| D21 (apply re-check/idempotent) | U6 §4/§8 | yes | accurate extension of D7 |
| D22/D23 (delight/UX direction) | U9 throughout | yes | accurate |
| D25 (in-app profiling + .gotm excluded) | U8 §8/§9 | yes | accurate |
| U2 `session_status` enum | referenced by U4/U5/U9 | partial | `running` + `judging`/`gating` not in it (F1, F3) |
| U2 `draft_status` enum | referenced by U4/U5/U6 | partial | 5 states not in it (F2) |
| U3 `evidence_refs` | claimed by U9 §6 as "U4 §3.4" | yes | correctly produced by U4 Reasoner, not U3 |
| Q1–Q4 | HLD §11 | yes | all `answered`, mapped to D10–D13 |
| No citation ≥ D26 / phantom U/Q | all docs | yes | none found |

---

## Verdict rationale

**PASS-WITH-FINDINGS.**

- No **blocker**: every promised output exists and is complete (existence, spec-match, decision-fidelity all clean); every D#/U#/Q# citation resolves and is correctly attributed (no dangling/wrong reference that changes meaning); no contradiction that would *break* implementation. The architecture's hard seams — profiling contract → consumer, stateless SessionState → caller persistence, diff-first conflict-aware apply, sanitized-profile governance — are mutually consistent and faithful to D1/D4/D7/D17/D21.
- The findings are **major (2) / minor (4)** consistency gaps concentrated in one place: the **state vocabulary** between the frozen U2 enums and the per-item/in-flight states the consuming docs (U4/U5/U6/U9) rely on (`running`, `low_confidence`, `error`, `conflict`, `unsupported`, `failed`, and the `judging`/`gating` mislabel), plus two minor decision-consequence omissions (profiling-provider registry modeling; `LICENSE` in U8). None blocks code from starting on the *core* path, but Findings 1–2 will surface the moment the persistence layer and apply-status are implemented, because the schema as frozen cannot store the states those code paths produce.

Per PROTOCOL's gate, these become **new follow-on ledger units** (U2/U4/U6 amendments), not edits to the closed units. The gate may open (code may proceed) on this PASS-WITH-FINDINGS, with Findings 1 and 2 prioritized as fix units sequenced ahead of the persistence/apply code so the schema and the code agree before they are wired together.
