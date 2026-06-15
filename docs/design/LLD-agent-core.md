# geniefy-v3 — Low-Level Design: Agent Core

**Status:** draft (U4) · **Last updated:** 2026-06-11
**Inputs:** [`HLD.md`](HLD.md) (U1), [`LLD-profiling-tool.md`](LLD-profiling-tool.md) (U3) · **Decisions:** D1 (core is a UI-free library), D4 (FMAPI Claude behind AI Gateway, sanitized profiles), D5 (built-in + pluggable MCP context), D8 (custom loop + MLflow + one judge for confidence & eval), D2 (v1 interactive single-table), D17 (stateless core, serializable SessionState — added by this unit)
**Scope:** the reasoning engine — `Template`, `Profiler`, `ContextGatherer`, `Reasoner`, `Judge`, `Gate`, and the orchestrator that sequences them. *Out of scope:* persistence/UI (App backend = U5), review/apply (U6), profiling internals (U3), the Lakebase schema (U2), deployment (U8).

> Living doc. v1 (interactive single-table) is specified concretely; batch specifics and the Agent-Framework graduation are sketched and marked.

---

## 1. Purpose & principles

The agent core is a **UI-free Python library** (D1) that turns a table reference + template into reviewable drafts with confidence. The App wraps it for interactive use; a Job wraps the same library for batch — the only differences are who answers low-confidence questions and whether profiling samples.

Principles:

- **Thin reasoning layer.** Profiling (U3) and context retrieval (D5) are external providers; the core orchestrates and reasons, it does not run profiling SQL.
- **Deterministic orchestration, LLM only where judgment is needed.** The control flow is plain Python (D8); only `Reasoner` and `Judge` call the model.
- **Stateless across calls (D17).** The core holds no session in memory between turns. A **serializable `SessionState`** is passed in and returned; the caller (App/Job) persists it (Lakebase, U2). This is what makes pause/resume and history work — state is data, not a live coroutine.
- **Everything grounded, nothing speculated.** The Reasoner fills template fields only from provided evidence or marks them unknown; the Judge independently checks grounding.
- **Every model call traced** (MLflow 3, D8).

---

## 2. Public API

The caller drives a simple state machine. Two entry points; both take and return `SessionState`.

```python
class DocumentationOrchestrator:
    def __init__(self, config: RunConfig, providers: ProviderRegistry,
                 llm: LLMClient, tracer: Tracer): ...

    # First pass: introspect → profile → gather context → reason → judge → gate
    def run(self, target: TableRef, state: SessionState | None = None) -> StepResult: ...

    # Resume after the caller has collected answers (interactive only)
    def resume(self, state: SessionState, answers: list[Answer]) -> StepResult: ...

StepResult =
    NeedsInput(questions: list[Question], state)     # interactive: low-confidence items → ask
  | ReadyForReview(table_draft, column_drafts, state) # all items resolved or flagged
  | Failed(error: RunError, state)                    # unrecoverable; partials in state
```

`SessionState` (serialized to JSON, persisted by the caller) carries: `target`, `template_id`, `config`, the **profile snapshot**, **schema_meta**, gathered **context snippets**, current **drafts** (table + columns with confidence/status), **open_questions**, `mlflow_run_id`, and a `phase` cursor. On `resume`, expensive phases (profile, gather) are **not** re-run — only `Reasoner`/`Judge`/`Gate` re-run for the items the answers touch. Maps 1:1 to the U2 tables and the `sessions.status` enum.

---

## 3. Components

### 3.1 `Template`
Loads the YAML spec (HLD §7). Exposes: `table_fields` (required/recommended), `column_fields` (required/conditional with their applicability rules), `style` (max words, voice, forbidden patterns), and the **rubric** the Judge scores against. Versioned; stored in Lakebase `templates` (U2). The Reasoner and Judge are both parameterized by it — the template is the single definition of "good."

### 3.2 `Profiler`
Wraps the U3 `profile_table` contract. Responsibilities:
- **Select the provider** from `config`/registry (reference MCP service, UC-function MCP, or customer MCP — D15); the agent is provider-agnostic.
- **Introspect schema_meta** the profile doesn't carry: declared PK/FK constraints and partitioning (`information_schema.table_constraints` / `key_column_usage`) — structural facts that ground join-key claims.
- **Drive wide-table batching** (U3 §7): call `profile_table` with a `columns` subset (`config.profile_batch_size`, default ~50) and merge, deduping the table-level block.
- **Normalize** to the U3 §4.2 schema and pin `profile_schema_version`. The profile is already PII-sanitized by the provider (U3 §5); the Profiler does not see raw rows.
Output: `{ profile, schema_meta }` → into `SessionState`.

