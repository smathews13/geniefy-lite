# CLAUDE.md

This project follows the **GOTM** operating protocol. The GOTM orchestration file-set lives in [`.gotm/`](.gotm/), kept separate from produced assets so it never collides with the code/docs being built.

Before doing any work in this repo:

1. Read [`.gotm/PROTOCOL.md`](.gotm/PROTOCOL.md) — the operating contract.
2. Read [`.gotm/LEDGER.md`](.gotm/LEDGER.md) — the authoritative list of units.
3. Read [`.gotm/QUESTIONS.md`](.gotm/QUESTIONS.md) — open ratifications.

`.gotm/PROTOCOL.md` is canonical. This file lives at the repo root **only** because Claude Code auto-loads a root `CLAUDE.md` across sessions — it is the bridge that carries the GOTM discipline forward. Do not move it into `.gotm/`; that silently breaks the auto-load.

## Non-negotiables (anti-drift)

These are the ways the discipline erodes. Guard against them, every turn (full detail in `.gotm/PROTOCOL.md` → *Anti-drift safeguards*, *Resilience*, and *Audit gates*):

- **Done units are frozen.** Before any `Edit`/`Write`, check `.gotm/LEDGER.md`: if the target is a `done` unit's output, do **not** edit it — append a follow-on unit and put the change there. Same for prior `DECISIONS.md` / `QUESTIONS.md` entries — append, don't rewrite (marking a Status line answered/superseded is the one allowed exception). Living governance docs (`PROTOCOL.md`, `CLAUDE.md`, `README.md`) stay editable.
- **Write-back gate.** Never end a turn that created or changed a unit's output without updating `.gotm/LEDGER.md` (and `DECISIONS.md` / `QUESTIONS.md` as needed) in the *same* turn. Output without write-back means the unit is not done.
- **Resilience / cold start.** On session start, *reconcile the ledger against disk before acting* — a crash can orphan an output or leave a unit `in_progress`. Never hold decision-relevant state only in chat; the on-disk GOTM state alone must reconstruct context with no transcript. When executing a unit, mark it `in_progress` before producing its output. See `.gotm/PROTOCOL.md` → *Resilience*.
- **Audit independence & gate.** A done unit is checked by a *different* agent than its author — dispatch the audit as a subagent with fresh, bounded context (inputs + output + spec only); never bless your own work. Run `/gotm:audit <Uxx>`. Downstream/code units wait for a passing verdict — `PASS` or `PASS-FINDINGS` (`Audit` column); a `FAIL` blocks. Findings become new units. See `.gotm/PROTOCOL.md` → *Audit gates*.

**Produced assets** (not GOTM machinery): design docs in [`docs/design/`](docs/design/); code at the repo root later.
