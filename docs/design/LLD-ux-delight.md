# geniefy-v3 — Low-Level Design: UX & Delight

**Status:** draft (U9) · **Last updated:** 2026-06-11
**Inputs:** [`HLD.md`](HLD.md) (U1), [`LLD-app-backend.md`](LLD-app-backend.md) (U5), [`LLD-review-apply.md`](LLD-review-apply.md) (U6) · **Decisions:** D11 (demo), D22 (delight is first-class), D23 (UX direction — added by this unit), D4/D5/D8 (evidence behind explainability), D18 (poll-based progress)
**Scope:** the **UX design language** and the **signature delightful moments** that make the demo land. *Out of scope:* component-level frontend implementation (code phase), the functional endpoints (U5), review/apply mechanics (U6 — this unit *elevates their presentation*), agent internals (U4).

> Direction chosen with the human: **data-viz-forward** vibe; three centerpieces — **"watch it think," undocumented→AI-ready glow-up, and "why it decided this" explainability.** Genie-payoff and column trading-card flips are supporting/deferred. Living doc.

---

## 1. North star

**"Watch a grey, illegible table become an AI-ready asset — and watch the agent *earn* every comment."** The emotional arc is **curiosity → trust → payoff**:
- *Curiosity* — the agent visibly works ("watch it think").
- *Trust* — every comment shows **why** it was decided, grounded in visible evidence.
- *Payoff* — the table transforms from "undocumented" to "Genie-ready," with a readiness score to prove it.

For a demo (D11/D22), the UX is a first-class deliverable, not chrome.

## 2. Design principles

1. **Data-viz IS the beauty.** The profile (distributions, top-K, cardinality, lineage/usage signals) is rendered, not hidden behind prose. Charts carry meaning *and* aesthetics.
2. **Explainability-first.** Every proposed comment is one click from its evidence and the agent's reasoning. Trust is the product.
3. **Confidence as visual language.** Color + shape encode certainty consistently everywhere (never color alone — paired with icon/label for accessibility).
4. **Motion with meaning.** Animation marks real state change (a phase completing, a comment landing), never decoration. (Fits the analytical vibe — restraint over confetti.)
5. **Honest theater.** The "live" narration reflects **real backend events**, and explainability shows the **actual evidence** the model used. Where evidence was thin, the UI says so. No fabricated certainty.

## 3. Visual design language (data-viz-forward)

- **Palette:** Databricks-aligned base, with a data-viz accent scale reserved for the **confidence/readiness encoding** (a perceptually-ordered scale, e.g. cool-grey → amber → green). Sequential/categorical scales for distributions chosen colorblind-safe.
- **The stat-viz vocabulary** (reusable primitives, used across run view, review, and explainability):
  - **distribution sparkline / histogram** (numeric & temporal), **top-K value chips** (categorical), **null-fraction bar**, **cardinality / key-likeness gauge**, **PII badge**, **pattern tag** (e.g. `ssn_like`).
  - **confidence chip** (color + value + icon), **confidence heatmap** (table-wide triage).
  - **readiness meter** (radial or bar: % columns documented & applied).
- These primitives consume the **sanitized profile** (U3 §4.2) — so the viz never renders raw/unmasked sensitive values (D4).

## 4. Centerpiece moment 1 — "Watch it think" (live agent narration)

Replace spinners with a **streaming play-by-play** of the agent's run, mapped to the real orchestrator phases (U4 §4) and surfaced via U5 status polling + persisted partials (D18):

| Phase (U4/U5 status) | What the user sees |
|---|---|
| `profiling` | Column placeholders **populate live** with their sparklines/chips as each batch returns (U3/U4 batching). "Sampling 100k rows…" |
| `gathering_context` | Lineage + query-history signals **light up**: "Read 1,204 recent queries · 89% join `orders` on `customer_id`." |
| `reasoning` | Drafts appear per column as batches complete; "Inferring grain: one row per order line." |
| `judging`/`gating` | Confidence chips resolve; low-confidence items begin to **pulse** ("wants to ask"). |

**Honesty (Principle 5):** every line is driven by a real phase event / partial from the backend — no scripted fakery. Mechanism is **poll-based** (D18) rendering incremental partials; live streaming (SSE/WebSocket) is a deferred upgrade. If a phase is slow, the narration idles truthfully rather than inventing progress.

## 5. Centerpiece moment 2 — Undocumented → AI-ready glow-up

A **visual state machine** per table/column, driven by draft status (U2 §6.2) and apply outcome (U6):

```
undocumented (desaturated/grey)
  → drafted        (proposed; tinted by confidence)
  → reviewed/edited
  → approved       (committed-to look)
  → applied        (vibrant; "Genie-ready ✨")
```

