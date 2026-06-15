# GOTM immutability hook (optional, opt-in)

`gotm-immutability.py` is a `PreToolUse` hook that converts the PROTOCOL's
*pre-edit check* from advisory to **enforced by the harness**. On every
`Edit` / `Write` / `MultiEdit` it parses `.gotm/LEDGER.md`, and if the edit
target is the output of a unit whose Status is `done`, it returns a `deny`
decision telling the agent to append a follow-on unit instead of mutating the
frozen output.

This is the only safeguard that does not depend on the agent *remembering* to
run the check — which is the whole point of GOTM. The doc-level safeguards in
`.gotm/PROTOCOL.md` remain the backstop if the hook is not wired.

## Wiring

Add this to `.claude/settings.json` at the repo root (merge into existing
`hooks` if you already have some):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/.gotm/hooks/gotm-immutability.py\" || true",
            "timeout": 10,
            "statusMessage": "GOTM immutability check"
          }
        ]
      }
    ]
  }
}
```

## ⚠️ Activation caveat (read this — empirically confirmed)

A `.claude/settings.json` that did **not** exist at session start is **not**
hot-loaded into the running session. The settings watcher only tracks
directories that had a settings file when the session started, and even then a
brand-new file is not reliably picked up.

Confirmed in testing: a probe edit against a frozen `done` output still reached
the Edit tool (guard did not fire), and **opening the `/hooks` dialog and
*dismissing* it did NOT activate the hook.** Only a **full restart** is reliable.

- **New sessions auto-load it.** If the hook + settings are present at session
  start, the guard fires from the first edit.
- **Wired mid-session?** Do a full restart. Do not rely on `/hooks` dismiss.

The bootstrap creates the hook script and offers to write the settings as part
of the initial file-set precisely so it is present at first session start.

## Why it is built the way it is

1. **Project root is derived from the script's own location** (`<root>/.gotm/hooks/`),
   not from cwd or a hardcoded path — so it survives being cloned to any path.
2. **Deny is signalled via stdout JSON** (`permissionDecision: deny`), never via a
   non-zero exit code — so the wrapping `|| true` can fail-open without masking a
   deny, and a *missing* interpreter/script fails **open** (allow) rather than
   blocking every edit in the repo.
3. **Fail-open on any exception** (`try/except → exit 0`). A guard bug must never
   brick editing; the PROTOCOL safeguards stay as the backstop.
