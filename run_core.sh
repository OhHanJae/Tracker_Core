#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

python_bin="${PYTHON:-python3}"
venv_dir="${CORE_VENV:-.venv-linux}"
venv_python="$venv_dir/bin/python"

if [[ ! -x "$venv_python" ]]; then
  if ! command -v "$python_bin" >/dev/null 2>&1; then
    echo "[ERROR] Python 3 was not found. Install python3 and python3-venv first."
    exit 1
  fi
  if ! "$python_bin" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    echo "[ERROR] Python 3.10 or later is required."
    exit 1
  fi

  echo "[1/2] Creating virtual environment: $venv_dir"
  if ! "$python_bin" -m venv "$venv_dir"; then
    echo "[ERROR] Failed to create $venv_dir. On Ubuntu, install: sudo apt install python3-venv"
    exit 1
  fi

  echo "[2/2] Installing Core dependencies..."
  if ! "$venv_python" -m pip install -r requirements.txt; then
    echo "[ERROR] Failed to install requirements.txt"
    exit 1
  fi
fi

if ! "$venv_python" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
  echo "[ERROR] $venv_dir must use Python 3.10 or later."
  exit 1
fi

exec "$venv_python" main.py "$@"
