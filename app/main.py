"""geniefy-v3 — Databricks App entry (U52; supersedes the U13 scaffold stub).

Thin wrapper (D36): build the real backend from `app.yaml` env (AppConfig, D33) and expose
the FastAPI app the App runtime serves (`app.yaml`: `uvicorn main:app`). The backend logic
lives in the unit-tested `geniefy_app` package; this module just wires + serves it. The
built React frontend (U23) gets mounted here as static files once that unit lands.

Imported only in the App runtime (env present); the hermetic tests exercise
`geniefy_app.api.create_app` with fakes, not this entry.
"""
import os

from geniefy_app.api import build_service, create_app
from geniefy_app.config import AppConfig

# Built at import (uvicorn imports `app`); AppConfig.from_env fails fast on misconfig (D33),
# build_service opens the live FMAPI/warehouse/Lakebase boundaries (integration-verified).
# The built React SPA (vite build → app/static, U23 §4) is served at "/" alongside /api.
_STATIC = os.path.join(os.path.dirname(__file__), 'static')
app = create_app(build_service(AppConfig.from_env()), static_dir=_STATIC)
