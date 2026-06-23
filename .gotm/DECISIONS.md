# Decisions — geniefy-v3

> Append-only ADR ledger. Never edit a prior entry. If a decision is reversed, append a new D# with `Status: superseded by D<n>`.

---

## D1 — Agent core is a standalone library; the App is a wrapper

**Date:** 2026-06-11
**Scope:** project
**Status:** locked

**Context.** The tool must run both interactively (a person refining comments for one table) and hands-free (profile many tables on a schedule). Building one monolith for the App couples the reasoning engine to a UI it doesn't need.

**Decision.** Build the agent as a UI-free Python library (Profiler · ContextGatherer · Reasoner · Judge · Gate · Template). The Databricks App wraps it for interactive use; a Databricks Job wraps the **same** library for batch. One engine, two front-ends.

**Consequences.** Hands-free mode is "the App backend minus the UI." Forces a clean API on the core and keeps the engine testable in isolation.

---

## D2 — v1 nails the interactive single-table loop end to end

**Date:** 2026-06-11
**Scope:** project / scope
**Status:** locked

**Context.** Tempting to chase batch scale or CI/CD first; both are easier once the core works. The hard, value-defining part is producing *correct* comments and a review/apply UX a human trusts.

**Decision.** v1 = one table: profile → context → draft → confidence-gated questions → review/edit → store → apply to UC. Deferred: Job/batch mode, approval → export-as-code → CI/CD, rich comment-library reuse, Genie-artifact generation (sample queries/synonyms/instructions), expanded eval harness.

**Consequences.** Sets the unit sequence (foundation/design → interactive loop → review/apply). Batch and governance ride on top later without rework, given D1.

---

## D3 — Profiling runs through pluggable MCP tools / UC functions

**Date:** 2026-06-11
**Scope:** project
**Status:** locked

**Context.** Profiling logic (and the sampling decision for huge tables) should be governed, reusable, and swappable per customer — not SQL embedded in the agent.

**Decision.** The agent calls a `profile_table(...)` tool backed by a UC function (exposed via the UC-functions MCP server) or a customer-provided MCP server. App mode passes a `sample_size` (samples big tables); Job mode passes none (full scan, any size). The agent stays a thin reasoning layer over the profile the tool returns.

**Consequences.** Profiling capability is configurable infrastructure, not agent code. Sampling/cost policy lives in the tool. Adds an MCP/UC-function contract to design (unit U3).

---

## D4 — Generation via FMAPI Claude behind Unity / AI Gateway; only sanitized profiles leave

**Date:** 2026-06-11
**Scope:** project
**Status:** locked

**Context.** Schema and data samples can be sensitive; sending raw rows to an external model is a non-starter for customer tables.

**Decision.** Generate with Claude via the Databricks Foundation Model API, fronted by the AI Gateway with PII guardrails (detection/masking), request logging, and rate limits. The profile sent to the model is sanitized — aggregates, top-K values, patterns, masked samples — never raw sensitive rows.

**Consequences.** No data egress beyond the governance boundary. AI Gateway rate limits also protect batch fan-out. Profiler must emit a sanitized profile shape.

---

## D5 — Context = built-in lineage + query history, plus pluggable MCP context providers

**Date:** 2026-06-11
**Scope:** project
**Status:** locked

**Context.** Profiling alone reveals structure/distributions but not business meaning. Real grain, join keys, and usage come from how the table is actually queried; business definitions come from docs/glossaries.

**Decision.** Built-in, always-on context: UC lineage (`system.access.table_lineage` / `column_lineage`) + query history (`system.query.history`, parsed for joins/filters/aggs). Pluggable, configured in-app: MCP context providers (Genie spaces, Glean, Confluence, custom). The ContextGatherer treats built-ins and MCP providers uniformly.

**Consequences.** Quality depends on context, not just profiling. Provider registration/config becomes a first-class app feature and a Lakebase entity.

---

## D6 — Lakebase is the working store

**Date:** 2026-06-11
**Scope:** project
**Status:** locked

**Context.** Sessions need pause/resume + history; drafts need to persist for review and reuse; the comment library and audit trail need durable storage with transactional updates.

**Decision.** Use Lakebase (managed Postgres) attached as an App resource as the working store: sessions + conversation, table/column drafts, comment library, context-provider config, templates, and an audit log.

**Consequences.** OLTP access patterns (per-session reads/writes, status transitions) fit Postgres. Defines the data-layer LLD (unit U2). Git becomes the eventual *source of truth* for applied comments via the deferred CI/CD export (D7).

---

## D7 — Apply is a separate, diff-first action; CI/CD export is deferred

**Date:** 2026-06-11
**Scope:** project
**Status:** locked

**Context.** Comments must never silently clobber curated human text, and an org may want comments to flow through code review before touching production tables.

**Decision.** Generation never writes to UC. A separate in-app **Apply** action writes `COMMENT ON TABLE` / `ALTER TABLE … ALTER COLUMN … COMMENT` via a SQL warehouse, always showing a diff vs. existing comments first, and records an audit entry. An optional **approval → export-as-code (SQL/DDL or declarative YAML) → CI/CD** path is deferred to post-v1; Lakebase is the working store, git the deployed source of truth.

**Consequences.** Two distinct flows (generate/store vs. review/apply). Draft records carry current-vs-proposed + status lifecycle. Applying requires `MODIFY` on the table for the acting identity.

---

## D8 — Lightweight custom orchestration + MLflow tracing; one judge for confidence and eval

**Date:** 2026-06-11
**Scope:** project
**Status:** locked

**Context.** The interactive loop (confidence-gated questions, pause/resume) fits a controllable custom loop better than a request/response serving endpoint. But we still want observability and a quality metric early.

**Decision.** v1 uses a lightweight custom orchestration in the App backend (calls FMAPI + MCP tools directly), instrumented with MLflow 3 tracing. A single LLM-judge scoring drafts against the template rubric (completeness · specificity · grounded-in-profile / no hallucination) produces **both** the per-item confidence that gates interactive questions **and** the quality metric for `mlflow.evaluate`. Graduate the core into a deployed Mosaic AI Agent Framework endpoint when batch mode is added.

**Consequences.** Lean v1, eval-ready. Hard signals (no usage history + ambiguous name + high null rate) augment the judge to force-low confidence. Judge is a core component to design (unit U4).

---

## D9 — GOTM orchestration lives in `.gotm/`; produced assets live in the main folder

**Date:** 2026-06-11
**Scope:** project / repo organization
**Status:** locked

**Context.** GOTM drives the build but shouldn't clutter or collide with the produced code. The user asked to keep all GOTM docs in a dedicated folder and the working repo clean.

**Decision.** All GOTM machinery (`PROTOCOL.md`, `LEDGER.md`, `DECISIONS.md`, `QUESTIONS.md`, `audits/`) lives in `.gotm/`. Produced assets live in the main folder: design docs in `docs/design/`, code at the repo root later. **One exception:** a thin `CLAUDE.md` stays at the repo root because Claude Code only auto-loads root `CLAUDE.md` across sessions — it is the bridge into `.gotm/PROTOCOL.md`.

**Consequences.** Clean separation of orchestration vs. output. `.gotm` is hidden to keep the root visually clean. Design-doc and audit paths in the ledger are absolute-from-root (`docs/design/...`, `.gotm/audits/...`).

---

## D10 — Mission ratified

**Date:** 2026-06-11
**Scope:** mission
**Status:** locked

**Context.** The mission was agent-drafted from the kickoff discussion and routed to Q1 for human ratification (Mission-layer per the ladder).

**Decision.** Human confirmed the mission verbatim: *"An agent, delivered as a Databricks App, that turns any Unity Catalog table into an AI/Genie-ready asset by profiling its data and usage context and generating reviewable, template-conformant table and column comments, with a governed path to apply them to Unity Catalog."*

**Consequences.** Mission is locked; it frames all scope. Closes Q1.

---

## D11 — Positioning: a shippable demo, hardened by the customer for prod

**Date:** 2026-06-11
**Scope:** project / scope
**Status:** locked

**Context.** Q2 asked who operates the tool and at what quality bar.

**Decision.** geniefy-v3 is a **shippable demo** for both internal (Databricks FE/SAs) and external customers. Customers receive a working demo they **harden and move to production themselves**. The bar is "demonstrable, clean, well-documented, easy to extend" — not enterprise-hardened (no HA / fine-grained RBAC / multi-tenant in v1).

**Consequences.** Reinforces lean v1 (D2). Auth stays demo-simple (app service principal, optionally on-behalf-of). Favor strong defaults + documented extension points over hardening. Closes Q2.

---

## D12 — Distribution via Databricks Asset Bundle from GitHub; dev on FE-VM; fully parameterized

**Date:** 2026-06-11
**Scope:** project
**Status:** locked

**Context.** Q3 asked for a target workspace + golden set; the human reframed it as a deployment model.

