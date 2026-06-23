"""Tests for the App backend concrete boundaries (U50).

Covers U49's adapters against the core protocols they implement: ``FmapiChatTransport``
builds the right payload + passes the response through (and works inside a real
``LLMClient``); ``WarehouseSqlRunner`` maps rows→dicts (and works inside a real
``LineageProvider``); both propagate errors so the core's retry/degrade logic owns them.
Hermetic — the low-level call is a fake. The ``*_from_databricks`` factories need the
live SDK/warehouse and are integration-verified, not unit-tested here.

Run: ``PYTHONPATH=src pytest tests/test_providers.py``
"""
from __future__ import annotations

import pytest

from geniefy_app.providers import FmapiChatTransport, WarehouseSqlRunner
from geniefy_core.context import LineageProvider
from geniefy_core.llm import ChatMessage, HeuristicTokenCounter, LLMClient


def _ok_completion(content: str) -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


# ─────────────────────────────────────────────────────────────────────────────
# FmapiChatTransport
# ─────────────────────────────────────────────────────────────────────────────
def test_chat_transport_builds_payload_and_passes_response():
    captured = {}

    def query(endpoint, payload):
        captured["endpoint"], captured["payload"] = endpoint, payload
        return _ok_completion("hi")

    r = FmapiChatTransport(query).send(
        [{"role": "user", "content": "x"}], model="databricks-claude-sonnet-4-6",
        max_tokens=256, temperature=0.0)
    assert captured["endpoint"] == "databricks-claude-sonnet-4-6"
    assert captured["payload"]["messages"] == [{"role": "user", "content": "x"}]
    assert captured["payload"]["max_tokens"] == 256 and captured["payload"]["temperature"] == 0.0
    assert "response_format" not in captured["payload"]  # omitted when not requested
    assert r["choices"][0]["message"]["content"] == "hi"


def test_chat_transport_does_not_forward_response_format():
    # Databricks-served Claude (FMAPI) REJECTS response_format=json_object, so the transport
    # accepts the arg (ChatTransport protocol) but must NOT put it in the payload (U77).
    captured = {}

    def query(endpoint, payload):
        captured["payload"] = payload
        return _ok_completion("{}")

    FmapiChatTransport(query).send([], model="m", max_tokens=10, temperature=0.0,
                                   response_format={"type": "json_object"})
    assert "response_format" not in captured["payload"]


def test_chat_transport_satisfies_llmclient():
    # the adapter IS a valid ChatTransport: a real LLMClient drives JSON through it
    def query(endpoint, payload):
        return _ok_completion('{"definition": "the order key"}')

    client = LLMClient(FmapiChatTransport(query), model_endpoint="databricks-claude-sonnet-4-6",
                       counter=HeuristicTokenCounter(), sleep=lambda s: None)
    obj, resp = client.complete_json([ChatMessage("user", "draft a comment")])
    assert obj == {"definition": "the order key"} and resp.finish_reason == "stop"


def test_chat_transport_propagates_errors():
    def query(endpoint, payload):
        raise RuntimeError("endpoint down")

    with pytest.raises(RuntimeError):  # LLMClient layer turns this into retries/LLMError
        FmapiChatTransport(query).send([], model="m", max_tokens=10, temperature=0.0)


# ─────────────────────────────────────────────────────────────────────────────
# WarehouseSqlRunner
# ─────────────────────────────────────────────────────────────────────────────
def test_sql_runner_maps_rows_to_dicts():
    runner = WarehouseSqlRunner(lambda sql: [{"a": 1}, {"a": 2}])
    assert runner("select 1") == [{"a": 1}, {"a": 2}]


def test_sql_runner_handles_empty_and_none():
    assert WarehouseSqlRunner(lambda sql: [])("x") == []
    assert WarehouseSqlRunner(lambda sql: None)("x") == []