### 3.3 `ContextGatherer` (D5)
Uniform interface over providers; each returns typed `ContextSnippet`s the Reasoner can cite.
- **Built-in, always-on:** `LineageProvider` (`system.access.table_lineage` / `column_lineage` → upstream sources, downstream consumers, column-level lineage → FK/derivation hints); `QueryHistoryProvider` (`system.query.history`, parse recent SQL for JOIN/WHERE/GROUP BY patterns → grain, join keys, common filters, hot columns).
- **Pluggable MCP providers:** Genie spaces, Glean, Confluence, custom — registered/enabled in-app (U2 `context_providers`).
- **Budgeting:** each provider returns ranked snippets; the gatherer trims to `config.context_token_budget` (most-relevant first) so wide tables don't blow the prompt.
Output: `context: list[ContextSnippet]` (each tagged with source + which columns/relationships it informs) → into `SessionState`.

> Boundary with Profiler (D5): Profiler = what the *data* reveals (distributions, cardinality, key-likeness *signal*). ContextGatherer = what *usage/lineage/docs* reveal (actual join keys, FK targets, business meaning).

### 3.4 `Reasoner` (D4)
Fuses everything into drafts via one or more FMAPI Claude calls (behind the AI Gateway).
- **Prompt:** system = role + the `Template` spec + style + grounding rules ("fill each applicable field from the evidence or mark it `unknown`; cite the signal; never speculate"). User = serialized `schema_meta` + sanitized `profile` (this column batch) + relevant `context` snippets + (deferred) comment-library suggestions.
- **Structured output:** request a JSON schema (table_draft + per-column drafts) so every field maps to the template; each draft includes `rationale`, the `evidence_refs` it used, a `self_confidence`, and any `open_question` the model itself raises.
- **Column chunking:** columns processed in batches of `config.reason_batch_size` (default ~25–30); the table-level comment is produced once with table-level context, then reconciled with column findings. Bounds context + cost on wide tables (HLD §10).

