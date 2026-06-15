# Git-Readiness Analysis (U130)

**Date:** 2026-06-14 · **Kind:** read-only assessment (no repo/code/git changes made) ·
**Trigger:** human directive — "analyze what it takes to make it git-ready, do not make changes, just analysis."

## Verdict

**geniefy-v3 is close to git-ready and safe to publish.** No secrets exist in the committable
surface, the project is Apache-2.0 licensed, and it is self-contained (no coupling to the parent
`fe-vibe/` workspace). The remaining work is small and mostly hygiene + a few decisions:

- **Blocking-for-quality (do before first push):** add a dev/test dependency manifest (reproducibility),
  tighten `.gitignore` for explicitness, decide repo visibility/name/author-email.
- **Not blocking (already safe):** Terraform state and all build/staging artifacts are already excluded.
- **Public/customer-grade only:** genericize env-specific identifiers + scrub internal run-IDs.

No code changes are required to push. The effort is configuration + decisions, not engineering.

---

## 1. Security / secret surface — **PASS (no secrets)**

- **Literal secret scan** across `*.py / *.ts / *.tsx / *.yml / *.yaml / *.sh / *.json / *.md / *.sql`
  (patterns: `dapi…`, `gho_/ghp_`, `AKIA…`, `BEGIN … PRIVATE KEY`, `PGPASSWORD=<literal>`,
  `client_secret=…`, `access_token=…`) → **zero matches** in the committable surface.
- `app/app.yaml` carries **secret-scope references only** (e.g. `{"secret_scope":"geniefy","key":"glean_token"}`),
  never raw tokens — by design (D5). Lakebase/FMAPI auth is minted at runtime (OAuth/per-call), never stored.
- **Terraform state is already protected.** Both `.databricks/` (131 MB) and `jobs-bundle/.databricks/`
  (131 MB) each contain a nested `.gitignore` whose sole line is `*` — git self-ignores their entire
  contents (including `terraform.tfstate`). So tfstate **cannot** be accidentally committed even though
  the top-level `.gitignore` does not name `.databricks/`. Recommend adding an explicit top-level entry
  anyway (clarity + defense-in-depth).

**Conclusion:** nothing sensitive would leak on a `git add -A` today (build artifacts aside).

## 2. Environment-coupled identifiers (NOT secrets, but environment-specific)

Present in `databricks.yml`, `app/app.yaml`, `jobs-bundle/databricks.yml`, `DEPLOY_VERIFY.md`,
`migrations/APPLY.md` + `DEV_VERIFY.md`, and `README.md`:

| Value | Where | Note |
|---|---|---|
| App SP UUID `bcc7089c-deac-4f42-…` | `jobs-bundle/databricks.yml` (run_as + permissions) | identifier, not a credential |
| Workspace host `fevm-rd-classic.cloud.databricks.com` | databricks.yml, docs | dev workspace |
| App URL host + workspace id `geniefy-dev-7474653107059373…databricksapps.com` | DEPLOY_VERIFY.md | live app |
| Lakebase endpoints `ep-curly-sunset-…` (dev) / `ep-blue-smoke-…` (prod) | databricks.yml, app.yaml, jobs-bundle | branch endpoint hosts |
| Profile `fe-vm-classic` | databricks.yml, deploy.sh, docs | local CLI profile name |

None are credentials — you still need authenticated access to use them. **For a private/internal repo
they are fine to commit as-is.** For a public or customer-facing repo, parameterize them (DAB target
vars / app.yaml templating) and scrub the live run-IDs — see §4.

## 3. `.gitignore` coverage

**Already ignored (good):** `__pycache__/`, `*.py[cod]`, `.venv/`, `*.egg-info/`, `.pytest_cache/`,
`app/frontend/node_modules/`, `app/static/`, `app/geniefy_core/` + `app/geniefy_app/` (deploy staging),
`jobs-bundle/geniefy_core/` + `geniefy_app/` + `schema_run_entry.py` (job staging), `.env`.

**Recommended additions (for explicitness / hygiene):**
- `.databricks/` and `jobs-bundle/.databricks/` — explicit top-level entries (already self-protected, but clearer).
- `.claude/settings.local.json` — **personal/local** (contains a user-specific allow-list + absolute paths). Do not commit.
- `.DS_Store` — none present now, but macOS will create them.
- *(optional)* `*.log`, `*.local`.

**Keep committed:**
- `.claude/settings.json` — the project-shared GOTM immutability hook wiring (collaborators need it).
- `app/frontend/package-lock.json` — needed for reproducible frontend installs (verify it is not ignored).

