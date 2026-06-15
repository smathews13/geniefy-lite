# geniefy-v3 — High-Level Design

**Status:** draft (U1) · **Last updated:** 2026-06-11
**Owner:** rohit.dashora@databricks.com
**Decisions referenced:** D1–D9 (see [`.gotm/DECISIONS.md`](../../.gotm/DECISIONS.md))

> This is the high-level design: core concepts and the major components we intend to build. Per-component detail lives in the `LLD-*.md` docs. This document is foundation — the LLDs and the code consume it.

---

## 1. Purpose & the problem

Unity Catalog **table and column comments** are exactly what Databricks **Genie**, **AI/BI**, and any **text-to-SQL agent** read to understand a table. Good comments → fewer hallucinated joins, correct filters, the right grain. So the deliverable is not documentation for humans — it is **structured metadata an LLM can act on**. "AI-ready" effectively means "Genie-ready."

geniefy-v3 is an agent that produces those comments by **profiling data and gathering usage context**, runnable two ways:

- **Interactive** — point at a table; the agent asks follow-up questions when unsure and produces comments a human reviews and refines.
- **Hands-free** — point at a table (or schema) and let it run to a reviewable result with no questions.

A **template** defines "what good looks like" and the agent must conform to it.

### The core insight (and the trap)

Profiling alone reveals **structure and distributions** — types, null rates, cardinality, min/max, top-K, patterns. It does **not** reveal **business meaning**. A column profiled as "int, 1–5, ~uniform" could be a rating, a tier, or a status code. Pure profiling produces confident-but-wrong comments — the worst outcome for an AI consumer.

So the agent's real job is **fusion + reasoning over four inputs**, emitting a **confidence** per item:

1. **Data profile** — sampled stats, top values, patterns, masked example values
2. **Schema & constraints** — names, types, PK/FK, partitioning, existing comments
3. **External context** — lineage (upstream sources), query history (real joins/filters/aggs → grain & join keys), sibling tables, business glossary
4. **Template** — the definition of a good comment

Confidence is the knob that unifies the two modes: high-confidence items are kept; low-confidence items become the interactive follow-up questions (or, hands-free, are flagged rather than asked).

### Differentiation

Databricks ships AI-generated comments in Catalog Explorer, but they are per-column, shallow, with no profiling, no business context, no template, no interactivity, no grain/join inference. Our wedge is precisely those gaps: **deep profiling + real usage context + template conformance + confidence-gated interactivity + a governed apply path + batch scale.**

---

## 2. Core concepts

| Concept | Definition |
|---|---|
| **Template** | A declarative spec (YAML) of the fields a good table/column comment must fill. The agent fills every applicable field or marks it unknown — no silent gaps, no speculation. |
| **Profile** | A *sanitized* statistical summary of a table/column (aggregates, top-K, patterns, masked samples). Never raw sensitive rows. Produced by a pluggable profiling tool. |
| **Context** | Signals beyond the data: lineage, query history, sibling tables, glossary/docs. Pulled from built-in providers and pluggable MCP providers. |
| **Draft** | A proposed table or column comment + the model's rationale + a confidence score + the judge's rubric breakdown + the profile snapshot it was grounded in. |
| **Judge** | A single LLM evaluator that scores a draft against the template rubric (completeness · specificity · grounded-in-profile / no hallucination). Its score is *both* the confidence gate and the quality metric. |
| **Gate** | The thresholding that routes drafts: high confidence → keep; low → ask (interactive) or flag (hands-free). Augmented by hard signals. |
| **Session** | One run against one table: its state, conversation, and drafts. Pausable/resumable; persisted. |
| **Comment library** | Approved/curated comments + glossary terms, reusable across tables — a quality flywheel. |
| **Apply** | The governed write of approved comments to Unity Catalog (`COMMENT ON` / `ALTER COLUMN`), diff-first, audited. |

---

## 3. Operating modes

| | Interactive (v1) | Hands-free / batch (deferred) |
|---|---|---|
| Surface | Databricks App | Databricks Job (same agent core) |
| Profiling | Samples big tables (`sample_size`) | Full scan, any size |
| Low confidence | Asks the user a question | Flags the draft for later review |
| Output | Reviewed/edited drafts → apply | Drafts land in Lakebase for review |

