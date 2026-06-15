"""Tests for the in-app ProfileProvider (U51, D25).

Covers the U3 §4.2 profile assembly + §5 PII sanitization (D4), driven by a fake
warehouse that returns canned rows keyed on the generated SQL. Includes an end-to-end
check that the provider satisfies ``geniefy_core.profiler.ProfileProvider`` — the real
core ``Profiler`` consumes it unchanged.

Run: ``PYTHONPATH=src pytest tests/test_profiling.py``
"""
from __future__ import annotations

import re

import pytest

from geniefy_app.profiling import InAppProfileProvider, _type_class
from geniefy_core.profiler import Profiler

COLS = [
    {"column_name": "o_orderkey", "ordinal_position": 1, "data_type": "bigint",
     "is_nullable": "NO", "comment": None},
    {"column_name": "o_orderstatus", "ordinal_position": 2, "data_type": "string",
     "is_nullable": "NO", "comment": "order status"},
    {"column_name": "ssn", "ordinal_position": 3, "data_type": "string",
     "is_nullable": "YES", "comment": None},
]
AGG = {
    "o_orderkey": {"total": 100, "non_null": 100, "ndv": 100, "min_v": 1, "max_v": 100},
    "o_orderstatus": {"total": 100, "non_null": 100, "ndv": 3},
    "ssn": {"total": 100, "non_null": 90, "ndv": 5},
}
TOPK = {
    "o_orderstatus": [{"value": "F", "cnt": 60}, {"value": "O", "cnt": 30}, {"value": "P", "cnt": 10}],
    "ssn": [{"value": "123-45-6789", "cnt": 2}, {"value": "987-65-4321", "cnt": 1}],
}


class FakeWarehouse:
    """Dispatches canned rows by inspecting the generated SQL."""

    def __init__(self):
        self.queries: list[str] = []

    def __call__(self, sql: str):
        self.queries.append(sql)
        if "information_schema.columns" in sql:
            return list(COLS)
        if "information_schema.tables" in sql:
            return [{"comment": "TPC-H orders"}]
        if "COUNT(*) AS n FROM" in sql:
            return [{"n": 100}]
        if "approx_count_distinct" in sql:
            col = re.search(r"COUNT\(`([^`]+)`\)", sql).group(1)  # the COUNT(`col`) AS non_null
            return [AGG[col]]
        if "GROUP BY" in sql and "LIMIT" in sql:
            col = re.search(r"SELECT `([^`]+)` AS value", sql).group(1)
            return [dict(t) for t in TOPK.get(col, [])]
        raise AssertionError(f"unexpected SQL: {sql}")


def _profile(**kw):
    return InAppProfileProvider(FakeWarehouse()).profile_table({"table": "samples.tpch.orders", **kw})


# ─────────────────────────────────────────────────────────────────────────────
# Type classification
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("dt,cls", [
    ("bigint", "integer"), ("int", "integer"), ("double", "decimal"), ("decimal(10,2)", "decimal"),
    ("string", "string"), ("varchar(20)", "string"), ("boolean", "boolean"),
    ("timestamp", "temporal"), ("date", "temporal"), ("array<int>", "complex"),
    ("struct<a:int>", "complex"), ("binary", "binary"),
])
def test_type_class(dt, cls):
    assert _type_class(dt) == cls


# ─────────────────────────────────────────────────────────────────────────────
# Profile assembly (U3 §4.2)
# ─────────────────────────────────────────────────────────────────────────────
def test_table_block():
    p = _profile()
    t = p["table"]
    assert p["profile_schema_version"] == "1.0"
    assert t["full_name"] == "samples.tpch.orders" and t["row_count"] == 100
    assert t["column_count"] == 3 and t["existing_comment"] == "TPC-H orders"
    assert t["sampled"] is False and t["warnings"] == []


def test_integer_key_column_stats():
    col = next(c for c in _profile()["columns"] if c["name"] == "o_orderkey")
    assert col["type_class"] == "integer" and col["nullable"] is False
    assert col["null_fraction"] == 0.0
    assert col["distinct_count"] == 100 and col["cardinality_ratio"] == 1.0  # keylike
    assert col["is_enum_candidate"] is False                                 # 100 > 50
    assert col["min"] == 1 and col["max"] == 100
    assert "top_k" not in col


