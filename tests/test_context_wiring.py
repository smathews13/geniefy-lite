"""Tests for MCP-provider wiring from app.yaml config (U63, D39/NFR-C).

Proves the path the human asked for: app.yaml `GENIEFY_MCP_PROVIDERS` → `AppConfig` →
`build_context_providers` → the `ContextGatherer` actually connects to + queries the
configured MCP server. Hermetic — the SQL runner + MCP session factory are fakes.

Run: ``PYTHONPATH=src pytest tests/test_context_wiring.py``
"""
from __future__ import annotations

import json

from geniefy_app.api import build_context_providers
from geniefy_app.config import AppConfig
from geniefy_core.context import ContextGatherer, McpContextProvider


def fake_sql(_sql: str):
    return []  # built-ins return no rows → no built-in snippets (keeps the test focused on MCP)


class FakeSession:
    def list_tools(self):
        return ['search', 'admin_delete']

    def call_tool(self, name, arguments):
        return {'results': [f'mcp snippet via {name} for {arguments.get("query", "")}']}


def fake_factory(_config):
    return FakeSession()


def _full_env(**over):
    env = {
        'GENIEFY_MODEL_ENDPOINT': 'databricks-claude-sonnet-4-6',
        'GENIEFY_WAREHOUSE_ID': 'wh',
        'GENIEFY_PG_HOST': 'host',
        'GENIEFY_PG_DATABASE': 'geniefy',
    }
    env.update(over)
    return env


# ─────────────────────────────────────────────────────────────────────────────
# build_context_providers
# ─────────────────────────────────────────────────────────────────────────────
def test_builtins_plus_enabled_mcp():
    providers = build_context_providers(
        fake_sql,
        [{'name': 'glean', 'tool_allowlist': ['search']}, {'name': 'off', 'enabled': False}],
        fake_factory,
    )
    names = [getattr(p, 'name', type(p).__name__) for p in providers]
    assert names[:2] == ['uc_lineage', 'query_history']         # built-ins always present, first
    assert 'glean' in names and 'off' not in names              # enabled MCP wired; disabled skipped
    assert sum(isinstance(p, McpContextProvider) for p in providers) == 1


def test_no_mcp_when_none_configured():
    assert len(build_context_providers(fake_sql, [], fake_factory)) == 2  # just built-ins


# ─────────────────────────────────────────────────────────────────────────────
# end-to-end: the gatherer discovers + queries the configured MCP server
# ─────────────────────────────────────────────────────────────────────────────
def test_gatherer_uses_configured_mcp_provider():
    providers = build_context_providers(
        fake_sql,
        [{'name': 'glean', 'tool_allowlist': ['search'], 'query_template': 'q {full_name}'}],
        fake_factory,
    )
    res = ContextGatherer(providers).gather('samples.tpch.orders', ['o_custkey'])
    assert any(s.source == 'glean' and 'mcp snippet' in s.text for s in res.snippets)
    assert res.warnings == []  # no degradation — the provider connected + returned


def test_app_yaml_config_flows_to_providers():
    # app.yaml GENIEFY_MCP_PROVIDERS → AppConfig.mcp_providers → build_context_providers
    env = _full_env(
        GENIEFY_MCP_PROVIDERS=json.dumps([{'name': 'confluence', 'tool_allowlist': ['search']}])
    )
    cfg = AppConfig.from_env(env)
    providers = build_context_providers(fake_sql, cfg.mcp_providers, fake_factory)
    assert any(getattr(p, 'name', '') == 'confluence' for p in providers)
