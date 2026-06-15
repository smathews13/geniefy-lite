"""geniefy-v3 agent core — Template (U25).

The comment template is the single definition of *"what good looks like"* (HLD §7,
``LLD-agent-core.md`` §3.1). It carries the table fields, the column fields (with
applicability rules for conditional ones), the style constraints, and the **rubric**
the Judge scores against. Both the Reasoner (U4 §3.4) and the Judge (U4 §3.5) are
parameterized by it — change the template, change the standard.

Storage: the canonical spec is **JSONB** in Lakebase ``templates.spec`` (U2); YAML is
the authoring format. So ``from_dict`` (load the stored jsonb) is the primary path and
needs no third-party dependency; ``from_yaml`` / ``load`` parse YAML for authoring, with
PyYAML imported **lazily** so importing this module never requires PyYAML.

Out of scope: filling the fields (Reasoner), scoring against the rubric (Judge),
template CRUD endpoints (U5).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

DEFAULT_TEMPLATE_NAME = "default"

# Applicability conditions a *conditional* column field can key off. The Reasoner/Gate
# evaluate these against a column's profile + schema_meta + context signals (U3/U4).
KNOWN_CONDITIONS = frozenset(
    {"numeric_measure", "enum", "nullable", "fk", "computed", "pii", "always"}
)

# Tolerance for the rubric weights summing to 1.0.
_WEIGHT_TOLERANCE = 1e-6


class TemplateError(ValueError):
    """A malformed or invalid template spec."""


@dataclass(frozen=True)
class StyleSpec:
    """Style constraints for a comment (HLD §7 ``style``)."""

    max_words: int | None = None
    voice: str | None = None
    forbid: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, d: Mapping[str, Any] | None) -> "StyleSpec":
        d = d or {}
        mw = d.get("max_words")
        if mw is not None and (not isinstance(mw, int) or mw <= 0):
            raise TemplateError(f"style.max_words must be a positive int, got {mw!r}")
        return cls(
            max_words=mw,
            voice=d.get("voice"),
            forbid=tuple(d.get("forbid") or ()),
        )


@dataclass(frozen=True)
class FieldSpec:
    """One template field. ``requirement`` ∈ required | recommended | conditional.
    For conditional fields, ``when`` is a :data:`KNOWN_CONDITIONS` key and ``rule`` is
    the human-readable guidance shown to the model."""

    name: str
    requirement: str
    when: str | None = None
    rule: str | None = None


@dataclass(frozen=True)
class RubricDimension:
    name: str
    weight: float
    desc: str | None = None


@dataclass(frozen=True)
class Rubric:
    """The scoring dimensions the Judge uses (U4 §3.5). Weights sum to 1.0."""

    dimensions: tuple[RubricDimension, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(d.name for d in self.dimensions)

    def weights(self) -> dict[str, float]:
        return {d.name: d.weight for d in self.dimensions}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any] | None) -> "Rubric":
        dims_in = (d or {}).get("dimensions") or {}
        if not dims_in:
            raise TemplateError("rubric.dimensions must be a non-empty mapping")
        dims: list[RubricDimension] = []
        for name, spec in dims_in.items():
            spec = spec or {}
            w = spec.get("weight")
            if not isinstance(w, (int, float)):
                raise TemplateError(f"rubric dimension {name!r} needs a numeric weight, got {w!r}")
            dims.append(RubricDimension(name=name, weight=float(w), desc=spec.get("desc")))
        total = sum(d.weight for d in dims)
        if abs(total - 1.0) > _WEIGHT_TOLERANCE:
            raise TemplateError(f"rubric weights must sum to 1.0, got {total}")
        return cls(tuple(dims))


@dataclass(frozen=True)
class Template:
    """A versioned comment template (HLD §7). Immutable once loaded."""

    name: str
    version: int
    table_required: tuple[str, ...]
    table_recommended: tuple[str, ...]
    table_style: StyleSpec
    column_required: tuple[str, ...]
    column_conditional: tuple[FieldSpec, ...]
    column_style: StyleSpec
    rubric: Rubric
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    # -- loading -----------------------------------------------------------
    @classmethod
    def from_dict(cls, spec: Mapping[str, Any]) -> "Template":
        """Load + validate a template from its stored/jsonb spec (the primary path)."""
        if not isinstance(spec, Mapping):
            raise TemplateError("template spec must be a mapping")
        name = spec.get("name")
        if not name or not isinstance(name, str):
            raise TemplateError("template needs a non-empty string 'name'")
        version = spec.get("version", 1)
        if not isinstance(version, int) or version < 1:
            raise TemplateError(f"template 'version' must be a positive int, got {version!r}")

        tc = spec.get("table_comment") or {}
        table_required = tuple(tc.get("required") or ())
        if not table_required:
            raise TemplateError("table_comment.required must be a non-empty list")

        cc = spec.get("column_comment") or {}
        column_required = tuple(cc.get("required") or ())
        if "definition" not in column_required:
            raise TemplateError("column_comment.required must include 'definition'")

        conditionals: list[FieldSpec] = []
        for fname, fspec in (cc.get("conditional") or {}).items():
            fspec = fspec or {}
            when = fspec.get("when", "always")
            if when not in KNOWN_CONDITIONS:
                raise TemplateError(
                    f"column conditional {fname!r}: unknown 'when'={when!r} "
                    f"(allowed: {sorted(KNOWN_CONDITIONS)})"
                )
            conditionals.append(
                FieldSpec(name=fname, requirement="conditional", when=when, rule=fspec.get("rule"))
            )

        return cls(
            name=name,
            version=version,
            table_required=table_required,
            table_recommended=tuple(tc.get("recommended") or ()),
            table_style=StyleSpec.from_dict(tc.get("style")),
            column_required=column_required,
            column_conditional=tuple(conditionals),
            column_style=StyleSpec.from_dict(cc.get("style")),
            rubric=Rubric.from_dict(spec.get("rubric")),
            raw=dict(spec),
        )

    @classmethod
    def from_yaml(cls, text: str) -> "Template":
        """Load from YAML (authoring format). PyYAML is imported lazily."""
        try:
            import yaml  # type: ignore
        except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
            raise TemplateError(
                "from_yaml requires PyYAML (pip install pyyaml); the stored spec path "
                "(from_dict) needs no dependency"
            ) from exc
        data = yaml.safe_load(text)
        if not isinstance(data, Mapping):
            raise TemplateError("YAML template must parse to a mapping")
        return cls.from_dict(data)

    @classmethod
    def load(cls, path: str | Path) -> "Template":
        return cls.from_yaml(Path(path).read_text(encoding="utf-8"))

    # -- use ---------------------------------------------------------------
    def applicable_column_fields(self, signals: Mapping[str, bool]) -> list[FieldSpec]:
        """The column fields that apply to a column with these signals: the required
        field(s) always, plus each conditional whose ``when`` is truthy in ``signals``
        (``when='always'`` conditionals always apply). ``signals`` keys are
        :data:`KNOWN_CONDITIONS` (e.g. ``{"nullable": True, "fk": False, ...}``)."""
        out = [FieldSpec(name=n, requirement="required") for n in self.column_required]
        for fs in self.column_conditional:
            if fs.when == "always" or signals.get(fs.when or "", False):
                out.append(fs)
        return out

    def to_dict(self) -> dict[str, Any]:
        """Round-trip back to the storable jsonb spec shape (U2 ``templates.spec``)."""
        return {
            "name": self.name,
            "version": self.version,
            "table_comment": {
                "required": list(self.table_required),
                "recommended": list(self.table_recommended),
                "style": _style_to_dict(self.table_style),
            },
            "column_comment": {
                "required": list(self.column_required),
                "conditional": {
                    fs.name: {"when": fs.when, "rule": fs.rule} for fs in self.column_conditional
                },
                "style": _style_to_dict(self.column_style),
            },
            "rubric": {
                "dimensions": {
                    d.name: {"weight": d.weight, "desc": d.desc} for d in self.rubric.dimensions
                }
            },
        }


def _style_to_dict(s: StyleSpec) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if s.max_words is not None:
        out["max_words"] = s.max_words
    if s.voice is not None:
        out["voice"] = s.voice
    if s.forbid:
        out["forbid"] = list(s.forbid)
    return out


def default_template() -> Template:
    """The bundled ``default`` template (HLD §7), seeded into Lakebase at setup."""
    return Template.load(Path(__file__).parent / "templates" / "default.yaml")
