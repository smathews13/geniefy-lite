# geniefy-v3 — Low-Level Design: Profiling Tool Contract

**Status:** draft (U3) · **Last updated:** 2026-06-11
**Inputs:** [`HLD.md`](HLD.md) (U1) · **Decisions:** D3 (profiling via pluggable MCP/UC tools), D4 (sanitized profiles only), D5 (context vs. profile boundary), D12 (parameterized, FE-VM dev), D15 (profiling realism — added by this unit)
**Scope:** the contract between the agent core and whatever produces a table profile — the `profile_table` interface, the **sanitized profile schema** it returns, the PII-safety rules, and the sampling policy. *Out of scope:* who consumes the profile (agent core = U4), the app/session wiring (U5), and the context providers (lineage/query-history/glossary), which are a separate interface (D5).

> Living doc. The v1 contract is specified concretely; richer stats and cached-stat reuse are sketched and marked deferred.

---

## 1. Purpose & principles

The agent must stay a thin reasoning layer (D1). Profiling — and the decision to sample huge tables — is **pluggable infrastructure** behind a stable contract (D3), so it can be governed by Unity Catalog, swapped per customer, and run identically in the App (sampled) and in a Job (full scan).

Principles:

- **One contract, many providers.** The agent calls `profile_table(...)` and receives a versioned **sanitized profile** JSON. It does not care how the provider computed it.
- **Sanitized at the source (D4).** The provider returns aggregates, top-K, patterns, and *masked* samples — never raw sensitive rows. This is the *primary* PII control; the AI Gateway (D4) is defense-in-depth.
- **Profile ≠ context (D5).** This tool returns what the *data itself* reveals (structure, distributions, patterns, key-likeness). Business meaning, lineage, FK targets, and usage come from the ContextGatherer, not here.
- **Cost is the provider's problem.** Sampling thresholds and `approx_*` usage live behind the contract; the agent only passes intent (`sample` policy).

---

## 2. Realism: why the reference provider is an MCP service, not a bare UC function — D15

D3 said "UC function / MCP tool." Designing the contract surfaces a constraint worth stating plainly:

- A **UC SQL function** is bound to fixed input/return types and cannot run *dynamic* SQL over an arbitrary table's arbitrary columns — so a single SQL UDF cannot generically profile any table.
- A **UC Python UDF** executes in a per-row sandbox without a Spark/SQL session — it cannot orchestrate table-wide profiling queries.

Therefore the **reference profiling provider is a small service exposed as an MCP server** that, given a table name, *generates and runs* profiling SQL against a SQL warehouse (Statement Execution API / SQL connector) and returns the sanitized JSON. The "UC function" path remains valid and useful for the **subset** that *is* expressible without dynamic SQL: returning cached `ANALYZE`/`information_schema` statistics, or templated profiling of a known table. Both paths implement the **same §4 contract**; the agent cannot tell them apart.

