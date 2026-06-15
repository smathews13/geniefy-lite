# geniefy-v3 — Low-Level Design: Frontend Stack & UX Implementation

**Status:** draft (U23) · **Last updated:** 2026-06-12
**Inputs:** [`HLD.md`](HLD.md) (U1) · [`LLD-app-backend.md`](LLD-app-backend.md) (U5) · [`LLD-review-apply.md`](LLD-review-apply.md) (U6) · [`LLD-ux-delight.md`](LLD-ux-delight.md) (U9)
**Decisions:** D11 (shippable demo), D18 (poll-based), D22 (delight is first-class), D23 (data-viz-forward direction), **D31** (frontend stack — ratified by this unit)
**Scope:** the concrete **frontend stack** and how U9's UX design language is *implemented* — component mapping, how the SPA is built and served, and the poll-based data layer. *Out of scope:* the UX language/centerpieces themselves (U9 owns those), backend endpoints (U5), review/apply semantics (U6).

> Living doc. The stack and the build/serve/data patterns are concrete; the per-component spec lands with the frontend build units.

---

## 1. Stack (D31)

| Layer | Choice | Why (for *this* demo) |
|---|---|---|
| Build / language | **Vite + React + TypeScript** | Fast dev loop; emits a static bundle the FastAPI app serves (U5); types keep the profile/draft shapes honest end-to-end. |
| Styling / components | **Tailwind CSS + shadcn/ui** | Polished, accessible primitives (dialog, table, tabs, hover-card, badge, toast) without building a design system; Tailwind tokens carry the D23 palette. |
| Server state / polling | **TanStack Query** | `refetchInterval` maps 1:1 to the D18 poll loop; cache + mutations give optimistic edits cleanly. |
| Data-viz | **visx** (signature primitives) **+ Recharts** (routine charts) | visx (D3-under-React) gives the control U9's bespoke vocabulary needs (gauge, radial, heatmap, custom sparkline) without hand-rolling all of D3; Recharts covers standard bar/line/radar fast. |

No global state library (Redux/MobX): server state lives in TanStack Query; local UI state is component-level. Routing: a light client router (run view · review · history).

## 2. "Delightful but restrained" (refines D22/D23 — explicit guardrails)

Delight comes from **clarity and motion-with-meaning**, not spectacle. Guardrails so it never goes overboard:

- **Motion marks real state change only** (a phase completing, a comment landing, a confidence resolving) — no idle/decorative animation, no confetti (D23 already says "restraint over confetti").
- **Performance budget:** interactions stay ~60fps; animations ≤ ~300ms; the run view renders incremental partials without layout thrash. Lazy-load the heavier viz.
- **Accessibility:** confidence/readiness encode with **color + icon + label** (never color alone — D23 Principle 3); honor `prefers-reduced-motion`.
- **One hero moment per screen**, not five. The "watch it think" view is the centerpiece; review/explainability are calm and information-dense.

## 3. U9 stat-viz vocabulary → concrete components

| U9 primitive (§3) | Implementation |
|---|---|
| distribution sparkline / histogram | **visx** (custom, shared scale) |
| top-K value chips · pattern tag · PII badge | **shadcn/ui** Badge variants |
| null-fraction bar | **visx** (or a Tailwind bar) |
| cardinality / key-likeness gauge | **visx** radial gauge |
| confidence chip | shadcn Badge (color + icon + value) |
| confidence heatmap (table-wide triage) | **visx** grid heatmap |
| readiness meter (radial/bar) | **visx** radial progress |
| Judge verdict (rubric subscores) | **Recharts** RadarChart (or small bar) |
| Apply confirmation · question panel · diff | shadcn Dialog · Card · a side-by-side diff component |

All viz consume the **sanitized profile** (U3 §4.2) — never raw/unmasked values (D4).

## 4. App structure & serving

```
app/
  app.yaml                  # command + env (U5/U8); serves the built SPA + API
  main.py                   # FastAPI: API routes + StaticFiles mount for the built bundle
  frontend/                 # Vite + React + TS project (source)
    src/{routes,components,viz,api,lib}/
    package.json  vite.config.ts  tailwind.config.ts
  static/                   # `vite build` output — what FastAPI serves (built in CI/deploy)
```

- **Serve:** FastAPI mounts `app/static` (the Vite build) at `/`; API under `/api/*` (U5). SPA fallback route serves `index.html` for client routing.
- **Build:** `vite build` runs in the deploy orchestrator **before** `bundle sync` (see U24 §4) so the bundle ships built assets — the Databricks App runtime does not run `npm build`.
- **Dev:** Vite dev server proxies `/api` to the local FastAPI (fast inner loop).

## 5. Data layer — the poll loop (D18)

- `useQuery(['session', id], fetchSession, { refetchInterval })` where `refetchInterval` is a function: poll (~1–2s) while `status ∈ {created, profiling, gathering_context, reasoning, applying}`; **stop** on terminal `awaiting_input | ready_for_review | applied | failed | cancelled` (U5 statuses).
- The run view renders **partials** as they arrive (columns populate live — U9 §4) from the polled `GET /sessions/{id}`.
- Mutations (`answer`, `approve`, `apply`) via `useMutation` with optimistic updates + invalidation; apply surfaces per-item `conflict/failed` (U6 §8).
- Live streaming (SSE/WebSocket) remains the deferred upgrade (D18) — the query layer can swap to a subscription without changing screens.

## 6. Key screens (elevating U6/U9)

1. **Run view** — "watch it think" (U9 §4): phase narration + live-populating column cards.
2. **Review** — the U6 table: side-by-side diff, edit-in-place, confidence chips (hover → Judge issues), question panel for `awaiting_input`.
3. **Explainability drawer** (U9 §6) — evidence viz + rationale + Judge radar for a chosen draft.
4. **Apply** — confirmation dialog listing writes + conflicts (U6 §7); the readiness meter updates on success (glow-up, U9 §5).
5. **History** — past sessions + applied outcomes (U2 §7).

## 7. Tooling / CI

- `npm run typecheck` + `eslint` + `vite build` gate the frontend; a `vite build` step precedes bundle deploy (U24 §4).
- Component tests are a deferred asset (with the broader test/eval harness, D2/D8).

## 8. Interfaces to other units

- **U5:** consumes the REST API + status polling; the served static mount lives in the same FastAPI app.
- **U6:** implements its review/apply UX and endpoints' client side.
- **U9:** the design language this unit renders; U9 stays the source of truth for *what* delights, this unit is *how*.
- **U8 / U24:** the built bundle ships in `app/`; `vite build` is sequenced by the deploy orchestrator (U24 §4); config (e.g. feature flags) arrives via app.yaml env (U24 §1).

## 9. Deferred / sketched

- SSE/WebSocket streaming for the run view (D18).
- Full design tokens / Storybook / component visual tests.
- i18n; theming beyond the D23 palette.
