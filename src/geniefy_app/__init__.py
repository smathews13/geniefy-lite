"""geniefy-v3 App backend — the Databricks App that wraps the agent core (D36).

A thin FastAPI service (U5) over the UI-free ``geniefy_core`` library: it owns config
(``AppConfig``), persistence (``SessionState`` ↔ Lakebase), the concrete injected
boundaries (FMAPI serving-endpoint transport, warehouse SQL runner, MCP session
factory), the async run loop, and the REST API. ``app/main.py`` is the thin Databricks-
App entry that imports this package; the package is unit-testable via ``PYTHONPATH=src``.
"""
