"""geniefy-v3 agent core — ContextGatherer + providers (U39).

Profiling reveals structure; **context** reveals meaning (D5). This layer is the uniform
interface over context providers (U4 §3.3): built-in, always-on **lineage** and
**query-history** providers (system-table SQL), and **pluggable MCP providers**
(Glean/Confluence/Atlassian/Genie/custom) per the D34 invocation contract (U24 §3).

Each provider returns ranked ``ContextSnippet``s tagged with their source and which
columns/relationships they inform; the gatherer merges, ranks, and trims to
``context_token_budget`` (U4 §3.3) so wide tables don't blow the prompt. A provider that
errors/unreachable is **skipped with a warning — degraded, not failed** (U4 §8); the
built-ins still run.

Hermetic (D1/D17): the system-table SQL runner and the MCP session are **injected** — the
query/contract knowledge lives here, the I/O is supplied by the caller (app layer, U5).
Auth is a secret-scope reference, never a raw secret (D4/D5).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

from geniefy_core.llm import HeuristicTokenCounter, TokenCounter
from geniefy_core.state import Phase, SessionState

DEFAULT_CONTEXT_TOKEN_BUDGET = 4000


# ─────────────────────────────────────────────────────────────────────────────
# ContextSnippet — the typed unit the Reasoner can cite
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ContextSnippet:
    text: str
    source: str                       # provider name (e.g. "uc_lineage", "glean")
    informs: list[str] = field(default_factory=list)  # column names / relationship hints
    relevance: float = 0.5            # [0,1] — ranking key (most-relevant first)
    raw: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "source": self.source,
                "informs": list(self.informs), "relevance": self.relevance}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ContextSnippet":
        return cls(text=d.get("text", ""), source=d.get("source", "context"),
                   informs=list(d.get("informs") or []), relevance=d.get("relevance", 0.5))


@runtime_checkable
class ContextProvider(Protocol):
    name: str

    def gather(self, table: str, columns: Sequence[str]) -> list[ContextSnippet]: ...


# ─────────────────────────────────────────────────────────────────────────────
# Built-in providers (D5) — system-table SQL; runner injected
# ─────────────────────────────────────────────────────────────────────────────
# A SQL runner returns rows as dicts. Supplied by the app layer (warehouse connection).
SqlRunner = Callable[[str], list[dict[str, Any]]]


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class LineageProvider:
    """Built-in lineage (D5): upstream sources, downstream consumers, and column-level
    lineage → FK/derivation hints, from ``system.access.{table,column}_lineage``."""

    name = "uc_lineage"

    def __init__(self, run_sql: SqlRunner):
        self._run = run_sql

    @staticmethod
    def queries(table: str) -> dict[str, str]:
        t = _lit(table)
        return {
            "upstream": (
                "SELECT DISTINCT source_table_full_name FROM system.access.table_lineage "
                f"WHERE target_table_full_name = {t} AND source_table_full_name IS NOT NULL"
            ),
            "downstream": (
                "SELECT DISTINCT target_table_full_name FROM system.access.table_lineage "
                f"WHERE source_table_full_name = {t} AND target_table_full_name IS NOT NULL"
            ),
            "column_lineage": (
                "SELECT source_table_full_name, source_column_name, target_column_name "
                f"FROM system.access.column_lineage WHERE target_table_full_name = {t} "
                "AND source_column_name IS NOT NULL"
            ),
        }

    def gather(self, table: str, columns: Sequence[str]) -> list[ContextSnippet]:
        q = self.queries(table)
        snippets: list[ContextSnippet] = []
        ups = [r.get("source_table_full_name") for r in self._run(q["upstream"])]
        ups = [u for u in ups if u]
        if ups:
            snippets.append(ContextSnippet(
                text=f"Upstream sources (lineage): {', '.join(ups[:10])}.",
                source=self.name, relevance=0.7))
        downs = [r.get("target_table_full_name") for r in self._run(q["downstream"])]
        downs = [d for d in downs if d]
        if downs:
            snippets.append(ContextSnippet(
                text=f"Downstream consumers (lineage): {', '.join(downs[:10])}.",
                source=self.name, relevance=0.55))
        for r in self._run(q["column_lineage"]):
            tgt, src_t, src_c = r.get("target_column_name"), r.get("source_table_full_name"), r.get("source_column_name")
            if tgt and src_t and src_c:
                snippets.append(ContextSnippet(
                    text=f"Column `{tgt}` derives from `{src_t}`.`{src_c}` (column lineage → FK/derivation hint).",
                    source=self.name, informs=[tgt], relevance=0.8))
        return snippets


class QueryHistoryProvider:
    """Built-in query-history (D5): recent SQL referencing the table, parsed (best-effort)
    for JOIN / WHERE / GROUP BY patterns → join keys, common filters, grain hints."""

    name = "query_history"

    def __init__(self, run_sql: SqlRunner, *, limit: int = 200):
        self._run = run_sql
        self._limit = limit

    def query(self, table: str) -> str:
        return (
            "SELECT statement_text FROM system.query.history "
            f"WHERE statement_text ILIKE {_lit('%' + table + '%')} "
            f"ORDER BY start_time DESC LIMIT {self._limit}"
        )

    def gather(self, table: str, columns: Sequence[str]) -> list[ContextSnippet]:
        rows = self._run(self.query(table))
        texts = [r.get("statement_text", "") for r in rows]
        if not texts:
            return []
        # Alias-tolerant: capture the equality in any JOIN ... ON <a> = <b> (aliases vary).
        joins = _top(re.findall(r"\bON\s+([\w.`]+\s*=\s*[\w.`]+)", " ".join(texts), re.I))
        groups = _top(re.findall(r"\bGROUP\s+BY\b\s+([\w.`,\s]+?)(?:\bORDER\b|\bLIMIT\b|$)", " ".join(texts), re.I))
        snippets = [ContextSnippet(
            text=f"Observed in {len(texts)} recent queries referencing this table.",
            source=self.name, relevance=0.5)]
        if joins:
            snippets.append(ContextSnippet(
                text=f"Common join conditions (query history): {'; '.join(j.strip() for j in joins)}.",
                source=self.name, relevance=0.75))
        if groups:
            snippets.append(ContextSnippet(
                text=f"Common GROUP BY (grain hint): {'; '.join(g.strip() for g in groups)}.",
                source=self.name, relevance=0.65))
        return snippets


def _top(items: list[str], k: int = 5) -> list[str]:
    seen: dict[str, int] = {}
    for it in items:
        key = re.sub(r"\s+", " ", it.strip())
        seen[key] = seen.get(key, 0) + 1
    return [k for k, _ in sorted(seen.items(), key=lambda kv: -kv[1])][:k]


# ─────────────────────────────────────────────────────────────────────────────
# Library reuse provider (D52 §A4 / U104) — previously-approved canonical wording
# ─────────────────────────────────────────────────────────────────────────────
# Injected lookup: (scope, match_keys) -> [{match_key, canonical_comment, tags, usage_count}, ...]
# Wired by the app layer to SessionStore.list_library_for_reuse (status ∈ {approved, applied},
# sunset excluded, per-key usage-ranked). Hermetic core: the SQL/DB lives outside (D1).
LibraryLookup = Callable[[str, Sequence[str]], list[dict[str, Any]]]


class LibraryProvider:
    """Feeds **previously-approved canonical comments** into generation as *suggestion-only*
    grounding (D52 §A4): the Reasoner reuses approved wording when it fits THIS table's data —
    never a blind copy. Flows through the normal ranked/budgeted context pipeline (not a
    Reasoner side-channel). One snippet per matched table FQN + per matched column name; the
    snippet text frames it explicitly as reusable-if-it-fits. Errors degrade (U4 §8)."""

    name = "comment_library"

    def __init__(self, lookup: LibraryLookup, *, table_relevance: float = 0.82,
                 column_relevance: float = 0.85):
        self._lookup = lookup
        self._table_relevance = table_relevance
        self._column_relevance = column_relevance

    def gather(self, table: str, columns: Sequence[str]) -> list[ContextSnippet]:
        out: list[ContextSnippet] = []
        for row in (self._lookup("table", [table]) or []):
            text = self._fmt("this table", row)
            if text:
                out.append(ContextSnippet(text=text, source=self.name, relevance=self._table_relevance))
        cols = [c for c in columns if c]
        if cols:
            for row in (self._lookup("column", cols) or []):
                mk = row.get("match_key")
                text = self._fmt(f"a column named `{mk}`", row)
                if text:
                    out.append(ContextSnippet(text=text, source=self.name,
                                              informs=[mk] if mk else [],
                                              relevance=self._column_relevance))
        return out

    @staticmethod
    def _fmt(subject: str, row: Mapping[str, Any]) -> str | None:
        comment = (row.get("canonical_comment") or "").strip()
        if not comment:
            return None
        uses = row.get("usage_count")
        tags = row.get("tags") or []
        suffix = f" [approved, used {uses}x]" if isinstance(uses, int) else " [approved]"
        tag_part = f" tags: {', '.join(map(str, tags))}." if tags else ""
        return (f"Previously-approved canonical comment for {subject} — REUSE this wording if it "
                f"fits this table's evidence; adapt or ignore it if the data differs (do not copy "
                f"blindly){suffix}: \"{comment}\".{tag_part}")


# ─────────────────────────────────────────────────────────────────────────────
# MCP provider (D34 / U24 §3) — registration schema + invocation contract
# ─────────────────────────────────────────────────────────────────────────────
@runtime_checkable
class McpSession(Protocol):
    """A connected MCP session. The concrete impl (built at the app layer from the
    provider config + secret-scope token) handles transport/auth; here it's injected."""

    def list_tools(self) -> list[str]: ...

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Any: ...


