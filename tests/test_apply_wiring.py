"""Test the /apply endpoint delegation (U54).

When a `SessionService` has an `applier` wired (as `build_service` now does), the
`POST /api/sessions/{id}/apply` route delegates to it; without one it returns 501.
(The real `build_service` Applier construction is integration-verified.)

Run: ``PYTHONPATH=src pytest tests/test_apply_wiring.py``
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from geniefy_app.api import SessionService, create_app
from geniefy_core.state import RunConfig


class FakeApplier:
    def __init__(self):
        self.calls = []

    def apply(self, session_id, *, created_by, access_token=None):
        self.calls.append((session_id, created_by, access_token))
        return {"session_id": session_id, "applied": 2, "results": []}


def _service(applier=None):
    return SessionService(make_orchestrator=lambda tracer: None, store=None,
                          config=RunConfig(model_endpoint="m"), applier=applier)


def test_apply_forwards_user_actor_and_obo_token_from_headers():
    # Apply runs as the END USER (D48/U85): actor from X-Forwarded-Email + the OBO token from
    # X-Forwarded-Access-Token are both forwarded to the Applier (NOT the SP).
    applier = FakeApplier()
    c = TestClient(create_app(_service(applier)))
    r = c.post("/api/sessions/sid1/apply",
               headers={"X-Forwarded-Email": "user@x.com", "X-Forwarded-Access-Token": "tok-xyz"})
    assert r.status_code == 200 and r.json()["applied"] == 2
    assert applier.calls == [("sid1", "user@x.com", "tok-xyz")]


def test_apply_actor_anonymous_no_token_without_forwarded_identity():
    # No Apps proxy (local/tests) → no forwarded identity → "anonymous", no token (never the SP).
    applier = FakeApplier()
    c = TestClient(create_app(_service(applier)))
    c.post("/api/sessions/sid1/apply")
    assert applier.calls == [("sid1", "anonymous", None)]


def test_apply_501_when_not_wired():
    c = TestClient(create_app(_service(applier=None)))
    assert c.post("/api/sessions/sid1/apply").status_code == 501
