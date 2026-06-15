"""geniefy-v3 agent core — Gate (U30).

Routes each draft to **keep** or **needs_input** using the Judge's confidence *plus*
hard signals (``LLD-agent-core.md`` §3.6). The Judge's ``overall`` is the authoritative
confidence; the Gate adds deterministic "force-low" rules that override a high score
when the evidence is too thin to trust, and shapes the outcome by mode:

  - **interactive** AND **hands_off** → ``needs_input`` items become targeted ``Question``s
    (interactive pauses for them; hands-off persists them for a later answer+resume, D51);
  - **batch** → never ask: mark those drafts ``low_confidence`` with the reason and proceed.

Pure logic, no I/O (D1): inputs are drafts (from the spine, U27) + per-item
``HardSignals`` that upstream components (Profiler/ContextGatherer/Judge) derive from
the sanitized profile + schema_meta + context. The Gate never re-reads raw data.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from geniefy_core.state import (
    ColumnDraft,
    DraftKind,
    DraftStatus,
    Question,
    RunConfig,
    SessionMode,
    SessionState,
    TableDraft,
)

# Stable reason tokens for a force-low trip (also drive question text + UI explanations).
REASON_AMBIGUOUS_UNUSED_HIGH_NULL = "ambiguous_unused_high_null"
REASON_ENUM_NO_DECODE = "enum_no_decode"
REASON_KEYLIKE_NO_TARGET = "keylike_no_target"
REASON_BELOW_THRESHOLD = "below_threshold"
REASON_NO_CONFIDENCE = "no_confidence"


@dataclass
class HardSignals:
    """Per-draft signals the Gate's force-low rules key off (U4 §3.6). Derived upstream
    from the profile (U3), schema_meta (U4 §3.2), and context (U4 §3.3) — the Gate just
    applies the rules."""

    ambiguous_name: bool = False        # name not in glossary / low semantic content
    has_usage_evidence: bool = False    # query-history / lineage shows real usage
    high_null_fraction: bool = False    # mostly null
    enum_candidate: bool = False        # low-cardinality coded values
    has_decode_source: bool = False     # a glossary/context decodes the codes
    keylike: bool = False               # cardinality ratio ≈ 1.0 (looks like a key)
    has_key_target: bool = False        # lineage/constraint resolves the key's target


@dataclass
class GateOutcome:
    """The routing decision for one draft. ``reasons`` is empty for ``keep``; otherwise
    it lists the force-low trip(s) and/or ``below_threshold`` (for question text + UI)."""

    decision: str  # "keep" | "needs_input"
    reasons: list[str] = field(default_factory=list)

    @property
    def keep(self) -> bool:
        return self.decision == "keep"


class Gate:
    """Score-plus-hard-signals router (U4 §3.6)."""

    def __init__(self, config: RunConfig):
        self.config = config

    # -- per-item routing (pure) ------------------------------------------
    def hard_trips(self, signals: HardSignals) -> list[str]:
        """The force-low conditions that fire for these signals (U4 §3.6)."""
        trips: list[str] = []
        if signals.ambiguous_name and not signals.has_usage_evidence and signals.high_null_fraction:
            trips.append(REASON_AMBIGUOUS_UNUSED_HIGH_NULL)
        if signals.enum_candidate and not signals.has_decode_source:
            trips.append(REASON_ENUM_NO_DECODE)
        if signals.keylike and not signals.has_key_target:
            trips.append(REASON_KEYLIKE_NO_TARGET)
        return trips

    def route_item(self, confidence: float | None, signals: HardSignals) -> GateOutcome:
        """Route one draft: a hard-signal trip forces ``needs_input`` regardless of
        score; otherwise ``keep`` iff ``confidence >= keep_threshold``."""
        trips = self.hard_trips(signals)
        if trips:
            return GateOutcome("needs_input", trips)
        if confidence is None:
            return GateOutcome("needs_input", [REASON_NO_CONFIDENCE])
        if confidence < self.config.keep_threshold:
            return GateOutcome("needs_input", [REASON_BELOW_THRESHOLD])
        return GateOutcome("keep", [])

    # -- apply to a session (mutates drafts; returns interactive questions) -
    def apply(
        self,
        state: SessionState,
        signals: dict[str | None, HardSignals],
    ) -> list[Question]:
        """Route every draft in ``state`` (table draft keyed ``None``; column drafts by
        name). Mutates each draft's ``status`` and returns the ``Question``s to ask.
        **Interactive AND hands-off** produce targeted questions (hands-off persists them for a
        human to answer + resume later, D51 — it just doesn't block); **batch** asks nothing and
        marks ``low_confidence``. Drafts already resolved (approved/edited/reviewed/applied/
        rejected) are left untouched (idempotent re-gating, U4 §8)."""
        asks_questions = self.config.mode in (SessionMode.INTERACTIVE, SessionMode.HANDS_OFF)
        questions: list[Question] = []
        qid = 0

        items: list[tuple[str | None, DraftKind, TableDraft | ColumnDraft]] = []
        if state.table_draft is not None:
            items.append((None, DraftKind.TABLE, state.table_draft))
        for cd in state.column_drafts:
            items.append((cd.column_name, DraftKind.COLUMN, cd))

        for target_name, kind, draft in items:
            if not _is_open(draft.status):
                continue
            outcome = self.route_item(draft.confidence, signals.get(target_name, HardSignals()))
            if outcome.keep:
                # Leave the draft as-is for review; gating doesn't advance review state.
                continue
            if asks_questions:
                draft.status = DraftStatus.NEEDS_INPUT
                qid += 1
                q = Question(
                    id=f"q{qid}",
                    target_kind=kind,
                    target_name=target_name,
                    text=self.question_text(kind, target_name, outcome.reasons),
                )
                questions.append(q)
            else:
                draft.status = DraftStatus.LOW_CONFIDENCE
                draft.rationale = _append_reason(draft.rationale, outcome.reasons)

        state.open_questions = questions if asks_questions else []
        return questions

    # -- targeted question text (U4 §3.6 example) -------------------------
    @staticmethod
    def question_text(kind: DraftKind, target_name: str | None, reasons: list[str]) -> str:
        who = f"column `{target_name}`" if kind == DraftKind.COLUMN else "this table"
        if REASON_ENUM_NO_DECODE in reasons:
            return f"{who} holds coded values with no decode source — what do the codes mean?"
        if REASON_KEYLIKE_NO_TARGET in reasons:
            return f"{who} looks like a key but no lineage resolves its target — what does it reference?"
        if REASON_AMBIGUOUS_UNUSED_HIGH_NULL in reasons:
            return f"{who} has an ambiguous name, no usage history, and is mostly null — what is it for?"
        return f"What is the business meaning of {who}? (low confidence — needs confirmation)"


def _is_open(status: DraftStatus) -> bool:
    """A draft the Gate may still route (not yet human-resolved or errored)."""
    return status in (DraftStatus.DRAFT, DraftStatus.NEEDS_INPUT, DraftStatus.LOW_CONFIDENCE)


def _append_reason(existing: str | None, reasons: list[str]) -> str:
    note = "low confidence: " + ", ".join(reasons)
    return note if not existing else f"{existing}\n{note}"
