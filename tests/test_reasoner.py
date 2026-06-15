"""Tests for the agent-core Reasoner (U36).

Covers U35 against U4 §3.4 + D32: structured JSON → drafts, column batching/merge,
current-comment carry-through (diff source, D7), per-batch error isolation (U4 §8),
dropped-column flagging (no fabrication, U4 §1), and budget compaction recording
(D32 / U24 §2). Hermetic — the model is a fake transport returning canned JSON.

Run: ``PYTHONPATH=src pytest tests/test_reasoner.py``
"""
from __future__ import annotations

import json
import re

import pytest

from geniefy_core.llm import HeuristicTokenCounter, LLMClient
from geniefy_core.reasoner import Reasoner
from geniefy_core.state import DraftStatus, Phase, RunConfig, SessionState
from geniefy_core.template import default_template


# ─────────────────────────────────────────────────────────────────────────────
# Fake transport driven by a responder(messages) -> content-string
# ─────────────────────────────────────────────────────────────────────────────
def _wrap(content: str) -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


class FakeTransport:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def send(self, messages, *, model, max_tokens, temperature, response_format=None):
        self.calls.append(messages)
        return _wrap(self.responder(messages))


def _instruction(messages) -> str:
    return messages[-1]["content"]


def _client(responder, **kw):
    return LLMClient(FakeTransport(responder), model_endpoint="m",
                     counter=HeuristicTokenCounter(), sleep=lambda s: None, **kw)


def _reasoner(responder, **kw):
    return Reasoner(_client(responder), default_template(), **kw)


def _profile(ncols: int, existing_table_comment=None):
    return {
        "profile_schema_version": "1.0",
        "table": {"full_name": "samples.tpch.orders", "row_count": 100,
                  "existing_comment": existing_table_comment},
        "columns": [
            {"name": f"c{i}", "ordinal": i + 1, "data_type": "bigint",
             "type_class": "integer", "existing_comment": None,
             "top_k": [{"value": "x", "count": 1}], "sample_values": ["1", "2"]}
            for i in range(ncols)
        ],
    }


def _state(ncols=3, mode_cfg=None, **profile_kw):
    cfg = mode_cfg or RunConfig(model_endpoint="m")
    return SessionState(target="samples.tpch.orders", config=cfg,
                        profile=_profile(ncols, **profile_kw),
                        context=[{"source": "uc_lineage", "text": "joins customer on c1"}])


# A responder that returns a table draft or a per-column batch depending on the instruction.
def _good_responder(messages):
    instr = _instruction(messages)
    if "table comment" in instr:
        return json.dumps({"proposed_comment": "Order headers.", "rationale": "from profile",
                           "evidence_refs": ["profile.row_count"], "self_confidence": 0.88,
                           "open_question": None})
    if "Summarize" in messages[0]["content"]:
        return "summary of context"
    # column batch: echo a draft for every requested column (names like c0, c1, ...)
    names = re.findall(r"c\d+", instr)
    return json.dumps({"columns": [
        {"name": n, "proposed_comment": f"def {n}", "rationale": "r",
         "conditional_fields": {"units": "rows"}, "evidence_refs": ["profile"],
         "self_confidence": 0.9, "open_question": None} for n in names
    ]})


# ─────────────────────────────────────────────────────────────────────────────
# Drafting
# ─────────────────────────────────────────────────────────────────────────────
def test_draft_populates_table_and_columns_and_phase():
    st = _state(ncols=3)
    _reasoner(_good_responder).draft(st)
    assert st.phase == Phase.REASONING
    assert st.table_draft.proposed_comment == "Order headers."
    assert st.table_draft.confidence == 0.88
    assert st.table_draft.evidence_refs == ["profile.row_count"]
    assert [c.column_name for c in st.column_drafts] == ["c0", "c1", "c2"]
    assert all(c.status == DraftStatus.DRAFT for c in st.column_drafts)
    assert st.column_drafts[0].proposed_comment == "def c0"
    assert st.column_drafts[0].conditional_fields == {"units": "rows"}
    assert st.column_drafts[0].data_type == "bigint" and st.column_drafts[0].ordinal == 1