Same engine. The only differences are who answers the low-confidence questions and whether profiling samples.

---

## 4. High-level architecture

```
┌────────────────────────────────────────────────────────────────┐
│  AGENT CORE  (Python library — no UI, no app dependency)  [D1]   │
│  Template · Profiler · ContextGatherer · Reasoner · Judge · Gate │
│  in:  table ref + template + config                              │
│  out: structured drafts + confidence + open questions            │
└───────────────┬─────────────────────────────────┬───────────────┘
                │ called by                        │ called by
        ┌───────▼────────┐                ┌────────▼───────────┐
        │ DATABRICKS APP │                │  DATABRICKS JOB    │
        │ interactive:   │                │  hands-free/batch  │
        │ sessions, Q&A, │                │  "profile N tables"│
        │ review, apply, │                │  (deferred)        │
        │ library, config│                └────────┬───────────┘
        └───────┬────────┘                         │
                │                                   │
                └──────────────► LAKEBASE ◄─────────┘   [D6]
                       sessions · drafts · library ·
                       context-provider config ·
                       templates · audit
                │
   external dependencies the core/app call:
   • FMAPI Claude behind Unity/AI Gateway (PII guardrails)   [D4]
   • Profiling UC function / MCP tool                        [D3]
   • Lineage + query-history (system tables) + MCP context   [D5]
   • SQL warehouse for the Apply write path                  [D7]
   • MLflow 3 tracing + evaluate                             [D8]
```

The agent is a library (D1). The App wraps it for interactive use; a Job wraps the *same* library for batch. Both write to one Lakebase store. The App backend is the orchestrator; heavy profiling and batch are delegated outward (to the profiling tool and, later, to Jobs) so the App stays responsive.

---

## 5. Components

### 5.1 Agent core (standalone library) — D1, D8

- **Template loader** — parses the YAML template; exposes the required/conditional fields the reasoner must fill.
- **Profiler** — thin client that calls the profiling tool (`profile_table(...)`) and normalizes the returned **sanitized** profile (D3, D4). Does not run profiling SQL itself.
- **ContextGatherer** — uniform interface over built-in providers (lineage, query history) and pluggable MCP providers (Genie/Glean/Confluence/custom) (D5). Returns context snippets.
- **Reasoner** — calls FMAPI Claude (behind AI Gateway) to fuse profile + schema + context + template into structured table/column drafts + rationale (D4). Chunks columns for wide tables.
- **Judge** — scores each draft against the template rubric; emits confidence + rubric breakdown + open questions (D8).
- **Gate** — thresholds the judge score, augmented by hard signals (no usage history + ambiguous name + high null rate → force-low), to route keep vs. ask/flag.
- Instrumented end-to-end with **MLflow 3 tracing**; the judge doubles as the `mlflow.evaluate` metric (D8).

### 5.2 Databricks App — D1, D6, D7

- **Session loop & interactive Q&A** — create → profile → reason → (awaiting_input) → resume with answers → ready_for_review; pause/resume + history persisted to Lakebase. *(LLD: U5)*
- **Review & apply UI** — profile view, draft table/column comments, **diff vs. existing**, inline edit, answer questions; Approve → Apply writes to UC and audits. *(LLD: U6)*
- **Comment library** — surfaces reuse suggestions (v1: match by column name).
- **Context-provider config** — register/enable MCP context providers (D5).

### 5.3 Databricks Job (deferred) — D1, D2

Wraps the same agent core for "point at a schema, profile N tables" with full-scan profiling; drafts land in Lakebase for later review.

### 5.4 Data layer — D6

Lakebase (managed Postgres) as the working store. Detailed in [`LLD-data-layer.md`](LLD-data-layer.md) (U2).

### 5.5 External, governed dependencies

- **Generation:** FMAPI Claude behind Unity/AI Gateway with PII guardrails; only sanitized profiles sent (D4).
- **Profiling:** UC function / MCP tool, sampling policy inside the tool (D3).
- **Context:** `system.access.table_lineage` / `column_lineage`, `system.query.history`; pluggable MCP providers (D5).
- **Apply:** SQL warehouse executes `COMMENT ON` / `ALTER COLUMN` (D7).

