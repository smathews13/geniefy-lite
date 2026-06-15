"""geniefy-v3 App backend — configuration (U43).

``app.yaml`` is the single runtime config surface (D33 / NFR-A, U24 §1): every knob is a
``GENIEFY_*`` environment variable fed by bundle variables + resource bindings. The
backend loads them **once at startup** into a typed ``AppConfig`` (infra coordinates +
a ``RunConfig`` for the agent core + the MCP-provider registrations) and **fails fast**
with one clear error listing every missing/invalid required var — so a misconfigured
deploy never limps along.

Pure + hermetic: ``from_env`` takes a mapping (defaults to ``os.environ``); no I/O.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from geniefy_core.state import RunConfig, SessionMode

# Required env vars — absence is a fail-fast (D33: no limping along on a bad deploy).
REQUIRED = ("GENIEFY_MODEL_ENDPOINT", "GENIEFY_PG_HOST", "GENIEFY_PG_DATABASE", "GENIEFY_WAREHOUSE_ID")


class ConfigError(ValueError):
    """A missing/invalid app configuration. Carries every problem, not just the first."""


@dataclass
class AppConfig:
    """Resolved backend configuration (D33). Infra coordinates + the agent-core
    ``RunConfig`` + MCP-provider registrations (D34)."""

    model_endpoint: str
    warehouse_id: str
    pg_host: str
    pg_database: str
    pg_schema: str = "geniefy"
    secret_scope: str = "geniefy"
    # Lakebase endpoint PATH (projects/<p>/branches/<b>/endpoints/<e>) — needed only OFF the App
    # runtime (e.g. a Job cluster) to mint a Lakebase credential when oauth_token() is unavailable
    # (U123/U125). On the App it's unset (the binding's oauth token works); the Job passes it via env.
    lakebase_endpoint: str | None = None
    run_config: RunConfig = field(default_factory=lambda: RunConfig(model_endpoint=""))
    mcp_providers: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AppConfig":
        env = os.environ if env is None else env
        errors: list[str] = []

        missing = [k for k in REQUIRED if not (env.get(k) or "").strip()]
        if missing:
            errors.append("missing required env: " + ", ".join(missing))

        def num(key: str, cast, default):
            raw = env.get(key)
            if raw is None or raw == "":
                return default
            try:
                return cast(raw)
            except (TypeError, ValueError):
                errors.append(f"{key}={raw!r} is not a valid {cast.__name__}")
                return default

        keep_threshold = num("GENIEFY_KEEP_THRESHOLD", float, 0.75)
        profile_batch = num("GENIEFY_PROFILE_BATCH_SIZE", int, 50)
        reason_batch = num("GENIEFY_REASON_BATCH_SIZE", int, 25)
        ctx_budget = num("GENIEFY_CONTEXT_TOKEN_BUDGET", int, 4000)
        max_in = num("GENIEFY_MAX_INPUT_TOKENS_PER_CALL", int, None) if env.get("GENIEFY_MAX_INPUT_TOKENS_PER_CALL") else None
        summ_target = num("GENIEFY_SUMMARY_TARGET_TOKENS", int, None) if env.get("GENIEFY_SUMMARY_TARGET_TOKENS") else None
        # LLM + per-phase comment tunables — app.yaml-configurable, no code change (NFR-A/D33, U81).
        max_retries = num("GENIEFY_MAX_RETRIES", int, 5)
        backoff_base = num("GENIEFY_BACKOFF_BASE", float, 0.5)
        llm_temperature = num("GENIEFY_LLM_TEMPERATURE", float, 0.0)
        default_max_tokens = num("GENIEFY_MAX_TOKENS", int, 4096)
        reason_table_max_tokens = num("GENIEFY_REASON_TABLE_MAX_TOKENS", int, 20000)
        reason_column_max_tokens = num("GENIEFY_REASON_COLUMN_MAX_TOKENS", int, 2000)

        mode_raw = (env.get("GENIEFY_MODE") or "interactive").strip()
        try:
            mode = SessionMode(mode_raw)
        except ValueError:
            errors.append(f"GENIEFY_MODE={mode_raw!r} must be one of interactive|batch")
            mode = SessionMode.INTERACTIVE

        mcp_providers: list[dict[str, Any]] = []
        raw_mcp = env.get("GENIEFY_MCP_PROVIDERS")
        if raw_mcp:
            try:
                parsed = json.loads(raw_mcp)
                if not isinstance(parsed, list):
                    errors.append("GENIEFY_MCP_PROVIDERS must be a JSON array of provider configs")
                else:
                    mcp_providers = parsed
            except json.JSONDecodeError as exc:
                errors.append(f"GENIEFY_MCP_PROVIDERS is not valid JSON: {exc}")

        if errors:
            raise ConfigError("invalid app configuration:\n  - " + "\n  - ".join(errors))

        run_config = RunConfig(
            model_endpoint=env["GENIEFY_MODEL_ENDPOINT"].strip(),
            mode=mode,
            template_id=(env.get("GENIEFY_TEMPLATE_ID") or "default").strip(),
            sample_mode=(env.get("GENIEFY_SAMPLE_MODE") or "auto").strip(),
            keep_threshold=keep_threshold,
            profile_batch_size=profile_batch,
            reason_batch_size=reason_batch,
            context_token_budget=ctx_budget,
            enabled_providers=_csv(env.get("GENIEFY_ENABLED_PROVIDERS")),
            max_input_tokens_per_call=max_in,
            summarize_over_budget=_bool(env.get("GENIEFY_SUMMARIZE_OVER_BUDGET"), default=True),
            summary_target_tokens=summ_target,
            max_retries=max_retries,
            backoff_base=backoff_base,
            llm_temperature=llm_temperature,
            default_max_tokens=default_max_tokens,
            reason_table_max_tokens=reason_table_max_tokens,
            reason_column_max_tokens=reason_column_max_tokens,
        )
        return cls(
            model_endpoint=run_config.model_endpoint,
            warehouse_id=env["GENIEFY_WAREHOUSE_ID"].strip(),
            pg_host=env["GENIEFY_PG_HOST"].strip(),
            pg_database=env["GENIEFY_PG_DATABASE"].strip(),
            pg_schema=(env.get("GENIEFY_PG_SCHEMA") or "geniefy").strip(),
            secret_scope=(env.get("GENIEFY_SECRET_SCOPE") or "geniefy").strip(),
            lakebase_endpoint=(env.get("GENIEFY_LAKEBASE_ENDPOINT") or "").strip() or None,
            run_config=run_config,
            mcp_providers=mcp_providers,
        )


def _csv(raw: str | None) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()] if raw else []


def _bool(raw: str | None, *, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")
