# geniefy-v3 — Low-Level Design: Non-Functional Requirements Plan

**Status:** draft (U24) · **Last updated:** 2026-06-12
**Inputs:** [`LLD-agent-core.md`](LLD-agent-core.md) (U4) · [`LLD-app-backend.md`](LLD-app-backend.md) (U5) · [`LLD-packaging-deployment.md`](LLD-packaging-deployment.md) (U8) · [`LLD-data-layer.md`](LLD-data-layer.md) (U2)
**Decisions:** D4 (sanitized profiles / secrets), D5 (built-in + MCP context), D12 (parameterized DAB), D18 (async + poll), D20 (per-iteration bounding), **D32–D35** (recorded by this unit)
**Scope:** the cross-cutting **non-functional requirements** the human raised and the concrete design to satisfy each: (A) fully configurable via `app.yaml`, (B) cost predictability via per-loop token budgets + prompt summarization, (C) pluggable MCP context providers via config, (D) git→customer deployability via DAB + a shell orchestrator. This doc **amends frozen U4 and U8 and extends D5/U2** — the frozen docs are not edited; the deltas live here (GOTM freeze rule).

> Living doc. Each section ends with the **implementing code units** that will land it. This is the *plan*; the contracts are specified to the level needed to build, with deeper contracts split out where flagged.

---

## 1. NFR-A — Fully configurable via `app.yaml` (D33)

**Requirement.** The app must be fully configurable through `app.yaml`.

**Design.** `app.yaml` is the **single runtime config surface**. Every knob is an environment variable declared in `app.yaml` `env`, sourced from bundle variables (`databricks.yml`) and resource bindings; the backend reads env once at startup into a typed `AppConfig` + `RunConfig` (U4 §7) and **fails fast** on a missing/invalid required var.

**Config schema (env → meaning).** *(`valueFrom` = a Databricks App resource binding / secret; literal = bundle variable.)*

| Env var | Source | Drives |
|---|---|---|
| `GENIEFY_MODEL_ENDPOINT` | var `model_endpoint` | FMAPI Claude endpoint (U4 §5 / D4) |
| `GENIEFY_WAREHOUSE_ID` | `valueFrom` warehouse | profiling + apply write-path |
| `GENIEFY_PG_HOST` / `GENIEFY_PG_DATABASE` / `GENIEFY_PG_SCHEMA` | `valueFrom` Lakebase + vars | working store (D6/D30) |
| `GENIEFY_KEEP_THRESHOLD` · `GENIEFY_PROFILE_BATCH_SIZE` · `GENIEFY_REASON_BATCH_SIZE` · `GENIEFY_CONTEXT_TOKEN_BUDGET` | vars | `RunConfig` tunables (U4 §7) |
| `GENIEFY_MAX_INPUT_TOKENS_PER_CALL` · `GENIEFY_SUMMARIZE_OVER_BUDGET` · `GENIEFY_SUMMARY_TARGET_TOKENS` | vars | token budgets (§2) |
| `GENIEFY_MCP_PROVIDERS` | var (JSON) / DB | MCP provider registrations (§3) |
| `GENIEFY_SECRET_SCOPE` | var | secret scope for provider tokens (D5) |
| `GENIEFY_SAMPLE_MODE` | var | default sampling intent (U3 §4.1) |

**Layering.** Bundle var default → per-target override (`databricks.yml`) → `--var` at deploy → `app.yaml` env → runtime `AppConfig`. Provider/template config that must change *without redeploy* lives in Lakebase (`context_providers`, `templates`) and is editable via U5 config endpoints; `app.yaml` carries the bootstrap + infra config.

**Amends:** U8 §6 (app config & resource wiring) — adds the explicit schema; U5 — `AppConfig` load + startup validation + config endpoints.
**Implementing units (later):** app-backend build (`AppConfig` loader + validation); deploy unit (the `app.yaml` env block + bundle vars).

## 2. NFR-B — Cost predictability: per-loop token budgets + summarization (D32)

**Requirement.** Tokens defined and configured **per agent-loop level**, so we can **summarize the prompt if we hit the limit in the loop** — making cost predictable.

