"""geniefy-v3 App backend — UC apply write-path (U53, D7/D21).

Generation never writes to Unity Catalog; this is the separate, explicit **apply** action
(D7). It is governed (D21):

  - **Diff-first + conflict-aware** — re-read each target's *live* UC comment at apply time
    and compare to the ``current_comment`` captured at profiling. If it changed, do NOT
    write: mark ``conflict`` and surface both versions; an explicit re-confirm
    (``confirm=True``) is required to override (optimistic concurrency).
  - **Idempotent** — proposed == live → ``skipped_noop`` (no write).
  - **Never clobber a good comment with an empty one** (U6 §8).
  - **Per-item + checkpointed** — each draft flips its own ``apply_status`` + ``applied_*``
    and the session is persisted after each item, so a partial failure/crash leaves precise,
    resumable state (D20/D21). A failed item (e.g. missing ``MODIFY``) doesn't stop the rest.
  - **Escaped DDL** — identifiers backtick-quoted, the comment a quote-escaped literal,
    never raw string interpolation.

Hermetic (D36): the warehouse SQL runner (UC read + DDL) and the ``SessionStore`` are
injected. The ``/api/sessions/{id}/apply`` endpoint (U52) delegates here (wired into
``build_service`` by U54). Each successful UC write also appends a D21 ``audit_log`` row via
``SessionStore.append_audit`` (U55) — the append-only governance trail of what was written
and by whom (before/after comment snapshots).
"""
import datetime as _dt
from typing import Any

from geniefy_core.state import ApplyStatus, DraftStatus

# only these review states are eligible to write to UC (U6)
_APPLYABLE = (DraftStatus.APPROVED, DraftStatus.EDITED)


class ApplyError(Exception):
    pass


