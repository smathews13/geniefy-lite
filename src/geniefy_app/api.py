"""geniefy-v3 App backend — FastAPI service (U52).

The integration capstone (U5/U6, D18): wraps the agent core in a REST API. A run executes
as an in-process **background task** (D18) — `POST /api/run` returns 202 and the frontend
**polls** `GET /api/sessions/{id}` until a terminal status; answers + review actions drive
resume/transitions. Every step is persisted via the injected `SessionStore` (D17); a
per-run `PersistingTracer` writes progressive partials per phase so the "watch it think"
UI (U9) has something to poll.

Testability (D36): `SessionService` takes an injected ``make_orchestrator(tracer)`` factory
+ the store + config, so the service/routing/lifecycle is unit-tested with fakes. The real
component wiring (FMAPI transport, warehouse runner, in-app profiler, built-in context
providers — all the U49/U51 boundaries) lives in ``build_service`` / `app/main.py`, lazy-
imported and integration-verified at deploy (like the SessionStore dev round-trip).

The UC **apply** write-path (U6 / D21 — conflict-aware `COMMENT ON` / `ALTER COLUMN`) is a
separate governed unit (U53); this exposes `POST /api/sessions/{id}/apply` that delegates to
the injected ``applier`` (wired by ``build_service`` in U54; 501 only if no applier is set).
"""
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from geniefy_core.gate import Gate
from geniefy_core.orchestrator import DocumentationOrchestrator
from geniefy_core.state import Answer, DraftStatus, Phase, RunConfig, SessionMode, SessionState


# ─────────────────────────────────────────────────────────────────────────────
# Per-run tracer: persist progressive partials per phase (D18 / U9)
# ─────────────────────────────────────────────────────────────────────────────
class PersistingTracer:
    """Saves the session after each orchestrator phase, so a polling client sees status
    + partials advance (D18). Best-effort: a progressive-save failure is swallowed (the
    authoritative final save in the worker still runs)."""

    def __init__(self, save: Callable[[], None]):
        self._save = save

    @contextmanager
    def span(self, name: str):
        try:
            yield
        finally:
            try:
                self._save()
            except Exception:  # progressive persistence is best-effort; final save is authoritative
                pass


# An object with .save(state, created_by) -> id and .load(id) -> SessionState|None (SessionStore).
class Store(Protocol):
    def save(self, state: SessionState, *, created_by: str) -> str: ...
    def load(self, session_id: str) -> SessionState | None: ...
    def list_sessions(self, *, status: str | None, table: str | None,
                      limit: int, offset: int) -> list[dict[str, Any]]: ...  # history (E6)
    def list_library(self, *, scope: str | None, include_sunset: bool,
                     limit: int, offset: int) -> list[dict[str, Any]]: ...  # library (E6/D52)
    # Library lifecycle methods (D52 §A) are duck-typed on the concrete SessionStore:
    # upsert_library_entry · sunset_library_entry · revive_library_entry · list_library_for_reuse.


# Builds an orchestrator wired with a per-run tracer (real wiring or a test fake).
MakeOrchestrator = Callable[[Any], DocumentationOrchestrator]

# In-flight statuses → reject a second concurrent run/resume on the same session (D18).
_IN_FLIGHT = {"profiling", "gathering_context", "reasoning", "applying"}


class SessionError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


_TABLE_TARGET = "__table__"


def _all_drafts(state: SessionState) -> list[tuple[str, Any]]:
    """``(target_name, draft)`` for the table comment + each column draft (LLD-amend-007)."""
    out: list[tuple[str, Any]] = []
    if state.table_draft is not None:
        out.append((_TABLE_TARGET, state.table_draft))
    out.extend((c.column_name, c) for c in state.column_drafts)
    return out


def _high_confidence_targets(state: SessionState) -> list[str]:
    """Targets eligible for bulk-approve (LLD-amend-007 §3, D59): ``status == DRAFT`` — which already
    encodes the Gate's keep decision (kept drafts stay DRAFT; trips / low-confidence become
    NEEDS_INPUT / LOW_CONFIDENCE) — AND ``confidence >= keep_threshold`` as a defensive re-check
    (covers a keep_threshold lowered after gating). Excludes everything flagged or already acted-on,
    so the flagged minority always needs explicit review (no blanket approve, D7)."""
    thr = state.config.keep_threshold
    return [name for name, d in _all_drafts(state)
            if d.status == DraftStatus.DRAFT and d.confidence is not None and d.confidence >= thr]


