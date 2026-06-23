"""geniefy-v3 App backend — SessionStore (U45).

The persistence boundary (U5 / D17): the agent core is stateless and returns a
serializable ``SessionState``; the App owns persisting it to Lakebase (U2 schema).

Per **U10 F5**, ``save`` writes BOTH:
  - the **normalized rows** (`sessions` + `table_drafts` + `column_drafts`) that power the
    review UI and history queries, and
  - the full ``session_state`` **jsonb snapshot** on the `sessions` row, which ``load``
    rehydrates from — lossless and simple (it carries everything, incl. fields without a
    dedicated column such as ``evidence_refs``; see note below).

Hermetic (D1/D36): the DB **connection is injected** (a psycopg-style object with
``cursor()`` + ``commit()``); this module imports no driver and opens no socket, so it is
unit-tested with a fake connection. The concrete Lakebase connection (dev/prod branch via
OAuth, D37) is built at the app/integration layer.

Note: U2's `*_drafts` tables have no ``evidence_refs`` column (added to the spine for U9
explainability). v1 preserves ``evidence_refs`` in the jsonb snapshot (load is lossless);
if the review UI must query them from normalized rows, a follow-on amendment adds the
column. Drafts are fully derived from ``SessionState``, so ``save`` rewrites them wholesale
(delete + insert) in one transaction — avoiding upsert/FK-id churn.
"""
from __future__ import annotations

import json
import re
import uuid
from contextlib import contextmanager
from typing import Any

from geniefy_core.state import SessionState

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Allowed schema-run rollup counters (D51/U109) — the bump key is validated against this set so
# it's never an arbitrary jsonb path.
_SCHEMA_RUN_COUNT_KEYS = {"ready", "needs_input", "applied", "error", "skipped"}


class StoreError(RuntimeError):
    pass


def _qualify(schema: str) -> str:
    if not _IDENT.match(schema):
        raise StoreError(f"unsafe schema identifier: {schema!r}")
    return schema


def _split_target(target: str) -> tuple[str, str, str]:
    parts = [p.strip().strip("`") for p in target.split(".")]
    if len(parts) != 3 or not all(parts):
        raise StoreError(f"target must be 'catalog.schema.table', got {target!r}")
    return parts[0], parts[1], parts[2]


