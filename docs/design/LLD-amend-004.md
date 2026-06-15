# geniefy-v3 — Design Amendment 004: User identity + on-behalf-of (OBO)

**Status:** draft (U90) · **Date:** 2026-06-12
**Amends/extends:** U5/U6 (App backend/API + apply write-path), U2/U10 (`audit_log`, `sessions`), U52 (FastAPI app), U53/U54 (Applier wiring), U49 (`WarehouseSqlRunner` / Statement Execution). Frozen docs are unedited; the deltas live here (GOTM freeze rule, as with amend-001/002/003).
**Decision recorded:** D48 (ratified by the human 2026-06-12).
**Driver:** the live deploy runs the whole app **as the app service principal (SP)**. Apply-to-UC (E9) fails because the SP has no `MODIFY` on read-only/foreign catalogs (e.g. `samples`). Databricks Apps forward the **end user's** identity — and, opt-in, the **user's OAuth token** — so geniefy can attribute work to the real user and apply comments **on-behalf-of (OBO)** the user, honoring *their* UC grants. This is the root fix for E9 and a governance upgrade for the D21 audit trail.

> Living doc. Each section ends with its **implementing unit(s)**; code is built only after this design passes an independent audit (foundation gate, D41).

---

## A1 — The Databricks Apps headers we consume

The Apps reverse proxy injects request headers (present **only** inside Databricks Apps; absent locally). geniefy uses:

| Header | Meaning | geniefy use |
|---|---|---|
| `X-Forwarded-Email` | end-user email (IdP) | session `created_by`, `audit_log.actor` (primary identity) |
| `X-Forwarded-User` | end-user id (IdP) | identity fallback / stable key |
| `X-Forwarded-Preferred-Username` | username (IdP) | display only |
| `X-Forwarded-Access-Token` | **user OAuth token** (OBO) | apply UC writes as the user (opt-in) |
| `X-Request-Id` | request UUID | tracing correlation (D9) |
| `X-Real-Ip` | client IP | tracing/debug only (not persisted) |

**Identity headers are unconditional** (no setup). **`X-Forwarded-Access-Token` is opt-in:** an operator adds the `sql` scope in the App UI → *User authorization → +Add scope*. It is **not** an `app.yaml` key — so it is an operator step, recorded with the others (postgres binding, SP grant) in `DEPLOY_VERIFY.md` and tracked by U78. The header name is matched **case-insensitively** (ASGI lowercases header keys).

## A2 — Authorization split: SP for reads, user for the write

geniefy runs two authorization models simultaneously (Databricks Apps supports this):

- **App SP (unchanged):** profiling (warehouse), context-gathering, and all **LLM** calls (FMAPI) — consistent permissions, independent of who is viewing. The SP path is what every run already uses.
- **End user (new, write-only):** the **Applier** (`COMMENT ON`/`ALTER COLUMN`) and **identity attribution**. Apply runs through the Statement Execution API authenticated with the **user token**, so a write succeeds iff *the user* holds `MODIFY` on the target — exactly the UC contract we want, and it makes `samples` (read-only to everyone) fail loudly rather than misleadingly.

Rationale: profiling/reasoning are read-only and benefit from the SP's stable, broad read access (and don't need per-user consent); only the mutation must respect the caller's grants.

## A3 — `RequestIdentity` (the request-context layer) — U91

A small, **pure** dataclass + a header parser, so the rest of the backend stays testable without a proxy.

```
@dataclass(frozen=True)
class RequestIdentity:
    email: str | None            # X-Forwarded-Email
    user_id: str | None          # X-Forwarded-User
    username: str | None         # X-Forwarded-Preferred-Username
    access_token: str | None     # X-Forwarded-Access-Token (OBO; None unless scope added)
    request_id: str | None       # X-Request-Id

    @property
    def actor(self) -> str:      # for audit_log.actor / created_by
        return self.email or self.user_id or "anonymous"
    @property
    def has_obo(self) -> bool:
        return bool(self.access_token)

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> "RequestIdentity": ...   # case-insensitive
    @classmethod
    def local(cls) -> "RequestIdentity": ...   # email="local-dev", no token — tests + local
```

