"""geniefy-v3 App backend — in-app ProfileProvider (U51, D25).

The reference profiling provider: implements the U3 `profile_table` contract **inside the
app backend** against the bound SQL warehouse (D25), so a demo needs no separate service.
It generates profiling SQL, runs it via the injected ``WarehouseSqlRunner``, and returns
the sanitized U3 §4.2 profile — applying §5 PII sanitization **at the source** (D4), so
raw/identifying values never leave the warehouse.

Conforms to ``geniefy_core.profiler.ProfileProvider`` (``profile_table(request) -> dict``),
so the agent core's ``Profiler`` consumes it unchanged (provider-agnostic, D15). Hermetic:
the SQL runner is injected; tests drive it with canned rows. Pluggable: a customer can
swap in an external MCP profiling server or a UC function via config (D15/D25).

v1 scope (what the core actually uses): column metadata (`information_schema`), per-column
null_fraction · distinct_count (approx) · cardinality_ratio · is_enum_candidate, min/max
for numeric/temporal, top-K for low-cardinality, existing comments, and PII classification
+ masking. Deferred (U3 §6/§11): single-pass multi-aggregate SQL, percentiles/stddev/len
stats, richer pattern library, true sampling thresholds, ANALYZE-stats reuse.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Mapping

from geniefy_app.providers import SqlExecute  # (sql) -> list[dict]; WarehouseSqlRunner is one

PROFILE_SCHEMA_VERSION = "1.0"

_INTEGER = {"int", "integer", "bigint", "smallint", "tinyint", "long", "byte", "short"}
_DECIMAL = {"double", "float", "real", "decimal", "numeric"}
_TEMPORAL = {"date", "timestamp", "timestamp_ntz", "interval"}
_COMPLEX = {"struct", "array", "map"}

# PII classifiers by column-name keyword and by value pattern (§5, first line of defense).
_NAME_PII = [
    ("email", re.compile(r"e[-_]?mail", re.I)),
    ("ssn", re.compile(r"ssn|social_security", re.I)),
    ("phone", re.compile(r"phone|mobile|msisdn", re.I)),
    ("credit_card", re.compile(r"credit_?card|card_number|ccnum|cc_num", re.I)),
    ("ip", re.compile(r"^ip(_?addr(ess)?)?$", re.I)),
]
_VALUE_PII = [
    ("ssn", re.compile(r"^\d{3}-\d{2}-\d{4}$")),
    ("email", re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")),
    ("credit_card", re.compile(r"^\d{13,16}$")),
]


def _ident(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


def _lit(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _split(table: str) -> tuple[str, str, str]:
    parts = [p.strip().strip("`") for p in str(table).split(".")]
    if len(parts) != 3 or not all(parts):
        raise ValueError(f"table must be 'catalog.schema.table', got {table!r}")
    return parts[0], parts[1], parts[2]


def _type_class(data_type: str | None) -> str:
    base = re.split(r"[(<]", (data_type or "").strip().lower(), maxsplit=1)[0].strip()
    if base in _INTEGER:
        return "integer"
    if base in _DECIMAL:
        return "decimal"
    if base in _TEMPORAL:
        return "temporal"
    if base == "boolean":
        return "boolean"
    if base == "binary":
        return "binary"
    if base in _COMPLEX:
        return "complex"
    return "string"


class InAppProfileProvider:
    """Reference ``profile_table`` provider over a SQL warehouse (D25)."""

    def __init__(self, run_sql: SqlExecute, *, default_top_k: int = 20,
                 default_max_cardinality_for_topk: int = 50):
        self._run = run_sql
        self._top_k = default_top_k
        self._max_card = default_max_cardinality_for_topk

    # -- U3 contract --------------------------------------------------------
    def profile_table(self, request: Mapping[str, Any]) -> dict[str, Any]:
        table = request["table"]
        cat, sch, tbl = _split(table)
        opts = request.get("options") or {}
        top_k = int(opts.get("top_k", self._top_k))
        max_card = int(opts.get("max_cardinality_for_topk", self._max_card))
        include_samples = bool(opts.get("include_samples", True))
        sample = request.get("sample") or {"mode": "auto"}
        sampled, frm = self._from_clause(table, sample)

        cols_meta = self._columns(cat, sch, tbl)
        wanted = request.get("columns")
        if wanted:
            wanted_set = set(wanted)
            cols_meta = [c for c in cols_meta if c["name"] in wanted_set]

        row_count = self._row_count(frm)
        warnings: list[str] = []
        if sampled:
            warnings.append("sampled: row_count is an estimate")
        if not cols_meta:
            warnings.append("no columns found (check the table name / SELECT grant)")
        if row_count == 0:
            warnings.append("table is empty (0 rows): distributions and cardinality are not meaningful (U3 §9)")

        columns: list[dict[str, Any]] = []
        for cm in cols_meta:
            columns.append(self._profile_column(frm, cm, row_count, top_k, max_card, include_samples))

        return {
            "profile_schema_version": PROFILE_SCHEMA_VERSION,
            "table": {
                "full_name": table,
                "row_count": row_count,
                "row_count_is_estimate": sampled,
                "sampled": sampled,
                "column_count": len(cols_meta),
                "partition_columns": [],
                "existing_comment": self._table_comment(cat, sch, tbl),
                "stats_source": "computed",
                "profiled_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "warnings": warnings,
            },
            "columns": columns,
        }

    # -- per column ---------------------------------------------------------
    def _profile_column(self, frm: str, cm: dict[str, Any], row_count: int | None,
                        top_k: int, max_card: int, include_samples: bool) -> dict[str, Any]:
        name = cm["name"]
        tclass = _type_class(cm.get("data_type"))
        col = {
            "name": name, "ordinal": cm.get("ordinal"), "data_type": cm.get("data_type"),
            "type_class": tclass, "nullable": cm.get("nullable"),
            "existing_comment": cm.get("comment"),
        }
        if tclass in ("complex", "binary"):
            # distributions are not meaningful / not cheap → just structure
            col["pii"] = {"detected": False, "classes": [], "action": "none"}
            return col

        agg = self._aggregates(frm, name, tclass)
        total = agg.get("total") or 0
        non_null = agg.get("non_null") or 0
        ndv = agg.get("ndv")
        col["null_fraction"] = round(1 - (non_null / total), 6) if total else None
        col["distinct_count"] = ndv
        col["distinct_is_approx"] = True
        col["cardinality_ratio"] = round(ndv / non_null, 6) if (ndv is not None and non_null) else None
        if tclass in ("integer", "decimal", "temporal") and agg.get("min_v") is not None:
            col["min"], col["max"] = _s(agg.get("min_v")), _s(agg.get("max_v"))
        col["is_enum_candidate"] = bool(ndv is not None and 0 < ndv <= max_card)

        if col["is_enum_candidate"] and top_k > 0:
            col["top_k"] = self._top_k_values(frm, name, top_k)

        # §5 PII: classify (name + value patterns) → mask/omit before return (D4)
        self._sanitize(col, include_samples)
        return col

    # -- SQL ----------------------------------------------------------------
    def _columns(self, cat: str, sch: str, tbl: str) -> list[dict[str, Any]]:
        rows = self._run(
            f"SELECT column_name, ordinal_position, full_data_type AS data_type, "
            f"is_nullable, comment FROM {_ident(cat)}.information_schema.columns "
            f"WHERE table_schema = {_lit(sch)} AND table_name = {_lit(tbl)} ORDER BY ordinal_position"
        )
        out = []
        for r in rows:
            nullable = r.get("is_nullable")
            out.append({
                "name": r.get("column_name"),
                "ordinal": r.get("ordinal_position"),
                "data_type": r.get("data_type"),
                "nullable": (str(nullable).upper() == "YES") if nullable is not None else None,
                "comment": r.get("comment"),
            })
        return [c for c in out if c["name"]]

    def _table_comment(self, cat: str, sch: str, tbl: str) -> str | None:
        rows = self._run(
            f"SELECT comment FROM {_ident(cat)}.information_schema.tables "
            f"WHERE table_schema = {_lit(sch)} AND table_name = {_lit(tbl)}"
        )
        return rows[0].get("comment") if rows else None

    def _row_count(self, frm: str) -> int | None:
        rows = self._run(f"SELECT COUNT(*) AS n FROM {frm}")
        return rows[0].get("n") if rows else None

    def _aggregates(self, frm: str, name: str, tclass: str) -> dict[str, Any]:
        c = _ident(name)
        select = [f"COUNT(*) AS total", f"COUNT({c}) AS non_null", f"approx_count_distinct({c}) AS ndv"]
        if tclass in ("integer", "decimal", "temporal"):
            select += [f"MIN({c}) AS min_v", f"MAX({c}) AS max_v"]
        rows = self._run(f"SELECT {', '.join(select)} FROM {frm}")
        return rows[0] if rows else {}

    def _top_k_values(self, frm: str, name: str, k: int) -> list[dict[str, Any]]:
        c = _ident(name)
        rows = self._run(
            f"SELECT {c} AS value, COUNT(*) AS cnt FROM {frm} WHERE {c} IS NOT NULL "
            f"GROUP BY {c} ORDER BY cnt DESC LIMIT {int(k)}"
        )
        return [{"value": r.get("value"), "count": r.get("cnt")} for r in rows]

    @staticmethod
    def _from_clause(table: str, sample: Mapping[str, Any]) -> tuple[bool, str]:
        cat, sch, tbl = _split(table)
        fq = f"{_ident(cat)}.{_ident(sch)}.{_ident(tbl)}"
        mode = sample.get("mode", "auto")
        if mode == "rows" and sample.get("value"):
            return True, f"{fq} TABLESAMPLE ({int(sample['value'])} ROWS)"
        if mode == "percent" and sample.get("value"):
            return True, f"{fq} TABLESAMPLE ({float(sample['value'])} PERCENT)"
        return False, fq  # auto/full → full scan in v1 (thresholded sampling deferred, U3 §6)

    # -- PII sanitization (§5 / D4) ----------------------------------------
    def _sanitize(self, col: dict[str, Any], include_samples: bool) -> None:
        classes = self._classify(col)
        if not classes:
            col["pii"] = {"detected": False, "classes": [], "action": "none"}
            return
        # mask the values we would otherwise emit; never return raw identifiers
        if "top_k" in col:
            for item in col["top_k"]:
                item["value"] = _mask(item.get("value"))
        col["pii"] = {"detected": True, "classes": sorted(classes), "action": "masked"}

    def _classify(self, col: dict[str, Any]) -> set[str]:
        found: set[str] = set()
        name = col.get("name") or ""
        for cls, rx in _NAME_PII:
            if rx.search(name):
                found.add(cls)
        for item in col.get("top_k", []) or []:
            v = item.get("value")
            if isinstance(v, str):
                for cls, rx in _VALUE_PII:
                    if rx.match(v):
                        found.add(cls)
        return found


def _mask(value: Any) -> str:
    s = "" if value is None else str(value)
    if len(s) <= 2:
        return "••"
    return s[:2] + "•" * max(2, len(s) - 2)


def _s(value: Any) -> Any:
    """Stringify temporals/Decimals so the profile is JSON-serializable (D17 snapshot)."""
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    return value