**Decision.** The solution **deploys via a Databricks Asset Bundle (DAB) from a GitHub repo** into any workspace. Developed/tested on **FE-VM** workspaces; customers clone and deploy into their own. Everything is **parameterized** — no hardcoded workspace, catalog, schema, warehouse, Lakebase instance, or model endpoint. Dev/demo data uses **Databricks sample datasets** (e.g., `samples.tpch`, `samples.nyctaxi`); a dedicated golden eval set is deferred with the eval harness (D2, D8). Documented deployment **prerequisites**: Lakebase enabled, FMAPI Claude + AI Gateway available, a SQL warehouse.

**Consequences.** Adds a first-class **Packaging & Deployment** concern → new unit **U8** (DAB, config, README deploy steps). Config-as-parameters threads through every component. Closes Q3.

---

## D13 — License & sharing: open-source, customer-shippable

**Date:** 2026-06-11
**Scope:** mission / license
**Status:** locked

**Context.** Q4 asked the sharing model and any delivery anchor.

**Decision.** **Open-source, customer-shippable.** Proposed license: **Apache 2.0** (permissive — lets customers harden and productionize), swappable to the Databricks License if distributed via official Databricks demo channels (e.g., dbdemos). No fixed delivery anchor/date.

**Consequences.** Repo carries a `LICENSE` + OSS-appropriate README/contribution notes. Closes Q4. Minor non-blocking open item: confirm Apache 2.0 vs. Databricks License.

---

## D14 — Harden the protocol with operational anti-drift safeguards

**Date:** 2026-06-11
**Scope:** project / process
**Status:** locked

**Context.** A GOTM-health review surfaced that the two erosion modes — **silent work** (acting without writing back) and **quiet edits** (changing a frozen artifact instead of appending) — were stated as rules but had no operational *catch*. They relied on agent memory, which does not survive session boundaries.