- **FastAPI wiring (U91):** a dependency reads `request.headers` → `RequestIdentity.from_headers(...)`; endpoints that **create a session** or **apply** receive it. No global mutable state; identity flows as a parameter (keeps the service hermetic + concurrency-safe).
- **Hermetic:** `identity.py` imports only stdlib; no SDK, no FastAPI types in the parser (it takes a plain mapping). Tests assert: header parse (incl. case-insensitivity + missing → `None`), `actor` precedence, `has_obo`, and `local()`.

## A4 — Attribution (session `created_by` + `audit_log.actor`) — U84 + U85

- **Sessions:** the create-session/run endpoint stamps `created_by = identity.actor` into the `SessionState` (persisted by `SessionStore`; surfaced in the E6 history list). Backfilled rows show `created_by = NULL`/legacy → render as "—".
- **Audit:** the `Applier`'s `append_audit` (U55) currently records the SP as actor; it now records `identity.actor`. The audit row already carries before/after + action; only the actor source changes.
- **No new tables**; `created_by` is part of the existing `sessions` row / jsonb snapshot (D38 pattern — store in the snapshot; promote to a column only if the history query needs to filter on it, decided in U84).

## A5 — OBO apply (the E9 fix) — U85

- The Applier's SQL execution gains an **optional per-call auth token**. When `identity.has_obo`, apply builds a Statement Execution client with the **user token** (base path `{host}/api/2.0/sql/statements`, `Authorization: Bearer <user-token>`), reusing the existing `WarehouseSqlRunner`/type-coercion path (U49/U77) — only the credential changes.
- **Warehouse:** the same bound warehouse id is used; the user must have `CAN_USE` on it (they generally do). The DDL itself (`COMMENT ON`, `ALTER COLUMN … COMMENT`) is unchanged (U53), diff-first + idempotent + conflict-aware.
- **Fallbacks (degrade, never silent-SP):**
  1. **No OBO configured** (`access_token is None`) → apply returns a per-item `failed` with a clear reason ("on-behalf-of not enabled — add the `sql` user-authorization scope") and does **not** fall back to the SP (that would write under the wrong principal and re-introduce the E9 confusion). Surfaced via the U70 apply-error banner.
  2. **OBO present but user lacks `MODIFY`** → the UC `PERMISSION_DENIED` surfaces as the per-item reason (this is correct behavior, not a bug).
  3. **Read-only catalog** (`samples`) → same `PERMISSION_DENIED`/managed-catalog error per item, now correctly attributed to the user's grants.
- **E9 root cause confirmed by this design:** apply never worked on `samples` because *no* principal (SP or user) can write a Databricks-managed catalog; against a writable target the user (with `MODIFY`) now succeeds. U85 verifies live on a writable table the operator's user owns.

## A6 — Security & privacy notes

- The **user token is never persisted** and never logged — it lives only on the request and is passed to the apply SQL client for the duration of the call. Audit stores the **actor identity**, not the token.
- `X-Real-Ip` is used only for transient debug; **not** stored in `sessions`/`audit_log`.
- OBO consent is governed by Databricks (the user consents to the app's scopes); geniefy adds no separate consent surface.
- Tracing (D9) correlates on `X-Request-Id` when present.

---

## Sequencing (foundation-first)

1. **U90** (this design) — audited before any code consumes it (D41 foundation gate).
2. **U91** — `RequestIdentity` + FastAPI request-context wiring (no behavior change yet; identity threaded but unused fallbacks safe).
3. **U84** — session `created_by` from identity (consumes U91).
4. **U85** — OBO apply + `audit_log.actor` + per-item surface + the live writable-target verification (consumes U90/U91, amends U53/U55).
5. **Operator step** (no unit): add the `sql` user-authorization scope in the App UI; recorded in `DEPLOY_VERIFY.md` + U78. Live OBO apply is re-verified in **U89** (R2 redeploy).

Each code unit gets an independent unit-wise audit (D41).
