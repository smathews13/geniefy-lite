# Feedback on GOTM — from bootstrapping & running `geniefy-v3`

**Author context:** notes captured while using GOTM to drive a real software project (`geniefy-v3` — an agentic UC table-documentation tool), 2026-06-11. Used in a single live session: bootstrapped the file-set, locked 15 decisions, ran 3 units (HLD + 2 LLDs), and hardened the protocol mid-stream.
**Audience:** maintainers of the GOTM framework (`gotm-framework-for-agentic-development/` docs + `gotm/templates/`).
**Purpose:** concrete, liftable changes to fold back into the framework. This is *not* geniefy-v3 project state — it's out-of-band meta-feedback (logged as such in our ledger).

---

## TL;DR

GOTM's core bet — **materialize the discipline in the filesystem so it survives session boundaries** — held up well in practice. The ratification ladder and append-only DECISIONS log were the standout wins. The biggest gap: the rules were stated but had **no operational catch**, so they relied on agent memory (which is exactly what GOTM claims to *not* rely on). We patched that locally; it belongs in the template. A few smaller gaps around repo layout, governance-doc editability, and off-mission work are worth documenting.

Severity legend: **[P1]** undermines a core GOTM promise · **[P2]** real friction · **[P3]** polish.

---

## What worked well (keep / amplify)

1. **Filesystem-as-memory genuinely works.** After a chain of decisions, a cold reader could reconstruct the whole project from `.gotm/` alone. We verified this explicitly and it passed. This is the central value prop and it delivers.
2. **The ratification ladder is the best part.** Cleanly separating *human-owned* (mission, audience, scope, license) from *agent-owned* (sequencing, unit splits, word counts) removed a whole class of "should I ask or just decide?" stalls. It made the human↔agent boundary legible.
3. **Append-only `DECISIONS.md` (ADR-style) captured the "why" that's normally lost.** By unit 3 we had 15 decisions; each was a one-screen rationale a newcomer could read. This is where most of the project's actual value-of-thinking lived.
4. **`QUESTIONS.md` as a non-blocking parking lot** let design proceed while mission-level questions (audience/license) stayed open without blocking. The "blocking vs non-blocking" tag on each question was useful.
5. **Atomic units + "foundation before drafts"** kept us from jumping to code. Forcing "one unit = one output file" made the work legible and the ledger honest.

---

## Gaps & friction (with recommended changes)

### G1 **[P1]** The anti-drift rules had no operational catch
**Observed.** `PROTOCOL.md` stated "never edit a done unit's output" and the session-start checklist said "write back." But there was no *procedure* to catch the two failure modes in the moment — silent work (acting without writing back) and quiet edits (changing a frozen artifact). Both depend on the agent *remembering* mid-task. That's the exact thing GOTM exists to avoid, yet the enforcement lived only in agent memory.

**What we did.** Added an **"Anti-drift safeguards (operational)"** section: a **pre-edit check** (run before every Edit/Write), a **write-back gate** (unit work + ledger write-back in the same turn), **done-means-written** (verify the file exists before marking done), and a **turn-end self-check**. Surfaced the two critical invariants in the auto-loading `CLAUDE.md`.

**Recommendation.** Add this section to `PROTOCOL.md.template` verbatim (text in Appendix A). It converts the rules from aspirational to checkable. **Strongest version:** ship an optional `PreToolUse` hook (Appendix B — a **working, validated reference implementation**, built for this project, not a sketch) that reads the ledger and *blocks* an Edit/Write whose target is a `done` unit's output — moving enforcement from "agent follows the doc" to "the harness refuses." Memory-based discipline is the thing GOTM distrusts; the framework should offer this non-memory enforcement path out of the box.

### G2 **[P2]** No guidance on *where* the file-set lives relative to produced assets
**Observed.** The bootstrap drops the file-set at repo root. For a software project that produces lots of its own files, mixing orchestration (`PROTOCOL/LEDGER/...`) with deliverables (code, docs) clutters the root and risks collisions. We moved the machinery into `.gotm/` and kept only a thin bridge at root.

