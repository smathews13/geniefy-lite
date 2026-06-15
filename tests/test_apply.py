"""Tests for the UC apply write-path (U53, D7/D21).

Covers the governed apply: diff-first conflict detection + re-confirm, idempotent no-op,
no-clobber-with-empty, per-item failure isolation, only approved/edited applied, escaped
DDL, per-item checkpoint, and target filtering. Hermetic — fake warehouse (UC I/O) + fake
store.

Run: ``PYTHONPATH=src pytest tests/test_apply.py``
"""
from __future__ import annotations

import re
import uuid

import pytest

from geniefy_app.apply import ApplyError, Applier
from geniefy_core.state import (
    ApplyStatus,
    ColumnDraft,
    DraftStatus,
    RunConfig,
    SessionState,
    TableDraft,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────────────
class FakeWarehouse:
    """Returns canned live comments for information_schema reads; records DDL; can fail
    DDL for named targets (simulating a missing MODIFY grant)."""

    def __init__(self, live: dict | None = None, fail_on: set | None = None):
        self.live = live or {}          # {"__table__"|col: live_comment}
        self.fail_on = fail_on or set()
        self.ddl: list[str] = []

    def __call__(self, sql: str):
        if "information_schema.tables" in sql:
            return [{"comment": self.live.get("__table__")}]
        if "information_schema.columns" in sql:
            col = re.search(r"column_name = '([^']+)'", sql).group(1)
            return [{"comment": self.live.get(col)}]
        if sql.startswith("COMMENT ON TABLE"):
            if "__table__" in self.fail_on:
                raise RuntimeError("permission denied: MODIFY")
            self.ddl.append(sql)
            return []
        if sql.startswith("ALTER TABLE"):
            col = re.search(r"ALTER COLUMN `([^`]+)`", sql).group(1)
            if col in self.fail_on:
                raise RuntimeError("permission denied: MODIFY")
            self.ddl.append(sql)
            return []
        raise AssertionError(f"unexpected SQL: {sql}")


class FakeStore:
    def __init__(self):
        self.data: dict[str, dict] = {}
        self.saves = 0
        self.audits: list[dict] = []  # D21 audit_log appends (U55)
        self.library: list[dict] = []  # comment_library upserts (U84/E6)

    def save(self, state: SessionState, *, created_by: str) -> str:
        self.saves += 1
        if not state.session_id:
            state.session_id = str(uuid.uuid4())
        self.data[state.session_id] = state.to_dict()
        return state.session_id

    def append_audit(self, session_id, *, action, actor, draft_kind=None, draft_id=None,
                     before=None, after=None):
        self.audits.append({"session_id": session_id, "action": action, "actor": actor,
                            "draft_kind": draft_kind, "before": before, "after": after})

    def upsert_library_entry(self, **kwargs):
        self.library.append(kwargs)

    def load(self, session_id: str):
        d = self.data.get(session_id)
        return SessionState.from_dict(d) if d else None


def _seed(store, *, table_draft=None, columns=()):
    st = SessionState(target="samples.tpch.orders", config=RunConfig(model_endpoint="m"))
    st.table_draft = table_draft
    st.column_drafts = list(columns)
    return store.save(st, created_by="seed")


def _col(name, *, proposed, current=None, status=DraftStatus.APPROVED):
    return ColumnDraft(column_name=name, proposed_comment=proposed, current_comment=current, status=status)


# ─────────────────────────────────────────────────────────────────────────────
# Apply (happy paths)
# ─────────────────────────────────────────────────────────────────────────────
def test_apply_approved_column_writes_ddl():
    store, wh = FakeStore(), FakeWarehouse(live={"o_orderkey": None})
    sid = _seed(store, columns=[_col("o_orderkey", proposed="The order's surrogate key.")])
    res = Applier(wh, store).apply(sid, created_by="u@x.com")
    assert res["applied"] == 1
    assert any("ALTER TABLE" in d and "ALTER COLUMN `o_orderkey`" in d for d in wh.ddl)
    reloaded = store.load(sid).column_draft("o_orderkey")
    assert reloaded.apply_status == ApplyStatus.APPLIED and reloaded.status == DraftStatus.APPLIED
    assert reloaded.applied_comment == "The order's surrogate key." and reloaded.applied_by == "u@x.com"


def test_apply_table_draft_writes_comment_on_table():
    store, wh = FakeStore(), FakeWarehouse(live={"__table__": None})
    sid = _seed(store, table_draft=TableDraft(proposed_comment="Order headers.", status=DraftStatus.APPROVED))
    Applier(wh, store).apply(sid, created_by="u")
    assert any(d.startswith("COMMENT ON TABLE") and "IS 'Order headers.'" in d for d in wh.ddl)


def test_escaping_doubles_single_quotes():
    store, wh = FakeStore(), FakeWarehouse(live={"c": None})
    sid = _seed(store, columns=[_col("c", proposed="it's the key")])
    Applier(wh, store).apply(sid, created_by="u")
    assert any("COMMENT 'it''s the key'" in d for d in wh.ddl)


# ─────────────────────────────────────────────────────────────────────────────
# Idempotency / conflict / no-clobber (D21, U6 §8)
# ─────────────────────────────────────────────────────────────────────────────
def test_noop_when_proposed_equals_live():
    store, wh = FakeStore(), FakeWarehouse(live={"c": "same"})
    sid = _seed(store, columns=[_col("c", proposed="same", current="same")])
    res = Applier(wh, store).apply(sid, created_by="u")
    assert res["results"][0]["status"] == "skipped_noop" and wh.ddl == []
    assert store.load(sid).column_draft("c").apply_status == ApplyStatus.SKIPPED_NOOP


def test_conflict_when_live_changed_since_profiling():
    store = FakeStore()
    # profiled current="old"; someone changed it to "changed by someone else"
    wh = FakeWarehouse(live={"c": "changed by someone else"})
    sid = _seed(store, columns=[_col("c", proposed="new", current="old")])
    res = Applier(wh, store).apply(sid, created_by="u")
    r = res["results"][0]
    assert r["status"] == "conflict" and r["live"] == "changed by someone else" and r["expected"] == "old"
    assert wh.ddl == []  # no write on conflict
    assert store.load(sid).column_draft("c").apply_status == ApplyStatus.CONFLICT


def test_conflict_overridden_by_confirm():
    store = FakeStore()
    wh = FakeWarehouse(live={"c": "changed by someone else"})
    sid = _seed(store, columns=[_col("c", proposed="new", current="old")])
    res = Applier(wh, store).apply(sid, created_by="u", confirm=True)
    assert res["results"][0]["status"] == "applied" and any("COMMENT 'new'" in d for d in wh.ddl)


def test_empty_over_existing_refused():
    store, wh = FakeStore(), FakeWarehouse(live={"c": "a good comment"})
    sid = _seed(store, columns=[_col("c", proposed="   ", current="a good comment")])
    res = Applier(wh, store).apply(sid, created_by="u")
    assert res["results"][0]["status"] == "failed" and wh.ddl == []


def test_empty_over_empty_is_noop():
    store, wh = FakeStore(), FakeWarehouse(live={"c": None})
    sid = _seed(store, columns=[_col("c", proposed="", current=None)])
    assert Applier(wh, store).apply(sid, created_by="u")["results"][0]["status"] == "skipped_noop"


# ─────────────────────────────────────────────────────────────────────────────
# Failure isolation + eligibility + checkpoint + targets
# ─────────────────────────────────────────────────────────────────────────────
def test_failed_item_isolated_others_proceed():
    store = FakeStore()
    wh = FakeWarehouse(live={"a": None, "b": None}, fail_on={"a"})
    sid = _seed(store, columns=[_col("a", proposed="x"), _col("b", proposed="y")])
    res = Applier(wh, store).apply(sid, created_by="u")
    by = {r["target"]: r["status"] for r in res["results"]}
    assert by == {"a": "failed", "b": "applied"}
    st = store.load(sid)
    assert st.column_draft("a").apply_status == ApplyStatus.FAILED
    assert st.column_draft("b").apply_status == ApplyStatus.APPLIED


def test_only_approved_or_edited_are_applied():
    store, wh = FakeStore(), FakeWarehouse(live={"a": None, "b": None, "c": None})
    sid = _seed(store, columns=[
        _col("a", proposed="x", status=DraftStatus.DRAFT),
        _col("b", proposed="y", status=DraftStatus.APPROVED),
        _col("c", proposed="z", status=DraftStatus.EDITED),
    ])
    res = Applier(wh, store).apply(sid, created_by="u")
    assert {r["target"] for r in res["results"]} == {"b", "c"}  # a (draft) skipped


def test_per_item_checkpoint_saves_each():
    store, wh = FakeStore(), FakeWarehouse(live={"a": None, "b": None})
    sid = _seed(store, columns=[_col("a", proposed="x"), _col("b", proposed="y")])
    store.saves = 0
    Applier(wh, store).apply(sid, created_by="u")
    assert store.saves == 2  # one checkpoint per applied item (D21)


def test_targets_filter_limits_apply():
    store, wh = FakeStore(), FakeWarehouse(live={"a": None, "b": None})
    sid = _seed(store, columns=[_col("a", proposed="x"), _col("b", proposed="y")])
    res = Applier(wh, store).apply(sid, created_by="u", targets=["a"])
    assert {r["target"] for r in res["results"]} == {"a"}


def test_session_not_found_raises():
    with pytest.raises(ApplyError):
        Applier(FakeWarehouse(), FakeStore()).apply("nope", created_by="u")


# ─────────────────────────────────────────────────────────────────────────────
# D21 audit trail (U55) — each UC write appends an append-only audit_log row
# ─────────────────────────────────────────────────────────────────────────────
def test_apply_appends_audit_row_per_written_item():
    store, wh = FakeStore(), FakeWarehouse(live={"a": None, "b": "old"})
    sid = _seed(store, table_draft=TableDraft(proposed_comment="Order headers.", status=DraftStatus.APPROVED),
                columns=[_col("a", proposed="x"), _col("b", proposed="y", current="old")])
    Applier(wh, store).apply(sid, created_by="u@x.com")
    applied = [a for a in store.audits if a["action"] == "applied"]
    # table + 2 columns all written → 3 audit rows, with the right kinds + actor + before/after
    assert len(applied) == 3
    assert {a["draft_kind"] for a in applied} == {"table", "column"}
    assert all(a["actor"] == "u@x.com" for a in applied)
    col_a = next(a for a in applied if a["after"]["comment"] == "x")
    assert col_a["draft_kind"] == "column" and col_a["before"]["comment"] is None


def test_no_audit_row_for_noop_or_conflict():
    # only genuine UC writes are audited — a no-op / conflict / failed item appends nothing
    store, wh = FakeStore(), FakeWarehouse(live={"n": "same", "c": "changed"})
    sid = _seed(store, columns=[_col("n", proposed="same", current="same"),      # noop
                                _col("c", proposed="new", current="old")])        # conflict
    Applier(wh, store).apply(sid, created_by="u")
    assert [a for a in store.audits if a["action"] == "applied"] == []


# ─────────────────────────────────────────────────────────────────────────────
# On-behalf-of (OBO) apply — runs as the END USER (U85 / D48)
# ─────────────────────────────────────────────────────────────────────────────
def test_obo_apply_uses_user_token_runner_not_the_sp():
    # When OBO-capable, the read+write SQL goes through the user-token runner built from the
    # forwarded access token — never the SP runner (D48: writes honor the user's UC grants).
    store = FakeStore()
    sp_wh = FakeWarehouse(live={"o_orderkey": None})    # SP runner — must stay untouched
    user_wh = FakeWarehouse(live={"o_orderkey": None})  # the OBO/user runner
    seen: list[str] = []

    def make_user_sql(tok):
        seen.append(tok)
        return user_wh

    sid = _seed(store, columns=[_col("o_orderkey", proposed="The order key.")])
    res = Applier(sp_wh, store, make_user_sql=make_user_sql).apply(
        sid, created_by="user@x.com", access_token="user-tok")
    assert res["applied"] == 1
    assert seen == ["user-tok"]                                       # runner built with the user's token
    assert any("ALTER COLUMN `o_orderkey`" in d for d in user_wh.ddl)  # write ran as the user
    assert sp_wh.ddl == []                                            # SP never wrote
    assert store.audits[-1]["actor"] == "user@x.com"                 # apply audit actor = the user


def test_obo_missing_token_fails_each_item_without_writing():
    # OBO-capable but no token (operator hasn't added the `sql` scope) → every applyable item
    # fails with a clear reason; NOTHING is written as the SP (D48 no-silent-fallback).
    store = FakeStore()
    sp_wh = FakeWarehouse(live={"o_orderkey": None})
    user_wh = FakeWarehouse()
    sid = _seed(store,
                table_draft=TableDraft(proposed_comment="Order headers.", status=DraftStatus.APPROVED),
                columns=[_col("o_orderkey", proposed="The order key.")])
    res = Applier(sp_wh, store, make_user_sql=lambda tok: user_wh).apply(
        sid, created_by="user@x.com", access_token=None)
    assert res["applied"] == 0 and len(res["results"]) == 2          # table + column both reported
    assert all(r["status"] == "failed" and "on-behalf-of" in r["error"] for r in res["results"])
    assert sp_wh.ddl == [] and user_wh.ddl == []                     # nothing written anywhere
    assert store.load(sid).column_draft("o_orderkey").apply_status == ApplyStatus.FAILED
    assert [a for a in store.audits if a["action"] == "applied"] == []  # no governance write


def test_apply_records_applied_comment_in_library():
    # a genuine UC write also lands in the reusable comment_library (E6/D46)
    store, wh = FakeStore(), FakeWarehouse(live={"o_orderkey": None})
    sid = _seed(store, columns=[_col("o_orderkey", proposed="The order key.")])
    Applier(wh, store).apply(sid, created_by="u@x.com")
    assert len(store.library) == 1
    e = store.library[0]
    assert e["scope"] == "column" and e["match_key"] == "o_orderkey"
    assert e["canonical_comment"] == "The order key." and e["approved_by"] == "u@x.com"
    assert e["source_table_ref"] == "samples.tpch.orders"
    # apply UPGRADES the entry to 'applied' and does NOT re-bump usage (approve already did, D52 §A1)
    assert e["status"] == "applied" and e["bump_usage"] is False


def test_apply_noop_or_conflict_does_not_write_library():
    store, wh = FakeStore(), FakeWarehouse(live={"n": "same"})
    sid = _seed(store, columns=[_col("n", proposed="same", current="same")])  # idempotent no-op
    Applier(wh, store).apply(sid, created_by="u")
    assert store.library == []  # only genuine writes populate the library


def test_no_factory_uses_injected_runner_regardless_of_token():
    # Hermetic/legacy path: with no make_user_sql, the injected runner is used (apply-logic is
    # principal-agnostic) — a passed token is simply ignored, so existing call sites are unaffected.
    store, wh = FakeStore(), FakeWarehouse(live={"o_orderkey": None})
    sid = _seed(store, columns=[_col("o_orderkey", proposed="The order key.")])
    res = Applier(wh, store).apply(sid, created_by="u", access_token="ignored")
    assert res["applied"] == 1 and any("ALTER COLUMN `o_orderkey`" in d for d in wh.ddl)
