"""Integration tests for the DocumentationOrchestrator (U42).

Wires the REAL agent-core components (Profiler · ContextGatherer · Reasoner · Judge ·
Gate) together — only the *boundaries* are faked: the profile provider and the model
transport. This exercises the full loop end-to-end (U4 §2/§4): run → ready/needs_input,
the interactive pause, resume folding answers back in, the batch path, and the failure
path. Hermetic.

Run: ``PYTHONPATH=src pytest tests/test_orchestrator.py``
"""
from __future__ import annotations

import json
import re

import pytest

from geniefy_core.context import ContextGatherer, ContextSnippet
from geniefy_core.gate import Gate
from geniefy_core.judge import Judge
from geniefy_core.llm import HeuristicTokenCounter, LLMClient
from geniefy_core.orchestrator import DocumentationOrchestrator
from geniefy_core.profiler import Profiler
from geniefy_core.reasoner import Reasoner
from geniefy_core.state import Answer, DraftStatus, Phase, RunConfig, SessionMode
from geniefy_core.template import default_template


# ─────────────────────────────────────────────────────────────────────────────
# Boundary fakes: profile provider + model transport
# ─────────────────────────────────────────────────────────────────────────────
TABLE = "samples.tpch.orders"


class FakeProfileProvider:
    """Implements the U3 profile_table contract. ``error`` makes it return a structured
    error (drives the failure path)."""

    def __init__(self, error: dict | None = None):
        self.error = error

    def profile_table(self, request):
        if self.error:
            return {"error": self.error}
        return {
            "profile_schema_version": "1.0",
            "table": {"full_name": TABLE, "row_count": 100, "existing_comment": None},
            "columns": [
                {"name": "o_orderkey", "ordinal": 1, "data_type": "bigint",
                 "type_class": "integer", "existing_comment": None,
                 "cardinality_ratio": 0.5, "null_fraction": 0.0},
                {"name": "o_total", "ordinal": 2, "data_type": "decimal",
                 "type_class": "decimal", "null_fraction": 0.0},
                {"name": "x", "ordinal": 3, "data_type": "string",
                 "type_class": "string", "null_fraction": 0.9},  # ambiguous + high-null
            ],
        }


def _wrap(content: str) -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


class FakeModelTransport:
    """One transport serving Reasoner (table + column drafts), Judge (subscores), and any
    summarize call — distinguished by prompt content."""

    def __init__(self, judge_score: float = 0.8):
        self.judge_score = judge_score

    def send(self, messages, *, model, max_tokens, temperature, response_format=None):
        system = messages[0]["content"]
        instr = messages[-1]["content"]
        if "independent reviewer" in system:  # Judge
            s = self.judge_score
            return _wrap(json.dumps({"subscores": {"completeness": s, "specificity": s,
                                                   "grounding": s, "template_conformance": s},
                                     "issues": []}))
        if "Summarize" in system:
            return _wrap("summary")
        if "table comment" in instr:  # Reasoner table
            return _wrap(json.dumps({"proposed_comment": "Order headers.", "rationale": "r",
                                     "evidence_refs": ["profile"], "self_confidence": 0.9}))
        # Reasoner column batch: a draft per requested name (names are quoted in the instr)
        names = re.findall(r"'([^']+)'", instr)
        return _wrap(json.dumps({"columns": [
            {"name": n, "proposed_comment": f"def {n}", "rationale": "r",
             "evidence_refs": ["profile"], "self_confidence": 0.9} for n in names]}))


# ─────────────────────────────────────────────────────────────────────────────
# Wiring
# ─────────────────────────────────────────────────────────────────────────────
class _CtxProvider:
    name = "uc_lineage"

    def __init__(self, informs):
        self._informs = informs

    def gather(self, table, columns):
        return [ContextSnippet(text="joins on o_total", source=self.name,
                               informs=list(self._informs), relevance=0.6)]


