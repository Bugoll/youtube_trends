#!/bin/bash
# Daily local refresh: fetch → enrich (LLM, incremental) → refresh metrics → rebuild → commit+push
# Requires: .env in project root with MYCRABS_TOKEN=ghp_...
# Optional: .env can also have ANTHROPIC_API_KEY=sk-ant-... for LLM enrichment

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$PROJECT_DIR/logs/daily_refresh.log"
mkdir -p "$PROJECT_DIR/logs"

exec >> "$LOG" 2>&1
echo ""
echo "=========================================="
echo "$(date '+%Y-%m-%d %H:%M:%S')  START"
echo "=========================================="

cd "$PROJECT_DIR"

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
else
  echo "ERROR: .env not found — create it from .env.example"
  exit 1
fi

[ -z "${MYCRABS_TOKEN:-}" ] && { echo "ERROR: MYCRABS_TOKEN not set in .env"; exit 1; }

PYTHON="${PYTHON:-python3}"

echo "--- fetch ---"
$PYTHON scripts/fetch_packages.py --dest data/raw

echo "--- process pass 1 ---"
$PYTHON scripts/process.py --raw data/raw --out docs/data

echo "--- enrich (LLM, incremental) ---"
$PYTHON scripts/enrich.py --out docs/data --limit "${ENRICH_LIMIT:-30}" \
  || echo "WARN: enrich.py finished with errors — continuing"

echo "--- refresh metrics ---"
$PYTHON scripts/refresh_metrics.py --out docs/data \
  || echo "WARN: refresh_metrics finished with errors — continuing"

echo "--- process pass 2 ---"
$PYTHON scripts/process.py --raw data/raw --out docs/data

echo "--- git ---"
git add data/raw docs/data
if git diff --cached --quiet; then
  echo "No changes — nothing to push."
else
  git commit -m "chore(data): daily refresh $(date '+%Y-%m-%d')"
  git push
  echo "Pushed."
fi

echo "$(date '+%Y-%m-%d %H:%M:%S')  DONE"
