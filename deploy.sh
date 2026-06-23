#!/usr/bin/env bash
#
# geniefy-v3 — one-command deploy orchestrator (U62, NFR-D / D35).
#
# DAB is the spine; this thin, **idempotent**, re-runnable script orchestrates the steps the
# bundle can't express today (Lakebase Autoscaling provisioning, the frontend build, the
# Postgres migration, grants). A customer clones the repo and runs:
#
#     ./deploy.sh -t prod --host https://<workspace> -p <cli-profile>
#
# Steps (each idempotent — a re-run heals a partial deploy):
#   1. Preflight   — CLI >= 0.285 (Autoscaling Lakebase), jq, node/npm, auth reachable.
#   2. Build FE    — vite build -> app/static (so the App serves the SPA at /, U61).
#   3. Lakebase    — create project/branch/endpoint if absent (DB itself is made in step 5).
#   4. Deploy      — `bundle deploy` (App + geniefy_setup job + bindings) on a FIRST deploy; with
#                    --code-only, a grant-safe REDEPLOY (sync + apps deploy, preserves grants/bindings).
#   5. Migrate     — apply migrations/ to the `geniefy` DB (local runner; or --use-job).
#   6. Grants      — print the UC/Lakebase grants the app service principal needs.
#   7. URL         — print the deployed App URL.
#
# See docs/design/LLD-nfr-plan.md §4 and migrations/APPLY.md.
set -euo pipefail

# ---- args ---------------------------------------------------------------------------------
TARGET="dev"
PROFILE=""
HOST=""
SKIP_BUILD=0
USE_JOB=0
CODE_ONLY=0
LAKEBASE_INSTANCE="${GENIEFY_LAKEBASE_INSTANCE:-geniefy}"   # = the `lakebase_instance` bundle var
MIN_CLI="0.285.0"

usage() {
  sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
  echo
  echo "Flags: -t <dev|prod>  -p <cli-profile>  --host <url>  --skip-build  --use-job  --code-only  -h"
  echo "  --code-only  Grant-safe REDEPLOY of an existing app: sync files + deploy the code snapshot"
  echo "               WITHOUT 'bundle deploy' (which reconciles app resources and wipes the UI-added"
  echo "               postgres + serving bindings → drops the SP Lakebase role + grants, U77/U78/D48)."
  exit "${1:-0}"
}
needval() { [[ -n "${2:-}" ]] || { echo "option $1 requires a value" >&2; usage 1; }; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    -t) needval "$1" "${2:-}"; TARGET="$2"; shift 2;;
    -p) needval "$1" "${2:-}"; PROFILE="$2"; shift 2;;
    --host) needval "$1" "${2:-}"; HOST="$2"; shift 2;;
    --skip-build) SKIP_BUILD=1; shift;;
    --use-job) USE_JOB=1; shift;;
    --code-only) CODE_ONLY=1; shift;;
    -h|--help) usage 0;;
    *) echo "unknown arg: $1" >&2; usage 1;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PROJ="projects/${LAKEBASE_INSTANCE}"
# Lakebase branch (D57/U135): default to the stable `production` branch for EVERY target. The dev
# branch's Autoscaling endpoint host churns (ep-curly-sunset→ep-broad-bread) and breaks the
# app.yaml-pinned GENIEFY_PG_HOST literal, so the dev deployment now uses production too — for
# sustained persistence (app.yaml points at the production endpoint host). Override with
# GENIEFY_LAKEBASE_BRANCH to target an isolated branch (e.g. GENIEFY_LAKEBASE_BRANCH=dev ./deploy.sh -t dev).
DB_BRANCH="${GENIEFY_LAKEBASE_BRANCH:-production}"
DB_ENDPOINT="primary"
APP_NAME="geniefy-${TARGET}"

# databricks CLI argument fragments (profile is optional). The workspace host comes from the
# profile or DATABRICKS_HOST — Databricks auth fields can't take a bundle ${var} (so --host
# exports DATABRICKS_HOST rather than passing --var host).
DBX=(databricks)
[[ -n "$PROFILE" ]] && DBX+=(-p "$PROFILE")
[[ -n "$HOST" ]] && export DATABRICKS_HOST="$HOST"

log()  { printf '\033[1;36m[deploy]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[deploy] FAILED:\033[0m %s\n' "$*" >&2; exit 1; }
step() { printf '\n\033[1;35m── %s ──\033[0m\n' "$*"; }

# ---- 1. preflight -------------------------------------------------------------------------
step "1/7 Preflight"
command -v databricks >/dev/null || die "databricks CLI not found (need >= ${MIN_CLI} for Autoscaling Lakebase)"
command -v jq >/dev/null         || die "jq not found (brew install jq)"
CLI_VER="$(databricks --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
if [[ "$(printf '%s\n%s\n' "$MIN_CLI" "$CLI_VER" | sort -V | head -1)" != "$MIN_CLI" ]]; then
  die "databricks CLI ${CLI_VER} < ${MIN_CLI} — upgrade (brew upgrade databricks)"
