"""Tests for the agent-core Judge (U38).

Covers U37 against U4 §3.5 / D8: per-draft scoring, the template-rubric-weighted
``overall`` (computed by us, not trusted from the model), the authoritative
confidence override, subscore clamping, issue capture, ERROR-draft skipping, and
per-draft error isolation (U4 §8). Hermetic — the model is a fake transport.

Run: ``PYTHONPATH=src pytest tests/test_judge.py``
"""
from __future__ import annotations

import json

import pytest

from geniefy_core.judge import Judge
from geniefy_core.llm import HeuristicTokenCounter, LLMClient
from geniefy_core.state import (
    ColumnDraft,
    DraftStatus,
    Phase,
    RunConfig,
    SessionState,
    TableDraft,
)
from geniefy_core.template import default_template


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


def _judge(responder, **kw):
    llm = LLMClient(FakeTransport(responder), model_endpoint="m",
                    counter=HeuristicTokenCounter(), sleep=lambda s: None)
    return Judge(llm, default_template(), **kw)


def _subscores(completeness=0.8, specificity=0.8, grounding=0.8, template_conformance=0.8, issues=None):
    return json.dumps({
        "subscores": {"completeness": completeness, "specificity": specificity,
                      "grounding": grounding, "template_conformance": template_conformance},
        "issues": issues or [],
    })


def _state():
    return SessionState(
        target="samples.tpch.orders", config=RunConfig(model_endpoint="m"),
        profile={"table": {"full_name": "samples.tpch.orders", "row_count": 5},
                 "columns": [{"name": "o_orderkey", "ordinal": 1, "data_type": "bigint"},
                             {"name": "o_custkey", "ordinal": 2, "data_type": "bigint"}]},
        context=[{"source": "uc_lineage", "text": "joins customer"}],
        table_draft=TableDraft(proposed_comment="Order headers.", rationale="r",
                               evidence_refs=["profile"], confidence=0.99),  # provisional self_confidence
        column_drafts=[
            ColumnDraft(column_name="o_orderkey", proposed_comment="PK", confidence=0.99),
            ColumnDraft(column_name="o_custkey", proposed_comment="FK", confidence=0.99),
        ],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Scoring + the weighted overall
# ─────────────────────────────────────────────────────────────────────────────
def test_score_sets_judge_scores_and_phase():
    st = _state()
    _judge(lambda m: _subscores(issues=["thin grounding"])).score(st)
    assert st.phase == Phase.JUDGING
    js = st.table_draft.judge_scores
    assert set(js["subscores"]) == {"completeness", "specificity", "grounding", "template_conformance"}
    assert js["issues"] == ["thin grounding"]
    assert abs(js["overall"] - 0.8) < 1e-9  # all 0.8 → weighted overall 0.8


def test_overall_is_template_weighted_not_mean():
    # weights {completeness 0.30, specificity 0.30, grounding 0.25, template_conformance 0.15}.
    # The prior inputs {1,1,0,1} gave 0.75 for BOTH weighted AND mean, so they did not actually
    # distinguish the two (U37 audit MED). Pick subscores where weighted ≠ mean:
    #   weighted = 1*.30 + 0*.30 + 0*.25 + 0*.15 = 0.30 ;  mean = (1+0+0+0)/4 = 0.25
    st = _state()
    _judge(lambda m: _subscores(completeness=1, specificity=0, grounding=0, template_conformance=0)).score(st)
    overall = st.table_draft.judge_scores["overall"]
    assert abs(overall - 0.30) < 1e-9   # template-weighted
    assert abs(overall - 0.25) > 1e-3   # NOT the plain mean


def test_confidence_overridden_by_overall():
    st = _state()
    assert st.column_drafts[0].confidence == 0.99  # provisional
    _judge(lambda m: _subscores(0.6, 0.6, 0.6, 0.6)).score(st)
    assert abs(st.column_drafts[0].confidence - 0.6) < 1e-9  # now the authoritative overall


def test_subscores_clamped_and_missing_defaulted():
    st = _state()
    # out-of-range + a missing dimension (template_conformance absent → 0.0)
    resp = json.dumps({"subscores": {"completeness": 1.5, "specificity": -0.2, "grounding": 0.5},
                       "issues": []})
    _judge(lambda m: resp).score(st)
    ss = st.table_draft.judge_scores["subscores"]
    assert ss["completeness"] == 1.0 and ss["specificity"] == 0.0
    assert ss["grounding"] == 0.5 and ss["template_conformance"] == 0.0
    # overall = 1*.30 + 0*.30 + .5*.25 + 0*.15 = 0.425
    assert abs(st.table_draft.judge_scores["overall"] - 0.425) < 1e-9


def test_all_drafts_scored():
    st = _state()
    _judge(lambda m: _subscores()).score(st)
    assert st.column_draft("o_orderkey").judge_scores is not None
    assert st.column_draft("o_custkey").judge_scores is not None


# ─────────────────────────────────────────────────────────────────────────────
# Skipping + isolation
# ─────────────────────────────────────────────────────────────────────────────
def test_error_drafts_skipped():
    st = _state()
    st.column_drafts[0].status = DraftStatus.ERROR  # Reasoner failed this one
    calls = []
    def responder(m):
        calls.append(1)
        return _subscores()
    _judge(responder).score(st)
    assert st.column_draft("o_orderkey").judge_scores is None  # skipped
    assert st.column_draft("o_custkey").judge_scores is not None
    # table + 1 column scored = 2 calls (the error column skipped)
    assert len(calls) == 2


def test_per_draft_error_isolation():
    def responder(messages):
        user = messages[-1]["content"]
        if '"proposed_comment": "FK"' in user:  # the o_custkey draft's judge call fails
            raise RuntimeError("judge model down")
        return _subscores()
    st = _state()
    _judge(responder).score(st)
    assert st.table_draft.judge_scores is not None
    assert st.column_draft("o_orderkey").judge_scores is not None
    bad = st.column_draft("o_custkey")
    assert bad.status == DraftStatus.ERROR and bad.judge_scores is None
    assert "judge failed" in (bad.rationale or "")


# ─────────────────────────────────────────────────────────────────────────────
# Prompt + budget
# ─────────────────────────────────────────────────────────────────────────────
def test_system_prompt_demands_grounding():
    st = _state()
    j = _judge(lambda m: _subscores())
    j.score(st)
    systems = [m[0]["content"] for m in j.llm._transport.calls]
    assert all("grounding" in s for s in systems)
    assert any("independent reviewer" in s for s in systems)


def test_large_evidence_trimmed_under_budget_but_still_scores():
    st = _state()
    st.context = [{"source": "uc_lineage", "text": "x" * 8000}]
    _judge(lambda m: _subscores(), max_input_tokens=80).score(st)
    # still produces a score despite the oversized evidence
    assert st.table_draft.judge_scores is not None
