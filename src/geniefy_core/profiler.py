"""geniefy-v3 agent core — Profiler (U14).

Implements the ``Profiler`` component of the agent core (``LLD-agent-core.md`` §3.2)
on top of the ``profile_table`` contract (``LLD-profiling-tool.md`` §4). The Profiler
is the *only* component that touches the profiling provider.

Responsibilities (U4 §3.2):
  - **Provider-agnostic selection** (D15): the reference MCP profiling service, a
    UC-function-via-managed-MCP provider, or a customer MCP server all implement the
    same §4 contract; the Profiler is injected with one and cannot tell them apart.
  - **schema_meta introspection** the sanitized profile does not carry — declared
    PK/FK constraints (``information_schema.table_constraints`` / ``key_column_usage``
    / ``constraint_column_usage``). The query knowledge lives here (U4 §3.2); the
    actual SQL *execution* is injected (the core holds no warehouse connection — D1/D17).
  - **Wide-table batching** (U3 §7): call ``profile_table`` with a ``columns`` subset
    of ``profile_batch_size`` (default 50) and merge, deduping the table-level block.
  - **Normalize** to the U3 §4.2 schema and pin ``profile_schema_version`` (tolerating
    additive fields).

Design constraints honored:
  - **Stateless / no embedded I/O** (D1, D17): the Profiler runs no SQL and opens no
    connections. Both the profiling provider and the schema-introspection SQL runner
    are injected dependencies. Output is plain data (``ProfileResult``) the caller
    folds into ``SessionState``.
  - **Sanitized at the source** (D4 / U3 §5): the provider returns already-masked
    aggregates; the Profiler never sees raw rows and does no (de)masking.

Out of scope (other units): the provider implementations and the warehouse SQL
execution (U5 / deployment U8); reasoning over the profile (Reasoner, U4 §3.4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Protocol, Sequence, runtime_checkable

# Profile contract version this core understands (U3 §4.2). We accept any provider
# response whose major matches and whose (major, minor) is >= this minimum, and we
# tolerate additive fields (kept verbatim in the ``raw`` mappings below).
PROFILE_SCHEMA_VERSION = "1.0"
MIN_SUPPORTED_PROFILE_VERSION = "1.0"

# U4 §3.2 / RunConfig.profile_batch_size (U4 §7). Columns per provider call.
DEFAULT_PROFILE_BATCH_SIZE = 50


# ─────────────────────────────────────────────────────────────────────────────
# Table reference
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TableRef:
    """A fully-qualified Unity Catalog table: ``catalog.schema.table``."""

    catalog: str
    schema: str
    table: str

    @classmethod
    def parse(cls, fqn: str | "TableRef") -> "TableRef":
        if isinstance(fqn, TableRef):
            return fqn
        parts = [p.strip().strip("`") for p in str(fqn).split(".")]
        if len(parts) != 3 or not all(parts):
            raise ValueError(
                f"table must be fully-qualified 'catalog.schema.table', got {fqn!r}"
            )
        return cls(*parts)

    @property
    def full_name(self) -> str:
        return f"{self.catalog}.{self.schema}.{self.table}"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.full_name


# ─────────────────────────────────────────────────────────────────────────────
# Errors (U3 §9) — structured, never a fabricated profile
# ─────────────────────────────────────────────────────────────────────────────
class ProfileError(Exception):
    """A profiling failure surfaced to the orchestrator (U3 §9, U4 §8).

    ``code`` is a stable machine token (e.g. ``permission_denied``, ``timeout``,
    ``unsupported``, ``provider_error``) the Gate/Reasoner can branch on; the agent
    surfaces it rather than inventing a profile.
    """

    def __init__(self, code: str, message: str, *, detail: Any | None = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.detail = detail


# Provider error codes the contract defines (U3 §9). Unknown codes pass through.
KNOWN_ERROR_CODES = frozenset(
    {"permission_denied", "timeout", "not_found", "unsupported", "provider_error"}
)


# ─────────────────────────────────────────────────────────────────────────────
# Request models (U3 §4.1)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class SampleSpec:
    """Sampling intent (U3 §4.1/§6). The *thresholds* live in the provider; the
    agent only passes intent. App mode → ``auto``; Job mode → ``full`` (D3)."""

    mode: str = "auto"  # auto | full | rows | percent
    value: int | None = None  # rows (mode=rows) or percent 0–100 (mode=percent)

    _MODES = frozenset({"auto", "full", "rows", "percent"})

    def __post_init__(self) -> None:
        if self.mode not in self._MODES:
            raise ValueError(f"sample.mode must be one of {sorted(self._MODES)}, got {self.mode!r}")
        if self.mode in ("rows", "percent") and self.value is None:
            raise ValueError(f"sample.mode={self.mode!r} requires a value")
        if self.mode == "percent" and self.value is not None and not (0 <= self.value <= 100):
            raise ValueError("sample.value for percent must be in 0–100")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"mode": self.mode}
        if self.value is not None:
            d["value"] = self.value
        return d


@dataclass
class ProfileOptions:
    """Per-call options (U3 §4.1)."""

    top_k: int = 20
    include_samples: bool = True
    max_cardinality_for_topk: int = 50
    reuse_analyze_stats: bool = True  # deferred refinement (U3 §11)

    def to_dict(self) -> dict[str, Any]:
        return {
            "top_k": self.top_k,
            "include_samples": self.include_samples,
            "max_cardinality_for_topk": self.max_cardinality_for_topk,
            "reuse_analyze_stats": self.reuse_analyze_stats,
        }


@dataclass
class ProfileRequest:
    """The ``profile_table`` request payload (U3 §4.1)."""

    table: str
    sample: SampleSpec = field(default_factory=SampleSpec)
    columns: list[str] | None = None
    options: ProfileOptions = field(default_factory=ProfileOptions)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"table": self.table, "sample": self.sample.to_dict()}
        if self.columns is not None:
            payload["columns"] = list(self.columns)
        payload["options"] = self.options.to_dict()
        return payload


# ─────────────────────────────────────────────────────────────────────────────
# Response models (U3 §4.2) — typed view with raw kept for additive tolerance
# ─────────────────────────────────────────────────────────────────────────────
def _get(d: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in d:
            return d[k]
    return default


@dataclass
class ColumnProfile:
    """One column of the sanitized profile (U3 §4.2). ``raw`` keeps every field the
    provider sent, so additive schema growth never drops data."""

    name: str
    ordinal: int | None
    data_type: str | None
    type_class: str | None
    nullable: bool | None
    existing_comment: str | None
    null_fraction: float | None
    distinct_count: int | None
    distinct_is_approx: bool | None
    cardinality_ratio: float | None
    top_k: list[dict[str, Any]]
    pattern_summary: list[dict[str, Any]]
    sample_values: list[Any]
    pii: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ColumnProfile":
        return cls(
            name=d["name"],
            ordinal=d.get("ordinal"),
            data_type=d.get("data_type"),
            type_class=d.get("type_class"),
            nullable=d.get("nullable"),
            existing_comment=d.get("existing_comment"),
            null_fraction=d.get("null_fraction"),
            distinct_count=d.get("distinct_count"),
            distinct_is_approx=d.get("distinct_is_approx"),
            cardinality_ratio=d.get("cardinality_ratio"),
            top_k=list(d.get("top_k") or []),
            pattern_summary=list(d.get("pattern_summary") or []),
            sample_values=list(d.get("sample_values") or []),
            pii=dict(d.get("pii") or {}),
            raw=dict(d),
        )


@dataclass
class TableProfileMeta:
    """The table-level block of the profile (U3 §4.2 ``table``)."""

    full_name: str
    table_type: str | None
    table_format: str | None
    row_count: int | None
    row_count_is_estimate: bool | None
    sampled: bool | None
    sample_method: str | None
    sample_rows: int | None
    column_count: int | None
    partition_columns: list[str]
    existing_comment: str | None
    stats_source: str | None
    profiled_at: str | None
    warnings: list[str]
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "TableProfileMeta":
        return cls(
            full_name=_get(d, "full_name", "table", default=""),
            table_type=d.get("table_type"),
            table_format=d.get("format"),
            row_count=d.get("row_count"),
            row_count_is_estimate=d.get("row_count_is_estimate"),
            sampled=d.get("sampled"),
            sample_method=d.get("sample_method"),
            sample_rows=d.get("sample_rows"),
            column_count=d.get("column_count"),
            partition_columns=list(d.get("partition_columns") or []),
            existing_comment=d.get("existing_comment"),
            stats_source=d.get("stats_source"),
            profiled_at=d.get("profiled_at"),
            warnings=list(d.get("warnings") or []),
            raw=dict(d),
        )


@dataclass
class TableProfile:
    """A normalized, merged §4.2 profile for one table."""

    profile_schema_version: str
    table: TableProfileMeta
    columns: list[ColumnProfile]
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "TableProfile":
        return cls(
            profile_schema_version=str(d.get("profile_schema_version", "")),
            table=TableProfileMeta.from_dict(d.get("table") or {}),
            columns=[ColumnProfile.from_dict(c) for c in (d.get("columns") or [])],
            raw=dict(d),
        )

    @property
    def sampled(self) -> bool:
        return bool(self.table.sampled)

    def column(self, name: str) -> ColumnProfile | None:
        for c in self.columns:
            if c.name == name:
                return c
        return None


# ─────────────────────────────────────────────────────────────────────────────
# schema_meta (U4 §3.2) — structural facts the profile doesn't carry
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ColumnMeta:
    name: str
    ordinal: int | None
    data_type: str | None
    nullable: bool | None
    comment: str | None = None


@dataclass
class ForeignKey:
    constraint_name: str | None
    columns: list[str]
    referenced_table: str | None
    referenced_columns: list[str]


@dataclass
class SchemaMeta:
    """Declared structure that grounds join-key/derivation claims (U4 §3.2, D5).

    A *signal* like a near-1.0 ``cardinality_ratio`` in the profile suggests a key;
    these *declared* constraints are what let the Reasoner assert one. Partition
    columns are sourced from the profile's table block (reliable there) and mirrored
    here for convenience.
    """

    table: str
    columns: list[ColumnMeta] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    partition_columns: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, table: str, d: Mapping[str, Any]) -> "SchemaMeta":
        return cls(
            table=table,
            columns=[
                ColumnMeta(
                    name=c["name"],
                    ordinal=c.get("ordinal"),
                    data_type=c.get("data_type"),
                    nullable=c.get("nullable"),
                    comment=c.get("comment"),
                )
                for c in (d.get("columns") or [])
            ],
            primary_key=list(d.get("primary_key") or []),
            foreign_keys=[
                ForeignKey(
                    constraint_name=fk.get("constraint_name"),
                    columns=list(fk.get("columns") or []),
                    referenced_table=fk.get("referenced_table"),
                    referenced_columns=list(fk.get("referenced_columns") or []),
                )
                for fk in (d.get("foreign_keys") or [])
            ],
            partition_columns=list(d.get("partition_columns") or []),
            raw=dict(d),
        )


@dataclass
class ProfileResult:
    """What ``Profiler.profile`` returns → folded into ``SessionState`` by the caller
    (U4 §3.2 output ``{ profile, schema_meta }``)."""

    profile: TableProfile
    schema_meta: SchemaMeta | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Provider protocols (D15) — anything conforming to §4 / §10 plugs in
# ─────────────────────────────────────────────────────────────────────────────
@runtime_checkable
class ProfileProvider(Protocol):
    """A ``profile_table`` provider (U3 §4/§10). Accepts the §4.1 request dict and
    returns the §4.2 response dict (or a structured error dict / raises)."""

    def profile_table(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class SchemaIntrospector(Protocol):
    """Returns the declared structure of ``table`` as a ``SchemaMeta``-shaped dict
    (keys: ``columns``, ``primary_key``, ``foreign_keys``). Implementations execute
    the SQL from :func:`information_schema_queries` against a warehouse — execution is
    injected so the core stays connection-free (D1/D17)."""

    def introspect(self, table: TableRef) -> Mapping[str, Any]: ...


# ─────────────────────────────────────────────────────────────────────────────
# Reference information_schema queries (U4 §3.2)
# ─────────────────────────────────────────────────────────────────────────────
def information_schema_queries(table: TableRef) -> dict[str, str]:
    """Reference Databricks UC ``information_schema`` queries for schema_meta.

    The query *knowledge* lives in the core (U4 §3.2); a :class:`SchemaIntrospector`
    runs them via its injected warehouse connection. Parameters are inlined as quoted
    literals against ``information_schema`` (catalog/schema/table identifiers, not user
    data) — but a SQL-executing introspector SHOULD prefer bound parameters where its
    client supports them. These are validated against live infra at U17 (deploy prep).

    Returns a mapping of name → SQL: ``columns``, ``constraints`` (PK/FK column usage),
    ``foreign_keys`` (referenced table/columns).
    """
    cat = _ident(table.catalog)
    sch = _literal(table.schema)
    tbl = _literal(table.table)
    return {
        "columns": (
            f"SELECT column_name, ordinal_position, full_data_type AS data_type, "
            f"is_nullable, comment "
            f"FROM {cat}.information_schema.columns "
            f"WHERE table_schema = {sch} AND table_name = {tbl} "
            f"ORDER BY ordinal_position"
        ),
        "constraints": (
            f"SELECT tc.constraint_name, tc.constraint_type, "
            f"kcu.column_name, kcu.ordinal_position "
            f"FROM {cat}.information_schema.table_constraints tc "
            f"JOIN {cat}.information_schema.key_column_usage kcu "
            f"  ON tc.constraint_catalog = kcu.constraint_catalog "
            f" AND tc.constraint_schema = kcu.constraint_schema "
            f" AND tc.constraint_name = kcu.constraint_name "
            f"WHERE tc.table_schema = {sch} AND tc.table_name = {tbl} "
            f"ORDER BY tc.constraint_name, kcu.ordinal_position"
        ),
        "foreign_keys": (
            f"SELECT rc.constraint_name, "
            f"ccu.table_catalog AS ref_catalog, ccu.table_schema AS ref_schema, "
            f"ccu.table_name AS ref_table, ccu.column_name AS ref_column "
            f"FROM {cat}.information_schema.referential_constraints rc "
            f"JOIN {cat}.information_schema.constraint_column_usage ccu "
            f"  ON rc.unique_constraint_catalog = ccu.constraint_catalog "
            f" AND rc.unique_constraint_schema = ccu.constraint_schema "
            f" AND rc.unique_constraint_name = ccu.constraint_name "
            f"WHERE rc.constraint_schema = {sch}"
        ),
    }


def _ident(name: str) -> str:
    """Backtick-quote a UC identifier, escaping embedded backticks."""
    return "`" + name.replace("`", "``") + "`"


def _literal(value: str) -> str:
    """Single-quote a string literal, escaping embedded quotes."""
    return "'" + value.replace("'", "''") + "'"


# ─────────────────────────────────────────────────────────────────────────────
# The Profiler
# ─────────────────────────────────────────────────────────────────────────────
class Profiler:
    """Thin, provider-agnostic wrapper over ``profile_table`` (U4 §3.2).

    Example::

        profiler = Profiler(provider=my_mcp_provider, introspector=my_introspector)
        result = profiler.profile("main.sales.orders", sample=SampleSpec("auto"))
        result.profile.table.row_count        # normalized §4.2 profile
        result.schema_meta.primary_key        # declared keys (or None if no introspector)
    """

    def __init__(
        self,
        provider: ProfileProvider,
        introspector: SchemaIntrospector | None = None,
        *,
        profile_batch_size: int = DEFAULT_PROFILE_BATCH_SIZE,
        min_schema_version: str = MIN_SUPPORTED_PROFILE_VERSION,
    ):
        if profile_batch_size < 1:
            raise ValueError("profile_batch_size must be >= 1")
        self._provider = provider
        self._introspector = introspector
        self._batch_size = profile_batch_size
        self._min_version = min_schema_version

    # -- public API --------------------------------------------------------
    def profile(
        self,
        table: str | TableRef,
        *,
        sample: SampleSpec | None = None,
        columns: Sequence[str] | None = None,
        options: ProfileOptions | None = None,
    ) -> ProfileResult:
        """Profile ``table`` and introspect its declared structure.

        Drives wide-table batching (U3 §7) when the column set exceeds
        ``profile_batch_size``, merges the responses, validates the schema version,
        and returns ``{ profile, schema_meta }`` (U4 §3.2). Raises :class:`ProfileError`
        on a provider/introspection failure — never a fabricated profile (U3 §9).
        """
        ref = TableRef.parse(table)
        sample = sample or SampleSpec()
        options = options or ProfileOptions()

        schema_meta = self._introspect(ref)

        # Decide the batching column set: explicit arg wins; else use introspected
        # columns (so we can batch); else let the provider return all in one call.
        batch_cols: list[str] | None
        if columns is not None:
            batch_cols = list(columns)
        elif schema_meta is not None and schema_meta.columns:
            batch_cols = [c.name for c in schema_meta.columns]
        else:
            batch_cols = None

        if batch_cols is not None and len(batch_cols) > self._batch_size:
            responses = [
                self._call_provider(ProfileRequest(ref.full_name, sample, chunk, options))
                for chunk in self._chunks(batch_cols)
            ]
        else:
            # Single call. Pass explicit columns only if the caller asked for a subset.
            single_cols = batch_cols if columns is not None else None
            responses = [
                self._call_provider(ProfileRequest(ref.full_name, sample, single_cols, options))
            ]

        merged = self._merge(responses)
        self._check_version(merged.get("profile_schema_version"))
        profile = TableProfile.from_dict(merged)

        # The profile reliably carries partition columns; mirror them into schema_meta.
        if schema_meta is not None and not schema_meta.partition_columns:
            schema_meta.partition_columns = list(profile.table.partition_columns)

        return ProfileResult(profile=profile, schema_meta=schema_meta)

    # -- internals ---------------------------------------------------------
    def _introspect(self, ref: TableRef) -> SchemaMeta | None:
        if self._introspector is None:
            return None
        try:
            raw = self._introspector.introspect(ref)
            # Parse inside the guard too: a malformed result (e.g. a column with no
            # 'name') becomes a structured ProfileError, not a raw KeyError (U3 §9).
            return SchemaMeta.from_dict(ref.full_name, raw or {})
        except ProfileError:
            raise
        except Exception as exc:  # introspection is best-effort structural enrichment
            raise ProfileError(
                "provider_error", f"schema introspection failed for {ref.full_name}", detail=str(exc)
            ) from exc

    def _call_provider(self, request: ProfileRequest) -> dict[str, Any]:
        try:
            resp = self._provider.profile_table(request.to_dict())
        except ProfileError:
            raise
        except Exception as exc:
            raise ProfileError(
                "provider_error", f"profile_table call failed: {exc}", detail=str(exc)
            ) from exc
        if resp is None:
            raise ProfileError("provider_error", "provider returned no response")
        resp = dict(resp)
        # A provider may signal a structured error in-band (U3 §9) instead of raising.
        if "error" in resp:
            err = resp["error"]
            if isinstance(err, Mapping):
                code = str(err.get("code", "provider_error"))
                raise ProfileError(code, str(err.get("message", "provider error")), detail=err)
            raise ProfileError("provider_error", str(err))
        return resp

    def _chunks(self, columns: list[str]) -> Iterator[list[str]]:
        for i in range(0, len(columns), self._batch_size):
            yield columns[i : i + self._batch_size]

    @staticmethod
    def _merge(responses: list[dict[str, Any]]) -> dict[str, Any]:
        """Merge batched §4.2 responses: table block from the first response, columns
        concatenated in order, warnings unioned (U3 §7 — provider returns the table
        block each time; we dedup)."""
        if not responses:
            raise ProfileError("provider_error", "no profile responses to merge")
        first = responses[0]
        merged: dict[str, Any] = {
            "profile_schema_version": first.get("profile_schema_version"),
            "table": dict(first.get("table") or {}),
            "columns": [],
        }
        seen_cols: set[str] = set()
        warnings: list[str] = list((merged["table"].get("warnings")) or [])
        for resp in responses:
            for col in resp.get("columns") or []:
                name = col.get("name")
                if not name:  # U3 §9/§10: a structured error, never a silent collapse
                    raise ProfileError(
                        "provider_error",
                        "profile response contains a column with no 'name'",
                        detail=col,
                    )
                if name in seen_cols:
                    continue
                seen_cols.add(name)
                merged["columns"].append(col)
            for w in (resp.get("table") or {}).get("warnings") or []:
                if w not in warnings:
                    warnings.append(w)
        if warnings:
            merged["table"]["warnings"] = warnings
        return merged

    def _check_version(self, version: Any) -> None:
        if not version:
            raise ProfileError("unsupported", "provider response missing profile_schema_version")
        v = _version_tuple(str(version))
        core = _version_tuple(PROFILE_SCHEMA_VERSION)  # the schema this core is written against
        floor = _version_tuple(self._min_version)
        # Major must match the core's target schema; a newer *minor* is additive and
        # tolerated (extra fields are preserved verbatim in each `raw` mapping).
        if v[0] != core[0]:
            raise ProfileError(
                "unsupported",
                f"profile_schema_version {version} major != core target {PROFILE_SCHEMA_VERSION}",
            )
        if v < floor:
            raise ProfileError(
                "unsupported",
                f"profile_schema_version {version} < minimum {self._min_version}",
            )


def _version_tuple(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in v.split("."))
    except ValueError:
        return (0,)
