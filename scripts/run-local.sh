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
. ./.env
set +a

if [ "${1:-}" = "--create-db-only" ]; then
  exec .venv/bin/python -m openhands_agent.main
fi

exec .venv/bin/python -m openhands_agent.main
