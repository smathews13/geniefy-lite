"""geniefy-v3 App backend — hands-off schema-run driver (U111, D51 / LLD-amend-005 §6).

The batch loop a Databricks Job runs (as the app SP) when a steward points the app at a schema:
enumerate the schema's tables → generate documentation for each in **hands-off** mode (persist a
session per table, **never apply** to UC) → update the parent ``schema_runs`` rollup counts →
finalize. It is **idempotent** (D20): a table already done for this run is skipped, so a Job retry
resumes where it left off; one table's failure is isolated (counted, not fatal — U4 §8).

Pure orchestration over injected ``service`` (``SessionService`` — ``enumerate_tables`` +
``run_hands_off``) and ``store`` (``SessionStore`` — ``update/finalize_schema_run``, ``bump_counts``,
``session_exists``), so it is unit-tested with fakes; the Job entrypoint (``jobs/schema_run_entry.py``)
builds the real service on the cluster and calls this.
"""
from __future__ import annotations

from typing import Any

# StepResult.kind → the schema_runs rollup counter (D51 §A / U109 _SCHEMA_RUN_COUNT_KEYS).
_COUNT_KEY_FOR_KIND = {"ready_for_review": "ready", "needs_input": "needs_input"}


def run_schema(service: Any, store: Any, *, schema_run_id: str, catalog: str, schema: str,
               filters: dict[str, Any] | None = None,
               created_by: str = "app-service-principal") -> dict[str, Any]:
    """Drive one hands-off schema run. Returns a small summary ``{total, counts}``.

    Steps: enumerate (SP) → per table: skip if already done (idempotent), else generate (hands-off,
    never apply) + bump the matching rollup counter; a table error bumps ``error`` and continues →
    finalize ``completed`` (or ``completed_with_errors`` if any table failed). An **enumeration**
    failure is fatal for the run (marked ``failed``)."""
    filters = filters or {}
    try:
        tables = service.enumerate_tables(catalog, schema, filters)
    except Exception:
        store.finalize_schema_run(schema_run_id, status="failed")
        raise

    store.update_schema_run(schema_run_id, status="running", total_tables=len(tables))

    counts: dict[str, int] = {}
    had_error = False
    for table in tables:
        table_ref = f"{catalog}.{schema}.{table}"
        if store.session_exists(schema_run_id, table_ref):
            _tally(store, schema_run_id, counts, "skipped")
            continue
        try:
            kind = service.run_hands_off(table_ref, schema_run_id=schema_run_id, created_by=created_by)
            _tally(store, schema_run_id, counts, _COUNT_KEY_FOR_KIND.get(kind, "error"))
        except Exception:
            had_error = True
            _tally(store, schema_run_id, counts, "error")

    store.finalize_schema_run(
        schema_run_id, status="completed_with_errors" if had_error else "completed")
    return {"total": len(tables), "counts": counts}


def _tally(store: Any, schema_run_id: str, counts: dict[str, int], key: str) -> None:
    """Bump the persisted rollup counter (best-effort) + the in-memory summary."""
    counts[key] = counts.get(key, 0) + 1
    try:
        store.bump_counts(schema_run_id, key)
    except Exception:
        pass  # a counter blip must not abort the batch; the sessions are the source of truth
