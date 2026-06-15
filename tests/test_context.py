"""Tests for the agent-core context layer (U40).

Covers U39 against U4 §3.3 / D5 / D34: the built-in lineage + query-history providers
(SQL shape + parsing), the MCP provider (connect → list∩allowlist → invoke → normalize),
and the gatherer's merge/rank/budget-trim + degrade-not-fail behavior. Hermetic — the SQL
runner and MCP session are injected fakes.

Run: ``PYTHONPATH=src pytest tests/test_context.py``
"""
from __future__ import annotations

import pytest

from geniefy_core.context import (
    ContextGatherer,
    ContextSnippet,
    LibraryProvider,
    LineageProvider,
    McpContextProvider,
    QueryHistoryProvider,
    _normalize_mcp_result,
)
from geniefy_core.llm import HeuristicTokenCounter
from geniefy_core.state import Phase, RunConfig, SessionState


# ─────────────────────────────────────────────────────────────────────────────
# ContextSnippet
# ─────────────────────────────────────────────────────────────────────────────
def test_snippet_round_trip():
    s = ContextSnippet(text="t", source="uc_lineage", informs=["c1"], relevance=0.8)
    assert ContextSnippet.from_dict(s.to_dict()).to_dict() == s.to_dict()


# ─────────────────────────────────────────────────────────────────────────────
# LineageProvider
# ─────────────────────────────────────────────────────────────────────────────
def _lineage_runner(sql: str):
    if "column_lineage" in sql:
        return [{"source_table_full_name": "c.s.dim", "source_column_name": "id",
                 "target_column_name": "o_custkey"}]
    if "SELECT DISTINCT source_table_full_name" in sql:  # upstream
        return [{"source_table_full_name": "c.s.raw_orders"}]
    if "SELECT DISTINCT target_table_full_name" in sql:  # downstream
        return [{"target_table_full_name": "c.s.mart"}]
    return []


def test_lineage_queries_target_system_tables_and_escape():
    q = LineageProvider.queries("samples.tpch.orders")
    assert "system.access.table_lineage" in q["upstream"]
    assert "system.access.column_lineage" in q["column_lineage"]
    assert "'samples.tpch.orders'" in q["upstream"]


def test_lineage_produces_up_down_and_column_snippets():
    snips = LineageProvider(_lineage_runner).gather("samples.tpch.orders", ["o_custkey"])
    texts = " ".join(s.text for s in snips)
    assert "Upstream" in texts and "c.s.raw_orders" in texts
    assert "Downstream" in texts and "c.s.mart" in texts
    colsnip = next(s for s in snips if s.informs == ["o_custkey"])
    assert "c.s.dim" in colsnip.text and colsnip.relevance >= 0.8


def test_lineage_empty_when_no_rows():
    assert LineageProvider(lambda sql: []).gather("c.s.t", []) == []


# ─────────────────────────────────────────────────────────────────────────────
# QueryHistoryProvider
# ─────────────────────────────────────────────────────────────────────────────
def test_query_history_parses_join_and_group():
    stmt = ("SELECT o.o_orderstatus, count(*) FROM samples.tpch.orders o "
            "JOIN samples.tpch.customer c ON o.o_custkey = c.c_custkey "
            "WHERE o.o_total > 0 GROUP BY o.o_orderstatus")
    p = QueryHistoryProvider(lambda sql: [{"statement_text": stmt}])
    texts = " ".join(s.text for s in p.gather("samples.tpch.orders", []))
    assert "recent queries" in texts
    assert "o.o_custkey = c.c_custkey" in texts          # join key parsed (alias-tolerant)
    assert "o.o_orderstatus" in texts and "GROUP BY" in texts  # grain hint


def test_query_history_query_shape():
    p = QueryHistoryProvider(lambda sql: [], limit=50)
    q = p.query("samples.tpch.orders")
    assert "system.query.history" in q and "LIMIT 50" in q and "%samples.tpch.orders%" in q


def test_query_history_empty():
    assert QueryHistoryProvider(lambda sql: []).gather("c.s.t", []) == []


# ─────────────────────────────────────────────────────────────────────────────
# MCP provider (D34 contract)
# ─────────────────────────────────────────────────────────────────────────────
class FakeSession:
    def __init__(self, tools, result):
        self._tools = tools
        self._result = result
        self.calls = []

    def list_tools(self):
        return self._tools

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self._result


def test_mcp_respects_allowlist_and_builds_query():
    sess = FakeSession(["search", "admin_delete"], {"results": ["snippet A", "snippet B"]})
    cfg = {"name": "glean", "tool_allowlist": ["search"],
           "query_template": "table {full_name} cols {column_terms}"}
    snips = McpContextProvider(cfg, lambda c: sess).gather("samples.tpch.orders", ["o_custkey"])
    assert [s.text for s in snips] == ["snippet A", "snippet B"]
    assert all(s.source == "glean" for s in snips)
    # only the allowlisted tool was called; query built from the template
    assert len(sess.calls) == 1 and sess.calls[0][0] == "search"
    assert "table samples.tpch.orders cols o_custkey" in sess.calls[0][1]["query"]


def test_mcp_caps_at_max_snippets():
    sess = FakeSession(["search"], {"results": [f"s{i}" for i in range(50)]})
    cfg = {"name": "glean", "tool_allowlist": ["search"], "max_snippets": 3}
    snips = McpContextProvider(cfg, lambda c: sess).gather("c.s.t", [])
    assert len(snips) == 3


