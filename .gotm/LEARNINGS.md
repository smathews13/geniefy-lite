# Learnings — geniefy-v3 (geniefy-lite)

> Transferable lessons distilled from this project's own GOTM record (`.gotm/DECISIONS.md` / `.gotm/audits/` / `.gotm/LEDGER.md`) when the project finished — produced via `.gotm/prompts/outcome-analysis.md` (or `/gotm:learn`). Only *transferable* claims belong here: a stranger to this project should be able to act on each one. Project-specific detail stays in the record.
>
> **Two consumers, one artifact.** A future project scans the **Index** (one line each), filters to the tags it's touching, and expands a record's `fix` only when relevant — cheap to read. An aggregation layer (user- or enterprise-level) ingests the **Records** and merges on `claim`. See *Confidence & merge model* below.
>
> Source project: a Databricks **App** (FastAPI + React SPA) over a hermetic Python agent core, backed by **Lakebase** (Autoscaling Postgres), deployed via a **Databricks Asset Bundle (DAB)** + a grant-safe shell orchestrator, generating LLM-authored Unity Catalog comments with a human-approved apply path. 13 candidate learnings: 5 gotcha · 3 anti-pattern · 1 prerequisite · 3 pattern/pivot.

## Index — generated from the Records below

> Format: `L#  [tags]  claim → fix-gist.  (kind · confidence · source)`. This is what a future project reads first; it expands a record only when its tags are relevant.

- `L1  [lakebase·postgres·databricks-apps·deploy]` Autoscaling Postgres scales to zero + OAuth creds expire (~1h), so a cached/pooled connection goes stale → `InterfaceError` → open a fresh connection per call. *(gotcha · candidate · D49)*
- `L2  [databricks-asset-bundle·databricks-apps·deploy]` `bundle deploy` reconciles `resources` and wipes UI-added bindings + their SP grants → use code-only redeploy; isolate jobs in their own bundle. *(gotcha · candidate · D54/D35)*
- `L3  [databricks-apps·app.yaml·lakebase·deploy]` DAB can't `${var}`-substitute into `app.yaml` env (hosts are hardcoded literals) and serverless endpoint hosts churn → bind via resource binding or pin a stable branch. *(gotcha · candidate · D57)*
- `L4  [databricks-apps·oauth·obo·deploy]` Apps OBO needs a one-time UI scope add (User authorization → +Add `sql`), not an `app.yaml` key → document it as an operator step + degrade loudly when the token is absent. *(prerequisite · candidate · D48)*
- `L5  [unity-catalog·profiling·sql·design]` UC SQL functions can't run dynamic SQL over arbitrary columns and Python UDFs have no Spark session → back generic profiling with a warehouse service, not a UC function. *(gotcha · candidate · D15)*
- `L6  [llm·pii·governance·design]` Sanitize the profile at the source (aggregates/top-K/masked samples, never raw rows) — that's the primary PII control; a gateway guardrail is secondary. *(pattern · candidate · D4)*
- `L7  [testing·postgres·build]` Hermetic tests with fakes pass while real column-type/constraint mismatches fail in prod (name string into a uuid FK) → run a live round-trip against the real schema. *(anti-pattern · candidate · D38)*
- `L8  [refactoring·testing·build]` A bulk find/replace (`replace_all`) meant for N sites can silently hit only ONE (indentation differs) → verify each site by grep count + a guard test per site. *(anti-pattern · candidate · audit U145→U146)*
- `L9  [process·build·governance]` A decision that's only documented, never enforced by a gate/config/assertion, silently fails to hold → wire enforcement where it bites + audit that every decision has one. *(anti-pattern · candidate · audit U13→U15)*
- `L10 [testing·serialization·build]` Symmetric serialize→deserialize round-trip tests hide symmetric field drops → assert an explicit key-set + mutation-test the suite. *(anti-pattern · candidate · audit U27)*
- `L11 [config·design·build]` A config knob can be fully designed/schema'd yet never consumed (inert) → verify a real read at a real call site, or drop it and record why. *(anti-pattern · candidate · D40)*
- `L12 [databricks-apps·async·design]` Long work behind short Apps HTTP timeouts times out → background task + DB-backed status + 202 + frontend polling (+ stale-run watchdog). *(pattern · candidate · D18)*
- `L13 [architecture·design·process]` Picking an app/frontend stack without evaluating the platform's own SDK is a design gap → evaluate it explicitly; a cross-language core constraint can correctly rule it out. *(pivot · candidate · D42)*