def _orchestrator(mode=SessionMode.INTERACTIVE, profile_error=None, judge_score=0.8,
                  ctx_informs=("o_total",)):
    cfg = RunConfig(model_endpoint="m", mode=mode, keep_threshold=0.75, template_id="default")
    llm = LLMClient(FakeModelTransport(judge_score), model_endpoint="m",
                    counter=HeuristicTokenCounter(), sleep=lambda s: None)
    tmpl = default_template()
    return DocumentationOrchestrator(
        profiler=Profiler(FakeProfileProvider(profile_error)),
        gatherer=ContextGatherer([_CtxProvider(ctx_informs)]),
        reasoner=Reasoner(llm, tmpl),
        judge=Judge(llm, tmpl),
        gate=Gate(cfg),
        config=cfg,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Full interactive run
# ─────────────────────────────────────────────────────────────────────────────
def test_run_interactive_pauses_on_hard_signal():
    res = _orchestrator().run(TABLE)
    # 'x' is ambiguous + high-null + no usage evidence → hard-signal trip → needs_input
    assert res.kind == "needs_input"
    assert res.state.phase == Phase.AWAITING_INPUT
    assert [q.target_name for q in res.questions] == ["x"]
    # the confident, non-tripping drafts were kept (not asked)
    assert res.state.column_draft("o_orderkey").status == DraftStatus.DRAFT
    assert res.state.column_draft("o_total").status == DraftStatus.DRAFT
    assert res.state.column_draft("x").status == DraftStatus.NEEDS_INPUT
    # drafts produced + judged (confidence = the Judge's authoritative overall, D8)
    assert res.state.table_draft.proposed_comment == "Order headers."
    assert abs(res.state.column_draft("o_orderkey").confidence - 0.8) < 1e-9
    assert res.state.column_draft("o_orderkey").judge_scores["overall"] == pytest.approx(0.8)


def test_profile_and_context_populated_in_state():
    res = _orchestrator().run(TABLE)
    assert res.state.profile["table"]["full_name"] == TABLE
    assert any(s["source"] == "uc_lineage" for s in res.state.context)


# ─────────────────────────────────────────────────────────────────────────────
# Resume
# ─────────────────────────────────────────────────────────────────────────────
def test_resume_answers_then_ready():
    orch = _orchestrator()
    res = orch.run(TABLE)
    assert res.kind == "needs_input"
    q = res.questions[0]  # the question about 'x'

    res2 = orch.resume(res.state, [Answer(question_id=q.id, text="x is the experiment tier code")])
    # the answer became evidence → 'x' now has usage → no longer trips → kept → ready
    assert res2.kind == "ready_for_review"
    assert res2.state.phase == Phase.READY_FOR_REVIEW
    assert res2.state.column_draft("x").status == DraftStatus.DRAFT
    assert res2.state.column_draft("x").proposed_comment == "def x"  # re-drafted
    assert any(s["source"] == "user_answer" for s in res2.state.context)
    assert q.answered is True


def test_resume_ignores_unknown_question_id():
    orch = _orchestrator()
    res = orch.run(TABLE)
    # an answer to a non-existent question changes nothing material; still needs_input
    res2 = orch.resume(res.state, [Answer(question_id="nope", text="...")])
    assert res2.kind == "needs_input"


# ─────────────────────────────────────────────────────────────────────────────
# Batch (hands-free) path
# ─────────────────────────────────────────────────────────────────────────────
def test_run_batch_marks_low_confidence_and_is_ready():
    res = _orchestrator(mode=SessionMode.BATCH).run(TABLE)
    assert res.kind == "ready_for_review"  # batch never pauses
    assert res.state.phase == Phase.READY_FOR_REVIEW
    assert res.state.column_draft("x").status == DraftStatus.LOW_CONFIDENCE
    assert res.questions == []


# ─────────────────────────────────────────────────────────────────────────────
# Hands-off (schema-batch) path (D51/U108)
# ─────────────────────────────────────────────────────────────────────────────
def test_run_hands_off_produces_and_persists_questions_like_interactive():
    # Unlike batch, hands-off PRODUCES the clarifying questions (so a human can answer + resume
    # later) and persists them on the returned state — the batch Job won't block on them (D51).
    res = _orchestrator(mode=SessionMode.HANDS_OFF).run(TABLE)
    assert res.kind == "needs_input"
    assert res.state.phase == Phase.AWAITING_INPUT
    assert [q.target_name for q in res.questions] == ["x"]
    assert [q.target_name for q in res.state.open_questions] == ["x"]   # persisted on state
    assert res.state.column_draft("x").status == DraftStatus.NEEDS_INPUT
    assert res.state.column_draft("o_orderkey").status == DraftStatus.DRAFT  # confident drafts kept


def test_hands_off_deferred_resume_reuses_the_existing_loop():
    # The "clarify later" path reuses resume UNCHANGED (D51/D17): answering → re-reason → ready.
    orch = _orchestrator(mode=SessionMode.HANDS_OFF)
    res = orch.run(TABLE)
    q = res.questions[0]
    res2 = orch.resume(res.state, [Answer(question_id=q.id, text="x is the experiment tier code")])
    assert res2.kind == "ready_for_review"
    assert res2.state.column_draft("x").status == DraftStatus.DRAFT


# ─────────────────────────────────────────────────────────────────────────────
# Confidence-driven needs_input (no hard signal)
# ─────────────────────────────────────────────────────────────────────────────
def test_low_judge_score_drives_needs_input():
    # every draft scores 0.5 < keep_threshold 0.75 → all need input (interactive)
    res = _orchestrator(judge_score=0.5).run(TABLE)
    assert res.kind == "needs_input"
    targets = {q.target_name for q in res.questions}
    assert {"o_orderkey", "o_total", "x"} <= targets  # table may or may not be asked


# ─────────────────────────────────────────────────────────────────────────────
# Failure path
# ─────────────────────────────────────────────────────────────────────────────
def test_profiling_error_fails_the_run():
    orch = _orchestrator(profile_error={"code": "permission_denied", "message": "need SELECT"})
    res = orch.run(TABLE)
    assert res.kind == "failed"
    assert res.state.phase == Phase.FAILED
    assert res.error.code == "permission_denied"
    # no drafts fabricated on failure
    assert res.state.table_draft is None and res.state.column_drafts == []


# ─────────────────────────────────────────────────────────────────────────────
# Regenerate (E3/D45) — re-reason named targets only, reusing profile/context (D17)
# ─────────────────────────────────────────────────────────────────────────────
def test_regenerate_named_column_only_redrafts_that_target():
    orch = _orchestrator()
    st = orch.run(TABLE).state
    st.column_draft("o_orderkey").proposed_comment = "STALE"
    st.column_draft("x").proposed_comment = "STALE"
    st.table_draft.proposed_comment = "STALE TABLE"

    res = orch.regenerate(st, ["x"])
    assert res.state.column_draft("x").proposed_comment == "def x"           # regenerated
    assert res.state.column_draft("o_orderkey").proposed_comment == "STALE"  # untouched
    assert res.state.table_draft.proposed_comment == "STALE TABLE"           # table untouched


def test_regenerate_table_only_keeps_columns():
    orch = _orchestrator()
    st = orch.run(TABLE).state
    st.table_draft.proposed_comment = "STALE TABLE"
    st.column_draft("o_orderkey").proposed_comment = "STALE"
    res = orch.regenerate(st, ["__table__"])
    assert res.state.table_draft.proposed_comment == "Order headers."        # table regenerated
    assert res.state.column_draft("o_orderkey").proposed_comment == "STALE"  # columns untouched


def test_regenerate_all_redrafts_table_and_every_column():
    orch = _orchestrator()
    st = orch.run(TABLE).state
    st.table_draft.proposed_comment = "STALE TABLE"
    for c in st.column_drafts:
        c.proposed_comment = "STALE"
    res = orch.regenerate(st, None)  # None ⇒ everything (table + all columns)
    assert res.state.table_draft.proposed_comment == "Order headers."
    assert all(c.proposed_comment.startswith("def ") for c in res.state.column_drafts)


def test_regenerate_reuses_profile_no_reprofile():
    # regenerate must NOT re-profile/gather (D17): the existing profile object is reused as-is.
    orch = _orchestrator()
    st = orch.run(TABLE).state
    prof = st.profile
    orch.regenerate(st, ["x"])
    assert st.profile is prof  # same object — never re-fetched from the profiler
