#!/usr/bin/env bash
#
# geniefy-v3 — deploy the hands-off schema-run Job (SEPARATE bundle, D54/U119).
#
# Deployed INDEPENDENTLY of the app bundle: this bundle's only resource is the job, so its
# `bundle deploy` never reconciles (and never wipes) the app's Lakebase/fmapi bindings + SP grants
# (the grant-safety reason the app uses `--code-only`, U77/U78/D48). Stages the agent core +
# entrypoint into jobs-bundle/ (mirroring deploy.sh's app staging) so the bundle is self-contained.
#
#   ./deploy_jobs.sh -t dev -p <cli-profile>
set -euo pipefail

TARGET="dev"; PROFILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -t) TARGET="$2"; shift 2;;
    -p) PROFILE="$2"; shift 2;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 1;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JB="$ROOT/jobs-bundle"
DBX=(databricks); [[ -n "$PROFILE" ]] && DBX+=(-p "$PROFILE")

echo "[jobs] staging agent core + entrypoint into jobs-bundle/ (gitignored build artifacts)"
rm -rf "$JB/geniefy_core" "$JB/geniefy_app" "$JB/schema_run_entry.py"
cp -R "$ROOT/src/geniefy_core" "$ROOT/src/geniefy_app" "$JB/"
cp "$ROOT/jobs/schema_run_entry.py" "$JB/"
find "$JB/geniefy_core" "$JB/geniefy_app" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "[jobs] bundle deploy (geniefy-jobs, target ${TARGET}) — independent of the app bundle (grant-safe)"
( cd "$JB" && "${DBX[@]}" bundle deploy -t "$TARGET" )
echo "[jobs] done — geniefy_schema_run deployed. The app trigger (POST /api/schema-runs) runs it."