**Recommendation.** Make the **`.gotm/` subfolder layout a documented, first-class option** in the bootstrap ("orchestration in `.gotm/`, produced assets in the main tree"). Provide both layouts and say when to pick which (writing/research project → root is fine; software project → `.gotm/`).

### G3 **[P1]** The `CLAUDE.md` auto-load dependency is implicit — and silently breaks the subfolder layout
**Observed.** GOTM's cross-session continuity *depends* on `CLAUDE.md` auto-loading, which only happens at the **repo root**. If a user (reasonably) moves the whole file-set into `.gotm/` to keep things tidy, the discipline **silently stops auto-loading** — the worst kind of failure (no error, just quiet erosion). We kept a thin root `CLAUDE.md` bridge pointing into `.gotm/PROTOCOL.md`.

**Recommendation.** The bootstrap should (a) explain *why* `CLAUDE.md` must be at root (auto-load), and (b) if using the `.gotm/` layout, automatically leave a root bridge `CLAUDE.md` pointing inward. Call out the failure mode explicitly in `PROTOCOL.md`.

### G4 **[P2]** "Never edit a done unit" reads as absolute — but governance docs must stay editable
**Observed.** Taken literally, the immutability rule would forbid editing `PROTOCOL.md`, `CLAUDE.md`, `README.md` — but those are *living* and must evolve (we hardened `PROTOCOL.md` itself this session). We had to reason out the carve-out: the no-edit rule applies to **unit outputs and closed decision/question entries**, not to living governance docs.

**Recommendation.** State the **"living governance docs vs. unit outputs"** distinction explicitly in the template (we encoded it as pre-edit check rule #3). Without it, a literal-minded agent either freezes the protocol or violates the rule.

### G5 **[P2]** Off-mission / side-quest work has no home
**Observed.** Mid-project the human asked for an off-mission artifact (this very file). GOTM's "single ledger / everything is a unit" implies it should be a unit — but it doesn't serve the mission, and forcing it into the mission ledger pollutes the unit graph. There's no documented convention for out-of-band work.

**Recommendation.** Add a short **"off-mission artifacts"** convention: produce the file, drop a one-line breadcrumb in `LEDGER.md` → Recent updates marked *not a mission unit*, and don't add it to the unit table. (That's what we did.) Gives traceability without polluting scope.

### G6 **[P2]** "Audit before downstream consumes" is heavy during active human review
**Observed.** Rule 4 wants a mechanical audit of each done unit before downstream consumes it. During an interactive design phase where the human reviews each doc as it lands, a separate mechanical audit per unit is friction. We deferred the audit to human review and logged a follow-up audit unit (U7).

**Recommendation.** The template should **bless "human review serves as the gate at the design stage, with the mechanical audit logged as a deferred follow-up unit"** as a sanctioned pattern — so deferring isn't conflated with skipping. Note the deferral must be *recorded* (reason + follow-up unit), which keeps it honest.

### G7 **[P3]** Template references prompt files the bootstrap doesn't create
**Observed.** `PROTOCOL.md.template` links to `prompts/session-start.md`, `prompts/subagent-dispatch.md`, `prompts/audit.md`. The bootstrap doesn't create a `prompts/` dir, so a fresh project has dangling links. (The template even says "add the prompt as you go," but the links read as broken.)

**Recommendation.** Either ship the three prompt files in the bootstrap, or make the references conditional ("if present") / move them to a "see framework repo" note.

### G8 **[P3]** Manual date/`last_updated` stamps drift easily
**Observed.** `LEDGER.md` `last_updated` and `DECISIONS.md` dates are hand-maintained — easy to forget, easy to drift.

**Recommendation.** A convention (or the same hook from G1) to stamp `last_updated` on write-back would remove a small but real source of drift.

