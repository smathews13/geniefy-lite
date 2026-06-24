"""Local UI devloop preview (U92, R3-refreshed U127) — serve the built SPA through the REAL
FastAPI routes against a SEEDED in-memory ``SessionService``, so the frontend can be exercised in a
browser with realistic data and no Databricks infra (no Lakebase / warehouse / FMAPI).

R3-aware seed: a ready-for-review session with a **steward-first hero** table draft carrying free-form
``tags`` + structured ``facts`` (U104/U114), column drafts with ``data_type`` + ``tags`` (pills), a
per-column profile (E4), open questions, a comment library with the ``approved → applied → sunset``
lifecycle (U103/U106), and a seeded hands-off ``schema_runs`` record + its per-table sessions (U109/U112).

Run:  PYTHONPATH=src .venv/bin/python scripts/local_ui_preview.py [port]
Then open http://127.0.0.1:<port>  (default 8770).
"""
from __future__ import annotations

import os
import sys

from geniefy_app.api import SessionService, create_app
from geniefy_core.state import (
    ColumnDraft,
    DraftKind,
    DraftStatus,
    Phase,
    Question,
    RunConfig,
    SessionState,
    StepResult,
    TableDraft,
)

_JUDGE = {"subscores": {"completeness": 0.9, "specificity": 0.95, "grounding": 0.9,
                        "template_conformance": 0.88}, "overall": 0.91, "issues": []}


def _seed() -> SessionState:
    st = SessionState(
        target="samples.tpch.orders",
        config=RunConfig(model_endpoint="databricks-claude-sonnet-4-6", template_id="default"),
        session_id="demo", template_id="default", phase=Phase.READY_FOR_REVIEW,
    )
    st.table_draft = TableDraft(
        proposed_comment=("The orders table is the central fact table of the TPC-H schema — one row per "
                          "customer order (o_orderkey). It records order status, total price, order date, "
                          "and priority, and joins to customer via o_custkey and to lineitem via o_orderkey. "
                          "Use it for revenue, order-fulfilment, and churn analytics; ~7.5M rows, refreshed daily."),
        confidence=0.91, rationale="Synthesized from the profile (row_count, key cardinality) + UC lineage.",
        evidence_refs=["profile.row_count", "context:uc_lineage"], judge_scores=_JUDGE,
        tags=["fact", "orders", "tpch", "revenue"],
        facts={"owner": "Data Engineering", "freshness": "daily 02:00 UTC",
               "grain": "one row per order", "keys": "o_orderkey (PK), o_custkey (FK)",
               "sensitivity": "no PII"},
        status=DraftStatus.DRAFT)
    st.column_drafts = [
        ColumnDraft(column_name="o_orderkey", ordinal=1, data_type="bigint", confidence=0.85,
                    proposed_comment="Primary key uniquely identifying each order; surrogate integer key.",
                    evidence_refs=["profile.cardinality_ratio"], judge_scores=_JUDGE,
                    tags=["identifier", "key"], status=DraftStatus.DRAFT),
        ColumnDraft(column_name="o_orderdate", ordinal=5, data_type="date", confidence=0.94,
                    proposed_comment="Date the order was placed; values range 1992-01-01 to 1998-08-02.",
                    evidence_refs=["profile.min", "profile.max"], judge_scores=_JUDGE,
                    tags=["temporal", "dimension"], status=DraftStatus.DRAFT),
        ColumnDraft(column_name="o_orderstatus", ordinal=3, data_type="string", confidence=0.60,
                    proposed_comment="Order status code (enum).", evidence_refs=[],
                    tags=["enum", "status"], status=DraftStatus.NEEDS_INPUT),
    ]
    st.profile = {
        "table": {"full_name": "samples.tpch.orders", "row_count": 7500000},
        "columns": [
            {"name": "o_orderkey", "null_fraction": 0.0, "distinct_count": 7500000,
             "cardinality_ratio": 1.0, "min": "1", "max": "6000000", "is_enum_candidate": False},
            {"name": "o_orderdate", "null_fraction": 0.0, "distinct_count": 2406,
             "cardinality_ratio": 0.00032, "min": "1992-01-01", "max": "1998-08-02", "is_enum_candidate": False},
            {"name": "o_orderstatus", "null_fraction": 0.0, "distinct_count": 3,
             "cardinality_ratio": 0.0000004, "is_enum_candidate": True,
             "top_k": [{"value": "O", "count": 3793296}, {"value": "F", "count": 3793218},
                       {"value": "P", "count": 13486}],
             "pii": {"detected": False, "classes": [], "action": "none"}},
        ],
    }
    st.open_questions = [Question(id="q1", target_kind=DraftKind.COLUMN, target_name="o_orderstatus",
                                  text="What do the status codes O / F / P mean?",
                                  suggested_answer="O = Open, F = Fulfilled, P = Pending (from the value distribution).")]
    return st


