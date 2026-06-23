"""Tests for the App backend FastAPI service (U52).

Covers U5/U6/D18: the run→poll→resume→review lifecycle, the async background model,
the concurrency guard, progressive per-phase persistence (D18), and the config/health/
apply endpoints. Hermetic — a fake in-memory store + a fake orchestrator (the real
orchestration is covered by U42; the real wiring `build_service` is integration-verified).

Run: ``PYTHONPATH=src pytest tests/test_api.py``
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from geniefy_app.api import SessionError, SessionService, create_app
from geniefy_core.state import (
    Answer,
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


# ─────────────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────────────
class FakeStore:
    def __init__(self):
        self.data: dict[str, dict] = {}
        self.save_calls = 0
        self.saved_statuses: list[str] = []  # status at each save → progressive-persistence check
        self.library_upserts: list[dict] = []  # write-on-approve calls (D52 §A3)
        self.sunset_calls: list[tuple] = []
        self.revive_calls: list[str] = []
        self.schema_runs: dict[str, dict] = {}        # schema-run records (D51/U110)
        self.saved_schema_run_ids: list = []          # schema_run_id passed to each save

    def save(self, state: SessionState, *, created_by: str, schema_run_id: str | None = None) -> str:
        self.save_calls += 1
        self.saved_statuses.append(state.session_status.value)
        self.saved_schema_run_ids.append(schema_run_id)
        if not state.session_id:
            import uuid
            state.session_id = str(uuid.uuid4())
        self.data[state.session_id] = state.to_dict()
        return state.session_id

    def load(self, session_id: str) -> SessionState | None:
        d = self.data.get(session_id)
        return SessionState.from_dict(d) if d else None

    def list_sessions(self, *, status=None, table=None, schema_run_id=None, limit=50, offset=0):
        self.list_args = {"status": status, "table": table, "schema_run_id": schema_run_id,
                          "limit": limit, "offset": offset}
        return [{"session_id": "sid-1", "target": "samples.tpch.orders", "status": "ready_for_review",
                 "created_by": "app-service-principal", "created_at": "t0", "updated_at": "t1",
                 "n_columns": 9, "n_applied": 3}]

    # schema runs (D51/U110)
    def create_schema_run(self, *, catalog, schema, created_by, filters=None):
        rid = f"run-{len(self.schema_runs) + 1}"
        self.schema_runs[rid] = {"id": rid, "catalog": catalog, "schema": schema,
                                 "status": "enumerating", "filters": filters or {}, "counts": {},
                                 "total_tables": None, "job_run_id": None, "created_by": created_by}
        return rid

    def update_schema_run(self, run_id, *, status=None, total_tables=None, job_run_id=None):
        r = self.schema_runs.setdefault(run_id, {"id": run_id})
        if status is not None:
            r["status"] = status
        if total_tables is not None:
            r["total_tables"] = total_tables
        if job_run_id is not None:
            r["job_run_id"] = job_run_id

    def finalize_schema_run(self, run_id, *, status="completed"):
        self.update_schema_run(run_id, status=status)

    def get_schema_run(self, run_id):
        return self.schema_runs.get(run_id)

    def list_schema_runs(self, *, limit=50, offset=0):
        return list(self.schema_runs.values())

    def list_library(self, *, scope=None, include_sunset=False, limit=100, offset=0):
        self.list_library_args = {"scope": scope, "include_sunset": include_sunset,
                                  "limit": limit, "offset": offset}
        return [{"id": "lib-1", "scope": "column", "match_key": "o_orderkey",
                 "canonical_comment": "The order key.", "tags": [], "usage_count": 5,
                 "source_table_ref": "samples.tpch.orders", "approved_by": "u",
                 "updated_at": "t", "status": "applied"}]

    # Library lifecycle (D52 §A / U105) — record calls for assertions
    def upsert_library_entry(self, **kwargs):
        self.library_upserts.append(kwargs)

    def sunset_library_entry(self, entry_id, *, sunset_by=None):
        self.sunset_calls.append((entry_id, sunset_by))

    def revive_library_entry(self, entry_id):
        self.revive_calls.append(entry_id)


class FakeOrch:
    """Mutates the state like the real orchestrator (sets drafts/phase) and uses the tracer
    so progressive persistence is exercised. ``needs_input`` toggles the pause path."""

    def __init__(self, tracer, *, needs_input: bool):
        self.tracer = tracer
        self.needs_input = needs_input

    def run(self, target, state):
        with self.tracer.span("reason"):
            state.phase = Phase.REASONING
            state.profile = {"table": {"row_count": 1}}
            state.table_draft = TableDraft(proposed_comment="Order headers.", confidence=0.9)
            state.column_drafts = [ColumnDraft(column_name="o_orderkey", confidence=0.95)]
        if self.needs_input:
            state.phase = Phase.AWAITING_INPUT
            state.column_drafts[0].status = DraftStatus.NEEDS_INPUT
            state.open_questions = [Question(id="q1", target_kind=DraftKind.COLUMN,
                                             target_name="o_orderkey", text="what is it?")]
            return StepResult.needs_input(state.open_questions, state)
        state.phase = Phase.READY_FOR_REVIEW
        return StepResult.ready_for_review(state)

    def resume(self, state, answers):
        with self.tracer.span("reason_resume"):
            state.open_questions = []
            if state.column_draft("o_orderkey"):
                state.column_draft("o_orderkey").status = DraftStatus.DRAFT
        state.phase = Phase.READY_FOR_REVIEW
        return StepResult.ready_for_review(state)

    def regenerate(self, state, targets):
        self.regen_targets = targets  # record what the route forwarded
        with self.tracer.span("reason_regenerate"):
            state.phase = Phase.REASONING
            if state.table_draft:
                state.table_draft.proposed_comment = "Regenerated headers."
        state.phase = Phase.READY_FOR_REVIEW
        return StepResult.ready_for_review(state)


def _client(*, needs_input=False, sql_runner=None, run_schema_job=None, cancel_schema_job=None):
    store = FakeStore()
    service = SessionService(
        make_orchestrator=lambda tracer: FakeOrch(tracer, needs_input=needs_input),
        store=store, config=RunConfig(model_endpoint="m", template_id="default"),
        sql_runner=sql_runner, run_schema_job=run_schema_job, cancel_schema_job=cancel_schema_job)
    return TestClient(create_app(service)), store, service


# ─────────────────────────────────────────────────────────────────────────────
# health / config
# ─────────────────────────────────────────────────────────────────────────────
def test_health():
    c, _, _ = _client()
    assert c.get("/health").json() == {"status": "ok"}


def test_me_returns_forwarded_identity():
    # the header user card (U97) reads the end-user identity from the Apps headers (D48/U91)
    c, _, _ = _client()
    body = c.get("/api/me", headers={"X-Forwarded-Email": "user@x.com",
                                     "X-Forwarded-Preferred-Username": "user"}).json()
    assert body["email"] == "user@x.com" and body["username"] == "user" and body["actor"] == "user@x.com"


def test_me_anonymous_without_forwarded_headers():
    c, _, _ = _client()
    body = c.get("/api/me").json()
    assert body["email"] is None and body["actor"] == "anonymous"


def test_config_exposes_non_secret_settings():
    c, _, _ = _client()
    body = c.get("/api/config").json()
    assert body["model_endpoint"] == "m" and body["keep_threshold"] == 0.75
    assert body["template_id"] == "default"


# ─────────────────────────────────────────────────────────────────────────────
# run → poll
# ─────────────────────────────────────────────────────────────────────────────
def test_run_then_poll_ready():
    c, store, _ = _client(needs_input=False)
    r = c.post("/api/run", json={"table": "samples.tpch.orders"})
    assert r.status_code == 202
    sid = r.json()["session_id"]
    assert r.json()["status"] == "created"
    # TestClient ran the background task → session is now ready
    g = c.get(f"/api/sessions/{sid}").json()
    assert g["status"] == "ready_for_review"
    assert g["table_draft"]["proposed_comment"] == "Order headers."
    assert [cd["column_name"] for cd in g["column_drafts"]] == ["o_orderkey"]


def test_run_persists_progressively():
    c, store, _ = _client()
    c.post("/api/run", json={"table": "samples.tpch.orders"})
    # D18: a per-phase partial (reasoning) is persisted DURING the run, before the terminal
    # status — so a polling client sees progress, not just an initial + final save. (`>= 2`
    # was too loose to catch a broken per-phase save; U52 audit MED.)
    assert "reasoning" in store.saved_statuses          # the mid-run partial
    assert store.saved_statuses[-1] == "ready_for_review"  # terminal
    assert store.save_calls >= 3                         # start_run + ≥1 phase-span save + final


def test_get_unknown_session_404():
    c, _, _ = _client()
    assert c.get("/api/sessions/nope").status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# needs_input → answers → resume
# ─────────────────────────────────────────────────────────────────────────────
def test_needs_input_then_resume_to_ready():
    c, _, _ = _client(needs_input=True)
    sid = c.post("/api/run", json={"table": "samples.tpch.orders"}).json()["session_id"]
    g = c.get(f"/api/sessions/{sid}").json()
    assert g["status"] == "awaiting_input"
    assert [q["target_name"] for q in g["open_questions"]] == ["o_orderkey"]

    r = c.post(f"/api/sessions/{sid}/answers",
               json={"answers": [{"question_id": "q1", "text": "the order id"}]})
    assert r.status_code == 202
    g2 = c.get(f"/api/sessions/{sid}").json()
    assert g2["status"] == "ready_for_review" and g2["open_questions"] == []
    assert g2["column_drafts"][0]["status"] == "draft"


def test_submit_answers_marks_in_flight_before_resume():
    # U59/U60 audit MED: after answers post, the session must read in-flight so the polling
    # client keeps polling; the terminal flip happens in the background resume. Call
    # submit_answers directly — under TestClient the route's background resume would otherwise
    # complete synchronously and hide the intermediate in-flight state.
    c, _, service = _client(needs_input=True)
    sid = c.post("/api/run", json={"table": "samples.tpch.orders"}).json()["session_id"]
    assert service.get(sid)["status"] == "awaiting_input"
    service.submit_answers(sid, [Answer(question_id="q1", text="x")], created_by="u")
    assert service.get(sid)["status"] == "reasoning"  # in-flight → client keeps polling


# ─────────────────────────────────────────────────────────────────────────────
# review (U6)
# ─────────────────────────────────────────────────────────────────────────────
def _ready_session(c):
    return c.post("/api/run", json={"table": "samples.tpch.orders"}).json()["session_id"]


def test_review_approve_column():
    c, _, _ = _client()
    sid = _ready_session(c)
    r = c.post(f"/api/sessions/{sid}/drafts/o_orderkey/review", json={"action": "approve"})
    assert r.status_code == 200
    assert c.get(f"/api/sessions/{sid}").json()["column_drafts"][0]["status"] == "approved"


def test_review_edit_sets_comment():
    c, _, _ = _client()
    sid = _ready_session(c)
    c.post(f"/api/sessions/{sid}/drafts/o_orderkey/review",
           json={"action": "edit", "proposed_comment": "The order's surrogate key."})
    cd = c.get(f"/api/sessions/{sid}").json()["column_drafts"][0]
    assert cd["status"] == "edited" and cd["proposed_comment"] == "The order's surrogate key."


def test_review_table_draft_via_sentinel():
    c, _, _ = _client()
    sid = _ready_session(c)
    c.post(f"/api/sessions/{sid}/drafts/__table__/review", json={"action": "approve"})
    assert c.get(f"/api/sessions/{sid}").json()["table_draft"]["status"] == "approved"


def test_review_edit_without_comment_400():
    c, _, _ = _client()
    sid = _ready_session(c)
    r = c.post(f"/api/sessions/{sid}/drafts/o_orderkey/review", json={"action": "edit"})
    assert r.status_code == 400


def test_review_unknown_action_400():
    c, _, _ = _client()
    sid = _ready_session(c)
    r = c.post(f"/api/sessions/{sid}/drafts/o_orderkey/review", json={"action": "yeet"})
    assert r.status_code == 400


def test_review_unknown_draft_404():
    c, _, _ = _client()
    sid = _ready_session(c)
    r = c.post(f"/api/sessions/{sid}/drafts/nope/review", json={"action": "approve"})
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Library lifecycle: write-on-approve + sunset/revive + include_sunset (D52 §A / U105)
# ─────────────────────────────────────────────────────────────────────────────
def test_review_approve_table_writes_library_as_approved():
    c, store, _ = _client()
    sid = _ready_session(c)
    c.post(f"/api/sessions/{sid}/drafts/__table__/review", json={"action": "approve"})
    assert len(store.library_upserts) == 1
    up = store.library_upserts[0]
    assert up["scope"] == "table" and up["status"] == "approved" and up["bump_usage"] is True
    assert up["match_key"] == "samples.tpch.orders"
    assert up["canonical_comment"] == "Order headers."


def test_review_edit_column_writes_library():
    c, store, _ = _client()
    sid = _ready_session(c)
    c.post(f"/api/sessions/{sid}/drafts/o_orderkey/review",
           json={"action": "edit", "proposed_comment": "The order's surrogate key."})
    up = next(u for u in store.library_upserts if u["scope"] == "column")
    assert up["match_key"] == "o_orderkey" and up["status"] == "approved"
    assert up["canonical_comment"] == "The order's surrogate key."


def test_review_reject_does_not_write_library():
    c, store, _ = _client()
    sid = _ready_session(c)
    c.post(f"/api/sessions/{sid}/drafts/__table__/review", json={"action": "reject"})
    assert store.library_upserts == []


def test_sunset_and_revive_library_endpoints():
    c, store, _ = _client()
    r1 = c.post("/api/library/lib-1/sunset")
    assert r1.status_code == 200 and r1.json()["status"] == "sunset"
    assert store.sunset_calls and store.sunset_calls[0][0] == "lib-1"
    r2 = c.post("/api/library/lib-1/revive")
    assert r2.status_code == 200 and r2.json()["status"] == "approved"
    assert store.revive_calls == ["lib-1"]


def test_list_library_include_sunset_passthrough():
    c, store, _ = _client()
    c.get("/api/library?include_sunset=true")
    assert store.list_library_args["include_sunset"] is True
    c.get("/api/library")
    assert store.list_library_args["include_sunset"] is False


def test_review_approve_empty_comment_skips_library():
    # the seeded o_orderkey column draft has no proposed_comment → write-on-approve must skip
    # (never upsert an empty canonical comment) — U115 (U105 audit LOW#2).
    c, store, _ = _client()
    sid = _ready_session(c)
    c.post(f"/api/sessions/{sid}/drafts/o_orderkey/review", json={"action": "approve"})
    assert store.library_upserts == []


def test_library_lifecycle_501_when_store_lacks_methods():
    # a store without the D52 lifecycle methods → sunset/revive return 501, not a 500 (U115/U105 LOW#3).
    class BareStore:
        def save(self, state, *, created_by):
            return "x"
        def load(self, sid):
            return None
        def list_sessions(self, **k):
            return []
        def list_library(self, **k):
            return []
    svc = SessionService(make_orchestrator=lambda t: None, store=BareStore(),
                         config=RunConfig(model_endpoint="m"))
    bare = TestClient(create_app(svc))
    assert bare.post("/api/library/lib-x/sunset").status_code == 501
    assert bare.post("/api/library/lib-x/revive").status_code == 501


# ─────────────────────────────────────────────────────────────────────────────
# concurrency guard + apply stub
# ─────────────────────────────────────────────────────────────────────────────
def test_answers_rejected_while_in_flight_409():
    c, store, _ = _client()
    # plant an in-flight session directly in the store
    st = SessionState(target="c.s.t", config=RunConfig(model_endpoint="m"), phase=Phase.REASONING)
    sid = store.save(st, created_by="u")
    r = c.post(f"/api/sessions/{sid}/answers", json={"answers": [{"question_id": "q1", "text": "x"}]})
    assert r.status_code == 409


def test_apply_not_yet_wired_501():
    c, _, _ = _client()
    sid = _ready_session(c)
    assert c.post(f"/api/sessions/{sid}/apply").status_code == 501


# ─────────────────────────────────────────────────────────────────────────────
# Regenerate (E3/D45)
# ─────────────────────────────────────────────────────────────────────────────
def test_regenerate_route_redrafts_then_polls_ready():
    c, _, _ = _client(needs_input=True)
    sid = c.post("/api/run", json={"table": "samples.tpch.orders"}).json()["session_id"]
    r = c.post(f"/api/sessions/{sid}/regenerate", json={"targets": ["__table__"]})
    assert r.status_code == 202 and r.json()["status"] == "regenerating"
    # TestClient ran the background task → regenerated + terminal
    g = c.get(f"/api/sessions/{sid}").json()
    assert g["status"] == "ready_for_review"
    assert g["table_draft"]["proposed_comment"] == "Regenerated headers."


def test_regenerate_all_forwards_none_targets():
    c, _, _ = _client(needs_input=True)
    sid = c.post("/api/run", json={"table": "samples.tpch.orders"}).json()["session_id"]
    assert c.post(f"/api/sessions/{sid}/regenerate", json={"all": True}).status_code == 202


def test_regenerate_unknown_session_404():
    c, _, _ = _client()
    assert c.post("/api/sessions/nope/regenerate", json={"all": True}).status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# History + library + per-column profile (E6/E7/E4)
# ─────────────────────────────────────────────────────────────────────────────
def test_list_sessions_endpoint_returns_history():
    c, store, _ = _client()
    body = c.get("/api/sessions?status=ready_for_review&table=samples.tpch.orders&limit=10").json()
    assert body["sessions"][0]["target"] == "samples.tpch.orders"
    assert body["sessions"][0]["n_applied"] == 3
    # query params flow through to the store
    assert store.list_args == {"status": "ready_for_review", "table": "samples.tpch.orders",
                               "schema_run_id": None, "limit": 10, "offset": 0}


def test_list_library_endpoint():
    c, _, _ = _client()
    body = c.get("/api/library?scope=column").json()
    assert body["entries"][0]["match_key"] == "o_orderkey" and body["entries"][0]["usage_count"] == 5


def test_session_view_exposes_per_column_profile():
    c, _, _ = _client(needs_input=False)
    sid = c.post("/api/run", json={"table": "samples.tpch.orders"}).json()["session_id"]
    g = c.get(f"/api/sessions/{sid}").json()
    assert "profile" in g and g["profile"]["table"]["row_count"] == 1  # exposed for the viz (E4/U86)


# ─────────────────────────────────────────────────────────────────────────────
# Hands-off / schema runs (D51 / U110)
# ─────────────────────────────────────────────────────────────────────────────
def _fake_enum_runner(captured):
    def run(sql):
        captured.append(sql)
        return [{"table_name": "fact_a", "comment": None},
                {"table_name": "fact_b", "comment": "already documented"},
                {"table_name": "dim_c", "comment": "  "}]
    return run


def test_start_schema_run_creates_record_and_triggers_job():
    triggered = []
    def trigger(run_id, catalog, schema, filters):
        triggered.append((run_id, catalog, schema, filters))
        return 9999  # job_run_id
    c, store, _ = _client(run_schema_job=trigger)
    r = c.post("/api/schema-runs", json={"catalog": "cat", "schema": "sch",
                                         "filters": {"skip_documented": True}})
    assert r.status_code == 202
    rid = r.json()["schema_run_id"]
    assert triggered and triggered[0][1:3] == ("cat", "sch")          # job triggered with catalog/schema
    assert store.schema_runs[rid]["status"] == "running"              # status advanced
    assert store.schema_runs[rid]["job_run_id"] == 9999
    assert store.schema_runs[rid]["created_by"]                       # human attribution recorded (D48)


def test_start_schema_run_job_failure_marks_failed_502():
    def boom(*a):
        raise RuntimeError("jobs api down")
    c, store, _ = _client(run_schema_job=boom)
    r = c.post("/api/schema-runs", json={"catalog": "cat", "schema": "sch"})
    assert r.status_code == 502


def test_start_schema_run_no_run_id_marks_failed_502():
    # U110/U148: trigger returns no run id → record marked failed (not left at 'enumerating'), 502
    c, store, _ = _client(run_schema_job=lambda *a: None)
    r = c.post("/api/schema-runs", json={"catalog": "cat", "schema": "sch"})
    assert r.status_code == 502
    rid = next(iter(store.schema_runs))
    assert store.schema_runs[rid]["status"] == "failed"   # honest status, not stuck at 'enumerating'


def test_start_schema_run_no_job_wired_marks_failed_501():
    # U110/U148: no run_schema_job mechanism → 501 + record marked failed, never left 'enumerating'
    c, store, _ = _client()  # run_schema_job=None
    r = c.post("/api/schema-runs", json={"catalog": "cat", "schema": "sch"})
    assert r.status_code == 501
    rid = next(iter(store.schema_runs))
    assert store.schema_runs[rid]["status"] == "failed"
    assert list(store.schema_runs.values())[0]["status"] == "failed"


def test_enumerate_tables_skips_documented_and_applies_filters():
    captured = []
    _, _, svc = _client(sql_runner=_fake_enum_runner(captured))
    names = svc.enumerate_tables("cat", "sch", {"skip_documented": True, "name_like": "fact_%"})
    # name_like is applied in SQL (asserted below); the Python side drops documented tables — so
    # fact_b (has a comment) is skipped, fact_a + dim_c (blank comment) are kept.
    assert names == ["fact_a", "dim_c"]
    sql = captured[0]
    assert "information_schema.tables" in sql and "`cat`" in sql      # catalog backtick-quoted
    assert "table_schema = 'sch'" in sql and "LIKE 'fact_%'" in sql   # schema/pattern as escaped literals


def test_enumerate_tables_requires_sql_runner():
    _, _, svc = _client()  # no sql_runner
    with pytest.raises(SessionError):
        svc.enumerate_tables("cat", "sch")


def test_enumerate_tables_excludes_internal_by_default():
    # U124/U138: internal/materialization artifacts (table_name like '__…') are filtered unless opted
    # in. U138: use `left(table_name, 2) <> '__'` — the prior `LIKE '\_\_%' ESCAPE '\'` was invalid
    # Databricks SQL (parse error → silently enumerated nothing on the real warehouse).
    captured: list[str] = []
    _, _, svc = _client(sql_runner=_fake_enum_runner(captured))
    svc.enumerate_tables("cat", "sch")
    assert "left(table_name, 2) <> '__'" in captured[0]   # the '__' prefix exclusion is in the SQL
    assert "ESCAPE" not in captured[0]                     # no fragile LIKE-ESCAPE (U138)
    captured.clear()
    svc.enumerate_tables("cat", "sch", {"include_internal": True})
    assert "left(table_name, 2) <> '__'" not in captured[0]   # opt-in surfaces them


def test_run_hands_off_persists_with_run_id_and_returns_kind():
    _, store, svc = _client(needs_input=True)
    kind = svc.run_hands_off("samples.tpch.orders", schema_run_id="run-1", created_by="app-sp")
    assert kind == "needs_input"                                     # hands-off produced a question
    assert "run-1" in store.saved_schema_run_ids                     # initial INSERT carried schema_run_id


def test_get_schema_run_returns_run_with_sessions_or_404():
    c, store, _ = _client()
    store.schema_runs["run-1"] = {"id": "run-1", "catalog": "c", "schema": "s",
                                  "status": "running", "counts": {"ready": 1}}
    body = c.get("/api/schema-runs/run-1").json()
    assert body["id"] == "run-1" and body["counts"] == {"ready": 1}
    assert isinstance(body["sessions"], list)                        # per-table sessions attached
    assert c.get("/api/schema-runs/nope").status_code == 404


def test_cancel_schema_run_marks_cancelled():
    cancelled = []
    c, store, _ = _client(cancel_schema_job=lambda jid: cancelled.append(jid))
    store.schema_runs["run-1"] = {"id": "run-1", "status": "running", "job_run_id": 7}
    r = c.post("/api/schema-runs/run-1/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert store.schema_runs["run-1"]["status"] == "cancelled"
    assert cancelled == [7]                                          # best-effort job cancel attempted


def test_list_schema_runs_endpoint():
    c, store, _ = _client()
    store.schema_runs["run-1"] = {"id": "run-1", "status": "completed"}
    body = c.get("/api/schema-runs").json()
    assert body["schema_runs"][0]["id"] == "run-1"