### G9 **[P3]** Bootstrap examples feel writing/research-shaped, not software-shaped
**Observed.** The "first 2-3 foundation units" guidance and unit examples read like they originated for writing/research deliverables. For a software project, the natural foundation units are **HLD then per-component LLDs** (which is what we did) — but we had to map that ourselves.

**Recommendation.** Add a software-project worked example to the bootstrap (HLD/LLD as foundation units, code/build units after, an audit unit before code consumes the design). Lowers the translation cost for engineering use.

---

### G10 **[P1]** The write-back gate alone doesn't guarantee the core promise under *hard* session ends
**Observed.** GOTM's headline claim is "no context loss across session boundaries." The write-back gate (G1) enforces that at *clean* turn-ends — but two cases slip through: (a) a crash/kill **mid-turn**, after a unit's output file is written but before `LEDGER.md` is updated, leaves an orphaned output the ledger doesn't know about; (b) a **cold restart with no resume** has no procedure to detect/heal that drift — the new session trusts the ledger and may redo or skip work. We hit the adjacent reality that even a *resume* isn't guaranteed (a dismissed `/hooks`, a closed terminal, a killed process), so the framework must assume the worst: accidental end, no resume.

**What we did (this project).** Added to `PROTOCOL.md`: (1) a **transcript-independence invariant** — on-disk state alone must reconstruct context; never hold decision state only in chat; (2) **crash-safe write ordering** — mark unit `in_progress` → produce output → mark `done` + decisions, so a mid-turn crash leaves a recoverable trail; (3) a **session-start reconciliation** step (now checklist step 4) that compares ledger ↔ disk and heals drift (done-row-but-missing-file → reopen; file-exists-for-non-done-unit → finalize or supersede; `in_progress` → resume), recording findings in Recent updates; (4) **size-to-the-loop for heavy/iterative work** — split into per-iteration units or checkpoint per iteration so a crash costs one iteration, not the whole unit. Also surfaced a reconcile-on-start bullet in the auto-loaded `CLAUDE.md`.

**Recommendation.** Fold all three into `PROTOCOL.md.template` (text in Appendix C) and the reconcile bullet into `CLAUDE.md.template`. This is what makes the "no context loss" promise true under accidental/hard ends, not just graceful ones — arguably GOTM's most important guarantee and, today, the least operationalized.

### G11 **[P1]** Rule 4 names audit independence but doesn't operationalize it — no procedure, no independence mechanism, no gate
**Observed.** The template states Rule 4 ("audit before downstream consumes") and notes audits need "independent context," but gives **no concrete checklist**, **no mechanism to guarantee the auditor ≠ the author**, and **no actual gate** that blocks downstream. In practice this lets the authoring agent rubber-stamp its own work — or skip it (we found ourselves repeatedly deferring "to human review"). For a framework whose whole point is *trustworthy* autonomous execution, an audit you can't trust is worse than none.

**What we did (this project).** Added an **"Audit gates"** section operationalizing it: **independence** via a dispatched subagent (fresh, bounded context — inputs + output + spec only, never the authoring conversation); a **5-point checklist** (existence · spec-match · cross-reference integrity · internal consistency · decision fidelity); **verdicts** (PASS / PASS-WITH-FINDINGS / FAIL) in `audits/`; and a **hard gate** (code waits for PASS; findings → new units, never silent edits). Surfaced a non-negotiable bullet in `CLAUDE.md`.

**Recommendation.** Fold into `PROTOCOL.md.template` (Appendix D) + `CLAUDE.md.template`. Make `prompts/audit.md` **real** — ship a subagent audit-dispatch prompt (the template already references it; see G7). Consider a deterministic gate later: a `PreToolUse` hook that blocks edits to a code/build unit's outputs while the audit covering its inputs is absent or `FAIL` (sibling to the immutability hook, Appendix B).

