# Dev-branch live verification log (U47)

Live integration checks run against the Lakebase **dev** branch (D37), which is isolated
from production so round-trips never touch prod data. Companion to the deploy runbook
[`APPLY.md`](APPLY.md).

## Target (dev branch — D37)

| | |
|---|---|
| Endpoint | `projects/geniefy/branches/dev/endpoints/primary` |
| Host | `ep-curly-sunset-d2sz2asi.database.us-east-1.cloud.databricks.com` |
| Database / schema | `geniefy` / `geniefy` |
| Connect | same OAuth-credential flow as `APPLY.md` (`databricks postgres generate-database-credential …`) |

## How to run

```bash
P=fe-vm-classic; EP=projects/geniefy/branches/dev/endpoints/primary
HOST=$(databricks postgres list-endpoints projects/geniefy/branches/dev -p $P -o json | jq -r '.[0].status.hosts.host')
TOKEN=$(databricks postgres generate-database-credential $EP -p $P -o json | jq -r '.token')
EMAIL=$(databricks current-user me -p $P -o json | jq -r '.userName')
# ensure DB + schema (idempotent), then a SessionStore save→load round-trip (see the U47 script)
```

## Result log

**2026-06-12 — `SessionStore` (U45/U48) round-trip:**

- Dev branch had no `geniefy` DB yet → **created** it; applied `001_init.sql` (idempotent) → schema present on dev.
- `SessionStore.save(state, created_by)` → `load(id)`: **`loaded == saved`** ✅ (lossless jsonb rehydration, U10 F5).
- Normalized rows verified live: `sessions.status = reasoning` (GATING→reasoning rollup, U10 F3); `template_id = NULL` (the name `"default"` is preserved in the `session_state` jsonb, **D38**); `column_drafts` = `[(o_orderkey, approved), (o_custkey, draft)]`.
- `DELETE` on the session **cascaded** to `column_drafts` (→ 0) ✅.

**Bug caught by this live test (→ U48):** `sessions.template_id` is a uuid FK, but the spine
carries the template *name* (`"default"`); Postgres rejected it. Fixed in U48 (`_uuid_or_none`:
write a valid uuid or NULL; name stays in the jsonb). Recorded as **D38**; proper name→uuid
resolution is deferred to the App template-management unit. The hermetic tests passed before the
fix because they don't exercise a real uuid column — value-of-live-testing demonstrated.

## Notes

- Dev test data is cleaned up after each run (cascade delete); the dev branch is isolated from prod.
- The deployed App binds the **production** branch (or the customer's own); see `APPLY.md` / D37.