# ─────────────────────────────────────────────────────────────────────────────
# Free-form tags + library reuse (D52/D53 / U104)
# ─────────────────────────────────────────────────────────────────────────────
def test_draft_extracts_and_normalizes_tags():
    def responder(messages):
        instr = _instruction(messages)
        if "table comment" in instr:
            return json.dumps({"proposed_comment": "Order headers.", "rationale": "r",
                               "tags": ["Fact", "fact", "  Revenue "], "evidence_refs": [],
                               "self_confidence": 0.9, "open_question": None})
        names = re.findall(r"c\d+", instr)
        return json.dumps({"columns": [
            {"name": n, "proposed_comment": f"def {n}", "rationale": "r",
             "tags": ["Identifier", "KEY"], "evidence_refs": [], "self_confidence": 0.9,
             "open_question": None} for n in names]})
    st = _state(ncols=2)
    _reasoner(responder).draft(st)
    assert st.table_draft.tags == ["fact", "revenue"]      # lowercased, trimmed, deduped
    assert st.column_drafts[0].tags == ["identifier", "key"]


def test_draft_table_extracts_steward_facts():
    # U114: the table call returns a `facts` object → hero chips; 'unknown'/empty values are dropped.
    def responder(messages):
        instr = _instruction(messages)
        if "table comment" in instr:
            return json.dumps({"proposed_comment": "Order headers.", "rationale": "r", "tags": [],
                               "facts": {"owner": "Data Eng", "freshness": "daily",
                                         "grain": "one row per order", "keys": "o_orderkey",
                                         "sensitivity": "unknown"},
                               "evidence_refs": [], "self_confidence": 0.9, "open_question": None})
        names = re.findall(r"c\d+", instr)
        return json.dumps({"columns": [
            {"name": n, "proposed_comment": f"def {n}", "rationale": "r", "tags": [],
             "evidence_refs": [], "self_confidence": 0.9, "open_question": None} for n in names]})
    st = _state(ncols=1)
    _reasoner(responder).draft(st)
    f = st.table_draft.facts
    assert f["owner"] == "Data Eng" and f["grain"] == "one row per order" and f["keys"] == "o_orderkey"
    assert "sensitivity" not in f          # 'unknown' dropped (don't render noise)


def test_table_prompt_requests_steward_facts():
    captured: list[str] = []
    def responder(messages):
        captured.append(messages[0]["content"])
        return _good_responder(messages)
    _reasoner(responder).draft(_state(ncols=1))
    table_prompt = next(p for p in captured if "TABLE comment" in p)
    assert '"facts"' in table_prompt and "owner" in table_prompt and "sensitivity" in table_prompt


def test_table_and_column_prompts_request_tags_and_library_reuse():
    captured: list[str] = []
    def responder(messages):
        captured.append(messages[0]["content"])  # the system prompt
        return _good_responder(messages)
    _reasoner(responder).draft(_state(ncols=1))
    sys_prompts = " ".join(captured)
    assert '"tags"' in sys_prompts                           # JSON shape requests tags
    assert "comment_library" in sys_prompts                  # reuse rule names the library source
    assert "prefer reusing" in sys_prompts.lower()           # suggestion-only reuse instruction
    # U116 (U107 live finding): the table prompt must demand PROSE, not a JSON-object dump
    table_prompt = next(p for p in captured if "TABLE comment" in p)
    assert "PROSE" in table_prompt and "NOT a JSON object" in table_prompt


def test_current_comment_carried_for_diff():
    st = _state(ncols=1, existing_table_comment="old table comment")
    _reasoner(_good_responder).draft(st)
    assert st.table_draft.current_comment == "old table comment"


def test_column_batching_makes_multiple_calls():
    st = _state(ncols=5)
    r = _reasoner(_good_responder, reason_batch_size=2)
    r.draft(st)
    # 1 table call + ceil(5/2)=3 column calls = 4
    assert len(r.llm._transport.calls) == 4
    assert [c.column_name for c in st.column_drafts] == ["c0", "c1", "c2", "c3", "c4"]


