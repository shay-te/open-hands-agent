FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY . .

RUN sh /app/scripts/install-python-deps.sh python && \
    chmod +x /app/docker/entrypoint-run.sh /app/docker/entrypoint-install.sh

CMD ["/app/docker/entrypoint-run.sh"]