# Library lifecycle demo rows (U103/U106): approved · applied · sunset.
_LIBRARY = [
    {"id": "lib-1", "scope": "column", "match_key": "o_orderdate", "canonical_comment": "Date the order was placed.",
     "tags": ["temporal"], "usage_count": 7, "source_table_ref": "samples.tpch.orders",
     "approved_by": "user@example.com", "updated_at": "2026-06-12T00:00:00", "status": "applied"},
    {"id": "lib-2", "scope": "column", "match_key": "country", "canonical_comment": "ISO-3166 alpha-2 country code.",
     "tags": ["dimension", "enum"], "usage_count": 3, "source_table_ref": "samples.tpch.customer",
     "approved_by": "user@example.com", "updated_at": "2026-06-13T00:00:00", "status": "approved"},
    {"id": "lib-3", "scope": "table", "match_key": "samples.tpch.legacy_orders",
     "canonical_comment": "Deprecated legacy orders snapshot — retired.", "tags": ["deprecated"], "usage_count": 1,
     "source_table_ref": "samples.tpch.legacy_orders", "approved_by": "user@example.com",
     "updated_at": "2026-06-10T00:00:00", "status": "sunset"},
]

# Hands-off schema run demo (U109/U112): a run + its per-table sessions.
_SCHEMA_RUN = {"id": "run-demo", "catalog": "my_catalog", "schema": "gaming_data",
               "status": "completed_with_errors", "filters": {"skip_documented": False},
               "total_tables": 4, "counts": {"ready": 2, "needs_input": 1, "applied": 0, "error": 1},
               "job_run_id": 573067574879500, "created_by": "user@example.com",
               "created_at": "2026-06-14T00:00:00", "updated_at": "2026-06-14T00:10:00"}
_RUN_SESSIONS = [
    {"session_id": "demo", "target": "my_catalog.gaming_data.gold_arpdau", "status": "ready_for_review",
     "created_by": "app-service-principal", "created_at": "t0", "updated_at": "t1", "n_columns": 6, "n_applied": 0},
    {"session_id": "s2", "target": "my_catalog.gaming_data.bronze_events", "status": "awaiting_input",
     "created_by": "app-service-principal", "created_at": "t0", "updated_at": "t1", "n_columns": 11, "n_applied": 0},
    {"session_id": "s3", "target": "my_catalog.gaming_data.silver_sessions", "status": "ready_for_review",
     "created_by": "app-service-principal", "created_at": "t0", "updated_at": "t1", "n_columns": 8, "n_applied": 0},
]


class _Store:
    def __init__(self):
        self._s = {"demo": _seed()}
        self._lib = list(_LIBRARY)
        self._runs = {"run-demo": dict(_SCHEMA_RUN)}

    def save(self, state, *, created_by, schema_run_id=None):
        self._s[state.session_id or "demo"] = state
        return state.session_id or "demo"

    def load(self, sid):
        return self._s.get(sid)

    def list_sessions(self, *, status=None, table=None, schema_run_id=None, limit=50, offset=0):
        if schema_run_id:
            return list(_RUN_SESSIONS)
        return [_RUN_SESSIONS[0] | {"target": "samples.tpch.orders", "n_columns": 3}]

    # comment library lifecycle (U103/U106)
    def list_library(self, *, scope=None, include_sunset=False, limit=100, offset=0):
        rows = self._lib if include_sunset else [e for e in self._lib if e["status"] != "sunset"]
        return [e for e in rows if scope is None or e["scope"] == scope]

    def upsert_library_entry(self, **k):
        pass

    def sunset_library_entry(self, entry_id, *, sunset_by=None):
        for e in self._lib:
            if e["id"] == entry_id:
                e["status"] = "sunset"

    def revive_library_entry(self, entry_id):
        for e in self._lib:
            if e["id"] == entry_id:
                e["status"] = "approved"

    # hands-off schema runs (U109/U112)
    def create_schema_run(self, *, catalog, schema, created_by, filters=None):
        rid = f"run-{len(self._runs) + 1}"
        self._runs[rid] = {"id": rid, "catalog": catalog, "schema": schema, "status": "enumerating",
                           "filters": filters or {}, "counts": {}, "total_tables": None, "job_run_id": None,
                           "created_by": created_by, "created_at": "now", "updated_at": "now"}
        return rid

    def update_schema_run(self, run_id, **k):
        self._runs.get(run_id, {}).update({x: v for x, v in k.items() if v is not None})

    def finalize_schema_run(self, run_id, *, status="completed"):
        if run_id in self._runs:
            self._runs[run_id]["status"] = status

    def list_schema_runs(self, *, limit=50, offset=0):
        return list(self._runs.values())

    def get_schema_run(self, run_id):
        return self._runs.get(run_id)

    def append_audit(self, *a, **k):
        pass


class _Orch:
    def __init__(self, tracer):
        self._t = tracer

    def run(self, target, state):
        state.phase = Phase.READY_FOR_REVIEW
        return StepResult.ready_for_review(state)

    def resume(self, state, answers):
        state.open_questions = []
        state.phase = Phase.READY_FOR_REVIEW
        return StepResult.ready_for_review(state)

    def regenerate(self, state, targets):
        state.phase = Phase.READY_FOR_REVIEW
        return StepResult.ready_for_review(state)


def build_app():
    svc = SessionService(make_orchestrator=lambda tracer: _Orch(tracer), store=_Store(),
                         config=RunConfig(model_endpoint="databricks-claude-sonnet-4-6", template_id="default"))
    static = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static")
    return create_app(svc, static_dir=static)


app = build_app()

if __name__ == "__main__":
    import uvicorn

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8770
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
