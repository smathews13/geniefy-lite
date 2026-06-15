"""Tests for the agent-core Gate (U31).

Covers the U30 routing logic against U4 §3.6: the score threshold, the three hard-signal
force-low rules, and the mode behavior (interactive → Questions; batch → low_confidence).
Hermetic — pure logic over spine objects (U27).

Run: ``PYTHONPATH=src pytest tests/test_gate.py``
"""
from __future__ import annotations

import pytest

from geniefy_core.gate import (
    REASON_AMBIGUOUS_UNUSED_HIGH_NULL,
    REASON_BELOW_THRESHOLD,
    REASON_ENUM_NO_DECODE,
    REASON_KEYLIKE_NO_TARGET,
    REASON_NO_CONFIDENCE,
    Gate,
    HardSignals,
)
from geniefy_core.state import (
    ColumnDraft,
    DraftKind,
    DraftStatus,
    RunConfig,
    SessionMode,
    SessionState,
    TableDraft,
)


def _cfg(mode=SessionMode.INTERACTIVE, keep=0.75) -> RunConfig:
    return RunConfig(model_endpoint="m", mode=mode, keep_threshold=keep)


# ─────────────────────────────────────────────────────────────────────────────
# route_item — threshold
# ─────────────────────────────────────────────────────────────────────────────
def test_keep_when_confident_and_no_trip():
    out = Gate(_cfg()).route_item(0.80, HardSignals())
    assert out.keep and out.reasons == []


def test_needs_input_below_threshold():
    out = Gate(_cfg()).route_item(0.74, HardSignals())
    assert not out.keep and out.reasons == [REASON_BELOW_THRESHOLD]


def test_threshold_is_inclusive():
    assert Gate(_cfg(keep=0.75)).route_item(0.75, HardSignals()).keep


def test_none_confidence_needs_input():
    out = Gate(_cfg()).route_item(None, HardSignals())
    assert not out.keep and out.reasons == [REASON_NO_CONFIDENCE]


# ─────────────────────────────────────────────────────────────────────────────
# Hard-signal force-low (override a high score)
# ─────────────────────────────────────────────────────────────────────────────
def test_enum_no_decode_forces_low_even_if_confident():
    sig = HardSignals(enum_candidate=True, has_decode_source=False)
    out = Gate(_cfg()).route_item(0.99, sig)
    assert not out.keep and REASON_ENUM_NO_DECODE in out.reasons


def test_enum_with_decode_does_not_trip():
    sig = HardSignals(enum_candidate=True, has_decode_source=True)
    assert Gate(_cfg()).route_item(0.99, sig).keep


def test_keylike_no_target_forces_low():
    sig = HardSignals(keylike=True, has_key_target=False)
    out = Gate(_cfg()).route_item(0.95, sig)
    assert not out.keep and REASON_KEYLIKE_NO_TARGET in out.reasons


def test_keylike_with_target_does_not_trip():
    assert Gate(_cfg()).route_item(0.95, HardSignals(keylike=True, has_key_target=True)).keep


def test_ambiguous_unused_high_null_forces_low():
    sig = HardSignals(ambiguous_name=True, has_usage_evidence=False, high_null_fraction=True)
    out = Gate(_cfg()).route_item(0.99, sig)
    assert not out.keep and REASON_AMBIGUOUS_UNUSED_HIGH_NULL in out.reasons


def test_ambiguous_but_used_does_not_trip():
    # all three sub-conditions are required; usage evidence breaks the trip
    sig = HardSignals(ambiguous_name=True, has_usage_evidence=True, high_null_fraction=True)
    assert Gate(_cfg()).route_item(0.99, sig).keep


def test_ambiguous_unused_but_not_high_null_does_not_trip():
    # all three required: not-high-null alone breaks the trip (U30 audit MED — negative case)
    sig = HardSignals(ambiguous_name=True, has_usage_evidence=False, high_null_fraction=False)
    assert Gate(_cfg()).route_item(0.99, sig).keep


def test_high_null_unused_but_not_ambiguous_does_not_trip():
    # all three required: a clear (non-ambiguous) name breaks the trip (U30 audit MED)
    sig = HardSignals(ambiguous_name=False, has_usage_evidence=False, high_null_fraction=True)
    assert Gate(_cfg()).route_item(0.99, sig).keep


def test_multiple_trips_collected():
    sig = HardSignals(enum_candidate=True, has_decode_source=False, keylike=True, has_key_target=False)
    trips = Gate(_cfg()).hard_trips(sig)
    assert REASON_ENUM_NO_DECODE in trips and REASON_KEYLIKE_NO_TARGET in trips