### 3.5 `Judge` (D8 — the unification)
A **separate** model call that scores each draft against the `Template` rubric. Independent of the Reasoner (Rule-4 spirit: the maker doesn't grade itself in the same pass).
- **Input:** the draft + the rubric + the evidence the draft claims to rest on.
- **Output (`judge_scores`):** subscores in [0,1] for **completeness** (required fields filled), **specificity** (business meaning, not a name restatement), **grounding / no-hallucination** (every claim traces to provided evidence — flags e.g. "asserts FK→orders with no lineage support"), **template-conformance** (style/length/forbidden patterns); plus a weighted `overall` and a list of `issues`.
- `overall` is the **authoritative confidence** (not the Reasoner's `self_confidence`) and is also the **eval metric** reused by `mlflow.evaluate` over a golden set (deferred, D2/D8).

### 3.6 `Gate`
Routes each draft using the Judge score **plus hard signals**:
- **Hard-signal force-low** (→ `needs_input` regardless of score): ambiguous name (not in glossary/low semantic content) **and** no usage evidence **and** high null fraction; enum-candidate with opaque codes and no decode source; key-like cardinality ratio with no lineage/constraint to resolve a target.
- **Decision:** `keep` if `overall ≥ config.keep_threshold` (default 0.75) and no hard-signal trip; else `needs_input`.
- **Mode behavior:** *interactive* → `needs_input` items become `Question`s (a targeted, answerable prompt generated for each, e.g. *"`tier` holds 1/2/3 with no decode — what do these mean?"*) returned as `NeedsInput`. *Hands-free* → never ask; mark those drafts `low_confidence` with the reason and proceed to `ReadyForReview`.

---

## 4. Orchestration (the loop)

```
run(target):
  phase=PROFILING        Profiler.profile(target)            -> profile, schema_meta
  phase=GATHERING        ContextGatherer.gather(target, …)   -> context
  phase=REASONING        Reasoner.draft(batches)             -> drafts (+ self_confidence)
  phase=JUDGING          Judge.score(each draft, evidence)   -> judge_scores
  phase=GATING           Gate.route(drafts)                  -> keep | needs_input
     ├─ interactive & any needs_input → StepResult.NeedsInput(questions, state)   [persist & pause]
     └─ else                          → StepResult.ReadyForReview(drafts, state)

resume(state, answers):
  merge answers into evidence for the affected drafts
  phase=REASONING (affected only) → JUDGING → GATING
  (repeat until no needs_input, or caller stops)  → ReadyForReview
```

`phase` is persisted in `SessionState`, so a crash/pause resumes at the right step without redoing profiling or context. Orchestration phases map to `sessions.status` (U2 §6.1): `profiling → gathering_context → reasoning → awaiting_input ⇄ reasoning → ready_for_review`.

---

## 5. Model integration (D4)

- **`LLMClient`** wraps the FMAPI Claude endpoint **fronted by the AI Gateway** (endpoint name from `config.model_endpoint`; default a Claude serving endpoint with gateway guardrails on). Supports structured/JSON output, temperature ~0 for determinism, bounded `max_tokens`, retries with backoff.
- **Guardrails:** the gateway's PII guardrail is defense-in-depth; the profile is already sanitized at the provider (U3 §5). If the gateway masks/blocks content, the client surfaces it as a `warning` on the affected draft rather than failing silently.
- **No egress:** all generation stays in-platform (D4).

## 6. Observability (D8)

One MLflow **run per session** (`mlflow_run_id` stored in `SessionState`/`sessions`). Spans: `profile`, `gather_context`, `reason` (one per batch), `judge` (one per draft), `gate`. Logged: profile size + sampling flag, context snippet count + token budget use, token usage per call, `self_confidence` vs `judge.overall`, hard-signal trips, final statuses. The Judge is registered as an MLflow scorer so `mlflow.evaluate(golden_set)` reuses it unchanged (deferred harness).

## 7. Configuration (`RunConfig`)

`mode` (interactive|batch) · `sample` policy (passed to U3; auto in App, full in Job) · `keep_threshold` (0.75) · `profile_batch_size` (50) · `reason_batch_size` (25) · `context_token_budget` · `model_endpoint` · `template_id` · `enabled_providers` (snapshot of U2 `context_providers`). Everything is parameterized — no hardcoded workspace/catalog/endpoint (D12).

## 8. Errors & edge cases

| Condition | Behavior |
|---|---|
| Profiling `permission_denied` / `timeout` (U3 §9) | `Failed(error, state)`; surface the missing grant; never fabricate a profile. |
| Empty table | Proceed: structural comments (purpose/grain from schema+context); distribution-dependent fields marked `unknown`; many items gate to `needs_input`/`low_confidence`. |
| LLM call fails / times out | Retry (backoff); on persistent failure, mark the affected batch's drafts `error`, keep other batches, return `ReadyForReview` with partials flagged. |
| Wide table | Batching (§3.2/§3.4) bounds each call; merge per draft. |
| Context provider down (MCP) | Skip that provider, record a warning; built-ins still run. Degraded, not failed. |
| Resume with answers for already-`keep` items | No-op for those; only re-reason the items the answers target. |

## 9. Interfaces to other units

- **U2 (data layer):** `SessionState`, drafts, questions serialize into `sessions` / `*_drafts` / `session_messages`. The core never writes Lakebase itself — the caller persists (D17).
- **U3 (profiling):** consumes `profile_table`; `Profiler` is the only component that calls it.
- **U5 (app backend):** owns the HTTP/session lifecycle, calls `run`/`resume`, persists `SessionState` between turns, renders `Question`s, collects `Answer`s.
- **U6 (review/apply):** consumes `ReadyForReview` drafts (current-vs-proposed + confidence) for diff/edit/approve/apply.

## 10. Deferred / sketched

- **Comment-library injection** — feed approved library entries (U2 `comment_library`) into the Reasoner prompt as suggestions for matching columns (the quality flywheel, HLD §5.2).
- **Batch-mode specifics** — concurrency across tables, per-table run isolation, rate-limit coordination via the gateway.
- **Agent-Framework graduation (D8)** — repackage the core as a deployed Mosaic AI Agent Framework endpoint when batch is added; the stateless `SessionState` design (D17) makes this a wrapper change, not a rewrite.
- **Cross-column reasoning** — composite keys / functional-dependency hints once U3 surfaces them.