def test_sql_runner_satisfies_lineage_provider():
    # the adapter IS a valid SqlRunner: a real LineageProvider produces snippets through it
    def execute(sql):
        if "SELECT DISTINCT source_table_full_name" in sql:
            return [{"source_table_full_name": "c.s.upstream"}]
        return []

    snips = LineageProvider(WarehouseSqlRunner(execute)).gather("c.s.orders", [])
    assert any("Upstream" in s.text and "c.s.upstream" in s.text for s in snips)


def test_sql_runner_propagates_errors():
    def execute(sql):
        raise RuntimeError("warehouse down")

    with pytest.raises(RuntimeError):  # context gatherer degrades; profiler raises ProfileError
        WarehouseSqlRunner(execute)("select 1")


# ─────────────────────────────────────────────────────────────────────────────
# Lazy SDK factories (U49 §"Real Databricks wiring") — import stays SDK-free
# ─────────────────────────────────────────────────────────────────────────────
def test_lazy_factories_import_sdk_only_when_called():
    # The *_from_databricks factories lazy-import the SDK *inside* the function, so importing
    # this module needs no SDK (proven by the import at the top succeeding here). In an
    # SDK-less env, CALLING a factory raises ModuleNotFoundError — i.e. the import is genuinely
    # deferred, not eager (U50 audit LOW: this contract had no named test).
    import importlib.util

    if importlib.util.find_spec("databricks") is not None:
        pytest.skip("databricks SDK present — the lazy-import-raises contract only manifests SDK-free")
    import geniefy_app.providers as P  # the module-level import already succeeded SDK-free

    with pytest.raises(ModuleNotFoundError):
        P.databricks_chat_query()
    with pytest.raises(ModuleNotFoundError):
        P.databricks_sql_execute(warehouse_id="wh")


# ─────────────────────────────────────────────────────────────────────────────
# Session-bound credentials: a fresh WorkspaceClient (+ token) per call (U98)
# ─────────────────────────────────────────────────────────────────────────────
def test_sql_execute_mints_fresh_client_per_call(monkeypatch):
    # U98: the SP credential must be session/request-bound — a NEW WorkspaceClient (→ fresh OAuth
    # token) per call, never cached. Mock the SDK + count instantiations.
    import sys
    import types

    built: list = []

    class _FakeWC:
        def __init__(self, **kw):
            built.append(kw)
            self.statement_execution = types.SimpleNamespace(
                execute_statement=lambda **k: types.SimpleNamespace(manifest=None, result=None))

    fake_sdk = types.ModuleType("databricks.sdk")
    fake_sdk.WorkspaceClient = _FakeWC
    fake_pkg = types.ModuleType("databricks")
    fake_pkg.sdk = fake_sdk
    monkeypatch.setitem(sys.modules, "databricks", fake_pkg)
    monkeypatch.setitem(sys.modules, "databricks.sdk", fake_sdk)

    from geniefy_app.providers import databricks_sql_execute

    execute = databricks_sql_execute(warehouse_id="wh")
    assert built == []                 # nothing minted at factory-build time
    execute("SELECT 1")
    execute("SELECT 2")
    assert len(built) == 2             # one fresh client (+ token) per call — not cached (U98)


def test_rows_from_statement_response_raises_on_failed_statement():
    # U139: a FAILED statement (SQL parse/permission error, result=None) must RAISE — not silently
    # return [] (the bug that masked U138's bad enumerate query → Job "succeeded" with total=0).
    import types

    from geniefy_app.providers import _rows_from_statement_response

    failed = types.SimpleNamespace(
        manifest=None, result=None,
        status=types.SimpleNamespace(
            state=types.SimpleNamespace(value="FAILED"),
            error=types.SimpleNamespace(message="[PARSE_SYNTAX_ERROR] near 'ESCAPE'")))
    with pytest.raises(RuntimeError, match="FAILED"):
        _rows_from_statement_response(failed)
    # a SUCCEEDED statement with no rows is a legitimate empty result (returns [])
    ok = types.SimpleNamespace(manifest=None, result=None,
                               status=types.SimpleNamespace(state=types.SimpleNamespace(value="SUCCEEDED")))
    assert _rows_from_statement_response(ok) == []
    # absent status (hermetic fakes that don't model status) → no raise, empty rows
    assert _rows_from_statement_response(types.SimpleNamespace(manifest=None, result=None)) == []


