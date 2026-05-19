#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source scripts/env_coderoom.sh

mkdir -p "$(dirname "$S2DR4_WHEEL_PATH")"
if [[ -f "$S2DR4_WHEEL_PATH" ]]; then
  echo "Wheel already exists: $S2DR4_WHEEL_PATH"
else
  curl -L "$S2DR4_WHEEL_URL" -o "$S2DR4_WHEEL_PATH"
  echo "Downloaded: $S2DR4_WHEEL_PATH"
fi