# ─────────────────────────────────────────────────────────────────────────────
# apply — interactive vs batch
# ─────────────────────────────────────────────────────────────────────────────
def _state(mode=SessionMode.INTERACTIVE) -> SessionState:
    return SessionState(
        target="samples.tpch.orders", config=_cfg(mode=mode),
        table_draft=TableDraft(proposed_comment="orders", confidence=0.9),  # keep
        column_drafts=[
            ColumnDraft(column_name="o_orderkey", confidence=0.95),  # keep
            ColumnDraft(column_name="tier", confidence=0.99),        # enum_no_decode → needs_input
            ColumnDraft(column_name="o_custkey", confidence=0.5),    # below_threshold
        ],
    )


def _signals() -> dict:
    return {
        None: HardSignals(),                                      # table: keep
        "o_orderkey": HardSignals(keylike=True, has_key_target=True),  # keep (PK resolved)
        "tier": HardSignals(enum_candidate=True, has_decode_source=False),
        "o_custkey": HardSignals(),
    }


def test_apply_interactive_creates_questions_and_marks_needs_input():
    st = _state(SessionMode.INTERACTIVE)
    qs = Gate(st.config).apply(st, _signals())
    # tier (enum) + o_custkey (below threshold) → 2 questions; table + o_orderkey kept
    assert {q.target_name for q in qs} == {"tier", "o_custkey"}
    assert st.column_draft("tier").status == DraftStatus.NEEDS_INPUT
    assert st.column_draft("o_custkey").status == DraftStatus.NEEDS_INPUT
    assert st.column_draft("o_orderkey").status == DraftStatus.DRAFT  # kept, untouched
    assert st.table_draft.status == DraftStatus.DRAFT
    assert st.open_questions == qs
    # the enum question is targeted
    tierq = next(q for q in qs if q.target_name == "tier")
    assert "decode" in tierq.text and tierq.target_kind == DraftKind.COLUMN


def test_apply_batch_marks_low_confidence_and_asks_nothing():
    st = _state(SessionMode.BATCH)
    qs = Gate(st.config).apply(st, _signals())
    assert qs == [] and st.open_questions == []
    assert st.column_draft("tier").status == DraftStatus.LOW_CONFIDENCE
    assert st.column_draft("o_custkey").status == DraftStatus.LOW_CONFIDENCE
    assert "low confidence" in (st.column_draft("tier").rationale or "")
    assert st.column_draft("o_orderkey").status == DraftStatus.DRAFT  # kept


def test_apply_trips_the_table_draft():
    # the TABLE draft itself can route to needs_input, not just columns (U30 audit MED — the
    # apply() table path was untested). Below-threshold table confidence → a table Question.
    st = SessionState(
        target="samples.tpch.orders", config=_cfg(SessionMode.INTERACTIVE),
        table_draft=TableDraft(proposed_comment="orders", confidence=0.4),  # below threshold
        column_drafts=[ColumnDraft(column_name="o_orderkey", confidence=0.95)],  # kept
    )
    qs = Gate(st.config).apply(st, {None: HardSignals(), "o_orderkey": HardSignals()})
    tableq = [q for q in qs if q.target_name is None]
    assert len(tableq) == 1 and tableq[0].target_kind == DraftKind.TABLE
    assert st.table_draft.status == DraftStatus.NEEDS_INPUT
    assert st.column_draft("o_orderkey").status == DraftStatus.DRAFT  # kept, untouched


def test_apply_is_idempotent_for_resolved_drafts():
    st = _state(SessionMode.INTERACTIVE)
    st.column_draft("o_custkey").status = DraftStatus.APPROVED  # human already resolved
    qs = Gate(st.config).apply(st, _signals())
    # only tier should be asked; the approved draft is left alone
    assert {q.target_name for q in qs} == {"tier"}
    assert st.column_draft("o_custkey").status == DraftStatus.APPROVED


def test_question_text_variants():
    g = Gate(_cfg())
    assert "decode" in g.question_text(DraftKind.COLUMN, "tier", [REASON_ENUM_NO_DECODE])
    assert "reference" in g.question_text(DraftKind.COLUMN, "fk", [REASON_KEYLIKE_NO_TARGET])
    assert "mostly null" in g.question_text(DraftKind.COLUMN, "x", [REASON_AMBIGUOUS_UNUSED_HIGH_NULL])
    assert "this table" in g.question_text(DraftKind.TABLE, None, [REASON_BELOW_THRESHOLD])