def test_await_terminal_polls_running_until_terminal():
    # U145: a PENDING/RUNNING statement is polled via get_statement until terminal — not treated as []
    import types

    from geniefy_app.providers import _await_terminal, _rows_from_statement_response

    running = types.SimpleNamespace(statement_id="s1", status=types.SimpleNamespace(state="RUNNING"))
    done = types.SimpleNamespace(statement_id="s1", manifest=None, result=None,
                                 status=types.SimpleNamespace(state="SUCCEEDED"))
    calls = {"n": 0}

    def get_statement(sid):
        calls["n"] += 1
        return done

    w = types.SimpleNamespace(statement_execution=types.SimpleNamespace(get_statement=get_statement))
    out = _await_terminal(w, running, poll_seconds=0, max_polls=5)
    assert calls["n"] == 1                                # polled once, to terminal
    assert _rows_from_statement_response(out) == []       # SUCCEEDED → rows (empty here), not masked


def test_await_terminal_gives_up_on_stuck_statement():
    # U145: a statement stuck non-terminal raises after max_polls — never an unbounded loop or silent []
    import types

    from geniefy_app.providers import _await_terminal

    running = types.SimpleNamespace(statement_id="s2", status=types.SimpleNamespace(state="RUNNING"))
    w = types.SimpleNamespace(
        statement_execution=types.SimpleNamespace(get_statement=lambda sid: running))
    with pytest.raises(RuntimeError, match="terminal"):
        _await_terminal(w, running, poll_seconds=0, max_polls=3)


def test_await_terminal_noop_when_already_terminal_or_no_status():
    # already-terminal or absent-status (hermetic fakes) → returned at once; get_statement NOT called
    import types

    from geniefy_app.providers import _await_terminal

    def boom(sid):
        raise AssertionError("get_statement must not be called for a terminal/absent-status response")

    w = types.SimpleNamespace(statement_execution=types.SimpleNamespace(get_statement=boom))
    succeeded = types.SimpleNamespace(status=types.SimpleNamespace(state="SUCCEEDED"))
    assert _await_terminal(w, succeeded, poll_seconds=0) is succeeded
    absent = types.SimpleNamespace(manifest=None, result=None)  # no status — like the mint-per-call fake
    assert _await_terminal(w, absent, poll_seconds=0) is absent


def test_await_terminal_raises_on_nonterminal_without_statement_id():
    # U147: a PENDING/RUNNING response that carries no statement_id can't be polled — raise a clear
    # error rather than calling get_statement(None).
    import types

    from geniefy_app.providers import _await_terminal

    def boom(sid):
        raise AssertionError("get_statement(None) must not be called")

    w = types.SimpleNamespace(statement_execution=types.SimpleNamespace(get_statement=boom))
    stuck = types.SimpleNamespace(statement_id=None, status=types.SimpleNamespace(state="RUNNING"))
    with pytest.raises(RuntimeError, match="statement_id"):
        _await_terminal(w, stuck, poll_seconds=0, max_polls=5)