Managed UC-function MCP servers are reachable at `https://<workspace-host>/api/2.0/mcp/functions/{catalog}/{schema}` ([docs](https://docs.databricks.com/aws/en/generative-ai/mcp/managed-mcp)); a custom profiling service is registered as an external/custom MCP server. This refinement is recorded as **D15**.

---

## 3. Provider shapes

| Provider | How it computes | When to use |
|---|---|---|
| **MCP profiling service** (reference) | Service holds a warehouse connection; generates + runs profiling SQL; sanitizes; returns JSON. Registered as a custom MCP server. | Default. Full distribution profiling of any table, any size. |
| **UC function (SQL) via managed MCP** | `…/api/2.0/mcp/functions/{catalog}/{schema}`; function returns cached/templated stats. | When profiling reduces to cached `ANALYZE` stats or a templated known table; keeps everything inside UC governance. |
| **Customer MCP server** | Customer's own implementation conforming to §4. | Customer has existing profiling tooling / policies and wants the demo to use it (D11 hardening path). |

Provider selection is config, not code: each session records the chosen provider in `sessions.config` (from the `context_providers`/tool registry in the data layer, U2).

---

## 4. The `profile_table` contract

### 4.1 Request

```jsonc
{
  "table": "catalog.schema.table",          // required, fully-qualified
  "sample": {                                // optional; default {"mode":"auto"}
    "mode": "auto | full | rows | percent",  // auto: provider decides by size; full: whole table
    "value": 100000                          // rows (mode=rows) or percent 0–100 (mode=percent)
  },
  "columns": ["col_a", "col_b"],             // optional; subset for wide-table batching (default: all)
  "options": {
    "top_k": 20,                             // max frequent values to return per low-cardinality column
    "include_samples": true,                 // emit masked example values
    "max_cardinality_for_topk": 50,          // above this, skip top_k (treat as high-cardinality)
    "reuse_analyze_stats": true              // use cached ANALYZE stats where available (deferred refinement)
  }
}
```

- **App mode** passes `sample.mode=auto` (provider samples big tables) or an explicit `rows` cap; **Job mode** passes `sample.mode=full` (D3). The *thresholds* for `auto` live in the provider, not the agent.

### 4.2 Response — the sanitized profile

```jsonc
{
  "profile_schema_version": "1.0",
  "table": {
    "full_name": "catalog.schema.table",
    "table_type": "MANAGED | EXTERNAL | VIEW | MATERIALIZED_VIEW",
    "format": "DELTA",
    "row_count": 12345678,
    "row_count_is_estimate": false,          // true when derived from a sample
    "sampled": true,
    "sample_method": "TABLESAMPLE_ROWS | TABLESAMPLE_PERCENT | NONE",
    "sample_rows": 100000,
    "column_count": 42,
    "partition_columns": ["dt"],
    "existing_comment": "…",                 // current UC table comment, if any (diff source, D7)
    "stats_source": "computed | analyze_cache | mixed",
    "profiled_at": "2026-06-11T12:00:00Z",
    "warnings": []                           // e.g. "sampled: row_count is an estimate"
  },
  "columns": [
    {
      "name": "customer_id",
      "ordinal": 1,
      "data_type": "bigint",
      "type_class": "integer | decimal | string | boolean | temporal | complex | binary",
      "nullable": true,
      "existing_comment": null,              // current UC column comment, if any (diff source, D7)

      "null_fraction": 0.001,
      "distinct_count": 1200000,
      "distinct_is_approx": true,            // approx_count_distinct used
      "cardinality_ratio": 0.97,             // distinct / non-null → key-likeness SIGNAL (not an FK claim)

      // numeric / temporal
      "min": "1", "max": "8000231",
      "mean": 401123.5, "stddev": 22310.2,
      "percentiles": {"p05": 5, "p25": 100, "p50": 4000, "p75": 90000, "p95": 7900000},

      // low-cardinality / categorical
      "is_enum_candidate": false,
      "top_k": [ {"value": "active", "count": 90123, "fraction": 0.90} ],  // omitted/empty if high-cardinality

      // string
      "len_min": 1, "len_max": 12, "len_mean": 7.2,
      "pattern_summary": [ {"regex": "\\d{3}-\\d{2}-\\d{4}", "fraction": 0.99, "label": "ssn_like"} ],

      // masked examples (per §5)
      "sample_values": ["12••••", "84••••"],

      // first-line classification (per §5)
      "pii": {"detected": false, "classes": [], "action": "none"}
    }
  ]
}
```

Notes:
- **`cardinality_ratio`** near 1.0 is the profiler's signal that a column *looks like* a key; deciding it *is* a PK/FK is the ContextGatherer's job (constraints + lineage), per D5.
- Type-specific blocks are populated by `type_class`; irrelevant blocks are omitted (a string column has no `percentiles`).
- The schema is **versioned** (`profile_schema_version`); the agent core (U4) pins a minimum version and tolerates additive fields.

---

## 5. PII-safety rules (primary control — D4)

Because the profile *is* what reaches the model, sanitization happens **in the provider, before return**:

1. **Classify** each column (first line): regex/heuristics (email, phone, SSN-like, credit-card, IP), plus any UC **column tags / classification** the provider can read. Result in `pii`.
2. **Mask** `top_k.value` and `sample_values` for columns classified PII — partial redaction (keep a few chars for shape, mask the rest, e.g. `12••••`) — or **omit** them entirely when `action="omit"` for high-sensitivity classes.
3. **Never** emit raw rows, full free-text values, or unmasked identifiers. Numeric aggregates (min/max/percentiles) of a PII numeric (rare) are allowed only if not themselves identifying.
4. Record what was done (`pii.action`: `none | masked | omitted`) so the agent can reflect sensitivity in the comment and the UI can show why a value is hidden.

The AI Gateway PII guardrail (D4) is the **second** line; the provider must not rely on it.

---

## 6. Sampling policy

- `full` → whole table (`stats_source` may still mix cached stats). Job mode default.
- `auto` → provider samples when the table exceeds an internal threshold (e.g., row count or byte size), else full. App mode default; keeps the App responsive.
- `rows: N` → `TABLESAMPLE (N ROWS)`; `percent: P` → `TABLESAMPLE (P PERCENT)`.
- When sampled: `sampled=true`, `row_count_is_estimate=true`, and a `warnings` entry — so the agent can hedge confidence (feeds the Judge/Gate, D8).
- **Scale tactics** the provider uses on large/wide tables: `approx_count_distinct` (HLL) for cardinality, `approx_percentile` for distributions, single-pass multi-aggregate SQL per column batch, and partition pruning when a recent partition is representative.

---

## 7. Wide-table handling

For tables with hundreds of columns, the agent core calls `profile_table` with a `columns` subset and **batches** (e.g., 25–50 columns per call), then merges responses. This bounds provider query cost and pairs with the Reasoner's per-batch column chunking (HLD §5.1, §10). The table-level block is returned on every call (cheap) or once on the first batch — provider returns it each time; the agent dedups.

---

## 8. Invocation paths

- **MCP (reference & customer):** standard MCP tool call to the registered server; tool name `profile_table`, args per §4.1. Custom service auths to the warehouse via its own service principal.
- **UC function via managed MCP:** tool call against `…/api/2.0/mcp/functions/{catalog}/{schema}`; the function returns the §4.2 shape (for the SQL-expressible subset).
- The agent core wraps either behind a single `Profiler.profile(table, sample, columns, options)` method (designed in U4) and normalizes to the §4.2 schema.

---

## 9. Errors & edge cases

| Condition | Contract behavior |
|---|---|
| Caller lacks `SELECT` on the table | Error `permission_denied` with the missing grant; agent surfaces it, does not fabricate a profile. |
| Empty table (0 rows) | Valid response: `row_count=0`, column structure present, distribution fields null, `warnings:["empty table"]`. |
| Unsupported / complex type (`struct`, `array`, `map`, `binary`) | `type_class:"complex"`/`"binary"`; emit type + null_fraction + (for complex) field names if cheap; skip distributions. |
| View / MV | Profile the resolved output columns; `table_type` reflects it; note in `warnings` that it may be compute-on-read. |
| Provider/warehouse timeout | Error `timeout` with the attempted scope; agent may retry with a smaller `sample` or fewer `columns`. |
| Very high column count | Agent drives batching (§7); a provider may also cap `columns` per call and signal it. |

---

## 10. Conformance (for custom / customer providers)

A provider is conformant if it: (a) accepts the §4.1 request and returns the §4.2 schema at the declared `profile_schema_version`; (b) honors `sample` semantics and sets the sampling flags/warnings truthfully; (c) applies §5 sanitization **before** returning; (d) returns structured errors (§9) rather than partial/garbage profiles. A conformance test fixture (golden table → expected profile shape) is a deferred test asset (with the eval harness, D2/D8).

---

## 11. Deferred / sketched

- **Cached-stat reuse** — read `ANALYZE TABLE … COMPUTE STATISTICS` results / `information_schema` to skip recomputation (`reuse_analyze_stats`); needs a freshness check.
- **`profile_cache`** in Lakebase keyed by (table, table version) — sketched in U2 §9.
- **Cross-column signals** — composite-key candidates, simple functional-dependency hints. Off by default (cost).
- **Richer pattern library** — beyond regex labels; pluggable detectors.

---

## 12. Open items touching this contract

- D15's UC-function-vs-service split should be reflected when U8 (deployment) decides what gets bundled (a profiling service container/app vs. a UC function + managed MCP).
- Provider auth model interacts with Q2 (audience) — a customer-shipped service profiling under its own SP vs. on-behalf-of the user.

**Source:** managed MCP server endpoint format — [Databricks: Use managed MCP servers](https://docs.databricks.com/aws/en/generative-ai/mcp/managed-mcp).