---

## 6. End-to-end pipeline

```
point at table
   │
   ├─ 1. INTROSPECT  schema, constraints, existing comments, partitioning
   ├─ 2. PROFILE     call profiling tool (sample in App mode; full scan in Job)   [D3]
   ├─ 3. GATHER      lineage + query history + sibling tables + MCP context        [D5]
   ├─ 4. REASON      FMAPI Claude fuses 1–3 vs. template → drafts + rationale      [D4]
   ├─ 5. JUDGE+GATE  score → confidence; high keep, low → ask (interactive)/flag   [D8]
   ├─ 6. RENDER      diff vs. existing comments — never silently clobber           [D7]
   └─ 7. APPLY       Approve → COMMENT ON / ALTER COLUMN via warehouse → audit     [D7]
```

Steps 1–5 are the agent core. Steps 6–7 are App flows. Generation (4–5) never writes to UC; Apply (7) is a separate, explicit, audited action.

---

## 7. The comment template — "what good looks like"

The passed-in spec. The agent fills every applicable field or explicitly marks it unknown (no speculation):

```yaml
table_comment:
  required:    [purpose, grain, primary_keys, join_keys]   # grain = "one row represents ..."
  recommended: [use_cases, update_cadence, source_systems, caveats, sensitivity]
  style: { max_words: 120, voice: "factual, present tense, no marketing" }

column_comment:
  required:    [definition]                 # business meaning, NOT a restatement of the name
  conditional:                              # include when the signal applies
    units:          "if numeric measure"
    allowed_values: "if enum -> map each value to its meaning"
    null_meaning:   "if nullable -> what NULL signifies"
    fk_reference:   "if FK -> catalog.schema.table.column"
    derivation:     "if computed -> the logic"
    sensitivity:    "if PII"
  style: { max_words: 40, forbid: ["restating the name", "speculation without signal"] }
```

Templates are first-class and versioned (stored in Lakebase) so different teams can enforce different standards.

---

## 8. Governance & safety

- **No egress beyond the boundary.** Generation uses in-platform FMAPI Claude behind the AI Gateway; only sanitized profiles leave the Profiler (D4).
- **PII guardrails** at the gateway (detection/masking) + masked samples in the profile.
- **Never clobber curated comments.** Apply is diff-first; existing comments are shown and preserved unless explicitly overwritten (D7).
- **Permissions.** Applying requires `MODIFY` on the table for the acting identity (app SP or on-behalf-of user); a missing grant surfaces a clear error, not a silent fail.
- **Auditability.** Every generate/edit/approve/apply is recorded in Lakebase (D6).
- **Cost.** AI Gateway rate limits bound batch fan-out; sampling bounds profiling cost.

---

## 9. v1 scope & deferred — D2

**In v1 (interactive single-table, full loop):** introspect → profile (sampled) → context (lineage + query history + configured MCP) → reason → judge/gate → review/edit + answer questions → store → apply to UC, with audit and a basic (name-match) library suggestion.

**Deferred:** Job/batch mode; approval → export-as-code → CI/CD; rich library reuse; Genie-artifact generation (sample queries, synonyms, instructions); expanded eval harness beyond the judge.

---

## 10. Watch-items

- **Wide tables** (100s of columns) — the Reasoner chunks columns per LLM call; profiling stays one tool call. Bounds context + cost.
- **Permissions** — `MODIFY` required to apply; surfaced explicitly.
- **Golden set** — the judge needs ~5–10 already-well-documented tables to score against (see Q3).

---

## 11. Open questions

Tracked in [`.gotm/QUESTIONS.md`](../../.gotm/QUESTIONS.md): Q1 mission ratification · Q2 audience/operator · Q3 target workspace + golden set · Q4 delivery anchor + license.

## 12. Glossary

- **UC** — Unity Catalog. **FMAPI** — Foundation Model API. **AI Gateway** — Mosaic AI Gateway (guardrails/logging/limits in front of model endpoints). **Lakebase** — Databricks managed Postgres (OLTP). **Genie** — Databricks natural-language-to-SQL. **MCP** — Model Context Protocol (tool/provider integration). **Grain** — what one row of a table represents.