class McpContextProvider:
    """A configured MCP context provider (D34). Config (from U2 ``context_providers``):
    ``name``, ``tool_allowlist``, ``query_template``, ``max_snippets``. The session is
    produced by an injected ``session_factory(config)`` (so transport/auth — a secret-
    scope ref, D4/D5 — live outside this hermetic core)."""

    def __init__(self, config: Mapping[str, Any], session_factory: Callable[[Mapping[str, Any]], McpSession]):
        self.config = dict(config)
        self.name = self.config.get("name", "mcp")
        self._factory = session_factory

    def gather(self, table: str, columns: Sequence[str]) -> list[ContextSnippet]:
        session = self._factory(self.config)  # may raise → gatherer degrades (U4 §8)
        allow = set(self.config.get("tool_allowlist") or [])
        tools = [t for t in session.list_tools() if not allow or t in allow]
        if not tools:
            return []
        query = self._build_query(table, columns)
        max_snippets = int(self.config.get("max_snippets", 8))
        out: list[ContextSnippet] = []
        for tool in tools:
            result = session.call_tool(tool, {"query": query})
            for item in _normalize_mcp_result(result):
                out.append(ContextSnippet(
                    text=item, source=self.name, relevance=0.6,
                    raw={"tool": tool}))
                if len(out) >= max_snippets:
                    return out
        return out

    def _build_query(self, table: str, columns: Sequence[str]) -> str:
        template = self.config.get("query_template") or "{full_name}: {column_terms}"
        return template.format(full_name=table, column_terms=", ".join(list(columns)[:25]))