**Design (amends U4 §4 loop, §5 LLMClient, §7 RunConfig).** Cost = (bounded input tokens per call) × (known number of calls). We bound the first and meter both.

1. **Per-call input-token budget.** Each model call type carries a budget from config: `reason` (per column batch — the "loop"), `judge` (per draft), and `summarize` (the compaction call itself). Default from `GENIEFY_MAX_INPUT_TOKENS_PER_CALL`, overridable per phase.
2. **Token accounting.** A `TokenCounter` (model-aware tokenizer) estimates the assembled prompt's tokens **before** sending (LLMClient, U4 §5). Output is bounded by the existing `max_tokens` (U4 §5).
3. **Compaction pipeline (the "summarize if over limit" step).** When an assembled prompt for a loop iteration exceeds its budget, apply ordered, recorded reductions until under budget:
   1. **Trim context** to the top-N most-relevant snippets (already `context_token_budget`, U4 §3.3).
   2. **Summarize context** — a cheap `summarize` LLM call compresses the gathered snippets to `GENIEFY_SUMMARY_TARGET_TOKENS`.
   3. **Reduce profile verbosity** — drop low-signal per-column detail (e.g. long `top_k`, sample_values) for that batch, keeping the load-bearing stats.
   4. **Shrink the batch** — fewer columns per `reason` call (smaller loop), re-batching the remainder.
   Stop at the first point under budget. If still over after (4) for a single column, send with a truncation marker + a `warning` on the draft.
4. **Honest surfacing (D23 Principle 5).** Compaction actions are recorded on the draft/session and shown in the UI ("context summarized to fit budget") — never silent.
5. **`RunConfig` additions:** `max_input_tokens_per_call` (or per-phase map), `summarize_over_budget: bool`, `summary_target_tokens: int`. Ties to **D20** — the per-iteration (per-batch) budget *is* the crash/cost bound.
6. **Observability (extends U4 §6).** Per call: budget vs. estimated vs. actual tokens, compaction steps taken, running session token total + an estimated cost. Predictable, inspectable spend.

**New component:** a `PromptBudgeter`/`Compactor` in the agent core (used by Reasoner/Judge via LLMClient). **Amends U4** (frozen) — lands as part of the LLMClient/Reasoner build units, referencing this section.
**Implementing units (later):** `TokenCounter` + `PromptBudgeter` (agent-core build); wire into Reasoner/Judge.

## 3. NFR-C — Pluggable MCP context providers via config (D34)

**Requirement.** Add more MCP servers via config so the agent can discover context from Glean, Confluence, Atlassian, etc.

**Design (implements D5; extends U2 `context_providers` + U4 §3.3 — both already exist).** The data model already supports this (`context_providers.kind = 'mcp'`, `config jsonb`, uniform `ContextGatherer`). This section closes the gap: the **registration schema** and the **MCP invocation contract**.

**Registration schema** (`context_providers.config` jsonb for `kind='mcp'`), settable via `GENIEFY_MCP_PROVIDERS` (bootstrap) or the U5 config endpoint (runtime, no redeploy):

```jsonc
{
  "server_url": "https://<host>/api/2.0/mcp/…",   // or a custom MCP endpoint
  "transport":  "streamable_http | sse | stdio",
  "auth":       { "secret_scope": "geniefy", "key": "glean_token" },  // ref only, never a raw secret (D4/D5)
  "tool_allowlist": ["search"],                    // which MCP tools the gatherer may call
  "query_template": "table {full_name}: {column_terms}",  // how to build the query from the table
  "max_snippets": 8,
  "token_budget": 2000                             // feeds §2 compaction
}
```

