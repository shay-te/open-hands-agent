#!/bin/sh
set -eu

cd /app

python - <<'PY'
import time
import urllib.request

url = "http://openhands:3000"

for _ in range(60):
    try:
        urllib.request.urlopen(url, timeout=5)
        break
    except Exception:
        time.sleep(2)
else:
    raise SystemExit("OpenHands did not become reachable in time")
PY

exec python -m openhands_agent.main