- An **AI-readiness meter** (% of columns documented **and** applied) climbs in real time as items move through the states — the single glanceable "are we there yet."
- **On apply** (per item, D21/D20): a restrained, data-viz-appropriate transition — the column row de-greys and the readiness meter ticks up; a per-table "now AI-ready" state when all approved items are applied. (No confetti — the vibe is analytical; the *score climbing* is the reward.)
- Conflicts (D21) render distinctly (not a failure-red, but an "attention" state) so the glow-up never masks a real issue.

## 6. Centerpiece moment 3 — "Why it decided this" (explainability)

Every proposed comment is one interaction from its **evidence trail** — the heart of trust and a perfect fit for data-viz-forward. For a given column/table comment, an expandable **"why" panel** shows:

- **The evidence the agent used** (the model's `evidence_refs`, U4 §3.4), rendered as viz: the relevant **profile stat** (e.g. the distribution + "97% cardinality → looks like a key"), the **usage/lineage signal** ("seen in 1,204 queries joining `orders.customer_id`" — D5), and any **glossary/context** snippet.
- **The agent's rationale** (U4 Reasoner `rationale`).
- **The Judge's verdict** (U4 §3.5): rubric subscores (completeness · specificity · grounding/no-hallucination · template-conformance) as a small **radar/bar**, plus any flagged **issues** ("asserted FK→orders with weak lineage support").
- **Where evidence was thin** → shown explicitly, which is *why* the item is low-confidence and (in interactive mode) became a question. Honesty over polish.

This turns "trust me" into "here's exactly why" — the demo's credibility moment.

## 7. Confidence as visual language

One consistent encoding everywhere: a perceptual color scale + value + icon (calm green = high/auto-kept; amber, **pulsing** = low/"wants to ask"). The **table-wide confidence heatmap** lets a reviewer triage at a glance and jump to the items that need them — which links directly into U6's review/answer flow. Color is never the sole signal (icon + numeric label for accessibility).

## 8. Key screens / journey (maps to U5 + U6)

1. **Start** — pick a table + template; set sampling/providers (U5 config). Calm, focused.
2. **Run view ("watch it think")** — §4; the live narrated profiling/reasoning canvas.
3. **Review canvas** — column rows with stat-viz + diff (current vs proposed, U6) + confidence + the "why" panel (§6); confidence heatmap for triage; conversational Q&A for low-confidence items (U6/U5).
4. **Apply + glow-up** — §5; per-item apply with conflict handling (D21), readiness meter, AI-ready state.
5. **History & library** — past sessions and outcomes (U2 §7); the comment library as a growing, reusable collection.

## 9. Interaction & motion

- **Keyboard-first triage:** `j/k` move, `e` edit, `a` approve, `?` help — fast for the demo-driver.
- **Motion** marks real transitions only (phase complete, comment applied, score tick). Respect reduced-motion preferences.
- **Responsiveness:** the run view streams partials so the screen is never empty/dead.

## 10. Micro-copy & personality

Data-savvy, precise, lightly warm. Honest about uncertainty ("I wasn't sure what `tier` means, so I asked"). Never overclaims certainty the Judge didn't support. Empty/done states have a light touch without being cutesy.

## 11. Honesty guardrails (Principle 5, expanded)

- Narration (§4) and progress reflect **real** backend phase events — no scripted timelines.
- Explainability (§6) shows the **actual** sanitized evidence used (D4); no invented citations.
- Confidence visuals derive from the **Judge** (D8), not cosmetic randomness.
- These aren't just ethics — for a *technical* audience, fabricated polish is the fastest way to lose the room.

## 12. Supporting / deferred (not v1 centerpieces)

- **Genie payoff panel** — a "Try it in Genie" before/after where a sample NL question that failed now resolves. Strong, but not a chosen must-have; sketch + revisit.
- **Column trading-card flip** — the stat-viz primitives (§3) cover most of this; the flip animation is optional polish.
- **Gamification / celebratory flourishes** — kept restrained per the analytical vibe.
- **Live streaming (SSE/WebSocket)** vs. polling for the run view (D18 deferred).
- **Full design system / component spec** — belongs to the frontend code phase.

## 13. Interfaces to other units

- **U4:** phases (narration), drafts + `rationale` + `evidence_refs` + Judge scores + confidence (explainability & confidence viz).
- **U5:** status polling + persisted partials (live run view); endpoints behind every screen.
- **U6:** review/diff/apply mechanics — this unit defines their *presentation* (stat-viz, "why" panel, glow-up); it does not change their logic.
- **U2:** draft statuses → readiness meter; history/library.
- **U8:** frontend assets packaged in the DAB; readiness derived data is read-only.
