# Project protocol

This file is the operating protocol for this project. Every agent that opens a session here reads this file first and follows it.

The framework this protocol implements is GOTM — described in the framework repo's `docs/`. Read those once for understanding, this file every session.

> **Layout.** This project keeps the GOTM orchestration file-set in `.gotm/` so the repo root stays reserved for produced assets (code, docs, the deliverable). The root `CLAUDE.md` is a thin bridge that points here so the discipline auto-loads across sessions — see *CLAUDE.md auto-load* below. All relative links in this file resolve inside `.gotm/`.
>
> If you instead keep the file-set at the repo root — fine for a pure writing/research project that produces few files of its own — drop the `.gotm/` prefixes below and delete this note.

## CLAUDE.md auto-load (do not break this)

GOTM's cross-session continuity depends on `CLAUDE.md` being **auto-loaded at the repo root** every session. That auto-load only happens for a root-level `CLAUDE.md`. Because this project's machinery lives in `.gotm/`, a thin `CLAUDE.md` stays at the repo root as a bridge that points into `.gotm/PROTOCOL.md`.

**Failure mode to avoid:** moving the root `CLAUDE.md` into `.gotm/` (to "tidy up") silently stops the discipline from auto-loading — no error, just quiet erosion across sessions. Leave the bridge at the root.

## Session-start checklist

On opening a session in this project:

1. Read this file (`.gotm/PROTOCOL.md`).
2. Read [`LEDGER.md`](LEDGER.md) — the authoritative list of units.
3. Read [`QUESTIONS.md`](QUESTIONS.md) — any open questions blocking work.
4. **Reconcile the ledger against disk** (crash recovery) — heal any drift before acting; see *Resilience* below.
5. Identify the **active unit** (top non-`done` row whose blockers are clear).
6. Act on that unit, or — if a higher-priority concern surfaces — surface it.
7. After acting, write back **in the same turn** — this is a hard gate, see *Anti-drift safeguards* below: update `LEDGER.md`, append to `DECISIONS.md` if a decision was made, update `QUESTIONS.md` if a question opened or closed.

These seven steps are not negotiable. The discipline depends on the project's state being the state of these files.

## The five rules

**1. Single ledger.** `LEDGER.md` is authoritative. State that is not in the ledger is not real state. If two documents disagree, the ledger wins.

**2. Atomic units.** Each row in the ledger is one execution pass producing one named output file. Not two outputs. Not "and a small revision." Not "while we're at it." If a planned unit hides more than one output, split it before work starts. (Atomicity is about one *deliverable*, not literally one file: a module and its test file are one unit — build-and-test is a single pass. The rule forbids *unrelated* outputs in one unit, not a thing and its test.)

**3. Foundation before drafts.** Some units are foundation (gather source, lock decisions, map terrain). Some are drafts (produce on top of foundation). Foundation precedes drafts. Drafts that begin before foundation closes fail by producing fluent prose grounded in nothing.

**4. Audit before downstream consumes.** Every claimed-done unit gets a mechanical check before downstream work depends on it. The check looks at: does the named output exist; does its content match what was promised; do its citations trace. Findings become new ledger units, not edits to closed ones.

**5. Ratification ladder routes human decisions.** Some decisions are the human's. The ladder names which and routes them to `QUESTIONS.md`. See next section.

## The ratification ladder

When a question or decision arises during work, the agent classifies it and routes:

| Layer | What it covers | What happens |
|---|---|---|
| **Mission** | mission, audience, scope, license, what counts as done | Route to `QUESTIONS.md`. Agent waits. |
| **Execution** | next unit, sequencing, word count, atomic split, in-loop vs dispatch | Agent decides, records in `LEDGER.md` or `DECISIONS.md`, proceeds. |
| **Ambiguous** | could be either; agent judges materiality | Agent surfaces with `MATERIAL?` flag. Human routes it. |

The boundary is fixed. The human is not surprised by mission-level decisions made unilaterally; the human is not pulled into execution-level choices.

## Unit lifecycle

