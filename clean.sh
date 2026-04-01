#!/bin/sh
set -eu

docker compose down --remove-orphans --volumes
sudo docker system prune --all --volumes
docker volume rm openhands-agent-data || true
rm -rf docker_data
