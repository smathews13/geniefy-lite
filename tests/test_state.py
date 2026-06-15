"""Tests for the agent-core domain spine (U28).

Covers the U27 module against U4 §2/§7, D17 (serializable round-trip), D32 (token
fields), and U10 F3 (phase→status rollup). The standout check is **enum↔schema
parity**: the Python enums must match ``migrations/001_init.sql`` exactly, so a
``SessionState`` serializes straight into the live Lakebase schema. Hermetic.

Run: ``PYTHONPATH=src pytest tests/test_state.py``
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from geniefy_core.state import (
    ApplyStatus,
    Answer,
    ColumnDraft,
    DraftKind,
    DraftStatus,
    Phase,
    Question,
    RunConfig,
    RunError,
    SessionMode,
    SessionState,
    SessionStatus,
    StepResult,
    TableDraft,
)

MIGRATIONS_DIR = Path(__file__).parents[1] / "migrations"


# ─────────────────────────────────────────────────────────────────────────────
# Enum ↔ live schema parity (the drift guard)
# ─────────────────────────────────────────────────────────────────────────────
def _schema_enum(name: str) -> set[str]:
    """Union an enum's values across ALL migrations: the base `create type … as enum (…)` plus any
    later `alter type … add value '…'` extensions (e.g. 'hands_off' in 003, U108). The drift guard
    must follow enum extensions, not just the initial 001 definition."""
    values: set[str] = set()
    found = False
    for f in sorted(MIGRATIONS_DIR.glob("[0-9]*.sql")):
        sql = f.read_text()
        m = re.search(rf"create type geniefy\.{name} as enum\s*\(([^)]*)\)", sql, re.S)
        if m:
            found = True
            values |= set(re.findall(r"'([^']+)'", m.group(1)))
        values |= set(re.findall(
            rf"alter type geniefy\.{name}\s+add value(?:\s+if not exists)?\s+'([^']+)'", sql, re.I))
    assert found, f"enum {name} not found in migrations"
    return values


@pytest.mark.parametrize("enum_cls,sql_name", [
    (SessionStatus, "session_status"),
    (DraftStatus, "draft_status"),
    (ApplyStatus, "apply_status"),
    (SessionMode, "session_mode"),
])
def test_enums_match_migration(enum_cls, sql_name):
    assert {e.value for e in enum_cls} == _schema_enum(sql_name)


# ─────────────────────────────────────────────────────────────────────────────
# Phase → SessionStatus rollup (U10 F3)
# ─────────────────────────────────────────────────────────────────────────────
def test_judging_and_gating_roll_up_to_reasoning():
    assert Phase.JUDGING.to_session_status() == SessionStatus.REASONING
    assert Phase.GATING.to_session_status() == SessionStatus.REASONING


@pytest.mark.parametrize("phase", [
    Phase.CREATED, Phase.PROFILING, Phase.GATHERING_CONTEXT, Phase.REASONING,
    Phase.AWAITING_INPUT, Phase.READY_FOR_REVIEW, Phase.APPLYING, Phase.APPLIED,
    Phase.PAUSED, Phase.FAILED, Phase.CANCELLED,
])
def test_non_rollup_phases_map_to_same_status(phase):
    assert phase.to_session_status() == SessionStatus(phase.value)


def test_every_phase_maps_to_a_valid_status():
    for p in Phase:  # no phase should raise / produce an invalid status
        assert isinstance(p.to_session_status(), SessionStatus)


# ─────────────────────────────────────────────────────────────────────────────
# RunConfig
# ─────────────────────────────────────────────────────────────────────────────
def test_runconfig_defaults():
    c = RunConfig(model_endpoint="m")
    assert c.mode == SessionMode.INTERACTIVE and c.keep_threshold == 0.75
    assert c.profile_batch_size == 50 and c.reason_batch_size == 25
    assert c.summarize_over_budget is True and c.max_input_tokens_per_call is None
    # LLM + per-phase tunables (U81/D43/D47) carry sensible defaults
    assert c.max_retries == 5 and c.backoff_base == 0.5 and c.llm_temperature == 0.0
    assert c.default_max_tokens == 4096
    assert c.reason_table_max_tokens == 20000 and c.reason_column_max_tokens == 2000


def test_runconfig_round_trip_incl_token_fields():
    c = RunConfig(model_endpoint="m", keep_threshold=0.9, max_input_tokens_per_call=8000,
                  summary_target_tokens=1200, enabled_providers=["uc_lineage", "glean"],
                  mode=SessionMode.BATCH,
                  max_retries=8, backoff_base=1.5, llm_temperature=0.3,
                  default_max_tokens=8192, reason_table_max_tokens=30000,
                  reason_column_max_tokens=3000)
    c2 = RunConfig.from_dict(c.to_dict())
    assert c2.to_dict() == c.to_dict()
    # the new tunables specifically survive the round-trip (not just defaults)
    assert c2.max_retries == 8 and c2.backoff_base == 1.5 and c2.llm_temperature == 0.3
    assert c2.default_max_tokens == 8192
    assert c2.reason_table_max_tokens == 30000 and c2.reason_column_max_tokens == 3000
    assert c2.mode == SessionMode.BATCH and c2.max_input_tokens_per_call == 8000


# ─────────────────────────────────────────────────────────────────────────────
# Drafts
# ─────────────────────────────────────────────────────────────────────────────
def test_table_draft_round_trip():
    d = TableDraft(proposed_comment="x", confidence=0.9, status=DraftStatus.REVIEWED,
                   apply_status=ApplyStatus.APPLIED, judge_scores={"overall": 0.9},
                   evidence_refs=["lineage:1"], applied_by="sp")
    d2 = TableDraft.from_dict(d.to_dict())
    assert d2.to_dict() == d.to_dict()
    assert d2.status == DraftStatus.REVIEWED and d2.apply_status == ApplyStatus.APPLIED


def test_column_draft_round_trip_with_conditionals():
    d = ColumnDraft(column_name="o_custkey", ordinal=2, data_type="bigint",
                    conditional_fields={"fk_reference": "samples.tpch.customer.c_custkey"},
                    proposed_comment="FK", confidence=0.7, status=DraftStatus.NEEDS_INPUT)
    d2 = ColumnDraft.from_dict(d.to_dict())
    assert d2.to_dict() == d.to_dict()
    assert d2.column_name == "o_custkey" and d2.conditional_fields["fk_reference"].endswith("c_custkey")


def test_draft_status_and_apply_status_defaults():
    d = ColumnDraft(column_name="c")
    assert d.status == DraftStatus.DRAFT and d.apply_status == ApplyStatus.NOT_APPLIED


# ─────────────────────────────────────────────────────────────────────────────
# Question / Answer / RunError
# ─────────────────────────────────────────────────────────────────────────────
def test_question_round_trip():
    q = Question(id="q1", target_kind=DraftKind.COLUMN, target_name="tier", text="what?")
    q2 = Question.from_dict(q.to_dict())
    assert q2.to_dict() == q.to_dict() and q2.target_kind == DraftKind.COLUMN


def test_answer_and_run_error_round_trip():
    assert Answer.from_dict(Answer("q1", "a").to_dict()).text == "a"
    e = RunError("timeout", "slow")
    assert RunError.from_dict(e.to_dict()).code == "timeout"


# ─────────────────────────────────────────────────────────────────────────────
# SessionState
# ─────────────────────────────────────────────────────────────────────────────
def _state() -> SessionState:
    return SessionState(
        target="samples.tpch.orders", config=RunConfig(model_endpoint="m"),
        session_id="s1", template_id="default", phase=Phase.GATING,
        profile={"table": {"row_count": 5}}, schema_meta={"primary_key": ["o_orderkey"]},
        context=[{"source": "uc_lineage", "text": "x"}],
        table_draft=TableDraft(proposed_comment="orders", confidence=0.9),
        column_drafts=[
            ColumnDraft(column_name="o_orderkey", ordinal=1, status=DraftStatus.APPROVED),
            ColumnDraft(column_name="o_custkey", ordinal=2, status=DraftStatus.NEEDS_INPUT),
        ],
        open_questions=[Question(id="q1", target_kind=DraftKind.COLUMN, target_name="o_custkey", text="?")],
        mlflow_run_id="run-1",
    )


def test_session_state_round_trip_stable():
    st = _state()
    d = st.to_dict()
    st2 = SessionState.from_dict(d)
    assert st2.to_dict() == d
    # Object-field checks: a symmetric `to_dict() == to_dict()` would hide a field dropped on
    # BOTH sides by from_dict, so read the rehydrated OBJECT instead (U27 audit MED).
    assert st2.target == "samples.tpch.orders" and st2.session_id == "s1"
    assert st2.phase == Phase.GATING and st2.template_id == "default"
    assert st2.table_draft.proposed_comment == "orders" and st2.table_draft.confidence == 0.9
    assert [c.column_name for c in st2.column_drafts] == ["o_orderkey", "o_custkey"]
    assert st2.column_draft("o_orderkey").status == DraftStatus.APPROVED
    assert [q.id for q in st2.open_questions] == ["q1"]
    assert st2.open_questions[0].target_kind == DraftKind.COLUMN
    assert st2.schema_meta == {"primary_key": ["o_orderkey"]}
    assert st2.context == [{"source": "uc_lineage", "text": "x"}]
    assert st2.mlflow_run_id == "run-1"


def test_table_draft_defaults():
    # the table-draft default path (U27 audit MED — only the column-draft defaults were tested)
    d = TableDraft(proposed_comment="x")
    assert d.status == DraftStatus.DRAFT and d.apply_status == ApplyStatus.NOT_APPLIED
    assert d.proposed_comment == "x"


def test_draft_kind_has_table_and_column():
    # DraftKind drives Question.target_kind (table vs column) — pin its members (U27 audit MED)
    assert {k.name for k in DraftKind} == {"TABLE", "COLUMN"}


def test_draft_kind_matches_migration_enum():
    # draft_kind is a DB enum too (migrations/001_init.sql) — pin schema parity like the other
    # enums (U29 audit LOW: the name-only check above doesn't guard the live-schema values).
    assert {k.value for k in DraftKind} == _schema_enum("draft_kind")


def test_draft_from_dict_applies_defaults_when_fields_omitted():
    # the from_dict omitting-dict path (U29 audit MED — only the dataclass constructor default
    # was tested, but `_draft_common_from_dict` supplies the status/apply_status defaults
    # independently, so a regression there would slip past the constructor-default test).
    t = TableDraft.from_dict({"proposed_comment": "x"})
    assert t.status == DraftStatus.DRAFT and t.apply_status == ApplyStatus.NOT_APPLIED
    c = ColumnDraft.from_dict({"column_name": "o_orderkey"})
    assert c.status == DraftStatus.DRAFT and c.apply_status == ApplyStatus.NOT_APPLIED


def test_session_status_property_uses_rollup():
    assert _state().session_status == SessionStatus.REASONING  # GATING → REASONING


def test_column_draft_lookup():
    st = _state()
    assert st.column_draft("o_custkey").status == DraftStatus.NEEDS_INPUT
    assert st.column_draft("missing") is None


def test_minimal_state_from_dict_applies_defaults():
    st = SessionState.from_dict({"target": "a.b.c", "config": {"model_endpoint": "m"}})
    assert st.phase == Phase.CREATED and st.column_drafts == [] and st.table_draft is None
    assert st.config.keep_threshold == 0.75


# ─────────────────────────────────────────────────────────────────────────────
# Free-form tags (D53/U104)
# ─────────────────────────────────────────────────────────────────────────────
def test_draft_tags_round_trip():
    from geniefy_core.state import ColumnDraft, TableDraft
    td = TableDraft(proposed_comment="t", tags=["fact", "revenue"])
    assert TableDraft.from_dict(td.to_dict()).tags == ["fact", "revenue"]
    cd = ColumnDraft(column_name="country", tags=["dimension", "enum"])
    assert ColumnDraft.from_dict(cd.to_dict()).tags == ["dimension", "enum"]
    # default empty + tolerant of a missing key (older snapshots)
    assert TableDraft().to_dict()["tags"] == []
    assert ColumnDraft.from_dict({"column_name": "x"}).tags == []


def test_table_draft_facts_round_trip():
    from geniefy_core.state import TableDraft
    td = TableDraft(facts={"owner": "Data Eng", "grain": "one row per day"})
    assert TableDraft.from_dict(td.to_dict()).facts == {"owner": "Data Eng", "grain": "one row per day"}
    assert TableDraft().to_dict()["facts"] is None            # default None
    assert TableDraft.from_dict({}).facts is None             # tolerant of older snapshots


# ─────────────────────────────────────────────────────────────────────────────
# StepResult
# ─────────────────────────────────────────────────────────────────────────────
def test_step_result_constructors():
    st = _state()
    ni = StepResult.needs_input(st.open_questions, st)
    assert ni.kind == "needs_input" and ni.questions and ni.error is None
    rr = StepResult.ready_for_review(st)
    assert rr.kind == "ready_for_review" and rr.questions == []
    f = StepResult.failed(RunError("timeout", "x"), st)
    assert f.kind == "failed" and f.error.code == "timeout"
    assert ni.state is st and rr.state is st  # state always travels with the result (D17)