A unit moves through states: **pending → in_progress → done**, or **pending → superseded** if scope changes mid-flight.

A unit is "done" when:

- The named output file exists at the stated path.
- The content matches what the unit promised (sections, structure, word count if specified).
- An audit has run on it — or, if explicitly deferred, the deferral is recorded with a reason and a follow-up unit appended.

**Audit deferral is sanctioned, not skipping.** During an interactive design phase where the human reviews each output as it lands, human review *is* the gate — a separate mechanical audit per unit would be friction. In that case, defer the mechanical audit: set the unit's `Audit` cell to `deferred→U<n>` and append the follow-up audit unit `U<n>`. See *Audit gates* for the full lifecycle and the independence requirement.

Mark done by updating the unit's row in `LEDGER.md`. Never edit a done unit's output to "fix" it later; append a new unit that supersedes or follows on. The ledger grows; it does not drift.

## Audit gates

Rule 4 says claimed-done work is checked before downstream consumes it. Two properties make that check meaningful rather than ceremonial — **independence** and a **gate** — and the ledger tracks audit state in its own `Audit` column so neither relies on memory.

**Independence is non-negotiable.** An audit is valid only if it is produced by a context that did **not** author the unit. A working agent blessing its own output reproduces its own blind spots — that is self-marking, not auditing. In practice: **dispatch the audit as a fresh subagent** that receives only the target output, the oracle (the unit's inputs / spec / the ledger), and `prompts/audit.md` — never the authoring session's transcript. Run `/gotm:audit <Uxx>`; it constructs that independent auditor for you. Do not audit a unit in the same session that wrote it.

**What the auditor checks (default 5-point checklist).** Unless the unit calls for a specialized check, the auditor runs all five: (1) **existence** — the output exists at the ledger's stated path; (2) **spec match** — content matches what the unit promised (sections, structure, word count); (3) **cross-reference integrity** — every `D<n>` / `U<n>` / `Q<n>` it cites exists and says what's claimed; (4) **internal consistency** — no contradictions across the audited set; (5) **decision fidelity** — the output honors the relevant `DECISIONS.md` entries. Findings are ranked in severity tiers (HIGH / MEDIUM / LOW / UNVERIFIED).

**Verdict — one of three.** The auditor returns: **`PASS`** (no findings above the trivial bar); **`PASS-FINDINGS`** (passed and consumable, but carries MEDIUM/LOW findings that become tracked non-blocking follow-on units); **`FAIL`** (one or more HIGH findings — blocks). HIGH ⇒ FAIL; MEDIUM/LOW-only ⇒ PASS-FINDINGS; clean ⇒ PASS.

**The gate.** A unit's `Audit` value is one of: `—` (no audit needed) · `pending` (done but unchecked) · `deferred→U<n>` (audit consciously deferred to a recorded follow-up unit) · `PASS→audits/U<id>.md` · `PASS-FINDINGS→audits/U<id>.md` · `FAIL→audits/U<id>.md`. A downstream unit may **consume** an input only when that input's `Audit` is `PASS`, `PASS-FINDINGS`, or `deferred→U<n>` (with the follow-up unit actually present). Drafts and code do not get built on `pending` or `FAIL` foundation. `done` (the output exists) and a passing verdict (an independent context checked it) are distinct states; the column keeps them honest.

**Findings become units, not edits.** The auditor does not fix — it writes findings to `audits/<Uxx>.md`. Each HIGH finding becomes a new fix unit appended to `LEDGER.md`; MEDIUM/LOW findings under a `PASS-FINDINGS` become tracked follow-on units too. The audited unit's output stays frozen. A `FAIL` blocks downstream until the fix units land and an independent re-audit returns `PASS`/`PASS-FINDINGS`.

**Deferral stays honest.** `deferred→U<n>` is allowed during active human review (the human is the interim gate), but the follow-up audit unit `U<n>` must exist in the ledger **and the independent audit must run before any code/build unit consumes the design** — design may be human-reviewed on the way in, but code does not get built on a deferral. Session-start reconciliation flags two smells: a `done` unit whose `Audit` is still `pending` and has a downstream consumer, and a `deferred→U<n>` whose `U<n>` is missing.

**Cadence — one audit, one unit, promptly.** The gate's *letter* (downstream waits for `PASS`) holds even when its *spirit* slips, so three cadence invariants are explicit:

- **One report per unit.** Each unit gets its own audit dispatch and its own `audits/<Uxx>.md`. No multi-unit reports.
- **Own-report only.** A unit's `Audit` cell is `PASS`/`PASS-FINDINGS`/`FAIL` from *its own* report — never "covered by another unit's audit." The one exception is a superseded unit, whose cell reads `superseded by U<yy>` (its output was replaced and re-audited under `U<yy>`).
- **Audit promptly.** Dispatch a unit's audit in — or right after — the turn it becomes `done` (`/gotm:audit <Uxx>`), before starting the next unit. Don't let `Audit: pending` pile up: audit-as-you-go silently degrades into audit-in-batches, and batched-later auditing erodes into rubber-stamping.

## Anti-drift safeguards (operational)

The five rules say *what* the discipline is. These checks make the two ways it erodes — **silent work** (acting without writing back) and **quiet edits** (changing a frozen artifact instead of appending) — mechanically catchable. Run them; do not rely on memory. (Memory-based discipline is the exact thing GOTM exists to not rely on.)

**Pre-edit check — run before every `Edit`/`Write`:**

1. Is the target path the `Output` of a unit whose Status is `done` in `LEDGER.md`? → **STOP. Do not edit it.** A done unit's output is frozen. Append a follow-on / superseding unit and put the change in the *new* unit's output.
2. Is the target a prior substantive entry in `DECISIONS.md` or `QUESTIONS.md`? → **Append a new entry instead.** (Allowed exception: updating a Status line to mark a question `answered` or a decision `superseded by D<n>` — that is the documented mechanism, not a substantive edit.)
3. Otherwise — a **living governance doc** (`PROTOCOL.md`, `CLAUDE.md`, `README.md`) or a `pending` / `in_progress` unit's output → editing is allowed. The immutability rule applies to unit outputs and closed decision/question entries, *not* to the living governance docs, which must evolve.

**Write-back gate — a unit's work and its ledger write-back are one turn, not two.** You may not end a turn in which you created or changed a unit's output without, in that same turn: updating the unit's row in `LEDGER.md`, appending to `DECISIONS.md` if a decision was made, and updating `QUESTIONS.md` if a question opened or closed. Output produced but not written back = the unit is **not** done; write back before yielding.

**Done-means-written.** Never set a unit's Status to `done` unless its named output file actually exists at the stated path with the promised content. Verify, don't assume.

**Turn-end self-check — before yielding any turn that touched the project, answer three questions:**

- Did I produce or change a unit output? → Is its `LEDGER.md` row updated to match?
- Did I make an execution decision? → Is it in `DECISIONS.md` (or captured as a new unit)?
- Did a question open or get answered? → Is `QUESTIONS.md` updated?

Any "no, but I should have" → fix it before the turn ends.

> **Optional hard enforcement.** This project ships a `PreToolUse` hook at `.gotm/hooks/gotm-immutability.py` that converts the pre-edit check from advisory to *enforced by the harness* — it blocks an `Edit`/`Write` whose target is a `done` unit's frozen output. Wire it in `.claude/settings.json` (see `.gotm/hooks/README.md`). It must be present at the **start** of a session to fire; a hook wired mid-session needs a **full restart** to activate (opening and dismissing `/hooks` is *not* enough).

## Resilience — no context loss across any session end

GOTM's core promise is that the on-disk state alone reconstructs full working context — so an accidental session end (crash, closed terminal, killed process) with **no resume** loses nothing. The write-back gate covers clean turn-ends; these rules close the mid-turn crash window and make cold restarts self-healing.

**Invariant — transcript independence.** At every yield point, the `.gotm/` files must be sufficient to resume *without* the chat transcript. Never hold decision-relevant state only in conversation. Treat `LEDGER.md` → Recent updates as the recovery log — rich enough that a cold session understands not just what is done but where we are and why.

**Crash-safe write ordering (journaling).** When executing a unit:

1. Mark the unit `in_progress` in `LEDGER.md` **before** producing its output.
2. Produce the single output file.
3. Mark the unit `done` and append any `DECISIONS.md` / `QUESTIONS.md` entries.

A crash between (1) and (3) leaves a recoverable trail — never a silent gap.

**A new unit is born `pending` or `in_progress` — never `done`.** Flip it to `done` only after its output exists and is verified, in the same turn. This is the same ordering, stated as a guard against a self-inflicted foot-gun: if you register a follow-on (e.g. a fix unit) as `done` *before* writing its output, the immutability hook freezes that done unit's output and locks you out of the very file you were about to create. Batch-registering several follow-ons at once makes `done` the path of least resistance — resist it; register them `pending`/`in_progress`.

**Bound the loss — size units to their loop (heavy/iterative work).** Never wrap a long loop (columns, tables, batches) in one un-checkpointed pass. Either split into per-iteration units, or checkpoint per iteration to the working state so reconciliation resumes at the last completed iteration — a crash then costs one iteration, not the whole unit.

**Session-start reconciliation (crash recovery).** Before acting (checklist step 4), reconcile the ledger against disk and heal drift:

- `done` row whose output file is **missing** → reopen the unit; note in Recent updates.
- Output file **exists** for a non-`done` unit → an interrupted unit: inspect, then finalize to `done` (if complete) or supersede with a follow-on unit (if partial).
- `in_progress` unit → resume/verify exactly that unit.
- **Audit-gate lint:** a `done` unit whose `Audit` is `pending` and that a downstream unit consumes → audit it (independently) before that downstream proceeds; a `deferred→U<n>` whose follow-up `U<n>` is missing → restore it or audit now. See *Audit gates*.
- Record what reconciliation found/did in Recent updates (recovery is auditable).

Recovery produces new ledger entries, never silent edits to closed units. This does not make the crash window literally zero (a crash *during* the ledger write itself still exists), but it makes every outcome **recoverable** — the bar is "no *unrecoverable* context loss."

## Off-mission artifacts

Sometimes the human asks for something that does not serve the mission — a side note, a one-off export, meta-feedback about the process itself. "Single ledger / everything is a unit" tempts you to file it as a unit, but it isn't mission work and adding it to the unit table pollutes the unit graph.

Convention: **produce the file, then drop a one-line breadcrumb in `LEDGER.md` → Recent updates marked _not a mission unit_.** Do not add it to the Units table. This keeps traceability without distorting scope.

## When to dispatch a subagent

Dispatch a subagent when:

- The unit's work exceeds the current session's bandwidth (long deliverable, large input set).
- An audit needs an independent context — the agent that did the work cannot audit the same work (Rule 4; see *Audit gates*). `/gotm:audit <Uxx>` dispatches that independent auditor.
- Multiple atomic units can run in parallel without shared state.

The dispatch prompt carries:

- A pointer to `.gotm/PROTOCOL.md` (this file) — so the worker reads the discipline.
- The bounded inputs the worker is allowed to read.
- The single named output path.
- The output spec (sections, word count, voice).
- The constraints (banned phrases, format, etc.).

The worker does not see the rest of the project. The worker reads the protocol and its own task and produces one output. See `prompts/subagent-dispatch.md` for the canonical dispatch template.

## Common moves

| Move | Use |
|---|---|
| Starting a session | `prompts/session-start.md` for the kickoff template |
| Dispatching a subagent | `prompts/subagent-dispatch.md` |
| Running an audit | `prompts/audit.md` |

## Mission (this project)

> An agent — delivered as a Databricks App — that turns any Unity Catalog table into an AI/Genie-ready asset by profiling its data and usage context and generating reviewable, template-conformant table and column comments, with a governed path to apply them to Unity Catalog.
