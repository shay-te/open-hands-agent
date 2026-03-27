#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

run_step() {
  printf '%s\n' "==> $*"
  "$@" || {
    status=$?
    printf '%s\n' "Bootstrap failed while running: $*"
    printf '%s\n' "Fix the error above and rerun ./scripts/bootstrap.sh"
    exit "$status"
  }
}

if [ ! -f .env ]; then
  cp .env.example .env
  printf '%s\n' "Created .env from .env.example"
fi

if [ ! -x .venv/bin/python ]; then
  run_step python3 -m venv .venv
fi

run_step sh ./scripts/install-python-deps.sh .venv/bin/python editable
run_step .venv/bin/python -m unittest discover -s tests

cat <<'EOF'

Bootstrap complete.

Next manual steps:
1. Fill the required secrets in .env
2. Run `make doctor` to validate the configuration
3. Run `make run` for local execution or `make compose-up` for Docker
EOF