class SessionStore:
    """Persists/rehydrates ``SessionState`` to a Lakebase (Postgres) connection (U5)."""

    def __init__(self, conn: Any = None, *, schema: str = "geniefy", connect: Any = None):
        # ``connect`` is a factory ``() -> connection``. In production we open a **fresh
        # connection per call and close it** (D49/U93 — never cache: Lakebase Autoscaling
        # scale-to-zero + ~1h OAuth credential expiry (D11) make a cached conn unreliable; a
        # per-call conn is always live). Tests inject a static ``conn`` (no factory) and reuse it.
        self._conn = conn
        self._connect = connect
        self._schema = _qualify(schema)

    @contextmanager
    def _connection(self):
        """Yield a connection scoped to ONE operation. With a factory (production): open a fresh
        connection and close it afterwards — no caching (D49). Without one (tests): reuse the
        injected conn, rolling back any aborted txn first so a prior failure can't poison it."""
        if self._connect is not None:
            conn = self._connect()
            try:
                yield conn
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        else:
            try:
                self._conn.rollback()
            except Exception:
                pass
            yield self._conn

    # -- save ---------------------------------------------------------------
    def save(self, state: SessionState, *, created_by: str, schema_run_id: str | None = None) -> str:
        """Persist ``state`` (assigning a ``session_id`` if new) + its jsonb snapshot, and
        rewrite its draft rows, in one transaction. Returns the session id. ``schema_run_id``
        (D51/U109) links a hands-off session to its parent ``schema_runs`` row — written on the
        initial INSERT and **preserved across progressive updates** (the ON CONFLICT clause does
        not touch it), so the tracer's per-phase saves don't need to re-pass it."""
        if not state.session_id:
            state.session_id = str(uuid.uuid4())
        sid = state.session_id
        s = self._schema
        cat, sch, tbl = _split_target(state.target)

        with self._connection() as conn:  # fresh per-call conn in prod (D49/U93)
            try:
                self._write(conn, state, sid, s, cat, sch, tbl, created_by, schema_run_id)
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:  # don't let a rollback on a dropped conn mask the real error
                    pass
                raise
        return sid

    def _write(self, conn: Any, state: SessionState, sid: str, s: str, cat: str, sch: str, tbl: str,
               created_by: str, schema_run_id: str | None = None) -> None:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {s}.sessions
                  (id, uc_catalog, uc_schema, uc_table, mode, status, template_id,
                   config, session_state, mlflow_run_id, created_by, schema_run_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s, %s::jsonb, %s::jsonb, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                  status        = EXCLUDED.status,
                  template_id   = EXCLUDED.template_id,
                  config        = EXCLUDED.config,
                  session_state = EXCLUDED.session_state,
                  mlflow_run_id = EXCLUDED.mlflow_run_id,
                  updated_at    = now()
                """,
                (sid, cat, sch, tbl, state.config.mode.value, state.session_status.value,
                 _uuid_or_none(state.template_id), json.dumps(state.config.to_dict()),
                 json.dumps(state.to_dict()), state.mlflow_run_id, created_by,
                 _uuid_or_none(schema_run_id)),
            )

            # Drafts are derived from state → rewrite wholesale (FK order: columns first).
            cur.execute(f"DELETE FROM {s}.column_drafts WHERE session_id = %s", (sid,))
            cur.execute(f"DELETE FROM {s}.table_drafts WHERE session_id = %s", (sid,))

            if state.table_draft is not None:
                td_id = str(uuid.uuid4())
                td = state.table_draft
                cur.execute(
                    f"""
                    INSERT INTO {s}.table_drafts
                      (id, session_id, uc_catalog, uc_schema, uc_table, current_comment,
                       proposed_comment, rationale, confidence, judge_scores, status, apply_status,
                       applied_comment, applied_at, applied_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)
                    """,
                    (td_id, sid, cat, sch, tbl, td.current_comment, td.proposed_comment,
                     td.rationale, td.confidence, _jsonb(td.judge_scores), td.status.value,
                     td.apply_status.value, td.applied_comment, td.applied_at, td.applied_by),
                )
                for cd in state.column_drafts:
                    cur.execute(
                        f"""
                        INSERT INTO {s}.column_drafts
                          (session_id, table_draft_id, column_name, ordinal, data_type,
                           current_comment, proposed_comment, rationale, confidence, judge_scores,
                           conditional_fields, status, apply_status, applied_comment, applied_at, applied_by)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s)
                        """,
                        (sid, td_id, cd.column_name, cd.ordinal, cd.data_type, cd.current_comment,
                         cd.proposed_comment, cd.rationale, cd.confidence, _jsonb(cd.judge_scores),
                         _jsonb(cd.conditional_fields), cd.status.value, cd.apply_status.value,
                         cd.applied_comment, cd.applied_at, cd.applied_by),
                    )

    # -- audit log (D21) ----------------------------------------------------
    def append_audit(self, session_id: str, *, action: str, actor: str,
                     draft_kind: str | None = None, draft_id: str | None = None,
                     before: Any = None, after: Any = None) -> None:
        """Append a D21 ``audit_log`` row — the append-only governance trail of what was
        written to UC and by whom. ``action`` is an ``audit_action`` enum value (U2):
        generated · edited · approved · applied · rejected · reverted; ``before``/``after``
        are jsonb change snapshots. Self-contained (own commit): the log is append-only,
        independent of the per-session transaction."""
        s = self._schema
        with self._connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        INSERT INTO {s}.audit_log
                          (session_id, draft_kind, draft_id, action, actor, before, after)
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                        """,
                        (session_id, draft_kind, draft_id, action, actor, _jsonb(before), _jsonb(after)),
                    )
                conn.commit()
            except Exception:  # rollback parity with save/upsert_library_entry (U93/U148)
                try:
                    conn.rollback()
                except Exception:  # a dropped conn can't roll back — don't mask the real error
                    pass
                raise

    # -- load ---------------------------------------------------------------
    def load(self, session_id: str) -> SessionState | None:
        """Rehydrate a ``SessionState`` from its jsonb snapshot (U10 F5). ``None`` if the
        session doesn't exist."""
        s = self._schema
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT session_state FROM {s}.sessions WHERE id = %s", (session_id,))
                row = cur.fetchone()
        if not row or row[0] is None:
            return None
        snapshot = row[0]
        if isinstance(snapshot, str):  # some drivers return jsonb as text
            snapshot = json.loads(snapshot)
        return SessionState.from_dict(snapshot)

    # -- history list (E6/E7/D46) -------------------------------------------
    def list_sessions(self, *, status: str | None = None, table: str | None = None,
                      schema_run_id: str | None = None, limit: int = 50,
                      offset: int = 0) -> list[dict[str, Any]]:
        """Paginated session history (newest-updated first) for the History view (E6/E7).
        Each row: session_id · target · status · created_by · created/updated · column +
        applied counts. Optional filters: ``status``, the fully-qualified ``table``
        (``catalog.schema.table``), and ``schema_run_id`` (a hands-off run's per-table sessions,
        D51/U109). Read-only; uses a per-operation connection (D49)."""
        s = self._schema
        limit = max(1, min(int(limit), 200))   # clamp defensively
        offset = max(0, int(offset))
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT s.id, s.uc_catalog, s.uc_schema, s.uc_table, s.status, s.created_by,
                           s.created_at, s.updated_at,
                           (SELECT count(*) FROM {s}.column_drafts c WHERE c.session_id = s.id) AS n_columns,
                           (SELECT count(*) FROM {s}.column_drafts c
                              WHERE c.session_id = s.id AND c.apply_status = 'applied') AS n_applied
                    FROM {s}.sessions s
                    WHERE (%s::text IS NULL OR s.status = %s)
                      AND (%s::text IS NULL OR (s.uc_catalog||'.'||s.uc_schema||'.'||s.uc_table) = %s)
                      AND (%s::uuid IS NULL OR s.schema_run_id = %s::uuid)
                    ORDER BY s.updated_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (status, status, table, table, schema_run_id, schema_run_id, limit, offset),
                )
                rows = cur.fetchall() or []
        return [
            {"session_id": str(r[0]), "target": f"{r[1]}.{r[2]}.{r[3]}", "status": r[4],
             "created_by": r[5], "created_at": _iso(r[6]), "updated_at": _iso(r[7]),
             "n_columns": r[8], "n_applied": r[9]}
            for r in rows
        ]

    # -- comment library: lifecycle + reuse (E6 / D52 / LLD-amend-006 §A) ----
    def upsert_library_entry(self, *, scope: str, match_key: str, canonical_comment: str,
                             conditional_fields: Any = None, tags: Any = None,
                             source_session_id: str | None = None, source_table_ref: str | None = None,
                             approved_by: str | None = None, status: str = "approved",
                             bump_usage: bool = True) -> None:
        """Upsert a canonical comment into ``comment_library`` (one entry per (scope, match_key),
        §A2). ``status`` is the lifecycle state to set (§A1): the **review** write-on-approve
        passes ``'approved'`` (§A3); the **apply** path passes ``'applied'`` to upgrade the same
        entry on a successful UC write (§A1). ``bump_usage`` increments ``usage_count`` for a
        genuine new adoption — **approve bumps, apply does NOT** (apply upgrades the entry the
        approve already created, so a single approve→apply cycle counts once). A new entry always
        starts at count 1. Self-contained (own commit) — independent of any session txn."""
        s = self._schema
        bump = "usage_count = usage_count + 1, " if bump_usage else ""
        with self._connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT id FROM {s}.comment_library WHERE scope = %s AND match_key = %s",
                        (scope, match_key))
                    existing = cur.fetchone()
                    if existing:
                        cur.execute(
                            f"""
                            UPDATE {s}.comment_library SET
                              canonical_comment = %s, conditional_fields = %s::jsonb, tags = %s::jsonb,
                              {bump}source_session_id = %s, source_table_ref = %s,
                              approved_by = %s, approved_at = now(), status = %s, updated_at = now()
                            WHERE id = %s
                            """,
                            (canonical_comment, _jsonb(conditional_fields), _jsonb(tags or []),
                             _uuid_or_none(source_session_id), source_table_ref, approved_by,
                             status, existing[0]))
                    else:
                        cur.execute(
                            f"""
                            INSERT INTO {s}.comment_library
                              (scope, match_key, canonical_comment, conditional_fields, tags,
                               source_session_id, source_table_ref, usage_count, approved_by,
                               approved_at, status)
                            VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,1,%s, now(), %s)
                            """,
                            (scope, match_key, canonical_comment, _jsonb(conditional_fields),
                             _jsonb(tags or []), _uuid_or_none(source_session_id), source_table_ref,
                             approved_by, status))
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise

    def sunset_library_entry(self, entry_id: str, *, sunset_by: str | None = None) -> None:
        """Soft-retire a library entry (§A5/Q6): ``status='sunset'`` + attribution. Kept for
        audit and **excluded from reuse** (``list_library_for_reuse``) and from the default
        Library view; revivable. No hard delete (D52)."""
        s = self._schema
        with self._connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""UPDATE {s}.comment_library
                            SET status = 'sunset', sunset_at = now(), sunset_by = %s, updated_at = now()
                            WHERE id = %s""",
                        (sunset_by, entry_id))
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise

    def revive_library_entry(self, entry_id: str) -> None:
        """Revive a sunset entry (§A5/Q6, D52 refinement): restore to ``'approved'`` and clear
        the sunset attribution. A subsequent successful apply re-upgrades it to ``'applied'`` via
        the normal path — there is intentionally **no** ``applied_at`` marker / direct revive→applied."""
        s = self._schema
        with self._connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""UPDATE {s}.comment_library
                            SET status = 'approved', sunset_at = NULL, sunset_by = NULL, updated_at = now()
                            WHERE id = %s""",
                        (entry_id,))
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise

    def list_library_for_reuse(self, *, scope: str, match_keys: list[str],
                               per_key_limit: int = 3) -> list[dict[str, Any]]:
        """Reuse candidates for generation (§A4/Q1): for a ``scope`` and a batch of exact
        ``match_keys`` (column names, or a single table FQN), return the top ``per_key_limit``
        canonical comments **per key**, ``status ∈ {approved, applied}`` (``sunset`` excluded),
        usage-ranked. Read-only; the ``LibraryProvider`` (U104) turns these into suggestion-only
        grounding for the Reasoner — never an auto-copy."""
        keys = [k for k in (match_keys or []) if k]
        if not keys:
            return []
        s = self._schema
        per_key_limit = max(1, int(per_key_limit))
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT match_key, canonical_comment, tags, usage_count FROM (
                      SELECT match_key, canonical_comment, tags, usage_count,
                             row_number() OVER (PARTITION BY match_key
                                                ORDER BY usage_count DESC, updated_at DESC) AS rn
                      FROM {s}.comment_library
                      WHERE scope = %s AND match_key = ANY(%s)
                        AND status IN ('approved','applied')
                    ) ranked
                    WHERE rn <= %s
                    ORDER BY match_key, usage_count DESC
                    """,
                    (scope, keys, per_key_limit))
                rows = cur.fetchall() or []
        return [{"match_key": r[0], "canonical_comment": r[1],
                 "tags": _parse_tags(r[2]), "usage_count": r[3]} for r in rows]

    def list_library(self, *, scope: str | None = None, include_sunset: bool = False,
                     limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """Paginated comment-library entries (most-used first) for the Library view (E6/§A6),
        now carrying ``status``. ``sunset`` entries are **hidden by default** (Q6); pass
        ``include_sunset=True`` to reveal them."""
        s = self._schema
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, scope, match_key, canonical_comment, tags, usage_count,
                           source_table_ref, approved_by, updated_at, status
                    FROM {s}.comment_library
                    WHERE (%s::text IS NULL OR scope = %s)
                      AND (%s::bool IS TRUE OR status <> 'sunset')
                    ORDER BY usage_count DESC, updated_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (scope, scope, bool(include_sunset), limit, offset))
                rows = cur.fetchall() or []
        return [{"id": str(r[0]), "scope": r[1], "match_key": r[2],
                 "canonical_comment": r[3], "tags": _parse_tags(r[4]), "usage_count": r[5],
                 "source_table_ref": r[6], "approved_by": r[7], "updated_at": _iso(r[8]),
                 "status": r[9]}
                for r in rows]

    # -- schema runs (hands-off / D51 / LLD-amend-005 §4) -------------------
    def create_schema_run(self, *, catalog: str, schema: str, created_by: str,
                          filters: Any = None) -> str:
        """Create a parent schema-run record (status ``enumerating``) and return its id."""
        s = self._schema
        run_id = str(uuid.uuid4())
        with self._connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""INSERT INTO {s}.schema_runs
                              (id, uc_catalog, uc_schema, status, filters, counts, created_by)
                            VALUES (%s,%s,%s,'enumerating',%s::jsonb,'{{}}'::jsonb,%s)""",
                        (run_id, catalog, schema, _jsonb(filters or {}), created_by))
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
        return run_id

    def update_schema_run(self, run_id: str, *, status: str | None = None,
                          total_tables: int | None = None, job_run_id: int | None = None) -> None:
        """Update only the provided fields of a schema run (``updated_at`` via the trigger)."""
        sets: list[str] = []
        params: list[Any] = []
        if status is not None:
            sets.append("status = %s"); params.append(status)
        if total_tables is not None:
            sets.append("total_tables = %s"); params.append(total_tables)
        if job_run_id is not None:
            sets.append("job_run_id = %s"); params.append(job_run_id)
        if not sets:
            return
        params.append(run_id)
        s = self._schema
        with self._connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(f"UPDATE {s}.schema_runs SET {', '.join(sets)} WHERE id = %s", tuple(params))
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise

    def finalize_schema_run(self, run_id: str, *, status: str = "completed") -> None:
        """Mark a run terminal: ``completed`` | ``completed_with_errors`` | ``failed`` | ``cancelled``."""
        self.update_schema_run(run_id, status=status)

    def bump_counts(self, run_id: str, key: str) -> None:
        """Increment one rollup counter (ready|needs_input|applied|error|skipped) on a schema run.
        The key is validated against an allowlist; the jsonb path + read use bound params."""
        if key not in _SCHEMA_RUN_COUNT_KEYS:
            raise StoreError(f"unknown schema_run count key: {key!r}")
        s = self._schema
        with self._connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""UPDATE {s}.schema_runs
                            SET counts = jsonb_set(counts, array[%s],
                                  to_jsonb(coalesce((counts->>%s)::int, 0) + 1))
                            WHERE id = %s""",
                        (key, key, run_id))
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise

    def session_exists(self, schema_run_id: str, table_ref: str) -> bool:
        """True if this schema run already has a session for ``table_ref`` (idempotent Job retry, D20)."""
        cat, sch, tbl = _split_target(table_ref)
        s = self._schema
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT 1 FROM {s}.sessions
                        WHERE schema_run_id = %s AND uc_catalog = %s AND uc_schema = %s AND uc_table = %s
                        LIMIT 1""",
                    (schema_run_id, cat, sch, tbl))
                return cur.fetchone() is not None

    def list_schema_runs(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Paginated schema runs (newest first) for the hands-off runs view."""
        s = self._schema
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT id, uc_catalog, uc_schema, status, filters, total_tables, counts,
                               job_run_id, created_by, created_at, updated_at
                        FROM {s}.schema_runs ORDER BY created_at DESC LIMIT %s OFFSET %s""",
                    (limit, offset))
                rows = cur.fetchall() or []
        return [_schema_run_row(r) for r in rows]

    def get_schema_run(self, run_id: str) -> dict[str, Any] | None:
        """A single schema run (``None`` if absent). Its per-table sessions come from
        ``list_sessions(schema_run_id=run_id)``."""
        s = self._schema
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT id, uc_catalog, uc_schema, status, filters, total_tables, counts,
                               job_run_id, created_by, created_at, updated_at
                        FROM {s}.schema_runs WHERE id = %s""",
                    (run_id,))
                row = cur.fetchone()
        return _schema_run_row(row) if row else None


def _iso(value: Any) -> Any:
    """Render a DB timestamp as an ISO string for JSON; pass through strings/None."""
    return value.isoformat() if hasattr(value, "isoformat") else value


def _parse_tags(value: Any) -> list:
    """Normalize a jsonb ``tags`` column to a list — some drivers return jsonb as text."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    return value or []


def _parse_jsonb_obj(value: Any) -> dict:
    """Normalize a jsonb object column (filters/counts) to a dict — drivers may return text."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value or {}


def _schema_run_row(r: Any) -> dict[str, Any]:
    """Map a ``schema_runs`` row tuple to the API dict (D51/U109)."""
    return {"id": str(r[0]), "catalog": r[1], "schema": r[2], "status": r[3],
            "filters": _parse_jsonb_obj(r[4]), "total_tables": r[5], "counts": _parse_jsonb_obj(r[6]),
            "job_run_id": r[7], "created_by": r[8], "created_at": _iso(r[9]), "updated_at": _iso(r[10])}


def _jsonb(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


def _uuid_or_none(value: Any) -> str | None:
    """`sessions.template_id` is a uuid FK (U2); the spine carries the template *name*
    (D38). Write it only when it parses as a uuid — else NULL (the name is preserved in
    the `session_state` jsonb). Name→uuid resolution is the App template-mgmt unit's job."""
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None
