#!/bin/sh
set -eu

docker compose down --remove-orphans --volumes
container_ids="$(docker ps -aq)"
if [ -n "$container_ids" ]; then
  docker rm -f $container_ids
fi
sudo docker system prune --all --volumes --force
if docker volume inspect openhands-agent-data >/dev/null 2>&1; then
  docker volume rm openhands-agent-data
fi
rm -rf docker_data