fi
ok "databricks CLI ${CLI_VER}"
if [[ "$SKIP_BUILD" -eq 0 ]]; then command -v npm >/dev/null || die "npm not found (needed to build the frontend; or pass --skip-build)"; fi
"${DBX[@]}" current-user me -o json >/dev/null 2>&1 || die "not authenticated — run 'databricks auth login --host <url> -p <profile>' (or pass -p)"
ok "workspace auth OK ($("${DBX[@]}" current-user me -o json | jq -r .userName))"

# ---- 2. build frontend --------------------------------------------------------------------
step "2/7 Build frontend → app/static"
if [[ "$SKIP_BUILD" -eq 1 ]]; then
  log "--skip-build set; reusing existing app/static"
else
  ( cd app/frontend && { [[ -d node_modules ]] || npm ci || npm install; } && npm run build )
  ok "vite build → app/static"
fi
[[ -f app/static/index.html ]] || die "app/static/index.html missing — the SPA build did not produce output"
# Stage the agent core + backend into the app tree: the Databricks App's source_code_path is
# ./app, so it imports geniefy_core/geniefy_app from its own dir at runtime (U77). Gitignored
# build artifacts, refreshed each deploy — psycopg2/pyyaml come from app/requirements.txt.
rm -rf app/geniefy_core app/geniefy_app
cp -R src/geniefy_core src/geniefy_app app/
find app/geniefy_core app/geniefy_app -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
ok "staged geniefy_core + geniefy_app into app/"

# ---- 3. ensure Lakebase (Autoscaling: project → branch → endpoint) ------------------------
step "3/7 Ensure Lakebase project ${PROJ}"
if "${DBX[@]}" postgres get-project "$PROJ" -o json >/dev/null 2>&1; then
  ok "project ${PROJ} exists"
else
  log "creating project ${PROJ} (auto-creates the production branch + primary endpoint)"
  "${DBX[@]}" postgres create-project "$LAKEBASE_INSTANCE" \
    --json "{\"spec\":{\"display_name\":\"geniefy (${TARGET})\"}}" >/dev/null
fi
# Ensure the TARGET's branch + endpoint exist (U117): create-project only makes production/primary,
# but the app may bind a non-production branch (the dev app binds .../branches/dev). Branch it from
# production so it inherits the schema; idempotent (no-op if it already exists).
if [[ "$DB_BRANCH" != "production" ]]; then
  if "${DBX[@]}" postgres list-endpoints "${PROJ}/branches/${DB_BRANCH}" -o json >/dev/null 2>&1; then
    ok "branch ${DB_BRANCH} exists"
  else
    log "creating branch ${DB_BRANCH} (from production) + endpoint ${DB_ENDPOINT}"
    "${DBX[@]}" postgres create-branch "$PROJ" "$DB_BRANCH" \
      --json "{\"spec\":{\"source_branch\":\"${PROJ}/branches/production\",\"no_expiry\":true}}" >/dev/null 2>&1 \
      || log "  (branch ${DB_BRANCH} may already exist)"
    "${DBX[@]}" postgres create-endpoint "${PROJ}/branches/${DB_BRANCH}" "$DB_ENDPOINT" \
      --json "{\"spec\":{\"endpoint_type\":\"ENDPOINT_TYPE_READ_WRITE\",\"autoscaling_limit_min_cu\":0.5,\"autoscaling_limit_max_cu\":2.0}}" >/dev/null 2>&1 \
      || log "  (endpoint ${DB_ENDPOINT} on ${DB_BRANCH} may already exist)"
  fi
fi
log "waiting for the read-write endpoint to become ACTIVE…"
for _ in $(seq 1 60); do
  STATE="$("${DBX[@]}" postgres list-endpoints "${PROJ}/branches/${DB_BRANCH}" -o json 2>/dev/null \
            | jq -r '.[0].status.current_state // "PENDING"')"
  [[ "$STATE" == "ACTIVE" ]] && { ok "endpoint ACTIVE"; break; }
  sleep 5
done
[[ "${STATE:-}" == "ACTIVE" ]] || die "Lakebase endpoint did not reach ACTIVE (current: ${STATE:-unknown})"
PG_HOST="$("${DBX[@]}" postgres list-endpoints "${PROJ}/branches/${DB_BRANCH}" -o json | jq -r '.[0].status.hosts.host')"
[[ -n "$PG_HOST" && "$PG_HOST" != "null" ]] || die "could not resolve the Lakebase endpoint host"
ok "Lakebase host ${PG_HOST}"