def test_enum_column_gets_top_k():
    col = next(c for c in _profile()["columns"] if c["name"] == "o_orderstatus")
    assert col["is_enum_candidate"] is True and col["existing_comment"] == "order status"
    assert [t["value"] for t in col["top_k"]] == ["F", "O", "P"]
    assert col["pii"]["detected"] is False


def test_null_fraction_computed():
    col = next(c for c in _profile()["columns"] if c["name"] == "ssn")
    assert col["null_fraction"] == round(1 - 90 / 100, 6)  # 0.1


# ─────────────────────────────────────────────────────────────────────────────
# PII sanitization (§5 / D4)
# ─────────────────────────────────────────────────────────────────────────────
def test_pii_detected_by_name_and_value_and_masked():
    col = next(c for c in _profile()["columns"] if c["name"] == "ssn")
    assert col["pii"]["detected"] is True
    assert "ssn" in col["pii"]["classes"] and col["pii"]["action"] == "masked"
    # raw SSNs never returned — top_k values are masked
    vals = [t["value"] for t in col["top_k"]]
    assert all("•" in v for v in vals) and not any(re.match(r"^\d{3}-\d{2}-\d{4}$", v) for v in vals)


def test_non_pii_values_not_masked():
    col = next(c for c in _profile()["columns"] if c["name"] == "o_orderstatus")
    assert [t["value"] for t in col["top_k"]] == ["F", "O", "P"]  # untouched


# ─────────────────────────────────────────────────────────────────────────────
# Sampling + column subset
# ─────────────────────────────────────────────────────────────────────────────
def test_sampling_sets_flags_and_tablesample():
    wh = FakeWarehouse()
    p = InAppProfileProvider(wh).profile_table(
        {"table": "samples.tpch.orders", "sample": {"mode": "rows", "value": 1000}})
    assert p["table"]["sampled"] is True and p["table"]["row_count_is_estimate"] is True
    assert any("sampled" in w for w in p["table"]["warnings"])
    assert any("TABLESAMPLE (1000 ROWS)" in q for q in wh.queries)


def test_column_subset_filters():
    p = _profile(columns=["o_orderstatus"])
    assert [c["name"] for c in p["columns"]] == ["o_orderstatus"]


def test_empty_table_emits_warning():
    # U3 §9: a 0-row table is still profiled but flagged — distributions aren't meaningful
    # (U51 audit MED — the empty-table warning was missing).
    def wh(sql):
        if "information_schema.columns" in sql:
            return list(COLS)
        if "information_schema.tables" in sql:
            return [{"comment": None}]
        if "COUNT(*) AS n FROM" in sql:
            return [{"n": 0}]
        if "approx_count_distinct" in sql:
            return [{"total": 0, "non_null": 0, "ndv": 0}]
        if "GROUP BY" in sql and "LIMIT" in sql:
            return []
        raise AssertionError(f"unexpected SQL: {sql}")

    p = InAppProfileProvider(wh).profile_table({"table": "samples.tpch.orders"})
    assert p["table"]["row_count"] == 0
    assert any("empty" in w for w in p["table"]["warnings"])


def test_complex_type_skips_distributions():
    cols = [{"column_name": "payload", "ordinal_position": 1, "data_type": "struct<a:int>",
             "is_nullable": "YES", "comment": None}]

    def wh(sql):
        if "information_schema.columns" in sql:
            return cols
        if "information_schema.tables" in sql:
            return [{"comment": None}]
        if "COUNT(*) AS n FROM" in sql:
            return [{"n": 5}]
        raise AssertionError(f"complex column should issue no aggregate query: {sql}")

    col = InAppProfileProvider(wh).profile_table({"table": "c.s.t"})["columns"][0]
    assert col["type_class"] == "complex" and "null_fraction" not in col
    assert col["pii"]["detected"] is False


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end: the provider satisfies the core Profiler (U3 contract)
# ─────────────────────────────────────────────────────────────────────────────
def test_provider_drives_the_core_profiler():
    result = Profiler(InAppProfileProvider(FakeWarehouse())).profile("samples.tpch.orders")
    prof = result.profile
    assert prof.profile_schema_version == "1.0"
    assert {c.name for c in prof.columns} == {"o_orderkey", "o_orderstatus", "ssn"}
    assert prof.column("ssn").pii["detected"] is True
    assert prof.column("o_orderkey").cardinality_ratio == 1.0
