#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

PYTHON_BIN="${1:-python3}"
INSTALL_MODE="${2:-standard}"

"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install --no-cache-dir \
  "core-lib>=0.2.0" \
  "email-core-lib"

# core-lib 0.2.2 pins hydra-core==1.2 and sqlalchemy==1.4.44, but this project
# runs on Python 3.11 and SQLAlchemy 2.x.
"$PYTHON_BIN" -m pip install --no-cache-dir --no-deps \
  "hydra-core>=1.3.2" \
  "sqlalchemy>=2.0.0"
"$PYTHON_BIN" -m pip install --no-cache-dir \
  "alembic>=1.14.0" \
  "omegaconf>=2.3.0" \
  "pydantic>=2.11.0"

if [ "$INSTALL_MODE" = "editable" ]; then
  exec "$PYTHON_BIN" -m pip install --no-cache-dir --no-deps -e .
fi

exec "$PYTHON_BIN" -m pip install --no-cache-dir --no-deps .
