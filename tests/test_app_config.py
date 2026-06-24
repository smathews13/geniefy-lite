"""Tests for the App backend config loader (U44).

Covers U43 against D33 / NFR-A / U24 §1: env → typed `AppConfig` + `RunConfig`, the
fail-fast (all problems at once), type coercion, MCP-provider JSON, and defaults.
Hermetic — `from_env` takes a dict.

Run: ``PYTHONPATH=src pytest tests/test_app_config.py``
"""
from __future__ import annotations

import json

import pytest

from geniefy_app.config import REQUIRED, AppConfig, ConfigError
from geniefy_core.state import SessionMode


def _full_env(**overrides) -> dict:
    env = {
        "GENIEFY_MODEL_ENDPOINT": "databricks-claude-sonnet-4-6",
        "GENIEFY_WAREHOUSE_ID": "abcd1234ef567890",
        "GENIEFY_PG_HOST": "ep-example.database.us-east-1.cloud.databricks.com",
        "GENIEFY_PG_DATABASE": "geniefy",
    }
    env.update(overrides)
    return env


# ─────────────────────────────────────────────────────────────────────────────
# Happy path + defaults
# ─────────────────────────────────────────────────────────────────────────────
def test_minimal_valid_env_applies_defaults():
    cfg = AppConfig.from_env(_full_env())
    assert cfg.model_endpoint == "databricks-claude-sonnet-4-6"
    assert cfg.warehouse_id == "abcd1234ef567890"
    assert cfg.pg_database == "geniefy" and cfg.pg_schema == "geniefy" and cfg.secret_scope == "geniefy"
    rc = cfg.run_config
    assert rc.mode == SessionMode.INTERACTIVE and rc.keep_threshold == 0.75
    assert rc.profile_batch_size == 50 and rc.reason_batch_size == 25 and rc.context_token_budget == 4000
    assert rc.template_id == "default" and rc.sample_mode == "auto"
    assert rc.max_input_tokens_per_call is None and rc.summarize_over_budget is True
    # LLM + per-phase tunables default when unset (U81/D43/D47)
    assert rc.max_retries == 5 and rc.backoff_base == 0.5 and rc.llm_temperature == 0.0
    assert rc.default_max_tokens == 4096
    assert rc.reason_table_max_tokens == 20000 and rc.reason_column_max_tokens == 2000
    assert cfg.mcp_providers == []
    assert cfg.lakebase_endpoint is None   # unset on the App runtime (U125)


def test_lakebase_endpoint_parsed_when_set():
    # off-App (Job cluster) the endpoint PATH is supplied so the cluster can mint a Lakebase cred (U123/U125)
    cfg = AppConfig.from_env(_full_env(GENIEFY_LAKEBASE_ENDPOINT="projects/geniefy/branches/dev/endpoints/primary"))
    assert cfg.lakebase_endpoint == "projects/geniefy/branches/dev/endpoints/primary"
    assert AppConfig.from_env(_full_env(GENIEFY_LAKEBASE_ENDPOINT="   ")).lakebase_endpoint is None  # blank → None
    # surrounding whitespace on a real value is trimmed (U126)
    assert AppConfig.from_env(_full_env(GENIEFY_LAKEBASE_ENDPOINT="  p/branches/dev/endpoints/primary  ")
                              ).lakebase_endpoint == "p/branches/dev/endpoints/primary"


def test_full_env_parses_all_types():
    env = _full_env(
        GENIEFY_MODE="batch", GENIEFY_PG_SCHEMA="gx", GENIEFY_SECRET_SCOPE="sc",
        GENIEFY_KEEP_THRESHOLD="0.9", GENIEFY_PROFILE_BATCH_SIZE="40",
        GENIEFY_REASON_BATCH_SIZE="20", GENIEFY_CONTEXT_TOKEN_BUDGET="8000",
        GENIEFY_MAX_INPUT_TOKENS_PER_CALL="12000", GENIEFY_SUMMARY_TARGET_TOKENS="1500",
        GENIEFY_SUMMARIZE_OVER_BUDGET="false", GENIEFY_SAMPLE_MODE="full",
        GENIEFY_TEMPLATE_ID="strict", GENIEFY_ENABLED_PROVIDERS="uc_lineage, query_history , glean",
        GENIEFY_MAX_RETRIES="8", GENIEFY_BACKOFF_BASE="1.5", GENIEFY_LLM_TEMPERATURE="0.3",
        GENIEFY_MAX_TOKENS="8192", GENIEFY_REASON_TABLE_MAX_TOKENS="30000",
        GENIEFY_REASON_COLUMN_MAX_TOKENS="3000",
    )
    cfg = AppConfig.from_env(env)
    rc = cfg.run_config
    assert rc.mode == SessionMode.BATCH and cfg.pg_schema == "gx" and cfg.secret_scope == "sc"
    assert rc.keep_threshold == 0.9 and rc.profile_batch_size == 40 and rc.reason_batch_size == 20
    assert rc.context_token_budget == 8000 and rc.max_input_tokens_per_call == 12000
    assert rc.summary_target_tokens == 1500 and rc.summarize_over_budget is False
    assert rc.sample_mode == "full" and rc.template_id == "strict"
    assert rc.enabled_providers == ["uc_lineage", "query_history", "glean"]  # trimmed
    # LLM + per-phase tunables read from app.yaml env (U81/D43/D47)
    assert rc.max_retries == 8 and rc.backoff_base == 1.5 and rc.llm_temperature == 0.3
    assert rc.default_max_tokens == 8192
    assert rc.reason_table_max_tokens == 30000 and rc.reason_column_max_tokens == 3000