def _confidence_summary(state: SessionState) -> dict[str, Any]:
    """Overall table confidence + Gate-bucket breakdown + weakest draft (LLD-amend-007 §2, D59).
    Weighted rollup (0.5 * table-comment + 0.5 * column mean); null confidences excluded; all-null →
    ``overall=None``. The single number is always returned WITH the buckets + weakest so it can't hide
    a weak draft (D22/D23). Deterministic — a rollup of the Judge's per-draft scores, no new Judge call."""
    drafts = _all_drafts(state)
    table_conf = state.table_draft.confidence if state.table_draft else None
    col_confs = [c.confidence for c in state.column_drafts if c.confidence is not None]
    col_mean = (sum(col_confs) / len(col_confs)) if col_confs else None
    if table_conf is not None and col_mean is not None:
        overall: float | None = 0.5 * table_conf + 0.5 * col_mean
    else:
        overall = table_conf if table_conf is not None else col_mean  # one side, or None if both null
    thr = state.config.keep_threshold
    review_ready = sum(1 for _, d in drafts
                       if d.status in (DraftStatus.DRAFT, DraftStatus.APPROVED, DraftStatus.EDITED)
                       and d.confidence is not None and d.confidence >= thr)
    scored = [(name, d.confidence) for name, d in drafts if d.confidence is not None]
    weakest = min(scored, key=lambda t: t[1]) if scored else None
    return {
        "overall": overall,
        "review_ready": review_ready,
        "needs_input": sum(1 for _, d in drafts if d.status == DraftStatus.NEEDS_INPUT),
        "low": sum(1 for _, d in drafts if d.status == DraftStatus.LOW_CONFIDENCE),
        "approvable": len(_high_confidence_targets(state)),  # exact count the per-table button approves (U157)
        "weakest": {"target": weakest[0], "confidence": weakest[1]} if weakest else None,
    }