def test_dropped_column_is_flagged_not_fabricated():
    def responder(messages):
        instr = _instruction(messages)
        if "table comment" in instr:
            return json.dumps({"proposed_comment": "t", "rationale": "r",
                               "evidence_refs": [], "self_confidence": 0.9})
        # return only c0, omit c1
        return json.dumps({"columns": [{"name": "c0", "proposed_comment": "def c0",
                                        "rationale": "r", "self_confidence": 0.9}]})
    st = _state(ncols=2)
    _reasoner(responder).draft(st)
    assert st.column_draft("c0").status == DraftStatus.DRAFT
    assert st.column_draft("c1").status == DraftStatus.ERROR
    assert "did not return" in st.column_draft("c1").rationale


# ─────────────────────────────────────────────────────────────────────────────
# Per-batch error isolation (U4 §8)
# ─────────────────────────────────────────────────────────────────────────────
def test_failing_batch_isolated():
    def responder(messages):
        instr = _instruction(messages)
        if "table comment" in instr:
            return json.dumps({"proposed_comment": "t", "rationale": "r",
                               "evidence_refs": [], "self_confidence": 0.9})
        if "c0" in instr:  # first batch fails
            raise RuntimeError("model exploded")
        return json.dumps({"columns": [{"name": "c2", "proposed_comment": "def c2",
                                        "rationale": "r", "self_confidence": 0.9},
                                       {"name": "c3", "proposed_comment": "def c3",
                                        "rationale": "r", "self_confidence": 0.9}]})
    st = _state(ncols=4)
    _reasoner(responder, reason_batch_size=2).draft(st)
    assert st.column_draft("c0").status == DraftStatus.ERROR
    assert st.column_draft("c1").status == DraftStatus.ERROR
    assert st.column_draft("c2").status == DraftStatus.DRAFT  # later batch unaffected
    assert st.column_draft("c3").status == DraftStatus.DRAFT


def test_table_failure_isolated():
    def responder(messages):
        if "table comment" in _instruction(messages):
            raise RuntimeError("boom")
        return json.dumps({"columns": [{"name": "c0", "proposed_comment": "d",
                                        "rationale": "r", "self_confidence": 0.9}]})
    st = _state(ncols=1)
    _reasoner(responder).draft(st)
    assert st.table_draft.status == DraftStatus.ERROR
    assert st.column_draft("c0").status == DraftStatus.DRAFT  # columns still drafted


# ─────────────────────────────────────────────────────────────────────────────
# Budget compaction (D32 / U24 §2)
# ─────────────────────────────────────────────────────────────────────────────
def test_budget_compaction_recorded_on_drafts():
    # huge context forces the Budgeter to compact; the action is noted on the draft
    st = _state(ncols=1)
    st.context = [{"source": "uc_lineage", "text": "x" * 8000}]
    r = _reasoner(_good_responder, max_input_tokens=50)
    r.draft(st)
    assert "compacted to fit token budget" in (st.table_draft.rationale or "")
    # the transport still received a (compacted) call and produced a real draft
    assert st.table_draft.proposed_comment == "Order headers."


def test_no_budget_means_no_compaction_note():
    st = _state(ncols=1)
    _reasoner(_good_responder).draft(st)  # max_input_tokens=None
    assert "compacted" not in (st.table_draft.rationale or "")


def test_system_prompt_carries_template_and_grounding():
    st = _state(ncols=1)
    r = _reasoner(_good_responder)
    r.draft(st)
    system_msgs = [m[0]["content"] for m in r.llm._transport.calls]  # first msg is system
    # table call's system prompt mentions a required table field + the grounding rule
    assert any("purpose" in s and "never speculate" in s for s in system_msgs)
    # column call mentions definition + conditional fields
    assert any("definition" in s and "Conditional fields" in s for s in system_msgs)


def test_bad_reason_batch_size_rejected():
    with pytest.raises(ValueError):
        Reasoner(_client(_good_responder), default_template(), reason_batch_size=0)


# ─────────────────────────────────────────────────────────────────────────────
# Per-phase output budgets (U81 / D43 / E1)
# ─────────────────────────────────────────────────────────────────────────────
class _CapTransport:
    """Records the (phase, max_tokens) of each call so we can assert per-phase budgets."""

    def __init__(self):
        self.seen: list[tuple[str, int]] = []

    def send(self, messages, *, model, max_tokens, temperature, response_format=None):
        instr = messages[-1]["content"]
        phase = "table" if "table comment" in instr else "column"
        self.seen.append((phase, max_tokens))
        return _wrap(_good_responder(messages))


