"""Tests for the agent-core Profiler (U18).

Covers the U14 module as polished by U19, against the U3 §4 contract and the
U4 §3.2 responsibilities: request shaping, wide-table batching + merge, schema_meta
introspection, structured errors (U3 §9), the schema-version gate, and additive
tolerance. No live infra — the provider and introspector are injected fakes.

Run: ``PYTHONPATH=src pytest tests/test_profiler.py``
"""
from __future__ import annotations

import pytest

from geniefy_core.profiler import (
    DEFAULT_PROFILE_BATCH_SIZE,
    PROFILE_SCHEMA_VERSION,
    ColumnProfile,
    Profiler,
    ProfileError,
    ProfileOptions,
    ProfileRequest,
    SampleSpec,
    SchemaMeta,
    TableProfile,
    TableRef,
    information_schema_queries,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────────────
class RecordingProvider:
    """Returns a §4.2 response with one column per requested column (or 120 if the
    request omits `columns`). Records every request it received."""

    def __init__(self, version: str = "1.0", total: int = 120):
        self.version = version
        self.total = total
        self.requests: list[dict] = []

    def profile_table(self, request):
        self.requests.append(request)
        cols = request.get("columns") or [f"c{i}" for i in range(self.total)]
        return {
            "profile_schema_version": self.version,
            "table": {
                "full_name": request["table"],
                "row_count": 42,
                "column_count": self.total,
                "partition_columns": ["dt"],
                "sampled": True,
                "warnings": ["sampled: row_count is an estimate"],
            },
            "columns": [
                {"name": n, "ordinal": i + 1, "data_type": "string", "type_class": "string",
                 "pii": {"detected": False}}
                for i, n in enumerate(cols)
            ],
        }


class FakeIntrospector:
    def __init__(self, n: int = 120):
        self.n = n

    def introspect(self, table: TableRef):
        return {
            "columns": [
                {"name": f"c{i}", "ordinal": i + 1, "data_type": "string", "nullable": True}
                for i in range(self.n)
            ],
            "primary_key": ["c0"],
            "foreign_keys": [
                {"constraint_name": "fk1", "columns": ["c1"],
                 "referenced_table": "c.s.dim", "referenced_columns": ["id"]}
            ],
        }


# ─────────────────────────────────────────────────────────────────────────────
# TableRef
# ─────────────────────────────────────────────────────────────────────────────
def test_tableref_parse_valid():
    ref = TableRef.parse("main.sales.orders")
    assert (ref.catalog, ref.schema, ref.table) == ("main", "sales", "orders")
    assert ref.full_name == "main.sales.orders"
    assert TableRef.parse(ref) is ref  # idempotent on a TableRef


@pytest.mark.parametrize("bad", ["", "a", "a.b", "a.b.c.d", "a..c", ".b.c"])
def test_tableref_parse_rejects_non_fqn(bad):
    with pytest.raises(ValueError):
        TableRef.parse(bad)


# ─────────────────────────────────────────────────────────────────────────────
# Request models (U3 §4.1)
# ─────────────────────────────────────────────────────────────────────────────
def test_request_to_dict_shape():
    req = ProfileRequest("c.s.t", SampleSpec("rows", 1000), ["a", "b"], ProfileOptions(top_k=5)).to_dict()
    assert req["table"] == "c.s.t"
    assert req["sample"] == {"mode": "rows", "value": 1000}
    assert req["columns"] == ["a", "b"]
    assert req["options"]["top_k"] == 5


def test_request_omits_columns_when_none():
    assert "columns" not in ProfileRequest("c.s.t").to_dict()


def test_samplespec_auto_has_no_value():
    assert SampleSpec("auto").to_dict() == {"mode": "auto"}


@pytest.mark.parametrize("mode", ["rows", "percent"])
def test_samplespec_requires_value(mode):
    with pytest.raises(ValueError):
        SampleSpec(mode)


def test_samplespec_rejects_bad_mode_and_percent_range():
    with pytest.raises(ValueError):
        SampleSpec("nonsense")
    with pytest.raises(ValueError):
        SampleSpec("percent", 150)


# ─────────────────────────────────────────────────────────────────────────────
# Batching + merge (U3 §7)
# ─────────────────────────────────────────────────────────────────────────────
def test_batches_wide_table_and_merges():
    prov = RecordingProvider(total=120)
    res = Profiler(prov, FakeIntrospector(120), profile_batch_size=50).profile("a.b.c")
    # 120 columns / 50 → 3 provider calls
    assert len(prov.requests) == 3
    assert [len(r["columns"]) for r in prov.requests] == [50, 50, 20]
    # merged to 120 unique columns, in order
    assert [c.name for c in res.profile.columns] == [f"c{i}" for i in range(120)]
    # table block + warnings preserved (deduped)
    assert res.profile.table.row_count == 42
    assert res.profile.table.warnings == ["sampled: row_count is an estimate"]


def test_single_call_when_no_introspector_and_no_columns():
    prov = RecordingProvider(total=10)
    res = Profiler(prov).profile("c.s.t")
    assert len(prov.requests) == 1
    assert prov.requests[0].get("columns") is None
    assert len(res.profile.columns) == 10


def test_explicit_columns_under_batch_size_single_call():
    prov = RecordingProvider()
    Profiler(prov, profile_batch_size=50).profile("c.s.t", columns=["x", "y"])
    assert len(prov.requests) == 1
    assert prov.requests[0]["columns"] == ["x", "y"]


def test_merge_dedups_duplicate_columns_across_batches():
    class DupProvider:
        def profile_table(self, request):
            return {"profile_schema_version": "1.0",
                    "table": {"full_name": request["table"]},
                    "columns": [{"name": "dup", "ordinal": 1}]}
    res = Profiler(DupProvider(), FakeIntrospector(80), profile_batch_size=40).profile("a.b.c")
    # two batches both return "dup" → deduped to one
    assert [c.name for c in res.profile.columns] == ["dup"]


# ─────────────────────────────────────────────────────────────────────────────
# schema_meta (U4 §3.2)
# ─────────────────────────────────────────────────────────────────────────────
def test_schema_meta_introspection_and_partition_mirroring():
    res = Profiler(RecordingProvider(total=3), FakeIntrospector(3)).profile("a.b.c")
    sm = res.schema_meta
    assert isinstance(sm, SchemaMeta)
    assert sm.primary_key == ["c0"]
    assert sm.foreign_keys[0].referenced_table == "c.s.dim"
    assert sm.foreign_keys[0].referenced_columns == ["id"]
    # partition columns absent in schema_meta are mirrored from the profile
    assert sm.partition_columns == ["dt"]


def test_no_introspector_yields_none_schema_meta():
    res = Profiler(RecordingProvider(total=3)).profile("a.b.c")
    assert res.schema_meta is None


def test_introspection_failure_becomes_profile_error():
    class BoomIntrospector:
        def introspect(self, table):
            raise RuntimeError("warehouse down")
    with pytest.raises(ProfileError) as ei:
        Profiler(RecordingProvider(), BoomIntrospector()).profile("a.b.c")
    assert ei.value.code == "provider_error"


# ─────────────────────────────────────────────────────────────────────────────
# Errors (U3 §9) — never a fabricated profile
# ─────────────────────────────────────────────────────────────────────────────
def test_in_band_error_mapped_to_profile_error():
    class ErrProvider:
        def profile_table(self, request):
            return {"error": {"code": "permission_denied", "message": "need SELECT"}}
    with pytest.raises(ProfileError) as ei:
        Profiler(ErrProvider()).profile("c.s.t")
    assert ei.value.code == "permission_denied"
    assert "SELECT" in ei.value.message


def test_raised_provider_exception_wrapped():
    class RaisingProvider:
        def profile_table(self, request):
            raise TimeoutError("boom")
    with pytest.raises(ProfileError) as ei:
        Profiler(RaisingProvider()).profile("c.s.t")
    assert ei.value.code == "provider_error"


def test_missing_column_name_is_structured_error():
    """U19 fix: a column with no 'name' raises ProfileError, not a raw KeyError."""
    class NoNameProvider:
        def profile_table(self, request):
            return {"profile_schema_version": "1.0", "table": {"full_name": "x"},
                    "columns": [{"ordinal": 1, "data_type": "int"}]}
    with pytest.raises(ProfileError) as ei:
        Profiler(NoNameProvider()).profile("c.s.t")
    assert ei.value.code == "provider_error"


def test_none_response_is_error():
    class NoneProvider:
        def profile_table(self, request):
            return None
    with pytest.raises(ProfileError):
        Profiler(NoneProvider()).profile("c.s.t")


# ─────────────────────────────────────────────────────────────────────────────
# Version gate (U3 §4.2) + U19 stamping
# ─────────────────────────────────────────────────────────────────────────────
def test_missing_version_rejected():
    class NoVer:
        def profile_table(self, request):
            return {"table": {"full_name": "x"}, "columns": []}
    with pytest.raises(ProfileError) as ei:
        Profiler(NoVer()).profile("c.s.t")
    assert ei.value.code == "unsupported"


def test_major_mismatch_rejected():
    with pytest.raises(ProfileError) as ei:
        Profiler(RecordingProvider(version="2.0", total=0)).profile("c.s.t")
    assert ei.value.code == "unsupported"


def test_newer_minor_accepted_additive():
    # 1.7 > core target 1.0 but same major → tolerated
    res = Profiler(RecordingProvider(version="1.7", total=2)).profile("c.s.t")
    assert res.profile.profile_schema_version == "1.7"


def test_core_target_version_constant_is_one_zero():
    assert PROFILE_SCHEMA_VERSION == "1.0"
    assert DEFAULT_PROFILE_BATCH_SIZE == 50


def test_min_version_floor_rejects_older(monkeypatch):
    """U18-audit MED: cover the `v < floor` branch with a raised minimum."""
    prov = RecordingProvider(version="1.0", total=0)
    with pytest.raises(ProfileError) as ei:
        Profiler(prov, min_schema_version="1.5").profile("c.s.t")
    assert ei.value.code == "unsupported"


def test_min_version_floor_accepts_at_or_above():
    res = Profiler(RecordingProvider(version="1.6", total=1), min_schema_version="1.5").profile("c.s.t")
    assert res.profile.profile_schema_version == "1.6"


# ─────────────────────────────────────────────────────────────────────────────
# U18-audit LOW: request-option fidelity + sample boundaries
# ─────────────────────────────────────────────────────────────────────────────
def test_profile_options_all_fields_in_request():
    req = ProfileRequest(
        "c.s.t", options=ProfileOptions(top_k=7, include_samples=False,
                                        max_cardinality_for_topk=99, reuse_analyze_stats=False)
    ).to_dict()
    assert req["options"] == {
        "top_k": 7, "include_samples": False,
        "max_cardinality_for_topk": 99, "reuse_analyze_stats": False,
    }


@pytest.mark.parametrize("pct", [0, 100])
def test_samplespec_percent_boundaries_ok(pct):
    assert SampleSpec("percent", pct).to_dict() == {"mode": "percent", "value": pct}


def test_information_schema_constraints_query_shape():
    q = information_schema_queries(TableRef.parse("main.sales.orders"))
    body = q["constraints"]
    assert "table_constraints" in body and "key_column_usage" in body
    assert "`main`.information_schema." in body
    assert "'sales'" in body and "'orders'" in body


# ─────────────────────────────────────────────────────────────────────────────
# U20: the introspect path also yields structured errors on a malformed result
# ─────────────────────────────────────────────────────────────────────────────
def test_introspector_missing_column_name_is_structured_error():
    class BadColIntrospector:
        def introspect(self, table):
            return {"columns": [{"ordinal": 1, "data_type": "int"}],  # no 'name'
                    "primary_key": [], "foreign_keys": []}
    with pytest.raises(ProfileError) as ei:
        Profiler(RecordingProvider(total=1), BadColIntrospector()).profile("a.b.c")
    assert ei.value.code == "provider_error"


# ─────────────────────────────────────────────────────────────────────────────
# Additive tolerance + parsing
# ─────────────────────────────────────────────────────────────────────────────
def test_additive_fields_preserved_in_raw():
    col = ColumnProfile.from_dict({"name": "x", "future_field": 99, "pii": {"detected": True}})
    assert col.raw["future_field"] == 99
    assert col.pii == {"detected": True}


def test_table_profile_column_lookup():
    tp = TableProfile.from_dict({
        "profile_schema_version": "1.0",
        "table": {"full_name": "a.b.c"},
        "columns": [{"name": "x"}, {"name": "y"}],
    })
    assert tp.column("y").name == "y"
    assert tp.column("missing") is None


# ─────────────────────────────────────────────────────────────────────────────
# information_schema queries (U4 §3.2)
# ─────────────────────────────────────────────────────────────────────────────
def test_information_schema_queries_qualified_and_escaped():
    q = information_schema_queries(TableRef.parse("main.sales.orders"))
    assert set(q) == {"columns", "constraints", "foreign_keys"}
    assert "`main`.information_schema.columns" in q["columns"]
    assert "'sales'" in q["columns"] and "'orders'" in q["columns"]


def test_information_schema_queries_escape_injection():
    # a single quote in the identifier must be doubled, not break out of the literal
    q = information_schema_queries(TableRef("main", "s'x", "t"))
    assert "'s''x'" in q["columns"]


def test_profiler_rejects_bad_batch_size():
    with pytest.raises(ValueError):
        Profiler(RecordingProvider(), profile_batch_size=0)
