"""geniefy-v3 agent core — Judge (U37).

A **separate** model call that scores each draft against the Template rubric (U4 §3.5),
independent of the Reasoner (Rule-4 spirit: the maker doesn't grade itself in the same
pass). It is the unification point of D8 — its weighted ``overall`` is **both** the
authoritative per-item confidence the Gate routes on (not the Reasoner's
``self_confidence``) **and** the quality metric an eval harness reuses.

Design:
  - **Per-draft scoring** (U4 §6: one judge span per draft): table draft + each column
    draft scored individually against its evidence.
  - **Authoritative overall computed deterministically** — the model returns per-dimension
    subscores in [0,1] + ``issues``; the Judge computes ``overall`` as the
    template-rubric-weighted sum itself (not trusting a model-returned scalar). This keeps
    the confidence grounded in the template's weights (U25) and reproducible.
  - **Grounding check** (U4 §1/§3.5): the rubric's ``grounding`` dimension flags claims
    that don't trace to the provided evidence (e.g. "asserts FK→orders with no lineage").
  - **Per-draft error isolation** (U4 §8): a failed judge call marks only that draft.

Hermetic (D1): all model access via the injected ``LLMClient`` (U32). ``ERROR`` drafts
from the Reasoner are skipped.
"""
from __future__ import annotations

import json
from typing import Any

from geniefy_core.llm import ChatMessage, LLMClient, LLMError
from geniefy_core.state import (
    ColumnDraft,
    DraftStatus,
    Phase,
    SessionState,
    TableDraft,
)
from geniefy_core.template import Template


class Judge:
    """Independent rubric scorer (U4 §3.5)."""

    def __init__(self, llm: LLMClient, template: Template, *, max_input_tokens: int | None = None):
        self.llm = llm
        self.template = template
        self.max_input_tokens = max_input_tokens

    # -- public --------------------------------------------------------------
    def score(self, state: SessionState) -> None:
        """Score every non-error draft in ``state``, setting each draft's
        ``judge_scores`` (subscores + weighted ``overall`` + issues) and overriding
        ``confidence`` with the authoritative ``overall`` (D8). Sets ``phase = JUDGING``.
        Mutates ``state`` in place (caller persists, D17)."""
        state.phase = Phase.JUDGING
        profile = state.profile or {}
        cols_by_name = {c.get("name"): c for c in (profile.get("columns") or [])}

        if state.table_draft is not None and state.table_draft.status != DraftStatus.ERROR:
            evidence = {"table_profile": (profile.get("table") or {}), "context": state.context or []}
            self._score_one(state.table_draft, "table", evidence)

        for cd in state.column_drafts:
            if cd.status == DraftStatus.ERROR:
                continue
            evidence = {
                "column_profile": cols_by_name.get(cd.column_name, {}),
                "schema_meta": state.schema_meta or {},
                "context": state.context or [],
            }
            self._score_one(cd, "column", evidence)

    # -- one draft -----------------------------------------------------------
    def _score_one(self, draft: TableDraft | ColumnDraft, kind: str, evidence: dict[str, Any]) -> None:
        dims = self.template.rubric.names
        weights = self.template.rubric.weights()
        descs = {d.name: d.desc for d in self.template.rubric.dimensions}
        system = (
            f"You are an independent reviewer scoring a Unity Catalog {kind} comment draft "
            "against a rubric. Score EACH dimension in [0,1] (1 = excellent). Be strict on "
            "'grounding': every claim in the draft must trace to the provided evidence — if a "
            "claim is unsupported, lower 'grounding' and add an issue naming it. "
            f"Dimensions and what they mean: {descs}. "
            'Return ONLY JSON: {"subscores": {<dimension>: number}, "issues": [str]}. '
            f"Dimensions to score: {list(dims)}."
        )
        user = (
            "DRAFT:\n" + json.dumps({
                "proposed_comment": draft.proposed_comment,
                "rationale": draft.rationale,
                "evidence_refs": draft.evidence_refs,
            }, default=str)
            + "\n\nEVIDENCE:\n" + json.dumps(evidence, default=str)
        )
        messages = self._fit([ChatMessage("system", system), ChatMessage("user", user)], evidence, system, draft)

        try:
            obj, _resp = self.llm.complete_json(messages)
        except LLMError as exc:
            draft.status = DraftStatus.ERROR
            draft.rationale = _note(draft.rationale, f"judge failed: {exc.message}")
            draft.judge_scores = None
            return

        subscores = _clean_subscores(obj.get("subscores") or {}, dims)
        issues = [str(i) for i in (obj.get("issues") or [])]
        overall = round(sum(subscores[d] * weights[d] for d in dims), 4)
        draft.judge_scores = {"subscores": subscores, "overall": overall, "issues": issues}
        draft.confidence = overall  # authoritative — overrides the Reasoner's self_confidence (D8)

    # -- token budget (D32) — trim evidence if a draft's evidence is large ---
    def _fit(self, messages: list[ChatMessage], evidence: dict, system: str, draft) -> list[ChatMessage]:
        if not self.max_input_tokens:
            return messages
        if self.llm.count_tokens(messages) <= self.max_input_tokens:
            return messages
        # Single reducer: replace verbose context with a count; the draft + its direct
        # profile entry are load-bearing and kept.
        trimmed = dict(evidence)
        ctx = trimmed.get("context") or []
        if ctx:
            trimmed["context"] = f"<{len(ctx)} context snippets omitted to fit budget>"
        user = "DRAFT:\n" + json.dumps({
            "proposed_comment": draft.proposed_comment, "rationale": draft.rationale,
            "evidence_refs": draft.evidence_refs}, default=str) + "\n\nEVIDENCE:\n" + json.dumps(trimmed, default=str)
        return [ChatMessage("system", system), ChatMessage("user", user)]


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────
def _clean_subscores(raw: dict[str, Any], dims: tuple[str, ...]) -> dict[str, float]:
    """Coerce + clamp each rubric dimension to [0,1]; a missing/invalid dimension → 0.0
    (so an incomplete judge response is penalized, not silently treated as perfect)."""
    out: dict[str, float] = {}
    for d in dims:
        v = raw.get(d)
        try:
            f = float(v)
        except (TypeError, ValueError):
            f = 0.0
        out[d] = min(1.0, max(0.0, f))
    return out


def _note(existing: str | None, msg: str) -> str:
    return f"{existing}\n[{msg}]" if existing else f"[{msg}]"
