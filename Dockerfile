FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY . .

RUN sh /app/scripts/install-python-deps.sh python && \
    apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/* && \
    chmod +x /app/docker/entrypoint-run.sh /app/docker/entrypoint-install.sh