# ─────────────────────────────────────────────────────────────────────────────
# Fail-fast on missing required
# ─────────────────────────────────────────────────────────────────────────────
def test_missing_all_required_lists_them_all():
    with pytest.raises(ConfigError) as ei:
        AppConfig.from_env({})
    msg = str(ei.value)
    for k in REQUIRED:
        assert k in msg


def test_missing_one_required():
    env = _full_env()
    del env["GENIEFY_PG_HOST"]
    with pytest.raises(ConfigError) as ei:
        AppConfig.from_env(env)
    assert "GENIEFY_PG_HOST" in str(ei.value)


def test_blank_required_treated_as_missing():
    with pytest.raises(ConfigError):
        AppConfig.from_env(_full_env(GENIEFY_MODEL_ENDPOINT="   "))


# ─────────────────────────────────────────────────────────────────────────────
# Type / value errors are collected (not just the first)
# ─────────────────────────────────────────────────────────────────────────────
def test_invalid_number_reported():
    with pytest.raises(ConfigError) as ei:
        AppConfig.from_env(_full_env(GENIEFY_KEEP_THRESHOLD="high"))
    assert "GENIEFY_KEEP_THRESHOLD" in str(ei.value)


def test_invalid_mode_reported():
    with pytest.raises(ConfigError) as ei:
        AppConfig.from_env(_full_env(GENIEFY_MODE="turbo"))
    assert "GENIEFY_MODE" in str(ei.value)


@pytest.mark.parametrize("var", ["GENIEFY_MAX_INPUT_TOKENS_PER_CALL", "GENIEFY_SUMMARY_TARGET_TOKENS"])
def test_invalid_optional_token_var_reported(var):
    # the optional token-budget vars also fail-fast on a non-int via the shared num() path
    # (U44 audit LOW: that path had no bad-value negative — only the always-on numbers did).
    with pytest.raises(ConfigError) as ei:
        AppConfig.from_env(_full_env(**{var: "lots"}))
    assert var in str(ei.value) and "not a valid int" in str(ei.value)


def test_multiple_errors_collected_at_once():
    env = _full_env(GENIEFY_KEEP_THRESHOLD="x", GENIEFY_PROFILE_BATCH_SIZE="y")
    del env["GENIEFY_WAREHOUSE_ID"]
    with pytest.raises(ConfigError) as ei:
        AppConfig.from_env(env)
    msg = str(ei.value)
    assert "GENIEFY_WAREHOUSE_ID" in msg and "GENIEFY_KEEP_THRESHOLD" in msg and "GENIEFY_PROFILE_BATCH_SIZE" in msg


# ─────────────────────────────────────────────────────────────────────────────
# MCP providers (D34)
# ─────────────────────────────────────────────────────────────────────────────
def test_mcp_providers_parsed():
    providers = [{"name": "glean", "tool_allowlist": ["search"]},
                 {"name": "confluence", "tool_allowlist": ["search"]}]
    cfg = AppConfig.from_env(_full_env(GENIEFY_MCP_PROVIDERS=json.dumps(providers)))
    assert [p["name"] for p in cfg.mcp_providers] == ["glean", "confluence"]


def test_mcp_providers_bad_json_reported():
    with pytest.raises(ConfigError) as ei:
        AppConfig.from_env(_full_env(GENIEFY_MCP_PROVIDERS="{not json"))
    assert "GENIEFY_MCP_PROVIDERS" in str(ei.value)


def test_mcp_providers_non_list_rejected():
    with pytest.raises(ConfigError) as ei:
        AppConfig.from_env(_full_env(GENIEFY_MCP_PROVIDERS='{"name": "glean"}'))
    assert "JSON array" in str(ei.value)
