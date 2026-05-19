#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source scripts/env_coderoom.sh

if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8787}"

python app.py --host "$HOST" --port "$PORT"