def _ident(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


def _lit(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _split(target: str) -> tuple[str, str, str]:
    parts = [p.strip().strip("`") for p in str(target).split(".")]
    if len(parts) != 3 or not all(parts):
        raise ApplyError(f"target must be 'catalog.schema.table', got {target!r}")
    return parts[0], parts[1], parts[2]


# surfaced per-item when the app isn't OBO-enabled (D48: never silently write as the SP)
_OBO_MISSING = ("on-behalf-of not enabled: add the `sql` user-authorization scope to the app so "
                "apply can run as you (X-Forwarded-Access-Token)")


class Applier:
    """Applies approved/edited drafts to Unity Catalog (D7/D21).

    Apply runs **on-behalf-of the end user** (D48): when ``make_user_sql`` is wired (production),
    the read+write SQL is authenticated with the user's OBO token so the write honors *their* UC
    grants (the root fix for E9). If OBO isn't enabled (no token), every item fails with a clear
    reason — we never silently fall back to the SP. ``make_user_sql`` is ``None`` in the hermetic
    tests, where the injected ``run_sql`` is used directly (apply-logic is principal-agnostic)."""

    def __init__(self, run_sql: Any, store: Any, *, make_user_sql: Any = None):
        self._sql = run_sql              # warehouse: (sql) -> list[dict]; DDL returns []
        self._store = store              # SessionStore: load/save
        self._make_user_sql = make_user_sql  # (access_token) -> runner; OBO write-path (D48)

    def apply(self, session_id: str, *, created_by: str, access_token: str | None = None,
              confirm: bool = False, targets: list[str] | None = None) -> dict[str, Any]:
        state = self._store.load(session_id)
        if state is None:
            raise ApplyError(f"session not found: {session_id}")
        cat, sch, tbl = _split(state.target)

        # Pick the principal for this apply (D48): the END USER via OBO when the Applier is
        # OBO-capable (production). No silent SP fallback — if OBO is on but no token arrived,
        # fail each item with a clear reason (the operator must add the `sql` scope).
        sql = self._sql
        obo_missing = False
        if self._make_user_sql is not None:
            if access_token:
                sql = self._make_user_sql(access_token)
            else:
                obo_missing = True

        items: list[tuple[str | None, Any]] = []
        if state.table_draft is not None:
            items.append((None, state.table_draft))
        items += [(cd.column_name, cd) for cd in state.column_drafts]
        tset = set(targets) if targets is not None else None

        results: list[dict[str, Any]] = []
        for name, draft in items:
            key = "__table__" if name is None else name
            if tset is not None and key not in tset and name not in tset:
                continue
            if draft.status not in _APPLYABLE:
                continue  # only approved/edited are written (U6)
            if obo_missing:
                draft.apply_status = ApplyStatus.FAILED
                results.append({"target": key, "status": "failed", "error": _OBO_MISSING})
            else:
                results.append(self._apply_one(session_id, cat, sch, tbl, name, draft, confirm, created_by, sql))
            self._store.save(state, created_by=created_by)  # per-item checkpoint (D21/D20)

        return {"session_id": session_id, "results": results, "applied": sum(
            1 for r in results if r["status"] == "applied")}

    def _apply_one(self, session_id: str, cat: str, sch: str, tbl: str, col: str | None,
                   draft: Any, confirm: bool, created_by: str, sql: Any) -> dict[str, Any]:
        target = "__table__" if col is None else col
        live = self._read_live(cat, sch, tbl, col, sql)
        proposed = draft.proposed_comment

        # conflict: the live comment changed since we profiled it (D21 optimistic concurrency)
        if _norm(live) != _norm(draft.current_comment) and not confirm:
            draft.apply_status = ApplyStatus.CONFLICT
            return {"target": target, "status": "conflict", "live": live,
                    "expected": draft.current_comment, "proposed": proposed}

        # never write an empty comment over a good one (U6 §8)
        if not (proposed or "").strip():
            if not (live or "").strip():
                draft.apply_status = ApplyStatus.SKIPPED_NOOP
                return {"target": target, "status": "skipped_noop", "reason": "both empty"}
            draft.apply_status = ApplyStatus.FAILED
            return {"target": target, "status": "failed",
                    "error": "refusing to write an empty comment over an existing one"}

        # idempotent no-op (D21)
        if _norm(proposed) == _norm(live):
            draft.apply_status = ApplyStatus.SKIPPED_NOOP
            draft.applied_comment = live
            return {"target": target, "status": "skipped_noop"}

        try:
            self._write(cat, sch, tbl, col, proposed, sql)
        except Exception as exc:  # e.g. missing MODIFY — surface per-item, keep going (U6 §8)
            draft.apply_status = ApplyStatus.FAILED
            return {"target": target, "status": "failed", "error": str(exc)}

        draft.apply_status = ApplyStatus.APPLIED
        draft.applied_comment = proposed
        draft.applied_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
        draft.applied_by = created_by
        draft.status = DraftStatus.APPLIED
        # D21 governance trail: record the UC write (append-only; before/after snapshots).
        self._store.append_audit(
            session_id, action="applied", actor=created_by,
            draft_kind="table" if col is None else "column",
            before={"comment": live}, after={"comment": proposed})
        # E6/D46/D52: upgrade the library entry to `applied` (best-effort — a library write must
        # never fail the UC apply; the audit + draft state are authoritative).
        self._write_library(session_id, cat, sch, tbl, col, proposed, draft, created_by)
        return {"target": target, "status": "applied"}

    def _write_library(self, session_id: str, cat: str, sch: str, tbl: str, col: str | None,
                       comment: str, draft: Any, created_by: str) -> None:
        upsert = getattr(self._store, "upsert_library_entry", None)
        if upsert is None:
            return  # store without a library (e.g. older fakes) — skip silently
        try:
            # status='applied' (the comment is now live in UC); bump_usage=False so an
            # approve→apply cycle counts once (review's write-on-approve already bumped, D52 §A1).
            upsert(scope=("table" if col is None else "column"),
                   match_key=(f"{cat}.{sch}.{tbl}" if col is None else col),
                   canonical_comment=comment,
                   conditional_fields=getattr(draft, "conditional_fields", None),
                   tags=getattr(draft, "tags", None),
                   source_session_id=session_id, source_table_ref=f"{cat}.{sch}.{tbl}",
                   approved_by=created_by, status="applied", bump_usage=False)
        except Exception:
            pass  # best-effort (D7): the library is secondary to the write + audit

    # -- UC I/O via the chosen runner (OBO user-token in prod, fake in tests) ------------
    def _read_live(self, cat: str, sch: str, tbl: str, col: str | None, sql: Any) -> str | None:
        if col is None:
            rows = sql(
                f"SELECT comment FROM {_ident(cat)}.information_schema.tables "
                f"WHERE table_schema = {_lit(sch)} AND table_name = {_lit(tbl)}")
        else:
            rows = sql(
                f"SELECT comment FROM {_ident(cat)}.information_schema.columns "
                f"WHERE table_schema = {_lit(sch)} AND table_name = {_lit(tbl)} "
                f"AND column_name = {_lit(col)}")
        return rows[0].get("comment") if rows else None

    def _write(self, cat: str, sch: str, tbl: str, col: str | None, comment: str, sql: Any) -> None:
        fq = f"{_ident(cat)}.{_ident(sch)}.{_ident(tbl)}"
        if col is None:
            sql(f"COMMENT ON TABLE {fq} IS {_lit(comment)}")
        else:
            sql(f"ALTER TABLE {fq} ALTER COLUMN {_ident(col)} COMMENT {_lit(comment)}")


def _norm(s: str | None) -> str:
    return (s or "").strip()