### G12 **[P2]** Audit *cadence* drifts: "audit-as-you-go" silently degrades into "audit-in-batches," and "covered-by-a-sibling-unit's-audit" is a tempting illegitimate shortcut
**Observed.** Even with the Audit-gates section (G11) in place, the discipline eroded at the edges in a long autonomous run: several done units (frontend screens, the apply-wiring) sat `Audit: pending` while downstream kept being built, then got audited in a **batch**; two units were covered by **one** report (`U59-U60.md`); and four test units had their `Audit` cell stamped *"covered by the module's audit"* rather than each getting a dedicated audit. The human had to intervene: *"the audit should be unit-wise and independent subagent, always."* The gate ("downstream waits for PASS") technically held — nothing consuming an un-audited output proceeded — but the *spirit* (one independent check per unit, promptly) slipped, because the protocol never explicitly **forbids** batched reports, "covered-by" stamps, or letting `pending` pile up.

**What we did (this project).** Recorded **D41**: every done unit gets its own independent unit-wise subagent audit; no batched reports; no "covered-by" stamps (a *superseded* unit — output replaced + audited under the superseding unit — is the only legitimate no-own-audit case, and it must say "superseded by Uyy"). Split the batched report; re-audited the covered-by units individually; audited the one never-audited unit; and audited every fix unit.