def test_sql_execute_polls_running_on_the_sp_runner(monkeypatch):
    # U146 (regression guard for the U145 HIGH): databricks_sql_execute — the SP READ runner used by
    # enumeration/profiling/lineage — must poll a RUNNING statement to terminal, not return []. The
    # U145 fix wired only the OBO runner; this test fails if the SP runner loses its _await_terminal.
    import sys
    import time as _time
    import types

    monkeypatch.setattr(_time, "sleep", lambda *a, **k: None)  # no real sleep on the poll path

    running = types.SimpleNamespace(statement_id="s9", status=types.SimpleNamespace(state="RUNNING"))
    done = types.SimpleNamespace(statement_id="s9", manifest=None, result=None,
                                 status=types.SimpleNamespace(state="SUCCEEDED"))
    gets = {"n": 0}

    def get_statement(sid):
        gets["n"] += 1
        return done

    class _FakeWC:
        def __init__(self, **kw):
            self.statement_execution = types.SimpleNamespace(
                execute_statement=lambda **k: running, get_statement=get_statement)

    fake_sdk = types.ModuleType("databricks.sdk")
    fake_sdk.WorkspaceClient = _FakeWC
    fake_pkg = types.ModuleType("databricks")
    fake_pkg.sdk = fake_sdk
    monkeypatch.setitem(sys.modules, "databricks", fake_pkg)
    monkeypatch.setitem(sys.modules, "databricks.sdk", fake_sdk)

    from geniefy_app.providers import databricks_sql_execute

    rows = databricks_sql_execute(warehouse_id="wh")("SELECT 1")
    assert gets["n"] == 1     # the SP runner polled the RUNNING statement to a terminal state
    assert rows == []         # terminal SUCCEEDED mapped (empty here) — NOT a silent [] from RUNNING


# ─────────────────────────────────────────────────────────────────────────────
# Cross-runtime auth helpers (U123) — App (oauth_token) vs Job cluster (authenticate / mint)
# ─────────────────────────────────────────────────────────────────────────────
class _Tok:
    def __init__(self, t):
        self.access_token = t


class _FakeConfig:
    def __init__(self, oauth=None, auth_headers=None, host="https://ws"):
        self._oauth = oauth          # _Tok on the App; None ⇒ oauth_token() raises (the cluster case)
        self._auth_headers = auth_headers or {}
        self.host = host

    def oauth_token(self):
        if self._oauth is None:      # cluster: the runtime credential strategy has no oauth_token
            raise AttributeError("'function' object has no attribute 'oauth_token'")
        return self._oauth

    def authenticate(self):
        return self._auth_headers


class _FakeApiClient:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def do(self, method, path, body=None):
        self.calls.append((method, path, body))
        return self.resp


class _FakeW:
    def __init__(self, config, api_client=None):
        self.config = config
        self.api_client = api_client


def test_workspace_bearer_prefers_oauth_token():
    from geniefy_app.providers import workspace_bearer
    assert workspace_bearer(_FakeW(_FakeConfig(oauth=_Tok("app-tok")))) == "app-tok"


def test_workspace_bearer_falls_back_to_authenticate_on_cluster():
    from geniefy_app.providers import workspace_bearer
    w = _FakeW(_FakeConfig(oauth=None, auth_headers={"Authorization": "Bearer cluster-tok"}))
    assert workspace_bearer(w) == "cluster-tok"   # oauth_token() raised → authenticate() header used


def test_lakebase_db_token_prefers_oauth_token_on_app():
    from geniefy_app.providers import lakebase_db_token
    api = _FakeApiClient({"token": "unused"})
    w = _FakeW(_FakeConfig(oauth=_Tok("app-db-tok")), api_client=api)
    assert lakebase_db_token(w, endpoint="projects/x/branches/dev/endpoints/primary") == "app-db-tok"
    assert api.calls == []                        # App path never mints via the API


def test_lakebase_db_token_mints_via_credentials_api_on_cluster():
    from geniefy_app.providers import lakebase_db_token
    api = _FakeApiClient({"token": "minted-tok"})
    w = _FakeW(_FakeConfig(oauth=None), api_client=api)
    ep = "projects/geniefy/branches/dev/endpoints/primary"
    assert lakebase_db_token(w, endpoint=ep) == "minted-tok"
    method, path, body = api.calls[0]
    assert method == "POST" and path == "/api/2.0/postgres/credentials"
    assert body["endpoint"] == ep and "request_id" in body


def test_lakebase_db_token_requires_endpoint_on_cluster():
    from geniefy_app.providers import lakebase_db_token
    with pytest.raises(RuntimeError):
        lakebase_db_token(_FakeW(_FakeConfig(oauth=None), api_client=_FakeApiClient({})), endpoint=None)
