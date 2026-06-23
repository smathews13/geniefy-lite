"""geniefy-v3 agent core — Reasoner (U35).

Fuses the sanitized profile (U3 §4.2), schema_meta (U4 §3.2), gathered context
(U4 §3.3), and the Template (U25) into reviewable drafts via the model (U4 §3.4).

Principles honored:
  - **Grounding, no speculation** (U4 §1): the system prompt instructs the model to fill
    each applicable template field from the provided evidence or mark it ``unknown``,
    cite the signal in ``evidence_refs``, and never invent. The Judge (U4 §3.5) then
    independently checks grounding — so the Reasoner's ``self_confidence`` is provisional,
    not authoritative (D8).
  - **Structured output** (U4 §3.4): one JSON object per call (table draft / column batch)
    so every field maps to the template.
  - **Column chunking** (U4 §3.4): columns processed in batches of
    ``config.reason_batch_size``; the table comment is produced once.
  - **Cost predictability** (D32 / U24 §2): each call is fit to a per-call input-token
    budget via the ``Budgeter`` with concrete reducers (trim context → summarize context
    → reduce profile verbosity); what was compacted is recorded on the drafts (honest UI).
  - **Per-batch error isolation** (U4 §8): a failing batch marks only its drafts ``error``;
    other batches proceed.

Hermetic (D1): all model access is through the injected ``LLMClient`` (U32).
"""
from __future__ import annotations

import json
from typing import Any

from geniefy_core.llm import Budgeter, ChatMessage, LLMClient, LLMError
from geniefy_core.state import (
    ColumnDraft,
    DraftStatus,
    Phase,
    SessionState,
    TableDraft,
)
from geniefy_core.template import Template

# Profile column fields dropped first when reducing verbosity (keep the load-bearing stats).
_VERBOSE_COLUMN_FIELDS = ("top_k", "sample_values", "pattern_summary", "percentiles")

_GROUNDING_RULES = (
    "Rules: fill each applicable field ONLY from the provided evidence; if the evidence "
    "does not support a field, omit it or write \"unknown\" — never speculate. Cite the "
    "signal(s) you used in evidence_refs (e.g. 'profile.null_fraction', 'context:uc_lineage'). "
    "Honor the style limits. Return ONLY the JSON object, with no extra prose outside it "
    "(the comment fields themselves are prose — that is expected)."
)

# Library reuse-on-generation (D52 §A4 / U104): prefer approved canonical wording when it fits.
_REUSE_RULE = (
    "If the CONTEXT contains a previously-approved canonical comment (source comment_library) for "
    "this table or column, PREFER reusing its wording when it fits the evidence — adapt it to this "
    "table; do not copy it blindly if the data differs."
)


# Structured steward facts for the hero chips (D53 §B4 / U114) — generated in the SAME table call as
# the prose comment (so they're consistent); UI-only, the prose comment stays the UC artifact.
_FACTS_RULE = (
    "Also return a \"facts\" object with SHORT, scannable values (a few words each) for a data owner: "
    "owner (technical/data owner), freshness (update cadence/SLA), grain (what one row represents), "
    "keys (primary/join keys), sensitivity (PII / access level). Ground each in the evidence and OMIT "
    "any key you cannot support — do not guess."
)


def _tag_rule(kind: str) -> str:
    """Free-form tag guidance (D53/Q2): seeded with examples, NOT a locked taxonomy."""
    return (
        f"Also propose 2-4 short lowercase {kind} tags (in a \"tags\" array) that help a human or "
        "agent scan and filter — free-form, coin apt ones; common examples: identifier, key, metric, "
        "measure, dimension, pii, sensitive, temporal, enum, status, fact, deprecated. Ground each "
        "tag in the evidence (tag 'pii' only if the profile/context indicates personal data; 'enum' "
        "only for low-distinct categoricals)."
    )


