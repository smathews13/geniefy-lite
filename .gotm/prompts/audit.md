---
prompt: audit
purpose: run a mechanical audit of a claimed-done unit
audience: orchestrating LLM (you read this, then build an audit dispatch)
license: Apache 2.0
---

# Audit prompt

Use this template to audit a claimed-done unit before downstream work consumes it (rule 4 of the protocol). Audits are mechanical: read target, read oracle, return findings in severity tiers. The auditor does not fix issues — fixes are separate units.

Audits run as dispatched subagents per `.gotm/prompts/subagent-dispatch.md`, with the audit-specific shape below.

### Independence (non-negotiable)

An audit is only valid if it is run by a context that did **not** author the unit. The author judging its own work reproduces its own blind spots — that is self-marking, not auditing. So:

- **Dispatch a fresh auditor subagent.** It receives only the Target, the Oracle, and the checks below — **never** the authoring session's transcript or reasoning. `/gotm:audit <Uxx>` constructs exactly this.
- **One unit per audit.** One dispatch produces one `audits/<Uxx>.md` for one unit — no multi-unit reports, no "covered by another unit's audit." (A superseded unit is the only no-own-audit case; its cell reads `superseded by U<yy>`.)
- The auditor reports findings; it does **not** fix them and does **not** mark its own subject `PASS` in the ledger — it returns a verdict; the orchestrator stamps the `Audit` cell.
- If you are the agent that wrote the unit, you may **not** audit it in the same session. Dispatch it. Do it promptly — right after the unit goes `done`, before starting the next.

---

## Paste this into the orchestrating LLM

You are about to dispatch an audit subagent. Build the audit dispatch using the template below.

### 1. Audit kind

Name what is being checked. Common kinds:

    - existence: does the named output file exist at the stated path
    - structure: do the required sections appear; is the format what was promised
    - content-claim: do load-bearing claims in the output trace to the cited sources
    - source-fidelity: do quoted spans match the source verbatim
    - render: does the output render cleanly (markdown, tables, fences, images, links)
    - ledger: does `.gotm/LEDGER.md` remain well-formed under the protocol's rules

Pick one. Audits are atomic — one kind per audit. If multiple kinds are needed, dispatch multiple audits.

### 2. Target

The file(s) being audited:

    Target: <path>
    Unit being audited: <Uxx>

### 3. Oracle

The reference the target is checked against (sources, spec, prior outputs):

    Oracle:
    - <path 1>
    - <path 2>

### 4. What to check

Default to the **5-point checklist** below unless the unit calls for a specialized kind. Each item is one mechanical check with a clear pass/fail answer:

    What to check (default 5-point):
    1. existence              — output exists at the ledger's stated path
    2. spec match             — content matches what the unit promised (sections/structure/length)
    3. cross-reference integrity — every D<n>/U<n>/Q<n> cited exists and says what's claimed
    4. internal consistency   — no contradictions across the audited set
    5. decision fidelity      — output honors the relevant DECISIONS.md entries

Add or substitute kind-specific checks (render, source-fidelity, …) where the unit warrants.

### 5. Severity tiers (universal)

The worker categorizes each finding into one of four tiers:

- **HIGH** — finding blocks the unit from being trusted as done; downstream work cannot consume the output until a fix unit lands.
- **MEDIUM** — finding degrades quality but the output can be cited downstream; fix is desirable, not blocking.
- **LOW** — cosmetic; fix if convenient.
- **UNVERIFIED** — auditor could not check due to missing context, ambiguous spec, or input absence; flag for the practitioner.

### 6. Return format

The audit report the worker writes (typically to `.gotm/audits/<Uxx>.md`):

    # Audit report — <kind> — <date>

    ## Target audited
    File: <path>
    Unit: <Uxx>

    ## Findings

    ### HIGH severity
    - <finding>: <one-paragraph description with cited file:line>
      - Suggested fix unit: <one-line proposal>

    ### MEDIUM severity
    - ...

    ### LOW severity
    - ...

    ### UNVERIFIED
    - <thing-could-not-check>: <what is missing>

    ## Summary
    HIGH: <n> · MEDIUM: <n> · LOW: <n> · UNVERIFIED: <n>
    Verdict: <PASS | PASS-FINDINGS | FAIL>   (HIGH ⇒ FAIL; MEDIUM/LOW-only ⇒ PASS-FINDINGS; clean ⇒ PASS)

### After the audit

The orchestrating agent reads the audit report and acts:

- **Stamp the ledger `Audit` cell** for the audited unit to match the verdict: `PASS→.gotm/audits/<Uxx>.md`, `PASS-FINDINGS→.gotm/audits/<Uxx>.md`, or `FAIL→.gotm/audits/<Uxx>.md`. `PASS`/`PASS-FINDINGS` let downstream consume; a `FAIL` blocks downstream until fix units land and an independent re-audit passes.
- For each **HIGH** finding, append a fix unit to `.gotm/LEDGER.md`. The fix unit's row references the audit report.
- For **MEDIUM** findings, append as optional follow-on units (or batch into a single "polish" unit if many are minor).
- For **LOW** findings, decide whether to act now or defer; if deferring, note in `.gotm/LEDGER.md` recent updates.
- For **UNVERIFIED** items, route the resolution to `.gotm/QUESTIONS.md` if a human decision is needed, or refine the audit's "what to check" and re-run.

The audit cycle is part of the project's normal forward motion. Drift is not avoided by being careful; drift is caught by being checked.
