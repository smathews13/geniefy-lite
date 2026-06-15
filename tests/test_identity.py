"""Tests for the request-identity layer (U91).

Covers the D48 OBO/attribution contract (LLD-amend-004 §A3): parse the Databricks Apps
`X-Forwarded-*` headers into a pure `RequestIdentity`, case-insensitively; `actor` precedence;
`has_obo`; and the local/anon default. Hermetic — plain dicts, no proxy, no SDK.

Run: ``PYTHONPATH=src pytest tests/test_identity.py``
"""
from __future__ import annotations

from geniefy_app.identity import RequestIdentity


def test_from_headers_full_set_case_insensitive():
    idy = RequestIdentity.from_headers({
        "X-Forwarded-Email": "user@x.com",
        "X-Forwarded-User": "1234",
        "X-Forwarded-Preferred-Username": "user",
        "X-Forwarded-Access-Token": "tok-abc",
        "X-Request-Id": "req-1",
    })
    assert idy.email == "user@x.com" and idy.user_id == "1234" and idy.username == "user"
    assert idy.access_token == "tok-abc" and idy.request_id == "req-1"
    assert idy.actor == "user@x.com" and idy.has_obo is True


def test_from_headers_lowercase_keys_also_parse():
    # ASGI lowercases header keys — the parser must handle the already-lowercased form.
    idy = RequestIdentity.from_headers({"x-forwarded-email": "a@b.com", "x-forwarded-access-token": "t"})
    assert idy.email == "a@b.com" and idy.has_obo is True


def test_missing_and_blank_headers_become_none():
    idy = RequestIdentity.from_headers({"X-Forwarded-Email": "   ", "X-Forwarded-User": ""})
    assert idy.email is None and idy.user_id is None
    assert idy.access_token is None and idy.has_obo is False


def test_actor_precedence_email_then_user_then_anonymous():
    assert RequestIdentity(email="e@x.com", user_id="u").actor == "e@x.com"
    assert RequestIdentity(email=None, user_id="u").actor == "u"
    assert RequestIdentity().actor == "anonymous"


def test_empty_headers_is_anonymous_no_obo():
    idy = RequestIdentity.from_headers({})
    assert idy.actor == "anonymous" and idy.has_obo is False


def test_local_default_has_identity_but_no_token():
    idy = RequestIdentity.local()
    assert idy.actor == "local-dev" and idy.has_obo is False and idy.access_token is None