**Recommendation.** Add three explicit invariants to `PROTOCOL.md.template` → Audit gates (Appendix D): **(1) one audit dispatch + one `audits/<Uxx>.md` per unit** (no multi-unit reports); **(2) the `Audit` cell is only ever PASS/PASS-FINDINGS/FAIL from the unit's *own* report** — the sole exception is `superseded by Uyy`; **(3) dispatch a unit's audit in or right after the turn it becomes `done`, before starting the next unit** (don't let `Audit: pending` accumulate). Optionally have `/gotm:what` warn when more than ~2 units sit `done` + `pending`. A natural companion to the G11 deterministic gate.

### G13 **[P3]** The frozen-file + atomicity rules make a multi-file "findings sweep" heavy — N one-line cosmetic fixes ⇒ N follow-on units + N ownership-transfers
**Observed.** Clearing a batch of LOW audit findings (e.g., dropping three unused imports across three frozen modules, fixing a stale docstring) required, by the rules, one follow-on unit **and** one ownership-transfer (messy-cell rewrite to release the immutability hook) **per file** — high ceremony for trivial, zero-risk edits. The discipline is correct (traceability + the hook), but the cost/benefit for cosmetic LOWs is poor and actively tempts a corner-cut (the very thing the rules exist to prevent).

**What we did (this project).** Paid the full ceremony anyway — module+test pairs as single units where possible (already-blessed grain), one fix unit per remaining file, ownership-transfer each — and wrote up the friction here rather than shortcutting.

**Recommendation.** Keep the rule, but reduce the cost two ways: **(a)** explicitly bless the **"module + its test = one unit"** grain in the template (we rely on it; it halves the count); and **(b)** consider a sanctioned **"cleanup unit"** pattern — a single follow-on unit may own a *named set* of trivial edits across multiple files **iff** every edit is a verbatim auditor LOW recommendation (rec-applying, D27) and the unit row enumerates each file. That preserves traceability without N-fold ceremony; the immutability hook would need a companion that recognizes a declared multi-file cleanup unit. (P3 — only worth doing if findings-sweeps recur.)

### G14 **[P2]** Registering a follow-on unit as `done` *before* writing its output trips the immutability hook — the crash-safe ordering is a foot-gun when batch-registering
**Observed.** When clearing audit findings, the natural move is to register the fix unit and edit its file in one go — and it's tempting to write the new ledger row with status `done` immediately. Doing so **locks you out of your own file**: the immutability hook (rightly) freezes any `done` unit's clean-path output, so the very next edit to produce that output is blocked (`'tests/...py' is the output of unit U72, which is marked DONE … therefore frozen`). The Resilience section already prescribes `in_progress`→output→`done`, but nothing flags that **a new unit must never be born `done`** — and batch-registering several follow-ons at once makes `done` the path of least resistance.

**What we did (this project).** Hit it exactly once (U72), recognized it as the crash-safe-ordering rule biting, flipped the row to `in_progress`, produced + verified the output, then `done`. No corner-cut.

**Recommendation.** State it explicitly in `PROTOCOL.md.template` (Resilience + Anti-drift) and the `gotm` skills: **"A unit is registered `queued`/`in_progress`, never `done`; flip to `done` only after its output exists and is verified — in the same turn (write-back gate)."** Optionally make the immutability hook's block message name the fix directly ("if you are *producing* this output now, set the unit to `in_progress` first"). Cheap to document; removes a self-inflicted block that, handled carelessly, tempts disabling the hook.

## Suggested priority order for the framework

1. **G10** — resilience: session-start reconciliation + crash-safe write ordering + transcript-independence. Makes GOTM's *core promise* (no context loss) hold under accidental/hard ends, not just clean ones. Most fundamental.
2. **G11** — operationalize audit gates: independence (auditor ≠ author, via dispatched subagent), a concrete checklist, verdicts, and a real gate. Without this, Rule 4 is aspirational and an autonomous run can't be trusted.
3. **G1** — add the anti-drift safeguards to the template (+ the enforcement hook). Closes the gap between GOTM's promise and its mechanism.
4. **G3** — fix/document the `CLAUDE.md` auto-load dependency. Silent continuity loss is a scary failure.
5. **G4** — codify governance-docs-vs-unit-outputs.
6. **G2** — document the `.gotm/` layout option.
7. **G5, G6** — off-mission convention + sanctioned audit deferral.
8. **G7, G8, G9** — polish.

---

## Sync checklist — applying this to the GOTM plugin

Concrete map of each improvement to the plugin file(s) to change, so this feedback can directly **sync the `gotm` plugin** (plugin root `gotm/` with `templates/` + the `gotm:gotm` skill). Liftable text is in the appendices.

| # | Change | Plugin file(s) to edit | Source |
|---|---|---|---|
| 1 | **Anti-drift safeguards** (pre-edit check, write-back gate, done-means-written, turn-end self-check) + governance-docs-vs-unit-outputs carve-out | `templates/PROTOCOL.md.template` (new section); session-start step "write back **in the same turn**" | Appendix A · G1 · G4 |
| 2 | **Resilience** (reconcile step in session-start checklist; Resilience section: transcript-independence, crash-safe `in_progress`→output→`done` ordering, size-to-the-loop) | `templates/PROTOCOL.md.template`; `templates/LEDGER.md.template` (Recent-updates = recovery log; `in_progress` usage) | Appendix C · G10 |
| 3 | **Audit gates** (independence via dispatched subagent, 5-point checklist, verdicts, hard gate) | `templates/PROTOCOL.md.template` (new section) | Appendix D · G11 |
| 4 | **Non-negotiables block** (frozen units · write-back · resilience/cold-start · audit independence) surfaced for auto-load | `templates/CLAUDE.md.template` | Appendices A/C/D · G1/G10/G11 |
| 5 | **Enforcement hook** (immutability) + settings snippet; bootstrap creates them at init (activation caveat: restart needed, not `/hooks`-dismiss) | new `templates/hooks/gotm-immutability.py` + a settings snippet; `gotm:gotm` skill bootstrap | Appendix B · G1/G10 |
| 6 | **Make referenced prompts real** (the template links `prompts/audit.md`, `prompts/session-start.md`, `prompts/subagent-dispatch.md` but the bootstrap never creates them) — esp. an audit-dispatch prompt | new `prompts/*.md` | G7 · G11 |
| 7 | **`.gotm/` layout option + root `CLAUDE.md` bridge + auto-load caveat** (document the dependency that silently breaks if buried in a subfolder) | `gotm:gotm` skill (bootstrap steps) + `README.md.template` | G2 · G3 |
| 8 | **Software-project worked example** (HLD/LLD as foundation units, code units after, audit unit before code) | `gotm:gotm` skill | G9 |
| 9 | **Auto-stamp `last_updated` / dates** (convention or hook) | `templates/LEDGER.md.template` / hook | G8 |

After syncing: bump the plugin version and add a changelog entry referencing these. Priority order is in the section above (G10 → G11 → G1 first).

> Provenance: all of the above was derived and battle-tested while bootstrapping & running **geniefy-v3** under GOTM (25 decisions, 9 units). The appendices below carry the verbatim, paste-ready text.

## Appendix A — Anti-drift safeguards text (lift into `PROTOCOL.md.template`)

> Insert after the "Unit lifecycle" section.

```markdown
## Anti-drift safeguards (operational)

The five rules say *what* the discipline is. These checks make the two ways it
erodes — **silent work** (acting without writing back) and **quiet edits**
(changing a frozen artifact instead of appending) — mechanically catchable.
Run them; do not rely on memory.

**Pre-edit check — run before every `Edit`/`Write`:**
1. Is the target path the `Output` of a unit whose Status is `done` in
   `LEDGER.md`? → STOP. Do not edit it. Append a follow-on / superseding unit
   and put the change in the *new* unit's output.
2. Is the target a prior substantive entry in `DECISIONS.md` or `QUESTIONS.md`?
   → Append a new entry instead. (Allowed exception: updating a Status line to
   mark a question `answered` or a decision `superseded by D<n>`.)
3. Otherwise — a living governance doc (`PROTOCOL.md`, `CLAUDE.md`, `README.md`)
   or a `pending`/`in_progress` unit's output → editing is allowed.

**Write-back gate — a unit's work and its ledger write-back are one turn.**
You may not end a turn in which you created or changed a unit's output without,
in that same turn, updating `LEDGER.md` (and `DECISIONS.md`/`QUESTIONS.md` as
needed). Output produced but not written back = the unit is not done.

**Done-means-written.** Never set a unit's Status to `done` unless its named
output file actually exists at the stated path with the promised content.

**Turn-end self-check — before yielding any turn that touched the project:**
- Produced/changed a unit output? → Is its `LEDGER.md` row updated?
- Made an execution decision? → Is it in `DECISIONS.md` (or a new unit)?
- A question opened/answered? → Is `QUESTIONS.md` updated?
Any "no, but I should have" → fix it before the turn ends.
```

Also add the two-line "Non-negotiables" block to `CLAUDE.md.template` so it rides in the auto-loaded context every session.

## Appendix B — Enforcement hook (working reference implementation)

A `PreToolUse` hook on `Edit|Write|MultiEdit` that converts the pre-edit check from advisory to **enforced by the harness**. Built and validated for geniefy-v3 (script at `.gotm/hooks/gotm-immutability.py`, wired in `.claude/settings.json`). Lift both into the framework as an opt-in.

**How it works:** on every Edit/Write/MultiEdit, the hook reads the payload from stdin, parses `LEDGER.md` for rows whose Status is `done`, and if the target file is one of those frozen Output paths it returns a `deny` decision naming the unit and telling the agent to append a follow-on unit instead.

**Three robustness decisions that matter (learned building it):**
1. **Derive project root from the script's own location** (`<root>/.gotm/hooks/`), not from cwd or a hardcoded path — so it survives being cloned to any path.
2. **Signal deny via stdout JSON (`permissionDecision: deny`), never via a non-zero exit code.** This lets the settings command wrap with `|| true` for fail-open *without* masking the deny. Critically, it also means a *missing* script (`python3` exit 2) fails **open** (allow) rather than exit-2-blocking *every* edit in the repo.
3. **Fail-open on any exception** (`try/except → exit 0`). A guard bug must never brick editing; the doc-level safeguards remain the backstop.

**`settings.json` snippet:**
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          { "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/.gotm/hooks/gotm-immutability.py\" || true",
            "timeout": 10,
            "statusMessage": "GOTM immutability check" }
        ]
      }
    ]
  }
}
```

**The script** (`.gotm/hooks/gotm-immutability.py`):
```python
#!/usr/bin/env python3
"""GOTM immutability guard — PreToolUse hook (Edit | Write | MultiEdit).
Blocks edits to a *done* unit's output (frozen). Deny via stdout JSON; fail-open."""
import json, os, re, sys

