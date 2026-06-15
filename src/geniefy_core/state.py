"""geniefy-v3 agent core — domain model & SessionState (U27).

The serializable spine the whole core hangs on (``LLD-agent-core.md`` §2/§7, D17):
``RunConfig`` (tunables), ``SessionState`` (the pause/resume snapshot), the draft /
``Question`` / ``Answer`` models, and the ``StepResult`` the orchestrator returns.

Design constraints:
  - **Serializable, no behavior** (D17): the core holds no session in memory between
    calls — a ``SessionState`` is passed in and returned; the caller persists it to
    Lakebase (U2). So every type here round-trips through ``to_dict``/``from_dict``
    (JSON/JSONB), enums serialize to their string ``value``, and the shapes map 1:1 to
    the U2 tables (``sessions`` / ``table_drafts`` / ``column_drafts`` /
    ``session_messages``) and the migration enums (incl. the U10/U11 amendments).
  - **No I/O, no infra deps** (D1): pure dataclasses + stdlib.

Enum values are kept identical to ``migrations/001_init.sql`` so a ``SessionState``
serializes straight into the schema. Orchestration phases roll up to the persisted
``session_status`` per U10 F3 (``judging``/``gating`` → ``reasoning``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


# ─────────────────────────────────────────────────────────────────────────────
# Enums — values mirror migrations/001_init.sql (U2 + U10/U11 amendments)
# ─────────────────────────────────────────────────────────────────────────────
class SessionMode(str, Enum):
    INTERACTIVE = "interactive"
    BATCH = "batch"
    HANDS_OFF = "hands_off"  # schema-batch: generate + persist questions, never block/apply (D51/U108)


class SessionStatus(str, Enum):
    """Persisted session status (U2 §5 enum)."""

    CREATED = "created"
    PROFILING = "profiling"
    GATHERING_CONTEXT = "gathering_context"
    REASONING = "reasoning"
    AWAITING_INPUT = "awaiting_input"
    READY_FOR_REVIEW = "ready_for_review"
    APPLYING = "applying"
    APPLIED = "applied"
    PAUSED = "paused"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Phase(str, Enum):
    """The orchestrator's internal phase cursor (U4 §4). A superset of
    ``SessionStatus`` — ``judging``/``gating`` roll up to ``reasoning`` when persisted
    (U10 F3)."""

    CREATED = "created"
    PROFILING = "profiling"
    GATHERING_CONTEXT = "gathering_context"
    REASONING = "reasoning"
    JUDGING = "judging"
    GATING = "gating"
    AWAITING_INPUT = "awaiting_input"
    READY_FOR_REVIEW = "ready_for_review"
    APPLYING = "applying"
    APPLIED = "applied"
    PAUSED = "paused"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def to_session_status(self) -> SessionStatus:
        """Map the internal phase to the persisted status (U10 F3: many-to-one)."""
        rollup = {Phase.JUDGING: SessionStatus.REASONING, Phase.GATING: SessionStatus.REASONING}
        if self in rollup:  # don't eval SessionStatus(self.value) for phases it lacks
            return rollup[self]
        return SessionStatus(self.value)


class DraftStatus(str, Enum):
    """Review-lifecycle axis (U2 + U10 F2)."""

    DRAFT = "draft"
    NEEDS_INPUT = "needs_input"
    LOW_CONFIDENCE = "low_confidence"
    ERROR = "error"
    REVIEWED = "reviewed"
    EDITED = "edited"
    APPROVED = "approved"
    APPLIED = "applied"
    REJECTED = "rejected"


class ApplyStatus(str, Enum):
    """Per-item apply-outcome axis (U10 F2 / U11)."""

    NOT_APPLIED = "not_applied"
    APPLIED = "applied"
    CONFLICT = "conflict"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    SKIPPED_NOOP = "skipped_noop"


class DraftKind(str, Enum):
    TABLE = "table"
    COLUMN = "column"


# ─────────────────────────────────────────────────────────────────────────────
# RunConfig (U4 §7 + the D32 token-budget fields)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class RunConfig:
    """Tunables for one run. Everything is parameterized — no hardcoded infra (D12);
    sourced from ``app.yaml`` env at startup (D33)."""

    model_endpoint: str
    mode: SessionMode = SessionMode.INTERACTIVE
    template_id: str | None = None
    sample_mode: str = "auto"  # default sampling intent (U3 §4.1/§6)
    keep_threshold: float = 0.75  # Gate keep cutoff (U4 §3.6)
    profile_batch_size: int = 50  # U4 §3.2
    reason_batch_size: int = 25  # U4 §3.4
    context_token_budget: int = 4000  # U4 §3.3
    enabled_providers: list[str] = field(default_factory=list)  # snapshot of U2 context_providers
    # Cost predictability (D32 / NFR-B): per-call input-token budget + summarization.
    max_input_tokens_per_call: int | None = None
    summarize_over_budget: bool = True
    summary_target_tokens: int | None = None
    # LLM tunables — surfaced to app.yaml so they're tunable without a code change
    # (D33/NFR-A; the "push max retries/tokens/temperature into app.yaml" ask, U81).
    max_retries: int = 5  # 429/rate-limit retry budget (D47/U80)
    backoff_base: float = 0.5  # full-jitter backoff base seconds (D47/U80)
    llm_temperature: float = 0.0  # default sampling temperature (deterministic drafts)
    default_max_tokens: int = 4096  # default per-call output cap (U77)
    reason_table_max_tokens: int = 20000  # richer table comment, per-phase budget (D43/E1)
    reason_column_max_tokens: int = 2000  # per column-batch budget (D43/E1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_endpoint": self.model_endpoint,
            "mode": self.mode.value,
            "template_id": self.template_id,
            "sample_mode": self.sample_mode,
            "keep_threshold": self.keep_threshold,
            "profile_batch_size": self.profile_batch_size,
            "reason_batch_size": self.reason_batch_size,
            "context_token_budget": self.context_token_budget,
            "enabled_providers": list(self.enabled_providers),
            "max_input_tokens_per_call": self.max_input_tokens_per_call,
            "summarize_over_budget": self.summarize_over_budget,
            "summary_target_tokens": self.summary_target_tokens,
            "max_retries": self.max_retries,
            "backoff_base": self.backoff_base,
            "llm_temperature": self.llm_temperature,
            "default_max_tokens": self.default_max_tokens,
            "reason_table_max_tokens": self.reason_table_max_tokens,
            "reason_column_max_tokens": self.reason_column_max_tokens,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "RunConfig":
        return cls(
            model_endpoint=d["model_endpoint"],
            mode=SessionMode(d.get("mode", "interactive")),
            template_id=d.get("template_id"),
            sample_mode=d.get("sample_mode", "auto"),
            keep_threshold=d.get("keep_threshold", 0.75),
            profile_batch_size=d.get("profile_batch_size", 50),
            reason_batch_size=d.get("reason_batch_size", 25),
            context_token_budget=d.get("context_token_budget", 4000),
            enabled_providers=list(d.get("enabled_providers") or []),
            max_input_tokens_per_call=d.get("max_input_tokens_per_call"),
            summarize_over_budget=d.get("summarize_over_budget", True),
            summary_target_tokens=d.get("summary_target_tokens"),
            max_retries=d.get("max_retries", 5),
            backoff_base=d.get("backoff_base", 0.5),
            llm_temperature=d.get("llm_temperature", 0.0),
            default_max_tokens=d.get("default_max_tokens", 4096),
            reason_table_max_tokens=d.get("reason_table_max_tokens", 20000),
            reason_column_max_tokens=d.get("reason_column_max_tokens", 2000),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Drafts (→ U2 table_drafts / column_drafts)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class TableDraft:
    current_comment: str | None = None
    proposed_comment: str | None = None
    rationale: str | None = None
    confidence: float | None = None  # the Judge's authoritative overall (U4 §3.5)
    judge_scores: dict[str, Any] | None = None
    evidence_refs: list[str] = field(default_factory=list)  # U4 §3.4 / U9 explainability
    tags: list[str] = field(default_factory=list)  # free-form LLM tags, pills (D53/U104)
    # Structured steward facts for the hero chips (owner/freshness/grain/keys/sensitivity, D53 §B4/U114)
    # — UI-only; the prose `proposed_comment` remains the UC artifact. Generated in the same call.
    facts: dict[str, Any] | None = None
    status: DraftStatus = DraftStatus.DRAFT
    apply_status: ApplyStatus = ApplyStatus.NOT_APPLIED
    applied_comment: str | None = None
    applied_at: str | None = None
    applied_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = _draft_common_to_dict(self)
        d["facts"] = self.facts
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "TableDraft":
        obj = cls()
        _draft_common_from_dict(obj, d)
        obj.facts = d.get("facts")
        return obj


@dataclass
class ColumnDraft:
    column_name: str = ""
    ordinal: int | None = None
    data_type: str | None = None
    conditional_fields: dict[str, Any] | None = None  # template conditional fields filled (U25)
    current_comment: str | None = None
    proposed_comment: str | None = None
    rationale: str | None = None
    confidence: float | None = None
    judge_scores: dict[str, Any] | None = None
    evidence_refs: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)  # free-form LLM tags, pills (D53/U104)
    status: DraftStatus = DraftStatus.DRAFT
    apply_status: ApplyStatus = ApplyStatus.NOT_APPLIED
    applied_comment: str | None = None
    applied_at: str | None = None
    applied_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = _draft_common_to_dict(self)
        d.update(
            column_name=self.column_name,
            ordinal=self.ordinal,
            data_type=self.data_type,
            conditional_fields=self.conditional_fields,
        )
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ColumnDraft":
        obj = cls(
            column_name=d.get("column_name", ""),
            ordinal=d.get("ordinal"),
            data_type=d.get("data_type"),
            conditional_fields=d.get("conditional_fields"),
        )
        _draft_common_from_dict(obj, d)
        return obj


def _draft_common_to_dict(o: Any) -> dict[str, Any]:
    return {
        "current_comment": o.current_comment,
        "proposed_comment": o.proposed_comment,
        "rationale": o.rationale,
        "confidence": o.confidence,
        "judge_scores": o.judge_scores,
        "evidence_refs": list(o.evidence_refs),
        "tags": list(getattr(o, "tags", []) or []),
        "status": o.status.value,
        "apply_status": o.apply_status.value,
        "applied_comment": o.applied_comment,
        "applied_at": o.applied_at,
        "applied_by": o.applied_by,
    }


def _draft_common_from_dict(o: Any, d: Mapping[str, Any]) -> None:
    o.current_comment = d.get("current_comment")
    o.proposed_comment = d.get("proposed_comment")
    o.rationale = d.get("rationale")
    o.confidence = d.get("confidence")
    o.judge_scores = d.get("judge_scores")
    o.evidence_refs = list(d.get("evidence_refs") or [])
    o.tags = list(d.get("tags") or [])
    o.status = DraftStatus(d.get("status", "draft"))
    o.apply_status = ApplyStatus(d.get("apply_status", "not_applied"))
    o.applied_comment = d.get("applied_comment")
    o.applied_at = d.get("applied_at")
    o.applied_by = d.get("applied_by")


# ─────────────────────────────────────────────────────────────────────────────
# Interactive Q&A (→ U2 session_messages) + StepResult
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Question:
    """A targeted, answerable prompt the Gate raises for a low-confidence item (U4 §3.6)."""

    id: str
    target_kind: DraftKind  # table | column
    target_name: str | None  # column name (None for the table)
    text: str
    answered: bool = False
    suggested_answer: str | None = None  # LLM-proposed answer to pre-fill the review box (D50/U100)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target_kind": self.target_kind.value,
            "target_name": self.target_name,
            "text": self.text,
            "answered": self.answered,
            "suggested_answer": self.suggested_answer,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Question":
        return cls(
            id=d["id"],
            target_kind=DraftKind(d.get("target_kind", "column")),
            target_name=d.get("target_name"),
            text=d["text"],
            answered=bool(d.get("answered", False)),
            suggested_answer=d.get("suggested_answer"),
        )


@dataclass
class Answer:
    question_id: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"question_id": self.question_id, "text": self.text}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Answer":
        return cls(question_id=d["question_id"], text=d["text"])


@dataclass
class RunError:
    code: str  # e.g. permission_denied | timeout | provider_error (U3 §9 / U4 §8)
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "RunError":
        return cls(code=d.get("code", "error"), message=d.get("message", ""))


@dataclass
class StepResult:
    """What ``run``/``resume`` return (U4 §2). ``kind`` ∈ needs_input | ready_for_review
    | failed; payload fields are populated per kind; ``state`` always travels with it so
    the caller can persist (D17)."""

    kind: str
    state: "SessionState"
    questions: list[Question] = field(default_factory=list)  # kind=needs_input
    error: RunError | None = None  # kind=failed

    # constructors mirroring U4 §2's NeedsInput / ReadyForReview / Failed
    @classmethod
    def needs_input(cls, questions: list[Question], state: "SessionState") -> "StepResult":
        return cls(kind="needs_input", state=state, questions=questions)

    @classmethod
    def ready_for_review(cls, state: "SessionState") -> "StepResult":
        return cls(kind="ready_for_review", state=state)

    @classmethod
    def failed(cls, error: RunError, state: "SessionState") -> "StepResult":
        return cls(kind="failed", state=state, error=error)


# ─────────────────────────────────────────────────────────────────────────────
# SessionState (U4 §2, D17) — the pause/resume snapshot
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class SessionState:
    """The serializable working state the orchestrator takes and returns. Maps 1:1 to
    the U2 tables; the whole thing also rehydrates from ``sessions.session_state`` jsonb
    (U10 F5). The agent core never persists it — the caller does (D17)."""

    target: str  # fully-qualified catalog.schema.table
    config: RunConfig
    session_id: str | None = None
    template_id: str | None = None
    phase: Phase = Phase.CREATED
    profile: dict[str, Any] | None = None  # sanitized profile snapshot (U3 §4.2)
    schema_meta: dict[str, Any] | None = None  # declared PK/FK/cols (U4 §3.2)
    context: list[dict[str, Any]] = field(default_factory=list)  # ContextSnippets (U4 §3.3)
    table_draft: TableDraft | None = None
    column_drafts: list[ColumnDraft] = field(default_factory=list)
    open_questions: list[Question] = field(default_factory=list)
    mlflow_run_id: str | None = None

    @property
    def session_status(self) -> SessionStatus:
        """The persisted status for this phase (U10 F3 rollup)."""
        return self.phase.to_session_status()

    def column_draft(self, name: str) -> ColumnDraft | None:
        for c in self.column_drafts:
            if c.column_name == name:
                return c
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "config": self.config.to_dict(),
            "session_id": self.session_id,
            "template_id": self.template_id,
            "phase": self.phase.value,
            "profile": self.profile,
            "schema_meta": self.schema_meta,
            "context": list(self.context),
            "table_draft": self.table_draft.to_dict() if self.table_draft else None,
            "column_drafts": [c.to_dict() for c in self.column_drafts],
            "open_questions": [q.to_dict() for q in self.open_questions],
            "mlflow_run_id": self.mlflow_run_id,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "SessionState":
        td = d.get("table_draft")
        return cls(
            target=d["target"],
            config=RunConfig.from_dict(d["config"]),
            session_id=d.get("session_id"),
            template_id=d.get("template_id"),
            phase=Phase(d.get("phase", "created")),
            profile=d.get("profile"),
            schema_meta=d.get("schema_meta"),
            context=list(d.get("context") or []),
            table_draft=TableDraft.from_dict(td) if td else None,
            column_drafts=[ColumnDraft.from_dict(c) for c in (d.get("column_drafts") or [])],
            open_questions=[Question.from_dict(q) for q in (d.get("open_questions") or [])],
            mlflow_run_id=d.get("mlflow_run_id"),
        )