# ---- 4. deploy ----------------------------------------------------------------------------
step "4/7 Deploy app -t ${TARGET}$([[ "$CODE_ONLY" -eq 1 ]] && echo ' (code-only)')"
"${DBX[@]}" bundle validate -t "$TARGET" --var "pg_host=${PG_HOST}" >/dev/null || die "bundle validate failed"
if [[ "$CODE_ONLY" -eq 1 ]]; then
  # Grant-safe REDEPLOY (U78/D48). `bundle deploy` reconciles the app's `resources` to databricks.yml
  # ([sql-warehouse] only) and WIPES the UI-added `geniefy-db` (Lakebase postgres) + `fmapi-endpoint`
  # bindings → drops the SP's Lakebase role + schema grants (U77 finding #6). So for an existing app we
  # sync files (NO resource reconciliation) + deploy the code snapshot — bindings + grants untouched.
  log "code-only redeploy: sync files + apps deploy (preserves resource bindings + Lakebase grants)"
  "${DBX[@]}" bundle sync -t "$TARGET" || die "bundle sync failed"
  SRC_PATH="$("${DBX[@]}" apps get "$APP_NAME" -o json 2>/dev/null | jq -r '.default_source_code_path // empty')"
  [[ -n "$SRC_PATH" ]] || die "could not resolve ${APP_NAME} source path — run a FULL deploy first (without --code-only)"
  "${DBX[@]}" apps deploy "$APP_NAME" --source-code-path "$SRC_PATH" || die "apps deploy failed"
  ok "app code redeployed (resource bindings + grants untouched)"
else
  "${DBX[@]}" bundle deploy -t "$TARGET" --var "pg_host=${PG_HOST}"
  ok "bundle deployed (App + geniefy_setup job + resource bindings)"
  log "deploying the app code + starting compute (provisions on first run; may take a few min)…"
  "${DBX[@]}" bundle run geniefy -t "$TARGET" --var "pg_host=${PG_HOST}" || die "app deploy/start failed (databricks bundle run geniefy)"
  ok "app deployed + started"
fi

# ---- 5. migrate ---------------------------------------------------------------------------
step "5/7 Apply migrations to the Lakebase 'geniefy' database"
if [[ "$USE_JOB" -eq 1 ]]; then
  log "running the geniefy_setup job (bundle-native path) on branch ${DB_BRANCH}"
  # Pass the per-target branch (U118) so the job migrates the branch the app binds, not always
  # production — run_migrations.py reads it as argv[1] via the job's lakebase_branch parameter.
  "${DBX[@]}" bundle run geniefy_setup -t "$TARGET" --params "lakebase_branch=${DB_BRANCH}"
else
  command -v python3 >/dev/null || die "python3 not found (needed for the local migrate path; or pass --use-job)"
  python3 -c "import psycopg2" 2>/dev/null || die "psycopg2 not installed (pip install psycopg2-binary) — or pass --use-job"
  GENIEFY_LAKEBASE_PROJECT="$PROJ" GENIEFY_LAKEBASE_BRANCH="$DB_BRANCH" \
    GENIEFY_LAKEBASE_ENDPOINT="$DB_ENDPOINT" DATABRICKS_CONFIG_PROFILE="$PROFILE" \
    python3 migrations/run_migrations.py
fi
ok "migrations applied (idempotent)"

# ---- 6. grants ----------------------------------------------------------------------------
step "6/7 Grants for the app service principal"
SP="$("${DBX[@]}" apps get "$APP_NAME" -o json 2>/dev/null | jq -r '.service_principal_client_id // .service_principal_name // empty')"
# The app SP authenticates to Postgres via OAuth — it needs a role on the branch (the access
# the removed `database` binding would have implied, U77). Best-effort; non-fatal.
# On a --code-only redeploy the role + grants are already in place and untouched, so we DON'T
# re-provision (re-creating the role would defeat the grant-safe path) — just print the guidance.
if [[ "$CODE_ONLY" -eq 1 ]]; then
  ok "code-only redeploy — SP Lakebase role + grants preserved (not re-provisioned)"
elif [[ -n "$SP" ]]; then
  if "${DBX[@]}" postgres create-role "${PROJ}/branches/${DB_BRANCH}" --role-id "$SP" --no-wait >/dev/null 2>&1; then
    ok "app SP granted a Lakebase role on ${DB_BRANCH}"
  else
    log "couldn't auto-grant the SP Lakebase role (may already exist) — if the app can't reach Lakebase, run:"
    echo "    ${DBX[*]} postgres create-role ${PROJ}/branches/${DB_BRANCH} --role-id ${SP}"
  fi
fi
cat <<EOF
  The app service principal${SP:+ ($SP)} needs (run as a metastore/table owner):
    GRANT SELECT ON TABLE <catalog>.<schema>.<table> TO \`$([[ -n "$SP" ]] && echo "$SP" || echo "<app-sp>")\`;   -- profiling
    GRANT MODIFY ON TABLE <catalog>.<schema>.<table> TO \`$([[ -n "$SP" ]] && echo "$SP" || echo "<app-sp>")\`;   -- apply write-path (D7/U6)
  Lakebase + secret-scope access for the SP is granted by the resource bindings in databricks.yml.
EOF
ok "grant guidance printed (table-specific grants are the operator's call)"

# ---- 7. print the App URL -----------------------------------------------------------------
step "7/7 App URL"
URL="$("${DBX[@]}" apps get "$APP_NAME" -o json 2>/dev/null | jq -r '.url // empty')"
if [[ -n "$URL" ]]; then ok "geniefy is deployed → ${URL}"; else log "deployed; fetch the URL with: ${DBX[*]} apps get ${APP_NAME}"; fi
log "done."
