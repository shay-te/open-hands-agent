PYTHON ?= python3
VENV_PYTHON = .venv/bin/python
KATO_SOURCE_FINGERPRINT := $(shell $(PYTHON) -m kato.helpers.runtime_identity_utils --root .)

.PHONY: bootstrap configure doctor doctor-agent doctor-openhands test run compose-up

bootstrap:
	./scripts/bootstrap.sh

configure:
	$(VENV_PYTHON) scripts/generate_env.py --output .env

doctor:
	$(VENV_PYTHON) -m kato.validate_env --env-file .env --mode all

doctor-agent:
	$(VENV_PYTHON) -m kato.validate_env --env-file .env --mode agent

doctor-openhands:
	$(VENV_PYTHON) -m kato.validate_env --env-file .env --mode openhands

test:
	$(VENV_PYTHON) -m unittest discover -s tests

run:
	./scripts/run-local.sh

compose-up:
	@if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
	export KATO_SOURCE_FINGERPRINT='$(KATO_SOURCE_FINGERPRINT)'; \
	if [ "$${OPENHANDS_SKIP_TESTING:-false}" != "true" ] && [ "$${OPENHANDS_TESTING_CONTAINER_ENABLED:-false}" = "true" ]; then \
		docker compose --profile testing up --build --attach kato; \
	else \
		docker compose up --build --attach kato; \
	fi
