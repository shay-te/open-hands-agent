PYTHON ?= python3
VENV_PYTHON = .venv/bin/python

.PHONY: bootstrap doctor doctor-agent doctor-openhands test create-db run compose-up

bootstrap:
	./scripts/bootstrap.sh

doctor:
	$(VENV_PYTHON) -m openhands_agent.validate_env --env-file .env --mode all

doctor-agent:
	$(VENV_PYTHON) -m openhands_agent.validate_env --env-file .env --mode agent

doctor-openhands:
	$(VENV_PYTHON) -m openhands_agent.validate_env --env-file .env --mode openhands

test:
	$(VENV_PYTHON) -m unittest discover -s tests

create-db:
	$(VENV_PYTHON) -m openhands_agent.create_db

run:
	./scripts/run-local.sh

compose-up:
	docker compose up --build
