#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  printf '%s\n' "Created .env from .env.example"
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests

cat <<'EOF'

Bootstrap complete.

Next manual steps:
1. Fill the required secrets in .env
2. Run `make doctor` to validate the configuration
3. Run `make run` for local execution or `make compose-up` for Docker
EOF
