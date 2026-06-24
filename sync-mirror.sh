#!/usr/bin/env bash
# sync-mirror.sh — pull latest from RohitDashora/geniefy-lite, scrub internal
# files and Rohit-specific values, and push to smathews13/geniefy-lite.
#
# Usage: bash sync-mirror.sh
# Requires: git, gh (authenticated as smathews13 + access to RohitDashora/geniefy-lite)

set -euo pipefail

UPSTREAM="https://github.com/RohitDashora/geniefy-lite.git"
MIRROR="smathews13/geniefy-lite"
WORK_DIR="$(mktemp -d)"
TOKEN=$(gh auth token --user smathews13)

echo "==> Cloning upstream..."
git clone "$UPSTREAM" "$WORK_DIR" 2>&1

cd "$WORK_DIR"

echo "==> Removing internal files..."
rm -rf .gotm .claude CLAUDE.md docs/GOTM-FEEDBACK.md
rm -f docs/verify/DEPLOY_VERIFY_R2.md docs/verify/DEPLOY_VERIFY_R3-005.md \
       docs/verify/DEPLOY_VERIFY_R3-006.md docs/verify/UI_DEVLOOP_VERIFY_R3.md

echo "==> Scrubbing Rohit-specific values..."
# Lakebase endpoint hosts
find . -not -path "./.git/*" \( -name "*.py" -o -name "*.yml" -o -name "*.yaml" \
  -o -name "*.sh" -o -name "*.md" -o -name "*.ts" -o -name "*.tsx" -o -name "*.json" \) \
  -exec sed -i '' \
    -e 's|ep-blue-smoke-d2yetcmv\.database\.us-east-1\.cloud\.databricks\.com|<your-lakebase-endpoint-host>|g' \
    -e 's|ep-blue-smoke\.database\.cloud\.databricks\.com|ep-example.database.us-east-1.cloud.databricks.com|g' \
    -e 's|rd_classic_catalog|my_catalog|g' \
    -e 's|RohitDashora/geniefy-lite|smathews13/geniefy-lite|g' \
    -e 's|rohit\.dashora@databricks\.com|<contributor-email>|g' \
    {} \;

echo "==> Checking for remaining internal references..."
REMAINING=$(grep -rn "RohitDashora\|rohit\.dashora\|rd_classic\|ep-blue-smoke" \
  --include="*.py" --include="*.yml" --include="*.yaml" --include="*.sh" \
  --include="*.md" --include="*.ts" --include="*.tsx" --include="*.json" \
  . 2>/dev/null | grep -v ".git/" || true)

if [[ -n "$REMAINING" ]]; then
  echo "[warn] Remaining references found — review before pushing:"
  echo "$REMAINING"
  read -r -p "Continue anyway? [y/N] " confirm
  [[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; rm -rf "$WORK_DIR"; exit 1; }
fi

echo "==> Pushing to $MIRROR..."
git remote set-url origin "https://smathews13:$TOKEN@github.com/$MIRROR.git"
git push origin main

echo "==> Done. Mirror updated: https://github.com/$MIRROR"
rm -rf "$WORK_DIR"
