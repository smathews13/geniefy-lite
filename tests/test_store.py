"""Tests for the App backend SessionStore (U46).

Covers U45 against U5 / U10 F5 / D17: save issues the sessions upsert + the draft
rewrite + commit, writes the full `session_state` jsonb snapshot, assigns a session_id
when new, and load rehydrates losslessly from that snapshot (a save→load round-trip via
the captured jsonb). Hermetic — the DB connection is a fake that records SQL.

Run: ``PYTHONPATH=src pytest tests/test_store.py``
"""
from __future__ import annotations

import json

import pytest

from geniefy_app.store import SessionStore, StoreError, _uuid_or_none
from geniefy_core.state import (
    ColumnDraft,
    DraftStatus,
    Phase,
    RunConfig,
    SessionState,
    TableDraft,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fake psycopg-style connection
# ─────────────────────────────────────────────────────────────────────────────
class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.conn.fetch_queue.pop(0) if self.conn.fetch_queue else None

    def fetchall(self):
        return self.conn.fetchall_queue.pop(0) if self.conn.fetchall_queue else []


class FakeConn:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self.fetch_queue: list = []
        self.fetchall_queue: list = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0  # psycopg2-style flag (0 = open); set non-zero to simulate a dead conn

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = 1

    # test helpers
    def stmts(self) -> list[str]:
        return [sql for sql, _ in self.executed]

    def find(self, needle: str):
        return [(sql, params) for sql, params in self.executed if needle in sql]


def _state(session_id=None, with_drafts=True):
    st = SessionState(
        target="samples.tpch.orders", config=RunConfig(model_endpoint="m"),
        session_id=session_id, template_id="default", phase=Phase.GATING,
        profile={"table": {"row_count": 1}}, mlflow_run_id="run-1",
    )
    if with_drafts:
        st.table_draft = TableDraft(proposed_comment="Order headers.", confidence=0.9,
                                    judge_scores={"overall": 0.9}, status=DraftStatus.REVIEWED)
        st.column_drafts = [
            ColumnDraft(column_name="o_orderkey", ordinal=1, data_type="bigint",
                        proposed_comment="PK", confidence=0.95, status=DraftStatus.APPROVED),
            ColumnDraft(column_name="o_custkey", ordinal=2, proposed_comment="FK",
                        conditional_fields={"fk_reference": "c.s.customer.c_custkey"}),
        ]
    return st


# ─────────────────────────────────────────────────────────────────────────────
# save
# ─────────────────────────────────────────────────────────────────────────────
def test_save_assigns_session_id_when_new():
    st = _state(session_id=None)
    sid = SessionStore(FakeConn()).save(st, created_by="user@example.com")
    assert sid and st.session_id == sid


def test_save_preserves_existing_session_id():
    st = _state(session_id="fixed-id")
    sid = SessionStore(FakeConn()).save(st, created_by="u@x.com")
    assert sid == "fixed-id"


def test_save_issues_upsert_rewrite_and_commit():
    conn = FakeConn()
    SessionStore(conn).save(_state(), created_by="u@x.com")
    stmts = conn.stmts()
    assert any("INSERT INTO geniefy.sessions" in s and "ON CONFLICT (id) DO UPDATE" in s for s in stmts)
    assert any("DELETE FROM geniefy.column_drafts" in s for s in stmts)
    assert any("DELETE FROM geniefy.table_drafts" in s for s in stmts)
    assert any("INSERT INTO geniefy.table_drafts" in s for s in stmts)
    # delete-before-insert ordering (FK-safe): columns deleted before table draft deleted
    order = [i for i, s in enumerate(stmts) if "DELETE FROM geniefy.column_drafts" in s][0]
    tbl_del = [i for i, s in enumerate(stmts) if "DELETE FROM geniefy.table_drafts" in s][0]
    assert order < tbl_del
    assert conn.commits == 1


def test_save_writes_session_snapshot_and_normalized_fields():
    conn = FakeConn()
    SessionStore(conn).save(_state(session_id="sid1"), created_by="u@x.com")
    sql, params = conn.find("INSERT INTO geniefy.sessions")[0]
    # params: (id, cat, sch, tbl, mode, status, template_id, config_json, snapshot_json, mlflow, created_by)
    assert params[0] == "sid1"
    assert (params[1], params[2], params[3]) == ("samples", "tpch", "orders")  # target split
    assert params[5] == "reasoning"          # GATING phase → session_status rollup (U10 F3)
    assert params[10] == "u@x.com"           # created_by
    snapshot = json.loads(params[8])         # the session_state jsonb
    assert snapshot["target"] == "samples.tpch.orders"
    assert snapshot["table_draft"]["proposed_comment"] == "Order headers."


def test_save_column_drafts_reference_one_table_draft_id():
    conn = FakeConn()
    SessionStore(conn).save(_state(session_id="sid1"), created_by="u@x.com")
    td_id = conn.find("INSERT INTO geniefy.table_drafts")[0][1][0]  # table_drafts id (param 0)
    col_inserts = conn.find("INSERT INTO geniefy.column_drafts")
    assert len(col_inserts) == 2
    for _sql, params in col_inserts:
        assert params[1] == td_id  # table_draft_id (param 1) points at the table draft


def test_save_without_table_draft_skips_draft_inserts():
    conn = FakeConn()
    SessionStore(conn).save(_state(with_drafts=False), created_by="u@x.com")
    assert conn.find("INSERT INTO geniefy.table_drafts") == []
    assert conn.find("INSERT INTO geniefy.column_drafts") == []
    assert conn.find("INSERT INTO geniefy.sessions")  # session still written
    assert conn.commits == 1


# ─────────────────────────────────────────────────────────────────────────────
# save → load round-trip (via the captured jsonb snapshot, U10 F5)
# ─────────────────────────────────────────────────────────────────────────────
def test_save_then_load_round_trip():
    conn = FakeConn()
    store = SessionStore(conn)
    original = _state(session_id="sid1")
    store.save(original, created_by="u@x.com")
    snapshot = json.loads(conn.find("INSERT INTO geniefy.sessions")[0][1][8])

    # simulate the DB returning that snapshot on load
    conn.fetch_queue.append((snapshot,))
    loaded = store.load("sid1")
    assert loaded.to_dict() == original.to_dict()
    assert loaded.column_draft("o_custkey").conditional_fields == {"fk_reference": "c.s.customer.c_custkey"}
    assert loaded.table_draft.confidence == 0.9


def test_load_missing_returns_none():
    conn = FakeConn()
    conn.fetch_queue.append(None)
    assert SessionStore(conn).load("nope") is None


def test_load_parses_jsonb_returned_as_text():
    conn = FakeConn()
    snap = _state(session_id="sid1").to_dict()
    conn.fetch_queue.append((json.dumps(snap),))  # driver returned text
    loaded = SessionStore(conn).load("sid1")
    assert loaded.session_id == "sid1"


# ─────────────────────────────────────────────────────────────────────────────
# guards
# ─────────────────────────────────────────────────────────────────────────────
def test_uuid_or_none_writes_uuid_or_null():
    # D38: sessions.template_id is a uuid FK; a real uuid passes through (canonicalized), a
    # template *name* or junk → NULL (U48 audit MED — D38 had no direct test).
    u = "595f9dad-0f09-4f60-80bf-9897a8793415"
    assert _uuid_or_none(u) == u
    assert _uuid_or_none("default") is None       # a name, not a uuid → NULL
    assert _uuid_or_none(None) is None and _uuid_or_none("") is None


def test_save_writes_template_name_as_null_but_keeps_it_in_snapshot():
    # D38 end-to-end: template_id="default" (a name) → the uuid column is NULL, while the name
    # is preserved in the session_state jsonb snapshot.
    conn = FakeConn()
    SessionStore(conn).save(_state(session_id="sid1"), created_by="u@x.com")
    _sql, params = conn.find("INSERT INTO geniefy.sessions")[0]
    assert params[6] is None                                   # template_id column → NULL
    assert json.loads(params[8])["template_id"] == "default"   # name preserved in jsonb


def test_append_audit_inserts_row():
    # D21 audit trail (U55): append_audit issues one audit_log INSERT + commit, with the
    # action/actor and before/after jsonb in the right columns.
    conn = FakeConn()
    SessionStore(conn).append_audit("sid1", action="applied", actor="u@x.com",
                                    draft_kind="column", before={"comment": None},
                                    after={"comment": "the order key"})
    ins = conn.find("INSERT INTO geniefy.audit_log")
    assert len(ins) == 1
    _sql, params = ins[0]
    # params: (session_id, draft_kind, draft_id, action, actor, before, after)
    assert params[0] == "sid1" and params[1] == "column" and params[3] == "applied"
    assert params[4] == "u@x.com"
    assert json.loads(params[5]) == {"comment": None}          # before jsonb
    assert json.loads(params[6]) == {"comment": "the order key"}  # after jsonb
    assert conn.commits == 1


def test_rejects_unsafe_schema():
    with pytest.raises(StoreError):
        SessionStore(FakeConn(), schema="geniefy; drop table")


def test_rejects_bad_target():
    st = _state(session_id="sid1")
    st.target = "not_qualified"
    with pytest.raises(StoreError):
        SessionStore(FakeConn()).save(st, created_by="u@x.com")


# ─────────────────────────────────────────────────────────────────────────────
# History list + comment library (U84 / E6 / D46)
# ─────────────────────────────────────────────────────────────────────────────
def test_list_sessions_maps_rows_and_binds_filters():
    conn = FakeConn()
    conn.fetchall_queue = [[
        ("sid-1", "samples", "tpch", "orders", "ready_for_review", "u@x.com",
         "2026-06-12T00:00:00", "2026-06-12T01:00:00", 9, 3),
    ]]
    out = SessionStore(conn).list_sessions(status="ready_for_review",
                                           table="samples.tpch.orders", limit=10, offset=0)
    assert out == [{"session_id": "sid-1", "target": "samples.tpch.orders",
                    "status": "ready_for_review", "created_by": "u@x.com",
                    "created_at": "2026-06-12T00:00:00", "updated_at": "2026-06-12T01:00:00",
                    "n_columns": 9, "n_applied": 3}]
    # filters + pagination are BOUND params, never interpolated (+ schema_run_id filter, U109)
    _, params = conn.find("FROM geniefy.sessions")[0]
    assert params == ("ready_for_review", "ready_for_review",
                      "samples.tpch.orders", "samples.tpch.orders", None, None, 10, 0)


def test_list_sessions_clamps_limit_and_offset():
    conn = FakeConn(); conn.fetchall_queue = [[]]
    SessionStore(conn).list_sessions(limit=9999, offset=-5)
    _, params = conn.find("FROM geniefy.sessions")[0]
    assert params[-2] == 200 and params[-1] == 0  # limit→200, offset→0


def test_upsert_library_inserts_when_absent():
    conn = FakeConn(); conn.fetch_queue = [None]  # SELECT existing → none
    SessionStore(conn).upsert_library_entry(scope="column", match_key="o_orderkey",
                                            canonical_comment="The order key.", approved_by="u@x.com")
    assert conn.find("INSERT INTO geniefy.comment_library") and conn.commits == 1


def test_upsert_library_bumps_usage_when_present():
    conn = FakeConn(); conn.fetch_queue = [("lib-1",)]  # SELECT existing → found
    SessionStore(conn).upsert_library_entry(scope="table", match_key="samples.tpch.orders",
                                            canonical_comment="Order headers.")
    upd = conn.find("UPDATE geniefy.comment_library")
    assert upd and "usage_count = usage_count + 1" in upd[0][0]
    assert upd[0][1][-1] == "lib-1"  # WHERE id = the existing row


def test_factory_opens_fresh_connection_per_call_and_closes():
    # production path (D49/U93): a NEW connection per op, closed after — never cached/reused, so
    # Lakebase scale-to-zero / OAuth ~1h expiry can never leave a stale connection behind.
    conns: list[FakeConn] = []

    def factory():
        c = FakeConn(); conns.append(c); return c

    store = SessionStore(connect=factory)
    store.load("sid1")
    store.load("sid2")
    assert len(conns) == 2                       # one fresh connection per call
    assert all(c.closed == 1 for c in conns)     # each closed after use
    assert all(c.executed for c in conns)        # each ran its own query


def test_factory_save_commits_on_fresh_conn_then_closes():
    conns: list[FakeConn] = []

    def factory():
        c = FakeConn(); conns.append(c); return c

    SessionStore(connect=factory).save(_state(session_id="sid1"), created_by="u@x.com")
    assert len(conns) == 1 and conns[0].commits == 1 and conns[0].closed == 1
    assert conns[0].find("INSERT INTO geniefy.sessions")


def test_no_factory_reuses_injected_conn_not_closed():
    # hermetic/test path: a static injected conn is reused (and NOT closed) so tests can inspect it.
    conn = FakeConn()
    SessionStore(conn).save(_state(session_id="sid1"), created_by="u@x.com")
    assert conn.commits == 1 and conn.closed == 0 and conn.find("INSERT INTO geniefy.sessions")


def test_list_library_maps_rows_and_parses_jsonb_tags():
    conn = FakeConn()
    conn.fetchall_queue = [[
        ("lib-1", "column", "o_orderkey", "The order key.", '["pii"]', 5,
         "samples.tpch.orders", "u@x.com", "2026-06-12T00:00:00", "applied"),
    ]]
    out = SessionStore(conn).list_library(scope="column")
    assert out[0]["match_key"] == "o_orderkey" and out[0]["usage_count"] == 5
    assert out[0]["tags"] == ["pii"]      # jsonb-as-text parsed to a list
    assert out[0]["status"] == "applied"  # lifecycle status surfaced (U103/§A6)


# ─────────────────────────────────────────────────────────────────────────────
# Library lifecycle + reuse (U103 / LLD-amend-006 §A / D52)
# ─────────────────────────────────────────────────────────────────────────────
def test_upsert_library_sets_status_and_bumps_usage_on_approve():
    # write-on-approve (§A3): status='approved' (default) + a usage bump on an existing key.
    conn = FakeConn(); conn.fetch_queue = [("lib-1",)]  # existing key
    SessionStore(conn).upsert_library_entry(scope="column", match_key="o_orderkey",
                                            canonical_comment="The order key.")
    sql, params = conn.find("UPDATE geniefy.comment_library")[0]
    assert "usage_count = usage_count + 1" in sql        # approve bumps
    assert "status = %s" in sql
    assert params[-2] == "approved" and params[-1] == "lib-1"  # status set, id last (WHERE)


def test_upsert_library_apply_upgrades_status_without_double_bump():
    # apply path (§A1): status='applied', bump_usage=False — a single approve→apply cycle
    # counts once (approve already bumped); apply only upgrades the status.
    conn = FakeConn(); conn.fetch_queue = [("lib-1",)]
    SessionStore(conn).upsert_library_entry(scope="column", match_key="o_orderkey",
                                            canonical_comment="The order key.",
                                            status="applied", bump_usage=False)
    sql, params = conn.find("UPDATE geniefy.comment_library")[0]
    assert "usage_count = usage_count + 1" not in sql    # apply does NOT double-bump
    assert params[-2] == "applied"


def test_upsert_library_new_entry_carries_status():
    conn = FakeConn(); conn.fetch_queue = [None]  # absent → INSERT
    SessionStore(conn).upsert_library_entry(scope="table", match_key="samples.tpch.orders",
                                            canonical_comment="Order headers.", status="applied")
    sql, params = conn.find("INSERT INTO geniefy.comment_library")[0]
    assert params[-1] == "applied"   # status is the last INSERT param
    assert conn.commits == 1


def test_sunset_library_entry_soft_retires_with_attribution():
    conn = FakeConn()
    SessionStore(conn).sunset_library_entry("lib-9", sunset_by="u@x.com")
    sql, params = conn.find("UPDATE geniefy.comment_library")[0]
    assert "status = 'sunset'" in sql and "sunset_at = now()" in sql
    assert params == ("u@x.com", "lib-9") and conn.commits == 1


def test_revive_library_entry_restores_to_approved():
    # D52 refinement: revive → 'approved' (never directly 'applied'); clears sunset attribution.
    conn = FakeConn()
    SessionStore(conn).revive_library_entry("lib-9")
    sql, params = conn.find("UPDATE geniefy.comment_library")[0]
    assert "status = 'approved'" in sql
    assert "sunset_at = NULL" in sql and "sunset_by = NULL" in sql
    assert params == ("lib-9",) and conn.commits == 1


def test_list_library_for_reuse_binds_keys_excludes_sunset_and_ranks():
    conn = FakeConn()
    conn.fetchall_queue = [[
        ("o_orderkey", "The order key.", '["identifier","pii"]', 7),
        ("country", "ISO-3166 alpha-2 country code.", "[]", 3),
    ]]
    out = SessionStore(conn).list_library_for_reuse(
        scope="column", match_keys=["o_orderkey", "country"], per_key_limit=2)
    sql, params = conn.find("FROM geniefy.comment_library")[0]
    assert "status IN ('approved','applied')" in sql      # sunset excluded from reuse (§A4)
    assert "match_key = ANY(%s)" in sql and "row_number() OVER" in sql
    assert params == ("column", ["o_orderkey", "country"], 2)
    assert out[0]["match_key"] == "o_orderkey" and out[0]["tags"] == ["identifier", "pii"]
    assert out[1]["canonical_comment"].startswith("ISO-3166")


def test_list_library_for_reuse_empty_keys_short_circuits():
    conn = FakeConn()
    assert SessionStore(conn).list_library_for_reuse(scope="column", match_keys=[]) == []
    assert conn.executed == []   # no query when there is nothing to match


def test_list_library_hides_sunset_by_default():
    conn = FakeConn(); conn.fetchall_queue = [[]]
    SessionStore(conn).list_library(scope="column")
    sql, params = conn.find("FROM geniefy.comment_library")[0]
    assert "status <> 'sunset'" in sql
    assert params[2] is False    # include_sunset defaults False → filter active


# ─────────────────────────────────────────────────────────────────────────────
# Schema runs (hands-off / D51 / U109)
# ─────────────────────────────────────────────────────────────────────────────
def test_save_writes_schema_run_id_on_insert_only():
    conn = FakeConn()
    run = "595f9dad-0f09-4f60-80bf-9897a8793415"
    SessionStore(conn).save(_state(session_id="sid1"), created_by="u@x.com", schema_run_id=run)
    sql, params = conn.find("INSERT INTO geniefy.sessions")[0]
    assert params[-1] == run                       # schema_run_id is the last INSERT param
    assert "schema_run_id" in sql                   # column written on INSERT
    assert "schema_run_id = EXCLUDED" not in sql    # NOT touched on conflict-update → preserved across saves


def test_create_schema_run_inserts_and_returns_id():
    conn = FakeConn()
    rid = SessionStore(conn).create_schema_run(catalog="c", schema="s", created_by="u@x.com",
                                               filters={"skip_documented": True})
    assert rid
    sql, params = conn.find("INSERT INTO geniefy.schema_runs")[0]
    assert params[0] == rid and params[1] == "c" and params[2] == "s" and params[-1] == "u@x.com"
    assert json.loads(params[3]) == {"skip_documented": True}   # filters jsonb (bound)
    assert conn.commits == 1


def test_update_schema_run_sets_only_provided_fields():
    conn = FakeConn()
    SessionStore(conn).update_schema_run("rid", status="running", total_tables=5)
    sql, params = conn.find("UPDATE geniefy.schema_runs")[0]
    assert "status = %s" in sql and "total_tables = %s" in sql and "job_run_id" not in sql
    assert params == ("running", 5, "rid")          # id is last (WHERE)


def test_update_schema_run_noop_when_nothing_to_set():
    conn = FakeConn()
    SessionStore(conn).update_schema_run("rid")
    assert conn.find("UPDATE geniefy.schema_runs") == [] and conn.commits == 0


def test_bump_counts_increments_known_key_with_bound_params():
    conn = FakeConn()
    SessionStore(conn).bump_counts("rid", "needs_input")
    sql, params = conn.find("UPDATE geniefy.schema_runs")[0]
    assert "jsonb_set(counts" in sql
    assert params == ("needs_input", "needs_input", "rid")   # key is bound (no interpolation)
    assert conn.commits == 1


def test_bump_counts_rejects_unknown_key():
    with pytest.raises(StoreError):
        SessionStore(FakeConn()).bump_counts("rid", "bogus")


def test_session_exists_binds_run_and_split_table():
    conn = FakeConn(); conn.fetch_queue = [(1,)]
    assert SessionStore(conn).session_exists("rid", "c.s.t") is True
    _sql, params = conn.find("FROM geniefy.sessions")[0]
    assert params == ("rid", "c", "s", "t")
    conn2 = FakeConn(); conn2.fetch_queue = [None]
    assert SessionStore(conn2).session_exists("rid", "c.s.t") is False


def test_list_schema_runs_maps_rows():
    conn = FakeConn()
    conn.fetchall_queue = [[
        ("rid", "c", "s", "running", '{"skip_documented": true}', 5, '{"ready": 2}', 42,
         "u@x.com", "t0", "t1"),
    ]]
    out = SessionStore(conn).list_schema_runs()
    assert out[0]["id"] == "rid" and out[0]["catalog"] == "c" and out[0]["schema"] == "s"
    assert out[0]["status"] == "running" and out[0]["total_tables"] == 5
    assert out[0]["counts"] == {"ready": 2} and out[0]["filters"] == {"skip_documented": True}
    assert out[0]["job_run_id"] == 42


def test_get_schema_run_maps_or_none():
    conn = FakeConn()
    conn.fetch_queue = [("rid", "c", "s", "completed", "{}", 3, '{"ready": 3}', None, "u", "t0", "t1")]
    got = SessionStore(conn).get_schema_run("rid")
    assert got["id"] == "rid" and got["status"] == "completed" and got["counts"] == {"ready": 3}
    conn2 = FakeConn(); conn2.fetch_queue = [None]
    assert SessionStore(conn2).get_schema_run("nope") is None
