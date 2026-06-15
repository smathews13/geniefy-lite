#!/usr/bin/env python3
"""GOTM immutability guard — PreToolUse hook (Edit | Write | MultiEdit).

Enforces the GOTM rule that a *done* unit's output file is frozen. It reads the
hook payload from stdin, parses `.gotm/LEDGER.md` for unit rows whose Status is
`done`, and if the edit target is one of those frozen outputs it returns a
`deny` decision telling the agent to append a follow-on unit instead.

Design notes:
- Project root is derived from THIS script's location (`<root>/.gotm/hooks/`),
  so it works regardless of cwd and survives being cloned to any path (D12).
- Deny is signalled via stdout JSON (`permissionDecision: deny`), never via a
  non-zero exit code — so the wrapping `|| true` in settings can't mask it.
- Fail-open: any internal error → allow (exit 0). A guard bug must never brick
  editing; the doc-level safeguards in PROTOCOL.md still apply as backstop.
- Header-aware: the ledger gained an `Audit` column (D15). The parser reads each
  table's header row to locate Output/Status by NAME, so extra columns don't
  shift cells. Falls back to the legacy 5-column layout (Output 2nd-last,
  Status last) when a table has no header.
"""
import json
import os
import re
import sys

UNIT_ROW = re.compile(r"^\|\s*U\d+[a-z]*\s*\|")  # | U1 | … |, | U12a | … |


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_separator(cells):
    return bool(cells) and all(set(c) <= set("-: ") for c in cells)


def _header_cols(cells):
    """If this row is a unit-table header, return {name: index} for id/output/status."""
    low = [c.lower() for c in cells]
    if "id" in low and "output" in low and "status" in low:
        return {"id": low.index("id"), "output": low.index("output"), "status": low.index("status")}
    return None


def frozen_outputs(ledger_path, root):
    """Return {abs_output_path: unit_id} for rows whose Status is 'done'.

    Tracks the current table's column map (a ledger may hold several tables of
    differing widths across phases); applies it to the unit rows that follow.
    """
    out = {}
    col = None  # current header column-index map, or None before any header
    with open(ledger_path, encoding="utf-8") as fh:
        for line in fh:
            if not line.lstrip().startswith("|"):
                continue
            cells = _cells(line)
            if _is_separator(cells):
                continue
            header = _header_cols(cells)
            if header:
                col = header
                continue
            if not UNIT_ROW.match(line) or len(cells) < 3:
                continue
            if col and col["output"] < len(cells) and col["status"] < len(cells) and col["id"] < len(cells):
                unit_id = cells[col["id"]]
                output = cells[col["output"]]
                status = cells[col["status"]].lower()
            else:  # legacy headerless table: Status last, Output 2nd-last
                unit_id = cells[0]
                status = cells[-1].lower()
                output = cells[-2]
            output = output.strip().strip("`").strip()
            if status != "done":
                continue
            if not output or output in ("—", "-", ""):
                continue
            abs_path = os.path.realpath(os.path.join(root, output))
            out[abs_path] = unit_id
    return out


def main():
    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}

    target = (payload.get("tool_input") or {}).get("file_path")
    if not target:
        return  # nothing to guard → allow

    script_dir = os.path.dirname(os.path.realpath(__file__))
    root = os.path.dirname(os.path.dirname(script_dir))  # .gotm/hooks -> .gotm -> root
    ledger = os.path.join(root, ".gotm", "LEDGER.md")
    if not os.path.isfile(ledger):
        return  # no ledger → nothing to enforce

    if not os.path.isabs(target):
        target = os.path.join(os.getcwd(), target)
    target_abs = os.path.realpath(target)

    frozen = frozen_outputs(ledger, root)
    unit_id = frozen.get(target_abs)
    if not unit_id:
        return  # not a frozen output → allow (normal permission flow continues)

    rel = os.path.relpath(target_abs, root)
    reason = (
        f"GOTM immutability guard: '{rel}' is the output of unit {unit_id}, which is "
        f"marked DONE in .gotm/LEDGER.md and is therefore frozen. Do NOT edit it. "
        f"Append a follow-on / superseding unit to the ledger and put the change in the "
        f"new unit's output (see .gotm/PROTOCOL.md -> Anti-drift safeguards). "
        f"If you are PRODUCING this output right now (you just registered {unit_id}), the "
        f"unit was wrongly born DONE: set {unit_id} to `in_progress` first, create the "
        f"output, then flip it to `done` (crash-safe ordering, .gotm/PROTOCOL.md -> Resilience)."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Fail-open: never block editing because of a guard bug.
        sys.exit(0)
