"""geniefy-v3 App backend — request identity + on-behalf-of (OBO) context (U91, D48).

Databricks Apps inject the end-user identity (and, opt-in, the user's OAuth token) as
reverse-proxy request headers (LLD-amend-004 §A1). This turns those headers into a pure,
hermetic ``RequestIdentity``.

Per the **D48 refinement**, the end-user identity is consumed for **one thing only — the UC
apply write-path** (the OBO token for ``COMMENT ON``/``ALTER COLUMN`` + that apply's
``audit_log.actor``). Everything else — Lakebase, profiler SQL, context, LLM, session
creation, run/resume/review — runs as the app SP, so ``RequestIdentity`` is extracted only
at the ``/apply`` route (api.py).

Pure + hermetic: ``from_headers`` takes a plain mapping (case-insensitive); ``local()`` gives
a no-token default for tests + local dev, since the headers exist only inside Databricks Apps.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

# Databricks Apps reverse-proxy headers (LLD-amend-004 §A1). Matched case-insensitively
# (ASGI lowercases header keys; we lowercase defensively so a plain test dict works too).
_EMAIL = "x-forwarded-email"
_USER = "x-forwarded-user"
_USERNAME = "x-forwarded-preferred-username"
_TOKEN = "x-forwarded-access-token"
_REQUEST_ID = "x-request-id"


@dataclass(frozen=True)
class RequestIdentity:
    """The end user behind a request (or a local/anon default). Pure — no I/O, no SDK."""

    email: str | None = None
    user_id: str | None = None
    username: str | None = None
    access_token: str | None = None  # OBO; None unless the `sql` user-authorization scope is added
    request_id: str | None = None

    @property
    def actor(self) -> str:
        """Stable identity string for the apply ``audit_log.actor`` (D21/D48). Falls back to
        the user id, then ``"anonymous"`` (no Apps proxy → local/anon)."""
        return self.email or self.user_id or "anonymous"

    @property
    def has_obo(self) -> bool:
        """True when the user's OAuth token is present → apply can run on-behalf-of."""
        return bool(self.access_token)

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> "RequestIdentity":
        """Build from request headers (case-insensitive). Missing/blank → ``None``."""
        lower = {str(k).lower(): v for k, v in headers.items()}

        def g(key: str) -> str | None:
            v = lower.get(key)
            v = v.strip() if isinstance(v, str) else v
            return v or None

        return cls(email=g(_EMAIL), user_id=g(_USER), username=g(_USERNAME),
                   access_token=g(_TOKEN), request_id=g(_REQUEST_ID))

    @classmethod
    def local(cls) -> "RequestIdentity":
        """Default identity outside Databricks Apps (tests + local dev): no token."""
        return cls(email="local-dev")