### Example retrieval
> A project deploying a Databricks App on Lakebase filters the index to tags `{databricks-apps, lakebase, databricks-asset-bundle, deploy}` → surfaces L1, L2, L3, L4, L12 before writing any deploy script, and expands only those. The testing/serialization records (L7, L8, L10) never enter its context unless it's also writing a persistence layer.

## Records — source of truth

Schema: `id` · `claim` (the merge key) · `kind` (`gotcha` | `prerequisite` | `pivot` | `pattern` | `anti-pattern`) · `tags` (tech / domain / phase, for retrieval) · `fix` (actionable) · `scope` (where it applies) · `evidence[]` (appendable — which project, which `D##`/audit) · `confidence` (+ optional `strength`).

```yaml
- id: geniefy-v3/L1
  claim: "Autoscaling/serverless Postgres (e.g. Databricks Lakebase) scales to zero when idle and its OAuth DB credential expires (~1h), so any cached or pooled DB connection goes stale and later requests fail with InterfaceError 'connection already closed'."
  kind: gotcha
  tags: [lakebase, postgres, databricks-apps, deploy]
  fix: "For interactive, low-QPS apps open a FRESH connection per operation and close it — inject a `connect` factory and wrap each op in a `_connection()` context manager. Don't cache/pool; revisit pooling only if throughput demands it. This eliminates the entire stale-connection failure class."
  scope: Databricks Apps (or any service) backed by Autoscaling/serverless Postgres with short-lived OAuth credentials
  evidence:
    - {project: geniefy-v3, ref: D49, note: "Decided fresh-conn-per-call after a cached conn died; per-call OAuth+connect overhead acceptable for low QPS."}
    - {project: geniefy-v3, ref: LEDGER U89->U93, note: "Live: a cached conn went stale -> /api/library -> /api/sessions -> /api/run all 500 with psycopg2.InterfaceError."}
  confidence: candidate
  strength: "1 decision, surfaced live; eliminated a whole failure class"

- id: geniefy-v3/L2
  claim: "A Databricks Asset Bundle `bundle deploy` reconciles the app's `resources` to the YAML — wiping resource bindings added later via the workspace UI, and with them the service-principal role/grants those bindings provisioned."
  kind: gotcha
  tags: [databricks-asset-bundle, databricks-apps, deploy]
  fix: "For iterative dev redeploys use a code-only path (`bundle sync` + `databricks apps deploy --source-code-path`) which never reconciles resources. Put any independently-deployed Job in its OWN bundle so deploying it can't touch the app's resources. A fresh customer deploy can still use `bundle deploy` (grants provision clean — nothing to wipe)."
  scope: DAB-deployed Databricks Apps whose bindings/grants were added out-of-band (UI), iterated on repeatedly
  evidence:
    - {project: geniefy-v3, ref: D54, note: "Moved the hands-off Job to a SEPARATE bundle because adding it as a task to the app bundle still triggered resource reconciliation that wipes bindings."}
    - {project: geniefy-v3, ref: D35, note: "deploy.sh orchestrates what DAB can't; the grant-safe --code-only path was codified (U77/U78)."}
  confidence: candidate
  strength: "2 decisions + codified in deploy.sh --code-only"

- id: geniefy-v3/L3
  claim: "DAB cannot `${var}`-substitute into a Databricks App's `app.yaml` env, so any host pinned there is a hardcoded literal; serverless/Autoscaling endpoint hosts get recreated (churn), which then breaks that literal and the app looks like its backend is 'gone'."
  kind: gotcha
  tags: [databricks-apps, app.yaml, lakebase, deploy]
  fix: "Prefer the App resource binding (it injects connection env like PG* and OVERRIDES app.yaml) over a hardcoded host. If you must pin a host, target a STABLE branch/endpoint (e.g. production), not a churning dev one. Treat app.yaml as static bootstrap/infra config only."
  scope: Databricks Apps configured via app.yaml + DAB, connecting to serverless endpoints
  evidence:
    - {project: geniefy-v3, ref: D57, note: "Dev Lakebase branch host churned ep-curly-sunset -> ep-broad-bread, breaking the app.yaml-pinned GENIEFY_PG_HOST; repointed to the stable production branch."}
    - {project: geniefy-v3, ref: D30/D48, note: "The geniefy-db resource binding injects PGHOST/PGUSER/PGDATABASE and takes precedence over app.yaml."}
  confidence: candidate
  strength: "1 decision + a live root-caused host churn"

- id: geniefy-v3/L4
  claim: "Databricks Apps on-behalf-of (OBO) user auth requires a one-time operator action in the App UI (User authorization -> +Add scope -> e.g. `sql`); this is a UI scope, NOT an app.yaml key, so nothing in code or config declares it and it is easy to forget."
  kind: prerequisite
  tags: [databricks-apps, oauth, obo, deploy]
  fix: "Document the UI scope add as an explicit step in the deploy runbook. Have the app degrade to a clear per-item error (never a silent service-principal fallback) when the OBO token is absent, so a missing scope is visible rather than silently wrong."
  scope: Databricks Apps that act on behalf of the end user (e.g. writes that must honor the user's own grants)
  evidence:
    - {project: geniefy-v3, ref: D48, note: "OBO via X-Forwarded-Access-Token requires the one-time `sql` UI scope; refinement narrowed user identity to ONLY the apply write + its audit row."}
    - {project: geniefy-v3, ref: LEDGER U78/U85, note: "No silent SP fallback: OBO-capable but no token -> every applyable item fails with an add-the-scope message, nothing written."}
  confidence: candidate
  strength: "1 decision, load-bearing operator step"

- id: geniefy-v3/L5
  claim: "A Unity Catalog SQL function can't run dynamic SQL over an arbitrary table's columns, and a UC Python UDF runs in a per-row sandbox with no Spark/SQL session — so neither can generically profile an arbitrary table."
  kind: gotcha
  tags: [unity-catalog, profiling, sql, design]
  fix: "Back generic table profiling with a small service (e.g. an MCP server) that generates and runs profiling SQL via the warehouse Statement Execution API and returns a sanitized profile. Reserve UC functions for the subset expressible without dynamic SQL (cached ANALYZE / information_schema stats)."
  scope: any feature needing to profile arbitrary UC tables programmatically
  evidence:
    - {project: geniefy-v3, ref: D15, note: "Refined D3: the reference profiling provider is an MCP service over a warehouse; the UC-function path is valid only for the static subset."}
  confidence: candidate
  strength: "1 decision, design constraint"

- id: geniefy-v3/L6
  claim: "When sending table data to an LLM, sanitizing at the source — aggregates, top-K values, patterns, masked samples, never raw rows — is the load-bearing PII control; a downstream gateway guardrail is a secondary enhancement, not the primary boundary."
  kind: pattern
  tags: [llm, pii, governance, design]
  fix: "Shape the profile the model sees to carry only sanitized signal and keep raw sensitive rows inside the governance boundary. Treat gateway masking as defense-in-depth, not the thing you rely on; a technical audience punishes data egress."
  scope: any LLM feature operating over potentially-sensitive customer data
  evidence:
    - {project: geniefy-v3, ref: D4, note: "Generate via in-platform FMAPI; only a sanitized profile (aggregates/top-K/masked) leaves the boundary."}
    - {project: geniefy-v3, ref: D28, note: "At deploy: sanitized-profile-at-source is the PRIMARY PII control; AI-Gateway guardrails are a deploy-time enhancement, not a blocker."}
  confidence: candidate
  strength: "1 decision, reaffirmed at deploy"

- id: geniefy-v3/L7
  claim: "Hermetic unit tests using in-memory fakes pass while a real database column-type/constraint mismatch fails in production — e.g. writing a name string into a uuid FK column — because the fake didn't model the real column type."
  kind: anti-pattern
  tags: [testing, postgres, build]
  fix: "Run a live integration round-trip against the REAL schema for any persistence seam; assert against actual column types/constraints, not just a fake's accept-anything behavior. Keep the unit fakes for logic, but gate the seam on a real round-trip."
  scope: any code persisting to a typed store behind an injected/faked interface
  evidence:
    - {project: geniefy-v3, ref: D38, note: "template_id was a name string but the column was a uuid FK; Postgres rejected it. Hermetic tests were green; live round-trip on the dev branch surfaced it (U47->U48)."}
  confidence: candidate
  strength: "1 decision, found by live test after a green hermetic suite"

- id: geniefy-v3/L8
  claim: "A bulk find/replace edit (e.g. an editor `replace_all`) intended to change N call sites can silently match only ONE — for instance when sites differ by indentation — leaving the others on the old code path; a happy-path test suite with no per-site assertion will not catch it."
  kind: anti-pattern
  tags: [refactoring, testing, build]
  fix: "After an extract-and-wire or bulk edit, verify EACH intended site by grep count and add a guard test per site. Read a claim like 'wired into both X and Y' as two separate checks, not one."
  scope: any refactor that wires a helper into multiple call sites, or any bulk text edit across a file
  evidence:
    - {project: geniefy-v3, ref: audit U145->U146, note: "FAIL: _await_terminal was wired into only the OBO write runner; the SP read path (the real slow path) was missed via a replace_all indentation mismatch; suite had no SP-runner await test."}
    - {project: geniefy-v3, ref: audit U116, note: "Same class recurred: an enumerate replace_all matched only one runner; lesson re-stated as verify-per-site."}
  confidence: candidate
  strength: "recurred >=2 units, one reached FAIL"

- id: geniefy-v3/L9
  claim: "A decision about behavior (what ships vs. stays dev-only, what's excluded, an invariant) that is only documented and not enforced by a gate, config, or assertion silently fails to hold; the absence of enforcement is itself a defect, not just a doc gap."
  kind: anti-pattern
  tags: [process, build, governance]
  fix: "For each behavioral decision, add the corresponding enforcement at the point it bites — a sync-exclude block, a startup fail-fast, a build gate, a test assertion. Make audits check that every stated decision has a matching enforcement, and treat a missing one as a finding."
  scope: any project that records decisions/ADRs separately from the code that must honor them
  evidence:
    - {project: geniefy-v3, ref: audit U13->U15, note: "FAIL: D25 decided '.gotm/ excluded from the deploy artifact' but databricks.yml had no sync:exclude block -> dev tooling would leak into the deployment."}
  confidence: candidate
  strength: "1 FAIL, high-signal"

- id: geniefy-v3/L10
  claim: "A serialize -> deserialize -> serialize round-trip test passes even when a field is dropped from BOTH sides symmetrically, so it cannot catch silent persistence data-loss."
  kind: anti-pattern
  tags: [testing, serialization, build]
  fix: "Assert an explicit expected key-set (`set(obj.to_dict()) == EXPECTED_KEYS`) and/or compare the original object's attributes after the round-trip. Mutation-test the suite — deliberately break each logical branch and confirm a test fails — to find blind spots a single green run hides."
  scope: any to_dict/from_dict (or serialize/deserialize) persistence layer
  evidence:
    - {project: geniefy-v3, ref: audit U27, note: "Dropping mlflow_run_id from both to_dict and from_dict kept all 29 tests green; only mutation testing revealed the loss."}
  confidence: candidate
  strength: "recurred across serialization audits; mutation-testing is the recurring fix"

- id: geniefy-v3/L11
  claim: "A configuration knob can be fully designed and schema'd yet never actually consumed by the code (inert) — the design doc and the runtime silently disagree about what's configurable."
  kind: anti-pattern
  tags: [config, design, build]
  fix: "For each designed knob, verify a real read at a real call site (grep the consumer). If a global mechanism already subsumes it, DROP the knob and record why, rather than leaving a dead config surface that implies a capability that isn't there."
  scope: any feature with a configurable surface designed ahead of (or apart from) the code
  evidence:
    - {project: geniefy-v3, ref: D40, note: "Per-provider token_budget was inert — McpContextProvider consumed only max_snippets; the global context_token_budget already trimmed after cross-provider ranking. Dropped, no code change."}
  confidence: candidate
  strength: "1 decision"

- id: geniefy-v3/L12
  claim: "Databricks Apps (and similar serving layers) have short HTTP request timeouts, so a synchronous multi-second/minute operation times out the request."
  kind: pattern
  tags: [databricks-apps, async, design]
  fix: "Run the work as an in-process background task that flips a DB-backed status and persists partials per phase; return 202 immediately and have the frontend poll a status endpoint until terminal. Add a watchdog that marks stale 'running' sessions failed — recoverable because the state is persisted (keep the work's state serializable so resume is a data load, not a process restart)."
  scope: Databricks Apps (or any short-timeout serving layer) running long operations
  evidence:
    - {project: geniefy-v3, ref: D18, note: "POST /run returns 202 + flips status; worker drives the orchestrator persisting per phase; frontend polls GET /sessions/{id}."}
    - {project: geniefy-v3, ref: D17, note: "Core holds no in-memory session state; a serializable SessionState passes in/out, so pause/resume and a watchdog re-run fall out naturally."}
  confidence: candidate
  strength: "1 decision, architecture-defining"

- id: geniefy-v3/L13
  claim: "Choosing an app/frontend stack without evaluating the platform's own SDK (e.g. Databricks AppKit) is an honest design gap; the evaluation still matters even when a hard constraint ends up ruling the SDK out."
  kind: pivot
  tags: [architecture, design, process]
  fix: "At stack-selection time, explicitly evaluate the platform SDK and record why it's in or out. A cross-language core constraint — e.g. a hermetic Python agent core reused by BOTH an App and a batch Job — can correctly rule out a TS-only SDK; log the reasoning so the choice is deliberate, not accidental, and flag the SDK as a revisit-if condition."
  scope: any project picking an app stack on a platform that ships its own first-party SDK
  evidence:
    - {project: geniefy-v3, ref: D42, note: "AppKit (TS-only) evaluated AFTER deploy; stayed with the Python core + FastAPI/React because AppKit can't host the Python core (D1/D2). Recorded as a revisit-if-the-app-layer-grows option."}
  confidence: candidate
  strength: "1 decision, retroactive but made explicit"
```

## Confidence & merge model

- **Ladder.** `candidate` (seen in 1 project) → `validated` (≥2 *independent* projects confirm the same `claim`) → `core` (enterprise-curated, broadly applicable). A project can't mint `validated` on its own — that needs independent confirmation (the *auditor ≠ author* principle).
- **Strength** weights a candidate by recurrence *within* this project; it never substitutes for cross-project confirmation. A lesson the audits flagged across many units is a sturdy candidate — still a candidate.
- **Merge.** Match incoming records on `claim`; on a match, **append** the new `evidence` and recompute `confidence`. On a **contradiction** (a later project reports the opposite outcome), don't overwrite — flag for review and demote. That demotion path is the decay that keeps the pool from rotting into stale, confidently-wrong advice.