@dataclass
class SessionService:
    """Backend business logic (run/resume/review/get), persistence-backed."""

    make_orchestrator: MakeOrchestrator
    store: Store
    config: RunConfig
    applier: Any = None  # optional UC apply write-path (U6/D21), injected later
    sql_runner: Any = None        # SP warehouse runner for schema enumeration (D51/U110)
    run_schema_job: Any = None    # (run_id, catalog, schema, filters) -> job_run_id | None (D51/U110)
    cancel_schema_job: Any = None # (job_run_id) -> None — best-effort Job cancel (D51/U110)

    # -- run ----------------------------------------------------------------
    def start_run(self, table: str, *, created_by: str, mode: SessionMode | None = None) -> str:
        cfg = self.config if mode is None else _with_mode(self.config, mode)
        state = SessionState(target=table, config=cfg, template_id=cfg.template_id)
        return self.store.save(state, created_by=created_by)

    def execute_run(self, session_id: str, created_by: str) -> None:
        """Background worker: drive a full run, persisting per phase + a final save (D18)."""
        state = self.store.load(session_id)
        if state is None:
            return
        tracer = PersistingTracer(lambda: self.store.save(state, created_by=created_by))
        orch = self.make_orchestrator(tracer)
        result = orch.run(state.target, state)
        self.store.save(result.state, created_by=created_by)

    # -- resume (answers) ---------------------------------------------------
    def submit_answers(self, session_id: str, answers: list[Answer], *, created_by: str) -> None:
        state = self.store.load(session_id)
        if state is None:
            raise SessionError(404, "session not found")
        if state.session_status.value in _IN_FLIGHT:
            raise SessionError(409, "a run is already in progress for this session")
        # Persist an in-flight status NOW (before the background resume) so the polling client
        # (D18) keeps polling through resume instead of parking on awaiting_input — the screen
        # would otherwise stop advancing (U59/U60 audit MED). execute_resume flips it terminal.
        state.phase = Phase.REASONING
        self.store.save(state, created_by=created_by)

    def execute_resume(self, session_id: str, answers: list[Answer], created_by: str) -> None:
        state = self.store.load(session_id)
        if state is None:
            return
        tracer = PersistingTracer(lambda: self.store.save(state, created_by=created_by))
        orch = self.make_orchestrator(tracer)
        result = orch.resume(state, answers)
        self.store.save(result.state, created_by=created_by)

    # -- regenerate (E3/D45) ------------------------------------------------
    def start_regenerate(self, session_id: str, targets: list[str] | None, *, created_by: str) -> None:
        state = self.store.load(session_id)
        if state is None:
            raise SessionError(404, "session not found")
        if state.session_status.value in _IN_FLIGHT:
            raise SessionError(409, "a run is already in progress for this session")
        # Persist an in-flight status before the background regenerate so the poll client (D18)
        # keeps advancing; execute_regenerate flips it terminal (like submit_answers).
        state.phase = Phase.REASONING
        self.store.save(state, created_by=created_by)

    def execute_regenerate(self, session_id: str, targets: list[str] | None, created_by: str) -> None:
        state = self.store.load(session_id)
        if state is None:
            return
        tracer = PersistingTracer(lambda: self.store.save(state, created_by=created_by))
        orch = self.make_orchestrator(tracer)
        result = orch.regenerate(state, targets)
        self.store.save(result.state, created_by=created_by)

    # -- review (U6) --------------------------------------------------------
    def review_draft(self, session_id: str, target_name: str | None, action: str, *,
                     created_by: str, proposed_comment: str | None = None) -> None:
        state = self.store.load(session_id)
        if state is None:
            raise SessionError(404, "session not found")
        draft = state.table_draft if target_name in (None, "", "__table__") else state.column_draft(target_name)
        if draft is None:
            raise SessionError(404, f"draft not found: {target_name}")
        if action == "approve":
            draft.status = DraftStatus.APPROVED
        elif action == "reject":
            draft.status = DraftStatus.REJECTED
        elif action == "edit":
            if not (proposed_comment or "").strip():
                raise SessionError(400, "edit requires a non-empty proposed_comment")
            draft.proposed_comment = proposed_comment
            draft.status = DraftStatus.EDITED
        else:
            raise SessionError(400, f"unknown action: {action}")
        # Write-on-approve (D52 §A3): an approved/edited draft seeds the reusable library at
        # status='approved' (apply later upgrades it to 'applied'). Best-effort — never block review.
        if action in ("approve", "edit"):
            self._write_library_on_approve(state, draft, target_name, created_by)
        self.store.save(state, created_by=created_by)

    def approve_high_confidence(self, session_id: str, *, created_by: str) -> dict[str, Any]:
        """Bulk-approve only the high-confidence, unflagged drafts (LLD-amend-007 §3, D59). Mirrors
        ``review_draft``'s approve semantics — set ``status=APPROVED`` + best-effort write-on-approve;
        **no ``audit_log`` row** (per-draft approve writes none; audit is apply-time + OBO, D48). Leaves
        needs_input / low_confidence / errored / already-acted drafts untouched for explicit review (no
        blanket approve, D7). Returns ``{approved: [target…], count}``. Bulk-APPROVE is NOT apply — UC
        writes stay explicit + human/OBO."""
        state = self.store.load(session_id)
        if state is None:
            raise SessionError(404, "session not found")
        approved: list[str] = []
        for name in _high_confidence_targets(state):
            draft = state.table_draft if name == _TABLE_TARGET else state.column_draft(name)
            if draft is None:
                continue
            draft.status = DraftStatus.APPROVED
            self._write_library_on_approve(state, draft, name, created_by)
            approved.append(name)
        if approved:
            self.store.save(state, created_by=created_by)
        return {"approved": approved, "count": len(approved)}

    def _write_library_on_approve(self, state: SessionState, draft: Any,
                                  target_name: str | None, created_by: str) -> None:
        upsert = getattr(self.store, "upsert_library_entry", None)
        if upsert is None:
            return  # store without a library (older fakes) — skip silently
        comment = (getattr(draft, "proposed_comment", None) or "").strip()
        if not comment:
            return
        is_table = target_name in (None, "", "__table__")
        # Normalize the table match_key the same way the apply path's `_split` does (strip
        # backticks + whitespace per part) so approve and apply key the SAME entry — the
        # one-per-(scope, match_key) invariant (D52 §A2) holds for quoted/padded FQNs (U115).
        table_key = ".".join(p.strip().strip("`") for p in (state.target or "").split("."))
        try:
            upsert(scope=("table" if is_table else "column"),
                   match_key=(table_key if is_table else target_name),
                   canonical_comment=comment,
                   conditional_fields=getattr(draft, "conditional_fields", None),
                   tags=getattr(draft, "tags", None),
                   source_session_id=state.session_id, source_table_ref=state.target,
                   approved_by=created_by, status="approved", bump_usage=True)
        except Exception:
            pass  # best-effort (D52): a library write must never fail the review action

    # -- library lifecycle (D52 §A5) ----------------------------------------
    def sunset_library(self, entry_id: str, *, by: str | None = None) -> None:
        fn = getattr(self.store, "sunset_library_entry", None)
        if fn is None:
            raise SessionError(501, "library lifecycle not supported")
        fn(entry_id, sunset_by=by)

    def revive_library(self, entry_id: str) -> None:
        fn = getattr(self.store, "revive_library_entry", None)
        if fn is None:
            raise SessionError(501, "library lifecycle not supported")
        fn(entry_id)

    # -- read ---------------------------------------------------------------
    def get(self, session_id: str) -> dict[str, Any] | None:
        state = self.store.load(session_id)
        if state is None:
            return None
        return {
            "session_id": state.session_id,
            "target": state.target,
            "status": state.session_status.value,
            "table_draft": state.table_draft.to_dict() if state.table_draft else None,
            "column_drafts": [c.to_dict() for c in state.column_drafts],
            "open_questions": [q.to_dict() for q in state.open_questions],
            "profile": state.profile or {},  # per-column profile for the viz (E4/U86)
        }

    # -- history + library (E6/E7/D46) --------------------------------------
    def list_sessions(self, *, status: str | None = None, table: str | None = None,
                      limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return self.store.list_sessions(status=status, table=table, limit=limit, offset=offset)

    def list_library(self, *, scope: str | None = None, include_sunset: bool = False,
                     limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return self.store.list_library(scope=scope, include_sunset=include_sunset,
                                       limit=limit, offset=offset)

    # -- hands-off / schema runs (D51 / LLD-amend-005 §5) -------------------
    def start_schema_run(self, catalog: str, schema: str, *, created_by: str,
                         filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Create a parent ``schema_runs`` record and trigger the batch Job (which enumerates the
        schema's tables + runs hands-off generation per table). ``created_by`` is the human who
        triggered (D48 attribution); the Job itself runs as the app SP. Returns ``{schema_run_id}``."""
        run_id = self.store.create_schema_run(catalog=catalog, schema=schema,
                                              created_by=created_by, filters=filters or {})
        if self.run_schema_job is None:  # U110: no job mechanism → don't leave the record stuck at 'enumerating'
            self.store.finalize_schema_run(run_id, status="failed")
            raise SessionError(501, "schema-run job not wired (no run_schema_job)")
        try:
            job_run_id = self.run_schema_job(run_id, catalog, schema, filters or {})
        except Exception as exc:
            self.store.finalize_schema_run(run_id, status="failed")
            raise SessionError(502, f"failed to trigger schema-run job: {exc}")
        if job_run_id is None:  # U110: triggered but no run id → mark failed, don't leave 'enumerating'
            self.store.finalize_schema_run(run_id, status="failed")
            raise SessionError(502, "schema-run job triggered but returned no run id; run not started")
        self.store.update_schema_run(run_id, status="running", job_run_id=int(job_run_id))
        return {"schema_run_id": run_id}

    def enumerate_tables(self, catalog: str, schema: str,
                         filters: dict[str, Any] | None = None) -> list[str]:
        """List the schema's base tables to document (SP reads, D48). Default skips already-
        documented tables (non-empty comment); optional ``name_like`` (SQL LIKE) + ``max_tables`` cap."""
        if self.sql_runner is None:
            raise SessionError(501, "schema enumeration not wired (no SQL runner)")
        filters = filters or {}
        sql = (f"SELECT table_name, comment FROM {_q_ident(catalog)}.information_schema.tables "
               f"WHERE table_schema = {_q_lit(schema)} AND table_type IN ('MANAGED','EXTERNAL')")
        # Skip internal / system artifacts (e.g. metric-view `__materialization_mat_*` tables, U124)
        # unless the caller opts in. Use left() rather than `LIKE '\_\_%' ESCAPE '\'`: a lone backslash
        # in the `'\'` literal escapes the closing quote in Databricks SQL → PARSE_SYNTAX_ERROR, which
        # (combined with the swallowed-failure bug) silently enumerated 0 tables (U138 live fix).
        if not filters.get("include_internal", False):
            sql += " AND left(table_name, 2) <> '__'"
        name_like = filters.get("name_like")
        if name_like:
            sql += f" AND table_name LIKE {_q_lit(str(name_like))}"
        sql += " ORDER BY table_name"
        rows = self.sql_runner(sql) or []
        skip_documented = filters.get("skip_documented", True)
        names: list[str] = []
        for r in rows:
            name = r.get("table_name")
            if not name:
                continue
            if skip_documented and (r.get("comment") or "").strip():
                continue
            names.append(name)
        max_tables = filters.get("max_tables")
        if max_tables:
            names = names[: int(max_tables)]
        return names

    def run_hands_off(self, table_ref: str, *, schema_run_id: str, created_by: str) -> str:
        """Generate + persist documentation for ONE table in hands-off mode (D51): never blocks,
        never applies; clarifying questions are persisted as ``awaiting_input`` for a later
        answer+resume. Links the session to its parent run. Returns the terminal ``StepResult.kind``
        (``ready_for_review`` | ``needs_input`` | ``failed``)."""
        cfg = _with_mode(self.config, SessionMode.HANDS_OFF)
        state = SessionState(target=table_ref, config=cfg, template_id=cfg.template_id)
        # initial INSERT carries schema_run_id (preserved across the tracer's progressive saves, U109)
        self.store.save(state, created_by=created_by, schema_run_id=schema_run_id)
        tracer = PersistingTracer(lambda: self.store.save(state, created_by=created_by))
        result = self.make_orchestrator(tracer).run(state.target, state)
        self.store.save(result.state, created_by=created_by)
        return result.kind

    def list_schema_runs(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return self.store.list_schema_runs(limit=limit, offset=offset)

    def get_schema_run(self, run_id: str) -> dict[str, Any] | None:
        """A schema run + its per-table sessions (for the run-detail view). ``None`` if absent."""
        run = self.store.get_schema_run(run_id)
        if run is None:
            return None
        sessions = self.store.list_sessions(schema_run_id=run_id, limit=200)
        # Attach a per-table confidence_summary (LLD-amend-007 §4) so the Schema view shows per-table
        # confidence + bulk-approve counts without opening each table. v1 loads each session to reuse
        # the one rollup helper; if runs get very large this should move to a SQL aggregate or a
        # summary persisted at generation time (perf follow-on).
        for s in sessions:
            sid = s.get("session_id") or s.get("id")
            st = self.store.load(sid) if sid else None
            s["confidence_summary"] = _confidence_summary(st) if st is not None else None
        run["sessions"] = sessions
        return run

    def cancel_schema_run(self, run_id: str) -> None:
        """Best-effort cancel: cancel the Job run if a canceller is wired, then mark the record
        ``cancelled``. Sessions already persisted remain (D51)."""
        run = self.store.get_schema_run(run_id)
        if run is None:
            raise SessionError(404, "schema run not found")
        job_run_id = run.get("job_run_id")
        if job_run_id is not None and self.cancel_schema_job is not None:
            try:
                self.cancel_schema_job(int(job_run_id))
            except Exception:
                pass  # best-effort — still mark the record cancelled
        self.store.finalize_schema_run(run_id, status="cancelled")


def _with_mode(cfg: RunConfig, mode: SessionMode) -> RunConfig:
    d = cfg.to_dict()
    d["mode"] = mode.value
    return RunConfig.from_dict(d)


def _q_ident(name: str) -> str:
    """Backtick-quote a SQL identifier (catalog) for the enumeration query (U110)."""
    return "`" + str(name).replace("`", "``") + "`"


def _q_lit(value: str) -> str:
    """Single-quote-escape a SQL string literal (schema name / LIKE pattern, U110)."""
    return "'" + str(value).replace("'", "''") + "'"


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI app
# ─────────────────────────────────────────────────────────────────────────────
def create_app(service: SessionService, *, static_dir: str | None = None):
    from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field

    from .identity import RequestIdentity

    app = FastAPI(title="geniefy-lite", version="0.1.0",
                  description="Agentic Unity Catalog table documentation — make your lakehouse AI ready.")

    class RunRequest(BaseModel):
        table: str
        mode: str | None = None

    class AnswerItem(BaseModel):
        question_id: str
        text: str

    class AnswersRequest(BaseModel):
        answers: list[AnswerItem]

    class ReviewRequest(BaseModel):
        action: str
        proposed_comment: str | None = None

    class RegenerateRequest(BaseModel):
        targets: list[str] | None = None   # column names and/or "__table__"
        all: bool = False                  # regenerate the table + every column

    def _actor() -> str:
        # The app SP is the actor for everything except apply (D48 refinement): Lakebase,
        # profiler SQL, context, LLM, session creation, run/resume/review all run as the SP.
        return "app-service-principal"

    def _identity(request: Request) -> RequestIdentity:
        # End-user identity (+ OBO token) from the Databricks Apps headers — consumed ONLY by
        # the apply write-path (D48 refinement). Other routes stay on the SP via _actor().
        return RequestIdentity.from_headers(request.headers)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/me")
    def me(identity: RequestIdentity = Depends(_identity)):
        # the end-user behind the request, from the Databricks Apps headers (U91/D48) — for the
        # header user card. Identity only (no token); "anonymous" outside Databricks Apps.
        return {"email": identity.email, "username": identity.username,
                "user_id": identity.user_id, "actor": identity.actor}

    @app.get("/api/config")
    def get_config():
        c = service.config
        return {"model_endpoint": c.model_endpoint, "mode": c.mode.value,
                "keep_threshold": c.keep_threshold, "template_id": c.template_id,
                "enabled_providers": c.enabled_providers}

    @app.post("/api/run", status_code=202)
    def run(req: RunRequest, background: BackgroundTasks):
        mode = SessionMode(req.mode) if req.mode else None
        actor = _actor()
        sid = service.start_run(req.table, created_by=actor, mode=mode)
        background.add_task(service.execute_run, sid, actor)
        return {"session_id": sid, "status": "created"}

    @app.get("/api/sessions")
    def list_sessions(status: str | None = None, table: str | None = None,
                      limit: int = 50, offset: int = 0):
        # session history (E6/E7) — newest first, optional status/table filters + pagination
        return {"sessions": service.list_sessions(status=status, table=table, limit=limit, offset=offset)}

    @app.get("/api/library")
    def list_library(scope: str | None = None, include_sunset: bool = False,
                     limit: int = 100, offset: int = 0):
        # reusable comment library (E6/D52) — most-used first; sunset hidden unless requested
        return {"entries": service.list_library(scope=scope, include_sunset=include_sunset,
                                                limit=limit, offset=offset)}

    @app.post("/api/library/{entry_id}/sunset")
    def sunset_library(entry_id: str, identity: RequestIdentity = Depends(_identity)):
        # Soft-retire a library definition (D52 §A5) — excluded from reuse + the default view;
        # sunset_by records the human who retired it (identity attribution, D48). Revivable.
        try:
            service.sunset_library(entry_id, by=identity.actor)
        except SessionError as e:
            raise HTTPException(e.status_code, e.detail)
        return {"id": entry_id, "status": "sunset"}

    @app.post("/api/library/{entry_id}/revive")
    def revive_library(entry_id: str):
        # Revive a sunset entry → 'approved' (D52 refinement); a later apply re-upgrades to 'applied'.
        try:
            service.revive_library(entry_id)
        except SessionError as e:
            raise HTTPException(e.status_code, e.detail)
        return {"id": entry_id, "status": "approved"}

    # -- hands-off / schema runs (D51 / LLD-amend-005 §5) ------------------
    class SchemaRunRequest(BaseModel):
        catalog: str
        schema_name: str = Field(alias="schema")  # avoid shadowing BaseModel; client sends "schema"
        filters: dict | None = None
        model_config = {"populate_by_name": True}

    @app.post("/api/schema-runs", status_code=202)
    def start_schema_run(req: SchemaRunRequest, identity: RequestIdentity = Depends(_identity)):
        # Outward/expensive: kicks a Job that reads + calls the model for every table. created_by =
        # the human who triggered (D48 attribution); the Job body runs as the app SP.
        try:
            return service.start_schema_run(req.catalog, req.schema_name, created_by=identity.actor,
                                            filters=req.filters)
        except SessionError as e:
            raise HTTPException(e.status_code, e.detail)

    @app.get("/api/schema-runs")
    def list_schema_runs(limit: int = 50, offset: int = 0):
        return {"schema_runs": service.list_schema_runs(limit=limit, offset=offset)}

    @app.get("/api/schema-runs/{run_id}")
    def get_schema_run(run_id: str):
        run = service.get_schema_run(run_id)
        if run is None:
            raise HTTPException(404, "schema run not found")
        return run

    @app.post("/api/schema-runs/{run_id}/cancel")
    def cancel_schema_run(run_id: str):
        try:
            service.cancel_schema_run(run_id)
        except SessionError as e:
            raise HTTPException(e.status_code, e.detail)
        return {"id": run_id, "status": "cancelled"}

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str):
        data = service.get(session_id)
        if data is None:
            raise HTTPException(404, "session not found")
        return data

    @app.post("/api/sessions/{session_id}/answers", status_code=202)
    def answers(session_id: str, req: AnswersRequest, background: BackgroundTasks):
        actor = _actor()
        items = [Answer(question_id=a.question_id, text=a.text) for a in req.answers]
        try:
            service.submit_answers(session_id, items, created_by=actor)
        except SessionError as e:
            raise HTTPException(e.status_code, e.detail)
        background.add_task(service.execute_resume, session_id, items, actor)
        return {"session_id": session_id, "status": "resuming"}

    @app.post("/api/sessions/{session_id}/regenerate", status_code=202)
    def regenerate(session_id: str, req: RegenerateRequest, background: BackgroundTasks):
        # Regenerate is reasoning, not apply → runs as the app SP (D48: only apply uses the user).
        actor = _actor()
        targets = None if req.all else req.targets
        try:
            service.start_regenerate(session_id, targets, created_by=actor)
        except SessionError as e:
            raise HTTPException(e.status_code, e.detail)
        background.add_task(service.execute_regenerate, session_id, targets, actor)
        return {"session_id": session_id, "status": "regenerating"}

    @app.post("/api/sessions/{session_id}/drafts/{target_name}/review")
    def review(session_id: str, target_name: str, req: ReviewRequest):
        try:
            service.review_draft(session_id, target_name, req.action,
                                 created_by=_actor(), proposed_comment=req.proposed_comment)
        except SessionError as e:
            raise HTTPException(e.status_code, e.detail)
        return {"session_id": session_id, "target": target_name, "action": req.action}

    @app.post("/api/sessions/{session_id}/approve-high-confidence")
    def approve_high_confidence(session_id: str):
        # Bulk-approve is a review action (not apply) → runs as the app SP (D48). Approves only the
        # high-confidence, unflagged drafts (LLD-amend-007 §3); flagged drafts still need explicit
        # review, and nothing is written to UC (approve != apply).
        try:
            return service.approve_high_confidence(session_id, created_by=_actor())
        except SessionError as e:
            raise HTTPException(e.status_code, e.detail)

    @app.post("/api/sessions/{session_id}/apply")
    def apply(session_id: str, identity: RequestIdentity = Depends(_identity)):
        # Apply runs as the END USER (D48): the apply audit row records identity.actor, and the
        # OBO token (identity.access_token) authorizes the UC write — consumed by the Applier in U85.
        if service.applier is None:
            return JSONResponse(status_code=501,
                                content={"detail": "UC apply write-path not yet wired (U6/D21)"})
        return service.applier.apply(session_id, created_by=identity.actor,
                                     access_token=identity.access_token)

    # Serve the built React SPA (U23 §4) at "/" — registered LAST so the /api + /health
    # routes above take precedence. Guarded so the hermetic tests need no `vite build`.
    if static_dir and os.path.isdir(static_dir):
        app.mount('/', StaticFiles(directory=static_dir, html=True), name='spa')

    return app


# ─────────────────────────────────────────────────────────────────────────────
# Real wiring (lazy infra imports; integration-verified at deploy, not unit-tested)
# ─────────────────────────────────────────────────────────────────────────────
def build_context_providers(sql_runner: Any, mcp_providers: Any, mcp_session_factory: Any,
                            *, library_lookup: Any = None) -> list:
    """The ContextGatherer's providers: built-in lineage + query-history, the optional
    reuse-on-generation `LibraryProvider` (D52 §A4 — when a `library_lookup` is wired), plus one
    `McpContextProvider` per enabled `app.yaml` MCP config (D39/D34 — declared via
    `GENIEFY_MCP_PROVIDERS`, discovered at startup). Hermetic; unit-tested with fakes."""
    from geniefy_core.context import (
        LibraryProvider, LineageProvider, McpContextProvider, QueryHistoryProvider,
    )

    providers: list = [LineageProvider(sql_runner), QueryHistoryProvider(sql_runner)]
    if library_lookup is not None:  # suggestion-only reuse of previously-approved wording (D52)
        providers.append(LibraryProvider(library_lookup))
    for p in mcp_providers or []:
        if p.get('enabled', True):
            providers.append(McpContextProvider(p, mcp_session_factory))
    return providers


def build_service(config: Any) -> SessionService:
    """Wire the production ``SessionService`` from an ``AppConfig``: the FMAPI transport →
    LLMClient → Reasoner/Judge; the warehouse runner → in-app ProfileProvider + built-in
    lineage/query-history context; the Gate; and a live Lakebase ``SessionStore``. All
    infra imports are lazy so the hermetic suite never needs the SDK/driver; validated
    live at deploy (like the U47 dev round-trip)."""
    from geniefy_app.apply import Applier
    from geniefy_app.mcp import databricks_mcp_session
    from geniefy_app.profiling import InAppProfileProvider
    from geniefy_app.providers import (
        FmapiChatTransport, WarehouseSqlRunner, databricks_chat_query, databricks_sql_execute,
        make_obo_sql_execute,
    )
    from geniefy_app.store import SessionStore
    from geniefy_core.context import ContextGatherer
    from geniefy_core.judge import Judge
    from geniefy_core.llm import LLMClient
    from geniefy_core.profiler import Profiler
    from geniefy_core.reasoner import Reasoner
    from geniefy_core.template import default_template

    rc = config.run_config
    sql = WarehouseSqlRunner(databricks_sql_execute(warehouse_id=config.warehouse_id))
    llm = LLMClient(
        FmapiChatTransport(databricks_chat_query()),
        model_endpoint=config.model_endpoint,
        default_temperature=rc.llm_temperature,      # app.yaml-configurable (U81/NFR-A)
        default_max_tokens=rc.default_max_tokens,
        max_retries=rc.max_retries,                  # 429/backoff budget (D47)
        backoff_base=rc.backoff_base,
    )
    template = default_template()
    profiler = Profiler(InAppProfileProvider(sql), profile_batch_size=rc.profile_batch_size)
    # Lakebase SessionStore with a fresh per-call connection (D49/U93: Autoscaling scale-to-zero +
    # ~1h OAuth expiry, D11, make a cached conn unreliable). Created BEFORE the gatherer so the
    # reuse-on-generation LibraryProvider (D52 §A4) can query the comment library as the SP.
    store = SessionStore(schema=config.pg_schema, connect=lambda: _lakebase_connection(config))
    # built-in providers + reuse-on-generation (D52) + the app.yaml-declared MCP servers (D39)
    gatherer = ContextGatherer(
        build_context_providers(
            sql, config.mcp_providers, databricks_mcp_session,
            library_lookup=lambda scope, keys: store.list_library_for_reuse(
                scope=scope, match_keys=list(keys))),
        context_token_budget=rc.context_token_budget,
    )
    reasoner = Reasoner(llm, template, reason_batch_size=rc.reason_batch_size,
                        max_input_tokens=rc.max_input_tokens_per_call,
                        table_max_tokens=rc.reason_table_max_tokens,      # per-phase output budgets (D43/E1)
                        column_max_tokens=rc.reason_column_max_tokens)
    judge = Judge(llm, template, max_input_tokens=rc.max_input_tokens_per_call)
    gate = Gate(rc)

    def make_orchestrator(tracer: Any) -> DocumentationOrchestrator:
        return DocumentationOrchestrator(profiler=profiler, gatherer=gatherer, reasoner=reasoner,
                                         judge=judge, gate=gate, config=rc, tracer=tracer)

    # The UC apply write-path (U53/U85) runs ON-BEHALF-OF the end user (D48): the read+write
    # SQL is authenticated with the user's OBO token on the same warehouse, so the write honors
    # the user's UC grants (root fix for E9). `make_user_sql(token)` builds that per-request
    # runner; if OBO isn't enabled (no token), the Applier fails each item rather than writing
    # as the SP. (Profiling/context above stay on the SP `sql` runner.)
    obo = make_obo_sql_execute(warehouse_id=config.warehouse_id)
    return SessionService(make_orchestrator=make_orchestrator, store=store, config=rc,
                          applier=Applier(sql, store,
                                          make_user_sql=lambda tok: WarehouseSqlRunner(obo(tok))),
                          # hands-off (D51): SP warehouse runner for enumeration + the Job trigger/cancel
                          sql_runner=sql,
                          run_schema_job=_make_run_schema_job(config),
                          cancel_schema_job=_make_cancel_schema_job())


def _lakebase_connection(config: Any):
    """Open a psycopg2 connection to Lakebase. In a Databricks App with a Lakebase database
    resource binding, the platform injects ``PGHOST``/``PGPORT``/``PGDATABASE``/``PGUSER``/
    ``PGSSLMODE`` (PGUSER = the app SP's client-id role) — prefer those, the documented
    Apps↔Lakebase contract; fall back to the ``GENIEFY_*`` config + the SDK identity for
    non-App contexts. Password = a short-lived OAuth DB credential (the U47 flow); refresh/
    pool is deferred hardening (D11, ~1h expiry)."""
    import os
    import psycopg2  # lazy
    from databricks.sdk import WorkspaceClient

    from geniefy_app.providers import lakebase_db_token  # cross-runtime token (U123)

    w = WorkspaceClient()
    host = os.environ.get("PGHOST") or config.pg_host
    port = int(os.environ.get("PGPORT") or 5432)
    database = os.environ.get("PGDATABASE") or config.pg_database
    user = os.environ.get("PGUSER") or w.current_user.me().user_name
    sslmode = os.environ.get("PGSSLMODE") or "require"
    # App runtime: the SP OAuth token (via the binding) works. Job cluster: no oauth_token() → mint a
    # Lakebase credential via the postgres credentials API for the endpoint path (U123/U113 finding).
    endpoint = os.environ.get("GENIEFY_LAKEBASE_ENDPOINT") or getattr(config, "lakebase_endpoint", None)
    token = lakebase_db_token(w, endpoint=endpoint)
    return psycopg2.connect(host=host, port=port, dbname=database, user=user,
                            password=token, sslmode=sslmode)


def _make_run_schema_job(config: Any):
    """Return ``(run_id, catalog, schema, filters) -> job_run_id`` that triggers the standalone
    **`geniefy_schema_run` Job** (D54/U119 — its OWN bundle, deployed independently so the app's
    grants are never touched) with the run parameters. Lazy SDK; the job is resolved by
    ``GENIEFY_SCHEMA_RUN_JOB_ID`` (config) or by name (``geniefy…schema…run``);
    integration-verified at deploy (U113), not in the hermetic suite."""
    import json as _json

    def run(run_id: str, catalog: str, schema: str, filters: dict[str, Any]) -> Any:
        from databricks.sdk import WorkspaceClient  # lazy
        w = WorkspaceClient()
        job_id = getattr(config, "schema_run_job_id", None)
        if not job_id:
            for j in w.jobs.list():
                name = ((j.settings.name if getattr(j, "settings", None) else None) or "").lower()
                if "schema-run" in name or "schema_run" in name:
                    job_id = j.job_id
                    break
        if not job_id:
            raise RuntimeError("geniefy_schema_run job not found (deploy it: ./deploy_jobs.sh)")
        # Override only the run-specific job parameters; config params (model_endpoint/warehouse_id/
        # pg_host/pg_database) use the standalone geniefy_schema_run job's declared defaults (its
        # bundle vars, D54/U119). The entrypoint no-ops when schema_run_id is empty.
        resp = w.jobs.run_now(job_id=job_id, job_parameters={
            "schema_run_id": run_id, "catalog": catalog,
            "schema": schema, "filters": _json.dumps(filters or {})})
        return getattr(resp, "run_id", None)

    return run


def _make_cancel_schema_job():
    """Return ``(job_run_id) -> None`` that cancels a Job run (best-effort, lazy SDK)."""
    def cancel(job_run_id: Any) -> None:
        from databricks.sdk import WorkspaceClient  # lazy
        WorkspaceClient().jobs.cancel_run(run_id=int(job_run_id))
    return cancel
