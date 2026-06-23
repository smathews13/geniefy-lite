"""geniefy-v3 App backend — concrete infra boundaries (U49).

The agent core is hermetic: it talks to the model and the warehouse only through injected
protocols (`geniefy_core.llm.ChatTransport`, `geniefy_core.context.SqlRunner`,
`geniefy_core.context.McpSession`). This module supplies the **real** Databricks-backed
implementations the App wires at startup:

  - ``FmapiChatTransport`` — implements ``ChatTransport`` over a Databricks **serving
    endpoint** (FMAPI Claude, D4). Builds the chat-completions payload and returns the
    OpenAI-compatible response dict the ``LLMClient`` parses.
  - ``WarehouseSqlRunner`` — implements ``SqlRunner`` (``(sql) -> list[dict]``) for the
    in-app profiling provider (D25) and the lineage/query-history context providers (D5).

Each adapter wraps an **injected low-level callable**, so the request-building + response-
mapping logic is unit-tested with fakes (no infra). The ``*_from_databricks`` factories
build the real callables from the Databricks SDK (**lazy import** — so importing this
module needs no SDK), and are validated against live infra at the integration/deploy step
(like the SessionStore dev round-trip, U47), not in the hermetic suite.

The MCP session factory (D34) is a separate follow-on; the built-in context providers
(lineage/query-history) only need ``WarehouseSqlRunner``.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

# Injected low-level callables:
#   ChatQuery:   (endpoint_name, payload_dict) -> OpenAI-compatible response dict
#   SqlExecute:  (sql) -> list of row dicts
ChatQuery = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]
SqlExecute = Callable[[str], list[dict[str, Any]]]


class FmapiChatTransport:
    """A ``geniefy_core.llm.ChatTransport`` backed by a Databricks serving endpoint.

    Thin by design: it builds the request payload and passes the response through
    unchanged (the serving endpoint is OpenAI-compatible, so the dict already matches
    what ``LLMClient._parse`` expects). It does NOT swallow errors — the ``LLMClient``
    owns retries/backoff (U4 §5)."""

    def __init__(self, query: ChatQuery):
        self._query = query

    def send(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        response_format: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        payload: dict[str, Any] = {
            "messages": list(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        # NB (U77): Databricks-served Claude (FMAPI) REJECTS OpenAI's response_format=json_object
        # ("INVALID_PARAMETER_VALUE: Response format type json_object is not supported for this
        # model"), so we do NOT forward it. The Reasoner/Judge system prompts already demand
        # JSON-only output and the LLMClient parses that. (Caught live at deploy — D36.)
        _ = response_format  # accepted for the ChatTransport protocol, intentionally not sent
        resp = self._query(model, payload)
        return resp if isinstance(resp, dict) else dict(resp)


class WarehouseSqlRunner:
    """A ``SqlRunner`` (``(sql) -> list[dict]``) over a Databricks SQL warehouse. Wraps an
    injected ``execute(sql) -> rows`` and normalizes each row to a plain ``dict``."""

    def __init__(self, execute: SqlExecute):
        self._execute = execute

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        return [dict(row) for row in (self._execute(sql) or [])]


# ─────────────────────────────────────────────────────────────────────────────
# Real Databricks wiring (lazy SDK import; integration-verified, not hermetic)
# ─────────────────────────────────────────────────────────────────────────────
def workspace_bearer(w: Any) -> str:
    """A workspace bearer token that works on BOTH runtimes (U123). On the Databricks **App** the
    SP's credential strategy exposes ``config.oauth_token()``; on a **Job cluster** it does not (the
    runtime credential strategy has no ``oauth_token`` — the live U113 ``AttributeError``), so fall
    back to the SDK's ``authenticate()`` headers, which any auth type populates."""
    try:
        return w.config.oauth_token().access_token
    except Exception:
        pass
    auth = ""
    try:
        res = w.config.authenticate()           # newer SDK: returns {"Authorization": "Bearer …"}
        if isinstance(res, dict):
            auth = res.get("Authorization", "")
    except TypeError:                            # older SDK: fills a passed-in dict
        h: dict[str, str] = {}
        w.config.authenticate(h)
        auth = h.get("Authorization", "")
    except Exception:
        auth = ""
    return auth[7:] if auth.startswith("Bearer ") else auth


