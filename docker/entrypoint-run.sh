#!/bin/sh
set -eu

cd /app

python - <<'PY'
import os
import time
import urllib.request

TRUE_VALUES = {"1", "true", "yes", "on"}


def wait_until_reachable(url: str, label: str) -> None:
    for _ in range(60):
        try:
            urllib.request.urlopen(url, timeout=5)
            return
        except Exception:
            time.sleep(2)
    raise SystemExit(f"{label} did not become reachable in time")


wait_until_reachable("http://openhands:3000", "OpenHands")
if os.getenv("OPENHANDS_TESTING_CONTAINER_ENABLED", "").strip().lower() in TRUE_VALUES:
    wait_until_reachable(
        os.getenv("OPENHANDS_TESTING_BASE_URL", "http://openhands-testing:3000"),
        "OpenHands testing",
    )
PY

exec python -m openhands_agent.main
