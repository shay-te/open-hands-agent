#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

PYTHON_BIN="${1:-python3}"
INSTALL_MODE="${2:-standard}"

"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install --no-cache-dir \
  "core-lib>=0.2.0" \
  "email-core-lib"

# core-lib 0.2.2 pins hydra-core==1.2, but this project runs on Python 3.11.
"$PYTHON_BIN" -m pip install --no-cache-dir --no-deps \
  "hydra-core>=1.3.2" \
  "omegaconf>=2.3.0"

if [ "$INSTALL_MODE" = "editable" ]; then
  exec "$PYTHON_BIN" -m pip install --no-cache-dir --no-deps -e .
fi

exec "$PYTHON_BIN" -m pip install --no-cache-dir --no-deps .
