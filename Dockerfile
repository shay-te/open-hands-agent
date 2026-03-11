FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY README.md /app/README.md
COPY hydra_plugins /app/hydra_plugins
COPY openhands_agent /app/openhands_agent
COPY docker/entrypoint-run.sh /app/docker/entrypoint-run.sh

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir . && \
    chmod +x /app/docker/entrypoint-run.sh

CMD ["/app/docker/entrypoint-run.sh"]
