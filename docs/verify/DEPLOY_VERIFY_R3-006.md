# R3 / LLD-amend-006 — live deploy + verification (U107)

**Date:** 2026-06-13 · **Target:** `geniefy-dev` (`fevm-rd-classic`, profile `fe-vm-classic`) ·
**App:** https://geniefy-dev-7474653107059373.aws.databricksapps.com · **SP:** `bcc7089c-…`

Verifies the comment-enrichment + library-lifecycle feature (U103–U106, +U115) end-to-end on the
live app + real tables, via the grant-safe `--code-only` path (D48/U78 — never `bundle deploy`).

## Deploy
- `./deploy.sh -t dev -p fe-vm-classic --code-only` → **App started SUCCEEDED** (deployment `01f16769…`,
  later `01f1676b…` after the U116 fix). Resource bindings + Lakebase grants **untouched** (code-only);
  staged `app/geniefy_core`/`app/geniefy_app` refreshed (resolves the U104 stale-staged-yaml LOW).
- Migration `002_library_status.sql` applied **idempotently**.

## Live findings (both fixed + re-verified)
1. **Migration branch mismatch → U117.** `deploy.sh` migrate hardcoded `DB_BRANCH=production`, but the
   dev app's `geniefy-db` binding is `projects/geniefy/branches/**dev**` (endpoint `ep-curly-sunset`).
   `002` first landed on `production` (`ep-blue-smoke`) → `/api/library` 500'd
   (`column "status" does not exist`). Re-applied `002` to the **dev** branch via the sanctioned runner
   (`GENIEFY_LAKEBASE_BRANCH=dev`) → `/api/library` 200 with the lifecycle schema. **U117** fixes
   deploy.sh to migrate the branch matching the target.
2. **Table comment as JSON blob → U116 (BLOCKER).** A live run produced the table `proposed_comment` as a
   JSON-object *string* (`{"purpose":…,"grain":…}`) — the richer-field list (U104) tipped the model into a
   structured dump. **U116** reworded the table prompt to demand a single PROSE comment (NOT JSON);
   re-deployed; re-verified prose. (Column comments were unaffected — verified prose.)

## End-to-end evidence (post-fix, real tables, as the app SP)
- **Generation** (`samples.tpch.nation`, `samples.tpch.region`): rich **prose** table comment covering
  purpose · grain (25 rows) · keys · join patterns · region hierarchy ("…enabling analysts and AI agents
  (Genie) to slice metrics by country and region…"); columns as grounded prose.
- **Free-form tags (live, D53/Q2):** table `["dimension","reference","tpch","geographic"]`; column
  `n_nationkey` `["identifier","key","dimension"]`, `r_regionkey` `["identifier","key","enum","dimension"]`.
- **Data type** surfaced per column (`bigint`) → pill in the UI.
- **Write-on-approve (D52 §A3):** approving the table draft created a `comment_library` row —
  `scope=table`, `match_key=samples.tpch.nation`, **`status=approved`**, tags carried, `usage_count=1`.
- **Sunset/revive (D52 §A5):** sunset → `status=sunset`, hidden from the default `/api/library` (count 0),
  shown with `include_sunset=true` (count 1); revive → back to `approved`.
- **Reuse-on-generation (D52 §A4):** `LibraryProvider` wired into `build_service` (SP reads, D48);
  the library populates on approve so subsequent runs receive approved wording as suggestion-only context.

## Covered hermetically, not live here
- **apply → `applied`** upgrade: `samples` is read-only so a live UC write isn't possible there; covered by
  `tests/test_apply.py` (`status='applied'`, `bump_usage=False`) — the same upsert path.
- **In-browser visual** (pills/hero pixels): the SPA `npm run build` is clean and the components consume the
  exact API shapes verified above; a browser screenshot pass is the one remaining light check.

## Result
The enrichment + library-lifecycle feature is **live and working** on `geniefy-dev`. Suite: 342 hermetic
tests + clean SPA build; live API + lifecycle confirmed. (U116 prose fix audited; U117 deploy fix applied.)