def _cap_reasoner(transport, **kw):
    llm = LLMClient(transport, model_endpoint="m", counter=HeuristicTokenCounter(),
                    sleep=lambda s: None, default_max_tokens=4096)
    return Reasoner(llm, default_template(), reason_batch_size=2, **kw)


def test_table_and_column_phases_use_their_own_max_tokens():
    t = _CapTransport()
    _cap_reasoner(t, table_max_tokens=20000, column_max_tokens=2000).draft(_state(ncols=3))
    table_tokens = [mt for ph, mt in t.seen if ph == "table"]
    column_tokens = [mt for ph, mt in t.seen if ph == "column"]
    assert table_tokens == [20000]                       # one table call, rich budget
    assert column_tokens and all(mt == 2000 for mt in column_tokens)  # each batch, lean budget


def test_per_phase_budgets_fall_back_to_client_default_when_unset():
    t = _CapTransport()
    _cap_reasoner(t).draft(_state(ncols=2))              # no per-phase override
    assert t.seen and all(mt == 4096 for _ph, mt in t.seen)  # LLMClient default_max_tokens


# ─────────────────────────────────────────────────────────────────────────────
# Two-pass: table comment → column context (U82 / E2 / D44)
# ─────────────────────────────────────────────────────────────────────────────
def _table_fails_responder(messages):
    # table call returns non-JSON (draft → ERROR); column calls return valid drafts
    instr = _instruction(messages)
    if "table comment" in instr:
        return "not json {"
    names = re.findall(r"c\d+", instr)
    return json.dumps({"columns": [
        {"name": n, "proposed_comment": f"def {n}", "rationale": "r", "conditional_fields": {},
         "evidence_refs": ["p"], "self_confidence": 0.9, "open_question": None} for n in names]})


def test_two_pass_threads_table_comment_into_column_prompt():
    r = _reasoner(_good_responder)
    r.draft(_state(ncols=2))
    col_instrs = [_instruction(m) for m in r.llm._transport.calls if "columns:" in _instruction(m)]
    # each column-batch call carries the just-generated table comment as grounding (E2/D44)
    assert col_instrs and all("TABLE COMMENT (just generated" in i and "Order headers." in i
                              for i in col_instrs)


def test_two_pass_omits_block_when_table_draft_failed():
    r = _reasoner(_table_fails_responder)
    st = _state(ncols=2)
    r.draft(st)
    assert st.table_draft.status == DraftStatus.ERROR
    col_instrs = [_instruction(m) for m in r.llm._transport.calls if "columns:" in _instruction(m)]
    assert col_instrs and all("TABLE COMMENT" not in i for i in col_instrs)  # no stale/empty block
    assert all(c.status != DraftStatus.ERROR for c in st.column_drafts)       # columns still drafted


# ─────────────────────────────────────────────────────────────────────────────
# Suggested answers for needs-input questions (U100 / D50)
# ─────────────────────────────────────────────────────────────────────────────
def test_suggest_answers_populates_from_llm():
    from geniefy_core.state import DraftKind, Question

    def responder(messages):  # the suggest call → {"answers": [...]}
        return json.dumps({"answers": [{"id": "q1", "answer": "An ISO 3166 country code."},
                                       {"id": "q2", "answer": "   "}]})

    r = _reasoner(responder)
    q1 = Question(id="q1", target_kind=DraftKind.COLUMN, target_name="c0", text="what is c0?")
    q2 = Question(id="q2", target_kind=DraftKind.COLUMN, target_name="c1", text="what is c1?")
    r.suggest_answers(_state(ncols=2), [q1, q2])
    assert q1.suggested_answer == "An ISO 3166 country code."
    assert q2.suggested_answer is None  # blank/whitespace answer ignored


def test_suggest_answers_best_effort_on_bad_json():
    from geniefy_core.state import DraftKind, Question

    r = _reasoner(lambda messages: "not json {")
    q = Question(id="q1", target_kind=DraftKind.COLUMN, target_name="c0", text="?")
    r.suggest_answers(_state(ncols=1), [q])  # must not raise
    assert q.suggested_answer is None