**Decision.** Added an **"Anti-drift safeguards (operational)"** section to `PROTOCOL.md` — a **pre-edit check** (stop before editing a `done` unit's output or a prior decision/question entry), a **write-back gate** (unit work + ledger write-back in the same turn), **done-means-written** (verify the output exists before marking done), and a **turn-end self-check**. Surfaced the two critical invariants in the auto-loading root `CLAUDE.md` so they are in context every session. `PROTOCOL.md` / `CLAUDE.md` / `README.md` are **living governance docs, not unit outputs**, so editing them is explicitly in-bounds under pre-edit check rule #3.

**Consequences.** The discipline is now checkable per-action and per-turn, not merely aspirational. Optional next level — a deterministic `PreToolUse` hook that blocks `Edit`/`Write` against any path the ledger marks as a `done` unit's output — is offered but not yet wired (would move enforcement from "agent follows the doc" to "harness refuses the action").

---

## D15 — Profiling reference provider is an MCP service; UC-function path is limited (refines D3)

**Date:** 2026-06-11
**Scope:** project (refines D3)
**Status:** locked

**Context.** D3 said profiling runs through "UC function / MCP tool." Designing the U3 contract surfaced a constraint: a UC **SQL** function can't run dynamic SQL over an arbitrary table's columns, and a UC **Python** UDF runs in a per-row sandbox without a Spark/SQL session — so neither can *generically* profile any table.

**Decision.** The **reference profiling provider is a small service exposed as an MCP server** that generates and runs profiling SQL against a SQL warehouse (Statement Execution API / SQL connector) and returns the sanitized profile. The **UC-function path remains valid for the subset** expressible without dynamic SQL — returning cached `ANALYZE`/`information_schema` stats or templated profiling of a known table — reachable via the managed UC-functions MCP endpoint `…/api/2.0/mcp/functions/{catalog}/{schema}`. Both paths implement the identical `profile_table` contract (U3 §4); the agent cannot tell them apart.

**Consequences.** Does not supersede D3 — refines it. Affects U8 (deployment must bundle a profiling service, not just a UC function) and U4 (the Profiler wraps either provider behind one method). Custom/customer MCP providers conform to the same contract (D11 hardening path).

---

## D16 — Wired the immutability guard hook (implements D14's enforcement level)

**Date:** 2026-06-11
**Scope:** project / process (implements D14)
**Status:** locked

**Context.** D14 hardened the protocol with doc-level safeguards but noted that the *deterministic* version — a harness-level block — was the strongest form and was not yet built.

**Decision.** Built and wired a `PreToolUse` hook on `Edit|Write|MultiEdit`: `.gotm/hooks/gotm-immutability.py` parses `LEDGER.md`, and if an edit targets a `done` unit's frozen output it returns a `deny`. Registered in committed `.claude/settings.json` (team-wide, ships with the repo per D12). Robustness: project root derived from the script's own location (clone-portable); deny via stdout JSON not exit code (so `|| true` can't mask deny and a missing script fails *open*, not blocking all edits); fail-open on exception. Validated by pipe-test (6 cases), `jq` schema check, and end-to-end run of the exact wrapped command.

**Consequences.** Done-unit outputs are now harness-enforced, not memory-enforced. **Activation caveat:** a newly created `.claude/settings.json` is not hot-loaded mid-session — it needs `/hooks` or a restart to activate in the *current* session (verified: a proof edit against `HLD.md` still reached the Edit tool this turn); new sessions auto-load it. Distribution note for U8: decide whether the customer-shipped demo includes `.gotm/` + this dev hook or strips them. Reference implementation + the activation caveat were folded back into `docs/GOTM-FEEDBACK.md` (Appendix B) for the framework.

---

## D17 — Agent core is stateless across calls; a serializable SessionState is passed in/out

**Date:** 2026-06-11
**Scope:** project (refines D1, enables D8 interactive loop)
**Status:** locked

**Context.** v1's interactive loop needs pause/resume + history (D2), and the core must also run head-less in a Job (D1). Holding a live in-memory session (coroutine) in the core would couple it to a single process and make pause/resume across HTTP turns awkward.

**Decision.** The agent core holds **no session state in memory between calls**. The orchestrator exposes `run()` / `resume()`, each taking and returning a **serializable `SessionState`** (profile snapshot, schema_meta, context, drafts, open questions, phase cursor, mlflow_run_id). The **caller (App/Job) owns persistence** — it writes `SessionState` to Lakebase (U2) between turns. On `resume`, expensive phases (profile, gather) are not re-run; only the items touched by new answers are re-reasoned.

**Consequences.** Pause/resume and history fall out naturally (state is data, not a process). Constrains U2 (must persist `SessionState` shape ↔ `sessions`/`*_drafts`/`session_messages`) and U5 (rehydrate between HTTP calls). Makes the future Agent-Framework graduation (D8) a wrapper change, not a rewrite.

---

## D18 — App backend runs the orchestrator async (background task + DB-backed status + polling); batch goes to a Job

**Date:** 2026-06-11
**Scope:** project (formalizes the App-first / Jobs-for-batch compute split)
**Status:** locked

**Context.** Profiling + multiple LLM calls take seconds-to-minutes, but Databricks Apps have short HTTP request timeouts. A synchronous request would time out. (Earlier kickoff answer: App-first; Jobs only for "profile N tables.")

**Decision.** Single-table interactive runs execute as an **in-process background task** in the App backend: `POST /run` returns 202 and flips `sessions.status`; the worker drives `orchestrator.run/resume`, persisting status + partials to Lakebase per phase; the **frontend polls** `GET /sessions/{id}` until `awaiting_input` / `ready_for_review` / `failed`. **At most one active run per session** (guarded by status). **Batch / large runs delegate to a Databricks Job** running the same core (deferred, D2); the App is then launcher + viewer. Live-progress streaming (WebSocket/SSE) is a deferred enhancement.

**Consequences.** Frontend (U6) is poll-based in v1. A watchdog marks stale `running` sessions `failed` after a timeout (recoverable via re-run since state is persisted, D17). Deployment (U8) must size App workers for in-process background work and wire the Job for batch.

---

## D19 — Hardened GOTM for crash / non-resume resilience (no context loss, ever)

**Date:** 2026-06-11
**Scope:** project / process (extends D14)
**Status:** locked

**Context.** The write-back gate (D14) prevents *silent work* at clean turn-ends, but GOTM's headline promise — no context loss across session boundaries — must also hold under *accidental* ends (crash, closed terminal, killed process) with **no resume**. Two gaps remained: a mid-turn crash after writing an output but before the ledger update, and the absence of a cold-restart procedure to detect/heal that drift. Reinforced by the human: *"the ledger is your friend, use it to the fullest."*

**Decision.** Added to `PROTOCOL.md`: (1) a **transcript-independence invariant** — on-disk state alone must reconstruct context, never hold decision-relevant state only in chat, and `LEDGER.md → Recent updates` is the **recovery log** (kept rich enough to convey *where we are and why*); (2) **crash-safe write ordering** — mark a unit `in_progress` before producing output, then `done` + decisions after; (3) a **session-start reconciliation** step (now checklist step 4) that compares ledger ↔ disk and heals drift (done-but-missing-file → reopen; file-for-non-done-unit → finalize or supersede; `in_progress` → resume), logging findings to Recent updates. Surfaced a reconcile-on-start bullet in the auto-loaded `CLAUDE.md`. Folded the same into `docs/GOTM-FEEDBACK.md` (finding G10 + Appendix C) for the framework.

**Consequences.** GOTM now self-heals on a cold (non-resume) restart and bounds the mid-turn-crash window — the guarantee is **no *unrecoverable* context loss** (a crash during the ledger write itself still exists, but its half-state is always detectable and healable on restart). Going forward, units are marked `in_progress` before their output is produced. `PROTOCOL`/`CLAUDE`/`README` remain living governance docs, so editing them is in-bounds.

---

## D20 — Bound crash loss by sizing units to their loop (heavy / iterative work)

**Date:** 2026-06-11
**Scope:** project / process (extends D19, reinforces Rule 2)
**Status:** locked

**Context.** D19 left a residual sub-second window (a crash *during* the ledger write itself). The human noted that because the prior step is logged we lose little, and that heavy context tasks should be **split down to each loop**.

**Decision.** For heavy/iterative units (looping over columns, tables, or batches), bound worst-case loss to a single iteration by either **(a) splitting** into per-iteration atomic units (preferred when iterations are independent, individually meaningful outputs — each completion is a ledger write) or **(b) checkpointing per iteration** into the working state/ledger so reconciliation resumes at the last completed iteration (preferred when iterations merge into one output — the working store is the checkpoint). Pick finer granularity as per-iteration cost rises. Added to `PROTOCOL.md` → Resilience and `GOTM-FEEDBACK.md` (G10 + Appendix C).

**Consequences.** Interprets D17's "persist after every step" to include **per-batch checkpoints** in the U4 orchestrator (column batches; batch-mode per-table). U4's output is frozen (`done`) — if this materially changes its design it lands in a **follow-on unit, not an edit**. Reinforces Rule 2 (atomic units) for crash-resilience, not just legibility.

---

## D21 — Apply re-checks the live comment, is conflict-aware and idempotent per item (extends D7)

**Date:** 2026-06-11
**Scope:** project (extends D7)
**Status:** locked

**Context.** D7 made apply diff-first and "never silently clobber." But the existing UC comment can change between profile-time and apply-time; writing the profile-time diff blindly could overwrite that change.

**Decision.** At apply time, **re-read each target's live UC comment** and re-diff against the stored `current_comment`. If it changed since profiling → **conflict**: do not write, surface both versions, require explicit re-confirmation (optimistic concurrency). Comment text is written via **escaped/bound DDL** (`COMMENT ON` / `ALTER COLUMN`), never string-interpolated. Apply is **per-item and idempotent** (proposed == live → no-op) and **checkpoints per item** (each column flips its own `status`/`applied_*` + an `audit_log` row), so partial failures/crashes leave precise per-item state and are resumable (per D20).

**Consequences.** No silent clobber even under concurrent edits; partial apply is safe and resumable. Constrains U2 (per-item `applied_*` + audit) and U5 (per-item warehouse execution). Revert/rollback uses `audit_log.before` (deferred).

---

## D22 — Delightful, creative UX is a first-class goal (it's a demo)

**Date:** 2026-06-11
**Scope:** project / quality bar (refines D11)
**Status:** locked

**Context.** geniefy-v3 is a shippable demo (D11) meant to *wow* internal and external audiences and sell the "make your table AI/Genie-ready" value. The human asked for the UX to be **delightful and creative**, not merely functional — and gave explicit license to be creative.

**Decision.** UX delight is a **first-class design goal alongside correctness**. Invest in a cohesive design language and the demo's "wow" moments — a narrative *agent-at-work* view (not a spinner), confidence-as-visual-language, a satisfying *undocumented → AI-ready* transformation, conversational interactive Q&A, a celebratory apply with a **Genie payoff** (a sample NL question that now resolves), and personality in micro-copy — all within demo scope (not enterprise hardening). Designed in a dedicated unit **U9**; the functional review/apply UX (U6) is *elevated* by U9's design language, not redefined.

**Consequences.** Adds unit **U9** (UX & delight LLD); U7's audit broadened to include it; later frontend code must honor the design language. Refines D11 — which set the bar at "clean/demonstrable"; D22 raises the UX axis to "delightful."

---

## D23 — UX direction: data-viz-forward; three centerpieces (watch-it-think · glow-up · explainability)

**Date:** 2026-06-11
**Scope:** project / UX (implements D22)
**Status:** locked

**Context.** D22 made delight a first-class goal; the human chose the concrete direction from the U9 options.

**Decision.** Vibe = **data-viz-forward** (the profile *is* the visual). Three centerpiece moments: (1) **"watch it think"** — live narration of the real run phases; (2) **undocumented → AI-ready glow-up** + a readiness meter; (3) **"why it decided this"** explainability — evidence viz + rationale + Judge subscores. **Confidence is a consistent visual language** throughout. The **Genie-payoff panel** and **column trading-card flips** are **supporting/deferred**, not centerpieces. **Honesty guardrail:** narration and explainability reflect *real* backend events and the *actual sanitized* evidence (D4) — never fabricated polish (a technical audience punishes fakery).

**Consequences.** Pins the UX language that frontend code must honor and that U7 audits. Explainability relies on U4 already surfacing `evidence_refs` + `rationale` + Judge scores, and on the sanitized profile (D4). Designed in U9.

---

## D24 — Operationalized independent audit gates (Rule 4 made real)

**Date:** 2026-06-11
**Scope:** project / process
**Status:** locked

**Context.** Rule 4 named the audit principle and PROTOCOL noted audits need "independent context," but we had only a placeholder audit unit (U7) and had been deferring mechanical audits to human review. The human flagged that **audit agents must differ from working agents** for a fair assessment — otherwise the author rubber-stamps its own work.

**Decision.** Added an **"Audit gates"** section to `PROTOCOL.md` operationalizing Rule 4: (1) **independence** — audits run by a *different* agent via a **dispatched subagent** with fresh, bounded context (inputs + output + spec only, never the authoring conversation); (2) a concrete **checklist** (existence · spec-match · cross-reference integrity · internal consistency · decision fidelity); (3) **verdicts** (`PASS` / `PASS-WITH-FINDINGS` / `FAIL`) written to `.gotm/audits/`; (4) **the gate** — code/downstream units don't start until the covering audit records PASS, and findings become new fix units (never silent edits). Surfaced in `CLAUDE.md`. **U7 will be executed by a dispatched independent subagent, not inline.**

**Consequences.** The full design set (U1–U6, U8–U9) gets an independent audit (U7) before any code. Interim "folded into human review" deferrals remain valid as *delays*, but U7 is the real gate. Enforcement is currently procedural; a future `PreToolUse` gate hook (block code-unit edits while a covering audit is absent/FAIL) is noted as the deterministic upgrade. Recorded as framework feedback (G11).

---

## D25 — Demo deployment packaging: in-app profiling by default; `.gotm/` + dev `.claude/` excluded from the deploy artifact

**Date:** 2026-06-11
**Scope:** project (refines D15)
**Status:** locked

**Context.** U8 packaging surfaced two choices: how to deploy the profiling provider for a one-command demo, and whether GOTM build tooling ships into the workspace.

**Decision.** (a) **Profiling for the demo** implements the U3 `profile_table` contract **inside the app backend** against the bound SQL warehouse — no separate service to stand up — while staying **pluggable** to an external/customer MCP profiling server or a UC function via config (refines D15 for demo deployability). (b) **`.gotm/` and dev `.claude/`** (the immutability hook, D16) are **dev-time tooling**: kept in the GitHub repo as build provenance but **excluded from the `bundle sync` deploy artifact** (never uploaded to the workspace).

**Consequences.** One-command demo deploy; pluggability preserved for customer hardening (D11). The immutability hook stays a contributor tool (inert for deploy-only customers). Affects the bundle's sync-excludes + the README prerequisites/notes.

---

## D26 — Pulled the updated GOTM plugin into the project (machinery sync)

**Date:** 2026-06-11
**Scope:** project / process
**Status:** locked

**Context.** The `gotm` plugin was updated (incorporating our G1–G11 feedback). The human said: pull it from there.

**Decision.** Reconciled the project's `.gotm/` machinery to the canonical plugin: (1) replaced `.gotm/PROTOCOL.md` with the refined template (mission preserved) — now includes the *CLAUDE.md auto-load* section, sanctioned audit deferral, the full *Audit gates* model with a ledger **`Audit` column** + `/gotm audit <Uxx>` + `PASS`/`PASS-FINDINGS`/`FAIL` over HIGH/MED/LOW severity tiers, and an *Off-mission artifacts* section; (2) updated the root `CLAUDE.md` to canonical (our produced-assets paths kept); (3) replaced `.gotm/hooks/gotm-immutability.py` with the **header-aware** version — required *before* adding the `Audit` column, since the old positional parser read Status as the last cell and would break; copied `.gotm/hooks/README.md`; (4) pulled the canonical **prompts** into `.gotm/prompts/` (`audit.md`, `session-start.md`, `subagent-dispatch.md`) so PROTOCOL's references resolve and `/gotm audit` has its prompt; (5) added the **`Audit` column** to the ledger and **backfilled** it from audit-001 — clean U1/U3 = `PASS`, U2/U4/U5/U6/U8/U9 = `PASS-FINDINGS`, U7 = `—`.

**Consequences.** The project now tracks audit state per-unit in the ledger and the gate is column-enforced; `/gotm audit` is available; prompt references resolve. Verdict naming aligns to `PASS-FINDINGS` (audit-001.md's title still reads "PASS-WITH-FINDINGS" — a frozen audit artifact, left as-is). The immutability hook is upgraded but, as before, only arms on a fresh session start.

---

## D27 — Design phase closed; build phase opens; code-unit granularity

**Date:** 2026-06-11
**Scope:** project / phase transition
**Status:** locked

**Context.** The design set (U1–U6, U8, U9) is complete and was **independently audited twice** — U7 (full set, PASS-FINDINGS) and U10's own audit (PASS-FINDINGS). All findings were resolved via amendments U10 (Audit-001) and U11 (U10-audit). Both amendments only *apply the independent auditors' explicit recommendations* (no new design), so re-auditing each amendment recursively is diminishing returns; they're human-reviewed and carry `Audit: pending` so the session-start gate-lint keeps them visible before the specific code consumes them.

**Decision.** Declare the **design/foundation phase CLOSED and consumable** (Rule 3 foundation gate satisfied) and **open the BUILD phase**. **Code-unit granularity:** per Rule 2 + D20, a code unit produces **one coherent, auditable artifact** — a single file or a tightly-scoped file group (e.g., a module + its test, or `migrations/<version>.sql`) — kept small so a crash/loss costs one unit. Code units get their own independent audits per *Audit gates* (now also: does it implement the design; do tests pass).

**Consequences.** Foundation → drafts transition. First build units defined (U12 migrations, U13 scaffold + DAB + LICENSE, U14 agent-core Profiler), more appended as we go. **Authoring code proceeds infra-free; deploying/testing requires an FE-VM workspace + Lakebase/FMAPI/warehouse (D12) — a human provisioning step at the deploy unit, not before.**

---

## D28 — Concrete deploy target: FE-VM `fe-vm-classic` (resolves the deferred workspace question)

**Date:** 2026-06-11
**Scope:** project / infra (makes D12 concrete; resolves the Q3 workspace deferral)
**Status:** locked

**Context.** The human provided an FE-VM deploy target. Verified the CLI profile and discovered the workspace resources.

**Decision.** Dev/demo deploy target = FE-VM workspace **`https://fevm-rd-classic.cloud.databricks.com`** via CLI profile **`fe-vm-classic`**. Confirmed resources → DAB `dev` defaults:
- **Warehouse:** `0336d1a2b47936b4` (Serverless Starter, auto-starts on query).
- **App-data catalog:** `rd_classic_catalog`, schema `geniefy`. **Demo documentation targets:** the `samples` catalog (`samples.tpch`, `samples.nyctaxi`) per D12.
- **Generation endpoint:** `databricks-claude-sonnet-4-6` (default; `databricks-claude-opus-4-8` available for max-quality comments) — FMAPI, in-platform.
- **Lakebase:** available in the workspace, **no instance yet** → provision a `geniefy` instance at the deploy unit.
- **AI Gateway:** FMAPI endpoints are governed/in-platform; the sanitized-profile-at-source (D4) remains the primary PII control; gateway guardrails are a deploy-time enhancement, not a blocker for the demo.

**Consequences.** Build is now deployable end-to-end on `fe-vm-classic`. The DAB (U13) carries these as `dev`-target defaults; `prod` stays parameterized for customers (D12). Deploy commands use `-p fe-vm-classic`. Provisioning the Lakebase instance + applying `migrations/001_init.sql` (U12) becomes a deploy unit.

---

## D29 — Lakebase instance provisioned on `fevm-rd-classic` (resolves D28's Lakebase deferral)

**Date:** 2026-06-12
**Scope:** project / infra (resolves the D28 open item)
**Status:** locked

**Context.** D28 recorded "Lakebase available in the workspace, no instance yet → provision a `geniefy` instance at the deploy unit." The human provisioned it.

**Decision.** A Lakebase (managed Postgres) instance now exists in the FE-VM workspace `fevm-rd-classic` — Lakebase project **`595f9dad-0f09-4f60-80bf-9897a8793415`** (`https://fevm-rd-classic.cloud.databricks.com/lakebase/projects/595f9dad-0f09-4f60-80bf-9897a8793415`). This is the concrete working store (D6) for the demo deploy target.

**Consequences.** Unblocks: (a) applying `migrations/001_init.sql` (U12) against live infra — closes U12's UNVERIFIED audit item — via new unit **U17**; (b) the eventual app bundle-deploy, which binds this instance as the App's database resource. The DAB's `lakebase_instance` variable default (`geniefy`) must match the actual provisioned instance/database name — **confirm the real instance + database name from the Lakebase project at apply time** (the URL exposes the project id, not the Postgres database name). Authoring stays infra-free; U17 is the first unit that touches live infra.

## D30 — App working-store database = `geniefy` (schema `geniefy`) on the Lakebase project

**Date:** 2026-06-12
**Scope:** project / infra (concretizes D6 on the D29 instance)
**Status:** locked

**Context.** `migrations/001_init.sql` (U12) creates **schema** `geniefy` but not a database — it must be applied *into* a database. The Lakebase Autoscaling default database is `postgres`, whose `public` schema is restricted. U17 had to pick a target database.

**Decision.** The app's working store (D6) is a dedicated Postgres database named **`geniefy`** on the Lakebase project (`projects/geniefy`, branch `production`), with the app objects in **schema `geniefy`** inside it. U17 created database `geniefy` and applied the migration there. The App backend (U5) connects to database `geniefy`, schema `geniefy`.

**Consequences.** Clean isolation from `postgres`/`public`. The DAB (U15/`databricks.yml`) currently parameterizes `lakebase_instance` and `schema` but not the **database name** — when the App's resource binding is wired (backend/deploy unit), add a `database` bundle variable (default `geniefy`) so customers can override. The `geniefy` schema is namespace-redundant with the database name but harmless (separate namespaces).

## D31 — Frontend stack: Vite + React + TS · Tailwind/shadcn · TanStack Query · visx + Recharts

**Date:** 2026-06-12
**Scope:** project / frontend (implements D22/D23; concretizes the "React frontend" named in U5/U6/U8)
**Status:** locked

**Context.** U5/U6/U8/U9 commit to a React SPA served by the FastAPI app, poll-based (D18), data-viz-forward (D23) — but no specific stack was ratified, and U9 deferred the component spec to the code phase. The human asked to go with the recommended stack and steered "delightful as well as data-viz, but do not go overboard."

**Decision.** Frontend stack = **Vite + React + TypeScript**, **Tailwind CSS + shadcn/ui**, **TanStack Query** (the poll loop, D18), and **visx for the bespoke viz primitives + Recharts for routine charts**. No global state lib. The built bundle is served by FastAPI (static mount); `vite build` runs in the deploy orchestrator before `bundle sync`. UX guardrail: delight via clarity + motion-with-meaning, a perf/accessibility budget, one hero moment per screen — **restrained, not overboard** (refines D22/D23). Designed in **U23** (`LLD-frontend.md`).

**Consequences.** Pins the frontend code-units' stack. Adds a `vite build` step to deployment (U24 §4 / U8 amendment). visx/Recharts become app dependencies. Frontend code lands after U5 backend; the `databricks-apps` skill / `databricks-apps-developer` agent assist the build.

## D32 — Cost predictability: per-loop token budgets + prompt summarization (NFR-B)

**Date:** 2026-06-12 · **Scope:** project / agent core (amends U4 §4/§5/§7) · **Status:** locked

**Context.** The human requires predictable cost: tokens defined/configured **per agent-loop level**, with the ability to **summarize the prompt if a loop iteration hits the limit**.

**Decision.** Each model-call type (`reason` per column batch, `judge` per draft, `summarize`) carries an **input-token budget** from config. A `TokenCounter` estimates the assembled prompt before sending (LLMClient); if over budget, a recorded **compaction pipeline** runs — trim context → summarize context → reduce profile verbosity → shrink the batch — until under budget, surfaced honestly in the UI. `RunConfig` gains `max_input_tokens_per_call`, `summarize_over_budget`, `summary_target_tokens`. Per-batch budget doubles as the D20 crash/cost bound. Designed in U24 §2.

**Consequences.** Predictable spend = bounded input tokens × known #calls. Adds a `PromptBudgeter`/`Compactor` to the agent core (built with LLMClient/Reasoner). Amends frozen U4 (delta in U24).

---

## D33 — `app.yaml` is the single runtime config surface (NFR-A)

**Date:** 2026-06-12 · **Scope:** project (amends U8 §6, U5; refines D12) · **Status:** locked

**Context.** The human requires the app be **fully configurable via `app.yaml`**.

**Decision.** Every runtime knob is a `GENIEFY_*` env var declared in `app.yaml`, sourced from bundle variables + resource bindings; the backend loads env once into a typed `AppConfig`+`RunConfig` and **fails fast** on missing/invalid required vars. Config that must change without redeploy (providers, templates) lives in Lakebase, edited via U5 endpoints; `app.yaml` carries bootstrap + infra config. Schema in U24 §1.

**Consequences.** Clear config contract for deploy + ops. Adds an `AppConfig` loader + startup validation (app-backend build). Amends frozen U8 (delta in U24).

---

## D34 — Pluggable MCP context providers via config + an invocation contract (NFR-C)

**Date:** 2026-06-12 · **Scope:** project (implements D5; extends U2 `context_providers`, U4 §3.3) · **Status:** locked

**Context.** The human requires adding MCP servers via config so the agent can discover context (Glean, Confluence, Atlassian, …).

**Decision.** The existing `context_providers` (`kind='mcp'`, `config` jsonb) is the registry; this locks the **registration schema** (`server_url`, `transport`, `auth` secret-scope ref, `tool_allowlist`, `query_template`, `max_snippets`, `token_budget`) and the **MCP invocation contract** for the ContextGatherer: connect → `list_tools` ∩ allowlist → invoke with a table-derived query → normalize to `ContextSnippet`s (source-attributed, budget-trimmed) → degrade if unreachable. Adding a provider is a config row, not code. Designed in U24 §3.

**Consequences.** Extensible context per D11 hardening. Auth via secret scope only (D4/D5). Adds an `McpContextProvider` client (ContextGatherer build) + a U5 provider-CRUD endpoint. A deeper U3-style contract LLD is an optional follow-on.

---

## D35 — Git → customer deploy via DAB + a shell orchestrator (NFR-D)

**Date:** 2026-06-12 · **Scope:** project (amends U8 §1/§3/§7/§11; refines D12) · **Status:** locked

**Context.** The human requires the whole solution deploy to a customer workspace from git; where DAB can't do everything, split components and orchestrate DAB + CLI with a shell script.

**Decision.** DAB stays the spine (app, `geniefy_setup` job, bindings, declarable grants). A thin idempotent **`deploy.sh`** orchestrates what DAB can't: preflight checks → `vite build` (U23) → ensure Lakebase project/branch/endpoint + `geniefy` DB (`databricks postgres …`) → `bundle deploy` → run migrations (job or APPLY.md path) → grants → print App URL. One command, re-runnable, from the cloned repo. Designed in U24 §4.

**Consequences.** True git→customer deployability without waiting for DAB to cover Lakebase/migrations/frontend-build. Replaces the manual migration step (U16/U22) once the deploy unit lands. Amends frozen U8 (delta in U24).

## D36 — App backend packaged as `src/geniefy_app/`; `app/` is the thin deploy entry

**Date:** 2026-06-12 · **Scope:** project / packaging (refines U8 §2 layout) · **Status:** locked

**Context.** U8 §2 sketched the backend living under `app/`. But `app/` is the Databricks App `source_code_path` (deploy artifact root) and isn't naturally importable as a Python package in tests; the agent core already lives in `src/geniefy_core/`. The App backend needs the same hermetic, unit-testable treatment (config loader, SessionStore, concrete transports, FastAPI routes).

**Decision.** The App backend is a proper package **`src/geniefy_app/`** (alongside `geniefy_core`), unit-tested via `PYTHONPATH=src` like the core. **`app/main.py`** stays the thin Databricks-App entry that imports `geniefy_app` to build the FastAPI app + mounts the built frontend (U23 §4). The deploy bundle ships `src/` + `app/` and puts `src` on the app's path (deploy unit / `deploy.sh`, D35).

**Consequences.** Backend units land in `src/geniefy_app/` (config, store, providers, api), each independently testable + auditable (D27). `app/main.py` (U13 scaffold) becomes a thin wrapper in the app-entry unit. Decomposes the U5 design into build units: **U43** config · **U45** SessionStore · transports · FastAPI app.

## D37 — Lakebase `dev` branch is the live integration-test target; `production` is the app/deploy DB

**Date:** 2026-06-12 · **Scope:** project / infra (extends D29/D30) · **Status:** locked

**Context.** The `geniefy` Lakebase project (D29) has two branches, each with an ACTIVE primary endpoint. The human directed: use the **dev branch** for live testing — non-production, so live round-trips don't risk prod data (resolving the prod-read permission concern raised at U17).

**Decision.** Live integration tests (e.g. the `SessionStore` round-trip, U45+) run against the **dev branch** endpoint `ep-curly-sunset-d2sz2asi.database.us-east-1.cloud.databricks.com` (`projects/geniefy/branches/dev/endpoints/primary`). The **production branch** endpoint `ep-blue-smoke-d2yetcmv...` (where U17 created the `geniefy` DB + applied the schema) is what the deployed App binds (or the customer's own, per D12). Both reachable via the same OAuth-credential flow (`migrations/APPLY.md`).

**Consequences.** Backend modules stay **hermetic** (DB connection injected, unit-tested with fakes); their live verification runs against dev. The dev branch may need the schema applied (it's a point-in-time branch of production); if absent, apply `migrations/001_init.sql` to dev as a one-time setup (same runbook as U17). Branches are isolated, so dev test data never reaches the prod branch.

## D38 — `template_id` seam: the spine carries the template *name*; `sessions.template_id` (uuid FK) is set only when resolvable

**Date:** 2026-06-12 · **Scope:** project / data (reconciles U27 spine with U2 schema) · **Status:** locked

**Context.** Live `SessionStore` round-trip on the dev branch (U47) surfaced a mismatch: `RunConfig`/`SessionState.template_id` is the template **name** (default `"default"`, matching `Template.name` / the seeded `templates.name='default'`), but U2's `sessions.template_id` is a **uuid FK → templates(id)**. Postgres rejected `"default"` as a uuid.

**Decision.** v1: `SessionStore` writes `sessions.template_id` as the value **only if it parses as a uuid, else NULL** (the column is nullable). The actual template reference is never lost — it lives in `config.template_id` inside the `session_state` jsonb snapshot (U10 F5), which `load` rehydrates from. Proper **name→uuid resolution** (look up `templates` by name at session creation and store the uuid) is deferred to the App's template-management unit; until then the normalized `sessions.template_id` FK is NULL and the jsonb is the source of truth.

**Consequences.** Unblocks live persistence without an FK violation; no data loss. Fix lands in **U48** (supersedes U45's `store.py`). A follow-on (App template management) resolves names→ids and backfills the FK. Found by live testing — the hermetic tests passed because they didn't exercise a real uuid column.

## D39 — Answer Q5: MCP providers + model configured via `app.yaml` (deploy-time); wire it for real

**Date:** 2026-06-12 · **Scope:** project (answers Q5; implements NFR-C; refines D33/D34) · **Status:** locked

**Context.** Human review caught that NFR-C was designed/schema'd but **not wired** (build_service ignored `config.mcp_providers`; no MCP session factory; no config surface). The human chose the surface: *"config more MCP servers via `app.yaml` so the agent can discover them at deployment."*

**Decision.** **`app.yaml` is the config surface (deploy-time, per D33)** — no runtime config UI for v1. MCP context providers are declared in `app.yaml` via **`GENIEFY_MCP_PROVIDERS`** (a JSON array of provider configs per D34/U24 §3); `AppConfig` parses them (already does), and **`build_service` constructs one `McpContextProvider` per config and adds them to the `ContextGatherer` at startup**, so the agent discovers + uses them at deploy. The **model (FMAPI) endpoint is likewise `app.yaml`-configured** (`GENIEFY_MODEL_ENDPOINT` → already wired). A **concrete MCP session factory** (connect via transport + a secret-scope token, D4/D5) is built (integration-verified, like the FMAPI/warehouse factories). The runtime provider-CRUD endpoints (U24 §3's alternative) are **explicitly deferred** — not needed for v1.

**Consequences.** Closes the NFR-C wiring gap. Implementing unit **U63**. Closes **Q5**. `app.yaml` surfaces the full `GENIEFY_*` config (incl. the model + MCP providers) so it is genuinely the single config surface. `databricks.yml` gets an `mcp_providers`/`model_endpoint` var feeding `app.yaml` at the deploy unit (U62).

## D40 — Per-provider `token_budget` is subsumed by the global `context_token_budget` (resolves U63-audit MED)

**Date:** 2026-06-12 · **Scope:** project (resolves the U63 audit MED; refines D34/U24 §3) · **Status:** locked

**Context.** The U63 audit found the D34/U24 §3 per-provider `token_budget` knob is **inert** — `McpContextProvider` consumes only `max_snippets`, and `ContextGatherer._trim` already enforces the global `context_token_budget` over the merged, ranked snippet set. The auditor offered two resolutions: honor it per-provider, or record that the global budget subsumes it.

**Decision.** The **global `context_token_budget`** (the gatherer's merge→rank→trim, `context.py:_trim`) is the single budget authority; the **per-provider `token_budget` is dropped** (not implemented). Per-provider volume is bounded by `max_snippets` (a count), and the global token trim governs prompt cost — a per-provider token budget is redundant complexity with no correctness benefit (snippets are trimmed *after* cross-provider relevance ranking, which a per-provider cap cannot do). The D34/U24 §3 schema field is superseded by this decision.

**Consequences.** Resolves the U63 audit MED with **no code change** — the behavior is already correct. No new unit. `McpContextProvider`'s config keys are `name · tool_allowlist · query_template · max_snippets`.

## D41 — Audits are unit-wise and by an independent subagent, always (human directive; tightens the audit gate)

**Date:** 2026-06-12 · **Scope:** project (governance; tightens PROTOCOL "Audit gates"; clarifies D27) · **Status:** locked

**Context.** The human directed: *"the audit should be unit-wise and independent subagent always."* This corrects two lapses this session: (a) four test units (U44/U46/U50) were stamped *"covered by the module's audit"* rather than each getting its own audit; (b) the frontend screens were audited in **one batched** report (`U59-U60.md`) instead of per unit.

**Decision.** **Every done unit gets its own independent audit, dispatched to a fresh subagent with bounded context (target + oracle + spec only) — one unit per audit.** No "covered-by-another-unit's-audit" stamps; no multi-unit batched reports; no self-audit. This applies to **fix units too**: a rec-applying fix (D27) still has its *output* independently audited — D27's "no recursive audit" is **clarified** to mean only that we don't re-audit the act of applying an auditor's own verbatim recommendation ad infinitum, not that the fix unit's output skips audit.

**Consequences.** (1) U44/U46/U50 are **re-audited individually** (their "covered-by" stamps superseded by their own reports). (2) The batched `U59-U60.md` is **split** — U59 and U60 each get their own audit/report. (3) U61 (never audited) gets its audit. (4) Every fix unit this turn (U29, U34, U64–U69) gets an independent unit-wise audit before U62 proceeds. Recorded to `GOTM-FEEDBACK.md`. Note: U45 is **superseded** by U48 (its output `store.py` is owned + audited under U48) — a superseded unit carries no separate audit, which is distinct from (and legitimate, unlike) a "covered-by" stamp.

## D42 — Evaluated AppKit (post-deploy); stay with the Python agent-core + FastAPI/React stack

**Date:** 2026-06-12 · **Scope:** project (architecture; revisits D1/D31; honest design-phase gap) · **Status:** locked

**Context.** The human asked (post-deploy) whether geniefy should use **Databricks AppKit** (https://developers.databricks.com/docs/appkit/v0/) — a TypeScript SDK for Databricks Apps with plugin-based resource access (SQL warehouse/UC), file-based typed queries, and built-in observability/caching/retry/telemetry. It abstracts much of the app-serving + resource-binding plumbing this project hand-wired (and debugged at deploy: Lakebase `PG*`, the serving-endpoint OpenAI client, `psycopg2`). **Honest gap:** U23 chose the Vite+React+TS+FastAPI stack (D31) without evaluating AppKit.

**Decision.** **Stay with the Python agent-core + FastAPI/React stack.** The load-bearing constraint is **D1/D17**: geniefy's value is the hermetic **Python** agent core (Profiler/Reasoner/Judge/Gate/Orchestrator), reused unchanged by the App *and* a deferred batch Job (D2). AppKit is **TypeScript-only** and cannot host that Python core; adopting it would force either a split architecture (AppKit TS app + a separate Python agent service) or a full rewrite that breaks D1/D2. The app-plumbing pain AppKit would ease is a **one-time deploy-wiring cost**, now mostly solved and codified in `deploy.sh` + this session's U77 fixes.

**Consequences.** No change to the current stack. AppKit is recorded as a **serious option to revisit IF** the app layer grows substantially and the agent core is split into its own Python service — at which point an AppKit TS app-serving/frontend layer calling that service is attractive (it would provide the observability/retry/resource-binding plumbing we hand-rolled). Logged so the choice is explicit, not accidental.

## D43 — Richer comments + per-phase output-token budgets

**Date:** 2026-06-12 · **Scope:** project (Enhancement R2 / amends U4 §3.1, §3.4) · **Status:** locked

**Decision.** Raise the template length caps (`table_comment.max_words` 120→500, `column_comment` 40→150; `forbid` rules kept) and give the Reasoner **per-phase output budgets** — `reason_table_max_tokens=20000`, `reason_column_max_tokens=2000` (RunConfig, `GENIEFY_*`-configurable) — instead of the shared `DEFAULT_MAX_TOKENS`. The Applier truncates-with-warning if a comment exceeds the UC length limit. Implementing: **U81**. See LLD-amend-003 §E1.

## D44 — Two-pass generation: table comment first, then columns grounded in it

**Date:** 2026-06-12 · **Scope:** project (amends U4 §3.4) · **Status:** locked

**Decision.** The Reasoner drafts the table comment first, then threads the **generated table comment into the column-batch prompt** as explicit context (purpose/grain/keys), rather than one all-in-one pass — smaller per-call context (cost) + better-grounded column comments. Reuses the existing table→columns split; no re-profiling. Implementing: **U82**. See §E2.

## D45 — Re-generate action (table or specific columns)

**Date:** 2026-06-12 · **Scope:** project (extends U6 review loop) · **Status:** locked

**Decision.** `POST /api/sessions/{id}/regenerate {targets|all}` → `Orchestrator.regenerate(state, targets)` re-reasons/judges/gates ONLY the named targets (reusing profile+context, D17), preserving the rest; async+poll (D18). UI: per-draft + table "Regenerate" buttons. Implementing: **U83** (backend), **U88** (UI). See §E3.

## D46 — Session history + comment library in Lakebase; generate-now-approve-later

**Date:** 2026-06-12 · **Scope:** project (extends U2/D6, U5) · **Status:** locked

**Decision.** Lakebase is the system of record (already persisting sessions/drafts, D6/D17). Add `SessionStore.list_sessions(...)` + `GET /api/sessions` (paginated history) and a **History** SPA view (open a past session → resume/review/apply) — which *is* generate-now-approve-later (E7), no new state. On apply, the Applier writes approved comments to the existing `geniefy.comment_library` (`upsert_library_entry`) for reuse; a **Library** view lists them (reuse-on-generation deferred). Implementing: **U84** (store+endpoints), **U87** (UI). See §E6/E7.

## D47 — LLM exponential backoff + jitter + 429 protection

**Date:** 2026-06-12 · **Scope:** project (amends U4 §5 / U32) · **Status:** locked

**Decision.** Harden `LLMClient` retries: treat **429/RateLimitError** (+ 5xx) as retryable, **full-jitter exponential backoff** (`random.uniform(0, base*2**attempt)`), honor `Retry-After` when present, raise the retry budget for rate limits (≈5, capped). Injected `sleep`+transport keep it hermetic. Ties to D4 (AI-Gateway limits) + NFR-B. Implementing: **U80**. See §E8.

## D48 — User identity + on-behalf-of (OBO) via Databricks Apps headers

**Date:** 2026-06-12 · **Scope:** project (amends U5/U6 apply-path + U2/U10 `audit_log` + D7/D21; ratified by human 2026-06-12) · **Status:** locked

**Decision.** geniefy reads the Databricks Apps reverse-proxy headers to (a) **attribute** each session + audit row to the real end user and (b) **apply UC comments on-behalf-of (OBO) the user**, so writes honor the user's own Unity Catalog grants — the principled fix for **E9** (the app SP lacks `MODIFY` on read-only/foreign catalogs like `samples`).
- **Identity (always present, no config):** `X-Forwarded-Email` / `X-Forwarded-User` / `X-Forwarded-Preferred-Username` → session `created_by` (E6 history) + `audit_log.actor` (D21, previously the SP). `X-Request-Id` carried into tracing.
- **OBO (opt-in):** `X-Forwarded-Access-Token` = the user's OAuth token → the **Applier** runs `COMMENT ON`/`ALTER COLUMN` via the Statement Execution API using the **user token**, not the SP. Requires a one-time **operator step**: App UI → *User authorization → +Add scope* → add `sql` (a UI scope, **not** an `app.yaml` key) — tracked with the other operator steps (postgres binding, SP grant) in `DEPLOY_VERIFY.md` + U78.
- **SP vs user split:** profiling, context-gathering, and LLM calls stay **as the SP** (warehouse + FMAPI); only the **apply write-path** + identity attribution use the user. Databricks Apps supports both models simultaneously.
- **Hermetic / local-dev:** headers exist only inside Databricks Apps, so a request-context layer takes them as inputs and defaults to a local/anon identity (no token) — tests + local dev need no proxy.
- **Fallback:** if OBO is not configured (no token header) or the user lacks `MODIFY`, apply degrades to a clear per-item error (U70 surfaces it) — never silently writes as the SP.

Implementing: **U90** (design LLD-amend-004) → **U91** (FastAPI request-context: extract `X-Forwarded-*`, thread identity+token into the service) → **U85** (apply OBO + surface) + **U84** (session `created_by`) + audit `actor`. See §E9.

**Refinement (2026-06-12, human — narrows the split).** The end-user identity is used for **ONE thing only: the UC apply write** (the `COMMENT ON`/`ALTER COLUMN` DDL against the target table, via the OBO `X-Forwarded-Access-Token`) **and that apply's `audit_log.actor`**. **Everything else runs as the app SP**, explicitly including:
- **Lakebase** working store — sessions / drafts / `audit_log` / `comment_library` (the SP self-OAuths; the user token is never used for Lakebase).
- **Profiler SQL** (warehouse Statement Execution) — SP.
- Context-gathering, **LLM** (FMAPI), session creation, and **run / resume / review** — SP.

So **session `created_by` = the SP, not the user** (revises A4): user attribution is recorded **only** on the apply audit row. `RequestIdentity` is therefore extracted **only at the `/apply` route** (U91), not threaded through run/answers/review. Supersedes LLD-amend-004 §A4's "session `created_by` ← user" (that design doc is frozen — **this decision-log refinement is authoritative**; A2/A5 in the doc are unchanged). U91/U84/U85 implement to this refinement.

## D49 — Lakebase: a fresh connection per call, never cached (human directive)

**Date:** 2026-06-13 · **Scope:** project (revises D11's deferred pool/refresh; supersedes U93's first cut, which cached + reconnected) · **Status:** locked

**Decision.** `SessionStore` opens a **new Lakebase connection for each operation and closes it** — no caching, no pooling, no reconnect-on-death. **Why (surfaced live, U89→U93):** Lakebase Autoscaling **scales to zero** when idle and the OAuth DB credential **expires (~1h, D11)**, so any cached connection goes stale → every later request 500s (`psycopg2.InterfaceError: connection already closed`, observed on `/api/library`→`/api/sessions`→`/api/run`). A per-call connection is always live and **eliminates the entire stale-connection failure class**; the per-call OAuth + connect overhead is acceptable for this interactive, low-QPS app. Implemented via a `_connection()` context manager over an injected `connect` factory (`build_service` → `_lakebase_connection`); tests inject a static conn (reused, not closed). Pooling can be revisited if throughput ever demands it.

Implementing: **U93**.

## D50 — LLM-suggested answers for needs-input questions (human directive)

**Date:** 2026-06-13 · **Scope:** project (extends U4 Gate/Reasoner + U6 review) · **Status:** locked

**Decision.** When the Gate routes a draft to **needs_input** (a clarifying question), geniefy pre-proposes a **suggested answer** so the operator accepts/edits rather than typing from scratch. After gating, the Orchestrator makes ONE batched LLM call (`Reasoner.suggest_answers(state, questions)`) that, for each open question, proposes a concise likely answer grounded in that column's profile + draft + the question text; the result is stored on `Question.suggested_answer` (nullable; **best-effort** — a failure leaves it `None` and never blocks the run). `Question.to_dict` carries it; the frontend `QuestionsPanel` pre-fills each answer box with the suggestion (fully editable). Interactive-mode only; one extra LLM call per gating when questions exist. Implementing: **U100**.

## D51 — Hands-off / schema-level batch generation + deferred review (human directive, R3)

**Date:** 2026-06-13 · **Scope:** project (net-new capability; extends D18 batch-via-Job, D17 stateless resume, D48 SP-vs-user split) · **Status:** locked · **Design:** `docs/design/LLD-amend-005.md` (U101)

**Decision.** geniefy gains a **hands-off mode**: point at a `catalog.schema`, and a **Databricks Job** (D18) enumerates every table (as the **app SP**, information_schema) and runs the agent core per table in a **non-interactive pass** that **generates + persists a session per table but NEVER writes to UC** — every apply remains **human-in-the-loop via OBO** (Q7, extends D48/D7). The hands-off pass still **produces clarifying questions and persists them as `awaiting_input`** (Q8) so a human can answer + **resume the loop later** in the app, reusing the D17 stateless-resume path unchanged (no pause/block during the batch — the Job moves to the next table). A new **`schema_runs` parent record** (Q9) groups the per-table sessions and tracks rollup counts (ready · needs-input · applied); the app **triggers the Job** via `POST /api/schema-runs {catalog, schema, filters}` (Q10) and lists/opens runs. Scope defaults to **all tables, skipping already-documented ones**, with an optional table-name filter (Q10). **Why:** scales geniefy from one-table-at-a-time to whole-schema documentation while preserving the governance invariant (no autonomous UC writes) and the existing resume mechanics. Implementing units: see U101 (design) → R3 build units.

**Refinement (2026-06-13, human, resolves Q3 + U101 audit notes).** The hands-off batch driver is a **new task/mode of the *existing* `geniefy_setup` Job** (option b) — **not** a new bundle resource — so the grant-safe `--code-only` redeploy path stays intact for our live dev app (no `bundle deploy` resource-reconciliation needed to add a job). Framing: a **fresh customer deployment** uses `bundle deploy` normally (grants provision fresh — nothing to wipe); the grant-preservation concern is specific to *our* iterative dev redeploys, which extending the existing job sidesteps. Build order: **LLD-amend-006 (U102) ships before LLD-amend-005 (U101).** Build units also fold the U101 audit deltas: extend **`SessionMode`** (not a new `RunMode`); migration `003` is `geniefy.`-qualified (`set search_path`, matching `001_init.sql`), adds the `'hands_off'` `session_mode` enum value, and uses a `uuid` PK for `schema_runs`.

## D52 — Library lifecycle (approved→applied→sunset) + reuse-on-generation (human directive, R3)

**Date:** 2026-06-13 · **Scope:** project (extends D9 comment_library / U55 / U84 apply-time upsert) · **Status:** locked · **Design:** `docs/design/LLD-amend-006.md` (U102)

**Decision.** `comment_library` becomes a governed **definition store**, not just an apply log. (1) **Status lifecycle** (Q4, migration `002`): a new `status` column — **`approved`** written when a draft is **approved/edited in review** (net-new; previously only apply wrote the library), upgraded to **`applied`** on a **successful** UC write (a failed apply stays `approved`), and **`sunset`** when manually retired. (2) **Soft sunset, revivable** (Q6): sunset is a soft state (kept for audit, **excluded from reuse + suggestions**) with a **revive** action back to `approved`/`applied`. (3) **One canonical entry per `(scope, match_key)`** (Q5): approvals upsert on the key — latest approved overwrites `canonical_comment`, `usage_count`++ (generic-name churn like `id`/`status` is handled by edit/sunset). (4) **Reuse-on-generation** (Q1): a `LibraryProvider` in the ContextGatherer does an **exact `match_key`** lookup (column name / table FQN), pulls `status ∈ {approved, applied}` ranked by `usage_count`, excludes `sunset`, and feeds the matches to the Reasoner as **suggestion-only** grounding ("previously-approved comments for columns named X: …") — the LLM still grounds in *this* table's profile (no blind copy); fuzzy/semantic matching is deferred. **Why:** turns every human approval into reusable, governed canonical wording → cross-table consistency. Implementing units: see U102 (design) → R3 build units.

**Refinement (2026-06-13, human, resolves U102 audit MEDIUM).** `revive` restores a sunset entry to **`approved`** (never directly to `applied`); a subsequent successful apply re-upgrades it to `applied` via the normal path. **No `applied_at` marker / no schema change** beyond migration `002`'s `status` + `sunset_at` + `sunset_by`.

## D53 — Comment enrichment: richer table comment + free-form tags + steward-first hero card (human directive, R3)

**Date:** 2026-06-13 · **Scope:** project (extends D43 richer comments, D23 UX, U99 table/column hierarchy) · **Status:** locked · **Design:** `docs/design/LLD-amend-006.md` (U102)

**Decision.** (1) **Bigger table comment** (Q3): the table template gains the fields a data steward / business owner / agent needs to *interact* with the table — **purpose, business definition, freshness/SLA, known business rules, known data-quality issues, technical owner / data owner** (the human's set), plus sensible additions: **grain, primary/join keys, source systems, downstream consumers, common join patterns, related tables, sensitivity/access, example questions (Genie-readiness)**; the word cap rises accordingly. (2) **Free-form tags** (Q2): the Reasoner generates **free-form tags** (seeded with `identifier`, `metric`, `dimension`, `PII`, `temporal`, `enum`, `key`, `deprecated`, … — not a locked taxonomy) for **both** table (`TableDraft.tags`) and each column (`ColumnDraft.tags`, 2–4). (3) **Data-type + tag pills** (#2): column cards render the data type and tags as **pills**. (4) **Steward-first hero card** (#4): the table card becomes a governance-forward **hero** — purpose · business definition · grain · keys · owner · freshness · sensitivity · tags · a trust/confidence signal up top — with columns clearly secondary beneath, designed from a data-owner POV. **Why:** the comment must serve humans *and* agents (Genie) as the table's governance story, and the hierarchy must read as steward-first. Implementing units: see U102 (design) → R3 build units.

## D54 — Hands-off Job lives in a SEPARATE bundle, deployed independently (human directive, revises D51 option-b)

**Date:** 2026-06-14 · **Scope:** project (revises the D51 "option b" — the schema-run as a task on the existing `geniefy_setup` job, U111) · **Status:** locked

**Decision.** The hands-off schema-run Job moves to its **own DAB bundle** (`jobs-bundle/` with its own `databricks.yml`, bundle name `geniefy-jobs`) defining a standalone **`geniefy_schema_run`** job, deployed **independently** of the app bundle. **Why (surfaced at U113 planning):** adding the `schema_run` *task* to `geniefy_setup` (U111, option b) is still a **resource change in the app bundle**, so deploying it needs `bundle deploy` — which reconciles the app's `resources` to `databricks.yml` and **wipes the UI-added `geniefy-db` + `fmapi-endpoint` bindings → drops the SP's Lakebase role + grants** (the exact churn the grant-safe `--code-only` path exists to avoid, U77/U78/D48). A **separate bundle** has its own resource set (just the job), so `bundle deploy` of it **never touches the app** → grant-safe, and the app keeps using the grant-safe `--code-only` redeploy. Code-sharing: the jobs bundle is **staged** (geniefy_core/geniefy_app/migrations/entrypoint copied into `jobs-bundle/`, mirroring how `deploy.sh` stages `app/`) so it needs no cross-root sync. The app's trigger (`_make_run_schema_job`) resolves + runs the **`geniefy_schema_run`** job by name. Implementing/revising: **U119** (supersedes U111's same-job task wiring); the `geniefy_setup` job reverts to migrate-only.

## D55 — R4 rebrand + first-run onboarding (human directive, cosmetic/UX)

**Date:** 2026-06-14 · **Scope:** project (UI cosmetic + onboarding; extends D22/D23 UX-delight) · **Status:** locked

**Decision.** A cosmetic/onboarding pass: (1) the product is shown as **"geniefy-lite"** (UI header + browser/page title + FastAPI title) — the **Databricks app resource stays `geniefy-dev`** (renaming it would recreate the app + drop bindings/URL; out of scope). (2) Tagline → **"Make your lakehouse AI ready"**. (3) Nav tabs → **Table · Schema · History · Library** (rename the ambiguous "Document"→"Table"; "Schema" already = hands-off). (4) **First-run onboarding** on the home/empty state (which has lots of space): a concise **"How it works"** (point at a table/schema → profile + gather context → draft grounded comments/tags, ask when unsure → review + apply, human-approved) and a **"fun" animated architecture diagram with flowing connections** (an SVG pipeline — UC table → Profiler → Context (lineage/queries) → Reasoner (Claude) → Judge → Review → AI/Genie-ready — with animated flowing connectors, restrained-but-delightful per D23), plus light inline help hints. **Why:** lower the first-time-user barrier + convey the agent's value at a glance. Implementing: **U128** (rebrand + tagline + tabs) · **U129** (home onboarding + architecture diagram + hints).

## D56 — Publish geniefy-lite to git (private repo)

**Date:** 2026-06-14 · **Scope:** project (publishing/governance) · **Status:** locked · **Grounded in:** U130

**Decision (human-ratified via the U130 §8 decision set).** Publish the self-contained codebase as a
**private** GitHub repo named **`geniefy-lite`** under the `RohitDashora` account. Commits use the existing
**personal** git identity (`rohitdashora@gmail.com`) plus the `CLAUDE.md` `Co-authored-by: Isaac` trailer.
**Include** the `.gotm/` build provenance and `docs/assets/` screenshots. **Add** a refreshed README
(rebranded geniefy-lite, current R1–R4 feature set) and a consolidated `docs/ARCHITECTURE.md`. Because the
repo is **private**, the env-coupled *non-secret* identifiers (app SP UUID, workspace host, Lakebase
endpoint hosts, the `fe-vm-classic` profile) stay as-is — **no genericization** (deferred to any future
public release). Safe to publish: U130's independent secret re-scan is clean, license is Apache-2.0, and
the code has no `fe-vibe/services/` coupling. **Implementing units:** U131 (.gitignore hardening) · U132
(`requirements-dev.txt`) · U133 (README refresh + `docs/ARCHITECTURE.md`) · U134 (git init + first commit +
create private remote + push). The Databricks app *resource* stays `geniefy-dev` (D55); only the repo is
named geniefy-lite.

## D57 — Dev deployment uses the production Lakebase branch (sustained persistence, human directive)

**Date:** 2026-06-22 · **Scope:** project (deployment topology / persistence) · **Status:** locked · **Grounded in:** U135 live findings

**Decision (human directive — "point to the prod branch so we can have sustained lakebase").** The dev
deployment (app resource `geniefy-dev` on `fe-vm-classic`) is repointed from the **dev** Lakebase branch to
the stable **production** branch. **Why:** the dev branch's Autoscaling endpoint host *churns* — its primary
endpoint host changed `ep-curly-sunset-d2sz2asi` → `ep-broad-bread-d28yr0ae`, which breaks the
`app.yaml`-pinned `GENIEFY_PG_HOST` literal (DAB can't `${var}`-substitute `app.yaml` env, so the host is
hardcoded) → the app can no longer reach Lakebase (looks "gone"). The **production** branch's primary
endpoint (`ep-blue-smoke-d2yetcmv`) is stable and not recreated → sustained persistence. **Changes (U135):**
(1) `app.yaml` `GENIEFY_PG_HOST` → the production endpoint host; (2) `deploy.sh` `DB_BRANCH` defaults to
`production` for every target (the `GENIEFY_LAKEBASE_BRANCH` override is preserved for an isolated branch);
(3) the production branch migrated `001–004` (idempotent) + the app SP (`bcc7089c-…`) granted a Postgres
role there. **Trade-off:** the dev branch's prior session/library data is left behind (production starts from
a clean migrated schema) — acceptable, it was throwaway test data and "sustained going forward" is the goal.
`databricks.yml`'s `pg_host` var default was already the production endpoint, so it needs no change.

## D58 — Frontend stack: raw Tailwind + hand-rolled primitives supersede D31's shadcn (audit-sweep resolution)

**Date:** 2026-06-23 · **Scope:** project (frontend stack) · **Status:** locked · **Grounded in:** U56-U58 audit, human "address all the audit findings"

**Decision.** D31/LLD-frontend §1 named the stack as "Tailwind/**shadcn**", but the SPA was built (R1→R4, live-verified) entirely with **raw Tailwind + small hand-rolled primitives** (`components/Pill.tsx`, the viz primitives, the screen components) — **shadcn/ui (and its radix deps) were never vendored.** This decision ratifies that as the **intended** stack rather than a defect: vendoring shadcn into a working, live-verified UI now would be pure styling-convention churn (re-skinning components that already render and behave correctly) with **zero functional gain** and real regression risk. The U56-U58 audit explicitly offered this as a valid resolution ("either vendor shadcn **OR** record a decision that raw Tailwind supersedes the shadcn part of D31"). **Why:** the deliverable is a working, governed documentation app; the component library is an implementation detail, and the hand-rolled primitives are lighter and already proven. **The other half of §7's FE gate — `eslint` — IS being added** (U152), so the lint gate the design called for is honored. If a future contributor wants shadcn, that's a deliberate, separately-scoped migration, not an open audit finding. Supersedes the "shadcn" clause of D31 only; the rest of the frontend stack (Vite·React·TS·Tailwind·TanStack Query·visx/Recharts) stands.

<!-- Append new decisions below this line. -->