UNIT_ROW = re.compile(r"^\|\s*U\d+\s*\|")

def frozen_outputs(ledger_path, root):
    out = {}
    with open(ledger_path, encoding="utf-8") as fh:
        for line in fh:
            if not UNIT_ROW.match(line):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 3:
                continue
            unit_id, status = cells[0], cells[-1].lower()
            output = cells[-2].strip().strip("`").strip()
            if status != "done" or not output or output in ("—", "-"):
                continue
            out[os.path.realpath(os.path.join(root, output))] = unit_id
    return out

def main():
    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    target = (payload.get("tool_input") or {}).get("file_path")
    if not target:
        return
    script_dir = os.path.dirname(os.path.realpath(__file__))
    root = os.path.dirname(os.path.dirname(script_dir))   # .gotm/hooks -> .gotm -> root
    ledger = os.path.join(root, ".gotm", "LEDGER.md")
    if not os.path.isfile(ledger):
        return
    if not os.path.isabs(target):
        target = os.path.join(os.getcwd(), target)
    target_abs = os.path.realpath(target)
    unit_id = frozen_outputs(ledger, root).get(target_abs)
    if not unit_id:
        return
    rel = os.path.relpath(target_abs, root)
    reason = (f"GOTM immutability guard: '{rel}' is the output of unit {unit_id}, marked "
              f"DONE in .gotm/LEDGER.md and therefore frozen. Do NOT edit it. Append a "
              f"follow-on / superseding unit instead (see .gotm/PROTOCOL.md).")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason}}))

