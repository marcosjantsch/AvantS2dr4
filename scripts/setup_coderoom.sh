#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source scripts/env_coderoom.sh

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
VENV_DIR="${VENV_DIR:-.venv}"
TORCH_INDEX_URL="${TORCH_INDEX_URL:-https://download.pytorch.org/whl/cu124}"

say() {
  printf "\n[setup] %s\n" "$1"
}

run_apt() {
  if command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    "$@"
  fi
}

say "Checking Linux and Python 3.12"
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This setup must run in Linux. Current system: $(uname -s)" >&2
  exit 1
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  say "Python 3.12 not found. Trying apt install."
  if command -v apt-get >/dev/null 2>&1; then
    run_apt apt-get update
    run_apt apt-get install -y \
      python3.12 python3.12-venv python3.12-dev \
      build-essential curl ca-certificates git \
      gdal-bin libgdal-dev python3-gdal libspatialindex-dev
  fi
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.12 is still missing. Install Python 3.12 in the CodeRoom and re-run this script." >&2
  exit 1
fi

say "Creating virtualenv at $VENV_DIR"
"$PYTHON_BIN" -m venv --system-site-packages "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip setuptools wheel

say "Installing CUDA-enabled PyTorch when possible"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi || true
  pip install --index-url "$TORCH_INDEX_URL" torch torchvision torchaudio || pip install torch torchvision torchaudio
else
  echo "nvidia-smi not found. Installing default PyTorch; S2DR4 may run slowly or fail without GPU."
  pip install torch torchvision torchaudio
fi

say "Installing app and geospatial dependencies"
pip install -r requirements-coderoom.txt
pip install --no-deps py_tools_ds==0.24.1 geoarray==0.19.2 arosics==1.13.2

say "Downloading S2DR4 wheel into local CodeRoom cache"
mkdir -p "$(dirname "$S2DR4_WHEEL_PATH")"
if [[ ! -f "$S2DR4_WHEEL_PATH" ]]; then
  curl -L "$S2DR4_WHEEL_URL" -o "$S2DR4_WHEEL_PATH"
fi
pip install --no-deps "$S2DR4_WHEEL_PATH"

say "Creating export/auth folders"
mkdir -p "$APP_EXPORT_DIR" auth

say "Validating environment"
python scripts/validate_coderoom.py

say "Done. Activate with: source $VENV_DIR/bin/activate"