def test_mcp_no_allowed_tools_returns_empty():
    sess = FakeSession(["other"], {"results": ["x"]})
    cfg = {"name": "glean", "tool_allowlist": ["search"]}
    assert McpContextProvider(cfg, lambda c: sess).gather("c.s.t", []) == []


@pytest.mark.parametrize("result,expected", [
    ("hello", ["hello"]),
    (["a", "b"], ["a", "b"]),
    ({"results": ["x", "y"]}, ["x", "y"]),
    ({"text": "single"}, ["single"]),
    ([{"text": "t1"}, {"snippet": "t2"}], ["t1", "t2"]),
    (None, []),
])
def test_normalize_mcp_result_shapes(result, expected):
    assert _normalize_mcp_result(result) == expected


# ─────────────────────────────────────────────────────────────────────────────
# ContextGatherer — merge / rank / budget / degrade
# ─────────────────────────────────────────────────────────────────────────────
class FakeProvider:
    def __init__(self, name, snippets=None, raises=False):
        self.name = name
        self._snippets = snippets or []
        self._raises = raises

    def gather(self, table, columns):
        if self._raises:
            raise RuntimeError("unreachable")
        return list(self._snippets)


def test_gatherer_ranks_by_relevance():
    p = FakeProvider("p", [ContextSnippet("low", "p", relevance=0.2),
                           ContextSnippet("high", "p", relevance=0.9),
                           ContextSnippet("mid", "p", relevance=0.5)])
    res = ContextGatherer([p], context_token_budget=10_000).gather("c.s.t", [])
    assert [s.text for s in res.snippets] == ["high", "mid", "low"]


def test_gatherer_degrades_on_provider_error():
    good = FakeProvider("good", [ContextSnippet("ok", "good", relevance=0.5)])
    bad = FakeProvider("flaky", raises=True)
    res = ContextGatherer([good, bad]).gather("c.s.t", [])
    assert [s.text for s in res.snippets] == ["ok"]  # good still ran
    assert any("flaky" in w and "unavailable" in w for w in res.warnings)


def test_gatherer_trims_to_budget_but_keeps_top():
    counter = HeuristicTokenCounter(chars_per_token=1)  # 1 char = 1 token
    snips = [ContextSnippet("x" * 8, "p", relevance=0.9),
             ContextSnippet("y" * 8, "p", relevance=0.5),
             ContextSnippet("z" * 8, "p", relevance=0.1)]
    res = ContextGatherer([FakeProvider("p", snips)], context_token_budget=10, counter=counter).gather("c.s.t", [])
    # budget 10: top (8) fits; adding the second (→16) exceeds → stop
    assert [s.text for s in res.snippets] == ["x" * 8]


def test_gatherer_keeps_at_least_top_even_if_over_budget():
    counter = HeuristicTokenCounter(chars_per_token=1)
    big = ContextSnippet("x" * 100, "p", relevance=0.9)
    res = ContextGatherer([FakeProvider("p", [big])], context_token_budget=10, counter=counter).gather("c.s.t", [])
    assert len(res.snippets) == 1  # never drop everything


def test_gather_into_sets_state_context_and_phase():
    st = SessionState(target="samples.tpch.orders", config=RunConfig(model_endpoint="m"),
                      profile={"columns": [{"name": "o_custkey"}]})
    p = FakeProvider("p", [ContextSnippet("hello", "p", relevance=0.7, informs=["o_custkey"])])
    res = ContextGatherer([p]).gather_into(st)
    assert st.phase == Phase.GATHERING_CONTEXT
    assert st.context == [{"text": "hello", "source": "p", "informs": ["o_custkey"], "relevance": 0.7}]
    assert res.warnings == []


# ─────────────────────────────────────────────────────────────────────────────
# LibraryProvider (D52 §A4 / U104) — suggestion-only reuse-on-generation
# ─────────────────────────────────────────────────────────────────────────────
def _lib_lookup(scope, keys):
    if scope == "table":
        return [{"match_key": "samples.tpch.orders", "canonical_comment": "Order headers.",
                 "tags": ["fact"], "usage_count": 4}]
    rows = {"o_custkey": {"match_key": "o_custkey", "canonical_comment": "FK to customer.",
                          "tags": ["fk", "identifier"], "usage_count": 9}}
    return [rows[k] for k in keys if k in rows]


def test_library_provider_emits_suggestion_only_snippets():
    snips = LibraryProvider(_lib_lookup).gather("samples.tpch.orders", ["o_custkey", "o_unknown"])
    assert len(snips) == 2  # one table match + one matched column (unknown column → no match)
    txt = " ".join(s.text for s in snips)
    # suggestion-only framing — never an instruction to copy verbatim
    assert "REUSE this wording if it fits" in txt and "do not copy" in txt
    assert "Order headers." in txt and "FK to customer." in txt
    col = next(s for s in snips if s.informs == ["o_custkey"])
    assert col.source == "comment_library" and "used 9x" in col.text


def test_library_provider_degrades_via_gatherer_on_lookup_error():
    def boom(scope, keys):
        raise RuntimeError("lakebase down")
    res = ContextGatherer([LibraryProvider(boom)]).gather("c.s.t", ["a"])
    assert res.snippets == [] and any("comment_library" in w for w in res.warnings)


def test_library_provider_no_matches_returns_empty():
    assert LibraryProvider(lambda scope, keys: []).gather("c.s.t", ["a"]) == []