if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)   # fail-open
```

**Validation performed:** pipe-tested 6 cases (done outputs deny — incl. relative paths; pending-unit / governance-doc / off-mission files allow); `jq -e` schema check on the settings; end-to-end run through the exact wrapped command. All passed.

**Activation caveat — a real finding the framework should document (empirically confirmed here).** A `.claude/settings.json` that did **not** exist at session start is **not** hot-loaded into the running session. The settings watcher only tracks directories that had a settings file when the session started, and even then a brand-new file is not reliably picked up. In testing for this project: a probe edit against a frozen done-output (`HLD.md`) still reached the Edit tool — i.e. the guard did not fire — and **opening the `/hooks` dialog and *dismissing* it, twice, did NOT activate the hook.** Only a **full restart** is reliable. (A `/hooks` interaction that actually *commits* a change may also reload, but a dismiss does not.) **New sessions auto-load it regardless.** So an adopter who wires this mid-session will see the hook silently not fire and wrongly conclude it's broken. **Strong recommendation:** the bootstrap should **create the hook + `settings.json` as part of the initial file-set** so they're present at first session start; if wired later, tell the user a **restart** is required (opening+dismissing `/hooks` is insufficient).

This is the only version that doesn't depend on the agent remembering to run the check — which is the whole point of GOTM.

## Appendix C — Resilience text (lift into `PROTOCOL.md.template` + `CLAUDE.md.template`)

> Add a reconciliation step to the session-start checklist, a "Resilience" section to `PROTOCOL.md`, and a reconcile-on-start bullet to `CLAUDE.md`.

Session-start checklist — add as a step **before** "identify the active unit":
```markdown
4. **Reconcile the ledger against disk** (crash recovery) — heal any drift
   before acting; see *Resilience* below.
```

`PROTOCOL.md` — new section:
```markdown
## Resilience — no context loss across any session end

GOTM's core promise is that the on-disk state alone reconstructs full working
context — so an accidental session end (crash, closed terminal, killed process)
with NO resume loses nothing. The write-back gate covers clean turn-ends; these
rules close the mid-turn crash window and make cold restarts self-healing.

