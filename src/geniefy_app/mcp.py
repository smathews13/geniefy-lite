"""geniefy-v3 App backend — concrete MCP session factory (U63, D34/D39).

Connects to a configured MCP context-provider server so the `McpContextProvider`
(U39) — which the gatherer wires from `app.yaml`'s `GENIEFY_MCP_PROVIDERS` (D39) — can
`list_tools` and `call_tool`. The provider config shape (D34/U24 §3): `server_url`,
`transport`, `auth: {secret_scope, key}`, `tool_allowlist`, `query_template`,
`max_snippets`.

Hermetic boundary (D1/D36): this is the *concrete* impl, lazy-importing the MCP client +
Databricks SDK so the module/hermetic-suite never needs them; it is **integration-verified
at deploy** (needs the `mcp` client lib + a live server), exactly like the FMAPI/warehouse
factories in `providers.py`. The wiring that *uses* this factory is unit-tested with a fake.

NOTE: v1 connects per call (simple). A pooled/persistent session + token refresh is a
deferred hardening (D11), as is async batching — the MCP protocol is async, wrapped here in
a sync `McpSession` (geniefy_core.context) via `asyncio.run`.
"""
from __future__ import annotations

from typing import Any, Mapping


def databricks_mcp_session(config: Mapping[str, Any]):
    """Build an `McpSession` for a provider config (D34). Returns an object with
    `list_tools()` + `call_tool(name, args)` (the `geniefy_core.context.McpSession`
    protocol). Integration-verified."""
    return _DatabricksMcpSession(dict(config))


class _DatabricksMcpSession:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    # -- auth: a secret-scope token (D4/D5), else the in-platform OAuth token --
    def _headers(self) -> dict[str, str]:
        from databricks.sdk import WorkspaceClient  # lazy

        w = WorkspaceClient()
        auth = self.config.get("auth") or {}
        scope, key = auth.get("secret_scope"), auth.get("key")
        if scope and key:
            import base64

            raw = w.secrets.get_secret(scope=scope, key=key).value
            token = base64.b64decode(raw).decode() if raw else ""
        else:
            token = w.config.oauth_token().access_token  # managed/in-platform MCP
        return {"Authorization": f"Bearer {token}"}

    def _run(self, coro):
        import asyncio

        return asyncio.run(coro)

    async def _with_session(self, fn):
        # streamable-http MCP client (the Databricks managed-MCP transport)
        from mcp import ClientSession  # lazy
        from mcp.client.streamable_http import streamablehttp_client  # lazy

        url = self.config["server_url"]
        async with streamablehttp_client(url, headers=self._headers()) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await fn(session)

    def list_tools(self) -> list[str]:
        async def _go(session):
            resp = await session.list_tools()
            return [t.name for t in resp.tools]

        return self._run(self._with_session(_go))

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        async def _go(session):
            resp = await session.call_tool(name, dict(arguments))
            # CallToolResult.content is a list of content blocks; surface text where present.
            out = [getattr(c, "text", None) for c in getattr(resp, "content", []) or []]
            return [t for t in out if t] or getattr(resp, "structuredContent", None)

        return self._run(self._with_session(_go))
