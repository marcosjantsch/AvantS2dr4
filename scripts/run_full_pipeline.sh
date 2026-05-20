#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source scripts/env_coderoom.sh

if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

REFERENCE_DATE="${REFERENCE_DATE:-2026-05-19}"
MONTHS="${MONTHS:-3}"
MAX_CLOUD="${MAX_CLOUD:-5}"
FARM_SLUG="${FARM_SLUG:-}"
S2DR4_LIMIT="${S2DR4_LIMIT:-}"

args=(
  --reference-date "$REFERENCE_DATE"
  --months "$MONTHS"
  --max-cloud "$MAX_CLOUD"
)

if [[ -n "$FARM_SLUG" ]]; then
  args+=(--farm-slug "$FARM_SLUG")
fi

python scripts/prepare_pipeline.py "${args[@]}"

s2_args=()
if [[ -n "$S2DR4_LIMIT" ]]; then
  s2_args+=(--limit "$S2DR4_LIMIT")
fi

python scripts/run_s2dr4_queue.py "${s2_args[@]}"