**Invariant — transcript independence.** At every yield point, the GOTM files
must be sufficient to resume WITHOUT the chat transcript. Never hold
decision-relevant state only in conversation. Treat LEDGER.md → Recent updates
as the recovery log — rich enough that a cold session understands not just what
is done but where we are and why.

**Crash-safe write ordering (journaling).** When executing a unit:
  1. Mark the unit `in_progress` in LEDGER.md BEFORE producing its output.
  2. Produce the single output file.
  3. Mark the unit `done` and append any DECISIONS.md / QUESTIONS.md entries.
A crash between (1) and (3) leaves a recoverable trail — never a silent gap.

**Bound the loss — size units to their loop (heavy/iterative work).** Never wrap
a long loop (columns, tables, batches) in one un-checkpointed pass. Either split
into per-iteration units, or checkpoint per iteration to the working state so
reconciliation resumes at the last completed iteration — a crash then costs one
iteration, not the whole unit.

**Session-start reconciliation (crash recovery).** Before acting, reconcile
the ledger against disk and heal drift:
  - `done` row whose output file is MISSING → reopen the unit; note in Recent updates.
  - Output file EXISTS for a non-`done` unit → an interrupted unit: inspect, then
    finalize to `done` (if complete) or supersede with a follow-on unit (if partial).
  - `in_progress` unit → resume/verify exactly that unit.
  - Record what reconciliation found/did in Recent updates (recovery is auditable).
Recovery produces new ledger entries, never silent edits to closed units.
```

`CLAUDE.md` — add a third non-negotiable bullet so it rides in auto-loaded context:
```markdown
- **Resilience / cold start.** On session start, reconcile the ledger against
  disk before acting — a crash can orphan an output or leave a unit in_progress.
  Never hold decision-relevant state only in chat. Mark a unit `in_progress`
  before producing its output. See PROTOCOL.md → Resilience.
```

**Honest scope note for maintainers:** this does not make the crash window literally zero (a crash *during* the ledger write itself still exists), but it makes every outcome **recoverable** — a cold session can always detect the half-state and heal it. The guarantee is "no *unrecoverable* context loss," which is the achievable and correct bar.

## Appendix D — Audit gates text (lift into `PROTOCOL.md.template` + `CLAUDE.md.template`)

`PROTOCOL.md` — new section:
```markdown
## Audit gates (Rule 4, operationalized)

To be a *fair* check, an audit must be independent of the author.

**Independence (non-negotiable).** An audit is run by a DIFFERENT agent than the
one that produced the work, with a fresh, bounded context — given only: a pointer
to PROTOCOL.md, the audited unit(s)' inputs, their output file(s), and each unit's
promised spec. The auditor does NOT see the authoring conversation. Dispatch the
audit via a subagent. The author never blesses its own units inline.

**Checklist:** (1) existence — outputs exist at ledger paths; (2) spec match;
(3) cross-reference integrity — referenced D#/U#/Q# exist and say what's claimed;
(4) internal consistency — no contradictions across the set; (5) decision fidelity.

**Verdict** → audits/audit-NNN.md: PASS / PASS-WITH-FINDINGS / FAIL, findings
pinned to file/section.

**The gate.** Downstream/code units must not start until the covering audit records
PASS / PASS-WITH-FINDINGS. Findings become new ledger units (never silent edits).
FAIL holds the gate until fixes close and a re-audit (independent) passes.

**Interim deferral** to a logged follow-up audit unit is allowed during active human
review, but the independent audit must run before code consumes the design.
```

`CLAUDE.md` — add a non-negotiable bullet:
```markdown
- **Audit independence & gate.** A done unit is checked by a different agent than
  its author (dispatch as a subagent with fresh, bounded context). Code waits for
  an audit PASS; findings become new units. See PROTOCOL.md -> Audit gates.
```

Also make `prompts/audit.md` real — a ready-to-use subagent audit-dispatch prompt (the template references it but the bootstrap doesn't create it; see G7). It should hand the auditor only the bounded inputs/output/spec and the checklist above, and require a verdict written to `audits/`.
