"""Test the SPA static-file mount (U61, U23 §4).

`create_app(service, static_dir=...)` serves the built React SPA at "/" while `/api` +
`/health` keep precedence; the mount is guarded so the hermetic tests need no `vite build`.

Run: ``PYTHONPATH=src pytest tests/test_static_mount.py``
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from geniefy_app.api import SessionService, create_app
from geniefy_core.state import RunConfig


def _service():
    return SessionService(make_orchestrator=lambda tracer: None, store=None,
                          config=RunConfig(model_endpoint='m'))


def test_spa_served_at_root_and_api_takes_precedence(tmp_path):
    (tmp_path / 'index.html').write_text('<!doctype html><title>geniefy</title>')
    c = TestClient(create_app(_service(), static_dir=str(tmp_path)))
    root = c.get('/')
    assert root.status_code == 200 and 'geniefy' in root.text
    # API + health still resolve (registered before the "/" mount)
    assert c.get('/api/config').status_code == 200
    assert c.get('/health').json() == {'status': 'ok'}


def test_no_mount_without_static_dir():
    c = TestClient(create_app(_service()))
    assert c.get('/').status_code == 404  # nothing mounted at root
    assert c.get('/health').status_code == 200


def test_missing_static_dir_is_ignored_not_crash():
    c = TestClient(create_app(_service(), static_dir='/no/such/dir'))
    assert c.get('/').status_code == 404
    assert c.get('/health').status_code == 200