def _normalize_mcp_result(result: Any) -> list[str]:
    """Coerce a tool result into text snippets (MCP results vary: str, list, or a dict
    with content/results/text). Keep it forgiving — unknown shapes stringify."""
    if result is None:
        return []
    if isinstance(result, str):
        return [result]
    if isinstance(result, Mapping):
        for key in ("results", "content", "items", "data"):
            if key in result:
                return _normalize_mcp_result(result[key])
        for key in ("text", "snippet", "summary"):
            if key in result:
                return [str(result[key])]
        return [str(result)]
    if isinstance(result, (list, tuple)):
        out: list[str] = []
        for r in result:
            if isinstance(r, Mapping):
                out.append(str(r.get("text") or r.get("snippet") or r.get("content") or r))
            else:
                out.append(str(r))
        return out
    return [str(result)]


# ─────────────────────────────────────────────────────────────────────────────
# ContextGatherer
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class GatherResult:
    snippets: list[ContextSnippet]
    warnings: list[str]  # providers that degraded (name + reason)


class ContextGatherer:
    """Runs the configured providers, merges + ranks their snippets, and trims to the
    token budget (U4 §3.3). Provider failures degrade (warning), never abort the run."""

    def __init__(
        self,
        providers: Sequence[ContextProvider],
        *,
        context_token_budget: int = DEFAULT_CONTEXT_TOKEN_BUDGET,
        counter: TokenCounter | None = None,
    ):
        self.providers = list(providers)
        self.budget = context_token_budget
        self.counter = counter or HeuristicTokenCounter()

    def gather(self, table: str, columns: Sequence[str]) -> GatherResult:
        collected: list[ContextSnippet] = []
        warnings: list[str] = []
        for p in self.providers:
            try:
                collected.extend(p.gather(table, columns))
            except Exception as exc:  # degrade, don't fail the run (U4 §8)
                warnings.append(f"{getattr(p, 'name', 'provider')}: unavailable ({exc})")
        ranked = sorted(collected, key=lambda s: s.relevance, reverse=True)
        return GatherResult(self._trim(ranked), warnings)

    def gather_into(self, state: SessionState, columns: Sequence[str] | None = None) -> GatherResult:
        """Convenience: gather for ``state.target`` and write the snippets into
        ``state.context`` (as dicts) + set ``phase = GATHERING_CONTEXT``."""
        state.phase = Phase.GATHERING_CONTEXT
        cols = list(columns or [c.get("name") for c in (state.profile or {}).get("columns", [])])
        result = self.gather(state.target, [c for c in cols if c])
        state.context = [s.to_dict() for s in result.snippets]
        return result

    def _trim(self, ranked: list[ContextSnippet]) -> list[ContextSnippet]:
        kept: list[ContextSnippet] = []
        used = 0
        for s in ranked:
            cost = self.counter.count(s.text)
            if used + cost > self.budget and kept:  # always keep at least the top snippet
                break
            kept.append(s)
            used += cost
        return kept
