#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  printf '%s\n' ".env is missing. Run ./scripts/bootstrap.sh first."
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  printf '%s\n' ".venv is missing. Run ./scripts/bootstrap.sh first."
  exit 1
fi

set -a
# shellcheck source=/dev/null
. ./.env
set +a

exec .venv/bin/python -m openhands_agent.main
