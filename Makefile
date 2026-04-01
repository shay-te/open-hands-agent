PYTHON ?= python3
VENV_PYTHON = .venv/bin/python

.PHONY: bootstrap configure doctor doctor-agent doctor-openhands test install run compose-up

bootstrap:
	./scripts/bootstrap.sh

configure:
	$(VENV_PYTHON) scripts/generate_env.py --output .env

doctor:
	$(VENV_PYTHON) -m openhands_agent.validate_env --env-file .env --mode all

doctor-agent:
	$(VENV_PYTHON) -m openhands_agent.validate_env --env-file .env --mode agent

doctor-openhands:
	$(VENV_PYTHON) -m openhands_agent.validate_env --env-file .env --mode openhands

test:
	$(VENV_PYTHON) -m unittest discover -s tests

install:
	$(VENV_PYTHON) -m openhands_agent.install

run:
	./scripts/run-local.sh

compose-up:
	@if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
	if [ "$${OPENHANDS_TESTING_CONTAINER_ENABLED:-false}" = "true" ]; then \
		docker compose --profile testing up --build --attach install --attach openhands-agent; \
	else \
		docker compose up --build --attach install --attach openhands-agent; \
	fi
