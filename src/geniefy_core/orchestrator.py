"""geniefy-v3 agent core — DocumentationOrchestrator (U41).

The keystone (``LLM-agent-core.md`` §2/§4): a deterministic, plain-Python loop that
sequences the components into the agent. It takes a target + a serializable
``SessionState`` and returns a ``StepResult`` — the core holds no state between calls
(D17), so pause/resume and history are just data.

    run(target):  PROFILING → GATHERING → REASONING → JUDGING → GATING
                    ├─ interactive & any needs_input → NeedsInput(questions, state)   [pause]
                    └─ else                          → ReadyForReview(state)
    resume(state, answers):  merge answers as evidence → re-reason ONLY affected drafts
                    → re-judge → re-gate  (profiling/gathering are NOT re-run, D17)

Hard signals for the Gate (U4 §3.6) are derived here from the profile + schema_meta +
context. Phase is persisted on the state so a crash/pause resumes at the right step
(U4 §4). All model/IO lives in the injected components (Profiler/Gatherer/Reasoner/
Judge/Gate) — the orchestrator itself is hermetic (D1). An optional ``tracer`` carries
MLflow spans (U4 §6) at the app layer; the default is a no-op.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Protocol

from geniefy_core.gate import Gate, HardSignals
from geniefy_core.profiler import ProfileError, Profiler, SampleSpec
from geniefy_core.state import (
    Answer,
    Phase,
    RunConfig,
    RunError,
    SessionMode,
    SessionState,
    StepResult,
)

HIGH_NULL_FRACTION = 0.5
KEYLIKE_RATIO = 0.98
_AMBIGUOUS_NAME_MAXLEN = 3  # very short names are low-semantic-content (heuristic)


class Tracer(Protocol):
    def span(self, name: str): ...  # returns a context manager


class NullTracer:
    @contextmanager
    def span(self, name: str):
        yield


class DocumentationOrchestrator:
    """Sequences the agent-core components (U4 §2). Components are injected; tests use
    fakes (with fake model transports), the app wires real ones."""

    def __init__(
        self,
        *,
        profiler: Profiler,
        gatherer: Any,   # ContextGatherer (duck-typed: .gather_into(state))
        reasoner: Any,   # Reasoner (.draft(state))
        judge: Any,      # Judge (.score(state))
        gate: Gate,
        config: RunConfig,
        tracer: Tracer | None = None,
    ):
        self.profiler = profiler
        self.gatherer = gatherer
        self.reasoner = reasoner
        self.judge = judge
        self.gate = gate
        self.config = config
        self.tracer = tracer or NullTracer()

    # -- run -----------------------------------------------------------------
    def run(self, target: str, state: SessionState | None = None) -> StepResult:
        state = state or SessionState(target=str(target), config=self.config,
                                      template_id=self.config.template_id)

        # PROFILING (a profiling failure is unrecoverable for this run — U4 §8)
        state.phase = Phase.PROFILING
        try:
            with self.tracer.span("profile"):
                result = self.profiler.profile(state.target, sample=SampleSpec(self.config.sample_mode))
        except ProfileError as exc:
            return self._fail(state, exc.code, exc.message)
        state.profile = (result.profile.raw or {})
        state.schema_meta = _schema_meta_dict(result.schema_meta)

        # GATHERING (degrades internally — never aborts; U4 §8)
        with self.tracer.span("gather_context"):
            self.gatherer.gather_into(state)

        # REASONING → JUDGING (each isolates its own per-item errors)
        with self.tracer.span("reason"):
            self.reasoner.draft(state)
        with self.tracer.span("judge"):
            self.judge.score(state)

        return self._gate(state)

    # -- resume --------------------------------------------------------------
    def resume(self, state: SessionState, answers: list[Answer]) -> StepResult:
        """Fold the human's answers into the evidence and re-reason ONLY the affected
        drafts (profiling/gathering are not re-run — D17), then re-judge + re-gate."""
        affected = self._merge_answers(state, answers)
        if affected:
            with self.tracer.span("reason_resume"):
                self._redraft(state, affected)
        return self._gate(state)

    # -- regenerate (E3/D45) -------------------------------------------------
    def regenerate(self, state: SessionState, targets: list[str] | None) -> StepResult:
        """Re-reason → re-judge → re-gate ONLY the named ``targets`` (column names and/or
        the table sentinel ``"__table__"``), reusing the existing profile + context — no
        re-profiling/gathering (D17), exactly like ``resume``. Fresh drafts replace the named
        ones; every other draft keeps its current review state. ``targets`` of ``None``/empty
        or containing ``"__all__"`` regenerates everything (table + all columns)."""
        affected = self._targets_to_affected(state, targets)
        if affected:
            with self.tracer.span("reason_regenerate"):
                self._redraft(state, affected)
        return self._gate(state)

    def _targets_to_affected(self, state: SessionState, targets: list[str] | None) -> set[str | None]:
        """Map request target strings → the affected-draft set (``None`` = table). ``None``/empty
        or ``"__all__"`` ⇒ the table + every profiled column."""
        cols = [c.get("name") for c in ((state.profile or {}).get("columns") or [])]
        if not targets or "__all__" in targets:
            return {None, *cols}
        affected: set[str | None] = set()
        for t in targets:
            affected.add(None if t in ("__table__", "", None) else t)
        return affected

    # -- shared gating -------------------------------------------------------
    def _gate(self, state: SessionState) -> StepResult:
        with self.tracer.span("gate"):
            state.phase = Phase.GATING
            questions = self.gate.apply(state, self._signals(state))
        # Hands-off (D51) gates like interactive: it PRODUCES + persists the clarifying questions
        # (with suggested answers) as awaiting_input so a human can answer + resume later — it simply
        # never blocks (the batch Job persists the returned state and moves to the next table). Batch
        # mode (no human in the loop) skips questions and auto-keeps the best draft. (Apply is never
        # done by the orchestrator in ANY mode — it's the separate, human-only Applier, D7/D48.)
        produces_questions = self.config.mode in (SessionMode.INTERACTIVE, SessionMode.HANDS_OFF)
        if produces_questions and questions:
            # pre-propose answers so the reviewer accepts/edits instead of typing (D50/U100);
            # best-effort — a suggestion failure must not block.
            suggest = getattr(self.reasoner, "suggest_answers", None)
            if suggest is not None:
                with self.tracer.span("suggest_answers"):
                    try:
                        suggest(state, questions)
                    except Exception:
                        pass
            state.phase = Phase.AWAITING_INPUT
            return StepResult.needs_input(questions, state)
        state.phase = Phase.READY_FOR_REVIEW
        return StepResult.ready_for_review(state)

    def _fail(self, state: SessionState, code: str, message: str) -> StepResult:
        state.phase = Phase.FAILED
        return StepResult.failed(RunError(code, message), state)

    # -- resume helpers ------------------------------------------------------
    def _merge_answers(self, state: SessionState, answers: list[Answer]) -> set[str | None]:
        """Append each answer as a `user_answer` context snippet (now part of the
        evidence) and mark its question answered. Returns the set of affected draft
        targets (column name, or None for the table)."""
        by_qid = {q.id: q for q in state.open_questions}
        affected: set[str | None] = set()
        for ans in answers:
            q = by_qid.get(ans.question_id)
            if q is None:
                continue
            q.answered = True
            target = q.target_name  # None ⇒ table
            state.context.append({
                "source": "user_answer",
                "text": f"Operator answer about {q.target_name or 'the table'}: {ans.text}",
                "informs": [q.target_name] if q.target_name else [],
                "relevance": 1.0,
            })
            affected.add(target)
        return affected

    def _redraft(self, state: SessionState, affected: set[str | None]) -> None:
        """Re-reason + re-judge only the affected drafts, on a sub-state that carries the
        full table block + just the affected columns + the (answer-augmented) context.
        Merge the fresh drafts back into ``state``."""
        profile = state.profile or {}
        cols = profile.get("columns") or []
        sub_cols = [c for c in cols if c.get("name") in affected]
        include_table = None in affected
        sub_profile = {
            "profile_schema_version": profile.get("profile_schema_version"),
            "table": profile.get("table") or {},
            "columns": sub_cols,
        }
        sub = SessionState(target=state.target, config=state.config, template_id=state.template_id,
                           profile=sub_profile, schema_meta=state.schema_meta, context=list(state.context))
        self.reasoner.draft(sub)
        self.judge.score(sub)

        if include_table and sub.table_draft is not None:
            state.table_draft = sub.table_draft
        fresh = {c.column_name: c for c in sub.column_drafts}
        state.column_drafts = [fresh.get(c.column_name, c) for c in state.column_drafts]

    # -- hard-signal derivation for the Gate (U4 §3.6) -----------------------
    def _signals(self, state: SessionState) -> dict[str | None, HardSignals]:
        """Derive each draft's :class:`HardSignals` from the profile + schema_meta +
        context. Heuristics (tunable): high null > 0.5; keylike cardinality ≥ 0.98;
        usage/decode evidence = a context snippet that informs the column; key target =
        a declared FK or a column-lineage hint; ambiguous = a very short/low-semantic
        name with no usage evidence."""
        profile = state.profile or {}
        schema_meta = state.schema_meta or {}
        fk_cols = {c for fk in (schema_meta.get("foreign_keys") or []) for c in (fk.get("columns") or [])}
        informed: set[str] = set()
        for snip in (state.context or []):
            for name in (snip.get("informs") or []):
                informed.add(name)

        signals: dict[str | None, HardSignals] = {None: HardSignals()}  # table: route on confidence
        for col in profile.get("columns") or []:
            name = col.get("name")
            usage = name in informed
            null_frac = col.get("null_fraction")
            ratio = col.get("cardinality_ratio")
            signals[name] = HardSignals(
                ambiguous_name=(_is_ambiguous(name) and not usage),
                has_usage_evidence=usage,
                high_null_fraction=(null_frac is not None and null_frac > HIGH_NULL_FRACTION),
                enum_candidate=bool(col.get("is_enum_candidate")),
                has_decode_source=usage,  # v1: context that informs a column can decode it
                keylike=(ratio is not None and ratio >= KEYLIKE_RATIO),
                has_key_target=(name in fk_cols or usage),
            )
        return signals


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────
def _is_ambiguous(name: str | None) -> bool:
    if not name:
        return True
    n = name.strip("_")
    return len(n) <= _AMBIGUOUS_NAME_MAXLEN


def _schema_meta_dict(sm: Any) -> dict[str, Any] | None:
    """Serialize a Profiler ``SchemaMeta`` (or None) to the dict the Reasoner/Judge read."""
    if sm is None:
        return None
    return {
        "primary_key": list(getattr(sm, "primary_key", []) or []),
        "foreign_keys": [
            {"columns": list(fk.columns), "referenced_table": fk.referenced_table,
             "referenced_columns": list(fk.referenced_columns)}
            for fk in (getattr(sm, "foreign_keys", []) or [])
        ],
        "partition_columns": list(getattr(sm, "partition_columns", []) or []),
        "columns": [
            {"name": c.name, "ordinal": c.ordinal, "data_type": c.data_type, "nullable": c.nullable}
            for c in (getattr(sm, "columns", []) or [])
        ],
    }
