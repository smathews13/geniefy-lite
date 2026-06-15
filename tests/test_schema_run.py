"""Tests for the hands-off schema-run driver (U111, D51 / LLD-amend-005 §6).

Covers the batch loop over injected fakes: enumerate → per-table generate (hands-off, never apply)
→ rollup counts → finalize; idempotent skip of already-done tables; per-table error isolation;
enumeration failure → run marked failed. Hermetic — no infra.

Run: ``PYTHONPATH=src pytest tests/test_schema_run.py``
"""
from __future__ import annotations

import pytest

from geniefy_app.schema_run import run_schema


class FakeService:
    def __init__(self, tables, *, kinds=None, enum_error=None, fail_tables=()):
        self._tables = tables
        self._kinds = kinds or {}
        self._enum_error = enum_error
        self._fail = set(fail_tables)
        self.run_calls: list[str] = []
        self.applied = False  # tripped if apply is ever called — it must NOT be (D51/Q7)

    def enumerate_tables(self, catalog, schema, filters):
        if self._enum_error:
            raise self._enum_error
        return list(self._tables)

    def run_hands_off(self, table_ref, *, schema_run_id, created_by):
        self.run_calls.append(table_ref)
        name = table_ref.split(".")[-1]
        if name in self._fail:
            raise RuntimeError("boom")
        return self._kinds.get(name, "ready_for_review")

    def apply(self, *a, **k):
        self.applied = True  # never reached by the driver


class FakeStore:
    def __init__(self, existing=()):
        self._existing = set(existing)
        self.updates: list[dict] = []
        self.finalized: str | None = None
        self.bumps: list[str] = []

    def update_schema_run(self, run_id, *, status=None, total_tables=None, job_run_id=None):
        self.updates.append({"status": status, "total_tables": total_tables})

    def finalize_schema_run(self, run_id, *, status="completed"):
        self.finalized = status

    def bump_counts(self, run_id, key):
        self.bumps.append(key)

    def session_exists(self, run_id, table_ref):
        return table_ref.split(".")[-1] in self._existing


def test_run_schema_generates_each_table_and_counts():
    svc = FakeService(["a", "b", "c"], kinds={"b": "needs_input"})
    store = FakeStore()
    summary = run_schema(svc, store, schema_run_id="r1", catalog="cat", schema="sch")
    assert svc.run_calls == ["cat.sch.a", "cat.sch.b", "cat.sch.c"]
    assert summary == {"total": 3, "counts": {"ready": 2, "needs_input": 1}}
    assert {"status": "running", "total_tables": 3} in store.updates   # status+total set up front
    assert store.finalized == "completed"
    assert sorted(store.bumps) == ["needs_input", "ready", "ready"]
    assert svc.applied is False                                        # never applies to UC (Q7)


def test_run_schema_skips_already_done_tables_idempotent():
    svc = FakeService(["a", "b"])
    store = FakeStore(existing=["a"])
    summary = run_schema(svc, store, schema_run_id="r1", catalog="cat", schema="sch")
    assert svc.run_calls == ["cat.sch.b"]                             # 'a' already done → skipped
    assert summary["counts"] == {"skipped": 1, "ready": 1}
    assert store.finalized == "completed"


def test_run_schema_isolates_a_table_error():
    svc = FakeService(["a", "b", "c"], fail_tables=["b"])
    store = FakeStore()
    summary = run_schema(svc, store, schema_run_id="r1", catalog="cat", schema="sch")
    assert svc.run_calls == ["cat.sch.a", "cat.sch.b", "cat.sch.c"]   # b failed but c still ran
    assert summary["counts"] == {"ready": 2, "error": 1}
    assert store.finalized == "completed_with_errors"


def test_run_schema_enumeration_failure_marks_run_failed():
    svc = FakeService([], enum_error=RuntimeError("no SELECT on schema"))
    store = FakeStore()
    with pytest.raises(RuntimeError):
        run_schema(svc, store, schema_run_id="r1", catalog="cat", schema="sch")
    assert store.finalized == "failed"
    assert svc.run_calls == []                                        # nothing generated
