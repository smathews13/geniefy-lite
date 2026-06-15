---
prompt: subagent-dispatch
purpose: construct a dispatch prompt for a bounded worker subagent
audience: orchestrating LLM (you read this, then build a worker prompt)
license: Apache 2.0
---

# Subagent dispatch prompt

Use this template when a unit's work needs a subagent — either because the work exceeds the current session's bandwidth, or because an audit requires an independent context (rule 4 of the protocol).

Two things go into every dispatch: a pointer to `PROTOCOL.md` (so the worker reads the discipline) and the bounded task (inputs, output, spec, constraints). The worker does not see the broader project.

---

## Paste this into the orchestrating LLM

You are about to dispatch a subagent to execute one unit of work in this GOTM-orchestrated project. Build the dispatch prompt by filling in the sections below, then send it to the worker runner.

The dispatch prompt the worker receives has these sections:

### 1. Protocol pointer

The first thing the worker reads. One line:

    Read `.gotm/PROTOCOL.md` before continuing. You will not see the broader project; the protocol tells you the rules of engagement.

### 2. Task identification

Name the unit so it is traceable:

    Unit ID: <Uxx>
    Title: <unit title>

### 3. Inputs (bounded; the worker reads only these)

    Inputs:
    - <path 1>
    - <path 2>

The worker does not read other files. Filter context for the worker; do not push the project's broader state into the dispatch.

### 4. Output path (exactly one)

    Output: <output/path.md>

One named file. If the unit hides more than one output, the dispatch is wrong — split the unit before dispatching.

### 5. Output spec

What the output must contain:

    Output spec:
    - Sections: <required H2 sections>
    - Length: <target word count or range>
    - Voice: <constraints, voice references>
    - Format: <markdown / yaml / etc.>

### 6. Constraints

    Constraints:
    - Banned phrases: <list, or reference to a project file>
    - Anonymization: <rules>
    - Format constraints: <fence handling, table conventions, etc.>
    - Other: <as needed>

### 7. Return format

What the worker reports back when done:

    Return format:
    - Output path written
    - One-sentence summary
    - Word count
    - Self-check verification against the spec (pass / fail, with notes)
    - Gaps surfaced, if any

### After dispatch

When the worker returns:

- Fold the output into the project — update `.gotm/LEDGER.md` to mark the unit `done`, append a recent-updates entry.
- Audit the output if rule 4 calls for it (see `.gotm/prompts/audit.md`).
- If the worker stopped due to missing inputs or ambiguous spec, treat the gap as a new question — route to `.gotm/QUESTIONS.md` if it is mission-level, refine the dispatch if it is execution-level, then re-dispatch.

The dispatch never short-circuits the discipline. The worker reads the protocol, does one task, returns one output, and reports.
