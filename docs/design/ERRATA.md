# Design-doc errata (U153)

Cosmetic citation/label corrections to the **frozen** design docs, surfaced by the audit-of-audits
sweep (2026-06-23). The source docs are point-in-time records and stay frozen per GOTM — the
substance of each is correct; only the cited labels are off. Corrections are recorded here rather
than rewritten in place.

| Doc | As written | Correction | Source audit |
|---|---|---|---|
| `LLD-nfr-plan.md` (U24) | "D23 Principle 5" | The numbered UX principles live in **U9 / `LLD-ux-delight.md`**, not D23. D23 ratifies the UX direction; the enumerated principles are U9's. | `audits/U24.md` |
| `LLD-nfr-plan.md` (U24) | `GENIEFY_SAMPLE_MODE` cites "U3 §4.1" | The anchor is the **`RunConfig`** definition (U3 §4.1 resolves via RunConfig); the cited section exists, the reference is just imprecise. | `audits/U24.md` |
| `LLD-amend-005.md` (U101) | cites "Q7–Q10" as numbered questions | `QUESTIONS.md` only has **Q1–Q5**; the R3 hands-off design answers live inside **D51**'s text, not as numbered Q-entries. | `audits/U101.md` |
| `LLD-amend-005.md` (U108 prose) | design prose says `RunMode.HANDS_OFF` | The implementation uses **`SessionMode.HANDS_OFF`** (extends `SessionMode`, not a new `RunMode` — U108 audit M1). The code is correct; the design prose label is stale. | `audits/U108.md` |
| `GIT-READINESS-ANALYSIS.md` (U130) | §1 "secret-scope references only" | Slightly misleading on a skim: `app.yaml` also carries a **literal (non-secret) Lakebase PG host** (correctly captured in §2/§5). No secret is exposed; the §1 phrasing just reads narrower than the full doc. | `audits/U130.md` |
| `ARCHITECTURE.md` (U133) | §3 component map omits `.py` on the profiler/judge nodes | Sibling nodes show filenames; profiler/judge should read `profiler.py`/`judge.py` for consistency. Cosmetic label only. | `audits/U133.md` |
| `ARCHITECTURE.md` (U133) | §9 lists top-level `jobs/` alongside `jobs-bundle/` | Both are real (`jobs/schema_run_entry.py` is the entrypoint; `jobs-bundle/` is the standalone DAB bundle that stages it) — the listing can read as duplicative but is accurate. | `audits/U133.md` |

All entries are LOW/cosmetic; none affect correctness, security, or any load-bearing invariant. They
were the residual tail of the audit-of-audits sweep after the actionable findings were fixed
(U148–U152).