**MCP invocation contract** (the ContextGatherer's external contract, analogous to U3 for profiling):
1. **Connect & discover** — on startup/config-change, open the MCP session, `list_tools`; intersect with `tool_allowlist`. Unreachable/no allowed tool → mark the provider disabled with a warning (degrade, not fail — U4 §8).
2. **Invoke** — for the target table, build a query from `query_template` (table name, column names, any business terms from built-in context) and call the allowed tool(s).
3. **Normalize** — map results → `ContextSnippet`s (U4 §3.3): text + source attribution (provider name, doc/url) + which columns/relationships they inform + a relevance score; truncate to `max_snippets` / `token_budget`.
4. **Auth** — token fetched from the secret scope at call time (D4/D5); never logged.

**Examples:** Glean (`search`), Confluence/Atlassian (page search), Genie spaces, customer-custom — all the same contract; adding one is a config row, not code (D11 hardening path).

**Extends:** D5, U2 (`context_providers`), U4 §3.3.
**Implementing units (later):** an `McpContextProvider` client in the ContextGatherer build; U5 config endpoint for provider CRUD. *A deeper, U3-style contract LLD can be split out if invocation/discovery needs more rigor — flagged, not required for v1.*

## 4. NFR-D — Git → customer deploy via DAB + shell orchestrator (D35)

**Requirement.** The whole solution deploys to a customer Databricks workspace from git. If DAB can't do everything, split components and use a shell script to orchestrate DAB + CLI.

**Design (amends U8 §1/§3/§7/§11).** Keep DAB as the spine; add a thin, idempotent `deploy.sh` for the steps DAB can't express today.

- **DAB covers:** the App resource (`source_code_path: ./app`), the `geniefy_setup` job, resource bindings (warehouse, Lakebase DB, model endpoint, secret scope), and UC grants it can declare.
- **DAB can't (today) → CLI/script:** create the **Lakebase Autoscaling project/branch/endpoint + the `geniefy` database** (`databricks postgres …`, see [`../../migrations/APPLY.md`](../../migrations/APPLY.md)); **apply migrations** (needs a Postgres connection — the `geniefy_setup` job or the APPLY.md driver path); build the **frontend** (`vite build`, U23 §4); some grants.
- **`deploy.sh` (single command, idempotent, re-runnable):**
  1. **Preflight** — CLI ≥ v0.285 (Autoscaling Lakebase), profile/host present, prereqs (UC, warehouse, FMAPI endpoint) reachable.
  2. **Build frontend** — `vite build` → `app/static`.
  3. **Ensure Lakebase** — create project/branch/endpoint + `geniefy` DB if absent (skip if present).
  4. **`databricks bundle deploy -t <env>`** — app + job + bindings.
  5. **Migrate** — run the `geniefy_setup` job (which runs `migrations/`), or fall back to the APPLY.md path.
  6. **Grants** — `SELECT`/`MODIFY` + Lakebase/secret-scope access for the app SP.
  7. **Print the App URL.**
- A `Makefile` / README "one command" wrapper (`./deploy.sh -t prod --host …`) — the customer clones the git repo and runs it. Each step idempotent so a re-run heals a partial deploy.

**Supersedes:** the implicit "everything is one `bundle deploy`" reading of U8 — the explicit DAB-vs-CLI split lives here. The README (U16/U22) already points at `migrations/APPLY.md`; the deploy unit replaces the manual step with `deploy.sh` + the wired `geniefy_setup` job.
**Implementing units (later):** the deploy unit (`deploy.sh` + `geniefy_setup` job in `databricks.yml` + grants).

## 5. Amendment register (what this doc changes in frozen artifacts)

| NFR | Touches (frozen) | Nature |
|---|---|---|
| A (config) | U8 §6, U5 | adds the `app.yaml` config schema + startup validation |
| B (tokens) | U4 §4/§5/§7 | adds per-call budgets, `TokenCounter`/`PromptBudgeter`/compaction, `RunConfig` fields, observability |
| C (MCP) | U2, U4 §3.3, D5 | adds the registration schema + MCP invocation contract (data model already supports it) |
| D (deploy) | U8 §1/§3/§7/§11 | adds the DAB-vs-CLI split + `deploy.sh` orchestrator |

Per the GOTM freeze rule, the frozen docs are **unedited**; these deltas are authoritative where they conflict, exactly as the U10/U11 amendments related to U2/U4/U6.

## 6. Sequencing

These are **design** changes (the plan). They get an **independent design-audit** (gate) before the **implementing code units** consume them. The implementing units are appended to the ledger as the build reaches each area (agent-core for B/C, app-backend for A/C, deploy unit for A/D, frontend for U23). No code is built on this plan until it is audited.