class Reasoner:
    """Profile + context + template → drafts (U4 §3.4)."""

    def __init__(
        self,
        llm: LLMClient,
        template: Template,
        *,
        reason_batch_size: int = 25,
        max_input_tokens: int | None = None,
        table_max_tokens: int | None = None,
        column_max_tokens: int | None = None,
    ):
        if reason_batch_size < 1:
            raise ValueError("reason_batch_size must be >= 1")
        self.llm = llm
        self.template = template
        self.reason_batch_size = reason_batch_size
        self.max_input_tokens = max_input_tokens
        # Per-phase output budgets (D43/E1): table comments are rich, column batches leaner.
        # None → fall back to the LLMClient's configured default_max_tokens.
        self.table_max_tokens = table_max_tokens
        self.column_max_tokens = column_max_tokens

    # -- public --------------------------------------------------------------
    def draft(self, state: SessionState) -> None:
        """Populate ``state.table_draft`` and ``state.column_drafts`` from the profile +
        context in ``state``. Sets ``phase = REASONING``. Mutates ``state`` in place
        (the caller persists it, D17)."""
        state.phase = Phase.REASONING
        profile = state.profile or {}
        table_block = profile.get("table") or {}
        columns = profile.get("columns") or []

        state.table_draft = self._draft_table(state, table_block)

        # Two-pass (E2/D44): feed the just-generated table comment into the column pass so each
        # column is grounded in the table's synthesized purpose/grain/keys. Skipped if the table
        # draft failed (degrade — columns still draft from their own profile).
        table_comment = None
        if state.table_draft is not None and state.table_draft.status != DraftStatus.ERROR:
            table_comment = (state.table_draft.proposed_comment or "").strip() or None

        drafts: list[ColumnDraft] = []
        for batch in _chunks(columns, self.reason_batch_size):
            drafts.extend(self._draft_columns(state, batch, table_comment))
        state.column_drafts = drafts

    # -- suggested answers for needs-input questions (D50/U100) --------------
    def suggest_answers(self, state: SessionState, questions: list) -> None:
        """Best-effort: propose a SHORT likely answer for each open clarifying question, grounded
        in that column's profile, so the reviewer accepts/edits instead of typing from scratch.
        Populates ``q.suggested_answer`` in place via ONE batched LLM call; any failure leaves the
        suggestions unset and NEVER blocks the run (D50). Duck-types the question objects."""
        pending = [q for q in questions if getattr(q, "suggested_answer", None) is None]
        if not pending:
            return
        cols = {c.get("name"): c for c in ((state.profile or {}).get("columns") or [])}
        keep = ("data_type", "top_k", "sample_values", "min", "max", "is_enum_candidate",
                "cardinality_ratio", "null_fraction")
        items = [
            {"id": q.id, "target": q.target_name or "__table__", "question": q.text,
             "profile": {k: (cols.get(q.target_name) or {}).get(k)
                         for k in keep if (cols.get(q.target_name) or {}).get(k) is not None}}
            for q in pending
        ]
        system = (
            "You pre-fill a human reviewer's answer box for clarifying questions about Unity Catalog "
            "columns. For each question propose a SHORT (<30 words) likely answer grounded ONLY in the "
            "provided profile evidence (enum top_k values, sample_values, min/max, etc.); if evidence is "
            "thin, give a brief plausible hint the reviewer can correct. Never invent specifics not in "
            'evidence. Return ONLY JSON: {"answers": [{"id": str, "answer": str}]}.'
        )
        try:
            obj, _resp = self.llm.complete_json(
                [ChatMessage("system", system),
                 ChatMessage("user", json.dumps({"questions": items}, default=str))],
                max_tokens=self.column_max_tokens,
            )
        except LLMError:
            return  # best-effort — a suggestion failure must never block review (D50)
        if not isinstance(obj, dict):
            return
        by_id = {a.get("id"): a.get("answer") for a in (obj.get("answers") or []) if isinstance(a, dict)}
        for q in pending:
            ans = by_id.get(q.id)
            if isinstance(ans, str) and ans.strip():
                q.suggested_answer = ans.strip()

    # -- table ---------------------------------------------------------------
    def _draft_table(self, state: SessionState, table_block: dict[str, Any]) -> TableDraft:
        draft = TableDraft(current_comment=table_block.get("existing_comment"))
        system = (
            "You write a rich Unity Catalog TABLE comment that a data steward / business owner and "
            "an AI agent (Genie) can use to understand and query the table. "
            "CRITICAL: `proposed_comment` is a SINGLE human-readable PROSE comment (one or a few short "
            "paragraphs of flowing sentences) — it is NOT a JSON object, NOT key:value pairs, NOT a "
            "bulleted field dump. Within that prose, naturally cover (only where the evidence supports "
            f"each) these REQUIRED aspects: {list(self.template.table_required)}; and these RECOMMENDED "
            f"aspects when known: {list(self.template.table_recommended)}. "
            f"Style: max_words={self.template.table_style.max_words}, "
            f"voice={self.template.table_style.voice!r}, avoid={list(self.template.table_style.forbid)}. "
            f"{_GROUNDING_RULES} {_REUSE_RULE} {_tag_rule('table')} {_FACTS_RULE} "
            'JSON shape: {"proposed_comment": str (the PROSE comment), "rationale": str, "tags": [str], '
            '"facts": {"owner": str, "freshness": str, "grain": str, "keys": str, "sensitivity": str}, '
            '"evidence_refs": [str], "self_confidence": number, "open_question": str|null}.'
        )
        profile_part = {"table": table_block}
        try:
            obj, actions = self._complete(state, system, profile_part, "Draft the table comment.",
                                          max_tokens=self.table_max_tokens)
        except LLMError as exc:
            draft.status = DraftStatus.ERROR
            draft.rationale = f"reasoning failed: {exc.message}"
            return draft
        _apply_common(draft, obj, actions)
        # Structured steward facts → hero chips (U114); keep only non-empty string values, drop noise.
        raw_facts = obj.get("facts")
        if isinstance(raw_facts, dict):
            facts = {k: v.strip() for k, v in raw_facts.items()
                     if isinstance(v, str) and v.strip()
                     and v.strip().lower() not in ("unknown", "n/a", "none")}
            draft.facts = facts or None
        return draft

    # -- columns -------------------------------------------------------------
    def _draft_columns(self, state: SessionState, batch: list[dict[str, Any]],
                       table_comment: str | None = None) -> list[ColumnDraft]:
        by_name = {c.get("name"): c for c in batch}
        conds = {fs.name: fs.rule for fs in self.template.column_conditional}
        system = (
            "You write Unity Catalog COLUMN comments for a batch of columns. Required: "
            f"{list(self.template.column_required)} (business meaning, NOT a restatement of the name). "
            f"Conditional fields (include only when applicable): {conds}. "
            f"Style: max_words={self.template.column_style.max_words}, "
            f"avoid={list(self.template.column_style.forbid)}. {_GROUNDING_RULES} {_REUSE_RULE} "
            f"{_tag_rule('column')} "
            'JSON shape: {"columns": [{"name": str, "proposed_comment": str, "rationale": str, '
            '"tags": [str], "conditional_fields": object, "evidence_refs": [str], '
            '"self_confidence": number, "open_question": str|null}]}.'
        )
        profile_part = {"columns": batch, "schema_meta": state.schema_meta or {}}
        names = [c.get("name") for c in batch]
        instruction = f"Draft comments for columns: {names}."
        if table_comment:  # two-pass grounding (E2/D44) — no apostrophes (keeps prompt clean)
            instruction = ("TABLE COMMENT (just generated — ground each column in the table "
                           f"purpose, grain, and keys):\n{table_comment}\n\n" + instruction)
        try:
            obj, actions = self._complete(state, system, profile_part, instruction,
                                          max_tokens=self.column_max_tokens)
        except LLMError as exc:
            return [
                ColumnDraft(column_name=c.get("name", ""), ordinal=c.get("ordinal"),
                            data_type=c.get("data_type"), current_comment=c.get("existing_comment"),
                            status=DraftStatus.ERROR, rationale=f"reasoning failed: {exc.message}")
                for c in batch
            ]
        out: list[ColumnDraft] = []
        returned = {r.get("name"): r for r in (obj.get("columns") or [])}
        for name, col in by_name.items():
            d = ColumnDraft(column_name=name or "", ordinal=col.get("ordinal"),
                            data_type=col.get("data_type"), current_comment=col.get("existing_comment"))
            r = returned.get(name)
            if r is None:
                # model dropped a requested column → flag it, don't fabricate (U4 §1)
                d.status = DraftStatus.ERROR
                d.rationale = "model did not return a draft for this column"
            else:
                _apply_common(d, r, actions)
                d.conditional_fields = r.get("conditional_fields") or None
            out.append(d)
        return out

    # -- one budgeted, structured call --------------------------------------
    def _complete(
        self, state: SessionState, system: str, profile_part: dict[str, Any], instruction: str,
        *, max_tokens: int | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        """Build messages, fit to the per-call token budget (D32), and call the model
        for a JSON object. ``max_tokens`` caps the *output* per phase (D43/E1); ``None``
        falls back to the LLMClient default. Returns ``(parsed_obj, compaction_actions)``."""
        ctx = list(state.context or [])

        def render(context: list[dict], profile: dict[str, Any]) -> list[ChatMessage]:
            msgs = [ChatMessage("system", system)]
            if context:
                msgs.append(ChatMessage("user", "CONTEXT:\n" + _fmt_context(context)))
            msgs.append(ChatMessage("user", "PROFILE:\n" + json.dumps(profile, default=str)))
            msgs.append(ChatMessage("user", instruction))
            return msgs

        actions: list[str] = []
        if self.max_input_tokens:
            holder = {"ctx": ctx, "profile": profile_part}

            def trim_context(_msgs):
                c = holder["ctx"]
                holder["ctx"] = c[: max(1, len(c) // 2)] if c else c
                return render(holder["ctx"], holder["profile"])

            def summarize_context(_msgs):
                if holder["ctx"]:
                    holder["ctx"] = [{"source": "summary", "text": self._summarize(holder["ctx"])}]
                return render(holder["ctx"], holder["profile"])

            def reduce_profile(_msgs):
                holder["profile"] = _strip_verbosity(holder["profile"])
                return render(holder["ctx"], holder["profile"])

            result = Budgeter(self.llm.counter, self.max_input_tokens).fit(
                render(ctx, profile_part),
                [("trim_context", trim_context),
                 ("summarize_context", summarize_context),
                 ("reduce_profile", reduce_profile)],
            )
            actions = result.actions
            messages = result.messages
        else:
            messages = render(ctx, profile_part)

        obj, _resp = self.llm.complete_json(messages, max_tokens=max_tokens)
        if not isinstance(obj, dict):
            raise LLMError("bad_json", "expected a JSON object from the model")
        return obj, actions

    def _summarize(self, context: list[dict[str, Any]]) -> str:
        """Cheap context compression (D32 step 2). Uses the same client; best-effort —
        on failure, fall back to a truncated join rather than blowing up the run."""
        try:
            obj_text = self.llm.complete(
                [ChatMessage("system", "Summarize the following context for table documentation "
                                       "in <=120 words, preserving join keys, grain, and business terms."),
                 ChatMessage("user", _fmt_context(context))],
                max_tokens=200,
            ).text
            return obj_text
        except LLMError:
            return _fmt_context(context)[:500]


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────
def _apply_common(draft: Any, obj: dict[str, Any], actions: list[str]) -> None:
    draft.proposed_comment = obj.get("proposed_comment")
    draft.rationale = obj.get("rationale")
    draft.confidence = obj.get("self_confidence")  # provisional; Judge overrides (D8)
    draft.evidence_refs = list(obj.get("evidence_refs") or [])
    # Free-form tags (D53/U104): normalize to short, deduped, lowercase strings (cap 6 defensively).
    tags, seen = [], set()
    for t in (obj.get("tags") or []):
        tag = str(t).strip().lower()
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    draft.tags = tags[:6]
    draft.status = DraftStatus.DRAFT
    if actions:  # honest record of any prompt compaction (U24 §2 / D23 P5)
        note = "context compacted to fit token budget: " + ", ".join(actions)
        draft.rationale = f"{draft.rationale}\n[{note}]" if draft.rationale else f"[{note}]"


def _fmt_context(context: list[dict[str, Any]]) -> str:
    lines = []
    for c in context:
        src = c.get("source", "context")
        lines.append(f"- ({src}) {c.get('text', '')}")
    return "\n".join(lines)


def _strip_verbosity(profile: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(profile, default=str))  # deep copy
    for col in out.get("columns", []) or []:
        for f in _VERBOSE_COLUMN_FIELDS:
            col.pop(f, None)
    return out


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]
