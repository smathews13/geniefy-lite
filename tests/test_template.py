"""Tests for the agent-core Template (U26).

Covers the U25 module against HLD §7 / U4 §3.1: loading + validation of the spec,
the conditional-field applicability rules, the rubric (weights sum to 1.0), and the
JSONB round-trip. Hermetic — no live infra. PyYAML is needed only for the YAML paths.

Run: ``PYTHONPATH=src pytest tests/test_template.py``
"""
from __future__ import annotations

import pytest

from geniefy_core.template import (
    DEFAULT_TEMPLATE_NAME,
    KNOWN_CONDITIONS,
    FieldSpec,
    Rubric,
    StyleSpec,
    Template,
    TemplateError,
    default_template,
)


# ─────────────────────────────────────────────────────────────────────────────
# Default template (bundled YAML)
# ─────────────────────────────────────────────────────────────────────────────
def test_default_template_loads():
    t = default_template()
    assert t.name == DEFAULT_TEMPLATE_NAME and t.version == 1
    # Richer, steward-facing table fields (D53/Q3/U104): the human's set + sensible additions.
    assert t.table_required == ("purpose", "business_definition", "grain", "primary_keys", "join_keys")
    assert {"technical_owner", "data_owner", "known_business_rules",
            "known_quality_issues", "freshness_sla"}.issubset(set(t.table_recommended))
    assert "definition" in t.column_required
    assert t.table_style.max_words == 900  # richer steward-facing comment (D53/Q3/U104; was 500)
    assert t.column_style.max_words == 150  # (D43/E1/U81; was 40)


def test_default_rubric_weights_sum_to_one():
    t = default_template()
    assert set(t.rubric.names) == {"completeness", "specificity", "grounding", "template_conformance"}
    assert abs(sum(t.rubric.weights().values()) - 1.0) < 1e-9


# ─────────────────────────────────────────────────────────────────────────────
# Applicability rules
# ─────────────────────────────────────────────────────────────────────────────
def _all_false():
    return {k: False for k in KNOWN_CONDITIONS}


def test_plain_column_only_definition():
    t = default_template()
    assert [f.name for f in t.applicable_column_fields(_all_false())] == ["definition"]


def test_nullable_numeric_fk_applicability():
    t = default_template()
    sig = _all_false() | {"numeric_measure": True, "nullable": True, "fk": True}
    names = [f.name for f in t.applicable_column_fields(sig)]
    assert names[0] == "definition"
    assert {"units", "null_meaning", "fk_reference"} <= set(names)
    assert "allowed_values" not in names and "sensitivity" not in names


def test_enum_and_pii_applicability():
    t = default_template()
    sig = _all_false() | {"enum": True, "pii": True}
    names = [f.name for f in t.applicable_column_fields(sig)]
    assert {"allowed_values", "sensitivity"} <= set(names)
    assert "units" not in names


def test_always_condition_field_always_applies():
    spec = _minimal_spec()
    spec["column_comment"]["conditional"] = {"note": {"when": "always", "rule": "x"}}
    t = Template.from_dict(spec)
    assert "note" in [f.name for f in t.applicable_column_fields(_all_false())]


def test_required_column_fields_marked_required():
    t = default_template()
    reqs = [f for f in t.applicable_column_fields(_all_false()) if f.requirement == "required"]
    assert [f.name for f in reqs] == ["definition"]


# ─────────────────────────────────────────────────────────────────────────────
# Round-trip
# ─────────────────────────────────────────────────────────────────────────────
def test_to_dict_from_dict_round_trip_stable():
    t = default_template()
    d = t.to_dict()
    t2 = Template.from_dict(d)
    assert t2.to_dict() == d
    assert t2.rubric.weights() == t.rubric.weights()
    assert t2.column_conditional == t.column_conditional


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────
def _minimal_spec() -> dict:
    return {
        "name": "t",
        "version": 1,
        "table_comment": {"required": ["purpose"], "recommended": [], "style": {"max_words": 100}},
        "column_comment": {"required": ["definition"], "conditional": {}, "style": {"max_words": 30}},
        "rubric": {"dimensions": {"a": {"weight": 0.5}, "b": {"weight": 0.5}}},
    }


def test_minimal_spec_valid():
    assert Template.from_dict(_minimal_spec()).name == "t"


@pytest.mark.parametrize("mutate,msg", [
    (lambda s: s.pop("name"), "name"),
    (lambda s: s.__setitem__("version", 0), "version"),
    (lambda s: s["table_comment"].__setitem__("required", []), "table_comment.required"),
    (lambda s: s["column_comment"].__setitem__("required", ["foo"]), "definition"),
    (lambda s: s["column_comment"]["conditional"].__setitem__("x", {"when": "bogus"}), "unknown 'when'"),
    (lambda s: s["rubric"]["dimensions"]["a"].__setitem__("weight", 0.9), "sum to 1.0"),
    (lambda s: s["rubric"]["dimensions"]["a"].__setitem__("weight", "hi"), "numeric weight"),
    (lambda s: s["table_comment"]["style"].__setitem__("max_words", -5), "max_words"),
])
def test_invalid_specs_rejected(mutate, msg):
    spec = _minimal_spec()
    mutate(spec)
    with pytest.raises(TemplateError) as ei:
        Template.from_dict(spec)
    assert msg in str(ei.value)


def test_non_mapping_spec_rejected():
    with pytest.raises(TemplateError):
        Template.from_dict(["not", "a", "mapping"])  # type: ignore[arg-type]


def test_empty_rubric_rejected():
    spec = _minimal_spec()
    spec["rubric"]["dimensions"] = {}
    with pytest.raises(TemplateError):
        Template.from_dict(spec)


# ─────────────────────────────────────────────────────────────────────────────
# YAML authoring path (PyYAML)
# ─────────────────────────────────────────────────────────────────────────────
def test_from_yaml_matches_default():
    yaml = pytest.importorskip("yaml")
    from pathlib import Path
    import geniefy_core.template as m
    path = Path(m.__file__).parent / "templates" / "default.yaml"
    t = Template.from_yaml(path.read_text())
    assert t.to_dict() == default_template().to_dict()


def test_from_yaml_non_mapping_rejected():
    pytest.importorskip("yaml")
    with pytest.raises(TemplateError):
        Template.from_yaml("- just\n- a\n- list\n")


# ─────────────────────────────────────────────────────────────────────────────
# Sub-objects
# ─────────────────────────────────────────────────────────────────────────────
def test_stylespec_defaults_and_forbid():
    s = StyleSpec.from_dict({"forbid": ["x", "y"]})
    assert s.max_words is None and s.forbid == ("x", "y")


def test_rubric_from_dict_direct():
    r = Rubric.from_dict({"dimensions": {"a": {"weight": 0.25}, "b": {"weight": 0.75}}})
    assert r.weights() == {"a": 0.25, "b": 0.75}