## 4. Reproducibility — **the main functional gap**

- **No root dependency manifest for the dev/test environment.** The repo runs 375 tests via a `.venv`
  (pytest, fastapi, uvicorn, databricks-sdk, openai, psycopg2-binary, pyyaml, httpx) but **nothing pins
  those**. A fresh clone cannot recreate the test env or run the suite. → Add `requirements-dev.txt`
  (or a `pyproject.toml` with a dev extra). `app/requirements.txt` covers only the **App runtime**.
- **Frontend is reproducible** (`app/frontend/package.json` + lockfile + documented `npm run build`).
- **Build/deploy is documented** (`README.md`, `deploy.sh`, `deploy_jobs.sh`, `DEPLOY_VERIFY.md`); add
  a one-line "create the test venv + run pytest" snippet to the README.

## 5. Portability / genericization (only for public or customer repos)

- `databricks.yml` encodes `fe-vm-classic` + dev/prod var defaults (Lakebase hosts, workspace host) as
  DAB target defaults — acceptable, but a customer-facing repo should document/parameterize them.
- The app SP UUID in `jobs-bundle/databricks.yml` (`run_as` + permissions) should become a bundle var.
- `DEPLOY_VERIFY.md` + `migrations/*.md` contain live run-IDs, hosts, and the app URL — internal
  verification records; fine internally, **scrub for a public release**.

## 6. Repo scope, structure & naming (decisions)

- **Scope:** no imports from the parent `fe-vibe/services/` → **push `geniefy-v3` as its own repo** (recommended).
- **Naming drift:** the UI/product was rebranded to **geniefy-lite** (R4), but the repo dir, `README.md`
  title (`# geniefy-v3`), `CLAUDE.md`, and internal module names still say **geniefy-v3**. Decide the repo
  name and (optionally) align the README title. Renaming internal package paths is **not** recommended
  (churn, no functional gain).
- **GOTM machinery** (`.gotm/` ≈ 1.1 MB: PROTOCOL, LEDGER, DECISIONS, QUESTIONS, audits): rich build
  provenance — **commit for an internal repo**; consider excluding for a clean OSS release.
- **`docs/assets/` screenshots** (≈ 3.6 MB of PNG verification evidence): commit (documentation) or
  gitignore (dev artifacts) — your call; 3.6 MB is modest.

## 7. License & authorship hygiene

- **License:** Apache-2.0 (`LICENSE` present) — permissive, fine for public or internal.
- **Commit identity:** global git config is `Rohit Dashora <rohitdashora@gmail.com>` (**personal gmail**).
  For a work/Databricks repo, set a per-repo `user.email = rohit.dashora@databricks.com`.
- **Trailers:** `CLAUDE.md` mandates a `Co-authored-by: Isaac` trailer on commits and a "written by Isaac"
  line on PR bodies.

## 8. Decisions needed before any git work

1. **Host + visibility:** GitHub `RohitDashora` (CLI already authenticated, scopes `repo, workflow`) —
   **private** or **public**? (Drives how much §4/§5 genericization is required.)
2. **Repo name:** `geniefy-lite` or `geniefy-v3`?
3. **Commit author email:** work (`rohit.dashora@databricks.com`) or personal gmail?
4. **Include `.gotm/` machinery + `docs/assets/` screenshots, or exclude?**
5. **Confirm scope:** push `geniefy-v3` only (recommended).

## 9. Effort estimate

| Path | Work | Effort |
|---|---|---|
| **Private / internal** | add 3–4 `.gitignore` lines + `requirements-dev.txt`; `git init` → first commit → create private repo → push. **No code changes.** | ~15 min |
| **Public / customer-grade** | the above **plus** genericize env identifiers (SP UUID + hosts → vars), scrub internal run-IDs from docs, align naming, polish README | +1–2 hrs |

## 10. Recommended "make it git-ready" unit list (for when you say go)

- **U131** — `.gitignore` hardening (`.databricks/`, `jobs-bundle/.databricks/`, `.claude/settings.local.json`, `.DS_Store`).
- **U132** — `requirements-dev.txt` (pin the test/dev deps) + a README "run the tests" snippet.
- **U133** *(public only)* — genericize env identifiers + scrub internal run-IDs.
- **U134** — `git init`, per-repo author email, first commit (GOTM trailer), create remote, push.

*(These are proposals, not yet registered — no changes made. Registration awaits the §8 decisions.)*
