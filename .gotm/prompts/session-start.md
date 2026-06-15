---
prompt: session-start
purpose: orient an agent to a GOTM project's state at session start
audience: LLM (paste the body into your LLM)
license: Apache 2.0
---

# Session-start prompt

Use this prompt at the start of any session in a GOTM-orchestrated project. Paste the body below into your LLM. The LLM will read the project's `PROTOCOL.md`, `LEDGER.md`, and `QUESTIONS.md`, identify the active unit, and report back. This is the first move of every session — not a single-use bootstrap. Run it whenever a fresh session opens.

---

## Paste this into your LLM

You are starting a session in a GOTM-orchestrated project. Your job in this prompt is to orient yourself to the project state. You do not execute any work yet.

### Steps

1. Read `.gotm/PROTOCOL.md`.
2. Read `.gotm/LEDGER.md`.
3. Read `.gotm/QUESTIONS.md`.
4. Identify:
   - The project's mission (top of `.gotm/LEDGER.md`).
   - The active unit (the `## Active unit` section, or the top non-`done` row whose blockers are clear).
   - Any open questions blocking work.
   - The most recent entry under "Recent updates" in `.gotm/LEDGER.md`.

### Report back

Return a short status to the practitioner in this shape:

    Mission: <mission line>
    Active unit: <ID and title>
    Open questions: <list, or "none">
    Last update: <most recent line from Recent updates>
    Ready to act on the active unit, or do you want to redirect?

### Do not

- Do not execute work on the active unit in this kickoff. The kickoff exists to confirm alignment before action.
- Do not skim the protocol or ledger. The discipline depends on reading them carefully each session.
- Do not propose new units or decisions in this prompt. Those happen during action, not orientation.

After the practitioner confirms direction, proceed under the protocol.