def lakebase_db_token(w: Any, *, endpoint: str | None = None) -> str:
    """A Lakebase Postgres password (U123). On the **App** the SP's workspace OAuth token is accepted
    directly (the resource binding), so ``config.oauth_token()`` works. On a **Job cluster** that
    call fails, so mint a Lakebase-specific DB credential via the postgres credentials API for the
    given ``endpoint`` path (``projects/<p>/branches/<b>/endpoints/<e>``) — the pattern from the
    cross-workspace reference; the SDK auth (= the run_as SP) authorizes the call."""
    try:
        return w.config.oauth_token().access_token
    except Exception:
        pass
    if not endpoint:
        raise RuntimeError("Lakebase endpoint path required to mint a cluster DB credential "
                           "(set GENIEFY_LAKEBASE_ENDPOINT, e.g. projects/geniefy/branches/production/endpoints/primary)")
    import uuid
    resp = w.api_client.do("POST", "/api/2.0/postgres/credentials",
                           body={"request_id": str(uuid.uuid4()), "endpoint": endpoint})
    token = resp.get("token") if isinstance(resp, dict) else None
    if not token:
        raise RuntimeError(f"postgres credentials API returned no token: {resp!r}")
    return token


def databricks_chat_query(*, profile: str | None = None) -> ChatQuery:
    """Build a ``ChatQuery`` over Databricks serving endpoints via the OpenAI-compatible API
    at ``{host}/serving-endpoints`` (U77). The SDK exposes no OpenAI-client helper, so we
    construct the ``openai`` client directly. **Session-bound credentials (U98):** a FRESH
    ``WorkspaceClient`` + OAuth token is minted **per call**, never cached across requests —
    even for the app SP. Validated against the live serving endpoint at deploy."""
    from databricks.sdk import WorkspaceClient  # lazy: hermetic import stays SDK-free
    from openai import OpenAI  # lazy

    def query(endpoint: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        w = WorkspaceClient(profile=profile) if profile else WorkspaceClient()  # fresh per call (U98)
        client = OpenAI(base_url=f"{w.config.host}/serving-endpoints",
                        api_key=workspace_bearer(w))  # cross-runtime bearer (App + Job cluster, U123)
        completion = client.chat.completions.create(model=endpoint, **dict(payload))
        return completion.model_dump()  # OpenAI ChatCompletion → the dict LLMClient parses

    return query


_INT_SQL_TYPES = {"INT", "LONG", "SHORT", "BYTE", "INTEGER", "BIGINT", "SMALLINT", "TINYINT"}
_FLOAT_SQL_TYPES = {"FLOAT", "DOUBLE", "DECIMAL", "REAL", "NUMERIC"}


def _value_caster(type_name: Any) -> Callable[[Any], Any]:
    """The Statement Execution API returns EVERY value as a string; cast numeric columns to
    int/float using the result-manifest column type so downstream arithmetic (e.g. the
    profiler's `non_null / total`) works. Non-numeric types pass through; a bad cast falls
    back to the raw string. (Caught only against a live warehouse — fakes return typed rows.)"""
    name = getattr(type_name, "value", type_name) or ""
    if name in _INT_SQL_TYPES:
        return lambda v: _try_cast(int, v)
    if name in _FLOAT_SQL_TYPES:
        return lambda v: _try_cast(float, v)
    return lambda v: v


def _try_cast(fn: Callable[[Any], Any], v: Any) -> Any:
    try:
        return fn(v)
    except (TypeError, ValueError):
        return v


def _statement_state(resp: Any) -> str | None:
    """The Statement Execution status state as a plain string (e.g. ``"SUCCEEDED"``), robust to the
    SDK enum (``.value``/``.name``), a bare string, or an absent status (→ ``None``)."""
    state = getattr(getattr(resp, "status", None), "state", None)
    return (getattr(state, "value", None) or getattr(state, "name", None)
            or (str(state) if state is not None else None))


def _await_terminal(w: Any, resp: Any, *, poll_seconds: float = 2.0, max_polls: int = 150) -> Any:
    """Poll ``get_statement`` until the statement leaves PENDING/RUNNING (U145/U146). ``execute_statement``
    waits only up to ``wait_timeout`` (the API max is 50s); a slower statement (e.g. a large-table
    profile) returns a non-terminal status, which ``_rows_from_statement_response`` would otherwise
    treat as an empty result — the same silent-[] masking as the U139 FAILED bug, for slow queries.
    Returns the terminal response (the FAILED check stays in ``_rows_from_statement_response``).

    The poll budget is ``poll_seconds × max_polls`` (default ≈ 5 min). This is a deliberate
    **hang-guard, not an SLA** (U147): warehouse statements (profiling / enumeration / lineage) normally
    finish inside the 50s synchronous wait, so this loop is the rare long-statement path; one still
    non-terminal past the budget is treated as **stuck** and raised — failing loud, never a silent ``[]``.
    A terminal or absent-status response returns at once without polling; a non-terminal response that
    carries no ``statement_id`` can't be polled, so it raises a clear error rather than ``get_statement(None)``."""
    import time

    for _ in range(max_polls):
        if _statement_state(resp) not in ("PENDING", "RUNNING"):
            return resp
        sid = getattr(resp, "statement_id", None)
        if not sid:
            raise RuntimeError("SQL statement is non-terminal but carries no statement_id — cannot poll "
                               "for completion")
        time.sleep(poll_seconds)
        resp = w.statement_execution.get_statement(sid)
    if _statement_state(resp) in ("PENDING", "RUNNING"):  # budget exhausted, still not terminal → stuck
        raise RuntimeError(
            f"SQL statement did not reach a terminal state within the poll budget "
            f"(~{int(poll_seconds * max_polls)}s = {max_polls}×{poll_seconds}s); "
            f"statement_id={getattr(resp, 'statement_id', None)}")
    return resp


def _rows_from_statement_response(resp: Any) -> list[dict[str, Any]]:
    """Map a Statement Execution response (manifest columns + ``data_array``) to row dicts,
    type-coercing numeric columns (the API returns every value as a string)."""
    # A FAILED statement must raise, not look like an empty result set (U139). The API returns
    # status.state=FAILED (e.g. a SQL parse or permission error) with result=None — extracting rows
    # blindly would yield [] and silently mask the error (the bug that hid the U138 parse error and
    # made the schema-run Job "succeed" with total=0). SUCCEEDED / absent-status are unaffected.
    state_name = _statement_state(resp)
    if state_name in ("FAILED", "CANCELED", "CLOSED"):
        status = getattr(resp, "status", None)
        err = getattr(status, "error", None)
        msg = getattr(err, "message", None) or state_name
        raise RuntimeError(f"SQL statement {state_name}: {msg}")
    manifest = getattr(resp, "manifest", None)
    schema = getattr(manifest, "schema", None) if manifest else None
    sch_cols = list(schema.columns) if schema and schema.columns else []
    names: Sequence[str] = [c.name for c in sch_cols]
    casters = [_value_caster(getattr(c, "type_name", None)) for c in sch_cols]
    result = getattr(resp, "result", None)
    data = (getattr(result, "data_array", None) or []) if result else []
    rows: list[dict[str, Any]] = []
    for row in data:
        rows.append({names[i]: (casters[i](v) if (i < len(casters) and v is not None) else v)
                     for i, v in enumerate(row)})
    return rows


def databricks_sql_execute(*, warehouse_id: str, profile: str | None = None, wait_timeout: str = "50s") -> SqlExecute:
    """Build a ``SqlExecute`` over the Statement Execution API, authenticated as the **app SP**
    (profiling/context — D48: reads are the SP). **Session-bound (U98):** a FRESH ``WorkspaceClient``
    + OAuth token is minted per call, never cached. Validated against the live warehouse at deploy."""
    from databricks.sdk import WorkspaceClient  # lazy

    def execute(sql: str) -> list[dict[str, Any]]:
        w = WorkspaceClient(profile=profile) if profile else WorkspaceClient()  # fresh per call (U98)
        resp = w.statement_execution.execute_statement(
            warehouse_id=warehouse_id, statement=sql, wait_timeout=wait_timeout
        )
        return _rows_from_statement_response(_await_terminal(w, resp))  # poll slow statements (U145/U146)

    return execute


def make_obo_sql_execute(
    *, warehouse_id: str, profile: str | None = None, host: str | None = None, wait_timeout: str = "50s"
) -> Callable[[str], SqlExecute]:
    """Return ``make(access_token) -> SqlExecute`` that runs statements on the **same warehouse**
    but authenticated with the **end-user's OBO token** (``X-Forwarded-Access-Token``, D48), so a
    UC write honors the *user's* Unity Catalog grants — the root fix for E9.

    The workspace ``host`` is the app's own workspace, resolved **once** here from the SDK config
    (or the ``host`` override) — not per request (closes the U90-audit LOW). Each call builds a
    token-authenticated ``WorkspaceClient``; the token is never persisted. **Operator note (U78):**
    the user needs ``CAN_USE`` on the warehouse and ``MODIFY`` on the target, and the app must have
    the ``sql`` user-authorization scope added in the App UI. Validated live at deploy."""
    from databricks.sdk import WorkspaceClient  # lazy

    base = WorkspaceClient(profile=profile) if profile else WorkspaceClient()
    resolved_host = host or base.config.host

    def make(access_token: str) -> SqlExecute:
        w = WorkspaceClient(host=resolved_host, token=access_token)  # OBO: run as the user

        def execute(sql: str) -> list[dict[str, Any]]:
            resp = w.statement_execution.execute_statement(
                warehouse_id=warehouse_id, statement=sql, wait_timeout=wait_timeout
            )
            return _rows_from_statement_response(_await_terminal(w, resp))

        return execute

    return make
