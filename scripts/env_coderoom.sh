#!/usr/bin/env bash
set -euo pipefail

export APP_ENV="${APP_ENV:-avantev02}"
export EE_PROJECT="${EE_PROJECT:-ee-mapa01}"
export APP_GEO_PATH="${APP_GEO_PATH:-Data/VisitaGFP.shp}"
export APP_EXPORT_DIR="${APP_EXPORT_DIR:-export}"

export S2DR4_WHEEL_URL="${S2DR4_WHEEL_URL:-https://storage.googleapis.com/0x7ff601307fa5/s2dr4-20260518.1-cp312-cp312-linux_x86_64.whl}"
export S2DR4_WHEEL_PATH="${S2DR4_WHEEL_PATH:-vendor/wheels/s2dr4-20260518.1-cp312-cp312-linux_x86_64.whl}"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

